# Professional research and simulation model

Tidebench separates a saved experiment from its execution model. A result contains the complete input snapshot, configuration, data provenance, deterministic hash, costs, decisions, orders, fills, funding settlements, liquidations and completed round trips. `replay_research_snapshot` reconstructs the domain inputs without downloading replacement market history.

The implementation is a bar-level research model and a persistent local simulation book. Neither sends exchange orders. A reproducible result is evidence about the specified model, not proof of trading profitability or a replica of exchange matching.

## Package handoff and replay evidence

A ready [research package](data-packages.md) binds the complete trade/mark/funding version set, UTC window and captured settlement marks. The run API rejects a mismatched package hash, component ID or window. The full package manifest and enriched funding observations enter the run snapshot; research does not replace them with later downloaded marks. Raw version selection remains available for deliberate experiments.

The manifest records installed research-module SHA-256 hashes, their combined code fingerprint, application/Python versions and the Decimal context. Every completed result receives a canonical full-result SHA-256. A replay captures the original expected hash before execution, computes from saved inputs and sets `replay_verified=true` only on exact equality; divergence fails the run and retains the expected/observed evidence. This is behavioral reproduction, not a signed attestation or a promise that a different engine version has identical semantics. Run history reads materialized summaries through stable keyset pagination, keeping full inputs and large result arrays on detail/export paths.

## Instruments and units

- Spot research is long-only, USDT-quoted, without borrowing or leverage. Spot simulation sells require existing inventory.
- Perpetual research supports `long_only`, `short_only` and `long_short`, with one signed, isolated net position per experiment. The simulation book separates positions by source and instrument.
- Perpetual quantity is **contracts**. Base quantity is `contracts × ctVal × ctMult`; price and settlement are USDT. Current support requires linear, base-valued, USDT-settled contracts and a verified unit multiplier (`ctMult = 1`). Unsupported multipliers are rejected.
- Catalog lot/minimum quantities are not base-currency quantities. For example, a contract face value of 0.01 BTC and a quantity of 0.01 contracts represents 0.0001 BTC.
- Instruments must be live. Price ticks, contract lots and minimum sizes are enforced. Spot and perpetual quantities never share an implicit unit conversion.

