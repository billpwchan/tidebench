import { ApiError, downloadBlob, request, setCsrfToken, readToken } from './api';
import type { Source, Strategy } from './api';
export type RecordData = Record<string, unknown>;
export type AuthUser = {
  id?: string;
  username: string;
  display_name?: string;
  role: string;
  created_at?: number;
  active?: boolean;
  enabled?: boolean | 0 | 1;
};
export type AuthStatus = {
  auth_required: boolean;
  setup_required: boolean;
  authenticated: boolean;
  user?: AuthUser;
  csrf_token?: string;
};
export type Dataset = {
  id: string;
  source: Source;
  inst_id: string;
  kind: 'trade' | 'mark' | 'index' | 'funding';
  bar: string;
  start?: number;
  end?: number;
  start_ts?: number;
  end_ts?: number;
  rows?: number;
  count?: number;
  created_at?: number;
  dataset_hash?: string;
  hash?: string;
  version?: number | string;
  quality?: RecordData;
  instrument?: RecordData;
  rules?: RecordData;
  metadata?: RecordData;
  [key: string]: unknown;
};
export type Job = {
  id: string;
  status: string;
  source?: Source;
  inst_id?: string;
  kind?: string;
  bar?: string;
  start?: number;
  end?: number;
  progress?: number | null;
  received?: number;
  expected?: number | null;
  rows?: number;
  pages?: number;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
  dataset_id?: string;
  [key: string]: unknown;
};
export type ImportRequest = {
  source: Source;
  inst_id: string;
  kind: string;
  bar: string;
  start: number;
  end: number;
  records: RecordData[];
  provenance: RecordData;
};
export type ResearchMode = 'single' | 'train_test' | 'walk_forward' | 'grid' | 'cost_stress';
export type Direction = 'long_short' | 'long_only' | 'short_only';
export type ProRunConfig = {
  dataset_id: string;
  start_ts?: number;
  end_ts?: number;
  mark_dataset_id?: string;
  funding_dataset_id?: string;
  strategy: Strategy;
  direction: Direction;
  initial_cash: string;
  leverage: number;
  fee_bps: string;
  slippage_bps: string;
  liquidation_fee_bps: string;
  mode: ResearchMode;
  options: RecordData;
};
export type ProResult = {
  metrics?: RecordData;
  equity?: RecordData[];
  trades?: RecordData[];
  fills?: RecordData[];
  funding?: RecordData[];
  attribution?: RecordData;
  costs?: RecordData;
  folds?: RecordData[];
  scenarios?: RecordData[];
  cases?: RecordData[];
  assumptions?: RecordData;
  [key: string]: unknown;
};
export type ProRun = {
  id: string;
  status: string;
  created_at?: number;
  updated_at?: number;
  source?: Source;
  progress?: number;
  config: ProRunConfig;
  result?: ProResult | null;
  summary?: RecordData | null;
  error?: string | null;
  manifest?: RecordData | null;
  [key: string]: unknown;
};
export type OrderRequest = {
  source: Source;
  inst_id: string;
  side: 'buy' | 'sell';
  quantity: string;
  leverage: number;
  reduce_only: boolean;
  margin_mode: 'isolated';
  order_type: 'market' | 'limit' | 'stop_market';
  limit_price?: string;
  stop_price?: string;
};
export type OrderPreview = {
  order: RecordData;
  estimated_price: string;
  notional: string;
  fee: string;
  required_margin: string;
  estimated_cash_after: string;
  warnings: string[];
  market_snapshot: RecordData;
  risk: RecordData;
};
export type ProAccount = {
  source: Source;
  execution?: string;
  execution_mode?: string;
  cash?: string;
  available_cash?: string;
  equity?: string | null;
  used_margin?: string;
  maintenance_margin?: string;
  gross_exposure?: string;
  realized_pnl?: string;
  unrealized_pnl?: string | null;
  funding_paid?: string;
  fees_paid?: string;
  positions?: RecordData[];
  valuation_status?: string;
  as_of?: number;
  [key: string]: unknown;
};
export type ProRisk = {
  source: Source;
  max_order_notional: string;
  max_gross_exposure_pct: number;
  max_leverage: number;
  max_daily_loss_pct: number;
  halted: boolean;
  [key: string]: unknown;
};
export type ProDeployment = {
  id: string;
  source: Source;
  inst_id: string;
  bar: string;
  strategy?: Strategy;
  direction?: Direction;
  leverage?: number;
  status: string;
  last_bar?: number | null;
  last_error?: string | null;
  [key: string]: unknown;
};
export type Ops = {
  health?: RecordData | string;
  feeds?: RecordData[];
  jobs?: Job[];
  storage?: RecordData;
  backups?: RecordData[];
  checkpoints?: RecordData[];
  metrics?: RecordData;
  [key: string]: unknown;
};
const q = (values: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(values).map(([k, v]) => [k, String(v)])).toString();
const post = <T>(path: string, body?: unknown, headers?: HeadersInit) =>
  request<T>(path, {
    method: 'POST',
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    headers,
  });
