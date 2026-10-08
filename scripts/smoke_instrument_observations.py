"""Read-only public integration; retain counts/hashes, never distribute raw venue rows."""

import asyncio
import hashlib
import json
import platform
import tempfile
from collections import Counter
from pathlib import Path

from tidebench.catalog import CatalogService
from tidebench.instrument_observations import digest
from tidebench.market import MarketService
from tidebench.store import Store


async def main():
    with tempfile.TemporaryDirectory(prefix="tidebench-instrument-acceptance-") as directory:
        market = MarketService(region="global")
        catalog = CatalogService(Store(Path(directory) / "acceptance.sqlite3"), market)
        observations = []
        try:
            for product in ("SPOT", "SWAP"):
                snapshot = await catalog.observe_instruments(product, "okx")
                exported = catalog.observations.export(snapshot["id"])
                if exported["content_hash"] != digest(exported["observation"]):
                    raise RuntimeError("Persisted observation hash mismatch")
                if len(exported["observation"]["rows"]) != snapshot["row_count"]:
                    raise RuntimeError("Raw source rows were lost")
                symbol = "BTC-USDT" + ("-SWAP" if product == "SWAP" else "")
                meta = await catalog.get_instrument(symbol)
                if meta["observation_id"] != snapshot["id"]:
                    raise RuntimeError("Current metadata did not bind its actual observation")
                before = catalog.observations.universe(
                    "okx", "global", product, snapshot["received_at"] - 1, 3600000
                )
                if before["coverage"] != "unknown":
                    raise RuntimeError("Future observation leaked into an earlier query")
                observations.append(
                    {
                        k: snapshot[k]
                        for k in (
                            "id",
                            "source",
                            "region",
                            "inst_type",
                            "received_at",
                            "received_ns",
                            "row_count",
                            "supported_count",
                            "counts",
                            "payload_hash",
                            "content_hash",
                            "parser_version",
                            "transport",
                        )
                    }
                    | {
                        "persisted_export_hash_verified": True,
                        "raw_rows_retained": True,
                        "btc_metadata_observation_verified": True,
                        "prior_coverage_unknown": True,
                        "unavailable_reasons": dict(
                            Counter(reason for member in snapshot["members"] for reason in member["reasons"])
                        ),
                    }
                )
        finally:
            await market.close()
        root = Path(__file__).resolve().parent.parent
        print(
            json.dumps(
                {
                    "status": "passed",
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "scope": "two public forward REST data-array observations; no private API or execution",
                    "historical_completeness": False,
                    "implementation_sha256": {
                        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in (
                            "backend/tidebench/catalog.py",
                            "backend/tidebench/instrument_observations.py",
                        )
                    },
                    "observations": observations,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
