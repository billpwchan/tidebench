# Elapsed-time lifecycle audit

`scripts/lifecycle_soak.py` archives an exact Git revision's backend into a disposable directory and runs an authenticated loopback-only HTTP subprocess. It uses synthetic Example data, isolated SQLite, an ephemeral bearer and an HTTP transport/socket policy that records and rejects external network attempts. It never opens the user's operational database or resets an operator account.

Three disjoint small-capital spot/perpetual groups generate actual decisions, committed orders, funding, target batches and contribution events. Market time steps separately from monotonic wall time. Concurrent readers request account, analytics and contribution reports while additional research jobs execute. Health, worker state, RSS and request latency are sampled. Midway through the requested elapsed duration, a real SIGTERM stops the process; the same workspace and archived implementation restart. Prior order IDs and bodies, unique command keys, completed-command references, accounting and ownership reconciliation are checked.

Uvicorn can re-raise SIGTERM after a successful lifespan shutdown, producing exit code `-15`. Exit code alone is therefore insufficient. An audit wrapper writes a completion marker only after the application's original lifespan finishes, checking drained offloads, stopped supervisors and released process lease. Each start removes the previous marker. A passing shutdown requires a fresh matching marker, an expected exit status and no forced kill. This wrapper observes the lifecycle; it does not replace the application's shutdown routine or change the research fingerprint.

The v0.5 trials are retained honestly:

- [Concurrent startup preflight](v0.5.0-concurrent-start-failure.json) failed because another group opened inventory after quote enumeration. An incomplete mark set was safely rejected, but the group incorrectly treated the pre-commit race as permanent leg failure. v0.6 re-fetches only this diagnosed missing-market valuation case, up to three times per cycle, retaining the immutable command and its idempotency key. Persistent contention defers the frozen command with an audited warning; it does not turn an admission rejection into a fill.
- [Elapsed trial interrupted at about 430 seconds](v0.5.0-soak-interrupted.json) found the operations endpoint's WAL `exists()`/`stat()` race during SQLite checkpoint removal. v0.6 observes WAL size with a single `stat()` and treats disappearance as zero. This trial also classified Uvicorn's expected `-15` too broadly as shutdown failure; the new lifecycle marker resolves that measurement limitation. The original failed record is not rewritten as a pass.

The [first v0.6 elapsed run](v0.6.0-soak-minimum-rebalance-failure.json) reached 600 real seconds, but final group inspection found an undersized same-side rebalance had entered compensation. It is a failed run, not a passing soak. The corrected shared planner retains such adjustments as visible residuals, preserves exits and side changes, and still enforces the reviewed residual limit. Subsequent audit outputs also capture terminal group, batch, command and inventory diagnostics before removing the temporary workspace.

The elapsed run admits groups sequentially, then exercises concurrent ongoing operation. It explicitly excludes simultaneous activation capacity; that race has a separate real-book regression. A successful 600-second single-host synthetic run is bounded lifecycle evidence, not weeks of uptime, venue execution, a distributed failover drill, a latency SLO or a production capacity guarantee. RSS observations on the shared development host are not a controlled benchmark.

Example invocation (use an actual committed revision):

```sh
uv run python scripts/lifecycle_soak.py --source-root . --revision HEAD \
  --duration 600 --output /tmp/tidebench-lifecycle-observations.json
```

The output includes the audited version/revision/fingerprint, actual monotonic elapsed time, restarts, acceptance predicates, counts and errors. The default 600-second duration cannot be satisfied by advancing the synthetic market clock.

## Corrected v0.6 observation

The [committed-source run](v0.6.0-soak-observations.json) passed after 608.323 monotonic seconds. It recorded 154 actual simulated orders, 93 completed batches, 120 funding settlements and 9 completed research jobs. All 240 contribution observations reconciled. The halfway SIGTERM restart preserved 77 prior order identities/bodies and a fresh completed lifespan marker; an additional order completed after restart readiness. Both normal shutdowns completed without forced kill. The raw observations include acceptance predicates, per-route latency, RSS, terminal group/batch diagnostics and finite-precision reconciliation differences. They are scoped observations, not controlled performance benchmarks.

## v0.10 observations

[The original funding-wait failure](v0.10.0-soak-funding-wait-failure.json) and [later correct v1 capital rejection](v0.10.0-soak-capital-allowance-rejection.json) remain adverse completed attempts. Waiting now preserves original targets/pending commands; capital continuation is an explicitly different v2 policy with smaller linked commands and unchanged hard limits, not an error-code tolerance. The [independent review](fresh-institutional-review-0.10.0-v2-allowance-addendum.zh-CN.md) proves real rejection/continuation and damaged-lineage refusal separately.

The [current immutable-source run](v0.10.0-soak-observations.json) passed 611.289 monotonic seconds at `d9418062b6521dcb3590971d786553a06c86fd8d`: 60 market steps, 154 simulated fills, 93 completed batches, 120 funding postings, 274 contribution events, nine research jobs and 1,271 HTTP samples. All prior 77 order IDs/bodies survived the real restart; shutdown drained without a forced kill. Its original full-allocation scenario was not replaced by the separate reserve hypothesis. No allowance replacement was required in this particular interleaving. This does not establish multi-week operation or venue execution capacity.
