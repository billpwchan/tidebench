# Verification record

## v0.10.0 financial evidence and trader workflows

Final local backend validation passed **880 tests** (161.67 seconds), plus Ruff lint/format, Prettier, TypeScript and the production build. The main JavaScript bundle is 497.87 kB before compression, with operations/capital/contribution detail deferred. The existing Starlette/HTTPX test-client deprecation remains visible.

**42 desktop/mobile workflows passed across the final verification runs**: 40 in the whole-suite run (4.2 minutes), followed by both corrected financial-window workflows (9.7 seconds). The test fix reads the actual snapshot envelope and matches the rendered verification paragraph; it does not alter backend behavior. Instrument capture is exercised with a real older list deliberately returned late. Final CI repeats the complete suite on its exact published commit.

The [fresh independent institutional review](audit/fresh-institutional-review-0.10.0.zh-CN.md) reproduces financial admission, funding conversion, maintenance breach and known-bad-book omission counterexamples. The additional manual-capital declaration review checks direct and real release/activation paths. [Byte-preserved scripts, before/after observations and publication hashes](audit/fresh-review-0.10.0/README.md) make the findings reconstructible. The independent report's 128-test run predates its separate six-case declaration correction; the complete 880-test run covers final source.

Separate [offline adversarial observations](audit/v0.10.0-offline-redteam-observations.json), [actual hard-exit/schema-8 recovery](audit/v0.10.0-managed-observations.json) and [240-request HTTP workload](audit/v0.10.0-http-workload.json) passed. The latter measured 12 clients, four mixed spot/perpetual positions and concurrent 24-case research: 240/240 HTTP 200, p95 344.65 ms. This is one local bounded workload, not a service capacity or availability guarantee.

[Real public L2 observations](audit/v0.10.0-public-liquidity-observations.json) retain all 72 captures across six spot/perpetual markets and six frozen reports. Collection source hashes remained unchanged; selected windows include all 12 known captures per market. **All six reports are unsupported**, because some venue timestamps are later than the local receive clock. The reports preserve the actual clock evidence and do not manufacture a capacity pass. No raw order-book array or operational database is bundled.

Schema is **8**. New public evidence is retained during actual restore; later financial epochs remain in the safety backup rather than being grafted onto older balances. Primary-user/account preservation is checked separately during the preview upgrade. See [professional workflows](professional-workflows-v0.10.md) for implemented behavior and [scope](commercial-readiness.md) for evidence boundaries.

## v0.9.0 portfolio risk allocation

The full local backend suite passed **729 tests** (110.19 seconds), and the subsequently added covariance-work admission refusal test passed separately: **730 total tests**. Formula and independent covariance checks, future perturbation, zero/invalid histories, ceiling cash, stress scaling, contribution reconciliation, frozen holdout controls, historical replay and managed restart idempotency are covered. Ruff, locked package synchronization, Prettier, TypeScript and production build passed. The existing Starlette/HTTPX deprecation remains visible.

All **24 desktop/mobile Chromium workflows passed** (2.4 minutes), including the new risk recipe → immutable version → study → decision selection → revised controls path. Its screenshot review led to a concise, unit-labeled performance summary and styled decision selection instead of raw technical metric strings. Final browser and CI results are linked in the release.

[The two-year OKX experiment](portfolio-risk-research.md) captured 4,380 4H rows per BTC/ETH/SOL market and discloses all 28 pre-declared cases. All **28 frozen-capture replays matched** their ordered input/result hashes with plan, runner and implementation identities checked. Its negative/cost-sensitive results are part of acceptance evidence, not profit or target-volatility guarantees. All 128 recorded execution adjustments were below-minimum rebalance deferrals; other execution error codes totaled zero.

