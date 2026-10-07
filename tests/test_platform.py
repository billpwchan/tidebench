import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import tidebench.platform as platform
from tidebench.config import Settings
from tidebench.platform import (
    COOKIE,
    AccessService,
    BackupService,
    PlatformError,
    RuntimeMetrics,
    password_hash,
    verify_password,
)
from tidebench.store import Store, dumps

PASSWORD = "a strong workspace password"
NEXT_PASSWORD = "a different strong password"


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        api_token="",
        bootstrap_token="remote-bootstrap",
        backup_retention=2,
    )
    store = Store(settings.database)
    return store, settings


@pytest.fixture
def access(workspace):
    store, settings = workspace
    service = AccessService(store, settings)
    admin = service.setup("admin", PASSWORD, "Administrator", local=True, bootstrap="remote-bootstrap")
    return service, admin


def request(token=None, authorization=""):
    return SimpleNamespace(cookies={COOKIE: token} if token else {}, headers={"authorization": authorization})


def test_password_hash_unique_salt_bounded_parameters_and_real_verification(monkeypatch):
    first, second = password_hash(PASSWORD), password_hash(PASSWORD)
    assert first != second and first.startswith("scrypt:32768:8:3:")
    assert verify_password(PASSWORD, first) and not verify_password("wrong", first)

    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted KDF parameters reached scrypt")

    monkeypatch.setattr(platform.hashlib, "scrypt", forbidden)
    for malformed in (
        first.replace(":32768:", ":1073741824:"),
        first.replace(":8:3:", ":99999:3:"),
        "scrypt:bad",
        first + "garbage",
    ):
        assert not verify_password(PASSWORD, malformed)


@pytest.mark.parametrize("password", ["short", "x" * 129, None, "\ud800" * 12])
def test_password_policy_rejects_invalid_input(password):
    with pytest.raises(PlatformError):
        password_hash(password)


def test_setup_remote_token_one_time_and_public_users(workspace):
    store, settings = workspace
    access = AccessService(store, settings)
    with pytest.raises(PlatformError) as denied:
        access.setup("admin", PASSWORD, "Admin", local=False, bootstrap="wrong")
    assert denied.value.status == 403
    # A reverse proxy that appears local must not bypass an explicitly set secret.
    with pytest.raises(PlatformError, match="bootstrap"):
        access.setup("admin", PASSWORD, "Admin", local=True, bootstrap="")
    admin = access.setup("admin", PASSWORD, "Admin", local=False, bootstrap="remote-bootstrap")
    assert admin["role"] == "admin" and "password_hash" not in admin
    with pytest.raises(PlatformError, match="already"):
        access.setup("other", PASSWORD, "Other", local=True, bootstrap="")


