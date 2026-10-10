"""Per-study cancellation with real SQLite, disposable workers and access controls."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_research import PortfolioInput
from tidebench.pro_api import ResearchInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.research_process import process_exists
from tidebench.store import dumps, encode

HOUR = 3_600_000
END = 1767225600000
START = END - 600 * HOUR


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    runtime = create_app(settings, MarketService(client=client)).state.professional
    yield runtime
    await runtime.stop()
    await client.aclose()


async def queued(runtime, kind, **changes):
    if kind == "single":
        job = runtime.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
        job = await runtime.catalog.run_job(job["id"])
        body = encode(ResearchInput(dataset_id=job["dataset_id"], **changes).model_dump())
        return runtime.create_run(body)
    packages = []
    for symbol in ("BTC-USDT", "ETH-USDT"):
        package = runtime.packages.create_package(symbol, "1H", START, END, "example")
        package = await runtime.packages.run_package(package["id"])
        assert package["ready"]
        packages.append(package)
    body = encode(
        PortfolioInput.model_validate(
            {
                "name": "Cancellation study",
                "hypothesis": "Shared capital allocation must survive conservative costs.",
                "legs": [{"package_id": p["id"], "weight": ".5"} for p in packages],
                "max_daily_loss_pct": 50,
            }
        ).model_dump()
    )
    return runtime.portfolios.create(body, "researcher", {})


def get(runtime, identifier, kind):
    return runtime.run(identifier) if kind == "single" else runtime.portfolios.get(identifier)


async def compute(runtime, identifier, kind):
    if kind == "single":
        await runtime.perform_run(identifier)
    else:
        await runtime.offload(runtime.portfolios.compute, identifier)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_queued_cancel_never_starts_and_is_idempotent(runtime, kind, monkeypatch):
    run = await queued(runtime, kind)
    with runtime.store.read() as conn:
        facts_before = [dict(r) for r in conn.execute("SELECT * FROM research_trials")]
    cancelled = runtime.cancel_research(run["id"], "researcher", kind)
    assert cancelled["status"] == "cancelled" and cancelled["result"] is None
    assert cancelled["manifest"]["cancellation"]["state"] == "confirmed"
    assert runtime.cancel_research(run["id"], "another-actor", kind) == cancelled
    monkeypatch.setattr(runtime, "compute", lambda *a: pytest.fail("Cancelled run started"))
    if kind == "portfolio":
        monkeypatch.setattr(
            runtime.portfolios, "capture", lambda *a: pytest.fail("Cancelled portfolio captured")
        )
    await compute(runtime, run["id"], kind)
    assert get(runtime, run["id"], kind) == cancelled
    with runtime.store.read() as conn:
        assert [dict(r) for r in conn.execute("SELECT * FROM research_trials")] == facts_before
    events = [e for e in runtime.store.events("example") if e["kind"].startswith("pro.research_cancel")]
    assert len(events) == 2
    assert all(e["details"]["actor"] == "researcher" for e in events)
    assert not runtime.research_cancelled.is_set()
    assert not runtime.store.risk("example")["kill_switch"]
    assert not runtime.book.risk("example")["halted"]
    with pytest.raises(PlatformError) as error:
        runtime.cancel_research("missing", "researcher", kind)
    assert error.value.status == 404


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_running_cancel_waits_for_actual_child_cleanup_and_targets_only_one(runtime, kind, monkeypatch):
    run = await queued(runtime, kind)
    sibling = await queued(runtime, kind)
    children, roots = [], []
    launched, continue_launch = threading.Event(), threading.Event()
    original = subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        roots.append(Path(args[0][3]))
        launched.set()
        assert continue_launch.wait(10)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    task = asyncio.create_task(compute(runtime, run["id"], kind))
    try:
        assert await asyncio.to_thread(launched.wait, 10)
        assert children[0].poll() is None
        requested = runtime.cancel_research(run["id"], "cancel-operator", kind)
        assert requested["status"] == "running"
        assert requested["manifest"]["cancellation"]["state"] == "requested"
        assert "confirmed_at" not in requested["manifest"]["cancellation"]
        assert not task.done()
        assert get(runtime, sibling["id"], kind)["status"] == "queued"
        assert not runtime.research_cancelled.is_set()
        continue_launch.set()
        await asyncio.wait_for(task, 10)
    finally:
        continue_launch.set()
        await asyncio.gather(task, return_exceptions=True)
    result = get(runtime, run["id"], kind)
    assert result["status"] == "cancelled" and result["result"] is None
    assert result["manifest"]["cancellation"]["state"] == "confirmed"
    assert all(child.poll() is not None for child in children)
    assert all(not root.exists() for root in roots)
    assert get(runtime, sibling["id"], kind)["status"] == "queued"
    assert not runtime.book.positions("example")


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_cancel_during_progress_reader_setup_still_reaps_owned_child(runtime, kind, monkeypatch):
    run = await queued(runtime, kind)
    children = []
    original_launch, original_start = subprocess.Popen, threading.Thread.start

    def launch(*args, **kwargs):
        child = original_launch(*args, **kwargs)
        children.append(child)
        runtime.cancel_research(run["id"], "cancel-operator", kind)
        return child

    def start(thread):
        if thread.name == "research-progress":
            raise RuntimeError("Injected reader setup failure")
        return original_start(thread)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(threading.Thread, "start", start)
    await compute(runtime, run["id"], kind)
    result = get(runtime, run["id"], kind)
    assert result["status"] == "cancelled"
    assert result["manifest"]["cancellation"]["state"] == "confirmed"
    assert children and all(child.poll() is not None for child in children)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_nonisolated_engine_observes_cancel_at_progress_and_cannot_commit(runtime, kind, monkeypatch):
    runtime.settings.research_process_isolation = False
    run = await queued(runtime, kind)
    original = runtime.research_checkpoint
    observations = []

    def checkpoint(identifier, study_kind, **changes):
        if changes.get("progress", 0) > 0.15 and not observations:
            observations.append(runtime.cancel_research(identifier, "researcher", study_kind))
        return original(identifier, study_kind, **changes)

    monkeypatch.setattr(runtime, "research_checkpoint", checkpoint)
    await compute(runtime, run["id"], kind)
    assert observations and observations[0]["status"] == "running"
    result = get(runtime, run["id"], kind)
    assert result["status"] == "cancelled" and result["result"] is None


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_cancel_completion_and_error_terminal_fences(runtime, kind):
    first = await queued(runtime, kind)
    second = await queued(runtime, kind)
    table = runtime.research_table(kind)
    with runtime.store.write() as conn:
        conn.execute(f"UPDATE {table} SET status='running' WHERE id IN (?,?)", (first["id"], second["id"]))

    def finish(identifier):
        payload = dumps({"metrics": {"total_return_pct": "1"}})
        manifest = dict((first if identifier == first["id"] else second)["manifest"] or {})
        manifest["result_hash"] = hashlib.sha256(payload.encode()).hexdigest()
        if kind == "single":
            runtime.complete_run(identifier, "example", payload, {}, manifest)
        else:
            with runtime.store.write() as conn:
                if runtime.research_can_complete(conn, identifier, kind):
                    pointer = runtime.artifacts.put(conn, payload)
                    conn.execute(
                        "UPDATE portfolio_runs SET status='completed',result=?,manifest=? WHERE id=?",
                        (dumps(pointer), dumps(manifest), identifier),
                    )

    requested = runtime.cancel_research(first["id"], "first-actor", kind)
    assert requested["status"] == "running"
    finish(first["id"])
    runtime.fail_research(first["id"], kind, RuntimeError("Late worker error"))
    cancelled = get(runtime, first["id"], kind)
    assert cancelled["status"] == "cancelled" and cancelled["result"] is None
    finish(second["id"])
    completed = get(runtime, second["id"], kind)
    assert completed["status"] == "completed" and completed["result"]
    assert runtime.cancel_research(second["id"], "late-actor", kind) == completed
    runtime.fail_research(second["id"], kind, RuntimeError("Late worker error"))
    assert get(runtime, second["id"], kind) == completed
    third = await queued(runtime, kind)
    with runtime.store.write() as conn:
        conn.execute(f"UPDATE {table} SET status='running' WHERE id=?", (third["id"],))
    runtime.cancel_research(third["id"], "error-race-actor", kind)
    runtime.fail_research(third["id"], kind, RuntimeError("Compute failed while cancellation was requested"))
    assert get(runtime, third["id"], kind)["status"] == "cancelled"


async def test_completion_transaction_wins_concurrent_cancel_without_relabeling(runtime, monkeypatch):
    run = await queued(runtime, "single")
    with runtime.store.write() as conn:
        conn.execute("UPDATE pro_runs SET status='running' WHERE id=?", (run["id"],))
    entered, release, cancelling = threading.Event(), threading.Event(), threading.Event()
    original = runtime.artifacts.put

    def persist(conn, payload):
        entered.set()
        assert release.wait(10)
        return original(conn, payload)

    monkeypatch.setattr(runtime.artifacts, "put", persist)
    payload = dumps({"metrics": {"total_return_pct": "1"}})
    manifest = {"result_hash": hashlib.sha256(payload.encode()).hexdigest()}
    completing = asyncio.create_task(
        runtime.offload(runtime.complete_run, run["id"], "example", payload, {}, manifest)
    )

    def cancel():
        cancelling.set()
        return runtime.cancel_research(run["id"], "late-actor")

    try:
        assert await asyncio.to_thread(entered.wait, 10)
        cancel_task = asyncio.create_task(runtime.offload(cancel))
        assert await asyncio.to_thread(cancelling.wait, 10)
        release.set()
        await asyncio.wait_for(completing, 10)
        result = await asyncio.wait_for(cancel_task, 10)
        assert result["status"] == "completed" and result["result"]
        assert "cancellation" not in result["manifest"]
    finally:
        release.set()
        await asyncio.gather(completing, return_exceptions=True)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_restart_confirms_durable_request_and_never_revives_cancelled(runtime, kind, monkeypatch):
    running = await queued(runtime, kind)
    queued_cancel = await queued(runtime, kind)
    assert runtime.claim_research(running["id"], kind, 0.05)
    runtime.cancel_research(running["id"], "researcher", kind)
    runtime.cancel_research(queued_cancel["id"], "researcher", kind)
    await runtime.stop()
    recovered = ProfessionalRuntime(runtime.store, runtime.market, runtime.settings)

    async def idle():
        await asyncio.Event().wait()

    for method in ("jobs_loop", "catalog_loop", "execution_loop", "strategy_loop", "backup_loop"):
        monkeypatch.setattr(recovered, method, idle)
    await recovered.start()
    try:
        for identifier in (running["id"], queued_cancel["id"]):
            result = get(recovered, identifier, kind)
            assert result["status"] == "cancelled"
            assert result["manifest"]["cancellation"]["state"] == "confirmed"
        backup = recovered.backups.create("Cancelled studies retain their evidence")
        assert recovered.backups.verify(backup["id"])["verified"]
    finally:
        await recovered.stop()


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_recovery_keeps_launch_before_record_unknown_instead_of_trusting_unlocked_file(
    runtime, kind, monkeypatch
):
    run = await queued(runtime, kind)
    assert runtime.claim_research(run["id"], kind, 0.05)
    token = "a" * 32
    directory = Path(tempfile.mkdtemp(prefix=f"tidebench-research-{token}-"))
    try:
        lock = directory / "ownership.lock"
        lock.write_bytes(b"0")
        lock.chmod(0o600)
        lock_info = lock.stat()
        runtime.record_research_worker(
            run["id"],
            kind,
            {
                "token": token,
                "owner_pid": os.getpid(),
                "directory": str(directory.resolve()),
                "state": "launching",
                "lock_device": lock_info.st_dev,
                "lock_inode": lock_info.st_ino,
            },
        )
        runtime.cancel_research(run["id"], "researcher", kind)
        runtime.recover_research_cancellations()
        pending = get(runtime, run["id"], kind)
        assert pending["status"] == "running"
        assert pending["manifest"]["cancellation"]["state"] == "requested"
        assert pending["manifest"]["cancellation"]["recovery"]["state"] == "blocked"
        assert "Cancellation pending recovery" in pending["error"]
        assert "replacement study" in pending["error"]
        assert "confirmed_at" not in pending["manifest"]["cancellation"]
        # A PID can be reused, and permission errors are not evidence of exit.
        runtime.record_research_worker(
            run["id"],
            kind,
            {
                "token": token,
                "owner_pid": os.getpid(),
                "pid": os.getpid(),
                "directory": str(directory.resolve()),
                "state": "spawned",
                "lock_device": lock_info.st_dev,
                "lock_inode": lock_info.st_ino,
            },
        )
        import tidebench.research_process as process_module

        monkeypatch.setattr(process_module, "process_exists", lambda _: None)
        identity = directory / "ownership.json"
        identity.write_text("[]")
        identity.chmod(0o600)
        runtime.recover_research_cancellations()
        assert get(runtime, run["id"], kind)["manifest"]["cancellation"]["state"] == "requested"
        identity.write_text(dumps({"token": "b" * 32, "owner_pid": os.getpid(), "pid": os.getpid()}))
        runtime.recover_research_cancellations()
        assert get(runtime, run["id"], kind)["status"] == "running"
        lock.chmod(0o666)
        runtime.recover_research_cancellations()
        assert "not trustworthy" in get(runtime, run["id"], kind)["error"]
        lock.chmod(0o600)
        # A symlink must never be used as ownership evidence or signal authority.
        lock.unlink()
        target = directory / "unrelated-file"
        target.write_bytes(b"0")
        target.chmod(0o600)
        lock.symlink_to(target)
        runtime.recover_research_cancellations()
        assert get(runtime, run["id"], kind)["status"] == "running"
    finally:
        shutil.rmtree(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX fault injection pauses the owned child")
@pytest.mark.parametrize("crash_before_pid_record", [False, True])
async def test_abrupt_owner_death_and_fast_restart_require_actual_owned_child_exit(
    runtime, monkeypatch, crash_before_pid_record
):
    run = await queued(
        runtime,
        "single",
        mode="grid",
        options={"grid": {"fast": list(range(5, 13)), "slow": list(range(20, 28))}},
    )
    # Force the real portable parent watcher path on every POSIX test host. Pausing
    # its process makes the restart window deterministic rather than sleep-based.
    child_code = """
