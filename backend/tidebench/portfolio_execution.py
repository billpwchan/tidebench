"""Versioned failure contract for historical and managed local-paper batches.

The transaction book remains the authority for fills and account admission. These
functions define the same all-leg preflight, residual and reduce-group semantics
without hiding already committed fills or returning their costs to the account.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from .account_capital import capital_cash_budget
from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .portfolio_targets import addition_plan, reduction_plan
from .pro_execution import base_size, number
from .store import encode

EXECUTION_CONTRACT = "reduce_group_v1"
LEGACY_EXECUTION_CONTRACT = "legacy_per_leg_v0"
D = Decimal


def split_addition(quantity, quote, policy):
    """Balanced lot-aligned children within the captured per-order notional limit."""
    with localcontext(ACCOUNTING_CONTEXT):
        quantity = number(quantity)
        meta, slip = quote["instrument"], number(policy["slippage_bps"]) / 10000
        lot = number(meta["lot_size"])
        if lot <= 0 or not quantity or abs(quantity) % lot:
            raise PlatformError(
                "portfolio_child_quantity", "A nonzero lot-aligned leg quantity is required.", 422
            )
        price = number(quote["ask"] if quantity > 0 else quote["bid"]) * (
            1 + slip * (1 if quantity > 0 else -1)
        )
        tick = number(meta["tick_size"])
        price = (price / tick).to_integral_value(
            rounding=ROUND_CEILING if quantity > 0 else ROUND_FLOOR
        ) * tick
        if price <= 0:
            raise PlatformError(
                "portfolio_quote_invalid", "Execution splitting needs positive rounded prices.", 422
            )
        maximum = int(
            (number(policy["max_order_notional"]) / price / base_size(meta) / lot).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        minimum = int((number(meta["min_size"]) / lot).to_integral_value(rounding=ROUND_CEILING))
        units = int(abs(quantity) / lot)
        children = (units + maximum - 1) // maximum if maximum > 0 else 0
        if not children or children > 20 or units < children * minimum:
            raise PlatformError(
                "portfolio_child_budget",
                "The leg cannot be split into at most twenty valid orders under the account order limit.",
                409,
            )
        quotient, remainder = divmod(units, children)
        return [(quotient + (i < remainder)) * lot * (1 if quantity > 0 else -1) for i in range(children)]


def portfolio_commands(phase, quantities, quotes=None, policy=None):
    """Same deterministic round-robin child sequence in historical and managed books."""
    if phase not in {"add", "reduce", "compensate"}:
        raise PlatformError("portfolio_execution_phase", "Unsupported portfolio execution phase.", 422)
    children = {
        s: split_addition(number(q), quotes[s], policy) if phase == "add" else [number(q)]
        for s, q in sorted(quantities.items())
        if number(q)
    }
    return [
        {"sequence": sequence, "inst_id": symbol, "quantity": quantity}
        for sequence, (symbol, quantity) in enumerate(
            (symbol, rows[index])
            for index in range(max((len(v) for v in children.values()), default=0))
            for symbol, rows in children.items()
            if index < len(rows)
        )
    ]


def require_addition_legs(skipped):
    """Undersized maintenance is allowed; an unenterable new leg fails the group."""
    if any(row["code"] not in {"rebalance_minimum", "rebalance_cash_rounding"} for row in skipped):
        raise PlatformError(
            "portfolio_minimum_leg",
            "Common cash scaling leaves a portfolio leg below minimum size.",
            409,
        )


def portfolio_residuals(targets, positions, quotes, capital):
    """Residual is gross absolute target error, measured against allocated capital."""
    with localcontext(ACCOUNTING_CONTEXT):
        residuals = {s: number(q) - number(positions.get(s, 0)) for s, q in sorted(targets.items())}
        amount = sum(
            (
                abs(q) * base_size(quotes[s]["instrument"]) * number(quotes[s]["last"])
                for s, q in residuals.items()
            ),
            D(0),
        )
        capital = number(capital)
        pct = amount / capital * 100 if capital > 0 else D(0) if not amount else D(100)
        return encode({"quantities": residuals, "notional": amount, "capital_pct": pct})


def require_residual_limit(residuals, definition):
    if number(residuals["capital_pct"]) > number(definition["max_residual_pct"]):
        raise PlatformError(
            "portfolio_residual_limit",
            "Executed portfolio differs from its target beyond the reviewed residual limit.",
            409,
        )


def compensation_quantities(targets, positions):
    """Flatten the failed group's entire declared inventory, not only its new fills."""
    return {
        s: number(positions[s]).copy_negate()
        for s in sorted(targets)
        if s in positions and number(positions[s])
    }


