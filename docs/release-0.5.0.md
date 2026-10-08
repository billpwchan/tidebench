# Tidebench 0.5.0 — managed portfolios and reconciled contribution P&L

A historical portfolio can now become a reviewed multi-market paper deployment without re-entering its definition. Immutable versions bind the hypothesis, markets, sizing, construction, exits and implementation to a completed study. Approval exposes execution-cost/risk differences, sequential-leg risk and current blockers; activation revalidates the entire group atomically.

- Managed 2–10 market spot/perpetual groups, with shared-account capital sizing, causal signals and exits, reduce-first execution and a frozen common cash scale for additions.
- Durable target batches and immutable child commands. Hard interruption reconciles committed orders before continuing; fresh quotes price unfinished commands without changing the captured quantities.
- Residual limits, bounded order splitting, group-wide stops and persistent reduce-only compensation. A failed compensation retains actual inventory and the cause; stopping retains filled positions.
- Transactional monetary attribution for portfolios, single strategies, manual actors and explicit pre-upgrade legacy history. Quantity, entry cost, realized P&L, fees and funding reconcile to the net book. No independent strategy return curve is invented.
- Contribution quarantine protects reductions and actual settlement when auxiliary ownership evidence is corrupt. New risk halts, the original evidence is retained and attribution becomes explicitly unavailable.
- Four editable portfolio research starting points with explicit benchmark/rejection plans and chronological-test defaults; responsive managed-portfolio and contribution workspaces, linked orders/events, paginated evidence and displayed-data exports.
- Schema 5 recovery preserves group, command and contribution evidence while stopping execution, canceling unfinished commands, halting risk, revoking sessions and pausing synthetic time.

The [managed portfolio guide](managed-portfolios.md) describes sizing, ownership, failure handling and operator actions. The [self-audit](audit/red-team-0.5.0.zh-CN.md) includes the mixed-owner Decimal precision issue, protective-reduction quarantine and remaining substantive work.

## Captured acceptance evidence

The [standalone observation artifact](audit/v0.5.0-managed-observations.json) records a child-process exit after one committed fill and recovery to four unique fills with unchanged target/addition hashes; an actual schema-5 SQLite restore with evidence preserved; and a rejected leg whose initially blocked compensation later flattens the inventory. The source is synthetic and the workspace temporary. Test counts, environment and broader verification belong to the [verification record](verification.md); no CI or production-uptime conclusion is inferred from this artifact.

## Upgrade from 0.4

1. With v0.4 still installed, create and verify a backup of the schema-4 workspace. Keep a separately protected copy and the matching v0.4.0 source/image.
2. Stop the old process. Deploy v0.5 and start exactly one process against the existing data directory. Schema 5 adds portfolio version/release/batch/command and contribution tables; existing financial records remain authoritative.
3. Inspect readiness, accounts, positions, orders and legacy attribution. Complete the synthetic version→study→approval→group→contribution workflow in a disposable workspace before using the operational workspace.
4. Create and verify a new schema-5 backup. In-app restore requires the running schema; a schema-4 artifact is a rollback input for matching v0.4 code, not an in-app v0.5 restore target.

For rollback, stop v0.5 and preserve its database/backups. Run v0.4.0 against a separate copy of the verified pre-upgrade schema-4 database. Do not point older code at schema 5 or edit manifests to bypass validation. Restoring any current workspace deliberately leaves execution stopped and risk halted; it never automatically resumes an old multi-leg batch.

## Execution and evidence scope

Execution remains local simulation on public data; no exchange credentials or venue order adapter are added. Portfolio-specific one-use holdouts/trial governance, attributed historical universes and contract lifecycle events, order-book capacity models and long elapsed-time soak evidence remain separate implementation/acceptance work. These limits are documented rather than covered by an industry-leading or complete commercial-readiness claim.
