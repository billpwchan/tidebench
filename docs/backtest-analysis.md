# Backtest analysis: an evidence workflow

Tidebench's single-strategy research desk separates the question being developed, the captured financial result, and the next editable trial. A selected result uses a full-width analytical surface. Its saved market, costs, window and configuration remain inspectable in a compact expandable context; viewing another result never silently changes the draft.

## Decide whether the result deserves further research

The first view shows net return, the passive reference, maximum drawdown, daily Sharpe and **completed trades**, followed by synchronized capital and underwater charts. Returns normalize against recorded starting capital rather than the first sampled equity. All/1M/3M/1Y controls, crosshair readings and keyboard observation inspection preserve the selected range when switching equity/returns. Zero or negative equity remains visible.

Six views answer different research questions:

| View | Question and recorded evidence |
|---|---|
| Performance | What happened to capital? Strategy/reference curves, linked drawdown, Sortino, volatility, exposure, executed-notional turnover and completed-trade statistics |
| Return calendar | When did it work? UTC monthly map, partial-period labels, daily observations, median/best/worst returns and empirical tails |
| Risk & recovery | How long did losses persist? Deepest episodes, peak/trough/recovery dates, underwater duration, unrecovered episodes and rolling 30-day risk |
| Trade analysis | Which trades produced it? Net-P&L distribution, winning-trade concentration, holding duration, sortable completed-position ledger and filtered CSV |
| Robustness | How sensitive is it? Chronological test-window statistics, exact fold navigation and cost/parameter candidates; multidimensional grids retain every candidate |
| Economics | Does it reconcile? Total equity P&L, completed net P&L, unclosed economic residual, recorded fees, signed funding, modeled execution-price impact and gross leverage |

The evaluation preview explains proposed candidate count, training/test duration and purge separation before submission. Short test samples explicitly explain missing annualized statistics. The study ledger supports search/status filtering; result analysis, next-trial draft and history have direct navigation.

## Interpretation contracts

- A development or training result is not independent validation. An OOS window is chronological, but prior use can contaminate it; inspect the existing holdout and trial-use records before calling it untouched.
- Every walk-forward test fold starts flat with its own account. Fold returns are not compounded into a fabricated investable curve. Candidate counts cover the captured run, not an unknown history of prior attempts.
- The passive reference is cost-adjusted, unlevered buy-and-hold. It may have different allocation/exposure; the perpetual reference uses underlying trade prices without funding. The return difference in percentage points is descriptive, not risk-adjusted alpha.
- Sharpe, Sortino and annualized volatility require at least 30 complete UTC daily observations. CAGR and Calmar require 365. Nonpositive equity suppresses incompatible ratios. Missing values retain their reason; no infinite profit factor is invented.
- Calendar returns use observed boundary equity. Partial periods are marked, and missing months are not painted as zero returns. Historical 5th/95th percentiles require 30 complete days; the worst-5% average requires 100 and is not a forecast of VaR or expected shortfall.
- Episode depth measures the worst trough; terminal drawdown comes from the final recorded close. An unrecovered episode is right-censored, not assumed to recover. Bar-close risk can miss intrabar losses.
- Trade statistics use completed positions, not fill count. Winning-trade concentration uses gross winning net P&L as its denominator. Small-sample warnings stay visible.
- Fees/funding are already inside final equity and net trade P&L. The unclosed residual includes open-entry costs, funding and mark-to-market effects; it is not pure unrealized P&L. Execution-price impact includes modeled slippage and tick rounding and is not another deduction. Unsupported separate slippage attribution remains null.

## Derived API and resource bounds

`GET /api/v1/pro/research/runs/{id}/diagnostics?variant=...` selects an exact captured candidate (`single`, `experiment-2`, `fold-1:test`, or `fold-1:experiment-2`). Unknown variants return 404; incomplete runs return 409. One diagnostic derivation can run per process; concurrent requests receive a retryable 429, and the slot remains owned until the worker thread actually finishes, including after client disconnect. All authenticated readers can inspect the report; it creates no run, modifies no financial artifact and grants no execution authority.

The runtime validates the original result hash before deriving diagnostics. Shared candidate inputs, contract multipliers and interval come from the result-hashed artifact, not an unchecked outer snapshot. Response identity includes run/source/variant, original `result_hash`, `diagnostics_version` and a separate report-code SHA-256. The client rejects identity mismatches. Existing replay remains a financial-artifact equality check; opening a new report does not rewrite history.

Amounts remain decimal strings. Nullable ratios carry unavailability reasons. Maximum financial-path input is 100,000 observations. Daily/monthly/yearly calendar arrays retain the latest 400 rows **per frequency**; all-sample distribution statistics still use all eligible observations. Rolling risk retains at most 400 evenly sampled eligible windows, and drawdown tables retain the ten deepest episodes. Counts/omissions are explicit in the response and visible tables. Daily CSV exports the displayed subset; the captured financial JSON contains the full path. The derived JSON export includes its method and input identity.

This analysis layer does not add order-book queue simulation, historical fee-tier reconstruction, structural-break forecasts, selection-adjusted significance or institutional certification. Those claims cannot be created by presenting more metrics. Existing public strategy counterevidence remains part of research review.

## Research references and design decisions

Primary sources informed the workflow, with each product's features distinguished from Tidebench's implementation:

- [QuantConnect backtest reports](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/report) and [results](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results): multiple diagnostic views, monthly returns, drawdowns and inspectable trade records informed the six-view desk. No unsupported rolling beta was copied into a non-risk-matched benchmark comparison.
- [TradingView strategy reports](https://www.tradingview.com/support/solutions/43000764138-tradingview-strategy-report-how-to-start/) and [strategy execution documentation](https://www.tradingview.com/pine-script-docs/concepts/strategies/): analysis should connect performance to individual trades and disclose simulation assumptions. Tidebench retains its next-open engine semantics rather than implying tick replay.
- [CFA Institute: Backtesting and simulation](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/backtesting-and-simulation): chronological validation, biased inputs and structural change inform the distinction between development evidence and a truly untouched holdout.
- [Lo, The Statistics of Sharpe Ratios](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios): square-root annualization does not remove serial dependence; the displayed ratios are descriptive.
- [GIPS asset-owner handbook](https://www.gipsstandards.org/standards/gips-standards-for-asset-owners/gips-standards-handbook-for-asset-owners/): calendar consistency and geometric boundary returns inform the zero-external-flow diagnostics. Tidebench makes no GIPS compliance claim.

Fresh reviewers independently challenged the financial integration and trader workflow. Three reproduced UI defects were corrected: maximum episode depth mislabelled as terminal drawdown; additional parameter dimensions silently merged in a two-axis map; and capped calendar/rolling arrays exported without explaining omissions. Tests bind diagnostics to verified shared inputs, exact variants, native perpetual contract units, unchanged financial artifacts and read-only authorization.
