"""Shared failure semantics preserve committed economics through compensation."""

import json
from decimal import Decimal as D
from decimal import localcontext

import pytest
from test_pro_execution import order, snapshot
from tidebench.platform import PlatformError
from tidebench.portfolio_execution import (
    EXECUTION_CONTRACT,
    execute_batch,
    portfolio_commands,
    portfolio_residuals,
    require_addition_legs,
    require_residual_limit,
    resume_compensation,
    split_addition,
)
from tidebench.pro_execution import SimulationBook
from tidebench.store import Store


class Harness:
    def __init__(self, path, *, fee="10", slip="5"):
        self.book = SimulationBook(Store(path), clock=lambda: 1_000_000, capture_contributions=False)
        self.quotes = {s: snapshot(s) for s in ("BTC-USDT", "ETH-USDT")}
        self.quotes["ETH-USDT"]["instrument"]["base"] = "ETH"
        self.config = dict(
            execution_contract=EXECUTION_CONTRACT,
            failure_policy="reduce_group",
            max_residual_pct="2",
            max_order_notional="1000000",
            fee_bps=fee,
            slippage_bps=slip,
        )
        self.book.set_risk(
            "example",
            dict(fee_bps=fee, slippage_bps=slip, max_order_notional="1000000", max_daily_loss_pct=50),
            "fixture",
        )
        self.fail = lambda *args: None
        self.attempts = []

    def positions(self):
        return {p["inst_id"]: D(p["quantity"]) for p in self.book.positions("example")}

    def account(self):
        return self.book.account("example", self.quotes)

    def submit(self, symbol, quantity, reduce, key):
        self.attempts.append((symbol, quantity, reduce, key))
        self.fail(symbol, quantity, reduce, key)
        return self.book.submit(
            order(symbol, "buy" if quantity > 0 else "sell", str(abs(quantity)), reduce_only=reduce),
            key,
            self.quotes,
            "test",
        )

    def execute(self, targets, capital=10000):
        return execute_batch(
            targets,
            capital,
            self.quotes,
            self.config,
            {s: 1 for s in self.quotes},
            self.positions,
            self.account,
            self.submit,
            "fixture-batch",
        )

    def assert_balanced(self):
        amounts = {}
        with localcontext() as context:
            context.prec = 200
            for row in self.book.ledger("example", limit=100000):
                amounts[row["asset"]] = amounts.get(row["asset"], D(0)) + D(row["debit"]) - D(row["credit"])
        assert not any(amounts.values())


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "execution.sqlite3")


def reject(code, message="Injected fixture rejection"):
    raise PlatformError(code, message, 409)


def test_minimum_new_leg_refuses_every_addition_before_first_fill(harness):
    harness.quotes["ETH-USDT"]["last"] = harness.quotes["ETH-USDT"]["ask"] = "100000"
    harness.quotes["ETH-USDT"]["instrument"]["min_size"] = "1"
    result = harness.execute({"BTC-USDT": D(50), "ETH-USDT": D(".05")})
    assert result["failure"]["code"] == "portfolio_minimum_leg"
    assert result["status"] == "compensated" and result["remaining_inventory"] == {}
    assert not harness.attempts and not harness.positions()
    assert harness.account()["cash"] == "10000" and harness.account()["fees_paid"] == "0"
    assert [(r["phase"], r["status"]) for r in result["trace"]] == [
        ("prepare_additions", "frozen"),
        ("prepare_additions", "failed"),
        ("compensate", "completed"),
    ]
    harness.assert_balanced()


def test_fill_then_rejection_flattens_group_and_keeps_round_trip_cost(harness):
    harness.fail = lambda symbol, quantity, reduce, key: (
        reject("fixture_add_denied") if symbol == "ETH-USDT" and not reduce else None
    )
    result = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    assert result["failure"]["phase"] == "add" and result["failure"]["code"] == "fixture_add_denied"
    assert result["status"] == "compensated" and not harness.positions()
    assert [(r["phase"], r["status"]) for r in result["trace"]] == [
        ("prepare_additions", "frozen"),
        ("add", "filled"),
        ("add", "rejected"),
        ("add", "failed"),
        ("compensate", "filled"),
        ("compensate", "completed"),
    ]
    assert D(harness.account()["cash"]) < D(10000)
    assert D(harness.account()["fees_paid"]) == D("8.00000")
    assert D(harness.account()["realized_pnl"]) < 0
    assert len(harness.book.orders("example")) == 2
    harness.assert_balanced()


def test_residual_failure_compensates_committed_fills_not_a_discarded_result(harness):
    harness.config["max_residual_pct"] = ".01"
    result = harness.execute({"BTC-USDT": D(50), "ETH-USDT": D(50)})
    assert D(result["cash_scale"]) < 1
    assert result["failure"]["code"] == "portfolio_residual_limit"
    # Two additions plus two actual compensation exits, with all fees retained.
    assert result["status"] == "compensated" and not harness.positions()
    assert len(harness.book.orders("example")) == 4
    assert D(harness.account()["fees_paid"]) > 0 and D(harness.account()["cash"]) < D(10000)
    observed = next(r for r in result["trace"] if r["phase"] == "residual")
    assert D(observed["capital_pct"]) > D(".01")
    harness.assert_balanced()


