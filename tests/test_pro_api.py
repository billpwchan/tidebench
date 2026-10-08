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
    assert client.get("/api/v1/pro/catalog/instrument-observations?source=example").status_code == 200
    assert client.post("/api/v1/pro/catalog/instrument-observations?source=example").status_code == 403


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
    assert replayed["manifest"]["replay_verified"] is True
    assert replayed["manifest"]["result_hash"] == run["manifest"]["result_hash"]
    assert len(run["manifest"]["research_implementation"]["code_fingerprint"]) == 64
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
    from tidebench.strategy_registry import StrategyDefinition

    runtime = client.app.state.professional
    definition = StrategyDefinition(strategy={"kind": "buy_hold", "allocation": ".1"}).record()
    project = runtime.registry.create_project(
        "Recovery lineage",
        "All research, approval and forward evidence must survive a verified restore.",
        definition,
        "operator",
    )
    queued = client.post(
        "/api/v1/pro/research/runs",
        json={
            "dataset_id": dataset(client),
            "strategy_version_id": project["version"]["id"],
            "strategy": definition["strategy"],
        },
    )
    assert queued.status_code == 202, queued.text
    run = wait(client, f"/api/v1/pro/research/runs/{queued.json()['id']}")
    preview = runtime.registry.preview_release(runtime, run["id"], "single")
    release = runtime.registry.approve_release(
        runtime,
        {
            "run_id": run["id"],
            "selection": "single",
            "preview_hash": preview["preview_hash"],
            "review": "Recovery fixture: reviewed version, costs and isolated paper scope.",
            "acknowledgements": preview["required_acknowledgements"],
        },
        "operator",
    )
    release = runtime.registry.activate_release(runtime, release["id"], "operator")
    deployment = next(d for d in runtime.deployments() if d["id"] == release["deployment_id"])
    client.portal.call(runtime.evaluate, deployment)
    client.portal.call(runtime.execution_once)
    assert runtime.history.decisions(deployment["id"])
    assert runtime.book.performance.report("example")["items"]
    runtime.incidents.reconcile(
        [{"kind": "recovery-fixture", "subject": "worker", "details": {"cause": "injected"}}]
    )
    saved_clock = runtime.clock.status()
    backup = client.post("/api/v1/pro/ops/backups/create")
    assert backup.status_code == 201, backup.text
    identifier = backup.json()["id"]
    assert backup.json()["database_schema"] == 7
    assert client.get(f"/api/v1/pro/ops/backups/{identifier}/verify").json()["verified"]
    runtime.clock.change(step_ms=3600000, expected_revision=saved_clock["revision"], actor="operator")
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
    assert runtime.clock.status()["market_ts"] == saved_clock["market_ts"]
    assert runtime.clock.status()["paused"]
    assert runtime.run(run["id"])["result"] == run["result"]
    assert (
        runtime.registry.version(project["version"]["id"])["content_hash"]
        == project["version"]["content_hash"]
    )
    assert runtime.history.decisions(deployment["id"])
    assert runtime.book.performance.report("example")["items"]
    assert any(i["kind"] == "recovery-fixture" for i in runtime.incidents.list())
    assert next(d for d in runtime.deployments() if d["id"] == deployment["id"])["status"] == "stopped"


def test_additive_upgrade_is_repeatable_and_preserves_legacy_state(tmp_path):
    from tidebench.store import Store

    configuration = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    legacy = Store(configuration.database)
    with legacy.write() as conn:
        conn.execute("UPDATE accounts SET cash='9876.54' WHERE source='example'")
    with TestClient(create_app(configuration)) as first:
        assert first.app.state.professional.book.account("example", {})["cash"] == "10000"
    with TestClient(create_app(configuration)) as second:
        with second.app.state.store.read() as conn:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 7
            assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=2").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0] == 1
            assert conn.execute("SELECT cash FROM accounts WHERE source='example'").fetchone()[0] == "9876.54"


