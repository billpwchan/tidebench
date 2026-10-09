"""One shared-capital historical book, not a sum of independent backtests.

Prices are bar-level execution scenarios. Quotes have zero modeled spread;
explicit slippage and fees apply. Instrument histories must be supplied to claim
point-in-time rules. Multi-leg fills are sequential and their residuals visible.
"""

import json
import logging
from decimal import Decimal, localcontext
from itertools import count as sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, model_validator

from .catalog import CATALOG_BARS
from .engine import ACCOUNTING_CONTEXT, EngineError, StrategyConfig
from .historical_lifecycle import (
    PARSER_VERSION,
    LifecycleEvent,
    apply_inventory_event,
    capture_events,
    compatible_units,
    eligibility,
)
from .historical_lifecycle import (
    initialize as initialize_lifecycle,
)
from .platform import PlatformError
from .portfolio_construction import apply_weight_caps, construction_weights, funding_carry_evidence
from .portfolio_execution import (
    SUPPORTED_EXECUTION_CONTRACTS,
    execute_batch,
    resume_compensation,
)
from .portfolio_risk import constrain_risk_weights, realized_portfolio_metrics, risk_momentum_weights
from .portfolio_targets import addition_plan, reduction_plan, target_quantities
from .pro_execution import SimulationBook, base_size, number, tier_for
from .pro_research import ResearchConfig, _DecisionState, json_safe
from .schemas import InputModel, Money
from .store import Store, dumps, encode, new_id, now_ms
from .strategy_program import ProStrategyInput
from .strategy_registry import digest
from .strategy_risk import exit_on_close, risk_notional

D = Decimal


class RuleEvent(InputModel):
    effective_ts: int = Field(ge=1577836800000)
    known_at: int = Field(ge=1577836800000)
    instrument: dict
    margin_tiers: list[dict] = Field(default_factory=list, max_length=100)
    provenance: str = Field(min_length=12, max_length=2000)

    @model_validator(mode="after")
    def causal(self):
        if self.known_at > self.effective_ts:
            raise ValueError("A point-in-time rule must be known by its effective time.")
        return self


class PortfolioLeg(InputModel):
    package_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    weight: Decimal = Field(ge=-5, le=5)
    leverage: Decimal = Field(default=1, ge=1, le=50)
    strategy: ProStrategyInput = Field(default_factory=ProStrategyInput)
    direction: Literal["long_only", "short_only", "long_short"] = "long_only"
    rule_events: list[RuleEvent] = Field(default_factory=list, max_length=200)
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list, max_length=200)


class PortfolioInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    hypothesis: str = Field(min_length=12, max_length=4000)
    legs: list[PortfolioLeg] = Field(min_length=2, max_length=10)
    mode: Literal["fixed_weights", "independent_signals", "momentum", "risk_momentum", "funding_carry"] = (
        "fixed_weights"
    )
    portfolio_version_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    capital_pct: Decimal = Field(default=100, gt=0, le=100)
    failure_policy: Literal["reduce_group"] = "reduce_group"
    max_residual_pct: Decimal = Field(default=2, ge=".01", le=100)
    execution_contract: Literal["reduce_group_v1", "reduce_group_v2_allowance"] = "reduce_group_v2_allowance"
    initial_cash: Money = D(10000)
    fee_bps: Decimal = Field(default=10, ge=0, le=100)
    slippage_bps: Decimal = Field(default=5, ge=0, le=100)
    liquidation_fee_bps: Decimal = Field(default=50, ge=0, le=500)
    max_gross_pct: Decimal = Field(default=200, ge=1, le=1000)
    max_order_notional: Decimal = Field(default=2500, gt=0, le=1000000000)
    max_base_asset_gross_pct: Decimal = Field(default=100, ge=1, le=1000)
    max_daily_loss_pct: Decimal = Field(default=5, ge=".1", le=50)
    rebalance_bars: int = Field(default=24, ge=1, le=1000)
    lookback: int = Field(default=20, ge=2, le=400)
    top_k: int = Field(default=1, ge=1, le=10)
    risk_window: int = Field(default=84, ge=10, le=400)
    vol_target_pct: Decimal = Field(default=20, gt=0, le=100)
    vol_floor_pct: Decimal = Field(default=20, gt=0, le=200)
    covariance_shrinkage: Decimal = Field(default=".25", ge=0, le=1)
    correlation_stress: Decimal = Field(default=".75", ge=0, le=1)
    carry_threshold: Decimal = Field(default=0, ge=-0.01, le=0.01)
    carry_window: int = Field(default=1, ge=1, le=30)
    carry_cost_settlements: int = Field(default=0, ge=0, le=300)
    carry_buffer_bps: Decimal = Field(default=0, ge=0, le=1000)
    carry_max_age_hours: int = Field(default=0, ge=0, le=168)
    rules_mode: Literal["captured_current", "point_in_time"] = "captured_current"
    universe_mode: Literal["static", "historical_lifecycle"] = "static"
    lifecycle_warmup_bars: int = Field(default=2, ge=1, le=400)
    evaluation: Literal["full", "train_test"] = "full"
    train_pct: int = Field(default=70, ge=50, le=85)
    embargo_bars: int = Field(default=1, ge=0, le=400)


