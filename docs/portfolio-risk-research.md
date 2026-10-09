# Risk-budgeted momentum: specification and counterevidence

The useful question is whether a causal allocation rule improves the risk and cost tradeoff, not whether adding a covariance matrix creates alpha. This release adds a runnable shared-capital strategy, historical replay, managed paper decisions and an inspectable risk panel. It does **not** establish a profitable edge.

## Sources and design choices

- [Moreira & Muir, *Volatility Managed Portfolios*](https://www.nber.org/papers/w22208) motivate taking less risk when volatility is high. Their factor evidence is not a validation of this crypto rotation or its particular parameters.
- [Cederburg, O'Doherty, Wang & Yan, *On the performance of volatility-managed portfolios*](https://www.sciencedirect.com/science/article/pii/S0304405X2030132X) find no systematic direct outperformance and poor implementable out-of-sample combinations. This motivates reporting the unmanaged comparator rather than assuming a benefit.
- [Ledoit & Wolf, *Honey, I Shrunk the Sample Covariance Matrix*](https://ledoit.net/honey.pdf) describe covariance estimation error and shrinkage. Our **fixed 25% diagonal shrinkage** is a transparent regularization choice, **not** their fitted estimator or a replication of their results.
- [AQR, *Understanding Risk Parity*](https://www.aqr.com/-/media/AQR/Documents/Insights/White-Papers/Understanding-Risk-Parity.pdf) distinguishes capital weights from risk contributions. Our allocation is **capped inverse volatility**, not an equal-risk-contribution solution; correlated assets can have unequal and even negative modeled contributions.
- [OKX API documentation](https://www.okx.com/docs-v5/en/) defines candle opening timestamps and confirmation. Only confirmed, contiguous aligned closing observations known by the decision's close enter the risk model; execution uses a later open or quote.

## Frozen rule

The example and published primary case use BTC/ETH/SOL spot, 4H bars, a 40% account capital ceiling, daily rebalances (six bars), positive 42-bar momentum and at most two selected markets. Every leg is long-only, leverage one; each leg's `weight=.5` is a **ceiling relative to allocated capital**, not a risk budget. Linear USDT perpetual legs with leverage one are also supported by the application, but were not part of this public spot experiment.

For each decision at the close of bar `t`:

1. Use `N=84` simple close returns `r=C_t/C_(t-1)-1`; subtract their sample means; covariance uses denominator `N−1`. A complete `max(lookback,N)+1` close window is required for every declared market. No missing bars are forward-filled. Historical early warmup remains cash; managed missing/corrupt histories refuse a target batch.
2. Annualize using `A=sqrt(365*86400000/interval_ms)` (4H: `sqrt(2190)`). This is a scaling convention, not proof of independent returns or a forecast accuracy claim.
3. Exclude exact zero-variance assets. Floor remaining bar volatilities at `20%/A`. Estimate correlations from the unfloored sample covariance, then rescale them by floored volatilities.
4. Build `Σ=(1−λ)Σ_floored+λ diag(Σ_floored)`, `λ=.25`. Build a second PSD stress matrix with the same diagonal and every off-diagonal correlation `ρ=.75`.
5. Rank strictly positive 42-bar returns, with deterministic descending-symbol tie-breaking. Select up to `top_k=2` eligible markets. Normalize inverse volatilities over selected markets, then cap each at its leg ceiling. **Do not redistribute capped weight**; cash is a deliberate residual.
6. Compute annualized modeled and stressed sleeve volatilities. Apply a common scale `min(1,.20/max(σ_model,σ_stress))`; never scale up or grant leverage. After close-based exits and loss-budget caps, reapply the governor: removing a negatively correlated leg can increase variance.
7. Existing shared-capital execution reduces first, sizes from next-open/post-close prices and equity, rounds lots, scales additions to available cash and retains subminimum residuals. Multi-leg execution is sequential. Rebalances occur on the declared clock; this is not continuous hedging.

The per-decision evidence includes sample bounds, eligible and excluded markets, raw/floored asset volatility, covariance and stress matrices, inverse-volatility weights before the governor, final model weights, cash, scale, and Euler volatility contributions. Contributions sum to modeled sleeve volatility within finite Decimal precision; they are not P&L attribution. Allocation uses the same functions in research and managed paper, and the implementation fingerprint covers the risk module.

`vol_target_pct` refers to **allocated sleeve capital**. At `capital_pct=40`, a 20% sleeve target corresponds approximately to 8% account volatility **before execution**, assuming only this sleeve and cash. Costs, gaps, residual inventory, price drift between rebalances and changing covariance can exceed it. It is neither a VaR limit nor a realized-loss or volatility guarantee. Risk panels between scheduled rebalances show a model reference, while the recorded quantity targets identify retained inventory and protective reductions.

## Recompute under v0.11

The [v0.11 report](audit/v0.11.0-portfolio-risk-battery.json) recomputes every declared case from the same hash-verified raw public captures under core `ed52b8e`; the [v0.11 replay](audit/v0.11.0-portfolio-risk-replay.json) matches all 28 ordered input/result hashes. No data is silently refreshed and no winner is chosen after seeing the check windows. Risk momentum remains negative in A (−6.94% standard costs, −8.45% doubled costs); B falls from 2.73% to 0.169% under doubled costs. Corrected workflow accounting does not establish profitable edge.

## Recompute under v0.10

The [v0.10 full report](audit/v0.10.0-portfolio-risk-battery.json) and [exact replay](audit/v0.10.0-portfolio-risk-replay.json) re-evaluate the preserved raw public captures under the current `reduce_group_v2_allowance` accounting/execution implementation (`d941806` core), without relabeling the v0.9 results. All 28 ordered input/result hash pairs match; the [manifest](audit/v0.10.0-public-evidence-manifest.json) records numerical identity and all returns. The adverse window and cost-sensitive conclusion remain. Economic decomposition is now available in individual portfolio studies; the battery does not become an alpha acceptance because execution contracts improved.

## Declared experiment

[Full report](audit/v0.9.0-portfolio-risk-battery.json) and [replay metadata](audit/v0.9.0-portfolio-risk-replay.json) retain all cases and identities.

The plan was written before downloads/evaluation. Public OKX captures cover `[2024-10-08 08:00 UTC, 2026-10-08 08:00 UTC)`: 4,380 4H observations per market. The first half is available development/history; check A and B are adjacent six-month windows, independently initialized from flat inventory and 10,000 USDT, with 126 prior bars for indicator/risk warmup. They are **exploratory checks**, not statistically blinded holdouts, and are not independent trials. This standalone battery is not registered in a workspace project's trial ledger.

Seven fixed cases × two windows × two cost scenarios = **28**: static equal-weight basket; fixed-weight positive momentum; primary 84-return risk model; 42/126-return neighbors; zero-correlation-stress ablation; and inverse volatility with a 100% target that leaves the 20% governor inactive in these observations. All rotate on the same daily clock and account capital ceiling. Achieved exposure and risk differ deliberately; no full-sample rescaling is presented as executable risk matching. Both rotation comparators use identical .5 leg ceilings; the static comparator uses 1/3 each. No stops or loss budgets distinguish these cases.

Fees/slippage are 10/5 and 20/10 bps per fill. Current captured instrument rules and the selected current three-market universe are scenario assumptions; there is no historical universe completeness or liquidity-capacity claim. Positions remain marked at the window end, without forced liquidation, so an eventual final exit would add costs.

At doubled costs:

| Case | A net % | A drawdown % | B net % | B drawdown % |
|---|---:|---:|---:|---:|
| Static equal weight | −24.074 | 28.691 | 10.256 | 14.321 |
| Fixed momentum | −17.048 | 19.900 | 2.878 | 7.121 |
| Risk momentum, 84 | −8.453 | 9.799 | 0.169 | 5.392 |
| Risk window, 42 | −8.891 | 10.403 | −1.381 | 6.048 |
| Risk window, 126 | −8.791 | 10.096 | 0.026 | 5.187 |

In A, mean gross account exposure fell from fixed momentum's 18.24% to 7.80%; realized annualized account volatility fell from 13.84% to 5.89%. In B, mean exposure fell from 25.00% to 13.32%, but realized account volatility was **8.97%, above the approximate 8% planned account level**. Lower exposure explains part of lower drawdown; this comparison does not establish superior timing or risk-adjusted alpha.

Primary B net return falls from 2.733% at ordinary costs to 0.169% at doubled costs. Turnover at doubled costs is 16.75 times initial account equity, with 335.10 USDT fees; fixed momentum has 26.27 times turnover and 525.33 USDT fees. These are actual simulated quantities, not a linear fee deduction from a different path. Window and neighboring-estimator sensitivity remain material. The stress ablation offers only modest differences; two windows cannot validate the stress coefficient.

The engine records below-minimum rebalance deferrals alongside execution rejections. All such events remain in per-case diagnostic counts; none are silently treated as successful fills. Neither a small positive marked result nor lower modeled risk is a deployment recommendation.

## Reproduce

```sh
uv run python scripts/portfolio_risk_battery.py --source okx --days 730 \
  --end 1791446400000 --capture-dir data/portfolio-risk-battery-v9 \
  --output /tmp/risk-battery.json
uv run python scripts/portfolio_risk_battery.py --source okx --days 730 \
  --capture-dir data/portfolio-risk-battery-v9 \
  --replay-report /tmp/risk-battery.json --output /tmp/risk-replay.json
```

Replay verifies the captured dataset identities and uses the frozen instrument records; it makes no mutable venue requests. Plan, runner and installed implementation must match. All ordered case metrics and result/input hashes must agree. Public reports include normalized instrument metadata and hashes, not private databases or credentials. `--capture-report` reuses verified **raw data only**, and makes no claim of result replay.

## Self-challenge and acceptance boundaries

- An early report writer failed on Decimal serialization after the first computed case. The writer was corrected; strategy parameters and plan were not tuned. The final report adds code-level execution diagnostics and is recomputed on the same captures before replay.
- Tests cover an independent covariance oracle, inverse-volatility ratios, contribution reconciliation, future perturbation, caller precision, corrupted/gapped/unconfirmed history, zero-variance cash, ceilings without redistribution, stress binding and rechecking after a hedge removal.
- Native-book integration verifies causal sample bounds, flat warmup, frozen replay, managed verified bars and idempotent controller restart. Frozen holdout tests bind all risk fields and enforce full warmup with mutable catalog access disabled.
- Covariance work has a five-million-product admission budget in addition to existing bar-leg, process deadline, memory and artifact limits. Protective exits use a conservative every-bar work estimate. This budget is not a measured multi-tenant SLO.
- Still unvalidated: capacity/partial fills, historical dynamic membership, robust calibrated forecasts, independent long-duration forward performance and reliable net alpha. A profitable, scalable strategy is not established by this release.

## Current public-feed integration observation

[The isolated real-feed smoke](audit/v0.9.0-public-feed-paper-smoke.json) exercised actual OKX public packages and current quotes through study, review, managed risk decision, idempotent controller reload and stop. All three current momentum signals were nonpositive, so the strategy made **zero orders**, correctly preserving cash. A separately labeled ~100 USDT manual **local-paper** round trip exercised real bid/ask, trading increments, fees and flat inventory. These two probe orders are not strategy returns. The primary operational workspace was untouched.

```sh
uv run python scripts/smoke_portfolio_risk.py \
  --data-dir /tmp/new-empty-tidebench-risk-check --probe-fills \
  --output /tmp/public-feed-risk-check.json
```

The directory must be new/empty. Public-feed results change with market time and are not historical replay. This one observed path does not establish long-duration forward reliability or executable exchange capacity.
