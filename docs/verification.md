# Verification record

## v0.6.0 · 2026-10-08

- Full local backend regression on the corrected planner: **666 passed** (123.99 seconds), followed by the additional strict-residual/minimum-deferral counterexample (**1 passed**). Ruff lint/format, frozen offline Python sync, TypeScript/Vite and Prettier passed. The existing Starlette/HTTPX deprecation remains visible.
- **16 desktop/mobile Chromium workflows passed** on the final build (2.0 minutes). New coverage includes exact portfolio capture → review → seal → primary evaluation → verified replay, plus preserving a newly edited order draft when a previous submission responds late. Desktop/mobile screenshots were inspected and long-decimal criteria formatted with exact hover values.
- Real SQLite counterexamples verify concurrent single-primary admission, queue rollback, committed-failure consumption, cross-bar/alias/unbound access rejection, indicator warmup exposure, schema-6 recovery retaining consumption/exposure/trials, missing primary artifacts and conflicting/corrupt retained facts. A real independent market fill between quote enumeration and managed-command admission now retries without duplicate fills or compensation.
- Current standalone managed/recovery and offline red-team audit scripts passed against this implementation. Earlier failed v0.5 lifecycle trials are preserved; see [audit method](audit/soak-method.md). The [independent Linux CI](https://github.com/billpwchan/tidebench/actions/runs/37755474249) passed on source `5e687fd716dfe4f7ef7b224c082ba656a613e828`, including 666 backend tests, 16 browser workflows, isolated audits and the built container/readiness smoke. Later evidence/tests/documentation commits preserve the same application implementation.
- Read-only [current public OKX acceptance](audit/v0.6.0-public-okx-observations.json) verified 48 trade/mark/index bars, 6 settled funding events with exact-time mark inputs and 99 captured current tiers; raw prices are not committed. A private copy migration and the actual local schema-5→6 upgrade preserved authoritative financial/user rows, passed SQLite integrity/FK checks and retained administrator setup state.
- The corrected [elapsed lifecycle audit](audit/v0.6.0-soak-observations.json) passed **608.323 real seconds** on committed source `5e687fd716dfe4f7ef7b224c082ba656a613e828`: 3 groups, 60 separate clock steps (240 synthetic hours), 154 orders/commands, 93 completed batches, 120 funding settlements, 274 contribution events, 9 research jobs and 720 concurrent reads. All 240 observed contribution reports reconciled; no unexpected HTTP failures, duplicate keys, missing filled-order references or external network attempts occurred. A real SIGTERM restart preserved 77 prior order IDs/bodies; a fresh shutdown marker confirmed drained work, stopped supervisors and released lease. Final owner-versus-account net delta was `-1.88E-47` USDT, within the declared finite-precision acceptance bound. This sequential-admission, shared-host synthetic workload does not establish simultaneous activation capacity, long-term uptime or venue liquidity.
- Main application bundle: about 466 kB before transfer compression; research/governance pages remain deferred bundles. This shared-host size and verification record do not establish a capacity or availability SLA.

## v0.5.0 · 2026-10-08

- **647 backend/core/property/API tests passed** on macOS arm64 / Python 3.13.15. Ruff lint/format and the frozen offline Python lock check passed. The existing Starlette TestClient/HTTPX deprecation warning remains visible.
- **14 desktop/mobile Chromium workflows passed** on the final build. They include the editable portfolio hypothesis → immutable version → chronological study → whole-group review/activation → completed targets/actual inventory → owner event/CSV → group stop flow, alongside the prior single-strategy/research/data/risk workflows. Keyboard skip navigation and viewport overflow are checked. Mobile emulation does not establish physical-device/Safari support.
- TypeScript/Vite production build and Prettier passed; main application bundle is about 458 kB before transfer compression. The research/governance/registry pages remain deferred bundles. Desktop and mobile managed-group/contribution screenshots were inspected after the final interaction fixes.
- The [standalone managed audit](audit/v0.5.0-managed-observations.json) uses real SQLite/economic services in a disposable synthetic workspace: hard child exit after the first committed fill, four unique fills after recovery, unchanged frozen targets/additions, blocked compensation followed by flattening, and actual schema-5 database replacement with stopped groups, preserved owner evidence, halted risk and paused clock.
- Additional regressions cover 201 newer stopped groups without losing an older active/compensating/failed group; one corrupt group alongside a healthy executing group; hash/JSON/identity faults; auxiliary-attribution corruption during protective reduction/funding/liquidation; repeated mixed-owner exact-decimal closes; asymmetric carry loss budgets; complete-account valuation severity; cross-bar holdout access; and bounded scientific-notation input before persistence.
- The existing offline red-team script also completed against this implementation. Its older archived observation file remains dated evidence; the [0.5 assessment](audit/red-team-0.5.0.zh-CN.md) describes the current contracts and remaining work. CI now runs and uploads the new managed recovery/attribution script as a separate artifact.

The [0.5 loopback workload](acceptance-load-v0.5.0.json) completed 240 HTTP 200 reads at concurrency 12 with four spot/perpetual positions and a concurrent 24-case × 1,000-bar grid: 3.896 seconds, 61.61 requests/second, median 167.79 ms, P95 343.40 ms, maximum 620.22 ms. All 24 cases completed. This was a shared development host, not a controlled release comparison, long-running capacity test or availability guarantee.

Before upgrading the existing local workspace, an online SQLite snapshot of application schema 4 passed integrity and foreign-key checks and was retained privately with mode 0600. No runtime database, backup or exchange credential is published.

These observations establish specific local contracts. They do not establish portfolio one-use final-test governance, historical-universe completeness, order-book capacity, unlimited retained-owner scale or multi-week operations. Use the published commit's actual [CI/container result](https://github.com/billpwchan/tidebench/actions/workflows/ci.yml) as independent Linux evidence.

## v0.4.0 · 2026-10-08

- 532 backend/core/property/API tests passed on macOS arm64 / Python 3.13.15; Ruff and frozen offline lock checks passed.
- 12 desktop/mobile Chromium workflows passed, including editable strategy recipe → immutable version → research → review → activation → holding-limit exit journal; shared-capital independent test/export; and one-use holdout evaluation. A subsequent presentation refinement is checked again in both portfolio workflows.
- Production TypeScript/Vite build and Prettier passed. Research/registry/governance pages are deferred bundles; the main bundle is approximately 436 kB before transfer compression.
- Actual schema-4 restore preserves compressed research, strategy/release lineage, forward decisions/equity and incidents, restores the saved synthetic time, revokes sessions and leaves deployment stopped/risk halted.
- Process tests cover direct-engine result parity, exclusion of workspace credentials, deadline/cancel/failure cleanup and abrupt owner death. Linux-specific limits are independently exercised by CI; this local macOS run does not establish Linux enforcement.
- [Current offline audit](audit/v0.4.0-observations.json) records complete research-module identities, incremental history, time progression, ownership and protective-stop faults. [Current assessment](audit/red-team-0.4.0.zh-CN.md) retains remaining economic gaps.

The final local mixed workload used 240 real loopback HTTP reads at concurrency 12, four BTC/ETH spot/perpetual positions and a concurrent 24-case × 1,000-bar grid. All 240 returned HTTP 200 and all 24 cases completed: 4.598 seconds, 52.2 requests/second, median 185.18 ms, P95 597.72 ms, maximum 744.16 ms. This shared development host was also running acceptance work; it is not a controlled comparison against the older baseline or a capacity guarantee. [Machine-readable workload evidence](acceptance-load.json).

A private online snapshot of the local schema-3 workspace passed integrity/FK checks before upgrade. Data and backup files are ignored by Git. Public market-data acceptance remains the separately dated read-only observation below; synthetic acceptance does not replace venue evidence. Use the published commit's actual CI/container outcome for independent Linux verification.

## 2026-10-08 red-team correction

The v0.3.0 evidence below described specific implemented behavior and was insufficient to establish product completeness. The [red-team assessment](audit/README.md) records ten concrete observations, including execution/ownership defects now fixed and substantial strategy/portfolio gaps still open. Accompanying local validation passed 466 backend tests, eight desktop/mobile Chromium flows (including actual OOS candidate submission), Ruff, Prettier and the production build. The offline [before](audit/v0.3.0-baseline.json)/[after](audit/post-fix-observations.json) observations preserve the injected-fault evidence and its limits. For the published v0.3.1 build, use its actual CI run as independent evidence.

## v0.3.0 baseline evidence

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
