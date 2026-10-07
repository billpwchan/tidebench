"""Read-only, captured spot/linear-perpetual portfolio risk scenarios.

These are deterministic parallel mark shocks, not a historical VaR model or
exchange liquidation prediction. Missing inputs remain unavailable; account
position marks are never used as a substitute for current source-bound marks.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, localcontext
from types import MappingProxyType
from typing import Any

from .derivatives import (
    LinearContract,
    MarginTier,
    contract_base_quantity,
    liquidation_price,
    select_margin_tier,
)
from .engine import (
    ACCOUNTING_CONTEXT,
    ONE,
    ZERO,
    EngineError,
    _accounting_value,
    _decimal,
    _fill_price,
    _string,
)

MODEL_VERSION = "portfolio-risk-1"
BPS = Decimal(10000)
HUNDRED = Decimal(100)
MAX_POSITIONS = 500
MAX_SCENARIOS = 25
MAX_TIERS = 200


class PortfolioAnalyticsError(EngineError):
    """Invalid request or arithmetic outside the supported accounting domain."""


def _number(value: Any, label: str, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | str | int):
        raise EngineError(f"{label} must be a finite decimal string, Decimal, or integer")
    if isinstance(value, str) and len(value) > 160:
        raise EngineError(f"{label} exceeds the supported numeric representation")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except DecimalException as exc:
        raise EngineError(f"{label} must be a finite decimal") from exc
    _decimal(result, label)
    _accounting_value(result, label)
    # Bound serialized scale without discarding the small increments in exchange data.
    if result != ZERO and result.adjusted() < -80:
        raise EngineError(f"{label} exceeds the supported numeric scale (adjusted exponent >= -80)")
    if positive and result <= ZERO or nonnegative and result < ZERO:
        raise EngineError(f"{label} must be {'positive' if positive else 'non-negative'}")
    return result


def _value(value: Decimal, label: str) -> Decimal:
    return _accounting_value(value, label)


def _s(value: Decimal | None) -> str | None:
    return None if value is None else _string(value)


def _timestamp(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EngineError(f"{label} must be a non-negative millisecond integer")
    return value


@dataclass(frozen=True)
class PriceShock:
    """Percentage price changes; market overrides asset, which overrides parallel.

    Changes are replacements, not additive. -100% is excluded because a zero
    mark cannot be valued/executed by the supported linear contract model.
    """

    name: str
    parallel_pct: Decimal = ZERO
    asset_pct: Mapping[str, Decimal] = field(default_factory=dict)
    market_pct: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise PortfolioAnalyticsError("scenario name must contain 1–80 characters")
        try:
            parallel = self._change(self.parallel_pct)
            assets = self._mapping(self.asset_pct)
            markets = self._mapping(self.market_pct)
        except EngineError as exc:
            raise PortfolioAnalyticsError(str(exc)) from exc
        # Copy external dictionaries: subsequent caller mutations cannot change a captured request.
        object.__setattr__(self, "parallel_pct", parallel)
        object.__setattr__(self, "asset_pct", MappingProxyType(assets))
        object.__setattr__(self, "market_pct", MappingProxyType(markets))

    @staticmethod
    def _change(value):
        result = _number(value, "price shock percentage")
        if not -HUNDRED < result <= Decimal(1000):
            raise EngineError("price shocks must be > -100% and <= 1000%")
        return result

    @classmethod
    def _mapping(cls, values):
        if not isinstance(values, Mapping) or len(values) > MAX_POSITIONS:
            raise EngineError("shock overrides must be mappings with at most 500 entries")
        output = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise EngineError("shock override keys must contain 1–80 characters")
            output[key] = cls._change(value)
        return output

    def for_position(self, inst_id: str, base: str) -> tuple[Decimal, str]:
        if inst_id in self.market_pct:
            return self.market_pct[inst_id], "market"
        if base in self.asset_pct:
            return self.asset_pct[base], "asset"
        return self.parallel_pct, "parallel"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "parallel_pct": _s(self.parallel_pct),
            "asset_pct": {key: _s(value) for key, value in sorted(self.asset_pct.items())},
            "market_pct": {key: _s(value) for key, value in sorted(self.market_pct.items())},
        }


DEFAULT_SCENARIOS = tuple(
    PriceShock(f"Parallel {change:+d}%", Decimal(change)) for change in (-20, -10, -5, 5, 10, 20)
)


def _issue(code: str, message: str, inst_id: str | None = None, scenario: str | None = None) -> dict:
    result = {"code": code, "message": message}
    if inst_id is not None:
        result["inst_id"] = inst_id
    if scenario is not None:
        result["scenario"] = scenario
    return result


def _json_safe(value):
    if isinstance(value, Decimal):
        # Scientific representation here is bounded and faithfully captures invalid raw inputs, too.
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | bool):
        return value
    raise PortfolioAnalyticsError(
        "captured inputs must be JSON values or Decimals; binary floats are unsupported"
    )


def _sum(values, label: str, issues: list[dict]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    try:
        return _value(sum(values, ZERO), label)
    except (EngineError, DecimalException):
        issues.append(_issue("accounting_domain", f"{label} exceeds the supported accounting domain."))
        return None


@dataclass
class _Position:
    inst_id: str
    inst_type: str | None = None
    base: str | None = None
    quantity: Decimal | None = None
    entry: Decimal | None = None
    basis: Decimal | None = None
    margin: Decimal | None = None
    mark: Decimal | None = None
    mark_ts: int | None = None
    base_quantity: Decimal | None = None
    net_notional: Decimal | None = None
    component: Decimal | None = None
    upnl: Decimal | None = None
    contract: LinearContract | None = None
    tier: MarginTier | None = None
    funding_pending: bool = False
    tradable: bool | None = None
    issues: list[dict] = field(default_factory=list)

    def fail(self, code, message):
        self.issues.append(_issue(code, message, self.inst_id))


def _instrument(meta: Mapping, label: str) -> dict:
    if not isinstance(meta, Mapping):
        raise EngineError(f"{label} instrument metadata is unavailable")
    output = dict(meta)
    for key in ("tick_size", "lot_size", "min_size"):
        output[key] = _number(meta.get(key), f"{label} {key}", positive=True)
    for key in ("inst_id", "inst_type", "base", "quote"):
        if not isinstance(meta.get(key), str) or not meta[key] or len(meta[key]) > 80:
            raise EngineError(f"{label} {key} is unavailable")
    if meta["quote"] != "USDT" or meta["inst_type"] not in {"SPOT", "SWAP"}:
        raise EngineError("only USDT-quoted spot and linear USDT-settled swaps are supported")
    if meta["inst_type"] == "SWAP":
        for key in ("ct_val", "ct_mult"):
            output[key] = _number(meta.get(key), f"{label} {key}", positive=True)
    return output


def _read_position(row, snapshots, source, as_of, max_age, max_tier_age) -> _Position:
    inst_id = row.get("inst_id") if isinstance(row, Mapping) else None
    p = _Position(inst_id if isinstance(inst_id, str) else "unknown")
    try:
        if not isinstance(row, Mapping) or not isinstance(inst_id, str) or not inst_id:
            raise EngineError("position requires an instrument identifier")
        meta = _instrument(row.get("instrument"), "held")
        p.inst_type, p.base = meta["inst_type"], meta["base"]
        if meta["inst_id"] != inst_id or row.get("inst_type") != p.inst_type:
            raise EngineError("held instrument identity/type disagrees with position")
        p.quantity = _number(row.get("quantity"), "position quantity")
        p.entry = _number(row.get("entry_price"), "entry price", positive=True)
        p.margin = _number(row.get("margin"), "isolated margin", nonnegative=True)
        if p.quantity == ZERO or p.inst_type == "SPOT" and p.quantity < ZERO:
            raise EngineError("positions must be nonzero; unlevered spot cannot be short")
        if p.inst_type == "SPOT" and p.margin != ZERO:
            raise EngineError("unlevered spot cannot carry isolated margin")
        if p.inst_type == "SPOT" and "basis" in row:
            p.basis = _number(row["basis"], "spot cost basis", nonnegative=True)
        if p.inst_type == "SWAP":
            # Unit arithmetic remains valid for a suspended held instrument; this grants no permission to trade it.
            p.contract = LinearContract.from_catalog({**meta, "state": "live"})
            p.base_quantity = contract_base_quantity(p.quantity, p.contract)
        else:
            p.base_quantity = p.quantity
    except (EngineError, DecimalException, KeyError, TypeError) as exc:
        p.fail("invalid_position", str(exc))
        return p
    snap = snapshots.get(inst_id)
    if not isinstance(snap, Mapping):
        p.fail(
            "missing_snapshot",
            "A current instrument snapshot is required; held-position marks are not substituted.",
        )
        return p
    if snap.get("source") != source or snap.get("inst_id") != inst_id:
        p.fail("snapshot_identity", "Snapshot source/instrument must match the account position.")
        return p
    try:
        current = _instrument(snap.get("instrument"), "current")
        keys = ["inst_id", "inst_type", "base", "quote"]
        if p.inst_type == "SWAP":
            keys += ["ct_type", "ct_val", "ct_mult", "ct_val_ccy", "settle_ccy"]
        if any(meta.get(key) != current.get(key) for key in keys):
            raise EngineError("current instrument units disagree with the held-position metadata")
        if p.inst_type == "SWAP":
            # Contract units match; hypothetical closes must use the current tick,
            # rather than a tick retained when the position was originally opened.
            p.contract = LinearContract.from_catalog({**current, "state": "live"})
        p.tradable = current.get("state", "live") == "live"
        p.mark_ts = _timestamp(
            snap.get("mark_ts") if p.inst_type == "SWAP" else snap.get("ts"), "mark timestamp"
        )
        snapshot_ts = _timestamp(snap.get("ts"), "snapshot timestamp")
        if source != "example" and (as_of - snapshot_ts > max_age or snapshot_ts - as_of > 5000):
            p.fail(
                "stale_snapshot" if snapshot_ts <= as_of else "future_snapshot",
                "Snapshot identity and mark observations must both be current.",
            )
            return p
        if source != "example" and (as_of - p.mark_ts > max_age or p.mark_ts - as_of > 5000):
            p.fail(
                "stale_mark" if p.mark_ts <= as_of else "future_mark",
                "Current mark is outside the accepted freshness window.",
            )
            return p
        raw_mark = snap.get("mark")
        if p.inst_type == "SPOT" and raw_mark is None:
            raw_mark = snap.get("last")
        p.mark = _number(raw_mark, "current mark", positive=True)
        p.net_notional = _value(p.base_quantity * p.mark, "position notional")
        p.upnl = (
            _value(p.base_quantity * (p.mark - p.entry), "unrealized pnl")
            if p.inst_type == "SWAP"
            else _value(p.net_notional - p.basis, "spot unrealized pnl")
            if p.basis is not None
            else None
        )
        p.component = (
            _value(p.margin + p.upnl, "isolated equity") if p.inst_type == "SWAP" else p.net_notional
        )
    except (EngineError, DecimalException, KeyError, TypeError) as exc:
        p.mark = p.net_notional = p.component = p.upnl = None
        p.fail("invalid_mark_or_units", str(exc))
        return p
    if p.inst_type == "SWAP":
        expected = meta.get("expected_funding_time")
        if expected is not None:
            try:
                cutoff = snapshot_ts if source == "example" else max(snapshot_ts, as_of)
                p.funding_pending = _timestamp(expected, "expected funding time") <= cutoff
            except EngineError:
                p.funding_pending = True
            if p.funding_pending:
                p.fail(
                    "funding_due_unreconciled",
                    "A known funding event is due or its cursor is invalid; settle actual historical funding before interpreting margin safety.",
                )
        try:
            tier_snapshot = snap.get("margin_tiers_snapshot")
            if tier_snapshot is not None:
                if not isinstance(tier_snapshot, Mapping) or any(
                    tier_snapshot.get(key) != value
                    for key, value in (
                        ("source", source),
                        ("inst_id", inst_id),
                        ("td_mode", "isolated"),
                        ("unit", "contracts"),
                    )
                ):
                    raise EngineError(
                        "maintenance snapshot source, instrument, isolated mode, and contract units must match"
                    )
                observed = _timestamp(tier_snapshot.get("observed_at"), "maintenance snapshot observation")
                if source != "example" and (as_of - observed > max_tier_age or observed - as_of > 5000):
                    raise EngineError("current maintenance tier snapshot is stale or future dated")
            records = snap.get("margin_tiers", tier_snapshot.get("tiers") if tier_snapshot else None)
            if not isinstance(records, list | tuple) or not 0 < len(records) <= MAX_TIERS:
                raise EngineError("current maintenance tiers are required (1–200 records)")
            tiers = []
            for record in records:
                if not isinstance(record, Mapping):
                    raise EngineError("maintenance tier must be an object")
                tier_number = record.get("tier")
                if (
                    isinstance(tier_number, bool)
                    or not isinstance(tier_number, int | str)
                    or isinstance(tier_number, str)
                    and (not tier_number.isascii() or not tier_number.isdecimal() or len(tier_number) > 4)
                ):
                    raise EngineError("maintenance tier number is invalid")
                tiers.append(
                    MarginTier(
                        int(tier_number),
                        _number(
                            record.get("min_contracts", record.get("min_size")),
                            "tier minimum",
                            nonnegative=True,
                        ),
                        _number(
                            record.get("max_contracts", record.get("max_size")), "tier maximum", positive=True
                        ),
                        _number(record.get("imr"), "initial margin ratio", positive=True),
                        _number(record.get("mmr"), "maintenance margin ratio", positive=True),
                        _number(record.get("max_leverage"), "maximum leverage", positive=True),
                    )
                )
            p.tier = select_margin_tier(p.quantity, tiers)
        except (EngineError, DecimalException, KeyError, TypeError, ValueError) as exc:
            p.fail("maintenance_unavailable", str(exc))
    return p


def _risk_fields() -> dict:
    return {
        "risk_status": "unavailable",
        "maintenance_margin": None,
        "close_fee_reserve": None,
        "maintenance_required": None,
        "maintenance_buffer": None,
        "maintenance_coverage": None,
        "maintenance_breach": None,
        "bankrupt": None,
        "liquidation_price": None,
        "liquidation_distance_pct": None,
        "tier": None,
    }


def _risk(p: _Position, mark: Decimal, component: Decimal, close_rate: Decimal) -> dict:
    result = _risk_fields()
    if p.inst_type == "SPOT":
        result.update(
            risk_status="not_applicable",
            maintenance_margin="0",
            close_fee_reserve="0",
            maintenance_required="0",
            maintenance_breach=False,
            bankrupt=False,
        )
        return result
    if p.funding_pending:
        return result
    result["bankrupt"] = component <= ZERO
    if p.tier is None:
        return result
    notional = _value((p.base_quantity * mark).copy_abs(), "shock notional")
    maintenance = _value(notional * p.tier.mmr, "maintenance margin")
    reserve = _value(notional * close_rate, "close fee reserve")
    required = _value(maintenance + reserve, "maintenance requirement")
    buffer = _value(component - required, "maintenance buffer")
    threshold = liquidation_price(p.margin, p.quantity, p.entry, p.contract, p.tier, close_rate)
    distance = (mark - threshold) / mark if p.quantity > ZERO else (threshold - mark) / mark
    result.update(
        risk_status="available",
        maintenance_margin=_s(maintenance),
        close_fee_reserve=_s(reserve),
        maintenance_required=_s(required),
        maintenance_buffer=_s(buffer),
        maintenance_coverage=_s(component / required),
        maintenance_breach=component <= required,
        liquidation_price=_s(threshold),
        liquidation_distance_pct=_s(distance * HUNDRED),
        tier=p.tier.tier,
    )
    return result


def _position_output(p, close_rate):
    output = {
        "inst_id": p.inst_id,
        "inst_type": p.inst_type,
        "base": p.base,
        "quantity": _s(p.quantity),
        "base_quantity": _s(p.base_quantity),
        "side": "long"
        if p.quantity is not None and p.quantity > ZERO
        else "short"
        if p.quantity is not None
        else None,
        "entry_price": _s(p.entry),
        "mark": _s(p.mark),
        "mark_ts": p.mark_ts,
        "tradable": p.tradable,
        "margin": _s(p.margin),
        "net_notional": _s(p.net_notional),
        "gross_notional": _s(p.net_notional.copy_abs()) if p.net_notional is not None else None,
        "equity_component": _s(p.component),
        "unrealized_pnl": _s(p.upnl),
        "unrealized_pnl_reason": "exact_spot_cost_basis_not_supplied"
        if p.inst_type == "SPOT" and p.basis is None
        else "current_mark_or_position_unavailable"
        if p.upnl is None
        else None,
        "exposure_status": "available" if p.net_notional is not None else "unavailable",
        "issues": p.issues,
    }
    output.update(_risk_fields())
    if p.component is not None:
        try:
            output.update(_risk(p, p.mark, p.component, close_rate))
        except (EngineError, DecimalException) as exc:
            p.fail("risk_accounting_domain", str(exc))
            output.update(risk_status="unavailable", maintenance_breach=None, bankrupt=None)
    else:
        output.update(risk_status="unavailable", maintenance_breach=None, bankrupt=None)
    return output


def _groups(positions, key, total_gross, issues):
    groups = {}
    for p in positions:
        group_key = getattr(p, key) or "unknown"
        groups.setdefault(group_key, []).append(p)
    output = []
    for name, members in sorted(groups.items()):
        net = _sum([p.net_notional for p in members], f"{key} net notional", issues)
        gross = _sum(
            [p.net_notional.copy_abs() if p.net_notional is not None else None for p in members],
            f"{key} gross notional",
            issues,
        )
        long = _sum(
            [max(p.net_notional, ZERO) if p.net_notional is not None else None for p in members],
            f"{key} long notional",
            issues,
        )
        short = _sum(
            [max(-p.net_notional, ZERO) if p.net_notional is not None else None for p in members],
            f"{key} short notional",
            issues,
        )
        share = (
            gross / total_gross * HUNDRED
            if gross is not None and total_gross is not None and total_gross > ZERO
            else None
        )
        output.append(
            {
                "inst_id" if key == "inst_id" else "asset": name,
                "position_count": len(members),
                "status": "available" if gross is not None else "unavailable",
                "long_notional": _s(long),
                "short_notional": _s(short),
                "gross_notional": _s(gross),
                "net_notional": _s(net),
                "gross_share_pct": _s(share),
                "net_base_quantity": _s(
                    _sum([p.base_quantity for p in members], "net base quantity", issues)
                ),
            }
        )
    return output


def _scenario(shock, positions, cash, debt, baseline, close_rate, slippage):
    issues, rows, components, post_components, releases, liabilities, fees = [], [], [], [], [], [], []
    risk_known = True
    triggers = bankruptcies = 0
    for p in positions:
        change, origin = shock.for_position(p.inst_id, p.base or "unknown")
        row = {
            "inst_id": p.inst_id,
            "base": p.base,
            "inst_type": p.inst_type,
            "shock_pct": _s(change),
            "shock_origin": origin,
            "current_mark": _s(p.mark),
            "shocked_mark": None,
            "equity_component": None,
            "pnl_change": None,
            "risk_status": "unavailable",
            "maintenance_breach": None,
            "bankrupt": None,
            "execution_price": None,
            "cash_release": None,
            "incremental_liability": None,
            "liquidation_fee": None,
            "post_equity_component": None,
        }
        row.update(_risk_fields())
        try:
            if p.mark is None or p.component is None:
                raise EngineError("Position cannot be shocked without valid current units and mark.")
            mark = _value(p.mark * (ONE + change / HUNDRED), "scenario mark")
            if mark <= ZERO:
                raise EngineError("Scenario mark rounds to zero.")
            pnl_change = _value(p.base_quantity * (mark - p.mark), "scenario pnl change")
            component = _value(p.component + pnl_change, "scenario equity component")
            components.append(component)
            row.update(shocked_mark=_s(mark), equity_component=_s(component), pnl_change=_s(pnl_change))
            risk = _risk(p, mark, component, close_rate)
            row.update(risk)
            if risk["bankrupt"] is None:
                bankruptcies = None
            elif risk["bankrupt"] and bankruptcies is not None:
                bankruptcies += 1
            if risk["maintenance_breach"] is None:
                risk_known = False
                post_components.append(None)
            elif risk["maintenance_breach"]:
                triggers += 1
                price = _fill_price(mark, "sell" if p.quantity > ZERO else "buy", p.contract, slippage)
                fee = _value((p.base_quantity * price).copy_abs() * close_rate, "liquidation fee")
                release = _value(p.margin + p.base_quantity * (price - p.entry) - fee, "liquidation release")
                cash_release, liability = max(release, ZERO), max(-release, ZERO)
                releases.append(cash_release)
                liabilities.append(liability)
                fees.append(fee)
                post_components.append(ZERO)
                row.update(
                    execution_price=_s(price),
                    cash_release=_s(cash_release),
                    incremental_liability=_s(liability),
                    liquidation_fee=_s(fee),
                    post_equity_component="0",
                )
            else:
                post_components.append(component)
                row.update(
                    cash_release="0",
                    incremental_liability="0",
                    liquidation_fee="0",
                    post_equity_component=_s(component),
                )
        except (EngineError, DecimalException) as exc:
            # Replace an already-appended component only if valuation itself failed; execution/risk errors leave MTM known.
            if row["equity_component"] is None:
                components.append(None)
            post_components.append(None)
            risk_known = False
            bankruptcies = None
            issues.append(_issue("scenario_unavailable", str(exc), p.inst_id, shock.name))
        if row["risk_status"] == "unavailable" and not any(
            item.get("inst_id") == p.inst_id for item in issues
        ):
            issues.append(
                _issue(
                    "scenario_risk_unavailable",
                    "Margin safety/full-liquidation settlement requires current tiers and reconciled funding.",
                    p.inst_id,
                    shock.name,
                )
            )
        rows.append(row)
    pre = _sum([cash, -debt if debt is not None else None, *components], "scenario equity", issues)
    difference = _sum([pre, -baseline if baseline is not None else None], "scenario equity change", issues)
    additional = _sum(liabilities, "incremental liability", issues) if risk_known else None
    fee_total = _sum(fees, "liquidation costs", issues) if risk_known else None
    post_cash = _sum([cash, *releases], "post-liquidation cash", issues) if risk_known else None
    post_debt = _sum([debt, additional], "post-liquidation liability", issues) if risk_known else None
    post = (
        _sum(
            [post_cash, -post_debt if post_debt is not None else None, *post_components],
            "post-liquidation equity",
            issues,
        )
        if risk_known
        else None
    )
    status = (
        "available"
        if pre is not None and post is not None and not issues
        else "partial"
        if pre is not None
        else "unavailable"
    )
    return {
        **shock.to_dict(),
        "status": status,
        "issues": issues,
        "positions": rows,
        "pre_liquidation_equity": _s(pre),
        "equity_change": _s(difference),
        "equity_change_pct": _s(difference / baseline * HUNDRED)
        if difference is not None and baseline > ZERO
        else None,
        "post_full_liquidation_equity": _s(post),
        "post_cash": _s(post_cash),
        "incremental_liability": _s(additional),
        "post_insurance_liability": _s(post_debt),
        "liquidation_costs": _s(fee_total),
        "liquidations": triggers if risk_known else None,
        "known_liquidations": triggers,
        "bankruptcies": bankruptcies,
    }


def analyze_portfolio(
    account: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    *,
    scenarios: Sequence[PriceShock | Mapping] | None = None,
    fee_bps: Decimal = Decimal(10),
    liquidation_fee_bps: Decimal = Decimal(50),
    slippage_bps: Decimal = ZERO,
    as_of_ms: int | None = None,
    max_age_ms: int = 15000,
    max_tier_age_ms: int = 86400000,
) -> dict:
    """Analyze an immutable capture, without reads, order submission, or account mutation.

    Money, quantity, percentages, and ratios are JSON decimal strings; unavailable
    computations are null. Real marks must be fresh at the explicitly captured
    as_of_ms; example snapshots retain their disclosed synthetic timestamps.
    """
    try:
        with localcontext(ACCOUNTING_CONTEXT):
            return _analyze(
                account,
                snapshots,
                scenarios,
                fee_bps,
                liquidation_fee_bps,
                slippage_bps,
                as_of_ms,
                max_age_ms,
                max_tier_age_ms,
            )
    except (EngineError, DecimalException, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PortfolioAnalyticsError):
            raise
        raise PortfolioAnalyticsError(
            f"Portfolio analysis request exceeds its supported domain: {exc}"
        ) from exc


def _analyze(
    account, snapshots, scenarios, fee_bps, liquidation_fee_bps, slippage_bps, as_of, max_age, max_tier_age
):
    if not isinstance(account, Mapping) or not isinstance(snapshots, Mapping):
        raise PortfolioAnalyticsError("account and snapshots must be mappings")
    source = account.get("source")
    if source not in {"example", "okx"}:
        raise PortfolioAnalyticsError("account source must be example or okx")
    as_of = _timestamp(int(time.time() * 1000) if as_of is None else as_of, "as_of_ms")
    for value, label in ((max_age, "max_age_ms"), (max_tier_age, "max_tier_age_ms")):
        if _timestamp(value, label) == 0:
            raise PortfolioAnalyticsError(f"{label} must be positive")
    rates = [
        _number(value, label, nonnegative=True)
        for value, label in (
            (fee_bps, "fee_bps"),
            (liquidation_fee_bps, "liquidation_fee_bps"),
            (slippage_bps, "slippage_bps"),
        )
    ]
    if any(value >= BPS for value in rates) or rates[0] + rates[1] >= BPS:
        raise PortfolioAnalyticsError("fee plus liquidation allowance and slippage must each be < 10000 bps")
    close_rate, slippage = (rates[0] + rates[1]) / BPS, rates[2] / BPS
    if scenarios is None:
        scenarios = DEFAULT_SCENARIOS
    if (
        not isinstance(scenarios, Sequence)
        or isinstance(scenarios, str)
        or not 1 <= len(scenarios) <= MAX_SCENARIOS
    ):
        raise PortfolioAnalyticsError("provide 1–25 price shock scenarios")
    shocks = [PriceShock(**shock) if isinstance(shock, Mapping) else shock for shock in scenarios]
    if any(not isinstance(shock, PriceShock) for shock in shocks):
        raise PortfolioAnalyticsError("scenarios must be PriceShock objects or scenario mappings")
    if len({shock.name for shock in shocks}) != len(shocks):
        raise PortfolioAnalyticsError("scenario names must be unique")
    raw_positions = account.get("positions")
    if not isinstance(raw_positions, list | tuple) or len(raw_positions) > MAX_POSITIONS:
        raise PortfolioAnalyticsError("account positions must be a sequence with at most 500 entries")
    issues = []
    cash_values = {}
    for key in ("cash", "reserved_cash", "insurance_debt"):
        try:
            cash_values[key] = _number(account.get(key), key, nonnegative=True)
        except (EngineError, DecimalException) as exc:
            cash_values[key] = None
            issues.append(_issue("invalid_account_balance", str(exc)))
    cash, reserved, debt = (cash_values[key] for key in ("cash", "reserved_cash", "insurance_debt"))
    positions = [
        _read_position(row, snapshots, source, as_of, max_age, max_tier_age) for row in raw_positions
    ]
    identifiers = [p.inst_id for p in positions]
    for p in positions:
        if identifiers.count(p.inst_id) > 1:
            p.mark = p.component = p.net_notional = None
            p.fail("duplicate_position", "A net position may occur only once per account and instrument.")
    position_rows = [_position_output(p, close_rate) for p in positions]
    for p in positions:
        issues.extend(p.issues)
    gross = _sum(
        [p.net_notional.copy_abs() if p.net_notional is not None else None for p in positions],
        "gross notional",
        issues,
    )
    net = _sum([p.net_notional for p in positions], "net notional", issues)
    spot = _sum([p.component for p in positions if p.inst_type == "SPOT"], "spot value", issues)
    perp = _sum([p.component for p in positions if p.inst_type == "SWAP"], "perpetual equity", issues)
    margin = _sum([p.margin for p in positions if p.inst_type == "SWAP"], "used margin", issues)
    if any(p.inst_type not in {"SPOT", "SWAP"} for p in positions):
        spot = perp = margin = None
    # Use the book's component ordering, retaining explicitly recorded insurance debt.
    equity = _sum(
        [cash, -debt if debt is not None else None, *[p.component for p in positions]],
        "portfolio equity",
        issues,
    )
    markets = _groups(positions, "inst_id", gross, issues)
    assets = _groups(positions, "base", gross, issues)
    hhi = (
        _sum(
            [(Decimal(asset["gross_notional"]) / gross) ** 2 for asset in assets],
            "gross concentration HHI",
            issues,
        )
        if gross is not None and gross > ZERO
        else None
    )
    largest = (
        max((Decimal(asset["gross_share_pct"]) for asset in assets), default=None)
        if gross is not None and gross > ZERO
        else None
    )
    summary = {
        "cash": _s(cash),
        "reserved_cash": _s(reserved),
        "available_cash": _s(cash - reserved) if cash is not None and reserved is not None else None,
        "insurance_liability": _s(debt),
        "used_margin": _s(margin),
        "spot_value": _s(spot),
        "perp_equity": _s(perp),
        "equity": _s(equity),
        "gross_notional": _s(gross),
        "net_notional": _s(net),
        "gross_leverage": _s(gross / equity)
        if gross is not None and equity is not None and equity > ZERO
        else None,
        "net_leverage": _s(net / equity)
        if net is not None and equity is not None and equity > ZERO
        else None,
        "leverage_reason": None
        if equity is not None and equity > ZERO
        else "positive_complete_equity_required",
        "concentration_hhi": _s(hhi),
        "largest_asset_share_pct": _s(largest),
        "concentration_reason": None if hhi is not None else "complete_positive_gross_exposure_required",
        "maintenance_margin": _s(
            _sum(
                [
                    Decimal(row["maintenance_margin"]) if row.get("maintenance_margin") is not None else None
                    for row in position_rows
                ],
                "total maintenance",
                issues,
            )
        ),
        "position_count": len(positions),
        "priced_position_count": sum(p.mark is not None for p in positions),
        "reported_equity": account.get("equity"),
        "reported_valuation_status": account.get("valuation_status"),
    }
    try:
        reported = (
            _number(account.get("equity"), "reported equity") if account.get("equity") is not None else None
        )
    except EngineError:
        reported = None
    summary["reported_equity"] = _s(reported)
    summary["equity_reconciliation_residual"] = _s(
        _sum([reported, -equity if equity is not None else None], "equity reconciliation residual", issues)
    )
    summary["equity_reconciliation"] = (
        "matched"
        if reported is not None and reported == equity
        else "different_capture_or_rounding"
        if reported is not None and equity is not None
        else "unavailable"
    )
    scenario_rows = [
        _scenario(shock, positions, cash, debt, equity, close_rate, slippage) for shock in shocks
    ]
    status = (
        "available"
        if equity is not None
        and gross is not None
        and not issues
        and all(row["status"] == "available" for row in scenario_rows)
        else "partial"
        if equity is not None or any(p.mark is not None for p in positions)
        else "unavailable"
    )
    # Capture only snapshots relevant to this account. No later network refresh participates in a replay.
    capture = _json_safe(
        {
            "account": account,
            "snapshots": {key: snapshots[key] for key in sorted(set(identifiers)) if key in snapshots},
            "scenarios": [shock.to_dict() for shock in shocks],
            "fee_bps": _s(rates[0]),
            "liquidation_fee_bps": _s(rates[1]),
            "slippage_bps": _s(rates[2]),
            "as_of_ms": as_of,
            "max_age_ms": max_age,
            "max_tier_age_ms": max_tier_age,
            "model_version": MODEL_VERSION,
        }
    )
    digest = hashlib.sha256(
        json.dumps(
            capture, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "source": source,
        "as_of": as_of,
        "status": status,
        "issues": issues,
        "summary": summary,
        "markets": markets,
        "assets": assets,
        "positions": position_rows,
        "scenarios": scenario_rows,
        "captured_scenario": {
            "id": digest[:16],
            "input_hash": digest,
            "as_of": as_of,
            "source": source,
            "model_version": MODEL_VERSION,
        },
        "input_snapshot": capture,
        "assumptions": {
            "model": "Deterministic parallel mark-price shocks; not historical VaR, expected loss, probability, or an exchange liquidation prediction.",
            "shock_precedence": "market_pct overrides asset_pct overrides parallel_pct; percentage changes are not added",
            "equity": "cash + spot mark value + sum(isolated margin + signed mark PnL) - recorded insurance liability; reservations reduce available cash only",
            "maintenance": "current tier notional × mmr plus normal trading fee and additional liquidation allowance; fixed quantity and current tiers",
            "tier_boundary": "min contracts exclusive, max inclusive; model convention, not an established OKX equality rule",
            "settlement": "Only breached isolated perpetual positions fully close, adverse slip and tick rounding at shocked mark, fees charged; spot and surviving positions remain marked. This hypothetical settlement does not establish venue tradability, even for suspended instruments",
            "insurance_liability": "Negative isolated settlement creates explicit additional liability and preserves free cash; liabilities reduce portfolio equity",
            "funding": "No hypothetical future funding. A known due unreconciled event makes margin safety/full-liquidation settlement unavailable; price-only MTM remains conditional on the captured ledger",
            "unknown_funding": "Absence of a known due event is not proof that every historical funding debit was reconciled; this is a captured-book analysis",
            "spot_unrealized_pnl": "Requires exact stored basis; rounded average entry or cached mark PnL cannot reconstruct it. Missing basis does not prevent exposure, equity or price-shock valuation",
            "liquidation_fee_bps": _s(rates[1]),
            "fee_bps": _s(rates[0]),
            "slippage_bps": _s(rates[2]),
            "max_mark_age_ms": max_age,
            "max_tier_age_ms": max_tier_age,
            "future_tolerance_ms": 5000,
            "example": "Synthetic source timestamps need not match wall-clock time; real-source freshness remains mandatory",
            "accounting": "Independent 50-significant-digit half-even Decimal context, finite accounting values with absolute value < 1e30; input adjusted exponent >= -80",
            "exclusions": "No cross margin, portfolio margin, order-book depth, partial liquidation ladder, insurance-fund/ADL execution, future funding, pending-order fills, correlations, or historical probabilities",
        },
    }


def replay_portfolio_snapshot(capture: Mapping[str, Any]) -> dict:
    """Replay the exact captured inputs and valuation instant; reject other model versions."""
    if not isinstance(capture, Mapping) or capture.get("model_version") != MODEL_VERSION:
        raise PortfolioAnalyticsError("unsupported portfolio snapshot model version")
    return analyze_portfolio(
        capture["account"],
        capture["snapshots"],
        scenarios=capture["scenarios"],
        fee_bps=capture["fee_bps"],
        liquidation_fee_bps=capture["liquidation_fee_bps"],
        slippage_bps=capture["slippage_bps"],
        as_of_ms=capture["as_of_ms"],
        max_age_ms=capture["max_age_ms"],
        max_tier_age_ms=capture["max_tier_age_ms"],
    )