export const proApi = {
  instruments: (source: Source, instType: 'SPOT' | 'SWAP') =>
    request<{ items: RecordData[] }>(
      `/pro/catalog/instruments?${q({ source, inst_type: instType })}`,
    ),
  async authStatus() {
    const status = await request<AuthStatus>('/auth/status');
    setCsrfToken(status.csrf_token ?? null);
    return status;
  },
  setup: (body: { username: string; password: string; display_name: string }, bootstrap?: string) =>
    post<AuthStatus>(
      '/auth/setup',
      body,
      bootstrap ? { 'X-Bootstrap-Token': bootstrap } : undefined,
    ),
  login: (body: { username: string; password: string }) => post<AuthStatus>('/auth/login', body),
  logout: () => post<void>('/auth/logout'),
  users: () => request<{ items: AuthUser[] }>('/auth/users'),
  changePassword: (body: { current_password: string; new_password: string }) =>
    post<RecordData>('/auth/password', body),
  resetPassword: ({ id, new_password }: { id: string; new_password: string }) =>
    post<RecordData>(`/auth/users/${encodeURIComponent(id)}/password`, { new_password }),
  updateUser: ({ id, role, enabled }: { id: string; role: string; enabled: boolean }) =>
    request<AuthUser>(`/auth/users/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ role, enabled }),
    }),
  createUser: (body: { username: string; password: string; display_name: string; role: string }) =>
    post<AuthUser>('/auth/users', body),
  datasets: () => request<{ items: Dataset[] }>('/pro/catalog/datasets'),
  jobs: () => request<{ items: Job[] }>('/pro/catalog/jobs'),
  createJob: (body: {
    source: Source;
    inst_id: string;
    kind: string;
    bar: string;
    start: number;
    end: number;
  }) => post<Job>('/pro/catalog/jobs', body),
  importDataset: (body: ImportRequest) => post<Dataset>('/pro/catalog/import', body),
  cancelJob: (id: string) => post<Job>(`/pro/catalog/jobs/${encodeURIComponent(id)}/cancel`),
  market: (source: Source, inst_id: string) =>
    request<RecordData>(`/pro/market?${q({ source, inst_id })}`),
  runs: () => request<{ items: ProRun[] }>('/pro/research/runs'),
  run: (id: string) => request<ProRun>(`/pro/research/runs/${encodeURIComponent(id)}`),
  createRun: (body: ProRunConfig) => post<ProRun>('/pro/research/runs', body),
  replay: (id: string) => post<ProRun>(`/pro/research/runs/${encodeURIComponent(id)}/replay`),
  compare: (ids: string[]) =>
    request<RecordData>(`/pro/research/compare?${q({ ids: ids.join(',') })}`),
  account: (source: Source) => request<ProAccount>(`/pro/execution/account?${q({ source })}`),
  orders: (source: Source) =>
    request<{ items: RecordData[] }>(`/pro/execution/orders?${q({ source })}`),
  ledger: (source: Source) =>
    request<{ items: RecordData[] }>(`/pro/execution/ledger?${q({ source })}`),
  previewOrder: (body: OrderRequest) => post<OrderPreview>('/pro/execution/orders/preview', body),
  order: (body: OrderRequest, key: string) =>
    post<RecordData>('/pro/execution/orders', body, { 'Idempotency-Key': key }),
  cancelOrder: (id: string) =>
    post<RecordData>(`/pro/execution/orders/${encodeURIComponent(id)}/cancel`),
  deployments: (source: Source) =>
    request<{ items: ProDeployment[] }>(`/pro/execution/deployments?${q({ source })}`),
  deploy: (body: {
    source: Source;
    inst_id: string;
    bar: string;
    strategy: Strategy;
    direction: Direction;
    leverage: number;
    allocation: string;
  }) => post<ProDeployment>('/pro/execution/deployments', body),
  stop: (id: string) =>
    post<ProDeployment>(`/pro/execution/deployments/${encodeURIComponent(id)}/stop`),
  risk: (source: Source) => request<ProRisk>(`/pro/execution/risk?${q({ source })}`),
  saveRisk: (body: {
    source: Source;
    max_order_notional: string;
    max_gross_exposure_pct: number;
    max_leverage: number;
    max_daily_loss_pct: number;
  }) =>
    request<ProRisk>(`/pro/execution/risk?${q({ source: body.source })}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  halt: (body: { source: Source; active: boolean; reason: string }) =>
    post<ProRisk>('/pro/execution/halt', body),
  ops: () => request<Ops>('/pro/ops'),
  createBackup: () => post<RecordData>('/pro/ops/backups/create'),
  verifyBackup: (id: string) =>
    request<RecordData>(`/pro/ops/backups/${encodeURIComponent(id)}/verify`),
  restore: (backup_id: string) =>
    post<RecordData>('/pro/ops/restore', { backup_id, confirmation: 'RESTORE' }),
  audit: () => request<{ items: RecordData[] }>('/pro/ops/audit'),
  async exportRun(id: string) {
    const response = await fetch(`/api/v1/pro/research/runs/${encodeURIComponent(id)}/export`, {
      credentials: 'same-origin',
      headers: readToken() ? { Authorization: `Bearer ${readToken()}` } : {},
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new ApiError(
        data?.error?.message ?? 'Export failed',
        response.status,
        data?.error?.code ?? 'export_failed',
      );
    }
    downloadBlob(await response.blob(), `tidebench-research-${id}.json`);
  },
};
