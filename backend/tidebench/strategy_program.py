"""Bounded declarative strategy SDK: typed operands, no eval or arbitrary code."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .schemas import InputModel, StrategyInput

Feature = Literal[
    "close", "volume", "fast_sma", "slow_sma", "rsi", "zscore", "channel_upper", "channel_lower", "atr"
]


class Operand(InputModel):
    feature: Feature | None = None
    constant: Decimal | None = Field(default=None, ge=-1e12, le=1e12)

    @model_validator(mode="after")
    def one_operand(self):
        if (self.feature is None) == (self.constant is None):
            raise ValueError("An operand requires exactly one feature or constant.")
        return self


class Comparison(InputModel):
    left: Operand
    op: Literal["gt", "ge", "lt", "le"]
    right: Operand


class SignalRule(InputModel):
    conditions: list[Comparison] = Field(min_length=1, max_length=4)
    signal: Literal[-1, 0, 1]
    tag: str = Field(min_length=1, max_length=80)


class ProStrategyInput(StrategyInput):
    kind: Literal[
        "sma_cross", "rsi_reversion", "buy_hold", "close_breakout", "zscore_reversion", "program"
    ] = "sma_cross"
    window: int = Field(default=20, ge=2, le=400)
    z_entry: Decimal = Field(default=2, gt=0, le=10)
    z_exit: Decimal = Field(default=".5", ge=0, lt=10)
    atr_period: int = Field(default=14, ge=2, le=200)
    stop_loss_pct: Decimal = Field(default=0, ge=0, le=100)
    take_profit_pct: Decimal = Field(default=0, ge=0, le=1000)
    trailing_stop_pct: Decimal = Field(default=0, ge=0, le=100)
    max_holding_bars: int = Field(default=0, ge=0, le=100000)
    risk_per_trade_pct: Decimal = Field(default=0, ge=0, le=10)
    rules: list[SignalRule] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def program_bounds(self):
        if self.risk_per_trade_pct and not self.stop_loss_pct:
            raise ValueError("Loss-budget sizing requires a positive close-based stop loss.")
        if self.z_exit >= self.z_entry:
            raise ValueError("Z-score exit threshold must be below entry threshold.")
        if self.kind == "program" and not self.rules:
            raise ValueError("A program requires one to four ordered rules.")
        return self


def compare_rule(rule, features):
    """Unavailable operands never satisfy a rule. First matching rule wins."""

    def value(operand):
        return (
            features.get(operand["feature"])
            if operand.get("feature") is not None
            else Decimal(str(operand["constant"]))
        )

    for condition in rule["conditions"]:
        left, right = value(condition["left"]), value(condition["right"])
        if left is None or right is None:
            return False
        op = condition["op"]
        if not {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[op]:
            return False
    return True


def strategy_complexity(strategy):
    return 1 + (sum(len(rule["conditions"]) for rule in strategy.rules) if strategy.kind == "program" else 0)
