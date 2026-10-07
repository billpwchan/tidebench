# Security policy

Tidebench supports authenticated users in one shared research and local simulation workspace. It sends no exchange orders and accepts no exchange credentials. Keep one application process per database. The current tagged release is supported; no response-time SLA or external security certification is claimed.

Password authentication is enabled by default. Remote initialization requires the configured bootstrap token; remote binding requires enabled authentication and a sufficiently long bootstrap/service token. Configure exact allowed hosts/origins, terminate TLS at a trusted proxy, enable secure cookies and keep the backend private. See [operations](docs/operations.md) for roles, session expiry, password rotation and recovery.

Sessions use random secrets in HTTP-only SameSite=Strict cookies, server-side token hashes, expiry, revocation and CSRF checks. Passwords use salted, bounded scrypt. The optional API token grants administrator privileges to automation; protect and rotate it accordingly. The browser's optional service token is session-only and is never included in URLs. Workspace roles do not provide tenant isolation.

Protect the private data directory, audit logs, backups and exports. Online backups are integrity/hash checked and published with private file permissions. Recovery prepares a revoked, halted image before replacing state. Local backups should also be copied to separately protected storage. Do not copy only a live SQLite database while ignoring WAL files.

## Reporting

Use **Security → Report a vulnerability** in the repository when private reporting is available. Otherwise contact the maintainer through the public GitHub profile without posting secrets, exploit details or private account data in public issues.
