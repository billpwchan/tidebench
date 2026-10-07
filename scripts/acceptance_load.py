"""Measured loopback acceptance under concurrent research and account reads.

Runs a disposable local server; never contacts OKX or uses exchange credentials.
"""

import asyncio
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


async def measure(base, token):
    async with httpx.AsyncClient(
        base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30
    ) as client:
        for _ in range(150):
            try:
                if (await client.get("/readyz")).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Disposable server did not become ready")
        for index, (instrument, quantity) in enumerate(
            [("BTC-USDT", "0.01"), ("ETH-USDT", "0.1"), ("BTC-USDT-SWAP", "1"), ("ETH-USDT-SWAP", "1")]
        ):
            response = await client.post(
                "/api/v1/pro/execution/orders",
                headers={"Idempotency-Key": f"acceptance-position-{index}"},
                json={
                    "source": "example",
                    "inst_id": instrument,
                    "side": "sell" if instrument.endswith("-SWAP") else "buy",
                    "quantity": quantity,
                    "leverage": 3 if instrument.endswith("-SWAP") else 1,
                },
            )
            response.raise_for_status()
            assert response.json()["status"] == "filled", response.json()
        response = await client.get("/api/v1/pro/execution/account?source=example")
        response.raise_for_status()
        initial_account = response.json()
        assert len(initial_account["positions"]) == 4, initial_account
        end = 1767225600000
        response = await client.post(
            "/api/v1/pro/catalog/jobs",
            json={
                "source": "example",
                "inst_id": "BTC-USDT",
                "kind": "trade",
                "bar": "1H",
                "start": end - 1000 * 3600000,
                "end": end,
            },
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            jobs = (await client.get("/api/v1/pro/catalog/jobs")).json()["items"]
            job = next(item for item in jobs if item["id"] == identifier)
            if job["status"] in {"completed", "failed", "degraded"}:
                assert job["status"] == "completed", job
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Catalog acceptance job exceeded 120 seconds")
        response = await client.post(
            "/api/v1/pro/research/runs",
            json={
                "dataset_id": job["dataset_id"],
                "mode": "grid",
                "options": {
                    "grid": {"fast": [5, 8, 12, 16], "slow": [21, 26, 40, 60, 90, 120]},
                    "max_workers": 2,
                },
            },
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        latencies, statuses = [], []
        gate = asyncio.Semaphore(12)

        async def read(index):
            async with gate:
                started = time.perf_counter()
                analytics = index % 5 == 0
                endpoint = "analytics" if analytics else "account"
                response = await client.get(f"/api/v1/pro/execution/{endpoint}?source=example")
                latencies.append((time.perf_counter() - started) * 1000)
                statuses.append(response.status_code)
                response.raise_for_status()
                body = response.json()
                if analytics:
                    assert body["status"] == "available", body
                    assert len(body["positions"]) == 4, body
                    assert body["scenarios"], body
                else:
                    assert body["cash"] == initial_account["cash"], body
                    assert len(body["positions"]) == 4, body

        started = time.perf_counter()
        await asyncio.gather(*(read(index) for index in range(240)))
        elapsed = time.perf_counter() - started
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            run = (await client.get(f"/api/v1/pro/research/runs/{run_id}")).json()
            if run["status"] in {"completed", "failed"}:
                assert run["status"] == "completed", run
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Research acceptance grid exceeded 120 seconds")
        ordered = sorted(latencies)
        return {
            "transport": "real HTTP over loopback",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "concurrency": 12,
            "requests": len(latencies),
            "status_counts": {str(status): statuses.count(status) for status in set(statuses)},
            "elapsed_seconds": round(elapsed, 3),
            "throughput_requests_second": round(len(latencies) / elapsed, 2),
            "latency_ms": {
                "median": round(statistics.median(ordered), 2),
                "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 2),
                "maximum": round(max(ordered), 2),
            },
            "read_workload": {
                "account_requests": 192,
                "portfolio_analytics_requests": 48,
                "open_positions": 4,
                "assets": ["BTC", "ETH"],
                "products": ["SPOT", "SWAP"],
            },
            "concurrent_work": "24 research grid cases x 1000 synthetic bars; two computation threads",
            "research_status": run["status"],
            "research_experiments": len(run["result"]["experiments"]),
            "interpretation": "Local acceptance workload; not a cloud capacity or availability guarantee.",
        }


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="tidebench-acceptance-") as data:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        import secrets

        token = secrets.token_urlsafe(32)
        environment = {
            **os.environ,
            "TIDEBENCH_PORT": str(port),
            "TIDEBENCH_DATA_DIR": data,
            "TIDEBENCH_API_TOKEN": token,
            "TIDEBENCH_AUTH_ENABLED": "true",
        }
        with open(Path(data) / "server.log", "w+") as logs:
            process = subprocess.Popen(
                [sys.executable, "-m", "tidebench"], cwd=root, env=environment, stdout=logs, stderr=logs
            )
            try:
                print(json.dumps(asyncio.run(measure(f"http://127.0.0.1:{port}", token)), indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
