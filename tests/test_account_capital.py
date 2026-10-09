"""Promises survive restart/stop and compete transactionally with marked risk."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest
from test_managed_portfolios import activate, approve, research
from test_managed_portfolios import runtime as runtime_fixture
from test_pro_execution import order, snapshot
from tidebench.account_capital import AccountCapital
from tidebench.platform import PlatformError
from tidebench.pro_execution import SimulationBook
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, dumps
from tidebench.strategy_registry import digest


@pytest.fixture
async def runtime(tmp_path):
    async for instance in runtime_fixture.__wrapped__(tmp_path):
        yield instance


@pytest.fixture
def book(tmp_path):
    result = SimulationBook(Store(tmp_path / "capital.sqlite3"))
    if not hasattr(result, "capital"):
        result.capital = AccountCapital(result.store, result.contributions)
    with result.store.write() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS managed_portfolios(id TEXT PRIMARY KEY,source TEXT,status TEXT,manifest TEXT,manifest_hash TEXT);
            CREATE TABLE IF NOT EXISTS portfolio_batches(id TEXT PRIMARY KEY,group_id TEXT);
            CREATE TABLE IF NOT EXISTS portfolio_commands(id TEXT PRIMARY KEY,batch_id TEXT,status TEXT);
        """)
    return result


def definition(capital=70, symbols=("BTC-USDT", "ETH-USDT")):
    return {"capital_pct": str(capital), "legs": [{"inst_id": s, "weight": ".5"} for s in symbols]}


def reserve(book, identifier, capital=70, symbols=("BTC-USDT", "ETH-USDT")):
    body = definition(capital, symbols)
    with book.store.write() as conn:
        manifest = {"definition": body}
        conn.execute(
            "INSERT INTO managed_portfolios VALUES(?,?,?,?,?)",
            (identifier, "example", "running", dumps(manifest), digest(manifest)),
        )
        return book.capital.reserve(conn, "example", "portfolio:" + identifier, body, "reviewer")


def priced_account(book, quotes=None):
    return book.account("example", quotes or {})


def test_concurrent_seventy_percent_promises_have_one_atomic_winner(book):
    def attempt(identifier, symbols):
        try:
            reserve(book, identifier, symbols=symbols)
            return "admitted"
        except PlatformError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda p: attempt(*p),
                [
                    ("a", ("BTC-USDT", "ETH-USDT")),
                    ("b", ("SOL-USDT", "DOGE-USDT")),
                ],
            )
        )
    assert sorted(outcomes) == ["account_capital_overcommitted", "admitted"]
    assert len(book.capital.commitments("example", active_only=True)) == 1
    with book.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM managed_portfolios").fetchone()[0] == 1


def test_failed_atomic_activation_rolls_back_its_promise(book):
    with pytest.raises(RuntimeError, match="deployment failed"), book.store.write() as conn:
        book.capital.reserve(conn, "example", "portfolio:a", definition(), "reviewer")
        raise RuntimeError("deployment failed")
    assert book.capital.get("example", "portfolio:a") is None


def test_promises_block_manual_use_of_reserved_capital_without_double_counting_owner(book):
    reserve(book, "a")
    account = priced_account(book)
    with book.store.write() as conn:
        evidence = book.capital.admit_order(
            conn, "example", "alice", account, snapshot("SOL-USDT", price="100")["instrument"], D(30), D(100)
        )
        assert D(evidence["capital_committed_or_used_pct"]) == 100
        with pytest.raises(PlatformError) as exc:
            book.capital.admit_order(
                conn,
                "example",
                "alice",
                account,
                snapshot("SOL-USDT", price="100")["instrument"],
                D(31),
                D(100),
            )
        assert exc.value.code == "account_capital_limit"
        config = {"group_id": "a"}
        conn.execute(
            "INSERT INTO pro_deployments VALUES('d','example','BTC-USDT',?,'running',NULL,NULL,0,0)",
            (dumps(config),),
        )
        evidence = book.capital.admit_order(
            conn,
            "example",
            "strategy:d",
            account,
            snapshot("BTC-USDT", price="100")["instrument"],
            D(30),
            D(100),
        )
        assert D(evidence["capital_committed_or_used_pct"]) == 70
        assert D(evidence["base_asset_gross_pct"]["BTC"]) == 35


