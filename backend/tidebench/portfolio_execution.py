"""Versioned failure contract for historical and managed local-paper batches.

The transaction book remains the authority for fills and account admission. These
functions define the same all-leg preflight, residual and reduce-group semantics
without hiding already committed fills or returning their costs to the account.
"""

import json
from contextvars import ContextVar
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from .account_capital import capital_cash_budget
from .engine import ACCOUNTING_CONTEXT
from .platform import PlatformError
from .portfolio_targets import addition_plan, reduction_plan
from .pro_execution import base_size, number
from .store import encode
from .strategy_registry import digest

FROZEN_EXECUTION_CONTRACT = "reduce_group_v1"
EXECUTION_CONTRACT = "reduce_group_v2_allowance"
SUPPORTED_EXECUTION_CONTRACTS = {FROZEN_EXECUTION_CONTRACT, EXECUTION_CONTRACT}
_TRACE_CONTRACT = ContextVar("portfolio_execution_contract", default=EXECUTION_CONTRACT)
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
    return encode({"contract": _TRACE_CONTRACT.get(), "phase": phase, "status": status, **evidence})


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
    token = _TRACE_CONTRACT.set(batch.get("contract", EXECUTION_CONTRACT))
    try:
        return _resume_compensation_context(batch, quotes, positions, submit)
    finally:
        _TRACE_CONTRACT.reset(token)


def _resume_compensation_context(batch, quotes, positions, submit):
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


def checked_allowance_lineage(batch, commands, events, *, source=None):
    with localcontext(ACCOUNTING_CONTEXT):
        return _checked_allowance_lineage(batch, commands, events, source=source)


