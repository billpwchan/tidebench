"""Content-addressed compressed research results in the backed-up SQLite file."""

import gzip
import hashlib
import json
import zlib

from .platform import PlatformError
from .store import now_ms

MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


class ResearchArtifacts:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS research_artifacts(content_hash TEXT PRIMARY KEY,codec TEXT NOT NULL,raw_bytes INTEGER NOT NULL,payload BLOB NOT NULL,created_at INTEGER NOT NULL)"
            )

    def put(self, conn, payload):
        raw = payload.encode()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise PlatformError(
                "research_artifact_budget",
                "Research result exceeds 128 MiB; split the window or candidate grid.",
                422,
            )
        identity = hashlib.sha256(raw).hexdigest()
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        conn.execute(
            "INSERT OR IGNORE INTO research_artifacts VALUES(?,'gzip',?,?,?)",
            (identity, len(raw), compressed, now_ms()),
        )
        return {"artifact_hash": identity, "codec": "gzip", "raw_bytes": len(raw)}

    def resolve(self, value):
        if not isinstance(value, dict) or "artifact_hash" not in value:
            return value
        if set(value) != {"artifact_hash", "codec", "raw_bytes"} or value.get("codec") != "gzip":
            raise PlatformError("run_artifact_integrity", "Research artifact pointer is invalid.", 409)
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT * FROM research_artifacts WHERE content_hash=?", (value["artifact_hash"],)
            ).fetchone()
        if (
            not row
            or row["codec"] != "gzip"
            or row["raw_bytes"] != value["raw_bytes"]
            or not 0 < row["raw_bytes"] <= MAX_ARTIFACT_BYTES
        ):
            raise PlatformError(
                "run_artifact_integrity", "Research artifact is missing or has invalid metadata.", 409
            )
        try:
            decoder = zlib.decompressobj(wbits=31)
            raw = decoder.decompress(row["payload"], MAX_ARTIFACT_BYTES + 1)
            if (
                not decoder.eof
                or decoder.unused_data
                or len(raw) != row["raw_bytes"]
                or hashlib.sha256(raw).hexdigest() != row["content_hash"]
            ):
                raise ValueError()
            return json.loads(raw)
        except (ValueError, zlib.error, UnicodeDecodeError):
            raise PlatformError(
                "run_artifact_integrity",
                "Research artifact failed decompression or its content check.",
                409,
            ) from None


def project_result(plan, selected=""):
    """Retain all comparable metrics and only one candidate's financial arrays."""
    if not plan:
        return plan
    import copy

    # Avoid a deep copy of every potentially large table before discarding it.
    output = {
        key: value
        for key, value in plan.items()
        if key not in {"result", "experiments", "folds", "shared_inputs"}
    }
    available = ["single"] if plan.get("result") else [item["id"] for item in plan.get("experiments", [])]
    if plan.get("folds"):
        available = [
            key
            for fold in plan["folds"]
            for key in [
                fold["id"] + ":test",
                *[fold["id"] + ":" + item["id"] for item in fold.get("training_experiments", [])],
            ]
        ]
    selected = selected if selected in available else available[0] if available else ""

    def result(value, key):
        snapshot = value.get("input_snapshot", {})
        metadata = {
            k: v for k, v in snapshot.items() if k not in {"trade_candles", "mark_candles", "funding_events"}
        }
        keep = (
            set(value)
            if key == selected
            else {"engine_version", "metrics", "assumptions", "quality", "provenance", "input_hash"}
        )
        return {k: v for k, v in value.items() if k in keep and k != "input_snapshot"} | {
            "input_snapshot": metadata
        }

    if plan.get("result"):
        output["result"] = result(plan["result"], "single")
    if "experiments" in plan:
        output["experiments"] = [
            {**item, "result": result(item["result"], item["id"])} for item in plan["experiments"]
        ]
    if "folds" in plan:
        output["folds"] = [
            {
                **fold,
                "test_result": result(fold["test_result"], fold["id"] + ":test"),
                "training_experiments": [
                    {**item, "result": result(item["result"], fold["id"] + ":" + item["id"])}
                    for item in fold.get("training_experiments", [])
                ],
            }
            for fold in plan["folds"]
        ]
    output["result_projection"] = {
        "selected": selected,
        "scope": "all candidate metrics; selected candidate detail only; full immutable artifact available in export",
    }
    return copy.copy(output)
