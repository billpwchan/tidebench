"""Shared capital, lagged decisions, funding, provenance and refusal boundaries."""

import copy
from decimal import Decimal

import httpx
import pytest
from tidebench.config import Settings
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
    assert all(e["code"] == "minimum_size" for e in result["execution_rejections"])
    assert any(D(v) > 0 for d in result["decisions"] for v in d["quantity_residuals"].values())


async def test_carry_uses_only_prior_realized_funding_and_exposes_leg_residuals(runtime):
    inputs = await packages(runtime, ("BTC-USDT", "BTC-USDT-SWAP"))
    body = config(inputs, mode="funding_carry", rebalance_bars=1)
    body["legs"][1]["weight"] = "-.5"
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