class PortfolioResearch:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        initialize_lifecycle(self.store)
        with self.store.write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS portfolio_runs(id TEXT PRIMARY KEY,source TEXT NOT NULL,status TEXT NOT NULL,config TEXT NOT NULL,manifest TEXT NOT NULL,result TEXT,error TEXT,progress REAL NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
            )

    def get(self, identifier, *, result=True):
        with self.store.read() as conn:
            columns = (
                "*" if result else "id,source,status,config,manifest,error,progress,created_at,updated_at"
            )
            row = conn.execute(f"SELECT {columns} FROM portfolio_runs WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise PlatformError("not_found", "Portfolio research not found.", 404)
        output = dict(row)
        for key in ("config", "manifest", "result"):
            if key in output:
                output[key] = json.loads(output[key]) if output[key] else None
        if "result" in output:
            output["result"] = self.runtime.artifacts.resolve(output["result"])
        if output.get("result") is not None and digest(output["result"]) != output["manifest"].get(
            "result_hash"
        ):
            raise PlatformError("portfolio_integrity", "Portfolio result integrity check failed.", 409)
        return output

    def list(self, source):
        with self.store.read() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM portfolio_runs WHERE source=? ORDER BY created_at DESC LIMIT 100",
                    (source,),
                )
            ]
        return [self.get(identifier, result=False) for identifier in ids]

    def create(self, config, actor, margin_snapshots=None):
        config, manifest, bundle = self.prepare(config, actor, margin_snapshots)
        return self.enqueue(config, manifest, bundle, actor)

    def prepare(self, config, actor, margin_snapshots=None, *, capture_window=None):
        config = encode(PortfolioInput.model_validate(config).model_dump())
        packages = [self.runtime.packages.get_package(leg["package_id"]) for leg in config["legs"]]
        if any(not p["ready"] for p in packages):
            raise PlatformError(
                "portfolio_inputs", "Every portfolio leg needs a ready, verified data package.", 409
            )
        lifecycle = config["universe_mode"] == "historical_lifecycle"
        alignment = {
            (p["source"], p["bar"]) if lifecycle else (p["source"], p["bar"], p["start"], p["end"])
            for p in packages
        }
        if len(alignment) != 1 or len({p["inst_id"] for p in packages}) != len(packages):
            raise PlatformError(
                "portfolio_alignment",
                "Use distinct markets with the same source, bar and exact UTC window.",
                422,
            )
        version = self.runtime.portfolio_registry.validate_binding(config, packages)
        bounds = (
            (min(p["start"] for p in packages), max(p["end"] for p in packages))
            if lifecycle
            else (packages[0]["start"], packages[0]["end"])
        )
        start, end = capture_window or bounds
        interval = CATALOG_BARS[packages[0]["bar"]]
        if not bounds[0] <= start < end <= bounds[1] or start % interval or end % interval:
            raise PlatformError(
                "portfolio_window", "The complete warmup and test window must fit every aligned package.", 422
            )
        bars = (end - start) // interval
        if config["mode"] == "risk_momentum":
            has_exits = any(
                any(
                    D(str(leg["strategy"].get(k) or 0)) != 0
                    for k in ("stop_loss_pct", "trailing_stop_pct", "take_profit_pct", "max_holding_bars")
                )
                for leg in config["legs"]
            )
            decisions = (
                bars if has_exits else (bars + config["rebalance_bars"] - 1) // config["rebalance_bars"]
            )
            if decisions * len(packages) ** 2 * config["risk_window"] > 5000000:
                raise PlatformError(
                    "portfolio_risk_budget",
                    "Risk covariance estimation exceeds five million products; shorten the window, universe or rebalance frequency.",
                    422,
                )
        if bars * len(packages) > 20000:
            raise PlatformError(
                "portfolio_budget", "Portfolio research is limited to 20000 bar-leg observations.", 422
            )
        if config["evaluation"] == "train_test":
            split = bars * config["train_pct"] // 100
            if min(split, bars - split - config["embargo_bars"]) < 20:
                raise PlatformError(
                    "portfolio_split", "Both independent evaluation windows need at least 20 bars.", 422
                )
        if sum((abs(D(leg["weight"])) for leg in config["legs"]), D(0)) * D(config["capital_pct"]) > D(
            config["max_gross_pct"]
        ):
            raise PlatformError(
                "portfolio_exposure", "Declared weights exceed the shared gross-exposure limit.", 422
            )
        for leg, package in zip(config["legs"], packages, strict=True):
            if not package["inst_id"].endswith("-SWAP") and (
                D(leg["weight"]) < 0 or D(leg["leverage"]) != 1 or leg["direction"] != "long_only"
            ):
                raise PlatformError(
                    "portfolio_spot",
                    "Spot legs require nonnegative weights, long-only signals and leverage one.",
                    422,
                )
            if lifecycle:
                if leg["rule_events"] or config["rules_mode"] != "captured_current":
                    raise PlatformError(
                        "lifecycle_rules",
                        "Historical lifecycle mode uses its own attributed events, not static rule histories.",
                        422,
                    )
                leg["lifecycle_events"] = capture_events(
                    self.store, package["source"], package["inst_id"], leg["lifecycle_events"], interval, end
                )
            elif leg["lifecycle_events"]:
                raise PlatformError(
                    "lifecycle_mode", "Lifecycle events require explicit historical_lifecycle mode.", 422
                )
            events = leg["rule_events"]
            baseline = package["manifest"]["instrument"]
            interval = CATALOG_BARS[package["bar"]]
            for event in events:
                meta = event["instrument"]
                if event["effective_ts"] % interval or event["effective_ts"] >= package["end"]:
                    raise PlatformError(
                        "portfolio_rules",
                        "Rule changes must align to a bar boundary before the study end.",
                        422,
                    )
                if any(
                    meta.get(key) != baseline.get(key)
                    for key in ("inst_type", "base", "quote", "ct_val", "ct_mult", "ct_val_ccy", "settle_ccy")
                ):
                    raise PlatformError(
                        "portfolio_rules",
                        "Contract-unit or product changes need an explicit inventory conversion model and are not supported.",
                        422,
                    )
                try:
                    if meta.get("state") != "live" or any(
                        number(meta.get(key)) <= 0 for key in ("tick_size", "lot_size", "min_size")
                    ):
                        raise ValueError()
                    base_size(meta)
                    if meta["inst_type"] == "SWAP":
                        tier_for(meta, D(1), event["margin_tiers"])
                    elif event["margin_tiers"]:
                        raise ValueError()
                except (PlatformError, EngineError, ValueError, KeyError, TypeError):
                    raise PlatformError(
                        "portfolio_rules",
                        "Rule events require supported live instrument increments and valid perpetual maintenance tiers.",
                        422,
                    ) from None
            if events and (
                [e["effective_ts"] for e in events] != sorted({e["effective_ts"] for e in events})
                or any(e["instrument"].get("inst_id") != package["inst_id"] for e in events)
            ):
                raise PlatformError(
                    "portfolio_rules", "Rule events must be ordered, unique and market-matched.", 422
                )
            if config["rules_mode"] == "point_in_time" and (not events or events[0]["effective_ts"] > start):
                raise PlatformError(
                    "portfolio_rules",
                    "Point-in-time mode requires attributed instrument rules covering the initial boundary of every leg.",
                    422,
                )
        if config["mode"] == "funding_carry":
            symbols = [p["inst_id"] for p in packages]
            if (
                len(symbols) != 2
                or symbols[0].endswith("-SWAP")
                or symbols[1] != symbols[0] + "-SWAP"
                or D(config["legs"][0]["weight"]) <= 0
                or D(config["legs"][1]["weight"]) != D(config["legs"][0]["weight"]).copy_negate()
            ):
                raise PlatformError(
                    "carry_legs",
                    "Carry requires ordered spot-long / matching swap-short legs with equal notional weights.",
                    422,
                )
        if config["mode"] == "momentum" and (
            config["top_k"] > len(packages) or any(D(leg["weight"]) < 0 for leg in config["legs"])
        ):
            raise PlatformError(
                "momentum_legs",
                "Momentum rotation uses nonnegative weights and top_k no greater than the universe.",
                422,
            )
        if config["mode"] == "risk_momentum" and (
            config["top_k"] > len(packages)
            or any(
                not 0 <= D(leg["weight"]) <= 1 or D(leg["leverage"]) != 1 or leg["direction"] != "long_only"
                for leg in config["legs"]
            )
        ):
            raise PlatformError(
                "risk_momentum_legs",
                "Risk momentum requires long-only unlevered legs and weight ceilings in [0,1].",
                422,
            )
        inputs = [self.runtime.packages.research_inputs(p["id"]) for p in packages]
        manifest = {
            "schema_version": 1,
            "portfolio_version_id": version["id"] if version else None,
            "portfolio_content_hash": version["content_hash"] if version else None,
            "implementation": self.runtime.engine_identity,
            "resource_policy": {
                "process_isolation": self.runtime.settings.research_process_isolation,
                "compute_deadline_seconds": self.runtime.settings.research_timeout_seconds,
                "additional_virtual_memory_mb": self.runtime.settings.research_memory_budget_mb,
                "memory_enforcement": "Linux RLIMIT_AS above initialized input baseline; other systems require OS deployment limits",
                "artifact_bytes": 128 * 1024 * 1024,
            },
            "packages": [
                {"id": p["id"], "manifest_hash": p["manifest_hash"], "inst_id": p["inst_id"]}
                for p in packages
            ],
            "inputs": inputs,
            "source": packages[0]["source"],
            "bar": packages[0]["bar"],
            "start": start,
            "end": end,
            "margin_tiers": margin_snapshots or {},
            "config_hash": digest(config),
            "created_by": actor,
            "rules_mode": config["rules_mode"],
            "execution_contract": config["execution_contract"],
            "execution_policy": {
                key: config[key]
                for key in (
                    "failure_policy",
                    "max_residual_pct",
                    "max_order_notional",
                    "max_base_asset_gross_pct",
                    "max_gross_pct",
                    "max_daily_loss_pct",
                )
            },
            "universe_scope": "explicit_research_universe; no survivorship-bias-free listing history is inferred",
            **(
                {
                    "lifecycle": {
                        "parser_version": PARSER_VERSION,
                        "source_attribution": "captured bytes and submitter-attributed dates; not independently verified venue history",
                        "event_hashes": [
                            digest(e) for leg in config["legs"] for e in leg["lifecycle_events"]
                        ],
                        "warmup_bars": config["lifecycle_warmup_bars"],
                        "coverage": "bounded supplied events for selected markets only; unknown is not tradable",
                    }
                }
                if lifecycle
                else {}
            ),
        }
        bundle = {"config": config, "manifest": manifest, "legs": self.capture(config, manifest)}
        return config, manifest, bundle

    def capture(self, config, manifest):
        legs = []
        for leg, captured in zip(config["legs"], manifest["inputs"], strict=True):
            current = self.runtime.packages.research_inputs(leg["package_id"])
            if current != captured:
                raise PlatformError("portfolio_inputs", "An input package identity changed.", 409)
            trade = self.runtime.catalog.get_dataset(captured["dataset_id"])
            for key in ("dataset_id", "mark_dataset_id", "funding_dataset_id"):
                if captured.get(key):
                    self.runtime.catalog.verify_dataset(captured[key])
            candles = self.runtime.catalog.load_candles(
                captured["dataset_id"], start=manifest["start"], end=manifest["end"]
            )
            marks = (
                self.runtime.catalog.load_candles(
                    captured["mark_dataset_id"], start=manifest["start"], end=manifest["end"]
                )
                if captured.get("mark_dataset_id")
                else candles
            )
            package = self.runtime.packages.get_package(leg["package_id"])
            funding = [
                event
                for event in package["manifest"]["funding_events"]
                if manifest["start"] <= int(event["ts"]) < manifest["end"]
            ]
            tiers = (manifest["margin_tiers"].get(package["inst_id"]) or {}).get("tiers", [])
            legs.append(
                dict(
                    config=leg,
                    instrument=trade["metadata"],
                    candles=candles,
                    marks=marks,
                    funding=funding,
                    tiers=tiers,
                )
            )
        return json_safe(legs)

    def enqueue(self, config, manifest, bundle, actor, *, holdout=None, replay_of=None):
        r = self.runtime
        identifier, timestamp = new_id(), now_ms()
        manifest = dict(manifest)
        with self.store.write() as conn:
            if holdout:
                # Idempotent admission precedes queue capacity: retrieving an existing
                # committed primary run is always possible even when the queue is full.
                current = r.protocol.get(holdout["id"], conn, private=True)
                if current["status"] == "consumed_unavailable":
                    raise PlatformError(
                        "holdout_consumed_unavailable", "Consumed evidence is absent after recovery.", 409
                    )
                if current["status"] == "consumed":
                    return self.get(current["run_id"])
            if not replay_of:
                r.research_facts.guard(
                    conn,
                    manifest["source"],
                    [(p["inst_id"], manifest["start"], manifest["end"]) for p in manifest["packages"]],
                    allowed=("portfolio", holdout["id"]) if holdout else None,
                )
            active = conn.execute(
                "SELECT (SELECT COUNT(*) FROM portfolio_runs WHERE status IN ('queued','running')) + (SELECT COUNT(*) FROM pro_runs WHERE status IN ('queued','running'))"
            ).fetchone()[0]
            if active >= 10:
                raise PlatformError("research_queue_full", "The shared research queue is full.", 429)
            if holdout:
                existing = r.protocol.admit(conn, holdout, identifier, timestamp)
                if existing:
                    return self.get(existing)
                manifest["governance"] = dict(
                    protocol_version=2,
                    kind="portfolio",
                    holdout_id=holdout["id"],
                    plan_hash=holdout["plan_hash"],
                    input_hash=holdout["input_hash"],
                    primary_run_id=identifier,
                    scope="research_api_workflow_seal",
                )
            pointer = r.artifacts.put(conn, dumps(bundle))
            manifest.update(input_artifact=pointer, input_hash=digest(bundle))
            conn.execute(
                "INSERT INTO portfolio_runs VALUES(?,?,'queued',?,?,NULL,NULL,0,?,?)",
                (identifier, manifest["source"], dumps(config), dumps(manifest), timestamp, timestamp),
            )
            version = (
                r.portfolio_registry.version(config["portfolio_version_id"])
                if config.get("portfolio_version_id")
                else None
            )
            r.research_facts.record(
                conn,
                "portfolio",
                identifier,
                config,
                manifest["source"],
                [(p["inst_id"], manifest["start"], manifest["end"]) for p in manifest["packages"]],
                timestamp,
                project_id=version["project_id"] if version else None,
                version_id=version["id"] if version else None,
                replay_of=replay_of,
                root_run_id=manifest.get("root_run_id") or identifier,
            )
            self.store.audit(
                conn,
                manifest["source"],
                "pro.portfolio_queued",
                "Captured shared-capital portfolio research queued",
                {
                    "run_id": identifier,
                    "actor": actor,
                    "manifest_hash": digest(manifest),
                    "replay_of": replay_of,
                },
            )
        r.wake.set()
        return self.get(identifier)

    def replay(self, identifier, actor):
        run = self.get(identifier)
        manifest = run["manifest"]
        if run["status"] != "completed" or not manifest.get("input_artifact"):
            raise PlatformError(
                "portfolio_replay_unavailable",
                "Replay requires a completed captured-input portfolio run. Legacy runs must be researched again.",
                409,
            )
        if manifest["implementation"]["code_fingerprint"] != self.runtime.engine_identity["code_fingerprint"]:
            raise PlatformError(
                "portfolio_replay_implementation",
                "Install the recorded implementation before replaying its evidence.",
                409,
            )
        bundle = self.runtime.artifacts.resolve(manifest["input_artifact"])
        if digest(bundle) != manifest["input_hash"]:
            raise PlatformError(
                "portfolio_input_integrity", "Captured portfolio inputs failed their hash check.", 409
            )
        copied = {
            k: v
            for k, v in manifest.items()
            if k not in {"result_hash", "completed_at", "replay_verified", "input_artifact"}
        }
        copied.update(
            replay_of=identifier,
            root_run_id=manifest.get("root_run_id") or identifier,
            expected_result_hash=manifest["result_hash"],
        )
        return self.enqueue(run["config"], copied, bundle, actor, replay_of=identifier)

    def compute(self, identifier):
        run = self.get(identifier)
        with self.store.write() as conn:
            claim = conn.execute(
                "UPDATE portfolio_runs SET status='running',progress=.01,updated_at=? WHERE id=? AND status='queued'",
                (now_ms(), identifier),
            )
            if claim.rowcount != 1:
                return
        try:
            manifest, config = run["manifest"], run["config"]
            if (
                manifest["config_hash"] != digest(config)
                or manifest["implementation"]["code_fingerprint"]
                != self.runtime.engine_identity["code_fingerprint"]
            ):
                raise PlatformError(
                    "portfolio_identity", "Captured configuration or installed implementation changed.", 409
                )
            from .research_protocol import decode_legs

            if manifest.get("input_artifact"):
                bundle = self.runtime.artifacts.resolve(manifest["input_artifact"])
                if (
                    digest(bundle) != manifest["input_hash"]
                    or digest(bundle["config"]) != manifest["config_hash"]
                ):
                    raise PlatformError(
                        "portfolio_input_integrity",
                        "Captured computation bundle failed its identity check.",
                        409,
                    )
            else:
                # Preserve the observable legacy path for pre-upgrade queued work.
                bundle = {"legs": self.capture(config, manifest)}
            legs = decode_legs(bundle)

            def progress(fraction):
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE portfolio_runs SET progress=?,updated_at=? WHERE id=?",
                        (fraction, now_ms(), identifier),
                    )

            with localcontext(ACCOUNTING_CONTEXT):
                if self.runtime.settings.research_process_isolation:
                    from .research_process import run_isolated

                    result = run_isolated(
                        "portfolio",
                        json_safe({"config": config, "manifest": manifest, "legs": legs}),
                        timeout=self.runtime.settings.research_timeout_seconds,
                        memory_mb=self.runtime.settings.research_memory_budget_mb,
                        progress=progress,
                        cancelled=self.runtime.research_cancelled,
                    )
                else:
                    if manifest.get("evaluation_plan"):
                        from .research_protocol import evaluate_frozen_portfolio

                        result = evaluate_frozen_portfolio(config, manifest, legs, progress)
                    else:
                        result = simulate_portfolio(config, manifest, legs, progress)
            if manifest.get("replay_of"):
                manifest["replay_verified"] = digest(result) == manifest["expected_result_hash"]
                if not manifest["replay_verified"]:
                    raise PlatformError(
                        "portfolio_replay_mismatch",
                        "Recomputed results differ from the captured original; replay is not verified.",
                        409,
                    )
            manifest = manifest | {"result_hash": digest(result), "completed_at": now_ms()}
            with self.store.write() as conn:
                pointer = self.runtime.artifacts.put(conn, dumps(result))
                conn.execute(
                    "UPDATE portfolio_runs SET status='completed',progress=1,result=?,manifest=?,updated_at=? WHERE id=?",
                    (dumps(pointer), dumps(manifest), now_ms(), identifier),
                )
        except Exception as exc:
            if isinstance(exc, PlatformError) and exc.code == "research_cancelled":
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE portfolio_runs SET status='queued',progress=0,error=NULL,updated_at=? WHERE id=?",
                        (now_ms(), identifier),
                    )
                return
            logging.getLogger("tidebench.portfolio").exception("Portfolio research failed: %s", identifier)
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE portfolio_runs SET status='failed',error=?,updated_at=? WHERE id=?",
                    (str(exc)[:1000], now_ms(), identifier),
                )


