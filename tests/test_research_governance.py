"""Holdout access is transactional, one-use, alias-resistant and pre-registered."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_research import PortfolioInput
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
    assert trials["recorded_attempts"] == 2
    assert trials["primary_evaluations"] == 1 and trials["replay_attempts"] == 1
    assert trials["distinct_configurations"] == 1
    assert trials["legacy_attempts"] == trials["evidence_unavailable"] == 0


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


async def different_interval_dataset(runtime, bar, *, start=END - 200 * HOUR, end=END):
    job = runtime.catalog.create_job("BTC-USDT", "trade", bar, start, end, "example")
    completed = await runtime.catalog.run_job(job["id"])
    assert completed["status"] == "completed", completed
    return completed["dataset_id"]


async def five_minute_portfolio(runtime, *, start=END - 120 * HOUR, end=END):
    packages = []
    for symbol in ("BTC-USDT", "ETH-USDT"):
        package = runtime.packages.create_package(symbol, "5m", start, end, "example")
        package = await runtime.packages.run_package(package["id"])
        assert package["ready"], package
        packages.append(package)
    return encode(
        PortfolioInput.model_validate(
            {
                "name": "Cross-interval portfolio",
                "hypothesis": "Market-time protection must also cover a differently aggregated portfolio leg.",
                "legs": [{"package_id": package["id"], "weight": ".5"} for package in packages],
            }
        ).model_dump()
    )


async def test_hourly_seal_blocks_five_minute_single_access_without_version_binding(setup):
    runtime, version, dataset = setup
    holdout = seal(runtime, version, dataset, end_ts=END - 50 * HOUR)
    finer = await different_interval_dataset(runtime, "5m")
    # Even a selected interval after the seal reads it through 2000 prior 5m bars.
    config = encode(ResearchInput(dataset_id=finer, start_ts=END - 40 * HOUR, end_ts=END).model_dump())
    with pytest.raises(PlatformError) as error:
        runtime.create_run(config)
    assert error.value.code == "holdout_reserved"
    assert not runtime.runs()
    assert runtime.governance.list()[0]["id"] == holdout["id"]
    assert runtime.governance.list()[0]["status"] == "sealed"


async def test_hourly_seal_blocks_five_minute_portfolio_leg(setup):
    runtime, version, dataset = setup
    seal(runtime, version, dataset)
    config = await five_minute_portfolio(runtime)
    with pytest.raises(PlatformError) as error:
        runtime.portfolios.create(config, "researcher")
    assert error.value.code == "holdout_reserved"
    assert not runtime.portfolios.list("example")
    assert runtime.governance.list()[0]["status"] == "sealed"


@pytest.mark.parametrize("warmup_only", [False, True])
async def test_prior_five_minute_access_prevents_hourly_relabelling(setup, warmup_only):
    runtime, version, dataset = setup
    finer = await different_interval_dataset(runtime, "5m")
    start = END - (40 if warmup_only else 80) * HOUR
    runtime.create_run(encode(ResearchInput(dataset_id=finer, start_ts=start, end_ts=END).model_dump()))
    with pytest.raises(PlatformError) as error:
        seal(runtime, version, dataset, end_ts=END - 50 * HOUR)
    assert error.value.code == "holdout_exposed"
    assert not runtime.governance.list()


async def test_prior_portfolio_access_prevents_hourly_relabelling(setup):
    runtime, version, dataset = setup
    runtime.portfolios.create(await five_minute_portfolio(runtime), "researcher")
    with pytest.raises(PlatformError) as error:
        seal(runtime, version, dataset)
    assert error.value.code == "holdout_exposed"
    assert not runtime.governance.list()


async def test_prior_warmup_uses_its_own_bar_and_does_not_overreserve(setup):
    runtime, version, dataset = setup
    finer = await different_interval_dataset(runtime, "5m", start=START)
    runtime.create_run(
        encode(ResearchInput(dataset_id=finer, start_ts=END - 10 * HOUR, end_ts=END).model_dump())
    )
    # The actual 2000 * 5m access starts about 177h before END. Using the new
    # hourly seal's interval would incorrectly claim 2000h of prior exposure.
    holdout = seal(runtime, version, dataset, start_ts=END - 500 * HOUR, end_ts=END - 450 * HOUR)
    assert holdout["status"] == "sealed"


async def test_existing_hourly_seal_cannot_be_reserved_again_at_five_minutes(setup):
    runtime, version, dataset = setup
    seal(runtime, version, dataset)
    finer = await different_interval_dataset(runtime, "5m")
    finer_version = runtime.registry.create_project(
        "Finer final interval",
        "Changing aggregation cannot create an independent final test on the same market and time.",
        {"bar": "5m", "strategy": {"kind": "sma_cross", "fast": 5, "slow": 20}},
        "researcher",
    )["version"]
    with pytest.raises(PlatformError) as error:
        seal(runtime, finer_version, finer)
    assert error.value.code == "holdout_overlap"
    assert len(runtime.governance.list()) == 1


async def test_single_trials_survive_restore_with_missing_run_evidence(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    runtime = create_app(settings, MarketService(client=client)).state.professional
    try:
        version = runtime.registry.create_project(
            "Retained single trials",
            "Restore must preserve the full extent of admitted research and replay attempts.",
            {"strategy": {"kind": "sma_cross", "fast": 5, "slow": 20}},
            "researcher",
        )["version"]
        job = runtime.catalog.create_job("BTC-USDT", "trade", "1H", START, END, "example")
        job = await runtime.catalog.run_job(job["id"])
        backup = runtime.backups.create()
        config = encode(
            ResearchInput(
                dataset_id=job["dataset_id"],
                strategy_version_id=version["id"],
                strategy=version["definition"]["strategy"],
                mode="grid",
                options={"grid": {"fast": [5, 8], "slow": [20]}},
            ).model_dump()
        )
        run = runtime.create_run(config)
        await runtime.perform_run(run["id"])
        replay = runtime.replay(run["id"])
        before = runtime.governance.trials(version["project_id"])
        assert before["run_count"] == 2 and before["candidate_configurations"] == 4
        assert before["primary_evaluations"] == before["replay_attempts"] == 1
        runtime.backups.restore(backup["id"])
        after = runtime.governance.trials(version["project_id"])
        for key in (
            "run_count",
            "recorded_attempts",
            "candidate_configurations",
            "primary_evaluations",
            "replay_attempts",
            "distinct_configurations",
        ):
            assert after[key] == before[key]
        assert after["evidence_unavailable"] == 2
        assert all(
            item["fact_protected"] and item["status"] == "evidence_unavailable" for item in after["items"]
        )
        assert {item["run_id"] for item in after["items"]} == {run["id"], replay["id"]}
        assert next(item for item in after["items"] if item["replay_of"])["root_run_id"] == run["id"]
    finally:
        await runtime.stop()
        await client.aclose()


async def test_single_trial_counts_union_legacy_without_duplicates_and_validate_facts(setup):
    runtime, version, dataset = setup
    config = encode(
        ResearchInput(
            dataset_id=dataset, strategy_version_id=version["id"], strategy=version["definition"]["strategy"]
        ).model_dump()
    )
    protected = runtime.create_run(config)
    legacy = runtime.create_run(config)
    with runtime.store.write() as conn:
        conn.execute("DELETE FROM research_trials WHERE run_id=?", (legacy["id"],))
        conn.execute(
            "UPDATE pro_runs SET status='failed',error='adverse computation failure' WHERE id=?",
            (protected["id"],),
        )
    evidence = runtime.governance.trials(version["project_id"])
    assert evidence["run_count"] == evidence["primary_evaluations"] == 2
    assert evidence["candidate_configurations"] == 2
    assert evidence["legacy_attempts"] == 1 and evidence["distinct_configurations"] == 1
    assert len({item["id"] for item in evidence["items"]}) == 2
    item = next(item for item in evidence["items"] if item["id"] == protected["id"])
    assert item["status"] == "failed" and item["error"] == "adverse computation failure"
    with runtime.store.write() as conn:
        conn.execute("UPDATE research_trials SET content_hash=? WHERE run_id=?", ("0" * 64, protected["id"]))
    with pytest.raises(PlatformError) as error:
        runtime.governance.trials(version["project_id"])
    assert error.value.code == "governance_integrity"
