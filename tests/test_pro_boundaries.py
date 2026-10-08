"""Independent ASGI authorization/recovery checks against a real workspace DB."""

import asyncio
import json
import shutil
from threading import Event

import httpx
import pytest
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.platform import COOKIE, BackupService
from tidebench.store import Store

TOKEN = "independent-boundary-service-token-32-characters"
PASSWORD = "independent-test-password-123"
ROLES = ("admin", "trader", "researcher", "viewer", "risk_operator")
ALL = set(ROLES)
TRADING = {"admin", "trader"}
RESEARCH = {"admin", "trader", "researcher"}
RISK = {"admin", "trader", "risk_operator"}


class NoNetworkMarket:
    region = "global"

    async def close(self):
        pass

    async def _get(self, *args, **kwargs):
        pytest.fail("Boundary tests must not access the exchange")

    get_tickers = get_instruments = get_candles = _get


@pytest.fixture
def app(tmp_path):
    return create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            worker_enabled=False,
            api_token=TOKEN,
            bootstrap_token="",
            auth_enabled=True,
        ),
        market=NoNetworkMarket(),
    )


def client(app, *, bearer=True, token=None, remote="127.0.0.1", raise_app_exceptions=True):
    headers = {"Authorization": "Bearer " + TOKEN} if bearer else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, client=(remote, 12345), raise_app_exceptions=raise_app_exceptions
        ),
        base_url="http://localhost",
        headers=headers,
        cookies={COOKIE: token} if token else {},
    )


def cookie_user(app, role="admin"):
    access = app.state.access
    if access.setup_required():
        access.setup("admin", PASSWORD, "Administrator", local=True, bootstrap="")
    if role != "admin":
        access.create_user(role, PASSWORD, role, role)
    token, user = access.login(role, PASSWORD, "127.0.0.1")
    return token, access.csrf(token), user


async def until(predicate, timeout=2):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout)


def lifecycle_spies(app, monkeypatch):
    calls = []
    for name, component in (("legacy", app.state.supervisor), ("professional", app.state.professional)):
        for operation in ("start", "stop"):

            async def record(name=name, operation=operation):
                calls.append((name, operation))

            monkeypatch.setattr(component, operation, record)
    app.state.settings.worker_enabled = True
    return calls


def running_workspace_backup(app):
    """A valid backup must not smuggle active strategies through recovery."""
    runtime = app.state.professional
    with runtime.store.write() as conn:
        conn.execute("UPDATE risk SET kill_switch=0")
        conn.execute("UPDATE pro_risk SET halted=0")
        conn.execute(
            "INSERT INTO deployments(id,source,inst_id,bar,strategy,status,created_at,updated_at) "
            "VALUES('legacy-active','example','BTC-USDT','1H','{}','running',1,1)"
        )
        conn.execute(
            "INSERT INTO pro_deployments(id,source,inst_id,config,status,created_at,updated_at) "
            "VALUES('professional-active','example','BTC-USDT','{}','running',1,1)"
        )
    return runtime.backups.create()


async def assert_recovery_failed_closed(app, session):
    assert app.state.maintenance and app.state.active_requests == 0
    for path in ("/api/v1/pro/catalog/datasets", "/healthz"):
        response = await session.get(path)
        assert response.status_code == 503 and response.json()["error"]["code"] == "maintenance"
    # Reopening the database proves a process restart retains the halt, rather
    # than relying on an in-memory flag from the failed recovery request.
    with Store(app.state.settings.database).read() as conn:
        assert {row[0] for row in conn.execute("SELECT kill_switch FROM risk")} == {1}
        assert {row[0] for row in conn.execute("SELECT halted FROM pro_risk")} == {1}
        assert conn.execute("SELECT status FROM deployments WHERE id='legacy-active'").fetchone()[0] == (
            "stopped"
        )
        assert (
            conn.execute("SELECT status FROM pro_deployments WHERE id='professional-active'").fetchone()[0]
            == "stopped"
        )