def require_compensation_flat(targets, positions):
    if compensation_quantities(targets, positions):
        raise PlatformError(
            "portfolio_compensation_residual",
            "Compensation retains inventory; operator action is required.",
            409,
        )


def execution_trace(phase, status, **evidence):
    """Economic trace omits installation time, command UUIDs and controller IDs."""
    return encode({"contract": EXECUTION_CONTRACT, "phase": phase, "status": status, **evidence})


def command_trace(phase, symbol, quantity, *, order=None, error=None):
    fields = {"inst_id": symbol, "signed_quantity": str(number(quantity))}
    if error is not None:
        return execution_trace(
            phase,
            "failed" if error.code == "portfolio_execution_failure" else "rejected",
            **fields,
            code=error.code,
            message=error.message,
        )
    # These are the transactional book's actual fill fields, not requested prices.
    fill = {k: order[k] for k in ("quantity", "price", "fee") if order is not None and k in order}
    return execution_trace(phase, "filled", **fields, fill=fill)


def resume_compensation(batch, quotes, positions, submit):
    """Retry frozen close commands, replanning only if protective activity changed inventory.

    `submit(symbol, signed_quantity, reduce_only, key)` must return the committed
    order or raise. A retry retains the same command key and quantity. A changed
    inventory generates an explicit superseded trace and a new immutable key.
    """
    if batch["status"] == "compensated":
        return batch
    with localcontext(ACCOUNTING_CONTEXT):
        return _resume_compensation(batch, quotes, positions, submit)


def _execution_error(exc):
    return (
        exc
        if isinstance(exc, PlatformError)
        else PlatformError("portfolio_execution_failure", "Execution failed: " + str(exc)[:900], 409)
    )


def _resume_compensation(batch, quotes, positions, submit):
    batch["compensation_attempts"] = batch.get("compensation_attempts", 0) + 1
    commands = batch.setdefault(
        "compensation_commands",
        [
            {"symbol": s, "quantity": str(q), "sequence": i, "status": "pending"}
            for i, (s, q) in enumerate(compensation_quantities(batch["targets"], positions()).items())
        ],
    )
    try:
        for command in commands:
            if command["status"] != "pending":
                continue
            symbol, quantity = command["symbol"], number(command["quantity"])
            remaining = number(positions().get(symbol, 0))
            if not remaining or remaining * quantity >= 0 or abs(quantity) > abs(remaining):
                command["status"] = "superseded"
                batch["trace"].append(
                    execution_trace(
                        "compensate",
                        "superseded",
                        inst_id=symbol,
                        signed_quantity=str(quantity),
                        remaining_quantity=str(remaining),
                    )
                )
                if remaining:
                    commands.append(
                        {
                            "symbol": symbol,
                            "quantity": str(-remaining),
                            "sequence": len(commands),
                            "status": "pending",
                        }
                    )
                continue
            key = f"{batch['key']}:compensate:{command['sequence']}:{symbol}"
            try:
                order = submit(symbol, quantity, True, key)
            except Exception as caught:
                exc = _execution_error(caught)
                batch["trace"].append(command_trace("compensate", symbol, quantity, error=exc))
                raise
            command["status"] = "completed"
            batch["trace"].append(command_trace("compensate", symbol, quantity, order=order))
        require_compensation_flat(batch["targets"], positions())
        batch["status"] = "compensated"
        batch["trace"].append(execution_trace("compensate", "completed", positions=positions()))
        batch.pop("compensation_error", None)
    except Exception as caught:
        exc = _execution_error(caught)
        batch["status"] = "compensating"
        batch["compensation_error"] = {"code": exc.code, "message": exc.message}
        batch["trace"].append(execution_trace("compensate", "blocked", code=exc.code, positions=positions()))
    batch["remaining_inventory"] = {
        s: str(q) for s, q in positions().items() if s in batch["targets"] and number(q)
    }
    batch["residuals"] = portfolio_residuals(batch["targets"], positions(), quotes, batch["capital"])
    return batch


