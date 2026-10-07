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
| GET / POST | `/pro/catalog/jobs` | List or enqueue `{source,inst_id,kind,bar,start,end}`; kinds trade/mark/index/funding |
| POST | `/pro/catalog/jobs/{id}/cancel`, `/retry` | Durable cancellation or retry |
| GET | `/pro/catalog/datasets` | `{items}` of immutable manifests |
| GET | `/pro/catalog/datasets/{id}` | Rules, quality, provenance, transport, range and content hash |
| GET | `/pro/catalog/datasets/{id}/verify` | Recomputed integrity verdict |
| POST | `/pro/catalog/import` | Job identity fields plus `records` and attributed `provenance`; maximum 10 MiB request |
| GET | `/pro/market?source=okx&inst_id=BTC-USDT-SWAP` | Bid/ask, last, separate mark/index/funding timestamps, observed schedule, tiers and rules |

See [data operations](data-operations.md) for import records, coverage declarations, pagination and retention. User-imported coverage is an assertion, not an exchange-verified fact.

## Research

`POST /pro/research/runs` accepts `{dataset_id,mark_dataset_id?,funding_dataset_id?,strategy,direction,initial_cash,leverage,fee_bps,slippage_bps,liquidation_fee_bps,start_ts?,end_ts?,mode,options}` and returns HTTP 202. Perpetuals require independently versioned mark and realized funding datasets. Trade and mark intervals must match; coverage and source must agree. Selected windows allow 2–98,000 bars plus up to 2,000 prior warmup bars. RSI seed dependence is recorded.

Modes: `single`, `grid`, `cost_stress`, `train_test`, `walk_forward`. Options include strategy parameter arrays, fee/slippage arrays, training fraction or bar windows, test/step/purge bars and bounded worker count. Invalid plans fail with an explicit error, never a fabricated successful result.

- `GET /pro/research/runs` → summaries with mode-appropriate metrics.
- `GET /pro/research/runs/{id}` → status/progress/config/manifest/result/error.
- `POST /pro/research/runs/{id}/replay` → new run from captured evidence.
- `GET /pro/research/runs/{id}/export` → actual JSON attachment with captured inputs and results.
- `GET /pro/research/compare?ids=id1,id2` → 2–8 completed runs plus differing-assumption warnings.

Plans deliberately have different shapes: single has `result`; grid/stress have `experiments`/`comparison`; OOS modes have `folds`/`oos_summary`. No fabricated common equity path. See [research](pro-research.md).

## Execution

`POST /pro/execution/orders/preview` validates `{source,inst_id,side,quantity,leverage,reduce_only,margin_mode:'isolated',order_type,limit_price?,stop_price?}` and returns actual current assumptions and estimated account effects. Quantity is spot base units or perpetual contracts.

`POST /pro/execution/orders` accepts the same command plus required `Idempotency-Key` (8–128 safe ASCII characters). Numeric formatting is canonicalized. A retry returns the original order; a different payload conflicts. Market orders fill locally; limit/stop orders remain pending until an observed trigger and current risk checks permit the full fill. Preview is not an execution guarantee.

- `GET /pro/execution/account?source=...`: unified account, positions, cash/reservations, margin, funding, fees and liability. Unavailable valuation remains explicit.
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

## Compatibility

The original spot `/backtests`, `/paper/*` and `/risk` APIs are retained; their [legacy contract](api-legacy-v0.1.md) applies with current authorization. Legacy paper capital is separate and is not silently merged into the professional portfolio. New clients should use `/pro/*`.
