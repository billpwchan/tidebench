"""Declared portfolio risk experiment, public captures and deterministic replay.

All definitions and checks are fixed before downloading/evaluating; not blinded.
Current market rules, selected universe, bar fills and constant costs are scenarios.
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from tidebench.catalog import CatalogService
from tidebench.engine import ACCOUNTING_CONTEXT
from tidebench.market import MarketService
from tidebench.portfolio_research import PortfolioInput, _simulate_portfolio
from tidebench.provenance import research_identity
from tidebench.store import Store, encode, now_ms
from tidebench.strategy_registry import digest

INTERVAL = 4 * 3600000
MARKETS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def cases():
    return {
        "static_equal_weight": dict(mode="fixed_weights"),
        "fixed_weight_momentum": dict(mode="momentum"),
        "risk_momentum_84": dict(mode="risk_momentum"),
        "risk_window_42": dict(mode="risk_momentum", risk_window=42),
        "risk_window_126": dict(mode="risk_momentum", risk_window=126),
        "risk_no_stress": dict(mode="risk_momentum", correlation_stress="0"),
        "inverse_vol_without_target": dict(mode="risk_momentum", vol_target_pct="100"),
    }


async def main(args):
    folder = Path(args.capture_dir)
    folder.mkdir(parents=True, exist_ok=True)
    prior = json.loads(Path(args.replay_report).read_text()) if args.replay_report else None
    captured_report = prior or (
        json.loads(Path(args.capture_report).read_text()) if args.capture_report else None
    )
    end = args.end or (captured_report["plan"]["end"] if captured_report else now_ms() // INTERVAL * INTERVAL)
    start = end - args.days * 6 * INTERVAL
    plan = dict(
        source=args.source,
        markets=MARKETS,
        bar="4H",
        start=start,
        end=end,
        cases=cases(),
        costs=[[10, 5], [20, 10]],
        windows="first half available development; third/fourth quarters are adjacent flat-book checks with 126 pre-window warmup bars",
        capital_pct=40,
        rebalance_bars=6,
        lookback=42,
        top_k=2,
        risk_window=84,
        vol_target_pct=20,
        vol_floor_pct=20,
        covariance_shrinkage=".25",
        correlation_stress=".75",
        ceiling=".5 in every rotation leg; static basket targets 1/3 in each leg",
        selection="none; all seven definitions and both costs reported; no test winner deployment",
        limitations=[
            "not statistically blinded or independent of prior public data research",
            "three current large-cap markets; no historical membership inference",
            "current instrument rules; zero modeled spread plus configured slippage",
            "constant costs, no partial fills/order book/capacity",
            "same capital ceiling and clock; achieved exposure/risk deliberately differ, no ex-post risk matching",
            "risk target is for the allocated sleeve; no bound on future realized account risk",
        ],
    )
    if captured_report and any(
        captured_report["plan"][k] != plan[k] for k in ["source", "markets", "bar", "start", "end"]
    ):
        raise RuntimeError("Captured data window differs from the declared plan")
    implementation = research_identity()
    runner_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = dict(
        schema_version=1,
        plan=plan,
        plan_hash=digest(plan),
        implementation=implementation,
        runner_hash=runner_hash,
        captures=[],
        results=[],
    )
    if prior and any(prior[k] != report[k] for k in ["plan_hash", "implementation", "runner_hash"]):
        raise RuntimeError("Replay requires the same plan, runtime and implementation")
    (folder / "risk-plan.json").write_text(json.dumps(encode(report), indent=2) + "\n")
    market = MarketService(region=args.region)
    catalog = CatalogService(Store(folder / "capture.sqlite3"), market)
    data = {}
    try:
        for symbol in MARKETS:
            if captured_report:
                capture = next(r for r in captured_report["captures"] if r["inst_id"] == symbol)
                dataset = catalog.get_dataset(capture["dataset_id"])
                if dataset["content_hash"] != capture["content_hash"]:
                    raise RuntimeError("Dataset identity differs")
            else:
                meta = await catalog.get_instrument(symbol, args.source)
                job = catalog.create_job(symbol, "trade", "4H", start, end, args.source)
                job = await catalog.run_job(job["id"])
                if job["status"] != "completed":
                    raise RuntimeError(f"{symbol}: incomplete capture {job.get('error')}")
                dataset = catalog.get_dataset(job["dataset_id"])
                capture = dict(
                    inst_id=symbol,
                    dataset_id=dataset["id"],
                    content_hash=dataset["content_hash"],
                    instrument=meta,
                )
            if not catalog.verify_dataset(dataset["id"]):
                raise RuntimeError("Dataset failed content verification")
            data[symbol] = catalog.load_candles(dataset["id"])
            capture["rows"] = len(data[symbol])
            report["captures"].append(capture)
            print(f"Captured {symbol}: {len(data[symbol])}", flush=True)
        size = len(data[MARKETS[0]])
        for label, lower, upper in [("check_A", size // 2, size * 3 // 4), ("check_B", size * 3 // 4, size)]:
            for fee, slip in plan["costs"]:
                for name, changes in cases().items():
                    config = encode(
                        PortfolioInput.model_validate(
                            dict(
                                name=name,
                                hypothesis="Declared test of risk-aware allocation under common cash, execution clock and costs.",
                                legs=[
                                    dict(
                                        package_id=str(i + 1) * 32,
                                        weight=str(D(1) / 3) if name == "static_equal_weight" else ".5",
                                        strategy=dict(kind="buy_hold"),
                                    )
                                    for i in range(3)
                                ],
                                capital_pct=40,
                                fee_bps=fee,
                                slippage_bps=slip,
                                max_daily_loss_pct=50,
                                rebalance_bars=6,
                                lookback=42,
                                top_k=2,
                                risk_window=84,
                                vol_target_pct=20,
                                vol_floor_pct=20,
                                covariance_shrinkage=".25",
                                correlation_stress=".75",
                            )
                            | changes
                        ).model_dump()
                    )
                    manifest = dict(
                        source=args.source,
                        bar="4H",
                        start=data[MARKETS[0]][lower - 126].ts,
                        end=data[MARKETS[0]][upper - 1].ts + INTERVAL,
                        universe_scope="explicit current three-market universe",
                        plan_hash=report["plan_hash"],
                    )
                    legs = [
                        dict(
                            config=leg,
                            instrument=capture["instrument"],
                            candles=data[symbol][lower - 126 : upper],
                            marks=data[symbol][lower - 126 : upper],
                            funding=[],
                            tiers=[],
                        )
                        for symbol, leg, capture in zip(
                            MARKETS, config["legs"], report["captures"], strict=True
                        )
                    ]
                    with localcontext(ACCOUNTING_CONTEXT):
                        result = _simulate_portfolio(config, manifest, legs, first_trading_index=126)
                    evidence = [
                        d["risk_evidence"]
                        for d in result["decisions"]
                        if d.get("risk_evidence") and d["risk_evidence"].get("modeled_vol_pct")
                    ]
                    row = dict(
                        case=name,
                        window=label,
                        start=data[MARKETS[0]][lower].ts,
                        end=manifest["end"],
                        fee_bps=fee,
                        slippage_bps=slip,
                        input_hash=digest(encode(dict(config=config, manifest=manifest, legs=legs))),
                        result_hash=digest(result),
                        metrics=result["metrics"],
                        execution_diagnostics=dict(
                            Counter(str(r.get("code", "unspecified")) for r in result["execution_rejections"])
                        ),
                        risk_summary=dict(
                            decisions=len(evidence),
                            max_modeled_vol_pct=max(
                                (D(e["modeled_vol_pct"]) for e in evidence), default=D(0)
                            ),
                            max_stressed_vol_pct=max(
                                (D(e["stressed_vol_pct"]) for e in evidence), default=D(0)
                            ),
                            last=evidence[-1] if evidence else None,
                        ),
                    )
                    report["results"].append(encode(row))
                    Path(args.output).write_text(json.dumps(encode(report), indent=2, allow_nan=False) + "\n")
                    print(
                        f"{label} {fee}/{slip} {name}: return {result['metrics']['total_return_pct']} dd {result['metrics']['max_drawdown_pct']}",
                        flush=True,
                    )
        report["all_cases_completed"] = len(report["results"]) == 28
        if prior:
            report["replay_verified"] = report["results"] == prior["results"]
            if not report["replay_verified"]:
                raise RuntimeError("Replay differs")
        report["completed_at"] = now_ms()
        Path(args.output).write_text(json.dumps(encode(report), indent=2, allow_nan=False) + "\n")
    finally:
        await market.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["example", "okx"], default="example")
    parser.add_argument("--region", default="global")
    parser.add_argument("--days", type=int, choices=range(90, 731), default=730)
    parser.add_argument("--end", type=int)
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay-report")
    parser.add_argument(
        "--capture-report",
        help="Reuse verified raw captures only, without claiming replay of previous results",
    )
    asyncio.run(main(parser.parse_args()))