def test_perpetual_package_binds_research_inputs_and_captured_funding(client, monkeypatch):
    end = 1767225600000
    request = {
        "source": "example",
        "inst_id": "BTC-USDT-SWAP",
        "bar": "1H",
        "start": end - 240 * 3600000,
        "end": end,
        "idempotency_key": "api-package-once",
    }
    created = client.post("/api/v1/pro/catalog/packages", json=request)
    assert created.status_code == 202, created.text
    identifier = created.json()["id"]
    assert client.post("/api/v1/pro/catalog/packages", json=request).json()["id"] == identifier
    for _ in range(300):
        package = client.get(f"/api/v1/pro/catalog/packages/{identifier}").json()
        if package["status"] == "ready":
            break
        assert package["status"] not in {"blocked", "failed", "canceled"}, package
        time.sleep(0.025)
    else:
        pytest.fail("Package did not prepare research inputs")
    assert package["ready"] and package["funding_marks"]["captured"] > 0
    assert "manifest" not in package
    manifest = client.get(f"/api/v1/pro/catalog/packages/{identifier}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["funding_events"]
    inputs = {key: value for key, value in package["research_inputs"].items() if key != "source"}
    mismatch = client.post("/api/v1/pro/research/runs", json=inputs | {"package_manifest_hash": "0" * 64})
    assert mismatch.status_code == 409, mismatch.text
    runtime = client.app.state.professional

    def forbidden(*args, **kwargs):
        pytest.fail("Ready package research reloaded funding instead of using its captured evidence")

    monkeypatch.setattr(runtime.catalog, "load_funding", forbidden)
    monkeypatch.setattr(runtime.catalog, "_settlement_mark", forbidden)
    response = client.post("/api/v1/pro/research/runs", json=inputs)
    assert response.status_code == 202, response.text
    run = wait(client, f"/api/v1/pro/research/runs/{response.json()['id']}")
    assert run["manifest"]["package_manifest_hash"] == package["manifest_hash"]
    assert run["result"]["result"]["input_snapshot"]["funding_events"]
    assert client.post(f"/api/v1/pro/catalog/packages/{identifier}/cancel").status_code == 409


def test_portfolio_analysis_is_read_only_and_supports_custom_asset_shocks(client):
    order = {"source": "example", "inst_id": "BTC-USDT", "side": "buy", "quantity": ".001"}
    assert (
        client.post(
            "/api/v1/pro/execution/orders", json=order, headers={"Idempotency-Key": "analytics-spot"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/pro/execution/orders",
            json=order | {"inst_id": "BTC-USDT-SWAP", "side": "sell", "quantity": "1", "leverage": 3},
            headers={"Idempotency-Key": "analytics-short"},
        ).status_code
        == 201
    )
    before = client.get("/api/v1/pro/execution/account?source=example").json()
    ledger = client.get("/api/v1/pro/execution/ledger?source=example").json()
    result = client.get("/api/v1/pro/execution/analytics?source=example")
    assert result.status_code == 200, result.text
    analysis = result.json()
    assert analysis["status"] == "available", analysis["issues"]
    assert Decimal(analysis["summary"]["gross_notional"]) > abs(Decimal(analysis["summary"]["net_notional"]))
    assert analysis["assets"][0]["asset"] == "BTC"
    custom = client.post(
        "/api/v1/pro/execution/analytics",
        json={"source": "example", "scenarios": [{"name": "BTC down", "asset_pct": {"BTC": "-20"}}]},
    )
    assert custom.status_code == 200, custom.text
    assert len(custom.json()["scenarios"]) == 1
    after = client.get("/api/v1/pro/execution/account?source=example").json()
    assert {key: after[key] for key in ("cash", "positions", "used_margin", "insurance_debt")} == {
        key: before[key] for key in ("cash", "positions", "used_margin", "insurance_debt")
    }
    assert client.get("/api/v1/pro/execution/ledger?source=example").json() == ledger
    rejected = client.post(
        "/api/v1/pro/execution/analytics",
        json={"source": "example", "scenarios": [{"name": "Invalid zero mark", "parallel_pct": "-100"}]},
    )
    assert rejected.status_code == 422


def test_research_comparison_distinguishes_captured_tiers_and_funding_marks(client, monkeypatch):
    trade = dataset(client, symbol="BTC-USDT-SWAP")
    mark = dataset(client, "mark", "BTC-USDT-SWAP")
    funding = dataset(client, "funding", "BTC-USDT-SWAP")
    runtime = client.app.state.professional
    original_tiers = runtime.catalog.get_margin_tiers
    original_mark = runtime.catalog._settlement_mark
    config = {"dataset_id": trade, "mark_dataset_id": mark, "funding_dataset_id": funding}

    def run():
        response = client.post("/api/v1/pro/research/runs", json=config)
        assert response.status_code == 202, response.text
        return wait(client, f"/api/v1/pro/research/runs/{response.json()['id']}")

    def compare(left, right):
        response = client.get(f"/api/v1/pro/research/compare?ids={left['id']},{right['id']}")
        assert response.status_code == 200, response.text
        return response.json()

    first = run()
    changed_tier = False

    async def tiers(*args, **kwargs):
        result = await original_tiers(*args, **kwargs)
        result["observed_at"] += 1000
        if changed_tier:
            result["tiers"][0]["mmr"] = Decimal("0.006")
        return result

    monkeypatch.setattr(runtime.catalog, "get_margin_tiers", tiers)
    timestamp_only = run()
    assert compare(first, timestamp_only)["comparable_inputs"] is True
    changed_tier = True
    different_tier = run()
    comparison = compare(first, different_tier)
    assert comparison["comparable_inputs"] is False
    assert "maintenance_tiers" in comparison["different_assumptions"]
    assert comparison["warning"]

    async def settlement_mark(*args, **kwargs):
        result = await original_mark(*args, **kwargs)
        return result | {"mark_price": Decimal(str(result["mark_price"])) * Decimal("1.01")}

    monkeypatch.setattr(runtime.catalog, "_settlement_mark", settlement_mark)
    different_funding_mark = run()
    assert "funding_observations" in compare(different_tier, different_funding_mark)["different_assumptions"]


def test_second_app_cannot_initialize_or_migrate_an_owned_workspace(tmp_path, monkeypatch):
    from tidebench.store import Store

    configuration = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)
    with TestClient(create_app(configuration)) as owner:
        with owner.app.state.store.write() as conn:
            conn.execute("UPDATE schema_version SET version=2")
            conn.execute("UPDATE pro_accounts SET cash='9876.54' WHERE source='example'")

        def forbidden_initialize(self):
            pytest.fail("A second owner must be rejected before any initialization or migration")

        monkeypatch.setattr(Store, "initialize", forbidden_initialize)
        with pytest.raises(RuntimeError, match="active Tidebench process"):
            create_app(configuration)
        with owner.app.state.store.read() as conn:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
            assert (
                conn.execute("SELECT cash FROM pro_accounts WHERE source='example'").fetchone()[0]
                == "9876.54"
            )


def test_constructor_failure_releases_exclusive_lease(tmp_path, monkeypatch):
    import tidebench.main as main
    from tidebench.store import Store

    configuration = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)

    def fail(*args):
        raise RuntimeError("Injected initialization failure")

    monkeypatch.setattr(main, "ProfessionalRuntime", fail)
    with pytest.raises(RuntimeError, match="Injected initialization failure"):
        create_app(configuration)
    next_owner = Store(configuration.database, acquire_lock=True)
    next_owner.release_process_lock()