def _checked_allowance_lineage(batch, commands, events, *, source=None):
    """Verify v2 plan lineage against actual immutable command indexes/payloads.

    Missing audit evidence never restores a superseded command or resets the
    three-attempt bound. This pure check is also suitable for restore validation.
    """
    try:
        body = json.loads(batch["body"]) if isinstance(batch["body"], str) else batch["body"]
        if digest(body) != batch["content_hash"] or any(
            body[k] != batch[k] for k in ("id", "group_id", "bar")
        ):
            raise ValueError("Frozen batch identity mismatch")
        original_hash = batch["additions_hash"]
        parent_hash, plans = original_hash, []
        additions = (
            json.loads(batch["additions"]) if isinstance(batch["additions"], str) else batch["additions"]
        )
        if additions is not None and digest(additions) != original_hash:
            raise ValueError("Original additions hash mismatch")
        indexed = {row["id"]: row for row in commands if row["phase"] == "add"}
        superseded, replacements = set(), set()
        for event in events:
            details = json.loads(event["details"]) if isinstance(event["details"], str) else event["details"]
            plan, identity = details["plan"], details["content_hash"]
            if (
                len(plans) >= 3
                or digest(plan) != identity
                or plan["contract"] != EXECUTION_CONTRACT
                or plan["attempt"] != len(plans) + 1
                or plan["group_id"] != batch["group_id"]
                or plan["batch_id"] != batch["id"]
                or details["group_id"] != batch["group_id"]
                or details["batch_id"] != batch["id"]
                or plan["parent_plan_hash"] != parent_hash
                or plan["original_additions_hash"] != original_hash
                or source is not None
                and event["source"] != source
            ):
                raise ValueError("Plan identity or lineage mismatch")
            totals = {"superseded_commands": {}, "replacement_commands": {}}
            for field, seen in (("superseded_commands", superseded), ("replacement_commands", replacements)):
                for reference in plan[field]:
                    row = indexed[reference["id"]]
                    payload = (
                        json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                    )
                    if (
                        row["id"] in seen
                        or reference["key"] != row["key"]
                        or reference["payload_hash"] != row["payload_hash"]
                        or digest(payload) != row["payload_hash"]
                        or row["batch_id"] != batch["id"]
                        or row["key"] != f"portfolio:{batch['id']}:add:{row['sequence']}:{batch['bar']}"
                        or source is not None
                        and payload["source"] != source
                    ):
                        raise ValueError("Command reference mismatch")
                    if field == "superseded_commands" and (
                        row["status"] != "superseded" or row["order_id"] is not None
                    ):
                        raise ValueError("Supersession contains a committed command")
                    seen.add(row["id"])
                    symbol = payload["inst_id"]
                    signed = number(payload["quantity"]) * (1 if payload["side"] == "buy" else -1)
                    totals[field][symbol] = totals[field].get(symbol, D(0)) + signed
            if totals["superseded_commands"] != {
                symbol: number(q) for symbol, q in plan["remaining_original_quantities"].items()
            }:
                raise ValueError("Remaining original quantity differs from commands")
            if totals["replacement_commands"] != {
                symbol: number(q) for symbol, q in plan["quantities"].items()
            }:
                raise ValueError("Replacement quantity differs from plan")
            remaining, replacement = totals["superseded_commands"], totals["replacement_commands"]
            if (
                not remaining
                or any(
                    symbol not in remaining
                    or quantity * remaining[symbol] <= 0
                    or abs(quantity) > abs(remaining[symbol])
                    for symbol, quantity in replacement.items()
                )
                or not any(abs(replacement.get(s, D(0))) < abs(q) for s, q in remaining.items())
            ):
                raise ValueError("Replacement must strictly shrink the remaining original plan")
            for reference in plan["replacement_commands"]:
                row = indexed[reference["id"]]
                payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                meta = plan["quotes"][payload["inst_id"]]["instrument"]
                quantity, lot, minimum = (
                    number(payload["quantity"]),
                    number(meta["lot_size"]),
                    number(meta["min_size"]),
                )
                if (
                    payload["side"] not in {"buy", "sell"}
                    or quantity <= 0
                    or lot <= 0
                    or minimum <= 0
                    or quantity < minimum
                    or quantity % lot
                ):
                    raise ValueError("Replacement violates its captured child quantity rules")
            require_addition_legs(plan["skipped"])
            previous_budget = plans[-1]["capital_budget"] if plans else additions["capital_budget"]
            fresh_budget = plan["capital_budget"]
            if (
                plan["policy_hash"] != additions["policy_hash"]
                or not plan["capital_policy_hash"]
                or plan["capital_policy_hash"] != additions["capital_policy_hash"]
                or digest(plan["previous_capital_budget"]) != digest(previous_budget)
                or number(fresh_budget["commitment_pct"]) != number(previous_budget["commitment_pct"])
                or number(fresh_budget["remaining_capital_including_entry_costs"]) < 0
                or number(fresh_budget["remaining_capital_including_entry_costs"])
                >= number(previous_budget["remaining_capital_including_entry_costs"])
                or number(fresh_budget["account_equity"]) <= 0
                or number(fresh_budget["account_equity"]) >= number(previous_budget["account_equity"])
            ):
                raise ValueError("Allowance or policy differs from the original captured chain")
            sequence = [indexed[ref["id"]]["sequence"] for ref in plan["replacement_commands"]]
            if sequence != list(
                range(
                    plan["replacement_sequence_offset"], plan["replacement_sequence_offset"] + len(sequence)
                )
            ):
                raise ValueError("Replacement sequence differs from plan")
            parent_hash = identity
            plans.append(plan | {"content_hash": identity})
        if {row["id"] for row in indexed.values() if row["status"] == "superseded"} != superseded:
            raise ValueError("Superseded additions have missing plan evidence")
        if plans:
            first_offset = plans[0]["replacement_sequence_offset"]
            if {row["id"] for row in indexed.values() if row["sequence"] >= first_offset} != replacements:
                raise ValueError("Replacement additions have missing plan evidence")
        original_quantities = {}
        for row in indexed.values():
            if row["id"] in replacements:
                continue
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            if digest(payload) != row["payload_hash"]:
                raise ValueError("Original command hash mismatch")
            symbol = payload["inst_id"]
            signed = number(payload["quantity"]) * (1 if payload["side"] == "buy" else -1)
            original_quantities[symbol] = original_quantities.get(symbol, D(0)) + signed
        if original_quantities != {s: number(q) for s, q in (additions or {}).get("quantities", {}).items()}:
            raise ValueError("Unexplained additions differ from original frozen plan")
        return plans
    except (KeyError, TypeError, ValueError, ArithmeticError, PlatformError) as exc:
        raise PlatformError(
            "portfolio_evidence_integrity", "Allowance plan lineage failed its immutable command check.", 409
        ) from exc


