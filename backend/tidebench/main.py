import asyncio
import logging
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .data_packages import DataPackageError
from .engine import EngineError
from .market import MarketError, MarketService
from .paper import DeskError, PaperDesk, order_payload
from .platform import AccessService, PlatformError
from .portfolio_analytics import PortfolioAnalyticsError
from .pro_api import professional_router
from .pro_service import ProfessionalRuntime
from .schemas import (
    Bar,
    DeploymentInput,
    KillInput,
    OrderInput,
    RiskInput,
    RunInput,
    Source,
    StrategyInput,
    Symbol,
)
from .store import QueueFullError, Store, dumps, encode, new_id, now_ms
from .worker import Supervisor, dataset_hash


def create_app(settings: Settings | None = None, market: MarketService | None = None):
    settings = settings or Settings()
    store = Store(settings.database, acquire_lock=True)
    try:
        return _build_app(settings, market, store)
    except BaseException:
        store.release_process_lock()
        raise


def _build_app(settings, market, store):
    market = market or MarketService(region=settings.region)
    desk = PaperDesk(store)
    supervisor = Supervisor(store, market, desk, settings)
    access = AccessService(store, settings)
    professional = ProfessionalRuntime(store, market, settings)
    # Additive module migrations are idempotent. Publish the schema version
    # only after every table has been created; interrupted upgrades retry safely.
    with store.write() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at INTEGER NOT NULL,description TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(2,?,'Authenticated professional workspace, catalog, research and unified execution')",
            (now_ms(),),
        )
        conn.execute("UPDATE schema_version SET version=2")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(3,?,'Immutable research packages, materialized run summaries and verified result identities')",
            (now_ms(),),
        )
        conn.execute("UPDATE schema_version SET version=3")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(4,?,'Strategy lineage, shared-capital research, governance, incremental forward evidence and operational incidents')",
            (now_ms(),),
        )
        conn.execute("UPDATE schema_version SET version=4")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(5,?,'Reviewed managed portfolios, durable multi-leg execution and transactional economic contribution sleeves')",
            (now_ms(),),
        )
        conn.execute("UPDATE schema_version SET version=5")

    @asynccontextmanager
    async def lifespan(app):
        store.acquire_process_lock()
        try:
            if settings.worker_enabled:
                await supervisor.start()
                await professional.start()
            yield
        finally:
            app.state.stopping = True
            app.state.maintenance = True
            await supervisor.stop()
            await professional.stop()
            await market.close()
            store.release_process_lock()

    app = FastAPI(
        title="Tidebench API",
        version=__version__,
        lifespan=lifespan,
        description="Authenticated research and local spot / linear USDT perpetual simulation workspace.",
    )
    app.state.store, app.state.market, app.state.desk = store, market, desk
    app.state.supervisor, app.state.settings = supervisor, settings
    app.state.access, app.state.professional = access, professional
    app.state.maintenance, app.state.active_requests, app.state.stopping = False, 0, False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Bootstrap-Token",
        ],
        allow_credentials=True,
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    def error(request, code, message, status):
        return JSONResponse(
            {
                "error": {"code": code, "message": message},
                "request_id": getattr(request.state, "request_id", new_id()),
            },
            status_code=status,
        )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request.state.request_id = new_id()
        started = time.monotonic()
        app.state.active_requests += 1
        response = None
        try:
            response = await enforce_boundaries(request, call_next)
            return response
        finally:
            app.state.active_requests -= 1
            route = request.scope.get("route")
            professional.metrics.record(
                request.method,
                getattr(route, "path", "unmatched"),
                response.status_code if response else 500,
                time.monotonic() - started,
            )
            if response is not None:
                response.headers["X-Request-ID"] = request.state.request_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                )
                if settings.cookie_secure:
                    response.headers["Strict-Transport-Security"] = "max-age=31536000"
                if request.url.path.startswith("/api/"):
                    response.headers["Cache-Control"] = "no-store"

    async def enforce_boundaries(request, call_next):
        path = request.url.path
        if request.url.hostname not in settings.hosts:
            return error(request, "invalid_host", "This host is not allowed.", 400)
        if app.state.maintenance:
            return error(request, "maintenance", "Workspace recovery is in progress.", 503)
        if path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in settings.origins:
                return error(
                    request, "origin_forbidden", "This origin is not allowed to access the workspace.", 403
                )
            public = path in {
                "/api/v1/system",
                "/api/v1/auth/status",
                "/api/v1/auth/session",
                "/api/v1/auth/login",
                "/api/v1/auth/setup",
            }
            user, token = access.principal(request)
            request.state.user, request.state.session_token = user, token
            unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
            if not public:
                if not user:
                    return error(request, "unauthorized", "Sign in to the workspace.", 401)
                if not settings.auth_enabled:
                    if settings.api_token and not secrets.compare_digest(
                        request.headers.get("authorization", "").encode(),
                        ("Bearer " + settings.api_token).encode(),
                    ):
                        return error(
                            request, "unauthorized", "Enter your workspace access token in Settings.", 401
                        )
                    if not settings.api_token and (
                        not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}
                    ):
                        return error(
                            request, "local_only", "Authentication is required for remote clients.", 403
                        )
                if (
                    token
                    and unsafe
                    and not secrets.compare_digest(
                        request.headers.get("x-csrf-token", "").encode("utf-8"),
                        access.csrf(token).encode("ascii"),
                    )
                ):
                    return error(
                        request, "csrf_required", "Refresh your session before sending this command.", 403
                    )
                role = user["role"]
                allowed = {"admin", "trader", "researcher", "viewer", "risk_operator"}
                if (
                    path.startswith("/api/v1/auth/users")
                    or path.endswith("/metrics")
                    or (unsafe and path.startswith("/api/v1/pro/ops/"))
                ):
                    allowed = {"admin"}
                elif unsafe:
                    if path == "/api/v1/pro/execution/analytics":
                        allowed = {"admin", "trader", "researcher", "viewer", "risk_operator"}
                    elif "/risk" in path or path.endswith("/halt"):
                        allowed = {"admin", "trader", "risk_operator"}
                    elif "/execution/" in path or "/paper/" in path:
                        allowed = {"admin", "trader"}
                    elif "/research/" in path or "/catalog/" in path or "/backtests" in path:
                        allowed = {"admin", "trader", "researcher"}
                    elif path.startswith(("/api/v1/pro/strategies", "/api/v1/pro/portfolio-strategies")):
                        allowed = {"admin", "trader", "researcher"}
                    elif path.endswith("/logout"):
                        allowed = {role}
                    elif path.endswith("/auth/password"):
                        allowed = {role}
                    else:
                        allowed = {"admin"}
                if role not in allowed:
                    return error(request, "role_forbidden", "Your role cannot perform this operation.", 403)
            if request.method in {"POST", "PUT", "PATCH"}:
                maximum = 10 * 1024 * 1024 if path == "/api/v1/pro/catalog/import" else 65536
                length = request.headers.get("content-length")
                if length:
                    try:
                        if not 0 <= int(length) <= maximum:
                            return error(
                                request, "request_too_large", "Request body exceeds the endpoint limit.", 413
                            )
                    except ValueError:
                        return error(
                            request,
                            "invalid_content_length",
                            "Content-Length must be a nonnegative integer.",
                            400,
                        )
                chunks, received = [], 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > maximum:
                        return error(
                            request, "request_too_large", "Request body exceeds the endpoint limit.", 413
                        )
                    chunks.append(chunk)
                request._body = b"".join(chunks)
        return await call_next(request)

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return error(request, exc.code, exc.message, exc.status)

    @app.exception_handler(DataPackageError)
    async def data_package_error(request, exc):
        return error(request, exc.code, exc.message, exc.status)

    @app.exception_handler(PortfolioAnalyticsError)
    async def portfolio_analytics_error(request, exc):
        return error(request, "invalid_portfolio_analysis", str(exc), 422)

    @app.exception_handler(DeskError)
    async def desk_error(request, exc):
        return error(request, exc.code, exc.message, exc.status)

    @app.exception_handler(MarketError)
    async def market_error(request, exc):
        code = getattr(exc, "code", "market_unavailable")
        if code in {"dataset_not_found", "job_not_found", "instrument_unavailable"}:
            status = 404
        elif code == "catalog_queue_full":
            status = 429
        elif code in {"catalog_running", "dataset_integrity_error", "region_mismatch", "conflicting_history"}:
            status = 409
        elif (
            code.startswith("invalid_")
            and code != "invalid_upstream_data"
            or code
            in {
                "unaligned_range",
                "future_range",
                "dataset_too_large",
                "unsupported_contract",
                "wrong_dataset_kind",
            }
        ):
            status = 422
        else:
            status = 502
        return error(request, code, str(exc), status)

    @app.exception_handler(EngineError)
    async def engine_error(request, exc):
        return error(request, "invalid_dataset", str(exc), 422)

    @app.exception_handler(QueueFullError)
    async def queue_full(request, exc):
        return error(request, "queue_full", str(exc), 429)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors can contain user input. Only return paths and messages, never submitted credentials.
        messages = [".".join(str(x) for x in e["loc"]) + ": " + e["msg"] for e in exc.errors()]
        return error(request, "validation_error", "; ".join(messages), 422)

    @app.exception_handler(sqlite3.OperationalError)
    async def db_error(request, exc):
        logging.getLogger("tidebench.api").exception("Storage operation failed")
        return error(
            request,
            "storage_unavailable",
            "Storage is temporarily unavailable. Retry with the same idempotency key.",
            503,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger("tidebench.api").exception(
            "Unhandled request failure; request_id=%s", getattr(request.state, "request_id", "unknown")
        )
        return error(
            request, "internal_error", "The request failed. Retain the request ID for investigation.", 500
        )

    @app.get("/healthz", include_in_schema=False)
    def health():
        with store.read() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    def ready():
        with store.read() as conn:
            conn.execute("SELECT 1").fetchone()
        workers = not settings.worker_enabled or professional.workers_healthy()
        return JSONResponse(
            {
                "status": "ready" if workers else "degraded",
                "database": "ok",
                "workers": workers,
                "version": __version__,
            },
            status_code=200 if workers else 503,
        )

    @app.get("/api/v1/system")
    def system():
        return {
            "name": "Tidebench",
            "version": __version__,
            "market_region": settings.region,
            "execution": "local-paper",
            "auth_required": settings.auth_enabled or bool(settings.api_token),
            "time": now_ms(),
            "capabilities": [
                "public-market-data",
                "deterministic-backtests",
                "snapshot-replay",
                "local-paper",
                "paper-strategies",
                "risk-limits",
                "audit-log",
                "versioned-data-catalog",
                "linear-usdt-perpetuals",
                "walk-forward-research",
                "native-asset-journal",
                "session-rbac",
                "verified-recovery",
                "immutable-research-packages",
                "captured-portfolio-stress",
                "result-hash-verification",
            ],
        }

    @app.get("/api/v1/strategies")
    def strategies():
        info = [
            (
                "sma_cross",
                "Trend following",
                "Enter when the fast simple moving average exceeds the slow average; exit when it does not.",
            ),
            (
                "rsi_reversion",
                "RSI reversion",
                "Enter below the RSI entry threshold; exit above the exit threshold. Neutral values hold the position.",
            ),
            (
                "buy_hold",
                "Buy & hold",
                "Enter once after the first close and hold. A baseline template, not an alpha claim.",
            ),
        ]
        return {
            "items": [
                {
                    "kind": k,
                    "name": n,
                    "description": d,
                    "defaults": encode(StrategyInput(kind=k).model_dump()),
                }
                for k, n, d in info
            ]
        }

    @app.get("/api/v1/market/instruments")
    async def instruments(source: Source = "okx"):
        return {"source": source, "items": encode(await market.get_instruments(source))}

    @app.get("/api/v1/market/tickers")
    async def tickers(source: Source = "okx"):
        return await market.get_tickers(source)

    @app.get("/api/v1/market/candles")
    async def candles(
        source: Source = "okx",
        inst_id: Symbol = "BTC-USDT",
        bar: Bar = "1H",
        limit: int = Query(default=720, ge=60, le=2000),
    ):
        result = await market.get_candles(inst_id, bar, limit, source)
        return encode(result) | {
            "inst_id": inst_id,
            "bar": bar,
            "dataset_hash": dataset_hash(result["candles"]),
        }

    @app.post("/api/v1/backtests", status_code=202)
    async def backtest(body: RunInput):
        result = store.create_run(encode(body.model_dump()))
        supervisor.wake.set()
        return result

    @app.get("/api/v1/backtests")
    def runs(source: Source = "okx"):
        return {"items": store.runs(source)}

    def require_run(run_id, snapshot=False):
        result = store.run(run_id, include_snapshot=snapshot)
        if result is None:
            raise DeskError("run_not_found", "This research run was not found.", 404)
        return result

    @app.get("/api/v1/backtests/{run_id}")
    def run(run_id: str):
        return require_run(run_id)

    @app.get("/api/v1/backtests/{run_id}/export")
    def export(run_id: str):
        result = require_run(run_id, snapshot=True)
        if result["status"] != "completed":
            raise DeskError("run_not_completed", "Only completed runs can be exported.")
        return JSONResponse(
            result, headers={"Content-Disposition": f'attachment; filename="tidebench-{result["id"]}.json"'}
        )

    @app.post("/api/v1/backtests/{run_id}/replay", status_code=202)
    async def replay(run_id: str):
        result = require_run(run_id, snapshot=True)
        if result["status"] != "completed" or result["snapshot"] is None:
            raise DeskError("run_not_replayable", "Replay requires a completed run with a saved dataset.")
        replayed = store.create_run(
            result["config"], result["snapshot"], result["manifest"] | {"replay_of": run_id}
        )
        supervisor.wake.set()
        return replayed

    @app.get("/api/v1/paper/account")
    async def account(source: Source = "okx"):
        try:
            data = await market.get_tickers(source)
        except MarketError:
            data = None
        return await asyncio.to_thread(desk.account, source, data)

    @app.get("/api/v1/paper/orders")
    def orders(source: Source = "okx"):
        return {"items": store.orders(source)}

    @app.post("/api/v1/paper/orders")
    async def place_order(body: OrderInput, idempotency_key: str = Header(alias="Idempotency-Key")):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
            raise DeskError(
                "invalid_idempotency_key",
                "Use 8–128 alphanumeric, dash, dot, colon or underscore characters.",
                422,
            )
        # A retry of a committed command works during an exchange outage or after a halt.
        state, existing = store.existing_order(body.source, idempotency_key, order_payload(body))
        if state == "conflict":
            raise DeskError("idempotency_conflict", "This idempotency key was used with a different order.")
        if existing:
            return JSONResponse(existing, status_code=200)
        data, metadata = await asyncio.gather(
            market.get_tickers(body.source), market.get_instruments(body.source)
        )
        instrument = next((item for item in metadata if item.inst_id == body.inst_id), None)
        if instrument is None:
            raise DeskError("instrument_unavailable", "Instrument rules are unavailable for this region.")
        result, replayed = await asyncio.to_thread(desk.place, body, idempotency_key, instrument, data)
        return JSONResponse(result, status_code=200 if replayed else 201)

    @app.get("/api/v1/paper/deployments")
    def deployments(source: Source = "okx"):
        return {"items": store.deployments(source)}

    @app.post("/api/v1/paper/deployments", status_code=201)
    def deploy(body: DeploymentInput):
        identifier, ts = new_id(), now_ms()
        try:
            with store.write() as conn:
                count = conn.execute("SELECT COUNT(*) FROM deployments WHERE status='running'").fetchone()[0]
                if count >= 10:
                    raise DeskError(
                        "deployment_limit", "At most ten paper strategies can run in this workspace.", 429
                    )
                if conn.execute("SELECT kill_switch FROM risk WHERE source=?", (body.source,)).fetchone()[0]:
                    raise DeskError(
                        "desk_halted", "Resume this paper desk in Risk before starting a strategy."
                    )
                conn.execute(
                    "INSERT INTO deployments(id,source,inst_id,bar,strategy,status,created_at,updated_at) VALUES(?,?,?,?,?,'running',?,?)",
                    (
                        identifier,
                        body.source,
                        body.inst_id,
                        body.bar,
                        dumps(body.strategy.model_dump()),
                        ts,
                        ts,
                    ),
                )
                store.audit(
                    conn,
                    body.source,
                    "strategy.started",
                    "Paper strategy started",
                    {"deployment_id": identifier, **body.model_dump()},
                )
        except sqlite3.IntegrityError:
            raise DeskError(
                "strategy_already_running",
                "Stop the existing strategy for this market before starting another.",
            ) from None
        return next(d for d in store.deployments(body.source) if d["id"] == identifier)

    @app.post("/api/v1/paper/deployments/{deployment_id}/stop")
    def stop(deployment_id: str):
        with store.write() as conn:
            row = conn.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
            if row is None:
                raise DeskError("deployment_not_found", "This paper strategy was not found.", 404)
            conn.execute(
                "UPDATE deployments SET status='stopped',updated_at=? WHERE id=?", (now_ms(), deployment_id)
            )
            store.audit(
                conn,
                row["source"],
                "strategy.stopped",
                "Paper strategy stopped; positions retained",
                {"deployment_id": deployment_id},
            )
        return next(d for d in store.deployments(row["source"]) if d["id"] == deployment_id)

    @app.get("/api/v1/risk")
    def risk(source: Source = "okx"):
        return store.risk(source)

    @app.put("/api/v1/risk")
    def limits(body: RiskInput):
        return desk.update_risk(body.source, body)

    @app.post("/api/v1/risk/kill-switch")
    def kill(body: KillInput):
        return desk.halt(body.source, body.active, body.reason)

    @app.get("/api/v1/audit")
    def audit(source: Source = "okx", limit: int = Query(default=50, ge=1, le=200)):
        return {"items": store.events(source, limit)}

    app.include_router(professional_router(app, access, professional, supervisor, settings))

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise DeskError("not_found", "This API endpoint does not exist.", 404)
            candidate = (frontend / path).resolve()
            if candidate.is_relative_to(frontend.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")

    return app
