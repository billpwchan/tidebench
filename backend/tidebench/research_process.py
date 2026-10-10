"""Owned disposable research processes; no workspace database or venue credentials."""

import gzip
import json
import os
import queue
import signal
import stat
import subprocess
import sys
import threading
import time
import zlib
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

from .platform import PlatformError
from .research_artifacts import MAX_ARTIFACT_BYTES
from .store import dumps, new_id, now_ms

# Raw descriptors deliberately live until OS process exit, not worker() return.
_ownership_descriptors = []


class ResearchCancellation:
    """An individual durable request, distinct from a resumable maintenance stop."""

    def __init__(self, requested, maintenance):
        self.requested, self.maintenance = requested, maintenance

    def is_set(self):
        return self.requested.is_set() or self.maintenance.is_set()

    def check(self):
        if self.requested.is_set():
            raise PlatformError("research_cancel_requested", "Research cancellation requested.", 409)
        if self.maintenance.is_set():
            raise PlatformError(
                "research_cancelled",
                "Research worker stopped for workspace maintenance; queued work can resume after restart.",
                503,
            )


research_cancellation = ContextVar("research_cancellation", default=None)


def check_cancellation(cancelled):
    if isinstance(cancelled, ResearchCancellation):
        cancelled.check()
    elif cancelled and cancelled.is_set():
        raise PlatformError(
            "research_cancelled",
            "Research worker stopped for workspace maintenance; queued work can resume after restart.",
            503,
        )


