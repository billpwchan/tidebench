# Workspace API · v1

Prefix `/api/v1`; UTC Unix millisecond timestamps; decimal amounts as strings. Errors are `{error:{code,message},request_id}`. Never send exchange credentials. Interactive OpenAPI documentation is available at `/docs`; the schemas there are generated from the typed implementation.

Cookie sessions require `X-CSRF-Token` on mutations. `GET /auth/status` returns the current user, setup state and CSRF token. An optional admin service token uses `Authorization: Bearer ...` and does not require cookie CSRF. Exact hosts/origins and body-size limits apply before routing.

## Access

| Method | Path | Contract |
|---|---|---|
| GET | `/auth/status`, `/auth/session` | `{auth_required,setup_required,authenticated,user,csrf_token}` |
| POST | `/auth/setup` | `{username,password,display_name}`; optional `X-Bootstrap-Token`; first admin only; opens session |
| POST | `/auth/login` | `{username,password}`; opens session cookie |
| POST | `/auth/logout` | Revokes current session |
| POST | `/auth/password` | `{current_password,new_password}`; revokes user's sessions |
| GET / POST | `/auth/users` | Admin list/create; create includes role |
| PUT | `/auth/users/{id}` | Admin `{role,enabled}`; revokes sessions, preserves last active admin |
| POST | `/auth/users/{id}/password` | Admin `{new_password}`; resets and revokes |

