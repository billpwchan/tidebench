"""Bounded, restartable research jobs and a single-instance paper strategy supervisor."""

import asyncio
import hashlib
import logging
from decimal import ROUND_DOWN, Decimal

from . import __version__
from .engine import (
    BacktestConfig,
    Candle,
    EngineError,
    Instrument,
    StrategyConfig,
    run_backtest,
    target_position,
    validate_candles,
)
from .paper import FEE_RATE, SLIPPAGE_RATE, DeskError, fresh_quote, quote_price
from .schemas import OrderInput, RunInput, StrategyInput
from .store import dumps, encode, now_ms

INTERVALS = {"15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
logger = logging.getLogger("tidebench.worker")


def strategy_config(raw: dict) -> StrategyConfig:
    validated = StrategyInput.model_validate(raw)
    return StrategyConfig(**validated.model_dump())


def deserialize_candles(items):
    return [
        Candle(
            ts=row["ts"],
            **{k: Decimal(row[k]) for k in ("open", "high", "low", "close", "volume")},
            confirmed=row["confirmed"],
        )
        for row in items
    ]


def deserialize_instrument(raw):
    return Instrument(
        **{k: Decimal(v) if k in ("tick_size", "lot_size", "min_size") else v for k, v in raw.items()}
    )


def dataset_hash(candles) -> str:
    return hashlib.sha256(dumps(candles).encode()).hexdigest()


class Supervisor:
    def __init__(self, store, market, desk, settings):
        self.store, self.market, self.desk, self.settings = store, market, desk, settings
        self.wake = asyncio.Event()
        self.tasks = []
        self.inflight = set()

    async def start(self):
        with self.store.write() as conn:
            conn.execute("UPDATE runs SET status='queued',updated_at=? WHERE status='running'", (now_ms(),))
        self.tasks = [asyncio.create_task(self.research_loop()), asyncio.create_task(self.strategy_loop())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        # Cancellation of an asyncio waiter cannot cancel a thread's SQLite transaction.
        # Keep the process lease until all bounded computations/fills have finished.
        if self.inflight:
            await asyncio.gather(*self.inflight, return_exceptions=True)

    async def offload(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.inflight.add(task)
        task.add_done_callback(self.inflight.discard)
        return await asyncio.shield(task)

    async def research_loop(self):
        while True:
            with self.store.read() as conn:
                row = conn.execute(
                    "SELECT id FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
            if row:
                await self.perform_run(row["id"])
                continue
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            except TimeoutError:
                pass

    async def perform_run(self, run_id):
        run = self.store.run(run_id, include_snapshot=True)
        config = RunInput.model_validate(run["config"])
        with self.store.write() as conn:
            conn.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (now_ms(), run_id))
        try:
            snapshot = run["snapshot"]
            if snapshot is None:
                history, instruments = await asyncio.gather(
                    self.market.get_candles(config.inst_id, config.bar, config.limit, config.source),
                    self.market.get_instruments(config.source),
                )
                instrument = next((i for i in instruments if i.inst_id == config.inst_id), None)
                if instrument is None:
                    raise EngineError("Instrument metadata is unavailable for this region.")
                candles = history["candles"]
                if len(candles) < config.limit:
                    raise EngineError(
                        f"Requested {config.limit} bars but only {len(candles)} confirmed bars are available."
                    )
                snapshot = encode(
                    {"candles": candles, "instrument": instrument, "fetched_at": history["fetched_at"]}
                )
                manifest = {
                    "schema_version": 1,
                    "engine_version": __version__,
                    "build_sha": self.settings.build_sha,
                    "strategy_version": "1",
                    "source": config.source,
                    "region": self.settings.region,
                    "venue": "OKX" if config.source == "okx" else "synthetic-example",
                    "dataset_hash": dataset_hash(snapshot["candles"]),
                    "fetched_at": history["fetched_at"],
                    "interval_ms": INTERVALS[config.bar],
                    "start_ts": candles[0].ts,
                    "end_ts": candles[-1].ts + INTERVALS[config.bar],
                    "instrument": encode(instrument),
                    "execution": "closed signal -> next bar open",
                    "fee_currency": "USDT",
                    "config": encode(config.model_dump()),
                    "data_rights": "Local research snapshot; exchange data terms apply.",
                }
                # Persist exact input before computation. Restart/replay never silently fetches newer input.
                with self.store.write() as conn:
                    conn.execute(
                        "UPDATE runs SET snapshot=?,manifest=?,updated_at=? WHERE id=?",
                        (dumps(snapshot), dumps(manifest), now_ms(), run_id),
                    )
            else:
                instrument = deserialize_instrument(snapshot["instrument"])
                candles = deserialize_candles(snapshot["candles"])
                manifest = run["manifest"] | {
                    "engine_version": __version__,
                    "build_sha": self.settings.build_sha,
                }
                with self.store.write() as conn:
                    conn.execute("UPDATE runs SET manifest=? WHERE id=?", (dumps(manifest), run_id))
            engine_config = BacktestConfig(
                initial_cash=config.initial_cash,
                fee_bps=config.fee_bps,
                slippage_bps=config.slippage_bps,
                strategy=strategy_config(config.strategy.model_dump()),
            )
            result = await self.offload(
                run_backtest, candles, INTERVALS[config.bar], instrument, engine_config
            )
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE runs SET status='completed',result=?,error=NULL,updated_at=? WHERE id=?",
                    (dumps(result), now_ms(), run_id),
                )
                self.store.audit(
                    conn, config.source, "research.completed", "Backtest completed", {"run_id": run_id}
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Expected adapter and engine failures are safe human-readable messages. Never return a traceback.
            from .market import MarketError

            error = (
                str(exc)
                if isinstance(exc, (MarketError, EngineError, ValueError))
                else "The research job failed. See server logs."
            )
            logger.exception("Research job %s failed", run_id)
            with self.store.write() as conn:
                conn.execute(
                    "UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?",
                    (error, now_ms(), run_id),
                )
                self.store.audit(conn, config.source, "research.failed", error, {"run_id": run_id})

    async def strategy_loop(self):
        while True:
            for deployment in self.store.deployments(running_only=True):
                try:
                    await self.evaluate(deployment)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    from .market import MarketError

                    message = (
                        str(exc)
                        if isinstance(exc, (MarketError, EngineError, DeskError, ValueError))
                        else "Strategy evaluation failed. See server logs."
                    )
                    if not isinstance(exc, (MarketError, EngineError, DeskError, ValueError)):
                        logger.exception("Strategy %s failed", deployment["id"])
                    with self.store.write() as conn:
                        previous = conn.execute(
                            "SELECT last_error FROM deployments WHERE id=?", (deployment["id"],)
                        ).fetchone()
                        conn.execute(
                            "UPDATE deployments SET last_error=?,updated_at=? WHERE id=?",
                            (message, now_ms(), deployment["id"]),
                        )
                        if previous and previous["last_error"] != message:
                            self.store.audit(
                                conn,
                                deployment["source"],
                                "strategy.blocked",
                                message,
                                {"deployment_id": deployment["id"]},
                            )
            await asyncio.sleep(20)

    async def evaluate(self, deployment):
        source = deployment["source"]
        if self.store.risk(source)["kill_switch"]:
            return
        history = await self.market.get_candles(deployment["inst_id"], deployment["bar"], 720, source)
        candles = history["candles"]
        validate_candles(candles, INTERVALS[deployment["bar"]])
        latest = candles[-1]
        if deployment["last_bar"] is not None and latest.ts <= deployment["last_bar"]:
            return
        interval = INTERVALS[deployment["bar"]]
        if source == "okx" and now_ms() - (latest.ts + interval) > interval + 30_000:
            raise DeskError("stale_candles", "The strategy is paused because confirmed candles are stale.")
        target = target_position(candles, strategy_config(deployment["strategy"]))
        tickers, instruments = await asyncio.gather(
            self.market.get_tickers(source), self.market.get_instruments(source)
        )
        quote = next((q for q in tickers["items"] if q["inst_id"] == deployment["inst_id"]), None)
        instrument = next((i for i in instruments if i.inst_id == deployment["inst_id"]), None)
        if not quote or not instrument:
            raise DeskError("market_unavailable", "The strategy needs a valid quote and instrument rules.")
        fresh_quote(quote, source)
        if quote["ts"] < latest.ts + interval:
            raise DeskError(
                "quote_before_signal", "Waiting for a quote observed after the signal candle closed."
            )
        account = self.desk.account(source, tickers)
        position = next((p for p in account["positions"] if p["inst_id"] == deployment["inst_id"]), None)
        quantity = Decimal(position["quantity"]) if position else Decimal(0)
        side, qty = None, Decimal(0)
        if target is not None and target > 0 and quantity == 0:
            if account["equity"] is None:
                raise DeskError(
                    "valuation_unavailable", "The strategy needs complete account marks before buying."
                )
            side = "buy"
            price = quote_price(quote, "buy") * (1 + SLIPPAGE_RATE)
            price = (price / instrument.tick_size).to_integral_value(
                rounding="ROUND_CEILING"
            ) * instrument.tick_size
            budget = Decimal(account["cash"]) * target / (1 + FEE_RATE)
            qty = (budget / price / instrument.lot_size).to_integral_value(
                rounding=ROUND_DOWN
            ) * instrument.lot_size
        elif target == 0 and quantity > 0:
            side, qty = "sell", quantity
        if side and qty >= instrument.min_size:
            order = OrderInput(source=source, inst_id=deployment["inst_id"], side=side, quantity=qty)
            await self.offload(
                self.desk.place,
                order,
                f"strategy:{deployment['id']}:{latest.ts}",
                instrument,
                tickers,
                "strategy",
                f"{deployment['strategy']['kind']} decision after UTC bar {latest.ts}",
                deployment["id"],
                latest.ts,
            )
        else:
            with self.store.write() as conn:
                # Check halt/stop again; do not advance a stopped strategy from an in-flight observation.
                risk = conn.execute("SELECT kill_switch FROM risk WHERE source=?", (source,)).fetchone()
                if risk["kill_switch"]:
                    return
                advanced = conn.execute(
                    "UPDATE deployments SET last_bar=?,last_error=NULL,updated_at=? WHERE id=? AND status='running'",
                    (latest.ts, now_ms(), deployment["id"]),
                )
                if advanced.rowcount:
                    self.store.audit(
                        conn,
                        source,
                        "strategy.observed",
                        "Confirmed bar evaluated; no new fill",
                        {
                            "deployment_id": deployment["id"],
                            "bar_ts": latest.ts,
                            "target": str(target) if target is not None else None,
                        },
                    )
