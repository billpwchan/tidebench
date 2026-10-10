# v0.13 acceptance history

This record retains material intermediate failures as well as the final validation. Tests use disposable synthetic data; the primary runtime account is not a test fixture.

## Material intermediate observations

- Source and browser review confirmed that research continuation displaced account risk and the current book, compact execution promoted the entry ticket above the book, and five-decimal formatting displayed positive micro prices as zero. The revised hierarchy puts money and the book first, opens the ticket explicitly, and separates price/quantity precision from USDT money formatting.
- An intermediate TypeScript build rejected an unknown value used as React content. The rendering was corrected before the successful build. The first combined bundle exceeded Vite's 500 kB warning threshold; deferring the overview and legacy pages reduced the main bundle rather than raising the warning threshold.
- The first fixture-based data reload check called an obsolete dataset route. It was corrected to the actual catalog route; that failed run is not treated as product acceptance.
- Late data-package responses could still cross a newer preparation context or an already changed workspace. Shared request ownership, checks on both sides of awaited catalog retrieval, and the current workspace reference now preserve the current task.
- An independent source review reproduced a valid Example quote being rejected after the synthetic clock advanced beyond wall time. The helper now respects Example time while rejecting stale or anomalously future OKX quotes. Independent browser cases exercise both sources.
- The same review found that a negative optional budget, even after its disclosure closed, participated in native HTML form validation and blocked a valid native order preview. The optional decimal input now uses helper validation; the regression requires a real preview request from a valid native limit order with that invalid helper value retained.
- Fixed native price input steps also rejected valid tiny instrument ticks. Limit and stop controls now use the selected instrument's tick rather than a universal decimal floor.
- One hundred compact order records made the outer mobile page excessively long. A bounded, keyboard-focusable record viewport retains all records, filtering, stable action identities and pagination; changing page resets its own scroll position.
- The first complete browser pass exposed a real mobile portfolio review problem: a wide flex child and sticky evidence cells could intercept a normal approval click. The release section is constrained to the available width, and table painting stays inside its scroll surface. Normal clicks and width assertions remain in the acceptance path; force clicks and larger timeouts are not substituted.
- Missing legacy cost/capital fields displayed literal `undefined` in result/history summaries. They now display unavailable values rather than inventing zero costs.
- Existing browser tests also needed to follow the deliberate new interaction paths: open Snapshot basis, account clock or risk controls, explicitly open New order, and use visible compact record actions. Account/ownership, protective reduction, controller stop, frozen research and late-response assertions remain intact.

## Final validation

The full backend regression completed: **997 passed** in **271.38 seconds**, with the existing Starlette/HTTPX test-client deprecation warning. Ruff lint and formatting passed for 105 Python files; frozen offline dependency synchronization passed. Backend numerical behavior is unchanged in this release.

The first full browser round collected 196 paths: **165 passed, 31 failed in 22.1 minutes**. Twenty-nine failures were stale entry/selectors/fixture routes after the deliberate layout changes; two were the reproduced mobile release-panel click interception. The latter are product defects, not test selectors. Their normal-click targeted verification passed **2/2 in 6.7 seconds** after correction.

Actual in-app browser inspection at 1111px also found that the parallel research breakpoint was too wide. The corrected breakpoint and the corresponding alignment/overflow check are included in the frozen build.

The final frozen-build browser round passed **208/208 in 8.7 minutes**. TypeScript, Vite production build and Prettier passed. The build's main JavaScript is 414.02 kB / 134.07 kB gzip, without increasing the chunk warning threshold. Final screenshots were reviewed at desktop and phone widths. No numerical, receipt, permission, frozen-report or account assertion was removed to accommodate the redesign.

The actual primary workspace was gracefully stopped, privately snapshotted and fast-forwarded to the tested source. After frozen offline environment synchronization, health/readiness passed at v0.13.0. All 37 frontend files matched the tested build; integrity/FK checks and all 24 authoritative before/after row hashes passed after startup. Schema and first-user setup were retained; no user/account initialization was introduced. Publication scanning checked 448 tracked/new files with zero credential/private-runtime hits and 124 byte-preserved historical report/image files. Exact-revision external CI is required before tagging the release.
