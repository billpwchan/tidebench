# Scope and release acceptance

Tidebench supplies a self-hosted shared workspace for public OKX spot/linear-USDT data, versioned research, shared-capital portfolios and persistent local paper simulation. This records implemented contracts and evidence; it does not certify a complete institutional platform or formal commercial deployment.

| Contract | Implemented behavior | Evidence |
|---|---|---|
| Data lineage | Immutable datasets/packages, funding marks, quality/attribution, resumable fenced downloads | Catalog/package tests; separate public-data acceptance |
| Instrument evidence | Immutable raw forward observations, unavailable-row isolation, causal time/age review and restore-retained evidence | Preopen/expiry/omission/time/precision/conflict tests; public response audit and rendered workflow |
| Strategy definitions | Immutable hypotheses/versions; seven families, economic dossiers, public negative/mixed checks, bounded programs, sizing and close exits | Registry/program/risk tests; rendered recipe/version workflow |
| Research | Next-open fills, train-only selection, replay hashes, cost stress, compressed shared-input artifacts | Research/storage/service tests; replay/export browser workflow |
| Research governance | Single and captured portfolio final evaluations, shared reservations/exposure, retained trials and consumption | Concurrent primary admission, frozen replay, tamper/cross-bar/warmup/restore counterexamples; rendered capture/seal/evaluate workflow |
| Historical portfolios | One cash book, 2–10 markets, shared group failure contract, typed causal lifecycle effects and unavailable economics on unresolved inventory | Historical/forward parity, settlement/conversion/warmup counterexamples; rendered import/study/freeze workflows |
| Research economics | Reconciled price/cost/funding decomposition, static monetary contributions, predeclared cash/passive references and explicit unavailable comparisons | Cost/unit/accounting identity and causal reference tests; rendered evidence |
| Portfolio identity/promotion | Immutable hypotheses/definitions, bound studies, exact cost/risk review and whole-group activation revalidation | Registry/release drift, ownership and role tests |
| Managed forward portfolios | Same-bar evidence, shared capital, reduce-first/frozen additions, idempotent commands, residual limits, durable compensation and whole-group stop | Hard-exit and failure tests; standalone audit; rendered group workflow |
| Contribution accounting | Actual quantity/cost/P&L/fee/funding ownership under the net book; explicit legacy; quarantine preserves protective reduction | Mixed-owner/full-close/funding/liquidation tests; account reconciliation and event history |
| Forward evidence | Entire frozen observation window, fixed IDs/hashes, discontinuity detection, measured clock/economic coverage and honest flow-valuation limits | Early-loss/>500-row, restore, source-clock, exact-window verification tests; rendered freeze/append/verify |
| Account capital | Effective manual/owner capital plus commitments, pending orders, fee/spread-adjusted admission, gross/base caps and stopped-inventory retention | Concurrent promises, manual 30% + promise 80% rejection, missing valuation, policy drift and negative-margin counterexamples |
| Funding obligations | Frozen original settlement inventory and owners, exactly-once late posting, provisional economics and available protection | Close/reopen/restart, missing/corrupt evidence, unit-conversion and late-owner tests |
| Current L2 evidence | Both-side finite depth/cost observations, exact units, full captured-window inclusion and immutable reports | Omitted bad row, stale/future clock, depth exhaustion, boundary coverage and pinned-review tests; separate real-public collection |
| Accounting/risk | Spot/contract units, native-asset double entry, funding, tiers, isolated liquidation/debt, reservations/idempotency/halt | Domain/property/concurrency/replay tests |
| Access | Named users, five roles, scrypt, sessions, CSRF, expiry/revocation | Platform/boundary tests; rendered role/password workflows |
| Resource/operations | Shared bounded queue, budgets, owned compute processes, deadlines/owner-death cleanup, progress health, incidents | Process/storage/operations tests; scoped HTTP mixed load |
| Recovery | Checksummed backups, schema match, maintenance/drain, preserved evidence, stopped groups and paused clock | Actual schema-8 restore with newer independent research/public facts retained; later financial epochs stay in safety backup; conflict/pinned-hash regressions |
| Interface/deployment | Responsive bilingual workflows, deferred bundles, locked build, non-root container, single-writer lease | Desktop/mobile browser checks and release-specific verification |

[Verification](verification.md) identifies actual counts, environment and workload scope. The [independent v0.10 review](audit/fresh-institutional-review-0.10.0.zh-CN.md) provides failure evidence and remaining contracts. Passing an isolated test or older CI run does not establish the state of a later release.

The [v0.9 risk allocation research](portfolio-risk-research.md) implements causal covariance, capped inverse-volatility sizing and a shared historical/managed risk governor. All 28 exploratory public-data cases remain cost-sensitive; no alpha or realized-risk bound is established.

## Evidence and deployment boundaries

- **Historical universe and contract events:** typed listing, suspension, rule, delisting, cash settlement and same-identity unit conversion facts now execute causally. Imported source hashes do not independently prove publication time or complete venue membership; managed lifecycle conversion routing is blocked where unsupported. Current margin tiers remain scenarios unless attributed history is supplied. See the [temporal evidence design](historical-universe-design.md).
- **Execution capacity:** full fills, bounded child orders and common cash scaling do not model queue priority, partial fills, impact, depth, cancellation latency or available market capacity. Current public L2 reports measure finite observed displayed-book costs; they do not replace a matching engine or establish future capacity. No investment edge or deployable strategy scale is established.
- **Long-lived attribution capacity:** economic updates currently scan retained owner/sleeve history. Closed-source accumulation needs capacity optimization and target-environment measurements without weakening reconciliation.
- **Elapsed-time operations:** short load tests, hard exits and accelerated synthetic time do not establish multi-week reliability. Representative target-host soak, resource/feed/recovery SLO evidence and off-host restoration remain acceptance work.

One process owns one SQLite workspace; there is no distributed failover or tenant isolation. Hosted operation also requires actual HTTPS/domain, protected off-host backups, host operations and applicable data permissions, configured using the [runbook](operations.md). The repository supplies the controls and procedures, not those external deployment resources.

## Exchange execution boundary

No exchange order endpoint is called. The selected execution scope is local simulation. Read-only credentials do not enable live execution; private execution needs explicit authorization and a dedicated acknowledgement/fill/unknown-state reconciliation adapter.