def test_unreserved_or_oversized_portfolio_new_risk_is_rejected(book):
    with book.store.write() as conn:
        conn.execute(
            "INSERT INTO pro_deployments VALUES('d','example','BTC-USDT',?,'running',NULL,NULL,0,0)",
            (dumps({"group_id": "a"}),),
        )
        with pytest.raises(PlatformError) as exc:
            book.capital.admit_order(
                conn,
                "example",
                "strategy:d",
                priced_account(book),
                snapshot("BTC-USDT", price="100")["instrument"],
                D(1),
                D(100),
            )
        assert exc.value.code == "account_capital_unreserved"
    reserve(book, "a", capital=20)
    with book.store.write() as conn, pytest.raises(PlatformError) as exc:
        book.capital.admit_order(
            conn,
            "example",
            "strategy:d",
            priced_account(book),
            snapshot("BTC-USDT", price="100")["instrument"],
            D(21),
            D(100),
        )
    assert exc.value.code == "portfolio_capital_limit"


def test_same_underlying_spot_and_swap_gross_add_even_with_opposite_delta(book):
    spot = snapshot("BTC-USDT", price="100")
    swap = snapshot("BTC-USDT-SWAP", price="100")
    # This synthetic oracle uses reconciled book fills, not invented quantities.
    book.set_risk("example", {"max_order_notional": "10000", "max_leverage": 10}, "risk")
    book.submit(
        order("BTC-USDT", quantity="60", leverage=1), "spot-entry-capital", {"BTC-USDT": spot}, "alice"
    )
    quotes = {"BTC-USDT": spot, "BTC-USDT-SWAP": swap}
    account = priced_account(book, quotes)
    with book.store.write() as conn:
        with pytest.raises(PlatformError) as exc:
            book.capital.admit_order(
                conn, "example", "hedger", account, swap["instrument"], D(-4500), D(100), leverage=10
            )
        assert exc.value.code == "account_base_asset_limit"
    book.capital.set_policy("example", {"max_base_asset_gross_pct": "150"}, "risk")
    with book.store.write() as conn:
        evidence = book.capital.admit_order(
            conn, "example", "hedger", account, swap["instrument"], D(-4500), D(100), leverage=10
        )
        assert 100 < D(evidence["base_asset_gross_pct"]["BTC"]) < 150


def test_pending_orders_preserve_original_owner_and_reserve_future_gross(book):
    quote = snapshot("BTC-USDT-SWAP", price="100")
    payload = order("BTC-USDT-SWAP", quantity="100", leverage=10, order_type="limit", limit_price="99")
    pending = book.submit(payload, "capital-pending-order", {"BTC-USDT-SWAP": quote}, "alice")
    assert pending["status"] == "pending"
    with book.store.write() as conn:
        evidence = book.capital.exposure(conn, "example", priced_account(book))
        assert any(
            r["owner"] == "manual:alice" and D(r["actual_capital_pct"]) > 0 for r in evidence["owners"]
        )
        excluded = book.capital.exposure(
            conn, "example", priced_account(book), exclude_order_id=pending["id"]
        )
        assert excluded["owners"] == []


def test_policy_persists_and_promises_are_not_erased_by_tighter_limit(book):
    reserve(book, "a")
    book.capital.set_policy("example", {"max_base_asset_gross_pct": "30"}, "risk")
    restored = AccountCapital(book.store, book.contributions)
    assert restored.policy("example")["max_base_asset_gross_pct"] == "30"
    assert restored.get("example", "portfolio:a")["status"] == "reserved"
    with book.store.write() as conn, pytest.raises(PlatformError) as exc:
        restored.admit_order(
            conn,
            "example",
            "alice",
            priced_account(book),
            snapshot("SOL-USDT", price="100")["instrument"],
            D(1),
            D(100),
        )
    assert exc.value.code == "account_base_asset_limit"
    with pytest.raises(PlatformError):
        restored.set_policy("example", {"capital_limit_pct": 200}, "risk")


def test_tampered_capital_evidence_fails_closed(book):
    reserve(book, "a")
    with book.store.write() as conn:
        conn.execute(
            "UPDATE account_capital_commitments SET body=? WHERE owner='portfolio:a'",
            (dumps({"source": "example", "owner": "portfolio:a", "capital_pct": "0"}),),
        )
    with pytest.raises(PlatformError) as exc:
        book.capital.preview("example", definition(30, ("SOL-USDT", "DOGE-USDT")))
    assert exc.value.code == "account_capital_integrity"


