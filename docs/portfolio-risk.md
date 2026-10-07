# Captured portfolio risk

Tidebench's portfolio analytics is a **read-only, deterministic price-shock model** over the actual local simulation book. It reports spot and linear USDT-perpetual exposure together, while checking each isolated perpetual position separately. It submits no orders and changes no account, journal, strategy cursor or funding record.

A result is a captured hypothesis about prices, costs and current maintenance tiers. It is not historical VaR, a loss probability, a portfolio-margin calculation or a prediction of exchange liquidation execution.

## Inputs and capture

```python
from decimal import Decimal
from tidebench.portfolio_analytics import PriceShock, analyze_portfolio

result = analyze_portfolio(
    book.account(source, snapshots),
    snapshots,
    scenarios=[PriceShock("BTC down 20%", asset_pct={"BTC": Decimal("-20")})],
    fee_bps=Decimal(risk["fee_bps"]),
    liquidation_fee_bps=Decimal(risk["liquidation_fee_bps"]),
    slippage_bps=Decimal(risk["slippage_bps"]),
    as_of_ms=captured_at,
)
```

The caller captures the book and source-matched market snapshots together, and supplies the actual simulation risk configuration. The domain function performs no network or database reads. The default six scenarios are parallel `−20%, −10%, −5%, +5%, +10%, +20%` changes. Its default slippage is zero; integrations should pass their configured assumption explicitly.

Current snapshots must match the account's `example` or `okx` source and each instrument identifier. Held and current metadata must agree on instrument type, base, quote and perpetual contract units. Linear perpetual support requires USDT settlement, a base-valued face value and `ctMult = 1`. Quantity is **contracts**, not base currency. Numeric equality allows equivalent decimal representations of contract units.

The model independently recomputes valuation from these snapshots. It never substitutes `account.positions[].mark`, `market_value`, `maintenance_margin` or a saved liquidation estimate. A perpetual needs its own mark and mark timestamp; a trade last price cannot replace a missing perpetual mark. Spot uses its snapshot mark, or last only when mark is absent.

For `okx`, both snapshot and mark observations must be at most 15 seconds old by default, with at most five seconds of forward-clock tolerance. A supplied maintenance snapshot must match source, instrument, isolated mode and contract units, and be no older than 24 hours by default. If tiers are attached directly to the current snapshot, its identity/freshness establishes their capture; their independent observation time is unavailable. Historical `example` timestamps remain synthetic and do not pass as fresh exchange data.

A held suspended instrument can still have observable exposure. `tradable: false` preserves that distinction; a hypothetical full close does not assert that a venue would accept an order.

`input_snapshot` captures the account, relevant market snapshots, configuration, shocks and valuation instant. `captured_scenario` supplies a full SHA-256 input hash, a shortened display identifier, source, instant and model version. `replay_portfolio_snapshot(result["input_snapshot"])` uses exactly those saved inputs; it does not refresh prices. This hash identifies an input capture and is not a digital signature or a guarantee that a capture is authentic.

## Exposure and concentration

For signed perpetual contracts `q`, face value `v`, multiplier `c`, current mark `p`, entry price `p0` and isolated collateral `m`:

```text
signed base quantity b = q × v × c
signed notional        = b × p
perpetual mark PnL     = b × (p − p0)
isolated equity       = m + perpetual mark PnL
```

Spot base quantity is its inventory quantity, signed notional is positive inventory mark value, and its equity component is that value. Spot unrealized PnL requires the exact stored `basis` and equals mark value minus that basis; if basis is absent, this metric is null with a reason. A rounded average entry price or cached PnL is not used to reconstruct exact basis. Missing basis does not prevent exposure, equity or price-shock analysis. All portfolio amounts are **USDT**, with no USD conversion or stablecoin-depeg model.

For each market and base asset:

```text
long notional  = sum(max(signed notional, 0))
short notional = sum(max(−signed notional, 0))
gross notional = long notional + short notional
net notional   = long notional − short notional
```

Total net notional is a signed accounting aggregate, not a correlation-adjusted risk measure. A spot/perpetual hedge can reduce net exposure while retaining large gross exposure, basis risk and isolated liquidation risk. Base-asset groups expose those offsets explicitly.

