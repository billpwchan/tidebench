# Scope and release acceptance

Tidebench supplies a self-hosted shared workspace for public OKX spot/linear-USDT data, versioned research, shared-capital portfolios and persistent local paper simulation. This records implemented contracts and evidence; it does not certify a complete institutional platform or formal commercial deployment.

| Contract | Implemented behavior | Evidence |
|---|---|---|
| Data lineage | Immutable datasets/packages, funding marks, quality/attribution, resumable fenced downloads | Catalog/package tests; separate public-data acceptance |
| Instrument evidence | Immutable raw forward observations, unavailable-row isolation, causal time/age review and restore-retained evidence | Preopen/expiry/omission/time/precision/conflict tests; public response audit and rendered workflow |
| Strategy definitions | Immutable hypotheses/versions; five families, bounded programs, sizing and close exits | Registry/program/risk tests; rendered recipe/version workflow |
| Research | Next-open fills, train-only selection, replay hashes, cost stress, compressed shared-input artifacts | Research/storage/service tests; replay/export browser workflow |
| Research governance | Single and captured portfolio final evaluations, shared reservations/exposure, retained trials and consumption | Concurrent primary admission, frozen replay, tamper/cross-bar/warmup/restore counterexamples; rendered capture/seal/evaluate workflow |
| Historical portfolios | One cash book, 2–10 aligned markets, fixed/signal/momentum/carry, sequential fills/residuals, independent chronological tests | Portfolio tests; rendered studies/exports |
| Portfolio identity/promotion | Immutable hypotheses/definitions, bound studies, exact cost/risk review and whole-group activation revalidation | Registry/release drift, ownership and role tests |
| Managed forward portfolios | Same-bar evidence, shared capital, reduce-first/frozen additions, idempotent commands, residual limits, durable compensation and whole-group stop | Hard-exit and failure tests; standalone audit; rendered group workflow |
| Contribution accounting | Actual quantity/cost/P&L/fee/funding ownership under the net book; explicit legacy; quarantine preserves protective reduction | Mixed-owner/full-close/funding/liquidation tests; account reconciliation and event history |
| Forward evidence | Incremental bars/checkpoints, decisions/intents/orders, observed account equity/TWR pages and controllable time | Forward/fault tests; browser decision journal |
| Accounting/risk | Spot/contract units, native-asset double entry, funding, tiers, isolated liquidation/debt, reservations/idempotency/halt | Domain/property/concurrency/replay tests |
| Access | Named users, five roles, scrypt, sessions, CSRF, expiry/revocation | Platform/boundary tests; rendered role/password workflows |
| Resource/operations | Shared bounded queue, budgets, owned compute processes, deadlines/owner-death cleanup, progress health, incidents | Process/storage/operations tests; scoped HTTP mixed load |
| Recovery | Checksummed backups, schema match, maintenance/drain, preserved evidence, stopped groups and paused clock | Actual schema-6 restore with newer independent research facts retained; failure/cancellation/conflict regressions |
| Interface/deployment | Responsive bilingual workflows, deferred bundles, locked build, non-root container, single-writer lease | Desktop/mobile browser checks and release-specific verification |

[Verification](verification.md) identifies actual counts, environment and workload scope. The [v0.6 self-audit](audit/red-team-0.6.0.zh-CN.md) provides failure evidence and remaining contracts. Passing an isolated test or older CI run does not establish the state of a later release.

## Substantive remaining work

- **Historical universe and contract events:** explicit selected markets and captured current rules reproduce a scenario; they do not prove historical tradability, listing/delisting completeness, contract conversions or survivorship-free history. Current margin tiers remain scenarios unless attributed history is supplied. See the [temporal evidence design](historical-universe-design.md).
- **Execution capacity:** full fills, bounded child orders and common cash scaling do not model queue priority, partial fills, impact, depth, cancellation latency or available market capacity. No investment edge or deployable strategy scale is established.
- **Long-lived attribution capacity:** economic updates currently scan retained owner/sleeve history. Closed-source accumulation needs capacity optimization and target-environment measurements without weakening reconciliation.
- **Elapsed-time operations:** short load tests, hard exits and accelerated synthetic time do not establish multi-week reliability. Representative target-host soak, resource/feed/recovery SLO evidence and off-host restoration remain acceptance work.

One process owns one SQLite workspace; there is no distributed failover or tenant isolation. Hosted operation also requires actual HTTPS/domain, protected off-host backups, host operations and applicable data permissions, configured using the [runbook](operations.md). The repository supplies the controls and procedures, not those external deployment resources.

## Exchange execution boundary

No exchange order endpoint is called. The selected execution scope is local simulation. Read-only credentials do not enable live execution; private execution needs explicit authorization and a dedicated acknowledgement/fill/unknown-state reconciliation adapter.
