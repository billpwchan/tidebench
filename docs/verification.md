# Verification record

Date: 2026-10-08. This records specific release checks, not a production certification or a profitability assessment.

## Local checks

Environment: macOS arm64, Python 3.13, Node.js 26. CI independently targets Python 3.13 and Node.js 24 on Linux. Dependencies are resolved in `uv.lock` and `frontend/package-lock.json`.

| Check | Observed result |
| --- | --- |
| Ruff lint and formatting, backend/tests/dev launcher | Passed |
| Python tests | 121 passed |
| TypeScript and production Vite build | Passed |
| Frontend Prettier check | Passed |
| Chromium desktop and mobile workflows | 6 passed |
| npm dependency audit | No reported vulnerabilities at the time of the check |
| Live OKX adapter smoke check | Five supported spot instruments and tickers returned; 720 confirmed BTC-USDT hourly bars passed completeness and timestamp-alignment checks |

Live endpoint observations are time- and region-dependent. They are not deterministic tests, latency benchmarks or availability guarantees. No private account endpoint or exchange trading credential was used. Raw market data from that check is not distributed in this repository.

The browser tests run against an isolated disposable database and explicitly synthetic data. They check saved-input research, costs, JSON download, identical snapshot replay, paper fills, persistent halt/resume, strategy start/stop, explicit source failures, market search and viewport overflow. Chromium's mobile emulation is not a test on a physical iPhone or Safari.

Product screenshots in `docs/assets/` come from the actual application with the Example source selected. Recreate them with `TIDEBENCH_CAPTURE_ASSETS=1 npm --prefix frontend run test:e2e -- --project=desktop` after building the frontend and installing Chromium.

The first clean Linux CI run found a randomized accounting replay using the caller's default 28-digit Decimal context rather than the engine's 50-digit contract. That exact case is retained as a regression. Follow-up adversarial checks found and fixed inherited rounding/traps/exponent settings and a non-progressing one-lot correction at extreme magnitude. The engine now rejects unsupported numeric/step-resolution domains; accepted paths still require exact replayed balances and nonnegative cash/inventory.

One dependency emits a Starlette TestClient deprecation warning for its current HTTPX integration. The warning is visible in test output; application requests and tests pass with the locked dependencies.

## Container and CI

The local Docker daemon was unavailable, so local container-runtime verification was not performed. The published [CI workflow](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml) separately builds and starts the image, checks `/healthz` and the frontend, verifies that an unauthenticated account request is rejected, and requests an authenticated synthetic candle dataset. The workflow's actual status is the evidence for those checks. Startup readiness tolerates bounded connection resets while the container begins listening.

## Remaining acceptance work

No external security audit, physical-device/browser matrix, prolonged paper soak, backup/restore exercise, multi-tenant isolation assessment or real exchange order execution has been completed. Those belong to the [commercial readiness gates](commercial-readiness.md) and [architecture challenge](architecture-review.md). Existing tests establish bounded model behavior; they do not validate order-book execution fidelity or investment performance.
