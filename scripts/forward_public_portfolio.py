"""Elapsed public-data integration of an isolated reviewed local-paper basket.

No credentials or venue orders. Research, confirmed forward decisions, child
fills, controller reconstruction, operator stop and actual flattening are
measured separately. Minutes of elapsed evidence never become an uptime/edge
acceptance. Use a new empty output directory.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.portfolio_research import PortfolioInput
from tidebench.pro_service import ProfessionalRuntime
from tidebench.provenance import research_identity
from tidebench.store import Store, encode, now_ms
from tidebench.strategy_registry import digest

MARKETS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")


async def main(options):
    root = options.output.resolve()
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Use a new empty evidence directory, never an operational workspace.")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    settings = Settings(data_dir=root, worker_enabled=False, _env_file=None)
    runtime = ProfessionalRuntime(Store(settings.database), MarketService(region=options.region), settings)
    report = {
        "scope": "Elapsed public OKX integration; isolated local paper; no venue orders, profitability or uptime claim.",
        "implementation": runtime.engine_identity,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_at": now_ms(),
        "passed": False,
        "decisions": [],
        "waiting_issues": [],
    }
    origin = time.monotonic()
    try:
        definition = PortfolioDefinition(
            bar="1m",
            mode="fixed_weights",
            capital_pct=10,
            rebalance_bars=1,
            legs=[
                {"inst_id": symbol, "weight": ".33333333333333333333333333333333333333333333333333"}
                for symbol in MARKETS
            ],
        ).record()
        hypothesis = "Integration-only fixed cash-budget basket tests causal confirmed bars, real public quotes, accounting and restart; no investment edge is asserted."
        project = runtime.portfolio_registry.create_project(
            "Public elapsed basket", hypothesis, definition, "acceptance-researcher"
        )
        version = project["version"]
        end = now_ms() // 60000 * 60000
        legs = []
        for leg in definition["legs"]:
            package = runtime.packages.create_package(leg["inst_id"], "1m", end - 96 * 60000, end, "okx")
            package = await runtime.packages.run_package(package["id"])
            if not package["ready"]:
                raise RuntimeError(f"Incomplete captured package: {package.get('error')}")
            legs.append({k: v for k, v in leg.items() if k != "inst_id"} | {"package_id": package["id"]})
            print(f"Captured ready package {leg['inst_id']}", flush=True)
        study = PortfolioInput.model_validate(
            {k: v for k, v in definition.items() if k in PortfolioInput.model_fields and k != "legs"}
            | {
                "name": project["name"],
                "hypothesis": hypothesis,
                "portfolio_version_id": version["id"],
                "legs": legs,
                "max_daily_loss_pct": 50,
            }
        )
        run = runtime.portfolios.create(encode(study.model_dump()), "acceptance-researcher")
        await runtime.offload(runtime.portfolios.compute, run["id"])
        run = runtime.portfolios.get(run["id"])
        if run["status"] != "completed":
            raise RuntimeError(f"Research failed: {run['error']}")
        report["research"] = {
            "run_id": run["id"],
            "input_hash": run["manifest"]["input_hash"],
            "result_hash": run["manifest"]["result_hash"],
            "metrics": run["result"]["metrics"],
            "economics": run["result"]["economics"],
        }
        preview = runtime.portfolio_releases.preview(run["id"])
        if preview["blockers"]:
            raise RuntimeError(f"Reviewed release refused: {preview['blockers']}")
        release = runtime.portfolio_releases.approve(
            {
                "run_id": run["id"],
                "preview_hash": preview["preview_hash"],
                "review": "Review accepts displayed exploratory limitations and local-paper sequential execution solely for isolated integration evidence.",
                "acknowledgements": preview["required_acknowledgements"],
            },
            "acceptance-reviewer",
        )
        release = runtime.portfolio_releases.activate(release["id"], "acceptance-trader")
        group_id = release["group_id"]
        forward_origin, reconstructed = time.monotonic(), False
        reconstruction_before = None
        reconstruction_verified = False
        while True:
            await runtime.execution_once()
            evaluated = False
            try:
                await runtime.managed_portfolios.evaluate(group_id)
                evaluated = True
            except PlatformError as exc:
                if exc.code not in {
                    "strategy_data",
                    "market_unavailable",
                    "stale_quote",
                    "stale_mark",
                    "portfolio_quote_missing",
                    "portfolio_market_unavailable",
                    "portfolio_quote_before_signal",
                }:
                    raise
                report["waiting_issues"].append({"at": now_ms(), "code": exc.code, "message": exc.message})
                with runtime.store.write() as conn:
                    conn.execute(
                        "UPDATE managed_portfolios SET last_error=?,updated_at=? WHERE id=? AND status='running'",
                        (exc.message, now_ms(), group_id),
                    )
                print(f"Waiting for verified public inputs: {exc.code}", flush=True)
            if reconstruction_before is not None and evaluated:
                before_bar, before = reconstruction_before
                after_bar = runtime.managed_portfolios.get(group_id)["last_bar"]
                after = {o["id"]: o for o in runtime.book.orders("okx")}
                if any(after.get(key) != value for key, value in before.items()):
                    raise RuntimeError("Controller reconstruction changed a committed original fill.")
                if after_bar == before_bar and set(before) != set(after):
                    raise RuntimeError("Controller reconstruction duplicated a same-boundary fill.")
                report["controller_reconstruction"] = {
                    "before_bar": before_bar,
                    "after_bar": after_bar,
                    "retained_fills": len(before),
                    "unchanged_original_fills": True,
                    "same_boundary": before_bar == after_bar,
                    "actual_evaluation_verified": True,
                    "new_boundary_fills": len(set(after) - set(before)),
                }
                reconstruction_verified = True
                reconstruction_before = None
            history = runtime.managed_portfolios.history(group_id)
            if history:
                latest = history[0]
                if not report["decisions"] or report["decisions"][-1]["id"] != latest["id"]:
                    report["decisions"].append(
                        {
                            "id": latest["id"],
                            "bar": latest["bar"],
                            "status": latest["status"],
                            "available_at": latest["body"].get("available_at"),
                            "targets": latest["body"].get("targets"),
                            "hash": digest(latest),
                            "orders": [c.get("order") for c in latest["commands"] if c.get("order")],
                        }
                    )
                    print(
                        f"Observed confirmed decision {latest['bar']} status={latest['status']}", flush=True
                    )
                if latest["status"] != "completed":
                    raise RuntimeError(f"Forward decision failed: {latest.get('error')}")
            elapsed = time.monotonic() - forward_origin
            if not reconstructed and history and elapsed >= options.duration_seconds / 2:
                reconstruction_before = (
                    runtime.managed_portfolios.get(group_id)["last_bar"],
                    {o["id"]: o for o in runtime.book.orders("okx")},
                )
                runtime.managed_portfolios = type(runtime.managed_portfolios)(runtime)
                # The next normal evaluation verifies retained fills and keys;
                # a new real-minute boundary is recorded rather than called a duplicate.
                reconstructed = True
            if elapsed >= options.duration_seconds:
                break
            await asyncio.sleep(min(20, options.duration_seconds - elapsed))
        report["forward_elapsed_seconds"] = round(time.monotonic() - forward_origin, 3)
        report["strategy_order_count"] = len(runtime.book.orders("okx"))
        runtime.managed_portfolios.stop(group_id, "acceptance-trader")
        report["stopped_before_flatten"] = runtime.managed_portfolios.get(group_id)
        protective = []
        for position in runtime.book.positions("okx"):
            protective.append(
                await runtime.submit(
                    {
                        "source": "okx",
                        "inst_id": position["inst_id"],
                        "side": "sell",
                        "quantity": position["quantity"],
                        "leverage": "1",
                        "reduce_only": True,
                        "margin_mode": "isolated",
                        "order_type": "market",
                    },
                    f"acceptance:flatten:{position['inst_id']}",
                    "acceptance-trader",
                )
            )
        await runtime.execution_once()
        report["protective_flatten_orders"] = protective
        report["final_group"] = runtime.managed_portfolios.get(group_id)
        report["performance_snapshot"] = runtime.book.performance.freeze("okx", "acceptance-trader")
        report["performance_verified"] = runtime.book.performance.verify(report["performance_snapshot"]["id"])
        report["contribution_reconciliation"] = runtime.book.contributions.report(
            "okx",
            runtime.book.account(
                "okx",
                {
                    symbol: snapshot
                    for (source, symbol), snapshot in runtime.snapshots.items()
                    if source == "okx"
                },
            ),
        )
        report["final_account"] = runtime.book.account(
            "okx",
            {symbol: snapshot for (source, symbol), snapshot in runtime.snapshots.items() if source == "okx"},
        )
        report["passed"] = bool(
            report["strategy_order_count"]
            and len(report["decisions"]) >= 2
            and reconstructed
            and reconstruction_verified
            and report["driver_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            and report["implementation"] == research_identity()
            and not runtime.book.positions("okx")
        )
        if not report["passed"]:
            raise RuntimeError(
                "Required strategy fills, distinct confirmed decisions or final flat state were absent."
            )
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - origin, 3)
        report["ended_at"] = now_ms()
        report["driver_sha256_at_end"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report["driver_changed_during_observation"] = (
            report["driver_sha256"] != report["driver_sha256_at_end"]
        )
        report["implementation_at_end"] = research_identity()
        report["implementation_changed_during_observation"] = (
            report["implementation"] != report["implementation_at_end"]
        )
        if report["implementation_changed_during_observation"]:
            report["passed"] = False
            report["error"] = "Installed research implementation changed during public observation."
        await runtime.stop()
        await runtime.market.close()
        (root / "summary.json").write_text(json.dumps(encode(report), indent=2, allow_nan=False) + "\n")
        if report["implementation_changed_during_observation"]:
            raise RuntimeError(report["error"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", choices=("global", "us", "eea"), default="global")
    parser.add_argument("--duration-seconds", type=int, metavar="120..86400", default=360)
    options = parser.parse_args()
    if not 120 <= options.duration_seconds <= 86400:
        parser.error("--duration-seconds must be between 120 and 86400")
    asyncio.run(main(options))
