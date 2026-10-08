"""Economic lineage, release admission and change races using real storage/engines."""

import copy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_api import ResearchInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, dumps, encode
from tidebench.strategy_registry import StrategyDefinition

END = 1767225600000
HOUR = 3_600_000


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected network"))
    )
    result = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    yield result
    await result.stop()
    await client.aclose()


def definition(**changes):
    return StrategyDefinition.model_validate(
        {"strategy": {"kind": "sma_cross", "fast": 5, "slow": 20, "allocation": ".1"}, **changes}
    ).record()


def project(runtime):
    return runtime.registry.create_project(
        "Trend hypothesis",
        "A persistent price trend may survive conservative transaction costs.",
        definition(),
        "researcher",
    )


async def research(runtime, mode="single", options=None, bound=True):
    version = project(runtime)["version"] if bound else None
    job = runtime.catalog.create_job("BTC-USDT", "trade", "1H", END - 400 * HOUR, END, "example")
    job = await runtime.catalog.run_job(job["id"])
    config = encode(
        ResearchInput(
            dataset_id=job["dataset_id"],
            strategy=definition()["strategy"],
            liquidation_fee_bps=50,
            strategy_version_id=version["id"] if version else None,
            mode=mode,
            options=options or {},
        ).model_dump()
    )
    run = runtime.create_run(config)
    await runtime.perform_run(run["id"])
    run = runtime.run(run["id"])
    assert run["status"] == "completed", run.get("error")
    return run


def approve(runtime, run, selection="single", **changes):
    preview = runtime.registry.preview_release(runtime, run["id"], selection)
    body = {
        "run_id": run["id"],
        "selection": selection,
        "preview_hash": preview["preview_hash"],
        "review": "Reviewed input coverage, costs and the local execution boundary.",
        "acknowledgements": preview["required_acknowledgements"],
        **changes,
    }
    return runtime.registry.approve_release(runtime, body, "trader")


def test_versions_are_immutable_content_addressed_and_parented(runtime):
    original = project(runtime)
    first = original["version"]
    updated = definition()
    updated["strategy"]["fast"] = 8
    second = runtime.registry.create_version(
        original["id"], first["hypothesis"], updated, "researcher", first["id"]
    )
    duplicate = runtime.registry.create_version(
        original["id"], first["hypothesis"], updated, "researcher", first["id"]
    )
    assert duplicate["id"] == second["id"]
    assert second["revision"] == 2 and second["parent_id"] == first["id"]
    assert runtime.registry.version(first["id"]) == first
    assert first["content_hash"] != second["content_hash"]
    assert len(runtime.registry.project(original["id"])["versions"]) == 2


def test_concurrent_version_creation_serializes_revision_identity(runtime):
    original = project(runtime)
    first = original["version"]

    def create(fast):
        config = definition()
        config["strategy"]["fast"] = fast
        return runtime.registry.create_version(
            original["id"], first["hypothesis"], config, "researcher", first["id"]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(create, [6, 7, 8, 9]))
    assert {row["revision"] for row in versions} == {2, 3, 4, 5}
    assert len({row["id"] for row in versions}) == 4


def test_tampered_version_cannot_be_used(runtime):
    first = project(runtime)["version"]
    changed = copy.deepcopy(first["definition"])
    changed["strategy"]["fast"] = 10
    with runtime.store.write() as conn:
        conn.execute("UPDATE strategy_versions SET definition=? WHERE id=?", (dumps(changed), first["id"]))
    with pytest.raises(PlatformError, match="integrity"):
        runtime.registry.version(first["id"])


async def test_research_binding_rejects_parameter_and_timeframe_drift(runtime):
    run = await research(runtime)
    changed = copy.deepcopy(run["config"])
    changed["strategy"]["fast"] = 8
    with pytest.raises(PlatformError, match="must match"):
        runtime.create_run(changed)
    assert run["config"]["strategy_version_id"]
    assert run["manifest"]["config"]["strategy_version_id"] == run["config"]["strategy_version_id"]


async def test_selected_research_is_promoted_without_parameter_reentry(runtime):
    run = await research(runtime)
    release = approve(runtime, run)
    assert release["status"] == "approved"
    assert release["strategy_version_id"] == run["config"]["strategy_version_id"]
    activated = runtime.registry.activate_release(runtime, release["id"], "trader")
    repeated = runtime.registry.activate_release(runtime, release["id"], "trader")
    assert activated == repeated and activated["status"] == "deployed"
    deployed = runtime.deployments()[0]
    assert deployed["config"]["strategy"] == release["config"]["strategy"]
    assert deployed["config"]["research_result_hash"] == run["manifest"]["result_hash"]
    assert deployed["config"]["release_id"] == release["id"]
    assert len(runtime.deployments()) == 1
    await runtime.evaluate(deployed)


