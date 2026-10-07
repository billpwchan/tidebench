import asyncio
from decimal import Decimal

import httpx
import pytest
from tidebench.market import BAR_MS, EXAMPLE_ANCHOR, MarketError, MarketService


def candle(ts, *, confirmed="1", close="101"):
    return [str(ts), "100", "102", "99", close, "12.34567890", "1234", "1234", confirmed]


def ticker(ts, *, last="101"):
    return {
        "instId": "BTC-USDT",
        "last": last,
        "bidPx": "100.9",
        "askPx": "101.1",
        "open24h": "100",
        "high24h": "105",
        "low24h": "95",
        "vol24h": "12345.12345678",
        "volCcy24h": "987654.12345678",
        "ts": str(ts),
    }


def ok(data):
    return httpx.Response(200, json={"code": "0", "msg": "", "data": data})


@pytest.fixture
def make_service():
    clients = []

    def make(handler, region="global"):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return MarketService(region=region, client=client)

    yield make
    # Injected clients belong to the caller; the async tests close them explicitly.


async def test_example_is_explicit_deterministic_and_network_free(make_service):
    def no_network(request):
        pytest.fail("Example mode must never make a network request")

    market = make_service(no_network)
    short = await market.get_candles("BTC-USDT", limit=60, source="example")
    long = await market.get_candles("BTC-USDT", limit=120, source="example")
    assert short["candles"] == long["candles"][-60:]
    assert short["fetched_at"] == EXAMPLE_ANCHOR
    assert short["candles"][-1].ts + BAR_MS["1H"] == EXAMPLE_ANCHOR
    assert short["quality"]["complete"]
    assert "Synthetic" in short["warning"]
    result = await market.get_tickers("example")
    assert result["transport"] == "example"
    assert all(item["ts"] == EXAMPLE_ANCHOR for item in result["items"])
    expected_volume = sum((c.volume * c.close for c in short["candles"][-24:]), Decimal(0))
    assert Decimal(result["items"][0]["volume_24h"]) == expected_volume
    instruments = await market.get_instruments("example")
    assert len(instruments) == 5
    assert all(isinstance(item.tick_size, Decimal) for item in instruments)
    await market._client.aclose()


async def test_pagination_goes_older_filters_open_bar_and_deduplicates(make_service):
    interval = BAR_MS["1H"]
    requests = []
    newest = EXAMPLE_ANCHOR

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            rows = [candle(newest - i * interval, confirmed="0" if i == 0 else "1") for i in range(300)]
        else:
            # One overlapping boundary and an older continuation.
            rows = [candle(newest - i * interval) for i in range(299, 599)]
        return ok(rows)

    market = make_service(handler)
    result = await market.get_candles("BTC-USDT", limit=500)
    candles = result["candles"]
    assert len(candles) == 500
    assert len({c.ts for c in candles}) == 500
    assert all(c.confirmed for c in candles)
    assert candles == sorted(candles, key=lambda c: c.ts)
    assert candles[-1].ts == newest - interval
    assert candles[-1].volume == Decimal("12.34567890")
    assert requests[0].url.path == "/api/v5/market/candles"
    assert requests[1].url.path == "/api/v5/market/history-candles"
    assert requests[1].url.params["after"] == str(newest - 299 * interval)
    assert requests[1].url.params["limit"] == "300"
    assert result["quality"]["duplicates_removed"] == 1
    assert result["quality"]["unconfirmed_removed"] == 1
    assert result["quality"]["complete"]
    await market._client.aclose()


async def test_gap_is_reported_without_inventing_a_candle(make_service):
    interval = BAR_MS["1H"]
    rows = [candle(EXAMPLE_ANCHOR - i * interval) for i in (1, 2, 4)]
    market = make_service(lambda request: ok(rows))
    result = await market.get_candles("BTC-USDT", limit=3)
    assert result["quality"]["missing_intervals"] == 1
    assert not result["quality"]["complete"]
    assert len(result["candles"]) == 3
    assert "gaps" in result["warning"]
    await market._client.aclose()


async def test_short_history_is_not_claimed_complete(make_service):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return ok([candle(EXAMPLE_ANCHOR - BAR_MS["1H"])]) if calls == 1 else ok([])

    market = make_service(handler)
    result = await market.get_candles("BTC-USDT", limit=60)
    assert not result["quality"]["sufficient_history"]
    assert result["quality"]["received"] == 1
    assert "1 of 60" in result["warning"]
    await market._client.aclose()


async def test_stalled_pagination_is_bounded_and_fails(make_service):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return ok([candle(EXAMPLE_ANCHOR)])

    market = make_service(handler)
    with pytest.raises(MarketError) as caught:
        await market.get_candles("BTC-USDT", limit=60)
    assert caught.value.code == "pagination_stalled"
    assert calls == 2
    await market._client.aclose()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0", "", "1e99999999"])
