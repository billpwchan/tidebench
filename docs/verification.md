# Verification record

Observed 2026-10-07 UTC / 2026-10-08 Singapore. Checks apply to the professional workspace and its explicit public-data / local-execution scope.

## Automated acceptance

| Check | Observed result |
|---|---|
| Backend/core/property/API tests | 459 passed |
| Ruff lint and formatting | Passed |
| TypeScript and production build | Passed |
| Prettier | Passed |
| Frozen Python lock / npm dependency audit | Passed / zero reported vulnerabilities |
| Desktop and mobile Chromium workflows | 8 passed |
| Rendered professional workflows | Five research modes, SWAP replay/export, spot/SWAP fills, pending cancellation, halt reductions, data import, strategy start/stop, user permissions/passwords, actual backup create/verify/restore and re-login |
| Public OKX integration | 48h trade/mark/index: 48 confirmed records each; 6 settled funding events with timestamp-matched minute marks; 99 current tiers; all dataset hashes verified |

The tests use isolated databases and synthetic inputs; live public integration is a separate read-only observation. No exchange credential or private account/order endpoint is used. No raw OKX market-history dataset is distributed. See [public integration evidence](acceptance-public-data.json).

Financial tests cover signal causality and indicator state; train-only selection and independent OOS folds; fee/funding/margin behavior; tier boundaries; explicit insolvency; native asset journal balance and persisted-cash replay; cancellation and canonical command deduplication under concurrency; stale independent marks; late, conflicting and duplicate funding; two-phase reversal crashes; and stop races.

Security/recovery acceptance includes exclusive ownership before initialization/migration (including database aliases), rejection of damaged research artifacts before replay, all five role boundaries, cookie CSRF including malformed headers, service-token behavior, setup bootstrap, persistent throttling, password revocation, session expiry, body limits, metric label redaction, actual backup mutation/restore, retention protection, schema mismatch, missing journal/funding/intent-table rejection and concurrent/canceled recovery. Injected stop/restart failures retain maintenance HTTP 503 and persist account halts and strategy stops; repeated request cancellation cannot release maintenance during database replacement.

## Measured workload

Run `uv run python scripts/acceptance_load.py`. It starts a disposable real HTTP server and executes 192 account reads and 48 captured portfolio analyses at concurrency 12, with four BTC/ETH spot/perpetual positions held, while a 24-case × 1,000-bar research grid runs with two computation threads. Every account read checks stable cash and position count; every analysis requires complete valuation and scenarios. Both job waits have explicit 120-second deadlines.

On the recorded macOS arm64 / Python 3.13.15 run:

- 240 HTTP 200 responses; no request failures.
- 2.291 seconds total, 104.77 requests/second.
- Median 63.39 ms; P95 226.17 ms; maximum 1,114.77 ms.
- All 24 research experiments completed.

[Machine-readable evidence](acceptance-load.json) states the environment and workload. This is local capacity evidence, not a cloud benchmark or availability guarantee. CI runs the same script and publishes its own result artifact.

Run `uv run python scripts/smoke_okx.py --region global --hours 48` for public venue acceptance. Network/region/time affect observations; failures remain failures, never synthetic fallbacks.

## Interface evidence

The automated browser flows cover one-action perpetual packages and exact-version research handoff; versioned research, verified replay and actual JSON download; captured custom price shocks and trading overview; spot/perpetual order preview/fill and persistent halt; data/operations navigation, source failure, market search and viewport overflow. Chromium mobile emulation does not establish physical iPhone/Safari support. Additional rendered review exercised 390px Chinese controls, viewer restrictions, password revocation and re-login, all five professional research modes, research packages, custom stress captures, imports, strategy lifecycle and actual backup restoration with forced re-login and restored balances without browser page errors.

The run manifest includes installed code identities, complete result hashes, tier/funding assumption hashes and replay verification. Analytic captures retain their exact book, quotes, costs and scenario input hash. Missing prices or maintenance evidence remain explicit unavailability.

Screenshots in `docs/assets` come from the actual Example workspace. Recreate them after a build with `TIDEBENCH_CAPTURE_ASSETS=1 npm --prefix frontend run test:e2e -- --project=desktop`.

## CI and release

The [CI workflow](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml) independently runs lint, formatting, tests, production build, desktop/mobile workflows and measured load on Ubuntu 24.04 / Python 3.13 / Node 24. Its container job builds and starts the non-root image, checks health/readiness and static UI, rejects anonymous account access and verifies authenticated market/account reads. Use the actual workflow result for the published commit as the evidence.

The local Docker daemon was unavailable; container runtime acceptance is performed by CI. One locked dependency emits a Starlette TestClient/HTTPX deprecation warning; the warning remains visible. No external security certification, multi-month availability result, distributed failover claim or profitable-strategy claim is implied by these checks.
