# Red-team record

Latest: [fresh v0.10 independent review](fresh-institutional-review-0.10.0.zh-CN.md), [reproduction artifacts](fresh-review-0.10.0/README.md), [v0.10 workflow and corrective contracts](../professional-workflows-v0.10.md), and [release verification](../verification.md).

Baseline: [fresh v0.9 institutional review](fresh-institutional-review-0.9.0.zh-CN.md).

Previous: [v0.9 portfolio risk self-audit](red-team-0.9.0.zh-CN.md), [28 public-data allocation cases](v0.9.0-portfolio-risk-battery.json) and [risk research methods](../portfolio-risk-research.md).

Previous: [v0.8 strategy self-audit](red-team-0.8.0.zh-CN.md), [all 108 public spot cases](v0.8.0-strategy-battery.json) and [methods/negative conclusions](../strategy-research.md).

Previous: [v0.7 instrument evidence self-audit](red-team-0.7.0.zh-CN.md) and [real public observation counts/hashes](v0.7.0-public-instrument-observations.json).

The [v0.6.0 assessment](red-team-0.6.0.zh-CN.md) examines captured portfolio evaluation, recovery-retained research facts and real lifecycle failures. The [v0.5.0 assessment](red-team-0.5.0.zh-CN.md) examines managed multi-market execution, hard-interruption recovery, failed-leg compensation and contribution accounting. It records the discovered auxiliary-attribution safety and mixed-owner Decimal issues, their corrective contracts, and the remaining product work. The [v0.4.0 assessment](red-team-0.4.0.zh-CN.md) and [v0.3.0 assessment](red-team-0.3.0.zh-CN.md) remain historical evidence for those versions.

## Reproduce

```bash
uv run python scripts/managed_portfolio_audit.py
uv run python scripts/redteam_audit.py
uv run pytest -q
npm --prefix frontend run test:e2e
uv run python scripts/acceptance_load.py
```

The standalone audits use temporary databases and synthetic fault injection; venue requests are forbidden. A successful script exit is not a product acceptance verdict. Financial causality, recovery, concurrency and rendered workflows have separate tests. A historical artifact retains its original version and environment rather than being relabeled as current verification.

- [v0.6.0 passed elapsed lifecycle observations](v0.6.0-soak-observations.json)
- [v0.6.0 failed minimum-rebalance observations](v0.6.0-soak-minimum-rebalance-failure.json)
- [v0.6.0 managed-portfolio observations](v0.6.0-managed-observations.json)
- [v0.6.0 public OKX acceptance](v0.6.0-public-okx-observations.json)
- [v0.6.0 offline red-team observations](v0.6.0-redteam-observations.json)
- [v0.5.0 managed-portfolio observations](v0.5.0-managed-observations.json)
- [v0.4.0 observations](v0.4.0-observations.json)
- [v0.3.1 corrections](post-fix-observations.json)
- [Original observations](v0.3.0-baseline.json)
- [Verification and workload scope](../verification.md)
- [Managed portfolio contract and operator guide](../managed-portfolios.md)
- [Design and delivery record](../professional-platform-design.zh-CN.md)

Managed forward portfolios, immutable release identity, monetary contribution accounting and captured one-use portfolio evaluations are implemented. Recovery retains newer research reservation, exposure, consumption and trial facts. Attributed historical eligibility, typed cash settlement/unit conversion, current public L2 cost evidence, account capital commitments and frozen full-window economics are implemented. Comprehensive venue membership archives, event-aware per-market lifecycle attribution, dynamic historical-to-forward conversion routing, exchange matching/capacity fidelity and longer elapsed external operations remain separate evidence or scope boundaries. The bounded elapsed-time audit has its own [method and scope](soak-method.md). Tests do not establish investment edge, maximum production capacity, tenant isolation or exchange matching fidelity.