async def test_invalid_price_never_becomes_zero_or_example(make_service, bad):
    market = make_service(lambda request: ok([candle(EXAMPLE_ANCHOR, close=bad)]))
    with pytest.raises(MarketError) as caught:
        await market.get_candles("BTC-USDT", limit=1)
    assert caught.value.code == "invalid_upstream_data"
    await market._client.aclose()


async def test_ticker_rejects_timestamp_regression_and_preserves_precision(make_service):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return ok(
            [
                ticker(
                    EXAMPLE_ANCHOR if calls == 1 else EXAMPLE_ANCHOR - 1000,
                    last="101" if calls == 1 else "99",
                )
            ]
        )

    market = make_service(handler)
    first = await market.get_tickers()
    market._cache.clear()
    second = await market.get_tickers()
    assert first["items"][0]["last"] == second["items"][0]["last"] == "101"
    assert second["items"][0]["ts"] == EXAMPLE_ANCHOR
    assert second["items"][0]["volume_24h"] == "987654.12345678"
    assert second["items"][0]["change_pct"] == 1
    assert "Unavailable" in second["warning"]
    await market._client.aclose()


async def test_cache_serializes_concurrent_requests_and_returns_copies(make_service):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ok([ticker(EXAMPLE_ANCHOR)])

    market = make_service(handler)
    a, b = await asyncio.gather(market.get_tickers(), market.get_tickers())
    a["items"][0]["last"] = "999"
    assert b["items"][0]["last"] == "101"
    assert calls == 1
    await market._client.aclose()


async def test_instruments_use_exchange_rules_in_decimal(make_service):
    market = make_service(
        lambda request: ok(
            [
                {
                    "instId": "BTC-USDT",
                    "instType": "SPOT",
                    "baseCcy": "BTC",
                    "quoteCcy": "USDT",
                    "tickSz": "0.1",
                    "lotSz": "0.00000001",
                    "minSz": "0.00001",
                    "state": "suspend",
                },
                {"instId": "UNSUPPORTED-USDT"},
            ]
        )
    )
    instruments = await market.get_instruments()
    assert len(instruments) == 1
    assert instruments[0].lot_size == Decimal("0.00000001")
    assert instruments[0].state == "suspend"
    await market._client.aclose()


async def test_http_redirect_is_not_followed(make_service):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://attacker.example"})

    market = make_service(handler)
    with pytest.raises(MarketError) as caught:
        await market.get_tickers()
    assert caught.value.code == "upstream_http_error"
    assert len(seen) == 1
    assert seen[0].startswith("https://openapi.okx.com/")
    await market._client.aclose()


async def test_network_fallback_stays_within_official_global_allowlist(make_service):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "openapi.okx.com":
            raise httpx.ConnectError("network down", request=request)
        return ok([ticker(EXAMPLE_ANCHOR)])

    market = make_service(handler)
    result = await market.get_tickers()
    assert result["source"] == "okx"
    assert hosts == ["openapi.okx.com"] * 3 + ["www.okx.com"]
    await market._client.aclose()


async def test_rate_limit_retry_does_not_switch_hosts_or_return_example(make_service):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(429)

    market = make_service(handler)
    with pytest.raises(MarketError) as caught:
        await market.get_tickers()
    assert caught.value.code == "upstream_rate_limited"
    assert seen == ["openapi.okx.com"] * 3
    await market._client.aclose()


@pytest.mark.parametrize("region,host", [("us", "us.okx.com"), ("eea", "eea.okx.com")])
async def test_regions_are_explicit_and_not_redirected_to_global(make_service, region, host):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return ok([ticker(EXAMPLE_ANCHOR)])

    market = make_service(handler, region)
    await market.get_tickers()
    assert seen == [host]
    await market._client.aclose()


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"inst_id": "../../private"}, "invalid_instrument"),
        ({"inst_id": "BTC-USDT", "bar": "1D"}, "invalid_bar"),
        ({"inst_id": "BTC-USDT", "limit": 2001}, "invalid_limit"),
        ({"inst_id": "BTC-USDT", "limit": True}, "invalid_limit"),
        ({"inst_id": "BTC-USDT", "source": "auto"}, "invalid_source"),
    ],
)
async def test_untrusted_input_fails_before_network(make_service, kwargs, code):
    market = make_service(lambda request: pytest.fail("Unexpected network request"))
    with pytest.raises(MarketError) as caught:
        await market.get_candles(**kwargs)
    assert caught.value.code == code
    await market._client.aclose()


def test_arbitrary_region_or_host_is_not_accepted():
    with pytest.raises(MarketError, match="Region must"):
        MarketService(region="https://attacker.example")