def simulate_portfolio(config, manifest, legs, progress=lambda _: None):
    if config.get("evaluation", "full") == "train_test":
        lifecycle = config.get("universe_mode") == "historical_lifecycle"
        count = (
            (manifest["end"] - manifest["start"]) // CATALOG_BARS[manifest["bar"]]
            if lifecycle
            else len(legs[0]["candles"])
        )
        split = count * config["train_pct"] // 100
        first_test = split + config["embargo_bars"]
        if min(split, count - first_test) < 20:
            raise PlatformError("portfolio_split", "Independent windows need at least 20 bars.", 422)
        train_end = manifest["start"] + split * CATALOG_BARS[manifest["bar"]]
        train_legs = [
            leg
            | {
                "candles": [c for c in leg["candles"] if c.ts < train_end],
                "marks": [c for c in leg["marks"] if c.ts < train_end],
            }
            if lifecycle
            else leg | {"candles": leg["candles"][:split], "marks": leg["marks"][:split]}
            for leg in legs
        ]
        train_manifest = manifest | {"end": train_end} if lifecycle else manifest
        train = _simulate_portfolio(config, train_manifest, train_legs, lambda p: progress(p * 0.5))
        test = _simulate_portfolio(
            config, manifest, legs, lambda p: progress(0.5 + p * 0.5), first_trading_index=first_test
        )
        return test | {
            "evaluation": {
                "mode": "train_test",
                "parameter_selection": "Fixed pre-declared construction; no optimization or test-score selection.",
                "capital_policy": "Independent initial capital, flat inventory and reset risk budget in each window.",
                "warmup": "Pre-test closes initialize indicators and momentum; no pre-test orders or positions are carried.",
                "train_start": manifest["start"] if lifecycle else legs[0]["candles"][0].ts,
                "train_end": train_end
                if lifecycle
                else legs[0]["candles"][split - 1].ts + CATALOG_BARS[manifest["bar"]],
                "test_start": manifest["start"] + first_test * CATALOG_BARS[manifest["bar"]]
                if lifecycle
                else legs[0]["candles"][first_test].ts,
                "test_end": manifest["end"]
                if lifecycle
                else legs[0]["candles"][-1].ts + CATALOG_BARS[manifest["bar"]],
                "embargo_bars": config["embargo_bars"],
                "train_metrics": train["metrics"],
                "train_result_hash": digest(train),
                "test_result_hash": digest(test),
                "scope": "Chronological evaluation, not a blinded one-use holdout. Top-level financial records cover test only.",
            }
        }
    return _simulate_portfolio(config, manifest, legs, progress)


