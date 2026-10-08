"""Fixed, disclosed spot research battery; no winner selection or alpha claim.

Downloads use public endpoints into an isolated SQLite capture retained under
--capture-dir. Output contains hashes and all cases, never private credentials.
"""

import argparse
import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from tidebench.catalog import CatalogService
from tidebench.engine import Instrument, StrategyConfig
from tidebench.market import MarketService
from tidebench.pro_research import ResearchConfig, json_safe, run_professional_backtest
from tidebench.provenance import research_identity
from tidebench.store import Store, dumps, now_ms
from tidebench.strategy_program import ProStrategyInput
from tidebench.strategy_registry import digest

INTERVAL = 4 * 3600000


def cases():
    base = StrategyConfig(allocation=D(".2"))
    trend = StrategyConfig(
        **ProStrategyInput(
            kind="ts_momentum",
            allocation=".2",
            stop_loss_pct=8,
            trailing_stop_pct=12,
            risk_per_trade_pct=".5",
        ).model_dump()
    )
    reversion = StrategyConfig(
        **ProStrategyInput(
            kind="regime_reversion",
            allocation=".2",
            window=48,
            stop_loss_pct=5,
            max_holding_bars=12,
            risk_per_trade_pct=".5",
        ).model_dump()
    )
    return {
        "equal_allocation_buy_hold": replace(base, kind="buy_hold"),
        "sma_20_80_reference": replace(base, fast=20, slow=80),
        "momentum_42_84_168": trend,
        "momentum_neighbor_36_72_144": replace(trend, momentum_horizons=[36, 72, 144]),
        "momentum_neighbor_48_96_192": replace(trend, momentum_horizons=[48, 96, 192]),
        "reversion_efficiency_035": reversion,
        "reversion_neighbor_025": replace(reversion, efficiency_max=D(".25")),
        "reversion_neighbor_045": replace(reversion, efficiency_max=D(".45")),
        "reversion_filter_ablation": replace(reversion, efficiency_max=D(1)),
    }


async def main(args):
    directory = Path(args.capture_dir)
    directory.mkdir(parents=True, exist_ok=True)
    market = MarketService(region=args.region)
    catalog = CatalogService(Store(directory / "capture.sqlite3"), market)
    prior = json.loads(Path(args.replay_report).read_text()) if args.replay_report else None
    end = args.end or (prior["plan"]["end"] if prior else now_ms() // INTERVAL * INTERVAL)
    start = end - args.days * 6 * INTERVAL
    definitions = cases()
    plan = {
        "source": args.source,
        "markets": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
        "bar": "4H",
        "start": start,
        "end": end,
        "cases": json_safe(definitions),
        "costs": [{"fee_bps": 10, "slippage_bps": 5}, {"fee_bps": 20, "slippage_bps": 10}],
        "windows": "first 50% development; next 25% check A; final 25% check B; flat independent books",
        "selection": "none; all fixed cases disclosed; exploratory checks are not blinded holdouts",
        "criteria": "cash, equal-allocation buy/hold, two windows, 2x costs, neighbor and filter ablation comparison",
        "limitations": [
            "selected present-day large-cap spot universe",
            "current instrument rules scenario",
            "no capacity/order-book model",
            "no familywise significance or profitable-edge assertion",
            "equal allocation cap, but stops and loss budgets differ across reference and new models",
        ],
    }
    report = {
        "schema_version": 1,
        "plan": plan,
        "plan_hash": digest(plan),
        "implementation": research_identity(),
        "markets": [],
        "results": [],
    }
    if prior and (
        prior["plan_hash"] != report["plan_hash"]
        or prior["implementation"]["code_fingerprint"] != report["implementation"]["code_fingerprint"]
    ):
        raise RuntimeError("Replay requires the identical declared plan and installed implementation")
    (directory / "plan.json").write_text(dumps(report) + "\n")  # Plan fixed before first download/evaluation.
    try:
        for symbol in plan["markets"]:
            if prior:
                captured = next(item for item in prior["markets"] if item["inst_id"] == symbol)
                record = captured["instrument"]
                dataset = catalog.get_dataset(captured["capture_dataset_id"])
                if dataset["content_hash"] != captured["dataset_hash"]:
                    raise RuntimeError("Replay dataset identity mismatch")
            else:
                record = await catalog.get_instrument(symbol, args.source)
                job = catalog.create_job(symbol, "trade", "4H", start, end, args.source)
                completed = await catalog.run_job(job["id"])
                if completed["status"] != "completed":
                    raise RuntimeError(f"{symbol}: incomplete capture: {completed.get('error')}")
                dataset = catalog.get_dataset(completed["dataset_id"])
            if not catalog.verify_dataset(dataset["id"]):
                raise RuntimeError("Captured dataset hash mismatch")
            data = catalog.load_candles(dataset["id"])
            instrument = Instrument(
                **{
                    k: D(record[k]) if k in {"tick_size", "lot_size", "min_size"} else record[k]
                    for k in ["inst_id", "base", "quote", "tick_size", "lot_size", "min_size", "state"]
                }
            )
            report["markets"].append(
                {
                    "inst_id": symbol,
                    "rows": len(data),
                    "dataset_hash": dataset["content_hash"],
                    "instrument": record,
                    "capture_dataset_id": dataset["id"],
                }
            )
            print(f"Captured {symbol}: {len(data)} bars", flush=True)
            for label, lower, upper in [
                ("check_A", len(data) // 2, len(data) * 3 // 4),
                ("check_B", len(data) * 3 // 4, len(data)),
            ]:
                for fee, slip in [(10, 5), (20, 10)]:
                    for name, strategy in definitions.items():
                        config = ResearchConfig(
                            strategy=strategy,
                            fee_bps=D(fee),
                            slippage_bps=D(slip),
                            start_ts=data[lower].ts,
                            end_ts=data[upper - 1].ts + INTERVAL,
                        )
                        result = run_professional_backtest(
                            data,
                            INTERVAL,
                            instrument,
                            config,
                            provenance={"trade": dataset, "research_battery_plan_hash": report["plan_hash"]},
                        )
                        report["results"].append(
                            {
                                "inst_id": symbol,
                                "window": label,
                                "start": config.start_ts,
                                "end": config.end_ts,
                                "case": name,
                                "fee_bps": fee,
                                "slippage_bps": slip,
                                "input_hash": result["input_hash"],
                                "result_hash": digest(result),
                                "metrics": result["metrics"],
                                "completed_round_trips": len(result["round_trips"]),
                                "fills": len(result["fills"]),
                            }
                        )
            Path(args.output).write_text(json.dumps(json_safe(report), indent=2, allow_nan=False) + "\n")
        if prior:
            report["replay_verified"] = report["results"] == prior["results"]
            if not report["replay_verified"]:
                raise RuntimeError("Captured research replay differs; refusing a success report")
        report["completed_at"] = now_ms()
        report["all_cases_completed"] = len(report["results"]) == 108
        Path(args.output).write_text(json.dumps(json_safe(report), indent=2, allow_nan=False) + "\n")
        print(f"Completed {len(report['results'])} cases; report {args.output}", flush=True)
    finally:
        await market.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["okx", "example"], default="example")
    parser.add_argument("--region", choices=["global", "us", "eea"], default="global")
    parser.add_argument("--days", type=int, choices=range(90, 731), default=365)
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Exclusive 4H-aligned UTC epoch ms; preserve report boundary for replay",
    )
    parser.add_argument(
        "--replay-report", help="Replay the exact report against its retained capture; no endpoint downloads"
    )
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