import sys
import tidebench.research_process as rp
original = rp.watch_parent
def portable(pid):
    platform = sys.platform
    sys.platform = 'darwin'
    try:
        original(pid)
    finally:
        sys.platform = platform
rp.watch_parent = portable
rp.worker(sys.argv[1], int(sys.argv[2]))
"""
    owner_code = """
import asyncio, subprocess, sys, time
from pathlib import Path
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store
import tidebench.research_process as rp
settings = Settings(data_dir=Path(sys.argv[1]), worker_enabled=False, _env_file=None)
runtime = ProfessionalRuntime(Store(settings.database), MarketService(), settings)
original = subprocess.Popen
def launch(command, **kwargs):
    return original([command[0], '-c', sys.argv[4], command[3], command[4]], **kwargs)
rp.subprocess.Popen = launch
if sys.argv[3] == 'True':
    record = runtime.record_research_worker
    def record_with_gap(identifier, kind, evidence):
        if evidence['state'] == 'spawned':
            while True:
                time.sleep(1)
        return record(identifier, kind, evidence)
    runtime.record_research_worker = record_with_gap
asyncio.run(runtime.perform_run(sys.argv[2]))
"""
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            owner_code,
            str(runtime.settings.data_dir),
            run["id"],
            str(crash_before_pid_record),
            child_code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid, directory, recovered = None, None, None
    try:
        for _ in range(500):
            current = runtime.run(run["id"])
            evidence = (current["manifest"] or {}).get("owned_worker")
            if evidence:
                directory = Path(evidence["directory"])
                identity_path = directory / "ownership.json"
                if identity_path.exists():
                    child_pid = json.loads(identity_path.read_text())["pid"]
                    if crash_before_pid_record or evidence["state"] == "spawned":
                        break
            await asyncio.sleep(0.01)
        assert child_pid is not None, (current, owner.poll())
        assert process_exists(child_pid) is True
        os.kill(child_pid, signal.SIGSTOP)
        owner.kill()
        owner.wait(timeout=5)
        assert process_exists(child_pid) is True
        requested = runtime.cancel_research(run["id"], "restart-operator")
        assert requested["status"] == "running"
        if crash_before_pid_record:
            assert "pid" not in requested["manifest"]["owned_worker"]
        recovered = ProfessionalRuntime(runtime.store, runtime.market, runtime.settings)

        async def idle():
            await asyncio.Event().wait()

        for method in ("catalog_loop", "execution_loop", "strategy_loop", "backup_loop"):
            monkeypatch.setattr(recovered, method, idle)
        await recovered.start()
        pending = recovered.run(run["id"])
        assert pending["status"] == "running"
        assert pending["manifest"]["cancellation"]["state"] == "requested"
        assert pending["manifest"]["cancellation"]["recovery"]["state"] == "blocked"
        assert "has not exited" in pending["error"]
        assert "confirmed_at" not in pending["manifest"]["cancellation"]
        # Replacing the pathname cannot impersonate release of the kernel lock
        # that the still-live worker holds on the original inode.
        lock = directory / "ownership.lock"
        original_lock = directory / "ownership.original"
        lock.replace(original_lock)
        lock.write_bytes(b"0")
        lock.chmod(0o600)
        recovered.recover_research_cancellations()
        assert recovered.run(run["id"])["manifest"]["cancellation"]["state"] == "requested"
        assert "kernel lock identity" in recovered.run(run["id"])["error"]
        lock.unlink()
        original_lock.replace(lock)
        os.kill(child_pid, signal.SIGCONT)
        for _ in range(500):
            result = recovered.run(run["id"])
            if result["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "cancelled", result
        assert result["manifest"]["cancellation"]["state"] == "confirmed"
        assert result["manifest"]["cancellation"]["recovery"]["state"] == "resolved"
        assert result["result"] is None
        assert recovered.backups.verify(recovered.backups.create("Recovered cancelled ownership")["id"])[
            "verified"
        ]
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)
        owner.stderr.close()
        if child_pid is not None:
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(child_pid, sig)
                except ProcessLookupError:
                    pass
        if recovered is not None:
            await recovered.stop()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
async def test_worker_identity_is_mutable_control_only_and_replay_remains_deterministic(runtime, kind):
    first = await queued(runtime, kind)
    await compute(runtime, first["id"], kind)
    first = get(runtime, first["id"], kind)
    assert first["status"] == "completed", first["error"]
    assert first["manifest"]["owned_worker"]["state"] == "exited"
    assert "execution_owner" not in dumps(first["result"])
    assert "owned_worker" not in dumps(first["result"])
    if kind == "single":
        replay = runtime.replay(first["id"])
    else:
        frozen = runtime.artifacts.resolve(first["manifest"]["input_artifact"])
        assert "owned_worker" not in dumps(frozen) and "execution_owner" not in dumps(frozen)
        replay = runtime.portfolios.replay(first["id"], "researcher")
    assert "owned_worker" not in (replay["manifest"] or {})
    await compute(runtime, replay["id"], kind)
    replay = get(runtime, replay["id"], kind)
    assert replay["status"] == "completed", replay["error"]
    assert replay["manifest"]["replay_verified"]
    assert replay["manifest"]["result_hash"] == first["manifest"]["result_hash"]
    assert replay["manifest"]["owned_worker"]["token"] != first["manifest"]["owned_worker"]["token"]


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("backup_phase", ["before_admission", "queued", "running"])
async def test_earlier_schema8_backup_cannot_resurrect_later_cancelled_research(
    runtime, kind, backup_phase, monkeypatch
):
    if backup_phase == "before_admission":
        backup = runtime.backups.create("Before this research attempt")
    run = await queued(runtime, kind)
    if backup_phase == "queued":
        backup = runtime.backups.create("Queued research before cancellation")
    if backup_phase == "running":
        launched, release = threading.Event(), threading.Event()
        original = subprocess.Popen

        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            launched.set()
            assert release.wait(10)
            return child

        monkeypatch.setattr(subprocess, "Popen", launch)
        task = asyncio.create_task(compute(runtime, run["id"], kind))
        try:
            assert await asyncio.to_thread(launched.wait, 10)
            backup = runtime.backups.create("Actual running research before cancellation")
            assert get(runtime, run["id"], kind)["status"] == "running"
            assert runtime.cancel_research(run["id"], "restore-cancel-actor", kind)["status"] == "running"
            release.set()
            await asyncio.wait_for(task, 10)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    else:
        runtime.cancel_research(run["id"], "restore-cancel-actor", kind)
    cancelled = get(runtime, run["id"], kind)
    assert cancelled["status"] == "cancelled"
    responsibility = cancelled["manifest"]["cancellation"]
    await runtime.stop()
    assert runtime.backups.verify(backup["id"])["database_schema"] == 8
    restored = runtime.backups.restore(backup["id"])
    assert restored["retained_research_cancellations"][kind] == 1
    recovered = ProfessionalRuntime(runtime.store, runtime.market, runtime.settings)

    async def idle():
        await asyncio.Event().wait()

    for method in ("catalog_loop", "execution_loop", "strategy_loop", "backup_loop"):
        monkeypatch.setattr(recovered, method, idle)
    monkeypatch.setattr(
        recovered, "compute", lambda *a: pytest.fail("Restored cancelled research recomputed")
    )
    monkeypatch.setattr(
        recovered.portfolios, "capture", lambda *a: pytest.fail("Restored cancelled portfolio captured")
    )
    await recovered.start()
    try:
        await asyncio.sleep(0.05)
        result = get(recovered, run["id"], kind)
        assert result["status"] == "cancelled" and result["result"] is None
        assert result["manifest"]["cancellation"] == responsibility
        assert result["config"] == run["config"]
        with runtime.store.read() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM research_trials WHERE run_id=?", (run["id"],)).fetchone()[
                    0
                ]
                == 1
            )
        assert runtime.backups.verify(runtime.backups.create("Retained cancellation verified")["id"])[
            "verified"
        ]
    finally:
        await recovered.stop()


async def test_restore_rejects_cancellation_run_identity_conflict_before_replacing_workspace(runtime):
    run = await queued(runtime, "single")
    backup = runtime.backups.create("Original immutable research configuration")
    runtime.cancel_research(run["id"], "researcher")
    with runtime.store.write() as conn:
        conn.execute(
            "UPDATE pro_runs SET config=? WHERE id=?", (dumps(run["config"] | {"fee_bps": "99"}), run["id"])
        )
        conn.execute("UPDATE pro_accounts SET cash='9999' WHERE source='example'")
    with pytest.raises(PlatformError) as error:
        runtime.backups.restore(backup["id"])
    assert error.value.code == "governance_conflict"
    assert runtime.run(run["id"])["status"] == "cancelled"
    with runtime.store.read() as conn:
        assert conn.execute("SELECT cash FROM pro_accounts WHERE source='example'").fetchone()[0] == "9999"


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.parametrize("isolated", [False, True])
async def test_actual_dead_owner_without_worker_launch_allows_safe_recovery(runtime, kind, isolated):
    run = await queued(runtime, kind)
    owner_code = """
