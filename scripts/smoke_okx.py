"""Read-only public integration acceptance; prints evidence, never raw price history."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from tidebench.catalog import CatalogService
from tidebench.market import MarketService
from tidebench.store import Store, now_ms


async def inspect(region, hours):
    with tempfile.TemporaryDirectory(prefix="tidebench-public-smoke-") as directory:
        market = MarketService(region=region)
        catalog = CatalogService(Store(Path(directory) / "acceptance.sqlite3"), market)
        try:
            end = now_ms() // 3600000 * 3600000
            start = end - hours * 3600000
            instrument = await catalog.get_instrument("BTC-USDT-SWAP")
            tiers = await catalog.get_margin_tiers("BTC-USDT-SWAP")
            evidence = {
                "region": region,
                "range": {"start": start, "end": end},
                "instrument": instrument["inst_id"],
                "contract_rules_validated": True,
                "current_margin_tiers": len(tiers["tiers"]),
                "datasets": [],
            }
            for kind in ("trade", "mark", "index", "funding"):
                job = catalog.create_job("BTC-USDT-SWAP", kind, "1H", start, end)
                completed = await catalog.run_job(job["id"])
                assert completed["status"] == "completed", completed
                dataset = catalog.get_dataset(completed["dataset_id"])
                assert catalog.verify_dataset(dataset["id"])
                evidence["datasets"].append(
                    {
                        "kind": kind,
                        "rows": completed["rows"],
                        "complete": dataset["quality"]["complete"],
                        "hash_verified": True,
                    }
                )
            events = await catalog.funding_history("BTC-USDT-SWAP", start, end)
            assert events and all(
                event["mark_ts"] == event["ts"] and event["mark_price"] > 0 for event in events
            )
            evidence["settled_funding_events_with_timestamp_matched_marks"] = len(events)
            evidence["settlement_mark_model"] = "historical_mark_1m_open_approximation"
            return evidence
        finally:
            await market.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=("global", "us", "eea"), default="global")
    parser.add_argument("--hours", type=int, choices=range(8, 73), default=48)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(inspect(args.region, args.hours)), indent=2))
