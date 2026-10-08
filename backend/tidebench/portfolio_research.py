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
from .platform import PlatformError
from .pro_execution import SimulationBook, base_size, number, tier_for
from .pro_research import ResearchConfig, _DecisionState
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


class PortfolioInput(InputModel):
    name: str = Field(min_length=2, max_length=100)
    hypothesis: str = Field(min_length=12, max_length=4000)
    legs: list[PortfolioLeg] = Field(min_length=2, max_length=10)
    mode: Literal["fixed_weights", "independent_signals", "momentum", "funding_carry"] = "fixed_weights"
    initial_cash: Money = D(10000)
    fee_bps: Decimal = Field(default=10, ge=0, le=100)
    slippage_bps: Decimal = Field(default=5, ge=0, le=100)
    liquidation_fee_bps: Decimal = Field(default=50, ge=0, le=500)
    max_gross_pct: Decimal = Field(default=200, ge=1, le=1000)
    max_daily_loss_pct: Decimal = Field(default=5, ge=".1", le=50)
    rebalance_bars: int = Field(default=24, ge=1, le=1000)
    lookback: int = Field(default=20, ge=2, le=400)
    top_k: int = Field(default=1, ge=1, le=10)
    carry_threshold: Decimal = Field(default=0, ge=-0.01, le=0.01)
    rules_mode: Literal["captured_current", "point_in_time"] = "captured_current"
    evaluation: Literal["full", "train_test"] = "full"
    train_pct: int = Field(default=70, ge=50, le=85)
    embargo_bars: int = Field(default=1, ge=0, le=400)


