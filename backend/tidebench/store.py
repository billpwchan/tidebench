"""Single-workspace SQLite storage. Each trading command is one BEGIN IMMEDIATE transaction."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return encode(asdict(value))
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def new_id() -> str:
    return uuid.uuid4().hex


class QueueFullError(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._process_lock = None
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.rollback()
            conn.close()

    @contextmanager
    def write(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts(
                    source TEXT PRIMARY KEY, initial_cash TEXT NOT NULL, cash TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0', fees_paid TEXT NOT NULL DEFAULT '0',
                    day_key TEXT NOT NULL DEFAULT '', day_equity TEXT NOT NULL DEFAULT '10000'
                );
                CREATE TABLE IF NOT EXISTS positions(
                    source TEXT NOT NULL REFERENCES accounts(source), inst_id TEXT NOT NULL,
                    quantity TEXT NOT NULL, cost_basis TEXT NOT NULL, PRIMARY KEY(source,inst_id)
                );
                CREATE TABLE IF NOT EXISTS risk(
                    source TEXT PRIMARY KEY REFERENCES accounts(source), kill_switch INTEGER NOT NULL DEFAULT 0,
                    max_order_notional TEXT NOT NULL DEFAULT '2500', max_position_pct REAL NOT NULL DEFAULT 50,
                    max_daily_loss_pct REAL NOT NULL DEFAULT 5, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES accounts(source),
                    idempotency_key TEXT NOT NULL, payload TEXT NOT NULL, body TEXT NOT NULL,
                    created_at INTEGER NOT NULL, UNIQUE(source,idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ts INTEGER NOT NULL,
                    kind TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_source_ts ON audit(source,ts DESC);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
                    config TEXT NOT NULL, snapshot TEXT, manifest TEXT, result TEXT, error TEXT,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_status ON runs(status,created_at);
                CREATE TABLE IF NOT EXISTS deployments(
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, inst_id TEXT NOT NULL, bar TEXT NOT NULL,
                    strategy TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL, last_bar INTEGER, last_error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_strategy_per_market
                    ON deployments(source,inst_id) WHERE status='running';
            """)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version VALUES(1)")
            elif row[0] != 1:
                raise RuntimeError("Unsupported database schema. Back up your data before upgrading.")
            for source in ("okx", "example"):
                conn.execute(
                    "INSERT OR IGNORE INTO accounts(source,initial_cash,cash) VALUES(?, '10000', '10000')",
                    (source,),
                )
                conn.execute("INSERT OR IGNORE INTO risk(source,updated_at) VALUES(?,?)", (source, now_ms()))

    def acquire_process_lock(self):
        import fcntl

        handle = open(self.path.with_suffix(".process.lockfile"), "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError(
                "This database already has an active Tidebench process. Use one worker."
            ) from None
        self._process_lock = handle

    def release_process_lock(self):
        if self._process_lock:
            self._process_lock.close()
            self._process_lock = None

    @staticmethod
    def audit(conn, source: str, kind: str, summary: str, details=None):
        conn.execute(
            "INSERT INTO audit(source,ts,kind,summary,details) VALUES(?,?,?,?,?)",
            (source, now_ms(), kind, summary, dumps(details or {})),
        )

    def events(self, source, limit=50):
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM audit WHERE source=? ORDER BY id DESC LIMIT ?", (source, limit)
            ).fetchall()
        return [dict(row) | {"details": json.loads(row["details"])} for row in rows]

    def risk(self, source):
        with self.read() as conn:
            row = conn.execute("SELECT * FROM risk WHERE source=?", (source,)).fetchone()
        return dict(row) | {"kill_switch": bool(row["kill_switch"])}

    def orders(self, source):
        with self.read() as conn:
            rows = conn.execute(
                "SELECT body FROM orders WHERE source=? ORDER BY created_at DESC,rowid DESC LIMIT 100",
                (source,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def existing_order(self, source, key, payload):
        with self.read() as conn:
            row = conn.execute(
                "SELECT payload,body FROM orders WHERE source=? AND idempotency_key=?", (source, key)
            ).fetchone()
        if row:
            if row["payload"] != payload:
                return "conflict", None
            return "found", json.loads(row["body"])
        return "missing", None

    @staticmethod
    def run_row(row):
        return {
            k: json.loads(row[k]) if row[k] is not None else None for k in ("config", "manifest", "result")
        } | {k: row[k] for k in ("id", "status", "created_at", "updated_at", "error")}

    def runs(self, source):
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE source=? ORDER BY created_at DESC LIMIT 50", (source,)
            ).fetchall()
        return [self.run_row(row) for row in rows]

    def run(self, run_id, include_snapshot=False):
        with self.read() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        item = self.run_row(row)
        if include_snapshot:
            item["snapshot"] = json.loads(row["snapshot"]) if row["snapshot"] else None
        return item

    def create_run(self, config, snapshot=None, manifest=None):
        run_id, timestamp = new_id(), now_ms()
        with self.write() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if pending >= 10:
                raise QueueFullError("The research queue is full. Wait for a job to finish.")
            conn.execute(
                "INSERT INTO runs(id,source,status,config,snapshot,manifest,created_at,updated_at) "
                "VALUES(?,?,'queued',?,?,?,?,?)",
                (
                    run_id,
                    config["source"],
                    dumps(config),
                    dumps(snapshot) if snapshot else None,
                    dumps(manifest) if manifest else None,
                    timestamp,
                    timestamp,
                ),
            )
            self.audit(conn, config["source"], "research.queued", "Backtest queued", {"run_id": run_id})
        return self.run(run_id)

    @staticmethod
    def deployment_row(row):
        return dict(row) | {"strategy": json.loads(row["strategy"])}

    def deployments(self, source=None, running_only=False):
        where, args = [], []
        if source:
            where.append("source=?")
            args.append(source)
        if running_only:
            where.append("status='running'")
        query = "SELECT * FROM deployments"
        if where:
            query += " WHERE " + " AND ".join(where)
        with self.read() as conn:
            rows = conn.execute(query + " ORDER BY created_at DESC LIMIT 100", args).fetchall()
        return [self.deployment_row(row) for row in rows]
