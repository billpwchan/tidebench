"""Immutable strategy definitions and reviewed research-to-paper releases.

User-visible names are metadata. Economic configuration, implementation identity,
selection evidence, risk policy and inventory admission are separate contracts.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .platform import PlatformError
from .schemas import InputModel
from .store import dumps, new_id, now_ms
from .strategy_program import ProStrategyInput


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def canonical(value):
    if isinstance(value, Decimal):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


class StrategyDefinition(InputModel):
    schema_version: Literal[1] = 1
    product: Literal["SPOT", "SWAP"] = "SPOT"
    bar: Literal["1m", "5m", "15m", "1H", "4H", "1Dutc"] = "1H"
    strategy: ProStrategyInput = Field(default_factory=ProStrategyInput)
    direction: Literal["long_only", "short_only", "long_short"] = "long_only"
    leverage: Decimal = Field(default=1, ge=1, le=50)

    @model_validator(mode="after")
    def spot_constraints(self):
        if self.product == "SPOT" and (self.direction != "long_only" or self.leverage != 1):
            raise ValueError("Spot definitions are long-only with leverage one.")
        return self

    def record(self):
        return canonical(self.model_dump())


class StrategyProjectInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    hypothesis: str = Field(min_length=12, max_length=4000)
    definition: StrategyDefinition


class StrategyVersionInput(InputModel):
    hypothesis: str = Field(min_length=12, max_length=4000)
    definition: StrategyDefinition
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ReleasePreviewInput(InputModel):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    selection: str = Field(default="single", min_length=1, max_length=80)


class ReleaseInput(ReleasePreviewInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledgements: list[
        Literal["in_sample_selection", "execution_cost_difference", "no_oos_evidence", "post_test_selection"]
    ] = Field(default_factory=list, max_length=4)
    review: str = Field(min_length=12, max_length=2000)


class StrategyRegistry:
    def __init__(self, store, implementation):
        self.store, self.implementation = store, implementation
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS strategy_versions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES strategy_projects(id),revision INTEGER NOT NULL,parent_id TEXT REFERENCES strategy_versions(id),hypothesis TEXT NOT NULL,definition TEXT NOT NULL,implementation TEXT NOT NULL,content_hash TEXT NOT NULL,created_by TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(project_id,revision),UNIQUE(project_id,content_hash));
                CREATE INDEX IF NOT EXISTS strategy_versions_project ON strategy_versions(project_id,revision DESC);
                CREATE TABLE IF NOT EXISTS paper_releases(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,strategy_version_id TEXT NOT NULL REFERENCES strategy_versions(id),source TEXT NOT NULL,selection TEXT NOT NULL,preview TEXT NOT NULL,preview_hash TEXT NOT NULL,config TEXT NOT NULL,review TEXT NOT NULL,acknowledgements TEXT NOT NULL,approved_by TEXT NOT NULL,approved_at INTEGER NOT NULL,approval_hash TEXT NOT NULL,status TEXT NOT NULL,deployment_id TEXT,activated_by TEXT,activated_at INTEGER);
                CREATE INDEX IF NOT EXISTS paper_releases_history ON paper_releases(source,approved_at DESC,id DESC);
            """)

    @staticmethod
    def version_row(row):
        return dict(row) | {
            "definition": json.loads(row["definition"]),
            "implementation": json.loads(row["implementation"]),
        }

    def version(self, identifier, *, conn=None):
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            row = connection.execute("SELECT * FROM strategy_versions WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("strategy_version_not_found", "Strategy version not found.", 404)
        item = self.version_row(row)
        expected = self.definition_hash(item["definition"], item["hypothesis"], item["implementation"])
        if item["content_hash"] != expected:
            raise PlatformError(
                "strategy_version_integrity", "Strategy definition integrity check failed.", 409
            )
        return item

    @staticmethod
    def definition_hash(definition, hypothesis, implementation):
        return digest({"definition": definition, "hypothesis": hypothesis, "implementation": implementation})

    def projects(self):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT p.*,COUNT(v.id) version_count,MAX(v.revision) latest_revision "
                "FROM strategy_projects p LEFT JOIN strategy_versions v ON v.project_id=p.id "
                "GROUP BY p.id ORDER BY p.created_at DESC,p.id DESC LIMIT 200"
            ).fetchall()
        return [dict(row) for row in rows]

    def project(self, identifier):
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM strategy_projects WHERE id=?", (identifier,)).fetchone()
            versions = conn.execute(
                "SELECT * FROM strategy_versions WHERE project_id=? ORDER BY revision DESC", (identifier,)
            ).fetchall()
        if not row:
            raise PlatformError("strategy_not_found", "Strategy project not found.", 404)
        return dict(row) | {"versions": [self.version_row(item) for item in versions]}

    def create_project(self, name, hypothesis, definition, actor):
        identifier = new_id()
        with self.store.write() as conn:
            conn.execute(
                "INSERT INTO strategy_projects VALUES(?,?,?,?)", (identifier, name.strip(), actor, now_ms())
            )
            version = self.create_version(identifier, hypothesis, definition, actor, conn=conn)
        return self.project(identifier) | {"version": version}

    def create_version(self, project_id, hypothesis, definition, actor, parent_id=None, *, conn=None):
        definition = StrategyDefinition.model_validate(definition).record()
        hypothesis = hypothesis.strip()
        if len(hypothesis) < 12:
            raise PlatformError(
                "strategy_hypothesis", "Describe the economic hypothesis in at least 12 characters.", 422
            )
        fingerprint = self.definition_hash(definition, hypothesis, self.implementation)
        with nullcontext(conn) if conn is not None else self.store.write() as connection:
            if not connection.execute("SELECT 1 FROM strategy_projects WHERE id=?", (project_id,)).fetchone():
                raise PlatformError("strategy_not_found", "Strategy project not found.", 404)
            if parent_id and self.version(parent_id, conn=connection)["project_id"] != project_id:
                raise PlatformError("strategy_parent", "Parent version must belong to this project.", 409)
            existing = connection.execute(
                "SELECT * FROM strategy_versions WHERE project_id=? AND content_hash=?",
                (project_id, fingerprint),
            ).fetchone()
            if existing:
                return self.version_row(existing)
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM strategy_versions WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            identifier = new_id()
            connection.execute(
                "INSERT INTO strategy_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    revision,
                    parent_id,
                    hypothesis,
                    dumps(definition),
                    dumps(self.implementation),
                    fingerprint,
                    actor,
                    now_ms(),
                ),
            )
            self.store.audit(
                connection,
                "system",
                "strategy.version_created",
                "Immutable strategy version created",
                {
                    "project_id": project_id,
                    "version_id": identifier,
                    "revision": revision,
                    "content_hash": fingerprint,
                    "actor": actor,
                },
            )
            return self.version(identifier, conn=connection)

    def validate_binding(self, config, dataset):
        identifier = config.get("strategy_version_id")
        if not identifier:
            return None
        version = self.version(identifier)
        if version["implementation"]["code_fingerprint"] != self.implementation["code_fingerprint"]:
            raise PlatformError(
                "strategy_implementation_changed",
                "Clone the definition under the current implementation before new research.",
                409,
            )
        definition = version["definition"]
        observed = StrategyDefinition.model_validate(
            {
                "product": "SWAP" if dataset["inst_id"].endswith("-SWAP") else "SPOT",
                "bar": dataset["bar"],
                "strategy": config["strategy"],
                "direction": config["direction"],
                "leverage": config["leverage"],
            }
        ).record()
        if observed != definition:
            raise PlatformError(
                "strategy_version_mismatch",
                "Research parameters, product and interval must match the selected immutable version.",
                409,
            )
        return version

    @staticmethod
    def select(run, selection):
        config, plan = run["config"], run["result"]
        selected = dict(config)
        if config["mode"] == "single":
            if selection != "single":
                raise PlatformError("release_selection", "Select the single result for this run.", 422)
            scope = "fixed_hypothesis"
        elif config["mode"] in {"grid", "cost_stress"}:
            case = next((item for item in plan.get("experiments", []) if item["id"] == selection), None)
            if not case:
                raise PlatformError("release_selection", "Select an existing experiment ID.", 422)
            parameters = case["parameters"]
            selected.update(
                {
                    key: parameters[key]
                    for key in (
                        "strategy",
                        "direction",
                        "leverage",
                        "fee_bps",
                        "slippage_bps",
                        "liquidation_fee_bps",
                    )
                }
            )
            scope = "in_sample_selection" if config["mode"] == "grid" else "fixed_hypothesis_cost_scenario"
        else:
            fold = next((item for item in plan.get("folds", []) if item["id"] == selection), None)
            if not fold:
                raise PlatformError(
                    "release_selection",
                    "Select an existing OOS fold ID; no test winner is chosen automatically.",
                    422,
                )
            selected["strategy"] = fold["selected_strategy"]
            # Training chose this strategy, but an operator chooses the fold
            # only after the completed run makes every test outcome available.
            # That selection cannot establish independent final validation.
            scope = "training_selected_test_exposed_fold"
        selected["strategy"] = canonical(ProStrategyInput.model_validate(selected["strategy"]).model_dump())
        return selected, scope

    def preview_release(self, runtime, run_id, selection="single", *, conn=None):
        run = runtime.run(run_id)
        if run["status"] != "completed" or not run.get("result"):
            raise PlatformError("run_not_complete", "A completed research result is required.", 409)
        manifest = run.get("manifest") or {}
        if not manifest.get("result_hash") or digest(run["result"]) != manifest["result_hash"]:
            raise PlatformError(
                "research_integrity", "The saved research result failed its integrity check.", 409
            )
        if (manifest.get("research_implementation") or {}).get("code_fingerprint") != self.implementation[
            "code_fingerprint"
        ]:
            raise PlatformError(
                "research_implementation_changed",
                "Re-run research under the current implementation before release.",
                409,
            )
        selected, scope = self.select(run, selection)
        selection_evidence = {
            "source_result_hash": manifest["result_hash"],
            "selection": selection,
            "selected_strategy_hash": digest(selected["strategy"]),
            "independent_final_validation": False,
        }
        if run["config"]["mode"] in {"train_test", "walk_forward"}:
            fold = next(item for item in run["result"]["folds"] if item["id"] == selection)
            selection_evidence.update(
                selection_policy="operator_selected_after_test_results_available",
                selected_fold_id=fold["id"],
                test_result_hash=digest(fold["test_result"]),
                available_fold_ids=[item["id"] for item in run["result"]["folds"]],
                test_results_available=True,
                scope="Training-only parameter choice; completed chronological fold selection is not a pre-registered final test. No inference about whether the operator actually inspected test scores is made.",
            )
        dataset = runtime.catalog.get_dataset(run["config"]["dataset_id"])
        parent = (
            self.version(run["config"]["strategy_version_id"])
            if run["config"].get("strategy_version_id")
            else None
        )
        governance = runtime.governance.trials(parent["project_id"]) if parent else None
        evidence = (
            {key: governance[key] for key in ("run_count", "candidate_configurations", "scope")}
            if governance
            else {"scope": "unbound legacy research; no project-wide trial count"}
        )
        evidence["holdout_id"] = run["config"].get("holdout_id")
        hypothesis = (
            parent["hypothesis"]
            if parent
            else "Captured legacy research hypothesis; inspect and document its assumptions before expanding scope."
        )
        definition = StrategyDefinition.model_validate(
            {
                "product": "SWAP" if dataset["inst_id"].endswith("-SWAP") else "SPOT",
                "bar": dataset["bar"],
                "strategy": selected["strategy"],
                "direction": selected["direction"],
                "leverage": selected["leverage"],
            }
        ).record()
        with nullcontext(conn) if conn is not None else self.store.read() as connection:
            risk = runtime.book.risk(run["source"], connection)
            positions = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM pro_positions WHERE source=? AND inst_id=? AND quantity!='0'",
                    (run["source"], dataset["inst_id"]),
                )
            ]
            pending = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,payload,reservation FROM pro_orders WHERE source=? AND status='pending'",
                    (run["source"],),
                )
                if json.loads(row["payload"])["inst_id"] == dataset["inst_id"]
            ]
            owners = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM pro_deployments WHERE source=? AND inst_id=? AND status='running'",
                    (run["source"], dataset["inst_id"]),
                )
            ]
        differences = [
            {"field": key, "research": str(selected[key]), "execution": str(risk[key])}
            for key in ("fee_bps", "slippage_bps", "liquidation_fee_bps")
            if Decimal(str(selected[key])) != Decimal(str(risk[key]))
        ]
        required = (["in_sample_selection"] if scope == "in_sample_selection" else []) + (
            ["execution_cost_difference"] if differences else []
        )
        if run["config"]["mode"] in {"train_test", "walk_forward"}:
            required.append("post_test_selection")
        else:
            required.append("no_oos_evidence")
        blockers = (
            (["execution_halted"] if risk["halted"] else [])
            + (["existing_inventory"] if positions else [])
            + (["pending_orders"] if pending else [])
            + (["strategy_ownership"] if owners else [])
        )
        config = {
            "source": run["source"],
            "inst_id": dataset["inst_id"],
            "bar": dataset["bar"],
            "strategy": definition["strategy"],
            "direction": definition["direction"],
            "leverage": definition["leverage"],
            "allocation": definition["strategy"]["allocation"],
            "research_run_id": run_id,
            "research_result_hash": manifest["result_hash"],
            "research_selection": selection,
            "risk_policy_hash": digest(risk),
            "research_implementation": self.implementation["code_fingerprint"],
        }
        preview = {
            "run_id": run_id,
            "result_hash": manifest["result_hash"],
            "selection": selection,
            "selection_scope": scope,
            "selection_evidence": selection_evidence,
            "research_governance": evidence,
            "base_strategy_version_id": parent["id"] if parent else None,
            "definition": definition,
            "hypothesis": hypothesis,
            "strategy_content_hash": self.definition_hash(definition, hypothesis, self.implementation),
            "execution_config": config,
            "risk_policy": risk,
            "cost_differences": differences,
            "required_acknowledgements": required,
            "blockers": blockers,
            "inventory_policy": "require_flat_and_no_pending",
            "positions": positions,
            "pending_order_ids": [row["id"] for row in pending],
            "model_difference": "Historical next-open full fills versus observed post-close bid/ask local full fills; no exchange order is sent.",
        }
        return preview | {"preview_hash": digest(preview)}

    def approve_release(self, runtime, body, actor):
        with self.store.write() as conn:
            preview = self.preview_release(runtime, body["run_id"], body["selection"], conn=conn)
            if preview["preview_hash"] != body["preview_hash"]:
                raise PlatformError(
                    "release_preview_changed",
                    "Risk, inventory or research changed. Review a fresh release preview.",
                    409,
                )
            if preview["blockers"]:
                raise PlatformError(
                    "release_blocked",
                    "Resolve release blockers before approval: " + ", ".join(preview["blockers"]),
                    409,
                )
            if not set(preview["required_acknowledgements"]).issubset(body["acknowledgements"]):
                raise PlatformError(
                    "release_acknowledgement",
                    "Explicitly acknowledge the displayed research/execution differences.",
                    409,
                )
            parent_id = preview["base_strategy_version_id"]
            if parent_id:
                project_id = self.version(parent_id, conn=conn)["project_id"]
            else:
                project_id = new_id()
                conn.execute(
                    "INSERT INTO strategy_projects VALUES(?,?,?,?)",
                    (project_id, "Research " + body["run_id"][:8], actor, now_ms()),
                )
            version = self.create_version(
                project_id, preview["hypothesis"], preview["definition"], actor, parent_id, conn=conn
            )
            identifier = new_id()
            config = preview["execution_config"] | {
                "strategy_version_id": version["id"],
                "strategy_content_hash": version["content_hash"],
                "release_id": identifier,
            }
            approval = {
                "id": identifier,
                "run_id": body["run_id"],
                "strategy_version_id": version["id"],
                "source": config["source"],
                "selection": body["selection"],
                "preview": preview,
                "preview_hash": preview["preview_hash"],
                "config": config,
                "review": body["review"],
                "acknowledgements": body["acknowledgements"],
                "approved_by": actor,
                "approved_at": now_ms(),
            }
            encoded = {
                key: dumps(value) if key in {"preview", "config", "acknowledgements"} else value
                for key, value in approval.items()
            }
            conn.execute(
                "INSERT INTO paper_releases(id,run_id,strategy_version_id,source,selection,preview,preview_hash,config,review,acknowledgements,approved_by,approved_at,approval_hash,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'approved')",
                (*encoded.values(), digest(approval)),
            )
            self.store.audit(
                conn,
                config["source"],
                "strategy.release_approved",
                "Research-to-paper release approved",
                {
                    "release_id": identifier,
                    "run_id": body["run_id"],
                    "version_id": version["id"],
                    "preview_hash": preview["preview_hash"],
                    "actor": actor,
                },
            )
        return self.release(identifier)

    @staticmethod
    def release_row(row):
        item = dict(row) | {key: json.loads(row[key]) for key in ("preview", "config", "acknowledgements")}
        approval = {
            key: item[key]
            for key in (
                "id",
                "run_id",
                "strategy_version_id",
                "source",
                "selection",
                "preview",
                "preview_hash",
                "config",
                "review",
                "acknowledgements",
                "approved_by",
                "approved_at",
            )
        }
        if digest(approval) != item["approval_hash"]:
            raise PlatformError("release_integrity", "Release approval integrity check failed.", 409)
        return item

    def release(self, identifier):
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM paper_releases WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("release_not_found", "Paper release not found.", 404)
        return self.release_row(row)

    def releases(self, source):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_releases WHERE source=? ORDER BY approved_at DESC,id DESC LIMIT 100",
                (source,),
            ).fetchall()
        return [self.release_row(row) for row in rows]

    def activate_release(self, runtime, identifier, actor):
        with self.store.write() as conn:
            row = conn.execute("SELECT * FROM paper_releases WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("release_not_found", "Paper release not found.", 404)
            release = self.release_row(row)
            if release["status"] == "deployed":
                return release
            preview = self.preview_release(runtime, release["run_id"], release["selection"], conn=conn)
            if preview["preview_hash"] != release["preview_hash"] or preview["blockers"]:
                raise PlatformError(
                    "release_preview_changed",
                    "Release conditions changed after approval; create a newly reviewed release.",
                    409,
                )
            version = self.version(release["strategy_version_id"], conn=conn)
            if version["content_hash"] != release["config"]["strategy_content_hash"]:
                raise PlatformError("release_integrity", "Release strategy identity changed.", 409)
            expected_config = preview["execution_config"] | {
                "strategy_version_id": version["id"],
                "strategy_content_hash": version["content_hash"],
                "release_id": identifier,
            }
            if release["config"] != expected_config:
                raise PlatformError(
                    "release_integrity",
                    "Release configuration differs from the reviewed research selection.",
                    409,
                )
            deployment = runtime.deploy(release["config"], actor, conn=conn)
            conn.execute(
                "UPDATE paper_releases SET status='deployed',deployment_id=?,activated_by=?,activated_at=? WHERE id=?",
                (deployment["id"], actor, now_ms(), identifier),
            )
            self.store.audit(
                conn,
                release["source"],
                "strategy.release_activated",
                "Reviewed paper release activated",
                {"release_id": identifier, "deployment_id": deployment["id"], "actor": actor},
            )
        return self.release(identifier)
