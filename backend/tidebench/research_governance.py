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
        return [self.row(row) for row in rows]

    def overlap(self, conn, dataset, start, end):
        return conn.execute(
            "SELECT * FROM research_holdouts WHERE source=? AND inst_id=? AND bar=? AND start_ts<? AND end_ts>?",
            (dataset["source"], dataset["inst_id"], dataset["bar"], end, start),
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
        with self.store.write() as conn:
            if self.overlap(conn, dataset, start, end):
                raise PlatformError(
                    "holdout_overlap", "An existing holdout already reserves this market interval.", 409
                )
            # Dataset aliases do not reset exposure history: market/time identity
            # is checked across all saved runs and portfolio packages.
            previous = conn.execute(
                "SELECT 1 FROM pro_runs r JOIN catalog_datasets d ON d.id=json_extract(r.config,'$.dataset_id') WHERE d.source=? AND d.inst_id=? AND d.bar=? AND MAX(json_extract(d.manifest,'$.start'),COALESCE(json_extract(r.config,'$.start_ts'),json_extract(d.manifest,'$.start'))-2000*?)<? AND COALESCE(json_extract(r.config,'$.end_ts'),json_extract(d.manifest,'$.end'))>? LIMIT 1",
                (dataset["source"], dataset["inst_id"], dataset["bar"], interval, end, start),
            ).fetchone()
            portfolios = conn.execute(
                "SELECT 1 FROM portfolio_runs p,json_each(p.manifest,'$.packages') market WHERE p.source=? AND json_extract(market.value,'$.inst_id')=? AND json_extract(p.manifest,'$.bar')=? AND json_extract(p.manifest,'$.start')<? AND json_extract(p.manifest,'$.end')>? LIMIT 1",
                (dataset["source"], dataset["inst_id"], dataset["bar"], end, start),
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
                    now_ms(),
                ),
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
        conn.execute(
            "UPDATE research_holdouts SET status='consumed',run_id=?,consumed_at=? WHERE id=?",
            (identifier, now_ms(), requested),
        )
        self.store.audit(
            conn,
            dataset["source"],
            "pro.holdout_consumed",
            "One-use holdout evaluation admitted",
            {"holdout_id": requested, "run_id": identifier},
        )

    def guard_portfolio(self, conn, packages):
        for package in packages:
            if self.overlap(conn, package, package["start"], package["end"]):
                raise PlatformError(
                    "holdout_reserved", "A portfolio leg overlaps a sealed or consumed research holdout.", 409
                )

    def trials(self, project_id):
        def recorded():
            with self.store.read() as conn:
                yield from conn.execute(
                    "SELECT r.id,r.status,r.config,r.created_at,r.error FROM pro_runs r JOIN strategy_versions v ON v.id=json_extract(r.config,'$.strategy_version_id') WHERE v.project_id=? ORDER BY r.created_at DESC,r.id DESC",
                    (project_id,),
                )

        items = []
        total, run_count = 0, 0
        for row in recorded():
            run_count += 1
            config = json.loads(row["config"])
            if config.get("mode") in {"grid", "train_test", "walk_forward"}:
                grid = config.get("options", {}).get("grid", {})
                candidates = 1
                for values in grid.values():
                    candidates *= len(values)
            elif config.get("mode") == "cost_stress":
                candidates = max(1, len(config.get("options", {}).get("fee_bps", []))) * max(
                    1, len(config.get("options", {}).get("slippage_bps", []))
                )
            else:
                candidates = 1
            total += candidates
            if len(items) < 100:
                items.append(dict(row) | {"config": config, "candidate_configurations": candidates})
        return {
            "project_id": project_id,
            "run_count": run_count,
            "candidate_configurations": total,
            "items": items,
            "scope": "all recorded version-bound runs in this project, including queued/failed/replayed attempts; candidates count distinct configurations per run, not each OOS fold. External trials and relabelled projects are not detectable.",
            "holdouts": self.list(project_id),
        }