@pytest.mark.parametrize("artifact", ["result", "snapshot"])
def test_replay_rejects_artifact_checksum_mismatch_before_queueing(client, artifact):
    import json

    from tidebench.store import dumps

    response = client.post("/api/v1/pro/research/runs", json={"dataset_id": dataset(client)})
    original = wait(client, f"/api/v1/pro/research/runs/{response.json()['id']}")
    runtime = client.app.state.professional
    with runtime.store.write() as conn:
        row = conn.execute(f"SELECT {artifact} FROM pro_runs WHERE id=?", (original["id"],)).fetchone()
        value = json.loads(row[0])
        value["unexpected_artifact_change"] = True
        conn.execute(f"UPDATE pro_runs SET {artifact}=? WHERE id=?", (dumps(value), original["id"]))
        count = conn.execute("SELECT COUNT(*) FROM pro_runs").fetchone()[0]
    replay = client.post(f"/api/v1/pro/research/runs/{original['id']}/replay")
    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "run_artifact_integrity"
    with runtime.store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pro_runs").fetchone()[0] == count


def test_importing_app_factory_does_not_initialize_workspace(tmp_path):
    import os
    import subprocess
    import sys

    data_dir = tmp_path / "uninitialized"
    subprocess.run(
        [sys.executable, "-c", "import tidebench.main"],
        env={**os.environ, "TIDEBENCH_DATA_DIR": str(data_dir)},
        check=True,
        capture_output=True,
        timeout=20,
    )
    assert not data_dir.exists()