def test_legacy_overcommit_is_preserved_and_blocks_new_admission(book):
    for identifier, symbols in [("a", ("BTC-USDT", "ETH-USDT")), ("b", ("SOL-USDT", "DOGE-USDT"))]:
        manifest = {"definition": definition(70, symbols)}
        with book.store.write() as conn:
            conn.execute(
                "INSERT INTO managed_portfolios VALUES(?,?,?,?,?)",
                (identifier, "example", "running", dumps(manifest), digest(manifest)),
            )
    with book.store.write() as conn:
        book.capital.bootstrap(conn)
    rows = book.capital.commitments("example", active_only=True)
    assert sum(D(r["body"]["capital_pct"]) for r in rows) == 140
    assert all(r["body"]["legacy_backfill"] for r in rows)
    assert "account_capital_overcommitted" in book.capital.preview("example", definition(1))["blockers"]


async def test_stop_retains_owned_inventory_across_restart_then_verified_close_releases(runtime):
    r = runtime
    group = await activate(r, capital_pct=70)
    await r.managed_portfolios.evaluate(group["id"])
    assert r.book.positions("example")
    r.managed_portfolios.stop(group["id"], "trader")
    retained = r.managed_portfolios.get(group["id"])["capital_commitment"]
    assert retained["status"] == "retained"
    assert retained["retention_reason"] == "owned_inventory"
    await r.stop()
    restored = ProfessionalRuntime(Store(r.settings.database), r.market, r.settings)
    try:
        assert restored.managed_portfolios.get(group["id"])["capital_commitment"]["status"] == "retained"
        positions = restored.book.positions("example")
        quotes = await restored.snapshots_for("example", [p["inst_id"] for p in positions])
        for position in positions:
            restored.book.submit(
                order(
                    position["inst_id"],
                    side="sell",
                    quantity=position["quantity"],
                    leverage=1,
                    reduce_only=True,
                ),
                "operator-close-" + position["inst_id"],
                quotes,
                "risk-operator",
            )
        with restored.store.write() as conn:
            restored.book.capital.reconcile("example", conn)
        released = restored.managed_portfolios.get(group["id"])["capital_commitment"]
        assert released["status"] == "released"
        assert not restored.book.positions("example")
    finally:
        await restored.stop()


