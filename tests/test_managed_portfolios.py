"""Real database, real economic book: release, interrupted fills and safe recovery."""

import asyncio
import copy
import json
from decimal import Decimal as D

import httpx
import pytest
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import BackupService, PlatformError
from tidebench.portfolio_execution import execute_batch
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.portfolio_research import PortfolioInput
from tidebench.portfolio_targets import target_quantities
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, dumps, encode
from tidebench.strategy_registry import digest

END = 1767225600000
HOUR = 3600000


@pytest.fixture
async def runtime(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected external request"))
    )
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    r = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    yield r
    await r.stop()
    await client.aclose()


@pytest.fixture
async def schema8_runtime(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected external request"))
    )
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    r = create_app(settings, MarketService(client=client)).state.professional
    yield r
    await r.stop()
    await client.aclose()


async def research(r, *, research_policy=None, research_bars=72, **definition_changes):
    definition = PortfolioDefinition.model_validate(
        {
            "legs": [
                {"inst_id": s, "weight": ".5", "strategy": {"kind": "buy_hold"}}
                for s in ("BTC-USDT", "ETH-USDT")
            ],
            **definition_changes,
        }
    ).record()
    project = r.portfolio_registry.create_project(
        "Shared risk budget",
        "Both holdings compete for one cash budget under declared fees.",
        definition,
        "researcher",
    )
    inputs = []
    for leg in definition["legs"]:
        package = r.packages.create_package(leg["inst_id"], "1H", END - research_bars * HOUR, END, "example")
        package = await r.packages.run_package(package["id"])
        assert package["ready"]
        inputs.append({k: v for k, v in leg.items() if k != "inst_id"} | {"package_id": package["id"]})
    config = encode(
        PortfolioInput.model_validate(
            {
                "name": project["name"],
                "hypothesis": project["version"]["hypothesis"],
                "portfolio_version_id": project["version"]["id"],
                "legs": inputs,
                **{
                    k: definition[k]
                    for k in (
                        "mode",
                        "rebalance_bars",
                        "lookback",
                        "top_k",
                        "carry_threshold",
                        "capital_pct",
                        "failure_policy",
                        "max_residual_pct",
                        "execution_contract",
                        "risk_window",
                        "vol_target_pct",
                        "vol_floor_pct",
                        "covariance_shrinkage",
                        "correlation_stress",
                        "carry_window",
                        "carry_cost_settlements",
                        "carry_buffer_bps",
                        "carry_max_age_hours",
                    )
                },
                **(research_policy or {}),
            }
        ).model_dump()
    )
    tiers = {
        leg["inst_id"]: await r.catalog.get_margin_tiers(leg["inst_id"], "example")
        for leg in definition["legs"]
        if leg["inst_id"].endswith("-SWAP")
    }
    run = r.portfolios.create(config, "researcher", tiers)
    await r.offload(r.portfolios.compute, run["id"])
    # API integration fixtures run the real queue worker. It may claim the
    # request first; compute correctly returns rather than executing it twice.
    # Await that owner's terminal result instead of assuming this caller won.
    async with asyncio.timeout(15):
        while True:
            run = r.portfolios.get(run["id"])
            if run["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.025)
    assert run["status"] == "completed", run["error"]
    return run


def approve(r, run):
    preview = r.portfolio_releases.preview(run["id"])
    return r.portfolio_releases.approve(
        {
            "run_id": run["id"],
            "preview_hash": preview["preview_hash"],
            "review": "Reviewed shared capital, sequential fills and compensation failure risks.",
            "acknowledgements": preview["required_acknowledgements"],
        },
        "trader",
    )


async def activate(r, **changes):
    run = await research(r, **changes)
    release = approve(r, run)
    activated = r.portfolio_releases.activate(release["id"], "trader")
    assert r.portfolio_releases.activate(release["id"], "trader") == activated
    return r.managed_portfolios.get(activated["group_id"])


async def test_review_binds_complete_version_and_revalidates_ownership_and_risk(runtime):
    r = runtime
    run = await research(r)
    preview = r.portfolio_releases.preview(run["id"])
    assert preview["version_id"] == run["config"]["portfolio_version_id"]
    assert preview["required_acknowledgements"] == ["sequential_leg_risk", "no_oos_evidence"]
    release = approve(r, run)
    r.book.set_risk("example", {"max_order_notional": "2400"}, "trader")
    with pytest.raises(PlatformError, match="fresh"):
        r.portfolio_releases.activate(release["id"], "trader")
    assert not r.deployments()
    release = approve(r, run)
    activated = r.portfolio_releases.activate(release["id"], "trader")
    assert len(r.deployments()) == 2
    assert r.managed_portfolios.get(activated["group_id"])["status"] == "running"
    second_preview = r.portfolio_releases.preview(run["id"])
    assert "strategy_ownership" in second_preview["blockers"]


async def test_target_batch_has_common_cash_scale_valid_children_and_actual_orders(runtime):
    r = runtime
    group = await activate(r)
    await r.managed_portfolios.evaluate(group["id"])
    history = r.managed_portfolios.history(group["id"])
    assert len(history) == 1
    batch = history[0]
    assert batch["status"] == "completed", batch["error"]
    assert D(batch["additions"]["cash_scale"]) < 1
    assert len(batch["commands"]) >= 4
    assert all(c["status"] == "completed" and c["order"]["status"] == "filled" for c in batch["commands"])
    assert all(D(c["order"]["notional"]) <= 2500 for c in batch["commands"])
    assert {c["payload"]["inst_id"] for c in batch["commands"][:2]} == {"BTC-USDT", "ETH-USDT"}
    assert D(batch["residuals"]["capital_pct"]) < 2
    assert len(r.book.positions("example")) == 2
    assert all(p["last_bar"] == END - HOUR for p in r.deployments())
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.history(group["id"]) == history


async def test_committed_first_child_then_process_restart_never_repeats_or_resizes(runtime, monkeypatch):
    r = runtime
    group = await activate(r)
    submit = r.book.submit
    seen = []

    def crash(order, key, snapshots, actor):
        result = submit(order, key, snapshots, actor)
        seen.append(result)
        raise asyncio.CancelledError()

    monkeypatch.setattr(r.book, "submit", crash)
    with pytest.raises(asyncio.CancelledError):
        await r.managed_portfolios.evaluate(group["id"])
    assert len(r.book.orders("example")) == 1
    pending = r.managed_portfolios.history(group["id"])[0]
    frozen = copy.deepcopy(pending["additions"])
    first_key = seen[0]["id"]
    await r.stop()
    restored = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
    try:
        restored.clock.change(
            step_ms=1, expected_revision=restored.clock.status()["revision"], actor="trader"
        )
        await restored.managed_portfolios.evaluate(group["id"])
        finished = restored.managed_portfolios.history(group["id"])[0]
        assert finished["status"] == "completed", finished["error"]
        assert finished["additions"] == frozen
        assert sum(c["order"]["id"] == first_key for c in finished["commands"]) == 1
        assert len(restored.book.orders("example")) == len(finished["commands"])
        assert any(c["order"]["quote_ts"] > seen[0]["quote_ts"] for c in finished["commands"])
    finally:
        await restored.stop()


async def test_stopping_any_leg_stops_whole_group_and_blocks_remaining_children(runtime, monkeypatch):
    r = runtime
    group = await activate(r)
    submit = r.book.submit

    def stop_after_fill(order, key, snapshots, actor):
        result = submit(order, key, snapshots, actor)
        r.stop_deployment(group["manifest"]["legs"][0]["deployment_id"], "trader")
        return result

    monkeypatch.setattr(r.book, "submit", stop_after_fill)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.get(group["id"])["status"] == "stopped"
    assert all(p["status"] == "stopped" for p in r.deployments())
    assert len(r.book.orders("example")) == 1
    assert len(r.book.positions("example")) == 1
    assert r.managed_portfolios.history(group["id"])[0]["status"] == "canceled"


async def test_leg_rejection_compensates_filled_inventory_and_keeps_failure_evidence(runtime, monkeypatch):
    r = runtime
    group = await activate(r)
    submit = r.book.submit

    def reject_second(order, key, snapshots, actor):
        if not order["reduce_only"] and order["inst_id"] == "ETH-USDT":
            raise PlatformError("test_leg_rejected", "Second leg refused by actual risk admission.", 409)
        return submit(order, key, snapshots, actor)

    monkeypatch.setattr(r.book, "submit", reject_second)
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert batch["status"] == "compensated", batch["error"]
    assert not r.book.positions("example")
    assert r.managed_portfolios.get(group["id"])["status"] == "failed"
    assert any(c["phase"] == "compensate" and c["order"] for c in batch["commands"])
    assert any(c["phase"] == "add" and c["status"] == "canceled" for c in batch["commands"])
    r.monitor_conditions()
    assert any(i["kind"] == "managed_portfolio" for i in r.incidents.list())


async def test_compensation_failure_retains_residual_and_retries_after_recovery(runtime, monkeypatch):
    r = runtime
    group = await activate(r)
    submit = r.book.submit

    def fail(order, key, snapshots, actor):
        if order["reduce_only"] or order["inst_id"] == "ETH-USDT":
            raise PlatformError("test_unavailable", "Observed execution temporarily unavailable.", 409)
        return submit(order, key, snapshots, actor)

    monkeypatch.setattr(r.book, "submit", fail)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.get(group["id"])["status"] == "compensating"
    assert len(r.book.positions("example")) == 1
    assert "Compensation blocked" in r.managed_portfolios.history(group["id"])[0]["error"]
    monkeypatch.setattr(r.book, "submit", submit)
    await r.managed_portfolios.evaluate(group["id"])
    assert not r.book.positions("example")
    assert r.managed_portfolios.history(group["id"])[0]["status"] == "compensated"


async def test_missing_leg_history_prevents_all_orders_and_retry_uses_saved_leg_evidence(
    runtime, monkeypatch
):
    r = runtime
    group = await activate(r)
    original = r.history.prepare

    async def incomplete(deployment, end, implementation):
        if deployment["config"]["inst_id"] == "ETH-USDT":
            raise PlatformError("missing_bar", "One confirmed leg is missing.", 409)
        return await original(deployment, end, implementation)

    monkeypatch.setattr(r.history, "prepare", incomplete)
    with pytest.raises(PlatformError, match="missing"):
        await r.managed_portfolios.evaluate(group["id"])
    assert not r.book.orders("example") and not r.managed_portfolios.history(group["id"])
    monkeypatch.setattr(r.history, "prepare", original)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.history(group["id"])[0]["status"] == "completed"


async def test_real_process_exit_after_committed_fill_recovers_durable_commands(runtime):
    import os
    import sys
    from pathlib import Path

    r = runtime
    group = await activate(r)
    script = r"""
import asyncio, os, sys
from pathlib import Path
import httpx
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store
async def main():
    settings = Settings(data_dir=Path(sys.argv[1]), worker_enabled=False, _env_file=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(RuntimeError('No network')))) as client:
        runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
        original = runtime.book.submit
        def exit_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(23)
        runtime.book.submit = exit_after_commit
        await runtime.managed_portfolios.evaluate(sys.argv[2])
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(r.settings.data_dir),
        group["id"],
        cwd=Path(__file__).parents[1],
        env={k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "SYSTEMROOT", "PYTHONPATH"}},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
    assert process.returncode == 23, (stdout, stderr)
    assert len(r.book.orders("example")) == 1
    first = r.book.orders("example")[0]["id"]
    restarted = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
    try:
        await restarted.managed_portfolios.evaluate(group["id"])
        batch = restarted.managed_portfolios.history(group["id"])[0]
        assert batch["status"] == "completed", batch["error"]
        assert len(restarted.book.orders("example")) == len(batch["commands"])
        assert sum(c["order"]["id"] == first for c in batch["commands"]) == 1
    finally:
        await restarted.stop()


async def test_protective_partial_exit_supersedes_frozen_compensation_without_rewriting_it(
    runtime, monkeypatch
):
    r = runtime
    group = await activate(r)
    original = r.book.submit
    interrupted = []

    def protective_exit(order, key, snapshots, actor):
        if not order["reduce_only"] and order["inst_id"] == "ETH-USDT":
            raise PlatformError("test_rejection", "Second leg rejected.", 409)
        if order["reduce_only"] and not interrupted:
            interrupted.append(copy.deepcopy(order))
            meta = snapshots[order["inst_id"]]["instrument"]
            lot = D(meta["lot_size"])
            partial = (D(order["quantity"]) / 2 / lot).to_integral_value(rounding="ROUND_FLOOR") * lot
            original(order | {"quantity": str(partial)}, "protective-partial-001", snapshots, "risk-operator")
        return original(order, key, snapshots, actor)

    monkeypatch.setattr(r.book, "submit", protective_exit)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.get(group["id"])["status"] == "compensating"
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert batch["status"] == "compensated", batch["error"]
    commands = [c for c in batch["commands"] if c["phase"] == "compensate"]
    assert len(commands) == 2 and commands[0]["status"] == "superseded"
    assert commands[0]["payload"] == interrupted[0]
    assert D(commands[1]["payload"]["quantity"]) < D(commands[0]["payload"]["quantity"])
    assert not r.book.positions("example")


async def test_policy_drift_prevents_all_new_risk(runtime):
    r = runtime
    group = await activate(r)
    r.book.set_risk("example", {"slippage_bps": "6"}, "risk-operator")
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert batch["status"] == "compensated"
    assert "policy changed" in batch["error"]
    assert not r.book.orders("example")


async def test_managed_spot_swap_carry_uses_contract_units_prior_funding_and_group_exit(runtime):
    r = runtime
    # A full-equity hedged pair still carries two absolute BTC exposures.
    # Declare the broader gross-underlying policy in both study and execution.
    r.book.capital.set_policy("example", {"max_base_asset_gross_pct": "200"}, "risk-operator")
    group = await activate(
        r,
        mode="funding_carry",
        research_policy={"max_base_asset_gross_pct": "200"},
        carry_threshold="-.01",
        legs=[
            {"inst_id": "BTC-USDT", "weight": ".5", "strategy": {"kind": "buy_hold"}},
            {
                "inst_id": "BTC-USDT-SWAP",
                "weight": "-.5",
                "leverage": "2",
                "direction": "short_only",
                "strategy": {"kind": "buy_hold", "max_holding_bars": 1},
            },
        ],
    )
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert batch["status"] == "completed", batch["error"]
    assert batch["body"]["past_funding_rate"] is not None
    positions = r.book.positions("example")
    assert len(positions) == 2
    swap = next(p for p in positions if p["inst_id"].endswith("-SWAP"))
    assert D(swap["quantity"]) < 0 and D(swap["margin"]) > 0
    quote = batch["body"]["quotes"]["BTC-USDT-SWAP"]
    assert abs(abs(D(swap["quantity"])) * D(quote["instrument"]["ct_val"]) * D(quote["last"]) - D(5000)) < 10
    r.clock.change(step_ms=HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
    await r.managed_portfolios.evaluate(group["id"])
    exit_batch = r.managed_portfolios.history(group["id"])[0]
    assert exit_batch["status"] == "completed", exit_batch["error"]
    assert set(exit_batch["body"]["risk_exits"]) == {"BTC-USDT", "BTC-USDT-SWAP"}
    assert all(c["payload"]["reduce_only"] for c in exit_batch["commands"])
    assert not r.book.positions("example")
    assert r.book.contribution_report("example", {})["reconciled"]


async def test_managed_carry_actual_funding_reconciles_to_portfolio_contribution(runtime):
    r = runtime
    # A full-equity hedged pair still carries two absolute BTC exposures.
    # Declare the broader gross-underlying policy in both study and execution.
    r.book.capital.set_policy("example", {"max_base_asset_gross_pct": "200"}, "risk-operator")
    group = await activate(
        r,
        mode="funding_carry",
        research_policy={"max_base_asset_gross_pct": "200"},
        carry_threshold="-.01",
        legs=[
            {"inst_id": "BTC-USDT", "weight": ".5"},
            {"inst_id": "BTC-USDT-SWAP", "weight": "-.5", "leverage": "2", "direction": "short_only"},
        ],
    )
    await r.managed_portfolios.evaluate(group["id"])
    assert r.managed_portfolios.history(group["id"])[0]["status"] == "completed"
    r.clock.change(step_ms=8 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
    await r.execution_once()
    report = r.book.contribution_report("example", await r.snapshots_for("example"))
    account = r.book.account("example", await r.snapshots_for("example"))
    assert report["reconciled"]
    assert D(account["funding_paid"]) != 0
    assert D(report["totals"]["funding_paid"]) == D(account["funding_paid"])
    assert {o["owner"] for o in report["owners"]} == {"portfolio:" + group["id"]}


async def test_risk_policy_differences_require_explicit_review(runtime):
    r = runtime
    run = await research(r)
    r.book.set_risk("example", {"max_daily_loss_pct": 10}, "risk-operator")
    p = r.portfolio_releases.preview(run["id"])
    assert p["risk_differences"] and "execution_risk_difference" in p["required_acknowledgements"]
    with pytest.raises(PlatformError, match="Acknowledge"):
        r.portfolio_releases.approve(
            {
                "run_id": run["id"],
                "preview_hash": p["preview_hash"],
                "review": "Reviewed capital and execution risks.",
                "acknowledgements": [
                    a for a in p["required_acknowledgements"] if a != "execution_risk_difference"
                ],
            },
            "trader",
        )


async def test_history_limit_never_hides_active_scheduling_or_old_failure_incidents(runtime, monkeypatch):
    r = runtime
    group = await activate(r, capital_pct=70)
    other_run = await research(
        r,
        capital_pct=30,
        legs=[
            {"inst_id": symbol, "weight": ".1", "leverage": "2"}
            for symbol in ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        ],
    )
    # Real admission and stop transitions produce newer, valid history without
    # fills. Operational queries must not inherit the UI's 200-row history cap.
    for _ in range(201):
        release = approve(r, other_run)
        activated = r.portfolio_releases.activate(release["id"], "trader")
        r.managed_portfolios.stop(activated["group_id"], "trader")
    visible = r.managed_portfolios.list()
    assert len(visible) == 201
    assert visible[0]["id"] == group["id"]
    assert sum(item["status"] == "stopped" for item in visible) == 200
    assert {item["id"] for item in r.managed_portfolios.list("example")} == {item["id"] for item in visible}
    assert not r.managed_portfolios.list("okx")

    def history_is_not_an_operational_query(*args):
        raise AssertionError("Scheduling and monitoring must use complete operational queries")

    monkeypatch.setattr(r.managed_portfolios, "list", history_is_not_an_operational_query)
    seen = asyncio.Event()
    calls = []

    async def observe(identifier):
        calls.append(identifier)
        seen.set()

    monkeypatch.setattr(r.managed_portfolios, "evaluate", observe)
    for status in ("running", "compensating"):
        with r.store.write() as conn:
            conn.execute(
                "UPDATE managed_portfolios SET status=?,last_error='Unresolved execution failure' WHERE id=?",
                (status, group["id"]),
            )
        assert {item["id"] for item in r.managed_portfolios.active()} == {group["id"]}
        seen.clear()
        loop = asyncio.create_task(r.strategy_loop())
        try:
            await asyncio.wait_for(seen.wait(), 5)
            assert calls[-1] == group["id"]
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        r.monitor_conditions()
        assert any(
            item["kind"] == "managed_portfolio" and item["subject"] == group["id"]
            for item in r.incidents.list()
        )
    with r.store.write() as conn:
        conn.execute("UPDATE managed_portfolios SET status='failed' WHERE id=?", (group["id"],))
    assert not r.managed_portfolios.active()
    assert {item["id"] for item in r.managed_portfolios.attention()} == {group["id"]}
    r.monitor_conditions()
    assert any(
        item["kind"] == "managed_portfolio" and item["subject"] == group["id"] for item in r.incidents.list()
    )


@pytest.mark.parametrize("corruption", ["content_hash", "malformed_json", "identity_drift"])
async def test_one_corrupt_group_is_isolated_and_can_stop_without_losing_inventory(runtime, corruption):
    from tidebench.store import dumps
    from tidebench.strategy_registry import digest

    r = runtime
    bad = await activate(r, capital_pct="20")
    await r.managed_portfolios.evaluate(bad["id"])
    assert len(r.book.positions("example")) == 2
    good = await activate(
        r,
        capital_pct=70,
        legs=[
            {"inst_id": symbol, "weight": ".1", "leverage": "2"}
            for symbol in ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
        ],
    )
    damaged = copy.deepcopy(bad["manifest"])
    damaged["name"] = "Unverified altered name"
    if corruption == "identity_drift":
        damaged["id"] = "0" * 32
    raw = "{invalid-json" if corruption == "malformed_json" else dumps(damaged)
    stored_hash = digest(damaged) if corruption == "identity_drift" else bad["manifest_hash"]
    with r.store.write() as conn:
        conn.execute(
            "UPDATE managed_portfolios SET manifest=?,manifest_hash=? WHERE id=?",
            (raw, stored_hash, bad["id"]),
        )

    shown = {item["id"]: item for item in r.managed_portfolios.list()}
    assert shown[bad["id"]]["manifest"] is None
    assert shown[bad["id"]]["integrity_error"]["code"] == "portfolio_evidence_integrity"
    assert shown[good["id"]]["manifest"] == good["manifest"]
    assert r.managed_portfolios.get(bad["id"])["manifest"] is None
    assert all("manifest" not in item for item in r.managed_portfolios.active())

    loop = asyncio.create_task(r.strategy_loop())
    try:
        async with asyncio.timeout(10):
            while True:
                good_batches = r.managed_portfolios.history(good["id"])
                if (
                    good_batches
                    and good_batches[0]["status"] == "completed"
                    and r.managed_portfolios.get(bad["id"])["last_error"]
                ):
                    break
                assert not loop.done()
                await asyncio.sleep(0.01)
        assert not loop.done()
        assert r.managed_portfolios.get(bad["id"])["last_error"].startswith("Portfolio evidence integrity:")
        r.monitor_conditions()
        assert any(
            item["kind"] == "managed_portfolio" and item["subject"] == bad["id"]
            for item in r.incidents.list()
        )

        # Even a direct strategy economic command cannot add a fill while its
        # group evidence is damaged; cancellation isolation is not the guard.
        quotes = await r.snapshots_for("example")
        leg = bad["manifest"]["legs"][0]
        quantity = quotes[leg["inst_id"]]["instrument"]["min_size"]
        payload = {
            "source": "example",
            "inst_id": leg["inst_id"],
            "side": "buy",
            "quantity": quantity,
            "leverage": "1",
            "reduce_only": False,
            "margin_mode": "isolated",
            "order_type": "market",
        }
        before_orders = len(r.book.orders("example"))
        with pytest.raises(PlatformError) as rejected:
            r.book.submit(payload, "corrupted-group-must-not-add", quotes, "strategy:" + leg["deployment_id"])
        assert rejected.value.code == "portfolio_ownership_integrity"
        assert len(r.book.orders("example")) == before_orders

        positions_before = copy.deepcopy(r.book.positions("example"))
        stopped = r.managed_portfolios.stop(bad["id"], "risk-operator")
        assert stopped["status"] == "stopped" and stopped["manifest"] is None
        assert stopped["integrity_error"]["code"] == "portfolio_evidence_integrity"
        assert r.book.positions("example") == positions_before
        assert r.managed_portfolios.get(good["id"])["status"] == "running"
        assert all(
            deployment["status"]
            == ("stopped" if deployment["config"]["group_id"] == bad["id"] else "running")
            for deployment in r.deployments("example")
        )
        with r.store.read() as conn:
            assert (
                conn.execute("SELECT manifest FROM managed_portfolios WHERE id=?", (bad["id"],)).fetchone()[0]
                == raw
            )
        r.monitor_conditions()
        assert any(
            item["kind"] == "managed_portfolio" and item["subject"] == bad["id"]
            for item in r.incidents.list()
        )
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)


async def test_carry_asymmetric_loss_budget_scales_both_hedge_targets(runtime):
    r = runtime
    run = await research(
        r,
        mode="funding_carry",
        carry_threshold="-.01",
        capital_pct="40",
        legs=[
            {
                "inst_id": "BTC-USDT",
                "weight": ".5",
                "strategy": {"kind": "buy_hold", "stop_loss_pct": "5", "risk_per_trade_pct": ".1"},
            },
            {
                "inst_id": "BTC-USDT-SWAP",
                "weight": "-.5",
                "leverage": "2",
                "direction": "short_only",
                "strategy": {"kind": "buy_hold"},
            },
        ],
    )
    active_decisions = [d for d in run["result"]["decisions"] if D(d["weights"]["BTC-USDT"])]
    assert active_decisions
    for decision in active_decisions:
        weights = decision["weights"]
        assert D(weights["BTC-USDT"]) == D(weights["BTC-USDT-SWAP"]).copy_negate()
        assert D(weights["BTC-USDT"]) < D(".1")
    release = r.portfolio_releases.activate(approve(r, run)["id"], "trader")
    await r.managed_portfolios.evaluate(release["group_id"])
    batch = r.managed_portfolios.history(release["group_id"])[0]
    assert batch["status"] == "completed", batch["error"]
    weights = batch["body"]["weights"]
    assert D(weights["BTC-USDT"]) == D(weights["BTC-USDT-SWAP"]).copy_negate()
    assert D(weights["BTC-USDT"]) < D(".1")
    for symbol, target in batch["body"]["targets"].items():
        quote = batch["body"]["quotes"][symbol]
        unit = D(quote["instrument"]["ct_val"]) if symbol.endswith("-SWAP") else D(1)
        notional = abs(D(target)) * unit * D(quote["last"])
        assert D(170) < notional < D(200)
    assert len(r.book.positions("example")) == 2
    assert r.book.contribution_report("example", await r.snapshots_for("example"))["reconciled"]


async def test_other_market_opened_after_quote_enumeration_retries_without_compensation(runtime, monkeypatch):
    r = runtime
    group = await activate(r, capital_pct="20")
    outside = await r.quote_snapshot("example", "SOL-USDT")
    submit = r.book.submit
    injected = []

    def interleave(payload, key, quotes, actor, **kwargs):
        if not injected and not payload["reduce_only"] and key.startswith("portfolio:"):
            injected.append(key)
            # This independent economic mutation occurs after the managed command's
            # quote enumeration, exactly the concurrent-start race observed in soak.
            submit(
                {
                    "source": "example",
                    "inst_id": "SOL-USDT",
                    "side": "buy",
                    "quantity": "1",
                    "leverage": "1",
                    "reduce_only": False,
                    "order_type": "market",
                    "margin_mode": "isolated",
                },
                "concurrent-outside-market",
                {**quotes, "SOL-USDT": outside},
                "manual:other-trader",
            )
            assert "SOL-USDT" not in quotes
        return submit(payload, key, quotes, actor, **kwargs)

    monkeypatch.setattr(r.book, "submit", interleave)
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert injected
    assert batch["status"] == "completed", batch["error"]
    assert r.managed_portfolios.get(group["id"])["status"] == "running"
    assert all(c["phase"] != "compensate" for c in batch["commands"])
    commands = [c for c in batch["commands"] if c["phase"] == "add"]
    assert len(commands) == len({c["order_id"] for c in commands})
    assert any(o["status"] == "filled" and o["inst_id"] == "SOL-USDT" for o in r.book.orders("example"))


async def test_small_carry_rebalances_accumulate_visible_residuals_over_sixty_steps(runtime):
    r = runtime
    group = await activate(
        r,
        mode="funding_carry",
        capital_pct="8",
        carry_threshold="-.01",
        rebalance_bars=2,
        legs=[
            {"inst_id": "BTC-USDT", "weight": ".5"},
            {"inst_id": "BTC-USDT-SWAP", "weight": "-.5", "leverage": "2", "direction": "short_only"},
        ],
    )
    skips = []
    for index in range(61):
        if index:
            r.clock.change(step_ms=4 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
        await r.managed_portfolios.evaluate(group["id"])
        batch = r.managed_portfolios.history(group["id"], limit=1)[0]
        assert batch["status"] == "completed", (index, batch["error"], batch["commands"])
        assert r.managed_portfolios.get(group["id"])["status"] == "running"
        assert D(batch["residuals"]["capital_pct"]) <= D("2")
        skips.extend(batch["body"].get("reduction_skips", []))
        skips.extend(batch["additions"]["skipped"])
        for command in batch["commands"]:
            meta = batch["body"]["quotes"][command["payload"]["inst_id"]]["instrument"]
            assert D(command["payload"]["quantity"]) >= D(meta["min_size"])
    assert skips and all(row["code"] == "rebalance_minimum" for row in skips)
    assert all(row["status"] == "completed" for row in r.managed_portfolios.history(group["id"], limit=100))
    report = r.book.contribution_report("example", await r.snapshots_for("example"))
    assert report["reconciled"] and D(report["totals"]["funding_paid"]) != 0


async def test_deferred_minimum_rebalance_cannot_bypass_reviewed_residual_limit(runtime):
    r = runtime
    group = await activate(
        r,
        mode="funding_carry",
        # A two-bar cash study has no lagged funding sample or execution
        # failure; later observed forward history genuinely permits entry.
        # Its no-OOS limitation is explicitly acknowledged by approve().
        research_bars=2,
        capital_pct="8",
        carry_threshold="-.01",
        rebalance_bars=2,
        max_residual_pct=".01",
        legs=[
            {"inst_id": "BTC-USDT", "weight": ".5"},
            {"inst_id": "BTC-USDT-SWAP", "weight": "-.5", "leverage": "2", "direction": "short_only"},
        ],
    )
    for index in range(61):
        if index:
            r.clock.change(step_ms=4 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
        await r.managed_portfolios.evaluate(group["id"])
        if r.managed_portfolios.get(group["id"])["status"] != "running":
            break
    batch = r.managed_portfolios.history(group["id"], limit=1)[0]
    assert batch["status"] == "compensated", batch
    assert "reviewed residual limit" in batch["error"]
    assert D(batch["residuals"]["capital_pct"]) > D(".01")
    assert batch["body"]["reduction_skips"] or batch["additions"]["skipped"]
    assert not r.book.positions("example")
    assert r.book.contribution_report("example", {})["reconciled"]


async def test_changed_observation_parser_requires_review_before_new_group_commands(runtime, monkeypatch):
    from pathlib import Path

    from tidebench.provenance import research_identity

    run = await research(runtime)
    release = approve(runtime, run)
    group = runtime.portfolio_releases.activate(release["id"], "trader")["group_id"]
    await runtime.managed_portfolios.evaluate(group)
    before_orders = runtime.book.orders("example")
    before_positions = runtime.book.positions("example")
    assert {order["inst_id"] for order in before_orders} == {"BTC-USDT", "ETH-USDT"}
    assert len(before_positions) == 2
    clock = runtime.clock.status()
    runtime.clock.change(step_ms=HOUR, expected_revision=clock["revision"], actor="trader")
    original_read = Path.read_bytes

    def installed_upgrade(path):
        original = original_read(path)
        return (
            original + b"\n# independently installed parser revision\n"
            if path.name == "instrument_observations.py"
            else original
        )

    monkeypatch.setattr(Path, "read_bytes", installed_upgrade)
    runtime.engine_identity = research_identity()
    with pytest.raises(PlatformError) as blocked:
        await runtime.managed_portfolios.evaluate(group)
    assert blocked.value.code == "portfolio_implementation_changed"
    assert runtime.book.orders("example") == before_orders
    assert runtime.book.positions("example") == before_positions
    assert len(runtime.managed_portfolios.history(group)) == 1


async def test_forward_carry_uses_current_policy_four_fill_hurdle_and_records_nonadmission(runtime):
    r = runtime
    group = await activate(
        r,
        mode="funding_carry",
        carry_window=12,
        carry_threshold="-.01",
        carry_cost_settlements=21,
        carry_buffer_bps=1000,
        carry_max_age_hours=16,
        legs=[
            {"inst_id": symbol, "weight": weight, "strategy": {"kind": "buy_hold"}}
            for symbol, weight in [("BTC-USDT", ".3"), ("BTC-USDT-SWAP", "-.3")]
        ],
    )
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    evidence = batch["body"]["carry_evidence"]
    assert not evidence["allowed"] and evidence["reason"] == "cost_hurdle"
    assert D(evidence["round_trip_cost_bps"]) == 4 * (
        D(r.book.risk("example")["fee_bps"]) + D(r.book.risk("example")["slippage_bps"])
    )
    assert not r.book.positions("example")
    assert all(D(w) == 0 for w in batch["body"]["weights"].values())


async def test_risk_rotation_forward_uses_verified_causal_bars_and_restart_stable_batch(runtime):
    from tidebench.managed_portfolios import verified
    from tidebench.portfolio_risk import risk_momentum_weights

    r = runtime
    group = await activate(r, mode="risk_momentum", risk_window=20, lookback=20, top_k=2, vol_target_pct="10")
    await r.managed_portfolios.evaluate(group["id"])
    batch = r.managed_portfolios.history(group["id"])[0]
    assert batch["status"] == "completed", batch["error"]
    evidence = batch["body"]["risk_evidence"]
    assert evidence["sample_end"] == END
    definition = group["definition"] if "definition" in group else group["manifest"]["definition"]
    bars = {}
    with r.store.read() as conn:
        for symbol in evidence["symbols"]:
            rows = conn.execute(
                "SELECT body,content_hash FROM forward_bars WHERE inst_id=? ORDER BY ts", (symbol,)
            ).fetchall()
            bars[symbol] = [verified(row, "body", "content_hash") for row in rows]
    oracle = risk_momentum_weights(definition, definition["legs"], bars, END - HOUR, HOUR)
    assert oracle["covariance"] == evidence["covariance"]
    assert oracle["selected"] == evidence["selected"]
    assert D(evidence["modeled_vol_pct"]) <= D(10) + D("1e-40")
    assert D(evidence["stressed_vol_pct"]) <= D(10) + D("1e-40")
    orders = r.book.orders("example")
    r.managed_portfolios = type(r.managed_portfolios)(r)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.book.orders("example") == orders
    assert r.managed_portfolios.history(group["id"]) == [batch]


async def mixed_funding_groups(r, monkeypatch):
    """Real managed owners and fills; only publication/quote transport is controlled."""
    due = END + 2 * HOUR
    state = {"published": False, "network_failure": False, "prices": {}, "rate": ".001"}
    r.book.set_risk("example", {"fee_bps": "0", "slippage_bps": "0"}, "risk-operator")

    def install(runtime):
        original_quote = runtime.catalog.get_market_snapshot

        async def observed_quote(symbol, source):
            quote = await original_quote(symbol, source)
            at = runtime.clock.now()
            price = state["prices"].get(symbol, "100")
            return quote | {
                "ts": at,
                "mark_ts": at,
                "bid": price,
                "ask": price,
                "last": price,
                "mark": price,
                "funding_time": due,
                "next_funding_time": due + 8 * HOUR,
            }

        original_history = runtime.catalog.funding_history

        async def published_history(symbol, start, end, source):
            # Historical study inputs still come from the normal example catalog.
            if end <= END:
                return await original_history(symbol, start, end, source)
            if state["network_failure"]:
                raise OSError("Realized funding publication is temporarily unavailable")
            if not state["published"] or not start <= due < end:
                return []
            return [{"ts": due, "rate": D(state["rate"]), "mark_price": D("100"), "mark_ts": due}]

        monkeypatch.setattr(runtime.catalog, "get_market_snapshot", observed_quote)
        monkeypatch.setattr(runtime.catalog, "funding_history", published_history)

    install(r)
    spots = await activate(
        r,
        capital_pct="20",
        rebalance_bars=1,
        legs=[{"inst_id": symbol, "weight": ".4"} for symbol in ("BTC-USDT", "ETH-USDT")],
    )
    swaps = await activate(
        r,
        capital_pct="20",
        legs=[{"inst_id": symbol, "weight": ".5"} for symbol in ("BTC-USDT-SWAP", "SOL-USDT-SWAP")],
    )
    await r.managed_portfolios.evaluate(swaps["id"])
    assert r.managed_portfolios.history(swaps["id"])[0]["status"] == "completed"
    return spots, swaps, state, install


async def test_account_funding_wait_preserves_reductions_then_reconciles_other_group_publication(
    runtime, monkeypatch
):
    r = runtime
    spots, swaps, publication, _ = await mixed_funding_groups(r, monkeypatch)
    await r.managed_portfolios.evaluate(spots["id"])
    r.clock.change(step_ms=HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
    publication["prices"] = {"BTC-USDT": "80", "ETH-USDT": "120"}
    # Other-group realized history has a transport failure: this must not turn
    # valid protective reductions into compensation or infer a zero payment.
    publication["network_failure"] = True
    before = len(r.book.orders("example"))
    submit = r.book.submit

    def due_after_reduction(order, key, snapshots, actor):
        result = submit(order, key, snapshots, actor)
        if order["reduce_only"] and r.clock.now() == END + HOUR:
            r.clock.change(step_ms=HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
            r.book.observe(
                "example",
                {
                    symbol: quote | {"ts": END + 2 * HOUR, "mark_ts": END + 2 * HOUR}
                    for symbol, quote in snapshots.items()
                },
            )
        return result

    monkeypatch.setattr(r.book, "submit", due_after_reduction)
    await r.managed_portfolios.evaluate(spots["id"])
    monkeypatch.setattr(r.book, "submit", submit)
    batch = r.managed_portfolios.history(spots["id"])[0]
    assert batch["status"] == "reducing" and batch["additions"] is None
    assert batch["error"].startswith("[funding_pending] ")
    assert batch["error"] == r.managed_portfolios.get(spots["id"])["last_error"]
    assert r.managed_portfolios.get(spots["id"])["status"] == "running"
    reductions = r.book.orders("example")[: len(r.book.orders("example")) - before]
    assert reductions and all(o["reduce_only"] for o in reductions)
    obligations = r.book.deferred_funding.pending("example")
    assert {p["inst_id"] for p in obligations} == {"BTC-USDT-SWAP", "SOL-USDT-SWAP"}
    assert all(set(p["owners"]) == {"portfolio:" + swaps["id"]} for p in obligations)
    assert r.managed_portfolios.get(spots["id"])["attention"]["phase"] == "funding_pending"
    frozen = copy.deepcopy(batch["body"])
    commands = [(c["key"], c["payload"], c["order_id"]) for c in batch["commands"]]
    await r.managed_portfolios.evaluate(spots["id"])
    repeated = r.managed_portfolios.history(spots["id"])[0]
    assert repeated["body"] == frozen
    assert [(c["key"], c["payload"], c["order_id"]) for c in repeated["commands"]] == commands
    assert r.book.deferred_funding.pending("example") == obligations

    publication["network_failure"] = False
    publication["published"] = True
    await r.managed_portfolios.evaluate(spots["id"])
    finished = r.managed_portfolios.history(spots["id"])[0]
    assert finished["id"] == batch["id"] and finished["body"] == frozen
    assert finished["status"] == "completed" and finished["error"] is None, finished["error"]
    assert all(c["status"] == "completed" and c["error"] is None for c in finished["commands"])
    assert not r.book.deferred_funding.pending("example")
    account = r.book.account("example", await r.snapshots_for("example"))
    assert D(account["funding_paid"]) == D(2)
    report = r.book.contribution_report("example", await r.snapshots_for("example"))
    assert report["reconciled"]
    owners = {row["owner"]: row for row in report["owners"]}
    assert D(owners["portfolio:" + spots["id"]]["funding_paid"]) == 0
    assert D(owners["portfolio:" + swaps["id"]]["funding_paid"]) == D(2)


async def partially_filled_funding_wait(r, monkeypatch):
    spots, swaps, publication, install = await mixed_funding_groups(r, monkeypatch)
    submit = r.book.submit
    first = []

    def due_after_first_child(order, key, snapshots, actor):
        result = submit(order, key, snapshots, actor)
        if not order["reduce_only"] and not first:
            first.append(result)
            r.clock.change(step_ms=2 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
            # The independent account observer captures other-group obligations
            # after one actual child committed, before the next admission.
            r.book.observe(
                "example",
                {s: q | {"ts": END + 2 * HOUR, "mark_ts": END + 2 * HOUR} for s, q in snapshots.items()},
            )
        return result

    monkeypatch.setattr(r.book, "submit", due_after_first_child)
    await r.managed_portfolios.evaluate(spots["id"])
    monkeypatch.setattr(r.book, "submit", submit)
    batch = r.managed_portfolios.history(spots["id"])[0]
    assert batch["status"] == "adding" and batch["error"].startswith("[funding_pending] ")
    assert sum(c["status"] == "completed" for c in batch["commands"]) == 1
    assert any(c["status"] == "pending" for c in batch["commands"])
    assert not any(c["phase"] == "compensate" for c in batch["commands"])
    return spots, swaps, publication, install, batch, first[0]


async def test_partially_filled_funding_wait_restarts_and_resumes_original_children_once(
    runtime, monkeypatch
):
    r = runtime
    spots, _, publication, install, batch, first = await partially_filled_funding_wait(r, monkeypatch)
    frozen = copy.deepcopy(batch["additions"])
    commands = [(c["key"], c["payload"]) for c in batch["commands"]]
    before = r.book.orders("example")
    await r.stop()
    restored = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
    install(restored)
    try:
        await restored.managed_portfolios.evaluate(spots["id"])
        waiting = restored.managed_portfolios.history(spots["id"])[0]
        assert waiting["id"] == batch["id"] and waiting["additions"] == frozen
        assert [(c["key"], c["payload"]) for c in waiting["commands"]] == commands
        assert restored.book.orders("example") == before
        assert restored.managed_portfolios.get(spots["id"])["status"] == "running"
        publication["published"] = True
        restored.clock.change(
            step_ms=1, expected_revision=restored.clock.status()["revision"], actor="trader"
        )
        await restored.managed_portfolios.evaluate(spots["id"])
        finished = restored.managed_portfolios.history(spots["id"])[0]
        assert finished["status"] == "completed" and finished["error"] is None, finished["error"]
        assert finished["additions"] == frozen
        assert [(c["key"], c["payload"]) for c in finished["commands"]] == commands
        assert sum(c["order"]["id"] == first["id"] for c in finished["commands"]) == 1
        assert all(
            c["order"]["quote_ts"] > first["quote_ts"]
            for c in finished["commands"]
            if c["order"]["id"] != first["id"]
        )
        after = restored.book.orders("example")
        await restored.managed_portfolios.evaluate(spots["id"])
        # A newer clock may legitimately admit the next decision batch; every
        # order from the original frozen batch still appears exactly once.
        for committed in after:
            assert sum(order["id"] == committed["id"] for order in restored.book.orders("example")) == 1
        assert (
            next(b for b in restored.managed_portfolios.history(spots["id"]) if b["id"] == batch["id"])
            == finished
        )
        assert restored.book.contribution_report("example", await restored.snapshots_for("example"))[
            "reconciled"
        ]
    finally:
        await restored.stop()


async def test_stop_cancels_funding_wait_continuation_but_retains_inventory_and_guard(runtime, monkeypatch):
    r = runtime
    spots, swaps, publication, _, batch, first = await partially_filled_funding_wait(r, monkeypatch)
    original_positions = r.book.positions("example")
    stopped = r.managed_portfolios.stop(spots["id"], "risk-operator")
    assert stopped["status"] == "stopped"
    assert r.book.positions("example") == original_positions
    canceled = r.managed_portfolios.history(spots["id"])[0]
    assert canceled["status"] == "canceled" and canceled["additions"] == batch["additions"]
    assert all(c["status"] in {"completed", "canceled"} for c in canceled["commands"])
    assert next(c for c in canceled["commands"] if c["order_id"] == first["id"])["status"] == "completed"
    with r.store.read() as conn:
        commitment = conn.execute(
            "SELECT * FROM account_capital_commitments WHERE source='example' AND owner=?",
            ("portfolio:" + spots["id"],),
        ).fetchone()
    assert commitment["status"] == "retained"
    quotes = await r.snapshots_for("example")
    inventory = next(p for p in original_positions if p["inst_id"] == first["inst_id"])
    # The unchanged economic guard rejects new risk while allowing a fresh
    # protective reduction even though another owner has unpaid obligations.
    with pytest.raises(PlatformError) as rejected:
        r.book.submit(
            first["request"] if "request" in first else batch["commands"][0]["payload"],
            "still-blocked-new-risk",
            quotes,
            "risk-operator",
        )
    assert rejected.value.code == "funding_pending"
    protection = batch["commands"][0]["payload"] | {
        "side": "sell",
        "quantity": inventory["quantity"],
        "reduce_only": True,
    }
    r.book.submit(protection, "protect-stopped-waiting-owner", quotes, "risk-operator")
    assert r.book.deferred_funding.pending("example")
    with r.store.read() as conn:
        assert (
            conn.execute(
                "SELECT status FROM account_capital_commitments WHERE source='example' AND owner=?",
                ("portfolio:" + spots["id"],),
            ).fetchone()[0]
            == "released"
        )
    r.managed_portfolios.stop(swaps["id"], "risk-operator")
    for position in r.book.positions("example"):
        r.book.submit(
            {
                "source": "example",
                "inst_id": position["inst_id"],
                "side": "sell",
                "quantity": position["quantity"],
                "leverage": position["leverage"],
                "reduce_only": True,
                "order_type": "market",
                "margin_mode": "isolated",
            },
            "protect-stopped-funding-owner:" + position["inst_id"],
            quotes,
            "risk-operator",
        )
    assert not r.book.positions("example") and r.book.deferred_funding.pending("example")
    with r.store.read() as conn:
        assert (
            conn.execute(
                "SELECT status FROM account_capital_commitments WHERE source='example' AND owner=?",
                ("portfolio:" + swaps["id"],),
            ).fetchone()[0]
            == "retained"
        )
    orders = r.book.orders("example")
    publication["published"] = True
    await r.managed_portfolios.evaluate(spots["id"])
    assert r.book.orders("example") == orders
    assert r.managed_portfolios.history(spots["id"])[0]["status"] == "canceled"
    # Background account reconciliation still owns the other portfolio's due
    # evidence, including after a waiting consumer has been stopped.
    await r.execution_once()
    assert not r.book.deferred_funding.pending("example")
    assert r.managed_portfolios.get(swaps["id"])["status"] == "stopped"
    with r.store.read() as conn:
        assert (
            conn.execute(
                "SELECT status FROM account_capital_commitments WHERE source='example' AND owner=?",
                ("portfolio:" + swaps["id"],),
            ).fetchone()[0]
            == "released"
        )
    assert r.book.contribution_report("example", await r.snapshots_for("example"))["reconciled"]


async def test_unsettled_equity_before_target_freeze_waits_without_inventing_capital(runtime, monkeypatch):
    r = runtime
    spots, swaps, publication, _ = await mixed_funding_groups(r, monkeypatch)
    r.clock.change(step_ms=2 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
    publication["network_failure"] = True
    before = r.book.orders("example")
    await r.managed_portfolios.evaluate(spots["id"])
    assert r.book.orders("example") == before
    assert not r.managed_portfolios.history(spots["id"])
    assert r.managed_portfolios.get(spots["id"])["status"] == "running"
    assert r.managed_portfolios.get(spots["id"])["last_error"].startswith("[funding_pending] ")
    quotes = await r.snapshots_for("example")
    account = r.book.account("example", quotes)
    assert account["equity"] is None and account["economic_status"] == "funding_pending"
    # A real protective reduction changes current quantity but leaves original
    # settlement-time inventory/owners intact while its publication is missing.
    held = r.book.positions("example")[0]
    r.book.submit(
        {
            "source": "example",
            "inst_id": held["inst_id"],
            "side": "sell",
            "quantity": str(D(held["quantity"]) / 2),
            "leverage": held["leverage"],
            "reduce_only": True,
            "order_type": "market",
            "margin_mode": "isolated",
        },
        "protect-before-new-target",
        quotes,
        "risk-operator",
    )
    publication["network_failure"] = False
    publication["published"] = True
    await r.managed_portfolios.evaluate(spots["id"])
    assert r.managed_portfolios.history(spots["id"])[0]["status"] == "completed"
    assert r.managed_portfolios.get(spots["id"])["last_error"] is None
    assert D(r.book.account("example", await r.snapshots_for("example"))["funding_paid"]) == D(2)
    report = r.book.contribution_report("example", await r.snapshots_for("example"))
    assert report["reconciled"]
    owner = next(row for row in report["owners"] if row["owner"] == "portfolio:" + swaps["id"])
    assert D(owner["funding_paid"]) == D(2)


async def allowance_drift_fixture(r, monkeypatch, contract):
    """A real unrelated owner pays entry costs between two rebalance children."""
    transport = r.catalog.get_market_snapshot

    async def quoted(symbol, source):
        observed = await transport(symbol, source)
        return observed | {
            "bid": "99.99",
            "ask": "100.01",
            "last": "100",
            "mark": "100",
            "funding_time": END + 8 * HOUR,
            "next_funding_time": END + 16 * HOUR,
        }

    monkeypatch.setattr(r.catalog, "get_market_snapshot", quoted)
    sleeve = await activate(r, capital_pct="8", rebalance_bars=2, execution_contract=contract)
    other = await activate(
        r,
        capital_pct="20",
        execution_contract=contract,
        legs=[{"inst_id": s, "weight": ".25", "leverage": "2"} for s in ("SOL-USDT-SWAP", "OKB-USDT-SWAP")],
    )
    outside = await r.quote_snapshot("example", "SOL-USDT-SWAP")
    actor = "strategy:" + other["manifest"]["legs"][0]["deployment_id"]
    command = {
        "source": "example",
        "inst_id": "SOL-USDT-SWAP",
        "side": "buy",
        "quantity": ".01",
        "leverage": "2",
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
    }
    r.book.submit(command, f"small-existing-other-owner:{END - HOUR}", {"SOL-USDT-SWAP": outside}, actor)
    await r.managed_portfolios.evaluate(sleeve["id"])
    assert r.managed_portfolios.history(sleeve["id"])[0]["status"] == "completed"
    r.clock.change(step_ms=2 * HOUR, expected_revision=r.clock.status()["revision"], actor="trader")
    submit = r.book.submit
    injected = []

    def another_owner_cost(payload, key, quotes, submitted_by, **kwargs):
        if (
            not injected
            and payload["inst_id"] == "ETH-USDT"
            and not payload["reduce_only"]
            and key.startswith("portfolio:")
        ):
            injected.append(key)
            # Real fee and fill-to-mark loss reduce shared equity. No rejection
            # is mocked; the original ETH child reaches AccountCapital admission.
            submit(
                command | {"quantity": "20"}, f"concurrent-other-owner-entry-cost:{END + HOUR}", quotes, actor
            )
        return submit(payload, key, quotes, submitted_by, **kwargs)

    monkeypatch.setattr(r.book, "submit", another_owner_cost)
    return sleeve, other, submit, injected


@pytest.mark.parametrize("contract", ["reduce_group_v1", "reduce_group_v2_allowance"])
async def test_real_capital_drift_preserves_v1_failure_and_v2_linked_smaller_plan(
    runtime, monkeypatch, contract
):
    r = runtime
    sleeve, _, submit, injected = await allowance_drift_fixture(r, monkeypatch, contract)
    await r.managed_portfolios.evaluate(sleeve["id"])
    monkeypatch.setattr(r.book, "submit", submit)
    batch = r.managed_portfolios.history(sleeve["id"])[0]
    assert injected
    if contract == "reduce_group_v1":
        assert batch["status"] == "compensated"
        assert "admitted capital percentage" in batch["error"]
        assert not batch["allowance_replans"]
        return
    assert batch["status"] == "completed", batch["error"]
    assert len(batch["allowance_replans"]) == 1
    plan = batch["allowance_replans"][0]
    assert plan["original_additions_hash"] == batch["additions_hash"]
    assert plan["parent_plan_hash"] == batch["additions_hash"]
    assert D(plan["capital_budget"]["account_equity"]) < D(plan["previous_capital_budget"]["account_equity"])
    assert D(plan["quantities"]["ETH-USDT"]) < D(plan["remaining_original_quantities"]["ETH-USDT"])
    assert D(batch["residuals"]["capital_pct"]) < D(2)
    assert any(
        c["status"] == "superseded" and c["payload"]["inst_id"] == "ETH-USDT" and c["order"] is None
        for c in batch["commands"]
    )
    assert not any(c["phase"] == "compensate" for c in batch["commands"])
    assert r.book.contribution_report("example", await r.snapshots_for("example"))["reconciled"]


async def interrupted_allowance(r, monkeypatch, *, commit_replacement):
    sleeve, other, original, injected = await allowance_drift_fixture(
        r, monkeypatch, "reduce_group_v2_allowance"
    )
    interleaved = r.book.submit
    committed = []

    def crash(payload, key, quotes, actor, **kwargs):
        replacement = (
            injected
            and key != injected[0]
            and payload["inst_id"] == "ETH-USDT"
            and not payload["reduce_only"]
        )
        if replacement and not commit_replacement:
            raise asyncio.CancelledError()
        receipt = interleaved(payload, key, quotes, actor, **kwargs)
        if replacement:
            committed.append(receipt)
            raise asyncio.CancelledError()
        return receipt

    monkeypatch.setattr(r.book, "submit", crash)
    with pytest.raises(asyncio.CancelledError):
        await r.managed_portfolios.evaluate(sleeve["id"])
    monkeypatch.setattr(r.book, "submit", original)
    batch = r.managed_portfolios.history(sleeve["id"])[0]
    assert batch["status"] == "adding" and len(batch["allowance_replans"]) == 1
    return sleeve, other, batch, committed


async def test_allowance_replacement_committed_before_restart_projects_original_receipt_once(
    runtime, monkeypatch
):
    r = runtime
    sleeve, _, interrupted, committed = await interrupted_allowance(r, monkeypatch, commit_replacement=True)
    assert len(committed) == 1
    receipt = committed[0]
    original_additions = copy.deepcopy(interrupted["additions"])
    original_plans = copy.deepcopy(interrupted["allowance_replans"])
    order_count = len(r.book.orders("example"))
    captured = r.catalog.get_market_snapshot
    await r.stop()
    restored = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
    monkeypatch.setattr(restored.catalog, "get_market_snapshot", captured)
    try:
        restored.clock.change(
            step_ms=1, expected_revision=restored.clock.status()["revision"], actor="trader"
        )
        await restored.managed_portfolios.evaluate(sleeve["id"])
        finished = restored.managed_portfolios.history(sleeve["id"])[0]
        assert finished["status"] == "completed", finished["error"]
        assert finished["additions"] == original_additions
        assert finished["allowance_replans"] == original_plans
        assert len(restored.book.orders("example")) == order_count
        assert (
            sum(c["order"] is not None and c["order"]["id"] == receipt["id"] for c in finished["commands"])
            == 1
        )
        assert all(c["order"] is None for c in finished["commands"] if c["status"] == "superseded")
        await restored.managed_portfolios.evaluate(sleeve["id"])
        assert len(restored.book.orders("example")) == order_count
    finally:
        await restored.stop()


async def test_stop_cancels_replacement_without_resurrecting_original_and_retains_capital(
    runtime, monkeypatch
):
    r = runtime
    sleeve, _, interrupted, _ = await interrupted_allowance(r, monkeypatch, commit_replacement=False)
    orders, positions = r.book.orders("example"), r.book.positions("example")
    retained_originals = {c["id"] for c in interrupted["commands"] if c["status"] == "superseded"}
    stopped = r.managed_portfolios.stop(sleeve["id"], "trader")
    assert stopped["status"] == "stopped"
    assert stopped["capital_commitment"]["status"] == "retained"
    final = r.managed_portfolios.history(sleeve["id"])[0]
    assert final["status"] == "canceled"
    assert {c["id"] for c in final["commands"] if c["status"] == "superseded"} == retained_originals
    assert all(c["status"] != "pending" for c in final["commands"])
    await r.managed_portfolios.evaluate(sleeve["id"])
    assert r.book.orders("example") == orders and r.book.positions("example") == positions


@pytest.mark.parametrize("changed_policy", ["risk", "capital"])
async def test_allowance_does_not_supersede_after_real_policy_change(runtime, monkeypatch, changed_policy):
    r = runtime
    sleeve, _, _, _ = await allowance_drift_fixture(r, monkeypatch, "reduce_group_v2_allowance")
    original = r.managed_portfolios._replan_additions

    async def changed(group, batch, failure):
        assert failure.code == "portfolio_capital_limit"
        if changed_policy == "risk":
            r.book.set_risk("example", {"max_order_notional": "2400"}, "trader")
        else:
            r.book.capital.set_policy("example", {"max_base_asset_gross_pct": "90"}, "trader")
        return await original(group, batch, failure)

    monkeypatch.setattr(r.managed_portfolios, "_replan_additions", changed)
    await r.managed_portfolios.evaluate(sleeve["id"])
    batch = r.managed_portfolios.history(sleeve["id"])[0]
    assert batch["status"] == "compensated", batch["error"]
    assert not batch["allowance_replans"]
    assert any(c["phase"] == "compensate" and c["order"] for c in batch["commands"])


async def test_allowance_backup_restore_keeps_plans_in_their_financial_epoch(schema8_runtime, monkeypatch):
    r = schema8_runtime
    sleeve, _, _, _ = await allowance_drift_fixture(r, monkeypatch, "reduce_group_v2_allowance")
    before = r.backups.create("before-allowance-financial-plan")
    await r.managed_portfolios.evaluate(sleeve["id"])
    batch = r.managed_portfolios.history(sleeve["id"])[0]
    assert batch["status"] == "completed" and len(batch["allowance_replans"]) == 1
    after = r.backups.create("with-allowance-financial-plan")
    restored = r.backups.restore(before["id"])
    with r.store.read() as conn:
        assert not conn.execute("SELECT 1 FROM portfolio_batches WHERE id=?", (batch["id"],)).fetchone()
        assert not conn.execute("SELECT 1 FROM audit WHERE kind='portfolio.allowance_superseded'").fetchone()
    with BackupService._readonly(r.backups.directory / (restored["safety_backup_id"] + ".sqlite3")) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM audit WHERE kind='portfolio.allowance_superseded'").fetchone()[
                0
            ]
            == 1
        )
    r.backups.restore(after["id"])
    verified = r.managed_portfolios.batch(batch["id"])
    assert verified["additions_hash"] == batch["additions_hash"]
    assert verified["allowance_replans"] == batch["allowance_replans"]
    assert r.managed_portfolios.get(sleeve["id"])["status"] == "stopped"


@pytest.mark.parametrize(
    "tamper",
    [
        "deleted_plan",
        "wrong_group",
        "wrong_reference",
        "inflated",
        "wrong_sign",
        "policy_anchor",
        "budget_chain",
    ],
)
async def test_allowance_loader_and_schema8_recovery_reject_consistent_hash_tampering(
    schema8_runtime, monkeypatch, tamper
):
    r = schema8_runtime
    sleeve, _, batch, _ = await interrupted_allowance(r, monkeypatch, commit_replacement=False)
    orders_before = r.book.orders("example")
    with r.store.write() as conn:
        row = conn.execute("SELECT * FROM audit WHERE kind='portfolio.allowance_superseded'").fetchone()
        details = json.loads(row["details"])
        plan = details["plan"]
        if tamper == "deleted_plan":
            conn.execute("DELETE FROM audit WHERE id=?", (row["id"],))
        else:
            if tamper == "wrong_group":
                plan["group_id"] = "another-group"
            elif tamper == "wrong_reference":
                plan["replacement_commands"][0]["key"] += ":unrelated"
            elif tamper in {"inflated", "wrong_sign"}:
                ref = plan["replacement_commands"][0]
                command = conn.execute("SELECT * FROM portfolio_commands WHERE id=?", (ref["id"],)).fetchone()
                payload = json.loads(command["payload"])
                symbol = payload["inst_id"]
                if tamper == "inflated":
                    payload["quantity"] = str(D(plan["remaining_original_quantities"][symbol]) * 2)
                else:
                    payload["side"] = "sell"
                plan["quantities"][symbol] = str(
                    D(payload["quantity"]) * (1 if payload["side"] == "buy" else -1)
                )
                ref["payload_hash"] = digest(payload)
                conn.execute(
                    "UPDATE portfolio_commands SET payload=?,payload_hash=? WHERE id=?",
                    (dumps(payload), ref["payload_hash"], ref["id"]),
                )
            elif tamper == "policy_anchor":
                plan["policy_hash"] = "0" * 64
            elif tamper == "budget_chain":
                plan["previous_capital_budget"]["account_equity"] = str(
                    D(plan["previous_capital_budget"]["account_equity"]) + 1
                )
            details["content_hash"] = digest(plan)
            conn.execute("UPDATE audit SET details=? WHERE id=?", (dumps(details), row["id"]))
    with pytest.raises(PlatformError) as failed:
        r.managed_portfolios.batch(batch["id"])
    assert failed.value.code == "portfolio_evidence_integrity"
    with r.store.read() as conn, pytest.raises(PlatformError) as failed:
        BackupService._check_connection(conn)
    assert failed.value.code == "backup_integrity"
    with pytest.raises(PlatformError) as failed:
        r.backups.create("must-refuse-corrupt-allowance-plan")
    assert failed.value.code == "backup_integrity"
    assert r.book.orders("example") == orders_before
    assert r.managed_portfolios.stop(sleeve["id"], "risk-operator")["status"] == "stopped"
    assert r.book.orders("example") == orders_before


@pytest.mark.parametrize("contract", ["reduce_group_v1", "reduce_group_v2_allowance"])
async def test_historical_shared_adapter_uses_real_same_owner_capital_guard_and_fee_drift(
    runtime, monkeypatch, contract
):
    """The historical adapter receives the same actual account and owner policy.

    Research normally owns one scenario book. Using the real mixed-owner book
    here makes the shared capital-percentage rejection observable without
    mocking an exception or inventing an account projection.
    """
    r = runtime
    sleeve, _, _, injected = await allowance_drift_fixture(r, monkeypatch, contract)
    legs = {leg["inst_id"]: leg for leg in sleeve["manifest"]["legs"]}
    quotes = await r.snapshots_for("example", legs)

    def account():
        return r.book.account("example", quotes)

    def positions():
        return {p["inst_id"]: D(p["quantity"]) for p in r.book.positions("example") if p["inst_id"] in legs}

    policy = r.book.risk("example")
    config = policy | {
        "execution_contract": contract,
        "failure_policy": "reduce_group",
        "max_residual_pct": "2",
        "capital_pct": "8",
    }
    capital = D(account()["equity"]) * D(".08")
    targets = target_quantities({s: ".5" for s in legs}, capital, quotes)
    committed = []

    def submit(symbol, quantity, reduce, key):
        payload = {
            "source": "example",
            "inst_id": symbol,
            "side": "buy" if quantity > 0 else "sell",
            "quantity": str(abs(quantity)),
            "leverage": legs[symbol]["leverage"],
            "reduce_only": reduce,
            "order_type": "market",
            "margin_mode": "isolated",
        }
        order = r.book.submit(
            payload, key + f":{END + HOUR}", quotes, "strategy:" + legs[symbol]["deployment_id"]
        )
        committed.append(order)
        return order

    result = execute_batch(
        targets,
        capital,
        quotes,
        config,
        {s: legs[s]["leverage"] for s in legs},
        positions,
        account,
        submit,
        "portfolio:shared-adapter",
        policy_state=lambda: {"risk": r.book.risk("example"), "capital": r.book.capital.policy("example")},
    )
    assert injected
    if contract == "reduce_group_v1":
        assert result["status"] == "compensated"
        assert result["failure"]["code"] == "portfolio_capital_limit"
        assert not result["allowance_replans"]
        assert not positions()
    else:
        assert result["status"] == "completed", result.get("failure")
        assert len(result["allowance_replans"]) == 1
        plan = result["allowance_replans"][0]
        assert D(plan["quantities"]["ETH-USDT"]) < D(plan["remaining_original_quantities"]["ETH-USDT"])
        original = next(t for t in result["trace"] if t["phase"] == "prepare_additions")
        assert D(original["quantities"]["ETH-USDT"]) == D(plan["remaining_original_quantities"]["ETH-USDT"])
        assert D(result["residuals"]["capital_pct"]) < 2
        assert not any(o["reduce_only"] for o in committed)
    assert all(t["contract"] == contract for t in result["trace"])
    assert r.book.contribution_report("example", quotes)["reconciled"]


async def test_real_repeated_equity_drift_is_bounded_at_three_linked_replans(runtime, monkeypatch):
    r = runtime
    sleeve, other, submit, injected = await allowance_drift_fixture(
        r, monkeypatch, "reduce_group_v2_allowance"
    )
    interleaved = r.book.submit
    touched = []
    actor = "strategy:" + other["manifest"]["legs"][0]["deployment_id"]

    def declining(payload, key, quotes, submitted_by, **kwargs):
        if (
            injected
            and key != injected[0]
            and payload["inst_id"] == "ETH-USDT"
            and not payload["reduce_only"]
            and key not in touched
        ):
            touched.append(key)
            submit(
                {
                    "source": "example",
                    "inst_id": "SOL-USDT-SWAP",
                    "side": "buy",
                    "quantity": "1",
                    "leverage": "2",
                    "reduce_only": False,
                    "order_type": "market",
                    "margin_mode": "isolated",
                },
                f"further-other-owner-cost:{len(touched)}:{END + HOUR}",
                quotes,
                actor,
            )
        return interleaved(payload, key, quotes, submitted_by, **kwargs)

    monkeypatch.setattr(r.book, "submit", declining)
    await r.managed_portfolios.evaluate(sleeve["id"])
    batch = r.managed_portfolios.history(sleeve["id"])[0]
    assert batch["status"] == "compensated", batch["error"]
    assert "admitted capital percentage" in batch["error"]
    assert len(batch["allowance_replans"]) == len(touched) == 3
    plans = batch["allowance_replans"]
    assert [p["attempt"] for p in plans] == [1, 2, 3]
    assert [p["parent_plan_hash"] for p in plans] == [
        batch["additions_hash"],
        plans[0]["content_hash"],
        plans[1]["content_hash"],
    ]
    assert all(
        D(p["quantities"]["ETH-USDT"]) < D(p["remaining_original_quantities"]["ETH-USDT"]) for p in plans
    )
    assert any(c["phase"] == "compensate" and c["order"] for c in batch["commands"])
    assert r.book.contribution_report("example", await r.snapshots_for("example"))["reconciled"]


async def test_release_read_time_is_display_only_but_actual_capital_changes_are_bound(runtime, monkeypatch):
    r = runtime
    run = await research(r)
    monkeypatch.setattr(r.book, "now", lambda: 1_000_000)
    first = r.portfolio_releases.preview(run["id"])
    monkeypatch.setattr(r.book, "now", lambda: 1_060_000)
    later = r.portfolio_releases.preview(run["id"])
    assert first["capital_admission"]["actual_admission"]["as_of"] == 1_000_000
    assert later["capital_admission"]["actual_admission"]["as_of"] == 1_060_000
    assert first["preview_hash"] == later["preview_hash"]
    command = {
        "run_id": run["id"],
        "preview_hash": first["preview_hash"],
        "review": "Reviewed current equity and every capital owner before approval.",
        "acknowledgements": first["required_acknowledgements"],
    }
    assert r.portfolio_releases.approve(command, "trader")["status"] == "approved"
    await r.submit(
        {
            "source": "example",
            "inst_id": "SOL-USDT",
            "side": "buy",
            "quantity": "1",
            "leverage": 1,
            "reduce_only": False,
            "order_type": "market",
            "margin_mode": "isolated",
        },
        "manual-capital-change",
        "trader",
    )
    changed = r.portfolio_releases.preview(run["id"])
    assert changed["preview_hash"] != first["preview_hash"]
    assert changed["capital_admission"]["actual_admission"]["owners"]
    with pytest.raises(PlatformError) as error:
        r.portfolio_releases.approve(command, "trader")
    assert error.value.code == "portfolio_release_changed"
