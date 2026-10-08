# Elapsed-time lifecycle audit

`scripts/lifecycle_soak.py` archives an exact Git revision's backend into a disposable directory and runs an authenticated loopback-only HTTP subprocess. It uses synthetic Example data, isolated SQLite, an ephemeral bearer and an HTTP transport/socket policy that records and rejects external network attempts. It never opens the user's operational database or resets an operator account.

Three disjoint small-capital spot/perpetual groups generate actual decisions, committed orders, funding, target batches and contribution events. Market time steps separately from monotonic wall time. Concurrent readers request account, analytics and contribution reports while additional research jobs execute. Health, worker state, RSS and request latency are sampled. Midway through the requested elapsed duration, a real SIGTERM stops the process; the same workspace and archived implementation restart. Prior order IDs and bodies, unique command keys, completed-command references, accounting and ownership reconciliation are checked.

Uvicorn can re-raise SIGTERM after a successful lifespan shutdown, producing exit code `-15`. Exit code alone is therefore insufficient. An audit wrapper writes a completion marker only after the application's original lifespan finishes, checking drained offloads, stopped supervisors and released process lease. Each start removes the previous marker. A passing shutdown requires a fresh matching marker, an expected exit status and no forced kill. This wrapper observes the lifecycle; it does not replace the application's shutdown routine or change the research fingerprint.

The v0.5 trials are retained honestly:

- [Concurrent startup preflight](v0.5.0-concurrent-start-failure.json) failed because another group opened inventory after quote enumeration. An incomplete mark set was safely rejected, but the group incorrectly treated the pre-commit race as permanent leg failure. v0.6 re-fetches only this diagnosed missing-market valuation case, up to three times per cycle, retaining the immutable command and its idempotency key. Persistent contention defers the frozen command with an audited warning; it does not turn an admission rejection into a fill.
- [Elapsed trial interrupted at about 430 seconds](v0.5.0-soak-interrupted.json) found the operations endpoint's WAL `exists()`/`stat()` race during SQLite checkpoint removal. v0.6 observes WAL size with a single `stat()` and treats disappearance as zero. This trial also classified Uvicorn's expected `-15` too broadly as shutdown failure; the new lifecycle marker resolves that measurement limitation. The original failed record is not rewritten as a pass.

The elapsed run admits groups sequentially, then exercises concurrent ongoing operation. It explicitly excludes simultaneous activation capacity; that race has a separate real-book regression. A successful 600-second single-host synthetic run is bounded lifecycle evidence, not weeks of uptime, venue execution, a distributed failover drill, a latency SLO or a production capacity guarantee. RSS observations on the shared development host are not a controlled benchmark.

Example invocation (use an actual committed revision):

```sh
uv run python scripts/lifecycle_soak.py --source-root . --revision HEAD \
  --duration 600 --output /tmp/tidebench-lifecycle-observations.json
```

The output includes the audited version/revision/fingerprint, actual monotonic elapsed time, restarts, acceptance predicates, counts and errors. The default 600-second duration cannot be satisfied by advancing the synthetic market clock.