def test_route_assembly_failure_releases_exclusive_lease(tmp_path, monkeypatch):
    import tidebench.main as main
    from tidebench.store import Store

    configuration = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False)

    def fail(*args):
        raise RuntimeError("Injected route assembly failure")

    monkeypatch.setattr(main, "professional_router", fail)
    with pytest.raises(RuntimeError, match="Injected route assembly failure"):
        create_app(configuration)
    next_owner = Store(configuration.database, acquire_lock=True)
    next_owner.release_process_lock()


def test_database_symlink_alias_cannot_acquire_a_second_lease(tmp_path):
    configuration = Settings(data_dir=tmp_path / "primary", _env_file=None, worker_enabled=False)
    with TestClient(create_app(configuration)) as owner:
        alias = Settings(data_dir=tmp_path / "alias", _env_file=None, worker_enabled=False)
        alias.data_dir.mkdir()
        alias.database.symlink_to(configuration.database)
        with pytest.raises(RuntimeError, match="active Tidebench process"):
            create_app(alias)
        assert owner.app.state.store.path == alias.database.resolve()


def test_database_hard_link_cannot_create_an_independent_writer(tmp_path):
    import os

    configuration = Settings(data_dir=tmp_path / "primary", _env_file=None, worker_enabled=False)
    with TestClient(create_app(configuration)):
        alias = Settings(data_dir=tmp_path / "alias", _env_file=None, worker_enabled=False)
        alias.data_dir.mkdir()
        os.link(configuration.database, alias.database)
        with pytest.raises(RuntimeError, match="Database hard links are unsupported"):
            create_app(alias)


def test_verified_restore_preserves_managed_commands_contributions_and_stops_group(client):
    from test_managed_portfolios import approve, research

    runtime = client.app.state.professional
    run = client.portal.call(research, runtime)
    release = approve(runtime, run)
    group = runtime.portfolio_releases.activate(release["id"], "operator")["group_id"]
    client.portal.call(runtime.managed_portfolios.evaluate, group)
    batch = runtime.managed_portfolios.history(group)[0]
    assert batch["status"] == "completed"
    before = client.get("/api/v1/pro/execution/contributions?source=example").json()
    assert before["reconciled"] and before["owners"]
    backup = client.post("/api/v1/pro/ops/backups/create").json()
    assert client.get(f"/api/v1/pro/ops/backups/{backup['id']}/verify").json()["database_schema"] == 7
    response = client.post(
        "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
    )
    assert response.status_code == 200, response.text
    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-only-password-123"}
    )
    assert login.status_code == 200
    after = client.get("/api/v1/pro/execution/contributions?source=example").json()
    assert after["owners"] == before["owners"] and after["reconciled"]
    assert runtime.managed_portfolios.get(group)["status"] == "stopped"
    assert all(row["status"] == "stopped" for row in runtime.deployments())
    assert runtime.managed_portfolios.history(group)[0]["body"] == batch["body"]
    assert len(runtime.managed_portfolios.history(group)[0]["commands"]) == len(batch["commands"])
    assert client.get("/api/v1/pro/execution/risk?source=example").json()["halted"]


