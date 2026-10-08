# Tidebench 0.6.0 — captured portfolio evaluations and recovery-safe research

Portfolio research now preserves the complete computation inputs before admission. A final evaluation binds an immutable portfolio, exact market data and settlement marks, rules/tiers, costs, capital limits, warmup and typed rejection criteria to one primary run. Repeated or concurrent evaluation requests retrieve that run; a committed failure cannot reset the final test.

- A complete **Research governance → Portfolios** workflow captures, reviews, seals and evaluates the contract; result pages display cash-benchmark criteria and distinguish rejected, inconclusive and reproduced evidence.
- Captured-input replay performs no catalog replacement reads and requires identical result hashes under the pinned implementation. Replays remain linked to their original primary evaluation.
- Shared source/market/time reservations protect across portfolio legs, single strategies, dataset aliases and bar intervals, including indicator warmup exposure.
- Schema 6 restore unions newer independently hashed reservation, exposure, consumption and trial facts before replacing financial state. A missing consumed result remains explicitly unavailable; restoring an older sealed backup cannot reopen it.
- Undersized same-side rebalance differences remain visible residuals under the reviewed limit; full exits, side changes, initial minimum failures and cash shortfalls retain their safety rules. Historical and managed portfolios use the same planning policy.
- Real elapsed testing exposed and led to fixes for concurrent inventory/quote enumeration and the operations endpoint's SQLite WAL disappearance race. A late order response also preserves a trader's already edited next draft.
- 16 desktop/mobile workflows, 666 backend regressions plus an additional strict-residual counterexample, real SQLite recovery cases and frozen perpetual funding/tier checks accompany the implementation. The corrected lifecycle audit passed 608 real seconds with a SIGTERM restart, 154 orders and 120 funding settlements; original failed observations are retained. Public OKX acceptance also verified a current 48-hour perpetual window.

Read the [research governance guide](research-governance.md), [lifecycle audit method](audit/soak-method.md) and [verification record](verification.md). Execution remains local paper trading, on public data. Cash is this protocol's explicit benchmark; it is not a universal benchmark or a statistical significance claim. Historical-universe completeness, contract lifecycle conversion, order-book capacity and longer external operations remain substantive implementation/acceptance work.

## Upgrade

Create and verify a schema-5 backup with v0.5 before upgrading. Stop its single writer, deploy v0.6 and inspect readiness plus unchanged authoritative accounts, positions and orders. Existing runs and single-strategy protocols keep their original evidence scope. New schema-6 tables add frozen portfolio seals and independent research facts; pre-upgrade recorded research backfills exposure/trial accounting. Create and verify a schema-6 backup after inspection.

In-app restore requires matching schema. Roll back with v0.5 code against a separately preserved pre-upgrade schema-5 copy, never against schema 6. In-app schema-6 recovery deliberately preserves newer research facts while restoring the financial workspace. External filesystem rollback must separately preserve the newer evidence; deleted history cannot be reconstructed by software.
