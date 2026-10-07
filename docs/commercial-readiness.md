# Scope and release acceptance

The professional workspace ships public OKX data, historical research and persistent local spot / linear USDT perpetual simulation. Authentication, recovery and operating controls below are implemented in the application. This document records their evidence; it is not a list of promised future replacements for missing core functionality.

| Control | Implemented behavior | Evidence |
|---|---|---|
| Data lineage | Durable pagination, cancellation fencing, immutable manifests, rules, quality, provider attribution and record hashes | `test_catalog.py`; public 48h integration acceptance |
| Research packages | Atomic trade/mark/funding preparation, bounded exact-time mark capture, immutable manifests and strict research binding | `test_data_packages.py`; complete perpetual-package browser workflow |
| Captured portfolio analysis | Asset/market gross and net exposure, concentration, isolated margin, custom price shocks, deterministic capture/replay | `test_portfolio_analytics.py`; read-only API and rendered workflow acceptance |
| Research reproducibility | Installed module identity, canonical full-result hash and explicit replay divergence failure | `test_pro_service.py`; API and browser replay acceptance |
| Research causality | Confirmed closes, next-open orders, prefix/future perturbation checks and explicit costs | `test_engine.py`, `test_pro_research.py` |
| OOS evaluation | Train-only candidate selection, purged windows, independent test folds, bounded grids/cost stress | `test_pro_research.py`; all five modes exercised through the rendered UI |
| Derivative accounting | Contract units, signed P&L, actual funding events, isolated tiers, explicit gap liabilities | `test_derivatives.py`, `test_pro_execution.py` |
| Durable orders | Transactional risk, cash/margin reservations, canonical idempotency, cancellation and native asset journal | Concurrency, rollback, exact balance replay and source/freshness tests |
| Forward strategies | Shared directional policy, persisted close/open intents, per-fill ownership and stop checks | Real Store/book crash/restart and stop-race tests in `test_pro_service.py` |
| Access | Salted bounded scrypt, server sessions, expiry, CSRF, five roles, password reset and revocation | `test_platform.py`, `test_pro_boundaries.py`; actual browser user/password workflows |
| Recovery | Online checksummed backups, retention, schema matching, maintenance/drain, pre-halted restore image | Actual SQLite mutate/restore tests, concurrent recovery and canceled-drain tests |
| Operations | Readiness, independent feed ages, job checkpoints, disk/WAL sizes, audit and Prometheus request metrics | API acceptance and measured HTTP workload |
| Deployment | Locked source build, non-root image, private Compose binding, schema upgrade record and rollback runbook | CI verify + container startup/auth/account smoke |
| Product usability | Connected data→research→execution workflow, bilingual controls, responsive layout, large-table pagination | Desktop/mobile browser acceptance and recorded workflow review |

Detailed results, environment and scripts are in the [verification record](verification.md). Backups, role controls and deployment procedures are described in the [operations runbook](operations.md).

## Evidence boundaries

The repository can establish tested behavior and reproducible local workloads. It cannot establish months of service availability from a short run, manufacture an external security certification, provide tenant isolation from shared-workspace roles, or grant rights to a third party's data. No such claim is made.

Historical bars and public observations support explicit simulation models. They do not establish exchange matching fidelity, investment edge, future returns or a live account reconciliation guarantee. Current tiers are captured scenarios; one-minute settlement marks are labeled approximations. Missing funding evidence blocks continuation rather than becoming zero cost.

The supplied local deployment is operational with authentication and recovery. A public HTTPS host, domain, separately protected off-host backup destination and any applicable provider permissions belong to the actual hosting environment. Configure those resources using the runbook when operating a hosted service.

## Exchange execution boundary

No exchange order endpoint is called. Introducing private execution would expand the authorized product scope and needs a dedicated acknowledgement/fill/unknown-state reconciliation adapter, durable outbox, deduplication and explicit live-execution authorization. An OKX Read key does not grant that authorization. This boundary is distinct from the implemented local simulation product.
