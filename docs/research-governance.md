# Portfolio final evaluations and reproducible evidence

A final test should answer a fixed question once. Save the portfolio's immutable version first, prepare one verified package for each ordered leg, then open **Research → Research governance → Portfolios**. The packages must share source, interval and exact coverage. Pick a final interval within that coverage and explicitly reserve enough earlier bars for indicator initialization.

The capture preview records trade/mark candles, realized funding and timestamp-matched settlement marks, instrument units, supplied rule histories, maintenance tiers, costs, account limits, construction and implementation identity. Review the visible window, legs, capital and rejection criteria before sealing that exact preview. Sealing does not fetch replacement tiers or market inputs.

The benchmark in this protocol is a constant USDT cash balance with zero interest and no trades. This is an explicit baseline, not a claim that cash is the best economic benchmark for every hypothesis. Minimum return above cash, maximum drawdown, minimum fills/observations and zero insurance debt are fixed before evaluation. Too few observations or fills make the assessment **inconclusive**. These thresholds are decision criteria, not a statistical significance or multiple-testing correction. `min_trades` counts executed fills, not round trips.

Evaluation uses flat initial inventory and the declared cash. Warmup initializes indicators only: no orders, positions, P&L or funding exposure exists before the final window. Signals formed during the final window execute at the next modeled open. Construction does not trade the final interval during a separate training simulation. Carry additionally requires an observed prior realized settlement, and momentum requires its full declared lookback.

## One-use admission

Queue capacity, shared interval admission, primary-run creation, consumption and trial records commit in one SQLite transaction. Capacity rejection leaves the seal unused. A committed computation failure consumes it. Concurrent or repeated evaluation requests return the same primary run; they cannot silently produce another final result.

Reservations and exposures use **source + market + time**. Replacing a dataset, changing bar aggregation, leaving a strategy unbound, renaming a project or submitting a portfolio leg does not reopen a reserved interval. Actual single-strategy warmup access is included in shared exposure facts. Existing single-strategy seals retain their older protocol and are not relabeled as fully captured portfolio-v2 evidence.

This is workflow governance, not statistical blindness. Public data, raw dataset inspection, prior human knowledge and external experiments remain outside the protocol. Counts include admitted internal attempts across saved versions and retain failed/replayed trials. Replays add attempts, but add no independent primary evaluation. Distinct-configuration counts are inspectable workflow accounting, not an estimate of effective independent hypotheses.

## Frozen replay and release

**Replay frozen inputs** recomputes the captured bundle with the pinned implementation, without reading replacement catalog records or tier snapshots. Result hashes must match before the run becomes a verified replay. Code drift blocks this path; install the recorded version to reproduce old evidence. Older portfolio runs without a captured bundle need a new study and are not promoted to reproducible-input evidence merely by upgrading.

Paper release checks the sealed result against its primary consumption, plan hash, input hash, version and every market reservation. A rejected or inconclusive final test requires a specific acknowledgement in the release review. Deployment does not change that assessment or establish a profitable strategy. Sequential full-fill simulation, residuals and compensation risks remain the declared execution model.

## Restore semantics

Schema 6 recovery restores the saved financial workspace and **unions newer reservation, exposure, consumption and trial facts** from the current database into the staged restore. These independently hashed records have no project/run foreign keys: a restored database can safely retain evidence that a newer run existed even when that run and its artifact are absent. Matching identities with conflicting content abort before financial replacement.

If a sealed backup predates consumption, restoring it yields **consumed_unavailable**, rather than allowing reevaluation. The primary result is absent; the missing evidence remains explicit. Newer exposure also prevents a restored or renamed project from declaring the same interval unseen. Sessions are revoked, execution stops, pending commands cancel, new risk halts and the synthetic clock pauses as in schema 5.

This protection applies to in-app recovery within the surviving workspace. Restoring an old filesystem image onto another machine without the newer facts cannot reconstruct those facts. Keep separately protected verified backups and retain the newest research evidence during disaster recovery; no local database can preserve history that has been externally deleted everywhere.

## API workflow

1. `POST /api/v1/pro/research/portfolio-holdouts/preview`: immutable portfolio version, ordered `package_ids`, `test_start`, `test_end`, `warmup_bars`, costs/limits, `benchmark: "cash"`, rejection plan and typed criteria.
2. `POST /api/v1/pro/research/portfolio-holdouts`: exact `preview_id` and `preview_hash`.
3. `POST /api/v1/pro/research/portfolio-holdouts/{id}/evaluate`: exact `plan_hash`.
4. `GET /api/v1/pro/research/portfolio-governance/{project_id}`: attempts, primaries, replays, configurations, retained/missing evidence and seals.
5. `POST /api/v1/pro/research/portfolios/{run_id}/replay`: captured-input reproduction.

All mutations require the existing authenticated researcher/trader/admin role and CSRF or configured bearer authentication. Execution approval remains a trader/admin action. No exchange order adapter or private credential is introduced.
