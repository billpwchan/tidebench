# Operating Tidebench

This runbook covers one shared workspace: authenticated users, public OKX data, historical research and local forward simulation. Use one application process and one database writer. The built application and Compose deployment use the same controls and persistent services.

## Access and first setup

The default enables password authentication. Direct loopback setup can create the first administrator without a bootstrap token. Docker and other non-loopback connections require `TIDEBENCH_BOOTSTRAP_TOKEN`, a private random value of at least 32 characters. Remote binding refuses disabled authentication or the absence of a sufficiently long bootstrap/service token. Remove the bootstrap value after setup if it is no longer needed; first-user setup cannot run again on an initialized database.

Passwords use salted scrypt with the OWASP 32 MiB profile (`N=32768, r=8, p=3`). KDF concurrency is bounded. Sessions are random bearer secrets held in HTTP-only, SameSite=Strict cookies; only SHA-256 token digests are stored. Absolute expiry defaults to 12 hours and idle expiry to 30 minutes. Role/enable changes, password changes and recovery revoke affected sessions. CSRF checks apply to cookie-authenticated mutations. Login throttling survives process restarts.

| Role | Read workspace | Research / downloads | Trading commands | Risk changes | Users / recovery |
|---|---|---|---|---|---|
| admin | yes | yes | yes | yes | yes |
| trader | yes | yes | yes | yes | no |
| researcher | yes | yes | no | no | no |
| risk_operator | yes | no | no | yes | no |
| viewer | yes | no | no | no | no |

All users share the same accounts and research library. These roles are workspace permissions, not tenant isolation. An optional `TIDEBENCH_API_TOKEN` grants administrator access to automation and must be protected accordingly. It bypasses cookie CSRF checks; do not put it in a URL or repository. This is a workspace token, never an exchange key.

## HTTPS deployment

1. Install the tagged source or build the Docker image. Keep the database on local durable storage; SQLite WAL is not intended for shared network filesystems.
2. Set `TIDEBENCH_AUTH_ENABLED=true`, a random bootstrap token, exact `TIDEBENCH_ALLOWED_HOSTS` and comma-separated `TIDEBENCH_ALLOWED_ORIGINS` for the actual public HTTPS origin.
3. Terminate TLS at your reverse proxy, forward to the private application port and set `TIDEBENCH_COOKIE_SECURE=true`. Keep the backend unreachable from the public network. The application does not trust forwarded client-IP headers.
4. Create the administrator, add the required users and test each role. Rotate/remove bootstrap credentials, protect the data directory and configure an independent backup copy.
5. Monitor readiness, disk space, worker health, feed freshness and backup age. Do not run multiple Uvicorn workers or replicas on the same database. The process lease rejects a second application owner.

The supplied Compose configuration binds only `127.0.0.1:8080`, drops Linux capabilities and uses a persistent named volume. The image runs as a non-root user. Plain HTTP is for local access; secure cookies require HTTPS.

## Data and strategy operation

Download jobs persist their cursor, pages, records and worker token with each accepted page. Restart requeues unfinished jobs; canceled jobs cannot finalize through an old worker token. Dataset versions are immutable and hash their identity, rules, quality, transport, attribution and records. Gaps remain explicit. Do not distribute fetched data merely because the application code is MIT licensed.

Research packages group the required versions and settlement marks into one durable operation. They remain blocked or failed when coverage, metadata or marks cannot be verified. Retry a failed package explicitly; create a new version with attributed replacement datasets for a quality blocker. A ready package is immutable. Custom portfolio scenarios are read-only and available to every authenticated role, while download/research permissions remain restricted.

Forward strategies use the latest confirmed bar and a later observed bid/ask. They do not replay missed historical trades after downtime. A durable signal intent and separate command identities recover close/open reversals without repeating economic fills. Strategy ownership and current risk are rechecked inside the fill transaction; stopping a strategy retains its positions. Halting blocks increasing exposure and permits reducing positions.

Funding reconciliation uses realized historical events and observed historical settlement marks, with approximation labels. Expected settlements that have not appeared in history block position changes. Missing history beyond venue retention also blocks reconciliation rather than being skipped. A restored or long-idle perpetual position may therefore need an attributed historical reconciliation process before it can continue; never delete its cursor or invent zero funding to bypass this condition.

## Backups and recovery

