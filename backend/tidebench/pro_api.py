"""Authenticated workspace API for versioned research and forward simulation."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field, model_validator

from .engine import ACCOUNTING_CONTEXT
from .platform import COOKIE, PlatformError
from .portfolio_analytics import PriceShock, analyze_portfolio
from .portfolio_registry import PortfolioProjectInput, PortfolioVersionInput
from .portfolio_releases import PortfolioReleaseInput, PortfolioReleasePreviewInput
from .portfolio_research import PortfolioInput
from .research_artifacts import project_result
from .research_governance import HoldoutInput
from .research_protocol import EvaluatePortfolioInput, PortfolioHoldoutInput, SealPortfolioInput
from .schemas import InputModel, KillInput, Money, Source
from .store import encode, now_ms
from .strategy_program import ProStrategyInput
from .strategy_registry import ReleaseInput, ReleasePreviewInput, StrategyProjectInput, StrategyVersionInput

MarketId = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,24}-USDT(?:-SWAP)?$")]
CatalogBar = Literal["1m", "5m", "15m", "1H", "4H", "1Dutc"]
Direction = Literal["long_only", "long_short", "short_only"]
Role = Literal["admin", "trader", "researcher", "viewer", "risk_operator"]
ShockPercent = Annotated[Decimal, Field(gt=-100, le=1000)]
AssetId = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,24}$")]


class ClockInput(InputModel):
    speed: int | None = Field(default=None, ge=0, le=3600)
    step_ms: int = Field(default=0, ge=0, le=86_400_000)
    expected_revision: int = Field(ge=0)


class IncidentAcknowledgement(InputModel):
    reason: str = Field(min_length=12, max_length=2000)


class ShockInput(InputModel):
    name: str = Field(min_length=1, max_length=80)
    parallel_pct: ShockPercent = Decimal(0)
    asset_pct: dict[AssetId, ShockPercent] = Field(default_factory=dict, max_length=500)
    market_pct: dict[MarketId, ShockPercent] = Field(default_factory=dict, max_length=500)


class AnalyticsInput(InputModel):
    source: Source = "okx"
    scenarios: list[ShockInput] = Field(min_length=1, max_length=25)


class LoginInput(InputModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class UserInput(LoginInput):
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(default="", max_length=100)


class CreateUserInput(UserInput):
    role: Role = "viewer"


class UpdateUserInput(InputModel):
    role: Role
    enabled: bool


class PasswordInput(InputModel):
    new_password: str = Field(min_length=12, max_length=128)


class ChangePasswordInput(PasswordInput):
    current_password: str = Field(min_length=1, max_length=128)


class JobInput(InputModel):
    source: Source = "okx"
    inst_id: MarketId
    kind: Literal["trade", "mark", "index", "funding"]
    bar: CatalogBar = "1H"
    start: int = Field(ge=1577836800000)
    end: int = Field(ge=1577836800000)


class ImportInput(JobInput):
    records: list[dict] = Field(min_length=1, max_length=250000)
    provenance: dict


class PackageInput(InputModel):
    source: Source = "okx"
    inst_id: MarketId
    bar: CatalogBar = "1H"
    start: int = Field(ge=1577836800000)
    end: int = Field(ge=1577836800000)
    include_index: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)
    dataset_ids: dict[Literal["trade", "mark", "funding", "index"], str] = Field(
        default_factory=dict, max_length=4
    )


class ResearchInput(InputModel):
    holdout_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    strategy_version_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    dataset_id: str = Field(min_length=1, max_length=64)
    mark_dataset_id: str | None = Field(default=None, max_length=64)
    funding_dataset_id: str | None = Field(default=None, max_length=64)
    package_id: str | None = Field(default=None, min_length=1, max_length=64)
    package_manifest_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    strategy: ProStrategyInput = Field(default_factory=ProStrategyInput)
    direction: Direction = "long_only"
    initial_cash: Money = Decimal("10000")
    leverage: Decimal = Field(default=1, ge=1, le=50)
    fee_bps: Decimal = Field(default=10, ge=0, le=100)
    slippage_bps: Decimal = Field(default=5, ge=0, le=100)
    liquidation_fee_bps: Decimal = Field(default=5, ge=0, le=500)
    start_ts: int | None = None
    end_ts: int | None = None
    mode: Literal["single", "train_test", "walk_forward", "grid", "cost_stress"] = "single"
    options: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def package_identity(self):
        if bool(self.package_id) != bool(self.package_manifest_hash):
            raise ValueError("Package ID and immutable manifest hash must be supplied together.")
        return self


class ProOrderInput(InputModel):
    source: Source
    inst_id: MarketId
    side: Literal["buy", "sell"]
    quantity: Money
    leverage: Decimal = Field(default=1, ge=1, le=50)
    reduce_only: bool = False
    margin_mode: Literal["isolated"] = "isolated"
    order_type: Literal["market", "limit", "stop_market"] = "market"
    limit_price: Money | None = None
    stop_price: Money | None = None

    @model_validator(mode="after")
    def validate_trigger(self):
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("Limit orders require limit_price.")
        if self.order_type == "stop_market" and self.stop_price is None:
            raise ValueError("Stop orders require stop_price.")
        if self.order_type != "limit" and self.limit_price is not None:
            raise ValueError("limit_price applies only to limit orders.")
        if self.order_type != "stop_market" and self.stop_price is not None:
            raise ValueError("stop_price applies only to stop orders.")
        if not self.inst_id.endswith("-SWAP") and self.leverage != 1:
            raise ValueError("Spot orders have leverage one.")
        return self

    def canonical(self):
        body = self.model_dump(exclude_none=True)
        with localcontext(ACCOUNTING_CONTEXT):
            return {
                key: format(value.normalize(), "f") if isinstance(value, Decimal) else value
                for key, value in body.items()
            }


class ProDeployInput(InputModel):
    source: Source
    inst_id: MarketId
    bar: CatalogBar = "1H"
    strategy: ProStrategyInput = Field(default_factory=ProStrategyInput)
    direction: Direction = "long_only"
    leverage: Decimal = Field(default=1, ge=1, le=50)
    allocation: Decimal = Field(default=Decimal(".25"), gt=0, le=1)

    @model_validator(mode="after")
    def spot_policy(self):
        if not self.inst_id.endswith("-SWAP") and (self.direction != "long_only" or self.leverage != 1):
            raise ValueError("Spot strategies are long only with leverage one.")
        return self


class ProRiskInput(InputModel):
    source: Source
    max_order_notional: Money
    max_gross_exposure_pct: Decimal = Field(ge=1, le=1000)
    max_leverage: Decimal = Field(ge=1, le=50)
    max_daily_loss_pct: Decimal = Field(ge=Decimal(".1"), le=50)
    fee_bps: Decimal | None = Field(default=None, ge=0, le=100)
    slippage_bps: Decimal | None = Field(default=None, ge=0, le=100)
    liquidation_fee_bps: Decimal | None = Field(default=None, ge=0, le=500)


class RestoreInput(InputModel):
    backup_id: str = Field(max_length=80)
    confirmation: Literal["RESTORE"]


def professional_router(app, access, runtime, supervisor, settings):
    router = APIRouter(prefix="/api/v1")
    restore_lock = asyncio.Lock()

    def actor(request):
        return request.state.user["username"]

    @router.get("/pro/strategies")
    def strategies():
        return {"items": runtime.registry.projects()}

    @router.post("/pro/strategies", status_code=201)
    def create_strategy(body: StrategyProjectInput, request: Request):
        return runtime.registry.create_project(
            body.name, body.hypothesis, body.definition.record(), actor(request)
        )

    @router.get("/pro/strategies/{identifier}")
    def strategy_project(identifier: str):
        return runtime.registry.project(identifier)

    @router.post("/pro/strategies/{identifier}/versions", status_code=201)
    def create_strategy_version(identifier: str, body: StrategyVersionInput, request: Request):
        return runtime.registry.create_version(
            identifier, body.hypothesis, body.definition.record(), actor(request), body.parent_id
        )

    @router.get("/pro/strategy-versions/{identifier}")
    def strategy_version(identifier: str):
        return runtime.registry.version(identifier)

    @router.post("/pro/execution/releases/preview")
    def preview_release(body: ReleasePreviewInput):
        return runtime.registry.preview_release(runtime, body.run_id, body.selection)

    @router.post("/pro/execution/releases", status_code=201)
    def approve_release(body: ReleaseInput, request: Request):
        return runtime.registry.approve_release(runtime, body.model_dump(), actor(request))

    @router.get("/pro/execution/releases")
    def releases(source: Source = "okx"):
        return {"items": runtime.registry.releases(source)}

    @router.post("/pro/execution/releases/{identifier}/activate")
    def activate_release(identifier: str, request: Request):
        return runtime.registry.activate_release(runtime, identifier, actor(request))

    def session_response(token, user):
        response = JSONResponse(
            {
                "authenticated": True,
                "auth_required": settings.auth_enabled,
                "setup_required": False,
                "user": user,
                "csrf_token": access.csrf(token),
            }
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        return response

    @router.get("/auth/status")
    @router.get("/auth/session")
    def auth_status(request: Request):
        return access.status(request)

    @router.post("/auth/setup", status_code=201)
    async def setup(
        body: UserInput, request: Request, bootstrap: str = Header(default="", alias="X-Bootstrap-Token")
    ):
        local = request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}
        user = await runtime.offload(
            access.setup, body.username, body.password, body.display_name, local=local, bootstrap=bootstrap
        )
        token, user = await runtime.offload(
            access.login,
            user["username"],
            body.password,
            request.client.host if request.client else "unknown",
        )
        return session_response(token, user)

    @router.post("/auth/login")
    async def login(body: LoginInput, request: Request):
        token, user = await runtime.offload(
            access.login, body.username, body.password, request.client.host if request.client else "unknown"
        )
        return session_response(token, user)

    @router.post("/auth/logout")
    def logout(request: Request):
        access.logout(request.state.session_token)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(
            COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
        )
        return response

    @router.get("/auth/users")
    def users():
        return {"items": access.users()}

    @router.post("/auth/users", status_code=201)
    async def create_user(body: CreateUserInput):
        return await runtime.offload(
            access.create_user, body.username, body.password, body.display_name, body.role
        )

    @router.put("/auth/users/{identifier}")
    def update_user(identifier: str, body: UpdateUserInput):
        return access.update_user(identifier, role=body.role, enabled=body.enabled)

    @router.post("/auth/password")
    async def change_password(body: ChangePasswordInput, request: Request):
        return await runtime.offload(
            access.change_password, request.state.user["id"], body.current_password, body.new_password
        )

    @router.post("/auth/users/{identifier}/password")
    async def reset_password(identifier: str, body: PasswordInput, request: Request):
        return await runtime.offload(access.reset_password, identifier, body.new_password, actor(request))

    @router.get("/pro/catalog/instruments")
    async def instruments(source: Source = "okx", inst_type: Literal["SPOT", "SWAP"] = "SPOT"):
        return {"items": encode(await runtime.catalog.refresh_instruments(inst_type, source))}

    @router.get("/pro/catalog/datasets")
    def datasets(source: Source | None = None):
        return {"items": runtime.catalog.list_datasets(source)}

    @router.get("/pro/catalog/packages")
    def packages(source: Source | None = None, limit: int = Query(default=100, ge=1, le=100)):
        return {"items": [package_response(item) for item in runtime.packages.list_packages(source, limit)]}

    def package_response(item):
        item = {key: value for key, value in item.items() if key != "manifest"}
        if item["ready"]:
            inputs = {
                "source": item["source"],
                "start_ts": item["start"],
                "end_ts": item["end"],
                "package_id": item["id"],
                "package_manifest_hash": item["manifest_hash"],
            }
            for component in item["components"]:
                key = "dataset_id" if component["kind"] == "trade" else component["kind"] + "_dataset_id"
                inputs[key] = component["dataset_id"]
            item["research_inputs"] = inputs
        return item

    @router.post("/pro/catalog/packages", status_code=202)
    def create_package(body: PackageInput):
        result = runtime.packages.create_package(**body.model_dump())
        runtime.wake.set()
        return package_response(result)

    @router.get("/pro/catalog/packages/{identifier}")
    def package(identifier: str):
        return package_response(runtime.packages.get_package(identifier, include_manifest=False))

    @router.get("/pro/catalog/packages/{identifier}/manifest")
    def package_manifest(identifier: str):
        return JSONResponse(
            encode(runtime.packages.get_manifest(identifier)),
            headers={"Content-Disposition": f'attachment; filename="tidebench-package-{identifier}.json"'},
        )

    @router.post("/pro/catalog/packages/{identifier}/cancel")
    def cancel_package(identifier: str):
        return package_response(runtime.packages.cancel_package(identifier))

    @router.post("/pro/catalog/packages/{identifier}/retry", status_code=202)
    def retry_package(identifier: str):
        result = runtime.packages.retry_package(identifier)
        runtime.wake.set()
        return package_response(result)

    @router.get("/pro/catalog/datasets/{identifier}")
    def dataset(identifier: str):
        return runtime.catalog.get_dataset(identifier)

    @router.get("/pro/catalog/datasets/{identifier}/verify")
    def verify_dataset(identifier: str):
        return {"verified": runtime.catalog.verify_dataset(identifier)}

    @router.get("/pro/catalog/jobs")
    def jobs(source: Source | None = None):
        return {"items": runtime.catalog.list_jobs(source)}

    @router.post("/pro/catalog/jobs", status_code=202)
    def create_job(body: JobInput):
        result = runtime.catalog.create_job(**body.model_dump())
        runtime.wake.set()
        return result

    @router.post("/pro/catalog/jobs/{identifier}/cancel")
    def cancel_job(identifier: str):
        return runtime.catalog.cancel_job(identifier)

    @router.post("/pro/catalog/jobs/{identifier}/retry", status_code=202)
    def retry_job(identifier: str):
        result = runtime.catalog.retry_job(identifier)
        runtime.wake.set()
        return result

    @router.post("/pro/catalog/import", status_code=201)
    async def import_dataset(body: ImportInput):
        return await runtime.catalog.import_dataset(**body.model_dump())

    @router.get("/pro/market")
    async def market(source: Source = "okx", inst_id: MarketId = "BTC-USDT"):
        snapshot = await runtime.catalog.get_market_snapshot(inst_id, source)
        runtime.snapshots[(source, inst_id)] = snapshot
        return encode(snapshot)

    @router.get("/pro/research/runs")
    def runs(
        source: Source | None = None,
        limit: int = Query(default=100, ge=1, le=100),
        before: str | None = Query(default=None, max_length=64),
    ):
        items = runtime.runs(source, limit=limit, before=before)
        return {
            "items": items,
            "next_cursor": f"{items[-1]['created_at']}:{items[-1]['id']}" if len(items) == limit else None,
        }

    @router.post("/pro/research/runs", status_code=202)
    def create_run(body: ResearchInput):
        return runtime.create_run(encode(body.model_dump()))

    @router.get("/pro/research/runs/{identifier}")
    def run(identifier: str, variant: str | None = Query(default=None, max_length=100)):
        result = runtime.run(identifier)
        if variant is not None:
            result["result"] = project_result(result["result"], variant)
        return result

    @router.post("/pro/research/runs/{identifier}/replay", status_code=202)
    def replay(identifier: str):
        return runtime.replay(identifier)

    @router.get("/pro/research/runs/{identifier}/export")
    def export(identifier: str):
        result = runtime.run(identifier, include_snapshot=True)
        if result["status"] != "completed":
            raise PlatformError("run_not_complete", "Export requires a completed run.", 409)
        # The engine's input_snapshot contains full captured prices, marks,
        # settled rates and rules, including every experiment's assumptions.
        return JSONResponse(
            encode(result),
            headers={"Content-Disposition": f'attachment; filename="tidebench-research-{result["id"]}.json"'},
        )

    @router.get("/pro/research/compare")
    def compare(ids: str = Query(max_length=600)):
        identifiers = list(dict.fromkeys(ids.split(",")))
        if not 2 <= len(identifiers) <= 8:
            raise PlatformError("comparison_size", "Compare two to eight completed runs.", 422)
        items = [runtime.run(identifier) for identifier in identifiers]
        if any(item["status"] != "completed" for item in items):
            raise PlatformError("run_not_complete", "All compared runs must be completed.", 409)
        fields = (
            "dataset_id",
            "mark_dataset_id",
            "funding_dataset_id",
            "start_ts",
            "end_ts",
            "initial_cash",
            "direction",
            "leverage",
            "fee_bps",
            "slippage_bps",
            "liquidation_fee_bps",
            "mode",
            "options",
        )
        differences = [
            key
            for key in fields
            if len({json.dumps(item["config"].get(key), sort_keys=True) for item in items}) > 1
        ]
        for label, field in (
            ("implementation", "research_implementation"),
            ("model_version", "model_version"),
            ("maintenance_tiers", "maintenance_tiers_hash"),
            ("funding_observations", "funding_observations_hash"),
        ):
            values = [(item.get("manifest") or {}).get(field) for item in items]
            if label == "implementation":
                values = [value.get("code_fingerprint") if value else None for value in values]
            if len({json.dumps(value, sort_keys=True) for value in values}) > 1:
                differences.append(label)
            elif (
                label in {"maintenance_tiers", "funding_observations"}
                and any(value is None for value in values)
                and any(item["config"].get("mark_dataset_id") for item in items)
            ):
                differences.append(f"{label}_unavailable")
        return {
            "items": items,
            "comparable_inputs": not differences,
            "different_assumptions": differences,
            "warning": "Different or unverified data, windows, costs, maintenance, funding or implementation assumptions require separate interpretation."
            if differences
            else None,
        }

    @router.get("/pro/execution/account")
    async def account(source: Source = "okx"):
        snapshots = await runtime.snapshots_for(source)
        return await runtime.offload(runtime.book.account, source, snapshots)

    async def portfolio_analysis(source, scenarios=None):
        snapshots = await runtime.snapshots_for(source)
        account = await runtime.offload(runtime.book.account, source, snapshots)
        risk = runtime.book.risk(source)
        return await runtime.offload(
            analyze_portfolio,
            account,
            snapshots,
            scenarios=scenarios,
            fee_bps=Decimal(risk["fee_bps"]),
            liquidation_fee_bps=Decimal(risk["liquidation_fee_bps"]),
            slippage_bps=Decimal(risk["slippage_bps"]),
            as_of_ms=now_ms(),
        )

    @router.get("/pro/execution/analytics")
    async def analytics(source: Source = "okx"):
        return await portfolio_analysis(source)

    @router.post("/pro/execution/analytics")
    async def custom_analytics(body: AnalyticsInput):
        return await portfolio_analysis(
            body.source, [PriceShock(**scenario.model_dump()) for scenario in body.scenarios]
        )

    @router.get("/pro/research/holdouts")
    def holdouts():
        return {"items": runtime.governance.list()}

    @router.post("/pro/research/holdouts", status_code=201)
    def create_holdout(body: HoldoutInput, request: Request):
        return runtime.governance.create(encode(body.model_dump()), actor(request))

    @router.get("/pro/research/governance/{project_id}")
    def research_governance(project_id: str):
        runtime.registry.project(project_id)
        return runtime.governance.trials(project_id)

    @router.get("/pro/portfolio-strategies")
    def portfolio_projects():
        return {"items": runtime.portfolio_registry.projects()}

    @router.post("/pro/portfolio-strategies", status_code=201)
    def create_portfolio_project(body: PortfolioProjectInput, request: Request):
        return runtime.portfolio_registry.create_project(
            body.name, body.hypothesis, body.definition.model_dump(), actor(request)
        )

    @router.get("/pro/portfolio-strategies/{identifier}")
    def portfolio_project(identifier: str):
        return runtime.portfolio_registry.project(identifier)

    @router.post("/pro/portfolio-strategies/{identifier}/versions", status_code=201)
    def create_portfolio_version(identifier: str, body: PortfolioVersionInput, request: Request):
        return runtime.portfolio_registry.create_version(
            identifier, body.hypothesis, body.definition.model_dump(), actor(request), body.parent_id
        )

    @router.get("/pro/portfolio-versions/{identifier}")
    def portfolio_version(identifier: str):
        return runtime.portfolio_registry.version(identifier)

    @router.post("/pro/execution/portfolio-releases/preview")
    def portfolio_release_preview(body: PortfolioReleasePreviewInput):
        return runtime.portfolio_releases.preview(body.run_id)

    @router.post("/pro/execution/portfolio-releases", status_code=201)
    def approve_portfolio_release(body: PortfolioReleaseInput, request: Request):
        return runtime.portfolio_releases.approve(body.model_dump(), actor(request))

    @router.get("/pro/execution/portfolio-releases")
    def portfolio_releases(source: Source = "okx"):
        return {"items": runtime.portfolio_releases.list(source)}

    @router.post("/pro/execution/portfolio-releases/{identifier}/activate")
    def activate_portfolio_release(identifier: str, request: Request):
        return runtime.portfolio_releases.activate(identifier, actor(request))

    @router.get("/pro/execution/portfolios")
    def managed_portfolios(source: Source = "okx"):
        return {"items": runtime.managed_portfolios.list(source)}

    @router.get("/pro/execution/portfolios/{identifier}")
    def managed_portfolio(identifier: str):
        return runtime.managed_portfolios.get(identifier)

    @router.get("/pro/execution/portfolios/{identifier}/batches")
    def portfolio_batches(
        identifier: str,
        limit: int = Query(default=30, ge=1, le=100),
        before: int = Query(default=2**63 - 1, ge=0, le=2**63 - 1),
    ):
        return {"items": runtime.managed_portfolios.history(identifier, limit, before)}

    @router.post("/pro/execution/portfolios/{identifier}/stop")
    def stop_managed_portfolio(identifier: str, request: Request):
        return runtime.managed_portfolios.stop(identifier, actor(request))

    @router.post("/pro/research/portfolio-holdouts/preview")
    async def preview_portfolio_holdout(body: PortfolioHoldoutInput, request: Request):
        tiers = {}
        for identifier in body.package_ids:
            package = runtime.packages.get_package(identifier)
            if package["inst_id"].endswith("-SWAP"):
                tiers[package["inst_id"]] = await runtime.catalog.get_margin_tiers(
                    package["inst_id"], package["source"]
                )
        return await runtime.offload(
            runtime.protocol.preview, encode(body.model_dump()), actor(request), tiers
        )

    @router.post("/pro/research/portfolio-holdouts", status_code=201)
    def seal_portfolio_holdout(body: SealPortfolioInput, request: Request):
        return runtime.protocol.seal(body.model_dump(), actor(request))

    @router.get("/pro/research/portfolio-holdouts")
    def portfolio_holdouts(source: Source | None = None, project_id: str | None = None):
        return {"items": runtime.protocol.list(source, project_id)}

    @router.get("/pro/research/portfolio-holdouts/{identifier}")
    def portfolio_holdout(identifier: str):
        return runtime.protocol.get(identifier)

    @router.post("/pro/research/portfolio-holdouts/{identifier}/evaluate", status_code=202)
    def evaluate_portfolio_holdout(identifier: str, body: EvaluatePortfolioInput, request: Request):
        return runtime.protocol.evaluate(identifier, body.plan_hash, actor(request))

    @router.get("/pro/research/portfolio-governance/{identifier}")
    def portfolio_governance(identifier: str):
        return runtime.protocol.trials(identifier)

    @router.post("/pro/research/portfolios/{identifier}/replay", status_code=202)
    def replay_portfolio(identifier: str, request: Request):
        return runtime.portfolios.replay(identifier, actor(request))

    @router.get("/pro/research/portfolios")
    def portfolios(source: Source = "okx"):
        return {"items": runtime.portfolios.list(source)}

    @router.get("/pro/research/portfolios/{identifier}")
    def portfolio_run(identifier: str):
        return runtime.portfolios.get(identifier)

    @router.post("/pro/research/portfolios", status_code=202)
    async def create_portfolio(body: PortfolioInput, request: Request):
        tiers = {}
        for leg in body.legs:
            package = runtime.packages.get_package(leg.package_id)
            if package["inst_id"].endswith("-SWAP"):
                tiers[package["inst_id"]] = await runtime.catalog.get_margin_tiers(
                    package["inst_id"], package["source"]
                )
        run = await runtime.offload(
            runtime.portfolios.create, encode(body.model_dump()), actor(request), tiers
        )
        runtime.wake.set()
        return run

    @router.get("/pro/execution/clock")
    def clock():
        return runtime.clock.status()

    @router.post("/pro/execution/clock")
    def clock_command(body: ClockInput, request: Request):
        return runtime.clock.change(**body.model_dump(), actor=actor(request))

    @router.get("/pro/execution/contributions")
    async def contributions(source: Source = "okx"):
        snapshots = await runtime.snapshots_for(source)
        return await runtime.offload(runtime.book.contribution_report, source, snapshots)

    @router.get("/pro/execution/contributions/events")
    def contribution_events(
        source: Source = "okx",
        owner: str | None = Query(default=None, max_length=128),
        before: int = Query(default=2**63 - 1, ge=0, le=2**63 - 1),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return {"items": runtime.book.contributions.events(source, owner=owner, before=before, limit=limit)}

    @router.get("/pro/execution/performance")
    def performance(
        source: Source = "okx",
        limit: int = Query(default=500, ge=1, le=5000),
        before: int = Query(default=2**63 - 1, ge=1),
    ):
        return runtime.book.performance.report(source, limit=limit, before=before)

    @router.get("/pro/execution/deployments/{identifier}/decisions")
    def decisions(
        identifier: str,
        limit: int = Query(default=100, ge=1, le=500),
        before: int = Query(default=2**63 - 1, ge=1),
    ):
        return {"items": runtime.history.decisions(identifier, limit, before)}

    @router.get("/pro/execution/orders")
    def orders(source: Source = "okx"):
        return {"items": runtime.book.orders(source)}

    @router.get("/pro/execution/ledger")
    def ledger(source: Source = "okx"):
        return {"items": runtime.book.ledger(source)}

    @router.post("/pro/execution/orders/preview")
    async def preview(body: ProOrderInput):
        snapshots = await runtime.snapshots_for(body.source, [body.inst_id])
        return encode(await runtime.offload(runtime.book.preview, body.canonical(), snapshots))

    @router.post("/pro/execution/orders", status_code=201)
    async def submit(
        body: ProOrderInput, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
            raise PlatformError(
                "idempotency_key", "Use 8–128 letters, digits, dashes, dots, colons or underscores.", 422
            )
        return await runtime.submit(body.canonical(), idempotency_key, actor(request))

    @router.post("/pro/execution/orders/{identifier}/cancel")
    def cancel(identifier: str, request: Request):
        return runtime.book.cancel(identifier, actor(request))

    @router.get("/pro/execution/deployments")
    def deployments(source: Source = "okx"):
        return {"items": [{**row, **row["config"]} for row in runtime.deployments(source)]}

    @router.post("/pro/execution/deployments", status_code=201)
    async def deploy(body: ProDeployInput, request: Request):
        await runtime.catalog.get_instrument(body.inst_id, body.source)
        return runtime.deploy(encode(body.model_dump()), actor(request))

    @router.post("/pro/execution/deployments/{identifier}/stop")
    def stop(identifier: str, request: Request):
        return runtime.stop_deployment(identifier, actor(request))

    @router.get("/pro/execution/risk")
    def risk(source: Source = "okx"):
        return runtime.book.risk(source)

    @router.put("/pro/execution/risk")
    def update_risk(body: ProRiskInput, request: Request, source: Source = "okx"):
        if source != body.source:
            raise PlatformError("source_mismatch", "Query and command sources must match.", 422)
        return runtime.book.set_risk(
            source, encode(body.model_dump(exclude_none=True, exclude={"source"})), actor(request)
        )

    @router.post("/pro/execution/halt")
    def halt(body: KillInput, request: Request):
        return runtime.book.halt(body.source, body.active, body.reason, actor(request))

    @router.get("/pro/ops")
    def operations():
        return runtime.operations()

    @router.post("/pro/ops/incidents/{identifier}/acknowledge")
    def acknowledge_incident(identifier: str, body: IncidentAcknowledgement, request: Request):
        return runtime.incidents.acknowledge(identifier, actor(request), body.reason)

    @router.get("/pro/ops/audit")
    def audit(limit: int = Query(default=100, ge=1, le=500)):
        with runtime.store.read() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"items": [{**dict(row), "details": json.loads(row["details"])} for row in rows]}

    @router.get("/pro/ops/metrics", response_class=PlainTextResponse)
    def metrics():
        return runtime.metrics.prometheus()

    @router.post("/pro/ops/backups/create", status_code=201)
    async def backup():
        return await runtime.offload(runtime.backups.create)

    @router.get("/pro/ops/backups/{identifier}/verify")
    async def verify_backup(identifier: str):
        return await runtime.offload(runtime.backups.verify, identifier)

    @router.post("/pro/ops/restore")
    async def restore(body: RestoreInput):
        if restore_lock.locked():
            raise PlatformError(
                "recovery_in_progress", "Another recovery request is already in progress.", 409
            )
        async with restore_lock:
            # Validate before entering maintenance; keep all public operations
            # quiesced while both schedulers and their worker threads drain.
            verified = await runtime.offload(runtime.backups.verify, body.backup_id)
            with runtime.store.read() as conn:
                current_schema = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            if verified["database_schema"] != current_schema:
                raise PlatformError(
                    "restore_schema_mismatch",
                    "Use a matching application version and pre-upgrade backup for cross-version rollback.",
                    409,
                )
            app.state.maintenance = True
            critical_started, critical_completed = False, False
            try:
                try:
                    async with asyncio.timeout(30):
                        while app.state.active_requests > 1:
                            await asyncio.sleep(0.02)
                except TimeoutError:
                    raise PlatformError(
                        "recovery_busy",
                        "Active requests did not drain. Retry after current work completes.",
                        409,
                    ) from None

                async def replace_workspace():
                    nonlocal critical_completed
                    legacy_stopped, professional_stopped = False, False
                    try:
                        with runtime.store.write() as conn:
                            conn.execute("UPDATE risk SET kill_switch=1")
                            conn.execute("UPDATE pro_risk SET halted=1")
                            conn.execute("UPDATE deployments SET status='stopped'")
                            conn.execute("UPDATE pro_deployments SET status='stopped'")
                            conn.execute(
                                "UPDATE managed_portfolios SET status='stopped',updated_at=?", (now_ms(),)
                            )
                            conn.execute(
                                "UPDATE portfolio_batches SET status='canceled',error='Workspace maintenance; portfolio stopped',updated_at=? WHERE status IN ('reducing','adding','compensating')",
                                (now_ms(),),
                            )
                            conn.execute(
                                "UPDATE portfolio_commands SET status='canceled',updated_at=? WHERE status='pending'",
                                (now_ms(),),
                            )
                            runtime.store.audit(
                                conn,
                                "system",
                                "backup.recovery_started",
                                "Recovery latched execution halt before stopping workers",
                                {"backup_id": body.backup_id},
                            )
                        await supervisor.stop()
                        legacy_stopped = True
                        await runtime.stop()
                        professional_stopped = True
                        result = await runtime.offload(runtime.backups.restore, body.backup_id)
                    finally:
                        if settings.worker_enabled and not app.state.stopping:
                            if legacy_stopped:
                                await supervisor.start()
                            if professional_stopped:
                                await runtime.start()
                    critical_completed = True
                    return result

                critical_started = True
                operation = asyncio.create_task(replace_workspace(), name="verified-workspace-recovery")
                try:
                    return await asyncio.shield(operation)
                except asyncio.CancelledError:
                    # HTTP disconnect/cancellation cannot release maintenance
                    # while an actual SQLite replacement thread is still active.
                    while not operation.done():
                        try:
                            await asyncio.shield(operation)
                        except asyncio.CancelledError:
                            continue
                    if not operation.cancelled():
                        operation.exception()
                    raise
            finally:
                app.state.maintenance = app.state.stopping or (critical_started and not critical_completed)

    return router
