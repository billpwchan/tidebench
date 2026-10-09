"""Shared capital, lagged decisions, funding, provenance and refusal boundaries."""

import copy
from decimal import Decimal

import httpx
import pytest
from tidebench.config import Settings
from tidebench.engine import Candle
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_research import PortfolioInput, simulate_portfolio
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, encode

D = Decimal
HOUR = 3_600_000
START = 1767225600000 - 96 * HOUR
END = 1767225600000


@pytest.fixture
async def runtime(tmp_path):
    settings = Settings(data_dir=tmp_path, worker_enabled=False, _env_file=None)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected venue request"))
    )
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
    yield runtime
    await runtime.stop()
    await client.aclose()


async def packages(runtime, symbols=("BTC-USDT", "ETH-USDT")):
    output = []
    for symbol in symbols:
        package = runtime.packages.create_package(symbol, "1H", START, END, "example")
        package = await runtime.packages.run_package(package["id"])
        assert package["ready"], package
        output.append(package)
    return output


def config(packages, **changes):
    return encode(
        PortfolioInput.model_validate(
            {
                "name": "Joint capital research",
                "hypothesis": "Distinct instruments share one cash budget and close-based rebalance decisions.",
                "legs": [{"package_id": p["id"], "weight": ".5"} for p in packages],
                "initial_cash": "10000",
                "fee_bps": 10,
                "slippage_bps": 5,
                "max_daily_loss_pct": 50,
                "rebalance_bars": 24,
                **changes,
            }
        ).model_dump()
    )


async def run(runtime, body):
    tiers = {}
    for leg in body["legs"]:
        package = runtime.packages.get_package(leg["package_id"])
        if package["inst_id"].endswith("-SWAP"):
            tiers[package["inst_id"]] = await runtime.catalog.get_margin_tiers(package["inst_id"], "example")
    queued = runtime.portfolios.create(body, "researcher", tiers)
    await runtime.offload(runtime.portfolios.compute, queued["id"])
    result = runtime.portfolios.get(queued["id"])
    assert result["status"] == "completed", result["error"]
    return result


async def test_joint_capital_balances_native_journal_and_never_opens_on_signal_bar(runtime):
    inputs = await packages(runtime)
    result = await run(runtime, config(inputs))
    plan = result["result"]
    assert D(plan["metrics"]["initial_cash"]) == 10000
    assert D(plan["equity"][0]["equity"]) == 10000
    assert len(plan["final_account"]["positions"]) == 2
    assert all(D(p["cash"]) >= 0 for p in plan["equity"])
    assert min(order["quote_ts"] for order in plan["orders"]) == START + HOUR
    first = plan["decisions"][0]
    assert first["ts"] == START + HOUR and first["bar_ts"] == START and first["executed_at"] == START + HOUR
    assert D(first["cash_scale"]) < 1  # Fees/slippage share one finite budget.
    assert not runtime.book.positions("example")  # Historical research cannot mutate forward accounts.
    assert runtime.book.account("example", {})["cash"] == "10000"
    balances = {}
    for row in plan["ledger"]:
        balances[row["asset"]] = balances.get(row["asset"], D(0)) + D(row["debit"]) - D(row["credit"])
    assert all(abs(v) < D("1e-20") for v in balances.values())
    assert result["manifest"]["result_hash"]
    assert not plan["execution_rejections"]


async def test_result_replay_is_identical_without_wall_clock_or_random_ids(runtime):
    inputs = await packages(runtime)
    first = await run(runtime, config(inputs))
    second = await run(runtime, config(inputs))
    # Economic paths match. Command/journal UUIDs and installation audit times
    # are intentionally excluded from this economic comparison.
    assert first["result"]["equity"] == second["result"]["equity"]
    assert first["result"] == second["result"]


