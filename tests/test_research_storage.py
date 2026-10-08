import copy
import gzip
import hashlib
from decimal import Decimal

import pytest
from tidebench.engine import Candle, EngineError, Instrument, StrategyConfig
from tidebench.platform import PlatformError
from tidebench.pro_research import ResearchConfig, replay_research_snapshot, run_research_plan
from tidebench.research_artifacts import ResearchArtifacts, project_result
from tidebench.research_budget import estimate_research_memory
from tidebench.store import Store, dumps

D = Decimal
HOUR = 3_600_000
SPOT = Instrument("BTC-USDT", "BTC", "USDT", D(".01"), D(".01"), D(".01"))


def plan(progress=None):
    candles = [Candle(i * HOUR, D(100 + i % 10), D(110), D(90), D(100 + i % 10), D(1)) for i in range(300)]
    return run_research_plan(
        candles,
        HOUR,
        SPOT,
        ResearchConfig(strategy=StrategyConfig(fast=5, slow=20)),
        mode="grid",
        options={"grid": {"fast": [5, 8, 10]}},
        progress=progress,
    )


def test_shared_inputs_projection_and_progress_do_not_change_the_financial_artifact():
    fractions = []
    result = plan(fractions.append)
    assert result == plan()
    assert fractions == sorted(fractions) and fractions[-1] == 1
    assert len(result["shared_inputs"]) == 1
    for experiment in result["experiments"]:
        snapshot = experiment["result"]["input_snapshot"]
        assert set(snapshot) == {"shared_input_hash", "config"}
        standalone = replay_research_snapshot(snapshot, result["shared_inputs"])
        assert standalone["input_hash"] == experiment["result"]["input_hash"]
        assert standalone["fills"] == experiment["result"]["fills"]
    projected = project_result(result, "experiment-2")
    assert "shared_inputs" not in projected
    assert "equity" not in projected["experiments"][0]["result"]
    assert projected["experiments"][1]["result"]["equity"] == result["experiments"][1]["result"]["equity"]
    assert projected["experiments"][0]["result"]["metrics"] == result["experiments"][0]["result"]["metrics"]
    altered = copy.deepcopy(result["shared_inputs"])
    next(iter(altered.values()))["trade_candles"][0]["close"] = "101"
    with pytest.raises(EngineError):
        replay_research_snapshot(result["experiments"][0]["result"]["input_snapshot"], altered)


def test_compressed_artifact_deduplicates_and_refuses_pointer_payload_tampering(tmp_path, monkeypatch):
    import tidebench.research_artifacts as module

    store = Store(tmp_path / "artifacts.sqlite3")
    artifacts = ResearchArtifacts(store)
    value = {"values": ["same value"] * 1000}
    with store.write() as conn:
        pointer = artifacts.put(conn, dumps(value))
        assert artifacts.put(conn, dumps(value)) == pointer
        assert conn.execute("SELECT COUNT(*) FROM research_artifacts").fetchone()[0] == 1
    assert artifacts.resolve(pointer) == value
    with pytest.raises(PlatformError, match="pointer"):
        artifacts.resolve(pointer | {"extra": 1})
    with store.write() as conn:
        conn.execute("UPDATE research_artifacts SET payload=?", (gzip.compress(b"{}"),))
    with pytest.raises(PlatformError, match="content check"):
        artifacts.resolve(pointer)
    raw = b" " * 2048
    identity = hashlib.sha256(raw).hexdigest()
    with store.write() as conn:
        conn.execute(
            "INSERT INTO research_artifacts VALUES(?,'gzip',?,?,0)", (identity, 100, gzip.compress(raw))
        )
    monkeypatch.setattr(module, "MAX_ARTIFACT_BYTES", 1024)
    with pytest.raises(PlatformError, match="content check"):
        artifacts.resolve({"artifact_hash": identity, "codec": "gzip", "raw_bytes": 100})


@pytest.mark.parametrize(
    "options",
    [
        {"train_fraction": "bad"},
        {"grid": {"fast": None}},
        {"grid": {"fast": []}},
        {"fee_bps": "bad"},
        {"train_bars": 2, "test_bars": 20, "step_bars": 1},
        {"train_bars": 50000},
    ],
)
def test_invalid_plan_rejected_at_admission_without_starting_work(options):
    with pytest.raises((EngineError, ValueError, TypeError)):
        estimate_research_memory(
            {"strategy": {}, "mode": "walk_forward", "options": options},
            {"bar": "1H", "start": 0},
            0,
            300 * HOUR,
        )
