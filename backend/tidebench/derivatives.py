"""Linear USDT perpetual arithmetic and explicit isolated-margin risk rules.

Quantity is always a number of contracts; it is never silently interpreted as
base currency. Helpers deliberately describe a full-liquidation model, not the
venue's partial-liquidation or order-book execution process.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from functools import wraps
from typing import Any

from .engine import ACCOUNTING_CONTEXT, ONE, ZERO, EngineError, _accounting_value, _decimal


def _accounting(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            with localcontext(ACCOUNTING_CONTEXT):
                return function(*args, **kwargs)
        except DecimalException as exc:
            raise EngineError("Derivative accounting exceeds the supported numeric domain") from exc

    return wrapped


def _positive(value: Decimal, name: str) -> Decimal:
    _decimal(value, name)
    _accounting_value(value, name)
    if value <= ZERO:
        raise EngineError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class LinearContract:
    inst_id: str
    base: str
    quote: str
    settle: str
    ct_val: Decimal
    ct_mult: Decimal
    ct_val_ccy: str
    tick_size: Decimal
    lot_size: Decimal
    min_size: Decimal
    state: str = "live"

    def __post_init__(self):
        if not self.inst_id or not self.base or self.quote != "USDT" or self.settle != "USDT":
            raise EngineError("only linear base-valued USDT-settled perpetual contracts are supported")
        if self.ct_val_ccy != self.base:
            raise EngineError("ct_val_ccy must equal the contract base currency")
        for name in ("ct_val", "ct_mult", "tick_size", "lot_size", "min_size"):
            _positive(getattr(self, name), name)
        if self.ct_mult != ONE:
            raise EngineError("non-unit ct_mult requires independently verified venue semantics")
        if self.state != "live":
            raise EngineError("contract is not live")

    @classmethod
    def from_catalog(cls, record: Mapping[str, Any]) -> LinearContract:
        if record.get("ct_type", "linear") != "linear" or record.get("inst_type", "SWAP") != "SWAP":
            raise EngineError("catalog instrument must be a linear SWAP")
        return cls(
            inst_id=record["inst_id"],
            base=record["base"],
            quote=record["quote"],
            settle=record.get("settle_ccy", record.get("settle", "")),
            ct_val=record["ct_val"],
            ct_mult=record["ct_mult"],
            ct_val_ccy=record["ct_val_ccy"],
            tick_size=record["tick_size"],
            lot_size=record["lot_size"],
            min_size=record["min_size"],
            state=record.get("state", "live"),
        )


@dataclass(frozen=True)
class MarginTier:
    tier: int
    min_contracts: Decimal
    max_contracts: Decimal
    imr: Decimal
    mmr: Decimal
    max_leverage: Decimal

    def __post_init__(self):
        if isinstance(self.tier, bool) or not isinstance(self.tier, int) or self.tier < 1:
            raise EngineError("margin tier must be a positive integer")
        for name in ("min_contracts", "max_contracts", "imr", "mmr", "max_leverage"):
            _decimal(getattr(self, name), name)
            _accounting_value(getattr(self, name), name)
        if not ZERO <= self.min_contracts < self.max_contracts:
            raise EngineError("margin tier contract bounds are invalid")
        if not ZERO < self.mmr <= self.imr <= ONE or self.max_leverage < ONE:
            raise EngineError("margin tier rates or leverage are invalid")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> MarginTier:
        return cls(
            tier=int(record["tier"]),
            min_contracts=record.get("min_contracts", record.get("min_size")),
            max_contracts=record.get("max_contracts", record.get("max_size")),
            imr=record["imr"],
            mmr=record["mmr"],
            max_leverage=record["max_leverage"],
        )


@dataclass(frozen=True)
class FundingEvent:
    ts: int
    rate: Decimal
    mark_price: Decimal | None = None
    formula_type: str | None = None
    method: str | None = None
    mark_price_source: str | None = None
    mark_ts: int | None = None
    inst_id: str | None = None

    def __post_init__(self):
        if isinstance(self.ts, bool) or not isinstance(self.ts, int) or self.ts < 0:
            raise EngineError("funding timestamp must be a non-negative integer")
        _decimal(self.rate, "realized funding rate")
        if self.rate.copy_abs() >= ONE:
            raise EngineError("absolute realized funding rate must be < 1")
        if self.mark_price is not None:
            _positive(self.mark_price, "settlement mark price")
        if self.mark_ts is not None and (isinstance(self.mark_ts, bool) or self.mark_ts != self.ts):
            raise EngineError("funding mark timestamp must match its settlement timestamp")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> FundingEvent:
        return cls(
            record["ts"],
            record["rate"],
            record.get("mark_price"),
            record.get("formula_type"),
            record.get("method"),
            record.get("mark_price_source"),
            record.get("mark_ts"),
            record.get("inst_id"),
        )


def validate_margin_tiers(tiers: list[MarginTier] | tuple[MarginTier, ...]) -> None:
    """Require ascending, contiguous tiers with explicit finite coverage.

    Engine boundary convention: min is exclusive and max is inclusive. OKX
    exposes shared raw boundaries without an equality rule; this conservative,
    reproducible scenario convention is disclosed in every research result.
    """
    if not tiers:
        raise EngineError("maintenance margin tiers are required")
    previous_max = ZERO
    previous_tier = 0
    previous_mmr = ZERO
    for tier in tiers:
        if not isinstance(tier, MarginTier):
            raise EngineError("margin tiers must be MarginTier values")
        if tier.min_contracts != previous_max or tier.tier <= previous_tier:
            raise EngineError("maintenance margin tiers must be ascending and contiguous from zero")
        if tier.mmr < previous_mmr:
            raise EngineError("maintenance margin rates must be non-decreasing")
        previous_max, previous_tier, previous_mmr = tier.max_contracts, tier.tier, tier.mmr


@_accounting
def contract_base_quantity(contracts: Decimal, contract: LinearContract) -> Decimal:
    _decimal(contracts, "contracts")
    return _accounting_value(contracts * contract.ct_val * contract.ct_mult, "base quantity")


contract_base_qty = contract_base_quantity


@_accounting
def linear_pnl(
    contracts: Decimal, entry_price: Decimal, mark_price: Decimal, contract: LinearContract
) -> Decimal:
    _positive(entry_price, "entry price")
    _positive(mark_price, "mark price")
    return _accounting_value(
        contract_base_quantity(contracts, contract) * (mark_price - entry_price), "linear pnl"
    )


unrealized_pnl = linear_pnl


@_accounting
def contract_notional(contracts: Decimal, mark_price: Decimal, contract: LinearContract) -> Decimal:
    _positive(mark_price, "mark price")
    return _accounting_value(
        contract_base_quantity(contracts, contract).copy_abs() * mark_price, "contract notional"
    )


def select_margin_tier(contracts: Decimal, tiers: list[MarginTier] | tuple[MarginTier, ...]) -> MarginTier:
    _decimal(contracts, "contracts")
    validate_margin_tiers(tiers)
    size = contracts.copy_abs()
    if size == ZERO:
        return tiers[0]
    for tier in tiers:
        if tier.min_contracts < size <= tier.max_contracts:
            return tier
    raise EngineError("position exceeds the supplied maintenance tier coverage")


@_accounting
def maintenance_margin(
    contracts: Decimal, mark_price: Decimal, contract: LinearContract, tier: MarginTier
) -> Decimal:
    if contracts != ZERO and not tier.min_contracts < contracts.copy_abs() <= tier.max_contracts:
        raise EngineError("maintenance tier does not cover the position")
    return _accounting_value(
        contract_notional(contracts, mark_price, contract) * tier.mmr, "maintenance margin"
    )


maintenance_requirement = maintenance_margin


@_accounting
def isolated_equity(
    margin: Decimal, contracts: Decimal, entry_price: Decimal, mark_price: Decimal, contract: LinearContract
) -> Decimal:
    _decimal(margin, "isolated margin")
    return _accounting_value(
        margin + linear_pnl(contracts, entry_price, mark_price, contract), "isolated equity"
    )


@_accounting
def funding_payment(
    contracts: Decimal, mark_price: Decimal, rate: Decimal, contract: LinearContract
) -> Decimal:
    """Positive means a debit: a positive rate charges longs and credits shorts."""
    _decimal(rate, "realized funding rate")
    _positive(mark_price, "funding mark price")
    return _accounting_value(
        contract_base_quantity(contracts, contract) * mark_price * rate, "funding payment"
    )


@_accounting
def liquidation_condition(
    margin: Decimal,
    contracts: Decimal,
    entry_price: Decimal,
    mark_price: Decimal,
    contract: LinearContract,
    tier: MarginTier,
    liquidation_fee_rate: Decimal = ZERO,
) -> bool:
    if contracts == ZERO:
        return False
    if not ZERO <= _decimal(liquidation_fee_rate, "liquidation fee rate") < ONE - tier.mmr:
        raise EngineError("maintenance and liquidation fee rates must sum to less than one")
    threshold = maintenance_margin(contracts, mark_price, contract, tier)
    threshold += contract_notional(contracts, mark_price, contract) * liquidation_fee_rate
    return isolated_equity(margin, contracts, entry_price, mark_price, contract) <= threshold


@_accounting
def liquidation_price(
    margin: Decimal,
    contracts: Decimal,
    entry_price: Decimal,
    contract: LinearContract,
    tier: MarginTier,
    liquidation_fee_rate: Decimal = ZERO,
) -> Decimal | None:
    """Continuous-price threshold for a fixed quantity/tier; not an order quote."""
    if contracts == ZERO:
        return None
    _positive(entry_price, "entry price")
    _decimal(margin, "isolated margin")
    if not tier.min_contracts < contracts.copy_abs() <= tier.max_contracts:
        raise EngineError("liquidation tier does not cover the position")
    rate = tier.mmr + _decimal(liquidation_fee_rate, "liquidation fee rate")
    if not ZERO <= liquidation_fee_rate or rate >= ONE:
        raise EngineError("maintenance and liquidation fee rates must sum to less than one")
    base = contract_base_quantity(contracts, contract).copy_abs()
    result = (
        (base * entry_price - margin) / (base * (ONE - rate))
        if contracts > ZERO
        else (margin + base * entry_price) / (base * (ONE + rate))
    )
    return max(_accounting_value(result, "liquidation threshold"), ZERO)


liquidation_threshold = liquidation_price