Roles: admin, trader, researcher, risk_operator, viewer. See [permission matrix](operations.md#access-and-first-setup). Public `/system`, `/healthz` and `/readyz` contain no credentials.

## Catalog and market

`source` is `okx` or `example`. Instrument IDs are USDT spot or linear perpetual identifiers, subject to regional metadata. Bars are `1m`, `5m`, `15m`, `1H`, `4H`, `1Dutc`. History ranges are `[start,end)`.

| Method | Path | Contract |
|---|---|---|
| GET | `/pro/catalog/instruments?source=okx&inst_type=SWAP` | Normalized rules, contract size and units |
| GET / POST | `/pro/catalog/packages` | List or atomically prepare `{source,inst_id,bar,start,end,include_index?,idempotency_key?,dataset_ids?}` |
| GET | `/pro/catalog/packages/{id}` | Progress, component states, blockers and verified-ready research handoff |
| POST | `/pro/catalog/packages/{id}/cancel`, `/retry` | Cancel owned work or explicitly retry a failed version |
| GET | `/pro/catalog/packages/{id}/manifest` | Full hash-bound package manifest attachment |
| GET / POST | `/pro/catalog/jobs` | List or enqueue `{source,inst_id,kind,bar,start,end}`; kinds trade/mark/index/funding |
| POST | `/pro/catalog/jobs/{id}/cancel`, `/retry` | Durable cancellation or retry |
| GET | `/pro/catalog/datasets` | `{items}` of immutable manifests |
| GET | `/pro/catalog/datasets/{id}` | Rules, quality, provenance, transport, range and content hash |
| GET | `/pro/catalog/datasets/{id}/verify` | Recomputed integrity verdict |
| POST | `/pro/catalog/import` | Job identity fields plus `records` and attributed `provenance`; maximum 10 MiB request |
| GET | `/pro/market?source=okx&inst_id=BTC-USDT-SWAP` | Bid/ask, last, separate mark/index/funding timestamps, observed schedule, tiers and rules |

See [data operations](data-operations.md) for import records, coverage declarations, pagination and retention. User-imported coverage is an assertion, not an exchange-verified fact.

## Research

`POST /pro/research/runs` accepts `{dataset_id,mark_dataset_id?,funding_dataset_id?,strategy,direction,initial_cash,leverage,fee_bps,slippage_bps,liquidation_fee_bps,start_ts?,end_ts?,package_id?,package_manifest_hash?,mode,options}` and returns HTTP 202. Perpetuals require independently versioned mark and realized funding datasets. Trade and mark intervals must match; coverage and source must agree. Selected windows allow 2–98,000 bars plus up to 2,000 prior warmup bars. RSI seed dependence is recorded.

Modes: `single`, `grid`, `cost_stress`, `train_test`, `walk_forward`. Options include strategy parameter arrays, fee/slippage arrays, training fraction or bar windows, test/step/purge bars and bounded worker count. Invalid plans fail with an explicit error, never a fabricated successful result.

- `GET /pro/research/runs?source=...&limit=100&before=...` → lightweight materialized summaries and `next_cursor`; stable creation-time/ID keyset pagination, maximum 100 items.
- `GET /pro/research/runs/{id}` → status/progress/config/manifest/result/error.
- `POST /pro/research/runs/{id}/replay` → new run from captured evidence; `manifest.replay_verified` is true only when the full result SHA-256 equals the original. Divergence fails the replay.
- `GET /pro/research/runs/{id}/export` → actual JSON attachment with captured inputs and results.
- `GET /pro/research/compare?ids=id1,id2` → 2–8 completed runs plus differing-assumption warnings.

Plans deliberately have different shapes: single has `result`; grid/stress have `experiments`/`comparison`; OOS modes have `folds`/`oos_summary`. No fabricated common equity path. See [research](pro-research.md).

## Execution

`POST /pro/execution/orders/preview` validates `{source,inst_id,side,quantity,leverage,reduce_only,margin_mode:'isolated',order_type,limit_price?,stop_price?}` and returns actual current assumptions and estimated account effects. Quantity is spot base units or perpetual contracts.

`POST /pro/execution/orders` accepts the same command plus required `Idempotency-Key` (8–128 safe ASCII characters). Numeric formatting is canonicalized. A retry returns the original order; a different payload conflicts. Market orders fill locally; limit/stop orders remain pending until an observed trigger and current risk checks permit the full fill. Preview is not an execution guarantee.

- `GET /pro/execution/account?source=...`: unified account, positions, cash/reservations, margin, funding, fees and liability. Unavailable valuation remains explicit.
- `GET /pro/execution/analytics?source=...`: captured asset/market exposure, concentration, isolated maintenance and default price shocks.
- `POST /pro/execution/analytics`: `{source,scenarios:[{name,parallel_pct,asset_pct?,market_pct?}]}`; custom read-only hypotheses. All roles can analyze; cookie CSRF still applies. Decimal strings, null reasons, input capture and hash are returned. See [portfolio risk](portfolio-risk.md).
- `GET /pro/execution/orders`, `/ledger`: order lifecycle and native asset journal.
- `POST /pro/execution/orders/{id}/cancel`: releases pending reservation.
- `GET / POST /pro/execution/deployments`: list/start forward strategies.
- `POST /pro/execution/deployments/{id}/stop`: stops new strategy commands, retains positions.
- `GET / PUT /pro/execution/risk?source=...`: current transactional policy.
- `POST /pro/execution/halt`: `{source,active,reason}`; increasing risk halts, reductions remain possible.

Historical replay and forward simulation have different fill observations. Neither endpoint sends an exchange order.

## Operations

`GET /pro/ops` returns measured health, feed ages, jobs, checkpoints, storage, backups, execution summary and request metrics. `GET /pro/ops/audit` returns audit events. Admin-only `GET /pro/ops/metrics` exposes Prometheus counters/histogram.

Admin `POST /pro/ops/backups/create`, `GET /pro/ops/backups/{id}/verify` and `POST /pro/ops/restore {backup_id,confirmation:'RESTORE'}` implement verified recovery. Recovery rejects concurrent attempts and enters maintenance. It revokes all sessions, cancels pending orders, stops strategies and halts execution. See [recovery procedure](operations.md#backups-and-recovery).

## Strategy, governance and forward evidence (v0.4)

| Method | Path | Contract |
|---|---|---|
| GET / POST | `/pro/strategies` | List/create `{name,hypothesis,definition}`; definition binds product/bar/direction/leverage/strategy |
| GET | `/pro/strategies/{id}`, `/pro/strategy-versions/{id}` | Project revisions or verified immutable version |
| POST | `/pro/strategies/{id}/versions` | `{hypothesis,definition,parent_id?}`; new immutable revision |
| POST | `/pro/execution/releases/preview` | `{run_id,selection}`; config, blockers, acknowledgements and preview hash |
| GET / POST | `/pro/execution/releases` | History or `{run_id,selection,preview_hash,acknowledgements,review}` approval |
| POST | `/pro/execution/releases/{id}/activate` | Atomic condition revalidation and idempotent deployment |
| GET / POST | `/pro/research/holdouts` | List or freeze version-bound `test_config`, benchmark and rejection plan |
| GET | `/pro/research/governance/{project_id}` | Trial/candidate counts and holdout usage |
| GET / POST | `/pro/research/portfolios` | List/create historical portfolios; 2–10 ready aligned package legs and shared policy |
| GET | `/pro/research/portfolios/{id}` | Status, manifest and hash-verified result |
| GET / POST | `/pro/execution/clock` | Durable synthetic time; mutation `{expected_revision,speed?,step_ms?}` |
| GET | `/pro/execution/performance?source=...&limit=500&before=...` | Observations, page-scoped summary and `next_before` |
| GET | `/pro/execution/deployments/{id}/decisions?limit=100&before=...` | Descending bars with intent and actual phase orders |
| POST | `/pro/ops/incidents/{id}/acknowledge` | Admin `{reason}`; failing condition stays active |

Research additionally accepts `strategy_version_id` and `holdout_id`. Detail `?variant=experiment-2` retains all candidate metrics but selected financial arrays; full export retains exact shared inputs. Programs/exits/sizing are specified in [strategy workflows](strategy-workflows.md). Portfolio `evaluation:'train_test'`, `train_pct` and `embargo_bars` fix construction and reset accounts; top-level financials cover test only, without test-score optimization.

## Compatibility

The original spot `/backtests`, `/paper/*` and `/risk` APIs are retained; their [legacy contract](api-legacy-v0.1.md) applies with current authorization. Legacy paper capital is separate and is not silently merged into the professional portfolio. New clients should use `/pro/*`.
