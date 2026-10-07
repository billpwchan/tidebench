"""Read-only OKX market data and explicit, deterministic example data.

All URLs come from REST_HOSTS. This module never accepts credentials or sends
orders, and an upstream failure never silently switches to example prices.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from weakref import WeakValueDictionary

import httpx

from .engine import Candle, Instrument

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "OKB-USDT", "DOGE-USDT")
BAR_MS = {"15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
REST_HOSTS = {
    "global": ("https://openapi.okx.com", "https://www.okx.com"),
    "us": ("https://us.okx.com",),
    "eea": ("https://eea.okx.com",),
}
EXAMPLE_ANCHOR = 1_767_225_600_000  # 2026-01-01T00:00:00Z; never the wall clock.
EXAMPLE_WARNING = "Synthetic example data, fixed at 2026-01-01 UTC. Not OKX market data."
_BASE_PRICES = {"BTC": "60000", "ETH": "3000", "SOL": "140", "OKB": "45", "DOGE": "0.12"}
_EXAMPLE_RULES = {
    "BTC": ("0.1", "0.00000001", "0.00001"),
    "ETH": ("0.01", "0.00000001", "0.0001"),
    "SOL": ("0.001", "0.000001", "0.01"),
    "OKB": ("0.001", "0.000001", "0.01"),
    "DOGE": ("0.00001", "0.000001", "1"),
}


class MarketError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    try:
        if value is None or isinstance(value, bool) or str(value).strip() == "":
            raise InvalidOperation
        if len(str(value)) > 80:
            raise InvalidOperation
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MarketError("invalid_upstream_data", f"Invalid numeric field: {field}.") from None
    if not number.is_finite() or abs(number.adjusted()) > 30 or (number <= 0 if positive else number < 0):
        raise MarketError("invalid_upstream_data", f"Out-of-range numeric field: {field}.")
    return number


def _timestamp(value: Any) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = int(value)
        if str(result) != str(value) or result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise MarketError("invalid_upstream_data", "Invalid exchange timestamp.") from None


def _text(number: Decimal) -> str:
    return format(number, "f")


def _source(source: str) -> None:
    if source not in {"okx", "example"}:
        raise MarketError("invalid_source", "Source must be 'okx' or 'example'.")


def _candle(row: Any) -> Candle:
    if not isinstance(row, list) or len(row) < 9 or row[8] not in ("0", "1"):
        raise MarketError("invalid_upstream_data", "Invalid OKX candlestick array.")
    ts = _timestamp(row[0])
    o, h, low, close = (_decimal(row[i], "ohlc", positive=True) for i in range(1, 5))
    volume = _decimal(row[5], "volume")
    if low > min(o, close) or h < max(o, close) or low > h:
        raise MarketError("invalid_upstream_data", "Inconsistent OHLC values.")
    return Candle(ts=ts, open=o, high=h, low=low, close=close, volume=volume, confirmed=row[8] == "1")


def _quality(candles: list[Candle], interval: int, requested: int, **counts: int) -> dict[str, Any]:
    gaps: list[dict[str, int]] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        delta = current.ts - previous.ts
        if delta != interval:
            gaps.append(
                {"after": previous.ts, "before": current.ts, "missing": max(0, delta // interval - 1)}
            )
    aligned = all(c.ts % interval == 0 for c in candles)
    return {
        "complete": len(candles) == requested and not gaps and aligned,
        "sufficient_history": len(candles) == requested,
        "requested": requested,
        "received": len(candles),
        "interval_ms": interval,
        "missing_intervals": sum(gap["missing"] for gap in gaps),
        "gaps": gaps,
        "timestamps_aligned": aligned,
        "start": candles[0].ts if candles else None,
        "end": candles[-1].ts if candles else None,
        **counts,
    }


class MarketService:
    def __init__(self, region: str = "global", client: httpx.AsyncClient | None = None) -> None:
        if region not in REST_HOSTS:
            raise MarketError("invalid_region", "Region must be 'global', 'us', or 'eea'.")
        self.region = region
        self._client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._locks: WeakValueDictionary[tuple[Any, ...], asyncio.Lock] = WeakValueDictionary()
        self._rate_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._last_tickers: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _throttle(self) -> None:
        # A conservative per-instance budget. A hosted multi-worker deployment
        # needs a shared IP-scoped limiter before increasing concurrency.
        async with self._rate_lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + 0.125

    async def _get(self, path: str, params: dict[str, Any]) -> list[Any]:
        last_error: MarketError | None = None
        for host in REST_HOSTS[self.region]:
            fallback_eligible = False
            rate_limited = False
            for attempt in range(3):
                await self._throttle()
                try:
                    response = await self._client.get(host + path, params=params, follow_redirects=False)
                except httpx.TransportError:
                    last_error = MarketError(
                        "upstream_unavailable", "Cannot reach the configured OKX market endpoint."
                    )
                    fallback_eligible = not rate_limited
                else:
                    if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                        last_error = MarketError(
                            "upstream_rate_limited"
                            if response.status_code == 429
                            else "upstream_unavailable",
                            "OKX market data is temporarily unavailable. Retry later.",
                        )
                        # Do not evade exchange rate limits by changing hosts.
                        rate_limited = rate_limited or response.status_code == 429
                        fallback_eligible = not rate_limited
                    elif response.status_code != 200:
                        raise MarketError(
                            "upstream_http_error",
                            f"OKX returned HTTP {response.status_code}. Check region and network access.",
                        )
                    else:
                        try:
                            payload = response.json()
                        except ValueError:
                            raise MarketError("invalid_upstream_data", "OKX returned invalid JSON.") from None
                        if not isinstance(payload, dict):
                            raise MarketError("invalid_upstream_data", "Invalid OKX response envelope.")
                        if str(payload.get("code")) == "50011":
                            last_error = MarketError(
                                "upstream_rate_limited", "OKX market API rate limit reached. Retry later."
                            )
                            rate_limited = True
                            fallback_eligible = False
                        elif str(payload.get("code")) != "0":
                            raise MarketError(
                                "upstream_api_error",
                                f"OKX rejected the market request (code {str(payload.get('code', 'unknown'))[:20]}).",
                            )
                        elif not isinstance(payload.get("data"), list):
                            raise MarketError("invalid_upstream_data", "Missing OKX response data array.")
                        else:
                            return payload["data"]
                if attempt < 2:
                    await asyncio.sleep(0.2 * 2**attempt)
            if not fallback_eligible:
                break
        raise last_error or MarketError("upstream_unavailable", "OKX market data is unavailable.")

    async def _cached(self, key: tuple[Any, ...], ttl: float, producer: Any) -> Any:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return copy.deepcopy(cached[1])
            result = await producer()
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= 64:
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
            self._cache[key] = (time.monotonic() + ttl, result)
            return copy.deepcopy(result)

    async def get_instruments(self, source: str = "okx") -> list[Instrument]:
        _source(source)

        async def load() -> list[Instrument]:
            if source == "example":
                return [
                    Instrument(
                        inst_id=symbol,
                        base=symbol.split("-")[0],
                        quote="USDT",
                        tick_size=Decimal(_EXAMPLE_RULES[symbol.split("-")[0]][0]),
                        lot_size=Decimal(_EXAMPLE_RULES[symbol.split("-")[0]][1]),
                        min_size=Decimal(_EXAMPLE_RULES[symbol.split("-")[0]][2]),
                        state="live",
                    )
                    for symbol in SYMBOLS
                ]
            rows = await self._get("/api/v5/public/instruments", {"instType": "SPOT"})
            found: dict[str, Instrument] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise MarketError("invalid_upstream_data", "Invalid instrument object.")
                symbol = row.get("instId")
                if symbol not in SYMBOLS:
                    continue
                base, quote = row.get("baseCcy"), row.get("quoteCcy")
                if (
                    not isinstance(base, str)
                    or not isinstance(quote, str)
                    or f"{base}-{quote}" != symbol
                    or row.get("instType", "SPOT") != "SPOT"
                ):
                    raise MarketError("invalid_upstream_data", "Invalid spot instrument metadata.")
                found[symbol] = Instrument(
                    inst_id=symbol,
                    base=base,
                    quote=quote,
                    tick_size=_decimal(row.get("tickSz"), "tickSz", positive=True),
                    lot_size=_decimal(row.get("lotSz"), "lotSz", positive=True),
                    min_size=_decimal(row.get("minSz"), "minSz", positive=True),
                    state=row.get("state", "unknown"),
                )
            if not found:
                raise MarketError(
                    "no_market_data", "The configured OKX region returned no supported spot instruments."
                )
            return [found[symbol] for symbol in SYMBOLS if symbol in found]

        return await self._cached(("instruments", source), 3600, load)

    async def get_tickers(self, source: str = "okx") -> dict[str, Any]:
        _source(source)

        async def load() -> dict[str, Any]:
            if source == "example":
                items = []
                for symbol in SYMBOLS:
                    bars = _example_candles(symbol, "1H", 25)
                    last, open_24h = bars[-1].close, bars[1].open
                    spread = last * Decimal("0.0002")
                    items.append(
                        {
                            "inst_id": symbol,
                            "last": _text(last),
                            "bid": _text(last - spread),
                            "ask": _text(last + spread),
                            "open_24h": _text(open_24h),
                            "high_24h": _text(max(c.high for c in bars[1:])),
                            "low_24h": _text(min(c.low for c in bars[1:])),
                            "volume_24h": _text(sum((c.volume * c.close for c in bars[1:]), Decimal(0))),
                            "change_pct": float((last / open_24h - 1) * 100),
                            "ts": EXAMPLE_ANCHOR,
                        }
                    )
                return {
                    "source": source,
                    "as_of": EXAMPLE_ANCHOR,
                    "transport": "example",
                    "items": items,
                    "warning": EXAMPLE_WARNING,
                }
            rows = await self._get("/api/v5/market/tickers", {"instType": "SPOT"})
            found: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise MarketError("invalid_upstream_data", "Invalid ticker object.")
                symbol = row.get("instId")
                if symbol not in SYMBOLS:
                    continue
                last = _decimal(row.get("last"), "last", positive=True)
                opening = _decimal(row.get("open24h"), "open24h", positive=True)
                item = {
                    "inst_id": symbol,
                    "last": _text(last),
                    "bid": _text(_decimal(row["bidPx"], "bidPx", positive=True)) if row.get("bidPx") else "",
                    "ask": _text(_decimal(row["askPx"], "askPx", positive=True)) if row.get("askPx") else "",
                    "open_24h": _text(opening),
                    "high_24h": _text(_decimal(row.get("high24h"), "high24h", positive=True)),
                    "low_24h": _text(_decimal(row.get("low24h"), "low24h", positive=True)),
                    "volume_24h": _text(_decimal(row.get("volCcy24h"), "volCcy24h")),
                    "change_pct": float((last / opening - 1) * 100),
                    "ts": _timestamp(row.get("ts")),
                }
                previous = found.get(symbol) or self._last_tickers.get(symbol)
                if previous and previous["ts"] > item["ts"]:
                    item = previous
                found[symbol] = item
            if not found:
                raise MarketError("no_market_data", "OKX returned no supported spot ticker data.")
            self._last_tickers.update(found)
            missing = [symbol for symbol in SYMBOLS if symbol not in found]
            items = [found[symbol] for symbol in SYMBOLS if symbol in found]
            return {
                "source": source,
                "as_of": max(item["ts"] for item in items),
                "transport": "rest",
                "items": items,
                "warning": f"Unavailable instruments: {', '.join(missing)}." if missing else None,
            }

        return await self._cached(("tickers", source), 2, load)

    async def get_candles(
        self, inst_id: str, bar: str = "1H", limit: int = 720, source: str = "okx"
    ) -> dict[str, Any]:
        _source(source)
        if inst_id not in SYMBOLS:
            raise MarketError("invalid_instrument", "Unsupported instrument.")
        if bar not in BAR_MS:
            raise MarketError("invalid_bar", "Bar must be 15m, 1H, 4H, or 1Dutc.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise MarketError("invalid_limit", "Candle limit must be an integer from 1 to 2000.")

        async def load() -> dict[str, Any]:
            if source == "example":
                candles = _example_candles(inst_id, bar, limit)
                return {
                    "source": source,
                    "inst_id": inst_id,
                    "bar": bar,
                    "fetched_at": EXAMPLE_ANCHOR,
                    "candles": candles,
                    "quality": _quality(
                        candles, BAR_MS[bar], limit, unconfirmed_removed=0, duplicates_removed=0
                    ),
                    "warning": EXAMPLE_WARNING,
                }
            found: dict[int, Candle] = {}
            cursor: int | None = None
            duplicates, unconfirmed = 0, 0
            # Extra pages cover the open candle and overlapping page boundaries;
            # the cursor progress check prevents a broken upstream infinite loop.
            for page in range(math.ceil(limit / 300) + 3):
                params: dict[str, Any] = {"instId": inst_id, "bar": bar, "limit": 300}
                if cursor is not None:
                    params["after"] = str(cursor)
                rows = await self._get(
                    "/api/v5/market/candles" if page == 0 else "/api/v5/market/history-candles", params
                )
                if not rows:
                    break
                parsed = [_candle(row) for row in rows]
                next_cursor = min(c.ts for c in parsed)
                if cursor is not None and next_cursor >= cursor:
                    raise MarketError(
                        "pagination_stalled", "OKX historical pagination did not advance to older candles."
                    )
                cursor = next_cursor
                for candle in parsed:
                    if not candle.confirmed:
                        unconfirmed += 1
                        continue
                    if candle.ts in found:
                        duplicates += 1
                    else:
                        found[candle.ts] = candle
                if len(found) >= limit:
                    break
            candles = sorted(found.values(), key=lambda c: c.ts)[-limit:]
            if not candles:
                raise MarketError("no_market_data", "OKX returned no completed candles for this instrument.")
            quality = _quality(
                candles, BAR_MS[bar], limit, unconfirmed_removed=unconfirmed, duplicates_removed=duplicates
            )
            warnings = []
            if quality["gaps"] or not quality["timestamps_aligned"]:
                warnings.append(
                    "Historical candle sequence has gaps or misaligned timestamps. Do not treat it as continuous."
                )
            if not quality["sufficient_history"]:
                warnings.append(f"Only {len(candles)} of {limit} requested completed candles are available.")
            return {
                "source": source,
                "inst_id": inst_id,
                "bar": bar,
                "fetched_at": int(time.time() * 1000),
                "candles": candles,
                "quality": quality,
                "warning": " ".join(warnings) or None,
            }

        return await self._cached(("candles", source, inst_id, bar, limit), 10, load)


def _example_candles(inst_id: str, bar: str, limit: int) -> list[Candle]:
    """Same instrument and timestamp always yield the same synthetic bar."""
    base = inst_id.split("-")[0]
    scale = Decimal(_BASE_PRICES[base])
    tick = Decimal(_EXAMPLE_RULES[base][0])
    interval = BAR_MS[bar]
    end = EXAMPLE_ANCHOR // interval
    phase = int(hashlib.sha256(inst_id.encode()).hexdigest()[:8], 16) % 1000 / 100

    def price(slot: int) -> Decimal:
        wave = 1 + 0.045 * math.sin(slot * 0.023 + phase) + 0.012 * math.sin(slot * 0.17 + phase)
        return (scale * Decimal(str(wave)) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick

    candles = []
    for slot in range(end - limit, end):
        o, close = price(slot), price(slot + 1)
        wick = scale * Decimal(str(0.002 + 0.001 * abs(math.sin(slot + phase))))
        high = ((max(o, close) + wick) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
        low = ((min(o, close) - wick) / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
        volume = (Decimal(str(100_000 + 25_000 * abs(math.sin(slot * 0.07)))) / close).quantize(
            Decimal("0.00000001")
        )
        candles.append(
            Candle(ts=slot * interval, open=o, high=high, low=low, close=close, volume=volume, confirmed=True)
        )
    return candles
