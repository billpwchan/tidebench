"""Attributed, bounded historical eligibility and actual lifecycle accounting.

Hashes prove captured bytes, not the truth of a submitter's attribution. The
explicit selected universe is not a comprehensive venue membership history.
Only same-identity linear-contract unit changes are executable conversions.
"""

import hashlib
import json
from decimal import Decimal, localcontext
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .engine import _floor_lot
from .platform import PlatformError
from .pro_execution import _accounted, _cash_journal, _exact_sum, base_size, number, tier_for
from .schemas import InputModel
from .store import dumps, encode
from .strategy_registry import digest

D = Decimal
PARSER_VERSION = "attributed_lifecycle_v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_lifecycle_events(event_hash TEXT PRIMARY KEY,source TEXT NOT NULL,inst_id TEXT NOT NULL,body TEXT NOT NULL,source_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS historical_lifecycle_applications(source TEXT NOT NULL,event_hash TEXT NOT NULL,body TEXT NOT NULL,applied_at INTEGER NOT NULL,PRIMARY KEY(source,event_hash));
CREATE TRIGGER IF NOT EXISTS historical_lifecycle_events_no_update BEFORE UPDATE ON historical_lifecycle_events BEGIN SELECT RAISE(ABORT,'immutable lifecycle event'); END;
CREATE TRIGGER IF NOT EXISTS historical_lifecycle_events_no_delete BEFORE DELETE ON historical_lifecycle_events BEGIN SELECT RAISE(ABORT,'immutable lifecycle event'); END;
CREATE TRIGGER IF NOT EXISTS historical_lifecycle_applications_no_update BEFORE UPDATE ON historical_lifecycle_applications BEGIN SELECT RAISE(ABORT,'immutable lifecycle application'); END;
CREATE TRIGGER IF NOT EXISTS historical_lifecycle_applications_no_delete BEFORE DELETE ON historical_lifecycle_applications BEGIN SELECT RAISE(ABORT,'immutable lifecycle application'); END;
"""


class LifecycleSource(InputModel):
    url: str = Field(min_length=12, max_length=2000)
    published_at: int = Field(ge=1577836800000)
    captured_at: int = Field(ge=1577836800000)
    raw_content: str = Field(min_length=12, max_length=32000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_type: Literal[
        "announcement_extract", "instrument_response", "settlement_record", "synthetic_fixture"
    ]

    @model_validator(mode="after")
    def captured_source(self):
        url = urlsplit(self.url)
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            raise ValueError("Lifecycle sources require an uncredentialed HTTPS source URL.")
        if self.evidence_type != "synthetic_fixture" and url.hostname not in {"okx.com", "www.okx.com"}:
            raise ValueError("Venue-attributed lifecycle evidence must use an official OKX source URL.")
        if hashlib.sha256(self.raw_content.encode()).hexdigest() != self.content_hash:
            raise ValueError("Lifecycle source bytes do not match the declared SHA-256.")
        if self.published_at > self.captured_at:
            raise ValueError("The source cannot be captured before its publication.")
        return self


class LifecycleEvent(InputModel):
    inst_id: str = Field(pattern=r"^[A-Z0-9]+-USDT(?:-SWAP)?$")
    kind: Literal["listing", "suspend", "resume", "rules", "delist", "cash_settlement", "unit_conversion"]
    effective_ts: int = Field(ge=1577836800000)
    known_at: int = Field(ge=1577836800000)
    effective_until: int | None = Field(default=None, ge=1577836800000)
    valid_until: int | None = Field(default=None, ge=1577836800000)
    source: LifecycleSource
    instrument: dict | None = None
    margin_tiers: list[dict] = Field(default_factory=list, max_length=100)
    quantity_ratio: Decimal | None = Field(default=None, gt=0, lt="1e20")
    settlement_currency: Literal["USDT"] = "USDT"
    settlement_price: Decimal | None = Field(default=None, ge=0, lt="1e30")
    settlement_fee: Decimal = Field(default=0, ge=0, lt="1e30")

    @model_validator(mode="after")
    def typed_event(self):
        if not self.source.published_at <= self.known_at <= self.source.captured_at:
            raise ValueError("Known-at must lie between source publication and capture.")
        if self.effective_until is not None and (
            self.effective_until <= self.effective_ts or self.kind not in {"suspend", "delist"}
        ):
            raise ValueError("Only suspension/delist facts can declare an uncertain effective interval.")
        if self.valid_until is not None and self.valid_until <= self.effective_ts:
            raise ValueError("Lifecycle coverage must end after its effective boundary.")
        if self.kind in {"listing", "resume", "rules", "unit_conversion"}:
            if self.instrument is None or self.valid_until is None:
                raise ValueError("Live rules require explicit metadata and a finite coverage end.")
            validate_instrument(self.inst_id, self.instrument, self.margin_tiers)
        elif self.instrument is not None or self.margin_tiers:
            raise ValueError("Only live rule events can supply execution metadata.")
        if (self.kind == "unit_conversion") != (self.quantity_ratio is not None):
            raise ValueError("Quantity ratios belong only to typed unit conversions.")
        if (self.kind == "cash_settlement") != (self.settlement_price is not None):
            raise ValueError("Cash settlement needs an explicit attributed price.")
        if self.kind != "cash_settlement" and self.settlement_fee:
            raise ValueError("Only cash settlement can charge a lifecycle fee.")
        return self


def validate_instrument(symbol, instrument, tiers):
    if instrument.get("inst_id") != symbol or instrument.get("state") != "live":
        raise ValueError("Lifecycle rules require the same identity and live state.")
    expected_type = "SWAP" if symbol.endswith("-SWAP") else "SPOT"
    if (
        instrument.get("inst_type") != expected_type
        or instrument.get("base") != symbol.split("-")[0]
        or instrument.get("quote") != "USDT"
    ):
        raise ValueError("Only USDT spot and linear perpetual rules are supported.")
    for key in ("tick_size", "lot_size", "min_size"):
        if number(instrument.get(key)) <= 0:
            raise ValueError("Execution rules require positive increments and minimum size.")
    base_size(instrument)
    if instrument["inst_type"] == "SWAP":
        tier_for(instrument, D(1), tiers)
    elif tiers:
        raise ValueError("Spot rules cannot contain perpetual margin tiers.")


def initialize(store):
    with store.write() as conn:
        conn.executescript(SCHEMA)


def capture_events(store, source, symbol, events, interval, end):
    normalized = [encode(LifecycleEvent.model_validate(e).model_dump()) for e in events]
    seen = set()
    for event in normalized:
        key = (event["effective_ts"], event["kind"])
        if event["inst_id"] != symbol or event["effective_ts"] % interval or event["effective_ts"] >= end:
            raise PlatformError(
                "lifecycle_alignment",
                "Lifecycle facts must match the market and an aligned boundary before the end.",
                422,
            )
        if key in seen:
            raise PlatformError(
                "lifecycle_conflict",
                "Conflicting same-kind facts at one boundary require a resolved import; both captured bodies remain evidence.",
                422,
            )
        seen.add(key)
        if source != "example" and event["source"]["evidence_type"] == "synthetic_fixture":
            raise PlatformError(
                "lifecycle_source", "Synthetic lifecycle evidence belongs only to the example source.", 422
            )
    normalized.sort(key=lambda e: (e["effective_ts"], event_order(e), digest(e)))
    with store.write() as conn:
        for event in normalized:
            conn.execute(
                "INSERT OR IGNORE INTO historical_lifecycle_events VALUES(?,?,?,?,?,?)",
                (
                    digest(event),
                    source,
                    symbol,
                    dumps(event),
                    event["source"]["content_hash"],
                    event["source"]["captured_at"],
                ),
            )
    return normalized


def event_order(event):
    return {
        "listing": 0,
        "resume": 1,
        "rules": 2,
        "unit_conversion": 3,
        "suspend": 4,
        "delist": 5,
        "cash_settlement": 6,
    }[event["kind"]]


def eligibility(events, timestamp, candle_timestamps, interval, warmup):
    known = [e for e in events if e["known_at"] <= timestamp and e["effective_ts"] <= timestamp]
    active, instrument, tiers, since, coverage = "unknown", None, [], None, None
    for event in sorted(known, key=lambda e: (e["effective_ts"], event_order(e), digest(e))):
        kind = event["kind"]
        if kind in {"listing", "resume", "rules", "unit_conversion"}:
            if active == "eligible" and coverage is not None and event["effective_ts"] >= coverage:
                active, since = "unknown", None
            if kind in {"listing", "resume"}:
                since = max(event["effective_ts"], event["known_at"])
                active = "eligible"
            # Numeric rules and inventory conversion cannot manufacture a
            # listing or resume a suspended/unknown market.
            instrument, tiers, coverage = event["instrument"], event["margin_tiers"], event["valid_until"]
        elif kind == "suspend":
            active, since = "suspended", None
            if event.get("effective_until") and timestamp < event["effective_until"]:
                active = "unknown"
        else:
            active, since = "delisted", None
            if event.get("effective_until") and timestamp < event["effective_until"]:
                active = "unknown"
    if active == "eligible" and (coverage is None or timestamp >= coverage):
        active = "unknown"
    closes = 0
    if active == "eligible" and since is not None:
        prior = timestamp - interval
        while prior >= since and prior in candle_timestamps:
            closes += 1
            prior -= interval
    reason = active
    if active == "eligible" and closes < warmup:
        reason = "warming_up"
    return {
        "state": active,
        "reason": reason,
        "tradable": active == "eligible" and closes >= warmup,
        "warmup_closes": closes,
        "eligible_since": since,
        "valid_until": coverage,
        "instrument": instrument,
        "margin_tiers": tiers,
        "event_hashes": [digest(e) for e in known],
    }


def compatible_units(old, new):
    return all(
        old.get(k) == new.get(k)
        for k in ("inst_id", "inst_type", "base", "quote", "ct_val_ccy", "settle_ccy")
    )


@_accounted
def apply_inventory_event(book, source, event):
    """One idempotent BEGIN IMMEDIATE mutation; no synthetic trade order."""
    if book.contributions is not None:
        raise PlatformError(
            "lifecycle_scope",
            "Historical lifecycle mutation requires an isolated research book; managed contribution and pending-order migration are not implemented.",
            409,
        )
    event = encode(LifecycleEvent.model_validate(event).model_dump())
    identifier, symbol = digest(event), event["inst_id"]
    if event["known_at"] > event["effective_ts"]:
        raise PlatformError(
            "lifecycle_late_inventory_fact",
            "Late inventory facts cannot retrospectively rewrite intervening fills and funding.",
            409,
        )
    if event["known_at"] > book.now() or event["effective_ts"] > book.now():
        raise PlatformError(
            "lifecycle_not_known", "The lifecycle fact is not yet available at the accounting clock.", 409
        )
    if event["kind"] not in {"cash_settlement", "unit_conversion"}:
        raise PlatformError("lifecycle_kind", "This event does not mutate inventory.", 422)
    if event["kind"] == "unit_conversion" and event["instrument"]["inst_type"] != "SWAP":
        raise PlatformError(
            "lifecycle_conversion_unsupported",
            "Spot redenomination requires an adjusted price-history and identity model, even without inventory.",
            409,
        )
    with book.store.write() as conn:
        existing = conn.execute(
            "SELECT * FROM historical_lifecycle_applications WHERE source=? AND event_hash=?",
            (source, identifier),
        ).fetchone()
        if existing:
            return checked_application(existing)
        for applied in conn.execute(
            "SELECT body FROM historical_lifecycle_applications WHERE source=?", (source,)
        ):
            prior = json.loads(applied[0])
            if all(prior[k] == event[k] for k in ("inst_id", "kind", "effective_ts")):
                raise PlatformError(
                    "lifecycle_conflict",
                    "A different inventory event is already applied at this market boundary.",
                    409,
                )
        row = conn.execute(
            "SELECT * FROM pro_positions WHERE source=? AND inst_id=?", (source, symbol)
        ).fetchone()
        result = {
            "source": source,
            "event_hash": identifier,
            "inst_id": symbol,
            "kind": event["kind"],
            "effective_ts": event["effective_ts"],
            "applied_at": book.now(),
            "source_hash": event["source"]["content_hash"],
            "status": "no_inventory",
        }
        if row and number(row["quantity"]):
            old = json.loads(row["metadata"])
            quantity, margin, basis = number(row["quantity"]), number(row["margin"]), number(row["basis"])
            if event["kind"] == "unit_conversion":
                new = event["instrument"]
                if old["inst_type"] != "SWAP" or new["inst_type"] != "SWAP" or not compatible_units(old, new):
                    raise PlatformError(
                        "lifecycle_conversion_unsupported",
                        "Only same-identity linear contract-unit conversion is executable; spot redenomination and renames require a price-history and identity model.",
                        409,
                    )
                with localcontext() as exact:
                    exact.prec = 200
                    converted = number(quantity * number(event["quantity_ratio"]))
                    before, after = number(quantity * base_size(old)), number(converted * base_size(new))
                if before != after or _floor_lot(abs(converted), number(new["lot_size"])) != abs(converted):
                    raise PlatformError(
                        "lifecycle_conversion_ratio",
                        "The declared ratio must exactly conserve signed base exposure and resolve the new lot.",
                        422,
                    )
                book.deferred_funding.freeze_interval(conn, row, event["effective_ts"], identifier)
                metadata = (
                    old
                    | new
                    | {"inventory_effective_at": event["effective_ts"], "lifecycle_event_hash": identifier}
                )
                delta = _exact_sum([converted, quantity.copy_negate()])
                # Explicit contract-unit journal; no cash, basis, margin or base-price change.
                book.post(
                    conn,
                    source,
                    identifier,
                    "Historical contract-unit conversion",
                    identifier,
                    [
                        (symbol, "contract_inventory", delta),
                        (symbol, "conversion_clearing", delta.copy_negate()),
                    ],
                )
                conn.execute(
                    "UPDATE pro_positions SET quantity=?,metadata=? WHERE source=? AND inst_id=?",
                    (str(converted), dumps(metadata), source, symbol),
                )
                result.update(
                    status="applied",
                    quantity_before=str(quantity),
                    quantity_after=str(converted),
                    base_before=str(before),
                    base_after=str(after),
                    basis_before=str(basis),
                    basis_after=str(basis),
                    margin_before=str(margin),
                    margin_after=str(margin),
                    entry_price_before=row["entry_price"],
                    entry_price_after=row["entry_price"],
                    signal_price_policy="unchanged underlying base-price history",
                )
            else:
                account = conn.execute("SELECT * FROM pro_accounts WHERE source=?", (source,)).fetchone()
                cash_before, realized, debt = (
                    number(account["cash"]),
                    number(account["realized"]),
                    number(account["debt"]),
                )
                price, fee = number(event["settlement_price"]), number(event["settlement_fee"])
                journal = []
                if old["inst_type"] == "SPOT":
                    if quantity < 0:
                        raise PlatformError(
                            "lifecycle_settlement", "Spot custody cannot have a negative quantity.", 422
                        )
                    proceeds = quantity * price
                    net = proceeds - fee
                    if net < 0:
                        raise PlatformError(
                            "lifecycle_settlement",
                            "A spot cash settlement cannot debit more than its attributed proceeds.",
                            422,
                        )
                    cash, profit = cash_before + net, net - basis
                    journal = [
                        (old["base"], "inventory", quantity.copy_negate()),
                        (old["base"], "venue_clearing", quantity),
                        ("USDT", "cash", proceeds),
                        ("USDT", "venue_clearing", proceeds.copy_negate()),
                    ]
                else:
                    profit = (price - number(row["entry_price"])) * quantity * base_size(old) - fee
                    net = margin + profit
                    deficit = max(-net, D(0))
                    cash, debt = cash_before + max(net, D(0)), debt + deficit
                    journal = [
                        ("USDT", "margin", margin.copy_negate()),
                        ("USDT", "cash", margin + profit + fee),
                        ("USDT", "derivative_pnl", -(profit + fee)),
                    ]
                    if deficit:
                        journal.append(("USDT", "insurance_liability", -deficit))
                        conn.execute("UPDATE pro_risk SET halted=1 WHERE source=?", (source,))
                    book.deferred_funding.freeze_interval(conn, row, event["effective_ts"], identifier)
                journal.extend([("USDT", "cash", -fee), ("USDT", "fee_expense", fee)])
                book.post(
                    conn,
                    source,
                    identifier,
                    "Historical attributed cash settlement",
                    identifier,
                    _cash_journal(journal, cash_before, cash),
                )
                for amount in (cash, realized + profit, debt, number(account["fees"]) + fee):
                    number(amount)
                conn.execute(
                    "UPDATE pro_accounts SET cash=?,realized=?,fees=?,debt=? WHERE source=?",
                    (
                        str(cash),
                        str(realized + profit),
                        str(number(account["fees"]) + fee),
                        str(debt),
                        source,
                    ),
                )
                conn.execute(
                    "UPDATE pro_positions SET quantity='0',margin='0',basis='0' WHERE source=? AND inst_id=?",
                    (source, symbol),
                )
                result.update(
                    status="applied",
                    quantity_before=str(quantity),
                    quantity_after="0",
                    price=str(price),
                    fee=str(fee),
                    cash_delta=str(cash - cash_before),
                    realized_delta=str(profit),
                    basis_released=str(basis),
                    margin_released=str(margin),
                    insurance_debt=str(debt),
                )
        result["application_hash"] = digest(result)
        conn.execute(
            "INSERT INTO historical_lifecycle_applications VALUES(?,?,?,?)",
            (source, identifier, dumps(result), book.now()),
        )
        return result


def checked_event(row):
    """Verify an immutable captured fact before evidence/recovery use."""
    try:
        body = json.loads(row["body"])
        if (
            digest(body) != row["event_hash"]
            or body["inst_id"] != row["inst_id"]
            or body["source"]["content_hash"] != row["source_hash"]
            or body["source"]["captured_at"] != row["created_at"]
        ):
            raise ValueError()
        LifecycleEvent.model_validate(body)
        if row["source"] != "example" and body["source"]["evidence_type"] == "synthetic_fixture":
            raise ValueError()
        return body
    except (KeyError, TypeError, ValueError, PlatformError):
        raise PlatformError(
            "lifecycle_integrity", "Captured lifecycle source identity does not verify.", 409
        ) from None


def checked_application(row):
    """Verify a journal-linked application identity and supported amounts."""
    try:
        body = json.loads(row["body"])
        unhashed = {k: v for k, v in body.items() if k != "application_hash"}
        if (
            digest(unhashed) != body["application_hash"]
            or body["event_hash"] != row["event_hash"]
            or body["source"] != row["source"]
            or body["applied_at"] != row["applied_at"]
            or body["kind"] not in {"unit_conversion", "cash_settlement"}
            or body["status"] not in {"applied", "no_inventory"}
        ):
            raise ValueError()
        for key in (
            "quantity_before",
            "quantity_after",
            "base_before",
            "base_after",
            "basis_before",
            "basis_after",
            "margin_before",
            "margin_after",
            "entry_price_before",
            "entry_price_after",
            "price",
            "fee",
            "cash_delta",
            "realized_delta",
            "basis_released",
            "margin_released",
            "insurance_debt",
        ):
            if key in body:
                if not isinstance(body[key], str):
                    raise ValueError()
                number(body[key])
        if (
            body["status"] == "applied"
            and body["kind"] == "unit_conversion"
            and number(body["base_before"]) != number(body["base_after"])
        ):
            raise ValueError()
        return body
    except (KeyError, TypeError, ValueError, PlatformError):
        raise PlatformError(
            "lifecycle_integrity", "Lifecycle accounting application identity does not verify.", 409
        ) from None