def test_blocked_compensation_survives_serialization_and_retries_once(harness):
    def failure(symbol, quantity, reduce, key):
        if reduce:
            reject("fixture_exit_denied")
        if symbol == "ETH-USDT":
            reject("fixture_add_denied")

    harness.fail = failure
    batch = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    assert batch["status"] == "compensating"
    assert {s: D(q) for s, q in batch["remaining_inventory"].items()} == {"BTC-USDT": D(40)}
    assert harness.positions() == {"BTC-USDT": D(40)}
    batch = json.loads(json.dumps(batch))  # Controller-independent retained evidence.
    frozen = batch["compensation_commands"][0].copy()
    harness.fail = lambda *args: None
    resume_compensation(batch, harness.quotes, harness.positions, harness.submit)
    assert batch["status"] == "compensated" and not harness.positions()
    assert batch["compensation_commands"][0]["quantity"] == frozen["quantity"]
    assert harness.attempts[-1][3] == harness.attempts[-2][3]
    orders_before, fees_before = len(harness.book.orders("example")), harness.account()["fees_paid"]
    resume_compensation(batch, harness.quotes, harness.positions, harness.submit)
    assert len(harness.book.orders("example")) == orders_before == 2
    assert harness.account()["fees_paid"] == fees_before
    harness.assert_balanced()


def test_existing_inventory_is_flattened_and_other_market_is_retained(harness):
    other = "SOL-USDT"
    harness.quotes[other] = snapshot(other)
    harness.quotes[other]["instrument"]["base"] = "SOL"
    harness.submit("BTC-USDT", D(10), False, "old-group-inventory")
    harness.submit(other, D(5), False, "outside-universe")
    harness.fail = lambda symbol, quantity, reduce, key: (
        reject("fixture_add_denied") if symbol == "ETH-USDT" and not reduce else None
    )
    batch = harness.execute({"BTC-USDT": D(20), "ETH-USDT": D(20)})
    assert batch["status"] == "compensated"
    assert harness.positions() == {other: D(5)}
    assert any(D(r.get("signed_quantity", 0)) == D(-20) for r in batch["trace"])
    harness.assert_balanced()


def test_compensation_replans_if_protection_changes_inventory(harness):
    harness.fail = lambda symbol, quantity, reduce, key: (
        reject("fixture_denied") if reduce or symbol == "ETH-USDT" else None
    )
    batch = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    harness.fail = lambda *args: None
    harness.submit("BTC-USDT", D(-25), True, "independent-protective-exit")
    resume_compensation(batch, harness.quotes, harness.positions, harness.submit)
    assert batch["status"] == "compensated" and not harness.positions()
    assert batch["compensation_commands"][0]["status"] == "superseded"
    assert D(batch["compensation_commands"][1]["quantity"]) == D(-15)
    assert harness.attempts[-1][1] == D(-15)
    harness.assert_balanced()


def test_same_side_minimum_residual_and_zero_capital_are_explicit(harness):
    require_addition_legs([{"code": "rebalance_minimum"}])
    with pytest.raises(PlatformError, match="minimum size"):
        require_addition_legs([{"code": "minimum_size"}])
    with localcontext() as caller:
        caller.prec = 8
        residual = portfolio_residuals({"BTC-USDT": "1.123456789123456789"}, {}, harness.quotes, "10000")
    assert residual["notional"] == "112.345678912345678900"
    assert D(residual["capital_pct"]) == D("1.123456789123456789")
    require_residual_limit(residual, {"max_residual_pct": residual["capital_pct"]})
    assert portfolio_residuals({"BTC-USDT": 0}, {}, harness.quotes, 0)["capital_pct"] == "0"
    assert portfolio_residuals({"BTC-USDT": 1}, {}, harness.quotes, 0)["capital_pct"] == "100"


def test_unexpected_submit_failure_after_commit_preserves_fill_and_compensates(harness):
    original = harness.submit
    seen = []

    def committed_then_raise(symbol, quantity, reduce, key):
        result = original(symbol, quantity, reduce, key)
        if not reduce:
            seen.append(result)
            raise RuntimeError("Injected error after the book committed")
        return result

    harness.submit = committed_then_raise
    batch = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    assert batch["status"] == "compensated" and not harness.positions()
    assert batch["failure"]["code"] == "portfolio_execution_failure"
    assert len(seen) == 1 and len(harness.book.orders("example")) == 2
    failed = next(row for row in batch["trace"] if row["phase"] == "add")
    assert failed["status"] == "failed"  # Unknown callback failure cannot be labeled a no-fill rejection.
    assert D(harness.account()["fees_paid"]) == 8
    harness.assert_balanced()


