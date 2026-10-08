"""Offline, real-storage managed-portfolio and contribution acceptance evidence.

Only synthetic provider data is used. The child exits immediately after the
first economic commit; there is no mocked fill, sleeping benchmark, or network.
"""

import asyncio
import json
import os
import platform
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import httpx
from tidebench import __version__
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService
from tidebench.platform import PlatformError
from tidebench.portfolio_registry import PortfolioDefinition
from tidebench.portfolio_research import PortfolioInput
from tidebench.store import encode

D = Decimal
END = 1767225600000
HOUR = 3600000
CHILD = """
import asyncio, os, sys
from pathlib import Path
import httpx
from tidebench.config import Settings
from tidebench.market import MarketService
from tidebench.pro_service import ProfessionalRuntime
from tidebench.store import Store
async def main():
    settings = Settings(data_dir=Path(sys.argv[1]), worker_enabled=False, _env_file=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(RuntimeError('Network forbidden')))) as client:
        runtime = ProfessionalRuntime(Store(settings.database), MarketService(client=client), settings)
        original = runtime.book.submit
        def exit_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(23)
        runtime.book.submit = exit_after_commit
        await runtime.managed_portfolios.evaluate(sys.argv[2])
asyncio.run(main())
"""


async def research(r):
    definition = PortfolioDefinition.model_validate(
        {"legs": [{"inst_id": s, "weight": ".5"} for s in ("BTC-USDT", "ETH-USDT")]}
    ).record()
    p = r.portfolio_registry.create_project(
        "Crash-recoverable basket",
        "One finite cash budget must reconcile every child and owner contribution.",
        definition,
        "offline-audit",
    )
    legs = []
    for leg in definition["legs"]:
        package = r.packages.create_package(leg["inst_id"], "1H", END - 72 * HOUR, END, "example")
        package = await r.packages.run_package(package["id"])
        assert package["ready"]
        legs.append({k: v for k, v in leg.items() if k != "inst_id"} | {"package_id": package["id"]})
    body = encode(
        PortfolioInput(
            name=p["name"],
            hypothesis=p["version"]["hypothesis"],
            portfolio_version_id=p["version"]["id"],
            legs=legs,
        ).model_dump()
    )
    queued = r.portfolios.create(body, "offline-audit")
    await r.offload(r.portfolios.compute, queued["id"])
    run = r.portfolios.get(queued["id"])
    assert run["status"] == "completed", run["error"]
    return run


def activate(r, run):
    preview = r.portfolio_releases.preview(run["id"])
    release = r.portfolio_releases.approve(
        {
            "run_id": run["id"],
            "preview_hash": preview["preview_hash"],
            "review": "Reviewed version, finite capital, sequential fills and failed compensation.",
            "acknowledgements": preview["required_acknowledgements"],
        },
        "offline-audit",
    )
    release = r.portfolio_releases.activate(release["id"], "offline-audit")
    return r.managed_portfolios.get(release["group_id"])


