# Full-window forward evidence

Forward performance is an observed account record, not reconstructed price history.
The displayed table remains paginated; the summary is computed over every observation
inside one frozen ID window. A gap or drawdown older than the latest 500 rows cannot
disappear from the summary.

## Inputs and replay

Each new observation binds the account source, observed market time, ledger sequence
and actual server wall-clock timestamp into its content hash. Captures retain economic
status and pending funding, including a missing-quote observation or an entirely flat
account with an unresolved funding obligation. Old observations remain readable but
their previously unbound clock fields are disclosed.

The window manifest includes minimum/maximum IDs, row count, an ordered hash of row
identities and content hashes, the coverage interval and a frozen audit boundary.
`POST /api/v1/pro/execution/performance/snapshots` stores the manifest, summary and
acceptance result. `GET .../snapshots/{id}/verify` recomputes that exact window and audit
boundary. Later rows do not change the frozen result; modifying or deleting an old
input is detected. These are application content checks, not an externally signed
attestation against an administrator who can replace the database and all hashes.

API query parameters:

| Field | Meaning |
|---|---|
| `limit`, `before` | Displayed observation page only |
| `window_start`, `window_end` | Inclusive frozen source observation IDs |
| `max_gap_ms` | Largest allowed interval between actual wall observations; default 60 seconds |

## Financial calculation

For adjacent complete observations with positive starting equity, the no-flow return
is current equity divided by previous equity minus one. These returns are geometrically
linked. The summary reports observed drawdown, complete endpoint net P&L less external
flows, actual fee/funding changes and the maximum observed gross and same-underlying
gross exposure. BTC spot and BTC perpetual absolute exposure add even if their deltas
offset.

An unavailable/stale valuation, pending funding, wall coverage gap or clock regression
breaks the linked full-window return. Contiguous segments are shown separately; they
are never joined across the gap. Endpoint net P&L is labeled as endpoint arithmetic,
not an inferred uninterrupted path.

An external capital flow without valuation at its actual time makes the return method
an estimate. The app exposes `boundary_adjusted_return` using
`(end equity - flow) / starting equity`, and withholds the main linked-return field.
A true time-weighted return requires appropriately timed subperiod valuations; external
flows cannot simply be treated as strategy profit. This design follows the calculation
distinction in the [GIPS handbook for firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/).
The product does not claim GIPS compliance.

No observation series proves the unobserved intraperiod minimum equity, realized
execution capacity or a profitable strategy.

## Operational observation acceptance

The default target is **14 days of actual wall time**, at least 500 observations,
99% observed interval coverage and 99% economic interval coverage, no pending funding,
no clock regressions, a fresh final observation, bound clocks and at least one recorded
recovery event. OKX public-source observations must progress through market time at
a ratio between 0.5 and 2 relative to elapsed wall time. Example data cannot pass the
public-source gate. Advancing the example clock by 30 days in a second remains one
second of wall observation.

Coverage weights elapsed intervals between observations. An interval exceeding the
declared gap threshold is entirely uncovered. Economic coverage additionally requires
both endpoints to be complete. A page size, accelerated simulation or high sample
count does not establish elapsed operating time.

Recovery counts come from actual persisted compensation-completed, operational-condition
resolved and workspace-restore audit events inside the wall window. Their scope is
shown; a generic condition recovery is not proof of every restart, backup or order
idempotency failure mode.

The target is an **account observation and recovery assessment**. It is not HTTP
request availability, an exchange SLA, multi-tenant capacity, cross-host failover or
strategy validation. Those need separate measured indicators and operational evidence.
This separation follows [Google SRE's definition of measured indicators and objectives](https://cloud.google.com/blog/en/products/devops-sre/sre-fundamentals-sli-vs-slo-vs-sla).
A short integration test cannot satisfy a multiweek wall-time target.

## Validation

The isolated tests include a 702-observation drawdown outside the displayed page,
an old economic gap, pending funding after flatness, cash-flow timing, overlapping
spot/perpetual gross, accelerated market time, clock tampering, immutable snapshot
verification after append and exact audit boundaries. Synthetic dates in those tests
verify calculation rules; they are not submitted as live operating evidence.


## Financial restore boundary

A workspace restore replaces financial state with the saved database. It does not
create a strategy return, a fee refund or uninterrupted history. A restore audit event
inside the selected wall window breaks linked returns and contiguous segments. A
decrease in cumulative fee counters or the financial ledger sequence also breaks the chain. Across such a boundary,
the report exposes a separately labeled endpoint state difference and withholds
strategy P&L and period fee/funding changes.

Performance snapshots stay with their financial database backup. Later snapshots
are not merged into an older restored ID timeline. The automatic before-restore
safety backup preserves the later financial observations and their original reports;
restore and inspect that matching state if replay is needed. A fresh acceptance
window should begin after the financial restore boundary.