These unit distinctions follow the venue's public instrument and contract definitions. Tidebench retains catalog metadata rather than inferring a perpetual's face value from its symbol. [OKX API documentation](https://www.okx.com/docs-v5/en/)

## Decisions, execution and attribution

Signals read confirmed trade-bar closes. Orders execute at the **following trade-bar open**, after adverse slippage and adverse tick rounding. The last bar's decision cannot create a future fill. An experiment starts flat with its configured cash and leaves final inventory marked to market; it does not invent a final closing order.

The shared `directional_signal` policy returns `+1` for long, `-1` for short, `0` for flat and `None` for warmup or retained inventory:

| Strategy | Positive regime | Negative regime | Retain / flat |
| --- | --- | --- | --- |
| SMA | Fast average above slow | Fast below slow | Equality is flat |
| RSI reversion | Wilder RSI below entry | RSI above exit | Middle region retains inventory |
| Buy and hold | Long, unless short-only | Short in short-only | No repeated weight rebalance |
| Closing channel | Above prior closing-price channel | Below prior channel | Inside retains |
| Z-score | Below negative entry threshold | Above positive entry threshold | Central band flat |
| Program | First matching +1 rule | First matching −1 rule | Rule 0 exits; no match retains |

`long_only` maps the negative regime to flat; `short_only` maps the positive regime to flat. A reversal closes existing inventory before opening the opposite side. Allocation applies when entering a regime, not as an instruction to rebalance on every bar. Zero allocation or an order below minimum size creates an attributed skipped order.

Forward deployment requires a flat market and no pending orders at admission. It does not adopt existing manual or stopped-strategy inventory. Once running, a strategy can reverse its own position with the durable close/open phases below. Additional closing-channel, Z-score and bounded program policies, exits, sizing and release workflows are specified in [strategy workflows](strategy-workflows.md). Reference policies do not establish alpha.

Every fill references an order and, when applicable, the originating closed-bar signal. Funding and risk liquidations have their own attribution and phase labels. Repeated entry fees, slippage, closing costs and remaining open inventory stay visible in the result.

This is a full-fill market-order approximation. OHLCV cannot establish market depth, queue priority, partial fills, participation capacity, spread history or market impact. Configured cost sensitivity is useful but does not replace those observations.

## Isolated collateral and risk

For signed contracts `q`, base quantity `b`, entry price `p0`, mark `p`, isolated collateral `m` and effective closing-fee reserve `f`:

```text
b                    = q × ctVal × ctMult
signed unrealized PnL = b × (p − p0)
mark notional         = abs(b) × p
isolated equity       = m + signed unrealized PnL
maintenance           = mark notional × tier.mmr
liquidation trigger   = isolated equity <= maintenance + mark notional × f
```

The effective reserve `f` is the normal trading fee plus the configured additional liquidation allowance. Liquidation fills charge both. The additional allowance is a configurable stress assumption, not a claim to reproduce an account's actual venue fee tier or clearance charge.

Initial collateral is execution notional divided by leverage. Entry size includes both collateral and the trading fee in the available-cash budget, then rounds down to the contract lot. Both the tier's initial-margin ratio and maximum leverage must permit the position.

Maintenance tiers must be ascending and contiguous from zero. The engine uses an explicit `(min_contracts, max_contracts]` convention. Shared equality boundaries are a reproducible engine convention; the public API's raw bounds do not establish that equality rule. A quantity above supplied coverage is rejected. Current tier snapshots remain **current-snapshot scenarios** when applied to old prices; they do not become historical tier schedules.

The venue uses mark risk and tiered maintenance rules, and its liquidation process can involve order cancellation and partial position reduction. Tidebench implements a simpler, disclosed full-position scenario. [OKX maintenance and liquidation rules](https://www.okx.com/en-us/help/v-tiered-maintenance-margin-ratio-rules)

## Bar risk ordering

Perpetual research requires a separate, confirmed, contiguous mark OHLC dataset covering every selected trade bar. Trade prices are not silently substituted for independent marks.

For each bar, the model processes:

1. Funding exactly at the opening boundary for inventory carried into that boundary.
2. Mark-open gap risk for the carried position.
3. Pending strategy exits and entries at the trade open, followed by immediate mark-risk checks.
4. Intrabar funding settlements in observed timestamp order.
5. The adverse mark excursion: low for a long, high for a short.
6. Closing mark valuation and the next closed-bar decision.

If an adverse range crosses the continuous liquidation threshold, the full position closes at that threshold, followed by adverse execution costs. A gap beyond the threshold uses the gap mark. Extrema have no observed timestamp; adverse-excursion events are labeled with the bar's end and their scenario phase, not presented as observed exchange execution timestamps.

The ordering of an intrabar funding event relative to an OHLC extreme is unknowable from bars alone. The chosen scenario places intrabar funding before the adverse excursion. Results disclose this ambiguity. A tick-level or higher-frequency sensitivity analysis is needed when that ordering changes a conclusion.

An isolated deficit preserves other free cash, records an insurance liability and deducts that liability from net equity. It halts further entries. Net equity can become negative; the model does not add an assumed insurance payout to improve a backtest. This liability policy is a conservative simulation assumption, not a statement about a customer's legal recourse or a venue's protection scheme.

## Funding and historical coverage

Funding uses the supplied **realized** rate and actual settlement timestamp. It never multiplies a predicted current rate across an assumed eight-hour schedule. Positive rates debit longs and credit shorts; negative rates reverse that direction:

```text
funding payment = signed base quantity × settlement mark × realized rate
```

A positive payment reduces isolated collateral. Funding can itself trigger liquidation.

The venue exposes realized funding history separately from current funding information, and historical availability is limited. Catalog completeness is established by pagination across the requested start, not by counting a presumed number of settlements. Exchange-source research rejects missing, incomplete or insufficient funding-coverage manifests. User imports retain provider attribution and importer-declared coverage rather than being labeled exchange-verified. [OKX funding history API](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)

An event can supply a historical mark at its own timestamp. Otherwise the engine requires a mark-bar open at that exact timestamp. A one-minute mark open is explicitly labeled a **historical bar approximation**, not the venue account's exact funding settlement price. A later close, nearby bar or current mark is not substituted. Formula/method labels, mark source and mark timestamp survive saved snapshots and replay.

Persistent simulation also retains `expected_funding_time` in position metadata. A known due settlement cannot be erased when the current funding endpoint rolls forward. Until its realized historical event is available and booked, inventory changes are blocked. Booking is transactional and unique by source, instrument and settlement timestamp; conflicting revisions require reconciliation rather than silently rewriting the journal.

## Research plans and selection boundaries

| Mode | Implemented behavior | Interpretation |
| --- | --- | --- |
| `single` | One saved configuration and date window | One model experiment |
| `grid` | Bounded Cartesian strategy grid and comparison | Descriptive in-sample ranking |
| `cost_stress` | Fixed strategy, fee/slippage matrix | Execution-cost sensitivity |
| `train_test` | Training candidates, optional purge gap, selected candidate on a later test window | Independent out-of-sample test |
| `walk_forward` | Rolling training and non-overlapping test windows | Distribution of independent out-of-sample folds |

Selection ranks training total return, then drawdown, then deterministic experiment identity. Test prices cannot change the selected training parameters. Each test starts with fresh configured cash; fold metrics are reported independently rather than stitched into a fictional compounded portfolio.

Date windows include only complete bars. The API captures up to 2,000 preceding bars for indicator warmup and limits selected windows to 98,000 bars. Wilder RSI retains a seed dependence; bounded warmup is not claimed to equal an infinite-history indicator. The captured warmup is part of the model manifest. Earlier observed prices may warm causal indicators; no inventory or pending order carries into a new test account. Purge bars separate training and test trading windows. These simple close-based strategies do not estimate overlapping future labels, so the gap is a chronological exclusion, not a claim to implement purged machine-learning cross-validation.

The service limits parameter grids to 64 candidates, walk-forward to 20 folds, cost matrices to 25 cells, worker threads to four and total work to 2,000,000 input bar-cases. A dataset is capped at 100,000 bars. Invalid parameter combinations fail before execution. Worker count does not change experiment ordering or economic results.

Trying many strategies and reporting the best outcome increases selection bias. Holdout windows and walk-forward results do not establish statistical significance or eliminate that bias. Tidebench does not claim to calculate PBO, deflated Sharpe or confidence intervals it has not implemented. [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

## Metric policy

| Metric | Definition / sample policy |
| --- | --- |
| Total return | Final net equity / initial cash − 1; includes fees, funding and recorded liabilities |
| Drawdown | Decline from the high-water bar-close equity observation, starting with initial cash; does not reconstruct intrabar unrealized drawdown; can exceed 100% after a deficit |
| Benchmark | Cost-adjusted, unlevered trade-price buy-and-hold proxy, entering at the second selected trade-bar open; for SWAP it is a hypothetical base-price curve, not independently observed spot returns; no perpetual funding |
| Sharpe | Mean daily return / sample standard deviation × √365; zero risk-free rate |
| Sortino | Mean daily return / root mean squared downside return × √365; zero downside target |
| Annualized volatility | Sample standard deviation of daily returns × √365 |
| CAGR / Calmar | Available after at least 365 complete UTC daily observations; Calmar divides annualized return by maximum drawdown |
| Turnover | Sum of absolute executed notionals / initial cash |
| Exposure | Fraction of selected bar closes with nonzero inventory, not inferred intrabar holding time |
| Profit factor | Sum of positive completed net round-trip PnL / absolute negative completed net round-trip PnL |
| Win rate | Positive completed round trips / completed round trips; sample count and warning below 30 |

Sharpe, Sortino and annualized volatility require at least **30 complete UTC daily returns**. Zero variance, missing downside variation and nonpositive equity have explicit null reasons. Partial first/last days are excluded. Daily aggregation requires a bar interval that divides a UTC day. CAGR, Calmar, profit factor and win rate also expose undefined or insufficient-sample reasons rather than invented numeric values.

Open-position PnL is marked to market but does not count as a completed round trip. Research round-trip net PnL includes entry/exit costs and signed funding. Persistent book metrics explicitly distinguish trading realized PnL, fees and funding; spot recognizes proportional entry costs on disposal, while perpetual trading fees are recognized when charged.

## Precision and persistent journal

Trading arithmetic uses an independent 50-significant-digit, half-even Decimal context. Caller precision, rounding, exponent settings and traps do not change results. Supported ledger values have absolute magnitude below `1e30`; impossible lot/tick resolution and out-of-domain paths fail closed with an explanatory error. This is finite precision, not unlimited mathematical accuracy.

Research fills record `cash_debit` or `cash_credit`. Replaying those amounts in fill order under the fixed context must reproduce every stored cash balance exactly. Regrouping already rounded PnL algebra can differ at the last decimal place and is not the cash-replay contract.

The persistent book journals each asset independently. Spot inventory balances in its base asset; cash, collateral, fees, derivative PnL, funding and liabilities balance in USDT. Actual persisted cash deltas are recorded. Finite-context residuals within the proven operand-ULP bound appear in a separate `rounding_adjustment` account; material imbalances roll back the transaction. Extra precision is used only to take exact differences of finite stored decimals and validate journal balance, not to alter a trading balance.

Idempotency, position changes, cash, liabilities, journal entries, order state, audit and strategy cursor updates commit together. Forward reversals persist separate close/open command identities and a durable signal intent. A pending intent can resume within its bar after a committed phase; once a newer bar is available, the latest decision supersedes an older pending intent. A stopped deployment cannot resume either phase. Changing risk limits reads the latest policy inside that same writer gate, so a concurrent limit edit cannot clear a halt. Exits may reduce risk while halted; leverage increases, oversells, insufficient cash and stale or missing independent marks are rejected.

This remains a single-workspace, single-writer local deployment architecture. Its transaction and restart tests are evidence for those deployment assumptions, not for an untested distributed or live brokerage system.

## Verification entry points

```sh
uv run pytest tests/test_derivatives.py tests/test_pro_research.py tests/test_pro_execution.py tests/test_pro_service.py
```

These suites exercise contract units, tier boundaries, funding signs and historical marks, next-open causality, final unfilled signals, long/short reversal, mark-only risk, gap deficits, strict cash replay, caller-context isolation, train-only selection, work bounds, native-asset journal balance, partial-cost conservation, concurrent idempotency, reservations, halt behavior, rollback and persistent settlement state.

Runtime/API acceptance tests separately cover saved dataset workflows, durable jobs, replay, authentication and deployment coordination. Passing domain tests does not validate order-book fill realism, account-specific exchange charges, historical tier reconstruction or exchange live execution.