async def test_every_role_has_explicit_read_and_command_permissions(app):
    """Malformed bodies prove authorization happens before command validation."""
    reads = (
        ("/pro/catalog/datasets", ALL),
        ("/pro/catalog/jobs", ALL),
        ("/pro/catalog/packages", ALL),
        ("/pro/research/runs", ALL),
        ("/pro/portfolio-strategies", ALL),
        ("/pro/execution/portfolios?source=example", ALL),
        ("/pro/execution/portfolio-releases?source=example", ALL),
        ("/pro/execution/contributions?source=example", ALL),
        ("/pro/execution/orders", ALL),
        ("/pro/execution/ledger", ALL),
        ("/pro/execution/deployments", ALL),
        ("/pro/execution/risk", ALL),
        ("/pro/execution/analytics?source=example", ALL),
        ("/pro/ops", ALL),
        ("/pro/ops/audit", ALL),
        ("/auth/users", {"admin"}),
        ("/pro/ops/metrics", {"admin"}),
    )
    commands = (
        ("POST", "/pro/catalog/jobs", RESEARCH),
        ("POST", "/pro/catalog/packages", RESEARCH),
        ("POST", "/pro/catalog/import", RESEARCH),
        ("POST", "/pro/research/runs", RESEARCH),
        ("POST", "/pro/portfolio-strategies", RESEARCH),
        ("POST", "/pro/portfolio-strategies/missing/versions", RESEARCH),
        ("POST", "/pro/execution/portfolio-releases/preview", TRADING),
        ("POST", "/pro/execution/portfolio-releases", TRADING),
        ("POST", "/pro/execution/portfolio-releases/missing/activate", TRADING),
        ("POST", "/pro/execution/portfolios/missing/stop", TRADING),
        ("POST", "/backtests", RESEARCH),
        ("POST", "/pro/execution/orders/preview", TRADING),
        ("POST", "/pro/execution/analytics", ALL),
        ("POST", "/pro/execution/orders", TRADING),
        ("POST", "/paper/orders", TRADING),
        ("POST", "/pro/execution/deployments", TRADING),
        ("POST", "/paper/deployments", TRADING),
        ("PUT", "/pro/execution/risk", RISK),
        ("PUT", "/risk", RISK),
        ("POST", "/pro/execution/halt", RISK),
        ("POST", "/risk/kill-switch", RISK),
        ("POST", "/pro/ops/restore", {"admin"}),
        ("POST", "/auth/users", {"admin"}),
        ("PUT", "/auth/users/missing", {"admin"}),
        ("POST", "/auth/users/missing/password", {"admin"}),
        ("POST", "/auth/password", ALL),
    )
    for role in ROLES:
        token, csrf, _ = cookie_user(app, role)
        async with client(app, bearer=False, token=token) as session:
            session.headers["X-CSRF-Token"] = csrf
            for path, allowed in reads:
                response = await session.get("/api/v1" + path)
                assert response.status_code == (200 if role in allowed else 403), (role, path, response.text)
            for method, path, allowed in commands:
                response = await session.request(
                    method, "/api/v1" + path, content=b"{", headers={"Content-Type": "application/json"}
                )
                valid_no_body = path in {
                    "/pro/execution/portfolio-releases/missing/activate",
                    "/pro/execution/portfolios/missing/stop",
                }
                expected = (404 if valid_no_body else 422) if role in allowed else 403
                assert response.status_code == expected, (role, path, response.text)
                if role not in allowed:
                    assert response.json()["error"]["code"] == "role_forbidden"
            # Logging out belongs to the user, independent of trading privileges.
            assert (await session.post("/api/v1/auth/logout")).status_code == 200
            assert (await session.get("/api/v1/pro/catalog/datasets")).status_code == 401


