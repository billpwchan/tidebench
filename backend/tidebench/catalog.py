"""Durable, source-isolated market datasets, resumable imports and REST watches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from typing import Any

from .engine import Candle
from .market import (
    BAR_MS,
    EXAMPLE_ANCHOR,
    SYMBOLS,
    MarketError,
    MarketService,
    _candle,
    _decimal,
    _source,
    _timestamp,
)
from .store import Store, dumps, new_id, now_ms

CATALOG_BARS = {"1m": 60_000, "5m": 300_000, **BAR_MS}
EXAMPLE_WARNING = "Deterministic synthetic market data for the requested UTC range. Not OKX market data. Forward example quotes use a fixed synthetic clock."
KINDS = ("trade", "mark", "index", "funding")
TERMINAL = ("completed", "degraded", "failed", "canceled")
MAX_ROWS = 1_000_000
_SYMBOL = re.compile(r"^[A-Z0-9]{1,24}-USDT(?:-SWAP)?$")
_ENDPOINTS = {
    "trade": "/api/v5/market/history-candles",
    "mark": "/api/v5/market/history-mark-price-candles",
    "index": "/api/v5/market/history-index-candles",
    "funding": "/api/v5/public/funding-rate-history",
}
_PAGE_SIZE = {"trade": 300, "mark": 100, "index": 100, "funding": 400}


def _hash(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def _signed(value: Any, field: str) -> Decimal:
    try:
        if value is None or isinstance(value, bool) or len(str(value)) > 80:
            raise InvalidOperation
        result = Decimal(str(value))
        if not result.is_finite() or abs(result.adjusted()) > 30:
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise MarketError("invalid_upstream_data", f"Invalid signed decimal: {field}.") from None


def _symbol(inst_id: str) -> str:
    if not isinstance(inst_id, str) or not _SYMBOL.fullmatch(inst_id):
        raise MarketError(
            "invalid_instrument", "Only USDT spot and linear USDT perpetual identifiers are accepted."
        )
    return "SWAP" if inst_id.endswith("-SWAP") else "SPOT"


def _record(row: Any, kind: str, inst_id: str) -> dict[str, Any] | None:
    if kind == "funding":
        if not isinstance(row, dict) or row.get("instId", inst_id) != inst_id:
            raise MarketError("invalid_upstream_data", "Invalid funding history record.")
        # Forecasts must never substitute for the settled rate.
        return {
            "ts": _timestamp(row.get("fundingTime")),
            "rate": _signed(row.get("realizedRate"), "realizedRate"),
            "mark_price": None,
            "inst_id": inst_id,
            "formula_type": row.get("formulaType", ""),
            "method": row.get("method", "current_period"),
        }
    if kind in {"mark", "index"}:
        if not isinstance(row, list) or len(row) != 6:
            raise MarketError("invalid_upstream_data", "Invalid price-only candle array.")
        parsed = _candle([*row[:5], "0", "0", "0", row[5]])
    else:
        parsed = _candle(row)
    if not parsed.confirmed:
        return None
    return {
        "ts": parsed.ts,
        "open": parsed.open,
        "high": parsed.high,
        "low": parsed.low,
        "close": parsed.close,
        "volume": parsed.volume,
        "confirmed": True,
    }


def _restore_record(record: dict[str, Any], kind: str) -> dict[str, Any]:
    fields = ("rate", "mark_price") if kind == "funding" else ("open", "high", "low", "close", "volume")
    return record | {key: Decimal(record[key]) if record.get(key) is not None else None for key in fields}


def _example_instrument(inst_id: str) -> dict[str, Any]:
    inst_type = _symbol(inst_id)
    base = inst_id.split("-")[0]
    rules = {
        "BTC": ("0.1", "0.00000001", "0.00001", "0.01"),
        "ETH": ("0.01", "0.00000001", "0.0001", "0.1"),
        "SOL": ("0.001", "0.000001", "0.01", "1"),
        "OKB": ("0.001", "0.000001", "0.01", "1"),
        "DOGE": ("0.00001", "0.000001", "1", "1000"),
    }
    if base not in rules:
        raise MarketError("invalid_instrument", "Example instruments are BTC, ETH, SOL, OKB, and DOGE only.")
    tick, lot, minimum, size = rules[base]
    result = {
        "inst_id": inst_id,
        "inst_type": inst_type,
        "base": base,
        "quote": "USDT",
        "settle_ccy": "USDT",
        "ct_type": "linear" if inst_type == "SWAP" else None,
        "ct_val": Decimal(size) if inst_type == "SWAP" else None,
        "ct_mult": Decimal(1) if inst_type == "SWAP" else None,
        "ct_val_ccy": base if inst_type == "SWAP" else None,
        "contract_size_base": Decimal(size) if inst_type == "SWAP" else None,
        "tick_size": Decimal(tick),
        "lot_size": Decimal("0.01") if inst_type == "SWAP" else Decimal(lot),
        "min_size": Decimal("0.01") if inst_type == "SWAP" else Decimal(minimum),
        "state": "live",
        "quantity_unit": "contracts" if inst_type == "SWAP" else "base",
        "inst_family": f"{base}-USDT",
        "source": "example",
        "synthetic": True,
        "observed_at": EXAMPLE_ANCHOR,
    }
    result["instrument_version"] = _hash({k: v for k, v in result.items() if k != "observed_at"})
    return result


def _example_price(inst_id: str, ts: int, kind: str = "trade") -> Decimal:
    """One absolute-time series across instruments, bar widths and consumers."""
    base = inst_id.split("-")[0]
    scale = Decimal({"BTC": "60000", "ETH": "3000", "SOL": "140", "OKB": "45", "DOGE": "0.12"}[base])
    phase = int(hashlib.sha256(base.encode()).hexdigest()[:8], 16) % 1000 / 100
    hour = ts / 3_600_000
    wave = 1 + 0.045 * math.sin(hour * 0.023 + phase) + 0.012 * math.sin(hour * 0.17 + phase)
    if kind == "index":
        wave *= 0.9998
    return (scale * Decimal(str(wave))).quantize(Decimal("0.00000001"))


def _example_records(
    inst_id: str, kind: str, bar: str, cursor: int, start: int, limit: int
) -> list[dict[str, Any]]:
    """Synthetic range fixtures are timestamp-addressed, never labelled OKX."""
    base = inst_id.split("-")[0]
    scale = Decimal({"BTC": "60000", "ETH": "3000", "SOL": "140", "OKB": "45", "DOGE": "0.12"}[base])
    if kind == "funding":
        # Deliberately variable synthetic intervals: 4h, 4h, 8h, 8h.
        day = (cursor - 1) // 86_400_000
        result = []
        while day * 86_400_000 + 16 * 3_600_000 >= start - 86_400_000 and len(result) < limit:
            for hour in (16, 8, 4, 0):
                ts = day * 86_400_000 + hour * 3_600_000
                if ts < cursor:
                    result.append(
                        {
                            "ts": ts,
                            "rate": Decimal(str(round(0.0001 * math.sin(day + hour), 10))),
                            "mark_price": None,
                            "inst_id": inst_id,
                            "formula_type": "synthetic",
                            "method": "synthetic",
                        }
                    )
            day -= 1
        return result[:limit]
    interval = CATALOG_BARS[bar]
    last = (cursor - 1) // interval
    result = []
    for slot in range(last, max(last - limit, start // interval - 2), -1):
        o, close = (
            _example_price(inst_id, slot * interval, kind),
            _example_price(inst_id, (slot + 1) * interval, kind),
        )
        wick = scale * Decimal("0.002")
        volume = Decimal("100000") / close
        if inst_id.endswith("-SWAP"):
            volume /= _example_instrument(inst_id)["contract_size_base"]
        result.append(
            {
                "ts": slot * interval,
                "open": o,
                "high": max(o, close) + wick,
                "low": min(o, close) - wick,
                "close": close,
                "volume": Decimal(0) if kind != "trade" else volume,
                "confirmed": True,
            }
        )
    return result


class CatalogService:
    def __init__(self, store: Store, market: MarketService):
        self.store, self.market = store, market
        self._instrument_lock = asyncio.Lock()
        self._poll_task: asyncio.Task | None = None
        self._poll_stop = asyncio.Event()
        self._watch: list[tuple[str, str]] = []
        self._poll_interval = 5.0
        self._tasks: set[asyncio.Task] = set()
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS catalog_instruments(
                    source TEXT NOT NULL, region TEXT NOT NULL, inst_id TEXT NOT NULL,
                    version TEXT NOT NULL, observed_at INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(source,region,inst_id,version)
                );
                CREATE TABLE IF NOT EXISTS catalog_jobs(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, region TEXT NOT NULL,
                    inst_id TEXT NOT NULL, kind TEXT NOT NULL, bar TEXT NOT NULL,
                    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, cursor INTEGER NOT NULL,
                    status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
                    rows INTEGER NOT NULL DEFAULT 0, progress REAL NOT NULL DEFAULT 0,
                    lower_bound_reached INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT, dataset_id TEXT, error TEXT, worker_token TEXT,
                    transport TEXT NOT NULL DEFAULT 'rest', provenance TEXT,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS catalog_jobs_pending ON catalog_jobs(status,created_at);
                CREATE TABLE IF NOT EXISTS catalog_records(
                    job_id TEXT NOT NULL REFERENCES catalog_jobs(id), ts INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(job_id,ts)
                );
                CREATE TABLE IF NOT EXISTS catalog_datasets(
                    id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES catalog_jobs(id),
                    source TEXT NOT NULL, inst_id TEXT NOT NULL, kind TEXT NOT NULL,
                    bar TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
                    manifest TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog_watches(
                    source TEXT NOT NULL, region TEXT NOT NULL, inst_id TEXT NOT NULL, body TEXT,
                    last_attempt INTEGER, last_success INTEGER, consecutive_errors INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, PRIMARY KEY(source,region,inst_id)
                );
                CREATE TABLE IF NOT EXISTS catalog_settlement_marks(
                    source TEXT NOT NULL, region TEXT NOT NULL, inst_id TEXT NOT NULL,
                    ts INTEGER NOT NULL, price TEXT NOT NULL, observed_at INTEGER NOT NULL,
                    PRIMARY KEY(source,region,inst_id,ts)
                );
                CREATE TRIGGER IF NOT EXISTS catalog_records_immutable_update
                BEFORE UPDATE ON catalog_records WHEN EXISTS(SELECT 1 FROM catalog_datasets WHERE job_id=OLD.job_id)
                BEGIN SELECT RAISE(ABORT, 'published dataset is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS catalog_records_immutable_delete
                BEFORE DELETE ON catalog_records WHEN EXISTS(SELECT 1 FROM catalog_datasets WHERE job_id=OLD.job_id)
                BEGIN SELECT RAISE(ABORT, 'published dataset is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS catalog_records_immutable_insert
                BEFORE INSERT ON catalog_records WHEN EXISTS(SELECT 1 FROM catalog_datasets WHERE job_id=NEW.job_id)
                BEGIN SELECT RAISE(ABORT, 'published dataset is immutable'); END;
            """)
            # Additive migration for catalogs created before import attribution.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(catalog_jobs)")}
            if "transport" not in columns:
                conn.execute("ALTER TABLE catalog_jobs ADD COLUMN transport TEXT NOT NULL DEFAULT 'rest'")
                conn.execute("UPDATE catalog_jobs SET transport='example' WHERE source='example'")
            if "provenance" not in columns:
                conn.execute("ALTER TABLE catalog_jobs ADD COLUMN provenance TEXT")
            watch_columns = {row[1] for row in conn.execute("PRAGMA table_info(catalog_watches)")}
            if "region" not in watch_columns:
                conn.execute("ALTER TABLE catalog_watches RENAME TO catalog_watches_legacy")
                conn.execute(
                    "CREATE TABLE catalog_watches(source TEXT NOT NULL,region TEXT NOT NULL,inst_id TEXT NOT NULL,body TEXT,last_attempt INTEGER,last_success INTEGER,consecutive_errors INTEGER NOT NULL DEFAULT 0,last_error TEXT,PRIMARY KEY(source,region,inst_id))"
                )
                conn.execute(
                    "INSERT INTO catalog_watches SELECT source,'global',inst_id,body,last_attempt,last_success,consecutive_errors,last_error FROM catalog_watches_legacy"
                )
                conn.execute("DROP TABLE catalog_watches_legacy")

    async def refresh_instruments(self, inst_type: str = "SPOT", source: str = "okx") -> list[dict[str, Any]]:
        _source(source)
        if inst_type not in ("SPOT", "SWAP"):
            raise MarketError("invalid_instrument_type", "Instrument type must be SPOT or SWAP.")
        async with self._instrument_lock:
            if source == "example":
                items = [_example_instrument(s + ("-SWAP" if inst_type == "SWAP" else "")) for s in SYMBOLS]
            else:
                rows = await self.market._get("/api/v5/public/instruments", {"instType": inst_type})
                items = []
                for row in rows:
                    if not isinstance(row, dict):
                        raise MarketError("invalid_upstream_data", "Invalid instrument definition.")
                    symbol = row.get("instId", "")
                    if not _SYMBOL.fullmatch(symbol) or _symbol(symbol) != inst_type:
                        continue
                    if inst_type == "SWAP" and (
                        row.get("ctType") != "linear" or row.get("settleCcy") != "USDT"
                    ):
                        continue
                    family = row.get("instFamily") or row.get("uly") or symbol.removesuffix("-SWAP")
                    base, quote = family.rsplit("-", 1)
                    ct_val = (
                        _decimal(row.get("ctVal"), "ctVal", positive=True) if inst_type == "SWAP" else None
                    )
                    ct_mult = (
                        _decimal(row.get("ctMult"), "ctMult", positive=True) if inst_type == "SWAP" else None
                    )
                    if ct_mult is not None and ct_mult != 1:
                        # The public API defines ctVal as face value. Non-unit
                        # multipliers require a venue-specific sizing review.
                        continue
                    item = {
                        "inst_id": symbol,
                        "inst_type": inst_type,
                        "inst_family": family,
                        "base": base,
                        "quote": quote,
                        "settle_ccy": row.get("settleCcy") or quote,
                        "ct_type": row.get("ctType") or None,
                        "ct_val": ct_val,
                        "ct_mult": ct_mult,
                        "ct_val_ccy": row.get("ctValCcy") or None,
                        "contract_size_base": ct_val * ct_mult if ct_val is not None else None,
                        "tick_size": _decimal(row.get("tickSz"), "tickSz", positive=True),
                        "lot_size": _decimal(row.get("lotSz"), "lotSz", positive=True),
                        "min_size": _decimal(row.get("minSz"), "minSz", positive=True),
                        "state": row.get("state", "unknown"),
                        "quantity_unit": "contracts" if inst_type == "SWAP" else "base",
                        "source": source,
                        "synthetic": False,
                        "observed_at": now_ms(),
                        "list_time": int(row["listTime"]) if row.get("listTime") else None,
                    }
                    if inst_type == "SWAP" and item["ct_val_ccy"] != base:
                        raise MarketError(
                            "unsupported_contract",
                            "Linear contract face-value currency does not match base currency.",
                        )
                    item["instrument_version"] = _hash({k: v for k, v in item.items() if k != "observed_at"})
                    items.append(item)
                if not items:
                    raise MarketError(
                        "no_market_data", "The selected OKX region returned no supported instruments."
                    )
            with self.store.write() as conn:
                for item in items:
                    conn.execute(
                        "INSERT INTO catalog_instruments VALUES(?,?,?,?,?,?) ON CONFLICT(source,region,inst_id,version) DO UPDATE SET observed_at=excluded.observed_at",
                        (
                            source,
                            self.market.region,
                            item["inst_id"],
                            item["instrument_version"],
                            item["observed_at"],
                            dumps(item),
                        ),
                    )
            return items

    async def get_instrument(self, inst_id: str, source: str = "okx") -> dict[str, Any]:
        _source(source)
        inst_type = _symbol(inst_id)
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT body,observed_at FROM catalog_instruments WHERE source=? AND region=? AND inst_id=? ORDER BY observed_at DESC LIMIT 1",
                (source, self.market.region, inst_id),
            ).fetchone()
        if row and (source == "example" or now_ms() - row["observed_at"] < 3_600_000):
            return self._instrument_decimals(json.loads(row["body"]))
        items = await self.refresh_instruments(inst_type, source)
        for item in items:
            if item["inst_id"] == inst_id:
                return item
        raise MarketError(
            "instrument_unavailable",
            "Instrument is unavailable in the configured region or has unsupported contract sizing.",
        )

    @staticmethod
    def _instrument_decimals(item: dict[str, Any]) -> dict[str, Any]:
        for field in ("ct_val", "ct_mult", "contract_size_base", "tick_size", "lot_size", "min_size"):
            if item.get(field) is not None:
                item[field] = Decimal(item[field])
        return item

    def _validate_job_request(
        self, inst_id: str, kind: str, bar: str, start: int, end: int, source: str = "okx"
    ) -> None:
        _source(source)
        inst_type = _symbol(inst_id)
        if kind not in KINDS or (kind in ("funding", "mark") and inst_type != "SWAP"):
            raise MarketError(
                "invalid_dataset_kind", "Mark and funding datasets require a USDT perpetual contract."
            )
        if bar not in CATALOG_BARS:
            raise MarketError("invalid_bar", "Unsupported catalog bar.")
        if (
            any(isinstance(x, bool) or not isinstance(x, int) for x in (start, end))
            or not 1_577_836_800_000 <= start < end <= 4_102_444_800_000
        ):
            raise MarketError("invalid_range", "Use a nonempty UTC range [start,end) between 2020 and 2100.")
        interval = CATALOG_BARS[bar]
        if kind != "funding" and (start % interval or end % interval):
            raise MarketError("unaligned_range", "Range boundaries must align to the selected UTC bar.")
        if kind != "funding" and (end - start) // interval > MAX_ROWS:
            raise MarketError(
                "dataset_too_large",
                f"One dataset supports at most {MAX_ROWS} bars. Split the requested range.",
            )
        if source == "okx" and end > now_ms():
            raise MarketError("future_range", "A real-data range cannot end in the future.")

    def create_job(
        self, inst_id: str, kind: str, bar: str, start: int, end: int, source: str = "okx"
    ) -> dict[str, Any]:
        self._validate_job_request(inst_id, kind, bar, start, end, source)
        job_id, ts = new_id(), now_ms()
        with self.store.write() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM catalog_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= 20:
                raise MarketError("catalog_queue_full", "At most 20 historical downloads may be active.")
            conn.execute(
                "INSERT INTO catalog_jobs(id,source,region,inst_id,kind,bar,start_ms,end_ms,cursor,status,created_at,updated_at,transport) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (
                    job_id,
                    source,
                    self.market.region,
                    inst_id,
                    kind,
                    bar,
                    start,
                    end,
                    end,
                    ts,
                    ts,
                    "example" if source == "example" else "rest",
                ),
            )
        return self.get_job(job_id)

    @staticmethod
    def _job(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["start"], result["end"] = result.pop("start_ms"), result.pop("end_ms")
        result["metadata"] = json.loads(result["metadata"]) if result["metadata"] else None
        result["provenance"] = json.loads(result["provenance"]) if result.get("provenance") else None
        result.pop("worker_token", None)
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM catalog_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise MarketError("job_not_found", "Catalog job was not found.")
        return self._job(row)

    def list_jobs(self, source: str | None = None) -> list[dict[str, Any]]:
        if source is not None:
            _source(source)
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM catalog_jobs WHERE (? IS NULL OR source=?) ORDER BY created_at DESC LIMIT 100",
                (source, source),
            ).fetchall()
        return [self._job(row) for row in rows]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self.store.write() as conn:
            conn.execute(
                "UPDATE catalog_jobs SET status='canceled',worker_token=NULL,updated_at=? WHERE id=? AND status IN ('queued','running')",
                (now_ms(), job_id),
            )
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self.store.write() as conn:
            conn.execute(
                "UPDATE catalog_jobs SET status='queued',error=NULL,worker_token=NULL,updated_at=? WHERE id=? AND status='failed'",
                (now_ms(), job_id),
            )
        return self.get_job(job_id)

    async def import_dataset(
        self,
        inst_id: str,
        kind: str,
        bar: str,
        start: int,
        end: int,
        source: str,
        records: list[dict[str, Any]],
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish attributed JSON records; never disguise an import as an API download.

        Funding completeness is an explicit provider assertion, not inferred from
        a regular schedule. HTTP authorization and upload byte limits are owned
        by the application; this boundary also limits rows and provenance size.
        """
        self._validate_job_request(inst_id, kind, bar, start, end, source)
        if not isinstance(records, list) or len(records) > 250_000:
            raise MarketError("invalid_import", "Import at most 250000 JSON records at a time.")
        if (
            not isinstance(provenance, dict)
            or not isinstance(provenance.get("provider"), str)
            or not provenance["provider"].strip()
        ):
            raise MarketError("invalid_provenance", "An import requires a nonempty provider attribution.")
        try:
            attribution = json.loads(dumps(provenance))
        except (ValueError, TypeError, RecursionError):
            raise MarketError("invalid_provenance", "Provenance must be JSON serializable.") from None
        if len(dumps(attribution).encode()) > 16_384 or len(attribution["provider"]) > 256:
            raise MarketError("invalid_provenance", "Import provenance is too large.")
        if kind == "funding" and attribution.get("rate_kind") != "realized":
            raise MarketError("invalid_provenance", "Funding imports must declare rate_kind='realized'.")
        normalized: dict[int, dict[str, Any]] = {}
        for position, row in enumerate(records):
            if position and position % 5000 == 0:
                await asyncio.sleep(0)
            if not isinstance(row, dict) or row.get("inst_id", inst_id) != inst_id:
                raise MarketError("invalid_import", "An import record has an inconsistent instrument.")
            ts = _timestamp(row.get("ts"))
            if not start <= ts < end:
                raise MarketError("invalid_import", "Import timestamps must lie inside [start,end).")
            if kind == "funding":
                record = {
                    "ts": ts,
                    "inst_id": inst_id,
                    "rate": _signed(row.get("rate"), "rate"),
                    "mark_price": _decimal(row["mark_price"], "mark_price", positive=True)
                    if row.get("mark_price") is not None
                    else None,
                    "formula_type": str(row.get("formula_type", "user_import"))[:128],
                    "method": str(row.get("method", "user_import"))[:128],
                }
                if record["mark_price"] is not None:
                    if row.get("mark_ts") != ts or not row.get("mark_price_source"):
                        raise MarketError(
                            "invalid_import",
                            "An imported settlement mark needs matching mark_ts and attribution.",
                        )
                    record.update(mark_ts=ts, mark_price_source=str(row["mark_price_source"])[:256])
            else:
                if bar not in CATALOG_BARS or ts % CATALOG_BARS[bar]:
                    raise MarketError("invalid_import", "Imported candles must align to their UTC bar.")
                if row.get("confirmed") is not True:
                    raise MarketError("invalid_import", "Imported candles must explicitly be confirmed=true.")
                parsed = _candle(
                    [
                        ts,
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("volume", 0),
                        0,
                        0,
                        "1",
                    ]
                )
                if kind in ("mark", "index") and parsed.volume != 0:
                    raise MarketError("invalid_import", "Price-only candles cannot contain traded volume.")
                record = {
                    "ts": ts,
                    "open": parsed.open,
                    "high": parsed.high,
                    "low": parsed.low,
                    "close": parsed.close,
                    "volume": parsed.volume,
                    "confirmed": True,
                }
            if ts in normalized and dumps(normalized[ts]) != dumps(record):
                raise MarketError("conflicting_history", "Duplicate import timestamps disagree.")
            normalized[ts] = record
        instrument = await self.get_instrument(inst_id, source)
        job_id, token, created_at = new_id(), new_id(), now_ms()
        coverage = attribution.get("coverage")
        declared_complete = (
            isinstance(coverage, dict)
            and coverage.get("complete") is True
            and coverage.get("start") == start
            and coverage.get("end") == end
        )
        with self.store.write() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM catalog_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= 20:
                raise MarketError("catalog_queue_full", "At most 20 historical downloads may be active.")
            conn.execute(
                "INSERT INTO catalog_jobs(id,source,region,inst_id,kind,bar,start_ms,end_ms,cursor,status,worker_token,metadata,transport,provenance,rows,pages,lower_bound_reached,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'running',?,?,'user_import',?,?,1,?,?,?)",
                (
                    job_id,
                    source,
                    self.market.region,
                    inst_id,
                    kind,
                    bar,
                    start,
                    end,
                    start,
                    token,
                    dumps(instrument),
                    dumps(attribution),
                    len(normalized),
                    int(declared_complete),
                    created_at,
                    created_at,
                ),
            )
            conn.executemany(
                "INSERT INTO catalog_records VALUES(?,?,?)",
                [(job_id, ts, dumps(record)) for ts, record in sorted(normalized.items())],
            )
        result = self._finalize(job_id, token)
        return self.get_dataset(result["dataset_id"])

    def resume_pending(self) -> list[dict[str, Any]]:
        """Call once at exclusive-process startup, before dispatching workers."""
        if self._tasks:
            raise MarketError(
                "catalog_running", "Cannot recover jobs while this service has active downloads."
            )
        with self.store.write() as conn:
            conn.execute(
                "UPDATE catalog_jobs SET status='queued',worker_token=NULL,updated_at=? WHERE status='running' AND region=?",
                (now_ms(), self.market.region),
            )
        return [
            job
            for job in self.list_jobs()
            if job["status"] == "queued" and job["region"] == self.market.region
        ]

    async def run_job(self, job_id: str) -> dict[str, Any]:
        token = new_id()
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        try:
            with self.store.write() as conn:
                claimed = conn.execute(
                    "UPDATE catalog_jobs SET status='running',worker_token=?,updated_at=? WHERE id=? AND status='queued'",
                    (token, now_ms(), job_id),
                ).rowcount
            if not claimed:
                return self.get_job(job_id)
            job = self.get_job(job_id)
            if job["region"] != self.market.region:
                raise MarketError("region_mismatch", "Resume this dataset using its original OKX region.")
            if job["transport"] == "user_import":
                # Import rows, attribution and metadata committed atomically.
                # Recovery only publishes them; it must never contact OKX.
                return self._finalize(job_id, token)
            if job["metadata"] is None:
                instrument = await self.get_instrument(job["inst_id"], job["source"])
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE catalog_jobs SET metadata=? WHERE id=? AND worker_token=? AND status='running'",
                        (dumps(instrument), job_id, token),
                    )
            while True:
                job = self.get_job(job_id)
                if job["status"] != "running":
                    return job
                if job["source"] == "example":
                    records = _example_records(
                        job["inst_id"],
                        job["kind"],
                        job["bar"],
                        job["cursor"],
                        job["start"],
                        _PAGE_SIZE[job["kind"]],
                    )
                    timestamps = [r["ts"] for r in records]
                else:
                    params: dict[str, Any] = {
                        "instId": job["inst_id"].removesuffix("-SWAP")
                        if job["kind"] == "index"
                        else job["inst_id"],
                        "after": str(job["cursor"]),
                        "limit": _PAGE_SIZE[job["kind"]],
                    }
                    if job["kind"] != "funding":
                        params["bar"] = job["bar"]
                    rows = await self.market._get(_ENDPOINTS[job["kind"]], params)
                    timestamps = [
                        _timestamp(row.get("fundingTime"))
                        if job["kind"] == "funding" and isinstance(row, dict)
                        else _timestamp(row[0])
                        if isinstance(row, list) and row
                        else 0
                        for row in rows
                    ]
                    if any(ts <= 0 for ts in timestamps):
                        raise MarketError("invalid_upstream_data", "Historical page has invalid timestamps.")
                    records = [
                        record
                        for row in rows
                        if (record := _record(row, job["kind"], job["inst_id"])) is not None
                    ]
                if timestamps and min(timestamps) >= job["cursor"]:
                    raise MarketError("pagination_stalled", "Historical cursor did not advance.")
                if any(ts > job["cursor"] for ts in timestamps):
                    raise MarketError("invalid_pagination", "Historical page crossed its upper cursor.")
                cursor = min(timestamps) if timestamps else job["cursor"]
                reached = bool(timestamps and cursor <= job["start"])
                with self.store.write() as conn:
                    active = conn.execute(
                        "SELECT status,worker_token FROM catalog_jobs WHERE id=?", (job_id,)
                    ).fetchone()
                    if active["status"] != "running" or active["worker_token"] != token:
                        return self.get_job(job_id)
                    for record in records:
                        if not job["start"] <= record["ts"] < job["end"]:
                            continue
                        body = dumps(record)
                        previous = conn.execute(
                            "SELECT body FROM catalog_records WHERE job_id=? AND ts=?", (job_id, record["ts"])
                        ).fetchone()
                        if previous and previous[0] != body:
                            raise MarketError(
                                "conflicting_history",
                                "Overlapping pages disagree. Retry as a new dataset version.",
                            )
                        conn.execute(
                            "INSERT OR IGNORE INTO catalog_records VALUES(?,?,?)",
                            (job_id, record["ts"], body),
                        )
                    count = conn.execute(
                        "SELECT COUNT(*) FROM catalog_records WHERE job_id=?", (job_id,)
                    ).fetchone()[0]
                    progress = min(
                        0.99, max(0, (job["end"] - max(cursor, job["start"])) / (job["end"] - job["start"]))
                    )
                    conn.execute(
                        "UPDATE catalog_jobs SET cursor=?,pages=pages+1,rows=?,progress=?,lower_bound_reached=?,updated_at=? WHERE id=?",
                        (cursor, count, progress, int(reached), now_ms(), job_id),
                    )
                if reached or not timestamps:
                    return self._finalize(job_id, token)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE catalog_jobs SET status='queued',worker_token=NULL,updated_at=? WHERE id=? AND worker_token=? AND status='running'",
                    (now_ms(), job_id, token),
                )
            raise
        except Exception as exc:
            message = (
                f"{exc.code}: {exc.message}"
                if isinstance(exc, MarketError)
                else "Unexpected catalog failure; inspect server diagnostics."
            )
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE catalog_jobs SET status='failed',error=?,worker_token=NULL,updated_at=? WHERE id=? AND worker_token=? AND status='running'",
                    (message, now_ms(), job_id, token),
                )
            if not isinstance(exc, MarketError):
                raise
            return self.get_job(job_id)
        finally:
            if task:
                self._tasks.discard(task)

    def _finalize(self, job_id: str, token: str) -> dict[str, Any]:
        with self.store.write() as conn:
            raw_job = conn.execute("SELECT * FROM catalog_jobs WHERE id=?", (job_id,)).fetchone()
            job = self._job(raw_job)
            if raw_job["status"] != "running" or raw_job["worker_token"] != token:
                return job
            rows = conn.execute(
                "SELECT ts,body FROM catalog_records WHERE job_id=? ORDER BY ts", (job_id,)
            ).fetchall()
            interval = CATALOG_BARS[job["bar"]]
            expected = None if job["kind"] == "funding" else (job["end"] - job["start"]) // interval
            gaps = []
            if expected is not None:
                previous = job["start"] - interval
                for row in rows:
                    if row["ts"] - previous > interval:
                        gaps.append({"start": previous + interval, "end": row["ts"]})
                    previous = row["ts"]
                if previous + interval < job["end"]:
                    gaps.append({"start": previous + interval, "end": job["end"]})
            aligned = job["kind"] == "funding" or all(row["ts"] % interval == 0 for row in rows)
            complete = (
                bool(rows) and aligned and len(rows) == expected and not gaps
                if expected is not None
                else bool(job["lower_bound_reached"])
            )
            quality = {
                "complete": complete,
                "expected_records": expected,
                "records": len(rows),
                "gaps": gaps,
                "timestamps_aligned": aligned,
                "coverage_start": job["start"] if complete else (rows[0]["ts"] if rows else None),
                "coverage_end": job["end"]
                if complete
                else (rows[-1]["ts"] + (interval if expected is not None else 1) if rows else None),
                "requested_start": job["start"],
                "requested_end": job["end"],
                "coverage_method": "importer_declared"
                if job["transport"] == "user_import"
                else "pagination_crossed_start"
                if job["lower_bound_reached"]
                else "available_history_only",
                "funding_interval_assumption": None,
            }
            metadata = job["metadata"]
            identity = {
                "schema_version": 3,
                "source": job["source"],
                "region": job["region"],
                "inst_id": job["inst_id"],
                "kind": job["kind"],
                "bar": job["bar"],
                "start": job["start"],
                "end": job["end"],
                "instrument_version": metadata["instrument_version"],
                "transport": job["transport"],
                "provenance": job["provenance"],
                "quality": quality,
                "metadata_hash": _hash(
                    {key: value for key, value in metadata.items() if key != "observed_at"}
                ),
            }
            digest = hashlib.sha256(dumps(identity).encode())
            for row in rows:
                digest.update(b"\n" + row["body"].encode())
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM catalog_datasets WHERE source=? AND inst_id=? AND kind=? AND bar=?",
                (job["source"], job["inst_id"], job["kind"], job["bar"]),
            ).fetchone()[0]
            dataset_id, created = new_id(), now_ms()
            manifest = identity | {
                "id": dataset_id,
                "version": version,
                "content_hash": digest.hexdigest(),
                "metadata": metadata,
                "quality": quality,
                "created_at": created,
                "synthetic": job["source"] == "example",
                "volume_unit": "not_applicable"
                if job["kind"] in ("mark", "index", "funding")
                else metadata["quantity_unit"],
                "warning": EXAMPLE_WARNING
                if job["source"] == "example"
                else "User-supplied records: provider attribution and coverage are assertions, not exchange-verified facts."
                if job["transport"] == "user_import"
                else None
                if complete
                else "Requested history is incomplete. Inspect coverage and gaps before research.",
                "funding_mark_price_policy": "Join an observed historical mark at the settlement timestamp; never substitute a future close."
                if job["kind"] == "funding"
                else None,
            }
            conn.execute(
                "INSERT INTO catalog_datasets VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    dataset_id,
                    job_id,
                    job["source"],
                    job["inst_id"],
                    job["kind"],
                    job["bar"],
                    version,
                    digest.hexdigest(),
                    dumps(manifest),
                    created,
                ),
            )
            conn.execute(
                "UPDATE catalog_jobs SET status=?,progress=1,dataset_id=?,worker_token=NULL,updated_at=? WHERE id=?",
                ("completed" if complete else "degraded", dataset_id, created, job_id),
            )
        return self.get_job(job_id)

    def list_datasets(self, source: str | None = None) -> list[dict[str, Any]]:
        if source is not None:
            _source(source)
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT manifest FROM catalog_datasets WHERE (? IS NULL OR source=?) ORDER BY created_at DESC LIMIT 100",
                (source, source),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self.store.read() as conn:
            row = conn.execute("SELECT manifest FROM catalog_datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise MarketError("dataset_not_found", "Dataset was not found.")
        return json.loads(row[0])

    def dataset_records(
        self, dataset_id: str, limit: int = 1000, after: int | None = None
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ROWS:
            raise MarketError("invalid_limit", "Invalid dataset record limit.")
        manifest = self.get_dataset(dataset_id)
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT r.body FROM catalog_records r JOIN catalog_datasets d ON d.job_id=r.job_id WHERE d.id=? AND (? IS NULL OR r.ts>?) ORDER BY r.ts LIMIT ?",
                (dataset_id, after, after, limit),
            ).fetchall()
        return [_restore_record(json.loads(row[0]), manifest["kind"]) for row in rows]

    def load_candles(self, dataset_id: str) -> list[Candle]:
        if self.get_dataset(dataset_id)["kind"] == "funding":
            raise MarketError("wrong_dataset_kind", "Funding events are not candles.")
        self.verify_dataset(dataset_id)
        return [Candle(**record) for record in self.dataset_records(dataset_id, MAX_ROWS)]

    def load_funding(self, dataset_id: str) -> list[dict[str, Any]]:
        if self.get_dataset(dataset_id)["kind"] != "funding":
            raise MarketError("wrong_dataset_kind", "Expected a funding dataset.")
        self.verify_dataset(dataset_id)
        return self.dataset_records(dataset_id, MAX_ROWS)

    def verify_dataset(self, dataset_id: str) -> bool:
        manifest = self.get_dataset(dataset_id)
        fields = (
            "schema_version",
            "source",
            "region",
            "inst_id",
            "kind",
            "bar",
            "start",
            "end",
            "instrument_version",
        )
        if manifest["schema_version"] >= 2:
            fields += ("transport", "provenance")
        if manifest["schema_version"] >= 3:
            fields += ("quality", "metadata_hash")
            metadata_hash = _hash(
                {key: value for key, value in manifest["metadata"].items() if key != "observed_at"}
            )
            if metadata_hash != manifest["metadata_hash"]:
                raise MarketError(
                    "dataset_integrity_error", "Saved instrument metadata hash does not match its manifest."
                )
        digest = hashlib.sha256(dumps({field: manifest[field] for field in fields}).encode())
        count = 0
        with self.store.read() as conn:
            for row in conn.execute(
                "SELECT r.body FROM catalog_records r JOIN catalog_datasets d ON d.job_id=r.job_id WHERE d.id=? ORDER BY r.ts",
                (dataset_id,),
            ):
                digest.update(b"\n" + row[0].encode())
                count += 1
        if digest.hexdigest() != manifest["content_hash"] or count != manifest["quality"]["records"]:
            raise MarketError(
                "dataset_integrity_error", "Saved dataset hash or record count does not match its manifest."
            )
        return True

    async def funding_history(
        self, inst_id: str, start: int, end: int, source: str = "okx"
    ) -> list[dict[str, Any]]:
        _source(source)
        with self.store.read() as conn:
            existing = conn.execute(
                "SELECT id FROM catalog_jobs WHERE source=? AND region=? AND inst_id=? AND kind='funding' AND start_ms=? AND end_ms=? AND status='completed' AND transport!='user_import' ORDER BY created_at DESC LIMIT 1",
                (source, self.market.region, inst_id, start, end),
            ).fetchone()
        if existing:
            result = self.get_job(existing[0])
        else:
            job = self.create_job(inst_id, "funding", "1m", start, end, source)
            result = await self.run_job(job["id"])
        if result["status"] != "completed":
            raise MarketError(
                "incomplete_funding_history",
                result.get("error")
                or "Funding history coverage could not be proven. Inspect the persisted catalog job.",
            )
        events = self.load_funding(result["dataset_id"])
        enriched = []
        for event in events:
            mark = await self._settlement_mark(inst_id, event["ts"], source)
            enriched.append(event | mark | {"funding_dataset_id": result["dataset_id"]})
        return enriched

    async def _settlement_mark(self, inst_id: str, ts: int, source: str) -> dict[str, Any]:
        """Use the open of exactly this confirmed minute, never a nearby/future close."""
        with self.store.read() as conn:
            cached = conn.execute(
                "SELECT price,observed_at FROM catalog_settlement_marks WHERE source=? AND region=? AND inst_id=? AND ts=?",
                (source, self.market.region, inst_id, ts),
            ).fetchone()
        if cached:
            price, observed_at = (
                _decimal(cached["price"], "cached_mark", positive=True),
                cached["observed_at"],
            )
        else:
            if source == "example":
                price, observed_at = _example_price(inst_id, ts, "mark"), EXAMPLE_ANCHOR
            else:
                if ts % 60_000:
                    raise MarketError(
                        "missing_settlement_mark",
                        "Funding timestamp does not align to an observed one-minute mark open.",
                    )
                rows = await self.market._get(
                    _ENDPOINTS["mark"],
                    {"instId": inst_id, "bar": "1m", "after": str(ts + 60_000), "limit": 1},
                )
                parsed = _record(rows[0], "mark", inst_id) if rows else None
                if parsed is None or parsed["ts"] != ts:
                    raise MarketError(
                        "missing_settlement_mark",
                        "No confirmed historical mark candle exists at the exact funding timestamp.",
                    )
                price, observed_at = parsed["open"], now_ms()
            with self.store.write() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO catalog_settlement_marks VALUES(?,?,?,?,?,?)",
                    (source, self.market.region, inst_id, ts, str(price), observed_at),
                )
                saved = conn.execute(
                    "SELECT price,observed_at FROM catalog_settlement_marks WHERE source=? AND region=? AND inst_id=? AND ts=?",
                    (source, self.market.region, inst_id, ts),
                ).fetchone()
                if Decimal(saved["price"]) != price:
                    raise MarketError(
                        "conflicting_history",
                        "Historical settlement mark changed across concurrent observations.",
                    )
                observed_at = saved["observed_at"]
        return {
            "mark_price": price,
            "mark_ts": ts,
            "mark_observed_at": observed_at,
            "mark_price_source": "synthetic_absolute_time_series"
            if source == "example"
            else "historical_mark_1m_open_approximation",
        }

    async def get_margin_tiers(
        self, inst_id: str, source: str = "okx", td_mode: str = "isolated"
    ) -> dict[str, Any]:
        _source(source)
        if _symbol(inst_id) != "SWAP" or td_mode not in ("isolated", "cross"):
            raise MarketError("invalid_margin_request", "Margin tiers require SWAP and isolated/cross mode.")
        await self.get_instrument(inst_id, source)
        if source == "example":
            tiers = [
                {
                    "tier": 1,
                    "min_size": Decimal(0),
                    "max_size": Decimal("1000"),
                    "mmr": Decimal("0.005"),
                    "imr": Decimal("0.1"),
                    "max_leverage": Decimal(10),
                },
                {
                    "tier": 2,
                    "min_size": Decimal("1000"),
                    "max_size": Decimal("10000"),
                    "mmr": Decimal("0.01"),
                    "imr": Decimal("0.2"),
                    "max_leverage": Decimal(5),
                },
            ]
        else:
            rows = await self.market._get(
                "/api/v5/public/position-tiers",
                {"instType": "SWAP", "tdMode": td_mode, "instFamily": inst_id.removesuffix("-SWAP")},
            )
            tiers = []
            for row in rows:
                if not isinstance(row, dict):
                    raise MarketError("invalid_upstream_data", "Invalid margin tier.")
                low, high = (
                    _decimal(row.get("minSz"), "minSz"),
                    _decimal(row.get("maxSz"), "maxSz", positive=True),
                )
                mmr, imr = (
                    _decimal(row.get("mmr"), "mmr", positive=True),
                    _decimal(row.get("imr"), "imr", positive=True),
                )
                if low >= high or not mmr <= imr <= 1:
                    raise MarketError("invalid_upstream_data", "Inconsistent margin tier bounds/rates.")
                tiers.append(
                    {
                        "tier": int(row["tier"]),
                        "min_size": low,
                        "max_size": high,
                        "mmr": mmr,
                        "imr": imr,
                        "max_leverage": _decimal(row.get("maxLever"), "maxLever", positive=True),
                    }
                )
            if not tiers:
                raise MarketError("no_margin_data", "OKX returned no margin tiers.")
            tiers.sort(key=lambda item: item["tier"])
        return {
            "source": source,
            "synthetic": source == "example",
            "inst_id": inst_id,
            "td_mode": td_mode,
            "observed_at": EXAMPLE_ANCHOR if source == "example" else now_ms(),
            "unit": "contracts",
            "historical": False,
            "boundary_policy": "At a shared boundary use the more conservative requirement; the API does not specify inclusivity.",
            "tiers": tiers,
        }

    async def get_derivative_snapshot(self, inst_id: str, source: str = "okx") -> dict[str, Any]:
        if _symbol(inst_id) != "SWAP":
            raise MarketError("invalid_instrument", "A derivative snapshot requires a USDT perpetual.")
        instrument = await self.get_instrument(inst_id, source)
        if source == "example":
            mark = _example_price(inst_id, EXAMPLE_ANCHOR, "mark")
            return {
                "source": source,
                "synthetic": True,
                "inst_id": inst_id,
                "mark_price": mark,
                "mark_ts": EXAMPLE_ANCHOR,
                "index_price": mark * Decimal("0.9998"),
                "index_ts": EXAMPLE_ANCHOR,
                "funding_rate": Decimal("0.0001"),
                "funding_time": EXAMPLE_ANCHOR + 4 * 3_600_000,
                "next_funding_time": EXAMPLE_ANCHOR + 8 * 3_600_000,
                "current_interval_ms": 4 * 3_600_000,
                "funding_ts": EXAMPLE_ANCHOR,
                "settled_funding_rate": Decimal("-0.0001"),
                "settlement_state": "synthetic",
                "formula_type": "synthetic",
                "method": "synthetic",
                "observed_at": EXAMPLE_ANCHOR,
                "transport": "example",
                "instrument": instrument,
                "warning": EXAMPLE_WARNING,
            }
        mark = await self.market._get("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst_id})
        index = await self.market._get("/api/v5/market/index-tickers", {"instId": instrument["inst_family"]})
        funding = await self.market._get("/api/v5/public/funding-rate", {"instId": inst_id})
        if not mark or not index or not funding:
            raise MarketError("no_market_data", "Mark, index or funding data is unavailable.")
        m, i, f = mark[0], index[0], funding[0]
        if (
            not all(isinstance(row, dict) for row in (m, i, f))
            or m.get("instId") != inst_id
            or i.get("instId") != instrument["inst_family"]
            or f.get("instId") != inst_id
        ):
            raise MarketError(
                "invalid_upstream_data", "Derivative snapshot contains an unexpected instrument."
            )
        funding_time, next_time = _timestamp(f.get("fundingTime")), _timestamp(f.get("nextFundingTime"))
        if next_time <= funding_time:
            raise MarketError("invalid_upstream_data", "Funding schedule did not advance.")
        return {
            "source": source,
            "synthetic": False,
            "inst_id": inst_id,
            "mark_price": _decimal(m.get("markPx"), "markPx", positive=True),
            "mark_ts": _timestamp(m.get("ts")),
            "index_price": _decimal(i.get("idxPx"), "idxPx", positive=True),
            "index_ts": _timestamp(i.get("ts")),
            "funding_rate": _signed(f.get("fundingRate"), "fundingRate"),
            "funding_time": funding_time,
            "next_funding_time": next_time,
            "current_interval_ms": next_time - funding_time,
            "funding_ts": _timestamp(f.get("ts")),
            "settled_funding_rate": _signed(f["settFundingRate"], "settFundingRate")
            if f.get("settFundingRate")
            else None,
            "settlement_state": f.get("settState", "unknown"),
            "formula_type": f.get("formulaType", ""),
            "method": f.get("method", ""),
            "observed_at": now_ms(),
            "transport": "rest",
            "instrument": instrument,
            "warning": None,
        }

    async def get_market_snapshot(self, inst_id: str, source: str = "okx") -> dict[str, Any]:
        """Unified execution inputs; all prices are USDT per base asset."""
        instrument = await self.get_instrument(inst_id, source)
        derivative = (
            await self.get_derivative_snapshot(inst_id, source) if _symbol(inst_id) == "SWAP" else None
        )
        if source == "example":
            last = _example_price(inst_id, EXAMPLE_ANCHOR)
            bid, ask, ts = last * Decimal("0.9999"), last * Decimal("1.0001"), EXAMPLE_ANCHOR
        else:
            rows = await self.market._get("/api/v5/market/ticker", {"instId": inst_id})
            if not rows or rows[0].get("instId") != inst_id:
                raise MarketError("no_market_data", "Requested market ticker is unavailable.")
            row = rows[0]
            last, bid, ask, ts = (
                _decimal(row.get("last"), "last", positive=True),
                _decimal(row.get("bidPx"), "bidPx", positive=True),
                _decimal(row.get("askPx"), "askPx", positive=True),
                _timestamp(row.get("ts")),
            )
        tiers = await self.get_margin_tiers(inst_id, source) if derivative else None
        return (derivative or {}) | {
            "source": source,
            "synthetic": source == "example",
            "inst_id": inst_id,
            "ts": ts,
            "last": last,
            "bid": bid,
            "ask": ask,
            "mark": derivative["mark_price"] if derivative else last,
            "mark_ts": derivative["mark_ts"] if derivative else ts,
            "index": derivative["index_price"] if derivative else None,
            "funding_rate": derivative["funding_rate"] if derivative else None,
            "next_funding_time": derivative["next_funding_time"] if derivative else None,
            "margin_tiers": tiers["tiers"] if tiers else [],
            "margin_tiers_snapshot": tiers,
            "instrument": instrument,
            "observed_at": EXAMPLE_ANCHOR if source == "example" else now_ms(),
            "transport": "example" if source == "example" else "rest",
        }

    async def poll_once(self, inst_id: str, source: str = "okx") -> dict[str, Any]:
        _source(source)
        inst_type = _symbol(inst_id)
        attempt = now_ms()
        with self.store.write() as conn:
            conn.execute(
                "INSERT INTO catalog_watches(source,region,inst_id,last_attempt) VALUES(?,?,?,?) ON CONFLICT(source,region,inst_id) DO UPDATE SET last_attempt=excluded.last_attempt",
                (source, self.market.region, inst_id, attempt),
            )
        try:
            if inst_type == "SWAP":
                result = await self.get_derivative_snapshot(inst_id, source)
            elif source == "example":
                result = await self.get_market_snapshot(inst_id, source)
            else:
                await self.get_instrument(inst_id, source)
                rows = await self.market._get("/api/v5/market/ticker", {"instId": inst_id})
                if not rows or not isinstance(rows[0], dict) or rows[0].get("instId") != inst_id:
                    raise MarketError("no_market_data", "Spot quote is unavailable.")
                row = rows[0]
                result = {
                    "source": source,
                    "inst_id": inst_id,
                    "last": _decimal(row.get("last"), "last", positive=True),
                    "bid": _decimal(row.get("bidPx"), "bidPx", positive=True),
                    "ask": _decimal(row.get("askPx"), "askPx", positive=True),
                    "ts": _timestamp(row.get("ts")),
                    "observed_at": now_ms(),
                    "transport": "rest",
                    "synthetic": False,
                }
            result["request_duration_ms"] = max(0, now_ms() - attempt)
            with self.store.write() as conn:
                previous = conn.execute(
                    "SELECT body FROM catalog_watches WHERE source=? AND region=? AND inst_id=?",
                    (source, self.market.region, inst_id),
                ).fetchone()
                if previous and previous["body"]:
                    prior = json.loads(previous["body"])
                    if any(
                        result[field] < prior[field]
                        for field in ("ts", "mark_ts", "index_ts", "funding_ts")
                        if field in result and field in prior
                    ):
                        raise MarketError("timestamp_regression", "Market snapshot moved backwards.")
                conn.execute(
                    "UPDATE catalog_watches SET body=?,last_success=?,consecutive_errors=0,last_error=NULL WHERE source=? AND region=? AND inst_id=?",
                    (dumps(result), now_ms(), source, self.market.region, inst_id),
                )
            return result
        except MarketError as exc:
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE catalog_watches SET consecutive_errors=consecutive_errors+1,last_error=? WHERE source=? AND region=? AND inst_id=?",
                    (f"{exc.code}: {exc.message}", source, self.market.region, inst_id),
                )
            raise

    async def start_polling(
        self, symbols: list[str], source: str = "okx", interval_seconds: float = 5
    ) -> None:
        _source(source)
        if not symbols or len(symbols) > 20 or not 1 <= interval_seconds <= 300:
            raise MarketError("invalid_watch", "Watch 1–20 instruments at a 1–300 second interval.")
        for symbol in symbols:
            _symbol(symbol)
        await self.stop_polling()
        self._watch = [(source, symbol) for symbol in dict.fromkeys(symbols)]
        self._poll_interval = float(interval_seconds)
        self._poll_stop.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="catalog-rest-watch")

    async def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            for source, symbol in self._watch:
                if self._poll_stop.is_set():
                    return
                with suppress(MarketError):
                    await self.poll_once(symbol, source)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._poll_stop.wait(), self._poll_interval)

    async def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_task:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

    def health(self, source: str | None = None) -> dict[str, Any]:
        if source is not None:
            _source(source)
        ts = now_ms()
        polling = self._poll_task is not None and not self._poll_task.done()
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM catalog_watches WHERE region=? AND (? IS NULL OR source=?) ORDER BY source,inst_id",
                (self.market.region, source, source),
            ).fetchall()
        items = []
        for row in rows:
            result = dict(row)
            body = json.loads(result.pop("body")) if row["body"] else None
            exchange_ts = (
                min(body.get("mark_ts", ts), body.get("index_ts", ts), body.get("funding_ts", ts))
                if body and "mark_ts" in body
                else body.get("ts")
                if body
                else None
            )
            age = ts - exchange_ts if exchange_ts is not None else None
            budgets = (
                {"mark_ts": 30_000, "index_ts": 30_000, "funding_ts": 180_000}
                if body and "mark_price" in body
                else {"ts": 30_000}
            )
            ages = {field: ts - body[field] for field in budgets if body and body.get(field) is not None}
            stale_fields = [
                field
                for field, budget in budgets.items()
                if field not in ages or not -5000 <= ages[field] <= budget
            ]
            result.update(
                {
                    "snapshot": body,
                    "exchange_ts": exchange_ts,
                    "age_ms": age,
                    "field_ages_ms": ages,
                    "freshness_budgets_ms": budgets,
                    "stale_fields": stale_fields,
                    "transport": "example" if row["source"] == "example" else "rest",
                    "watch_active": polling and (row["source"], row["inst_id"]) in self._watch,
                    "state": "synthetic"
                    if row["source"] == "example" and body
                    else "unavailable"
                    if body is None
                    else "degraded"
                    if row["consecutive_errors"]
                    else "stale"
                    if stale_fields
                    else "healthy",
                }
            )
            items.append(result)
        return {
            "transport": "rest",
            "polling": polling,
            "interval_seconds": self._poll_interval,
            "observed_at": ts,
            "items": items,
        }