def test_child_commands_use_balanced_round_robin_order_and_adverse_limit(harness):
    policy = harness.config | {"max_order_notional": "2500"}
    quantities = {"ETH-USDT": D(40), "BTC-USDT": D(40)}
    rows = portfolio_commands("add", quantities, harness.quotes, policy)
    assert [(r["sequence"], r["inst_id"], r["quantity"]) for r in rows] == [
        (0, "BTC-USDT", D(20)),
        (1, "ETH-USDT", D(20)),
        (2, "BTC-USDT", D(20)),
        (3, "ETH-USDT", D(20)),
    ]
    assert rows == portfolio_commands("add", dict(reversed(list(quantities.items()))), harness.quotes, policy)
    assert all(r["quantity"] * D("100.05") <= D(2500) for r in rows)
    assert split_addition(D(-40), harness.quotes["ETH-USDT"], policy) == [D(-20), D(-20)]
    with pytest.raises(PlatformError, match="twenty valid orders"):
        split_addition(D(600), harness.quotes["BTC-USDT"], policy)
    with pytest.raises(PlatformError, match="lot-aligned"):
        split_addition(D("40.005"), harness.quotes["BTC-USDT"], policy)
    with pytest.raises(PlatformError, match="twenty valid orders"):
        split_addition(D(40), harness.quotes["BTC-USDT"], policy | {"max_order_notional": "-1"})
    quote = harness.quotes["BTC-USDT"] | {
        "instrument": harness.quotes["BTC-USDT"]["instrument"] | {"min_size": "20"}
    }
    with pytest.raises(PlatformError, match="twenty valid orders"):
        split_addition(D(30), quote, policy)


def test_second_child_failure_closes_both_already_filled_legs(harness):
    harness.config["max_order_notional"] = "2500"
    harness.book.set_risk("example", {"max_order_notional": "2500"}, "fixture")
    harness.fail = lambda symbol, quantity, reduce, key: (
        reject("fixture_child_denied") if ":add:2:" in key else None
    )
    batch = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    assert batch["status"] == "compensated" and not harness.positions()
    assert batch["failure"]["code"] == "fixture_child_denied"
    assert [(s, q, reduce) for s, q, reduce, _ in harness.attempts] == [
        ("BTC-USDT", D(20), False),
        ("ETH-USDT", D(20), False),
        ("BTC-USDT", D(20), False),
        ("BTC-USDT", D(-20), True),
        ("ETH-USDT", D(-20), True),
    ]
    assert len(harness.book.orders("example")) == 4 and D(harness.account()["fees_paid"]) == 8
    assert batch["trace"][0]["child_commands"][2]["sequence"] == 2
    harness.assert_balanced()


def test_allocated_capital_includes_entry_fees_even_when_account_has_spare_cash(harness):
    harness.config["capital_pct"] = "70"
    batch = harness.execute({"BTC-USDT": D(35), "ETH-USDT": D(35)}, capital=7000)
    assert batch["status"] == "completed" and batch["capital_budget"]["budget_cash"] == "7000"
    assert D(batch["cash_scale"]) < 1 and D(harness.account()["available_cash"]) > D(3000)
    spent = D(10000) - D(harness.account()["cash"])
    assert spent <= D(7000)  # Spare account cash cannot fund an undeclared sleeve expansion.
    assert batch["capital_budget"]["commitment_pct"] == "70"
    assert len(harness.positions()) == 2
    harness.assert_balanced()


def test_zero_allocated_budget_retains_only_existing_same_side_maintenance_under_residual_limit(harness):
    first = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})
    assert first["status"] == "completed"
    harness.config["capital_pct"] = "80"
    for quote in harness.quotes.values():
        quote["instrument"]["min_size"] = ".02"
    before = harness.account(), len(harness.book.orders("example"))
    capital = D(before[0]["equity"]) * D(".8")
    result = harness.execute({"BTC-USDT": D("39.99"), "ETH-USDT": D("40.02")}, capital)
    assert result["status"] == "completed" and D(result["capital_budget"]["budget_cash"]) == 0
    assert {row["code"] for row in result["skipped"]} == {"rebalance_minimum", "rebalance_cash_rounding"}
    assert D(result["residuals"]["notional"]) == 3 and D(result["residuals"]["capital_pct"]) < D(".1")
    assert (harness.account(), len(harness.book.orders("example"))) == before
    harness.assert_balanced()


def test_cash_rounded_maintenance_still_fails_and_compensates_above_reviewed_residual(harness):
    assert harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(40)})["status"] == "completed"
    harness.config["capital_pct"] = "80"
    result = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(50)}, D(8000))
    assert result["status"] == "compensated" and result["failure"]["code"] == "portfolio_residual_limit"
    assert any(row["code"] == "rebalance_cash_rounding" for row in result["skipped"])
    assert not harness.positions() and len(harness.book.orders("example")) == 4
    harness.assert_balanced()


def test_existing_inventory_does_not_allow_a_new_cash_rounded_leg_to_be_skipped(harness):
    assert harness.execute({"BTC-USDT": D(40)})["status"] == "completed"
    harness.config["capital_pct"] = "40"
    result = harness.execute({"BTC-USDT": D(40), "ETH-USDT": D(1)}, D(4000))
    assert result["status"] == "compensated" and result["failure"]["code"] == "portfolio_minimum_leg"
    assert any(
        row["code"] == "minimum_size" and D(row["existing_quantity"]) == 0 for row in result["skipped"]
    )
    assert not harness.positions()
    harness.assert_balanced()
