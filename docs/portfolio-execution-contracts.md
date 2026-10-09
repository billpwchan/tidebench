# Reviewed portfolio execution policies

A portfolio version binds its execution policy together with its target rules, costs, capital percentage and residual limit. Historical research and managed local paper use the same ordered planning functions. A newer default never rewrites an existing study, release, batch, target or order key.

| Policy | When a frozen addition no longer passes its capital allowance |
|---|---|
| `reduce_group_v1` — frozen orders | Preserve the frozen quantities. A genuine admission rejection stops additions and enters group compensation. This behavior remains available for already bound versions. |
| `reduce_group_v2_allowance` — bounded allowance replanning | At most three explicitly recorded replacements may shrink still-unfilled additions after a complete fresh account valuation shows shared equity and remaining allowance have fallen. Every replacement still passes the real transaction guards and original residual/minimum rules. |
| Raw legacy studies without a shared policy | Preserve original results and identity. Create a new immutable revision and evaluate it under a reviewed shared policy before release. |

## Why a replacement can be needed

The [independent capital-boundary assessment](audit/fresh-institutional-review-0.10.0-capital-boundary-addendum.zh-CN.md) measured two other-owner fills reducing shared equity by 0.0095525937388882 USDT after an ETH/SOL plan was frozen. The original ETH child included its own fee and spread in planning, but the later projected fill would exceed its 8% capital ceiling by 0.0001422205681272 USDT. The transaction guard correctly refused it. Under v1, refusal means compensation; v2 offers a separately reviewed, smaller continuation.

A small amount does not create a tolerance exception. The program never raises a capital limit to make a planned fill pass.

## V2 eligibility and evidence

A replacement requires the actual `portfolio_capital_limit` rejection, a complete fresh account, a strictly smaller remaining allowance and lower shared equity, and unchanged captured risk/capital policy identities and commitment percentage. Funding publication waits retain their own pending state; they do not become allowance replacements. Quote/metadata errors, order-size, account-wide, underlying, gross and policy failures retain their existing rejection behavior.

Only quantities still unfilled can be superseded. Committed fills are reconciled before planning, including interruption between book commit and command projection. The new plan is no larger than the remaining frozen quantity in any market and cannot turn a maintenance adjustment into a new side or silently remove an unenterable new leg. The original target, additions and payload hashes remain unchanged. Original minimum-entry requirements and maximum target residual remain binding. An ineligible plan, exhausted replacement budget or genuine risk failure enters the shared compensation policy.

Each continuation has fresh smaller quantities and new idempotency keys, linked original/replacement command identities, captured quotes, before/after capital budgets, unchanged policy hashes, projected target residuals and a content hash linked to the previous plan. The old commands remain visibly superseded. Inspect the batch evidence and actual orders to distinguish desired targets, original frozen requests and economic fills.

Stop cancels unfinished continuation without selling inventory. Retained inventory and pending funding keep the capital promise until independently verified resolution. Restart reconciles existing receipts rather than creating another fill under an old key. Recovery replaces the financial epoch, stops groups, cancels unfinished commands and preserves the captured evidence for inspection.

## Research and observation scope

A changed execution policy changes the frozen research identity even if its numerical return happens to stay equal. Exact replay uses the captured policy and implementation; older report hashes remain intact. The real-account boundary reproduction, focused version/lineage/restart tests, matching-source allocation replay and separate elapsed run are recorded in [verification](verification.md).

This policy is for sequential local-paper groups. It supplies neither venue partial fills nor queue priority, price impact, simultaneous activation capacity or profitable strategy evidence. A cash-reserve hypothesis is a separate strategy and cannot replace an adverse full-allocation experiment.

[Actual desktop review](assets/execution-policy-review-desktop.png) · [mobile form](assets/execution-policy-form-mobile.png). These captures use the explicitly labeled synthetic Example workspace.