Daily backups and manual **Operations → Backups → Create backup** use SQLite's online backup API. Each artifact has an integrity/FK check, streamed SHA-256 checksum, schema manifest and private file permissions. Files and manifests are flushed and atomically published. Retention defaults to 14 backups and can be configured from 2 to 90. These local backups protect recovery, not loss of the entire host; copy them to separately protected storage.

To restore:

1. Select **Verify backup**. A missing artifact, checksum mismatch, invalid schema or failed integrity check blocks recovery.
2. Select **Restore backup** and explicitly enter the required confirmation in the UI.
3. The server enters maintenance, drains active HTTP work, stops both legacy and professional workers and cancels/reaps owned research processes and drains tracked storage work.
4. It creates a safety backup. Before replacement, a separate verified recovery image revokes all sessions, stops strategies and managed groups, cancels pending simulated orders and unfinished group batches/commands, halts accounts and pauses synthetic time. Actual inventory, target/command evidence and contribution ownership are preserved.
5. It restores that safe image, restarts workers and leaves execution halted. Sign in again and inspect balances, ledger, datasets, groups, commands, contribution status and retained inventory. A restored group never resumes its unfinished batch automatically; manage exposure explicitly and review a new release when appropriate.

The safety backup and selected target are protected from retention during this operation. Recovery is tested by modifying actual SQLite state and restoring it; it is not a file-copy-only health assertion.

Disconnecting the browser does not interrupt an in-progress database replacement or release maintenance early. Execution halts and strategy stops are persisted before workers stop. If stopping workers, copying the recovery image or restarting workers fails, the process remains in maintenance with HTTP 503. Preserve the logs and backup files, resolve the reported failure, then restart exactly one application process. Inspect the recovered state and verify readiness before deliberately resuming execution; a restart does not clear the persisted halt.

## Monitoring and incident response

- `/healthz`: database/process liveness. `/readyz`: database and expected supervisors with recent progress; HTTP 503 on maintenance or failed readiness.
- **Operations**: measured database/WAL/disk sizes, backup age, worker health, feed timestamps, errors, job checkpoints and audit records.
- `/api/v1/pro/ops/metrics`: administrator-authenticated Prometheus counters, request latency histogram and uptime. Use a private service token in your scraper configuration.
- Logs include operational failures and request IDs; API validation errors omit submitted credentials. Keep logs and backups private.

For stale prices, new risk is rejected; investigate the independent trade/mark/funding timestamps and regional endpoint reachability. For storage failures, do not remove the database or WAL; halt activity, preserve the files and inspect disk space and integrity. For unexplained account drift, retain the native asset journal and order identities, halt increasing risk and restore only after determining which evidence is authoritative.

The default daily-loss anchor is the first complete observed valuation in each UTC day, not an invented midnight price. Polling and network failure can delay observation; the guard is checked again inside every fill transaction.

## Upgrade and rollback

Take and verify a backup before changing versions. The app factory acquires the canonical database lease before any database initialization or migration; importing its module has no database side effects. File symlink aliases share that lease, and database hard links are unsupported. When launching Uvicorn directly, use `uvicorn tidebench.main:create_app --factory --workers 1`. Stop the application, deploy the new image/source, start exactly one instance, check readiness and perform the synthetic acceptance workflow. New modules add tables without rewriting legacy records. The professional portfolio is the authoritative account for new `/pro/execution` commands; v0.1 `/paper` records remain in their legacy ledger for compatibility and are not silently combined with new capital. Export historical records before changing operational workflows.

Version 0.3 upgrades a schema-2 workspace additively to schema 3 with package tables and run-summary storage, preserving balances and existing research. Backups declare the actual database schema. In-app restore requires a matching current schema; keep pre-upgrade backups with the prior application version for rollback. Never edit a manifest schema number to bypass this check.

For rollback, stop the new application and preserve its database and backups. Restore the verified pre-upgrade database to a separate data directory with the prior tagged application. Do not point older code at a database with unreviewed schema changes. An external reverse proxy, off-host backups, host patching and a hosting account are deployment resources, not artifacts supplied by this repository.

## v0.4 process limits and schema 4

`TIDEBENCH_RESEARCH_PROCESS_ISOLATION=true` is the default. Compute deadline defaults to 900 seconds and the additional Linux virtual-address budget to 256 MiB above initialized input memory. Limits enter the run manifest; this is not total RSS enforcement. Configure OS/container memory/CPU limits. Explicitly disabling isolation loses process deadline enforcement. Maintenance cancellation leaves resumable work queued; a normal deadline fails the study without fabricated results.

