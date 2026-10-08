"""Observed, cash-flow-adjusted account performance; no reconstructed history."""

import json
from decimal import Decimal, localcontext

from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .store import dumps, now_ms
from .strategy_registry import digest

D = Decimal


class ForwardPerformance:
    def __init__(self, store):
        self.store = store
        with store.write() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS forward_equity(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,market_ts INTEGER NOT NULL,ledger_sequence INTEGER NOT NULL,observed_at INTEGER NOT NULL,body TEXT NOT NULL,state_hash TEXT NOT NULL,UNIQUE(source,market_ts,ledger_sequence,state_hash));
                CREATE INDEX IF NOT EXISTS forward_equity_history ON forward_equity(source,id DESC);
            """)

    @staticmethod
    def capture(conn, source, snapshots, account):
        if not snapshots:
            return
        market_ts = max(int(s["ts"]) for s in snapshots.values())
        sequence = conn.execute(
            "SELECT COALESCE(MAX(id),0) FROM pro_ledger WHERE source=?", (source,)
        ).fetchone()[0]
        flow = sum(
            (
                D(r["credit"]) - D(r["debit"])
                for r in conn.execute(
                    "SELECT debit,credit FROM pro_ledger WHERE source=? AND asset='USDT' AND account='contributed_capital'",
                    (source,),
                )
            ),
            D(0),
        )
        body = {
            key: account[key]
            for key in (
                "equity",
                "cash",
                "available_cash",
                "used_margin",
                "unrealized_pnl",
                "realized_pnl",
                "fees_paid",
                "funding_paid",
                "insurance_debt",
                "valuation_status",
                "positions",
            )
        }
        body["external_capital"] = str(flow)
        body["quote_timestamps"] = {key: s["ts"] for key, s in snapshots.items()}
        conn.execute(
            "INSERT OR IGNORE INTO forward_equity(source,market_ts,ledger_sequence,observed_at,body,state_hash) VALUES(?,?,?,?,?,?)",
            (source, market_ts, sequence, now_ms(), dumps(body), digest(body)),
        )

    def report(self, source, *, limit=500, before=2**63 - 1):
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT * FROM forward_equity WHERE source=? AND id<? ORDER BY id DESC LIMIT ?",
                (source, before, limit),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM forward_equity WHERE source=?", (source,)).fetchone()[
                0
            ]
        items = [dict(row) | {"body": json.loads(row["body"])} for row in reversed(rows)]
        if any(digest(item["body"]) != item["state_hash"] for item in items):
            raise PlatformError("performance_integrity", "Forward observation failed its content check.", 409)
        with localcontext(ACCOUNTING_CONTEXT):
            valid = [
                item
                for item in items
                if item["body"]["equity"] is not None
                and item["body"]["valuation_status"] in {"fresh", "example"}
            ]
            twr, peak, drawdown, chain_complete = D(1), D(1), D(0), len(valid) == len(items)
            chain_valid = chain_complete
            for previous, current in zip(valid, valid[1:], strict=False):
                p, c = previous["body"], current["body"]
                if D(p["equity"]) <= 0:
                    chain_valid = False
                    continue
                flow = D(c["external_capital"]) - D(p["external_capital"])
                twr *= (D(c["equity"]) - flow) / D(p["equity"])
                peak = max(peak, twr)
                drawdown = max(drawdown, (peak - twr) / peak)
            summary = {
                "observations": len(items),
                "total_observations": total,
                "scope": "selected_observation_page",
                "complete_valuation_chain": chain_valid,
                "return": str(twr - 1) if len(valid) >= 2 and chain_valid else None,
                "max_drawdown": str(drawdown) if len(valid) >= 2 and chain_valid else None,
                "first_market_ts": valid[0]["market_ts"] if valid else None,
                "last_market_ts": valid[-1]["market_ts"] if valid else None,
                "warning": "Observed equity only. Missing/stale marks are explicit gaps. Returns use external cash flows at the observation boundary; no unobserved intraperiod path is inferred.",
            }
            if len(valid) >= 2:
                p, c = valid[0]["body"], valid[-1]["body"]
                summary["net_pnl"] = str(
                    D(c["equity"]) - D(p["equity"]) - (D(c["external_capital"]) - D(p["external_capital"]))
                )
                for key in ("fees_paid", "funding_paid", "realized_pnl"):
                    summary[key + "_change"] = str(D(c[key]) - D(p[key]))
        return {
            "items": items,
            "summary": summary,
            "next_before": items[0]["id"] if len(items) == limit else None,
        }
