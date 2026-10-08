"""Append-only confirmed bars and exact incremental indicator checkpoints."""

import json
from decimal import Decimal, localcontext

from .catalog import CATALOG_BARS
from .engine import ACCOUNTING_CONTEXT, Candle, StrategyConfig
from .platform import PlatformError
from .pro_research import ResearchConfig, _DecisionState
from .store import dumps, encode, now_ms
from .strategy_registry import digest
from .strategy_risk import exit_on_close


class ForwardHistory:
    def __init__(self, store, catalog):
        self.store, self.catalog = store, catalog
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS forward_bars(source TEXT NOT NULL,inst_id TEXT NOT NULL,bar TEXT NOT NULL,ts INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,dataset_id TEXT NOT NULL,PRIMARY KEY(source,inst_id,bar,ts));
                CREATE TABLE IF NOT EXISTS forward_checkpoints(deployment_id TEXT PRIMARY KEY,last_bar INTEGER NOT NULL,identity TEXT NOT NULL,state TEXT NOT NULL,state_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS forward_decisions(deployment_id TEXT NOT NULL,bar INTEGER NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(deployment_id,bar));
            """)

    async def prepare(self, deployment, end, implementation):
        config, identifier = deployment["config"], deployment["id"]
        source, symbol, bar = config["source"], config["inst_id"], config["bar"]
        interval = CATALOG_BARS[bar]
        latest = end - interval
        identity = digest({"config": config, "implementation": implementation})
        with self.store.read() as conn:
            checkpoint = conn.execute(
                "SELECT * FROM forward_checkpoints WHERE deployment_id=?", (identifier,)
            ).fetchone()
            maximum = conn.execute(
                "SELECT MAX(ts) FROM forward_bars WHERE source=? AND inst_id=? AND bar=?",
                (source, symbol, bar),
            ).fetchone()[0]
        start = max(end - 2000 * interval, (maximum + interval) if maximum is not None else 0)
        if checkpoint and (
            checkpoint["identity"] != identity
            or digest(json.loads(checkpoint["state"])) != checkpoint["state_hash"]
        ):
            raise PlatformError(
                "strategy_checkpoint",
                "Strategy checkpoint identity or integrity changed; stop and review a new version.",
                409,
            )
        if checkpoint and latest - checkpoint["last_bar"] > 2000 * interval:
            raise PlatformError(
                "strategy_catchup",
                "More than 2000 bars were missed. Stop and review a fresh deployment.",
                409,
            )
        if start < end:
            job = self.catalog.create_job(symbol, "trade", bar, start, end, source)
            job = await self.catalog.run_job(job["id"])
            if job["status"] != "completed":
                raise PlatformError("strategy_data", "Confirmed strategy history is incomplete.", 409)
            candles = self.catalog.load_candles(job["dataset_id"])
            with self.store.write() as conn:
                for candle in candles:
                    body = encode(candle.__dict__)
                    content_hash = digest(body)
                    previous = conn.execute(
                        "SELECT content_hash FROM forward_bars WHERE source=? AND inst_id=? AND bar=? AND ts=?",
                        (source, symbol, bar, candle.ts),
                    ).fetchone()
                    if previous and previous[0] != content_hash:
                        raise PlatformError(
                            "strategy_revision",
                            "A confirmed historical bar was revised; explicit review is required.",
                            409,
                        )
                    conn.execute(
                        "INSERT OR IGNORE INTO forward_bars VALUES(?,?,?,?,?,?,?)",
                        (source, symbol, bar, candle.ts, dumps(body), content_hash, job["dataset_id"]),
                    )
        after = checkpoint["last_bar"] + interval if checkpoint else end - 2000 * interval
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM forward_bars WHERE source=? AND inst_id=? AND bar=? AND ts>=? AND ts<? ORDER BY ts",
                (source, symbol, bar, after, end),
            ).fetchall()
        if (
            not rows
            or rows[-1]["ts"] != latest
            or any(row["ts"] != after + i * interval for i, row in enumerate(rows))
        ):
            raise PlatformError(
                "strategy_data", "Latest confirmed bar or a contiguous history segment is missing.", 409
            )
        strategy = dict(config["strategy"]) | {"allocation": Decimal(str(config["allocation"]))}
        for key in (
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
            strategy[key] = Decimal(
                str(
                    strategy.get(
                        key,
                        {
                            "z_entry": 2,
                            "z_exit": ".5",
                            "momentum_entry": ".5",
                            "max_bar_vol_pct": 5,
                            "efficiency_max": ".35",
                        }.get(key, 0),
                    )
                )
            )
        state = _DecisionState(
            ResearchConfig(strategy=StrategyConfig(**strategy), direction=config["direction"])
        )
        if checkpoint:
            state.restore(json.loads(checkpoint["state"]))
        with localcontext(ACCOUNTING_CONTEXT):
            chain = json.loads(checkpoint["state"]).get("data_chain", "") if checkpoint else ""
            for row in rows:
                body = json.loads(row["body"])
                if digest(body) != row["content_hash"]:
                    raise PlatformError(
                        "strategy_data_integrity", "Stored forward bar failed its content check.", 409
                    )
                signal, indicators = state.on_bar(
                    Candle(
                        **{
                            key: Decimal(value)
                            if key in {"open", "high", "low", "close", "volume"}
                            else value
                            for key, value in body.items()
                        }
                    )
                )
                chain = digest({"previous": chain, "bar_hash": row["content_hash"]})
            target = None if signal is None else Decimal(signal) * Decimal(str(config["allocation"]))
        with self.store.read() as conn:
            position = conn.execute(
                "SELECT * FROM pro_positions WHERE source=? AND inst_id=? AND quantity!='0'", (source, symbol)
            ).fetchone()
        exit_state = {}
        if position:
            meta = json.loads(position["metadata"])
            previous_exit = json.loads(checkpoint["state"]).get("exit_state", {}) if checkpoint else {}
            generation = meta.get("position_generation")
            peak = (
                Decimal(previous_exit["peak_close"])
                if previous_exit.get("generation") == generation and previous_exit.get("peak_close")
                else Decimal(position["entry_price"])
            )
            for row in rows:
                if row["ts"] + interval > meta.get("position_opened_at", end):
                    close = Decimal(json.loads(row["body"])["close"])
                    peak = max(peak, close) if Decimal(position["quantity"]) > 0 else min(peak, close)
            bars = max(0, (end - meta.get("position_opened_at", end) + interval - 1) // interval)
            reason, peak = exit_on_close(
                state.config.strategy,
                Decimal(position["quantity"]),
                Decimal(position["entry_price"]),
                Decimal(body["close"]),
                peak,
                bars,
            )
            exit_state = {
                "generation": generation,
                "peak_close": str(peak),
                "holding_closes": bars,
                "reason": reason,
            }
            if reason:
                target = Decimal(0)
                indicators = indicators | {"exit_reason": reason, "exit_reference": body["close"]}
        saved = state.snapshot() | {"data_chain": chain, "exit_state": exit_state}
        decision = {
            "schema_version": 1,
            "deployment_id": identifier,
            "bar": latest,
            "available_at": end,
            "source": source,
            "inst_id": symbol,
            "interval": bar,
            "new_bars": len(rows),
            "data_chain": chain,
            "last_bar_hash": rows[-1]["content_hash"],
            "dataset_id": rows[-1]["dataset_id"],
            "indicators": indicators,
            "exit_state": exit_state,
            "signal": signal,
            "target": str(target) if target is not None else None,
            "strategy_version_id": config.get("strategy_version_id"),
            "identity": identity,
            "execution_model": "post_close_observed_quote",
        }
        # Checkpoint, evidence and intent are one transaction. Retries resume
        # that intent, never silently recompute RSI from a sliding warmup.
        with self.store.write() as conn:
            active = conn.execute("SELECT status FROM pro_deployments WHERE id=?", (identifier,)).fetchone()
            if not active or active[0] != "running":
                raise PlatformError("strategy_stopped", "The strategy no longer owns this market.", 409)
            conn.execute(
                "INSERT OR REPLACE INTO forward_checkpoints VALUES(?,?,?,?,?)",
                (identifier, latest, identity, dumps(saved), digest(saved)),
            )
            conn.execute(
                "INSERT OR IGNORE INTO forward_decisions VALUES(?,?,?,?,?)",
                (identifier, latest, dumps(decision), digest(decision), now_ms()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO pro_strategy_intents VALUES(?,?,?,'pending',?)",
                (identifier, latest, decision["target"], now_ms()),
            )
            conn.execute(
                "UPDATE pro_strategy_intents SET status='superseded',updated_at=? WHERE deployment_id=? AND bar<? AND status='pending'",
                (now_ms(), identifier, latest),
            )
        return target

    def decisions(self, identifier, limit=100, before=2**63 - 1):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM forward_decisions WHERE deployment_id=? AND bar<? ORDER BY bar DESC LIMIT ?",
                (identifier, before, limit),
            ).fetchall()
            if not rows:
                return []
            placeholders = ",".join("?" for _ in rows)
            order_placeholders = ",".join("?" for _ in range(2 * len(rows)))
            bars = [row["bar"] for row in rows]
            orders = conn.execute(
                f"SELECT body,key FROM pro_orders WHERE key IN ({order_placeholders}) ORDER BY created_at",
                [f"strategy:{identifier}:{phase}:{bar}" for bar in bars for phase in ("open", "close")],
            ).fetchall()
            intents = {
                row["bar"]: dict(row)
                for row in conn.execute(
                    f"SELECT * FROM pro_strategy_intents WHERE deployment_id=? AND bar IN ({placeholders})",
                    [identifier, *bars],
                )
            }
        output = []
        for row in rows:
            body = json.loads(row["body"])
            if digest(body) != row["content_hash"]:
                raise PlatformError("decision_integrity", "Decision evidence failed its content check.", 409)
            output.append(
                body
                | {
                    "content_hash": row["content_hash"],
                    "created_at": row["created_at"],
                    "intent": intents.get(row["bar"]),
                    "orders": [
                        json.loads(o["body"]) for o in orders if o["key"].endswith(":" + str(row["bar"]))
                    ],
                }
            )
        return output
