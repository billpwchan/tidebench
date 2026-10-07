# Security policy

Tidebench 0.1 is a developer preview for one local operator. It is not a hardened multi-tenant service and does not submit exchange orders. Do not expose an unauthenticated instance to the internet.

The supported entry point binds to loopback by default. Binding beyond loopback requires an access token of at least 32 characters. Configure exact allowed hosts and origins, terminate TLS at a trusted reverse proxy and keep the service on a private network. Bearer access is not a replacement for production OIDC/RBAC or tenant isolation.

Exchange API keys, secrets and passphrases are not accepted by this version. Public data needs no key. The Settings access token is for the **Tidebench workspace**, not an exchange credential. Store it in an ignored local `.env`, and enter it only into the local workspace Settings form. Browser storage is session-only; API requests never put tokens in URLs.

Back up the entire data directory with the process stopped, or use SQLite's online backup API. Do not copy only the database file while WAL writes are active. Backups and local JSON research exports may contain data subject to exchange terms. There is no implemented retention/export access policy for multiple users.

## Reporting

For a suspected credential exposure or exploitable issue, use the repository's **Security → Report a vulnerability** when private reporting is available. If unavailable, contact the maintainer through their public GitHub profile without posting exploit details or secrets. Never put credentials or private account data in a public issue.

No response-time SLA or third-party security certification is claimed. Only the current development release is supported.
