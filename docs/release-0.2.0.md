# Tidebench 0.2.0 · Professional workspace

This release connects versioned OKX data, out-of-sample research and a unified spot/perpetual simulation account in an authenticated operating workspace.

- Persistent trade, mark, index and settled-funding history; resumable jobs, versioned hashes, provenance and attributed imports.
- Single replay, parameter grids, cost stress, train/test and rolling walk-forward; independent OOS folds and complete captured evidence.
- Linear USDT perpetual long/short simulation with contract rules, isolated tiers, funding and explicit gap liabilities; spot inventory shares the same journal.
- Previewed market/limit/stop orders, reservations, cancellation, canonical command deduplication and persistent transactional risk.
- Durable strategy reversal intents with crash/restart recovery and per-fill stop/ownership checks.
- Named users, five roles, server sessions, CSRF, password revocation, audit, readiness, metrics, verified backups and actual safe recovery.
- Rebuilt bilingual desktop/mobile workspace, mode-specific research inspection and paginated financial tables.

Public market data needs no key. Execution remains local simulation and sends no exchange orders. Historical bar/settlement approximations and current-tier scenarios are visible in the model evidence.

See [verification](https://github.com/billpwchan/tidebench/blob/v0.2.0/docs/verification.md), [model contract](https://github.com/billpwchan/tidebench/blob/v0.2.0/docs/pro-research.md) and [operations](https://github.com/billpwchan/tidebench/blob/v0.2.0/docs/operations.md). Upgrade creates additive schema version 2; legacy paper records are retained in their separate compatibility ledger. Take and verify a backup before upgrading; cross-version rollback uses a matching tagged application and pre-upgrade backup.

Local acceptance: 323 backend/core/API tests, 6 desktop/mobile browser workflows, passing lint/format/build and frozen dependency checks, zero reported npm audit vulnerabilities. Public integration verified 48 hours of trade/mark/index history and realized funding with all dataset hashes checked; a separate measured HTTP/research workload is published with its environment. The linked CI independently verifies the published commit and starts the non-root container.