The [independent Linux CI](https://github.com/billpwchan/tidebench/actions/runs/37805356717) on the numerical implementation passed **730 backend tests** (201.55 seconds), **24 browser workflows** (2.8 minutes), the measured HTTP workload, offline red team, managed recovery audit and nonroot container smoke. A final presentation check adds English-to-Chinese navigation and translated risk summaries on desktop/mobile; its isolated workflow passed after correcting the test helper's localized navigation selector.

A [real public-feed integration observation](audit/v0.9.0-public-feed-paper-smoke.json) uses a new isolated database: real OKX BTC/ETH/SOL 4H packages → bound research → reviewed local-paper activation → completed current-bar risk batch → idempotent controller reload → group stop. The current strategy correctly selected cash (zero strategy orders). A separately labeled ~100 USDT manual local-paper probe then filled and closed with actual public bid/ask quotes, ending flat. It did not invent strategy signals or submit exchange orders. The observation is a single path, not a long-running forward or capacity acceptance. `scripts/smoke_portfolio_risk.py` refuses nonempty directories and accepts no venue credentials.

Schema remains 7. Primary operational account/users are preserved across the release upgrade; all browser/research QA uses separate stores.

## v0.8.0 strategy research

Local validation passed **705 backend tests** (120.83 seconds), **22 desktop/mobile Chromium workflows** (2.2 minutes), Ruff lint/format, Prettier, TypeScript and the production build. Formula, causality, captured replay, durable forward restart and historical/managed cost-aware carry counterexamples are included. The existing Starlette/HTTPX deprecation warning remains visible. Immutable-binding checks additionally reject changes to every new carry control.

The [108-case public OKX spot battery](audit/v0.8.0-strategy-battery.json) captured 2,190 4H bars each for BTC/ETH/SOL and disclosed all two-window, two-cost, neighbor and ablation outcomes. Exact local-capture replay matched every input/result hash. Default momentum was positive in 4/12 correlated scenarios; reversion in 6/12, with no consistent filter improvement. This is contrary/mixed exploratory evidence, not alpha acceptance. [Methods, sources and scope](strategy-research.md).

The isolated browser workflow loads an executable hypothesis into the actual editor, saves its immutable model parameters and verifies English/Chinese library content and mobile width. Published screenshots depict a synthetic workspace containing a separately labeled, fixed public OKX research report. Schema remains 7; no operational user/account reset is part of this release.

## v0.7.0 instrument evidence

Local final-source validation passed **684 backend tests** (111.62 seconds), **20 desktop/mobile Chromium workflows** (2.1 minutes), Ruff, Prettier, TypeScript and the production build. The two new browser paths exercise actual capture, raw inspection/download, prior-time unknown coverage, age expiry and source-product separation. The final parser review additionally passed all 17 instrument counterexamples, including contract-family conflicts and invalid/oversized source arrays. Focused research/recovery regressions passed after expanding code identity; a real-book counterexample changes only the installed observation-parser identity and confirms a new-bar group evaluation is blocked without adding commands or changing inventory. One existing Starlette/HTTPX deprecation warning remains visible.

The [public instrument audit](audit/v0.7.0-public-instrument-observations.json) captured 1,144 spot and 500 perpetual data-array rows, checked persistent canonical hashes and BTC metadata observation links, and confirmed a later response cannot supply earlier-time evidence. Its implementation-file hashes match the final tested parser/catalog. No raw venue response or private credential is published.

Schema-7 recovery tests perform an actual backup/restore while preserving newer observations and reject hash-valid conflicting identities before replacement. Cached announced expiry, preopen blanks, malformed units, duplicate rows, A → B → A and omission counterexamples are covered. See [evidence contract](instrument-evidence.md) and [v0.7 self-audit](audit/red-team-0.7.0.zh-CN.md). The exact published commit's CI independently runs the complete suite, offline load/recovery audits and container smoke.

The 608-second lifecycle soak below applies to its recorded v0.6 backend commit. It is not relabeled as a new v0.7 soak or a long-term service guarantee. Historical/dynamic universe stages and execution-capacity acceptance remain open.

## v0.6.0 · 2026-10-08

- Full local backend regression on the corrected planner: **666 passed** (123.99 seconds), followed by the additional strict-residual/minimum-deferral counterexample (**1 passed**). Ruff lint/format, frozen offline Python sync, TypeScript/Vite and Prettier passed. The existing Starlette/HTTPX deprecation remains visible.
- **18 desktop/mobile Chromium workflows passed** after adding view-failure recovery (2.2 minutes); the two recovery flows also passed separately against the final HTML cache headers. New coverage includes exact portfolio capture → review → seal → primary evaluation → verified replay, preserving a newly edited order draft when a previous submission responds late, rejected-evidence release acknowledgement and a real aborted view-file download with retained navigation and explicit reload recovery. Desktop/mobile screenshots were inspected and long-decimal criteria formatted with exact hover values.
- Real SQLite counterexamples verify concurrent single-primary admission, queue rollback, committed-failure consumption, cross-bar/alias/unbound access rejection, indicator warmup exposure, schema-6 recovery retaining consumption/exposure/trials, missing primary artifacts and conflicting/corrupt retained facts. A real independent market fill between quote enumeration and managed-command admission now retries without duplicate fills or compensation.
- Current standalone managed/recovery and offline red-team audit scripts passed against this implementation. Earlier failed v0.5 lifecycle trials are preserved; see [audit method](audit/soak-method.md). The [independent Linux CI](https://github.com/billpwchan/tidebench/actions/runs/37755474249) passed on source `5e687fd716dfe4f7ef7b224c082ba656a613e828`, including 666 backend tests, 16 browser workflows, isolated audits and the built container/readiness smoke. The later [evidence/test CI](https://github.com/billpwchan/tidebench/actions/runs/37756868967) passed **667 backend tests** and 16 browser workflows. The final presentation review added explicit rejected-result coloring, preserved pre-declared nonnegative cash-return criteria in the public screenshots, and verified the rejected-evidence acknowledgement in both desktop/mobile browsers; the numerical backend is unchanged from the elapsed audit.
- Read-only [current public OKX acceptance](audit/v0.6.0-public-okx-observations.json) verified 48 trade/mark/index bars, 6 settled funding events with exact-time mark inputs and 99 captured current tiers; raw prices are not committed. A private copy migration and the actual local schema-5→6 upgrade preserved authoritative financial/user rows, passed SQLite integrity/FK checks and retained administrator setup state.
- The corrected [elapsed lifecycle audit](audit/v0.6.0-soak-observations.json) passed **608.323 real seconds** on committed source `5e687fd716dfe4f7ef7b224c082ba656a613e828`: 3 groups, 60 separate clock steps (240 synthetic hours), 154 orders/commands, 93 completed batches, 120 funding settlements, 274 contribution events, 9 research jobs and 720 concurrent reads. All 240 observed contribution reports reconciled; no unexpected HTTP failures, duplicate keys, missing filled-order references or external network attempts occurred. A real SIGTERM restart preserved 77 prior order IDs/bodies; a fresh shutdown marker confirmed drained work, stopped supervisors and released lease. Final owner-versus-account net delta was `-1.88E-47` USDT, within the declared finite-precision acceptance bound. This sequential-admission, shared-host synthetic workload does not establish simultaneous activation capacity, long-term uptime or venue liquidity.
- Post-soak deltas are page rendering/recovery and HTML revalidation headers, with actual built-browser failure/reload checks; the numerical simulation, ledger and worker implementation remains the audited source.
- Main application bundle: about 467 kB before transfer compression; research/governance pages remain deferred bundles. This shared-host size and verification record do not establish a capacity or availability SLA.

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
