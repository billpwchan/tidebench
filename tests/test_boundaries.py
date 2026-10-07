"""Bounded API input and atomic queue capacity, without exchange access."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from tidebench.config import Settings
from tidebench.main import create_app
from tidebench.schemas import RunInput
from tidebench.store import QueueFullError, Store, dumps, encode

TOKEN = "boundary-test-token-longer-than-thirty-two-characters"


class NoNetworkMarket:
    async def close(self):
        pass

    async def get_tickers(self, *args):
        raise AssertionError("boundary test must not request market data")

    async def get_instruments(self, *args):
        raise AssertionError("boundary test must not request market data")

    async def get_candles(self, *args):
        raise AssertionError("boundary test must not request market data")


@pytest.fixture
def app(tmp_path):
    settings = Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False, api_token=TOKEN)
    return create_app(settings, market=NoNetworkMarket())


def client(app, remote="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(remote, 12345)),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


@pytest.mark.asyncio
async def test_non_ascii_authorization_is_unauthorized_instead_of_server_error(app):
    async with client(app) as session:
        response = await session.get("/api/v1/risk", headers=[(b"authorization", b"Bearer \xffwrong-token")])
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert TOKEN not in response.text


@pytest.mark.asyncio
async def test_large_content_length_is_rejected_before_consuming_stream(app):
    consumed = []

    async def body():
        consumed.append(True)
        yield b"x" * 70_000

    async with client(app) as session:
        response = await session.post(
            "/api/v1/backtests",
            content=body(),
            headers={"Content-Length": "70000", "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert consumed == []


@pytest.mark.asyncio
async def test_oversized_chunked_body_stops_consumption_at_first_excess_chunk(app):
    consumed = []

    async def body():
        for index in range(10):
            consumed.append(index)
            yield b"x" * 16_384

    async with client(app) as session:
        # The public system route must obey the same cap even without credentials.
        response = await session.post("/api/v1/system", content=body(), headers={"Authorization": ""})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert consumed == list(range(5))


@pytest.mark.asyncio
async def test_exact_body_limit_reaches_json_validation_and_queue(app):
    payload = b'{"source":"example","limit":60}'
    payload += b" " * (65_536 - len(payload))
    async with client(app) as session:
        response = await session.post(
            "/api/v1/backtests", content=payload, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "length,status,code",
    [
        ("not-an-integer", 400, "invalid_content_length"),
        ("-1", 413, "request_too_large"),
    ],
)
async def test_malformed_content_lengths_are_bounded_errors(app, length, status, code):
    async with client(app) as session:
        response = await session.post("/api/v1/backtests", content=b"{}", headers={"Content-Length": length})
    assert response.status_code == status and response.json()["error"]["code"] == code


def test_concurrent_queue_admission_cannot_exceed_ten_pending_jobs(tmp_path):
    store = Store(tmp_path / "queue.sqlite3")
    barrier = Barrier(20)
    configuration = encode(RunInput(source="example", limit=60).model_dump())

    def enqueue():
        barrier.wait(timeout=5)
        try:
            return store.create_run(configuration)["id"]
        except QueueFullError:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(enqueue) for _ in range(20)]]
    assert sum(result is not None for result in results) == 10
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE status='queued'").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM audit WHERE kind='research.queued'").fetchone()[0] == 10


@pytest.mark.asyncio
async def test_replay_uses_the_same_queue_capacity_guard(app):
    store = app.state.store
    configuration = encode(RunInput(source="example", limit=60).model_dump())
    original = store.create_run(configuration)
    with store.write() as conn:
        conn.execute(
            "UPDATE runs SET status='completed',snapshot=?,manifest=?,result=? WHERE id=?",
            (dumps({"candles": []}), dumps({"dataset_hash": "test-snapshot"}), dumps({}), original["id"]),
        )
    for _ in range(10):
        store.create_run(configuration)
    async with client(app) as session:
        replay = await session.post(f"/api/v1/backtests/{original['id']}/replay")
        new = await session.post("/api/v1/backtests", json={"source": "example", "limit": 60})
    assert replay.status_code == new.status_code == 429
    assert replay.json()["error"]["code"] == new.json()["error"]["code"] == "queue_full"
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 11


@pytest.mark.asyncio
async def test_unauthenticated_remote_client_cannot_use_loopback_mode(tmp_path):
    app = create_app(
        Settings(data_dir=tmp_path, _env_file=None, worker_enabled=False, api_token=""),
        market=NoNetworkMarket(),
    )
    async with client(app, remote="203.0.113.8") as session:
        private = await session.get("/api/v1/risk")
        public = await session.get("/api/v1/system")
    assert private.status_code == 403 and private.json()["error"]["code"] == "local_only"
    assert public.status_code == 200 and public.json()["auth_required"] is False
