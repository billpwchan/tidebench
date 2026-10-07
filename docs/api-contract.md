# API contract · v1

All endpoints are prefixed `/api/v1`. Decimal amounts are **strings**, timestamps UTC Unix milliseconds, percentage metrics floats. Errors: `{error: {code: string, message: string}, request_id: string}`. Validation errors use the same shape (422). Send `Authorization: Bearer <token>` if configured. Never send exchange credentials. Source is exactly `okx` or `example`; the two paper accounts are entirely separate. Example data is synthetic and not a real price or trading record.

## System and market

- `GET /system` → `{name:"Tidebench",version:"0.1.0",market_region:"global",execution:"local-paper",auth_required:boolean,time:number,capabilities:string[]}`. Public endpoint, no secrets.
- `GET /strategies` → `{items:[{kind:"sma_cross"|"rsi_reversion"|"buy_hold",name,description,defaults:Strategy}]}`.
- `GET /market/instruments?source=okx` → `{source,items:[{inst_id,base,quote,tick_size,lot_size,min_size,state}]}`.
- `GET /market/tickers?source=okx` → `{source,as_of,transport:"rest"|"example",items:[{inst_id,last,bid,ask,open_24h,high_24h,low_24h,volume_24h,change_pct,ts}],warning:null|string}`.
- `GET /market/candles?source=okx&inst_id=BTC-USDT&bar=1H&limit=720` → `{source,fetched_at,inst_id,bar,dataset_hash,candles:[{ts,open,high,low,close,volume,confirmed}],quality:object,warning:null|string}`.

Symbols: BTC-USDT, ETH-USDT, SOL-USDT, OKB-USDT, DOGE-USDT. Bars: `15m`, `1H`, `4H`, `1Dutc`. Count: 60–2000, default 720. Prices from confirmed bars only. All fields from the exchange must pass validation; errors must not be disguised as zero or sample data. Frontend defaults to `okx`, with an explicit action to switch to Example if unavailable.

## Research

`Strategy = {kind:string,fast:number,slow:number,rsi_period:number,entry:string,exit:string,allocation:string}`. Defaults: kind=sma_cross, fast=12, slow=26, rsi_period=14, entry="30", exit="60", allocation="0.25". Allocation is a fraction, 0.25 means 25%.

`RunConfig = {source,inst_id,bar,limit,strategy,initial_cash:string,fee_bps:string,slippage_bps:string}`. Defaults: BTC-USDT,1H,720,10000,10,5.

- `POST /backtests` body `RunConfig` → 202 Run (queued).
- `GET /backtests?source=example` → `{items:Run[]}` newest first, max 50.
- `GET /backtests/{id}` → Run.
- `POST /backtests/{id}/replay` → 202 new Run using the exact saved dataset/instrument/config, with manifest `replay_of`. No network needed for replay.
- `GET /backtests/{id}/export` → JSON attachment with run, saved input data and manifest. Local research export; downstream data rights still apply.

`Run = {id,status:"queued"|"running"|"completed"|"failed",created_at,updated_at,config:RunConfig,result:null|Result,error:null|string,manifest:null|object}`.

`Result = {metrics:{total_return_pct,benchmark_return_pct,max_drawdown_pct,sharpe:number|null,sharpe_reason:null|string,trades:number,fees_paid:string,final_equity:string,initial_cash:string,realized_pnl:string},equity:[{ts,equity:string,benchmark:string,drawdown_pct:number}],trades:[{ts,side:"buy"|"sell",quantity:string,price:string,fee:string,cash:string}],assumptions:object,quality:object}`.

`null` Sharpe must render as unavailable with reason, not zero. No annualized marketing numbers. Close(t) signals execute at earliest open(t+1), net of fees/slippage. Run manifest exposes version, dataset hash, source, interval, range and instrument rules. A run is not successful merely because the request was accepted. Poll detail every 1s only while queued/running.

## Paper desk

- `GET /paper/account?source=okx` → `{source,initial_cash,cash,equity:null|string,realized_pnl,unrealized_pnl:null|string,fees_paid,valuation_status:"fresh"|"stale"|"unavailable"|"example",positions:[{inst_id,quantity,avg_cost,mark:null|string,market_value:null|string,unrealized_pnl:null|string,as_of:null|number}],as_of:number}`. 10000 USDT starting cash; no invented profits. On mark failure, equity is null if there are positions, while balances remain available.
- `GET /paper/orders?source=okx` → `{items:[{id,source,inst_id,side,quantity,price,fee,notional,status:"filled",created_at,origin:"manual"|"strategy",reason:string|null}]}` newest first, max 100.
- `POST /paper/orders` body `{source,inst_id,side:"buy"|"sell",quantity:string}`. Required `Idempotency-Key` header, 8–128 safe ASCII chars. → order (201 new, 200 replay). Same key with a different payload =409. Errors record audit reasons, reject stale OKX quotes, overspending, overselling, risk breaches. Quotes use ask for buy / bid for sell, 5bps adverse slippage plus 10bps fee. Precision is enforced. This is local simulated execution, not OKX Demo Trading.
- `GET /paper/deployments?source=okx` → `{items:Deployment[]}`.
- `POST /paper/deployments` body `{source,inst_id,bar,strategy:Strategy}` → 201 Deployment.
- `POST /paper/deployments/{id}/stop` → Deployment.

`Deployment = {id,source,inst_id,bar,strategy:Strategy,status:"running"|"stopped",created_at,updated_at,last_bar:null|number,last_error:null|string}`. Strategy runner evaluates only new confirmed bars (~20s polling); uses the same pure target-position logic as research, and fills from an observed quote after the signal close. No historical backfill fills. Example feed is fixed, so each deployment evaluates once and then waits; UI must say so. No repeated per-bar rebalance. All manual and strategy commands use the same ledger and risk gates.

## Risk and audit

- `GET /risk?source=okx` → `{source,kill_switch:boolean,max_order_notional:string,max_position_pct:number,max_daily_loss_pct:number,updated_at:number}`. Percentage fields here are 0–100. Defaults 2500,50,5.
- `PUT /risk` body `{source,max_order_notional:string,max_position_pct:number,max_daily_loss_pct:number}` → Risk. Limits govern buys/increased risk. Sales remain allowed under ordinary limits, but kill switch blocks all fills; it never silently liquidates positions.
- `POST /risk/kill-switch` body `{source,active:boolean,reason:string}` → Risk. A durable halt checked in the same transaction as fills. Does not sell assets. Stop strategy evaluation when active; resume only evaluates the newest bar, not missed bars.
- `GET /audit?source=okx&limit=50` → `{items:[{id,source,ts,kind,summary,details:object}]}`.

## Client behavior

TanStack Query; central `api.ts`; Vite `/api` proxy to localhost:8000. Source is a local UI preference, no global backend mutation. On source change query keys include source, so old responses cannot appear as new source. Mark `Example · synthetic` and `Local paper` throughout. Tokens in sessionStorage only, optional settings form; never include token in URLs. All export controls must download actual server data. No fake latency, confidence, accuracy, sparkline or portfolio values.
