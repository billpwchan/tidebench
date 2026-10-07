"""Professional workspace acceptance: real API flows with isolated synthetic data."""

import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketService


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, _env_file=None), MarketService())
    with TestClient(app) as session:
        response = session.post(
            "/api/v1/auth/setup",
            json={
                "username": "operator",
                "password": "test-only-password-123",
                "display_name": "QA operator",
            },
        )
        assert response.status_code == 200, response.text
        session.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield session


def wait(client, path):
    for _ in range(200):
        response = client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] in {"completed", "degraded", "failed", "canceled"}:
            assert result["status"] == "completed", result
            return result
        time.sleep(0.025)
    pytest.fail("Background work did not complete")


def dataset(client, kind="trade", symbol="BTC-USDT"):
    interval, end = 3600000, 1767225600000
    response = client.post(
        "/api/v1/pro/catalog/jobs",
        json={
            "source": "example",
            "inst_id": symbol,
            "kind": kind,
            "bar": "1H",
            "start": end - 240 * interval,
            "end": end,
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    for _ in range(200):
        job = next(
            item for item in client.get("/api/v1/pro/catalog/jobs").json()["items"] if item["id"] == job_id
        )
        if job["status"] in {"completed", "degraded", "failed"}:
            assert job["status"] == "completed", job
            return job["dataset_id"]
        time.sleep(0.025)
    pytest.fail("Dataset job timed out")


def test_auth_csrf_roles_and_password_storage(client):
    assert client.get("/api/v1/auth/status").json()["user"]["role"] == "admin"
    csrf = client.headers.pop("X-CSRF-Token")
    assert (
        client.post(
            "/api/v1/pro/execution/halt", json={"source": "example", "active": True, "reason": "QA test"}
        ).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = csrf
    created = client.post(
        "/api/v1/auth/users",
        json={"username": "observer", "password": "viewer-test-password", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    assert "password" not in created.text
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/pro/execution/account").status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"username": "observer", "password": "viewer-test-password"}
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.get("/api/v1/pro/execution/account?source=example").status_code == 200
    assert (
        client.post(
            "/api/v1/pro/execution/halt", json={"source": "example", "active": True, "reason": "Unauthorized"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/auth/users").status_code == 403


def test_versioned_spot_research_replay_export_and_walk_forward(client):
    identifier = dataset(client)
    assert client.get(f"/api/v1/pro/catalog/datasets/{identifier}/verify").json()["verified"]
    response = client.post(
        "/api/v1/pro/research/runs",
        json={"dataset_id": identifier, "strategy": {"kind": "sma_cross", "fast": 5, "slow": 12}},
    )
    assert response.status_code == 202, response.text
    run = wait(client, f"/api/v1/pro/research/runs/{response.json()['id']}")
    assert run["result"]["result"]["metrics"]["initial_cash"] == "10000"
    replay = client.post(f"/api/v1/pro/research/runs/{run['id']}/replay")
    replayed = wait(client, f"/api/v1/pro/research/runs/{replay.json()['id']}")
    assert replayed["result"] == run["result"]
    export = client.get(f"/api/v1/pro/research/runs/{run['id']}/export")
    assert export.status_code == 200 and export.json()["snapshot"]["trade"]["id"] == identifier
    wf = client.post(
        "/api/v1/pro/research/runs",
        json={
            "dataset_id": identifier,
            "mode": "walk_forward",
            "options": {
                "train_bars": 100,
                "test_bars": 40,
                "step_bars": 40,
                "grid": {"fast": [5, 8], "slow": [12]},
            },
        },
    )
    assert wf.status_code == 202, wf.text
    result = wait(client, f"/api/v1/pro/research/runs/{wf.json()['id']}")["result"]
    assert len(result["folds"]) >= 2
    assert all(fold["test_result"]["metrics"] for fold in result["folds"])


def test_perpetual_research_requires_independent_mark_and_settled_funding(client):
    trade = dataset(client, symbol="BTC-USDT-SWAP")
    incomplete = client.post("/api/v1/pro/research/runs", json={"dataset_id": trade})
    assert incomplete.status_code == 422
    mark = dataset(client, "mark", "BTC-USDT-SWAP")
    funding = dataset(client, "funding", "BTC-USDT-SWAP")
    response = client.post(
        "/api/v1/pro/research/runs",
        json={
            "dataset_id": trade,
            "mark_dataset_id": mark,
            "funding_dataset_id": funding,
            "leverage": 3,
            "direction": "long_short",
            "strategy": {"kind": "sma_cross", "fast": 5, "slow": 12},
        },
    )
    assert response.status_code == 202, response.text
    run = wait(client, f"/api/v1/pro/research/runs/{response.json()['id']}")
    assert run["result"]["result"]["funding"]
    assert run["manifest"]["margin_tiers_are_historical"] is False


def test_unified_spot_swap_orders_idempotency_halt_and_balanced_journal(client):
    command = {"source": "example", "inst_id": "BTC-USDT", "side": "buy", "quantity": "0.001", "leverage": 1}
    first = client.post(
        "/api/v1/pro/execution/orders", json=command, headers={"Idempotency-Key": "test-command-one"}
    )
    assert first.status_code == 201, first.text
    retried = client.post(
        "/api/v1/pro/execution/orders",
        json={**command, "quantity": "0.0010"},
        headers={"Idempotency-Key": "test-command-one"},
    )
    assert retried.json()["id"] == first.json()["id"]
    conflict = client.post(
        "/api/v1/pro/execution/orders",
        json={**command, "quantity": "0.002"},
        headers={"Idempotency-Key": "test-command-one"},
    )
    assert conflict.status_code == 409
    swap = client.post(
        "/api/v1/pro/execution/orders",
        json={**command, "inst_id": "BTC-USDT-SWAP", "quantity": "1", "leverage": 3},
        headers={"Idempotency-Key": "test-command-two"},
    )
    assert swap.status_code == 201, swap.text
    account = client.get("/api/v1/pro/execution/account?source=example").json()
    assert len(account["positions"]) == 2 and Decimal(account["used_margin"]) > 0
    halted = client.post(
        "/api/v1/pro/execution/halt", json={"source": "example", "active": True, "reason": "QA halt"}
    )
    assert halted.status_code == 200
    assert (
        client.post(
            "/api/v1/pro/execution/orders", json=command, headers={"Idempotency-Key": "test-command-three"}
        ).status_code
        == 409
    )
    close = client.post(
        "/api/v1/pro/execution/orders",
        json={**command, "side": "sell", "reduce_only": True},
        headers={"Idempotency-Key": "test-command-four"},
    )
    assert close.status_code == 201, close.text
    ledger = client.get("/api/v1/pro/execution/ledger?source=example").json()["items"]
    balances = {}
    for entry in ledger:
        key = (entry["tx_id"], entry["asset"])
        balances[key] = balances.get(key, Decimal(0)) + Decimal(entry["debit"]) - Decimal(entry["credit"])
    assert balances and all(value == 0 for value in balances.values())


def test_verified_restore_revokes_session_and_halts_execution(client):
    backup = client.post("/api/v1/pro/ops/backups/create")
    assert backup.status_code == 201, backup.text
    identifier = backup.json()["id"]
    assert client.get(f"/api/v1/pro/ops/backups/{identifier}/verify").json()["verified"]
    restore = client.post(
        "/api/v1/pro/ops/restore", json={"backup_id": identifier, "confirmation": "RESTORE"}
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["execution_halted"] and restore.json()["sessions_revoked"]
    assert client.get("/api/v1/pro/execution/account?source=example").status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-only-password-123"}
    )
    assert login.status_code == 200, login.text
    assert client.get("/api/v1/pro/execution/risk?source=example").json()["halted"]


def test_additive_upgrade_is_repeatable_and_preserves_legacy_state(tmp_path):
    from tidebench.store import Store

    configuration = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    legacy = Store(configuration.database)
    with legacy.write() as conn:
        conn.execute("UPDATE accounts SET cash='9876.54' WHERE source='example'")
    first = create_app(configuration)
    second = create_app(configuration)
    with second.state.store.read() as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=2").fetchone()[0] == 1
        assert conn.execute("SELECT cash FROM accounts WHERE source='example'").fetchone()[0] == "9876.54"
    assert first.state.professional.book.account("example", {})["cash"] == "10000"
