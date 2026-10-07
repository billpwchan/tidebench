"""Durable, version-pinned bundles of research history and funding marks.

Catalog downloads remain the only download workers. Packages own their child
jobs atomically and never reset a live download's worker token on reconciliation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from .catalog import CATALOG_BARS, CatalogService, _signed
from .market import MarketError, _decimal
from .store import Store, dumps, new_id, now_ms

ACTIVE = ("queued", "running", "preparing")
MAX_ACTIVE_PACKAGES = 5
MAX_PACKAGE_BARS = 98_000
MAX_FUNDING_EVENTS = 100_000
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class DataPackageError(MarketError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(code, message)
        self.status = status


def _hash(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


class DataPackageService:
    def __init__(self, store: Store, catalog: CatalogService):
        if store.path != catalog.store.path:
            raise ValueError("Packages and catalog must use the same workspace database")
        self.store, self.catalog = store, catalog
        self._tasks: set[asyncio.Task] = set()
        self._last_prepared: str | None = None
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS data_packages(
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
                    source TEXT NOT NULL, region TEXT NOT NULL, inst_id TEXT NOT NULL, bar TEXT NOT NULL,
                    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, include_index INTEGER NOT NULL,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    blockers TEXT NOT NULL DEFAULT '[]', error TEXT, prepare_token TEXT,
                    manifest TEXT, manifest_hash TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS data_packages_pending ON data_packages(status,created_at);
                CREATE TABLE IF NOT EXISTS data_package_components(
                    package_id TEXT NOT NULL REFERENCES data_packages(id), kind TEXT NOT NULL,
                    job_id TEXT UNIQUE REFERENCES catalog_jobs(id), dataset_id TEXT REFERENCES catalog_datasets(id),
                    PRIMARY KEY(package_id,kind)
                );
                CREATE TABLE IF NOT EXISTS data_package_funding_marks(
                    package_id TEXT NOT NULL REFERENCES data_packages(id), ts INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(package_id,ts)
                );
                CREATE TRIGGER IF NOT EXISTS data_package_manifest_immutable
                BEFORE UPDATE ON data_packages WHEN OLD.manifest IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_manifest_no_delete
                BEFORE DELETE ON data_packages WHEN OLD.manifest IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_component_immutable_update
                BEFORE UPDATE ON data_package_components WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=OLD.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_component_immutable_delete
                BEFORE DELETE ON data_package_components WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=OLD.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_component_immutable_insert
                BEFORE INSERT ON data_package_components WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=NEW.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_mark_immutable_update
                BEFORE UPDATE ON data_package_funding_marks WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=OLD.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_mark_immutable_delete
                BEFORE DELETE ON data_package_funding_marks WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=OLD.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS data_package_mark_immutable_insert
                BEFORE INSERT ON data_package_funding_marks WHEN EXISTS(
                    SELECT 1 FROM data_packages WHERE id=NEW.package_id AND manifest IS NOT NULL)
                BEGIN SELECT RAISE(ABORT, 'published data package is immutable'); END;
            """)

    @staticmethod
    def _kinds(inst_id: str, include_index: bool) -> tuple[str, ...]:
        return (("trade", "mark", "funding") if inst_id.endswith("-SWAP") else ("trade",)) + (
            ("index",) if include_index else ()
        )

    def create_package(
        self,
        inst_id: str,
        bar: str,
        start: int,
        end: int,
        source: str = "okx",
        *,
        include_index: bool = False,
        idempotency_key: str | None = None,
        dataset_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not all(isinstance(value, str) for value in (inst_id, bar, source)):
            raise DataPackageError("invalid_package", "Instrument, bar and source must be strings.")
        if not isinstance(include_index, bool):
            raise DataPackageError("invalid_package", "include_index must be a boolean.")
        self.catalog._validate_job_request(inst_id, "trade", bar, start, end, source)
        if not 2 <= (end - start) // CATALOG_BARS[bar] <= MAX_PACKAGE_BARS:
            raise DataPackageError("package_range", "A research package accepts 2–98000 aligned UTC bars.")
        kinds = self._kinds(inst_id, include_index)
        if dataset_ids is None:
            dataset_ids = {}
        if not isinstance(dataset_ids, dict) or any(
            kind not in kinds or not isinstance(identifier, str) or not _KEY.fullmatch(identifier)
            for kind, identifier in dataset_ids.items()
        ):
            raise DataPackageError(
                "invalid_package", "Bind only explicitly selected datasets for package kinds."
            )
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key)
        ):
            raise DataPackageError(
                "invalid_idempotency_key", "Use 1–128 letters, digits, dots, colons, underscores or dashes."
            )
        descriptor = {
            "source": source,
            "region": self.catalog.market.region,
            "inst_id": inst_id,
            "bar": bar,
            "start": start,
            "end": end,
            "include_index": include_index,
            "dataset_ids": dataset_ids,
        }
        fingerprint = _hash(descriptor)
        request_key = "user:" + idempotency_key if idempotency_key is not None else "auto:" + fingerprint
        identifier, timestamp = new_id(), now_ms()
        # Child allocation and links commit together. A crash cannot leave a
        # queued download without its package or accidentally allocate it twice.
        with self.store.write() as conn:
            previous = conn.execute(
                "SELECT id,request_hash FROM data_packages WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous:
                if previous["request_hash"] != fingerprint:
                    raise DataPackageError(
                        "idempotency_conflict", "This request key already describes a different package.", 409
                    )
                identifier = previous["id"]
            else:
                if (
                    conn.execute(
                        "SELECT COUNT(*) FROM data_packages WHERE status IN ('queued','running','preparing')"
                    ).fetchone()[0]
                    >= MAX_ACTIVE_PACKAGES
                ):
                    raise DataPackageError(
                        "package_queue_full", "At most five data packages may be active.", 429
                    )
                missing = len(kinds) - len(dataset_ids)
                if (
                    conn.execute(
                        "SELECT COUNT(*) FROM catalog_jobs WHERE status IN ('queued','running')"
                    ).fetchone()[0]
                    + missing
                    > 20
                ):
                    raise DataPackageError(
                        "catalog_queue_full",
                        "This package would exceed the twenty active download limit.",
                        429,
                    )
                for kind, dataset_id in dataset_ids.items():
                    row = conn.execute(
                        "SELECT manifest FROM catalog_datasets WHERE id=?", (dataset_id,)
                    ).fetchone()
                    if not row:
                        raise DataPackageError(
                            "dataset_not_found", "A selected package dataset does not exist.", 404
                        )
                    selected = json.loads(row[0])
                    if (
                        any(
                            selected.get(key) != descriptor[key]
                            for key in ("source", "region", "inst_id", "bar", "start", "end")
                        )
                        or selected.get("kind") != kind
                    ):
                        raise DataPackageError(
                            "package_dataset_mismatch",
                            "Selected dataset must exactly match source, region, instrument, kind, bar and UTC range.",
                        )
                conn.execute(
                    "INSERT INTO data_packages(id,request_key,request_hash,source,region,inst_id,bar,start_ms,end_ms,include_index,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'queued',?,?)",
                    (
                        identifier,
                        request_key,
                        fingerprint,
                        source,
                        descriptor["region"],
                        inst_id,
                        bar,
                        start,
                        end,
                        int(include_index),
                        timestamp,
                        timestamp,
                    ),
                )
                for kind in kinds:
                    job_id = None
                    if kind not in dataset_ids:
                        job_id = new_id()
                        conn.execute(
                            "INSERT INTO catalog_jobs(id,source,region,inst_id,kind,bar,start_ms,end_ms,cursor,status,created_at,updated_at,transport) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                            (
                                job_id,
                                source,
                                descriptor["region"],
                                inst_id,
                                kind,
                                bar,
                                start,
                                end,
                                end,
                                timestamp,
                                timestamp,
                                "example" if source == "example" else "rest",
                            ),
                        )
                    conn.execute(
                        "INSERT INTO data_package_components VALUES(?,?,?,?)",
                        (identifier, kind, job_id, dataset_ids.get(kind)),
                    )
                self.store.audit(
                    conn,
                    source,
                    "data_package.created",
                    "Research data package queued",
                    {"package_id": identifier, "request_hash": fingerprint},
                )
        return self.reconcile_package(identifier)

    def _row(self, identifier: str) -> dict[str, Any]:
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM data_packages WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise DataPackageError("package_not_found", "Data package was not found.", 404)
        result = dict(row)
        if result["region"] != self.catalog.market.region:
            raise DataPackageError(
                "region_mismatch", "Use the package's original configured OKX region.", 409
            )
        return result

    def get_package(self, identifier: str, *, include_manifest: bool = True) -> dict[str, Any]:
        raw = self._row(identifier)
        with self.store.read() as conn:
            components = conn.execute(
                "SELECT c.kind,c.job_id,COALESCE(c.dataset_id,j.dataset_id) AS dataset_id,j.status,j.progress,j.error,j.rows,j.pages FROM data_package_components c LEFT JOIN catalog_jobs j ON j.id=c.job_id WHERE c.package_id=? ORDER BY c.kind",
                (identifier,),
            ).fetchall()
            captured = conn.execute(
                "SELECT COUNT(*) FROM data_package_funding_marks WHERE package_id=?", (identifier,)
            ).fetchone()[0]
        result = {
            key: raw[key]
            for key in (
                "id",
                "source",
                "region",
                "inst_id",
                "bar",
                "status",
                "progress",
                "error",
                "manifest_hash",
                "created_at",
                "updated_at",
            )
        }
        result.update(
            start=raw["start_ms"],
            end=raw["end_ms"],
            include_index=bool(raw["include_index"]),
            ready=raw["status"] == "ready",
            blockers=json.loads(raw["blockers"]),
        )
        result["components"] = [
            dict(row)
            | {
                "status": row["status"] or "bound",
                "progress": row["progress"] if row["progress"] is not None else 1.0,
            }
            for row in components
        ]
        funding = next((row for row in components if row["kind"] == "funding"), None)
        total = None
        if funding and funding["dataset_id"]:
            total = self.catalog.get_dataset(funding["dataset_id"])["quality"]["records"]
        result["coverage"] = {"start": result["start"], "end": result["end"], "complete": result["ready"]}
        result["funding_marks"] = {
            "captured": captured,
            "total": total if funding else 0,
            "policy": "attributed_import_or_exact_timestamp_historical_1m_open_approximation"
            if funding
            else "not_applicable",
        }
        if include_manifest:
            result["manifest"] = json.loads(raw["manifest"]) if raw["manifest"] else None
        return result

    def list_packages(self, source: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if (
            source not in (None, "okx", "example")
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise DataPackageError(
                "invalid_package_query", "Use a valid source and a list limit from 1 to 100."
            )
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT id FROM data_packages WHERE region=? AND (? IS NULL OR source=?) ORDER BY created_at DESC,id DESC LIMIT ?",
                (self.catalog.market.region, source, source, limit),
            ).fetchall()
        return [self.get_package(row[0], include_manifest=False) for row in rows]

    def _datasets(
        self, package: dict[str, Any], *, verify: bool = True
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        output, blockers = {}, []
        for component in package["components"]:
            kind, identifier = component["kind"], component["dataset_id"]
            if identifier is None:
                continue
            try:
                dataset = self.catalog.get_dataset(identifier)
                if verify:
                    self.catalog.verify_dataset(identifier)
            except MarketError as exc:
                blockers.append({"kind": kind, "code": exc.code, "message": exc.message})
                continue
            if (
                dataset.get("schema_version", 0) < 3
                or dataset.get("kind") != kind
                or any(
                    dataset.get(key) != package[key]
                    for key in ("source", "region", "inst_id", "bar", "start", "end")
                )
            ):
                blockers.append(
                    {
                        "kind": kind,
                        "code": "package_dataset_mismatch",
                        "message": "Dataset identity or hash schema does not match this exact package.",
                    }
                )
            quality = dataset["quality"]
            if (
                not quality.get("complete")
                or quality.get("coverage_start") != package["start"]
                or quality.get("coverage_end") != package["end"]
                or quality.get("gaps")
                or not quality.get("timestamps_aligned")
            ):
                blockers.append(
                    {
                        "kind": kind,
                        "code": "funding_history_incomplete"
                        if kind == "funding"
                        else "dataset_coverage_incomplete",
                        "message": "Funding coverage is unproven; OKX history is retention-limited. Bind an attributed, complete imported version in a new package."
                        if kind == "funding"
                        else "Confirmed, aligned candles must cover the full requested UTC range without gaps.",
                    }
                )
            output[kind] = dataset
        versions = {dataset["metadata_hash"] for dataset in output.values()}
        if len(versions) > 1:
            blockers.append(
                {
                    "kind": "instrument",
                    "code": "instrument_rules_mismatch",
                    "message": "Component datasets captured different instrument rules. Select coherent immutable versions.",
                }
            )
        return output, blockers

    def reconcile_package(self, identifier: str) -> dict[str, Any]:
        package = self.get_package(identifier)
        if package["status"] in ("ready", "canceled", "blocked", "failed"):
            return package
        components = package["components"]
        all_finished = all(c["status"] in ("bound", "completed", "degraded") for c in components)
        datasets, blockers = self._datasets(package, verify=all_finished)
        failed = [c for c in components if c["status"] == "failed"]
        canceled = [c for c in components if c["status"] == "canceled"]
        if failed:
            status = "failed"
            blockers += [
                {
                    "kind": c["kind"],
                    "code": "download_failed",
                    "message": c["error"]
                    or "Historical download failed. Retry resumes its committed cursor.",
                }
                for c in failed
            ]
        elif canceled:
            status = "blocked"
            blockers += [
                {
                    "kind": c["kind"],
                    "code": "download_canceled",
                    "message": "A child download was canceled. Create a new package version.",
                }
                for c in canceled
            ]
        elif blockers and all_finished:
            status = "blocked"
        elif all_finished:
            status = "preparing"
        elif any(c["status"] == "running" or c["progress"] > 0 for c in components):
            status = "running"
        else:
            status = "queued"
        progress = min(0.95, sum(c["progress"] for c in components) / len(components) * 0.9)
        if status == "preparing":
            total = package["funding_marks"]["total"] or 0
            progress = 0.9 + 0.09 * (min(1, package["funding_marks"]["captured"] / total) if total else 1)
        with self.store.write() as conn:
            current = conn.execute(
                "SELECT status,prepare_token FROM data_packages WHERE id=?", (identifier,)
            ).fetchone()
            if current["status"] in ("ready", "canceled") or current["prepare_token"]:
                return self.get_package(identifier)
            for component in components:
                if component["job_id"]:
                    actual = conn.execute(
                        "SELECT status,dataset_id FROM catalog_jobs WHERE id=?", (component["job_id"],)
                    ).fetchone()
                    if (
                        actual["status"] != component["status"]
                        or actual["dataset_id"] != component["dataset_id"]
                    ):
                        return self.get_package(identifier)
            for kind, dataset in datasets.items():
                conn.execute(
                    "UPDATE data_package_components SET dataset_id=? WHERE package_id=? AND kind=?",
                    (dataset["id"], identifier, kind),
                )
            conn.execute(
                "UPDATE data_packages SET status=?,progress=?,blockers=?,error=?,updated_at=? WHERE id=?",
                (
                    status,
                    progress,
                    dumps(blockers),
                    "One or more historical downloads failed." if failed else None,
                    now_ms(),
                    identifier,
                ),
            )
        return self.get_package(identifier)

    def reconcile_pending(self) -> list[dict[str, Any]]:
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT id FROM data_packages WHERE region=? AND status IN ('queued','running','preparing') ORDER BY created_at LIMIT 100",
                (self.catalog.market.region,),
            ).fetchall()
        return [self.reconcile_package(row[0]) for row in rows]

    def cancel_package(self, identifier: str) -> dict[str, Any]:
        self._row(identifier)
        with self.store.write() as conn:
            row = conn.execute("SELECT status FROM data_packages WHERE id=?", (identifier,)).fetchone()
            if row["status"] == "canceled":
                return self.get_package(identifier)
            if row["status"] not in (*ACTIVE, "failed", "blocked"):
                raise DataPackageError("package_state", "A published package cannot be canceled.", 409)
            conn.execute(
                "UPDATE catalog_jobs SET status='canceled',worker_token=NULL,updated_at=? WHERE id IN (SELECT job_id FROM data_package_components WHERE package_id=?) AND status IN ('queued','running')",
                (now_ms(), identifier),
            )
            conn.execute(
                "UPDATE data_packages SET status='canceled',prepare_token=NULL,updated_at=? WHERE id=?",
                (now_ms(), identifier),
            )
            self.store.audit(
                conn,
                self._row(identifier)["source"],
                "data_package.canceled",
                "Data package canceled",
                {"package_id": identifier},
            )
        return self.get_package(identifier)

    def retry_package(self, identifier: str) -> dict[str, Any]:
        self._row(identifier)
        with self.store.write() as conn:
            package = conn.execute(
                "SELECT status,source,prepare_token FROM data_packages WHERE id=?", (identifier,)
            ).fetchone()
            if package["status"] != "failed" or package["prepare_token"]:
                raise DataPackageError(
                    "package_state", "Only a failed, inactive package can be retried.", 409
                )
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM data_packages WHERE status IN ('queued','running','preparing')"
                ).fetchone()[0]
                >= MAX_ACTIVE_PACKAGES
            ):
                raise DataPackageError("package_queue_full", "At most five data packages may be active.", 429)
            children = conn.execute(
                "SELECT j.id,j.status,j.worker_token FROM catalog_jobs j JOIN data_package_components c ON c.job_id=j.id WHERE c.package_id=?",
                (identifier,),
            ).fetchall()
            failures = [child for child in children if child["status"] == "failed"]
            if any(child["worker_token"] is not None for child in failures):
                raise DataPackageError(
                    "package_worker_active",
                    "A failed child still has a worker claim; recover only at exclusive startup.",
                    409,
                )
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM catalog_jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                + len(failures)
                > 20
            ):
                raise DataPackageError(
                    "catalog_queue_full", "Retry would exceed the active download limit.", 429
                )
            for child in failures:
                conn.execute(
                    "UPDATE catalog_jobs SET status='queued',error=NULL,updated_at=? WHERE id=? AND status='failed' AND worker_token IS NULL",
                    (now_ms(), child["id"]),
                )
            conn.execute(
                "UPDATE data_packages SET status='queued',error=NULL,blockers='[]',updated_at=? WHERE id=?",
                (now_ms(), identifier),
            )
            self.store.audit(
                conn,
                package["source"],
                "data_package.retried",
                "Data package resumed existing download cursors",
                {"package_id": identifier},
            )
        return self.reconcile_package(identifier)

    def resume_pending(self) -> list[dict[str, Any]]:
        """Exclusive-process startup only, before either catalog or package workers.

        This releases abandoned package preparation claims, never catalog claims.
        The runtime owns calling catalog.resume_pending() at that same boundary.
        """
        if self._tasks or self.catalog._tasks:
            raise DataPackageError(
                "package_workers_active", "Do not recover packages while workers are active.", 409
            )
        with self.store.write() as conn:
            conn.execute(
                "UPDATE data_packages SET prepare_token=NULL,updated_at=? WHERE status='preparing' AND region=?",
                (now_ms(), self.catalog.market.region),
            )
        return self.reconcile_pending()

    async def prepare_package(self, identifier: str, *, max_observations: int = 100) -> dict[str, Any]:
        if (
            isinstance(max_observations, bool)
            or not isinstance(max_observations, int)
            or not 1 <= max_observations <= 1000
        ):
            raise DataPackageError(
                "invalid_preparation_budget", "Prepare between 1 and 1000 new funding observations per call."
            )
        package = self.reconcile_package(identifier)
        if package["status"] != "preparing":
            return package
        token, task = new_id(), asyncio.current_task()
        with self.store.write() as conn:
            claimed = conn.execute(
                "UPDATE data_packages SET prepare_token=?,updated_at=? WHERE id=? AND status='preparing' AND prepare_token IS NULL",
                (token, now_ms(), identifier),
            ).rowcount
        if not claimed:
            return self.get_package(identifier)
        if task:
            self._tasks.add(task)
        try:
            funding = next((c for c in package["components"] if c["kind"] == "funding"), None)
            if funding:
                events = self.catalog.load_funding(funding["dataset_id"])
                if len(events) > MAX_FUNDING_EVENTS:
                    raise DataPackageError(
                        "package_funding_limit", "Split packages with more than 100000 funding events."
                    )
                captured = 0
                for event in events:
                    with self.store.read() as conn:
                        active = conn.execute(
                            "SELECT status,prepare_token FROM data_packages WHERE id=?", (identifier,)
                        ).fetchone()
                        saved = conn.execute(
                            "SELECT body FROM data_package_funding_marks WHERE package_id=? AND ts=?",
                            (identifier, event["ts"]),
                        ).fetchone()
                    if active["status"] != "preparing" or active["prepare_token"] != token:
                        return self.get_package(identifier)
                    if saved:
                        self._funding_observation(json.loads(saved[0]), event)
                        continue
                    if captured >= max_observations:
                        return self.get_package(identifier)
                    if event.get("mark_price") is None:
                        observed = event | await self.catalog._settlement_mark(
                            package["inst_id"], event["ts"], package["source"]
                        )
                    else:
                        observed = event
                    observation = self._funding_observation(observed, event)
                    with self.store.write() as conn:
                        active = conn.execute(
                            "SELECT status,prepare_token FROM data_packages WHERE id=?", (identifier,)
                        ).fetchone()
                        if active["status"] != "preparing" or active["prepare_token"] != token:
                            return self.get_package(identifier)
                        conn.execute(
                            "INSERT INTO data_package_funding_marks VALUES(?,?,?)",
                            (identifier, event["ts"], dumps(observation)),
                        )
                    captured += 1
                    await asyncio.sleep(0)
            return self._publish(identifier, token)
        except asyncio.CancelledError:
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE data_packages SET prepare_token=NULL,updated_at=? WHERE id=? AND status='preparing' AND prepare_token=?",
                    (now_ms(), identifier, token),
                )
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, MarketError) else "package_preparation_failed"
            message = (
                exc.message
                if isinstance(exc, MarketError)
                else "Unexpected preparation failure; inspect server diagnostics."
            )
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE data_packages SET status='failed',prepare_token=NULL,error=?,blockers=?,updated_at=? WHERE id=? AND status='preparing' AND prepare_token=?",
                    (
                        message,
                        dumps([{"kind": "funding_marks", "code": code, "message": message}]),
                        now_ms(),
                        identifier,
                        token,
                    ),
                )
            if not isinstance(exc, MarketError):
                raise
            return self.get_package(identifier)
        finally:
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE data_packages SET prepare_token=NULL,updated_at=? WHERE id=? AND status='preparing' AND prepare_token=?",
                    (now_ms(), identifier, token),
                )
            if task:
                self._tasks.discard(task)

    @staticmethod
    def _funding_observation(observed: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        if (
            observed.get("ts") != event["ts"]
            or _signed(observed.get("rate"), "realized_rate") != event["rate"]
            or observed.get("mark_ts") != event["ts"]
            or not isinstance(observed.get("mark_price_source"), str)
            or not observed["mark_price_source"].strip()
            or len(observed["mark_price_source"]) > 256
        ):
            raise DataPackageError(
                "invalid_funding_mark",
                "A funding mark must bind the same settlement timestamp, realized rate and explicit attribution.",
            )
        price = _decimal(observed.get("mark_price"), "settlement_mark", positive=True)
        return event | {
            "mark_price": price,
            "mark_ts": event["ts"],
            "mark_price_source": observed["mark_price_source"],
            "mark_observed_at": observed.get("mark_observed_at"),
        }

    def _publish(self, identifier: str, token: str) -> dict[str, Any]:
        package = self.get_package(identifier)
        datasets, blockers = self._datasets(package)
        if blockers or len(datasets) != len(package["components"]):
            raise DataPackageError(
                "package_integrity", "Package dataset verification failed before publication."
            )
        funding_records = (
            self.catalog.load_funding(datasets["funding"]["id"]) if "funding" in datasets else []
        )
        with self.store.write() as conn:
            current = conn.execute(
                "SELECT status,prepare_token FROM data_packages WHERE id=?", (identifier,)
            ).fetchone()
            if current["status"] != "preparing" or current["prepare_token"] != token:
                return self.get_package(identifier)
            events = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT body FROM data_package_funding_marks WHERE package_id=? ORDER BY ts",
                    (identifier,),
                )
            ]
            expected = datasets.get("funding", {}).get("quality", {}).get("records", 0)
            if len(events) != expected:
                raise DataPackageError(
                    "package_funding_marks_incomplete",
                    "Every realized funding event needs a captured historical mark.",
                )
            events = [
                self._funding_observation(observed, event)
                for observed, event in zip(events, funding_records, strict=True)
            ]
            timestamp = now_ms()
            manifest = {
                "schema_version": 1,
                "package_id": identifier,
                "source": package["source"],
                "region": package["region"],
                "inst_id": package["inst_id"],
                "bar": package["bar"],
                "start": package["start"],
                "end": package["end"],
                "synthetic": package["source"] == "example",
                "datasets": datasets,
                "instrument": datasets["trade"]["metadata"],
                "instrument_version": datasets["trade"]["instrument_version"],
                "funding_events": events,
                "funding_events_hash": _hash(events),
                "funding_interval_assumption": None,
                "funding_mark_policy": "Attributed imported marks or exact-timestamp historical 1m open approximations; not account settlement execution prices.",
                "margin_tiers_policy": "Not historical inputs in this package. Research must separately capture and disclose its current-tier scenario.",
                "created_at": timestamp,
            }
            digest = _hash(manifest)
            conn.execute(
                "UPDATE data_packages SET status='ready',progress=1,error=NULL,blockers='[]',prepare_token=NULL,manifest=?,manifest_hash=?,updated_at=? WHERE id=?",
                (dumps(manifest), digest, timestamp, identifier),
            )
            self.store.audit(
                conn,
                package["source"],
                "data_package.ready",
                "Verified immutable research inputs are ready",
                {"package_id": identifier, "manifest_hash": digest},
            )
        return self.get_package(identifier)

    async def advance_pending(self) -> list[dict[str, Any]]:
        packages = self.reconcile_pending()
        preparing = [package for package in packages if package["status"] == "preparing"]
        if preparing:
            previous = next(
                (index for index, package in enumerate(preparing) if package["id"] == self._last_prepared), -1
            )
            package = preparing[(previous + 1) % len(preparing)]
            await self.prepare_package(package["id"], max_observations=20)
            self._last_prepared = package["id"]
            # One bounded batch per tick, rotating among eligible packages,
            # prevents a large history from starving other research inputs.
        return self.list_packages()

    async def run_package(self, identifier: str) -> dict[str, Any]:
        """Optional local/CLI driver; safe beside the existing catalog scheduler."""
        package = self.reconcile_package(identifier)
        if package["status"] in ("ready", "canceled", "blocked", "failed"):
            return package
        for component in package["components"]:
            if self.get_package(identifier)["status"] == "canceled":
                break
            if component["job_id"] and self.catalog.get_job(component["job_id"])["status"] == "queued":
                await self.catalog.run_job(component["job_id"])
        return await self.prepare_package(identifier)

    def get_manifest(self, identifier: str, verify: bool = True) -> dict[str, Any]:
        package = self.get_package(identifier)
        if not package["ready"]:
            raise DataPackageError(
                "package_not_ready",
                "All historical components and funding observations must be verified before research.",
                409,
            )
        manifest = package["manifest"]
        if (
            _hash(manifest) != package["manifest_hash"]
            or _hash(manifest["funding_events"]) != manifest["funding_events_hash"]
        ):
            raise DataPackageError(
                "package_integrity", "Published package manifest does not match its hash.", 409
            )
        if verify:
            datasets, blockers = self._datasets(package)
            if blockers or dumps(datasets) != dumps(manifest["datasets"]):
                raise DataPackageError(
                    "package_integrity",
                    "Published dataset versions no longer match the package manifest.",
                    409,
                )
            with self.store.read() as conn:
                events = [
                    json.loads(row[0])
                    for row in conn.execute(
                        "SELECT body FROM data_package_funding_marks WHERE package_id=? ORDER BY ts",
                        (identifier,),
                    )
                ]
            if dumps(events) != dumps(manifest["funding_events"]):
                raise DataPackageError(
                    "package_integrity",
                    "Published funding observations no longer match the package manifest.",
                    409,
                )
        return manifest

    def research_inputs(self, identifier: str) -> dict[str, Any]:
        manifest = self.get_manifest(identifier)
        result = {
            "dataset_id": manifest["datasets"]["trade"]["id"],
            "start_ts": manifest["start"],
            "end_ts": manifest["end"],
            "source": manifest["source"],
            "package_id": identifier,
            "package_manifest_hash": _hash(manifest),
        }
        for kind in ("mark", "funding", "index"):
            if kind in manifest["datasets"]:
                result[kind + "_dataset_id"] = manifest["datasets"][kind]["id"]
        return result
