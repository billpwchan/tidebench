# Market data operations

`CatalogService` is a durable, single-workspace data catalog. It downloads and
validates public OKX history, publishes immutable dataset versions, imports
attributed external records, and runs observable REST watches. No exchange key
is required. These services do not submit exchange orders, reconstruct a full
order book, or establish the correctness of a private account ledger.

## Dataset contract

Every job and dataset records `source`, region, instrument, kind, UTC range and
bar. `source=okx` and `source=example` are isolated; a failed real request never
changes source. Ranges are **[start, end)**, with millisecond Unix timestamps.
Price bars must align to UTC boundaries. Funding events use their actual
settlement timestamps and need no assumed schedule.

| Field | Accepted values / meaning |
| --- | --- |
| Instrument | USDT spot or linear USDT perpetual, such as `BTC-USDT` / `BTC-USDT-SWAP` |
| Kind | `trade`, `mark`, `index`, `funding` |
| Bar | `1m`, `5m`, `15m`, `1H`, `4H`, `1Dutc` |
| Transport | `rest`, `example`, `user_import` |
| Job status | `queued`, `running`, `completed`, `degraded`, `failed`, `canceled` |
| Size bound | 1,000,000 bars per download; 250,000 records per import; 20 active jobs |

Example data is deterministic and explicitly synthetic. Its price is addressed
by absolute UTC time: different bars, spot/perpetual trade candles, mark
candles, execution quotes and funding marks share one series. Index prices
have a small declared synthetic basis. The current example quote is fixed at
2026-01-01 00:00 UTC; its old timestamp is intentional and cannot demonstrate
live connectivity. Example funding deliberately alternates 4-hour and 8-hour
intervals to exercise consumers that accidentally hardcode eight hours.

Instrument snapshots retain tick/lot/minimum size, state, base/quote/settlement
currencies and, for perpetuals, `ct_val`, `ct_mult`, `ct_val_ccy` and
`contract_size_base`. Only unit-multiplier, base-denominated linear USDT
contracts are supported. **Spot quantity and trade volume are base units;
perpetual quantity and trade volume are contracts.** Mark and index candles
have no traded volume. USDT turnover in the legacy ticker is a different field.
See the [official instrument and candle definitions](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-instruments).

Rules are observed current snapshots, not a reconstructed history of every
listing, tick-size change or margin-policy change. A historical run must retain
that assumption alongside its dataset IDs. Cached rules refresh after an hour;
call `refresh_instruments` when an immediate refresh is required.

## Download, restart and cancellation

```python
job = catalog.create_job(
    "BTC-USDT-SWAP", "trade", "1H", start_ms, end_ms, source="okx"
)
result = await catalog.run_job(job["id"])
manifest = catalog.get_dataset(result["dataset_id"])
if not manifest["quality"]["complete"]:
    raise ValueError("Inspect missing coverage before running research")
candles = catalog.load_candles(manifest["id"])
```

Each page commits records, cursor, row count and progress together. A job claim
uses a token; a canceled worker cannot commit a page that returned after the
cancellation. Identical timestamps deduplicate; conflicting overlapping records
fail the job rather than silently selecting a price. Pagination must move
backward and cannot cross its upper cursor. Confirmed candles enter the dataset;
unconfirmed, absent or misaligned bars prevent complete coverage. Missing bars
are never filled with a flat or generated price.

`progress` measures the requested time range traversed, capped at 0.99 until
publication. A degraded published dataset reaches 1.0 because the download
finished; **this does not mean complete market history**. Read
`quality.complete`, `gaps`, `expected_records`, `records`, `coverage_start/end`
and `coverage_method` before accepting it.

On graceful task cancellation, the last committed cursor survives and the job
returns to `queued`. On a process restart, acquire the application's exclusive
workspace/process lock, construct the service, call `resume_pending()` once,
then dispatch returned jobs. Recovery is limited to the configured region.
`resume_pending()` is not a distributed lease and must not run beside another
writer process. Explicit user cancellation is terminal. A failed download can
be requeued with `retry_job(id)`; its committed cursor survives. An immutable
published version cannot be resumed or patched into a different history.

The catalog uses the market adapter's fixed regional allowlist, GET-only
transport, bounded retries and throttle. Historical funding is limited by the
provider's three-month API retention. Exhausting available history before
proving the requested lower bound produces degraded coverage; it does not
invent earlier settlements. Funding completeness never relies on a fixed
event interval. [Official funding history contract](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history).

## Reproducibility and integrity

Published manifests contain a monotonic version, canonical SHA-256 content
hash, full instrument snapshot, source/region/range, quality, transport and
attribution. Schema version 3 binds the quality result, rule snapshot hash and
import provenance as well as sorted records. Observation and publication times
do not change the content identity. Repeated identical inputs can have separate
dataset IDs and versions while sharing the same hash.

Database triggers reject inserts, updates and deletes against a published
dataset's records. `load_candles()` and `load_funding()` verify hashes and row
counts before returning data. `dataset_records(id, limit, after)` provides an
ascending, cursor-based read; call `verify_dataset(id)` before using that lower
level method for research. Back up the whole SQLite database with a consistent
SQLite backup/snapshot, including catalog tables; copying only a running main
database file can omit WAL state.

## Funding and risk inputs

