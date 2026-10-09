"""Immutable settlement-time inventory and unresolved funding obligations.

Protective fills never erase a liability. Inventory intervals use (start, end]
so settlement at a fill timestamp belongs to the pre-fill inventory.
"""

import hashlib
import json
from decimal import Decimal

from .contributions import exact_sum
from .platform import PlatformError
from .store import dumps

D = Decimal


def _hash(body):
    return hashlib.sha256(dumps(body).encode()).hexdigest()


def _read(row):
    try:
        body = json.loads(row["body"])
        if not isinstance(body, dict) or _hash(body) != row["content_hash"]:
            raise ValueError
        quantity = D(body["quantity"])
        if not quantity.is_finite() or not quantity:
            raise ValueError
        if (
            body.get("source") != row["source"]
            or body.get("inst_id") != row["inst_id"]
            or type(body.get("observed_at")) is not int
            or body["observed_at"] < 0
            or not isinstance(body.get("metadata"), dict)
            or body["metadata"].get("inst_type") != "SWAP"
        ):
            raise ValueError
        if "ts" in row.keys() and body.get("settlement_ts") != row["ts"]:
            raise ValueError
        if "start_ts" in row.keys() and not 0 <= row["start_ts"] < row["end_ts"] == body["observed_at"]:
            raise ValueError
        owners = body.get("owners")
        if owners is not None:
            if not isinstance(owners, dict) or any(
                not isinstance(k, str) or not k or not isinstance(v, str) for k, v in owners.items()
            ):
                raise ValueError
            amounts = [D(v) for v in owners.values()]
            if (
                not all(v.is_finite() and v >= 0 for v in amounts)
                or exact_sum(amounts) != quantity.copy_abs()
            ):
                raise ValueError
        return body
    except (KeyError, ValueError, TypeError, ArithmeticError, RecursionError):
        raise PlatformError(
            "funding_inventory_integrity", "Frozen funding inventory failed its integrity check.", 409
        ) from None