def process_exists(pid):
    """Read-only liveness; None means permission/identity is uncertain."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return None
        return True
    # os.kill(pid, 0) can terminate a process on Windows. Query it instead.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        code = wintypes.DWORD()
        return code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
    finally:
        kernel.CloseHandle(handle)


def ownership_lock(descriptor):
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)


def worker_exit_evidence(worker):
    """Never signal a recovered PID. Missing launch/identity evidence is a blocker."""
    if not isinstance(worker, dict):
        return False, "Previous worker identity is unavailable."
    token = worker.get("token", "")
    if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        return False, "Previous worker identity is unavailable."
    if worker.get("state") in {"exited", "not_launched"}:
        return True, "Owned worker was reaped or was never launched."
    if not isinstance(worker.get("directory"), str):
        return False, "Previous worker ownership path is unavailable."
    descriptor = None
    try:
        root = Path(worker["directory"])
        if (
            root.is_symlink()
            or root.parent.resolve() != Path(gettempdir()).resolve()
            or not root.name.startswith(f"tidebench-research-{token}-")
        ):
            return False, "Previous worker ownership path is not trustworthy."
        # A recorded PID which no longer exists proves that specific child exited.
        # A live/reused PID does not: require its random-token kernel-lock evidence.
        if root.exists():
            info = root.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_mode & 0o077
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                return False, "Previous worker ownership directory permissions are not trustworthy."
        if process_exists(worker.get("pid")) is False:
            return True, "Recorded worker PID no longer exists."
        descriptor = os.open(root / "ownership.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        ):
            return False, "Previous worker ownership lock is not trustworthy."
        lock_identity = (info.st_dev, info.st_ino)
        if lock_identity != (worker.get("lock_device"), worker.get("lock_inode")):
            return False, "Previous worker kernel lock identity does not match its launch intent."
        identity_path = root / "ownership.json"
        identity_fd = os.open(identity_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(identity_fd) as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > 4096
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                return False, "Previous worker identity evidence is not trustworthy."
            identity = json.load(stream)
        if (
            not isinstance(identity, dict)
            or identity.get("token") != token
            or identity.get("owner_pid") != worker.get("owner_pid")
            or (identity.get("lock_device"), identity.get("lock_inode")) != lock_identity
            or not isinstance(identity.get("pid"), int)
            or (worker.get("pid") is not None and identity["pid"] != worker["pid"])
        ):
            return False, "Previous worker identity does not match its launch intent."
        # The identity file is written only after the worker acquires this lock.
        # Its descriptor is never closed voluntarily: acquisition now proves exit,
        # including a reused PID, rather than an unstarted worker's unlocked file.
        ownership_lock(descriptor)
        return True, "Matching owned-worker kernel lock was released on exit."
    except BlockingIOError:
        return False, "Previous owned worker has not exited."
    except (OSError, ValueError, TypeError):
        return False, "Previous worker launch or exit evidence is unavailable; cancellation remains pending."
    finally:
        if descriptor is not None:
            os.close(descriptor)


def run_isolated(kind, payload, *, timeout=900, memory_mb=256, progress=None, cancelled=None, lifecycle=None):
    if isinstance(cancelled, ResearchCancellation):
        cancelled.check()
    worker_token = new_id()
    with TemporaryDirectory(prefix=f"tidebench-research-{worker_token}-") as directory:
        root = Path(directory)
        request = root / "input.json"
        request.write_text(
            dumps(
                {
                    "kind": kind,
                    "payload": payload,
                    "memory_mb": memory_mb,
                    "cpu_seconds": timeout + 5,
                    "ownership_token": worker_token,
                }
            )
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
        if isinstance(cancelled, ResearchCancellation):
            cancelled.check()
        lock = root / "ownership.lock"
        lock.write_bytes(b"0")
        lock.chmod(0o600)
        lock_info = lock.stat()
        evidence = {
            "token": worker_token,
            "owner_pid": os.getpid(),
            "directory": str(root.resolve()),
            "state": "launching",
            "launch_requested_at": now_ms(),
            "lock_device": lock_info.st_dev,
            "lock_inode": lock_info.st_ino,
        }
        if lifecycle:
            lifecycle(evidence)
        try:
            if isinstance(cancelled, ResearchCancellation):
                cancelled.check()
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
        except BaseException:
            if lifecycle:
                lifecycle(evidence | {"state": "not_launched", "verified_at": now_ms()})
            raise
        reader = None
        try:
            evidence = evidence | {"state": "spawned", "pid": process.pid, "spawned_at": now_ms()}
            if lifecycle:
                lifecycle(evidence)
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
            while True:
                check_cancellation(cancelled)
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
            check_cancellation(cancelled)
            return json.loads(raw)
        finally:
            if process.poll() is None:
                # A failed kill cannot be treated as proof that computation stopped.
                # Waiting below still owns the child until its actual exit.
                with suppress(OSError):
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
            # Do not acknowledge a terminal cancellation before the child is reaped.
            process.wait()
            if reader is not None and reader.ident is not None:
                reader.join(timeout=1)
            process.stdout.close()
            if lifecycle:
                lifecycle(
                    evidence
                    | {
                        "state": "exited",
                        "pid": process.pid,
                        "returncode": process.returncode,
                        "reaped_at": now_ms(),
                    }
                )


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
    root = Path(directory)
    request = json.loads((root / "input.json").read_text())
    if request.get("ownership_token"):
        descriptor = os.open(root / "ownership.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        lock_info = os.fstat(descriptor)
        ownership_lock(descriptor)
        _ownership_descriptors.append(descriptor)
        identity = root / "ownership.tmp"
        identity.write_text(
            dumps(
                {
                    "token": request["ownership_token"],
                    "pid": os.getpid(),
                    "owner_pid": parent_pid,
                    "lock_device": lock_info.st_dev,
                    "lock_inode": lock_info.st_ino,
                }
            )
        )
        identity.chmod(0o600)
        with identity.open("rb") as stream:
            os.fsync(stream.fileno())
        identity.replace(root / "ownership.json")
    if parent_pid is not None:
        watch_parent(parent_pid)
    import hashlib
    from decimal import Decimal, localcontext

    from .engine import ACCOUNTING_CONTEXT, Candle
    from .portfolio_research import simulate_portfolio
    from .pro_research import replay_research_snapshot

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
