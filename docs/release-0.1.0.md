# Tidebench v0.1.0 — developer preview

Evidence before execution: an open-source, self-hosted crypto research and local paper-trading workspace, starting with OKX public market data.

## Included

- Interactive React/TypeScript market, research, paper, risk and settings workflows, with desktop and mobile layouts.
- Five USDT spot markets, four candle intervals, explicit regional REST endpoints and source/quality timestamps.
- SMA crossover, Wilder RSI reversion and buy-and-hold research with Decimal accounting, causal next-open fills, explicit costs and a benchmark.
- Saved candle/instrument snapshots, versioned manifests, dataset hashes, JSON export and identical-input replay.
- Persistent local paper accounts, manual fills and strategy supervision, durable command idempotency, transactional risk checks, halt/resume and audit events.
- An isolated deterministic synthetic example; no exchange API key is needed.
- Locked dependencies, a source launcher, Docker configuration, CI, English/Chinese documentation, trader product specifications and a failure-oriented architecture review.

## Validation

112 backend tests and six Chromium desktop/mobile workflow tests pass locally; TypeScript, the production build, Ruff and Prettier pass. The live OKX smoke check returned the five supported spot markets and 720 confirmed hourly BTC-USDT bars. See [verification details](https://github.com/billpwchan/tidebench/blob/main/docs/verification.md) and the [CI workflow](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml) for independent build/container status.

## Boundaries

This is a **developer preview**, limited to long-only spot research and local simulated fills. It does not send exchange orders or connect to OKX Demo Trading. It uses one workspace, one operator and one process. Backtests and simulated results are not evidence of future returns.

WebSocket transport, larger research datasets, walk-forward validation, multi-asset execution accounting, exchange reconciliation, team authorization and operational recovery require further implementation and acceptance work. See the [readiness gates](https://github.com/billpwchan/tidebench/blob/main/docs/commercial-readiness.md).

Reviews of causal timing, accounting invariants, data quality and trader usability are particularly welcome. Start with [CONTRIBUTING](https://github.com/billpwchan/tidebench/blob/main/CONTRIBUTING.md).