def execute_batch(targets, capital, quotes, config, leverage, positions, account, submit, key):
    """Synchronous historical adapter for the same persisted managed phase contract.

    A failed batch stops additions, closes declared inventory through real book
    transactions and becomes compensated or remains compensating. The research
    caller must stop new decisions and retry outstanding compensation at later
    executable quotes. Financial output remains available even after failure.
    """
    if (
        config.get("execution_contract") != EXECUTION_CONTRACT
        or config.get("failure_policy") != "reduce_group"
    ):
        raise PlatformError("portfolio_execution_contract", "Unsupported portfolio execution contract.", 422)
    with localcontext(ACCOUNTING_CONTEXT):
        batch = {
            "contract": EXECUTION_CONTRACT,
            "key": key,
            "status": "reducing",
            "targets": encode(targets),
            "capital": str(number(capital)),
            "trace": [],
            "cash_scale": "1",
            "skipped": [],
        }
        phase = "reduce"
        try:
            reductions = reduction_plan(targets, positions(), quotes)
            batch["skipped"].extend(reductions.skipped)
            for symbol, quantity in reductions.quantities.items():
                try:
                    order = submit(symbol, quantity, True, f"{key}:reduce:{symbol}")
                except Exception as caught:
                    exc = _execution_error(caught)
                    batch["trace"].append(command_trace(phase, symbol, quantity, error=exc))
                    raise
                batch["trace"].append(command_trace(phase, symbol, quantity, order=order))
            phase = "prepare_additions"
            current = account()
            owned_capital = sum(
                (
                    number(p["market_value"] if p["inst_type"] == "SPOT" else p["margin"])
                    for p in current["positions"]
                    if p["inst_id"] in targets
                ),
                D(0),
            )
            batch["capital_budget"] = capital_cash_budget(
                current, config.get("capital_pct", 100), owned_capital
            )
            plan = addition_plan(
                targets,
                positions(),
                quotes,
                batch["capital_budget"]["budget_cash"],
                leverage,
                config["fee_bps"],
                config["slippage_bps"],
            )
            batch["skipped"].extend(plan.skipped)
            batch["cash_scale"] = str(plan.cash_scale)
            batch["trace"].append(
                execution_trace(
                    phase,
                    "frozen",
                    quantities=plan.quantities,
                    requested=plan.requested,
                    cash_scale=plan.cash_scale,
                    required_cash=plan.required_cash,
                    skipped=plan.skipped,
                    capital_budget=batch["capital_budget"],
                )
            )
            require_addition_legs(plan.skipped)
            child_commands = portfolio_commands("add", plan.quantities, quotes, config)
            batch["trace"][-1]["child_commands"] = encode(child_commands)
            phase = "add"
            for command in child_commands:
                symbol, quantity = command["inst_id"], command["quantity"]
                try:
                    order = submit(symbol, quantity, False, f"{key}:add:{command['sequence']}:{symbol}")
                except Exception as caught:
                    exc = _execution_error(caught)
                    batch["trace"].append(command_trace(phase, symbol, quantity, error=exc))
                    raise
                batch["trace"].append(command_trace(phase, symbol, quantity, order=order))
            phase = "residual"
            batch["residuals"] = portfolio_residuals(targets, positions(), quotes, capital)
            batch["trace"].append(execution_trace(phase, "observed", **batch["residuals"]))
            require_residual_limit(batch["residuals"], config)
            batch["status"] = "completed"
            return batch
        except Exception as caught:
            exc = _execution_error(caught)
            batch["failure"] = {"phase": phase, "code": exc.code, "message": exc.message}
            batch["status"] = "compensating"
            batch["trace"].append(
                execution_trace(
                    phase,
                    "failed",
                    code=exc.code,
                    message=exc.message,
                    positions=positions(),
                    cash=account()["cash"],
                    fees_paid=account()["fees_paid"],
                )
            )
            return resume_compensation(batch, quotes, positions, submit)