def _simulate_portfolio(config, manifest, legs, progress=lambda _: None, *, first_trading_index=0):
    source, interval = manifest["source"], CATALOG_BARS[manifest["bar"]]
    lifecycle = config.get("universe_mode") == "historical_lifecycle"
    count = (manifest["end"] - manifest["start"]) // interval if lifecycle else len(legs[0]["candles"])
    strict_execution = config.get("execution_contract") in SUPPORTED_EXECUTION_CONTRACTS
    if (
        config.get("execution_contract") is not None
        and config.get("execution_contract") not in SUPPORTED_EXECUTION_CONTRACTS
    ):
        raise PlatformError("portfolio_execution_contract", "Unsupported portfolio execution contract.", 422)
    # Missing contract denotes retained pre-upgrade captured inputs. New API inputs
    # always carry an explicit versioned contract; this branch never rewrites stored evidence.
    execution_status, compensation = "running", None
    if not lifecycle and any(
        len(leg["candles"]) != count or [c.ts for c in leg["candles"]] != [c.ts for c in leg["marks"]]
        for leg in legs
    ):
        raise PlatformError("portfolio_alignment", "Trade and mark bars must align exactly across legs.", 422)
    timestamps = (
        list(range(manifest["start"], manifest["end"], interval))
        if lifecycle
        else [c.ts for c in legs[0]["candles"]]
    )
    if not lifecycle and any([c.ts for c in leg["candles"]] != timestamps for leg in legs):
        raise PlatformError(
            "portfolio_alignment",
            "All leg timestamps must align; missing bars are never forward-filled.",
            422,
        )
    with TemporaryDirectory(prefix="tidebench-portfolio-") as directory:
        clock = [manifest["start"]]
        order_sequence = sequence(1)
        book = SimulationBook(
            Store(Path(directory) / "book.sqlite3"),
            clock=lambda: clock[0],
            capture_contributions=False,
            order_id_factory=lambda: f"order-{next(order_sequence)}",
        )
        if lifecycle:
            initialize_lifecycle(book.store)
        initial = D(config["initial_cash"])
        with book.store.write() as conn:
            delta = initial - D(10000)
            book.post(
                conn,
                source,
                "portfolio-capital",
                "Research capital",
                "portfolio-capital",
                [("USDT", "cash", delta), ("USDT", "contributed_capital", -delta)],
            )
            conn.execute(
                "UPDATE pro_accounts SET cash=?,initial_cash=?,day_equity=? WHERE source=?",
                (str(initial), str(initial), str(initial), source),
            )
        book.set_risk(
            source,
            {
                "fee_bps": config["fee_bps"],
                "slippage_bps": config["slippage_bps"],
                "liquidation_fee_bps": config["liquidation_fee_bps"],
                "max_order_notional": config["max_order_notional"] if strict_execution else "1000000000",
                "max_gross_exposure_pct": config["max_gross_pct"],
                "max_leverage": 50,
                "max_daily_loss_pct": config["max_daily_loss_pct"],
            },
            "research-policy",
        )
        if strict_execution:
            book.capital.set_policy(
                source, {"max_base_asset_gross_pct": config["max_base_asset_gross_pct"]}, "research-policy"
            )
        states = []
        for leg in legs:
            strategy = dict(leg["config"]["strategy"])
            for key in (
                "allocation",
                "entry",
                "exit",
                "momentum_entry",
                "max_bar_vol_pct",
                "efficiency_max",
                "z_entry",
                "z_exit",
                "stop_loss_pct",
                "take_profit_pct",
                "trailing_stop_pct",
                "risk_per_trade_pct",
            ):
                strategy[key] = D(str(strategy.get(key, getattr(StrategyConfig(), key))))
            states.append(
                _DecisionState(
                    ResearchConfig(strategy=StrategyConfig(**strategy), direction=leg["config"]["direction"])
                )
            )
        decisions, equity, errors, funding, pending = [], [], [], [], None
        exit_states = {}
        symbol_legs = {leg["instrument"]["inst_id"]: leg for leg in legs}
        candle_maps = {s: {c.ts: c for c in leg["candles"]} for s, leg in symbol_legs.items()}
        mark_maps = {s: {c.ts: c for c in leg["marks"]} for s, leg in symbol_legs.items()}
        lifecycle_rows, lifecycle_applications, lifecycle_issues = [], [], []
        lifecycle_incomplete = False
        histories = {s: [] for s in symbol_legs}
        processed_events = set()
        current_eligibility = {}
        blocked_symbols = set()
        history_epochs = {}

        def bar(leg, index, mark=False):
            if not lifecycle:
                return leg["marks" if mark else "candles"][index]
            symbol = leg["instrument"]["inst_id"]
            return (mark_maps if mark else candle_maps)[symbol].get(timestamps[index])

        def lifecycle_state(ts):
            return {
                s: eligibility(
                    leg["config"].get("lifecycle_events", []),
                    ts,
                    candle_maps[s],
                    interval,
                    config["lifecycle_warmup_bars"],
                )
                for s, leg in symbol_legs.items()
            }

        def stop_lifecycle(code, symbol, **evidence):
            nonlocal lifecycle_incomplete, pending
            lifecycle_incomplete, pending = True, None
            blocked_symbols.add(symbol)
            issue = {"ts": clock[0], "inst_id": symbol, "code": code, **evidence}
            if issue not in lifecycle_issues:
                lifecycle_issues.append(issue)

        def snapshots(index, phase="open", timestamp=None):
            output = {}
            for leg in legs:
                instrument, tiers = leg["instrument"], leg["tiers"]
                symbol = instrument["inst_id"]
                if lifecycle:
                    info = current_eligibility[symbol]
                    if (
                        symbol in blocked_symbols
                        or info["instrument"] is None
                        or info["state"] in {"unknown", "delisted"}
                    ):
                        continue
                    instrument, tiers = dict(info["instrument"]), info["margin_tiers"]

                eligible = [
                    event
                    for event in leg["config"]["rule_events"]
                    if event["effective_ts"] <= clock[0] and event["known_at"] <= clock[0]
                ]
                if eligible:
                    instrument, tiers = eligible[-1]["instrument"], eligible[-1]["margin_tiers"]
                candle, mark = bar(leg, index), bar(leg, index, True)
                if candle is None or mark is None:
                    continue
                price, mark_price = getattr(candle, phase), getattr(mark, phase)
                output[instrument["inst_id"]] = {
                    "source": source,
                    "inst_id": instrument["inst_id"],
                    "ts": clock[0] if timestamp is None else timestamp,
                    "mark_ts": clock[0] if timestamp is None else timestamp,
                    "last": price,
                    "bid": price,
                    "ask": price,
                    "mark": mark_price,
                    "instrument": instrument,
                    "margin_tiers": tiers,
                }
            return output

        def submit(symbol, quantity, reduce, quotes, key, liquidation=False, *, propagate=False):
            if not quantity:
                return
            leg = symbol_legs[symbol]
            try:
                if lifecycle and (symbol in blocked_symbols or not current_eligibility[symbol]["tradable"]):
                    raise PlatformError(
                        "lifecycle_not_tradable",
                        "Lifecycle evidence blocks execution in this market at the current boundary.",
                        409,
                    )
                return book.submit(
                    {
                        "source": source,
                        "inst_id": symbol,
                        "side": "buy" if quantity > 0 else "sell",
                        "quantity": str(abs(quantity)),
                        "leverage": leg["config"]["leverage"],
                        "reduce_only": reduce,
                        "order_type": "market",
                        "margin_mode": "isolated",
                    },
                    key,
                    quotes,
                    "portfolio-research",
                    liquidation=liquidation,
                )
            except PlatformError as exc:
                errors.append(
                    {"ts": clock[0], "inst_id": symbol, "key": key, "code": exc.code, "message": exc.message}
                )
                if propagate:
                    raise

        def liquidate(quotes, phase):
            account = book.account(source, quotes)
            for position in account["positions"]:
                if position["inst_type"] != "SWAP" or position["unrealized_pnl"] is None:
                    continue
                allowance = (
                    D(position["market_value"])
                    * (D(config["fee_bps"]) + D(config["liquidation_fee_bps"]))
                    / 10000
                )
                if (
                    D(position["margin"]) + D(position["unrealized_pnl"])
                    <= D(position["maintenance_margin"]) + allowance
                ):
                    if lifecycle and not current_eligibility[position["inst_id"]]["tradable"]:
                        stop_lifecycle("lifecycle_liquidation_unexecutable", position["inst_id"])
                        continue
                    submit(
                        position["inst_id"],
                        -D(position["quantity"]),
                        True,
                        quotes,
                        f"liquidation:{phase}:{position['inst_id']}:{clock[0]}",
                        True,
                    )

        for index, ts in enumerate(timestamps):
            if index % 50 == 0:
                progress((index + 1) / count * 0.95)
            clock[0] = ts
            if lifecycle:
                current_eligibility = lifecycle_state(ts)
                for symbol, info in current_eligibility.items():
                    if ts not in candle_maps[symbol] or ts not in mark_maps[symbol]:
                        info.update(tradable=False, reason="missing_price_bar")
                    epoch = info["eligible_since"]
                    if epoch != history_epochs.get(symbol):
                        position_index = list(symbol_legs).index(symbol)
                        states[position_index] = _DecisionState(states[position_index].config)
                        histories[symbol] = []
                        history_epochs[symbol] = epoch
                for symbol, leg in symbol_legs.items():
                    for event in leg["config"].get("lifecycle_events", []):
                        event_hash = digest(event)
                        if (
                            event_hash in processed_events
                            or event["effective_ts"] > ts
                            or event["known_at"] > ts
                        ):
                            continue
                        if index < first_trading_index:
                            processed_events.add(event_hash)
                            continue
                        prior_rules = [
                            e
                            for e in leg["config"].get("lifecycle_events", [])
                            if e["effective_ts"] < event["effective_ts"]
                            and e["known_at"] <= ts
                            and e.get("instrument")
                        ]
                        if event["kind"] in {"listing", "resume", "rules"} and prior_rules:
                            old_rules = max(prior_rules, key=lambda e: e["effective_ts"])["instrument"]
                            if not compatible_units(old_rules, event["instrument"]) or base_size(
                                old_rules
                            ) != base_size(event["instrument"]):
                                stop_lifecycle("lifecycle_unit_event_required", symbol, event_hash=event_hash)
                        if event["kind"] in {"cash_settlement", "unit_conversion"}:
                            try:
                                if event["known_at"] > event["effective_ts"]:
                                    raise PlatformError(
                                        "lifecycle_late_inventory_fact",
                                        "A late inventory fact cannot retrospectively mutate intervening fills and funding.",
                                        409,
                                    )
                                applied = apply_inventory_event(book, source, event)
                                lifecycle_applications.append(applied)
                            except PlatformError as exc:
                                stop_lifecycle(exc.code, symbol, event_hash=event_hash, message=exc.message)
                        processed_events.add(event_hash)
                for position in book.positions(source):
                    symbol = position["inst_id"]
                    info = current_eligibility[symbol]
                    old = json.loads(position["metadata"])
                    new = info["instrument"]
                    if (
                        info["state"] in {"unknown", "delisted"}
                        or bar(symbol_legs[symbol], index) is None
                        or bar(symbol_legs[symbol], index, True) is None
                    ):
                        stop_lifecycle(
                            "lifecycle_inventory_unvalued",
                            symbol,
                            state=info["state"],
                            quantity=position["quantity"],
                        )
                    elif new is not None and (
                        not compatible_units(old, new) or base_size(old) != base_size(new)
                    ):
                        stop_lifecycle(
                            "lifecycle_unconverted_inventory", symbol, quantity=position["quantity"]
                        )
                lifecycle_rows.append(
                    {
                        "ts": ts,
                        "markets": {
                            s: {k: v for k, v in info.items() if k not in {"instrument", "margin_tiers"}}
                            for s, info in current_eligibility.items()
                        },
                    }
                )
            if index < first_trading_index:
                for state, leg in zip(states, legs, strict=True):
                    candle = bar(leg, index)
                    symbol = leg["instrument"]["inst_id"]
                    if candle is not None and (
                        not lifecycle or current_eligibility[symbol]["state"] == "eligible"
                    ):
                        state.on_bar(candle)
                        histories[symbol].append(candle)
                continue
            quotes = snapshots(index)
            # Realized funding at a boundary applies to pre-existing inventory.
            events = sorted(
                (event for leg in legs for event in leg["funding"] if ts <= int(event["ts"]) < ts + interval),
                key=lambda e: (int(e["ts"]), e["inst_id"]),
            )

            def settle(event, quotes=quotes):
                clock[0] = int(event["ts"])
                exact = {key: dict(value, ts=clock[0], mark_ts=clock[0]) for key, value in quotes.items()}
                if event["inst_id"] not in exact:
                    if any(p["inst_id"] == event["inst_id"] for p in book.positions(source)):
                        stop_lifecycle("lifecycle_funding_mark_unavailable", event["inst_id"])
                    return
                exact[event["inst_id"]]["mark"] = D(str(event["mark_price"]))
                funding.extend(book.settle_funding(source, event["inst_id"], [event], exact))
                liquidate(exact, "funding")

            for event in events:
                if int(event["ts"]) == ts:
                    settle(event)
            clock[0] = ts
            liquidate(quotes, "open")
            book.observe(source, quotes)
            if compensation is not None and not lifecycle_incomplete:
                batch, failed_decision = compensation
                resume_compensation(
                    batch,
                    quotes,
                    lambda: {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)},
                    lambda s, q, reduce, key, quotes=quotes: submit(
                        s, q, reduce, quotes, key, propagate=True
                    ),
                )
                failed_decision.update(
                    status=batch["status"],
                    residuals=batch["residuals"],
                    quantity_residuals=batch["residuals"]["quantities"],
                )
                if batch["status"] == "compensated":
                    execution_status, compensation = "failed", None
            if pending is not None and not lifecycle_incomplete:
                weights, decision = pending
                capital = (
                    D(book.account(source, quotes)["equity"] or 0)
                    * D(str(config.get("capital_pct", 100)))
                    / 100
                )
                targets = target_quantities(
                    {s: w for s, w in weights.items() if s in quotes}, capital, quotes
                )
                if lifecycle:
                    positions_now = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    for symbol, info in current_eligibility.items():
                        if symbol in quotes and (
                            not info["tradable"]
                            or not decision.get("eligibility", {}).get(symbol, {}).get("tradable", True)
                        ):
                            targets[symbol] = positions_now.get(symbol, D(0))

                if decision.get("exit_only"):
                    current_positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    targets = {
                        symbol: D(0)
                        if symbol in decision["risk_exits"]
                        else current_positions.get(symbol, D(0))
                        for symbol in targets
                    }
                if strict_execution:
                    decision["execution"] = batch = execute_batch(
                        targets,
                        capital,
                        quotes,
                        config,
                        {s: leg["config"]["leverage"] for s, leg in symbol_legs.items()},
                        lambda: {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)},
                        lambda quotes=quotes: book.account(source, quotes),
                        lambda s, q, reduce, key, quotes=quotes: submit(
                            s, q, reduce, quotes, key, propagate=True
                        ),
                        f"rebalance:{index}",
                        policy_state=lambda: {
                            "risk": book.risk(source),
                            "capital": book.capital.policy(source),
                        },
                    )
                    decision.update(
                        executed_at=ts,
                        cash_scale=batch["cash_scale"],
                        quantity_targets=batch["targets"],
                        quantity_residuals=batch["residuals"]["quantities"],
                        residuals=batch["residuals"],
                        rebalance_skips=batch["skipped"],
                        status=batch["status"],
                    )
                    errors.extend(
                        row | {"ts": clock[0], "key": f"rebalance:{index}:add:{row['inst_id']}"}
                        for row in batch["skipped"]
                    )
                    if batch.get("failure"):
                        if (
                            batch["failure"]["phase"] not in {"reduce", "add"}
                            or batch["failure"]["code"] == "portfolio_execution_failure"
                        ):
                            errors.append(batch["failure"] | {"ts": clock[0], "key": f"rebalance:{index}"})
                        execution_status = "failed" if batch["status"] == "compensated" else "compensating"
                        compensation = (batch, decision) if batch["status"] == "compensating" else None
                else:
                    # Shared domain planning is also used by the managed controller.
                    positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    reductions = reduction_plan(targets, positions, quotes)
                    for symbol, quantity in reductions.quantities.items():
                        submit(symbol, quantity, True, quotes, f"rebalance:{index}:reduce:{symbol}")
                    positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    plan = addition_plan(
                        targets,
                        positions,
                        quotes,
                        book.account(source, quotes)["available_cash"],
                        {s: leg["config"]["leverage"] for s, leg in symbol_legs.items()},
                        config["fee_bps"],
                        config["slippage_bps"],
                    )
                    scale = plan.cash_scale
                    for symbol, quantity in plan.quantities.items():
                        submit(symbol, quantity, False, quotes, f"rebalance:{index}:add:{symbol}")
                    errors.extend(
                        row | {"ts": clock[0], "key": f"rebalance:{index}:add:{row['inst_id']}"}
                        for row in [*reductions.skipped, *plan.skipped]
                    )
                    positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    residual = {
                        symbol: str(target - positions.get(symbol, D(0)))
                        for symbol, target in targets.items()
                    }
                    decision.update(
                        executed_at=ts,
                        cash_scale=str(scale),
                        quantity_targets={s: str(q) for s, q in targets.items()},
                        quantity_residuals=residual,
                        rebalance_skips=[*reductions.skipped, *plan.skipped],
                    )
            pending = None
            for event in events:
                if int(event["ts"]) > ts:
                    settle(event)
            # OHLC scenario: funding precedes each position's adverse mark.
            clock[0] = ts + interval
            adverse = snapshots(index, "close")
            for position in book.positions(source):
                if (
                    symbol_legs[position["inst_id"]]["instrument"]["inst_type"] == "SWAP"
                    and position["inst_id"] in adverse
                ):
                    symbol = position["inst_id"]
                    leg = symbol_legs[symbol]
                    adverse[symbol]["mark"] = (
                        bar(leg, index, True).low
                        if D(position["quantity"]) > 0
                        else bar(leg, index, True).high
                    )
                    adverse[symbol]["bid"] = adverse[symbol]["ask"] = adverse[symbol]["mark"]
            liquidate(adverse, "adverse_mark")
            closing = snapshots(index, "close")
            account = book.observe(source, closing)
            if lifecycle and (lifecycle_incomplete or account["equity"] is None):
                lifecycle_incomplete = True
                account = account | {
                    "equity": None,
                    "valuation_status": "unavailable",
                    "unrealized_pnl": None,
                }
            equity.append(
                {
                    "ts": clock[0],
                    **{
                        key: account[key]
                        for key in (
                            "equity",
                            "cash",
                            "used_margin",
                            "unrealized_pnl",
                            "realized_pnl",
                            "fees_paid",
                            "funding_paid",
                            "insurance_debt",
                            "positions",
                        )
                    },
                }
            )
            signals = []
            for state, leg in zip(states, legs, strict=True):
                symbol, candle = leg["instrument"]["inst_id"], bar(leg, index)
                if lifecycle and (candle is None or current_eligibility[symbol]["state"] != "eligible"):
                    signals.append((None, {"lifecycle": current_eligibility[symbol]["reason"]}))
                    histories[symbol] = []
                else:
                    signal, features = state.on_bar(candle)
                    histories[symbol].append(candle)
                    histories[symbol] = histories[symbol][
                        -max(config["lookback"], config["risk_window"], 400) - 1 :
                    ]
                    signals.append((signal, features))
            risk_exits = {}
            positions = {p["inst_id"]: p for p in book.positions(source)}
            for leg, state in zip(legs, states, strict=True):
                symbol = leg["instrument"]["inst_id"]
                position = positions.get(symbol)
                if (
                    not position
                    or lifecycle
                    and (not current_eligibility[symbol]["tradable"] or bar(leg, index) is None)
                ):
                    exit_states.pop(symbol, None)
                    continue
                meta = json.loads(position["metadata"])
                generation = meta["position_generation"]
                prior = exit_states.get(symbol, {})
                peak = (
                    D(prior["peak"]) if prior.get("generation") == generation else D(position["entry_price"])
                )
                bars = max(0, (clock[0] - meta["position_opened_at"] + interval - 1) // interval)
                reason, peak = exit_on_close(
                    state.config.strategy,
                    D(position["quantity"]),
                    D(position["entry_price"]),
                    bar(leg, index).close,
                    peak,
                    bars,
                )
                exit_states[symbol] = {"generation": generation, "peak": str(peak), "holding_closes": bars}
                if reason:
                    risk_exits[symbol] = reason
            rebalance_due = (index - first_trading_index) % config["rebalance_bars"] == 0
            if (
                lifecycle_incomplete
                or account["equity"] is None
                or execution_status != "running"
                or index == count - 1
                or not rebalance_due
                and not risk_exits
            ):
                continue
            past = (
                [e for e in legs[1]["funding"] if int(e["ts"]) < ts]
                if config["mode"] == "funding_carry"
                else []
            )
            carry_evidence = (
                funding_carry_evidence(config, past, ts, config["fee_bps"], config["slippage_bps"])
                if config["mode"] == "funding_carry"
                else None
            )
            risk_evidence = None
            selected_legs = [
                leg
                for leg in legs
                if not lifecycle or current_eligibility[leg["instrument"]["inst_id"]]["tradable"]
            ]
            if config["mode"] == "risk_momentum":
                selected_legs = [
                    leg
                    for leg in selected_legs
                    if not lifecycle
                    or len(histories[leg["instrument"]["inst_id"]])
                    > max(config["lookback"], config["risk_window"])
                ]
                if not selected_legs:
                    continue
                risk_evidence = risk_momentum_weights(
                    config,
                    [leg["config"] | {"inst_id": leg["instrument"]["inst_id"]} for leg in selected_legs],
                    {
                        leg["instrument"]["inst_id"]: histories[leg["instrument"]["inst_id"]][
                            -max(config["lookback"], config["risk_window"]) - 1 :
                        ]
                        if lifecycle
                        else leg["candles"][
                            max(0, index - max(config["lookback"], config["risk_window"])) : index + 1
                        ]
                        for leg in selected_legs
                    },
                    ts,
                    interval,
                )
            weights = construction_weights(
                config,
                [leg["config"] | {"inst_id": leg["instrument"]["inst_id"]} for leg in legs],
                {
                    leg["instrument"]["inst_id"]: signal
                    for leg, (signal, _) in zip(legs, signals, strict=True)
                },
                {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)},
                {
                    leg["instrument"]["inst_id"]: histories[leg["instrument"]["inst_id"]][-1].close
                    / histories[leg["instrument"]["inst_id"]][-1 - config["lookback"]].close
                    - 1
                    for leg in selected_legs
                    if len(histories[leg["instrument"]["inst_id"]]) > config["lookback"]
                }
                if index >= config["lookback"]
                else {},
                carry_evidence["mean_rate"] if carry_evidence and carry_evidence["allowed"] else None,
                risk_evidence=risk_evidence,
            )
            if lifecycle:
                weights = {s: weights.get(s, D(0)) for s in symbol_legs}
                for symbol in weights:
                    if not current_eligibility[symbol]["tradable"]:
                        weights[symbol] = D(0)
                if config["mode"] == "funding_carry" and any(
                    not info["tradable"] for info in current_eligibility.values()
                ):
                    weights = {s: D(0) for s in weights}
            if config["mode"] == "funding_carry" and risk_exits:
                risk_exits = {leg["instrument"]["inst_id"]: "carry_group_exit" for leg in legs}
            caps = {
                leg["instrument"]["inst_id"]: risk_notional(
                    D(account["equity"]), state.config.strategy, config["fee_bps"], config["slippage_bps"]
                )
                for leg, state in zip(legs, states, strict=True)
            }
            weights = apply_weight_caps(
                config["mode"],
                weights,
                caps,
                max(D(0), D(account["equity"]) * D(str(config.get("capital_pct", 100))) / 100),
                risk_exits,
            )
            if risk_evidence:
                weights, risk_evidence = constrain_risk_weights(config, weights, risk_evidence)
                if lifecycle:
                    weights = {s: weights.get(s, D(0)) for s in symbol_legs}
            decision = {
                **({"eligibility": lifecycle_rows[-1]["markets"]} if lifecycle else {}),
                "ts": clock[0],
                "bar_ts": ts,
                "weights": {s: str(w) for s, w in weights.items()},
                "signals": [
                    {"inst_id": leg["instrument"]["inst_id"], "signal": signal, "features": features}
                    for leg, (signal, features) in zip(legs, signals, strict=True)
                ],
                "mode": config["mode"],
                "risk_exits": risk_exits,
                **({"carry_evidence": carry_evidence} if carry_evidence else {}),
                **({"risk_evidence": risk_evidence} if risk_evidence else {}),
                "exit_only": not rebalance_due,
                "status": "next_open",
            }
            decisions.append(decision)
            pending = (weights, decision)
            if index % 50 == 0:
                progress((index + 1) / count * 0.95)
        final = book.account(source, closing)
        if lifecycle_incomplete:
            final = final | {"equity": None, "valuation_status": "unavailable", "unrealized_pnl": None}
        with book.store.read() as conn:
            rows = conn.execute(
                "SELECT id,key,body FROM pro_orders WHERE source=? ORDER BY created_at,key", (source,)
            ).fetchall()
        identities = {row["id"]: f"order-{i + 1}" for i, row in enumerate(rows)}
        orders = [
            json.loads(row["body"]) | {"id": identities[row["id"]], "command_key": row["key"]} for row in rows
        ]
        ledger = book.ledger(source, limit=1000000)
        balances = {}
        for row in ledger:
            row["tx_id"] = identities.get(row["tx_id"], row["tx_id"])
            row["reference"] = identities.get(row["reference"], row["reference"])
        with localcontext() as ledger_context:
            ledger_context.prec = 200
            for row in ledger:
                balances[row["asset"]] = balances.get(row["asset"], D(0)) + D(row["debit"]) - D(row["credit"])
        if any(balances.values()):
            raise RuntimeError("Portfolio native-asset journal does not balance")
        peak, drawdown = initial, D(0)
        for point in equity:
            if point["equity"] is None:
                continue
            value = D(point["equity"])
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        result = {
            "schema_version": 1,
            "mode": config["mode"],
            **(
                {
                    "execution_contract": config["execution_contract"],
                    "execution_policy": {
                        k: config[k]
                        for k in (
                            "max_order_notional",
                            "max_base_asset_gross_pct",
                            "max_gross_pct",
                            "max_daily_loss_pct",
                        )
                    },
                    "failure_policy": config["failure_policy"],
                    "max_residual_pct": config["max_residual_pct"],
                    "execution_status": execution_status,
                    "economic_state": "incomplete_lifecycle"
                    if lifecycle_incomplete
                    else "incomplete_compensation"
                    if execution_status == "compensating"
                    else "marked_inventory",
                }
                if strict_execution
                else {}
            ),
            "metrics": {
                "initial_cash": str(initial),
                "final_equity": final["equity"],
                "total_return_pct": str((D(final["equity"]) / initial - 1) * 100)
                if final["equity"] is not None
                else None,
                "max_drawdown_pct": str(drawdown * 100) if not lifecycle_incomplete else None,
                "fees_paid": final["fees_paid"],
                "funding_paid": final["funding_paid"],
                "insurance_debt": final["insurance_debt"],
                **(
                    realized_portfolio_metrics(equity, orders, initial, interval)
                    if not lifecycle_incomplete
                    else {"risk_metrics_status": "unavailable_incomplete_economics"}
                ),
                "orders": len(orders),
                "execution_rejections": len(errors),
            },
            "equity": equity,
            "decisions": decisions,
            "orders": orders,
            "funding": funding,
            "ledger": ledger,
            "execution_rejections": errors,
            "final_account": final,
            "assumptions": {
                "capital": "one shared SimulationBook, one USDT cash balance, native-asset double entry",
                "weights": "signed notional / pre-rebalance equity; reductions first, common cash scaling, lot rounding",
                "fills": (
                    f"sequential next-open market fills; shared {config['execution_contract']} preflight, residual limit and compensated failure; actual costs retained"
                    if strict_execution
                    else "sequential next-open market fills; no atomic multi-leg guarantee; residuals retained"
                ),
                "spread": "zero synthetic spread; configured adverse slippage and fees",
                "funding": "captured realized settlement marks; lagged known settlements for carry signals",
                "intrabar": "boundary funding before orders; intrabar funding before adverse mark; gap/adverse full isolated liquidation",
                "universe": manifest["universe_scope"],
                "rules": config["rules_mode"],
                "no_history_inference": "No missing bars are forward-filled; current captured rules are not historical point-in-time rules.",
            },
            "input_hash": manifest.get("input_hash") or digest({"config": config, "manifest": manifest}),
        }

        if lifecycle:
            result["lifecycle"] = {
                "parser_version": PARSER_VERSION,
                "eligibility": lifecycle_rows,
                "applications": lifecycle_applications,
                "issues": lifecycle_issues,
                "status": "incomplete" if lifecycle_incomplete else "complete_within_supplied_scope",
                "source_hashes": sorted(
                    {
                        e["source"]["content_hash"]
                        for leg in legs
                        for e in leg["config"].get("lifecycle_events", [])
                    }
                ),
                "scope": "Attributed bounded events for selected markets; source byte hashes do not independently authenticate historical dates or comprehensive venue membership.",
                "conversion_scope": "Same inst_id linear contract units preserving signed base exposure; spot redenomination and symbol renames unsupported.",
                "forward_support": "Historical lifecycle accounting is not implemented by the managed current-market controller; a release requires explicit lifecycle-scope review.",
            }
        from .research_economics import explain_portfolio

        result["economics"] = explain_portfolio(result, config, legs, manifest)
        return result