def test_session_hash_idle_and_absolute_expiration_are_server_enforced(access, monkeypatch):
    service, _ = access
    instant = 1_800_000_000_000
    monkeypatch.setattr(platform, "now_ms", lambda: instant)
    token, _ = service.login("ADMIN", PASSWORD, "127.0.0.1")
    with service.store.read() as conn:
        session = conn.execute("SELECT * FROM workspace_sessions").fetchone()
    assert (
        session["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and session["token_hash"] != token
    )
    assert service.principal(request(token))[0]["username"] == "admin"
    instant += service.settings.idle_minutes * 60000
    assert service.principal(request(token)) == (None, None)
    with service.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace_sessions").fetchone()[0] == 0
    token, _ = service.login("admin", PASSWORD, "127.0.0.1")
    with service.store.write() as conn:
        conn.execute("UPDATE workspace_sessions SET expires_at=?", (instant,))
    assert service.principal(request(token)) == (None, None)


def test_last_admin_atomic_role_revocation_logout_and_disable(access):
    service, admin = access
    with pytest.raises(PlatformError) as error:
        service.update_user(admin["id"], role="viewer", enabled=True)
    assert error.value.code == "last_admin"
    second = service.create_user("second", PASSWORD, "Second", "admin")
    token, _ = service.login("admin", PASSWORD, "127.0.0.1")
    service.update_user(admin["id"], role="viewer", enabled=True)
    assert service.principal(request(token)) == (None, None)
    token, _ = service.login("second", PASSWORD, "127.0.0.1")
    service.logout(token)
    assert service.principal(request(token)) == (None, None)
    with pytest.raises(PlatformError, match="at least one"):
        service.update_user(second["id"], role="admin", enabled=False)


def test_login_rechecks_enabled_after_password_verification(access, monkeypatch):
    service, admin = access

    def disable_during_kdf(password, encoded):
        with service.store.write() as conn:
            conn.execute("UPDATE workspace_users SET enabled=0 WHERE id=?", (admin["id"],))
        return True

    monkeypatch.setattr(platform, "verify_password", disable_during_kdf)
    with pytest.raises(PlatformError, match="Invalid username"):
        service.login("admin", PASSWORD, "127.0.0.1")
    with service.store.read() as conn:
        assert not conn.execute("SELECT 1 FROM workspace_sessions").fetchone()


def test_successful_logins_do_not_exhaust_ip_failure_limit(access, monkeypatch):
    service, _ = access
    monkeypatch.setattr(platform, "verify_password", lambda *args: True)
    for _ in range(20):
        service.login("admin", PASSWORD, "127.0.0.1")
    with service.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM workspace_sessions").fetchone()[0] == 20


def test_failed_login_throttle_survives_service_restart_and_expires(access, monkeypatch):
    service, _ = access
    instant = 1_800_000_000_000
    monkeypatch.setattr(platform, "now_ms", lambda: instant)
    monkeypatch.setattr(platform, "verify_password", lambda *args: False)
    for _ in range(10):
        with pytest.raises(PlatformError) as bad:
            service.login("admin", "wrong", "127.0.0.1")
        assert bad.value.code == "invalid_credentials"
    restarted = AccessService(service.store, service.settings)
    with pytest.raises(PlatformError) as limited:
        restarted.login("admin", "wrong", "127.0.0.1")
    assert limited.value.status == 429
    instant += 900001
    with pytest.raises(PlatformError) as expired:
        restarted.login("admin", "wrong", "127.0.0.1")
    assert expired.value.status == 401


def test_change_and_admin_reset_password_revoke_all_sessions_without_secret_audit(access):
    service, admin = access
    token, _ = service.login("admin", PASSWORD, "127.0.0.1")
    with pytest.raises(PlatformError, match="Current password"):
        service.change_password(admin["id"], "incorrect", NEXT_PASSWORD)
    changed = service.change_password(admin["id"], PASSWORD, NEXT_PASSWORD)
    assert changed["sessions_revoked"] and service.principal(request(token)) == (None, None)
    token, _ = service.login("admin", NEXT_PASSWORD, "127.0.0.1")
    service.reset_password(admin["id"], PASSWORD, actor="recovery_admin")
    assert service.principal(request(token)) == (None, None)
    service.login("admin", PASSWORD, "127.0.0.1")
    events = dumps(service.store.events("system", 100))
    assert PASSWORD not in events and NEXT_PASSWORD not in events


def test_legacy_password_is_upgraded_after_successful_login(access):
    service, admin = access
    salt = bytes.fromhex("ab" * 16)
    digest = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    with service.store.write() as conn:
        conn.execute(
            "UPDATE workspace_users SET password_hash=? WHERE id=?",
            (f"scrypt:16384:8:1:{salt.hex()}:{digest.hex()}", admin["id"]),
        )
    service.login("admin", PASSWORD, "127.0.0.1")
    with service.store.read() as conn:
        assert (
            conn.execute("SELECT password_hash FROM workspace_users")
            .fetchone()[0]
            .startswith("scrypt:32768:8:3:")
        )


def test_backup_streams_hash_and_atomic_private_metadata(workspace, monkeypatch):
    store, settings = workspace
    backups = BackupService(store, settings)
    monkeypatch.setattr(Path, "read_bytes", lambda *args: pytest.fail("Backup hashing loaded the whole DB"))
    manifest = backups.create()
    verified = backups.verify(manifest["id"])
    assert verified["verified"] and verified["tables"]["accounts"] == 2
    assert os.stat(backups.directory).st_mode & 0o777 == 0o700
    assert os.stat(backups.directory / (manifest["id"] + ".sqlite3")).st_mode & 0o777 == 0o600
    assert os.stat(backups.directory / (manifest["id"] + ".json")).st_mode & 0o777 == 0o600
    assert not list(backups.directory.glob("*.partial"))


def test_restore_round_trip_preserves_target_during_retention_and_halts_before_copy(access, monkeypatch):
    service, _ = access
    store, settings = service.store, service.settings
    from tidebench.pro_execution import SimulationBook

    SimulationBook(store)
    token, _ = service.login("admin", PASSWORD, "127.0.0.1")
    with store.write() as conn:
        conn.execute(
            "INSERT INTO deployments(id,source,inst_id,bar,strategy,status,created_at,updated_at) VALUES('legacy','okx','BTC-USDT','1H','{}','running',1,1)"
        )
        conn.execute(
            "INSERT INTO pro_deployments(id,source,inst_id,config,status,created_at,updated_at) VALUES('pro','okx','BTC-USDT','{}','running',1,1)"
        )
        conn.execute(
            "INSERT INTO pro_orders(id,source,key,payload,status,body,reservation,created_at,updated_at) VALUES('pending','okx','k','{}','pending','{\"status\":\"pending\"}','100',1,1)"
        )
        conn.execute("UPDATE accounts SET cash='12345' WHERE source='okx'")
    backups = BackupService(store, settings)
    target = backups.create("target")
    with store.write() as conn:
        conn.execute("UPDATE accounts SET cash='999' WHERE source='okx'")
    backups.create("newer")
    copy = backups._copy
    copies = 0

    def inspect_prepared_image(source, destination):
        nonlocal copies
        copies += 1
        if copies == 3:
            # These invariants hold before the live DB is replaced, not only
            # after a successful restore response.
            assert source.execute("SELECT COUNT(*) FROM workspace_sessions").fetchone()[0] == 0
            assert all(row[0] == 1 for row in source.execute("SELECT kill_switch FROM risk"))
            assert source.execute("SELECT status FROM pro_orders").fetchone()[0] == "canceled"
        copy(source, destination)

    monkeypatch.setattr(backups, "_copy", inspect_prepared_image)
    result = backups.restore(target["id"])
    assert result["sessions_revoked"] and result["execution_halted"] and result["pending_orders_canceled"]
    assert backups.verify(target["id"])["verified"] and backups.verify(result["safety_backup_id"])["verified"]
    assert service.principal(request(token)) == (None, None)
    with store.read() as conn:
        assert conn.execute("SELECT cash FROM accounts WHERE source='okx'").fetchone()[0] == "12345"
        assert all(row[0] == 1 for row in conn.execute("SELECT kill_switch FROM risk"))
        assert all(row[0] == 1 for row in conn.execute("SELECT halted FROM pro_risk"))
        assert conn.execute("SELECT status FROM deployments").fetchone()[0] == "stopped"
        assert conn.execute("SELECT status FROM pro_deployments").fetchone()[0] == "stopped"
        order = conn.execute("SELECT status,reservation,body FROM pro_orders").fetchone()
        assert order[0:2] == ("canceled", "0") and json.loads(order[2])["status"] == "canceled"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_corrupt_backup_hash_blocks_restore_and_preserves_live_state(workspace):
    store, settings = workspace
    backups = BackupService(store, settings)
    manifest = backups.create()
    path = backups.directory / (manifest["id"] + ".sqlite3")
    with path.open("r+b") as stream:
        stream.write(b"corrupt")
    with pytest.raises(PlatformError) as rejected:
        backups.restore(manifest["id"])
    assert rejected.value.code == "backup_hash_mismatch"
    with store.read() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_untrusted_manifest_cannot_delete_outside_backup_directory(workspace):
    store, settings = workspace
    backups = BackupService(store, settings)
    victim = settings.data_dir / "keep.sqlite3"
    victim.write_text("do not remove")
    malicious = backups.directory / "20250101T000000Z-aaaaaaaa.json"
    malicious.write_text(json.dumps({"id": "../keep", "created_at": 0}))
    for _ in range(3):
        backups.create()
    assert victim.read_text() == "do not remove" and malicious.exists()
    with pytest.raises(PlatformError, match="identifier"):
        backups.verify("../keep")


def test_restore_preparation_failure_keeps_original_workspace(workspace, monkeypatch):
    store, settings = workspace
    backups = BackupService(store, settings)
    target = backups.create()
    with store.write() as conn:
        conn.execute("UPDATE accounts SET cash='777'")
    original = backups._copy
    calls = 0

    def failure(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:  # Safety succeeds; staging fails before live restore.
            raise sqlite3.OperationalError("Injected storage failure")
        original(source, destination)

    monkeypatch.setattr(backups, "_copy", failure)
    with pytest.raises(PlatformError) as failed:
        backups.restore(target["id"])
    assert failed.value.code == "restore_failed"
    with store.read() as conn:
        assert conn.execute("SELECT cash FROM accounts LIMIT 1").fetchone()[0] == "777"
    assert not list(backups.directory.glob("*.restore-partial"))


def test_backup_manifest_publication_failure_leaves_no_partial_backup(workspace, monkeypatch):
    store, settings = workspace
    backups = BackupService(store, settings)
    replace = platform.os.replace

    def interrupted(source, target):
        if str(target).endswith(".json"):
            raise OSError("Injected metadata rename failure")
        return replace(source, target)

    monkeypatch.setattr(platform.os, "replace", interrupted)
    with pytest.raises(OSError, match="metadata rename"):
        backups.create()
    assert backups.list() == [] and list(backups.directory.iterdir()) == []


@pytest.mark.parametrize("corruption", ["bytes", "schema"])
def test_backup_verify_checks_database_even_when_manifest_hash_matches(workspace, corruption):
    store, settings = workspace
    backups = BackupService(store, settings)
    manifest = backups.create()
    path = backups.directory / (manifest["id"] + ".sqlite3")
    metadata = backups.directory / (manifest["id"] + ".json")
    if corruption == "bytes":
        path.write_bytes(b"not a SQLite database")
    else:
        conn = sqlite3.connect(path)
        conn.execute("UPDATE schema_version SET version=2")
        conn.commit()
        conn.close()
    manifest.update(sha256=backups._digest(path), size_bytes=path.stat().st_size)
    metadata.write_text(json.dumps(manifest))
    with pytest.raises(PlatformError) as error:
        backups.verify(manifest["id"])
    assert error.value.code == ("backup_integrity" if corruption == "bytes" else "backup_schema")


def test_backup_verify_rejects_symlinked_data_file(workspace):
    store, settings = workspace
    backups = BackupService(store, settings)
    manifest = backups.create()
    path = backups.directory / (manifest["id"] + ".sqlite3")
    destination = settings.data_dir / "elsewhere.sqlite3"
    path.rename(destination)
    path.symlink_to(destination)
    with pytest.raises(PlatformError) as error:
        backups.verify(manifest["id"])
    assert error.value.code == "unsafe_backup_path"


def test_metrics_histogram_is_measured_and_labels_are_escaped():
    metrics = RuntimeMetrics()
    metrics.record("GET", '/bad"label', 500, 0.2)
    assert metrics.snapshot()["server_errors"] == 1
    output = metrics.prometheus()
    assert 'route="/bad\\"label"' in output and 'le="+Inf"} 1' in output


def test_metrics_bound_unknown_method_cardinality():
    metrics = RuntimeMetrics()
    for index in range(1000):
        metrics.record(f"ARBITRARY{index}", "unmatched", 405, 0.001)
    assert len(metrics.requests) == 1
    assert metrics.snapshot()["requests"] == 1000
    assert 'method="OTHER"' in metrics.prometheus()


def test_change_password_cannot_bypass_credential_verification_throttle(access, monkeypatch):
    service, admin = access
    monkeypatch.setattr(platform, "verify_password", lambda *args: False)
    for _ in range(10):
        with pytest.raises(PlatformError) as error:
            service.change_password(admin["id"], "wrong", NEXT_PASSWORD)
        assert error.value.status == 401
    with pytest.raises(PlatformError) as limited:
        service.change_password(admin["id"], "wrong", NEXT_PASSWORD)
    assert limited.value.status == 429


def test_backup_schema_is_observed_and_cross_version_restore_is_blocked(access):
    from tidebench.pro_service import ProfessionalRuntime

    service, _ = access
    store, settings = service.store, service.settings
    backups = BackupService(store, settings)
    legacy = backups.create("pre_upgrade")
    assert legacy["database_schema"] == 1
    ProfessionalRuntime(store, SimpleNamespace(region="global"), settings)
    with store.write() as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,description TEXT NOT NULL,applied_at INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations VALUES(2,'Professional workspace tables initialized',1)")
        conn.execute("UPDATE schema_version SET version=2")
    professional = backups.create("post_upgrade")
    assert professional["database_schema"] == 2
    assert backups.verify(professional["id"])["database_schema"] == 2
    with pytest.raises(PlatformError) as mismatch:
        backups.restore(legacy["id"])
    assert mismatch.value.code == "restore_schema_mismatch" and mismatch.value.status == 409
    assert len(backups.list()) == 2  # Reject before making/pruning a safety snapshot.
    result = backups.restore(professional["id"])
    assert result["restored"] == professional["id"]
    with store.read() as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1


def test_manifest_schema_must_match_database(workspace):
    store, settings = workspace
    backups = BackupService(store, settings)
    manifest = backups.create()
    manifest["database_schema"] = 2
    (backups.directory / (manifest["id"] + ".json")).write_text(json.dumps(manifest))
    with pytest.raises(PlatformError) as mismatch:
        backups.verify(manifest["id"])
    assert mismatch.value.code == "backup_schema"


def test_unknown_database_schema_cannot_be_backed_up(workspace):
    store, settings = workspace
    with store.write() as conn:
        conn.execute("UPDATE schema_version SET version=999")
    backups = BackupService(store, settings)
    with pytest.raises(PlatformError) as unsupported:
        backups.create()
    assert unsupported.value.code == "backup_schema"
    assert list(backups.directory.iterdir()) == []


@pytest.mark.parametrize(
    "table", ["pro_ledger", "pro_funding", "pro_strategy_intents", "catalog_settlement_marks"]
)
@pytest.mark.parametrize("version", [2, 3])
def test_backup_rejects_missing_financial_history_even_with_valid_checksum(access, table, version):
    from tidebench.pro_service import ProfessionalRuntime

    service, _ = access
    store, settings = service.store, service.settings
    ProfessionalRuntime(store, SimpleNamespace(region="global"), settings)
    with store.write() as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,description TEXT NOT NULL,applied_at INTEGER NOT NULL)"
        )
        conn.execute("UPDATE schema_version SET version=?", (version,))
    backups = BackupService(store, settings)
    manifest = backups.create()
    path = backups.directory / (manifest["id"] + ".sqlite3")
    metadata = backups.directory / (manifest["id"] + ".json")
    with sqlite3.connect(path) as conn:
        conn.execute(f'DROP TABLE "{table}"')
    manifest.update(sha256=backups._digest(path), size_bytes=path.stat().st_size)
    metadata.write_text(json.dumps(manifest))
    with pytest.raises(PlatformError) as error:
        backups.verify(manifest["id"])
    assert error.value.code == "backup_schema"
    with pytest.raises(PlatformError) as error:
        backups.restore(manifest["id"])
    assert error.value.code == "backup_schema"
    assert store.path.exists()
