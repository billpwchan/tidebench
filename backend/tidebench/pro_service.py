"""Durable professional research scheduling and forward-simulation supervision."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from contextlib import suppress
from decimal import Decimal, localcontext

from . import __version__
from .catalog import CATALOG_BARS, CatalogService
from .data_packages import DataPackageService
from .derivatives import FundingEvent, LinearContract, MarginTier
from .engine import ACCOUNTING_CONTEXT, Instrument, StrategyConfig
from .platform import BackupService, PlatformError, RuntimeMetrics
from .pro_execution import SimulationBook, base_size, number
from .provenance import research_identity, serialized_result
from .store import dumps, encode, new_id, now_ms

logger = logging.getLogger("tidebench.professional")
D = Decimal


class ProfessionalRuntime:
    def __init__(self, store, market, settings):
        self.store, self.market, self.settings = store, market, settings
        self.catalog = CatalogService(store, market)
        self.packages = DataPackageService(store, self.catalog)
        self.book = SimulationBook(store)
        self.backups = BackupService(store, settings)
        self.metrics = RuntimeMetrics()
        self.engine_identity = research_identity()
        self.tasks, self.inflight = [], set()
        self.snapshots, self.market_errors = {}, {}
        self.funding_checks = {}
        self.locks = defaultdict(asyncio.Lock)
        self.strategy_locks = defaultdict(asyncio.Lock)
        self.wake = asyncio.Event()
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pro_runs(id TEXT PRIMARY KEY,source TEXT NOT NULL,status TEXT NOT NULL,config TEXT NOT NULL,snapshot TEXT,manifest TEXT,result TEXT,error TEXT,progress REAL NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS pro_runs_pending ON pro_runs(status,created_at);
                CREATE INDEX IF NOT EXISTS pro_runs_history ON pro_runs(source,created_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS pro_strategy_intents(deployment_id TEXT NOT NULL,bar INTEGER NOT NULL,target TEXT,status TEXT NOT NULL,updated_at INTEGER NOT NULL,PRIMARY KEY(deployment_id,bar));
            """)
            if "summary" not in {row[1] for row in conn.execute("PRAGMA table_info(pro_runs)")}:
                conn.execute("ALTER TABLE pro_runs ADD COLUMN summary TEXT")

    async def offload(self, function, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self.inflight.add(task)
        task.add_done_callback(self.inflight.discard)
        return await asyncio.shield(task)

    async def start(self):
        if self.tasks:
            raise RuntimeError("Professional supervisors are already started")
        self.catalog.resume_pending()
        self.packages.resume_pending()
        with self.store.write() as conn:
            conn.execute(
                "UPDATE pro_runs SET status='queued',updated_at=? WHERE status='running'", (now_ms(),)
            )
        self.tasks = [
            asyncio.create_task(self.jobs_loop(), name="professional-research"),
            asyncio.create_task(self.catalog_loop(), name="professional-catalog"),
            asyncio.create_task(self.execution_loop(), name="professional-execution"),
            asyncio.create_task(self.strategy_loop(), name="professional-strategies"),
            asyncio.create_task(self.backup_loop(), name="professional-backups"),
        ]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        await self.catalog.stop_polling()
        if self.inflight:
            await asyncio.gather(*self.inflight, return_exceptions=True)

    def run(self, identifier, *, include_snapshot=False):
        with self.store.read() as conn:
            columns = (
                "*"
                if include_snapshot
                else "id,source,status,config,manifest,result,error,progress,created_at,updated_at,summary"
            )
            row = conn.execute(f"SELECT {columns} FROM pro_runs WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("not_found", "Research run not found.", 404)
        output = dict(row)
        for key in ("config", "snapshot", "manifest", "result", "summary"):
            if key in output:
                output[key] = json.loads(output[key]) if output[key] else None
        return output

    @staticmethod
    def summarize(config, plan):
        if not plan:
            return None
        return (
            (plan.get("result") or {}).get("metrics", {})
            if config["mode"] == "single"
            else plan.get(
                "oos_summary", {"mode": config["mode"], "experiments": len(plan.get("experiments", []))}
            )
        )

    def runs(self, source=None, *, limit=100, before=None):
        if not 1 <= limit <= 100:
            raise PlatformError("history_limit", "Use a history page of one to one hundred runs.", 422)
        boundary, boundary_id = 2**63 - 1, "z"
        if before:
            try:
                timestamp, boundary_id = before.split(":", 1)
                boundary = int(timestamp)
                if (
                    not 0 <= boundary < 2**63
                    or len(boundary_id) != 32
                    or any(char not in "0123456789abcdef" for char in boundary_id)
                ):
                    raise ValueError
            except (ValueError, AttributeError):
                raise PlatformError("history_cursor", "History cursor is invalid.", 422) from None
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT id,source,status,config,manifest,summary,error,progress,created_at,updated_at "
                "FROM pro_runs WHERE (? IS NULL OR source=?) AND "
                "(created_at<? OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC LIMIT ?",
                (source, source, boundary, boundary, boundary_id, limit),
            ).fetchall()
        items = []
        for row in rows:
            run = dict(row)
            for key in ("config", "manifest", "summary"):
                run[key] = json.loads(run[key]) if run[key] else None
            if run["status"] == "completed" and run["summary"] is None:
                # One bounded, lazy migration per older run; subsequent pages
                # never read or decode its potentially large financial tables.
                with self.store.read() as conn:
                    previous = conn.execute("SELECT result FROM pro_runs WHERE id=?", (run["id"],)).fetchone()
                run["summary"] = self.summarize(
                    run["config"], json.loads(previous[0]) if previous[0] else None
                )
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE pro_runs SET summary=? WHERE id=? AND summary IS NULL",
                        (dumps(run["summary"] or {}), run["id"]),
                    )
            items.append(run)
        return items

    def create_run(self, config, *, snapshot=None, replay_of=None, replay_evidence=None):
        if config.get("package_id"):
            inputs = self.packages.research_inputs(config["package_id"])
            if any(
                config.get(key) != inputs.get(key)
                for key in (
                    "dataset_id",
                    "mark_dataset_id",
                    "funding_dataset_id",
                    "start_ts",
                    "end_ts",
                    "package_manifest_hash",
                )
            ):
                raise PlatformError(
                    "research_package_mismatch",
                    "Research inputs must match the exact ready package version and UTC window.",
                    409,
                )
        dataset = self.catalog.get_dataset(config["dataset_id"])
        if dataset["kind"] != "trade" or not dataset["quality"].get("complete"):
            raise PlatformError(
                "dataset_quality", "Research requires a complete, confirmed trade dataset.", 422
            )
        for key, kind in (("mark_dataset_id", "mark"), ("funding_dataset_id", "funding")):
            identifier = config.get(key)
            if identifier:
                auxiliary = self.catalog.get_dataset(identifier)
                if (
                    auxiliary["kind"] != kind
                    or auxiliary["source"] != dataset["source"]
                    or auxiliary["inst_id"] != dataset["inst_id"]
                    or not auxiliary["quality"].get("complete")
                ):
                    raise PlatformError(
                        "dataset_mismatch",
                        "Auxiliary datasets must be complete and match the market and source.",
                        422,
                    )
                if kind == "mark" and auxiliary["bar"] != dataset["bar"]:
                    raise PlatformError(
                        "dataset_interval", "Trade and mark bars must have the same interval.", 422
                    )
                if auxiliary["start"] > dataset["start"] or auxiliary["end"] < dataset["end"]:
                    raise PlatformError(
                        "dataset_coverage", "Auxiliary datasets must cover the trade dataset range.", 422
                    )
        if dataset["inst_id"].endswith("-SWAP") and (
            not config.get("mark_dataset_id") or not config.get("funding_dataset_id")
        ):
            raise PlatformError(
                "derivative_inputs",
                "Perpetual research requires separate mark and funding dataset versions.",
                422,
            )
        interval = CATALOG_BARS[dataset["bar"]]
        start = config.get("start_ts") if config.get("start_ts") is not None else dataset["start"]
        end = config.get("end_ts") if config.get("end_ts") is not None else dataset["end"]
        if not dataset["start"] <= start < end <= dataset["end"] or start % interval or end % interval:
            raise PlatformError(
                "research_window", "Use an aligned research window within the dataset coverage.", 422
            )
        if not 2 <= (end - start) // interval <= 98000:
            raise PlatformError(
                "research_window", "Research accepts 2–98,000 selected bars plus indicator warmup.", 422
            )
        identifier, now = new_id(), now_ms()
        with self.store.write() as conn:
            if (
                conn.execute("SELECT COUNT(*) FROM pro_runs WHERE status IN ('queued','running')").fetchone()[
                    0
                ]
                >= 10
            ):
                raise PlatformError("research_queue_full", "The research queue is full.", 429)
            conn.execute(
                "INSERT INTO pro_runs(id,source,status,config,snapshot,manifest,created_at,updated_at) VALUES(?,?,'queued',?,?,?,?,?)",
                (
                    identifier,
                    dataset["source"],
                    dumps(config),
                    dumps(snapshot) if snapshot else None,
                    dumps({"replay_of": replay_of, **(replay_evidence or {})}) if replay_of else None,
                    now,
                    now,
                ),
            )
            self.store.audit(
                conn,
                dataset["source"],
                "pro.research_queued",
                "Professional research queued",
                {"run_id": identifier, "replay_of": replay_of},
            )
        self.wake.set()
        return self.run(identifier)

    def replay(self, identifier):
        run = self.run(identifier, include_snapshot=True)
        if run["status"] != "completed" or not run["snapshot"]:
            raise PlatformError("run_not_complete", "Only a completed captured run can be replayed.", 409)
        _, expected_hash = serialized_result(run["result"])
        manifest = run["manifest"] or {}
        actual_snapshot_hash = hashlib.sha256(dumps(run["snapshot"]).encode()).hexdigest()
        if (manifest.get("result_hash") is not None and manifest["result_hash"] != expected_hash) or (
            manifest.get("snapshot_hash") is not None and manifest["snapshot_hash"] != actual_snapshot_hash
        ):
            raise PlatformError(
                "run_artifact_integrity", "Saved research evidence does not match its recorded checksum.", 409
            )
        captured = (run["manifest"] or {}).get("research_implementation", {})
        return self.create_run(
            run["config"],
            snapshot=run["snapshot"],
            replay_of=identifier,
            replay_evidence={
                "expected_result_hash": expected_hash,
                "original_code_fingerprint": captured.get("code_fingerprint"),
            },
        )

    async def catalog_loop(self):
        while True:
            jobs = [job for job in self.catalog.list_jobs() if job["status"] == "queued"]
            if jobs:
                try:
                    await self.catalog.run_job(jobs[-1]["id"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Catalog worker failure")
                    await asyncio.sleep(1)
            try:
                await self.packages.advance_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Research package preparation failed")
            if not jobs:
                await asyncio.sleep(0.25)

    async def jobs_loop(self):
        while True:
            with self.store.read() as conn:
                row = conn.execute(
                    "SELECT id FROM pro_runs WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
            if row:
                await self.perform_run(row[0])
                continue
            self.wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), 1)

    async def perform_run(self, identifier):
        run = self.run(identifier, include_snapshot=True)
        with self.store.write() as conn:
            claimed = conn.execute(
                "UPDATE pro_runs SET status='running',progress=.05,updated_at=? WHERE id=? AND status='queued'",
                (now_ms(), identifier),
            )
            if claimed.rowcount != 1:
                return
        try:
            config, snapshot = run["config"], run["snapshot"]
            if snapshot is None:
                dataset = self.catalog.get_dataset(config["dataset_id"])
                tier_snapshot = (
                    await self.catalog.get_margin_tiers(dataset["inst_id"], dataset["source"])
                    if dataset["inst_id"].endswith("-SWAP")
                    else None
                )
                snapshot = {
                    "trade": dataset,
                    "mark": self.catalog.get_dataset(config["mark_dataset_id"])
                    if config.get("mark_dataset_id")
                    else None,
                    "funding": self.catalog.get_dataset(config["funding_dataset_id"])
                    if config.get("funding_dataset_id")
                    else None,
                    "margin_tiers": tier_snapshot,
                    "instrument": dataset["metadata"],
                    "captured_at": now_ms(),
                }
                if config.get("package_id"):
                    package_manifest = self.packages.get_manifest(config["package_id"])
                    snapshot["data_package"] = package_manifest
                    snapshot["funding_events"] = package_manifest["funding_events"]
                if snapshot["funding"] and "funding_events" not in snapshot:
                    events = self.catalog.load_funding(config["funding_dataset_id"])
                    start = config.get("start_ts") or dataset["start"]
                    end = config.get("end_ts") or dataset["end"]
                    captured_events = []
                    for event in events:
                        if start <= event["ts"] < end:
                            if event.get("mark_price") is None:
                                event = event | await self.catalog._settlement_mark(
                                    dataset["inst_id"], event["ts"], dataset["source"]
                                )
                            captured_events.append(event)
                    snapshot["funding_events"] = encode(captured_events)
            manifest = {
                **(run["manifest"] or {}),
                "version": 2,
                "engine_version": __version__,
                "build_sha": self.settings.build_sha,
                "research_implementation": self.engine_identity,
                "package_id": config.get("package_id"),
                "package_manifest_hash": config.get("package_manifest_hash"),
                "dataset_hash": snapshot["trade"]["content_hash"],
                "snapshot_hash": hashlib.sha256(dumps(snapshot).encode()).hexdigest(),
                "maintenance_tiers_hash": hashlib.sha256(
                    dumps(
                        {
                            key: value
                            for key, value in (snapshot.get("margin_tiers") or {}).items()
                            if key != "observed_at"
                        }
                    ).encode()
                ).hexdigest(),
                "funding_observations_hash": hashlib.sha256(
                    dumps(snapshot.get("funding_events") or []).encode()
                ).hexdigest(),
                "source": run["source"],
                "config": config,
                "replay_of": (run["manifest"] or {}).get("replay_of"),
                "funding_coverage": (
                    "importer_declared"
                    if snapshot.get("funding", {}).get("transport") == "user_import"
                    else "source_manifest_complete"
                )
                if snapshot.get("funding") and snapshot["funding"]["quality"].get("complete")
                else "not_applicable",
                "model_version": "pro-research-1",
                "margin_tiers_are_historical": False,
                "indicator_warmup": "up to 2000 preceding bars; Wilder RSI seed depends on captured warmup; not an infinite-history equivalence claim",
                "margin_tier_policy": "captured_current_tier_scenario",
            }
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE pro_runs SET snapshot=?,manifest=?,progress=.15,updated_at=? WHERE id=?",
                    (dumps(snapshot), dumps(manifest), now_ms(), identifier),
                )
            result = await self.offload(self.compute, config, snapshot, manifest)
            payload, result_hash = await self.offload(serialized_result, result)
            manifest["result_hash"] = result_hash
            if manifest.get("replay_of"):
                manifest["replay_verified"] = result_hash == manifest.get("expected_result_hash")
                if not manifest["replay_verified"]:
                    with self.store.write() as conn:
                        conn.execute(
                            "UPDATE pro_runs SET manifest=? WHERE id=?", (dumps(manifest), identifier)
                        )
                    raise PlatformError(
                        "replay_mismatch",
                        "Recomputed results differ from the captured original. No identical-replay claim is made; use the recorded application and input artifact.",
                        409,
                    )
            await self.offload(
                self.complete_run,
                identifier,
                run["source"],
                payload,
                self.summarize(config, result),
                manifest,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Professional research failed: %s", identifier)
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE pro_runs SET status='failed',error=?,updated_at=? WHERE id=?",
                    (str(exc)[:1000], now_ms(), identifier),
                )

    def complete_run(self, identifier, source, payload, summary, manifest):
        with self.store.write() as conn:
            conn.execute(
                "UPDATE pro_runs SET status='completed',result=?,summary=?,manifest=?,progress=1,updated_at=? WHERE id=?",
                (payload, dumps(summary or {}), dumps(manifest), now_ms(), identifier),
            )
            self.store.audit(
                conn,
                source,
                "pro.research_completed",
                "Professional research completed",
                {
                    "run_id": identifier,
                    "result_hash": manifest["result_hash"],
                    "replay_verified": manifest.get("replay_verified"),
                },
            )

    def compute(self, config, snapshot, manifest):
        from .pro_research import ResearchConfig, run_research_plan

        raw = snapshot["instrument"]
        decimals = {
            key: D(str(value))
            if key in {"ct_val", "ct_mult", "contract_size_base", "tick_size", "lot_size", "min_size"}
            and value is not None
            else value
            for key, value in raw.items()
        }
        if raw["inst_type"] == "SWAP":
            instrument = LinearContract.from_catalog(decimals)
        else:
            instrument = Instrument(
                **{
                    key: decimals[key]
                    for key in ("inst_id", "base", "quote", "tick_size", "lot_size", "min_size", "state")
                }
            )
        strategy = {**config["strategy"]}
        for key in ("allocation", "entry", "exit"):
            strategy[key] = D(str(strategy[key]))
        research = ResearchConfig(
            strategy=StrategyConfig(**strategy),
            direction=config["direction"],
            initial_cash=D(config["initial_cash"]),
            leverage=D(str(config["leverage"])),
            fee_bps=D(config["fee_bps"]),
            slippage_bps=D(config["slippage_bps"]),
            liquidation_fee_bps=D(config["liquidation_fee_bps"]),
            start_ts=config.get("start_ts"),
            end_ts=config.get("end_ts"),
        )
        candles = self.catalog.load_candles(config["dataset_id"])
        marks = (
            self.catalog.load_candles(config["mark_dataset_id"]) if config.get("mark_dataset_id") else None
        )
        if config.get("start_ts") is not None:
            first = next(
                (index for index, candle in enumerate(candles) if candle.ts >= config["start_ts"]),
                len(candles),
            )
            lower = max(0, first - 2000)
            candles = candles[lower:]
        if config.get("end_ts") is not None:
            candles = [candle for candle in candles if candle.ts < config["end_ts"]]
        if marks is not None:
            marks = [candle for candle in marks if candles[0].ts <= candle.ts <= candles[-1].ts]
        funding = (
            [
                FundingEvent.from_record(
                    {
                        **item,
                        "rate": D(str(item["rate"])),
                        "mark_price": D(str(item["mark_price"]))
                        if item.get("mark_price") is not None
                        else None,
                    }
                )
                for item in (
                    snapshot["funding_events"]
                    if "funding_events" in snapshot
                    else self.catalog.load_funding(config["funding_dataset_id"])
                )
            ]
            if config.get("funding_dataset_id")
            else None
        )
        tiers = []
        if snapshot.get("margin_tiers"):
            for item in snapshot["margin_tiers"]["tiers"]:
                tiers.append(
                    MarginTier.from_record(
                        {
                            key: D(value)
                            if key
                            in {
                                "min_size",
                                "max_size",
                                "min_contracts",
                                "max_contracts",
                                "imr",
                                "mmr",
                                "max_leverage",
                            }
                            else value
                            for key, value in item.items()
                        }
                    )
                )
        return run_research_plan(
            candles,
            CATALOG_BARS[snapshot["trade"]["bar"]],
            instrument,
            research,
            mode=config["mode"],
            options=config["options"],
            mark_candles=marks,
            funding_events=funding,
            margin_tiers=tiers,
            provenance={
                "trade": snapshot["trade"],
                "mark": snapshot.get("mark"),
                "funding": snapshot.get("funding"),
                "tiers": snapshot.get("margin_tiers") or {},
            },
        )

    async def snapshots_for(self, source, extra=()):
        symbols = set(extra) | {row["inst_id"] for row in self.book.positions(source)}
        output = {}
        for symbol in symbols:
            try:
                snapshot = await self.catalog.get_market_snapshot(symbol, source)
                self.snapshots[(source, symbol)] = snapshot
                self.market_errors.pop((source, symbol), None)
                output[symbol] = snapshot
            except Exception as exc:
                self.market_errors[(source, symbol)] = {"error": str(exc)[:300], "at": now_ms()}
                if (source, symbol) in self.snapshots:
                    output[symbol] = self.snapshots[(source, symbol)]
        return output

    async def sync_funding(self, source, symbol, snapshots, *, periodic=False):
        position = next((p for p in self.book.positions(source) if p["inst_id"] == symbol), None)
        if not position or not symbol.endswith("-SWAP"):
            return
        snapshot = snapshots.get(symbol)
        if not snapshot:
            raise PlatformError(
                "market_unavailable", "A market snapshot is required to reconcile funding.", 409
            )
        end = int(snapshot["ts"])
        if end <= position["funding_cursor"]:
            return
        check_key = (source, symbol)
        expected = json.loads(position["metadata"]).get("expected_funding_time")
        if (
            periodic
            and (not expected or int(expected) > end)
            and now_ms() - self.funding_checks.get(check_key, 0) < 30000
        ):
            return
        events = await self.catalog.funding_history(symbol, position["funding_cursor"] + 1, end + 1, source)
        await self.offload(self.book.settle_funding, source, symbol, events, snapshots)
        metadata = json.loads(position["metadata"])
        expected = metadata.get("expected_funding_time")
        if (
            expected
            and position["funding_cursor"] < int(expected) <= end
            and not any(int(event["ts"]) == int(expected) for event in events)
        ):
            raise PlatformError(
                "funding_pending",
                "An expected funding settlement is not yet present in realized history; position changes are paused.",
                409,
            )
        self.funding_checks[check_key] = now_ms()

    async def submit(self, order, key, actor):
        existing = self.book.existing(order["source"], key, dumps(order))
        if existing:
            return existing
        async with self.locks[(order["source"], order["inst_id"])]:
            snapshots = await self.snapshots_for(order["source"], [order["inst_id"]])
            await self.sync_funding(order["source"], order["inst_id"], snapshots)
            await self.offload(self.book.observe, order["source"], snapshots)
            return await self.offload(self.book.submit, order, key, snapshots, actor)

    def deployments(self, source=None):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM pro_deployments WHERE (? IS NULL OR source=?) ORDER BY created_at DESC",
                (source, source),
            ).fetchall()
        return [{**dict(row), "config": json.loads(row["config"])} for row in rows]

    def deploy(self, config, actor):
        identifier, now = new_id(), now_ms()
        with self.store.write() as conn:
            if self.book.risk(config["source"], conn)["halted"]:
                raise PlatformError("execution_halted", "Resume new risk before starting a strategy.", 409)
            if conn.execute(
                "SELECT 1 FROM pro_positions WHERE source=? AND inst_id=?",
                (config["source"], config["inst_id"]),
            ).fetchone():
                raise PlatformError(
                    "deployment_inventory",
                    "Close the existing position before starting a strategy; inventory adoption is not supported.",
                    409,
                )
            if any(
                json.loads(row[0])["inst_id"] == config["inst_id"]
                for row in conn.execute(
                    "SELECT payload FROM pro_orders WHERE source=? AND status='pending'", (config["source"],)
                )
            ):
                raise PlatformError(
                    "deployment_pending_orders",
                    "Cancel this market's pending orders before starting a strategy.",
                    409,
                )
            if (
                conn.execute("SELECT COUNT(*) FROM pro_deployments WHERE status='running'").fetchone()[0]
                >= 20
            ):
                raise PlatformError(
                    "deployment_limit", "At most twenty strategies may run concurrently.", 429
                )
            try:
                conn.execute(
                    "INSERT INTO pro_deployments(id,source,inst_id,config,status,created_at,updated_at) VALUES(?,?,?,?,'running',?,?)",
                    (identifier, config["source"], config["inst_id"], dumps(config), now, now),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise PlatformError(
                        "strategy_ownership", "A strategy already owns this source and instrument.", 409
                    ) from None
                raise
            self.store.audit(
                conn,
                config["source"],
                "pro.strategy_started",
                "Forward simulation strategy started",
                {"deployment_id": identifier, "actor": actor},
            )
        return next(row for row in self.deployments(config["source"]) if row["id"] == identifier)

    def stop_deployment(self, identifier, actor):
        with self.store.write() as conn:
            row = conn.execute("SELECT * FROM pro_deployments WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("not_found", "Strategy not found.", 404)
            conn.execute(
                "UPDATE pro_deployments SET status='stopped',updated_at=? WHERE id=?", (now_ms(), identifier)
            )
            self.store.audit(
                conn,
                row["source"],
                "pro.strategy_stopped",
                "Strategy stopped; positions retained",
                {"deployment_id": identifier, "actor": actor},
            )
        return next(row for row in self.deployments() if row["id"] == identifier)

    async def evaluate(self, deployment):
        with localcontext(ACCOUNTING_CONTEXT):
            from .pro_research import directional_signal

            config = deployment["config"]
            source, symbol, identifier = config["source"], config["inst_id"], deployment["id"]
            # History transport must never hold the economic market lock. A
            # separate deployment lock still serializes retries of one signal.
            async with self.strategy_locks[identifier]:
                with self.store.read() as conn:
                    active = conn.execute(
                        "SELECT * FROM pro_deployments WHERE id=?", (identifier,)
                    ).fetchone()
                if not active or active["status"] != "running":
                    return
                interval = CATALOG_BARS[config["bar"]]
                end = 1767225600000 if source == "example" else now_ms() // interval * interval
                latest = end - interval
                with self.store.read() as conn:
                    intent = conn.execute(
                        "SELECT * FROM pro_strategy_intents WHERE deployment_id=? AND bar=?",
                        (identifier, latest),
                    ).fetchone()
                if intent and intent["status"] == "completed":
                    return
                if intent is None:
                    if active["last_bar"] is not None and active["last_bar"] >= latest:
                        return
                    job = self.catalog.create_job(
                        symbol, "trade", config["bar"], end - 2000 * interval, end, source
                    )
                    job = await self.catalog.run_job(job["id"])
                    if job["status"] != "completed":
                        raise PlatformError("strategy_data", "Confirmed strategy history is incomplete.", 409)
                    candles = self.catalog.load_candles(job["dataset_id"])
                    if candles[-1].ts != latest:
                        raise PlatformError("strategy_data", "Latest confirmed bar is missing.", 409)
                    strategy = {
                        **config["strategy"],
                        "allocation": D(str(config["allocation"])),
                        "entry": D(str(config["strategy"]["entry"])),
                        "exit": D(str(config["strategy"]["exit"])),
                    }
                    signal = directional_signal(candles, StrategyConfig(**strategy), config["direction"])
                    target = None if signal is None else D(signal) * D(str(config["allocation"]))
                    with self.store.write() as conn:
                        active = conn.execute(
                            "SELECT status FROM pro_deployments WHERE id=?", (identifier,)
                        ).fetchone()
                        if not active or active["status"] != "running":
                            raise PlatformError(
                                "strategy_stopped", "The strategy no longer owns this market.", 409
                            )
                        conn.execute(
                            "INSERT OR IGNORE INTO pro_strategy_intents VALUES(?,?,?,'pending',?)",
                            (identifier, latest, str(target) if target is not None else None, now_ms()),
                        )
                        conn.execute(
                            "UPDATE pro_strategy_intents SET status='superseded',updated_at=? WHERE deployment_id=? AND bar<? AND status='pending'",
                            (now_ms(), identifier, latest),
                        )
                else:
                    target = D(intent["target"]) if intent["target"] is not None else None
                await self.execute_strategy_intent(config, identifier, latest, end, target)

    async def execute_strategy_intent(self, config, identifier, latest, end, target):
        source, symbol = config["source"], config["inst_id"]
        with localcontext(ACCOUNTING_CONTEXT):
            async with self.locks[(source, symbol)]:
                with self.store.read() as conn:
                    active = conn.execute(
                        "SELECT status FROM pro_deployments WHERE id=?", (identifier,)
                    ).fetchone()
                if not active or active["status"] != "running":
                    raise PlatformError("strategy_stopped", "The strategy no longer owns this market.", 409)
                snapshots = await self.snapshots_for(source, [symbol])
                await self.sync_funding(source, symbol, snapshots)
                snapshot = snapshots[symbol]
                self.book.fresh(snapshot)
                if snapshot["ts"] < end:
                    raise PlatformError("quote_before_signal", "A post-close quote is required.", 409)
                account = await self.offload(self.book.observe, source, snapshots)
                position = next((row for row in account["positions"] if row["inst_id"] == symbol), None)
                quantity = D(position["quantity"]) if position else D(0)
                close_key, open_key = (
                    f"strategy:{identifier}:close:{latest}",
                    f"strategy:{identifier}:open:{latest}",
                )
                actor = f"strategy:{identifier}"
                # Each phase has durable command identity. If the process dies after
                # a close, the persisted intent resumes the opening phase; a stop is
                # checked again inside each fill transaction.
                if target is not None and quantity and (target == 0 or target * quantity < 0):
                    close = {
                        "source": source,
                        "inst_id": symbol,
                        "side": "sell" if quantity > 0 else "buy",
                        "quantity": str(abs(quantity)),
                        "leverage": config["leverage"],
                        "reduce_only": True,
                        "margin_mode": "isolated",
                        "order_type": "market",
                    }
                    await self.offload(self.book.submit, close, close_key, snapshots, actor)
                    account = await self.offload(self.book.account, source, snapshots)
                    quantity = D(0)
                if target and quantity == 0:
                    # Recover a committed opening before sizing from its changed
                    # available cash; this prevents payload drift on crash retry.
                    with self.store.read() as conn:
                        committed = conn.execute(
                            "SELECT 1 FROM pro_orders WHERE source=? AND key=?", (source, open_key)
                        ).fetchone()
                    if not committed:
                        with localcontext(ACCOUNTING_CONTEXT):
                            meta, policy = snapshot["instrument"], self.book.risk(source)
                            leverage = D(str(config["leverage"]))
                            price = number(snapshot["ask"] if target > 0 else snapshot["bid"])
                            price *= (
                                1 + number(policy["slippage_bps"]) / 10000
                                if target > 0
                                else 1 - number(policy["slippage_bps"]) / 10000
                            )
                            tick, lot = number(meta["tick_size"]), number(meta["lot_size"])
                            price = (price / tick).to_integral_value(
                                rounding="ROUND_CEILING" if target > 0 else "ROUND_FLOOR"
                            ) * tick
                            denominator = (
                                price * base_size(meta) * (1 / leverage + number(policy["fee_bps"]) / 10000)
                            )
                            size = (
                                number(account["available_cash"]) * abs(target) / denominator / lot
                            ).to_integral_value(rounding="ROUND_FLOOR") * lot
                        if size >= number(meta["min_size"]):
                            order = {
                                "source": source,
                                "inst_id": symbol,
                                "side": "buy" if target > 0 else "sell",
                                "quantity": str(size),
                                "leverage": config["leverage"],
                                "reduce_only": False,
                                "margin_mode": "isolated",
                                "order_type": "market",
                            }
                            await self.offload(self.book.submit, order, open_key, snapshots, actor)
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE pro_strategy_intents SET status='completed',updated_at=? WHERE deployment_id=? AND bar=?",
                        (now_ms(), identifier, latest),
                    )
                    conn.execute(
                        "UPDATE pro_deployments SET last_bar=?,last_error=NULL,updated_at=? WHERE id=? AND status='running'",
                        (latest, now_ms(), identifier),
                    )

    async def execution_loop(self):
        with localcontext(ACCOUNTING_CONTEXT):
            while True:
                for source in ("okx", "example"):
                    symbols = (
                        {p["inst_id"] for p in self.book.positions(source)}
                        | {row["inst_id"] for row in self.deployments(source) if row["status"] == "running"}
                        | {
                            json.loads(row["body"])["inst_id"]
                            for row in self.book.pending()
                            if row["source"] == source
                        }
                    )
                    if not symbols:
                        continue
                    for symbol in symbols:
                        try:
                            async with self.locks[(source, symbol)]:
                                snapshots = await self.snapshots_for(source, [symbol])
                                await self.sync_funding(source, symbol, snapshots, periodic=True)
                                account = await self.offload(self.book.observe, source, snapshots)
                                for position in account["positions"]:
                                    if (
                                        position["inst_id"] != symbol
                                        or position["inst_type"] != "SWAP"
                                        or position["unrealized_pnl"] is None
                                    ):
                                        continue
                                    policy = self.book.risk(source)
                                    mark_notional = number(position["market_value"])
                                    if (
                                        number(position["margin"]) + number(position["unrealized_pnl"])
                                        <= number(position["maintenance_margin"])
                                        + mark_notional
                                        * (number(policy["fee_bps"]) + number(policy["liquidation_fee_bps"]))
                                        / 10000
                                    ):
                                        order = {
                                            "source": source,
                                            "inst_id": symbol,
                                            "side": "sell" if number(position["quantity"]) > 0 else "buy",
                                            "quantity": str(abs(number(position["quantity"]))),
                                            "leverage": position["leverage"],
                                            "reduce_only": True,
                                            "order_type": "market",
                                            "margin_mode": "isolated",
                                        }
                                        await self.offload(
                                            self.book.submit,
                                            order,
                                            f"liquidation:{symbol}:{snapshots[symbol]['ts']}",
                                            snapshots,
                                            "risk-engine",
                                            liquidation=True,
                                        )
                                for row in self.book.pending():
                                    order = json.loads(row["payload"])
                                    if order["source"] != source or order["inst_id"] != symbol:
                                        continue
                                    bid, ask, mark = self.book.fresh(snapshots[symbol])
                                    trigger = number(
                                        order.get("limit_price")
                                        if order["order_type"] == "limit"
                                        else order.get("stop_price")
                                    )
                                    matched = (
                                        (ask <= trigger if order["side"] == "buy" else bid >= trigger)
                                        if order["order_type"] == "limit"
                                        else (mark >= trigger if order["side"] == "buy" else mark <= trigger)
                                    )
                                    if matched:
                                        try:
                                            await self.offload(
                                                self.book.submit,
                                                order,
                                                row["key"],
                                                snapshots,
                                                "pending-order",
                                                pending_id=row["id"],
                                            )
                                        except PlatformError as exc:
                                            self.market_errors[(source, symbol)] = {
                                                "error": exc.message,
                                                "at": now_ms(),
                                                "order_id": row["id"],
                                            }
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            self.market_errors[(source, symbol)] = {"error": str(exc)[:300], "at": now_ms()}
                await asyncio.sleep(5)

    async def strategy_loop(self):
        # Bounded concurrency isolates a slow history request from other
        # deployments and from quote/risk/order polling. Children are owned and
        # drained here so restore/shutdown cannot leave economic tasks behind.
        slots = asyncio.Semaphore(4)
        pending, scheduled = {}, {}

        async def run(deployment):
            async with slots:
                try:
                    async with asyncio.timeout(60):
                        await self.evaluate(deployment)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    message = (
                        "Strategy evaluation exceeded 60 seconds."
                        if isinstance(exc, TimeoutError)
                        else str(exc)
                    )
                    with self.store.write() as conn:
                        conn.execute(
                            "UPDATE pro_deployments SET last_error=?,updated_at=? WHERE id=? AND status='running'",
                            (message[:500], now_ms(), deployment["id"]),
                        )

        try:
            while True:
                for identifier, task in list(pending.items()):
                    if task.done():
                        if not task.cancelled():
                            await task
                        del pending[identifier]
                running = {row["id"]: row for row in self.deployments() if row["status"] == "running"}
                for identifier, task in pending.items():
                    if identifier not in running:
                        task.cancel()
                scheduled = {key: value for key, value in scheduled.items() if key in running}
                for identifier, deployment in running.items():
                    if identifier not in pending and now_ms() - scheduled.get(identifier, 0) >= 20000:
                        pending[identifier] = asyncio.create_task(run(deployment))
                        scheduled[identifier] = now_ms()
                await asyncio.sleep(1)
        finally:
            for task in pending.values():
                task.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)

    def workers_healthy(self):
        return len(self.tasks) == 5 and all(not task.done() for task in self.tasks)

    async def backup_loop(self):
        while True:
            backups = self.backups.list()
            if not backups or now_ms() - backups[0]["created_at"] >= 86400000:
                try:
                    await self.offload(self.backups.create, "scheduled_daily")
                except Exception:
                    logger.exception("Scheduled verified backup failed")
            await asyncio.sleep(60)

    def operations(self):
        feeds = self.catalog.health()["items"]
        for (source, symbol), snapshot in self.snapshots.items():
            if not any(item["source"] == source and item["inst_id"] == symbol for item in feeds):
                feeds.append(
                    {
                        "source": source,
                        "inst_id": symbol,
                        "transport": "rest" if source == "okx" else "example",
                        "last_success": snapshot.get("observed_at"),
                        "exchange_ts": snapshot["ts"],
                        "age_ms": max(0, now_ms() - snapshot["ts"]) if source == "okx" else None,
                        "status": "fresh"
                        if source == "example" or now_ms() - snapshot["ts"] < 15000
                        else "stale",
                        **self.market_errors.get((source, symbol), {}),
                    }
                )
        for (source, symbol), failure in self.market_errors.items():
            if not any(item["source"] == source and item["inst_id"] == symbol for item in feeds):
                feeds.append({"source": source, "inst_id": symbol, "status": "unavailable", **failure})
        jobs = self.catalog.list_jobs()
        backups = self.backups.list()
        storage = self.backups.storage()
        checks = {
            "database": "ok",
            "workers": "ok" if not self.settings.worker_enabled or self.workers_healthy() else "failed",
            "disk": "ok" if storage["disk_free_bytes"] > 100000000 else "low",
            "backup": "ok" if backups and now_ms() - backups[0]["created_at"] < 90000000 else "overdue",
            "execution": "degraded" if self.market_errors else "ok",
        }
        return {
            "health": {
                "status": "ok" if all(value == "ok" for value in checks.values()) else "degraded",
                "checks": checks,
            },
            "feeds": feeds,
            "jobs": jobs + self.runs(),
            "checkpoints": [
                {key: job.get(key) for key in ("id", "cursor", "rows", "pages", "status")} for job in jobs
            ],
            "backups": backups,
            "storage": storage,
            "metrics": self.metrics.snapshot(),
            "execution": {
                "mode": "local-paper",
                "strategies_running": sum(row["status"] == "running" for row in self.deployments()),
                "positions_open": len(self.book.positions()),
            },
        }
