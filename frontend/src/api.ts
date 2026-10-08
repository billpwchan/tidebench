export type Source = 'okx' | 'example';
export type Bar = '15m' | '1H' | '4H' | '1Dutc';
export type Strategy = {
  kind: string;
  fast: number;
  slow: number;
  rsi_period: number;
  entry: string;
  exit: string;
  allocation: string;
  window?: number;
  z_entry?: string;
  z_exit?: string;
  atr_period?: number;
  stop_loss_pct?: string;
  take_profit_pct?: string;
  trailing_stop_pct?: string;
  max_holding_bars?: number;
  risk_per_trade_pct?: string;
  rules?: Record<string, unknown>[];
};
export type RunConfig = {
  source: Source;
  inst_id: string;
  bar: Bar;
  limit: number;
  strategy: Strategy;
  initial_cash: string;
  fee_bps: string;
  slippage_bps: string;
};
export type Instrument = {
  inst_id: string;
  base: string;
  quote: string;
  tick_size: string;
  lot_size: string;
  min_size: string;
  state: string;
};
export type Ticker = {
  inst_id: string;
  last: string;
  bid: string;
  ask: string;
  open_24h: string;
  high_24h: string;
  low_24h: string;
  volume_24h: string;
  change_pct: number;
  ts: number;
};
export type Candle = {
  ts: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  confirmed: boolean;
};
export type CandleSet = {
  source: Source;
  fetched_at: number;
  inst_id: string;
  bar: Bar;
  dataset_hash: string;
  candles: Candle[];
  quality: Record<string, unknown>;
  warning: string | null;
};
export type Trade = {
  ts: number;
  side: 'buy' | 'sell';
  quantity: string;
  price: string;
  fee: string;
  cash: string;
};
export type Result = {
  metrics: {
    total_return_pct: number;
    benchmark_return_pct: number;
    max_drawdown_pct: number;
    sharpe: number | null;
    sharpe_reason: string | null;
    trades: number;
    fees_paid: string;
    final_equity: string;
    initial_cash: string;
    realized_pnl: string;
  };
  equity: { ts: number; equity: string; benchmark: string; drawdown_pct: number }[];
  trades: Trade[];
  assumptions: Record<string, unknown>;
  quality: Record<string, unknown>;
};
export type Run = {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: number;
  updated_at: number;
  config: RunConfig;
  result: Result | null;
  error: string | null;
  manifest: Record<string, unknown> | null;
};
export type Position = {
  inst_id: string;
  quantity: string;
  avg_cost: string;
  mark: string | null;
  market_value: string | null;
  unrealized_pnl: string | null;
  as_of: number | null;
};
export type Account = {
  source: Source;
  initial_cash: string;
  cash: string;
  equity: string | null;
  realized_pnl: string;
  unrealized_pnl: string | null;
  fees_paid: string;
  valuation_status: 'fresh' | 'stale' | 'unavailable' | 'example';
  positions: Position[];
  as_of: number;
};
export type Order = {
  id: string;
  source: Source;
  inst_id: string;
  side: 'buy' | 'sell';
  quantity: string;
  price: string;
  fee: string;
  notional: string;
  status: 'filled';
  created_at: number;
  origin: 'manual' | 'strategy';
  reason: string | null;
};
export type Deployment = {
  id: string;
  source: Source;
  inst_id: string;
  bar: Bar;
  strategy: Strategy;
  status: 'running' | 'stopped';
  created_at: number;
  updated_at: number;
  last_bar: number | null;
  last_error: string | null;
};
export type Risk = {
  source: Source;
  kill_switch: boolean;
  max_order_notional: string;
  max_position_pct: number;
  max_daily_loss_pct: number;
  updated_at: number;
};
export type Audit = {
  id: string;
  source: Source;
  ts: number;
  kind: string;
  summary: string;
  details: Record<string, unknown>;
};
export type System = {
  name: string;
  version: string;
  market_region: string;
  execution: string;
  auth_required: boolean;
  time: number;
  capabilities: string[];
};
export const defaultStrategy: Strategy = {
  kind: 'sma_cross',
  fast: 12,
  slow: 26,
  rsi_period: 14,
  entry: '30',
  exit: '60',
  allocation: '0.25',
};
const TOKEN_KEY = 'tidebench:session-token';
export const readToken = () => sessionStorage.getItem(TOKEN_KEY) ?? '';
export function saveToken(value: string) {
  if (value.trim()) sessionStorage.setItem(TOKEN_KEY, value.trim());
  else sessionStorage.removeItem(TOKEN_KEY);
}
export class ApiError extends Error {
  status: number;
  code: string;
  requestId?: string;
  constructor(message: string, status: number, code: string, requestId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}
let csrfToken: string | null = null;
export function setCsrfToken(value: string | null) {
  csrfToken = value;
}
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  const token = readToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (
    csrfToken &&
    options.method &&
    !['GET', 'HEAD', 'OPTIONS'].includes(options.method.toUpperCase())
  )
    headers.set('X-CSRF-Token', csrfToken);
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { credentials: 'same-origin', ...options, headers });
  } catch {
    throw new ApiError(
      'The API is unreachable. Check that the Tidebench server is running.',
      0,
      'network_error',
    );
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/status')
      window.dispatchEvent(new Event('tidebench:auth-required'));
    const data = await response.json().catch(() => null);
    throw new ApiError(
      data?.error?.message ?? `Request failed (${response.status}).`,
      response.status,
      data?.error?.code ?? 'request_failed',
      data?.request_id,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
const q = (values: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(values).map(([k, v]) => [k, String(v)])).toString();
export const api = {
  system: () => request<System>('/system'),
  strategies: () =>
    request<{ items: { kind: string; name: string; description: string; defaults: Strategy }[] }>(
      '/strategies',
    ),
  instruments: (source: Source) =>
    request<{ source: Source; items: Instrument[] }>(`/market/instruments?${q({ source })}`),
  tickers: (source: Source) =>
    request<{
      source: Source;
      as_of: number;
      transport: string;
      items: Ticker[];
      warning: string | null;
    }>(`/market/tickers?${q({ source })}`),
  candles: (source: Source, inst_id: string, bar: Bar, limit = 720) =>
    request<CandleSet>(`/market/candles?${q({ source, inst_id, bar, limit })}`),
  runs: (source: Source) => request<{ items: Run[] }>(`/backtests?${q({ source })}`),
  run: (id: string) => request<Run>(`/backtests/${encodeURIComponent(id)}`),
  createRun: (config: RunConfig) =>
    request<Run>('/backtests', { method: 'POST', body: JSON.stringify(config) }),
  replayRun: (id: string) =>
    request<Run>(`/backtests/${encodeURIComponent(id)}/replay`, { method: 'POST' }),
  account: (source: Source) => request<Account>(`/paper/account?${q({ source })}`),
  orders: (source: Source) => request<{ items: Order[] }>(`/paper/orders?${q({ source })}`),
  createOrder: (
    data: { source: Source; inst_id: string; side: 'buy' | 'sell'; quantity: string },
    key: string,
  ) =>
    request<Order>('/paper/orders', {
      method: 'POST',
      body: JSON.stringify(data),
      headers: { 'Idempotency-Key': key },
    }),
  deployments: (source: Source) =>
    request<{ items: Deployment[] }>(`/paper/deployments?${q({ source })}`),
  deploy: (data: { source: Source; inst_id: string; bar: Bar; strategy: Strategy }) =>
    request<Deployment>('/paper/deployments', { method: 'POST', body: JSON.stringify(data) }),
  stop: (id: string) =>
    request<Deployment>(`/paper/deployments/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  risk: (source: Source) => request<Risk>(`/risk?${q({ source })}`),
  saveRisk: (data: Omit<Risk, 'kill_switch' | 'updated_at'>) =>
    request<Risk>('/risk', { method: 'PUT', body: JSON.stringify(data) }),
  halt: (data: { source: Source; active: boolean; reason: string }) =>
    request<Risk>('/risk/kill-switch', { method: 'POST', body: JSON.stringify(data) }),
  audit: (source: Source) => request<{ items: Audit[] }>(`/audit?${q({ source, limit: 50 })}`),
  async exportRun(id: string) {
    const headers = new Headers();
    const token = readToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const r = await fetch(`/api/v1/backtests/${encodeURIComponent(id)}/export`, { headers });
    if (!r.ok) {
      const d = await r.json().catch(() => null);
      throw new ApiError(
        d?.error?.message ?? 'Export failed.',
        r.status,
        d?.error?.code ?? 'export_error',
      );
    }
    downloadBlob(await r.blob(), `tidebench-${id}.json`);
  },
};
export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function downloadCsv(rows: Record<string, unknown>[], name: string) {
  if (!rows.length) return;
  const keys = Object.keys(rows[0]);
  const cell = (v: unknown) => `"${String(v ?? '').replaceAll('"', '""')}"`;
  downloadBlob(
    new Blob(
      [
        keys.map(cell).join(',') +
          '\n' +
          rows.map((row) => keys.map((key) => cell(row[key])).join(',')).join('\n'),
      ],
      { type: 'text/csv;charset=utf-8' },
    ),
    name,
  );
}
