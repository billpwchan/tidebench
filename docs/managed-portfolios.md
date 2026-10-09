# Managed paper portfolios and contribution accounting

A saved portfolio now connects historical research to a managed, multi-market paper deployment. The unit of review and control is the whole portfolio: one immutable definition, one bound result, one approval and one group of owned legs. All legs use the existing shared economic account. This is local simulation; no exchange order is sent.

![Managed portfolio with actual synthetic fills and captured target evidence](assets/managed-portfolios.png)

## From hypothesis to forward evidence

1. Prepare ready research packages for 2–10 distinct USDT spot or linear perpetual markets. Their source, interval and research window must align. Perpetual packages also need mark prices, settled funding and settlement marks.
2. In **Portfolio research**, select the markets, construction, capital percentage, weights, leg signals/exits, leverage and rebalancing policy. Saving the study creates or reuses an immutable portfolio version; a revision retains its parent identity. **Saved portfolios** loads a definition without re-entering its parameters.
3. Run a full-window or chronological train/test study. The run binds the exact definition, hypothesis, package evidence and installed implementation. **Revise & research** starts a new revision rather than changing a completed study.
4. Open **Review portfolio release** on the result. Inspect the version, evaluation, costs, account risk policy, capital basis, sequential execution and residual limit. Acknowledge each displayed difference and record a review. Full-window research requires an explicit acknowledgement that it provides no independent test evidence.
5. Approve and activate. Activation rechecks the captured conditions in the same database transaction that creates the group and all leg owners. Existing inventory, pending orders, another owner, a policy change, an incompatible implementation or exhausted deployment slots blocks activation. A repeated successful activation returns the existing deployment.
6. In **Execution → Managed portfolios**, inspect actual inventory, target batches, cash scaling, residuals, child commands and their linked fills. Active and compensating groups remain listed independently of the bounded non-active history. Synthetic time is controllable in Example; OKX uses observed public data. Stop the group from this workspace when needed.
7. In **Contributions**, inspect monetary P&L by portfolio, single strategy, manual actor or legacy owner. Drill into virtual inventory and actual event history, then export the displayed evidence.

Researcher roles may create portfolio definitions and studies. Approval, activation and stopping are trading actions available to administrators and traders. All authenticated roles may inspect the evidence.

## Reference hypotheses

**Portfolio research** can load four editable starting points from [`examples/portfolios.json`](../examples/portfolios.json). They default to chronological train/test and include a benchmark comparison plan, failure regimes and rejection criteria in the hypothesis. Loading a recipe does not run those additional comparisons automatically or certify a profitable strategy.

| Starting point | Research question and main rejection test |
|---|---|
| BTC / ETH reserve-aware basket | Does daily rebalancing justify its costs versus a non-rebalanced basket and cash on the same unseen dates? Correlated drawdowns can erase diversification |
| Trend and reversion under one cash budget | Does joint BTC trend / ETH reversion improve the constrained result beyond leverage, one episode or omitted costs? Compare individual legs and joint behavior |
| Seven-day positive momentum rotation | Does positive BTC/ETH/SOL momentum persist after costs and neighboring-parameter checks? Reject dependence on one asset/lookback; the fixed universe is not historical selection evidence |
| BTC spot / perpetual lagged-funding study | Does actual funding exceed fees, slippage and hedge drift without unacceptable basis/margin/failed-hedge loss? The previous settlement is a lagged signal, not a forecast or annual yield |

## What the definition means

| Field | Meaning |
|---|---|
| `legs` | Ordered explicit universe; 2–10 distinct markets, each with weight, direction, strategy and leverage |
| `capital_pct` | Percentage of current complete account equity used as the target-sizing basis; greater than 0, at most 100 |
| `weight` | Signed target notional divided by that capital basis; leverage changes collateral, not the declared notional weight |
| `rebalance_bars` | Normal target refresh interval, anchored to the first decision; causal risk exits do not wait for it |
| `max_residual_pct` | Maximum gross absolute target shortfall/excess, valued at the observed prices, divided by captured allocated capital |
| `failure_policy` | `reduce_group`: stop additions and attempt durable reduce-only compensation after an execution failure |

The definition also captures the bar interval, construction parameters, leg exit/sizing policies, economic hypothesis and implementation identity. Decimal values use canonical strings. Identical content within a project reuses its existing version; revised content creates a new immutable revision.

The four constructions share their weight and target helpers with historical portfolio research:

