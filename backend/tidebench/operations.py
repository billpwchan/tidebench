"""Durable condition incidents. Acknowledging never clears a failing condition."""

import hashlib
import json

from .platform import PlatformError
from .store import dumps, now_ms


class IncidentStore:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS ops_incidents(
                id TEXT PRIMARY KEY,kind TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL,
                first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL,resolved_at INTEGER,
                occurrences INTEGER NOT NULL,details TEXT NOT NULL,ack_actor TEXT,ack_reason TEXT,ack_at INTEGER)""")

    def reconcile(self, conditions):
        timestamp = now_ms()
        active = set()
        with self.store.write() as conn:
            for condition in conditions:
                identifier = hashlib.sha256(
                    dumps([condition["kind"], condition["subject"]]).encode()
                ).hexdigest()
                active.add(identifier)
                previous = conn.execute(
                    "SELECT status FROM ops_incidents WHERE id=?", (identifier,)
                ).fetchone()
                if not previous:
                    conn.execute(
                        "INSERT INTO ops_incidents VALUES(?,?,?,'open',?,?,NULL,1,?,NULL,NULL,NULL)",
                        (
                            identifier,
                            condition["kind"],
                            condition["subject"],
                            timestamp,
                            timestamp,
                            dumps(condition["details"]),
                        ),
                    )
                elif previous["status"] == "resolved":
                    conn.execute(
                        "UPDATE ops_incidents SET status='open',first_seen=?,last_seen=?,resolved_at=NULL,occurrences=occurrences+1,details=?,ack_actor=NULL,ack_reason=NULL,ack_at=NULL WHERE id=?",
                        (timestamp, timestamp, dumps(condition["details"]), identifier),
                    )
                else:
                    conn.execute(
                        "UPDATE ops_incidents SET last_seen=?,details=? WHERE id=?",
                        (timestamp, dumps(condition["details"]), identifier),
                    )
                if not previous or previous["status"] == "resolved":
                    self.store.audit(
                        conn,
                        "system",
                        "ops.incident_opened",
                        "Operational condition requires attention",
                        condition | {"incident_id": identifier},
                    )
            for row in conn.execute("SELECT id FROM ops_incidents WHERE status != 'resolved'").fetchall():
                if row["id"] not in active:
                    conn.execute(
                        "UPDATE ops_incidents SET status='resolved',resolved_at=? WHERE id=?",
                        (timestamp, row["id"]),
                    )
                    self.store.audit(
                        conn,
                        "system",
                        "ops.incident_resolved",
                        "Operational condition recovered",
                        {"incident_id": row["id"]},
                    )

    def list(self, limit=100):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_incidents ORDER BY (status='resolved'),last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) | {"details": json.loads(row["details"])} for row in rows]

    def acknowledge(self, identifier, actor, reason):
        if not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 2000:
            raise PlatformError("incident_reason", "Record a 12–2000 character response note.", 422)
        with self.store.write() as conn:
            row = conn.execute("SELECT * FROM ops_incidents WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("not_found", "Incident not found.", 404)
            if row["status"] == "resolved":
                raise PlatformError("incident_resolved", "This condition has already recovered.", 409)
            conn.execute(
                "UPDATE ops_incidents SET status='acknowledged',ack_actor=?,ack_reason=?,ack_at=? WHERE id=?",
                (actor, reason.strip(), now_ms(), identifier),
            )
            self.store.audit(
                conn,
                "system",
                "ops.incident_acknowledged",
                "Operational incident acknowledged",
                {"incident_id": identifier, "actor": actor, "reason": reason.strip()},
            )
            acknowledged = dict(
                conn.execute("SELECT * FROM ops_incidents WHERE id=?", (identifier,)).fetchone()
            )
        return acknowledged | {"details": json.loads(acknowledged["details"])}