async def test_cookie_csrf_and_bearer_mutations_have_distinct_boundaries(app):
    token, csrf, _ = cookie_user(app)
    body = {"source": "example", "active": True, "reason": "Boundary test"}
    async with client(app, bearer=False, token=token) as session:
        assert (await session.get("/api/v1/pro/execution/risk")).status_code == 200
        for supplied in ("", "wrong", csrf[:-1] + ("a" if csrf[-1] != "a" else "b")):
            response = await session.post(
                "/api/v1/pro/execution/halt", json=body, headers={"X-CSRF-Token": supplied}
            )
            assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_required"
        response = await session.post("/api/v1/pro/execution/halt", json=body, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        forbidden_origin = await session.post(
            "/api/v1/pro/execution/halt",
            json=body,
            headers={"X-CSRF-Token": csrf, "Origin": "https://hostile.invalid"},
        )
        assert forbidden_origin.status_code == 403
    async with client(app) as service:
        assert (await service.post("/api/v1/pro/execution/halt", json=body)).status_code == 200


async def test_non_ascii_csrf_header_is_a_denial_not_a_server_error(app):
    token, _, _ = cookie_user(app)
    async with client(app, bearer=False, token=token) as session:
        response = await session.post(
            "/api/v1/pro/execution/halt",
            json={"source": "example", "active": True, "reason": "Boundary test"},
            headers=[(b"x-csrf-token", b"\xff")],
        )
    assert response.status_code == 403 and response.json()["error"]["code"] == "csrf_required"


async def test_configured_bootstrap_is_enforced_even_on_loopback(app):
    app.state.settings.bootstrap_token = "configured-bootstrap-secret"
    body = {"username": "admin", "password": PASSWORD, "display_name": "Admin"}
    async with client(app, bearer=False) as session:
        for header in ([], [(b"x-bootstrap-token", b"\xff")], [(b"x-bootstrap-token", b"incorrect")]):
            response = await session.post("/api/v1/auth/setup", json=body, headers=header)
            assert response.status_code == 403 and response.json()["error"]["code"] == "bootstrap_required"
            assert app.state.access.setup_required()
        response = await session.post(
            "/api/v1/auth/setup", json=body, headers={"X-Bootstrap-Token": "configured-bootstrap-secret"}
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
        assert PASSWORD not in response.text and "configured-bootstrap-secret" not in response.text


async def test_restore_rejects_overlap_then_quiesces_all_requests(app, monkeypatch):
    runtime = app.state.professional
    backup = runtime.backups.create()
    calls = lifecycle_spies(app, monkeypatch)
    entered, release = Event(), Event()
    stop_entered, stop_release = asyncio.Event(), asyncio.Event()
    verify = runtime.backups.verify

    def held_verify(identifier):
        entered.set()
        assert release.wait(5), "Verification gate was never released"
        return verify(identifier)

    async def held_stop():
        calls.append(("legacy", "stop"))
        stop_entered.set()
        await stop_release.wait()

    monkeypatch.setattr(runtime.backups, "verify", held_verify)
    monkeypatch.setattr(app.state.supervisor, "stop", held_stop)
    body = {"backup_id": backup["id"], "confirmation": "RESTORE"}
    async with client(app) as session:
        first = asyncio.create_task(session.post("/api/v1/pro/ops/restore", json=body))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not app.state.maintenance
            second = await session.post("/api/v1/pro/ops/restore", json=body)
            assert second.status_code == 409 and second.json()["error"]["code"] == "recovery_in_progress"
            release.set()
            await asyncio.wait_for(stop_entered.wait(), 2)
            assert app.state.maintenance
            for method, path in (
                ("GET", "/api/v1/pro/catalog/datasets"),
                ("GET", "/healthz"),
                ("POST", "/api/v1/pro/ops/restore"),
            ):
                response = await session.request(method, path, json=body if method == "POST" else None)
                assert response.status_code == 503 and response.json()["error"]["code"] == "maintenance"
            stop_release.set()
            response = await asyncio.wait_for(first, 5)
            assert response.status_code == 200, response.text
            assert calls == [
                ("legacy", "stop"),
                ("professional", "stop"),
                ("legacy", "start"),
                ("professional", "start"),
            ]
            assert not app.state.maintenance and app.state.active_requests == 0
        finally:
            release.set()
            stop_release.set()
            await asyncio.gather(first, return_exceptions=True)


async def test_canceled_drain_does_not_start_duplicate_workers(app, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    @app.get("/api/v1/pro/catalog/_blocked_boundary_read")
    async def blocked_read():
        entered.set()
        await release.wait()
        return {"finished": True}

    # The application's SPA catch-all precedes test-added routes; move only
    # this test endpoint ahead of it, retaining the real middleware stack.
    route = app.router.routes.pop()
    app.router.routes.insert(0, route)

    backup = app.state.professional.backups.create()
    calls = lifecycle_spies(app, monkeypatch)
    async with client(app) as session:
        reader = asyncio.create_task(session.get("/api/v1/pro/catalog/_blocked_boundary_read"))
        restore = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            restore = asyncio.create_task(
                session.post(
                    "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
                )
            )
            await until(lambda: app.state.maintenance)
            assert app.state.active_requests == 2
            restore.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(restore, 2)
            assert calls == [] and not app.state.maintenance
            release.set()
            assert (await asyncio.wait_for(reader, 2)).status_code == 200
            assert app.state.active_requests == 0
        finally:
            release.set()
            if restore and not restore.done():
                restore.cancel()
            await asyncio.gather(reader, *([restore] if restore else []), return_exceptions=True)


@pytest.mark.parametrize("cancellations,stopping", [(1, False), (3, False), (3, True)])
async def test_canceled_restore_keeps_maintenance_until_actual_copy_thread_finishes(
    app, monkeypatch, cancellations, stopping
):
    runtime = app.state.professional
    backup = runtime.backups.create()
    calls = lifecycle_spies(app, monkeypatch)
    entered, release = Event(), Event()
    restore = runtime.backups.restore

    def held_copy(identifier):
        entered.set()
        assert release.wait(5), "Restore copy gate was never released"
        return restore(identifier)

    monkeypatch.setattr(runtime.backups, "restore", held_copy)
    async with client(app) as session:
        command = asyncio.create_task(
            session.post(
                "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert app.state.maintenance and calls == [("legacy", "stop"), ("professional", "stop")]
            app.state.stopping = stopping
            for _ in range(cancellations):
                command.cancel()
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            # Canceling the HTTP waiter cannot cancel a SQLite worker thread.
            # Keep the workspace quiesced until that actual writer has finished.
            probe = await session.get("/api/v1/pro/catalog/datasets")
            assert probe.status_code == 503 and app.state.maintenance
            assert calls == [("legacy", "stop"), ("professional", "stop")]
        finally:
            release.set()
            await asyncio.gather(command, return_exceptions=True)
            await until(lambda: not runtime.inflight, timeout=5)
        assert app.state.maintenance is stopping
        expected = [("legacy", "stop"), ("professional", "stop")]
        if not stopping:
            expected += [("legacy", "start"), ("professional", "start")]
        assert calls == expected


async def test_restore_stop_failure_keeps_maintenance_and_persistent_execution_halts(app, monkeypatch):
    runtime = app.state.professional
    backup = running_workspace_backup(app)
    calls = lifecycle_spies(app, monkeypatch)

    async def failed_stop():
        calls.append(("professional", "stop"))
        raise RuntimeError("Injected professional stop failure")

    monkeypatch.setattr(runtime, "stop", failed_stop)
    async with client(app, raise_app_exceptions=False) as session:
        response = await session.post(
            "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
        )
        assert response.status_code == 500 and "Injected" not in response.text
        await assert_recovery_failed_closed(app, session)
    assert calls == [("legacy", "stop"), ("professional", "stop"), ("legacy", "start")]


async def test_restore_restart_failure_keeps_maintenance_and_persistent_execution_halts(app, monkeypatch):
    runtime = app.state.professional
    backup = running_workspace_backup(app)
    calls = lifecycle_spies(app, monkeypatch)

    async def failed_start():
        calls.append(("professional", "start"))
        raise RuntimeError("Injected professional restart failure")

    monkeypatch.setattr(runtime, "start", failed_start)
    async with client(app, raise_app_exceptions=False) as session:
        response = await session.post(
            "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
        )
        assert response.status_code == 500 and "Injected" not in response.text
        await assert_recovery_failed_closed(app, session)
    assert calls == [
        ("legacy", "stop"),
        ("professional", "stop"),
        ("legacy", "start"),
        ("professional", "start"),
    ]


async def test_restore_schema_mismatch_is_rejected_before_maintenance_or_worker_stop(
    app, tmp_path, monkeypatch
):
    old_settings = Settings(_env_file=None, data_dir=tmp_path / "legacy", api_token="", bootstrap_token="")
    old_backups = BackupService(Store(old_settings.database), old_settings)
    old = old_backups.create("pre_upgrade")
    for suffix in (".sqlite3", ".json"):
        shutil.copyfile(
            old_backups.directory / (old["id"] + suffix),
            app.state.professional.backups.directory / (old["id"] + suffix),
        )
    calls = lifecycle_spies(app, monkeypatch)
    async with client(app) as session:
        response = await session.post(
            "/api/v1/pro/ops/restore", json={"backup_id": old["id"], "confirmation": "RESTORE"}
        )
    assert response.status_code == 409 and response.json()["error"]["code"] == "restore_schema_mismatch"
    assert calls == [], "An incompatible backup must be rejected before stopping or restarting workers"
    assert not app.state.maintenance


async def test_request_body_limits_apply_before_reading_and_to_chunked_forged_lengths(app):
    consumed = []

    async def huge():
        consumed.append(True)
        yield b"x" * 70_000

    async with client(app) as session:
        too_large = await session.post(
            "/api/v1/pro/research/runs", content=huge(), headers={"Content-Length": "70000"}
        )
        assert too_large.status_code == 413 and consumed == []

        async def chunked():
            for index in range(10):
                consumed.append(index)
                yield b"x" * 32_768

        response = await session.post(
            "/api/v1/pro/research/runs", content=chunked(), headers={"Content-Length": "1"}
        )
        assert response.status_code == 413 and consumed == [0, 1, 2]
        consumed.clear()
        response = await session.post(
            "/api/v1/pro/catalog/import",
            content=huge(),
            headers={"Content-Length": str(10 * 1024 * 1024 + 1)},
        )
        assert response.status_code == 413 and consumed == []

        async def import_chunks():
            for index in range(8):
                consumed.append(index)
                yield b"x" * (2 * 1024 * 1024)

        response = await session.post("/api/v1/pro/catalog/import", content=import_chunks())
        assert response.status_code == 413 and consumed == list(range(6))


async def test_import_has_larger_bounded_body_budget_and_keeps_provenance(app):
    start = 1767222000000
    body = {
        "source": "example",
        "inst_id": "BTC-USDT",
        "kind": "trade",
        "bar": "1H",
        "start": start,
        "end": start + 3600000,
        "records": [
            {
                "ts": start,
                "open": "100",
                "high": "102",
                "low": "99",
                "close": "101",
                "volume": "1",
                "confirmed": True,
            }
        ],
        "provenance": {"provider": "Boundary test archive"},
    }
    encoded = json.dumps(body).encode() + b" " * 70_000
    async with client(app) as session:
        response = await session.post(
            "/api/v1/pro/catalog/import", content=encoded, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 201, response.text
    manifest = response.json()
    assert (
        manifest["transport"] == "user_import"
        and manifest["provenance"]["provider"] == "Boundary test archive"
    )


async def test_metrics_are_admin_only_and_route_labels_cannot_disclose_inputs(app):
    secret = "never-include-this-password-or-market-id-in-metrics"
    async with client(app) as session:
        await session.get("/api/v1/pro/catalog/datasets/" + secret, params={"api_key": TOKEN})
        await session.get("/api/v1/unknown-" + secret, params={"password": PASSWORD})
        invalid = await session.post(
            "/api/v1/auth/login", json={"username": "nonexistent", "password": secret}
        )
        assert invalid.status_code == 401 and secret not in invalid.text
        metrics = await session.get("/api/v1/pro/ops/metrics")
        assert metrics.status_code == 200 and "tidebench_http_requests_total" in metrics.text
        assert secret not in metrics.text and TOKEN not in metrics.text and PASSWORD not in metrics.text
        assert "/api/v1/pro/catalog/datasets/{identifier}" in metrics.text
        assert 'route="unmatched"' in metrics.text or 'route="/{path:path}"' in metrics.text
    async with client(app, bearer=False) as anonymous:
        assert (await anonymous.get("/api/v1/pro/ops/metrics")).status_code == 401


async def test_secure_cookie_configuration_preserves_session_confidentiality(app):
    app.state.settings.cookie_secure = True
    app.state.settings.bootstrap_token = "secure-first-setup-token"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 12345)), base_url="https://localhost"
    ) as session:
        response = await session.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "password": PASSWORD},
            headers={"X-Bootstrap-Token": "secure-first-setup-token"},
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"].lower()
        assert all(flag in cookie for flag in ("secure", "httponly", "samesite=strict", "path=/"))
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        token = session.cookies.get(COOKIE)
        assert token and token not in response.text and PASSWORD not in response.text
        assert response.json()["csrf_token"] == app.state.access.csrf(token)
        assert (await session.get("/api/v1/pro/catalog/datasets")).status_code == 200
