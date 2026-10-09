"""Real database, real economic book: release, interrupted fills and safe recovery."""

import asyncio
import copy
from decimal import Decimal as D

import httpx
import pytest
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.portfolio_research import PortfolioInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, encode

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
