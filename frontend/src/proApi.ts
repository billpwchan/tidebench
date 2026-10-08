import { ApiError, downloadBlob, request, setCsrfToken, readToken } from './api';
import type { Source, Strategy } from './api';
export type RecordData = Record<string, unknown>;
export type InstrumentMember = RecordData & {
  row: number;
  inst_id: string | null;
  scope: string;
  eligibility: string;
  reasons: string[];
  state: string | null;
  list_time: number | null;
  expiry_time: number | null;
  metadata: RecordData | null;
};
export type InstrumentObservation = RecordData & {
  id: string;
  source: Source;
  region: string;
  inst_type: 'SPOT' | 'SWAP';
  received_at: number;
  received_ns: string;
  content_hash: string;
  payload_hash: string;
  row_count: number;
  counts: Record<string, number>;
  supported_count: number;
  members?: InstrumentMember[];
  rows?: unknown[];
};
export type InstrumentUniverse = {
  as_of: number;
  coverage: string;
  reason: string | null;
  age_ms?: number;
  max_age_ms: number;
  observation: InstrumentObservation | null;
  members: InstrumentMember[];
};
export type StrategyDefinition = {
  schema_version: 1;
  product: 'SPOT' | 'SWAP';
  bar: string;
  strategy: Strategy;
  direction: Direction;
  leverage: string;
};
export type StrategyVersion = {
  id: string;
  project_id: string;
  revision: number;
  parent_id?: string | null;
  hypothesis: string;
  definition: StrategyDefinition;
  implementation: RecordData;
  content_hash: string;
  created_by: string;
  created_at: number;
};
export type StrategyProject = {
  id: string;
  name: string;
  version_count?: number;
  latest_revision?: number;
  created_by: string;
  created_at: number;
  versions?: StrategyVersion[];
  version?: StrategyVersion;
};
export type ReleasePreview = {
  run_id: string;
  selection: string;
  selection_scope: string;
  preview_hash: string;
  definition: StrategyDefinition;
  execution_config: RecordData;
  risk_policy: RecordData;
  cost_differences: RecordData[];
  required_acknowledgements: string[];
  blockers: string[];
  model_difference: string;
  [key: string]: unknown;
};
export type PaperRelease = {
  id: string;
  run_id: string;
  strategy_version_id: string;
  preview: ReleasePreview;
  status: string;
  config: RecordData;
  deployment_id?: string;
  approval_hash: string;
  review: string;
  approved_by: string;
  approved_at: number;
};
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
export type ResearchInputs = {
  dataset_id: string;
  mark_dataset_id?: string;
  funding_dataset_id?: string;
  start_ts?: number;
  end_ts?: number;
  package_id?: string;
  package_manifest_hash?: string;
};
export type DataPackage = {
  id: string;
  source: Source;
  inst_id: string;
  bar: string;
  start: number;
  end: number;
  status: 'queued' | 'running' | 'preparing' | 'blocked' | 'failed' | 'canceled' | 'ready';
  ready: boolean;
  progress?: number;
  components: {
    kind: string;
    job_id?: string;
    dataset_id?: string;
    status: string;
    progress?: number;
    error?: string | null;
  }[];
  blockers: { code: string; kind?: string; message: string }[];
  coverage?: { start: number; end: number; complete: boolean };
  funding_marks?: RecordData;
  manifest?: RecordData;
  manifest_hash?: string;
  research_inputs?: ResearchInputs;
  created_at?: number;
  updated_at?: number;
  [key: string]: unknown;
};
export type PriceShock = {
  name: string;
  parallel_pct: string;
  asset_pct?: Record<string, string>;
  market_pct?: Record<string, string>;
};
export type PortfolioAnalytics = {
  status: 'available' | 'partial' | 'unavailable';
  as_of?: number;
  as_of_ms?: number;
  summary: RecordData;
  assets: RecordData[];
  markets: RecordData[];
  positions: RecordData[];
  scenarios: RecordData[];
  issues: (string | RecordData)[];
  assumptions?: RecordData;
  [key: string]: unknown;
};
export type ProRunConfig = {
  strategy_version_id?: string | null;
  dataset_id: string;
  package_id?: string;
  package_manifest_hash?: string;
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
export type PortfolioLegDefinition = {
  inst_id: string;
  weight: string;
  leverage: string;
  direction: string;
  strategy: Strategy;
};
export type PortfolioDefinition = {
  schema_version?: number;
  bar: string;
  mode: string;
  legs: PortfolioLegDefinition[];
  capital_pct: string;
  rebalance_bars: number;
  lookback: number;
  top_k: number;
  carry_threshold: string;
  risk_window?: number;
  vol_target_pct?: string;
  vol_floor_pct?: string;
  covariance_shrinkage?: string;
  correlation_stress?: string;
  carry_window?: number;
  carry_cost_settlements?: number;
  carry_buffer_bps?: string;
  carry_max_age_hours?: number;
  max_residual_pct: string;
  failure_policy: 'reduce_group';
};
export type PortfolioVersion = {
  id: string;
  project_id: string;
  revision: number;
  parent_id?: string;
  hypothesis: string;
  definition: PortfolioDefinition;
  content_hash: string;
  created_at: number;
};
export type PortfolioProject = {
  id: string;
  name: string;
  latest_revision: number;
  version_count: number;
  versions?: PortfolioVersion[];
  version?: PortfolioVersion;
};
export type PortfolioReleasePreview = {
  name: string;
  run_id: string;
  version_id: string;
  source: Source;
  definition: PortfolioDefinition;
  hypothesis: string;
  result_hash: string;
  portfolio_content_hash: string;
  preview_hash: string;
  risk_policy: RecordData;
  cost_differences: RecordData[];
  risk_differences: RecordData[];
  required_acknowledgements: string[];
  blockers: string[];
  metrics: RecordData;
  capital_basis: string;
  execution_model: string;
  research_evidence: RecordData;
};
export type PortfolioRelease = {
  id: string;
  status: string;
  source: Source;
  group_id?: string;
  created_at: number;
  approval: {
    preview: PortfolioReleasePreview;
    review: string;
    actor: string;
    approved_at: number;
  };
};
export type ManagedPortfolio = {
  id: string;
  source: Source;
  status: string;
  version_id: string;
  release_id: string;
  last_bar?: number;
  last_error?: string;
  created_at: number;
  integrity_error?: { code: string; message: string };
  manifest: {
    name: string;
    version_revision: number;
    definition: PortfolioDefinition;
    legs: (PortfolioLegDefinition & { deployment_id: string })[];
    result_hash: string;
  } | null;
};
export type PortfolioCommand = {
  id: string;
  phase: string;
  sequence: number;
  status: string;
  key: string;
  error?: string;
  payload: RecordData;
  order?: RecordData;
};
export type PortfolioAdjustment = {
  inst_id: string;
  code: string;
  requested_quantity: string;
  minimum_size: string;
  message: string;
};
export type PortfolioBatch = {
  id: string;
  bar: number;
  status: string;
  error?: string;
  body: RecordData & {
    targets: Record<string, string>;
    weights: Record<string, string>;
    capital: string;
    available_at: number;
    rebalance_due: boolean;
    reduction_skips?: PortfolioAdjustment[];
  };
  additions?: RecordData & { skipped?: PortfolioAdjustment[] };
  residuals?: { quantities: Record<string, string>; capital_pct: string; notional: string };
  commands: PortfolioCommand[];
};
export type ContributionOwner = {
  owner: string;
  realized_pnl: string;
  unrealized_pnl: string | null;
  fees_paid: string;
  funding_paid: string;
  net_pnl: string | null;
  markets: RecordData[];
};
export type Contributions = {
  owners: ContributionOwner[];
  totals: RecordData;
  account_net_pnl: string | null;
  reconciliation_delta: string | null;
  reconciled: boolean;
  valuation_status: string;
  policy: string;
  legacy_policy: string;
  as_of: number;
};
export type PortfolioResearchRun = {
  id: string;
  source: Source;
  status: string;
  config: RecordData;
  manifest: RecordData;
  error?: string;
  progress: number;
  created_at: number;
  updated_at: number;
  result?: {
    metrics: RecordData;
    equity: RecordData[];
    decisions: RecordData[];
    orders: RecordData[];
    ledger: RecordData[];
    execution_rejections: RecordData[];
    assumptions: RecordData;
    [key: string]: unknown;
  };
};
export type Holdout = {
  id: string;
  project_id: string;
  version_id: string;
  source: Source;
  inst_id: string;
  bar: string;
  start_ts: number;
  end_ts: number;
  status: string;
  run_id?: string;
  plan: {
    name: string;
    test_config: RecordData;
    benchmark: string;
    rejection_plan: string;
    scope: string;
  };
  plan_hash: string;
  created_at: number;
};
export type PortfolioHoldout = {
  id: string;
  project_id: string;
  version_id: string;
  source: Source;
  status: 'draft' | 'sealed' | 'consumed' | 'consumed_unavailable';
  run_id?: string | null;
  plan_hash: string;
  input_hash: string;
  created_at: number;
  plan: {
    name: string;
    definition: PortfolioDefinition;
    hypothesis: string;
    test_config: RecordData;
    test_start: number;
    test_end: number;
    access_start: number;
    warmup_bars: number;
    benchmark: 'cash';
    criteria: RecordData;
    rejection_plan: string;
    packages: { id: string; inst_id: string; manifest_hash: string }[];
    scope: string;
    [key: string]: unknown;
  };
};
export type PortfolioHoldoutPreview = PortfolioHoldout & {
  preview_id: string;
  preview_hash: string;
  blockers: string[];
};
export type PortfolioGovernanceReport = {
  project_id: string;
  recorded_attempts: number;
  primary_evaluations: number;
  replay_attempts: number;
  candidate_configurations: number;
  distinct_configurations: number;
  items: RecordData[];
  holdouts: PortfolioHoldout[];
  scope: string;
};
export const proApi = {
  portfolioHoldouts: (source: Source, project_id: string) =>
    request<{ items: PortfolioHoldout[] }>(
      `/pro/research/portfolio-holdouts?${q({ source, project_id })}`,
    ),
  previewPortfolioHoldout: (body: RecordData) =>
    post<PortfolioHoldoutPreview>('/pro/research/portfolio-holdouts/preview', body),
  sealPortfolioHoldout: (preview: PortfolioHoldoutPreview) =>
    post<PortfolioHoldout>('/pro/research/portfolio-holdouts', {
      preview_id: preview.preview_id,
      preview_hash: preview.preview_hash,
    }),
  evaluatePortfolioHoldout: (holdout: PortfolioHoldout) =>
    post<PortfolioResearchRun>(
      `/pro/research/portfolio-holdouts/${encodeURIComponent(holdout.id)}/evaluate`,
      { plan_hash: holdout.plan_hash },
    ),
  portfolioGovernance: (id: string) =>
    request<PortfolioGovernanceReport>(
      `/pro/research/portfolio-governance/${encodeURIComponent(id)}`,
    ),
  replayPortfolio: (id: string) =>
    post<PortfolioResearchRun>(`/pro/research/portfolios/${encodeURIComponent(id)}/replay`),
  holdouts: () => request<{ items: Holdout[] }>('/pro/research/holdouts'),
  sealHoldout: (body: RecordData) => post<Holdout>('/pro/research/holdouts', body),
  evaluateHoldout: (holdout: Holdout) =>
    post<ProRun>('/pro/research/runs', { ...holdout.plan.test_config, holdout_id: holdout.id }),
  governance: (project: string) => request<RecordData>('/pro/research/governance/' + project),
  portfolioProjects: () => request<{ items: PortfolioProject[] }>('/pro/portfolio-strategies'),
  portfolioProject: (id: string) =>
    request<PortfolioProject>(`/pro/portfolio-strategies/${encodeURIComponent(id)}`),
  portfolioVersion: (id: string) =>
    request<PortfolioVersion>(`/pro/portfolio-versions/${encodeURIComponent(id)}`),
  createPortfolioProject: (body: {
    name: string;
    hypothesis: string;
    definition: PortfolioDefinition;
  }) => post<PortfolioProject>('/pro/portfolio-strategies', body),
  createPortfolioVersion: (
    id: string,
    body: { hypothesis: string; definition: PortfolioDefinition; parent_id?: string },
  ) => post<PortfolioVersion>(`/pro/portfolio-strategies/${encodeURIComponent(id)}/versions`, body),
  previewPortfolioRelease: (run_id: string) =>
    post<PortfolioReleasePreview>('/pro/execution/portfolio-releases/preview', { run_id }),
  approvePortfolioRelease: (body: {
    run_id: string;
    preview_hash: string;
    review: string;
    acknowledgements: string[];
  }) => post<PortfolioRelease>('/pro/execution/portfolio-releases', body),
  activatePortfolioRelease: (id: string) =>
    post<PortfolioRelease>(`/pro/execution/portfolio-releases/${encodeURIComponent(id)}/activate`),
  portfolioReleases: (source: Source) =>
    request<{ items: PortfolioRelease[] }>(`/pro/execution/portfolio-releases?${q({ source })}`),
  managedPortfolios: (source: Source) =>
    request<{ items: ManagedPortfolio[] }>(`/pro/execution/portfolios?${q({ source })}`),
  portfolioBatches: (id: string, before?: number) =>
    request<{ items: PortfolioBatch[] }>(
      `/pro/execution/portfolios/${encodeURIComponent(id)}/batches?${q(before === undefined ? {} : { before })}`,
    ),
  stopPortfolio: (id: string) =>
    post<ManagedPortfolio>(`/pro/execution/portfolios/${encodeURIComponent(id)}/stop`),
  contributionEvents: (source: Source, owner: string, before?: number) =>
    request<{ items: (RecordData & { id: number; body: RecordData; order?: RecordData })[] }>(
      `/pro/execution/contributions/events?${q({ source, owner, ...(before === undefined ? {} : { before }) })}`,
    ),
  contributions: (source: Source) =>
    request<Contributions>(`/pro/execution/contributions?${q({ source })}`),
  portfolioRuns: (source: Source) =>
    request<{ items: PortfolioResearchRun[] }>('/pro/research/portfolios?source=' + source),
  portfolioRun: (id: string) => request<PortfolioResearchRun>('/pro/research/portfolios/' + id),
  createPortfolioRun: (body: RecordData) =>
    post<PortfolioResearchRun>('/pro/research/portfolios', body),
  clock: () =>
    request<{ market_ts: number; speed: number; paused: boolean; revision: number }>(
      '/pro/execution/clock',
    ),
  changeClock: (body: { speed?: number; step_ms?: number; expected_revision: number }) =>
    request('/pro/execution/clock', { method: 'POST', body: JSON.stringify(body) }),
  performance: (source: Source, before?: number) =>
    request<{
      items: { id: number; market_ts: number; observed_at: number; body: RecordData }[];
      summary: RecordData;
      next_before?: number;
    }>(`/pro/execution/performance?${q({ source, ...(before === undefined ? {} : { before }) })}`),
  decisions: (id: string, before?: number) =>
    request<{ items: RecordData[] }>(
      `/pro/execution/deployments/${id}/decisions?${q(before === undefined ? {} : { before })}`,
    ),
  strategies: () => request<{ items: StrategyProject[] }>('/pro/strategies'),
  strategy: (id: string) => request<StrategyProject>(`/pro/strategies/${encodeURIComponent(id)}`),
  strategyVersion: (id: string) =>
    request<StrategyVersion>(`/pro/strategy-versions/${encodeURIComponent(id)}`),
  createStrategy: (body: { name: string; hypothesis: string; definition: StrategyDefinition }) =>
    post<StrategyProject>('/pro/strategies', body),
  createStrategyVersion: (
    id: string,
    body: { hypothesis: string; definition: StrategyDefinition; parent_id?: string },
  ) => post<StrategyVersion>(`/pro/strategies/${encodeURIComponent(id)}/versions`, body),
  previewRelease: (run_id: string, selection: string) =>
    post<ReleasePreview>('/pro/execution/releases/preview', { run_id, selection }),
  approveRelease: (body: {
    run_id: string;
    selection: string;
    preview_hash: string;
    acknowledgements: string[];
    review: string;
  }) => post<PaperRelease>('/pro/execution/releases', body),
  activateRelease: (id: string) =>
    post<PaperRelease>(`/pro/execution/releases/${encodeURIComponent(id)}/activate`),
  releases: (source: Source) =>
    request<{ items: PaperRelease[] }>(`/pro/execution/releases?${q({ source })}`),
  packages: (source: Source) =>
    request<{ items: DataPackage[] }>(`/pro/catalog/packages?${q({ source })}`),
  package: (id: string) => request<DataPackage>(`/pro/catalog/packages/${encodeURIComponent(id)}`),
  preparePackage: (body: {
    source: Source;
    inst_id: string;
    bar: string;
    start: number;
    end: number;
    include_index?: boolean;
    idempotency_key?: string;
    dataset_ids?: Record<string, string>;
  }) => post<DataPackage>('/pro/catalog/packages', body),
  cancelPackage: (id: string) =>
    post<DataPackage>(`/pro/catalog/packages/${encodeURIComponent(id)}/cancel`),
  retryPackage: (id: string) =>
    post<DataPackage>(`/pro/catalog/packages/${encodeURIComponent(id)}/retry`),
  packageManifest: (id: string) =>
    request<RecordData>(`/pro/catalog/packages/${encodeURIComponent(id)}/manifest`),
  async packageInputs(id: string): Promise<ResearchInputs> {
    const item = await request<DataPackage>(`/pro/catalog/packages/${encodeURIComponent(id)}`);
    if (!item.ready || !item.research_inputs)
      throw new Error('The package is not ready for research.');
    return item.research_inputs;
  },
  analytics: (source: Source) =>
    request<PortfolioAnalytics>(`/pro/execution/analytics?${q({ source })}`),
  analyzeScenarios: (body: { source: Source; scenarios: PriceShock[] }) =>
    post<PortfolioAnalytics>('/pro/execution/analytics', body),
  instruments: (source: Source, instType: 'SPOT' | 'SWAP') =>
    request<{ items: RecordData[] }>(
      `/pro/catalog/instruments?${q({ source, inst_type: instType })}`,
    ),
  instrumentObservations: (source: Source, instType: 'SPOT' | 'SWAP') =>
    request<{ items: InstrumentObservation[] }>(
      `/pro/catalog/instrument-observations?${q({ source, inst_type: instType })}`,
    ),
  captureInstruments: (source: Source, instType: 'SPOT' | 'SWAP') =>
    post<InstrumentObservation>(
      `/pro/catalog/instrument-observations?${q({ source, inst_type: instType })}`,
    ),
  instrumentObservation: (id: string) =>
    request<InstrumentObservation>(
      `/pro/catalog/instrument-observations/${encodeURIComponent(id)}`,
    ),
  exportInstrumentObservation: (id: string) =>
    request<{ content_hash: string; observation: RecordData }>(
      `/pro/catalog/instrument-observations/${encodeURIComponent(id)}/export`,
    ),
  instrumentUniverse: (source: Source, instType: 'SPOT' | 'SWAP', asOf: number, maxAge: number) =>
    request<InstrumentUniverse>(
      `/pro/catalog/instrument-universe?${q({ source, inst_type: instType, as_of: asOf, max_age_ms: maxAge })}`,
    ),
  instrumentDiff: (id: string, previous: string) =>
    request<{ changes: RecordData[] }>(
      `/pro/catalog/instrument-observations/${encodeURIComponent(id)}/diff?${q({ previous })}`,
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
  runs: (source: Source, before?: string, limit = 100) =>
    request<{ items: ProRun[]; next_cursor?: string | null }>(
      `/pro/research/runs?${q({ source, limit, ...(before ? { before } : {}) })}`,
    ),
  run: (id: string, variant?: string) =>
    request<ProRun>(
      `/pro/research/runs/${encodeURIComponent(id)}${variant === undefined ? '' : '?variant=' + encodeURIComponent(variant)}`,
    ),
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
  acknowledgeIncident: (id: string, reason: string) =>
    post<RecordData>(`/pro/ops/incidents/${id}/acknowledge`, { reason }),
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