async def test_grid_release_preserves_explicit_candidate_and_requires_ack(runtime):
    run = await research(runtime, "grid", {"grid": {"fast": [5, 8], "slow": [20]}})
    preview = runtime.registry.preview_release(runtime, run["id"], "experiment-2")
    assert preview["definition"]["strategy"]["fast"] == 8
    assert set(preview["required_acknowledgements"]) == {"in_sample_selection", "no_oos_evidence"}
    with pytest.raises(PlatformError, match="acknowledge"):
        approve(runtime, run, "experiment-2", acknowledgements=[])
    release = approve(runtime, run, "experiment-2")
    selected = runtime.registry.version(release["strategy_version_id"])
    assert selected["definition"]["strategy"]["fast"] == 8
    assert selected["parent_id"] == run["config"]["strategy_version_id"]


async def test_oos_release_uses_training_selection_and_does_not_choose_test_winner(runtime):
    run = await research(
        runtime,
        "walk_forward",
        {"train_bars": 160, "test_bars": 80, "step_bars": 80, "grid": {"fast": [5, 8], "slow": [20]}},
    )
    fold = run["result"]["folds"][-1]
    preview = runtime.registry.preview_release(runtime, run["id"], fold["id"])
    assert preview["definition"]["strategy"] == fold["selected_strategy"]
    assert preview["selection_scope"] == "training_selected_independent_oos_fold"
    assert "no_oos_evidence" not in preview["required_acknowledgements"]
    with pytest.raises(PlatformError, match="existing OOS fold"):
        runtime.registry.preview_release(runtime, run["id"], "best-test")


async def test_risk_change_invalidates_preview_and_approved_release(runtime):
    run = await research(runtime)
    release = approve(runtime, run)
    previous = release["preview"]
    runtime.book.set_risk("example", {"fee_bps": "20"}, "risk-operator")
    with pytest.raises(PlatformError, match="changed"):
        runtime.registry.activate_release(runtime, release["id"], "trader")
    with pytest.raises(PlatformError, match="changed"):
        runtime.registry.approve_release(
            runtime,
            {
                "run_id": run["id"],
                "selection": "single",
                "preview_hash": previous["preview_hash"],
                "acknowledgements": ["no_oos_evidence"],
                "review": "Reviewed before the policy changed.",
            },
            "trader",
        )
    current = runtime.registry.preview_release(runtime, run["id"])
    assert current["cost_differences"] == [{"field": "fee_bps", "research": "10", "execution": "20"}]
    assert "execution_cost_difference" in current["required_acknowledgements"]
    assert not runtime.deployments()


async def test_order_transaction_rechecks_approved_policy_after_runtime_check(runtime, monkeypatch):
    run = await research(runtime)
    release = approve(runtime, run)
    runtime.registry.activate_release(runtime, release["id"], "trader")
    original = runtime.book.submit

    def changed_policy(*args, **kwargs):
        runtime.book.set_risk("example", {"fee_bps": "20"}, "risk-operator")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.book, "submit", changed_policy)
    # Ensure this fixture asks for an entry irrespective of the trend at its last bar.
    deployed = runtime.deployments()[0]
    with pytest.raises(PlatformError, match="Approved risk policy changed"):
        await runtime.execute_strategy_intent(
            deployed["config"], deployed["id"], END - HOUR, END, Decimal(".1")
        )
    assert not runtime.book.positions("example")


async def test_corrupted_research_or_changed_implementation_blocks_release(runtime):
    run = await research(runtime)
    with runtime.store.write() as conn:
        changed = copy.deepcopy(run["result"])
        changed["result"]["metrics"]["total_return_pct"] = 999999
        conn.execute("UPDATE pro_runs SET result=? WHERE id=?", (dumps(changed), run["id"]))
    with pytest.raises(PlatformError, match="integrity"):
        runtime.registry.preview_release(runtime, run["id"])
    with runtime.store.write() as conn:
        manifest = copy.deepcopy(run["manifest"])
        manifest["research_implementation"]["code_fingerprint"] = "0" * 64
        conn.execute(
            "UPDATE pro_runs SET result=?,manifest=? WHERE id=?",
            (dumps(run["result"]), dumps(manifest), run["id"]),
        )
    with pytest.raises(PlatformError, match="current implementation"):
        runtime.registry.preview_release(runtime, run["id"])


async def test_legacy_research_gets_a_captured_version_at_release(runtime):
    run = await research(runtime, bound=False)
    release = approve(runtime, run)
    version = runtime.registry.version(release["strategy_version_id"])
    assert version["definition"]["strategy"] == release["config"]["strategy"]
    assert version["created_by"] == "trader"
    assert version["parent_id"] is None
