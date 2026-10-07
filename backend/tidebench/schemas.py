from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["okx", "example"]
Bar = Literal["15m", "1H", "4H", "1Dutc"]
Symbol = Literal["BTC-USDT", "ETH-USDT", "SOL-USDT", "OKB-USDT", "DOGE-USDT"]
Money = Annotated[Decimal, Field(gt=0, le=Decimal("1000000000"), max_digits=28, decimal_places=12)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StrategyInput(InputModel):
    kind: Literal["sma_cross", "rsi_reversion", "buy_hold"] = "sma_cross"
    fast: int = Field(default=12, ge=2, le=200)
    slow: int = Field(default=26, ge=3, le=400)
    rsi_period: int = Field(default=14, ge=2, le=200)
    entry: Decimal = Field(default=Decimal("30"), ge=0, lt=100)
    exit: Decimal = Field(default=Decimal("60"), gt=0, le=100)
    allocation: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)

    @model_validator(mode="after")
    def validate_windows(self):
        if self.fast >= self.slow:
            raise ValueError("Fast window must be smaller than slow window.")
        if self.entry >= self.exit:
            raise ValueError("RSI entry must be smaller than exit.")
        return self


class RunInput(InputModel):
    source: Source = "okx"
    inst_id: Symbol = "BTC-USDT"
    bar: Bar = "1H"
    limit: int = Field(default=720, ge=60, le=2000)
    strategy: StrategyInput = Field(default_factory=StrategyInput)
    initial_cash: Money = Decimal("10000")
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=100)


class OrderInput(InputModel):
    source: Source
    inst_id: Symbol
    side: Literal["buy", "sell"]
    quantity: Money


class DeploymentInput(InputModel):
    source: Source
    inst_id: Symbol
    bar: Bar = "1H"
    strategy: StrategyInput = Field(default_factory=StrategyInput)


class RiskInput(InputModel):
    source: Source
    max_order_notional: Money
    max_position_pct: float = Field(ge=1, le=100, allow_inf_nan=False)
    max_daily_loss_pct: float = Field(ge=0.1, le=50, allow_inf_nan=False)


class KillInput(InputModel):
    source: Source
    active: bool
    reason: str = Field(min_length=3, max_length=300)
