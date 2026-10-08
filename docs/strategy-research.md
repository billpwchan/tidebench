# Strategy research: mechanisms, implementations and contrary evidence

Tidebench ships executable research hypotheses, not a profitable-strategy catalogue. The v0.8 strategy library connects economic mechanisms, exact signal rules, original sources, costs, failure regimes and actual contrary evidence to immutable versions. Loading a hypothesis edits a real definition that can enter the existing captured research and reviewed paper workflow.

## What was implemented

| Model | Economic question | Implemented behavior | Evidence status |
|---|---|---|---|
| Multi-horizon time-series momentum | Does weekly price persistence survive disagreement, reversals and costs? | Volatility-normalized 1/2/4-week return consensus, explicit flat state, volatility guard | Mixed/negative public checks; no established edge |
| Range-gated reversion | Does avoiding directional paths improve temporary-displacement reversal? | Prior-only price standardization, path-efficiency veto, volatility guard and timed exit | Filter does not consistently improve its ablation |
| Cost-aware matched funding carry | Can a lagged funding scenario cover two-leg turnover and a buffer? | Prior-settlement sample, mean rate, age bound and four-fill cost hurdle in historical and managed controllers | Admission mechanics tested; no profitable carry claim |
| Cross-sectional rotation | Do relative winners outperform a declared passive basket? | Existing positive-momentum ranking with declared weights and reserve cash | Constrained selected-universe hypothesis, not a replicated factor |
| Closing-channel breakout | Does a new close extreme predict continued repricing? | Existing prior-only closing-price channel | Simple reference with explicit falsification criteria |

SMA, RSI, price Z-score and bounded rule programs remain editable references. A single price Z-score is **not** pairs trading, a stationary spread or a cointegration test. Seven single-market recipes and four portfolio recipes are schema validated; the five research dossiers are bilingual. Every default is a declared starting specification, not a hidden optimized setting.

## Research basis and limits of transfer

