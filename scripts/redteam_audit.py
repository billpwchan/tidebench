"""Offline, isolated adversarial observations against the installed workbench.

Run from a source checkout with development dependencies. This deliberately
reports observed product limitations; an observation is not a passing release
acceptance or a claim about exchange execution. No venue endpoint is called.
"""

import asyncio
import copy
import hashlib
import json
import tempfile
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import ValidationError
from tidebench import __version__
from tidebench.catalog import CatalogService
from tidebench.config import Settings
from tidebench.engine import Candle, StrategyConfig
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.pro_research import ResearchConfig, run_research_plan
from tidebench.pro_service import ProfessionalRuntime
from tidebench.provenance import serialized_result
from tidebench.schemas import StrategyInput
from tidebench.store import Store

D = Decimal
END = 1767225600000
HOUR = 3_600_000
SYMBOL = "BTC-USDT-SWAP"


def snapshot(symbol=SYMBOL, price="100"):
    swap = symbol.endswith("-SWAP")
    return {
        "source": "example",
        "inst_id": symbol,
        "ts": END,
        "mark_ts": END,
        "bid": price,
        "ask": price,
        "last": price,
        "mark": price,
        "funding_time": END + 60_000,
        "next_funding_time": END + 120_000,
        "margin_tiers": [
            {
                "tier": 1,
                "min_size": "0",
                "max_size": "100000",
                "imr": ".01",
                "mmr": ".004",
                "max_leverage": "100",
            }
        ],
        "instrument": {
            "inst_id": symbol,
            "inst_type": "SWAP" if swap else "SPOT",
            "base": symbol.split("-")[0],
            "quote": "USDT",
            "settle_ccy": "USDT" if swap else "",
            "ct_type": "linear" if swap else None,
            "ct_val": ".01" if swap else None,
            "ct_mult": "1" if swap else None,
            "ct_val_ccy": symbol.split("-")[0] if swap else None,
            "tick_size": ".01",
            "lot_size": ".01",
            "min_size": ".01",
            "state": "live",
        },
    }


def order(**changes):
    return {
        "source": "example",
        "inst_id": SYMBOL,
        "side": "buy",
        "quantity": "100",
        "leverage": 1,
        "reduce_only": False,
        "order_type": "market",
        "margin_mode": "isolated",
        **changes,
    }


class FaultCatalog:
    """Only provider/catalog transport is replaced; storage and book are real."""

    def __init__(self):
        self.price = "100"
        self.calls = 0
        self.jobs = 0
        self.history_entered = asyncio.Event()
        self.release_history = asyncio.Event()
        self.release_history.set()
        self.candles = [
            Candle(END - (500 - i) * HOUR, D(100), D(100), D(100), D(100), D(1)) for i in range(500)
        ]

    async def get_market_snapshot(self, symbol, source):
        assert source == "example"
        self.calls += 1
        return snapshot(symbol, self.price)

    def create_job(self, *args):
        self.jobs += 1
        return {"id": str(self.jobs)}

    async def run_job(self, identifier):
        self.history_entered.set()
        await self.release_history.wait()
        return {"status": "completed", "dataset_id": "captured-example"}

    def load_candles(self, identifier):
        return copy.copy(self.candles)

    async def funding_history(self, *args):
        return []

    async def stop_polling(self):
        pass


def deployment(runtime):
    return runtime.deploy(
        {
            "source": "example",
            "inst_id": SYMBOL,
            "bar": "1H",
            "direction": "short_only",
            "leverage": 1,
            "allocation": ".1",
            "strategy": asdict(StrategyConfig(kind="buy_hold", allocation=D(".1"))),
        },
        "audit-user",
    )


def runtime_at(directory, market):
    settings = Settings(data_dir=directory, _env_file=None)
    runtime = ProfessionalRuntime(Store(settings.database), market, settings)
    runtime.catalog = FaultCatalog()
    runtime.book.set_risk(
        "example", {"fee_bps": "0", "slippage_bps": "0", "liquidation_fee_bps": "0"}, "audit"
    )
    return runtime


