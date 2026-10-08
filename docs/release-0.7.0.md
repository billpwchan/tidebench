# Tidebench 0.7.0 — immutable instrument observations

A newly announced instrument with blank rules could previously fail a healthy market refresh. Repeated rules also overwrote the latest observation time, losing A → suspension → A chronology. This release preserves every full REST data-array observation before deriving supported instruments, quarantines unavailable rows and binds current metadata to its actual observation.

- **Data library → Instrument evidence** adds capture, raw inspection, exact JSON export, prior-response comparison and UTC review with an explicit maximum observation age. Desktop and mobile layouts are bilingual.
- Before the first observation and after the selected age, coverage stays unknown. Announced listing/expiry times do not become retrospective knowledge. Omission blocks old cached rules without inventing a delisting or settlement; known expiry also blocks an otherwise fresh cache.
- Nanosecond receipt ordering is represented as a JSON string to preserve exported hashes in browsers. Raw rows, parser identity and unsupported or malformed members remain auditable.
- Schema 7 verified recovery retains newer immutable observations before replacing financial state. Corrupt or conflicting observation identities fail before replacement. Existing one-use research facts retain their schema-6 recovery controls.
- Local validation passed 684 backend regressions and 20 desktop/mobile Chromium workflows, including actual capture/export/time review and recovery conflicts.
- Real public acceptance retained 1,144 spot and 500 perpetual rows, verified exact persistence/export hashes and BTC metadata provenance, and rejected earlier-time coverage. Source responses remain local; the repository publishes only counts/hashes.

See the [instrument evidence guide](instrument-evidence.md), [self-audit](audit/red-team-0.7.0.zh-CN.md) and [verification record](verification.md). This establishes forward observation evidence. Comprehensive historical membership, dynamic lifecycle conversions, order-book execution capacity and prolonged external operations still require separate implementation and evidence. Exchange execution remains local paper simulation.

## Upgrade

Create and verify a schema-6 backup with v0.6. Stop its single writer, deploy v0.7, and verify schema 7, readiness and unchanged financial/user records. Capture and verify a new schema-7 backup. An in-app restore requires matching schema; code rollback uses the preserved schema-6 copy, not the upgraded database. New observations are forward evidence: pre-upgrade mutable metadata is not relabeled as an immutable historical snapshot.
