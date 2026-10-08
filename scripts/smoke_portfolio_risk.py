"""Real OKX public-feed / local-paper integration in a new isolated directory.

Never accepts venue credentials and never submits an exchange order. This is a
single observed integration path, not an alpha, capacity or uptime acceptance.
"""

import argparse
import asyncio
import hashlib
import json
import time
from decimal import Decimal as D
from pathlib import Path

from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.portfolio_research import PortfolioInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store, encode, now_ms
from tidebench.strategy_registry import digest


async def main(args):
    directory = Path(args.data_dir).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise RuntimeError("Use a new empty directory; existing workspaces are refused")
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=directory, worker_enabled=False, _env_file=None)
    r = ProfessionalRuntime(Store(settings.database), MarketService(region=args.region), settings)
    started = time.perf_counter()
    definition = next(
        x["definition"]
        for x in json.loads(Path("examples/portfolios.json").read_text())
        if x["id"] == "risk-rotation"
    )
    hypothesis = "Integration-only check of real public OKX data and quotes through a reviewed isolated local-paper risk portfolio; no claim of investment edge."
    report = dict(
        schema_version=1,
        source="okx",
        execution="isolated_local_paper_no_exchange_orders",
        implementation=r.engine_identity,
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        observed_at=now_ms(),
        passed=False,
    )
    try:
        end = now_ms() // 14400000 * 14400000
        project = r.portfolio_registry.create_project(
            "Public-feed risk smoke", hypothesis, definition, "smoke-researcher"
        )
        definition = project["version"]["definition"]
        legs = []
        for leg in definition["legs"]:
            package = r.packages.create_package(leg["inst_id"], "4H", end - 168 * 14400000, end, "okx")
            package = await r.packages.run_package(package["id"])
            if not package["ready"]:
                raise RuntimeError(f"Incomplete package: {package.get('error')}")
            legs.append({k: v for k, v in leg.items() if k != "inst_id"} | dict(package_id=package["id"]))
            print(f"Ready real package: {leg['inst_id']}", flush=True)
        body = encode(
            PortfolioInput.model_validate(
                {k: v for k, v in definition.items() if k in PortfolioInput.model_fields and k != "legs"}
                | dict(
                    name=project["name"],
                    hypothesis=hypothesis,
                    portfolio_version_id=project["version"]["id"],
                    legs=legs,
                    max_daily_loss_pct=50,
                )
            ).model_dump()
        )
        run = r.portfolios.create(body, "smoke-researcher")
        await r.offload(r.portfolios.compute, run["id"])
        run = r.portfolios.get(run["id"])
        if run["status"] != "completed":
            raise RuntimeError(f"Research failed: {run['error']}")
        report["research"] = dict(
            input_hash=run["manifest"]["input_hash"],
            result_hash=run["manifest"]["result_hash"],
            metrics=run["result"]["metrics"],
        )
        preview = r.portfolio_releases.preview(run["id"])
        if preview["blockers"]:
            raise RuntimeError(f"Release refused: {preview['blockers']}")
        release = r.portfolio_releases.approve(
            dict(
                run_id=run["id"],
                preview_hash=preview["preview_hash"],
                review="Integration-only reviewer accepts the displayed research limitations and sequential local-paper risks.",
                acknowledgements=preview["required_acknowledgements"],
            ),
            "smoke-reviewer",
        )
        release = r.portfolio_releases.activate(release["id"], "smoke-trader")
        identifier = release["group_id"]
        print("Reviewed isolated paper group activated; preparing confirmed forward history.", flush=True)
        await r.managed_portfolios.evaluate(identifier)
        batch = r.managed_portfolios.history(identifier)[0]
        if batch["status"] != "completed":
            raise RuntimeError(f"Batch failed: {batch['error']}")
        report["forward"] = dict(
            status=batch["status"],
            bar=batch["bar"],
            available_at=batch["body"]["available_at"],
            risk_evidence=batch["body"]["risk_evidence"],
            rebalance_due=batch["body"]["rebalance_due"],
            targets=batch["body"]["targets"],
            residuals=batch["residuals"],
            commands=[
                dict(status=c["status"], order_id=(c.get("order") or {}).get("id")) for c in batch["commands"]
            ],
        )
        order_ids = [o["id"] for o in r.book.orders("okx")]
        batch_hash = digest(batch)
        r.managed_portfolios = type(r.managed_portfolios)(r)
        await r.managed_portfolios.evaluate(identifier)
        assert [o["id"] for o in r.book.orders("okx")] == order_ids
        assert digest(r.managed_portfolios.history(identifier)[0]) == batch_hash
        r.managed_portfolios.stop(identifier, "smoke-trader")
        if args.probe_fills:
            symbol = "BTC-USDT"
            quote = await r.quote_snapshot("okx", symbol)
            lot = D(quote["instrument"]["lot_size"])
            quantity = (D(100) / D(quote["ask"]) / lot).to_integral_value(rounding="ROUND_FLOOR") * lot
            order = dict(
                source="okx",
                inst_id=symbol,
                side="buy",
                quantity=str(quantity),
                leverage="1",
                reduce_only=False,
                margin_mode="isolated",
                order_type="market",
            )
            opened = await r.submit(order, "smoke:manual:open", "smoke-trader")
            closed = await r.submit(
                order | dict(side="sell", reduce_only=True), "smoke:manual:close", "smoke-trader"
            )
            assert opened["status"] == closed["status"] == "filled"
            assert not r.book.positions("okx")
            report["manual_fill_probe"] = dict(
                scope="separate_100_USDT_local_paper_round_trip_not_a_strategy_signal",
                orders=[
                    {
                        k: o[k]
                        for k in ["id", "side", "quantity", "price", "notional", "fee", "quote_ts", "status"]
                    }
                    for o in [opened, closed]
                ],
                final_account=r.book.account("okx", {}),
            )
        report |= dict(
            passed=True,
            idempotent_controller_reload=True,
            paper_order_count=len(order_ids),
            group_stopped=True,
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        await r.stop()
        await r.market.close()
        Path(args.output).write_text(json.dumps(encode(report), indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--region", default="global")
    parser.add_argument(
        "--probe-fills",
        action="store_true",
        help="After stopping the strategy, exercise a separate ~100 USDT isolated local-paper round trip with real quotes",
    )
    asyncio.run(main(parser.parse_args()))
