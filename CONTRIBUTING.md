# Contributing

Thanks for helping make research easier to inspect. Tidebench is an early project; clear bug reports and reviews of accounting, causal timing and source handling are particularly useful.

1. Open an issue for changes that introduce a new trading model or data source.
2. Keep engine code pure and independent of HTTP/database modules.
3. Use Decimal for accounting and decimal strings at API boundaries.
4. Preserve explicit source labels, saved input snapshots and next-bar execution timing.
5. Add a focused test for a behavioral bug; do not add tests that only repeat implementation details.
6. Run `make check` before opening a pull request. Install Chromium with `cd frontend && npx playwright install chromium`, return to the project root and run `make browser-test` for desktop and mobile workflows.

Never commit credentials, `.env`, local databases, account data, downloaded exchange datasets, or screenshots of private accounts. Use synthetic data for reproducible reports. Review the [architecture](docs/architecture.md) and [commercial gates](docs/commercial-readiness.md) before adding execution features.

## Useful first contributions

- Improve keyboard and screen-reader behavior in the research workflow.
- Add adapter tests for documented regional API edge cases.
- Improve data-quality explanations without silently changing historical input.
- Add an independently reviewed causality test or small accounting counterexample.
- Improve translations and installation documentation.

Do not submit claims of profitable strategies without reproducible methodology. New live-order paths and arbitrary strategy-code execution are outside the current release boundary and require a separate design review.
