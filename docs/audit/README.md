# Red-team record

The [v0.4.0 assessment](red-team-0.4.0.zh-CN.md) records the implemented strategy/research/release/forward evidence chain, fixes discovered in real workflows and remaining product gaps. The [v0.3.0 assessment](red-team-0.3.0.zh-CN.md) is retained unchanged as historical evidence.

## Reproduce

```bash
uv run python scripts/redteam_audit.py
uv run pytest -q
npm --prefix frontend run test:e2e
uv run python scripts/acceptance_load.py
```

The audit uses temporary databases and synthetic fault injection; venue requests are forbidden. A successful script exit is not a product acceptance verdict. Financial causality, recovery, concurrency and rendered workflows have separate tests.

- [Original observations](v0.3.0-baseline.json)
- [v0.3.1 corrections](post-fix-observations.json)
- [v0.4.0 observations](v0.4.0-observations.json)
- [Verification and workload scope](../verification.md)
- [Design and delivery record](../professional-platform-design.zh-CN.md)

Test evidence does not establish investment edge, maximum capacity, multi-week reliability, tenant isolation or exchange matching fidelity. Managed multi-asset forward execution, strategy contribution accounting, historical listing/delisting coverage and portfolio-version governance remain distinct implementation work.
