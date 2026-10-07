import time
from decimal import Decimal

from fastapi.testclient import TestClient
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.market import MarketError, MarketService


def settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, _env_file=None, **kwargs)


def wait_for_run(client, run_id):
    for _ in range(100):
        run = client.get(f"/api/v1/backtests/{run_id}").json()
        if run["status"] in {"completed", "failed"}:
            return run
        time.sleep(0.02)
    raise AssertionError("Research job did not finish")


def test_complete_research_replay_and_paper_workflow(tmp_path):
    with TestClient(create_app(settings(tmp_path), market=BrokenMarket())) as client:
        data = client.get("/api/v1/market/candles?source=example&limit=100").json()
        assert len(data["candles"]) == 100
        assert data["source"] == "example" and len(data["dataset_hash"]) == 64
        queued = client.post("/api/v1/backtests", json={"source": "example", "limit": 100})
        assert queued.status_code == 202
        run = wait_for_run(client, queued.json()["id"])
        assert run["status"] == "completed", run
        assert run["manifest"]["dataset_hash"] == data["dataset_hash"]
        assert run["result"]["metrics"]["sharpe"] is None
        export = client.get(f"/api/v1/backtests/{run['id']}/export")
        assert export.status_code == 200
        assert len(export.json()["snapshot"]["candles"]) == 100
        replay = client.post(f"/api/v1/backtests/{run['id']}/replay")
        replayed = wait_for_run(client, replay.json()["id"])
        assert replayed["result"] == run["result"]
        assert replayed["manifest"]["replay_of"] == run["id"]
        order = {"source": "example", "inst_id": "BTC-USDT", "side": "buy", "quantity": "0.001"}
        first = client.post(
            "/api/v1/paper/orders", json=order, headers={"Idempotency-Key": "test-command-01"}
        )
        assert first.status_code == 201, first.text
        again = client.post(
            "/api/v1/paper/orders", json=order, headers={"Idempotency-Key": "test-command-01"}
        )
        assert again.status_code == 200 and again.json() == first.json()
        account = client.get("/api/v1/paper/account?source=example").json()
        assert len(account["positions"]) == 1
        assert Decimal(account["cash"]) < 10000
        assert client.get("/api/v1/paper/account?source=okx").json()["cash"] == "10000"
        halt = client.post(
            "/api/v1/risk/kill-switch",
            json={"source": "example", "active": True, "reason": "Integration test halt"},
        )
        assert halt.json()["kill_switch"] is True
        blocked = client.post(
            "/api/v1/paper/orders", json=order, headers={"Idempotency-Key": "test-command-02"}
        )
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "desk_halted"
        replay_after_halt = client.post(
            "/api/v1/paper/orders", json=order, headers={"Idempotency-Key": "test-command-01"}
        )
        assert replay_after_halt.status_code == 200
        assert client.get("/api/v1/audit?source=example").json()["items"]
    # A fresh process/app preserves accounts, risk, jobs and command deduplication.
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/v1/risk?source=example").json()["kill_switch"] is True
        assert len(client.get("/api/v1/paper/orders?source=example").json()["items"]) == 1
        assert client.get(f"/api/v1/backtests/{run['id']}").json()["status"] == "completed"


def test_auth_origin_host_validation_and_no_exchange_keys(tmp_path):
    token = "a-secret-test-token-at-least-32-chars"
    with TestClient(create_app(settings(tmp_path, api_token=token, worker_enabled=False))) as client:
        assert client.get("/api/v1/system").json()["auth_required"] is True
        assert client.get("/api/v1/risk").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/risk", headers=headers).status_code == 200
        assert (
            client.get("/api/v1/risk", headers=headers | {"Origin": "https://hostile.example"}).status_code
            == 403
        )
        assert client.get("/api/v1/risk", headers=headers | {"Host": "hostile.example"}).status_code == 400
        invalid = client.post(
            "/api/v1/backtests", headers=headers, json={"source": "example", "apikey": "do-not-store"}
        )
        assert invalid.status_code == 422
        assert "do-not-store" not in invalid.text
        assert client.get("/api/v1/market/candles?source=other", headers=headers).status_code == 422
        assert (
            client.post(
                "/api/v1/paper/orders",
                headers=headers,
                json={"source": "example", "inst_id": "BTC-USDT", "side": "buy", "quantity": "NaN"},
            ).status_code
            == 422
        )


def test_deploy_stop_duplicate_and_restart(tmp_path):
    body = {"source": "example", "inst_id": "BTC-USDT", "bar": "1H", "strategy": {"kind": "buy_hold"}}
    with TestClient(create_app(settings(tmp_path, worker_enabled=False))) as client:
        first = client.post("/api/v1/paper/deployments", json=body)
        assert first.status_code == 201
        assert client.post("/api/v1/paper/deployments", json=body).status_code == 409
        deployment_id = first.json()["id"]
        stopped = client.post(f"/api/v1/paper/deployments/{deployment_id}/stop")
        assert stopped.json()["status"] == "stopped"
        assert client.post("/api/v1/paper/deployments", json=body).status_code == 201
    with TestClient(create_app(settings(tmp_path, worker_enabled=False))) as client:
        active = client.get("/api/v1/paper/deployments?source=example").json()["items"]
        assert sum(d["status"] == "running" for d in active) == 1


class BrokenMarket(MarketService):
    async def get_tickers(self, source="okx"):
        if source == "okx":
            raise MarketError("network_error", "Exchange unavailable")
        return await super().get_tickers(source)


def test_no_silent_example_fallback_and_balances_survive_outage(tmp_path):
    with TestClient(create_app(settings(tmp_path, worker_enabled=False), market=BrokenMarket())) as client:
        market = client.get("/api/v1/market/tickers?source=okx")
        assert market.status_code == 502 and market.json()["error"]["code"] == "network_error"
        account = client.get("/api/v1/paper/account?source=okx").json()
        assert account["valuation_status"] == "unavailable" and account["cash"] == "10000"
        assert account["positions"] == []


def test_resumable_queued_job_uses_captured_snapshot(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        queued = client.post("/api/v1/backtests", json={"source": "example", "limit": 60}).json()
        completed = wait_for_run(client, queued["id"])
        assert completed["status"] == "completed"
        store = client.app.state.store
        with store.write() as conn:
            conn.execute("UPDATE runs SET status='running',result=NULL WHERE id=?", (completed["id"],))
    with TestClient(create_app(settings(tmp_path))) as client:
        recovered = wait_for_run(client, completed["id"])
        assert recovered["status"] == "completed"
        assert recovered["result"] == completed["result"]