class PortfolioResearch:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
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
        config = encode(PortfolioInput.model_validate(config).model_dump())
        packages = [self.runtime.packages.get_package(leg["package_id"]) for leg in config["legs"]]
        if any(not p["ready"] for p in packages):
            raise PlatformError(
                "portfolio_inputs", "Every portfolio leg needs a ready, verified data package.", 409
            )
        if len({(p["source"], p["bar"], p["start"], p["end"]) for p in packages}) != 1 or len(
            {p["inst_id"] for p in packages}
        ) != len(packages):
            raise PlatformError(
                "portfolio_alignment",
                "Use distinct markets with the same source, bar and exact UTC window.",
                422,
            )
        bars = (packages[0]["end"] - packages[0]["start"]) // CATALOG_BARS[packages[0]["bar"]]
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
        if sum((abs(D(leg["weight"])) for leg in config["legs"]), D(0)) * 100 > D(config["max_gross_pct"]):
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
            if config["rules_mode"] == "point_in_time" and (
                not events or events[0]["effective_ts"] > package["start"]
            ):
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
                or D(config["legs"][1]["weight"]) != -D(config["legs"][0]["weight"])
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
        inputs = [self.runtime.packages.research_inputs(p["id"]) for p in packages]
        manifest = {
            "schema_version": 1,
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
            "start": packages[0]["start"],
            "end": packages[0]["end"],
            "margin_tiers": margin_snapshots or {},
            "config_hash": digest(config),
            "created_by": actor,
            "rules_mode": config["rules_mode"],
            "universe_scope": "explicit_research_universe; no survivorship-bias-free listing history is inferred",
        }
        identifier, timestamp = new_id(), now_ms()
        with self.store.write() as conn:
            self.runtime.governance.guard_portfolio(conn, packages)
            active = (
                conn.execute(
                    "SELECT COUNT(*) FROM portfolio_runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                + conn.execute(
                    "SELECT COUNT(*) FROM pro_runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
            )
            if active >= 10:
                raise PlatformError("research_queue_full", "The shared research queue is full.", 429)
            conn.execute(
                "INSERT INTO portfolio_runs VALUES(?,?,'queued',?,?,NULL,NULL,0,?,?)",
                (identifier, manifest["source"], dumps(config), dumps(manifest), timestamp, timestamp),
            )
            self.store.audit(
                conn,
                manifest["source"],
                "pro.portfolio_queued",
                "Shared-capital portfolio research queued",
                {"run_id": identifier, "actor": actor, "manifest_hash": digest(manifest)},
            )
        return self.get(identifier)

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
            legs = []
            for leg, captured in zip(config["legs"], manifest["inputs"], strict=True):
                current = self.runtime.packages.research_inputs(leg["package_id"])
                if current != captured:
                    raise PlatformError("portfolio_inputs", "An input package identity changed.", 409)
                trade = self.runtime.catalog.get_dataset(captured["dataset_id"])
                for key in ("dataset_id", "mark_dataset_id", "funding_dataset_id"):
                    if captured.get(key):
                        self.runtime.catalog.verify_dataset(captured[key])
                candles = self.runtime.catalog.load_candles(captured["dataset_id"])
                marks = (
                    self.runtime.catalog.load_candles(captured["mark_dataset_id"])
                    if captured.get("mark_dataset_id")
                    else candles
                )
                package = self.runtime.packages.get_package(leg["package_id"])
                funding = package["manifest"]["funding_events"]
                tiers = (manifest["margin_tiers"].get(package["inst_id"]) or {}).get("tiers", [])
                legs.append(
                    {
                        "config": leg,
                        "instrument": trade["metadata"],
                        "candles": candles,
                        "marks": marks,
                        "funding": funding,
                        "tiers": tiers,
                    }
                )

            def progress(fraction):
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE portfolio_runs SET progress=?,updated_at=? WHERE id=?",
                        (fraction, now_ms(), identifier),
                    )

            with localcontext(ACCOUNTING_CONTEXT):
                if self.runtime.settings.research_process_isolation:
                    from .pro_research import json_safe
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
                    result = simulate_portfolio(config, manifest, legs, progress)
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
        count = len(legs[0]["candles"])
        split = count * config["train_pct"] // 100
        first_test = split + config["embargo_bars"]
        if min(split, count - first_test) < 20:
            raise PlatformError("portfolio_split", "Independent windows need at least 20 bars.", 422)
        train_legs = [
            leg | {"candles": leg["candles"][:split], "marks": leg["marks"][:split]} for leg in legs
        ]
        train = _simulate_portfolio(config, manifest, train_legs, lambda p: progress(p * 0.5))
        test = _simulate_portfolio(
            config, manifest, legs, lambda p: progress(0.5 + p * 0.5), first_trading_index=first_test
        )
        return test | {
            "evaluation": {
                "mode": "train_test",
                "parameter_selection": "Fixed pre-declared construction; no optimization or test-score selection.",
                "capital_policy": "Independent initial capital, flat inventory and reset risk budget in each window.",
                "warmup": "Pre-test closes initialize indicators and momentum; no pre-test orders or positions are carried.",
                "train_start": legs[0]["candles"][0].ts,
                "train_end": legs[0]["candles"][split - 1].ts + CATALOG_BARS[manifest["bar"]],
                "test_start": legs[0]["candles"][first_test].ts,
                "test_end": legs[0]["candles"][-1].ts + CATALOG_BARS[manifest["bar"]],
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
    count = len(legs[0]["candles"])
    if any(
        len(leg["candles"]) != count or [c.ts for c in leg["candles"]] != [c.ts for c in leg["marks"]]
        for leg in legs
    ):
        raise PlatformError("portfolio_alignment", "Trade and mark bars must align exactly across legs.", 422)
    timestamps = [c.ts for c in legs[0]["candles"]]
    if any([c.ts for c in leg["candles"]] != timestamps for leg in legs):
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
            order_id_factory=lambda: f"order-{next(order_sequence)}",
        )
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
                "max_order_notional": "1000000000",
                "max_gross_exposure_pct": config["max_gross_pct"],
                "max_leverage": 50,
                "max_daily_loss_pct": config["max_daily_loss_pct"],
            },
            "research-policy",
        )
        states = []
        for leg in legs:
            strategy = dict(leg["config"]["strategy"])
            for key in (
                "allocation",
                "entry",
                "exit",
                "z_entry",
                "z_exit",
                "stop_loss_pct",
                "take_profit_pct",
                "trailing_stop_pct",
                "risk_per_trade_pct",
            ):
                strategy[key] = D(str(strategy[key]))
            states.append(
                _DecisionState(
                    ResearchConfig(strategy=StrategyConfig(**strategy), direction=leg["config"]["direction"])
                )
            )
        decisions, equity, errors, funding, pending = [], [], [], [], None
        exit_states = {}
        symbol_legs = {leg["instrument"]["inst_id"]: leg for leg in legs}

        def snapshots(index, phase="open", timestamp=None):
            output = {}
            for leg in legs:
                instrument, tiers = leg["instrument"], leg["tiers"]
                eligible = [
                    event
                    for event in leg["config"]["rule_events"]
                    if event["effective_ts"] <= clock[0] and event["known_at"] <= clock[0]
                ]
                if eligible:
                    instrument, tiers = eligible[-1]["instrument"], eligible[-1]["margin_tiers"]
                candle, mark = leg["candles"][index], leg["marks"][index]
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

        def submit(symbol, quantity, reduce, quotes, key, liquidation=False):
            if not quantity:
                return
            leg = symbol_legs[symbol]
            try:
                book.submit(
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
            if index < first_trading_index:
                for state, leg in zip(states, legs, strict=True):
                    state.on_bar(leg["candles"][index])
                continue
            clock[0] = ts
            quotes = snapshots(index)
            # Realized funding at a boundary applies to pre-existing inventory.
            events = sorted(
                (event for leg in legs for event in leg["funding"] if ts <= int(event["ts"]) < ts + interval),
                key=lambda e: (int(e["ts"]), e["inst_id"]),
            )

            def settle(event, quotes=quotes):
                clock[0] = int(event["ts"])
                exact = {key: dict(value, ts=clock[0], mark_ts=clock[0]) for key, value in quotes.items()}
                exact[event["inst_id"]]["mark"] = D(str(event["mark_price"]))
                funding.extend(book.settle_funding(source, event["inst_id"], [event], exact))
                liquidate(exact, "funding")

            for event in events:
                if int(event["ts"]) == ts:
                    settle(event)
            clock[0] = ts
            liquidate(quotes, "open")
            book.observe(source, quotes)
            if pending is not None:
                weights, decision = pending
                capital = D(book.account(source, quotes)["equity"] or 0)
                targets = {}
                for symbol, weight in weights.items():
                    meta = quotes[symbol]["instrument"]
                    lot = D(str(meta["lot_size"]))
                    amount = (
                        capital * abs(weight) / (base_size(meta) * D(str(quotes[symbol]["last"]))) / lot
                    ).to_integral_value(rounding="ROUND_FLOOR") * lot
                    targets[symbol] = amount if weight >= 0 else -amount
                if decision.get("exit_only"):
                    current_positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                    targets = {
                        symbol: D(0)
                        if symbol in decision["risk_exits"]
                        else current_positions.get(symbol, D(0))
                        for symbol in targets
                    }
                # Reductions release cash before one common budget scales additions.
                for position in book.positions(source):
                    symbol = position["inst_id"]
                    old = D(position["quantity"])
                    desired = targets[symbol]
                    reduction = (
                        -old if desired * old <= 0 else desired - old if abs(desired) < abs(old) else D(0)
                    )
                    submit(symbol, reduction, True, quotes, f"rebalance:{index}:reduce:{symbol}")
                positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                additions = {
                    symbol: target - positions.get(symbol, D(0))
                    for symbol, target in targets.items()
                    if target
                    and (
                        not positions.get(symbol)
                        or target * positions[symbol] > 0
                        and abs(target) > abs(positions[symbol])
                    )
                }
                required = D(0)
                for symbol, quantity in additions.items():
                    leg = symbol_legs[symbol]
                    meta = quotes[symbol]["instrument"]
                    price = D(str(quotes[symbol]["last"])) * (
                        1 + D(config["slippage_bps"]) / 10000
                        if quantity > 0
                        else 1 - D(config["slippage_bps"]) / 10000
                    )
                    tick = D(str(meta["tick_size"]))
                    price = (price / tick).to_integral_value(
                        rounding="ROUND_CEILING" if quantity > 0 else "ROUND_FLOOR"
                    ) * tick
                    required += (
                        abs(quantity)
                        * base_size(meta)
                        * price
                        * (1 / D(leg["config"]["leverage"]) + D(config["fee_bps"]) / 10000)
                    )
                cash = D(book.account(source, quotes)["available_cash"])
                scale = min(D(1), max(D(0), cash / required)) if required else D(1)
                residual = {}
                for symbol, quantity in sorted(additions.items()):
                    lot = D(str(quotes[symbol]["instrument"]["lot_size"]))
                    size = (abs(quantity) * scale / lot).to_integral_value(rounding="ROUND_FLOOR") * lot
                    if size >= D(str(quotes[symbol]["instrument"]["min_size"])):
                        submit(
                            symbol,
                            size if quantity > 0 else -size,
                            False,
                            quotes,
                            f"rebalance:{index}:add:{symbol}",
                        )
                    else:
                        errors.append(
                            {
                                "ts": clock[0],
                                "inst_id": symbol,
                                "key": f"rebalance:{index}:add:{symbol}",
                                "code": "minimum_size",
                                "message": "Common cash scaling and lot rounding leave this leg below minimum size.",
                                "requested_quantity": str(abs(quantity)),
                                "scaled_quantity": str(size),
                                "cash_scale": str(scale),
                                "minimum_size": str(quotes[symbol]["instrument"]["min_size"]),
                            }
                        )
                positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                residual = {
                    symbol: str(target - positions.get(symbol, D(0))) for symbol, target in targets.items()
                }
                decision.update(
                    executed_at=ts,
                    cash_scale=str(scale),
                    quantity_targets={s: str(q) for s, q in targets.items()},
                    quantity_residuals=residual,
                )
            pending = None
            for event in events:
                if int(event["ts"]) > ts:
                    settle(event)
            # OHLC scenario: funding precedes each position's adverse mark.
            clock[0] = ts + interval
            adverse = snapshots(index, "close")
            for position in book.positions(source):
                if symbol_legs[position["inst_id"]]["instrument"]["inst_type"] == "SWAP":
                    symbol = position["inst_id"]
                    leg = symbol_legs[symbol]
                    adverse[symbol]["mark"] = (
                        leg["marks"][index].low if D(position["quantity"]) > 0 else leg["marks"][index].high
                    )
                    adverse[symbol]["bid"] = adverse[symbol]["ask"] = adverse[symbol]["mark"]
            liquidate(adverse, "adverse_mark")
            closing = snapshots(index, "close")
            account = book.observe(source, closing)
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
                signal, features = state.on_bar(leg["candles"][index])
                signals.append((signal, features))
            risk_exits = {}
            positions = {p["inst_id"]: p for p in book.positions(source)}
            for leg, state in zip(legs, states, strict=True):
                symbol = leg["instrument"]["inst_id"]
                position = positions.get(symbol)
                if not position:
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
                    leg["candles"][index].close,
                    peak,
                    bars,
                )
                exit_states[symbol] = {"generation": generation, "peak": str(peak), "holding_closes": bars}
                if reason:
                    risk_exits[symbol] = reason
            rebalance_due = (index - first_trading_index) % config["rebalance_bars"] == 0
            if index == count - 1 or not rebalance_due and not risk_exits:
                continue
            weights = {leg["instrument"]["inst_id"]: D(leg["config"]["weight"]) for leg in legs}
            if config["mode"] == "independent_signals":
                positions = {p["inst_id"]: D(p["quantity"]) for p in book.positions(source)}
                weights = {
                    leg["instrument"]["inst_id"]: abs(D(leg["config"]["weight"])) * signal
                    if signal is not None
                    else abs(D(leg["config"]["weight"]))
                    * (
                        1
                        if positions.get(leg["instrument"]["inst_id"], D(0)) > 0
                        else -1
                        if positions.get(leg["instrument"]["inst_id"], D(0)) < 0
                        else 0
                    )
                    for leg, (signal, _) in zip(legs, signals, strict=True)
                }
            elif config["mode"] == "momentum":
                ranks = (
                    sorted(
                        (
                            (
                                leg["candles"][index].close / leg["candles"][index - config["lookback"]].close
                                - 1,
                                leg["instrument"]["inst_id"],
                            )
                            for leg in legs
                        ),
                        reverse=True,
                    )
                    if index >= config["lookback"]
                    else []
                )
                chosen = {symbol for momentum, symbol in ranks[: config["top_k"]] if momentum > 0}
                weights = {s: w if s in chosen else D(0) for s, w in weights.items()}
            elif config["mode"] == "funding_carry":
                past = [e for e in legs[1]["funding"] if int(e["ts"]) < ts]
                rate = D(str(past[-1]["rate"])) if past else None
                if rate is None or rate < D(config["carry_threshold"]):
                    weights = {s: D(0) for s in weights}
            for leg, state in zip(legs, states, strict=True):
                symbol = leg["instrument"]["inst_id"]
                if symbol in risk_exits:
                    weights[symbol] = D(0)
                else:
                    cap = risk_notional(
                        D(account["equity"]), state.config.strategy, config["fee_bps"], config["slippage_bps"]
                    )
                    if cap is not None and D(account["equity"]) > 0:
                        weights[symbol] = (1 if weights[symbol] >= 0 else -1) * min(
                            abs(weights[symbol]), cap / D(account["equity"])
                        )
            decision = {
                "ts": clock[0],
                "bar_ts": ts,
                "weights": {s: str(w) for s, w in weights.items()},
                "signals": [
                    {"inst_id": leg["instrument"]["inst_id"], "signal": signal, "features": features}
                    for leg, (signal, features) in zip(legs, signals, strict=True)
                ],
                "mode": config["mode"],
                "risk_exits": risk_exits,
                "exit_only": not rebalance_due,
                "status": "next_open",
            }
            decisions.append(decision)
            pending = (weights, decision)
            if index % 50 == 0:
                progress((index + 1) / count * 0.95)
        final = book.account(source, closing)
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
            value = D(point["equity"])
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        return {
            "schema_version": 1,
            "mode": config["mode"],
            "metrics": {
                "initial_cash": str(initial),
                "final_equity": final["equity"],
                "total_return_pct": str((D(final["equity"]) / initial - 1) * 100),
                "max_drawdown_pct": str(drawdown * 100),
                "fees_paid": final["fees_paid"],
                "funding_paid": final["funding_paid"],
                "insurance_debt": final["insurance_debt"],
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
                "fills": "sequential next-open market fills; no atomic multi-leg guarantee; residuals retained",
                "spread": "zero synthetic spread; configured adverse slippage and fees",
                "funding": "captured realized settlement marks; lagged known settlements for carry signals",
                "intrabar": "boundary funding before orders; intrabar funding before adverse mark; gap/adverse full isolated liquidation",
                "universe": manifest["universe_scope"],
                "rules": config["rules_mode"],
                "no_history_inference": "No missing bars are forward-filled; current captured rules are not historical point-in-time rules.",
            },
            "input_hash": digest({"config": config, "manifest": manifest}),
        }
