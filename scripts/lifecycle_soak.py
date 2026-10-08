"""Elapsed-time, offline HTTP lifecycle audit against an immutable Git revision.

Uses a disposable workspace and loopback-only subprocess, never the user's
preview or account. Synthetic market time advances independently of monotonic
wall time. This is bounded lifecycle evidence, not an uptime or capacity SLA.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import platform
import secrets
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import httpx

ANCHOR = 1767225600000
HOUR = 3600000
PREFIX = "/api/v1"


def save_json(path, body):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(body, indent=2, allow_nan=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def distribution(values):
    rows = sorted(values)
    if not rows:
        return {"count": 0}
    return {
        "count": len(rows),
        "min_ms": round(rows[0], 3),
        "median_ms": round(statistics.median(rows), 3),
        "p95_ms": round(rows[min(len(rows) - 1, int(0.95 * len(rows)))], 3),
        "p99_ms": round(rows[min(len(rows) - 1, int(0.99 * len(rows)))], 3),
        "max_ms": round(rows[-1], 3),
    }


def child(args):
    # The archive is immutable and has no .env, account keys or user database.
    sys.path.insert(0, str(Path(args.code) / "backend"))
    import uvicorn
    from tidebench.config import Settings
    from tidebench.main import create_app
    from tidebench.market import MarketService
    from tidebench.provenance import research_identity

    root = Path(args.data)
    attempts = root / "network-attempts.jsonl"

    def blocked(kind, target):
        with attempts.open("a") as stream:
            stream.write(json.dumps({"kind": kind, "target": str(target)}) + "\n")
        raise RuntimeError("External network forbidden by lifecycle audit")

    def reject(request):
        blocked("httpx_mock_transport", request.url.host)

    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def local_only(original):
        def wrapped(self, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
                blocked("socket_connect", address[0])
            return original(self, address)

        return wrapped

    socket.socket.connect = local_only(connect)
    socket.socket.connect_ex = local_only(connect_ex)
    settings = Settings(
        data_dir=root,
        api_token=os.environ["TIDEBENCH_API_TOKEN"],
        auth_enabled=False,
        worker_enabled=True,
        allowed_hosts="127.0.0.1,localhost",
        build_sha=args.revision,
        research_timeout_seconds=60,
        _env_file=None,
    )
    save_json(root / "runtime-identity.json", research_identity())
    market = MarketService(client=httpx.AsyncClient(transport=httpx.MockTransport(reject)))
    app = create_app(settings, market)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def audited_lifespan(application):
        async with original_lifespan(application):
            yield
        runtime = application.state.professional
        save_json(
            root / "shutdown-completed.json",
            {
                "revision": args.revision,
                "inflight_drained": not runtime.inflight,
                "supervisors_stopped": not runtime.tasks,
                "process_lease_released": application.state.store._process_lock is None,
            },
        )

    app.router.lifespan_context = audited_lifespan
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning", proxy_headers=False
    )


class Audit:
    def __init__(self, args, root, code, revision):
        self.args, self.root, self.code, self.revision = args, root, code, revision
        self.process = self.log = None
        self.client = None
        self.token = secrets.token_urlsafe(40)
        self.port = 0
        self.latencies = defaultdict(list)
        self.statuses = Counter()
        self.errors, self.samples, self.jobs, self.restarts = [], [], [], []
        self.identity = None
        self.groups, self.configs = [], []
        self.elapsed_start = None
        self.reads = 0
        self.owner_checks = Counter()
        self.request_failures = 0
        self.clock_steps = 0
        self.logs = []
        self.process_number = 0

    def progress(self, phase):
        body = {
            "phase": phase,
            "revision": self.revision,
            "elapsed_seconds": round(time.monotonic() - self.elapsed_start, 3) if self.elapsed_start else 0,
            "target_seconds": self.args.duration,
            "clock_steps": self.clock_steps,
            "http_samples": sum(map(len, self.latencies.values())),
            "errors": len(self.errors),
            "research_jobs": len(self.jobs),
            "restarts": len(self.restarts),
        }
        if self.args.progress:
            save_json(self.args.progress, body)
        print(json.dumps(body), flush=True)

    async def request(self, method, path, body=None, *, probe=False):
        started = time.monotonic()
        try:
            response = await self.client.request(method, path, json=body)
            if not probe:
                self.latencies[f"{method} {path.split('?')[0]}"].append((time.monotonic() - started) * 1000)
                self.statuses[str(response.status_code)] += 1
            if not response.is_success:
                if probe:
                    return None
                self.request_failures += 1
                detail = response.text[:300].replace(self.token, "[redacted]")
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {detail}")
            return response.json()
        except httpx.HTTPError as exc:
            if probe:
                return None
            self.request_failures += 1
            raise RuntimeError(f"{method} {path}: {type(exc).__name__}") from exc

    async def wait(self, predicate, seconds, description):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = await predicate()
            if result:
                return result
            if self.process.returncode is not None:
                raise RuntimeError(f"Service exited {self.process.returncode} while {description}")
            await asyncio.sleep(0.2)
        raise TimeoutError(f"Deadline exceeded while {description}")

    async def start(self):
        (self.root / "shutdown-completed.json").unlink(missing_ok=True)
        # Bind an ephemeral loopback port, never the user's :8000 preview.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        environment = {
            key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}
        }
        environment.update(
            {
                "PYTHONPATH": str(self.code / "backend"),
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TIDEBENCH_API_TOKEN": self.token,
            }
        )
        self.process_number += 1
        log_path = self.root / f"server-{self.process_number}.log"
        self.log = log_path.open("wb")
        self.logs.append(log_path)
        self.process = await asyncio.create_subprocess_exec(
            self.args.python,
            str(Path(__file__).resolve()),
            "--child",
            "--code",
            str(self.code),
            "--data",
            str(self.root),
            "--revision",
            self.revision,
            "--port",
            str(self.port),
            cwd=self.code,
            env=environment,
            stdout=self.log,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self.client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.port}",
            headers={"Authorization": "Bearer " + self.token},
            timeout=httpx.Timeout(self.args.http_timeout),
            limits=httpx.Limits(max_connections=32),
            trust_env=False,
        )
        ready = await self.wait(
            lambda: self.request("GET", "/readyz", probe=True), 25, "starting ready workers"
        )
        if ready.get("workers") is not True:
            raise RuntimeError("Unexpected runtime readiness or version")
        identity = json.loads((self.root / "runtime-identity.json").read_text())
        if self.identity is not None and identity != self.identity:
            raise RuntimeError("Implementation fingerprint changed across restart")
        self.identity = identity

    async def stop(self):
        code, forced = None, False
        if self.process and self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                code = await asyncio.wait_for(self.process.wait(), 20)
            except TimeoutError:
                forced = True
                self.process.kill()
                code = await asyncio.wait_for(self.process.wait(), 5)
        elif self.process:
            code = self.process.returncode
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.log:
            self.log.close()
            self.log = None
        marker = self.root / "shutdown-completed.json"
        shutdown = json.loads(marker.read_text()) if marker.exists() else {}
        complete = shutdown.get("revision") == self.revision and all(
            shutdown.get(k) is True
            for k in ("inflight_drained", "supervisors_stopped", "process_lease_released")
        )
        return {"exit_code": code, "forced_kill": forced, "lifespan_shutdown_completed": complete}

    def database(self):
        connection = sqlite3.connect(f"file:{self.root / 'tidebench.sqlite3'}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def orders_snapshot(self):
        with contextlib.closing(self.database()) as conn:
            return {
                (row["source"], row["key"]): {
                    "id": row["id"],
                    "body_hash": hashlib.sha256(row["body"].encode()).hexdigest(),
                }
                for row in conn.execute("SELECT source,key,id,body FROM pro_orders")
            }

    async def restart(self):
        before = self.orders_snapshot()
        started = time.monotonic()
        shutdown = await self.stop()
        await self.start()
        after = self.orders_snapshot()
        preserved = all(after.get(key) == value for key, value in before.items())
        record = {
            "at_elapsed_seconds": round(started - self.elapsed_start, 3),
            "sigterm_to_ready_ms": round((time.monotonic() - started) * 1000, 3),
            "orders_before": len(before),
            "orders_at_ready": len(after),
            "prior_order_ids_and_bodies_preserved": preserved,
            **shutdown,
        }
        self.restarts.append(record)
        if (
            not preserved
            or shutdown["forced_kill"]
            or shutdown["exit_code"] not in (0, -signal.SIGTERM)
            or not shutdown["lifespan_shutdown_completed"]
        ):
            self.errors.append({"phase": "restart", "detail": record})

    async def setup(self):
        definitions = [
            (
                "BTC basis carry",
                {
                    "mode": "funding_carry",
                    "capital_pct": "8",
                    "carry_threshold": "-.01",
                    "rebalance_bars": 2,
                    "legs": [
                        {"inst_id": "BTC-USDT", "weight": ".5"},
                        {
                            "inst_id": "BTC-USDT-SWAP",
                            "weight": "-.5",
                            "leverage": "2",
                            "direction": "short_only",
                        },
                    ],
                },
            ),
            (
                "ETH SOL cash basket",
                {
                    "mode": "fixed_weights",
                    "capital_pct": "8",
                    "rebalance_bars": 2,
                    "legs": [
                        {"inst_id": "ETH-USDT", "weight": ".5"},
                        {"inst_id": "SOL-USDT", "weight": ".5"},
                    ],
                },
            ),
            (
                "Independent swap exposure",
                {
                    "mode": "fixed_weights",
                    "capital_pct": "8",
                    "rebalance_bars": 2,
                    "legs": [
                        {"inst_id": "OKB-USDT-SWAP", "weight": ".25", "leverage": "2"},
                        {
                            "inst_id": "DOGE-USDT-SWAP",
                            "weight": "-.25",
                            "leverage": "2",
                            "direction": "short_only",
                        },
                    ],
                },
            ),
        ]
        packages = {}
        for _, definition in definitions:
            for leg in definition["legs"]:
                package = await self.request(
                    "POST",
                    PREFIX + "/pro/catalog/packages",
                    {
                        "source": "example",
                        "inst_id": leg["inst_id"],
                        "bar": "1H",
                        "start": ANCHOR - 96 * HOUR,
                        "end": ANCHOR,
                    },
                )
                packages[leg["inst_id"]] = package["id"]

                async def ready(identifier=package["id"], symbol=leg["inst_id"]):
                    p = await self.request("GET", PREFIX + "/pro/catalog/packages/" + identifier)
                    if p["status"] in {"failed", "canceled"}:
                        raise RuntimeError(f"Package {symbol} failed: {p.get('error')}")
                    return p if p.get("ready") else None

                # Respect the five-active-package admission budget. This audit
                # measures lifecycle behavior, not deliberate queue overflow.
                await self.wait(ready, 60, "building synthetic data packages")
        for name, definition in definitions:
            project = await self.request(
                "POST",
                PREFIX + "/pro/portfolio-strategies",
                {
                    "name": name,
                    "hypothesis": "Lifecycle acceptance: actual orders, funding and owners must reconcile under restart and concurrent reads.",
                    "definition": definition,
                },
            )
            version = project["version"]
            definition = version["definition"]
            config = {
                "name": name,
                "hypothesis": version["hypothesis"],
                "portfolio_version_id": version["id"],
                "legs": [
                    {
                        **{k: v for k, v in leg.items() if k != "inst_id"},
                        "package_id": packages[leg["inst_id"]],
                    }
                    for leg in definition["legs"]
                ],
                **{
                    key: definition[key]
                    for key in (
                        "mode",
                        "capital_pct",
                        "rebalance_bars",
                        "lookback",
                        "top_k",
                        "carry_threshold",
                    )
                },
            }
            run = await self.run_research(config, "initial")
            preview = await self.request(
                "POST", PREFIX + "/pro/execution/portfolio-releases/preview", {"run_id": run["id"]}
            )
            release = await self.request(
                "POST",
                PREFIX + "/pro/execution/portfolio-releases",
                {
                    "run_id": run["id"],
                    "preview_hash": preview["preview_hash"],
                    "review": "Offline lifecycle audit; reviewed shared capital, full-fill costs, sequential leg risk and compensation.",
                    "acknowledgements": preview["required_acknowledgements"],
                },
            )
            activated = await self.request(
                "POST", PREFIX + "/pro/execution/portfolio-releases/" + release["id"] + "/activate"
            )
            self.groups.append({"id": activated["group_id"], "name": name, "definition": definition})
            self.configs.append(config)
            # The v0.5 concurrent-start preflight exposed an inventory/quote race.
            # This elapsed audit explicitly measures sequential admission, then
            # concurrent ongoing operation. The failed preflight is retained separately.
            await self.wait(self.settled, 70, "sequential initial economic fills")

    async def run_research(self, config, phase):
        started = time.monotonic()
        run = await self.request("POST", PREFIX + "/pro/research/portfolios", config)

        async def done():
            result = await self.request("GET", PREFIX + "/pro/research/portfolios/" + run["id"])
            if result["status"] == "failed":
                raise RuntimeError(f"Short research failed: {result.get('error')}")
            return result if result["status"] == "completed" else None

        result = await self.wait(done, 65, "completing bounded portfolio research")
        self.jobs.append(
            {
                "id": run["id"],
                "phase": phase,
                "status": result["status"],
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "result_hash": result["manifest"]["result_hash"],
            }
        )
        return result

    async def settled(self):
        clock = await self.request("GET", PREFIX + "/pro/execution/clock")
        latest = clock["market_ts"] // HOUR * HOUR - HOUR
        rows = (await self.request("GET", PREFIX + "/pro/execution/portfolios?source=example"))["items"]
        current = {row["id"]: row for row in rows}
        for group in self.groups:
            row = current[group["id"]]
            if row["status"] != "running":
                raise RuntimeError(
                    f"Managed group {group['name']} became {row['status']}: {row.get('last_error')}"
                )
            if row["last_bar"] != latest:
                return None
        return current

    def memory(self):
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=3, check=True
        )
        processes = {
            int(pid): (int(ppid), int(rss))
            for pid, ppid, rss in (line.split() for line in result.stdout.splitlines() if line.strip())
        }
        selected = {self.process.pid}
        changed = True
        while changed:
            added = {pid for pid, (parent, _) in processes.items() if parent in selected} - selected
            changed = bool(added)
            selected |= added
        return {
            "server_rss_kib": processes.get(self.process.pid, (0, 0))[1],
            "tree_rss_kib": sum(processes[pid][1] for pid in selected if pid in processes),
            "descendant_count": len(selected) - 1,
        }

    async def sample(self):
        routes = [
            "/pro/execution/account?source=example",
            "/pro/execution/contributions?source=example",
            "/pro/execution/analytics?source=example",
        ]
        responses = await asyncio.gather(
            *(self.request("GET", PREFIX + path) for _ in range(self.args.read_clients) for path in routes),
            return_exceptions=True,
        )
        for index, result in enumerate(responses):
            if isinstance(result, Exception):
                self.errors.append(
                    {"phase": "concurrent_read", "route": routes[index % 3], "detail": str(result)}
                )
            elif index % 3 == 1:
                self.owner_checks["reconciled" if result["reconciled"] else "failed"] += 1
        self.reads += len(responses)
        ready, operations = await asyncio.gather(
            self.request("GET", "/readyz"), self.request("GET", PREFIX + "/pro/ops")
        )
        workers = operations["workers"]
        memory = await asyncio.to_thread(self.memory)
        sample = {
            "elapsed_seconds": round(time.monotonic() - self.elapsed_start, 3),
            "ready": ready["status"],
            "workers_healthy": all(row["healthy"] for row in workers),
            "worker_count": len(workers),
            "operations_status": operations["health"]["status"],
            "incident_count": len(operations["incidents"]),
            **memory,
        }
        self.samples.append(sample)
        if not sample["workers_healthy"] or sample["worker_count"] != 5 or sample["ready"] != "ready":
            self.errors.append({"phase": "worker_health", "detail": sample})

    async def run(self):
        await self.start()
        self.progress("preparing_data_research_and_groups")
        await self.setup()
        self.elapsed_start = time.monotonic()
        self.progress("elapsed_soak_started")
        next_step = next_sample = 0.0
        next_job = self.args.research_interval
        next_progress = 30.0
        work = set()
        while (elapsed := time.monotonic() - self.elapsed_start) < self.args.duration:
            if not self.restarts and elapsed >= self.args.duration * 0.5:
                # Drain audit HTTP jobs before the explicit process interruption.
                if work:
                    await asyncio.gather(*work)
                    work.clear()
                self.progress("sigterm_restart")
                await self.restart()
                self.progress("resumed_same_workspace")
            if elapsed >= next_step:
                clock = await self.request("GET", PREFIX + "/pro/execution/clock")
                await self.request(
                    "POST",
                    PREFIX + "/pro/execution/clock",
                    {"expected_revision": clock["revision"], "step_ms": self.args.step_hours * HOUR},
                )
                self.clock_steps += 1
                next_step += self.args.interval
            if elapsed >= next_sample:
                await self.sample()
                next_sample += self.args.sample_interval
            if elapsed >= next_job:
                task = asyncio.create_task(self.run_research(self.configs[1], "concurrent_soak"))
                work.add(task)
                next_job += self.args.research_interval
            for task in list(work):
                if task.done():
                    try:
                        await task
                    except Exception as exc:
                        self.errors.append({"phase": "concurrent_research", "detail": str(exc)})
                    work.remove(task)
            if elapsed >= next_progress:
                self.progress("elapsed_soak_running")
                next_progress += 30
            await asyncio.sleep(min(0.2, max(0, self.args.duration - elapsed)))
        if work:
            results = await asyncio.gather(*work, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.errors.append({"phase": "concurrent_research", "detail": str(result)})
        active = await self.wait(self.settled, 70, "final confirmed portfolio targets")
        # No further market steps; stop only these disposable groups to capture
        # stable economics. Stopping retains their actual open inventory.
        for group in self.groups:
            await self.request("POST", PREFIX + "/pro/execution/portfolios/" + group["id"] + "/stop")
        await asyncio.sleep(5.1)
        account = await self.request("GET", PREFIX + "/pro/execution/account?source=example")
        contributions = await self.request("GET", PREFIX + "/pro/execution/contributions?source=example")
        clock = await self.request("GET", PREFIX + "/pro/execution/clock")
        with contextlib.closing(self.database()) as conn:
            totals = {
                name: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for name, table in {
                    "orders": "pro_orders",
                    "decisions": "forward_decisions",
                    "batches": "portfolio_batches",
                    "commands": "portfolio_commands",
                    "funding_settlements": "pro_funding",
                    "contribution_events": "contribution_events",
                }.items()
            }
            duplicates = conn.execute(
                "SELECT COUNT(*) FROM (SELECT source,key FROM pro_orders GROUP BY source,key HAVING COUNT(*)>1)"
            ).fetchone()[0]
            missing = conn.execute(
                "SELECT COUNT(*) FROM portfolio_commands c LEFT JOIN pro_orders o ON o.id=c.order_id AND o.key=c.key WHERE c.status='completed' AND o.id IS NULL"
            ).fetchone()[0]
            statuses = dict(conn.execute("SELECT status,COUNT(*) FROM portfolio_batches GROUP BY status"))
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk = list(conn.execute("PRAGMA foreign_key_check"))
        with localcontext() as context:
            context.prec = 80
            equity_net = Decimal(account["equity"]) - Decimal(account["initial_cash"])
            owner_net = Decimal(contributions["totals"]["net_pnl"])
            delta = owner_net - equity_net
        attempts = self.root / "network-attempts.jsonl"
        attempt_count = len(attempts.read_text().splitlines()) if attempts.exists() else 0
        elapsed = time.monotonic() - self.elapsed_start
        conditions = {
            "requested_monotonic_duration_reached": elapsed >= self.args.duration,
            "three_groups_running_before_audit_stop": len(active) == 3
            and all(g["status"] == "running" for g in active.values()),
            "actual_confirmed_decisions_and_fills": totals["decisions"] > 0 and totals["orders"] > 0,
            "actual_funding_settled": totals["funding_settlements"] > 0
            and Decimal(account["funding_paid"]) != 0,
            "all_batches_completed": set(statuses) == {"completed"},
            "all_owner_observations_reconciled": self.owner_checks["failed"] == 0
            and self.owner_checks["reconciled"] > 0,
            "final_owner_and_account_reconciled": contributions["reconciled"]
            and abs(delta) < Decimal("1e-35"),
            "graceful_restart_preserved_prior_economics": len(self.restarts) == 1
            and self.restarts[0]["prior_order_ids_and_bodies_preserved"]
            and not self.restarts[0]["forced_kill"]
            and self.restarts[0]["exit_code"] in (0, -signal.SIGTERM)
            and self.restarts[0]["lifespan_shutdown_completed"],
            "no_duplicate_keys_or_missing_filled_command_orders": duplicates == missing == 0,
            "no_unexpected_http_failures": self.request_failures == 0,
            "database_integrity_and_foreign_keys": integrity == "ok" and not fk,
            "no_external_network_attempts": attempt_count == 0,
            "concurrent_short_research_completed": any(
                job["phase"] == "concurrent_soak" for job in self.jobs
            ),
        }
        return {
            "monotonic_elapsed_seconds": round(elapsed, 3),
            "clock_steps": self.clock_steps,
            "synthetic_elapsed_hours": (clock["market_ts"] - ANCHOR) / HOUR,
            "acceptance": conditions,
            "passed": all(conditions.values()) and not self.errors,
            "groups": self.groups,
            "economic_counts": totals,
            "batch_status_counts": statuses,
            "duplicate_key_count": duplicates,
            "missing_filled_order_count": missing,
            "restart": self.restarts,
            "research_jobs": self.jobs,
            "concurrent_reads": self.reads,
            "http_status_counts": dict(self.statuses),
            "http_latency": {name: distribution(rows) for name, rows in self.latencies.items()},
            "owner_observations": dict(self.owner_checks),
            "health_and_rss_samples": self.samples,
            "final": {
                "equity": account["equity"],
                "initial_cash": account["initial_cash"],
                "account_equity_net_pnl": str(equity_net),
                "owner_net_pnl": str(owner_net),
                "owner_minus_equity_net_delta": str(delta),
                "contribution_reconciliation_delta": contributions["reconciliation_delta"],
                "funding_paid": account["funding_paid"],
                "fees_paid": account["fees_paid"],
                "open_position_count": len(account["positions"]),
                "valuation_status": account["valuation_status"],
                "owners": [
                    {
                        key: owner[key]
                        for key in (
                            "owner",
                            "realized_pnl",
                            "unrealized_pnl",
                            "fees_paid",
                            "funding_paid",
                            "net_pnl",
                        )
                    }
                    for owner in contributions["owners"]
                ],
            },
            "external_network_attempts": attempt_count,
        }


async def audit(args):
    revision = subprocess.run(
        ["git", "rev-parse", args.revision],
        cwd=args.source_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", revision, "backend"],
        cwd=args.source_root,
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout
    report = {
        "audited_version": None,
        "source_revision": revision,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "configuration": {
            key: getattr(args, key)
            for key in (
                "duration",
                "interval",
                "sample_interval",
                "step_hours",
                "research_interval",
                "read_clients",
                "http_timeout",
            )
        },
        "scope": "Offline loopback HTTP, sequentially admitted three small-capital Example groups (concurrent-start capability excluded), real SQLite/fills/funding/owners, concurrent readers and research, one real SIGTERM restart. Synthetic market time does not count as wall time. This bounded single-host run is not a weeks-long soak, venue or availability/capacity SLA.",
        "passed": False,
    }
    with tempfile.TemporaryDirectory(prefix="tidebench-lifecycle-soak-") as directory:
        root = Path(directory)
        code = root / "code"
        code.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            if any(not (member.isfile() or member.isdir()) for member in tar.getmembers()):
                raise RuntimeError("Source archive contains unexpected non-regular files")
            tar.extractall(code, filter="data")
        runner = Audit(args, root, code, revision)
        try:
            result = await runner.run()
            report.update(result)
        except Exception as exc:
            runner.errors.append(
                {"phase": "audit_aborted", "detail": str(exc).replace(runner.token, "[redacted]")}
            )
            report["monotonic_elapsed_seconds"] = (
                round(time.monotonic() - runner.elapsed_start, 3) if runner.elapsed_start else 0
            )
        finally:
            report["final_process_shutdown"] = await runner.stop()
            report["runtime_identity"] = runner.identity
            report["audited_version"] = runner.identity["application_version"] if runner.identity else None
            report["errors"] = runner.errors
            report["finished_at_utc"] = datetime.now(UTC).isoformat()
            report["server_log_tails"] = [
                path.read_text()[-3000:].replace(runner.token, "[redacted]") for path in runner.logs
            ]
            report["temporary_workspace_removed_on_exit"] = True
            save_json(args.output, report)
            runner.progress("finished_passed" if report["passed"] else "finished_with_failures")
    return 0 if report["passed"] else 1


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--sample-interval", type=float, default=5)
    parser.add_argument("--step-hours", type=int, default=4)
    parser.add_argument("--research-interval", type=float, default=90)
    parser.add_argument("--read-clients", type=int, default=2)
    parser.add_argument("--http-timeout", type=float, default=15)
    parser.add_argument("--output", type=Path, default=Path("docs/audit/v0.5.0-soak-observations.json"))
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--code", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--data", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.child:
        if (
            args.duration < 30
            or min(args.interval, args.sample_interval, args.research_interval) < 1
            or not 1 <= args.step_hours <= 24
            or not 1 <= args.read_clients <= 8
            or not 1 <= args.http_timeout <= 60
        ):
            parser.error(
                "duration >=30, intervals >=1, step-hours 1..24, clients 1..8, HTTP timeout 1..60 required"
            )
        args.output = args.output.resolve()
        args.output.parent.mkdir(parents=True, exist_ok=True)
    return args


if __name__ == "__main__":
    options = arguments()
    if options.child:
        child(options)
    else:
        raise SystemExit(asyncio.run(audit(options)))