import sys,time
from pathlib import Path
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store
settings = Settings(data_dir=Path(sys.argv[1]), worker_enabled=False, research_process_isolation=sys.argv[4]=='True', _env_file=None)
runtime = ProfessionalRuntime(Store(settings.database), MarketService(), settings)
runtime.claim_research(sys.argv[2], sys.argv[3], .05)
print('claimed', flush=True)
time.sleep(30)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code, str(runtime.settings.data_dir), run["id"], kind, str(isolated)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert await asyncio.to_thread(owner.stdout.readline) == "claimed\n"
        assert get(runtime, run["id"], kind)["status"] == "running"
        runtime.cancel_research(run["id"], "researcher", kind)
        runtime.recover_research_cancellations()
        assert get(runtime, run["id"], kind)["status"] == "running"
        owner.kill()
        owner.wait(timeout=5)
        runtime.recover_research_cancellations()
        assert get(runtime, run["id"], kind)["status"] == "cancelled"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)
        owner.stdout.close()


async def test_cancelled_final_single_evaluation_retains_one_use_and_trial(runtime):
    version = runtime.registry.create_project(
        "Unseen interval",
        "Fixed parameters must survive a final untouched market window.",
        {"strategy": {"kind": "sma_cross", "fast": 5, "slow": 20}},
        "researcher",
    )["version"]
    job = runtime.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
    job = await runtime.catalog.run_job(job["id"])
    holdout = runtime.governance.create(
        {
            "name": "Final window",
            "strategy_version_id": version["id"],
            "dataset_id": job["dataset_id"],
            "start_ts": END - 100 * HOUR,
            "end_ts": END,
            "benchmark": "Cash after costs",
            "rejection_plan": "Reject if conservative after-cost return and drawdown violate the fixed criteria.",
        },
        "researcher",
    )
    backup = runtime.backups.create("Sealed single holdout before consumption and cancellation")
    body = encode(
        ResearchInput.model_validate(
            holdout["plan"]["test_config"] | {"holdout_id": holdout["id"]}
        ).model_dump()
    )
    run = runtime.create_run(body)
    with runtime.store.read() as conn:
        before = [dict(r) for r in conn.execute("SELECT * FROM research_consumptions")]
    runtime.cancel_research(run["id"], "researcher")
    await runtime.stop()
    runtime.backups.restore(backup["id"])
    with pytest.raises(PlatformError) as error:
        runtime.create_run(body)
    assert error.value.code == "holdout_consumed"
    with runtime.store.read() as conn:
        assert [dict(r) for r in conn.execute("SELECT * FROM research_consumptions")] == before
    trials = runtime.governance.trials(version["project_id"])
    assert trials["recorded_attempts"] == trials["primary_evaluations"] == 1
    assert runtime.governance.list()[0]["status"] == "consumed"


