"""Trader-facing current condition projection backed by the economic book."""

from decimal import Decimal as D

from test_managed_portfolios import activate
from test_managed_portfolios import runtime as runtime_fixture
from test_pro_execution import order, snapshot
from tidebench.store import now_ms

runtime = runtime_fixture


async def test_preparing_and_flat_failed_episode_stays_visible_until_disposition(runtime):
    group = await activate(runtime)
    timestamp = now_ms()
    with runtime.store.write() as conn:
        conn.execute(
            "UPDATE managed_portfolios SET last_error='Entry leg rejected',updated_at=? WHERE id=?",
            (timestamp, group["id"]),
        )
    projected = runtime.managed_portfolios.get(group["id"])
    assert projected["attention"]["phase"] == "preparing"
    assert projected["attention"]["since"] == timestamp
    assert projected["attention"]["inventory_notional"] == "0"
    runtime.monitor_conditions()
    incident = next(i for i in runtime.incidents.list() if i["subject"] == group["id"])
    runtime.incidents.acknowledge(incident["id"], "admin", "Investigating the rejected portfolio entry.")
    assert runtime.managed_portfolios.get(group["id"])["attention"]["incident_id"] == incident["id"]
    assert runtime.operations()["health"]["checks"]["database"] == "ok"
    assert runtime.operations()["economic_health"]["status"] == "degraded"
    with runtime.store.write() as conn:
        conn.execute("UPDATE managed_portfolios SET status='failed' WHERE id=?", (group["id"],))
    assert runtime.managed_portfolios.get(group["id"])["attention"]["phase"] == "failed"
    runtime.managed_portfolios.stop(group["id"], "trader")
    runtime.monitor_conditions()
    assert runtime.managed_portfolios.get(group["id"])["attention"] is None
    assert next(i for i in runtime.incidents.list() if i["id"] == incident["id"])["status"] == "resolved"


async def test_stop_retains_owner_inventory_and_unknown_value_until_actual_flat(runtime):
    group = await activate(runtime)
    leg = group["manifest"]["legs"][0]
    symbol = leg["inst_id"]
    quote = snapshot(symbol, price="100")
    runtime.snapshots[("example", symbol)] = quote
    runtime.book.submit(
        order(symbol, quantity="10", leverage=1),
        "attention:entry:123",
        {symbol: quote},
        "strategy:" + leg["deployment_id"],
    )
    with runtime.store.write() as conn:
        conn.execute(
            "UPDATE managed_portfolios SET status='compensating',last_error='Compensation quote unavailable' WHERE id=?",
            (group["id"],),
        )
    runtime.monitor_conditions()
    attention = runtime.managed_portfolios.get(group["id"])["attention"]
    assert attention["phase"] == "compensating"
    assert D(attention["inventory_notional"]) == 1000
    assert attention["inventory"][0]["quantity"] == "10"
    runtime.managed_portfolios.stop(group["id"], "trader")
    assert runtime.managed_portfolios.get(group["id"])["attention"]["phase"] == "retained_inventory"
    del runtime.snapshots[("example", symbol)]
    attention = runtime.managed_portfolios.get(group["id"])["attention"]
    assert attention["inventory_notional"] is None
    assert attention["inventory"][0]["market_value"] is None
    assert attention["valuation_status"] == "unavailable"
    runtime.book.submit(
        order(symbol, side="sell", quantity="10", reduce_only=True, leverage=1),
        "attention-close-123",
        {symbol: quote},
        "manual",
    )
    runtime.monitor_conditions()
    current = runtime.managed_portfolios.get(group["id"])
    assert current["attention"] is None
    assert current["capital_commitment"]["status"] == "released"
