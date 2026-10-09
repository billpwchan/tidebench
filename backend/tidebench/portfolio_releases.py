"""Reviewed promotion of exact portfolio research into one managed paper group."""

import json
from contextlib import nullcontext
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .platform import PlatformError
from .schemas import InputModel
from .store import dumps, new_id, now_ms
from .strategy_registry import digest


class PortfolioReleasePreviewInput(InputModel):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class PortfolioReleaseInput(PortfolioReleasePreviewInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    review: str = Field(min_length=12, max_length=2000)
    acknowledgements: list[
        Literal[
            "execution_cost_difference",
            "execution_risk_difference",
            "no_oos_evidence",
            "sequential_leg_risk",
            "holdout_rejected",
            "holdout_inconclusive",
        ]
    ] = Field(default_factory=list, max_length=6)


class PortfolioReleases:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        with self.store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_releases(id TEXT PRIMARY KEY,source TEXT NOT NULL,run_id TEXT NOT NULL,version_id TEXT NOT NULL REFERENCES portfolio_versions(id),approval TEXT NOT NULL,approval_hash TEXT NOT NULL,status TEXT NOT NULL,group_id TEXT,created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS portfolio_releases_history ON portfolio_releases(source,created_at DESC,id DESC);
            """)

    @staticmethod
    def decode(row):
        body = json.loads(row["approval"])
        if digest(body) != row["approval_hash"]:
            raise PlatformError(
                "portfolio_release_integrity", "Portfolio approval integrity check failed.", 409
            )
        if any(body[key] != row[key] for key in ("id", "source", "run_id", "version_id")):
            raise PlatformError(
                "portfolio_release_integrity", "Portfolio approval identity differs from its index.", 409
            )
        return dict(row) | {"approval": body}

    def get(self, identifier, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute("SELECT * FROM portfolio_releases WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("portfolio_release_missing", "Portfolio release not found.", 404)
        return self.decode(row)

    def list(self, source):
        with self.store.read() as conn:
            return [
                self.decode(r)
                for r in conn.execute(
                    "SELECT * FROM portfolio_releases WHERE source=? ORDER BY created_at DESC,id DESC LIMIT 100",
                    (source,),
                )
            ]

    @staticmethod
    def liquidity_review(source, symbols, connection):
        from .liquidity_evidence import checked_evidence

        reports = []
        if source == "okx":
            for symbol in symbols:
                row = connection.execute(
                    "SELECT * FROM liquidity_calibrations WHERE inst_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if row is None:
                    continue
                report = checked_evidence(row, "liquidity_calibrations")
                for pinned in report["captures"]:
                    capture = connection.execute(
                        "SELECT * FROM liquidity_captures WHERE id=?", (pinned["id"],)
                    ).fetchone()
                    if (
                        capture is None
                        or checked_evidence(capture, "liquidity_captures")["content_hash"]
                        != pinned["content_hash"]
                    ):
                        raise PlatformError(
                            "portfolio_liquidity_integrity",
                            "Pinned liquidity evidence is missing or changed.",
                            409,
                        )
                reports.append(
                    {
                        "id": report["id"],
                        "inst_id": symbol,
                        "content_hash": report["content_hash"],
                        "created_at": report["created_at"],
                        "status_at_freeze": report["status"],
                        "independent_window": report["independent_window"],
                        "declared_sizes": {
                            key: report["input"][key] for key in ("child_notional", "sleeve_notional")
                        },
                        "conditions": report["conditions"],
                    }
                )
        return {
            "role": "informational_local_paper_review",
            "scope": "Pins the exact public observed-window reports reviewed with this release; no venue execution, historical cost, future capacity or fill guarantee. Missing or old reports are not replaced with model estimates.",
            "reports": reports,
            "missing_markets": [
                symbol for symbol in symbols if symbol not in {r["inst_id"] for r in reports}
            ],
        }

    def preview(self, run_id, conn=None):
        r = self.runtime
        run = r.portfolios.get(run_id)
        manifest, config = run["manifest"], run["config"]
        if run["status"] != "completed" or not run["result"]:
            raise PlatformError(
                "portfolio_release_incomplete", "Complete portfolio research before release.", 409
            )
        if manifest["implementation"]["code_fingerprint"] != r.engine_identity["code_fingerprint"]:
            raise PlatformError(
                "portfolio_release_implementation",
                "Re-run portfolio research with the installed implementation.",
                409,
            )
        if not config.get("portfolio_version_id"):
            raise PlatformError(
                "portfolio_release_unbound",
                "Bind research to an immutable portfolio version before release.",
                409,
            )
        version = r.portfolio_registry.version(config["portfolio_version_id"])
        packages = [r.packages.get_package(leg["package_id"]) for leg in config["legs"]]
        r.portfolio_registry.validate_binding(config, packages)
        if manifest.get("portfolio_content_hash") != version["content_hash"]:
            raise PlatformError(
                "portfolio_release_identity", "Research portfolio identity no longer verifies.", 409
            )
        symbols = [leg["inst_id"] for leg in version["definition"]["legs"]]
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            name = connection.execute(
                "SELECT name FROM portfolio_projects WHERE id=?", (version["project_id"],)
            ).fetchone()[0]
            risk = r.book.risk(run["source"], connection)
            inventory = [
                dict(p)
                for p in connection.execute(
                    "SELECT inst_id,quantity FROM pro_positions WHERE source=? AND quantity!='0'",
                    (run["source"],),
                )
                if p["inst_id"] in symbols
            ]
            pending = [
                row["id"]
                for row in connection.execute(
                    "SELECT id,payload FROM pro_orders WHERE source=? AND status='pending'", (run["source"],)
                )
                if json.loads(row["payload"])["inst_id"] in symbols
            ]
            owners = [
                row["id"]
                for row in connection.execute(
                    "SELECT id,inst_id FROM pro_deployments WHERE source=? AND status='running'",
                    (run["source"],),
                )
                if row["inst_id"] in symbols
            ]
            count = connection.execute(
                "SELECT COUNT(*) FROM pro_deployments WHERE status='running'"
            ).fetchone()[0]
            liquidity_review = self.liquidity_review(run["source"], symbols, connection)
            capital_admission = r.managed_portfolios.capital.preview(
                run["source"],
                version["definition"],
                connection,
                account=r.book.account(
                    run["source"],
                    {
                        symbol: snapshot
                        for (source, symbol), snapshot in r.snapshots.items()
                        if source == run["source"]
                    },
                    connection,
                ),
            )
        differences = [
            {"field": key, "research": str(config[key]), "execution": str(risk[key])}
            for key in ("fee_bps", "slippage_bps", "liquidation_fee_bps")
            if Decimal(str(config[key])) != Decimal(str(risk[key]))
        ]
        risk_differences = [
            {"field": rkey, "research": str(config[ckey]), "execution": str(risk[rkey])}
            for ckey, rkey in (
                ("max_gross_pct", "max_gross_exposure_pct"),
                ("max_daily_loss_pct", "max_daily_loss_pct"),
                ("max_order_notional", "max_order_notional"),
            )
            if Decimal(str(config[ckey])) != Decimal(str(risk[rkey]))
        ]
        if Decimal(str(config["max_base_asset_gross_pct"])) != Decimal(
            capital_admission["policy"]["max_base_asset_gross_pct"]
        ):
            risk_differences.append(
                {
                    "field": "max_base_asset_gross_pct",
                    "research": str(config["max_base_asset_gross_pct"]),
                    "execution": capital_admission["policy"]["max_base_asset_gross_pct"],
                }
            )
        lifecycle_scenario = config.get("universe_mode") == "historical_lifecycle"
        if lifecycle_scenario:
            risk_differences.append(
                {
                    "field": "lifecycle_execution_support",
                    "research": "attributed_lifecycle_v1",
                    "execution": "current_market_rules_only; historical settlement and unit conversion not executable by managed controller",
                }
            )
        evidence = r.protocol.verify_run(run)
        required = (
            ["sequential_leg_risk"]
            + (["execution_risk_difference"] if risk_differences else [])
            + (["execution_cost_difference"] if differences else [])
            + (["no_oos_evidence"] if config["evaluation"] == "full" and not evidence["one_use"] else [])
            + (["holdout_rejected"] if evidence.get("rejection_status") == "rejected" else [])
            + (["holdout_inconclusive"] if evidence.get("rejection_status") == "inconclusive" else [])
        )
        blockers = (
            capital_admission["blockers"]
            + (
                ["research_economics_incomplete"]
                if run["result"].get("economic_state") == "incomplete_lifecycle"
                or run["result"].get("metrics", {}).get("final_equity") is None
                else []
            )
            + (["historical_lifecycle_forward_unsupported"] if lifecycle_scenario else [])
            + (
                ["research_execution_failed"]
                if run["result"].get("execution_status") in {"failed", "compensating"}
                else []
            )
            + (["execution_halted"] if risk["halted"] else [])
            + (["existing_inventory"] if inventory else [])
            + (["pending_orders"] if pending else [])
            + (["strategy_ownership"] if owners else [])
            + (
                ["leverage_limit"]
                if any(
                    Decimal(leg["leverage"]) > Decimal(str(risk["max_leverage"]))
                    for leg in version["definition"]["legs"]
                )
                else []
            )
            + (["deployment_limit"] if count + len(symbols) > 20 else [])
        )
        preview = {
            "name": name,
            "version_revision": version["revision"],
            "run_id": run_id,
            "source": run["source"],
            "version_id": version["id"],
            "portfolio_content_hash": version["content_hash"],
            "result_hash": manifest["result_hash"],
            "definition": version["definition"],
            "hypothesis": version["hypothesis"],
            "risk_policy": risk,
            "risk_policy_hash": digest(risk),
            "implementation": r.engine_identity["code_fingerprint"],
            "cost_differences": differences,
            "risk_differences": risk_differences,
            "required_acknowledgements": required,
            "blockers": blockers,
            "inventory": inventory,
            "pending_order_ids": pending,
            "owner_ids": owners,
            "research_evidence": evidence,
            "evaluation": run["result"].get("evaluation", {"mode": config["evaluation"]}),
            "metrics": run["result"]["metrics"],
            "capital_basis": "current complete account equity × declared capital percentage; durable promises are admitted before deployment and retained until verified terminal flatness",
            "capital_admission": capital_admission,
            "capital_policy_hash": digest(capital_admission["policy"]),
            "liquidity_review": liquidity_review,
            "execution_model": "post-close observed bid/ask, sequential local full fills; durable reduce-group compensation may also fail; no exchange order",
        }
        identity = json.loads(dumps(preview))
        actual = identity.get("capital_admission", {}).get("actual_admission")
        if actual is not None:
            # Display-only account read time is not an economic condition.
            # Equity, owners, quantities and policies remain hash-bound.
            actual.pop("as_of", None)
        return preview | {"preview_hash": digest(identity)}

    def approve(self, body, actor):
        if not 12 <= len(body["review"].strip()) <= 2000:
            raise PlatformError("portfolio_review", "Record a review of 12–2000 characters.", 422)
        with self.store.write() as conn:
            preview = self.preview(body["run_id"], conn)
            if preview["preview_hash"] != body["preview_hash"] or preview["blockers"]:
                raise PlatformError(
                    "portfolio_release_changed",
                    "Release conditions changed or are blocked; review a fresh preview.",
                    409,
                )
            if not set(preview["required_acknowledgements"]).issubset(body["acknowledgements"]):
                raise PlatformError(
                    "portfolio_release_ack",
                    "Acknowledge the displayed execution and research differences.",
                    409,
                )
            identifier, timestamp = new_id(), now_ms()
            approval = {
                "id": identifier,
                "source": preview["source"],
                "run_id": body["run_id"],
                "version_id": preview["version_id"],
                "preview": preview,
                "review": body["review"].strip(),
                "acknowledgements": sorted(set(body["acknowledgements"])),
                "actor": actor,
                "approved_at": timestamp,
            }
            conn.execute(
                "INSERT INTO portfolio_releases VALUES(?,?,?,?,?,?,'approved',NULL,?)",
                (
                    identifier,
                    preview["source"],
                    body["run_id"],
                    preview["version_id"],
                    dumps(approval),
                    digest(approval),
                    timestamp,
                ),
            )
            self.store.audit(
                conn,
                preview["source"],
                "portfolio.release_approved",
                "Portfolio research release approved",
                {"release_id": identifier, "preview_hash": preview["preview_hash"], "actor": actor},
            )
        return self.get(identifier)

    def activate(self, identifier, actor):
        with self.store.write() as conn:
            release = self.get(identifier, conn)
            if release["status"] == "deployed":
                return release
            preview = self.preview(release["run_id"], conn)
            if (
                preview["preview_hash"] != release["approval"]["preview"]["preview_hash"]
                or preview["blockers"]
            ):
                raise PlatformError(
                    "portfolio_release_changed",
                    "Review a fresh approval after release conditions change.",
                    409,
                )
            group = self.runtime.managed_portfolios.activate(release, actor, conn)
            conn.execute(
                "UPDATE portfolio_releases SET status='deployed',group_id=? WHERE id=?",
                (group["id"], identifier),
            )
        return self.get(identifier)
