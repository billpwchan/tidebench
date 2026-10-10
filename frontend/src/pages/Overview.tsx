import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, CircleAlert, Clock3, RefreshCw } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { ProAccount, RecordData } from '../proApi';
import DeskAttention from '../components/DeskAttention';
import { deskIssues } from '../lib/deskAttention';
import type { Page } from '../lib/config';
import { date, number, percent, price, quantityText, tone } from '../lib/format';
import { useNow } from '../lib/hooks';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { researchReturn, resultScope } from '../lib/research';
import { useSession } from '../components/AuthGate';
import { DataTable, JsonDetails, ProductSymbol, valueText } from '../components/ProWorkspace';
import { ErrorBox, Loading, Metric, PageHeading, Status } from '../components/workspace';

type Alert = {
  key: string;
  title: string;
  detail: string;
  page: Page;
  view?: string;
  severity: 'warning' | 'neutral' | 'bad';
};
const isRecord = (value: unknown): value is RecordData =>
  !!value && typeof value === 'object' && !Array.isArray(value);
function accountForSource(value: unknown, source: Source): ProAccount | undefined {
  if (!isRecord(value) || value.source !== source) return undefined;
  const moneyFields = [
    'cash',
    'available_cash',
    'equity',
    'used_margin',
    'maintenance_margin',
    'unrealized_pnl',
  ];
  if (
    !moneyFields.every(
      (key) =>
        value[key] == null ||
        typeof value[key] === 'string' ||
        (typeof value[key] === 'number' && Number.isFinite(value[key])),
    )
  )
    return undefined;
  return value as ProAccount;
}
export default function Overview({
  source,
  journey,
  marketRequested = false,
  navigate,
  symbol,
  setSymbol,
}: {
  source: Source;
  journey?: ReactNode;
  marketRequested?: boolean;
  symbol: string;
  setSymbol: (symbol: string) => void;
  navigate: (page: Page, view?: string) => void;
}) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const [allMetrics, setAllMetrics] = useState(false);
  const [conditionsOpen, setConditionsOpen] = useState(false);
  const conditionsRef = useRef<HTMLDetailsElement>(null);
  const qc = useQueryClient();
  const now = useNow();
  const canOperate = canTrade(useSession()?.user?.role);
  const [bookTab, setBookTab] = useState('positions');
  const bookChosen = useRef(false);
  const chooseBook = (next: string) => {
    bookChosen.current = true;
    setBookTab(next);
  };
  const account = useQuery({
    queryKey: ['pro-account', source],
    queryFn: () => proApi.account(source),
    refetchOnMount: 'always',
    refetchInterval: 5000,
  });
  const analytics = useQuery({
    queryKey: ['portfolio-analytics', source],
    queryFn: () => proApi.analytics(source),
    refetchOnMount: 'always',
    refetchInterval: 10000,
  });
  const orders = useQuery({
    queryKey: ['pro-orders', source],
    queryFn: () => proApi.orders(source),
    refetchInterval: 5000,
  });
  const deployments = useQuery({
    queryKey: ['pro-deployments', source],
    queryFn: () => proApi.deployments(source),
    refetchInterval: 5000,
  });
  const groups = useQuery({
    queryKey: ['managed-portfolios', source],
    queryFn: () => proApi.managedPortfolios(source),
    refetchInterval: 5000,
  });
  const risk = useQuery({
    queryKey: ['pro-risk', source],
    queryFn: () => proApi.risk(source),
    refetchInterval: 5000,
  });
  const ops = useQuery({ queryKey: ['pro-ops'], queryFn: proApi.ops, refetchInterval: 5000 });
  const packages = useQuery({
    queryKey: ['pro-packages', source],
    queryFn: () => proApi.packages(source),
    refetchInterval: 5000,
  });
  const runs = useQuery({
    queryKey: ['pro-runs', source, 'latest'],
    queryFn: () => proApi.runs(source, undefined, 6),
    refetchInterval: 10000,
  });
  const refresh = () => {
    for (const key of [
      'pro-account',
      'portfolio-analytics',
      'pro-orders',
      'pro-deployments',
      'managed-portfolios',
      'pro-risk',
      'pro-ops',
      'pro-packages',
      'pro-runs',
    ])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const cancel = useMutation({ mutationFn: proApi.cancelOrder, onSuccess: refresh });
  const report = analytics.data;
  const capturedCandidate = accountForSource(report?.input_snapshot?.account, source);
  const capturedAccount =
    report?.source === source &&
    ['available', 'partial', 'unavailable'].includes(report.status) &&
    isRecord(report.summary) &&
    Array.isArray(report.assets) &&
    report.assets.every(isRecord) &&
    capturedCandidate &&
    Array.isArray(capturedCandidate.positions) &&
    capturedCandidate.positions.every(isRecord)
      ? capturedCandidate
      : undefined;
  // Money and positions belong to the same capture as its exposure summary.
  // Null/missing economic equity stays unknown, even if model equity is valued.
  const matchedReport = capturedAccount ? report : undefined;
  const a = capturedAccount ?? accountForSource(account.data, source);
  const summary = matchedReport?.summary;
  const positions =
    Array.isArray(a?.positions) && a.positions.every(isRecord) ? a.positions : undefined;
  const pending = orders.data?.items.filter((o) => o.status === 'pending') ?? [];
  useEffect(() => {
    if (bookChosen.current || !positions || !orders.isSuccess) return;
    bookChosen.current = true;
    setBookTab(!positions.length && pending.length ? 'orders' : 'positions');
  }, [positions, orders.isSuccess, pending.length]);
  const strategies = deployments.data?.items ?? [];
  const currentPackages = packages.data?.items.filter((p) => p.source === source) ?? [];
  const currentRuns = runs.data?.items.filter((r) => r.source === source) ?? [];
  const jobs = (ops.data?.jobs ?? []).filter((j) => !j.source || j.source === source);
  const activeJobs = jobs.filter((j) => ['queued', 'running', 'preparing'].includes(j.status));
  const health = ops.data?.health;
  const healthStatus = typeof health === 'object' ? health.status : health;
  const currentGroups = groups.data?.items.filter((group) => group.source === source) ?? [];
  const issues = deskIssues(source, currentGroups, ops.data?.incidents ?? [], strategies);
  const groupMembers = new Set(
    currentGroups.flatMap((group) => group.manifest?.legs.map((leg) => leg.deployment_id) ?? []),
  );
  const independentStrategies = strategies.filter(
    (strategy) => !strategy.group_id && !groupMembers.has(strategy.id),
  );
  const managedMembers = strategies.filter(
    (strategy) => !!strategy.group_id || groupMembers.has(strategy.id),
  );
  const alerts: Alert[] = [];
  if (risk.data?.halted)
    alerts.push({
      key: 'halt',
      title: t('New risk is halted'),
      detail: t('Reduce-only exits remain available. Review the saved risk controls.'),
      page: 'risk',
      severity: 'bad',
    });
  if (a && !['fresh', 'example'].includes(a.valuation_status ?? ''))
    alerts.push({
      key: 'valuation',
      title: t('Account valuation needs attention'),
      detail: `${t('Valuation status')}: ${t(a.valuation_status ?? 'unavailable')}`,
      page: 'execution',
      severity: 'warning',
    });
  if (a?.economic_status === 'funding_pending')
    alerts.push({
      key: 'funding',
      title: t('Funding settlement remains pending'),
      detail: t('Historical liability remains unsettled; account equity is provisional.'),
      page: 'execution',
      view: 'ledger',
      severity: 'bad',
    });
  if (a?.economic_status === 'funding_schedule_unavailable')
    alerts.push({
      key: 'funding-schedule',
      title: t('Funding schedule needs attention'),
      detail: t(
        'A current settlement schedule is unavailable. Equity is provisional and new perpetual risk is blocked; reduce-only protection remains available.',
      ),
      page: 'execution',
      view: 'positions',
      severity: 'bad',
    });
  const strategyErrors = independentStrategies.filter(
    (s) =>
      s.status !== 'stopped' && (s.last_error || ['failed', 'blocked', 'error'].includes(s.status)),
  );
  if (strategyErrors.length)
    alerts.push({
      key: 'strategies',
      title: `${strategyErrors.length} ${t('strategies report an error')}`,
      detail: strategyErrors.map((s) => s.inst_id).join(' · '),
      page: 'execution',
      view: 'strategies',
      severity: 'bad',
    });
  const blocked = currentPackages.filter((p) => ['blocked', 'failed'].includes(p.status));
  if (blocked.length)
    alerts.push({
      key: 'packages',
      title: `${blocked.length} ${t('research packages need attention')}`,
      detail:
        blocked[0].blockers?.[0]?.message ??
        t('Inspect component errors and coverage before research.'),
      page: 'data',
      severity: 'warning',
    });
  if (pending.length)
    alerts.push({
      key: 'orders',
      title: `${pending.length} ${t('orders are working')}`,
      detail: t('Review limits, triggers and reserved cash.'),
      page: 'execution',
      view: 'orders',
      severity: 'neutral',
    });
  if (analytics.isSuccess && !matchedReport)
    alerts.push({
      key: 'analytics-capture',
      title: t('Exposure unavailable'),
      detail: t('A matching account capture was not returned. Refresh the exposure snapshot.'),
      page: 'risk',
      severity: 'warning',
    });
  if (matchedReport && matchedReport.status !== 'available')
    alerts.push({
      key: 'analytics',
      title: t('Exposure is not fully valued'),
      detail: t('Missing prices or instrument rules limit risk calculations.'),
      page: 'risk',
      severity: 'warning',
    });
  const economicAttention = !!(
    issues.length ||
    risk.data?.halted ||
    strategyErrors.length ||
    (a && a.economic_status !== 'complete') ||
    (a && !['fresh', 'example'].includes(a.valuation_status ?? '')) ||
    (matchedReport && matchedReport.status !== 'available')
  );
  const errors = [
    account,
    analytics,
    orders,
    deployments,
    groups,
    risk,
    ops,
    packages,
    runs,
  ].filter((q) => q.isError);
  const ready =
    !!matchedReport &&
    [account, analytics, orders, deployments, groups, risk, ops, packages, runs].every(
      (q) => q.isSuccess,
    );
  const showJourneyInBook =
    !!journey &&
    ready &&
    !economicAttention &&
    positions?.length === 0 &&
    pending.length === 0 &&
    strategies.length === 0 &&
    currentGroups.length === 0;
  const priorityIssue =
    issues.find((issue) => issue.severity === 'bad') ??
    (alerts.some((alert) => alert.severity === 'bad') ? undefined : issues[0]);
  const priorityAlert = priorityIssue
    ? undefined
    : (alerts.find((alert) => alert.severity === 'bad') ??
      alerts.find((alert) => alert.severity === 'warning'));
  const inspectConditions = () => {
    setConditionsOpen(true);
    requestAnimationFrame(() => {
      conditionsRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      conditionsRef.current?.querySelector('summary')?.focus({ preventScroll: true });
    });
  };
  return (
    <div className="desk-overview">
      <PageHeading
        eyebrow="TRADING WORKSPACE"
        title="Trading overview"
        description="Current account state, working orders, strategy health, and research readiness."
      >
        <button className="button button-secondary" aria-label={t('Refresh')} onClick={refresh}>
          <RefreshCw size={14} />
          <span className="desk-refresh-label">{t('Refresh')}</span>
        </button>
        <button className="button button-dark" onClick={() => navigate('execution')}>
          {t('Open execution')}
          <ArrowRight size={14} />
        </button>
      </PageHeading>
      <div className="overview-state-line">
        <span>
          <Clock3 size={12} />
          {t('Account snapshot')}: {date(a?.as_of, true)}
        </span>
        <Status type={risk.data?.halted ? 'bad' : 'neutral'}>
          {risk.data
            ? t(risk.data.halted ? 'New risk halted' : 'New risk enabled')
            : t('Risk status unavailable')}
        </Status>
        <span className="overview-economic-state" aria-label={t('Trading conditions')}>
          <Status type={economicAttention ? 'bad' : ready ? 'neutral' : 'warning'}>
            {t(
              economicAttention
                ? 'Action required'
                : ready
                  ? 'No known unresolved conditions'
                  : 'Monitoring incomplete',
            )}
          </Status>
        </span>
      </div>
      {(priorityIssue || priorityAlert) && (
        <section className="desk-risk-summary" aria-label={t('Trading conditions')}>
          <CircleAlert size={17} />
          <div>
            <strong>{priorityIssue?.name ?? priorityAlert?.title}</strong>
            <span>
              {priorityIssue
                ? `${t(priorityIssue.phase)} · ${priorityIssue.group ? `${number(priorityIssue.inventoryNotional)} USDT · ${t('Current group inventory')}` : t('Durable incident')}`
                : priorityAlert?.detail}
            </span>
          </div>
          <button
            className="text-button"
            onClick={() => {
              if (priorityIssue?.group) navigate('execution', `managed:${priorityIssue.group.id}`);
              else if (priorityIssue?.incident)
                navigate('operations', `incidents:${priorityIssue.incident.id}`);
              else if (priorityAlert) navigate(priorityAlert.page, priorityAlert.view);
            }}
          >
            {priorityIssue?.group
              ? t('Inspect group & recovery')
              : priorityIssue?.incident
                ? t('Inspect incident & response')
                : priorityAlert?.key === 'funding'
                  ? t('Inspect funding & ledger')
                  : priorityAlert?.key === 'halt'
                    ? text('Review risk controls', '审查风险控制')
                    : priorityAlert?.key === 'valuation'
                      ? text('Inspect account valuation', '检查账户估值')
                      : ['analytics', 'analytics-capture'].includes(priorityAlert?.key ?? '')
                        ? text('Inspect exposure', '检查风险敞口')
                        : priorityAlert?.key === 'strategies'
                          ? text('Inspect strategies', '检查策略')
                          : priorityAlert?.key === 'funding-schedule'
                            ? text('Inspect account positions', '检查账户持仓')
                            : text('Inspect condition', '检查当前状况')}
            <ArrowRight size={13} />
          </button>
          <button className="text-button desk-all-conditions" onClick={inspectConditions}>
            {text('All conditions', '全部状况')} ·{' '}
            {issues.length + alerts.filter((alert) => alert.severity !== 'neutral').length}
          </button>
        </section>
      )}
      <div
        className={`pro-metric-strip trader-metrics desk-account-strip${allMetrics ? ' is-expanded' : ''}`}
      >
        <Metric label="Account equity" value={number(a?.equity)} unit="USDT" />
        <Metric label="Gross exposure" value={number(summary?.gross_notional)} unit="USDT" />
        <Metric label="Available cash" value={number(a?.available_cash)} unit="USDT" />
        <Metric label="Net exposure" value={number(summary?.net_notional)} unit="USDT" />
        <Metric
          label="Used margin"
          value={number(a?.used_margin)}
          unit="USDT"
          note={`${t('Maintenance margin')}: ${number(a?.maintenance_margin)}`}
        />
        <Metric
          label="Unrealized P&L"
          value={number(a?.unrealized_pnl)}
          className={tone(a?.unrealized_pnl)}
          unit="USDT"
        />
      </div>
      <button
        className="text-button desk-metrics-toggle"
        aria-expanded={allMetrics}
        onClick={() => setAllMetrics((value) => !value)}
      >
        {allMetrics
          ? text('Fewer account values', '收起账户数值')
          : text('Cash, margin & P&L', '现金、保证金与盈亏')}
      </button>
      {!a && account.isPending && <Loading label="Loading account state…" />}
      <div className="trader-overview-layout">
        <div className="trader-primary">
          <section className="pro-panel overview-book">
            <div className="section-heading">
              <div>
                <h2>{t('Portfolio & working orders')}</h2>
                <p className="section-description">
                  {t(
                    'Current positions and pending instructions. Historical returns are not account performance.',
                  )}
                </p>
              </div>
              <button className="text-button" onClick={() => navigate('execution', bookTab)}>
                {t('Open execution')}
                <ArrowRight size={13} />
              </button>
            </div>
            <div
              className="desk-book-tabs"
              role="tablist"
              aria-label={t('Portfolio & working orders')}
              onKeyDown={(event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const next =
                  event.key === 'Home'
                    ? 'positions'
                    : event.key === 'End'
                      ? 'orders'
                      : bookTab === 'positions'
                        ? 'orders'
                        : 'positions';
                chooseBook(next);
                event.currentTarget
                  .querySelectorAll<HTMLButtonElement>('[role="tab"]')
                  [next === 'positions' ? 0 : 1]?.focus();
              }}
            >
              <button
                role="tab"
                tabIndex={bookTab === 'positions' ? 0 : -1}
                aria-selected={bookTab === 'positions'}
                className={bookTab === 'positions' ? 'active' : ''}
                onClick={() => chooseBook('positions')}
              >
                {t('Positions')} <span>{positions ? positions.length : '—'}</span>
              </button>
              <button
                role="tab"
                tabIndex={bookTab === 'orders' ? 0 : -1}
                aria-selected={bookTab === 'orders'}
                className={bookTab === 'orders' ? 'active' : ''}
                onClick={() => chooseBook('orders')}
              >
                {t('Pending orders')} <span>{orders.isSuccess ? pending.length : '—'}</span>
              </button>
            </div>
            <p className="desk-money-unit">
              {text(
                'Monetary values in USDT. Instrument units are shown per position.',
                '金额单位为 USDT；持仓数量单位按品种标明。',
              )}
            </p>
            {bookTab === 'positions' ? (
              !a && account.isPending ? (
                <Loading />
              ) : !a && account.isError ? (
                <ErrorBox error={account.error} />
              ) : !positions ? (
                <p className="quiet-copy">{t('Position snapshot unavailable')}</p>
              ) : (
                <DataTable
                  rows={positions}
                  rowKey={(position) => String(position.inst_id)}
                  searchLabel="Search positions"
                  searchKeys={['inst_id', 'side', 'inst_type']}
                  compactColumns={[
                    'inst_id',
                    'quantity',
                    'market_value',
                    'unrealized_pnl',
                    'protect',
                  ]}
                  compactLabel={t('Positions')}
                  empty="No open positions"
                  columns={[
                    {
                      key: 'inst_id',
                      sticky: 'identity',
                      sortable: true,
                      label: 'Market',
                      render: (p) => (
                        <div className="table-stacked">
                          <strong>{valueText(p.inst_id)}</strong>
                          <small>
                            {valueText(p.inst_type)} · {t(valueText(p.side))}
                          </small>
                        </div>
                      ),
                    },
                    {
                      key: 'quantity',
                      sortable: true,
                      sortType: 'number',
                      label: 'Quantity',
                      render: (p) => (
                        <div className="table-stacked">
                          <span>{quantityText(p.quantity)}</span>
                          <small>{t(p.inst_type === 'SWAP' ? 'Contracts' : 'Base units')}</small>
                        </div>
                      ),
                    },
                    {
                      key: 'mark',
                      sortable: true,
                      sortType: 'number',
                      label: 'Mark',
                      render: (p) => (
                        <div className="table-stacked">
                          <span>{price(p.mark)}</span>
                          <small>{date(Number(p.as_of))}</small>
                        </div>
                      ),
                    },
                    {
                      key: 'market_value',
                      sortable: true,
                      sortType: 'number',
                      label: 'Market value',
                      render: (p) => number(p.market_value),
                    },
                    { key: 'margin', label: 'Margin', render: (p) => number(p.margin) },
                    {
                      key: 'unrealized_pnl',
                      sortable: true,
                      sortType: 'number',
                      label: 'Unrealized P&L',
                      render: (p) => (
                        <span className={tone(p.unrealized_pnl)}>{number(p.unrealized_pnl)}</span>
                      ),
                    },
                    {
                      key: 'protect',
                      sticky: 'action',
                      label: 'Actions',
                      render: (p) => (
                        <button
                          className="text-button"
                          disabled={!canOperate}
                          onClick={() => navigate('execution', 'protect:' + String(p.inst_id))}
                        >
                          {t('Reduce account position')}
                        </button>
                      ),
                    },
                  ]}
                />
              )
            ) : orders.isPending ? (
              <Loading />
            ) : orders.isError ? (
              <ErrorBox error={orders.error} />
            ) : (
              <DataTable
                rows={pending}
                rowKey={(order) => String(order.id)}
                searchLabel="Search orders"
                searchKeys={['inst_id', 'side', 'order_type']}
                compactColumns={['inst_id', 'side', 'quantity', 'trigger', 'actions']}
                compactLabel={t('Pending orders')}
                empty="No working orders"
                columns={[
                  {
                    key: 'inst_id',
                    sticky: 'identity',
                    sortable: true,
                    label: 'Market',
                    render: (o) => (
                      <div className="table-stacked">
                        <strong>{valueText(o.inst_id)}</strong>
                        <small>{date(Number(o.created_at))}</small>
                      </div>
                    ),
                  },
                  { key: 'side', label: 'Side', render: (o) => t(valueText(o.side)) },
                  {
                    key: 'order_type',
                    label: 'Order type',
                    render: (o) => t(valueText(o.order_type)),
                  },
                  { key: 'quantity', label: 'Quantity', render: (o) => quantityText(o.quantity) },
                  {
                    key: 'trigger',
                    label: 'Limit / trigger',
                    render: (o) => price(o.limit_price ?? o.stop_price),
                  },
                  {
                    key: 'reduce_only',
                    label: 'Reduce only',
                    render: (o) => t(o.reduce_only ? 'Yes' : 'No'),
                  },
                  {
                    key: 'actions',
                    sticky: 'action',
                    label: 'Actions',
                    render: (o) => (
                      <button
                        className="text-button"
                        disabled={!canOperate || cancel.isPending}
                        onClick={() => cancel.mutate(String(o.id))}
                      >
                        {t('Cancel order')}
                      </button>
                    ),
                  },
                ]}
              />
            )}{' '}
            {cancel.isError && <ErrorBox error={cancel.error} />}
          </section>
          {showJourneyInBook && <section className="desk-onboarding-next">{journey}</section>}
          <section className="pro-panel overview-exposures">
            <div className="section-heading">
              <div>
                <h2>{t('Exposure by asset')}</h2>
                <p className="section-description">
                  {t('Spot and perpetual exposure are combined by underlying asset.')}
                </p>
              </div>
              <button className="text-button" onClick={() => navigate('risk')}>
                {t('Stress scenarios')}
                <ArrowRight size={13} />
              </button>
            </div>
            {analytics.isPending && !matchedReport ? (
              <Loading />
            ) : analytics.isError && !matchedReport ? (
              <ErrorBox error={analytics.error} />
            ) : !matchedReport ? (
              <p className="quiet-copy">{t('Exposure unavailable')}</p>
            ) : (
              <>
                <DataTable
                  rows={matchedReport.assets}
                  empty="No asset exposure"
                  columns={[
                    {
                      key: 'asset',
                      label: 'Asset',
                      render: (r) => <strong>{valueText(r.asset)}</strong>,
                    },
                    {
                      key: 'long_notional',
                      label: 'Long exposure',
                      render: (r) => number(r.long_notional),
                    },
                    {
                      key: 'short_notional',
                      label: 'Short exposure',
                      render: (r) => number(r.short_notional),
                    },
                    {
                      key: 'gross_notional',
                      label: 'Gross exposure',
                      render: (r) => number(r.gross_notional),
                    },
                    {
                      key: 'net_notional',
                      label: 'Net exposure',
                      render: (r) => number(r.net_notional),
                    },
                    {
                      key: 'gross_share_pct',
                      label: 'Gross share',
                      render: (r) =>
                        r.gross_share_pct == null ? '—' : `${number(r.gross_share_pct)}%`,
                    },
                  ]}
                />
                <p className="snapshot-footnote">
                  {t(
                    'Current snapshot only. No account equity history is inferred from research results.',
                  )}
                </p>
              </>
            )}
          </section>
          <section className="pro-panel overview-research">
            <div className="section-heading">
              <h2>{t('Research workspace')}</h2>
              <button className="text-button" onClick={() => navigate('research')}>
                {t('Open research')}
                <ArrowRight size={13} />
              </button>
            </div>
            {runs.isPending ? (
              <Loading />
            ) : runs.isError ? (
              <ErrorBox error={runs.error} />
            ) : (
              <DataTable
                rows={currentRuns.slice(0, 6)}
                empty="No research runs"
                columns={[
                  { key: 'id', label: 'Run', render: (r) => <code>{r.id.slice(0, 10)}</code> },
                  { key: 'mode', label: 'Evaluation scope', render: (r) => t(resultScope(r)) },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (r) => (
                      <Status type={r.status === 'failed' ? 'bad' : 'neutral'}>{r.status}</Status>
                    ),
                  },
                  {
                    key: 'return',
                    label: 'Research return',
                    render: (r) => percent(researchReturn(r)),
                  },
                  { key: 'created_at', label: 'Created', render: (r) => date(r.created_at) },
                ]}
              />
            )}
          </section>
        </div>
        <aside className="trader-secondary">
          <details className="desk-snapshot-basis">
            <summary>{text('Snapshot basis', '快照依据')}</summary>{' '}
            {matchedReport ? (
              <p className="snapshot-footnote">
                {t('Account and exposure values share one captured snapshot.')}
              </p>
            ) : (
              !analytics.isPending && (
                <p role="status" className="inline-warning">
                  {t('Exposure unavailable')}.{' '}
                  {t('A matching account capture was not returned. Refresh the exposure snapshot.')}{' '}
                  <button className="text-button" onClick={() => void analytics.refetch()}>
                    {t('Refresh snapshot')}
                  </button>
                </p>
              )
            )}
            <p className="snapshot-footnote">
              {t('Execution engine')}: {a?.execution_mode ?? '—'} · {t('Valuation status')}:{' '}
              {a?.valuation_status ?? '—'}
            </p>
            <p className="snapshot-footnote">
              {t('Service health does not confirm trading risk is clear.')}
            </p>
          </details>
          <details
            className="desk-conditions"
            ref={conditionsRef}
            open={conditionsOpen}
            onToggle={(event) => setConditionsOpen(event.currentTarget.open)}
          >
            <summary>
              {text('Condition details', '当前状况详情')}{' '}
              <span>
                {issues.length + alerts.filter((alert) => alert.severity !== 'neutral').length}
                {errors.length > 0 ? ` · ${text('Monitoring incomplete', '监控不完整')}` : ''}
              </span>
            </summary>
            <DeskAttention issues={issues} source={source} now={now} navigate={navigate} />
            {a?.economic_status === 'funding_pending' && (
              <div className="overview-funding-notice">
                <CircleAlert size={15} />
                <div>
                  <strong>{t('Funding settlement remains pending')}</strong>
                  <p>
                    {t(
                      'Account equity remains provisional until the historical funding obligation is settled. Inspect the ledger and protective exits before adding risk.',
                    )}
                  </p>
                </div>
                <button className="text-button" onClick={() => navigate('execution', 'ledger')}>
                  {t('Inspect funding & ledger')}
                  <ArrowRight size={13} />
                </button>
              </div>
            )}
            {!!errors.length && (
              <div className="overview-errors">
                <p className="inline-warning">
                  {t(
                    'Some snapshots could not be refreshed. Timestamped values may be from the previous successful capture.',
                  )}
                </p>
                {errors.map((q, i) => (
                  <ErrorBox key={i} error={q.error} onRetry={() => void q.refetch()} />
                ))}
              </div>
            )}
            <section className="pro-panel attention-panel">
              <div className="section-heading">
                <h2>{t('Attention queue')}</h2>
                <span className="subtle-tag">{ready ? alerts.length + issues.length : '—'}</span>
              </div>
              {alerts.length ? (
                <div className="attention-list">
                  {alerts.map((alert) => (
                    <button
                      key={alert.key}
                      className={`attention-item attention-${alert.severity}`}
                      onClick={() => navigate(alert.page, alert.view)}
                    >
                      <CircleAlert size={15} />
                      <span>
                        <strong>{alert.title}</strong>
                        <small>{alert.detail}</small>
                      </span>
                      <ArrowRight size={13} />
                    </button>
                  ))}
                </div>
              ) : ready ? (
                <p className="quiet-state">
                  {t(
                    issues.length
                      ? 'Unresolved conditions are listed above with their current inventory and response links.'
                      : 'No known unresolved conditions in the loaded snapshots.',
                  )}
                </p>
              ) : (
                <p className="quiet-state">
                  {t('Waiting for service snapshots. Missing data does not mean zero risk.')}
                </p>
              )}
            </section>
          </details>
          {journey && !showJourneyInBook && <div className="desk-continue-work">{journey}</div>}

          <section className="pro-panel overview-strategies">
            <div className="section-heading">
              <h2>{t('Deployment health')}</h2>
              <button className="text-button" onClick={() => navigate('execution', 'strategies')}>
                <ArrowRight size={14} />
                <span className="sr-only">{t('Open strategies')}</span>
              </button>
            </div>
            <div className="desk-status-row">
              <span>{t('Active portfolio groups')}</span>
              <strong>
                {groups.isSuccess
                  ? currentGroups.filter((group) =>
                      ['running', 'compensating'].includes(group.status),
                    ).length
                  : '—'}
              </strong>
            </div>
            <div className="desk-status-row">
              <span>{t('Active managed legs')}</span>
              <strong>
                {deployments.isSuccess
                  ? managedMembers.filter((strategy) => strategy.status === 'running').length
                  : '—'}
              </strong>
            </div>
            <div className="desk-status-row">
              <span>{t('Active standalone strategies')}</span>
              <strong>
                {deployments.isSuccess
                  ? independentStrategies.filter((strategy) => strategy.status === 'running').length
                  : '—'}
              </strong>
            </div>
            {deployments.isPending ? (
              <Loading />
            ) : deployments.isError ? (
              <ErrorBox error={deployments.error} />
            ) : strategies.length ? (
              <div className="strategy-health-list">
                {strategies.slice(0, 6).map((s) => {
                  const owner = currentGroups.find(
                    (group) =>
                      group.id === s.group_id ||
                      group.manifest?.legs.some((leg) => leg.deployment_id === s.id),
                  );
                  const ownerIssue =
                    !!owner && issues.some((issue) => issue.group?.id === owner.id);
                  return (
                    <div key={s.id}>
                      <div>
                        <strong>{s.inst_id}</strong>
                        <Status type={ownerIssue ? 'warning' : s.last_error ? 'bad' : 'neutral'}>
                          {ownerIssue ? t('Group requires attention') : s.status}
                        </Status>
                      </div>
                      {owner && (
                        <small>
                          {t('Portfolio group')}: {owner.manifest?.name ?? owner.id.slice(0, 8)}
                        </small>
                      )}
                      <small>
                        {t('Last evaluation')}: {date(s.last_bar)}
                      </small>
                      {s.last_error && <p className="inline-warning">{s.last_error}</p>}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="quiet-state">{t('No strategy is running for this source.')}</p>
            )}
          </section>
          <section className="pro-panel overview-work">
            <div className="section-heading">
              <h2>{t('Work & service')}</h2>
              <span className="service-health-state">
                {t('Service health')}:{' '}
                <Status
                  type={['healthy', 'ok'].includes(String(healthStatus)) ? 'neutral' : 'warning'}
                >
                  {t(String(healthStatus ?? 'unavailable'))}
                </Status>
              </span>
            </div>
            {ops.data?.economic_health && (
              <div className="desk-status-row">
                <span>{t('Workspace economic monitor')}</span>
                <Status type={ops.data.economic_health.status === 'ok' ? 'neutral' : 'warning'}>
                  {t(String(ops.data.economic_health.status ?? 'unavailable'))}
                </Status>
              </div>
            )}
            <div className="desk-status-row">
              <span>{t('Active jobs')}</span>
              <strong>{ops.isSuccess ? activeJobs.length : '—'}</strong>
            </div>
            <div className="desk-status-row">
              <span>{t('Research packages ready')}</span>
              <strong>
                {packages.isSuccess ? currentPackages.filter((p) => p.ready).length : '—'}
              </strong>
            </div>
            <div className="desk-status-row">
              <span>{t('Research packages preparing')}</span>
              <strong>
                {packages.isSuccess
                  ? currentPackages.filter((p) =>
                      ['queued', 'running', 'preparing'].includes(p.status),
                    ).length
                  : '—'}
              </strong>
            </div>
            {!!activeJobs.length && (
              <ul className="active-job-list">
                {activeJobs.slice(0, 4).map((j) => (
                  <li key={j.id}>
                    <span>{j.inst_id ?? j.id.slice(0, 8)}</span>
                    <Status>{j.status}</Status>
                  </li>
                ))}
              </ul>
            )}
            <div className="overview-side-actions">
              <button className="text-button" onClick={() => navigate('data')}>
                {t('Prepare research data')}
                <ArrowRight size={12} />
              </button>
              <button className="text-button" onClick={() => navigate('operations')}>
                {t('Inspect operations')}
                <ArrowRight size={12} />
              </button>
            </div>
          </section>
        </aside>
      </div>
      <MarketContext
        initiallyOpen={marketRequested}
        source={source}
        initialSymbol={symbol}
        onSymbol={setSymbol}
        now={now}
      />
    </div>
  );
}
function MarketContext({
  source,
  initialSymbol,
  onSymbol,
  now,
  initiallyOpen = false,
}: {
  source: Source;
  initialSymbol: string;
  onSymbol: (symbol: string) => void;
  now: number;
  initiallyOpen?: boolean;
}) {
  const { t } = useI18n();
  const marketSection = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(initiallyOpen);
  useEffect(() => {
    if (initiallyOpen) {
      setOpen(true);
      const frame = requestAnimationFrame(() => {
        marketSection.current?.scrollIntoView({ block: 'start' });
        marketSection.current?.querySelector('button')?.focus({ preventScroll: true });
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [initiallyOpen, initialSymbol]);
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [symbol, setSymbol] = useState(initialSymbol);
  useEffect(() => {
    setSymbol(initialSymbol);
    setProduct(initialSymbol.endsWith('-SWAP') ? 'SWAP' : 'SPOT');
  }, [initialSymbol]);
  const market = useQuery({
    queryKey: ['pro-market', source, symbol],
    queryFn: () => proApi.market(source, symbol),
    enabled: open,
    refetchInterval: open ? 5000 : false,
  });
  const m = market.data;
  const tick = isRecord(m?.instrument) ? m.instrument.tick_size : undefined;
  const stale = source === 'okx' && m && now - Number(m.ts) >= 15000;
  return (
    <section ref={marketSection} className="pro-panel overview-market-context">
      <button
        type="button"
        className="market-context-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>
          <strong>{t('Market context')}</strong>
          <small>{t('Spot and perpetual snapshots, requested on demand.')}</small>
        </span>
        <span>
          {t(open ? 'Hide' : 'Show')}
          <ArrowRight size={13} />
        </span>
      </button>
      {open && (
        <div className="market-context-content">
          <ProductSymbol
            source={source}
            value={symbol}
            product={product}
            onProductChange={setProduct}
            onChange={(s) => {
              setSymbol(s);
              onSymbol(s);
            }}
          />
          {market.isPending ? (
            <Loading />
          ) : market.isError ? (
            <ErrorBox error={market.error} onRetry={() => void market.refetch()} />
          ) : (
            <>
              <div className="market-snapshot-strip">
                <Metric label="Last price" value={price(m?.last, tick)} />
                <Metric label="Bid" value={price(m?.bid, tick)} />
                <Metric label="Ask" value={price(m?.ask, tick)} />
                {product === 'SWAP' && (
                  <>
                    <Metric label="Mark price" value={price(m?.mark, tick)} />
                    <Metric
                      label="Funding rate"
                      value={
                        m?.funding_rate == null
                          ? '—'
                          : `${number(Number(m.funding_rate) * 100, 4)}%`
                      }
                    />
                  </>
                )}
              </div>
              <p className={stale ? 'inline-warning' : 'snapshot-footnote'}>
                {t('Exchange as of')}: {date(Number(m?.ts))} ·{' '}
                {source === 'example'
                  ? t('Example · synthetic')
                  : `${Math.max(0, Math.floor((now - Number(m?.ts)) / 1000))}s ${t('old')}`}
                {stale && ` · ${t('Quote is stale. New risk may be rejected.')}`}
              </p>
              <JsonDetails value={m} label="Instrument rules" />
            </>
          )}
        </div>
      )}
    </section>
  );
}