| Construction | Decision rule |
|---|---|
| Fixed weights | Rebalance toward the declared signed notionals |
| Independent signals | Each leg's causal signal determines direction; an unavailable direction signal retains the actual held direction |
| Momentum | Rank contiguous lookback returns, retain up to `top_k` positive-return markets and apply their declared weights; ties are deterministic |
| Funding carry | Ordered matching spot-long/perpetual-short pair with equal absolute notional weights; open only when the most recent strictly prior settled rate meets the threshold |

Spot legs are long-only, nonnegative and unlevered. Funding carry is a hypothesis about past funding, not a forecast or guaranteed hedge. Either carry leg's exit closes both legs through the sequential execution model. Costs, basis moves, margin changes and delayed leg execution remain economic risks.

A capital percentage is a sizing instruction. It does not reserve an independent subaccount or guarantee available cash. Every group, manual order and strategy competes for the same cash and account risk limits. Contribution P&L therefore does not imply independent strategy equity or returns.

## Target and command lifecycle

Every new batch requires the same confirmed decision bar for all legs, verified decision/history hashes, source-matched post-close quotes and a complete account valuation. A missing leg or failed quote preparation cannot silently create a partially informed target. The controller prepares history outside economic locks and drains its owned work on shutdown.

The execution sequence is explicit:

1. Persist the target, decision bar, equity/capital, positions, quotes, policy hash and reduce commands before execution.
2. Execute reductions first. Each command obtains a fresh observed quote and passes the economic book's transactional ownership and risk checks.
3. Revalue the available cash after actual reductions. Apply one common cash scale to additions, including adverse bid/ask, tick rounding, fees and perpetual collateral. Persist the resulting addition quantities and commands before the first addition.
4. Split additions into bounded lot-compatible child commands under the current order-notional limit, then alternate across legs. There are at most 20 children per leg. This limits local command size; it is not a liquidity or market-impact model.
5. Compare actual inventory with the frozen target. Record gross residual quantities/notional and the percentage of allocated capital. Same-side adjustments below the minimum order size are recorded as deferred residuals; a reduction also cannot intentionally leave a below-minimum remainder. The frozen target remains unchanged, so the reviewed residual limit still applies and future decisions can accumulate a tradable difference. Full exits and side changes still reduce actual inventory. Initial undersized legs, cash-scaled minimum failures, rejected legs or an excessive residual enter compensation.

Commands have durable batch/phase/sequence identities and immutable payload hashes. After interruption, an unfinished batch is resumed before newer decisions are considered. A fill already committed to the economic book is reconciled by its original command key, even if the command acknowledgement was interrupted. Unfilled commands use new observed quotes; the frozen target and addition quantities are retained. A changed market can reject the reviewed quantity instead of quietly resizing it.

Historical studies use next-open bars; forward batches use later observed bid/ask quotes. Shared construction rules do not imply identical execution prices or identical economic outcomes.

## Failure, stop and recovery

| Observed state | Meaning and operator action |
|---|---|
| Waiting / missing decision data | No complete new target; inspect the reported market/history problem |
| Running / completed batch | The captured commands completed and residual stayed within the reviewed bound; inspect actual fills rather than assuming an exact target |
| Compensating | Additions are canceled and reduce-only commands are being attempted; inventory and the last error remain visible if compensation is blocked |
| Failed / compensated batch | The failed batch was flattened; the group does not automatically start a new strategy cycle |
| Stopped | Group and leg owners stopped; unfinished commands are canceled and filled inventory remains in the account |

Compensation is a new sequence of economic orders, not a rollback of already realized fees or P&L. It can fail because quotes, funding reconciliation or storage are unavailable. While it remains blocked, preserve the residual and cause, correct the underlying problem, and let the supervised retry reconcile the same commands. Stop the group if automatic continuation is no longer desired, then use explicit reduce-only orders to manage its retained inventory.

A protective order may reduce inventory before a frozen compensation command runs. The old command is retained as superseded; a new immutable reduce-only command records the remaining quantity. This avoids both over-closing and rewriting the original evidence.

Stopping any managed leg stops the whole group. Stopping is not a flatten instruction. Resuming a stopped or failed group through an undocumented status edit is unsupported; close/cancel retained economic exposure, review current evidence and activate a new approval. New deployments never silently adopt existing positions.

Account risk remains authoritative for each fill. A change to the approved risk policy blocks new group risk. A new installed implementation requires renewed research/version review. An account halt blocks increases but permits eligible reductions.

## Contribution P&L and reconciliation

![Actual economic-event attribution on the synthetic paper account](assets/contributions.png)

The attribution ledger records virtual ownership under the real net position. Owners are `portfolio:<group_id>`, `strategy:<deployment_id>`, `manual:<actor>` or explicit `legacy` history. It is an accounting policy, not a causal explanation of investment performance.