- [Moskowitz, Ooi and Pedersen, Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum): own-return persistence across traditional futures motivates trend testing. Its markets, horizons and portfolio construction differ from this implementation.
- [Liu and Tsyvinski, Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877.pdf): historical crypto-specific daily/weekly momentum motivates the weekly horizons; it does not validate our threshold or current OKX returns.
- [Liu, Tsyvinski and Wu, Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882): broad crypto factor evidence motivates relative-strength comparisons; a selected BTC/ETH/SOL basket does not reproduce that universe or long-short factor.
- [Dai et al., Reversals and the Returns to Liquidity Provision](https://www.nber.org/papers/w30917): reversal behavior depends on liquidity conditions. Its principally equity evidence is weaker support for this 4H crypto adaptation; the efficiency filter is our engineering hypothesis.
- [Schmeling, Schrimpf and Todorov, Crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry): carry has capital and margin risks; dated-futures basis evidence is distinct from perpetual funding.
- [OKX funding mechanism](https://www.okx.com/en-gb/help/perps-funding-fee-mechanism): funding sign and settlement frequency can change. Settlement counts must not silently become fixed-day returns or APR.

These are research motivations and implementation-specific adaptations, with no affiliation to the cited authors or institutions. Adding filters increases opportunities to overfit. A plausible story, an accurate formula and correct accounting are separate from evidence of a usable advantage.

## Exact causal rules

All signals use confirmed closes; historical decisions execute at the following open. Risk exits retain the existing close-based, next-open semantics. A gap can exceed the loss estimate. Paper decisions use the same incremental implementation and persist its bounded price history in the hashed checkpoint.

### Multi-horizon momentum (`ts_momentum`)

For simple close returns `r[t] = C[t]/C[t-1]-1`, compute population standard deviation `sigma` over the most recent `vol_window` returns, including the return just observed at the decision close. For each horizon `h`:

```
score[h] = (C[t] / C[t-h] - 1) / (sigma * sqrt(h))
```

All scores strictly above `momentum_entry` request long; all strictly below its negative request short; disagreement requests flat. Equality is not agreement. Direction restrictions map prohibited directions to flat. Zero volatility and volatility above `max_bar_vol_pct` also request flat; zero maximum disables that guard. Scores are neither t-statistics nor probabilities. Volatility normalizes the signal, **not** the position size.

The default 4H recipe uses horizons 42/84/168 (1/2/4 weeks), entry 0.5, volatility window 42 returns and guard 5% per bar. It needs 169 closes. The 20% allocation cap, 0.5% estimated loss budget, 8% close stop and 12% trailing-close exit still apply. This is not the original paper's volatility-scaled, 12-month futures portfolio.

### Range-gated reversion (`regime_reversion`)

```
prior = C[t-window] ... C[t-1]
z = (C[t] - mean(prior)) / population_std(prior)
efficiency = abs(C[t] - C[t-N]) / sum(abs(delta_close), last N changes)
```

The current close is excluded from its reference mean and price deviation. Efficiency above `efficiency_max` requests flat; otherwise `z < -z_entry` requests long, `z > z_entry` short, and `abs(z) <= z_exit` flat. Intermediate scores retain inventory. A zero price deviation or return volatility requests flat. No missing values are converted into a trade.

Default: prior 48 closes, efficiency over 84 changes with ceiling 0.35, return-volatility window 42 and 5% bar guard. It requires 85 closes. The 4H recipe uses a 5% close stop, 12-close holding limit, 20% cap and 0.5% estimated loss budget. The filter cannot identify a regime before it becomes observable.

### Funding carry hurdle

Only realized events **strictly before the decision bar's opening boundary** enter the signal; this intentionally lags the current settlement. The new recipe needs 12 events and a latest-event age no greater than 16 hours. With lagged mean rate `f`, projected settlement count `S`, common per-fill fees/slippage and buffer:

```
net_hurdle_bps = S * f * 10_000 - 4 * (fee_bps + slippage_bps) - buffer_bps
```

Admission requires a full sample, fresh latest event, mean at/above threshold and a strictly positive net hurdle. Four fills represent entry/exit on both spot and perpetual for one matched notional unit. Historical costs come from the frozen study; forward costs come from the current account policy. The full evidence and non-admission reason appear in the decision/batch export. When admission is lost, weights become zero; group reductions and failure compensation retain their existing contracts.

At 10 bps fee and 5 bps slippage, turnover costs 60 bps. A 20 bps buffer and 21-settlement scenario need a mean above 3.81 bps per settlement. Positive 1 bp funding does not clear that hurdle. `S` is a constant-rate scenario, not guaranteed holding time, funding forecast, APR or guaranteed cost recovery. Basis, financing/opportunity cost and margin risk are not inferred from funding. Equal quote notionals are only approximately base-delta neutral. Staleness checks apply to the latest observation, not a proof that all intermediate exchange events were received.

Legacy definitions default to one prior event, zero projected settlements and no age limit, retaining rate-only admission. The editor makes that legacy behavior explicit; changing it requires a new immutable definition. The new shipped carry recipe enables the cost/sample/freshness controls.

## Actual public-data battery

[All 108 cases and input/result hashes](audit/v0.8.0-strategy-battery.json) are public. Data: OKX public spot, BTC-USDT / ETH-USDT / SOL-USDT, 2,190 confirmed 4H bars each over `[2025-10-08 08:00, 2026-10-08 08:00)` UTC. No API key or operational account was used.

The fixed plan was written before evaluation. First 50% is available development/history; the next 25% and final 25% are separate flat-start checks, with preceding history for indicators. No training optimization or test-winner selection occurs. Nine disclosed cases × three markets × two windows × two cost levels = 108:

- Equal-allocation buy/hold and 20/80 SMA references.
- Momentum 42/84/168 plus neighbors 36/72/144 and 48/96/192.
- Reversion efficiency 0.35 plus 0.25/0.45 neighbors and efficiency=1 filter ablation.
- Per-fill costs 10 fee + 5 slippage bps, and 20 + 10 bps.

The default momentum is positive in **4/12** scenarios and reversion in **6/12**. Those are descriptive counts over shared windows and cost levels, not twelve independent statistical tests. The reversion filter does not consistently improve the ablation. Small round-trip samples remain visible. No profitable advantage is established, and the parameters were not retuned to erase these results.

### Doubled-cost returns, percent of initial account equity

| Market | Check | Momentum | Filtered reversion | Reversion without filter | Passive reference |
|---|---|---:|---:|---:|---:|
| BTC | A | -0.389 | -0.749 | -1.291 | -2.525 |
| BTC | B | -0.044 | 0.131 | 0.131 | 6.708 |
| ETH | A | -0.871 | -1.242 | -1.083 | -4.127 |
| ETH | B | -0.358 | 0.417 | 0.417 | 9.595 |
| SOL | A | -0.538 | -1.523 | -1.449 | -1.206 |
| SOL | B | 0.525 | 0.815 | 0.815 | 9.623 |

Both reference and strategies have a 20% allocation cap, but the strategies' loss budgets/stops reduce realized exposure; this is **not a risk-matched alpha comparison**. The snapshot uses selected present-day markets and current instrument rules. The checks are exploratory, not a blinded one-use final evaluation or multiple-testing-adjusted significance result. This battery evaluates spot models, not carry profitability, short-side perpetual edge or order-book capacity.

Initial runner attempts failed on an incorrect manifest field name and Decimal JSON serialization. They did not complete the battery. Both runner defects were corrected before the successful report; model parameters were unchanged. The completed 108-result battery was subsequently replayed from retained captures with every result/input hash identical. The compact UI table is derived from that full report.

### Reproduce and retain inputs

```bash
uv run python scripts/strategy_battery.py --source okx --days 365 \
  --end 1791446400000 --capture-dir data/strategy-battery \
  --output data/strategy-battery-report.json

# Same capture, declaration and installed numerical implementation:
uv run python scripts/strategy_battery.py --source okx --days 365 \
  --replay-report data/strategy-battery-report.json \
  --capture-dir data/strategy-battery --output data/strategy-battery-replay.json
```

A fresh exchange download can receive revised data and therefore different hashes. Exact replay requires the original retained capture, matching plan and implementation. Public reports contain hashes and metrics; the raw captured data remains local. Changing code requires a new disclosed research run, not rewriting prior evidence. This standalone CLI battery does not register workspace project trials or consume the in-app one-use protocol; its exploratory status is explicit.

## Self-challenge and next hypotheses

The present results question the usefulness of the default consensus and regime filter. More indicators or a larger parameter search would create more selection opportunities, not resolve those doubts. First discriminate the mechanism: compare slower decision/rebalance schedules against turnover, measure directional exposure and episode concentration, and compare risk-matched benchmarks before interpreting relative returns. These require separate declared experiments and a fresh final evaluation; the displayed checks cannot be recycled as independent validation.

Pairs/statistical arbitrage needs a trained and frozen hedge ratio, stationary-spread diagnostics, causal residual construction, matched execution and borrow/financing evidence. Market making requires depth/queue/partial-fill data. Neither is relabeled from the existing independent RSI/Z-score models. The platform already supports the implemented strategies end to end; these distinct mechanisms require their own evidence before entering a professional catalogue.
