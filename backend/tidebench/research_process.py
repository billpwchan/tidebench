"""Owned disposable research processes; no workspace database or venue credentials."""

import gzip
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import zlib
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from .platform import PlatformError
from .research_artifacts import MAX_ARTIFACT_BYTES
from .store import dumps


def run_isolated(kind, payload, *, timeout=900, memory_mb=256, progress=None, cancelled=None):
    with TemporaryDirectory(prefix="tidebench-research-") as directory:
        root = Path(directory)
        request = root / "input.json"
        request.write_text(
            dumps({"kind": kind, "payload": payload, "memory_mb": memory_mb, "cpu_seconds": timeout + 5})
        )
        request.chmod(0o600)
        if request.stat().st_size > MAX_ARTIFACT_BYTES:
            raise PlatformError(
                "research_input_budget", "Worker input exceeds 128 MiB; split the study.", 422
            )
        # Deliberately exclude parent credentials and private workspace settings.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV"}
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "tidebench.research_process", str(root), str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
            start_new_session=os.name == "posix",
        )
        messages = queue.Queue(maxsize=100)

        def receive():
            for line in process.stdout:
                try:
                    message = json.loads(line)
                    if message.get("kind") in {"completed", "failed"}:
                        # Terminal evidence must survive a saturated progress queue.
                        while True:
                            try:
                                messages.put_nowait(message)
                                break
                            except queue.Full:
                                with suppress(queue.Empty):
                                    messages.get_nowait()
                    else:
                        messages.put_nowait(message)
                except (ValueError, queue.Full):
                    pass

        reader = threading.Thread(target=receive, name="research-progress", daemon=True)
        reader.start()
        started = time.monotonic()
        terminal = None
        try:
            while True:
                if cancelled and cancelled.is_set():
                    raise PlatformError(
                        "research_cancelled",
                        "Research worker stopped for workspace maintenance; queued work can resume after restart.",
                        503,
                    )
                if time.monotonic() - started > timeout:
                    raise PlatformError(
                        "research_deadline",
                        "Research exceeded its wall-clock deadline and its worker was terminated. Split the study.",
                        422,
                    )
                try:
                    message = messages.get(timeout=0.05)
                    if message.get("kind") == "progress" and progress:
                        progress(message["fraction"])
                    elif message.get("kind") in {"completed", "failed"}:
                        terminal = message
                except queue.Empty:
                    pass
                if process.poll() is not None:
                    reader.join(timeout=1)
                    while not messages.empty():
                        message = messages.get_nowait()
                        if message.get("kind") in {"completed", "failed"}:
                            terminal = message
                    break
            if process.returncode != 0 or not terminal or terminal["kind"] != "completed":
                raise PlatformError(
                    "research_worker_failed",
                    (terminal or {}).get(
                        "error",
                        "Research worker exited without a complete artifact; memory/CPU constraints or a process failure may be responsible.",
                    ),
                    422,
                )
            artifact = root / "result.json.gz"
            if not artifact.is_file() or artifact.stat().st_size > MAX_ARTIFACT_BYTES:
                raise PlatformError(
                    "research_artifact_budget", "Worker result exceeds the artifact budget.", 422
                )
            decoder = zlib.decompressobj(wbits=31)
            raw = decoder.decompress(artifact.read_bytes(), MAX_ARTIFACT_BYTES + 1)
            import hashlib

            if (
                not decoder.eof
                or decoder.unused_data
                or len(raw) > MAX_ARTIFACT_BYTES
                or hashlib.sha256(raw).hexdigest() != terminal["sha256"]
            ):
                raise PlatformError(
                    "research_worker_integrity", "Worker artifact failed its size or content check.", 409
                )
            return json.loads(raw)
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
            process.wait(timeout=5)
            reader.join(timeout=1)
            process.stdout.close()


def watch_parent(parent_pid, interval=0.25):
    """Exit if our owner disappears, including an abrupt server SIGKILL.

    Linux also uses the kernel parent-death signal. The portable watcher covers
    macOS/Windows; it is not a replacement for deployment OS resource limits.
    """
    if os.getppid() != parent_pid:
        os._exit(72)
    if sys.platform.startswith("linux"):
        import ctypes

        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Cannot install research parent-death signal")
        if os.getppid() != parent_pid:
            os._exit(72)

    def monitor():
        while True:
            time.sleep(interval)
            if os.getppid() != parent_pid:
                os._exit(72)

    threading.Thread(target=monitor, name="research-owner", daemon=True).start()


def worker(directory, parent_pid=None):
    if parent_pid is not None:
        watch_parent(parent_pid)
    import hashlib
    from decimal import Decimal, localcontext

    from .engine import ACCOUNTING_CONTEXT, Candle
    from .portfolio_research import simulate_portfolio
    from .pro_research import replay_research_snapshot

    root = Path(directory)
    request = json.loads((root / "input.json").read_text())
    if sys.platform.startswith("linux"):
        import resource

        # Linux RLIMIT_AS constrains virtual address space, not measured RSS.
        baseline = (
            int(
                next(
                    line.split()[1]
                    for line in Path("/proc/self/status").read_text().splitlines()
                    if line.startswith("VmSize:")
                )
            )
            * 1024
        )
        bound = baseline + int(request["memory_mb"]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        resource.setrlimit(resource.RLIMIT_CPU, (int(request["cpu_seconds"]), int(request["cpu_seconds"])))
    progress_lock = threading.Lock()
    last = [0.0]

    def progress(fraction):
        with progress_lock:
            if time.monotonic() - last[0] >= 0.25 or fraction >= 1:
                print(json.dumps({"kind": "progress", "fraction": fraction}), flush=True)
                last[0] = time.monotonic()

    try:
        payload = request["payload"]
        with localcontext(ACCOUNTING_CONTEXT):
            if request["kind"] == "single":
                result = replay_research_snapshot(
                    payload["snapshot"], _plan=payload["plan"], progress=progress
                )
            elif request["kind"] == "portfolio":
                for leg in payload["legs"]:
                    for name in ("candles", "marks"):
                        leg[name] = [
                            Candle(
                                **{
                                    key: Decimal(value)
                                    if key in {"open", "high", "low", "close", "volume"}
                                    else value
                                    for key, value in row.items()
                                }
                            )
                            for row in leg[name]
                        ]
                if payload["manifest"].get("evaluation_plan"):
                    from .research_protocol import evaluate_frozen_portfolio

                    result = evaluate_frozen_portfolio(
                        payload["config"], payload["manifest"], payload["legs"], progress
                    )
                else:
                    result = simulate_portfolio(
                        payload["config"], payload["manifest"], payload["legs"], progress
                    )
            else:
                raise ValueError("Unsupported research operation")
        raw = dumps(result).encode()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("Research artifact exceeds 128 MiB")
        temporary = root / "result.tmp"
        temporary.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        temporary.chmod(0o600)
        temporary.replace(root / "result.json.gz")
        print(json.dumps({"kind": "completed", "sha256": hashlib.sha256(raw).hexdigest()}), flush=True)
    except Exception as exc:
        print(json.dumps({"kind": "failed", "error": str(exc)[:1000]}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    worker(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