def allowance_replan(
    targets,
    capital,
    positions,
    quotes,
    remaining,
    previous_budget,
    fresh_budget,
    leverage,
    policy,
    previous_policy_hash,
    capital_policy_hash,
    previous_capital_policy_hash,
    attempts,
    max_residual_pct=None,
):
    """V2 only: preserve target identity and shrink the still-unfilled plan.

    This never authorizes a fill. Complete evidence, unchanged policy and a
    strictly lower fresh allowance are necessary; every replacement still
    passes the transaction book, original minimum and original residual rules.
    """
    with localcontext(ACCOUNTING_CONTEXT):
        if (
            attempts >= 3
            or not capital_policy_hash
            or capital_policy_hash != previous_capital_policy_hash
            or digest(policy) != previous_policy_hash
            or number(fresh_budget["commitment_pct"]) != number(previous_budget["commitment_pct"])
            or number(fresh_budget["remaining_capital_including_entry_costs"])
            >= number(previous_budget["remaining_capital_including_entry_costs"])
            or number(fresh_budget["account_equity"]) >= number(previous_budget["account_equity"])
        ):
            return None
        allowed_targets = {}
        for symbol, raw in remaining.items():
            quantity = number(raw)
            old, target = number(positions.get(symbol, 0)), number(targets[symbol])
            gap = target - old
            if not quantity or gap * quantity <= 0 or old and old * quantity < 0:
                return None
            allowed_targets[symbol] = old + min(abs(quantity), abs(gap)) * (1 if quantity > 0 else -1)
        plan = addition_plan(
            allowed_targets,
            positions,
            quotes,
            fresh_budget["budget_cash"],
            leverage,
            policy["fee_bps"],
            policy["slippage_bps"],
        )
        require_addition_legs(plan.skipped)
        if any(abs(number(q)) > abs(number(remaining[s])) for s, q in plan.quantities.items()):
            return None
        if not any(abs(number(plan.quantities.get(s, 0))) < abs(number(q)) for s, q in remaining.items()):
            return None
        projected = {s: number(q) for s, q in positions.items()}
        for symbol, quantity in plan.quantities.items():
            projected[symbol] = projected.get(symbol, D(0)) + quantity
        residuals = portfolio_residuals(targets, projected, quotes, capital)
        require_residual_limit(
            residuals,
            {
                "max_residual_pct": max_residual_pct
                if max_residual_pct is not None
                else policy["max_residual_pct"]
            },
        )
        return encode(
            {
                "contract": EXECUTION_CONTRACT,
                "attempt": attempts + 1,
                "remaining_original_quantities": remaining,
                "quantities": plan.quantities,
                "requested": plan.requested,
                "cash_scale": plan.cash_scale,
                "required_cash": plan.required_cash,
                "skipped": plan.skipped,
                "previous_capital_budget": previous_budget,
                "capital_budget": fresh_budget,
                "policy_hash": digest(policy),
                "capital_policy_hash": capital_policy_hash,
                "projected_target_residuals": residuals,
                "quotes": {s: quotes[s] for s in remaining},
            }
        )


def execute_batch(*args, **kwargs):
    config = args[3] if len(args) > 3 else kwargs["config"]
    token = _TRACE_CONTRACT.set(config.get("execution_contract", EXECUTION_CONTRACT))
    try:
        return _execute_batch(*args, **kwargs)
    finally:
        _TRACE_CONTRACT.reset(token)