An asset's gross share is its gross notional divided by total gross notional. `concentration_hhi` is the sum of squared fractional gross shares, between zero and one apart from finite Decimal rounding. `largest_asset_share_pct` uses percent units. An empty portfolio has zero notional and undefined concentration, rather than an invented concentration of zero.

```text
portfolio equity = cash + spot mark value + sum(isolated equity) − insurance liability
available cash   = cash − pending-order cash reservations
gross leverage   = gross notional / portfolio equity
net leverage     = net notional / portfolio equity
```

Reservations reduce available cash, not equity a second time. The kernel recomputes available cash rather than trusting the account's derived field. Ratios require complete, positive equity; otherwise they are null with an explicit reason. A reservation deficit can produce negative available cash and remains visible.

The result includes the book's reported equity, recomputed equity and their reconciliation residual. A difference is labeled `different_capture_or_rounding`, because a changed observation or finite-context addition ordering can cause a difference. It is not silently balanced into another account.

## Isolated maintenance and mark bankruptcy

Each perpetual selects a current tier by absolute **contract** quantity. Tiers must be ascending, contiguous from zero and cover the position. The model uses `(minimum, maximum]`; this is an explicit reproducibility convention, not a claim that the public API establishes equality handling at shared boundaries.

```text
N                  = abs(b) × p
maintenance        = N × tier.mmr
closing reserve    = N × (fee_bps + liquidation_fee_bps) / 10000
required           = maintenance + closing reserve
maintenance buffer = isolated equity − required
breach             = isolated equity <= required
mark bankruptcy    = isolated equity <= 0
```

The continuous liquidation estimate uses that fixed quantity/tier and closing reserve. Distance is `(mark − threshold) / mark` for longs and `(threshold − mark) / mark` for shorts, in percent. Negative distance indicates that the mark has already crossed the model threshold. Maintenance coverage is isolated equity divided by the requirement. Spot has no maintenance requirement in the supported unlevered model.