`get_derivative_snapshot` returns mark/index prices with **separate exchange
timestamps**, current forecast funding rate, the next announced settlement
times, most recent settled rate/state, and instrument rules.
`get_market_snapshot` adds last/bid/ask and a current isolated-margin tier
snapshot for execution consumers. Spot snapshots return mark=last, no index,
no funding, and an empty tier list. A recent quote does not refresh an old mark.

Historical funding uses **`realizedRate`**, never the forecast `fundingRate`.
The schedule can change; use reported settlement timestamps and announced
`fundingTime` / `nextFundingTime`. Tiers are current snapshots in **contracts**,
not historical liquidation policies. Shared tier boundaries are conservatively
flagged; model equality conventions are application policies, not guarantees
about an undocumented venue edge. These endpoints are inputs for a model,
not an exact cross-margin or portfolio-margin liquidation oracle.
[Official funding and tier fields](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate).

Research funding datasets preserve `mark_price=None`. Join a suitable historical
mark series explicitly. For forward paper accounting,
`await funding_history(inst_id, start, end, source)` returns the settled events
enriched with the **open of the confirmed 1-minute mark candle at exactly the
funding timestamp**:

```json
{
  "ts": 1767225600000,
  "rate": "0.0001",
  "mark_price": "60000",
  "mark_ts": 1767225600000,
  "mark_price_source": "historical_mark_1m_open_approximation",
  "funding_dataset_id": "..."
}
```

This is a declared bar-level settlement approximation. It is not the exact
price used in a venue's account bill. A missing, unconfirmed or differently
timestamped mark fails with `missing_settlement_mark`; the service never uses
a current mark, future close or nearest earlier candle. Exact marks are cached
persistently by source/region/instrument/settlement timestamp. Repeated requests
for an already completed identical funding range reuse its dataset. Schedule
calls only for newly relevant settlement windows to avoid unnecessary history
downloads; a current rate must never settle historical positions.

## Attributed imports

Older funding history may be supplied by a licensed archive or a user's own
records. `await import_dataset(inst_id, kind, bar, start, end, source, records,
provenance)` publishes a **new manifest**. No API failure triggers an import.
The `source` partition still identifies the modeled market family, while
`transport=user_import` and mandatory `provenance.provider` disclose how the
records entered the catalog. Consumers must not equate these records with an
exchange-authenticated download.

Candles use normalized `ts/open/high/low/close/volume/confirmed` fields, with
`confirmed=true` mandatory. Funding records use `ts/rate/mark_price`; funding
provenance must declare `rate_kind="realized"`. An optional supplied mark needs
`mark_ts == ts` and `mark_price_source`. Finite decimal, OHLC, positive price,
nonnegative volume, alignment, instrument and range checks run before publishing.
Conflicting duplicate timestamps reject the import. Attribution is capped at
16 KiB, records at 250,000; the HTTP layer must also limit bytes and authorize
uploads. The catalog accepts parsed JSON records; CSV parsing belongs to the
ingestion client.

Funding completeness cannot be established from a sparse list alone. To assert
it, provenance must contain `coverage={"start": start, "end": end,
"complete": true}`. The manifest explicitly reports
`coverage_method="importer_declared"`; this is the provider's assertion, not
independent verification. Imports without it remain degraded. Records,
attribution and job identity commit atomically; recovery after interruption
publishes that import and never appends records fetched from OKX.

## Watch health and operating limits

`start_polling(symbols, source, interval_seconds)` runs a real asynchronous REST
watch, with 1–20 instruments and a 1–300 second configured interval.
`poll_once` is available to a supervisor; `stop_polling` cancels the watch.
`health` reports actual worker activity, source/region, last attempt and success,
consecutive failures, last error, round-trip duration, persisted last good
snapshot and individual timestamp ages. A failure retains the last good data
and reports degradation. Exchange timestamps cannot regress.

Health price budgets are 30 seconds for mark/index/spot quotes and 180 seconds
for forecast funding observations, with a five-second future-clock tolerance.
The differing budget accommodates the provider's documented 30–90 second
funding channel cadence. These are explicit application policies; order risk
checks may impose tighter requirements. Synthetic data remains labeled
`synthetic` even when its timestamp is old. A fresh stored sample is distinct
from an active watch (`watch_active` / top-level `polling`).
[Official funding channel cadence](https://my.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel).

This implementation provides no WebSocket connection, depth replay, automatic
gap repair, multi-region failover or distributed rate limiter. Its bounded
SQLite workspace has one writer, and large imports/publication can hold that
writer; benchmark representative workloads before claiming a trading latency
SLO. Split large datasets rather than treating the row ceiling as a performance
promise. A deployment with many desks or workers requires measured storage,
egress-limit coordination and operating procedures; changing database names
alone does not supply them. Before commercial data redistribution, review the
[data rights requirements](okx-integration.md#data-rights-and-commercial-scope).

## Verification evidence

The offline suite covers cursor recovery, crash during publication, concurrent
claims, cancellation of in-flight pages, pagination stalls, missing and
unconfirmed history, settled-rate/forecast separation, exact-time funding mark
joins, imported coverage claims, source/region isolation and hash tampering.
It uses mocked transport and contains no captured public datasets.

An additional public, credential-free smoke on 2026-10-08 checked BTC spot and
USDT-perpetual rules, current mark/index/funding, 99 current tiers, 48 consecutive
hourly trade/mark/index bars and six actual settlements. All four downloads
completed with no gaps and verified hashes; all settlement marks matched the
event timestamps. This confirms those requests at that time, not continued
availability or a commercial operating SLA. Fetched data lived in a temporary
directory and was not added to the repository.
