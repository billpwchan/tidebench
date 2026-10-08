"""Shared close-based exits and approximate loss-budget sizing.

These rules decide after a confirmed close. They are not intrabar stop orders;
gaps, spread, slippage and funding can exceed the nominal loss budget.
"""

from decimal import Decimal, localcontext
from functools import wraps

from .engine import ACCOUNTING_CONTEXT

D = Decimal


def accounted(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with localcontext(ACCOUNTING_CONTEXT):
            return fn(*args, **kwargs)

    return run


@accounted
def exit_on_close(strategy, quantity, entry, close, peak, bars):
    if not quantity:
        return None, close
    peak = max(peak or close, close) if quantity > 0 else min(peak or close, close)
    pnl = (close / entry - 1) * 100 if quantity > 0 else (1 - close / entry) * 100
    trailing = (1 - close / peak) * 100 if quantity > 0 else (close / peak - 1) * 100
    if strategy.stop_loss_pct and pnl <= -strategy.stop_loss_pct:
        return "close_stop_loss", peak
    if strategy.trailing_stop_pct and trailing >= strategy.trailing_stop_pct:
        return "close_trailing_stop", peak
    if strategy.take_profit_pct and pnl >= strategy.take_profit_pct:
        return "close_take_profit", peak
    if strategy.max_holding_bars and bars >= strategy.max_holding_bars:
        return "maximum_holding_closes", peak
    return None, peak


@accounted
def risk_notional(capital, strategy, fee_bps, slippage_bps):
    if not strategy.risk_per_trade_pct:
        return None
    loss_fraction = strategy.stop_loss_pct / 100 + 2 * (D(str(fee_bps)) + D(str(slippage_bps))) / 10000
    return capital * strategy.risk_per_trade_pct / 100 / loss_fraction