class PendingFunding:
    def __init__(self, store, contributions=None):
        self.store, self.contributions = store, contributions
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pro_funding_obligations(source TEXT NOT NULL,inst_id TEXT NOT NULL,ts INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL,payment TEXT,settled_at INTEGER,PRIMARY KEY(source,inst_id,ts));
                CREATE TABLE IF NOT EXISTS pro_funding_inventory(source TEXT NOT NULL,inst_id TEXT NOT NULL,start_ts INTEGER NOT NULL,end_ts INTEGER NOT NULL,reference TEXT PRIMARY KEY,body TEXT NOT NULL,content_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS pro_funding_inventory_time ON pro_funding_inventory(source,inst_id,start_ts,end_ts);
            """)

    def inventory(self, conn, row, at):
        metadata = json.loads(row["metadata"])
        owners, error = None, None
        if self.contributions:
            try:
                self.contributions.before(conn, row["source"])
                owners = {
                    r["owner"]: str(D(r["quantity"]).copy_abs())
                    for r in self.contributions.rows(conn, row["source"])
                    if r["inst_id"] == row["inst_id"] and D(r["quantity"])
                }
            except PlatformError as exc:
                error = exc.message
        return {
            "source": row["source"],
            "inst_id": row["inst_id"],
            "quantity": row["quantity"],
            "metadata": metadata,
            "position_generation": metadata.get("position_generation"),
            "owners": owners,
            "attribution_error": error,
            "observed_at": at,
        }

    def capture(self, conn, row, snapshot):
        if not row or not D(row["quantity"]):
            return
        meta = json.loads(row["metadata"])
        if meta["inst_type"] != "SWAP":
            return
        at = int(snapshot["ts"])
        times = set(meta.get("observed_funding_times", []))
        if meta.get("expected_funding_time") is not None:
            times.add(int(meta["expected_funding_time"]))
        for ts in sorted(int(t) for t in times if row["funding_cursor"] < int(t) <= at):
            if conn.execute(
                "SELECT 1 FROM pro_funding WHERE source=? AND inst_id=? AND ts=?",
                (row["source"], row["inst_id"], ts),
            ).fetchone():
                continue
            if conn.execute(
                "SELECT 1 FROM pro_funding_obligations WHERE source=? AND inst_id=? AND ts=?",
                (row["source"], row["inst_id"], ts),
            ).fetchone():
                continue
            body = self.inventory(conn, row, at) | {"settlement_ts": ts}
            conn.execute(
                "INSERT INTO pro_funding_obligations VALUES(?,?,?,?,?,'pending',NULL,NULL)",
                (row["source"], row["inst_id"], ts, dumps(body), _hash(body)),
            )
            self.store.audit(
                conn,
                row["source"],
                "pro.funding_pending",
                "Funding obligation retained at its original inventory",
                {"inst_id": row["inst_id"], "ts": ts, "quantity": row["quantity"]},
            )

    def freeze_interval(self, conn, row, at, reference):
        if not row or not D(row["quantity"]):
            return
        meta = json.loads(row["metadata"])
        if meta["inst_type"] != "SWAP":
            return
        start = int(meta.get("inventory_effective_at", meta.get("position_opened_at", row["funding_cursor"])))
        if at < start:
            raise PlatformError(
                "inventory_time_regression", "Execution cannot precede retained funding inventory.", 409
            )
        if at == start:
            return
        body = self.inventory(conn, row, at)
        conn.execute(
            "INSERT INTO pro_funding_inventory VALUES(?,?,?,?,?,?,?)",
            (row["source"], row["inst_id"], start, at, reference, dumps(body), _hash(body)),
        )

    def get(self, conn, source, symbol, ts):
        row = conn.execute(
            "SELECT * FROM pro_funding_obligations WHERE source=? AND inst_id=? AND ts=?",
            (source, symbol, ts),
        ).fetchone()
        return dict(row) | {"inventory": _read(row)} if row else None

    def event_inventory(self, conn, source, symbol, ts, position):
        obligation = self.get(conn, source, symbol, ts)
        if obligation:
            return obligation["inventory"]
        rows = conn.execute(
            "SELECT * FROM pro_funding_inventory WHERE source=? AND inst_id=? AND start_ts<? AND end_ts>=? ORDER BY start_ts DESC",
            (source, symbol, ts, ts),
        ).fetchall()
        if len(rows) > 1:
            raise PlatformError("funding_inventory_integrity", "Funding inventory intervals overlap.", 409)
        if rows:
            body = _read(rows[0])
        elif position and D(position["quantity"]):
            meta = json.loads(position["metadata"])
            start = int(meta.get("inventory_effective_at", position["funding_cursor"]))
            if ts <= start:
                return None
            body = self.inventory(conn, position, ts)
        else:
            return None
        body = body | {"settlement_ts": ts}
        conn.execute(
            "INSERT INTO pro_funding_obligations VALUES(?,?,?,?,?,'pending',NULL,NULL)",
            (source, symbol, ts, dumps(body), _hash(body)),
        )
        return body

    def pending(self, source=None, conn=None):
        if conn is None:
            with self.store.read() as connection:
                return self.pending(source, connection)
        rows = conn.execute(
            "SELECT * FROM pro_funding_obligations WHERE status='pending' AND (? IS NULL OR source=?) ORDER BY ts,inst_id",
            (source, source),
        ).fetchall()
        output = []
        for row in rows:
            try:
                body = _read(row)
                output.append(
                    dict(
                        source=row["source"],
                        inst_id=row["inst_id"],
                        ts=row["ts"],
                        quantity=body["quantity"],
                        owners=body.get("owners"),
                        observed_at=body["observed_at"],
                        integrity="verified",
                    )
                )
            except PlatformError:
                output.append(
                    dict(
                        source=row["source"],
                        inst_id=row["inst_id"],
                        ts=row["ts"],
                        quantity=None,
                        owners=None,
                        observed_at=None,
                        integrity="failed",
                    )
                )
        return output