async def test_chronological_portfolio_test_uses_independent_capital_and_no_train_inventory(runtime):
    inputs = await packages(runtime)
    body = config(inputs, evaluation="train_test", train_pct=70, embargo_bars=2, rebalance_bars=1)
    result = await run(runtime, body)
    plan, evaluation = result["result"], result["result"]["evaluation"]
    boundary = START + (96 * 70 // 100 + 2) * HOUR
    assert evaluation["test_start"] == boundary
    assert evaluation["train_end"] == START + (96 * 70 // 100) * HOUR
    assert plan["equity"][0]["equity"] == "10000"
    assert not plan["equity"][0]["positions"]
    assert min(o["quote_ts"] for o in plan["orders"]) == boundary + HOUR
    assert all(d["bar_ts"] >= boundary for d in plan["decisions"])
    assert evaluation["train_metrics"]["initial_cash"] == "10000"
    repeated = await run(runtime, body)
    assert repeated["result"] == plan


async def test_portfolio_refuses_short_test_before_enqueuing(runtime):
    inputs = await packages(runtime)
    body = config(inputs, evaluation="train_test", train_pct=85, embargo_bars=20)
    with pytest.raises(PlatformError, match="at least 20 bars"):
        runtime.portfolios.create(body, "researcher")
    assert not runtime.portfolios.list("example")


async def test_missing_leg_bars_fail_closed_and_minimum_sizes_leave_explained_residuals(runtime):
    inputs = await packages(runtime)
    body = config(inputs)
    legs = []
    for package, policy in zip(inputs, body["legs"], strict=True):
        captured = runtime.packages.research_inputs(package["id"])
        trade = runtime.catalog.get_dataset(captured["dataset_id"])
        candles = runtime.catalog.load_candles(captured["dataset_id"])
        legs.append(
            {
                "config": policy,
                "instrument": trade["metadata"],
                "candles": candles,
                "marks": candles,
                "funding": [],
                "tiers": [],
            }
        )
    manifest = {"source": "example", "bar": "1H", "start": START, "universe_scope": "synthetic_test"}
    missing = copy.deepcopy(legs)
    missing[1]["candles"].pop(10)
    with pytest.raises(PlatformError, match="align exactly"):
        simulate_portfolio(body, manifest, missing)
    for leg in legs:
        leg["instrument"]["min_size"] = "1000000000"
    result = simulate_portfolio(body, manifest, legs)
    assert not result["orders"]
    assert result["final_account"]["cash"] == "10000"
    assert result["execution_rejections"]
    assert {e["code"] for e in result["execution_rejections"]} == {"minimum_size", "portfolio_minimum_leg"}
    assert result["execution_status"] == "failed"
    assert len(result["decisions"]) == 1 and result["decisions"][0]["status"] == "compensated"
    assert any(D(v) > 0 for d in result["decisions"] for v in d["quantity_residuals"].values())


async def test_carry_uses_only_prior_realized_funding_and_exposes_leg_residuals(runtime):
    inputs = await packages(runtime, ("BTC-USDT", "BTC-USDT-SWAP"))
    # Reserve headroom inside the declared sleeve; spare account cash cannot
    # finance fee-sensitive maintenance beyond that allocated budget.
    body = config(inputs, mode="funding_carry", rebalance_bars=1, capital_pct="20")
    body["legs"][0]["weight"], body["legs"][1]["weight"] = ".4", "-.4"
    result = await run(runtime, body)
    plan = result["result"]
    assert not any(D(w) for w in plan["decisions"][0]["weights"].values())
    assert plan["funding"]
    assert any(D(p["funding_paid"]) != 0 for p in plan["equity"])
    assert all("quantity_residuals" in d for d in plan["decisions"])
    assert plan["assumptions"]["fills"].startswith("sequential")


async def test_point_in_time_claim_requires_rules_and_rejects_future_known_dates(runtime):
    inputs = await packages(runtime)
    body = config(inputs, rules_mode="point_in_time")
    with pytest.raises(PlatformError, match="covering the initial"):
        runtime.portfolios.create(body, "researcher")
    for leg, package in zip(body["legs"], inputs, strict=True):
        leg["rule_events"] = [
            {
                "effective_ts": START,
                "known_at": START,
                "instrument": package["manifest"]["instrument"],
                "margin_tiers": [],
                "provenance": "Synthetic historical rule fixture, explicitly attributed.",
            }
        ]
    result = await run(runtime, body)
    assert result["result"]["assumptions"]["rules"] == "point_in_time"
    bad = copy.deepcopy(body)
    bad["legs"][0]["rule_events"][0]["known_at"] = START + HOUR
    with pytest.raises(ValueError, match="known by its effective"):
        PortfolioInput.model_validate(bad)


async def test_duplicate_market_and_input_identity_tamper_are_refused(runtime):
    inputs = await packages(runtime)
    body = config(inputs)
    body["legs"][1]["package_id"] = body["legs"][0]["package_id"]
    with pytest.raises(PlatformError, match="distinct markets"):
        runtime.portfolios.create(body, "researcher")
    run = await run_portfolio(runtime, config(inputs))
    with runtime.store.write() as conn:
        conn.execute("UPDATE portfolio_runs SET result='{}' WHERE id=?", (run["id"],))
    with pytest.raises(PlatformError, match="integrity"):
        runtime.portfolios.get(run["id"])


async def run_portfolio(runtime, body):
    return await run(runtime, body)


async def test_cost_aware_carry_preserves_zero_trade_evidence_when_positive_rates_cannot_pay_costs(runtime):
    inputs = await packages(runtime, ("BTC-USDT", "BTC-USDT-SWAP"))
    body = config(
        inputs,
        mode="funding_carry",
        carry_window=12,
        carry_cost_settlements=21,
        carry_buffer_bps=1000,
        carry_max_age_hours=16,
        legs=[
            {"package_id": p["id"], "weight": weight, "strategy": {"kind": "buy_hold"}}
            for p, weight in zip(inputs, [".3", "-.3"], strict=True)
        ],
    )
    result = (await run(runtime, body))["result"]
    assert result["orders"] == [] and result["metrics"]["total_return_pct"] == "0"
    evidence = [d["carry_evidence"] for d in result["decisions"]]
    assert any(e["reason"] == "cost_hurdle" for e in evidence)
    assert all(not e["allowed"] for e in evidence)
    for decision in result["decisions"]:
        assert all(ts < decision["bar_ts"] for ts in decision["carry_evidence"]["settlement_times"])


async def test_risk_rotation_causal_window_replay_and_risk_budget(runtime):
    inputs = await packages(runtime)
    body = config(
        inputs,
        mode="risk_momentum",
        risk_window=20,
        lookback=20,
        top_k=2,
        vol_target_pct="10",
        rebalance_bars=6,
    )
    result = await run(runtime, body)
    decisions = result["result"]["decisions"]
    assert decisions[0]["risk_evidence"]["reason"] == "insufficient_history"
    allocated = [d for d in decisions if d["risk_evidence"].get("covariance")]
    assert allocated
    for decision in allocated:
        e = decision["risk_evidence"]
        assert e["sample_end"] == decision["ts"]
        assert D(e["modeled_vol_pct"]) <= D(10) + D("1e-40")
        assert D(e["stressed_vol_pct"]) <= D(10) + D("1e-40")
        assert all(D(w) >= 0 for w in decision["weights"].values())
    replay = runtime.portfolios.replay(result["id"], "researcher")
    await runtime.offload(runtime.portfolios.compute, replay["id"])
    replay = runtime.portfolios.get(replay["id"])
    assert replay["manifest"]["replay_verified"]
    assert replay["result"] == result["result"]


async def test_covariance_product_budget_refuses_excess_work_before_enqueue(runtime):
    inputs = []
    for symbol in ("BTC-USDT", "ETH-USDT"):
        p = runtime.packages.create_package(symbol, "1H", END - 3360 * HOUR, END, "example")
        p = await runtime.packages.run_package(p["id"])
        assert p["ready"]
        inputs.append(p)
    body = config(inputs, mode="risk_momentum", risk_window=400, rebalance_bars=1)
    with pytest.raises(PlatformError, match="five million"):
        runtime.portfolios.create(body, "researcher")
    assert runtime.portfolios.list("example") == []


def failure_fixture(*, second_price="100", second_minimum=".01", weight=".4", **changes):
    """Exactly the audited economic obstacle, independent of exchange/dataset helpers."""
    from test_pro_execution import snapshot

    body = config(
        [{"id": str(i) * 32} for i in (1, 2)],
        legs=[dict(package_id=str(i) * 32, weight=weight, strategy={"kind": "buy_hold"}) for i in (1, 2)],
        rebalance_bars=1,
        **({"max_order_notional": 1000000000} | changes),
    )
    legs = []
    for symbol, price, minimum, policy in zip(
        ("BTC-USDT", "ETH-USDT"),
        ("100", second_price),
        (".01", second_minimum),
        body["legs"],
        strict=True,
    ):
        meta = snapshot(symbol)["instrument"]
        meta.update(base=symbol.split("-")[0], min_size=minimum)
        bars = [
            Candle(
                ts=START + i * HOUR,
                open=D(price),
                high=D(price),
                low=D(price),
                close=D(price),
                volume=D(100),
                confirmed=True,
            )
            for i in range(4)
        ]
        legs.append(dict(config=policy, instrument=meta, candles=bars, marks=bars, funding=[], tiers=[]))
    return body, {"source": "example", "bar": "1H", "start": START, "universe_scope": "synthetic_test"}, legs


def test_audited_minimum_failure_is_now_same_group_policy_and_legacy_inputs_are_explicit():
    body, manifest, legs = failure_fixture(
        second_price="100000", second_minimum="1", weight=".5", fee_bps=0, slippage_bps=0
    )
    result = simulate_portfolio(body, manifest, legs)
    assert not result["orders"] and not result["final_account"]["positions"]
    assert result["execution_status"] == "failed"
    assert result["decisions"][0]["execution"]["failure"]["code"] == "portfolio_minimum_leg"
    assert len(result["decisions"]) == 1
    # Pre-upgrade captured dictionaries retain their old semantics. They cannot
    # become newly bound v1 studies; get/replay already require original identity.
    old = {
        k: v for k, v in body.items() if k not in {"execution_contract", "failure_policy", "max_residual_pct"}
    }
    legacy = simulate_portfolio(old, manifest, legs)
    assert "execution_status" not in legacy
    assert len(legacy["orders"]) == 1 and legacy["final_account"]["positions"][0]["quantity"] == "50.0"


@pytest.mark.parametrize("blocked", [False, True])
def test_historical_partial_failure_preserves_book_and_marks_incomplete_compensation(monkeypatch, blocked):
    from tidebench.pro_execution import SimulationBook

    body, manifest, legs = failure_fixture()
    submit = SimulationBook.submit

    def reject_second(self, order, key, quotes, *args, **kwargs):
        if order["inst_id"] == "ETH-USDT" and not order["reduce_only"]:
            raise PlatformError("fixture_add_denied", "Injected add rejection", 409)
        if blocked and order["reduce_only"]:
            raise PlatformError("fixture_exit_denied", "Injected blocked compensation", 409)
        return submit(self, order, key, quotes, *args, **kwargs)

    monkeypatch.setattr(SimulationBook, "submit", reject_second)
    result = simulate_portfolio(body, manifest, legs)
    assert len(result["decisions"]) == 1
    decision = result["decisions"][0]
    assert decision["execution"]["failure"]["phase"] == "add"
    if blocked:
        assert result["execution_status"] == "compensating"
        assert result["economic_state"] == "incomplete_compensation"
        assert len(result["orders"]) == 1 and result["final_account"]["positions"]
        assert decision["execution"]["compensation_attempts"] == 3
        assert {s: D(q) for s, q in decision["execution"]["remaining_inventory"].items()} == {
            "BTC-USDT": D(40)
        }
    else:
        assert result["execution_status"] == "failed" and decision["status"] == "compensated"
        assert len(result["orders"]) == 2 and not result["final_account"]["positions"]
        assert D(result["final_account"]["cash"]) < 10000
        assert D(result["final_account"]["fees_paid"]) == D(8)
        assert all(row["inst_id"] == "BTC-USDT" for row in result["orders"])


def test_historical_blocked_compensation_retries_at_next_quote_without_new_decision(monkeypatch):
    from tidebench.pro_execution import SimulationBook

    body, manifest, legs = failure_fixture()
    submit, compensation_keys = SimulationBook.submit, []

    def failure(self, order, key, quotes, *args, **kwargs):
        if order["inst_id"] == "ETH-USDT" and not order["reduce_only"]:
            raise PlatformError("fixture_add_denied", "Injected add rejection", 409)
        if order["reduce_only"]:
            compensation_keys.append(key)
            if quotes[order["inst_id"]]["ts"] == START + HOUR:
                raise PlatformError("fixture_exit_denied", "Transient compensation failure", 409)
        return submit(self, order, key, quotes, *args, **kwargs)

    monkeypatch.setattr(SimulationBook, "submit", failure)
    result = simulate_portfolio(body, manifest, legs)
    assert result["execution_status"] == "failed" and not result["final_account"]["positions"]
    assert len(result["orders"]) == 2 and len(result["decisions"]) == 1
    assert result["decisions"][0]["execution"]["compensation_attempts"] == 2
    assert compensation_keys[0] == compensation_keys[1]
    assert result["orders"][1]["quote_ts"] == START + 2 * HOUR


def test_historical_residual_failure_keeps_four_fills_and_round_trip_fees():
    body, manifest, legs = failure_fixture(weight=".5", max_residual_pct=".01")
    result = simulate_portfolio(body, manifest, legs)
    assert result["execution_status"] == "failed"
    assert result["decisions"][0]["execution"]["failure"]["code"] == "portfolio_residual_limit"
    assert len(result["orders"]) == 4 and not result["final_account"]["positions"]
    assert D(result["final_account"]["fees_paid"]) > 0 and D(result["final_account"]["cash"]) < 10000


def test_execution_contract_bounds_and_only_supported_failure_policy():
    for values in (
        {"max_residual_pct": 0},
        {"max_residual_pct": 101},
        {"failure_policy": "ignore"},
        {"execution_contract": "legacy_per_leg_v0"},
    ):
        with pytest.raises(ValueError):
            failure_fixture(**values)


def test_failed_execution_cannot_pass_financial_holdout_criteria():
    from tidebench.research_protocol import evaluate_frozen_portfolio

    body, manifest, legs = failure_fixture(weight=".5", max_residual_pct=".01")
    manifest |= dict(
        evaluation_plan=dict(
            warmup_bars=0,
            rejection_plan="Reject when the declared group execution fails regardless of financial outcome.",
            test_start=START,
            test_end=START + 4 * HOUR,
            criteria=dict(
                min_observations=2,
                min_trades=1,
                min_return_vs_cash_pct="-100",
                max_drawdown_pct="100",
                require_zero_debt=True,
            ),
        ),
        governance={},
    )
    result = evaluate_frozen_portfolio(body, manifest, legs)
    rejection = result["evaluation"]["rejection"]
    assert rejection["status"] == "rejected"
    assert all(row["passed"] for row in rejection["checks"] if row["metric"] != "execution_status")
    execution = next(row for row in rejection["checks"] if row["metric"] == "execution_status")
    assert execution["actual"] == "failed" and not execution["passed"]


@pytest.mark.parametrize("failure_case", ["minimum", "child", "residual", "blocked_compensation"])
async def test_real_managed_and_historical_books_match_equivalent_failure_input(
    runtime, monkeypatch, failure_case
):
    """Same weights, quotes, increments and risk limits through both actual adapters."""
    from test_managed_portfolios import activate, approve
    from test_pro_execution import snapshot
    from tidebench.portfolio_registry import PortfolioDefinition
    from tidebench.pro_execution import SimulationBook

    residual_limit = ".01" if failure_case == "residual" else "2"
    weight = ".4" if failure_case in {"child", "blocked_compensation"} else ".5"
    declared_legs = [
        {"inst_id": s, "weight": weight, "strategy": {"kind": "buy_hold"}} for s in ("BTC-USDT", "ETH-USDT")
    ]
    if failure_case == "residual":
        # The tight limit really fails at 10/5 costs in both adapters, so it
        # cannot be approved from that failed study. An explicit zero-cost
        # study passes; paper review acknowledges the later 10/5 difference.
        definition = PortfolioDefinition(legs=declared_legs, max_residual_pct=residual_limit).record()
        project = runtime.portfolio_registry.create_project(
            "Cost-perturbed parity fixture",
            "Test a declared residual limit under an explicitly reviewed execution cost change.",
            definition,
            "researcher",
        )
        inputs = await packages(runtime)
        baseline = config(
            inputs,
            name=project["name"],
            hypothesis=project["version"]["hypothesis"],
            portfolio_version_id=project["version"]["id"],
            fee_bps=0,
            slippage_bps=0,
            max_daily_loss_pct=5,
            max_residual_pct=residual_limit,
            legs=[
                {k: v for k, v in leg.items() if k != "inst_id"} | {"package_id": package["id"]}
                for leg, package in zip(definition["legs"], inputs, strict=True)
            ],
        )
        study = await run(runtime, baseline)
        assert study["result"]["execution_status"] == "running"
        release = approve(runtime, study)
        assert "execution_cost_difference" in release["approval"]["preview"]["required_acknowledgements"]
        group = runtime.managed_portfolios.get(
            runtime.portfolio_releases.activate(release["id"], "trader")["group_id"]
        )
    else:
        group = await activate(runtime, max_residual_pct=residual_limit, legs=declared_legs)
    quotes = {s: snapshot(s, ts=END) for s in ("BTC-USDT", "ETH-USDT")}
    quotes["ETH-USDT"]["instrument"]["base"] = "ETH"
    if failure_case == "minimum":
        for field in ("last", "mark", "bid", "ask"):
            quotes["ETH-USDT"][field] = "100000"
        quotes["ETH-USDT"]["instrument"]["min_size"] = "1"

    async def exact_quotes(*args, **kwargs):
        return quotes

    monkeypatch.setattr(runtime, "snapshots_for", exact_quotes)
    original = SimulationBook.submit

    def reject_child(self, order, key, snapshots, *args, **kwargs):
        if failure_case in {"child", "blocked_compensation"} and ":add:2:" in key:
            raise PlatformError("fixture_child_denied", "Same frozen third-child failure", 409)
        if failure_case == "blocked_compensation" and order["reduce_only"]:
            raise PlatformError("fixture_exit_denied", "Same frozen compensation obstruction", 409)
        return original(self, order, key, snapshots, *args, **kwargs)

    monkeypatch.setattr(SimulationBook, "submit", reject_child)
    body, manifest, legs = failure_fixture(
        second_price="100000" if failure_case == "minimum" else "100",
        second_minimum="1" if failure_case == "minimum" else ".01",
        weight=weight,
        max_residual_pct=residual_limit,
        max_order_notional=2500,
        max_daily_loss_pct=5,
    )
    historical = simulate_portfolio(body, manifest, legs)
    await runtime.managed_portfolios.evaluate(group["id"])
    forward = runtime.managed_portfolios.history(group["id"])[0]
    expected_batch = "compensating" if failure_case == "blocked_compensation" else "compensated"
    expected_group = "compensating" if failure_case == "blocked_compensation" else "failed"
    assert forward["status"] == historical["decisions"][0]["status"] == expected_batch
    assert (
        runtime.managed_portfolios.get(group["id"])["status"]
        == historical["execution_status"]
        == expected_group
    )
    account = runtime.book.account("example", quotes)
    for field in ("cash", "equity", "fees_paid", "realized_pnl", "funding_paid", "insurance_debt"):
        assert D(account[field]) == D(historical["final_account"][field]), field
    actual_positions = {p["inst_id"]: D(p["quantity"]) for p in runtime.book.positions("example")}
    historical_positions = {p["inst_id"]: D(p["quantity"]) for p in historical["final_account"]["positions"]}
    assert actual_positions == historical_positions
    assert bool(actual_positions) == (failure_case == "blocked_compensation")

    def fill_fields(order):
        return (order["inst_id"], order["side"], D(order["quantity"]), D(order["price"]), D(order["fee"]))

    forward_fills = [fill_fields(c["order"]) for c in forward["commands"] if c["order"]]
    assert forward_fills == [fill_fields(o) for o in historical["orders"]]
    assert runtime.book.contribution_report("example", quotes)["reconciled"]


def test_known_group_failure_is_rejected_even_without_statistical_sample():
    from tidebench.research_protocol import evaluate_frozen_portfolio

    body, manifest, legs = failure_fixture(second_price="100000", second_minimum="1", weight=".5")
    manifest |= dict(
        evaluation_plan=dict(
            warmup_bars=0,
            test_start=START,
            test_end=START + 4 * HOUR,
            rejection_plan="Reject an execution policy violation even when there are no fills.",
            criteria=dict(
                min_observations=100,
                min_trades=10,
                min_return_vs_cash_pct="-100",
                max_drawdown_pct="100",
                require_zero_debt=True,
            ),
        ),
        governance={},
    )
    result = evaluate_frozen_portfolio(body, manifest, legs)
    assert result["metrics"]["orders"] == 0
    assert result["evaluation"]["rejection"]["status"] == "rejected"


async def test_managed_and_history_retain_zero_budget_same_side_rounding_with_exact_economics(
    runtime, monkeypatch
):
    from decimal import localcontext

    from test_managed_portfolios import activate
    from test_pro_execution import snapshot
    from tidebench.engine import ACCOUNTING_CONTEXT

    symbols = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    weights = ".333333333333333333"
    runtime.book.set_risk("example", dict(fee_bps=0, slippage_bps=0, max_daily_loss_pct=50), "fixture")
    group = await activate(
        runtime,
        capital_pct="10",
        rebalance_bars=1,
        legs=[dict(inst_id=s, weight=weights, strategy={"kind": "buy_hold"}) for s in symbols],
        research_policy=dict(fee_bps=0, slippage_bps=0, max_daily_loss_pct=50),
    )
    quotes = {s: snapshot(s, ts=END) for s in symbols}
    for s, minimum in zip(symbols, (".003", ".002", ".005"), strict=True):
        quotes[s]["instrument"].update(base=s.split("-")[0], lot_size=".001", min_size=minimum)

    async def observed(*args, **kwargs):
        return quotes

    monkeypatch.setattr(runtime, "snapshots_for", observed)
    await runtime.managed_portfolios.evaluate(group["id"])
    first = runtime.managed_portfolios.history(group["id"])[0]
    assert first["status"] == "completed"
    assert all(D(p["quantity"]) == D("3.333") for p in runtime.book.positions("example"))
    prices = ("100.06", "99.93", "100.13")
    for s, price in zip(symbols, prices, strict=True):
        quotes[s].update(ts=END + HOUR, mark_ts=END + HOUR, bid=price, ask=price, last=price, mark=price)
    runtime.clock.change(step_ms=HOUR, expected_revision=runtime.clock.status()["revision"], actor="fixture")
    await runtime.managed_portfolios.evaluate(group["id"])
    forward = runtime.managed_portfolios.history(group["id"])[0]
    assert forward["status"] == "completed"
    assert D(forward["additions"]["capital_budget"]["budget_cash"]) == 0
    assert [r["code"] for r in forward["additions"]["skipped"]] == ["rebalance_cash_rounding"]
    assert len(forward["body"]["reduction_skips"]) == 2
    assert D(forward["residuals"]["capital_pct"]) < D(".1")
    assert not forward["commands"] and len(runtime.book.orders("example")) == 3

    body = config(
        [{"id": str(i) * 32} for i in (1, 2, 3)],
        capital_pct="10",
        fee_bps=0,
        slippage_bps=0,
        max_daily_loss_pct=50,
        rebalance_bars=1,
        legs=[dict(package_id=str(i) * 32, weight=weights, strategy={"kind": "buy_hold"}) for i in (1, 2, 3)],
    )
    legs = []
    for s, price, policy in zip(symbols, prices, body["legs"], strict=True):
        candles = []
        for i in range(4):
            opening = D(100) if i < 2 else D(price)
            closing = D(100) if i == 0 else D(price)
            candles.append(
                Candle(
                    ts=START + i * HOUR,
                    open=opening,
                    high=max(opening, closing),
                    low=min(opening, closing),
                    close=closing,
                    volume=D(1),
                )
            )
        legs.append(
            dict(
                config=policy,
                instrument=copy.deepcopy(quotes[s]["instrument"]),
                candles=candles,
                marks=candles,
                funding=[],
                tiers=[],
            )
        )
    with localcontext(ACCOUNTING_CONTEXT):
        historical = simulate_portfolio(
            body,
            dict(
                source="example",
                bar="1H",
                start=START,
                universe_scope="zero sleeve budget three-market parity",
            ),
            legs,
        )
    second = historical["decisions"][1]["execution"]
    assert historical["execution_status"] == "running" and second["status"] == "completed"
    assert D(second["capital_budget"]["budget_cash"]) == 0
    assert second["residuals"] == forward["residuals"]
    actual = runtime.book.account("example", quotes)
    for field in ("cash", "equity", "fees_paid", "realized_pnl", "funding_paid", "insurance_debt"):
        assert D(actual[field]) == D(historical["final_account"][field]), field
    assert len(historical["orders"]) == len(runtime.book.orders("example")) == 3
    assert runtime.book.contribution_report("example", quotes)["reconciled"]
