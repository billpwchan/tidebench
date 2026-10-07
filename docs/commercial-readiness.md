# Commercial readiness gates

**Release 0.1: developer preview, single operator, local paper only.** Good architecture and a passing test suite do not establish production maturity. This document is a gate list for future releases, not a claim that all controls are implemented.

| Gate | v0.1 evidence or limit | Required before expanding scope |
|---|---|---|
| Research causality | Next-open fills, prefix/perturbation tests, closed-bar validation | Wider strategy-path coverage, independent review |
| Input reproducibility | Saved normalized dataset, instrument snapshot, hash, versioned manifest and replay | Data catalog, change policy, large-dataset artifact retention |
| Accounting | Decimal, atomic local fills, durable idempotency, concurrency/rollback tests | Multi-asset fee ledger, property testing of additional order types |
| Execution | Local spot market-fill approximation | Exchange demo state machine, partial fills, unknown states and reconciliation |
| Data transport | Bounded REST polling, timestamp/quality checks | WS subscribe/reconnect/gap recovery and soak tests |
| Availability | Single process with recovery of saved research inputs | SLOs, chaos tests, worker fencing and incident exercises |
| Access | Local listener, optional bearer token, origin/host checks | OIDC, RBAC, tenant isolation, session/token lifecycle |
| Secrets | No exchange credentials in browser/API/database | KMS/Vault, rotation, IP allowlists, private worker-only access |
| Operations | Docker/source setup, health endpoint, CI | Tested backup/restore, migrations, logs/metrics/tracing, upgrade rollback |
| Legal/data | Synthetic examples; users fetch market data locally | Applicable exchange/data licensing and regional product review |
| Product quality | Functional UI, responsive layout and workflow tests | User interviews, accessibility audit, support and usability validation |

## Exchange execution acceptance

An order acknowledgement is not a fill. Startup and reconnect must reconcile pending orders, fills and balances. `clOrdId` is not a permanent global idempotency key. Network timeouts require an Unknown state and reconciliation before retries can create risk. See [OKX trading best practices](https://www.okx.com/docs-v5/trick_en/).

No real-money execution should be introduced merely by adding an environment flag to this release. It needs a reviewed adapter, state machine, durable outbox, fill deduplication, per-asset accounting, independent risk controls and explicit operational approval.

## Pilot evidence to collect

- A defined continuous demo/paper run with observed reconnects and no unexplained balance drift.
- Crash/restart tests around submission, acknowledgement, partial fill and ledger commit.
- Complete recovery from a tested backup, including risk state and deduplication records.
- Measured API/job latency and capacity under a documented workload; no invented benchmarks.
- A published limitations and incident policy, maintained alongside release notes.

Profitability and trading-system correctness are different questions. Passing these gates would not establish an investment edge.
