# Attributed historical lifecycle

Historical portfolio research now supports a bounded, explicitly selected universe with per-market listing warmup, suspension and resume, changing rules, custody after delisting, attributed cash settlement, and exact same-identity linear contract-unit conversion. Static studies retain their captured semantics. This is an execution and evidence contract; it does not establish comprehensive historical venue coverage.

Use `universe_mode: "historical_lifecycle"`, `lifecycle_warmup_bars` (default 2), and each leg's `lifecycle_events`. One-use holdout requests carry the same scenario fields and an event dictionary keyed by market. Packages may have different verified windows. Their union defines the study clock; missing bars are never fabricated. Listed markets build their own contiguous causal warmup. Momentum and covariance rank only eligible markets with enough observations. Suspended holdings retain quantity, basis and posted margin. A missing fresh valuation or expired rule coverage cannot be used as a trading quote.

Each event has an explicit market, kind, `effective_ts`, `known_at`, and source containing official HTTPS URL, publication/capture dates, captured `raw_content`, its SHA-256, and evidence type. Live metadata requires a finite `valid_until`. `effective_until` retains uncertain suspension/removal windows: the market is unknown during the announced interval. The validator checks hashes, source domain, market, ordering conflicts and metadata; it does not authenticate a submitter's historical timestamp or independently prove that a supplied extract was published by the venue. `synthetic_fixture` belongs only to the example source. The complete bodies and hashes are frozen in the computation artifact. Importing new facts or changing the current catalog cannot change a sealed replay.

| Event | Inventory and eligibility |
| --- | --- |
| `listing`, `resume` | Valid metadata plus fresh causal warmup before execution |
| `suspend` | No execution; independent fresh captured marks can value inventory |
| `rules` | Positive execution increments and supported tiers; an economic unit change requires an explicit conversion |
| `delist` | No new exposure; custody is retained without a supported attributed settlement |
| `cash_settlement` | Explicit USDT price and cash fee; an idempotent cash/basis/margin/PnL journal, never a synthetic market order |
| `unit_conversion` | Same `inst_id` linear perpetual only; quantity ratio must conserve exact signed base exposure and resolve new lots; entry price, basis and margin remain unchanged |

Contract-unit conversion preserves underlying base-price signal history. Spot redenomination and symbol renames are unsupported because their price-series and economic identity mapping are not implemented. Those events preserve held inventory and block complete economic results. Inventory mutations known after their effective time cannot retrospectively rewrite intervening fills or funding; late inventory facts remain unresolved. Cash settlements require an attributed actual price available at the modeled accounting boundary. A future formula or an earlier trading close is not sufficient price evidence.

Unresolved custody, valuation, conversion or unexecutable liquidation marks `economic_state: "incomplete_lifecycle"`. Final equity, return and drawdown are `null`; original quantity and cash remain visible. Financial metrics are unavailable rather than computed from an invented zero value or last price. A holdout rejects this known failure even when the statistical sample is insufficient. Release preview hard-blocks incomplete economics and the current managed controller's unsupported historical lifecycle execution; the difference appears in `lifecycle_execution_support`. The historical engine does not grant forward support by implication.

## Official source examples

[Three spot removal facts](../examples/lifecycle/okx-spot-removals-2025-03-20.json) record XR/USDT, GOAL/USDT and KP3R/USDT from an [OKX announcement](https://www.okx.com/en-au/help/okx-to-delist-xr-goal-kp3r-lbr-lamb-bzz-and-gpt-spot-trading-pairs). Publication is dated March 13, 2025; removal is within 08:00–09:00 UTC on March 20. The source describes retained token custody and provides no cash settlement price. The sample preserves that interval and missing economics. It contains no listing history or executable dataset.

The [June 30, 2025 perpetual removal announcement](https://www.okx.com/en-sg/help/okx-to-delist-several-margin-trading-pairs-and-perpetual-futures-20250630) gives a preceding-hour index-average delivery convention while reserving an adjustment for abnormal index prices. It supplies no realized delivery price. That convention cannot create a `cash_settlement` observation by itself.

The [XAUT to XAU announcement](https://www.okx.com/en-gb/help/okx-will-rename-xautusdt-perpetual-to-xauusdt-perpetual) describes retained position quantity and entry price, a changed risk unit, new API identity and a trading pause whose resume time could change. A same-symbol quantity ratio is therefore not a valid substitute for that rename. The [API change log](https://www.okx.com/docs-v5/log_en/) also describes pre-listing rows with incomplete numeric rules and rebase states. Such observations cannot qualify a market for execution.

## Accounting and acceptance

Two append-only SQLite tables preserve captured events and applied lifecycle transactions. Inventory accounting runs under the same 50-digit domain and `BEGIN IMMEDIATE` journal transaction as the simulation book. Application keys derive from the captured event body, so a retry cannot duplicate cash or conversion effects. Fees, settlement profit, released basis/margin, and insurance debt remain actual account values. Native-asset ledger sums must balance.

Synthetic three-market tests cover staggered listings, unknown membership, suspend/resume warmup, finite coverage expiry, missing marks, retained delisted custody, settlement profit/fees, swap long/short/insolvent settlement, exact contract-unit conversion and unchanged signals, rejected bad ratios, immutable raw captures, insufficient-sample holdout rejection and source-pinned replay after new imports. These are falsifiable model tests, not a claim of multi-year real-market validation.

The desktop and mobile browser acceptance imports the three synthetic market arrays through the actual interface, verifies source-byte tamper rejection without erasing prior facts, runs the study, inspects a suspended as-of boundary, switches English and Chinese, restores the same source bodies when revising, and seals the matching lifecycle scenario and per-market source hashes in a one-use holdout. Horizontal evidence tables scroll within the mobile viewport. Screenshots are [desktop](assets/lifecycle-desktop.png) and [mobile](assets/lifecycle-mobile.png).