Entries add the actual quantity and entry cost to the originating owner. Reductions allocate across existing owners in proportion to held quantity, preserving each owner's own entry cost. Protection and liquidation retain the original economic owners rather than assigning their loss to the risk engine. Pending manual orders retain their submitting actor when they later fill. Funding follows held perpetual quantities at settlement.

| Report field | Treatment |
|---|---|
| Realized P&L | Spot entry fees are included in cost basis; perpetual trading fees are recognized when charged |
| Fees paid | Disclosed separately; not subtracted a second time from already net realized contribution |
| Funding paid | Signed actual settlement allocation; positive payment reduces net contribution |
| Unrealized P&L | Actual marked virtual quantity against its own entry cost; unavailable marks remain explicit |
| Net contribution | Realized + unrealized − funding; liquidation debt is disclosed by the account and is not deducted twice |

Owner quantities, entry cost, realized P&L, fees and funding reconcile to the economic book. The report reads the account and attribution in one SQLite snapshot. Events retain their allocation policy, after-state and any bounded finite-precision adjustment; event history is hash-verified and paginated. A full economic close leaves no virtual inventory or cost basis.

On upgrade, historical counters and inventory without ownership evidence become explicit legacy attribution. The program does not infer old strategy ownership from a name or reconstruct missing historical trades.

### Corrupt auxiliary attribution

Bad attribution must not trap economic risk. If attribution integrity or reconciliation fails, increasing risk is rejected. An otherwise valid protective reduction, funding settlement or liquidation can still commit to the economic book while attribution enters a persistent quarantine. Its original sleeves remain untouched; the transaction records the actual economic evidence and halts new risk. The contribution report then fails explicitly instead of displaying reconciled-looking numbers.

Only attribution-owned parsing/integrity failures take this path; an actual economic-book failure remains a financial error and is not hidden as attribution quarantine.

There is no automatic rebase or delete-and-retry repair. Preserve the database, backup, event evidence and incident; investigate the authoritative financial state. Recovery must use a verified appropriate backup or an explicitly reviewed repair procedure. Clearing the account halt alone does not clear contribution quarantine. See the [runbook](operations.md#contribution-quarantine).

## Evidence and current limits

The [standalone observation record](audit/v0.5.0-managed-observations.json) captures a real child-process hard exit after one committed fill, recovery to four unique fills with unchanged target/addition hashes, actual schema-5 backup replacement, and a failed leg whose compensation is initially blocked before recovery flattens it. It uses temporary SQLite workspaces and synthetic data; it is not venue, uptime or capacity evidence.

Owner reconciliation currently scans retained owner history on each economic update. Long-lived workspaces with many closed sleeves need capacity optimization and representative measurements; small-workspace recovery tests do not establish a latency SLO.

The [current self-audit](audit/red-team-0.6.0.zh-CN.md) distinguishes implemented contracts from remaining work. Portfolio final evaluations now capture complete inputs, share market-time access guards with single studies and retain newer research facts through schema-6 recovery; see [research governance](research-governance.md). Historical listing/delisting/contract changes, queue/partial-fill/impact capacity and longer external operational evidence still require their own implementation and acceptance.

Design references: [LEAN portfolio construction](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts) separates targets from execution; [NautilusTrader execution reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/) documents the need to reconcile asynchronous execution and net-position ownership. Tidebench applies those boundaries to its local transaction model without claiming equivalent engine maturity.

## Loss budgets and unavailable evidence

A leg loss budget limits target notional using account equity, its declared close-stop distance and configured round-trip costs. It does not reserve separate capital or guarantee a maximum loss. In funding carry, the tightest leg budget scales **both** weights together before unit rounding; a leg exit closes the group. Other constructions apply individual sleeve caps.

A damaged group manifest appears with its trusted group identifier and an explicit integrity error, rather than an invented definition. Other verifiable groups continue to run. Authorized users can stop the damaged group using database ownership; filled inventory and original evidence remain. New strategy risk from the invalid group is rejected transactionally. Missing account prices or perpetual tiers suppress complete net value rather than presenting a partial account total.

## Versioned allowance continuation

New studies can choose bounded allowance replanning (`reduce_group_v2_allowance`). Existing v1 releases keep frozen-order behavior. Only a demonstrated decline in shared equity and remaining capital under unchanged policies can supersede unfilled additions, at most three times; original targets, payloads, keys and residual limits remain inspectable. Hard risk guards remain binding. See [the execution policy contract](portfolio-execution-contracts.md) and [the independently measured original capital rejection](audit/fresh-institutional-review-0.10.0-capital-boundary-addendum.zh-CN.md).
