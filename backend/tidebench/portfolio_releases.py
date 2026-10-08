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
            "execution_cost_difference", "execution_risk_difference", "no_oos_evidence", "sequential_leg_risk"
        ]
    ] = Field(default_factory=list, max_length=4)


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
            )
            if Decimal(str(config[ckey])) != Decimal(str(risk[rkey]))
        ]
        required = (
            ["sequential_leg_risk"]
            + (["execution_risk_difference"] if risk_differences else [])
            + (["execution_cost_difference"] if differences else [])
            + (["no_oos_evidence"] if config["evaluation"] == "full" else [])
        )
        blockers = (
            (["execution_halted"] if risk["halted"] else [])
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
            "evaluation": run["result"].get("evaluation", {"mode": config["evaluation"]}),
            "metrics": run["result"]["metrics"],
            "capital_basis": "current complete account equity × declared capital percentage; cash and risk are shared with the whole account",
            "execution_model": "post-close observed bid/ask, sequential local full fills; durable reduce-group compensation may also fail; no exchange order",
        }
        return preview | {"preview_hash": digest(preview)}

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