async def test_cancelled_final_portfolio_evaluation_retains_consumption_and_attempt(runtime):
    project = runtime.portfolio_registry.create_project(
        "Untouched basket",
        "Fixed joint allocations must survive a final untouched window.",
        {
            "capital_pct": "20",
            "rebalance_bars": 4,
            "legs": [
                {"inst_id": symbol, "weight": ".5", "strategy": {"kind": "buy_hold"}}
                for symbol in ("BTC-USDT", "ETH-USDT")
            ],
        },
        "researcher",
    )
    packages = []
    for symbol in ("BTC-USDT", "ETH-USDT"):
        package = runtime.packages.create_package(symbol, "1H", END - 100 * HOUR, END, "example")
        packages.append(await runtime.packages.run_package(package["id"]))
    body = {
        "name": "Fixed final basket",
        "portfolio_version_id": project["version"]["id"],
        "package_ids": [p["id"] for p in packages],
        "test_start": END - 40 * HOUR,
        "test_end": END,
        "warmup_bars": 20,
        "rejection_plan": "Reject if fixed after-cost return, drawdown or solvency criteria fail; do not retune.",
        "criteria": {
            "min_return_vs_cash_pct": "-100",
            "max_drawdown_pct": "100",
            "min_trades": 1,
            "min_observations": 20,
            "require_zero_debt": True,
        },
    }
    preview = runtime.protocol.preview(body, "researcher")
    holdout = runtime.protocol.seal(
        {"preview_id": preview["id"], "preview_hash": preview["plan_hash"]}, "researcher"
    )
    backup = runtime.backups.create("Sealed portfolio holdout before consumption and cancellation")
    run = runtime.protocol.evaluate(holdout["id"], holdout["plan_hash"], "researcher")
    with runtime.store.read() as conn:
        before = [dict(r) for r in conn.execute("SELECT * FROM research_consumptions")]
    runtime.cancel_research(run["id"], "researcher", "portfolio")
    await runtime.stop()
    runtime.backups.restore(backup["id"])
    same = runtime.protocol.evaluate(holdout["id"], holdout["plan_hash"], "researcher")
    assert same["id"] == run["id"] and same["status"] == "cancelled"
    with runtime.store.read() as conn:
        assert [dict(r) for r in conn.execute("SELECT * FROM research_consumptions")] == before
    trials = runtime.protocol.trials(project["id"])
    assert trials["recorded_attempts"] == trials["primary_evaluations"] == 1