The additional liquidation allowance is a configurable simulation cost. It does not reproduce an account's actual fee tier or the venue's complete clearance schedule. OKX distinguishes position/account modes and can cancel orders, partially reduce positions or liquidate them; its current tier schedule can also change. This kernel models a full close of breached isolated positions using the captured current schedule. [OKX maintenance-margin and liquidation rules](https://www.okx.com/en-us/help/v-tiered-maintenance-margin-ratio-rules)

A persisted `expected_funding_time` at or before the real capture instant or snapshot instant is known due. Until the book reconciles that actual settlement, margin safety, bankruptcy flags and modeled liquidation settlement are unavailable. For synthetic examples the cutoff uses the synthetic snapshot instant. Price-only mark valuation remains calculable and explicitly conditional on the captured ledger. Absence of a known due cursor is not proof that every historical funding event was settled. The analytics function does not invent or apply a funding rate.

## Parallel price shocks and hypothetical settlement

For each position, its shocked price is `current mark × (1 + change_pct / 100)`. Override priority is **market → base asset → parallel**; overrides replace the fallback percentage and are not added. Spot and perpetual prices move from their own observed marks, retaining any starting basis difference. All changes occur simultaneously; no intrapath order or correlation is inferred.

The scenario first reports `pre_liquidation_equity` and its change from captured equity. Then each perpetual independently checks the shocked mark against its own isolated requirement. Surviving positions remain marked; spot inventory is not sold. Only a breached perpetual is hypothetically closed in full:

```text
execution price       = shocked mark with adverse configured slippage and current tick rounding
closing PnL           = b × (execution price − entry price)
closing costs         = abs(b) × execution price × (fee_bps + liquidation_fee_bps) / 10000
isolated net release  = margin + closing PnL − closing costs
cash release          = max(isolated net release, 0)
additional liability  = max(−isolated net release, 0)
```

Positive releases add to free cash. An isolated deficit becomes explicit additional insurance liability, preserving other free cash and isolated collateral; net equity deducts both old and additional liabilities. This is the local book's conservative accounting policy, not a promise about venue insurance coverage or a customer's legal liability.

`post_full_liquidation_equity` means equity **after the full close of triggered perpetual positions**, not liquidation of every asset. `bankruptcies` counts nonpositive pre-cost mark equity; a position can have positive mark equity but a settlement deficit after fees/slippage. The separate `incremental_liability` field captures that deficit. A sell that rounds to zero or another execution/domain failure makes settlement unavailable, rather than inventing a fill.

A zero-slippage scenario with a valid settlement preserves mark equity minus explicit closing costs, within finite-context arithmetic. Adverse execution reduces it further. A delta-neutral example demonstrates the distinction: four BTC spot plus a short of 400 contracts at 0.01 BTC/contract, both marked at 100, has gross notional 800 and net notional zero. With 40 isolated collateral, a 20% increase leaves pre-close portfolio equity unchanged, but the short has negative isolated equity. A 10 bps trading fee plus 50 bps additional allowance creates 42.88 of additional liability and 2.88 of costs; the unaffected cash balance stays unchanged.

These static hypotheses exclude cross/portfolio margin, order-book depth, partial liquidation ladders, ADL, insurance-fund execution, hypothetical future funding, pending-order fills, liquidity timing and historical probabilities. They are not a basis for claiming an observed or expected exchange fill.

## Availability, interface and limits

Every monetary amount, quantity, percentage and ratio is a JSON **decimal string**. Timestamps and counts are integers; conditions are booleans or null. Missing values remain null and display as unavailable, never zero. `status` is `available`, `partial` or `unavailable`, with structured `issues` identifying the cause and affected instrument/scenario.

A missing mark makes the affected exposure, complete portfolio totals, concentration and scenario equity unavailable. Known unrelated position values remain visible. Missing tiers preserve known exposure and price-only equity but make maintenance and post-liquidation settlement unavailable. Known pre-cost bankruptcy may still be calculable without tiers; complete liquidation counts remain null. `known_liquidations` is explicitly a partial count when any other position is unknown.

The application exposes `GET /api/v1/pro/execution/analytics?source=...` for default hypotheses and `POST /api/v1/pro/execution/analytics` for custom ones. Both are read-only and available to every authenticated role; cookie-authenticated POST requests still require CSRF. The runtime captures book/configuration inputs and supplies current source-matched snapshots. The POST body is:

```json
{
  "source": "example",
  "scenarios": [
    {
      "name": "BTC-specific stress",
      "parallel_pct": "-5",
      "asset_pct": {"BTC": "-20"},
      "market_pct": {"BTC-USDT-SWAP": "-25"}
    }
  ]
}
```

The response contains `summary`, `markets`, `assets`, `positions`, `scenarios`, `assumptions`, `captured_scenario` and `input_snapshot`. Each scenario retains its submitted percentages and per-position `shock_origin`, mark, shocked mark, equity component, PnL change, maintenance state and hypothetical settlement attribution. The HTTP runtime passes captured book/configuration inputs; request fields cannot override the book's actual cash, positions or liquidation policy.

Limits are 500 held positions, 25 uniquely named scenarios, 500 overrides per map and 200 supplied tiers per instrument. Percentage changes must be greater than −100% and at most +1000%; fees plus the additional liquidation allowance must be below 10000 bps, as must slippage. These are finite stress-model bounds, not implied probabilities.

Arithmetic uses an independent 50-significant-digit, half-even Decimal context, with absolute accounting values below `1e30`; input adjusted exponents below `−80` and excessively large decimal representations are rejected. Caller precision, rounding, traps and flags remain unchanged. Individual invalid data and aggregate overflow produce explicit unavailability; invalid configuration fails with `PortfolioAnalyticsError`. No finite-precision model provides unlimited numerical accuracy.

Tests cover real-book spot/perpetual hedges, contract units, long/short shocks, isolated deficit conservation, old liabilities, reservations, adverse ticks/slippage, maintenance equality and tier boundaries, missing/stale/foreign marks, due funding, metadata changes, bounded work, numeric-domain failures, replay, immutability and caller-context independence. Property tests verify spot mark valuation and isolated settlement conservation across signed exposures and stress sizes.