async def observe(directory, market):
    settings = Settings(data_dir=directory, worker_enabled=False, _env_file=None)
    app = create_app(settings, market)
    r = app.state.professional
    observations = []
    try:
        run = await research(r)
        group = activate(r, run)
        root = Path(__file__).resolve().parents[1]
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            CHILD,
            str(directory),
            group["id"],
            cwd=root,
            env={k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "SYSTEMROOT", "PYTHONPATH"}},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
        assert child.returncode == 23, (stdout, stderr)
        interrupted = r.managed_portfolios.history(group["id"])[0]
        orders_before = r.book.orders("example")
        assert len(orders_before) == 1
        await r.managed_portfolios.evaluate(group["id"])
        recovered = r.managed_portfolios.history(group["id"])[0]
        assert recovered["status"] == "completed", recovered["error"]
        report = r.book.contribution_report("example", await r.snapshots_for("example"))
        assert report["reconciled"]
        observations.append(
            {
                "contract": "hard_exit_after_committed_child",
                "child_exit_code": child.returncode,
                "committed_orders_before_recovery": len(orders_before),
                "frozen_target_unchanged": interrupted["content_hash"] == recovered["content_hash"],
                "frozen_additions_unchanged": interrupted["additions_hash"] == recovered["additions_hash"],
                "orders_after_recovery": len(r.book.orders("example")),
                "command_count": len(recovered["commands"]),
                "unique_fills": len({c["order"]["id"] for c in recovered["commands"]}),
                "status": recovered["status"],
                "cash_scale": recovered["additions"]["cash_scale"],
                "residual_pct": recovered["residuals"]["capital_pct"],
                "contributions_reconciled": report["reconciled"],
                "net_contribution": report["totals"]["net_pnl"],
                "account_net_pnl": report["account_net_pnl"],
            }
        )
        backup = r.backups.create("managed-portfolio-offline-acceptance")
        assert r.backups.verify(backup["id"])["database_schema"] == 5
        r.backups.restore(backup["id"])
        after = r.book.contribution_report("example", await r.snapshots_for("example"))
        assert (
            after["owners"] == report["owners"]
            and r.managed_portfolios.get(group["id"])["status"] == "stopped"
        )
        observations.append(
            {
                "contract": "schema5_actual_backup_restore",
                "schema": backup["database_schema"],
                "group_status": r.managed_portfolios.get(group["id"])["status"],
                "risk_halted": r.book.risk("example")["halted"],
                "clock_paused": r.clock.status()["paused"],
                "command_evidence_preserved": r.managed_portfolios.history(group["id"])[0]["body"]
                == recovered["body"],
                "contribution_owners_preserved": after["owners"] == report["owners"],
                "reconciled": after["reconciled"],
            }
        )
        # Clear only this disposable synthetic account to admit a second group.
        for position in r.book.positions("example"):
            await r.submit(
                {
                    "source": "example",
                    "inst_id": position["inst_id"],
                    "side": "sell" if D(position["quantity"]) > 0 else "buy",
                    "quantity": str(abs(D(position["quantity"]))),
                    "leverage": position["leverage"],
                    "reduce_only": True,
                    "order_type": "market",
                    "margin_mode": "isolated",
                },
                "audit-flatten-" + position["inst_id"],
                "offline-audit",
            )
        r.book.halt("example", False, "Continue isolated compensation audit", "offline-audit")
        group = activate(r, run)
        original = r.book.submit

        def refuse(order, key, snapshots, actor):
            if order["reduce_only"] or order["inst_id"] == "ETH-USDT":
                raise PlatformError(
                    "injected_unavailability", "Temporary second-leg and compensation failure.", 409
                )
            return original(order, key, snapshots, actor)

        r.book.submit = refuse
        await r.managed_portfolios.evaluate(group["id"])
        blocked = r.managed_portfolios.get(group["id"])
        residual = [dict(p) for p in r.book.positions("example")]
        assert blocked["status"] == "compensating" and residual
        r.book.submit = original
        await r.managed_portfolios.evaluate(group["id"])
        final = r.managed_portfolios.history(group["id"])[0]
        assert final["status"] == "compensated" and not r.book.positions("example")
        report = r.book.contribution_report("example", {})
        assert report["reconciled"]
        observations.append(
            {
                "contract": "failed_leg_and_failed_compensation_recovery",
                "blocked_status": blocked["status"],
                "retained_inventory_count": len(residual),
                "blocked_error": blocked["last_error"],
                "final_group_status": r.managed_portfolios.get(group["id"])["status"],
                "final_batch_status": final["status"],
                "remaining_positions": len(r.book.positions("example")),
                "contributions_reconciled": report["reconciled"],
                "final_net_contribution": report["totals"]["net_pnl"],
            }
        )
        return observations
    finally:
        await r.stop()
        app.state.store.release_process_lock()


async def main():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(RuntimeError("Offline audit forbids network"))
        )
    ) as client:
        with tempfile.TemporaryDirectory(prefix="tidebench-managed-audit-") as directory:
            observations = await observe(Path(directory), MarketService(client=client))
            print(
                json.dumps(
                    {
                        "audited_version": __version__,
                        "environment": {"python": platform.python_version(), "system": platform.system()},
                        "source": "example; synthetic provider; real SQLite/economic engine",
                        "observations": observations,
                        "scope": "Hard process exit, financial recovery and real backup replacement in a temporary workspace. This is not a venue, uptime, liquidity or performance-capacity benchmark.",
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
