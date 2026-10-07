# OKX integration

Tidebench v0.1 uses **read-only REST polling** for live spot quotes and historical candles. It sends no orders to OKX, uses no exchange API keys, and implements local paper execution. Local paper is different from OKX Demo Trading. WebSocket ingestion and private execution are future work, not current capabilities.

## Sources and provenance

`okx` fetches public market data from the selected region. Failures are explicit API errors; they never switch the user to example data. `example` generates deterministic synthetic candles locally, with a fixed end time of **2026-01-01 00:00 UTC**. It is neither recorded OKX data nor a live simulation feed. Example candle ranges overlap exactly across requests. Its ticker timestamp describes the synthetic dataset, not the time the page was opened. All example accounts and research results stay separate from OKX-sourced accounts.

The professional catalog discovers USDT spot and linear USDT perpetual rules from the venue; Overview and Classic retain five spot shortcuts. History supports `1m`, `5m`, `15m`, `1H`, `4H`, `1Dutc`. Contract quantity is contracts, with explicit contract-size conversion to base units. Separate trade, mark, index and realized funding datasets preserve versions and quality. See [data operations](data-operations.md) and [professional research](pro-research.md).

Ticker `volume_24h` is quote turnover in **USDT**, using OKX spot `volCcy24h`. Candle `volume` is the base-asset quantity from `vol`; these fields have different units. Example quote turnover is the sum of synthetic base volumes multiplied by their synthetic close prices.

## Fixed regional hosts

| Region | REST host | Future public WebSocket host |
| --- | --- | --- |
| Global | `https://openapi.okx.com` | `wss://ws.okx.com/ws/v5/public` |
| US / Australia | `https://us.okx.com` | `wss://wsus.okx.com/ws/v5/public` |
| EEA | `https://eea.okx.com` | `wss://wseea.okx.com/ws/v5/public` |

Global also permits the fixed official `https://www.okx.com` host after bounded transport or server failures. It does not switch hosts after rate limiting, regional rejection or other client errors. Redirects are disabled. User-supplied URLs are never accepted. Select the region appropriate to the account and jurisdiction; this fallback is not a way to bypass restrictions. See the [Global](https://www.okx.com/docs-v5/en/), [US/AU](https://app.okx.com/docs-v5/en/) and [EEA](https://my.okx.com/docs-v5/en/) official documentation.

The [2026-09-30 change log](https://www.okx.com/docs-v5/log_en/) announces that WebSocket port **8443 stops working on 2026-10-31**. Port 443 already works. Future integration must use URLs without `:8443`, even where older reference examples still show it. Candles use `/ws/v5/business`, whereas tickers use `/ws/v5/public`.

## Data correctness

The adapter reads `/api/v5/public/instruments`, `/api/v5/market/tickers`, `/api/v5/market/candles`, and `/api/v5/market/history-candles`. Historical pagination uses `after` to request **older** records, at most 300 per page. Pages must advance; duplicates are removed. Only `confirm=1` candles enter research. The returned series is oldest first and reports insufficient history, missing intervals and timestamp misalignment. Gaps are never filled with invented prices. `1Dutc` is explicit because the unqualified daily bar uses a different opening timezone. Decimal strings are validated for finite, sensible values and retained as `Decimal` internally. Exchange ticker timestamps cannot move an existing quote backwards. See [official market data fields](https://www.okx.com/docs-v5/en/).

Requests are bounded to three attempts per host with backoff, timeouts, conservative per-instance throttling, cache lifetimes and per-cache-key locks. Permanent HTTP/business/data errors fail immediately. The single-process deployment does not provide a distributed IP-scoped limiter; multiple workers behind one egress IP require one. Quotes are polled, so the UI reports REST transport and exchange timestamps rather than claiming streaming latency. An authenticated exchange key is unnecessary for these endpoints.

## Data rights and commercial scope

The [OKX API Agreement](https://www.okx.com/help/okx-api-agreement), updated 2026-07-28, addresses third-party commercial API services in §9.3 and market-data use and redistribution in §9.4, including unauthenticated public endpoints. A hosted commercial product or shared market-data service may require prior written authorization and separate data licensing. Obtain a review of the actual intended deployment and applicable regional terms before operating such a service.

This repository distributes original application code and synthetic test inputs, not an OKX historical dataset. The repository's code license does not grant rights to exchange data, services or trademarks. Local retrieval and local research exports must still respect the data provider's terms; do not publish fetched raw datasets or real-data exports merely because the application source is open.

## Future execution design

Before adding private execution, require server-side secret storage, least privilege, IP restrictions, independent risk gates, an order journal and reconciliation. OKX client order IDs are checked for uniqueness among pending orders; they do not provide permanent application-level idempotency. Successful submission/cancellation acknowledgements do not establish the final order state, and the private orders stream has no initial snapshot. A disconnected or timed-out submission must be reconciled before retrying. These are design requirements, not shipped execution features. See [OKX trading best practice](https://www.okx.com/docs-v5/trick_en/).