@pytest.mark.parametrize(
    "role,allowed",
    [("admin", True), ("researcher", True), ("trader", True), ("viewer", False), ("risk_operator", False)],
)
def test_cancel_api_requires_csrf_and_research_role_and_audits_actor(tmp_path, role, allowed):
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None), MarketService())
    with TestClient(app) as client:
        setup = client.post(
            "/api/v1/auth/setup", json={"username": "admin-qa", "password": "synthetic-admin-password"}
        )
        assert setup.status_code == 200, setup.text
        client.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        if role != "admin":
            assert (
                client.post(
                    "/api/v1/auth/users",
                    json={"username": "cancel-actor", "password": "synthetic-actor-password", "role": role},
                ).status_code
                == 201
            )
            assert client.post("/api/v1/auth/logout").status_code == 200
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "cancel-actor", "password": "synthetic-actor-password"},
            )
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        runtime = app.state.professional
        for kind, segment in (("single", "runs"), ("portfolio", "portfolios")):
            identifier = client.portal.call(queued, runtime, kind)["id"]
            path = f"/api/v1/pro/research/{segment}/{identifier}/cancel"
            csrf = client.headers.pop("X-CSRF-Token")
            assert client.post(path).status_code == 403
            client.headers["X-CSRF-Token"] = csrf
            response = client.post(path)
            assert response.status_code == (202 if allowed else 403), response.text
            assert get(runtime, identifier, kind)["status"] == ("cancelled" if allowed else "queued")
            if allowed:
                # Runtime/PID/token control evidence is not a writable API input.
                forged = get(runtime, identifier, kind)["config"] | {
                    "execution_owner": {"state": "drained"},
                    "owned_worker": {"state": "exited"},
                }
                assert client.post(f"/api/v1/pro/research/{segment}", json=forged).status_code == 422
                assert response.json()["manifest"]["cancellation"]["actor"] == (
                    "admin-qa" if role == "admin" else "cancel-actor"
                )
        events = [e for e in runtime.store.events("example") if e["kind"].startswith("pro.research_cancel")]
        assert len(events) == (4 if allowed else 0)
        assert not runtime.store.risk("example")["kill_switch"]
        assert not runtime.book.risk("example")["halted"]