async def test_flat_stop_releases_and_pending_funding_ownership_retains(runtime):
    r = runtime
    group = await activate(r, capital_pct=70)
    owner = "portfolio:" + group["id"]
    with r.store.write() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pro_funding_obligations(source TEXT,inst_id TEXT,ts INTEGER,body TEXT,content_hash TEXT,status TEXT,payment TEXT,settled_at INTEGER,PRIMARY KEY(source,inst_id,ts))
        """)
        body = {
            "source": "example",
            "inst_id": "BTC-USDT-SWAP",
            "owners": {owner: "1"},
            "quantity": "1",
            "observed_at": 1,
            "settlement_ts": 1,
            "metadata": {"inst_type": "SWAP"},
        }
        conn.execute(
            "INSERT INTO pro_funding_obligations VALUES('example','BTC-USDT-SWAP',1,?,?,'pending',NULL,NULL)",
            (dumps(body), digest(body)),
        )
    stopped = r.managed_portfolios.stop(group["id"], "trader")
    assert stopped["capital_commitment"]["status"] == "retained"
    assert stopped["capital_commitment"]["retention_reason"] == "unsettled_funding"
    with r.store.write() as conn:
        conn.execute("UPDATE pro_funding_obligations SET status='settled',payment='0',settled_at=2")
        r.book.capital.reconcile("example", conn)
    assert r.managed_portfolios.get(group["id"])["capital_commitment"]["status"] == "released"


async def test_release_preview_exposes_and_binds_account_promise_admission(runtime):
    r = runtime
    await activate(r, capital_pct=70)
    run = await research(
        r,
        capital_pct=40,
        legs=[
            {"inst_id": s, "weight": ".5", "strategy": {"kind": "buy_hold"}}
            for s in ("SOL-USDT", "DOGE-USDT")
        ],
    )
    preview = r.portfolio_releases.preview(run["id"])
    assert "account_capital_overcommitted" in preview["blockers"]
    assert D(preview["capital_admission"]["projected_committed_capital_pct"]) == 110
    with pytest.raises(PlatformError):
        approve(r, run)


def test_cash_budget_reserves_all_fees_instead_of_spending_other_sleeves(book):
    from tidebench.account_capital import capital_cash_budget
    from tidebench.portfolio_targets import addition_plan

    account = priced_account(book)
    budget = capital_cash_budget(account, 70, 0)
    quotes = {s: snapshot(s, price="100") for s in ("BTC-USDT", "ETH-USDT")}
    plan = addition_plan(
        {s: D(35) for s in quotes},
        {},
        quotes,
        budget["budget_cash"],
        {s: 1 for s in quotes},
        100,
        0,
    )
    gross = sum(plan.quantities.values()) * 100
    fees = gross / 100
    assert gross + fees <= D(7000)
    assert gross <= (D(account["equity"]) - fees) * D(".7")
    assert D(plan.cash_scale) < 1
    # Existing marked capital and pending promises consume exactly the same
    # budget, rather than treating another group's cash as available.
    used = capital_cash_budget(account, 70, 6500)
    assert D(used["budget_cash"]) == 500
    assert D(capital_cash_budget(account, 70, 7100)["budget_cash"]) == 0


def test_unattributed_or_damaged_pending_funding_cannot_release_terminal_promise(book):
    reserve(book, "a")
    with book.store.write() as conn:
        conn.execute("UPDATE managed_portfolios SET status='stopped' WHERE id='a'")
        conn.execute("""CREATE TABLE IF NOT EXISTS pro_funding_obligations(
            source TEXT,inst_id TEXT,ts INTEGER,body TEXT,content_hash TEXT,status TEXT,
            payment TEXT,settled_at INTEGER,PRIMARY KEY(source,inst_id,ts)
        )""")
        body = {
            "source": "example",
            "inst_id": "BTC-USDT",
            "owners": None,
            "quantity": "1",
            "observed_at": 1,
            "settlement_ts": 1,
            "metadata": {"inst_type": "SWAP"},
        }
        conn.execute(
            "INSERT INTO pro_funding_obligations VALUES('example','BTC-USDT',1,?,?,'pending',NULL,NULL)",
            (dumps(body), digest(body)),
        )
        result = book.capital.terminal("example", "portfolio:a", conn)
        assert result["status"] == "retained"
        assert result["retention_reason"] == "unsettled_funding"
        conn.execute("UPDATE pro_funding_obligations SET body='invalid'")
        result = book.capital.terminal("example", "portfolio:a", conn)
        assert result["status"] == "retained"
        assert result["retention_reason"] == "funding_evidence_unverified"


async def test_damaged_capital_evidence_does_not_block_protective_controller_stop(runtime):
    r = runtime
    group = await activate(r, capital_pct=70)
    with r.store.write() as conn:
        conn.execute(
            "UPDATE account_capital_commitments SET body='invalid' WHERE owner=?",
            ("portfolio:" + group["id"],),
        )
    shown = r.managed_portfolios.get(group["id"])
    assert shown["capital_integrity_error"]["code"] == "account_capital_integrity"
    stopped = r.managed_portfolios.stop(group["id"], "risk-operator")
    assert stopped["status"] == "stopped"
    assert all(d["status"] == "stopped" for d in r.deployments("example"))
    with r.store.read() as conn:
        assert (
            conn.execute(
                "SELECT status FROM account_capital_commitments WHERE owner=?", ("portfolio:" + group["id"],)
            ).fetchone()[0]
            == "retained"
        )


async def test_failed_historical_execution_is_not_promotable(runtime, monkeypatch):
    r = runtime
    run = await research(r, capital_pct=30)
    failed = dict(run) | {"result": dict(run["result"]) | {"execution_status": "failed"}}
    monkeypatch.setattr(r.portfolios, "get", lambda identifier: failed if identifier == run["id"] else run)
    preview = r.portfolio_releases.preview(run["id"])
    assert "research_execution_failed" in preview["blockers"]
    with pytest.raises(PlatformError):
        approve(r, failed)


def test_promise_and_cash_budget_ignore_caller_decimal_precision(book):
    from decimal import localcontext

    from tidebench.account_capital import capital_cash_budget

    body = {
        "capital_pct": "67.123456789123456789",
        "legs": [
            {"inst_id": "BTC-USDT", "weight": ".123456789123456789"},
            {"inst_id": "BTC-USDT-SWAP", "weight": "-.123456789123456789"},
            {"inst_id": "ETH-USDT", "weight": ".234567891234567891"},
        ],
    }
    with localcontext() as ctx:
        ctx.prec = 6
        low = book.capital.promise("example", "portfolio:a", body)
        low_budget = capital_cash_budget(priced_account(book), body["capital_pct"], "123.123456789123456789")
    with localcontext() as ctx:
        ctx.prec = 60
        high = book.capital.promise("example", "portfolio:a", body)
        high_budget = capital_cash_budget(priced_account(book), body["capital_pct"], "123.123456789123456789")
    assert low == high
    assert low_budget == high_budget


async def test_release_binds_order_size_and_underlying_gross_policy_differences(runtime):
    r = runtime
    run = await research(
        r,
        capital_pct=30,
        research_policy={"max_order_notional": "5000", "max_base_asset_gross_pct": "150"},
    )
    preview = r.portfolio_releases.preview(run["id"])
    differences = {item["field"]: item for item in preview["risk_differences"]}
    assert differences["max_order_notional"] == {
        "field": "max_order_notional",
        "research": "5000",
        "execution": "2500",
    }
    assert differences["max_base_asset_gross_pct"] == {
        "field": "max_base_asset_gross_pct",
        "research": "150",
        "execution": "100",
    }
    assert "execution_risk_difference" in preview["required_acknowledgements"]
    assert preview["capital_policy_hash"] == digest(preview["capital_admission"]["policy"])
    with pytest.raises(PlatformError) as exc:
        r.portfolio_releases.approve(
            {
                "run_id": run["id"],
                "preview_hash": preview["preview_hash"],
                "review": "Reviewed historical results and sequential risk only.",
                "acknowledgements": [
                    item
                    for item in preview["required_acknowledgements"]
                    if item != "execution_risk_difference"
                ],
            },
            "trader",
        )
    assert exc.value.code == "portfolio_release_ack"
    release = approve(r, run)
    r.book.capital.set_policy("example", {"max_base_asset_gross_pct": "150"}, "risk-reviewer")
    with pytest.raises(PlatformError) as exc:
        r.portfolio_releases.activate(release["id"], "trader")
    assert exc.value.code == "portfolio_release_changed"
    assert not r.deployments()
    fresh = r.portfolio_releases.preview(run["id"])
    assert fresh["capital_policy_hash"] != preview["capital_policy_hash"]
    assert "max_base_asset_gross_pct" not in {item["field"] for item in fresh["risk_differences"]}


@pytest.mark.parametrize("product", ["SPOT", "SWAP"])
def test_candidate_costs_use_post_fill_equity_for_capital_admission(book, product):
    reserve(book, "a", capital=70)
    symbol = "SOL-USDT" + ("-SWAP" if product == "SWAP" else "")
    quote = snapshot(symbol, price="100")
    if product == "SWAP":
        quote["instrument"].update(ct_val="1", ct_mult="1", ct_val_ccy="SOL", base="SOL")
    else:
        quote["instrument"]["base"] = "SOL"
    book.set_risk("example", {"fee_bps": "100", "slippage_bps": "0", "max_order_notional": "10000"}, "audit")
    request = order(symbol, quantity="30", leverage=1)
    with pytest.raises(PlatformError) as failed:
        book.submit(request, "post-fee-overcommit", {symbol: quote}, "alice")
    assert failed.value.code == "account_capital_limit"
    assert book.orders("example") == [] and book.positions("example") == []
    accepted = book.submit(
        order(symbol, quantity="29", leverage=1), "cost-safe-use", {symbol: quote}, "alice"
    )
    assert accepted["status"] == "filled"
    account = book.account("example", {symbol: quote})
    with book.store.read() as conn:
        exposure = book.capital.exposure(conn, "example", account)
    assert D(exposure["capital_committed_or_used_pct"]) <= 100


def test_fill_to_mark_loss_cannot_bypass_promised_capital_limit(book):
    reserve(book, "a", capital=70)
    quote = snapshot("SOL-USDT", price="100")
    quote["ask"] = "102"
    quote["instrument"]["base"] = "SOL"
    book.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "max_order_notional": "10000"}, "audit")
    with pytest.raises(PlatformError) as failed:
        book.submit(
            order("SOL-USDT", quantity="30", leverage=1), "spread-overcommit", {"SOL-USDT": quote}, "alice"
        )
    assert failed.value.code == "account_capital_limit"


def test_negative_isolated_margin_cannot_fund_other_markets_and_protection_stays_available(book):
    symbol = "BTC-USDT-SWAP"
    quote = snapshot(symbol, ts=1000000)
    book.now = lambda: 1000000
    book.set_risk(
        "example",
        {
            "fee_bps": "0",
            "slippage_bps": "0",
            "liquidation_fee_bps": "0",
            "max_order_notional": "10000",
            "max_leverage": 50,
        },
        "audit",
    )
    book.submit(
        order(symbol, quantity="100", leverage=50), "original-high-leverage", {symbol: quote}, "alice"
    )
    book.now = lambda: 2000000
    current = dict(quote, ts=2000000, mark_ts=2000000)
    book.settle_funding(
        "example",
        symbol,
        [{"ts": 2000000, "rate": ".03", "mark_price": "100", "mark_ts": 2000000}],
        {symbol: current},
    )
    spot = snapshot("SOL-USDT", ts=2000000)
    spot["instrument"]["base"] = "SOL"
    quotes = {symbol: current, "SOL-USDT": spot}
    account = book.account("example", quotes)
    assert D(account["positions"][0]["margin"]) == -1
    with book.store.read() as conn:
        evidence = book.capital.exposure(conn, "example", account)
    assert all(D(owner["actual_capital_pct"]) >= 0 for owner in evidence["owners"])
    for actor in ("alice", "bob"):
        with pytest.raises(PlatformError) as failed:
            book.submit(order("SOL-USDT", quantity="49.99"), "blocked-other-" + actor, quotes, actor)
        assert failed.value.code == "existing_margin_breach"
    assert len(book.orders("example")) == 1
    closed = book.submit(
        order(symbol, side="sell", quantity="100", leverage=50, reduce_only=True),
        "protect-insolvent-isolated",
        quotes,
        "alice",
    )
    assert closed["status"] == "filled" and not book.positions("example")


def test_new_promise_counts_manual_capital_on_other_markets_before_reservation(book):
    quote = snapshot("SOL-USDT")
    quote["instrument"]["base"] = "SOL"
    book.set_risk("example", {"fee_bps": "0", "slippage_bps": "0", "max_order_notional": "10000"}, "audit")
    book.submit(order("SOL-USDT", quantity="30"), "existing-unrelated-manual", {"SOL-USDT": quote}, "alice")
    account = book.account("example", {"SOL-USDT": quote})
    refused = book.capital.preview("example", definition(80), account=account)
    assert "account_capital_overcommitted" in refused["blockers"]
    assert D(refused["actual_admission"]["capital_committed_or_used_pct"]) == 110
    with pytest.raises(PlatformError) as failed, book.store.write() as conn:
        book.capital.reserve(
            conn, "example", "portfolio:overpromise", definition(80), "audit", account=account
        )
    assert failed.value.code == "account_capital_overcommitted"
    assert book.capital.get("example", "portfolio:overpromise") is None
    assert book.capital.preview("example", definition(60), account=account)["blockers"] == []
    with book.store.write() as conn:
        book.capital.reserve(
            conn, "example", "portfolio:validpromise", definition(60), "audit", account=account
        )
    with book.store.read() as conn:
        assert D(book.capital.exposure(conn, "example", account)["capital_committed_or_used_pct"]) == 90


def test_new_promise_cannot_ignore_unavailable_other_market_marks(book):
    account = book.account("example", {}) | {"equity": None, "valuation_status": "unavailable"}
    assert (
        "account_capital_valuation"
        in book.capital.preview("example", definition(60), account=account)["blockers"]
    )
    with pytest.raises(PlatformError) as failed, book.store.write() as conn:
        book.capital.reserve(conn, "example", "portfolio:unvalued", definition(60), "audit", account=account)
    assert failed.value.code == "account_capital_valuation"
