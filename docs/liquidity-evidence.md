# Public depth, execution costs and capacity evidence

Tidebench can capture a public OKX order book, walk both sides at declared order sizes, and freeze a review of a specific observation window. The result describes displayed liquidity. It does not certify an executable price, a live fill model, or the capacity of a historical strategy.

## An inspectable observation

`POST /api/v1/pro/research/liquidity/captures` accepts `inst_id` and `depth` (1–400, default 400). It requests public instrument metadata followed by `/api/v5/market/books`; neither call needs exchange credentials. Each immutable capture retains:

- The canonical decoded OKX data arrays for depth and instrument metadata, with separate SHA-256 hashes. The transport abstraction exposes decoded `data`, so this is explicitly **not a hash of HTTP wire bytes**.
- Request start, metadata receipt, book request and final receipt timestamps, wall-clock nanoseconds, and elapsed monotonic nanoseconds. Elapsed time includes any throttling, retry or host fallback in the market client.
- `known_at`, the time the application received the capture. A venue timestamp ahead of receipt is an unsupported clock condition and cannot pass a review.
- A content hash covering identity, request, provenance, raw data and derived evidence. Update/delete triggers prohibit rewriting captures or reports; reads verify hashes and indexed identity. A report and its referenced captures are verified in one SQLite read snapshot.

[OKX's market documentation](https://app.okx.com/docs-v5/en/) describes independent server caches, so a subsequent REST request may return an older book. Capturing a response never makes it new economic information. Repeated exchange timestamps do not increase the independent sample count or observation span; timestamp regressions block approval.

## What the arithmetic means

The fixed scenarios are 1,000, 2,500, 10,000 and 100,000 USDT. The declared operational baseline is a 2,500 USDT child order and a 10,000 USDT sleeve at low or medium frequency.

For spot, displayed quantity is in base-asset units. For a supported linear USDT perpetual, displayed quantity is contracts and base quantity is `contracts × ctVal × ctMult`, provided `ctValCcy` is the underlying asset. Inverse or otherwise ambiguous units are retained as unsupported evidence. These units follow [OKX's instrument fields](https://www.okx.com/docs-v5/en/) and [its derivatives sizing explanation](https://www.okx.com/en-us/help/how-can-i-do-derivatives-trading-with-the-jupyter-notebook).

Each dollar scenario is converted to quantity at the snapshot mid, then rounded **up** to the instrument's lot and minimum size. This means a passing scenario actually covers at least the stated dollar amount. Buy and sell walks use that same base quantity. VWAP is matched quote value divided by matched base quantity; shortfall is adverse deviation from mid in basis points. Fees are excluded and remain a separate cost assumption.

The walk consumes only captured levels. If depth ends, the unmatched quantity stays unmatched: `depth_exhausted` cannot borrow the final level's price to fill unlimited size. Captured-depth participation is matched quantity divided by the captured side's displayed quantity. It is a share of these levels, not a share of total venue volume or ADV. Misordered, duplicate, crossed, locked, nonpositive or tick/lot-misaligned levels cannot establish capacity.

## A frozen review, not a general capacity guarantee

`POST /api/v1/pro/research/liquidity/calibrations` freezes selected capture IDs and hashes, their declared/observed windows, the input hash and the exact thresholds. One report covers one market. It retains the original selected IDs, then explicitly expands to **every already captured observation of that market in the declared window**. `selection_audit` lists the available count and omitted IDs that were automatically included; a favorable selected subset cannot conceal a bad book within that window. Windows exceeding 512 available captures must be narrowed.

Default temporal gates require at least 12 distinct exchange book timestamps, at least five minutes between independent observations, gaps no longer than one minute, book age at capture no greater than two seconds, and no timestamp regression. The first and last independent observations must also lie within one maximum gap of the declared window boundaries: a five-minute sample cannot pass an otherwise unobserved day. `uncovered_edges` records both missing edge durations. Default cost gates are at most 10 bps mid shortfall and at most 10% participation in captured side depth. Every selected snapshot and both sides must pass for a size to pass. Nearest-rank median/P90/P95/worst figures describe the selected sample; the approval rule uses every sample, not a favorable quantile.

No samples, unknown contract units, insufficient time, sparse sampling, stale captures or one adverse side produce explicit negative/inconclusive states. Current review status becomes stale when the latest independent observation exceeds the frozen review age threshold (five minutes by default). The historical report hash and its original result remain unchanged.

The largest passing size is limited to that observed window and chosen assumptions. Hidden liquidity, queue priority, replenishment, RPI liquidity, volatility shocks, endogenous market impact and execution fees are outside this measurement. A present-day book cannot validate years of backtest costs. Release review displays actual current reports with their sample counts, windows and hashes; it preserves `historical_capacity = unknown` and `live_execution_calibration = not_measured`.

## Collect and reproduce

Operations → **Cost and depth** supports actual public capture, raw/metadata inspection, frozen window review and prior report inspection. Only researchers, traders or administrators may capture/freeze; viewers can inspect. CSRF and authenticated workspace access still apply to these durable writes.

For a repeatable six-market window in an isolated directory:

```sh
uv run python scripts/capture_liquidity.py --output /tmp/tidebench-public-depth-window
```

The default twelve rounds, thirty seconds apart, take about 5.5 minutes. BTC/ETH/SOL spot and USDT swaps are observed independently. Exact raw payloads, the immutable SQLite database, report JSON and an implementation fingerprint remain local. The collector reports real failures rather than filling a missing window with synthetic samples. Establish redistribution rights before publishing venue raw data; a public verification summary can retain counts, identities and hashes without those rows.

## Local paper comparisons

`GET /api/v1/pro/research/liquidity/paper-comparison?inst_id=...` compares local synthetic paper orders with captures already known before their fill and at most two seconds old by both receipt and venue timestamp. A future capture is never backfilled as evidence. Missing matches remain `no_causal_capture`.

The output separates paper-price shortfall from a finite displayed-depth walk. Their difference also includes intervening price movement, so it is descriptive model comparison, not an estimate of live venue slippage or realized live execution quality. No exchange order is submitted.
