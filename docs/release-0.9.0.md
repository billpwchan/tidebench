# v0.9.0 — causal portfolio risk allocation

Momentum ranking alone left position sizes largely fixed and could hide concentrated crypto market risk. This release adds a complete risk-budgeted momentum workflow: causal sample covariance, a volatility floor, explicit fixed diagonal shrinkage, capped inverse-volatility allocation, a correlation stress and a downward-only sleeve risk governor. Risk is rechecked after protective exits and loss-budget caps. Historical and managed paper paths share the same implementation, and frozen versions, holdouts and replay bind every control.

The bilingual interface adds an executable portfolio recipe, a research dossier with primary sources and counterevidence, published comparisons, and a decision-selectable risk panel. Users can inspect covariance inputs, sample timestamps, cash allocation and per-asset volatility contributions. Performance summaries distinguish realized account risk from planned sleeve risk and include actual turnover and gross exposure.

The [two-year OKX spot experiment](portfolio-risk-research.md) publishes all 28 fixed cases, including neighbors, stress ablation, unmanaged rotation and static allocation. Lower drawdowns came with substantially lower exposure. At doubled costs the primary model returned −8.453% in A and +0.169% in B; the short-window neighbor lost money in B. Realized B account volatility exceeded the approximate planned account level. This is useful contrary evidence, not alpha validation.

Local verification: 729 full-suite backend tests plus the separately passing new covariance-budget refusal test (730 total tests), all 24 desktop/mobile Chromium workflows, lint, format and production build. Complete browser and Linux CI results are recorded in the release verification. Frozen-capture replay reproduced all 28 input/result hashes before publication.

SQLite schema remains 7; no user or financial-account reset. Numerical implementation identity changes, retaining existing drift/review safeguards before new risk. Public OKX data, historical research and local paper execution remain the authorized scope; there is no exchange-order adapter.

A current public-feed integration smoke also passed in a disposable workspace. The strategy selected cash; a separate, labeled local-paper fill probe exercised real bid/ask and ended flat. English/Chinese risk controls and result navigation passed in desktop/mobile browsers. [Detailed verification](verification.md).
