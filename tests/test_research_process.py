"""Financial parity and owned-process cleanup, including forced deadlines."""

import os
import select
import signal
import subprocess
import sys
import threading
import time
from decimal import Decimal

import pytest
from tidebench.engine import Candle, Instrument, StrategyConfig
from tidebench.platform import PlatformError
from tidebench.pro_research import ResearchConfig, json_safe, run_research_plan
from tidebench.research_process import run_isolated

D = Decimal
HOUR = 3_600_000


def payload():
    candles = [Candle(i * HOUR, D(100 + i % 10), D(110), D(90), D(100 + i % 10), D(1)) for i in range(300)]
    instrument = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".01"), D(".01"))
    config = ResearchConfig(strategy=StrategyConfig(fast=5, slow=20, stop_loss_pct=D(5)))
    return (
        candles,
        instrument,
        config,
        json_safe(
            {
                "snapshot": {
                    "trade_candles": candles,
                    "interval_ms": HOUR,
                    "instrument": instrument,
                    "instrument_type": "SPOT",
                    "config": config,
                    "mark_candles": None,
                    "funding_events": [],
                    "margin_tiers": [],
                    "provenance": {},
                },
                "plan": {"mode": "grid", "options": {"grid": {"fast": [5, 8]}}},
            }
        ),
    )


def test_disposable_worker_matches_direct_engine_and_excludes_workspace_environment(monkeypatch):
    candles, instrument, config, request = payload()
    monkeypatch.setenv("TIDEBENCH_API_TOKEN", "synthetic-test-token-not-a-real-secret")
    original = subprocess.Popen

    def launch(*args, **kwargs):
        assert "TIDEBENCH_API_TOKEN" not in kwargs["env"]
        assert "TIDEBENCH_DATA_DIR" not in kwargs["env"]
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    observed = []
    result = run_isolated("single", request, timeout=10, progress=observed.append)
    expected = run_research_plan(
        candles, HOUR, instrument, config, mode="grid", options=request["plan"]["options"]
    )
    assert result == expected and observed


@pytest.mark.parametrize("failure", ["deadline", "cancelled", "worker_failure"])
def test_worker_failure_never_leaves_a_child_process_running(monkeypatch, failure):
    _, _, _, request = payload()
    children = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    event = threading.Event()
    if failure == "cancelled":
        event.set()
    with pytest.raises(PlatformError) as error:
        run_isolated(
            "unsupported" if failure == "worker_failure" else "single",
            request,
            timeout=0.001 if failure == "deadline" else 10,
            cancelled=event,
        )
    assert (
        error.value.code
        == {
            "deadline": "research_deadline",
            "cancelled": "research_cancelled",
            "worker_failure": "research_worker_failed",
        }[failure]
    )
    assert children and all(child.poll() is not None for child in children)


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX process state to observe an orphan")
def test_abrupt_server_death_terminates_owned_worker(tmp_path):
    ready = tmp_path / "owner-installed"
    child_code = (
        "import os,time; from pathlib import Path; "
        "from tidebench.research_process import watch_parent; "
        f"watch_parent(os.getppid(), interval=.05); Path({str(ready)!r}).touch(); time.sleep(30)"
    )
    owner_code = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "print(child.pid,flush=True); time.sleep(30)"
    )
    owner = subprocess.Popen([sys.executable, "-c", owner_code], stdout=subprocess.PIPE, text=True)
    child_pid = None
    try:
        child_pid = int(owner.stdout.readline())
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        owner.kill()
        owner.wait(timeout=5)
        # The worker inherits this write end. EOF proves both processes exited,
        # without relying on permission to inspect unrelated system processes.
        readable, _, _ = select.select([owner.stdout], [], [], 3)
        assert readable, "Research worker survived abrupt owner death"
        assert owner.stdout.read() == ""
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=5)
        owner.stdout.close()
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
