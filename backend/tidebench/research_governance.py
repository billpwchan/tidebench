"""Pre-registered one-use holdouts and cross-run trial accounting.

This controls access through research APIs, not knowledge of public market data.
A sealed interval cannot make an already informed researcher statistically blind.
"""

import json
from decimal import Decimal

from pydantic import Field

from .catalog import CATALOG_BARS
from .platform import PlatformError
from .schemas import InputModel, Money
from .store import dumps, encode, new_id, now_ms
from .strategy_registry import canonical, digest


class HoldoutInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    strategy_version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    start_ts: int = Field(ge=1577836800000)
    end_ts: int = Field(ge=1577836800000)
    benchmark: str = Field(min_length=4, max_length=1000)
    rejection_plan: str = Field(min_length=20, max_length=4000)
    initial_cash: Money = Decimal(10000)
    fee_bps: Decimal = Field(default=10, ge=0, le=100)
    slippage_bps: Decimal = Field(default=5, ge=0, le=100)
    liquidation_fee_bps: Decimal = Field(default=50, ge=0, le=500)
    mark_dataset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    funding_dataset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ResearchGovernance:
    def __init__(self, store, catalog, registry):
        self.store, self.catalog, self.registry = store, catalog, registry
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_holdouts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,version_id TEXT NOT NULL,source TEXT NOT NULL,inst_id TEXT NOT NULL,bar TEXT NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,plan TEXT NOT NULL,plan_hash TEXT NOT NULL,status TEXT NOT NULL,run_id TEXT,created_by TEXT NOT NULL,created_at INTEGER NOT NULL,consumed_at INTEGER);
                CREATE INDEX IF NOT EXISTS research_holdouts_market ON research_holdouts(source,inst_id,bar,start_ts,end_ts);
            """)

    @staticmethod
    def row(row):
        output = dict(row)
        output["plan"] = json.loads(output["plan"])
        if digest(output["plan"]) != output["plan_hash"]:
            raise PlatformError("holdout_integrity", "Holdout plan failed its integrity check.", 409)
        return output

    def list(self, project_id=None):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM research_holdouts WHERE (? IS NULL OR project_id=?) ORDER BY created_at DESC LIMIT 100",
                (project_id, project_id),
            ).fetchall()
        output = [self.row(row) for row in rows]
        if hasattr(self, "facts"):
            with self.store.read() as conn:
                for item in output:
                    fact = self.facts.consumption(conn, "single", item["id"])
                    if fact:
                        item.update(status="consumed", run_id=fact["run_id"], consumed_at=fact["created_at"])
                        if not conn.execute(
                            "SELECT 1 FROM pro_runs WHERE id=?", (item["run_id"],)
                        ).fetchone():
                            item["status"] = "consumed_unavailable"
        return output

    def overlap(self, conn, dataset, start, end):
        # Bar aggregation and dataset identity cannot reset market-time exposure.
        return conn.execute(
            "SELECT * FROM research_holdouts WHERE source=? AND inst_id=? AND start_ts<? AND end_ts>?",
            (dataset["source"], dataset["inst_id"], end, start),
        ).fetchall()

    def create(self, body, actor):
        from .pro_api import ResearchInput

        config = encode(HoldoutInput.model_validate(body).model_dump())
        version = self.registry.version(config["strategy_version_id"])
        dataset = self.catalog.get_dataset(config["dataset_id"])
        interval = CATALOG_BARS[dataset["bar"]]
        start, end = config["start_ts"], config["end_ts"]
        if (
            dataset["kind"] != "trade"
            or not dataset["start"] <= start < end <= dataset["end"]
            or start % interval
            or end % interval
            or end - start < 2 * interval
        ):
            raise PlatformError(
                "holdout_window", "Choose an aligned, covered holdout of at least two bars.", 422
            )
        test = canonical(
            ResearchInput.model_validate(
                {
                    "strategy_version_id": version["id"],
                    "strategy": version["definition"]["strategy"],
                    "direction": version["definition"]["direction"],
                    "leverage": version["definition"]["leverage"],
                    **{
                        key: config[key]
                        for key in (
                            "dataset_id",
                            "start_ts",
                            "end_ts",
                            "mark_dataset_id",
                            "funding_dataset_id",
                            "initial_cash",
                            "fee_bps",
                            "slippage_bps",
                            "liquidation_fee_bps",
                        )
                    },
                }
            ).model_dump(exclude={"holdout_id"})
        )
        self.registry.validate_binding(test, dataset)
        if dataset["inst_id"].endswith("-SWAP") and (
            not test["mark_dataset_id"] or not test["funding_dataset_id"]
        ):
            raise PlatformError(
                "holdout_inputs",
                "A perpetual holdout requires trade, mark and realized funding versions.",
                422,
            )
        plan = {
            "name": config["name"],
            "strategy_version_id": version["id"],
            "strategy_hash": version["content_hash"],
            "dataset_hash": dataset["content_hash"],
            "test_config": canonical(test),
            "benchmark": config["benchmark"],
            "rejection_plan": config["rejection_plan"],
            "scope": "research_api_workflow_seal; does not guarantee blindness to public or externally inspected data",
        }
        identifier = new_id()
        timestamp = now_ms()
        with self.store.write() as conn:
            if hasattr(self, "facts"):
                self.facts.unseen(conn, dataset["source"], [dataset["inst_id"]], start, end)
            if self.overlap(conn, dataset, start, end):
                raise PlatformError(
                    "holdout_overlap", "An existing holdout already reserves this market interval.", 409
                )
            # Dataset aliases do not reset exposure history: market/time identity
            # is checked across all saved runs and portfolio packages, regardless
            # of aggregation. Each prior run owns its own 2000-bar access window.
            previous = conn.execute(
                "SELECT 1 FROM pro_runs r JOIN catalog_datasets d ON d.id=json_extract(r.config,'$.dataset_id') JOIN json_each(?) intervals ON intervals.key=d.bar WHERE d.source=? AND d.inst_id=? AND MAX(json_extract(d.manifest,'$.start'),COALESCE(json_extract(r.config,'$.start_ts'),json_extract(d.manifest,'$.start'))-2000*intervals.value)<? AND COALESCE(json_extract(r.config,'$.end_ts'),json_extract(d.manifest,'$.end'))>? LIMIT 1",
                (dumps(CATALOG_BARS), dataset["source"], dataset["inst_id"], end, start),
            ).fetchone()
            portfolios = conn.execute(
                "SELECT 1 FROM portfolio_runs p,json_each(p.manifest,'$.packages') market WHERE p.source=? AND json_extract(market.value,'$.inst_id')=? AND json_extract(p.manifest,'$.start')<? AND json_extract(p.manifest,'$.end')>? LIMIT 1",
                (dataset["source"], dataset["inst_id"], end, start),
            ).fetchone()
            if previous or portfolios:
                raise PlatformError(
                    "holdout_exposed",
                    "Research already accessed this interval. It cannot be newly registered as an unseen holdout.",
                    409,
                )
            conn.execute(
                "INSERT INTO research_holdouts VALUES(?,?,?,?,?,?,?,?,?,?,'sealed',NULL,?,?,NULL)",
                (
                    identifier,
                    version["project_id"],
                    version["id"],
                    dataset["source"],
                    dataset["inst_id"],
                    dataset["bar"],
                    start,
                    end,
                    dumps(plan),
                    digest(plan),
                    actor,
                    timestamp,
                ),
            )
            if hasattr(self, "facts"):
                self.facts.reserve(
                    conn, identifier, "single", dataset["source"], [dataset["inst_id"]], start, end, timestamp
                )
            self.store.audit(
                conn,
                dataset["source"],
                "pro.holdout_sealed",
                "Fixed strategy, costs and rejection plan pre-registered",
                {"holdout_id": identifier, "plan_hash": digest(plan), "actor": actor},
            )
        return next(row for row in self.list(version["project_id"]) if row["id"] == identifier)

    def admit(self, conn, config, dataset, identifier, replay_of=None):
        start = config.get("start_ts") if config.get("start_ts") is not None else dataset["start"]
        end = config.get("end_ts") if config.get("end_ts") is not None else dataset["end"]
        access_start = max(dataset["start"], start - 2000 * CATALOG_BARS[dataset["bar"]])
        overlaps = self.overlap(conn, dataset, access_start, end)
        requested = config.get("holdout_id")
        if replay_of:
            # Only the existing replay endpoint supplies this server argument;
            # the body cannot grant a replay exemption.
            return
        if hasattr(self, "facts"):
            self.facts.guard(
                conn,
                dataset["source"],
                [(dataset["inst_id"], access_start, end)],
                allowed=("single", requested) if requested else None,
            )
            if requested and self.facts.consumption(conn, "single", requested):
                raise PlatformError(
                    "holdout_consumed",
                    "The holdout was already evaluated; restoring older state cannot reset it.",
                    409,
                )
        if not overlaps and not requested:
            return
        if len(overlaps) != 1 or overlaps[0]["id"] != requested:
            raise PlatformError(
                "holdout_reserved",
                "This interval is reserved for a pre-registered holdout. Use its exact one-use evaluation.",
                409,
            )
        holdout = self.row(overlaps[0])
        if holdout["status"] != "sealed":
            raise PlatformError(
                "holdout_consumed",
                "The holdout was already evaluated. Replay the captured run for reproduction; it is not new independent evidence.",
                409,
            )
        comparison = {key: config.get(key) for key in holdout["plan"]["test_config"]}
        from .pro_api import ResearchInput

        comparison = canonical(ResearchInput.model_validate(comparison).model_dump(exclude={"holdout_id"}))
        if comparison != holdout["plan"]["test_config"]:
            raise PlatformError(
                "holdout_config",
                "Holdout evaluation must match its pre-registered strategy, window, data and costs exactly.",
                409,
            )
        version = self.registry.version(holdout["version_id"], conn=conn)
        if (
            version["content_hash"] != holdout["plan"]["strategy_hash"]
            or dataset["content_hash"] != holdout["plan"]["dataset_hash"]
        ):
            raise PlatformError("holdout_identity", "Holdout input identity changed.", 409)
        consumed_at = now_ms()
        if hasattr(self, "facts"):
            self.facts.consume(conn, "single", requested, identifier, holdout["plan_hash"], consumed_at)
        conn.execute(
            "UPDATE research_holdouts SET status='consumed',run_id=?,consumed_at=? WHERE id=?",
            (identifier, consumed_at, requested),
        )
        self.store.audit(
            conn,
            dataset["source"],
            "pro.holdout_consumed",
            "One-use holdout evaluation admitted",
            {"holdout_id": requested, "run_id": identifier},
        )

    def guard_portfolio(self, conn, packages):
        if hasattr(self, "facts"):
            for package in packages:
                self.facts.guard(
                    conn, package["source"], [(package["inst_id"], package["start"], package["end"])]
                )
        for package in packages:
            if self.overlap(conn, package, package["start"], package["end"]):
                raise PlatformError(
                    "holdout_reserved", "A portfolio leg overlaps a sealed or consumed research holdout.", 409
                )

    def trials(self, project_id):
        from .research_protocol import ResearchFacts

        # Protected facts survive restoration even when their run/artifact does
        # not. Current runs supply status, never a second copy of the attempt.
        records = {}
        with self.store.read() as conn:
            if hasattr(self, "facts"):
                for row in conn.execute(
                    "SELECT * FROM research_trials WHERE kind='single' AND project_id=?",
                    (project_id,),
                ):
                    fact = self.facts.checked(row)
                    run = conn.execute(
                        "SELECT status,error FROM pro_runs WHERE id=?", (fact["run_id"],)
                    ).fetchone()
                    records[fact["run_id"]] = {
                        "id": fact["run_id"],
                        "trial_id": fact["id"],
                        "run_id": fact["run_id"],
                        "project_id": fact["project_id"],
                        "version_id": fact["version_id"],
                        "status": run["status"] if run else "evidence_unavailable",
                        "error": run["error"] if run else None,
                        "config": json.loads(fact["config"]),
                        "config_hash": fact["config_hash"],
                        "created_at": fact["created_at"],
                        "candidate_configurations": fact["candidate_count"],
                        "attempt_type": fact["attempt_type"],
                        "replay_of": fact["replay_of"],
                        "root_run_id": fact["root_run_id"],
                        "fact_protected": True,
                    }
            # Retained pre-upgrade runs without a fact remain visible. They do
            # not erase or duplicate protected history and are labeled legacy.
            for row in conn.execute(
                "SELECT r.id,r.status,r.config,r.created_at,r.error,r.manifest FROM pro_runs r JOIN strategy_versions v ON v.id=json_extract(r.config,'$.strategy_version_id') WHERE v.project_id=?",
                (project_id,),
            ):
                if row["id"] in records:
                    continue
                config = json.loads(row["config"])
                manifest = json.loads(row["manifest"]) if row["manifest"] else {}
                replay_of = manifest.get("replay_of")
                records[row["id"]] = {
                    "id": row["id"],
                    "trial_id": None,
                    "run_id": row["id"],
                    "project_id": project_id,
                    "version_id": config.get("strategy_version_id"),
                    "status": row["status"],
                    "error": row["error"],
                    "config": config,
                    "config_hash": digest(ResearchFacts.economic(config)),
                    "created_at": row["created_at"],
                    "candidate_configurations": ResearchFacts.candidates("single", config),
                    "attempt_type": "replay" if replay_of else "evaluation",
                    "replay_of": replay_of,
                    "root_run_id": replay_of or row["id"],
                    "fact_protected": False,
                }
        items = sorted(records.values(), key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return {
            "project_id": project_id,
            "run_count": len(items),
            "recorded_attempts": len(items),
            "primary_evaluations": sum(r["attempt_type"] == "evaluation" for r in items),
            "replay_attempts": sum(r["attempt_type"] == "replay" for r in items),
            "candidate_configurations": sum(r["candidate_configurations"] for r in items),
            "distinct_configurations": len({r["config_hash"] for r in items}),
            "evidence_unavailable": sum(r["status"] == "evidence_unavailable" for r in items),
            "legacy_attempts": sum(not r["fact_protected"] for r in items),
            "items": items[:100],
            "scope": "All retained admitted version-bound project attempts, including queued/failed/replayed work and attempts whose run evidence is unavailable after restore. Replays are not independent primary evaluations. Candidate counts sum each run's configurations, not OOS folds; distinct_configurations counts complete run configurations, not effective independent hypotheses. Legacy current runs without protected facts are labeled. Unbound/external trials and relabelled projects cannot be assigned to this project.",
            "holdouts": self.list(project_id),
        }