def _execute_batch(
    targets, capital, quotes, config, leverage, positions, account, submit, key, policy_state=None
):
    """Synchronous historical adapter for the same persisted managed phase contract.

    A failed batch stops additions, closes declared inventory through real book
    transactions and becomes compensated or remains compensating. The research
    caller must stop new decisions and retry outstanding compensation at later
    executable quotes. Financial output remains available even after failure.
    """
    if (
        config.get("execution_contract") not in SUPPORTED_EXECUTION_CONTRACTS
        or config.get("failure_policy") != "reduce_group"
    ):
        raise PlatformError("portfolio_execution_contract", "Unsupported portfolio execution contract.", 422)
    with localcontext(ACCOUNTING_CONTEXT):
        batch = {
            "contract": config["execution_contract"],
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
            identity = (
                policy_state() if policy_state else {"risk": config, "capital": {"scenario": "captured"}}
            )
            policy_hash, capital_hash = digest(identity["risk"]), digest(identity["capital"])
            previous_budget = batch["capital_budget"]
            batch["allowance_replans"] = []
            for command in child_commands:
                if command.get("status") == "superseded":
                    continue
                symbol, quantity = command["inst_id"], command["quantity"]
                try:
                    order = submit(symbol, quantity, False, f"{key}:add:{command['sequence']}:{symbol}")
                except Exception as caught:
                    exc = _execution_error(caught)
                    batch["trace"].append(command_trace(phase, symbol, quantity, error=exc))
                    if (
                        exc.code != "portfolio_capital_limit"
                        or config["execution_contract"] != EXECUTION_CONTRACT
                    ):
                        raise
                    remaining = {}
                    for pending in child_commands:
                        if pending.get("status", "pending") == "pending":
                            remaining[pending["inst_id"]] = (
                                remaining.get(pending["inst_id"], D(0)) + pending["quantity"]
                            )
                    current = account()
                    owned = sum(
                        (
                            number(p["market_value"] if p["inst_type"] == "SPOT" else p["margin"])
                            for p in current["positions"]
                            if p["inst_id"] in targets
                        ),
                        D(0),
                    )
                    fresh_budget = capital_cash_budget(current, config.get("capital_pct", 100), owned)
                    identity = (
                        policy_state()
                        if policy_state
                        else {"risk": config, "capital": {"scenario": "captured"}}
                    )
                    revised = (
                        allowance_replan(
                            targets,
                            capital,
                            positions(),
                            quotes,
                            remaining,
                            previous_budget,
                            fresh_budget,
                            leverage,
                            config,
                            digest(config),
                            digest(identity["capital"]),
                            capital_hash,
                            len(batch["allowance_replans"]),
                        )
                        if digest(identity["risk"]) == policy_hash
                        else None
                    )
                    if revised is None:
                        raise
                    superseded = [row for row in child_commands if row.get("status", "pending") == "pending"]
                    for row in superseded:
                        row["status"] = "superseded"
                    replacement = portfolio_commands("add", revised["quantities"], quotes, config)
                    for market in remaining:
                        if (
                            sum(
                                c["inst_id"] == market and c.get("status") == "completed"
                                for c in child_commands
                            )
                            + sum(c["inst_id"] == market for c in replacement)
                            > 20
                        ):
                            raise exc from caught
                    offset = max(c["sequence"] for c in child_commands) + 1
                    for row in replacement:
                        row["sequence"] += offset
                    revised["superseded_sequences"] = [row["sequence"] for row in superseded]
                    revised["child_commands"] = encode(replacement)
                    revised["content_hash"] = digest(revised)
                    batch["allowance_replans"].append(revised)
                    batch["trace"].append(execution_trace("add", "allowance_superseded", **revised))
                    previous_budget = revised["capital_budget"]
                    child_commands.extend(replacement)
                    continue
                command["status"] = "completed"
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
