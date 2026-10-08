# Scope and release acceptance

Tidebench supplies a self-hosted shared workspace for public OKX spot/linear-USDT data, versioned research, shared-capital historical portfolios and persistent local paper simulation. This records implemented behavior and checks; it does not certify an industry-leading or complete institutional platform.

| Contract | Implemented behavior | Evidence |
|---|---|---|
| Data lineage | Immutable datasets/packages, funding marks, quality/attribution, resumable fenced downloads | Catalog/package tests; separate public-data acceptance |
| Strategy definitions | Immutable hypotheses/versions; five families, bounded programs, sizing and close exits | Registry/program/risk tests; rendered recipe/version workflow |
| Research | Next-open fills, train-only selection, replay hashes, cost stress, compressed shared-input artifacts | Research/storage/service tests; replay/export browser workflow |
| Governance | Project trials, frozen one-use tests, alias/warmup/portfolio overlap guards | Governance concurrency/tamper tests; seal/evaluate browser workflow |
| Historical portfolios | One cash book, 2–10 aligned markets, fixed/signal/momentum/carry, sequential fills/residuals, independent chronological tests | Portfolio tests; rendered two-market study/export |
| Reviewed promotion | Candidate/cost/policy review, immutable approval, transactional activation revalidation | Registry drift/concurrency/ownership tests; version→research→approval→activation workflow |
| Forward evidence | Incremental bars/checkpoints, decisions/intents/orders, observed equity/TWR pages and controllable time | Forward/fault tests; browser exit journal |
| Accounting/risk | Spot/contract units, native-asset double entry, funding, tiers, isolated liquidation/debt, reservations/idempotency/halt | Domain/property/concurrency/replay tests |
| Access | Named users, five roles, scrypt, sessions, CSRF, expiry/revocation | Platform/boundary tests; browser role/password workflows |
| Resource/operations | Shared bounded queue, budgets, owned compute processes, deadlines/owner-death cleanup, progress health, incidents | Process/storage/operations tests; real HTTP mixed load |
| Recovery | Checksummed backups, schema match, maintenance/drain, preserved new evidence chain, paused clock | Actual schema-4 restore; failure/cancellation regressions |
| Interface/deployment | Responsive bilingual workflows, deferred bundles, locked build, non-root container, single-writer lease | Desktop/mobile Chromium; CI verify/container job |

See [verification](verification.md) for observed counts/environment and [current self-audit](audit/red-team-0.4.0.zh-CN.md) for remaining contracts: managed multi-asset forward execution, contribution attribution, historical universe/contract events and portfolio version/holdout governance. Account observations and historical portfolios do not substitute for them.

Bar-level full fills cannot establish queue priority, partial fills, impact, capacity or investment edge. Current rules remain scenarios unless attributed historical rules are supplied. Workflow holdouts cannot detect outside experiments or prevent raw public-data access.

One process owns one SQLite workspace; there is no distributed failover or tenant isolation. Short runs do not establish months of uptime. Hosted operation also needs actual HTTPS/domain, protected off-host backups, host operations and applicable data permissions, configured using the [runbook](operations.md).

## Exchange execution boundary

No exchange order endpoint is called. The selected execution scope is local simulation. Read-only credentials do not enable live execution; private execution needs explicit authorization and a dedicated acknowledgement/fill/unknown-state reconciliation adapter.