Incidents retain feed/strategy errors, stopped/stalled workers, backup failure/age and low free disk (256 MiB). Administrator acknowledgement records a response without hiding failure. Recovery resolves it; recurrence reopens it. No external messages are sent. Connect private metrics/logs to an actual operator alert destination when hosting.

Schema 4 adds registry/releases, governance, portfolio runs, compressed artifacts, forward state/equity, time and incidents. Real restore acceptance preserves this evidence while leaving execution stopped/halted and time paused. Keep a verified schema-3 database with v0.3.1 for rollback; in-app restore rejects cross-schema replacement.

## Managed portfolio operation

The whole-group workflow is described in [Managed portfolios](managed-portfolios.md). Before activation, every leg must be flat, without pending orders or another running owner. Approval captures the exact version/result and current costs/risk policy; a changed condition requires a fresh review. Capital percentage sizes targets from complete account equity and does not isolate cash from other activity.

Inspect the latest frozen target, common cash scale, actual child fills and residual before treating a batch as successful. A missing leg's decision/history prevents a complete new target. An unfinished batch is reconciled before a newer bar is considered, using immutable command keys and current observed prices for unfilled commands.

A leg failure enters `compensating`, cancels outstanding additions and attempts reduce-only orders. If compensation is blocked, the actual inventory and error persist; investigate the market quote, funding or storage problem. A successful retry marks the batch `compensated` and group `failed`. It does not refund fees or start a new strategy cycle.

**Stop group** stops every leg and cancels unfinished commands, retaining filled positions. Stopping an individual managed leg has the same group-wide effect. If automatic compensation should cease, stop the group, inspect its inventory and use explicit reduce-only orders. New deployment requires a fresh approval and flat/unowned legs; database status edits and silent inventory adoption are unsupported.

## Contribution quarantine

The contribution report is monetary attribution under one economic net book. Retained closed-owner history currently participates in each economic reconciliation; monitor command latency and storage growth on long-lived workspaces rather than assuming short-run results are a capacity guarantee. It is not an independently funded strategy account. An upgrade captures unowned historical balances and positions as explicit legacy evidence.

If contribution integrity or reconciliation fails, new risk is blocked. Eligible protective reductions, funding settlement and liquidation still complete their economic transactions; auxiliary changes roll back to their savepoint, original ownership records remain intact, and hash-bound quarantine events retain the actual economic evidence. The source account is halted and a persistent operational condition identifies the quarantine. Contribution reports fail explicitly with `contribution_quarantined` rather than displaying a false reconciliation.

1. Preserve the database/WAL, relevant backup, order/journal identities, contribution events and incident. Do not delete ownership records or use a raw SQL balance reset.
2. Inspect actual economic exposure. Use eligible reduce-only protection if necessary; a contribution report failure does not itself mean the economic ledger failed.
3. Determine the authoritative financial and ownership evidence before a recovery. Restore only a verified suitable backup using the normal safe procedure, or perform a separately reviewed repair with retained before/after evidence. The product has no automatic attribution repair or quarantine-clear endpoint.
4. Recheck account/journal/contribution integrity and current prices/funding before deliberately permitting new risk. Removing the ordinary risk halt alone does not bypass persistent attribution quarantine.

## v0.5 upgrade and schema 5

Schema 5 adds immutable portfolio projects/versions/releases, managed groups, target batches, immutable command payloads, contribution baselines/sleeves/events and persistent quarantine state. Migration preserves existing economic records. Historical ownership without evidence remains `legacy`; it is not guessed from prior deployment names.

Before replacing v0.4, create and verify a schema-4 backup and keep the matching v0.4.0 source/image. Stop the old owner, install v0.5 and start exactly one process against the operational data directory. Inspect readiness, account balances, inventory, pending orders, version/release evidence and legacy attribution; create and verify a new schema-5 backup after acceptance.

A schema-5 restore validates required portfolio/contribution evidence, preserves its captured commands and owners, stops groups, cancels unfinished batches/commands, revokes sessions, halts accounts and pauses the clock. Existing quarantine state is retained. In-app restore requires a matching schema; a schema-4 backup remains usable with matching v0.4 code in a separate rollback data directory. Never relabel a backup or point v0.4 at schema 5. See the [release upgrade procedure](release-0.5.0.md#upgrade-from-04).
