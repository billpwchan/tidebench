# Red-team review: v0.3.0

Reviewed on 2026-10-08 against commit `53e16cceca1f70cf47fc219d3f5c720ac7ea523c`. The full [Chinese assessment](red-team-0.3.0.zh-CN.md) examines strategy semantics, research governance, execution ownership, data fitness, runtime faults and trader workflows.

**Verdict:** Tidebench has useful engineering foundations, but remains a constrained single-market research and local simulation workbench. It is not a complete professional strategy platform. Test counts and reproducibility do not establish investment edge or product completeness.

## Reproduced and fixed in the accompanying changes

- A blocked history download stalled risk and pending-order polling, even while the supervisor remained alive. History preparation now runs outside the economic market lock in a separate, bounded strategy supervisor. Under the same six-second injected stall, a same-market protective stop fills.
- Starting a strategy implicitly adopted manual inventory and admitted pre-existing manual pending orders. Deployment now transactionally requires a flat market with no pending orders. No position is silently closed and no order is automatically canceled.
- Valid usernames `pending-order` and `risk-engine` bypassed manual strategy ownership checks. Username text no longer grants these internal authorities. This concerns local simulation controls, not exchange access.
- OOS engine candidate selection existed, but the UI submitted no candidate grid. The form now distinguishes fixed parameters from training-only candidate selection; real browser submission verifies two training experiments.

## Still open

Only three reference rules are supported. Research accepts one instrument, with no multi-asset target portfolio, multi-leg strategy model or user strategy SDK. Research artifacts are not promoted into version-bound deployments. Forward performance history, strategy attribution and complete decision provenance are missing. Overlapping forward history captures and candidate payload duplication retain avoidable storage costs. Example time is fixed. Readiness remains a liveness check, and long-running representative workload evidence is absent.

The full review defines task-level acceptance criteria and distinguishes reproduced defects from absent capabilities and model boundaries. Live exchange execution remains outside the user-selected scope.

## Evidence

- [Baseline observations](v0.3.0-baseline.json)
- [Post-fix observations](post-fix-observations.json)
- [Offline reproduction script](../../scripts/redteam_audit.py): isolated temporary databases and synthetic fault injection; venue requests are forbidden
- [Runtime regression tests](../../tests/test_pro_service.py) and [rendered browser flows](../../frontend/e2e/workspace.spec.ts)

Local verification: 466 backend tests and eight desktop/mobile Chromium flows passed, plus lint, format and production build. These checks establish specific behavior, not maximum capacity, multi-week reliability or profitable strategies. The observation script's successful exit is not a product acceptance verdict.