def test_instrument_evidence_api_and_restore_retain_newer_observations(client):
    import hashlib
    import json

    from tidebench.store import dumps

    path = "/api/v1/pro/catalog/instrument-observations"
    first = client.post(path + "?source=example&inst_type=SPOT")
    assert first.status_code == 201, first.text
    first = first.json()
    assert first["row_count"] == 5 and first["counts"] == {"eligible": 5}
    assert client.get(path + "/00000000000000000000000000000000").status_code == 404
    exported = client.get(path + f"/{first['id']}/export").json()
    assert isinstance(exported["observation"]["received_ns"], str)
    assert hashlib.sha256(dumps(exported["observation"]).encode()).hexdigest() == exported["content_hash"]
    assert json.loads(json.dumps(exported)) == exported
    unknown = client.get("/api/v1/pro/catalog/instrument-universe?source=example&as_of=1577836800000").json()
    assert unknown["reason"] == "no_prior_observation" and not unknown["members"]
    assert (
        client.get("/api/v1/pro/catalog/instrument-universe?as_of=999999999999999999999999").status_code
        == 422
    )
    backup = client.post("/api/v1/pro/ops/backups/create").json()
    second = client.post(path + "?source=example&inst_type=SPOT").json()
    assert second["id"] != first["id"] and second["payload_hash"] == first["payload_hash"]
    response = client.post(
        "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
    )
    assert response.status_code == 200, response.text
    assert client.get(path + "?source=example").status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-only-password-123"}
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    retained = client.get(path + "?source=example").json()["items"]
    assert [r["id"] for r in retained] == [second["id"], first["id"]]
    assert client.get(path + f"/{second['id']}/export").json()["content_hash"] == second["content_hash"]
    assert client.get(path + f"/{second['id']}/diff?previous={first['id']}").json()["changes"] == []
    assert client.get(path + "?source=example&inst_type=SWAP").json()["items"] == []


def test_restore_rejects_conflicting_instrument_evidence_before_replacement(client):
    import json

    from tidebench.instrument_observations import digest
    from tidebench.store import dumps

    path = "/api/v1/pro/catalog/instrument-observations"
    original = client.post(path + "?source=example").json()
    backup = client.post("/api/v1/pro/ops/backups/create").json()
    with client.app.state.store.write() as conn:
        body = json.loads(
            conn.execute("SELECT body FROM instrument_observations WHERE id=?", (original["id"],)).fetchone()[
                0
            ]
        )
        body["rows"][0]["tickSz"] = "0.2"
        body["payload_hash"] = digest(body["rows"])
        conn.execute("DROP TRIGGER instrument_observations_no_update")
        conn.execute(
            "UPDATE instrument_observations SET body=?,content_hash=? WHERE id=?",
            (dumps(body), digest(body), original["id"]),
        )
        conn.execute("UPDATE workspace_users SET display_name='Later retained identity'")
    result = client.post(
        "/api/v1/pro/ops/restore", json={"backup_id": backup["id"], "confirmation": "RESTORE"}
    )
    assert result.status_code == 409 and result.json()["error"]["code"] == "backup_integrity", result.text
    with client.app.state.store.read() as conn:
        assert (
            conn.execute("SELECT display_name FROM workspace_users").fetchone()[0]
            == "Later retained identity"
        )
        assert conn.execute(
            "SELECT content_hash FROM instrument_observations WHERE id=?", (original["id"],)
        ).fetchone()[0] == digest(body)
