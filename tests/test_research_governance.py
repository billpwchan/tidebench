"""Holdout access is transactional, one-use, alias-resistant and pre-registered."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_api import ResearchInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, encode

HOUR = 3_600_000
END = 1767225600000
START = END - 600 * HOUR


@pytest.fixture
async def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    version = runtime.registry.create_project(
        "Trend preregistration",
        "Persistent directional pressure may survive conservative execution costs.",
        {"strategy": {"kind": "sma_cross", "fast": 5, "slow": 20}},
        "researcher",
    )["version"]
    job = runtime.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
    job = await runtime.catalog.run_job(job["id"])
    yield runtime, version, job["dataset_id"]
    await runtime.stop()
    await client.aclose()


def seal(runtime, version, dataset, **changes):
    return runtime.governance.create(
        {
            "name": "Unseen final interval",
            "strategy_version_id": version["id"],
            "dataset_id": dataset,
            "start_ts": END - 100 * HOUR,
            "end_ts": END,
            "benchmark": "Cost-adjusted unlevered underlying",
            "rejection_plan": "Reject if after-cost return and drawdown are worse than the predefined benchmark; do not retune on this interval.",
            **changes,
        },
        "researcher",
    )


def evaluation(holdout):
    return encode(
        ResearchInput.model_validate(
            holdout["plan"]["test_config"] | {"holdout_id": holdout["id"]}
        ).model_dump()
    )


async def test_exact_one_use_evaluation_then_immutable_replay(setup):
    runtime, version, dataset = setup
    holdout = seal(runtime, version, dataset)
    run = runtime.create_run(evaluation(holdout))
    await runtime.perform_run(run["id"])
    assert runtime.run(run["id"])["status"] == "completed"
    assert runtime.governance.list()[0]["run_id"] == run["id"]
    with pytest.raises(PlatformError, match="already evaluated"):
        runtime.create_run(evaluation(holdout))
    replay = runtime.replay(run["id"])
    await runtime.perform_run(replay["id"])
    assert runtime.run(replay["id"])["manifest"]["replay_verified"]
    trials = runtime.governance.trials(version["project_id"])
    assert trials["run_count"] == 2 and trials["candidate_configurations"] == 2


async def test_cost_parameter_drift_cannot_consume_holdout(setup):
    runtime, version, dataset = setup
    holdout = seal(runtime, version, dataset)
    changed = evaluation(holdout) | {"fee_bps": "99"}
    with pytest.raises(PlatformError, match="exactly"):
        runtime.create_run(changed)
    assert runtime.governance.list()[0]["status"] == "sealed"


async def test_concurrent_evaluations_consume_only_once(setup):
    runtime, version, dataset = setup
    holdout = seal(runtime, version, dataset)

    def submit(_):
        try:
            return runtime.create_run(evaluation(holdout))["id"]
        except PlatformError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("holdout_consumed") == 1
    assert len(runtime.runs()) == 1


async def test_alias_and_warmup_cannot_bypass_reserved_window(setup):
    runtime, version, dataset = setup
    holdout = seal(runtime, version, dataset, end_ts=END - 50 * HOUR)
    alias = runtime.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
    alias = await runtime.catalog.run_job(alias["id"])
    assert alias["dataset_id"] != dataset
    config = encode(
        ResearchInput(
            dataset_id=alias["dataset_id"],
            start_ts=END - 40 * HOUR,
            end_ts=END,
            strategy_version_id=version["id"],
            strategy=version["definition"]["strategy"],
        ).model_dump()
    )
    with pytest.raises(PlatformError, match="reserved"):
        runtime.create_run(config)
    assert runtime.governance.list()[0]["status"] == "sealed"
    with runtime.store.write() as conn:
        conn.execute("UPDATE research_holdouts SET plan='{}' WHERE id=?", (holdout["id"],))
    with pytest.raises(PlatformError, match="integrity"):
        runtime.governance.list()


async def test_seen_interval_cannot_be_relabelled_as_independent_holdout(setup):
    runtime, version, dataset = setup
    config = encode(
        ResearchInput(
            dataset_id=dataset,
            strategy_version_id=version["id"],
            strategy=version["definition"]["strategy"],
            mode="grid",
            options={"grid": {"fast": [5, 8], "slow": [20]}},
        ).model_dump()
    )
    runtime.create_run(config)
    with pytest.raises(PlatformError, match="already accessed"):
        seal(runtime, version, dataset)
    trials = runtime.governance.trials(version["project_id"])
    assert trials["candidate_configurations"] == 2
