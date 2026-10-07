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
3. The server enters maintenance, drains active HTTP work, stops both legacy and professional workers and waits for tracked computation threads.
4. It creates a safety backup. Before replacement, a separate verified recovery image revokes all sessions, stops strategies, cancels pending simulated orders and halts accounts.
5. It restores that safe image, restarts workers and leaves execution halted. Sign in again, inspect balances, ledger, datasets and pending work, then resume risk deliberately.

The safety backup and selected target are protected from retention during this operation. Recovery is tested by modifying actual SQLite state and restoring it; it is not a file-copy-only health assertion.

Disconnecting the browser does not interrupt an in-progress database replacement or release maintenance early. Execution halts and strategy stops are persisted before workers stop. If stopping workers, copying the recovery image or restarting workers fails, the process remains in maintenance with HTTP 503. Preserve the logs and backup files, resolve the reported failure, then restart exactly one application process. Inspect the recovered state and verify readiness before deliberately resuming execution; a restart does not clear the persisted halt.

## Monitoring and incident response

- `/healthz`: database/process liveness. `/readyz`: database and expected supervisor tasks; HTTP 503 on maintenance or failed readiness.
- **Operations**: measured database/WAL/disk sizes, backup age, worker health, feed timestamps, errors, job checkpoints and audit records.
- `/api/v1/pro/ops/metrics`: administrator-authenticated Prometheus counters, request latency histogram and uptime. Use a private service token in your scraper configuration.
- Logs include operational failures and request IDs; API validation errors omit submitted credentials. Keep logs and backups private.

For stale prices, new risk is rejected; investigate the independent trade/mark/funding timestamps and regional endpoint reachability. For storage failures, do not remove the database or WAL; halt activity, preserve the files and inspect disk space and integrity. For unexplained account drift, retain the native asset journal and order identities, halt increasing risk and restore only after determining which evidence is authoritative.

The default daily-loss anchor is the first complete observed valuation in each UTC day, not an invented midnight price. Polling and network failure can delay observation; the guard is checked again inside every fill transaction.

## Upgrade and rollback

Take and verify a backup before changing versions. The app factory acquires the canonical database lease before any database initialization or migration; importing its module has no database side effects. File symlink aliases share that lease, and database hard links are unsupported. When launching Uvicorn directly, use `uvicorn tidebench.main:create_app --factory --workers 1`. Stop the application, deploy the new image/source, start exactly one instance, check readiness and perform the synthetic acceptance workflow. New modules add tables without rewriting legacy records. The professional portfolio is the authoritative account for new `/pro/execution` commands; v0.1 `/paper` records remain in their legacy ledger for compatibility and are not silently combined with new capital. Export historical records before changing operational workflows.

Version 0.3 upgrades a schema-2 workspace additively to schema 3 with package tables and run-summary storage, preserving balances and existing research. Backups declare the actual database schema. In-app restore requires a matching current schema; keep pre-upgrade backups with the prior application version for rollback. Never edit a manifest schema number to bypass this check.

For rollback, stop the new application and preserve its database and backups. Restore the verified pre-upgrade database to a separate data directory with the prior tagged application. Do not point older code at a database with unreviewed schema changes. An external reverse proxy, off-host backups, host patching and a hosting account are deployment resources, not artifacts supplied by this repository.
