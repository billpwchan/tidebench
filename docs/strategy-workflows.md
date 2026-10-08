# Strategy research, review and forward evidence

## From a hypothesis to a reviewed deployment

1. In **Strategies**, start a custom hypothesis or load a reference recipe. State the economic mechanism, expected failure regime and rejection criteria. Save an immutable version with its product, interval, direction, leverage, signal, sizing and exits.
2. Select **Research this version** and choose a verified dataset/package. The saved strategy travels with the study; incompatible product/interval or changed parameters are rejected. Use cost stress and development windows before freezing a final test.
3. In **Research governance**, choose the version, an unused dataset/window, benchmark, costs and rejection plan. Seal the holdout before evaluating it. The exact evaluation can be admitted once; even a failed admitted job consumes it. Replay is reproducibility evidence, not another independent trial.
4. From a completed version-bound study, **Review paper release**. Choose a specific candidate or training-selected fold. Review costs, input identity, current policy, inventory and warnings. Approval captures the preview hash and acknowledgements. Activation revalidates it transactionally; intervening risk changes, pending orders, inventory or implementation drift require a new review.
5. In **Execution → Forward performance**, inspect actual account observations and the deployment's decision journal. Each decision links input hashes, indicators, exits, intent and actual orders. Browse older pages without silently extending the metric window.

Single-market releases own one flat/no-pending market at activation. Stops retain inventory. Reductions remain possible after a policy change; new risk requires a valid policy approval. The independent reference deployment form remains available for deliberate paper experiments and does not invent research approval.

## Strategy expression

Five reference signal families are available: SMA crossover, Wilder RSI reversion, buy-and-hold, **prior closing-price channel** breakout, and population-standard-deviation Z-score reversion. The bounded `program` model supports up to four ordered rules with up to four comparisons each. Operands are typed features or constants; first matching rule wins. Unavailable features do not match. No match retains the existing regime. No Python, JavaScript, `eval`, arbitrary imports or remote code is accepted.

Features: `close`, `volume`, `fast_sma`, `slow_sma`, `rsi`, `zscore`, `channel_upper`, `channel_lower`, `atr`. Thresholds and windows are versioned with the program. See the runnable, schema-validated [recipes](../examples/strategies.json); the strategy editor loads the same file. These are research starting points with rejection criteria, not validated alpha.

Close-confirmed exits include loss, profit, trailing-close and holding-close limits. Historical exits fill at the next available open; a gap can exceed the stop distance. Forward exits require a later observed quote. Loss-budget sizing estimates notional from capital, stop distance and round-trip configured costs, then applies allocation and lot constraints. It is not a guaranteed maximum loss: gaps, funding, liquidation and model error can exceed it. Exchange-style observed stop orders are a separate local order mechanism.

Incremental indicator checkpoints include an implementation/input identity and exact state hash. Decision and durable intent commit together. Recovery resumes an existing intent with the same phase command identities. A new confirmed close does not rerun 2,000 bars or store a duplicate full dataset.

## Shared-capital portfolio research

Use **Research → Portfolio research** with 2–10 distinct ready packages having exactly the same source, bar and UTC window. The 20,000 bar-leg bound is an admission limit. Supported constructions are fixed signed notional weights, independent signals, positive-momentum rotation and a matched spot-long/perpetual-short funding carry pair.

All legs use one temporary simulation book and one cash balance. Close decisions fill at the next open. Reductions precede additions; a common finite-cash scale, lot rounding and sequential fills determine actual inventory. Quantity residuals and execution rejections are retained. There is no atomic two-leg fill assumption. Carry sees only previously settled funding, never the funding event at the current bar boundary.

An optional chronological train/test evaluation fixes construction in advance, inserts an embargo, initializes indicators from preceding closes and starts each trading window flat with independent capital and risk anchors. Top-level equity/orders/journal cover **test only**. Training metrics and a full-result hash are retained for comparison. This is not the one-use blinded holdout mechanism and performs no candidate optimization.

Current instrument rules are explicitly scenario inputs. API clients can supply attributed, causally available rule events for point-in-time rules. Missing bars, future-known rules, unsupported contract-unit conversions and unsupported non-live instruments are rejected. A selected present-day universe does not become survivorship-free historical listing coverage.

Portfolio research saves an immutable multi-market definition and binds it to exact package/result evidence. **Review portfolio release** promotes that result as a whole managed group, with current cost/risk review and flat/unowned activation checks. Shared forward targets, frozen commands, failed-leg compensation and whole-group stops preserve the multi-leg lifecycle; independently starting single-market strategies is unnecessary. See [Managed portfolios](managed-portfolios.md) for failure handling and actual contribution accounting.

The editor loads four editable [portfolio hypotheses](../examples/portfolios.json): a reserve-aware BTC/ETH basket, shared trend/reversion, seven-day positive momentum and a lagged-funding spot/perpetual pair. They default to chronological train/test and state benchmarks, failure regimes and rejection criteria. Additional comparisons described in the hypothesis still need to be run; a recipe is not validated alpha.

## Governance and interpretation

The single-strategy trial ledger counts all recorded version-bound attempts in a project, including failed jobs, replays and candidate configurations. It cannot detect external experiments or relabelled projects. Holdout guards include preceding indicator-access windows and reject dataset aliases/portfolio legs overlapping a sealed or consumed interval. Public data and raw downloads remain accessible; a workflow seal is not cryptographic blinding. Portfolio-specific one-use final tests and cross-run trial governance remain separate work; a portfolio version or chronological test does not provide that control.

Observed account returns adjust for external capital flows at observation boundaries. Missing/stale valuations create gaps and suppress chained return/drawdown claims. Statistics cover the selected page; they are not reconstructed inception returns. The separate contribution ledger attributes actual monetary P&L to owners under the shared account and reconciles quantity/cost/fee/funding evidence; it does not turn account returns into independent strategy returns.
