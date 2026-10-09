"""Collect public OKX books into an isolated local evidence directory.

Usage: uv run python scripts/capture_liquidity.py --output /tmp/tidebench-l2-acceptance
The default twelve rounds take about 5.5 minutes. No exchange credentials or
orders are used. Raw venue rows stay in the output directory; publish only the
summary hashes if redistribution rights have not been established.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

from tidebench.liquidity_evidence import LiquidityCalibrationInput, LiquidityCaptureInput, LiquidityEvidence
from tidebench.market import MarketError, MarketService
from tidebench.platform import PlatformError
from tidebench.store import Store, dumps

MARKETS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New isolated evidence directory, never the workspace database.",
    )
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--region", choices=("global", "us", "eea"), default="global")
    options = parser.parse_args()
    if not 1 <= options.samples <= 512 or not 1 <= options.interval_seconds <= 3600:
        parser.error("Use 1–512 samples and an interval of 1–3600 seconds.")
    if options.output.exists() and any(options.output.iterdir()):
        parser.error("Use a new empty output directory to preserve an unambiguous capture window.")
    return options


async def main(options):
    options.output.mkdir(parents=True, mode=0o700, exist_ok=True)
    market = MarketService(region=options.region)
    evidence = LiquidityEvidence(Store(options.output / "public-liquidity.sqlite3"), market)
    captured = {symbol: [] for symbol in MARKETS}
    errors = []
    root = Path(__file__).resolve().parent.parent
    implementation_files = (
        "backend/tidebench/liquidity_evidence.py",
        "backend/tidebench/market.py",
        "scripts/capture_liquidity.py",
    )
    # Freeze source bytes before observation starts; never relabel an active
    # collection with files edited later in the same working tree.
    initial_fingerprints = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in implementation_files
    }
    (options.output / "implementation-at-start.json").write_text(
        json.dumps(initial_fingerprints, indent=2) + "\n"
    )
    origin = time.monotonic()
    try:
        for index in range(options.samples):
            await asyncio.sleep(max(0, origin + index * options.interval_seconds - time.monotonic()))
            for symbol in MARKETS:
                try:
                    item = await evidence.capture(
                        LiquidityCaptureInput(inst_id=symbol), "public-acceptance-collector"
                    )
                    # Keep exact canonical payloads in local artifacts, not in the public repo.
                    (options.output / (item["id"] + ".capture.json")).write_text(dumps(item) + "\n")
                    evidence.get_capture(item["id"])
                    captured[symbol].append(
                        {
                            k: item[k]
                            for k in (
                                "id",
                                "inst_id",
                                "received_at",
                                "content_hash",
                                "raw_hash",
                                "metadata_hash",
                                "evidence_hash",
                            )
                        }
                        | {
                            "status": item["evidence"]["status"],
                            "book_age_at_capture_ms": item["evidence"].get("book_age_at_capture_ms"),
                        }
                    )
                    print(
                        f"Round {index + 1}/{options.samples} {symbol}: {item['evidence']['status']}",
                        file=sys.stderr,
                        flush=True,
                    )
                except (MarketError, PlatformError) as exc:
                    errors.append(
                        {"round": index + 1, "inst_id": symbol, "code": exc.code, "message": str(exc)}
                    )
                    print(
                        f"Round {index + 1}/{options.samples} {symbol}: {exc.code}",
                        file=sys.stderr,
                        flush=True,
                    )
            (options.output / "capture-index.json").write_text(
                json.dumps({"captures": captured, "errors": errors}, indent=2) + "\n"
            )
        reports = []
        for symbol in MARKETS:
            report = evidence.calibrate(
                LiquidityCalibrationInput(inst_id=symbol, capture_ids=[r["id"] for r in captured[symbol]]),
                "public-acceptance-collector",
            )
            verified = evidence.get_calibration(report["id"])
            (options.output / (report["id"] + ".calibration.json")).write_text(
                json.dumps(verified, indent=2) + "\n"
            )
            reports.append(verified)
    finally:
        await market.close()
    summary = {
        "collection_status": "completed_with_errors" if errors else "completed",
        "source": "OKX public REST",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "requested_rounds": options.samples,
        "interval_seconds": options.interval_seconds,
        "scope": "Current cached organic L2 observations, no authenticated API, no orders, no historical or live execution calibration.",
        "implementation_sha256_at_start": initial_fingerprints,
        "implementation_sha256_at_end": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in implementation_files
        },
        "implementation_changed_during_collection": any(
            hashlib.sha256((root / name).read_bytes()).hexdigest() != initial_fingerprints[name]
            for name in implementation_files
        ),
        "captures": captured,
        "errors": errors,
        "reports": reports,
    }
    (options.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main(arguments()))
