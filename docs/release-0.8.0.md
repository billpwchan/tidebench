# v0.8.0 — strategy mechanisms and contrary evidence

This release puts substantive strategy research into the existing research-to-paper workflow. It adds shared causal implementations for multi-horizon momentum and prior-only, regime-gated reversion, and a cost/sample/freshness admission hurdle for matched spot/perpetual funding carry. Immutable definitions and captured portfolio final evaluations retain these settings; managed decisions use current account costs and record the admission evidence.

Strategies now opens a bilingual editorial research library with exact rules, original sources, economic mechanisms, failure regimes, rejection experiments and executable recipe loading. Actual public OKX checks appear beside the hypotheses. Seven single-market and four portfolio recipes remain starting hypotheses rather than validated alpha.

The fixed 108-case spot battery and all negative/mixed outcomes are published in [strategy research](strategy-research.md). Default momentum was positive in 4/12 correlated scenarios, reversion in 6/12, and the reversion filter did not consistently beat its ablation. Captured replay reproduced all 108 input/result hashes. This release claims correct implementation and transparent research, not profitable strategies.

No SQLite migration: schema remains 7. Numerical implementation identity changes; existing deployments must pass the existing drift/review safeguards before new risk. Operational inventory, local accounts and users are preserved. Execution remains local paper simulation with public OKX market data and no venue order adapter.