async def observe(directory, market):
    findings = []
    runtime = runtime_at(directory / "adoption", market)
    runtime.book.submit(order(), "audit-manual-open", {SYMBOL: snapshot()}, "audit-user")
    admission_error = None
    try:
        deployed = deployment(runtime)
        await runtime.evaluate(deployed)
    except PlatformError as exc:
        admission_error = exc.code
        deployed = None
    positions = runtime.book.positions("example")
    findings.append(
        {
            "id": "RT-02",
            "observation": "starting_strategy_adopts_and_reverses_existing_manual_inventory",
            "manual_quantity_before": "100",
            "quantity_after": positions[0]["quantity"],
            "strategy_close_orders": sum(
                1
                for row in runtime.book.orders("example")
                if row.get("actor", "").startswith("strategy:") and row.get("reduce_only")
            ),
            "explicit_adoption_field_in_saved_config": bool(
                deployed and "adopt_position" in deployed["config"]
            ),
            "deployment_admission_error": admission_error,
        }
    )
    await runtime.stop()

    runtime = runtime_at(directory / "clock", market)
    deployed = deployment(runtime)
    await runtime.evaluate(deployed)
    jobs = runtime.catalog.jobs
    await runtime.evaluate(deployed)
    findings.append(
        {
            "id": "RT-06",
            "observation": "example_strategy_clock_does_not_advance",
            "history_jobs_after_first_and_second_evaluation": [jobs, runtime.catalog.jobs],
            "last_evaluated_bar": runtime.deployments()[0]["last_bar"],
        }
    )
    await runtime.stop()

    runtime = runtime_at(directory / "pending", market)
    pending = runtime.book.submit(
        order(order_type="limit", limit_price="95"),
        "audit-pending-before",
        {SYMBOL: snapshot()},
        "audit-user",
    )
    admission_error = None
    try:
        deployed = deployment(runtime)
        filled = runtime.book.submit(
            order(order_type="limit", limit_price="95"),
            "audit-pending-before",
            {SYMBOL: snapshot(price="90")},
            "pending-order",
            pending_id=pending["id"],
        )
    except PlatformError as exc:
        admission_error = exc.code
        deployed, filled = None, runtime.book.orders("example")[0]
    findings.append(
        {
            "id": "RT-03",
            "observation": "preexisting_manual_pending_order_fills_after_strategy_takes_market",
            "active_strategy": deployed["status"] if deployed else None,
            "deployment_admission_error": admission_error,
            "pending_order_status_after": filled["status"],
            "quantity_after": next((p["quantity"] for p in runtime.book.positions("example")), "0"),
        }
    )
    await runtime.stop()

    runtime = runtime_at(directory / "scheduler", market)
    deployed = deployment(runtime)
    runtime.book.submit(
        order(),
        f"strategy:{deployed['id']}:open:{END - 2 * HOUR}",
        {SYMBOL: snapshot()},
        f"strategy:{deployed['id']}",
    )
    pending = runtime.book.submit(
        order(side="sell", reduce_only=True, order_type="stop_market", stop_price="95"),
        "audit-stop-before",
        {SYMBOL: snapshot()},
        "audit-user",
    )
    runtime.catalog.release_history.clear()
    task = asyncio.create_task(runtime.execution_loop())
    strategy_task = (
        asyncio.create_task(runtime.strategy_loop()) if hasattr(runtime, "strategy_loop") else None
    )
    try:
        await asyncio.wait_for(runtime.catalog.history_entered.wait(), 2)
        calls_before = runtime.catalog.calls
        runtime.catalog.price = "90"
        started = time.monotonic()
        await asyncio.sleep(6)
        findings.append(
            {
                "id": "RT-01",
                "observation": "strategy_history_wait_blocks_pending_order_and_risk_polling",
                "injected_wait_ms": round((time.monotonic() - started) * 1000),
                "quote_reads_during_wait": runtime.catalog.calls - calls_before,
                "stop_order_status_during_triggered_price": next(
                    row for row in runtime.book.orders("example") if row["id"] == pending["id"]
                )["status"],
                "task_alive": not task.done(),
            }
        )
    finally:
        task.cancel()
        tasks = [task]
        if strategy_task:
            strategy_task.cancel()
            tasks.append(strategy_task)
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.stop()

    runtime = runtime_at(directory / "actor", market)
    deployment(runtime)
    outcomes = []
    for actor in ["pending-order", "risk-engine"]:
        try:
            runtime.book.submit(order(), f"audit-actor-{actor}", {SYMBOL: snapshot()}, actor)
            outcomes.append({"username": actor, "manual_order_accepted": True})
        except Exception as exc:
            outcomes.append({"username": actor, "manual_order_accepted": False, "error": str(exc)})
    findings.append(
        {
            "id": "RT-10",
            "observation": "reserved_actor_usernames_bypass_manual_strategy_ownership",
            "outcomes": outcomes,
            "caveat": "These names satisfy the workspace username policy; this is a local simulation ownership bypass, not exchange trading access.",
        }
    )
    await runtime.stop()

    runtime = runtime_at(directory / "fanout", market)
    quotes = {symbol: snapshot(symbol) for symbol in ["BTC-USDT", "ETH-USDT", "SOL-USDT"]}
    for index, symbol in enumerate(quotes):
        runtime.book.submit(order(inst_id=symbol, quantity="1"), f"audit-held-{index}", quotes, "audit-user")
    runtime.catalog.calls = 0
    for symbol in quotes:
        await runtime.snapshots_for("example", [symbol])
    findings.append(
        {
            "id": "RT-07",
            "observation": "execution_per_market_refresh_revisits_entire_portfolio",
            "held_markets": 3,
            "logical_snapshot_dispatches": runtime.catalog.calls,
            "caveat": "Logical calls, not measured venue HTTP calls; the 1.5s provider cache can coalesce fast repeats.",
        }
    )
    await runtime.stop()

    catalog = CatalogService(Store(directory / "storage.sqlite3"), market)
    for shift in range(5):
        job = catalog.create_job(
            "BTC-USDT", "trade", "1H", END - 2000 * HOUR + shift * HOUR, END + shift * HOUR, "example"
        )
        result = await catalog.run_job(job["id"])
        assert result["status"] == "completed", result
    with catalog.store.read() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM catalog_records").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT ts) FROM catalog_records").fetchone()[0]
        bytes_used = (
            conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]
        )
    findings.append(
        {
            "id": "RT-04",
            "observation": "five_overlapping_forward_history_captures_duplicate_records",
            "windows": 5,
            "bars_per_window": 2000,
            "stored_records": rows,
            "distinct_bar_timestamps": distinct,
            "database_allocated_bytes": bytes_used,
            "caveat": "Local synthetic catalog measurement; no cloud/storage capacity extrapolation is claimed.",
        }
    )

    trade = catalog.list_datasets("example")[-1]
    candles = catalog.load_candles(trade["id"])[-240:]
    instrument = await market.get_instruments("example")
    instrument = next(item for item in instrument if item.inst_id == "BTC-USDT")
    config = ResearchConfig(strategy=StrategyConfig(kind="sma_cross", fast=5, slow=20))
    fixed = run_research_plan(
        candles, HOUR, instrument, config, mode="train_test", options={"train_fraction": 0.7}
    )
    selected = run_research_plan(
        candles,
        HOUR,
        instrument,
        config,
        mode="train_test",
        options={"train_fraction": 0.7, "grid": {"fast": [5, 8], "slow": [20]}},
    )
    findings.append(
        {
            "id": "RT-05",
            "observation": "oos_api_supports_candidates_but_ui_omits_grid_in_oos_modes",
            "training_candidates_without_grid": len(fixed["folds"][0]["training_experiments"]),
            "training_candidates_with_grid": len(selected["folds"][0]["training_experiments"]),
            "ui_evidence": "Inspect ProResearch and browser acceptance separately; this observation measures engine candidate handling only.",
        }
    )
    result = run_research_plan(
        candles,
        HOUR,
        instrument,
        config,
        mode="grid",
        options={"grid": {"fast": [5, 8, 10, 12], "slow": [20]}},
    )
    payload, _ = serialized_result(result)
    findings.append(
        {
            "id": "RT-08",
            "observation": "research_artifact_contains_full_result_and_input_arrays_per_candidate",
            "cases": 4,
            "bars_per_case": len(candles),
            "json_bytes": len(payload.encode()),
            "bytes_per_bar_case": round(len(payload.encode()) / (4 * len(candles)), 1),
            "caveat": "Small measured artifact only; no OOM at the maximum budget was injected.",
        }
    )
    rejected = []
    for kind in ["trend_breakout", "funding_carry", "cross_sectional_momentum"]:
        try:
            StrategyInput(kind=kind)
        except ValidationError:
            rejected.append(kind)
    findings.append(
        {
            "id": "RT-09",
            "observation": "only_three_builtin_strategy_kinds_are_supported",
            "rejected_examples": rejected,
            "caveat": "These are absent capabilities; adding names alone would not implement their required data and portfolio semantics.",
        }
    )
    await catalog.stop_polling()
    return findings


async def main():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(RuntimeError("Network forbidden in offline audit"))
        )
    ) as client:
        market = MarketService(client=client)
        with tempfile.TemporaryDirectory(prefix="tidebench-redteam-") as directory:
            findings = await observe(Path(directory), market)
            root = Path(__file__).resolve().parents[1]
            observed_files = [
                "backend/tidebench/__init__.py",
                "backend/tidebench/pro_service.py",
                "backend/tidebench/pro_execution.py",
                "backend/tidebench/main.py",
                "backend/tidebench/schemas.py",
                "frontend/src/pages/ProResearch.tsx",
                "frontend/src/pages/Portfolio.tsx",
            ]
            print(
                json.dumps(
                    {
                        "audited_version": __version__,
                        "stage": "working-tree-observation",
                        "implementation_sha256": {
                            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                            for name in observed_files
                        },
                        "data_source": "isolated synthetic fixtures",
                        "venue_requests": 0,
                        "observations": findings,
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
