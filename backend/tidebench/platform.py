"""Workspace access control, measured operations and verified SQLite recovery.

Exchange credentials are deliberately outside this service's boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from threading import BoundedSemaphore, Lock, RLock

from .store import Store, dumps, new_id, now_ms

ROLES = {"admin", "trader", "researcher", "viewer", "risk_operator"}
COOKIE = "tidebench_session"
# https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt
SCRYPT_PARAMETERS = (32768, 8, 3)  # OWASP's 32 MiB equivalent minimum profile.
_KDF_SLOTS = BoundedSemaphore(2)
_BACKUP_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}")


class PlatformError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def password_hash(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise PlatformError("password_policy", "Use a password between 12 and 128 characters.", 422)
    salt = secrets.token_bytes(16)
    try:
        with _KDF_SLOTS:
            digest = hashlib.scrypt(
                password.encode(),
                salt=salt,
                n=SCRYPT_PARAMETERS[0],
                r=8,
                p=3,
                dklen=32,
                maxmem=64 * 1024 * 1024,
            )
    except UnicodeEncodeError:
        raise PlatformError("password_policy", "Password must be valid Unicode.", 422) from None
    return f"scrypt:32768:8:3:{salt.hex()}:{digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        if not isinstance(password, str) or not isinstance(encoded, str) or len(encoded) > 256:
            return False
        kind, n, r, p, salt, expected = encoded.split(":")
        # Accept the old application's profile for verification only. Never
        # allocate memory/CPU based on an arbitrary stored parameter string.
        parameters = (int(n), int(r), int(p))
        if (
            kind != "scrypt"
            or not 1 <= len(password) <= 128
            or parameters not in (SCRYPT_PARAMETERS, (16384, 8, 1))
            or not re.fullmatch(r"[a-f0-9]{32}", salt)
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
        ):
            return False
        with _KDF_SLOTS:
            digest = hashlib.scrypt(
                password.encode(),
                salt=bytes.fromhex(salt),
                n=parameters[0],
                r=parameters[1],
                p=parameters[2],
                dklen=32,
                maxmem=64 * 1024 * 1024,
            )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, UnicodeEncodeError, OverflowError):
        return False


def public_user(row):
    return {
        **{key: row[key] for key in ("id", "username", "display_name", "role", "created_at")},
        "enabled": bool(row["enabled"]),
    }


class AccessService:
    def __init__(self, store: Store, settings):
        self.store, self.settings = store, settings
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workspace_users(
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES workspace_users(id),
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON workspace_sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT NOT NULL, ts INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_key_ts ON auth_attempts(key, ts);
            """)
        self._dummy_hash = password_hash(secrets.token_urlsafe(24))

    def setup_required(self):
        with self.store.read() as conn:
            return not conn.execute("SELECT 1 FROM workspace_users LIMIT 1").fetchone()

    @staticmethod
    def csrf(token):
        return hmac.new(token.encode(), b"tidebench-csrf-v1", hashlib.sha256).hexdigest()

    def principal(self, request):
        authorization = request.headers.get("authorization", "")
        if self.settings.api_token and hmac.compare_digest(
            authorization.encode(), ("Bearer " + self.settings.api_token).encode()
        ):
            return {
                "id": "service",
                "username": "service",
                "display_name": "Service token",
                "role": "admin",
                "enabled": True,
            }, None
        if not self.settings.auth_enabled:
            return {
                "id": "local",
                "username": "local",
                "display_name": "Local operator",
                "role": "admin",
                "enabled": True,
            }, None
        token = request.cookies.get(COOKIE)
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None, None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = now_ms()
        with self.store.write() as conn:
            row = conn.execute(
                "SELECT u.*,s.expires_at,s.last_seen FROM workspace_sessions s JOIN workspace_users u ON u.id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if (
                not row
                or not row["enabled"]
                or row["expires_at"] <= now
                or row["last_seen"] <= now - self.settings.idle_minutes * 60000
                or row["last_seen"] > now + 5000
            ):
                conn.execute("DELETE FROM workspace_sessions WHERE token_hash=?", (token_hash,))
                return None, None
            if row["last_seen"] < now:
                conn.execute(
                    "UPDATE workspace_sessions SET last_seen=? WHERE token_hash=?", (now, token_hash)
                )
        return public_user(row), token

    def status(self, request):
        user, token = self.principal(request)
        return {
            "auth_required": self.settings.auth_enabled,
            "setup_required": self.setup_required() if self.settings.auth_enabled else False,
            "authenticated": user is not None,
            "user": user,
            "csrf_token": self.csrf(token) if token else None,
        }

    def setup(self, username, password, display_name, *, local, bootstrap):
        if not self.setup_required():
            raise PlatformError("setup_complete", "The workspace has already been initialized.", 409)
        configured = self.settings.bootstrap_token
        if (
            configured
            and (
                not isinstance(bootstrap, str)
                or not hmac.compare_digest(bootstrap.encode("utf-8"), configured.encode("utf-8"))
            )
        ) or (not configured and not local):
            raise PlatformError(
                "bootstrap_required",
                "Initial setup requires the configured bootstrap token; remote setup cannot proceed without one.",
                403,
            )
        return self.create_user(username, password, display_name, "admin", setup=True)

    def create_user(self, username, password, display_name, role, *, setup=False):
        if not isinstance(username, str) or not isinstance(display_name, str):
            raise PlatformError("username_policy", "Username and display name must be strings.", 422)
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", username):
            raise PlatformError(
                "username_policy",
                "Username must contain 3–40 letters, digits, dots, underscores or dashes.",
                422,
            )
        if role not in ROLES:
            raise PlatformError("invalid_role", "Unknown workspace role.", 422)
        encoded = password_hash(password)
        identifier = new_id()
        with self.store.write() as conn:
            if setup and conn.execute("SELECT 1 FROM workspace_users LIMIT 1").fetchone():
                raise PlatformError("setup_complete", "The workspace has already been initialized.", 409)
            try:
                conn.execute(
                    "INSERT INTO workspace_users VALUES(?,?,?,?,?,1,?)",
                    (identifier, username, display_name[:100] or username, encoded, role, now_ms()),
                )
            except sqlite3.IntegrityError:
                raise PlatformError("username_exists", "This username is already registered.", 409) from None
            self.store.audit(
                conn,
                "system",
                "auth.user_created",
                "Workspace user created",
                {"user_id": identifier, "role": role},
            )
        return self.user(identifier)

    def user(self, identifier):
        with self.store.read() as conn:
            row = conn.execute("SELECT * FROM workspace_users WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("not_found", "User not found.", 404)
        return public_user(row)

    def users(self):
        with self.store.read() as conn:
            return [
                public_user(row) for row in conn.execute("SELECT * FROM workspace_users ORDER BY created_at")
            ]

    def update_user(self, identifier, *, role, enabled):
        if role not in ROLES:
            raise PlatformError("invalid_role", "Unknown workspace role.", 422)
        if not isinstance(enabled, bool):
            raise PlatformError("invalid_enabled", "Enabled must be a boolean.", 422)
        with self.store.write() as conn:
            row = conn.execute("SELECT * FROM workspace_users WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise PlatformError("not_found", "User not found.", 404)
            if row["role"] == "admin" and row["enabled"] and (role != "admin" or not enabled):
                admins = conn.execute(
                    "SELECT COUNT(*) FROM workspace_users WHERE role='admin' AND enabled=1"
                ).fetchone()[0]
                if admins <= 1:
                    raise PlatformError("last_admin", "Keep at least one active administrator.", 409)
            conn.execute(
                "UPDATE workspace_users SET role=?,enabled=? WHERE id=?", (role, int(enabled), identifier)
            )
            conn.execute("DELETE FROM workspace_sessions WHERE user_id=?", (identifier,))
            self.store.audit(
                conn,
                "system",
                "auth.user_updated",
                "User access changed; sessions revoked",
                {"user_id": identifier, "role": role, "enabled": enabled},
            )
        return self.user(identifier)

    def login(self, username, password, address):
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 128
            or not isinstance(address, str)
            or len(address) > 256
        ):
            raise PlatformError("invalid_credentials", "Invalid username or password.", 401)
        now = now_ms()
        keys = ["ip:" + address, "user:" + hashlib.sha256(username.strip().lower().encode()).hexdigest()]
        with self.store.write() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE ts<=?", (now - 900000,))
            for key in keys:
                if conn.execute("SELECT COUNT(*) FROM auth_attempts WHERE key=?", (key,)).fetchone()[0] >= 10:
                    raise PlatformError(
                        "login_rate_limit", "Too many login attempts. Try again in 15 minutes.", 429
                    )
            attempts = [
                conn.execute("INSERT INTO auth_attempts VALUES(?,?)", (key, now)).lastrowid for key in keys
            ]
            row = conn.execute(
                "SELECT * FROM workspace_users WHERE username=?", (username.strip().lower(),)
            ).fetchone()
        if (
            not verify_password(password, row["password_hash"] if row else self._dummy_hash)
            or not row
            or not row["enabled"]
        ):
            raise PlatformError("invalid_credentials", "Invalid username or password.", 401)
        token = secrets.token_urlsafe(32)
        upgraded_hash = (
            password_hash(password) if row["password_hash"].startswith("scrypt:16384:8:1:") else None
        )
        with self.store.write() as conn:
            fresh = conn.execute("SELECT * FROM workspace_users WHERE id=?", (row["id"],)).fetchone()
            if not fresh or not fresh["enabled"] or fresh["password_hash"] != row["password_hash"]:
                raise PlatformError("invalid_credentials", "Invalid username or password.", 401)
            row = fresh
            if upgraded_hash:
                conn.execute(
                    "UPDATE workspace_users SET password_hash=? WHERE id=?", (upgraded_hash, row["id"])
                )
            now = now_ms()
            conn.execute(
                "DELETE FROM workspace_sessions WHERE expires_at<=? OR last_seen<=?",
                (now, now - self.settings.idle_minutes * 60000),
            )
            conn.execute(
                "DELETE FROM workspace_sessions WHERE token_hash IN (SELECT token_hash FROM workspace_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT -1 OFFSET 19)",
                (row["id"],),
            )
            conn.execute(
                "INSERT INTO workspace_sessions VALUES(?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    row["id"],
                    now,
                    now + self.settings.session_hours * 3600000,
                    now,
                ),
            )
            conn.execute("DELETE FROM auth_attempts WHERE key=?", (keys[1],))
            # Successful logins remove their own IP reservation; failed or
            # concurrent attempts from that address remain counted.
            conn.execute("DELETE FROM auth_attempts WHERE rowid=?", (attempts[0],))
            self.store.audit(conn, "system", "auth.login", "Workspace session opened", {"user_id": row["id"]})
        return token, public_user(row)

    def change_password(self, identifier, current_password, new_password):
        key, now = "password_change:" + identifier, now_ms()
        with self.store.write() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE ts<=?", (now - 900000,))
            if conn.execute("SELECT COUNT(*) FROM auth_attempts WHERE key=?", (key,)).fetchone()[0] >= 10:
                raise PlatformError(
                    "login_rate_limit",
                    "Too many password verification attempts. Try again in 15 minutes.",
                    429,
                )
            conn.execute("INSERT INTO auth_attempts VALUES(?,?)", (key, now))
            row = conn.execute("SELECT * FROM workspace_users WHERE id=?", (identifier,)).fetchone()
        if not row or not row["enabled"] or not verify_password(current_password, row["password_hash"]):
            raise PlatformError("invalid_credentials", "Current password is invalid.", 401)
        encoded = password_hash(new_password)
        with self.store.write() as conn:
            current = conn.execute(
                "SELECT password_hash,enabled FROM workspace_users WHERE id=?", (identifier,)
            ).fetchone()
            if not current or not current["enabled"] or current["password_hash"] != row["password_hash"]:
                raise PlatformError("invalid_credentials", "User access changed during this request.", 409)
            conn.execute("UPDATE workspace_users SET password_hash=? WHERE id=?", (encoded, identifier))
            conn.execute("DELETE FROM workspace_sessions WHERE user_id=?", (identifier,))
            conn.execute("DELETE FROM auth_attempts WHERE key=?", (key,))
            self.store.audit(
                conn,
                "system",
                "auth.password_changed",
                "Password changed; all user sessions revoked",
                {"user_id": identifier},
            )
        return {"user": self.user(identifier), "sessions_revoked": True}

    def reset_password(self, identifier, new_password, actor="admin"):
        """Admin authorization belongs to the API; no password enters audit data."""
        encoded = password_hash(new_password)
        with self.store.write() as conn:
            if not conn.execute("SELECT 1 FROM workspace_users WHERE id=?", (identifier,)).fetchone():
                raise PlatformError("not_found", "User not found.", 404)
            conn.execute("UPDATE workspace_users SET password_hash=? WHERE id=?", (encoded, identifier))
            conn.execute("DELETE FROM workspace_sessions WHERE user_id=?", (identifier,))
            conn.execute("DELETE FROM auth_attempts WHERE key=?", ("password_change:" + identifier,))
            self.store.audit(
                conn,
                "system",
                "auth.password_reset",
                "Administrator reset password; all user sessions revoked",
                {"user_id": identifier, "actor": actor},
            )
        return {"user": self.user(identifier), "sessions_revoked": True}

    def logout(self, token):
        if token:
            with self.store.write() as conn:
                conn.execute(
                    "DELETE FROM workspace_sessions WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                )


class RuntimeMetrics:
    def __init__(self):
        self.started = time.monotonic()
        self.lock = Lock()
        self.requests = defaultdict(int)
        self.duration = defaultdict(float)
        self.buckets = defaultdict(lambda: [0] * 7)
        self.bounds = (0.01, 0.05, 0.1, 0.5, 1, 5, float("inf"))

    def record(self, method, path, status, elapsed):
        method = (
            method
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
            else "OTHER"
        )
        key = (method, path, str(status))
        with self.lock:
            self.requests[key] += 1
            self.duration[key] += elapsed
            for i, bound in enumerate(self.bounds):
                self.buckets[key][i] += int(elapsed <= bound)

    def snapshot(self):
        with self.lock:
            return {
                "uptime_seconds": time.monotonic() - self.started,
                "requests": sum(self.requests.values()),
                "server_errors": sum(v for k, v in self.requests.items() if k[2].startswith("5")),
            }

    def prometheus(self):
        rows = [
            "# TYPE tidebench_http_requests_total counter",
            "# TYPE tidebench_http_request_seconds histogram",
        ]
        with self.lock:
            for (method, path, status), count in self.requests.items():
                labels = f"method={json.dumps(method)},route={json.dumps(path)},status={json.dumps(status)}"
                rows.append(f"tidebench_http_requests_total{{{labels}}} {count}")
                rows.append(f"tidebench_http_request_seconds_count{{{labels}}} {count}")
                rows.append(
                    f"tidebench_http_request_seconds_sum{{{labels}}} {self.duration[(method, path, status)]}"
                )
                for bound, value in zip(self.bounds, self.buckets[(method, path, status)], strict=True):
                    rows.append(
                        f'tidebench_http_request_seconds_bucket{{{labels},le="{bound if bound != float("inf") else "+Inf"}"}} {value}'
                    )
        rows.append(f"tidebench_uptime_seconds {time.monotonic() - self.started}")
        return "\n".join(rows) + "\n"


class BackupService:
    """Verified backups; restoration requires callers to drain all writers first.

    A staging database is revoked/halted *before* replacing workspace contents,
    so a crash cannot expose restored sessions or restart old simulated orders.
    """

    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.directory = settings.data_dir / "backups"
        if self.directory.is_symlink():
            raise PlatformError("unsafe_backup_path", "Backup directory must not be a symbolic link.", 500)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._lock = RLock()

    @staticmethod
    def _digest(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sync_file(path):
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    def _sync_directory(self):
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _paths(self, identifier):
        if not isinstance(identifier, str) or not _BACKUP_ID.fullmatch(identifier):
            raise PlatformError("invalid_backup", "Invalid backup identifier.", 422)
        paths = (self.directory / (identifier + ".sqlite3"), self.directory / (identifier + ".json"))
        if any(path.is_symlink() for path in paths):
            raise PlatformError("unsafe_backup_path", "Backup files must not be symbolic links.", 409)
        return paths

    def _manifest(self, identifier):
        path, metadata = self._paths(identifier)
        if not path.is_file() or not metadata.is_file():
            raise PlatformError("not_found", "Backup not found.", 404)
        try:
            if metadata.stat().st_size > 65536:
                raise ValueError
            manifest = json.loads(metadata.read_text())
            if (
                not isinstance(manifest, dict)
                or manifest.get("id") != identifier
                or not isinstance(manifest.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
                or type(manifest.get("size_bytes")) is not int
                or manifest["size_bytes"] <= 0
                or type(manifest.get("created_at")) is not int
                or type(manifest.get("database_schema")) is not int
                or manifest["database_schema"] not in (1, 2, 3, 4, 5)
            ):
                raise ValueError
        except (OSError, ValueError, TypeError, UnicodeError):
            raise PlatformError("invalid_backup_manifest", "Backup metadata is invalid.", 409) from None
        return manifest

    def list(self):
        items = []
        for path in self.directory.glob("*.json"):
            try:
                items.append(self._manifest(path.stem))
            except PlatformError:
                continue
        return sorted(items, key=lambda item: (item["created_at"], item["id"]), reverse=True)

    @staticmethod
    def _schema_version(conn):
        try:
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
        except sqlite3.Error:
            raise PlatformError("backup_schema", "Backup database schema is missing.", 409) from None
        if len(versions) != 1 or type(versions[0]) is not int or versions[0] not in (1, 2, 3, 4, 5):
            raise PlatformError("backup_schema", "Backup database schema is unsupported.", 409)
        return versions[0]

    @classmethod
    def _check_connection(cls, conn):
        if (
            conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or conn.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise PlatformError("backup_integrity", "Backup database integrity is invalid.", 409)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"schema_version", "accounts", "risk", "deployments", "audit"}.issubset(tables):
            raise PlatformError("backup_schema", "Backup is missing required workspace tables.", 409)
        version = cls._schema_version(conn)
        if version >= 2 and not {
            "workspace_users",
            "workspace_sessions",
            "pro_accounts",
            "pro_positions",
            "pro_orders",
            "pro_ledger",
            "pro_funding",
            "pro_strategy_intents",
            "pro_risk",
            "pro_deployments",
            "pro_runs",
            "catalog_jobs",
            "catalog_datasets",
            "catalog_records",
            "catalog_instruments",
            "catalog_watches",
            "catalog_settlement_marks",
            "schema_migrations",
        }.issubset(tables):
            raise PlatformError(
                "backup_schema", "Schema version 2 is missing professional workspace tables.", 409
            )
        if version >= 3 and not {
            "data_packages",
            "data_package_components",
            "data_package_funding_marks",
        }.issubset(tables):
            raise PlatformError("backup_schema", "Schema version 3 is missing research package tables.", 409)
        required_columns = {
            "accounts": {"source", "cash"},
            "risk": {"kill_switch"},
            "deployments": {"status"},
            "audit": {"source", "ts", "kind", "summary", "details"},
            "workspace_sessions": {"token_hash"},
            "pro_risk": {"halted", "updated_at"},
            "pro_deployments": {"status"},
            "pro_orders": {"id", "status", "body", "reservation", "updated_at"},
        }
        if version >= 2:
            required_columns.update(
                {
                    "pro_accounts": {"source", "cash", "debt", "realized", "fees", "funding"},
                    "pro_positions": {
                        "source",
                        "inst_id",
                        "quantity",
                        "basis",
                        "margin",
                        "metadata",
                        "funding_cursor",
                    },
                    "pro_ledger": {
                        "tx_id",
                        "source",
                        "ts",
                        "asset",
                        "account",
                        "debit",
                        "credit",
                        "reference",
                    },
                    "pro_funding": {"source", "inst_id", "ts", "body"},
                    "pro_strategy_intents": {"deployment_id", "bar", "target", "status", "updated_at"},
                    "catalog_settlement_marks": {"source", "region", "inst_id", "ts", "price", "observed_at"},
                }
            )
        if version >= 3:
            required_columns.update(
                {
                    "pro_runs": {"summary"},
                    "data_packages": {"id", "status", "manifest", "manifest_hash", "prepare_token"},
                    "data_package_components": {"package_id", "kind", "job_id", "dataset_id"},
                    "data_package_funding_marks": {"package_id", "ts", "body"},
                }
            )
        if version >= 4:
            lineage = {
                "research_artifacts": {"content_hash", "codec", "raw_bytes", "payload"},
                "research_holdouts": {
                    "id",
                    "project_id",
                    "version_id",
                    "source",
                    "inst_id",
                    "bar",
                    "start_ts",
                    "end_ts",
                    "plan",
                    "plan_hash",
                    "status",
                    "run_id",
                },
                "portfolio_runs": {"id", "source", "status", "config", "manifest", "result", "progress"},
                "simulation_clock": {"id", "market_ts", "wall_ts", "speed", "revision"},
                "forward_bars": {"source", "inst_id", "bar", "ts", "body", "content_hash", "dataset_id"},
                "forward_checkpoints": {"deployment_id", "last_bar", "identity", "state", "state_hash"},
                "forward_decisions": {"deployment_id", "bar", "body", "content_hash"},
                "forward_equity": {"id", "source", "market_ts", "ledger_sequence", "body", "state_hash"},
                "ops_incidents": {
                    "id",
                    "kind",
                    "subject",
                    "status",
                    "first_seen",
                    "last_seen",
                    "details",
                    "ack_actor",
                    "ack_reason",
                },
                "strategy_projects": {"id", "name", "created_by", "created_at"},
                "strategy_versions": {
                    "id",
                    "project_id",
                    "revision",
                    "definition",
                    "implementation",
                    "content_hash",
                },
                "paper_releases": {
                    "id",
                    "run_id",
                    "strategy_version_id",
                    "preview",
                    "config",
                    "approval_hash",
                    "status",
                    "deployment_id",
                },
            }
            if not set(lineage).issubset(tables):
                raise PlatformError(
                    "backup_schema", "Schema version 4 is missing strategy/release tables.", 409
                )
            required_columns.update(lineage)
        if version >= 5:
            managed = {
                "portfolio_projects": {"id", "name", "created_at"},
                "portfolio_versions": {
                    "id",
                    "project_id",
                    "revision",
                    "definition",
                    "implementation",
                    "content_hash",
                },
                "portfolio_releases": {
                    "id",
                    "source",
                    "run_id",
                    "version_id",
                    "approval",
                    "approval_hash",
                    "status",
                    "group_id",
                },
                "managed_portfolios": {
                    "id",
                    "source",
                    "version_id",
                    "release_id",
                    "manifest",
                    "manifest_hash",
                    "status",
                    "last_bar",
                    "anchor_bar",
                },
                "portfolio_batches": {
                    "id",
                    "group_id",
                    "bar",
                    "body",
                    "content_hash",
                    "status",
                    "additions",
                    "additions_hash",
                    "residuals",
                },
                "portfolio_commands": {
                    "id",
                    "batch_id",
                    "phase",
                    "sequence",
                    "key",
                    "payload",
                    "payload_hash",
                    "status",
                    "order_id",
                },
                "contribution_status": {"source", "reason", "first_seen", "last_seen"},
                "contribution_accounts": {"source", "baseline", "baseline_hash", "initialized_at"},
                "contribution_sleeves": {"source", "owner", "inst_id", "body", "content_hash"},
                "contribution_events": {"id", "source", "reference", "body", "content_hash"},
            }
            if not set(managed).issubset(tables):
                raise PlatformError(
                    "backup_schema",
                    "Schema version 5 is missing managed portfolio or contribution evidence.",
                    409,
                )
            required_columns.update(managed)
        for table, required in required_columns.items():
            if table in tables and not required.issubset(
                {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            ):
                raise PlatformError(
                    "backup_schema", "Backup table columns are incompatible with safe recovery.", 409
                )
        return tables

    @staticmethod
    def _readonly(path):
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)

    def _exclusive_temp(self, path):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)

    @staticmethod
    def _copy(source, destination):
        deadline = time.monotonic() + 60

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise PlatformError("backup_timeout", "Database copy exceeded its bounded deadline.", 503)

        source.backup(destination, pages=256, sleep=0.01, progress=progress)

    def create(self, reason="manual", *, protected_ids=()):
        with self._lock:
            return self._create(reason, protected_ids)

    def _create(self, reason, protected_ids):
        for identifier in protected_ids:
            self._paths(identifier)
        identifier = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{new_id()[:8]}"
        path, metadata = self._paths(identifier)
        temp, metadata_temp = path.with_suffix(".partial"), metadata.with_suffix(".metadata-partial")
        self._exclusive_temp(temp)
        try:
            with closing(self.store.connect()) as source, closing(sqlite3.connect(temp)) as destination:
                self._copy(source, destination)
                destination.execute("PRAGMA journal_mode=DELETE")
                self._check_connection(destination)
                schema_version = self._schema_version(destination)
            self._sync_file(temp)
            os.replace(temp, path)
            self._sync_directory()
            manifest = {
                "id": identifier,
                "created_at": now_ms(),
                "size_bytes": path.stat().st_size,
                "sha256": self._digest(path),
                "integrity": "ok",
                "reason": str(reason)[:256],
                "contains_private_workspace_data": True,
                "database_schema": schema_version,
            }
            self._exclusive_temp(metadata_temp)
            with metadata_temp.open("w") as stream:
                stream.write(dumps(manifest))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(metadata_temp, metadata)
            self._sync_directory()
        except BaseException:
            temp.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)
            if not metadata.exists():
                path.unlink(missing_ok=True)
            raise
        with self.store.write() as conn:
            self.store.audit(
                conn,
                "system",
                "backup.created",
                "Verified workspace backup created",
                {"backup_id": identifier},
            )
        protected = set(protected_ids) | {identifier}
        keep = {item["id"] for item in self.list()[: self.settings.backup_retention]} | protected
        for old in self.list():
            if old["id"] not in keep:
                old_path, old_metadata = self._paths(old["id"])
                old_metadata.unlink(missing_ok=True)
                old_path.unlink(missing_ok=True)
        self._sync_directory()
        return manifest

    def verify(self, identifier):
        with self._lock:
            manifest = self._manifest(identifier)
            path, _ = self._paths(identifier)
            if path.stat().st_size != manifest["size_bytes"] or not hmac.compare_digest(
                self._digest(path), manifest["sha256"]
            ):
                raise PlatformError("backup_hash_mismatch", "Backup hash mismatch; restore is blocked.", 409)
            try:
                with closing(self._readonly(path)) as conn:
                    tables = self._check_connection(conn)
                    if self._schema_version(conn) != manifest["database_schema"]:
                        raise PlatformError(
                            "backup_schema", "Backup manifest and database schema versions disagree.", 409
                        )
                    counts = {
                        name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                        for name in tables
                        if re.fullmatch(r"[a-z_]+", name)
                    }
            except sqlite3.Error:
                raise PlatformError(
                    "backup_integrity", "Backup database cannot be read safely.", 409
                ) from None
            return {
                **manifest,
                "verified": True,
                "tables": counts,
                "restore_effect": "Replace workspace state; invalidate cookie sessions; halt simulated execution; cancel pending simulation orders.",
            }

    def restore(self, identifier):
        # Caller must stop supervisors and drain every in-flight mutation first.
        with self._lock:
            manifest = self.verify(identifier)
            with closing(self.store.connect()) as current:
                if self._schema_version(current) != manifest["database_schema"]:
                    raise PlatformError(
                        "restore_schema_mismatch",
                        "Restore requires the same database schema as the running application. Use the matching application tag and its pre-upgrade backup for rollback.",
                        409,
                    )
            safety = self.create("before_restore", protected_ids=(identifier,))
            path, _ = self._paths(identifier)
            # Recheck after retention/safety creation, before trusting contents.
            self.verify(identifier)
            stage = self.directory / (new_id() + ".restore-partial")
            self._exclusive_temp(stage)
            try:
                with (
                    closing(self._readonly(path)) as source,
                    closing(sqlite3.connect(stage, isolation_level=None)) as prepared,
                ):
                    self._copy(source, prepared)
                    prepared.execute("PRAGMA journal_mode=DELETE")
                    prepared.execute("PRAGMA synchronous=FULL")
                    tables = self._check_connection(prepared)
                    prepared.execute("BEGIN IMMEDIATE")
                    try:
                        if "workspace_sessions" in tables:
                            prepared.execute("DELETE FROM workspace_sessions")
                        prepared.execute("UPDATE risk SET kill_switch=1")
                        prepared.execute("UPDATE deployments SET status='stopped'")
                        if "pro_deployments" in tables:
                            prepared.execute("UPDATE pro_deployments SET status='stopped'")
                        if "managed_portfolios" in tables:
                            prepared.execute(
                                "UPDATE managed_portfolios SET status='stopped',updated_at=?", (now_ms(),)
                            )
                            prepared.execute(
                                "UPDATE portfolio_batches SET status='canceled',error='Workspace restored; portfolio stopped and inventory retained',updated_at=? WHERE status IN ('reducing','adding','compensating')",
                                (now_ms(),),
                            )
                            prepared.execute(
                                "UPDATE portfolio_commands SET status='canceled',updated_at=? WHERE status='pending'",
                                (now_ms(),),
                            )
                        if "simulation_clock" in tables:
                            prepared.execute(
                                "UPDATE simulation_clock SET speed=0,wall_ts=?,revision=revision+1 WHERE id=1",
                                (now_ms(),),
                            )
                        if "pro_risk" in tables:
                            prepared.execute("UPDATE pro_risk SET halted=1,updated_at=?", (now_ms(),))
                        if "pro_orders" in tables:
                            pending = prepared.execute(
                                "SELECT id,body FROM pro_orders WHERE status='pending'"
                            ).fetchall()
                            for order_id, body_text in pending:
                                body = json.loads(body_text)
                                body.update(
                                    status="canceled",
                                    updated_at=now_ms(),
                                    cancellation_reason="workspace_restored",
                                )
                                prepared.execute(
                                    "UPDATE pro_orders SET status='canceled',reservation='0',body=?,updated_at=? WHERE id=?",
                                    (dumps(body), now_ms(), order_id),
                                )
                        self.store.audit(
                            prepared,
                            "system",
                            "backup.restored",
                            "Workspace restored; sessions revoked and execution halted",
                            {"backup_id": identifier, "safety_backup_id": safety["id"]},
                        )
                        prepared.commit()
                    except BaseException:
                        prepared.rollback()
                        raise
                    self._check_connection(prepared)
                self._sync_file(stage)
                with closing(self._readonly(stage)) as source, closing(self.store.connect()) as destination:
                    self._copy(source, destination)
                    self._check_connection(destination)
            except sqlite3.Error:
                raise PlatformError(
                    "restore_failed",
                    "SQLite restore failed; preserve the safety backup and inspect storage diagnostics.",
                    500,
                ) from None
            finally:
                stage.unlink(missing_ok=True)
                self._sync_directory()
            return {
                "restored": identifier,
                "safety_backup_id": safety["id"],
                "sessions_revoked": True,
                "execution_halted": True,
                "pending_orders_canceled": True,
                "manifest": manifest,
            }

    def storage(self):
        free = shutil.disk_usage(self.settings.data_dir)
        wal = self.store.path.with_name(self.store.path.name + "-wal")
        return {
            "database_bytes": self.store.path.stat().st_size,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
            "disk_free_bytes": free.free,
            "backup_count": len(self.list()),
            "backup_retention": self.settings.backup_retention,
        }
