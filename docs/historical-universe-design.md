# Historical universe and contract lifecycle — design under review

Status: **forward observations and bounded, attributed historical lifecycle research are implemented**. See [instrument evidence](instrument-evidence.md) and [historical lifecycle](historical-lifecycle.md). Comprehensive historical venue membership, spot redenomination, cross-symbol identity migration and matching managed lifecycle execution remain outside the supported scope. Explicit instrument selection and attributed events do not establish a survivorship-free universe.

## Source findings, checked 2026-10-08

The [OKX API change log](https://www.okx.com/docs-v5/log_en/) records that listing announcements can publish an instrument before its numeric trading rules are available, and that listing/delisting timestamps are updated after announcements. Its September 2026 instruments-channel change permits changed-instrument-only pushes. Therefore a channel message is not a complete universe snapshot, an absent member is not a proven delisting, and a preopen row with blank sizes must not crash an otherwise valid market refresh. The log also documents contract renaming and rebase states; neither is equivalent to a normal close at the last observed trade.

The [official instrument endpoint](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments) describes current metadata. Persisting it today does not establish when older rules were historically knowable. An attributed historical import and a forward observation have different evidence scopes. An announcement's publication time may support a known-at claim; an unsupported user-supplied timestamp does not independently prove it.

## Evidence model

Capture the entire REST response under a unique immutable observation ID before deriving supported instruments. Store source, region, instrument type, requested/received timestamps, transport, endpoint, content hash, raw rows and parser version. Separate endpoint observation from economic rules. Incomplete preopen rows remain visible as unavailable for execution, with their actual missing fields. Do not invent lot/tick/contract values or borrow them from another contract.

Retain each observation, including A → B → A changes; a primary key on instrument/version with a mutable last-seen time loses chronology. Observed-at, externally attributed known-at and effective-at must be separate. A later import must not silently alter an existing run's universe. Same-timestamp conflicting facts fail admission until an explicit supersession references the original fact and preserves both bodies.

An authoritative full snapshot can establish membership at its observed time for its exact source/region/type. It does not establish a continuous interval until the next snapshot. An incremental message changes only its listed members. Omission creates an unresolved observation gap, not an expired instrument or zero-valued asset. APIs must return coverage and unknown members, rather than filtering unresolved rows out of a seemingly complete list.

## Research contract

A universe study freezes the evidence IDs, parser identity, declared known-at policy, decision timestamps, eligibility filters and maximum evidence age. At each decision use only facts known by that time. Membership is eligible / ineligible / unknown; unknown cannot default to eligible. A pre-listing or suspended market produces no new position. Entry/exit logic must retain the asset identity and quantity unit, not infer identity from a symbol prefix.

Published delist time does not supply a settlement price. A held delisting contract requires attributed settlement instructions and price evidence; otherwise the simulation stops with an explicit unavailable valuation rather than closing at an invented last price. Spot delisting does not destroy custody of the token. Renames, redenominations, contract multipliers and rebases need typed conversion events with exact before/after units, ratio, cash adjustments and source evidence. Treat unsupported conversions as blocking events.

Warmup is per market. A newly listed market cannot receive synthetic pre-listing bars, and stale marks must not be forward-filled across missing history or suspension without a declared valuation policy. Cross-sectional selection ranks only eligible markets with sufficient causal observations; removed markets remain in the historical evidence and realized results. Dynamic target changes share the existing economic book and declared risk/cost rules.

Captured portfolio evaluation must include the complete universe bundle, per-decision eligibility, lifecycle events and settlement evidence. Existing static studies keep their original semantics and cannot acquire a historical-universe label after an upgrade. Replay never reads today's instrument endpoint as a substitute.

## Rejection and acceptance cases

| Counterexample | Required outcome |
|---|---|
| Valid live BTC plus blank-rule preopen asset | Refresh retains both observations; BTC remains usable; preopen metadata is unavailable for orders |
| REST snapshot followed by one changed-instrument WS row | Unchanged members retained; stream row cannot replace the whole universe |
| Same economic rules reappear after a suspension | Three distinct temporal observations remain auditable |
| Future announcement or retrospectively imported current rule | Not eligible before supported known-at time; evidence scope explicit |
| Historical test starts before first observation | Unknown coverage; no claim of complete historical membership |
| Symbol disappears from a later current response | No fabricated delist/settlement event or automatic zero write-off |
| Delist with an open perpetual and missing settlement price | Explicit failed/unavailable result with the unresolved inventory preserved |
| Contract rename or ratio conversion during a position | Exact typed event or an explicit unsupported-event stop |
| Mutate source catalog after capture | Same pinned study/replay result and unchanged evidence identity |
| Restore a backup then revise lifecycle history | Prior captured runs and research-consumption facts remain unchanged |

## Delivery order

First fix current-refresh resilience while retaining complete raw observation evidence and truthful forward coverage. Then add attributed temporal imports, historical queries and visible coverage review. Finally bind dynamic membership/lifecycle events into the shared simulation and captured evaluation, with settlement and conversion counterexamples. None of these stages alone establishes comprehensive historical coverage; real source data must accompany the implemented contract.
