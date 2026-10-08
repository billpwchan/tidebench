"""Immutable forward observations, never a reconstructed historical universe."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter

from .market import MarketError, _decimal
from .store import dumps, new_id

PARSER_VERSION = 1
SYMBOL = re.compile(r"^[A-Z0-9]{1,24}-USDT(?:-SWAP)?$")
ENDPOINT = "/api/v5/public/instruments"
COLUMNS = "id,source,region,inst_type,received_at,received_ns,content_hash,body"


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def timestamp(value, name):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,16}", str(value)):
        raise MarketError("invalid_upstream_data", f"Invalid {name}.")
    result = int(value)
    if result > 32_503_680_000_000:
        raise MarketError("invalid_upstream_data", f"Invalid {name}.")
    return result or None


def parse_members(rows, inst_type, source, observed_at):
    """One invalid/preopen member cannot remove healthy members or its own evidence."""
    ids = Counter(r.get("instId") for r in rows if isinstance(r, dict) and isinstance(r.get("instId"), str))
    members = []
    for index, raw in enumerate(rows):
        symbol = raw.get("instId") if isinstance(raw, dict) else None
        member = {
            "row": index,
            "inst_id": symbol if isinstance(symbol, str) else None,
            "scope": "unsupported",
            "eligibility": "unknown",
            "reasons": [],
            "metadata": None,
            "state": raw.get("state") if isinstance(raw, dict) else None,
            "list_time": None,
            "expiry_time": None,
        }
        members.append(member)
        if not isinstance(raw, dict) or not isinstance(symbol, str):
            member["reasons"] = ["invalid_row"]
            continue
        if ids[symbol] != 1:
            member["reasons"] = ["duplicate_instrument"]
            continue
        if not SYMBOL.fullmatch(symbol) or ("SWAP" if symbol.endswith("-SWAP") else "SPOT") != inst_type:
            member["reasons"] = ["outside_supported_usdt_scope"]
            continue
        member["scope"] = "supported"
        try:
            member["list_time"] = timestamp(raw.get("listTime"), "listTime")
            member["expiry_time"] = timestamp(raw.get("expTime"), "expTime")
            if raw.get("instType") not in (None, "", inst_type):
                raise MarketError("invalid_upstream_data", "Instrument type conflicts with endpoint.")
            base = symbol.removesuffix("-SWAP").removesuffix("-USDT")
            fields = ["tickSz", "lotSz", "minSz"]
            if inst_type == "SWAP":
                fields += ["ctType", "settleCcy", "ctVal", "ctMult", "ctValCcy"]
            missing = [f for f in fields if raw.get(f) in (None, "")]
            if missing:
                member["reasons"] = ["missing_" + f for f in missing]
                continue
            ct_val = ct_mult = None
            if inst_type == "SWAP":
                if raw.get("instFamily") not in (None, "", base + "-USDT") or raw.get("uly") not in (
                    None,
                    "",
                    base + "-USDT",
                ):
                    raise MarketError(
                        "invalid_upstream_data", "Contract family conflicts with instrument identity."
                    )
                if raw["ctType"] != "linear" or raw["settleCcy"] != "USDT":
                    member["scope"] = "unsupported"
                    member["reasons"] = ["unsupported_settlement_or_contract"]
                    continue
                ct_val = _decimal(raw["ctVal"], "ctVal", positive=True)
                ct_mult = _decimal(raw["ctMult"], "ctMult", positive=True)
                if ct_mult != 1 or raw["ctValCcy"] != base:
                    member["scope"] = "unsupported"
                    member["reasons"] = ["unsupported_contract_units"]
                    continue
            elif raw.get("baseCcy") not in (None, "", base) or raw.get("quoteCcy") not in (None, "", "USDT"):
                raise MarketError("invalid_upstream_data", "Spot asset identity conflicts with identifier.")
            state = raw.get("state", "unknown")
            if not isinstance(state, str):
                raise MarketError("invalid_upstream_data", "Invalid instrument state.")
            item = {
                "inst_id": symbol,
                "inst_type": inst_type,
                "inst_family": base + "-USDT",
                "base": base,
                "quote": "USDT",
                "settle_ccy": "USDT",
                "ct_type": "linear" if inst_type == "SWAP" else None,
                "ct_val": ct_val,
                "ct_mult": ct_mult,
                "ct_val_ccy": base if inst_type == "SWAP" else None,
                "contract_size_base": ct_val * ct_mult if ct_val is not None else None,
                "tick_size": _decimal(raw["tickSz"], "tickSz", positive=True),
                "lot_size": _decimal(raw["lotSz"], "lotSz", positive=True),
                "min_size": _decimal(raw["minSz"], "minSz", positive=True),
                "state": state,
                "quantity_unit": "contracts" if inst_type == "SWAP" else "base",
                "source": source,
                "synthetic": source == "example",
                "observed_at": observed_at,
                "list_time": timestamp(raw.get("listTime"), "listTime"),
                "expiry_time": timestamp(raw.get("expTime"), "expTime"),
            }
            item["instrument_version"] = digest({k: v for k, v in item.items() if k != "observed_at"})
            member["metadata"] = item
            if state != "live":
                member["eligibility"] = (
                    "ineligible" if state in {"preopen", "suspend", "rebase", "test"} else "unknown"
                )
                member["reasons"] = ["state_" + state]
            elif item["list_time"] and item["list_time"] > observed_at:
                member["eligibility"] = "ineligible"
                member["reasons"] = ["before_announced_listing"]
            elif item["expiry_time"] and item["expiry_time"] <= observed_at:
                member["eligibility"] = "ineligible"
                member["reasons"] = ["at_or_after_announced_expiry"]
            else:
                member["eligibility"] = "eligible"
        except (MarketError, ValueError, TypeError) as exc:
            member["reasons"] = [getattr(exc, "code", "invalid_upstream_data")]
    return members


class InstrumentObservations:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS instrument_observations(
                    id TEXT PRIMARY KEY,source TEXT NOT NULL,region TEXT NOT NULL,inst_type TEXT NOT NULL,
                    received_at INTEGER NOT NULL,received_ns INTEGER NOT NULL,content_hash TEXT NOT NULL,body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS instrument_observations_scope
                    ON instrument_observations(source,region,inst_type,received_at DESC,received_ns DESC);
                CREATE TRIGGER IF NOT EXISTS instrument_observations_no_update
                    BEFORE UPDATE ON instrument_observations BEGIN SELECT RAISE(ABORT,'instrument observation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS instrument_observations_no_delete
                    BEFORE DELETE ON instrument_observations BEGIN SELECT RAISE(ABORT,'instrument observation is immutable'); END;
            """)

    @staticmethod
    def checked(row):
        try:
            row = (
                dict(zip(COLUMNS.split(","), row, strict=True))
                if isinstance(row, (tuple, list))
                else dict(row)
            )
            body = json.loads(row["body"])
            valid = (
                isinstance(body, dict)
                and set(body)
                == {
                    "id",
                    "source",
                    "region",
                    "inst_type",
                    "requested_at",
                    "received_at",
                    "received_ns",
                    "endpoint",
                    "transport",
                    "parser_version",
                    "payload_hash",
                    "rows",
                }
                and isinstance(body["id"], str)
                and re.fullmatch(r"[a-f0-9]{32}", body["id"])
                and body["source"] in {"okx", "example"}
                and body["region"] in {"global", "us", "eea"}
                and body["inst_type"] in {"SPOT", "SWAP"}
                and type(body["parser_version"]) is int
                and body["parser_version"] == PARSER_VERSION
                and body["endpoint"] == ENDPOINT
                and body["transport"] == ("example" if body["source"] == "example" else "rest_data_array")
                and type(body["received_at"]) is int
                and type(row["received_at"]) is int
                and type(row["received_ns"]) is int
                and isinstance(body["received_ns"], str)
                and re.fullmatch(r"[0-9]{1,19}", body["received_ns"])
                and 0 < int(body["received_ns"]) < 2**63
                and type(body["requested_at"]) is int
                and 0 < body["requested_at"] <= body["received_at"] == int(body["received_ns"]) // 1_000_000
                and isinstance(body["rows"], list)
                and len(body["rows"]) <= 10000
                and len(row["body"].encode()) <= 8 * 1024 * 1024
                and body["payload_hash"] == digest(body["rows"])
                and row["content_hash"] == digest(body)
                and row["received_ns"] == int(body["received_ns"])
                and all(row[f] == body[f] for f in ("id", "source", "region", "inst_type", "received_at"))
            )
            if not valid:
                raise ValueError
            return body
        except (KeyError, ValueError, TypeError, OverflowError):
            raise MarketError(
                "instrument_evidence_integrity", "Instrument observation failed integrity validation."
            ) from None

    @classmethod
    def insert(cls, conn, body):
        values = {k: body[k] for k in ("id", "source", "region", "inst_type", "received_at", "received_ns")}
        values["received_ns"] = int(body["received_ns"])
        values.update(content_hash=digest(body), body=dumps(body))
        cls.checked(values)
        previous = conn.execute("SELECT * FROM instrument_observations WHERE id=?", (body["id"],)).fetchone()
        if previous:
            if cls.checked(previous) != body:
                raise MarketError(
                    "instrument_evidence_conflict", "Conflicting immutable instrument observations."
                )
            return
        conn.execute(
            "INSERT INTO instrument_observations(" + COLUMNS + ") VALUES(?,?,?,?,?,?,?,?)",
            tuple(values[k] for k in COLUMNS.split(",")),
        )

    def capture(self, rows, source, region, inst_type, requested_at):
        received_ns = time.time_ns()
        if not isinstance(rows, list) or len(rows) > 10000:
            raise MarketError(
                "invalid_upstream_data", "Instrument response exceeds the row budget or is not an array."
            )
        try:
            payload_hash = digest(rows)
        except (ValueError, TypeError, OverflowError):
            raise MarketError(
                "invalid_upstream_data", "Instrument response is not a finite JSON data array."
            ) from None
        body = {
            "id": new_id(),
            "source": source,
            "region": region,
            "inst_type": inst_type,
            "requested_at": requested_at,
            "received_at": received_ns // 1_000_000,
            "received_ns": str(received_ns),
            "endpoint": ENDPOINT,
            "transport": "example" if source == "example" else "rest_data_array",
            "parser_version": PARSER_VERSION,
            "payload_hash": payload_hash,
            "rows": rows,
        }
        if len(dumps(body).encode()) > 8 * 1024 * 1024:
            raise MarketError(
                "instrument_evidence_size", "Instrument response exceeds the bounded observation budget."
            )
        with self.store.write() as conn:
            self.insert(conn, body)
        return self.present(body, include_rows=True)

    @staticmethod
    def present(body, *, include_rows=False):
        members = parse_members(body["rows"], body["inst_type"], body["source"], body["received_at"])
        result = {k: v for k, v in body.items() if k != "rows"}
        result.update(
            content_hash=digest(body),
            row_count=len(members),
            counts=dict(Counter(m["eligibility"] for m in members)),
            supported_count=sum(m["scope"] == "supported" for m in members),
            evidence_scope="forward_full_response",
        )
        if include_rows:
            result.update(rows=body["rows"], members=members)
        return result

    def export(self, identifier):
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM instrument_observations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise MarketError("instrument_observation_not_found", "Instrument observation not found.")
        body = self.checked(row)
        return {"observation": body, "content_hash": digest(body)}

    def get(self, identifier):
        return self.present(self.export(identifier)["observation"], include_rows=True)

    def list(self, source, region, inst_type, limit=100):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM instrument_observations WHERE source=? AND region=? AND inst_type=? ORDER BY received_at DESC,received_ns DESC,id DESC LIMIT ?",
                (source, region, inst_type, limit),
            ).fetchall()
        return [self.present(self.checked(r)) for r in rows]

    def latest(self, source, region, inst_type, as_of):
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM instrument_observations WHERE source=? AND region=? AND inst_type=? AND received_at<=? ORDER BY received_at DESC,received_ns DESC,id DESC LIMIT 1",
                (source, region, inst_type, as_of),
            ).fetchone()
        return self.present(self.checked(row), include_rows=True) if row else None

    def universe(self, source, region, inst_type, as_of, max_age_ms):
        snapshot = self.latest(source, region, inst_type, as_of)
        if snapshot is None:
            return {
                "as_of": as_of,
                "coverage": "unknown",
                "reason": "no_prior_observation",
                "observation": None,
                "members": [],
                "max_age_ms": max_age_ms,
            }
        age = as_of - snapshot["received_at"]
        stale = age > max_age_ms
        members = []
        for m in snapshot["members"]:
            item = dict(m)
            if stale:
                item.update(eligibility="unknown", reasons=["observation_stale", *m["reasons"]])
            elif item["metadata"] and item["eligibility"] == "eligible":
                meta = item["metadata"]
                if meta["expiry_time"] and meta["expiry_time"] <= as_of:
                    item.update(eligibility="ineligible", reasons=["at_or_after_announced_expiry"])
            members.append(item)
        observation = {k: v for k, v in snapshot.items() if k not in {"rows", "members"}}
        return {
            "as_of": as_of,
            "coverage": "unknown"
            if stale
            else "point_observation"
            if age == 0
            else "bounded_carry_forward_assumption",
            "reason": "observation_stale" if stale else None,
            "age_ms": age,
            "max_age_ms": max_age_ms,
            "observation": observation,
            "members": members,
            "absence_policy": "unknown_not_delisted",
            "historical_completeness": False,
        }

    def diff(self, identifier, previous):
        current, old = self.get(identifier), self.get(previous)
        if any(current[k] != old[k] for k in ("source", "region", "inst_type")) or int(
            current["received_ns"]
        ) < int(old["received_ns"]):
            raise MarketError(
                "instrument_observation_scope",
                "Compare chronologically ordered observations in the same scope.",
            )

        def indexed(snapshot):
            result = {}
            for m in snapshot["members"]:
                symbol = m["inst_id"]
                if symbol is not None:
                    result.setdefault(symbol, []).append(snapshot["rows"][m["row"]])
            return result

        a, b = indexed(old), indexed(current)
        changes = []
        for symbol in sorted(a.keys() | b.keys()):
            if a.get(symbol) == b.get(symbol):
                continue
            kind = "first_observed" if symbol not in a else "not_observed" if symbol not in b else "changed"
            before, after = a.get(symbol), b.get(symbol)
            fields = sorted({k for r in (before or []) + (after or []) for k in r})
            changed = [
                k for k in fields if [r.get(k) for r in before or []] != [r.get(k) for r in after or []]
            ]
            changes.append(
                {"inst_id": symbol, "change": kind, "fields": changed, "before": before, "after": after}
            )
        return {
            "observation_id": identifier,
            "previous_id": previous,
            "changes": changes,
            "absence_policy": "unknown_not_delisted",
        }
