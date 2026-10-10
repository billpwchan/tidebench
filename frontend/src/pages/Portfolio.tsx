import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  CircleAlert,
  Clock3,
  Loader2,
  Play,
  Plus,
  ShieldOff,
  Square,
  X,
} from 'lucide-react';
import { defaultStrategy, downloadCsv } from '../api';
import type { Source, Strategy } from '../api';
import { useSession } from '../components/AuthGate';
const ForwardPerformance = lazy(() => import('../components/ForwardPerformance'));
const ProtectiveExit = lazy(() => import('../components/ProtectiveExit'));
import ManagedPortfolios, { StopPortfolioDialog } from '../components/ManagedPortfolios';
const Contributions = lazy(() => import('../components/Contributions'));
import SimulationClock from '../components/SimulationClock';
import BudgetSizing from '../components/BudgetSizing';
const ReleaseHistory = lazy(() => import('../components/ReleaseHistory'));
import PortfolioAnalytics from '../components/PortfolioAnalytics';
import { proApi } from '../proApi';
import type { Direction, ManagedPortfolio, OrderRequest, ProRisk, RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { canTrade, canManageRisk } from '../lib/permissions';
import { bars } from '../lib/config';
import { date, number, price, quantityText, tone } from '../lib/format';
import { useDialogFocus, useNow } from '../lib/hooks';
import { useReceiptOwnership, type ReceiptTask } from '../lib/receiptOwnership';
import {
  ActionNote,
  ErrorBox,
  Field,
  Loading,
  Metric,
  PageHeading,
  Status,
  StrategyFields,
} from '../components/workspace';
import {
  DataTable,
  JsonDetails,
  ProductSymbol,
  RecordGrid,
  valueText,
} from '../components/ProWorkspace';

const AccountCapitalPolicy = lazy(() => import('../components/AccountCapitalPolicy'));

export default function Portfolio({
  source,
  initialView = 'positions',
  initialSymbol = 'BTC-USDT',
  marketRequested = false,
  onViewChange,
  onSymbolChange,
}: {
  source: Source;
  initialView?: string;
  initialSymbol?: string;
  marketRequested?: boolean;
  onViewChange?: (view: string) => void;
  onSymbolChange?: (symbol: string) => void;
}) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const [ticketOpen, setTicketOpen] = useState(marketRequested || initialView === 'order');
  const [allMetrics, setAllMetrics] = useState(false);
  const [orderScope, setOrderScope] = useState<'working' | 'all'>('working');
  const ticketRef = useRef<HTMLElement>(null);
  const ticketReturnFocus = useRef<HTMLElement | null>(null);
  const ticketRequest = useRef('');
  const canOperate = canTrade(useSession()?.user?.role);
  const qc = useQueryClient();
  const now = useNow();
  const protectiveLink = useRef('');
  const [protection, setProtection] = useState<{ source: Source; position: RecordData }>();
  const viewTable = (view: string) =>
    view === 'order' || view.startsWith('protect:')
      ? 'positions'
      : view.startsWith('portfolio-release:')
        ? 'managed'
        : view.split(':')[0];
  const [table, setTable] = useState(viewTable(initialView));
  useEffect(() => setTable(viewTable(initialView)), [initialView]);
  const [viewsOpen, setViewsOpen] = useState(
    !['positions', 'orders'].includes(viewTable(initialView)),
  );
  useEffect(() => {
    setProtection(undefined);
    setTicketOpen(false);
  }, [source]);
  useEffect(() => {
    if (!['positions', 'orders'].includes(table)) setViewsOpen(true);
  }, [table]);
  useEffect(() => {
    const requested = marketRequested || initialView === 'order';
    const key = requested ? `${source}:${initialSymbol}:${initialView}` : '';
    if (initialView.startsWith('protect:')) setTicketOpen(false);
    else if (key && ticketRequest.current !== key) setTicketOpen(true);
    ticketRequest.current = key;
  }, [marketRequested, initialView, initialSymbol, source]);
  const initialGroupId = initialView.startsWith('managed:') ? initialView.slice(8) : undefined;
  const initialReleaseId = initialView.startsWith('portfolio-release:')
    ? initialView.slice('portfolio-release:'.length)
    : undefined;
  const initialDeploymentId = initialView.startsWith('strategies:')
    ? initialView.slice('strategies:'.length)
    : undefined;
  const changeView = (view: string) => {
    deployReceipt.invalidate();
    setTable(viewTable(view));
    onViewChange?.(view);
  };
  const closeTicket = () => {
    setTicketOpen(false);
    if (initialView === 'order') changeView('positions');
    const target = ticketReturnFocus.current?.isConnected
      ? ticketReturnFocus.current
      : document.querySelector<HTMLButtonElement>(
          '.desk-execution button[aria-controls="desk-order-ticket"]',
        );
    target?.focus({ preventScroll: true });
  };
  const openTicket = () => {
    ticketReturnFocus.current = document.activeElement as HTMLElement | null;
    setTicketOpen(true);
    changeView('order');
  };
  const protectPosition = (position: RecordData) => {
    setTicketOpen(false);
    setProtection({ source, position });
  };
  const closeTicketRef = useRef(closeTicket);
  closeTicketRef.current = closeTicket;
  useEffect(() => {
    if (!ticketOpen) return;
    const frame = requestAnimationFrame(() => {
      ticketRef.current?.scrollIntoView({ block: 'nearest' });
      ticketRef.current?.querySelector<HTMLElement>('h2')?.focus({ preventScroll: true });
    });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && ticketRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        closeTicketRef.current();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKey);
    };
  }, [ticketOpen, initialSymbol, source]);
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>(
    initialSymbol.endsWith('-SWAP') ? 'SWAP' : 'SPOT',
  );
  const [symbol, setSymbol] = useState(initialSymbol);
  useEffect(() => {
    setSymbol(initialSymbol);
    setProduct(initialSymbol.endsWith('-SWAP') ? 'SWAP' : 'SPOT');
  }, [initialSymbol]);
  const selectSymbol = (next: string) => {
    setSymbol(next);
    setProduct(next.endsWith('-SWAP') ? 'SWAP' : 'SPOT');
    onSymbolChange?.(next);
  };
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [leverage, setLeverage] = useState(1);
  const [reduceOnly, setReduceOnly] = useState(false);
  const [orderType, setOrderType] = useState<OrderRequest['order_type']>('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [stopGroup, setStopGroup] = useState<{ id: string; group?: ManagedPortfolio }>();
  const [strategy, setStrategy] = useState<Strategy>({ ...defaultStrategy });
  const [direction, setDirection] = useState<Direction>('long_only');
  const [bar, setBar] = useState('1H');
  const deployReceipt = useReceiptOwnership(
    JSON.stringify([
      source,
      initialView,
      table,
      symbol,
      product,
      strategy,
      direction,
      bar,
      leverage,
      deployOpen,
    ]),
  );
  const closeDeployment = () => {
    deployReceipt.invalidate();
    setDeployOpen(false);
  };
  const idempotency = useRef<{ payload: string; key: string } | null>(null);
  const account = useQuery({
    queryKey: ['pro-account', source],
    queryFn: () => proApi.account(source),
    refetchInterval: 5000,
  });
  const orders = useQuery({
    queryKey: ['pro-orders', source],
    queryFn: () => proApi.orders(source),
    refetchInterval: 5000,
  });
  const ledger = useQuery({
    queryKey: ['pro-ledger', source],
    queryFn: () => proApi.ledger(source),
    refetchInterval: 10000,
  });
  const deployments = useQuery({
    queryKey: ['pro-deployments', source],
    queryFn: () => proApi.deployments(source),
    refetchInterval: 5000,
  });
  const groups = useQuery({
    queryKey: ['managed-portfolios', source],
    queryFn: () => proApi.managedPortfolios(source),
    enabled: table === 'strategies',
    refetchInterval: 5000,
  });
  const risk = useQuery({
    queryKey: ['pro-risk', source],
    queryFn: () => proApi.risk(source),
    refetchInterval: 10000,
  });
  const market = useQuery({
    queryKey: ['pro-market', source, symbol],
    queryFn: () => proApi.market(source, symbol),
    refetchInterval: 5000,
  });
  const body: OrderRequest = {
    source,
    inst_id: symbol,
    side,
    quantity,
    leverage: product === 'SWAP' ? leverage : 1,
    reduce_only: product === 'SWAP' ? reduceOnly : side === 'sell',
    margin_mode: 'isolated',
    order_type: orderType,
    ...(orderType === 'limit' ? { limit_price: limitPrice } : {}),
    ...(orderType === 'stop_market' ? { stop_price: stopPrice } : {}),
  };
  const riskBlocksOrder = !!risk.data?.halted && !body.reduce_only;
  const payload = JSON.stringify(body);
  const currentDraft = useRef(payload);
  currentDraft.current = payload;
  const refresh = () => {
    for (const key of [
      'pro-account',
      'pro-orders',
      'pro-ledger',
      'pro-deployments',
      'pro-risk',
      'portfolio-analytics',
      'managed-portfolios',
      'contributions',
      'portfolio-releases',
    ])
      void qc.invalidateQueries({ queryKey: [key, source] });
    void qc.invalidateQueries({ queryKey: ['pro-ops'] });
  };
  const preview = useMutation({ mutationFn: proApi.previewOrder });
  const previewMatches = !!preview.data && JSON.stringify(preview.variables) === payload;
  const order = useMutation({
    mutationFn: ({ data, key }: { data: OrderRequest; key: string }) => proApi.order(data, key),
    onSuccess: (r, submitted) => {
      setNotice(`${t('Order submitted')} · ${r.id} · ${r.status}`);
      const submittedPayload = JSON.stringify(submitted.data);
      if (idempotency.current?.payload === submittedPayload) idempotency.current = null;
      // A response for a previous order must not erase the trader's next draft.
      if (currentDraft.current === submittedPayload) {
        setQuantity('');
        preview.reset();
      }
      refresh();
    },
  });
  const cancel = useMutation({ mutationFn: proApi.cancelOrder, onSuccess: refresh });
  const stop = useMutation({
    mutationFn: proApi.stop,
    onSuccess: () => {
      setNotice(t('Strategy stopped; positions retained'));
      refresh();
    },
  });
  const deploy = useMutation({
    mutationFn: ({ body }: { body: Parameters<typeof proApi.deploy>[0]; task: ReceiptTask }) =>
      proApi.deploy(body),
    onSuccess: (deployment, submitted) => {
      refresh();
      if (
        deployReceipt.owns(submitted.task) &&
        deployment.source === submitted.body.source &&
        deployment.inst_id === submitted.body.inst_id
      ) {
        setDeployOpen(false);
        setNotice(t('Strategy deployed'));
        changeView(`strategies:${deployment.id}`);
      }
    },
  });
  const submit = () => {
    if (!previewMatches) return;
    if (idempotency.current?.payload !== payload)
      idempotency.current = { payload, key: crypto.randomUUID() };
    order.mutate({ data: body, key: idempotency.current.key });
  };
  useDialogFocus(deployOpen, '.deployment-dialog', closeDeployment);
  const a = account.data;
  useEffect(() => {
    const link = source + ':' + initialView;
    if (!initialView.startsWith('protect:')) {
      protectiveLink.current = '';
      return;
    }
    if (!account.data || protectiveLink.current === link) return;
    const selected = account.data.positions?.find((p) => p.inst_id === initialView.slice(8));
    if (selected) {
      protectiveLink.current = link;
      setTicketOpen(false);
      setProtection({ source, position: selected });
    }
  }, [initialView, source, account.data]);
  const instrument = (market.data?.instrument ?? {}) as RecordData;
  return (
    <div className="desk-execution">
      {stopGroup && (
        <StopPortfolioDialog
          source={source}
          groupId={stopGroup.id}
          name={stopGroup.group?.manifest?.name}
          markets={stopGroup.group?.manifest?.legs.map((leg) => leg.inst_id)}
          onClose={() => setStopGroup(undefined)}
          onStopped={() => {
            setNotice(
              language === 'zh-CN'
                ? '整个组合已停止。已成交库存仍保留在账户中；停止不代表清仓。'
                : 'Whole portfolio stopped. Filled inventory remains in the account; stopping does not close positions.',
            );
            setStopGroup(undefined);
            refresh();
          }}
        />
      )}
      {protection?.source === source && (
        <Suspense fallback={<Loading />}>
          <ProtectiveExit
            key={source + ':' + String(protection.position.inst_id)}
            source={source}
            position={protection.position}
            onClose={() => {
              setProtection(undefined);
              if (initialView.startsWith('protect:')) changeView('positions');
            }}
          />
        </Suspense>
      )}
      <PageHeading
        eyebrow="PORTFOLIO & EXECUTION"
        title="Portfolio"
        description="Spot balances, isolated perpetual margin, order lifecycle, and strategy health."
      >
        <span className="subtle-tag">
          {a?.execution_mode ?? '—'} · {source.toUpperCase()}
        </span>
        <button
          className="button button-dark"
          disabled={!canOperate}
          onClick={openTicket}
          aria-expanded={ticketOpen}
          aria-controls="desk-order-ticket"
        >
          <Plus size={14} /> {text('New order', '新建订单')}
        </button>
        <button
          className="button button-secondary"
          disabled={!canOperate}
          onClick={() => {
            setTicketOpen(false);
            deployReceipt.invalidate();
            setDeployOpen(true);
          }}
        >
          <Plus size={14} />
          {t('Deploy strategy')}
        </button>
      </PageHeading>
      <ActionNote text={notice} />
      {risk.data?.halted && (
        <div className="halt-notice">
          <ShieldOff size={17} />
          <strong>{t('Execution halted')}</strong>
          <span>{t('New risk is paused. Reduce-only exits remain available.')}</span>
        </div>
      )}
      {account.isPending ? (
        <Loading />
      ) : account.isError ? (
        <ErrorBox error={account.error} onRetry={() => void account.refetch()} />
      ) : (
        <div
          className={`pro-metric-strip account-metrics desk-account-strip${allMetrics ? ' is-expanded' : ''}`}
        >
          <Metric
            label="Account equity"
            value={number(a?.equity)}
            unit="USDT"
            note={a?.valuation_status}
          />
          <Metric label="Used margin" value={number(a?.used_margin)} unit="USDT" />
          <Metric label="Available cash" value={number(a?.available_cash)} unit="USDT" />
          <Metric label="Maintenance margin" value={number(a?.maintenance_margin)} unit="USDT" />
          <Metric
            label="Unrealized P&L"
            value={number(a?.unrealized_pnl)}
            className={tone(a?.unrealized_pnl)}
            unit="USDT"
          />
          <Metric label="Funding paid" value={number(a?.funding_paid)} unit="USDT" />
        </div>
      )}
      <button
        className="text-button desk-metrics-toggle"
        aria-expanded={allMetrics}
        onClick={() => setAllMetrics((value) => !value)}
      >
        {allMetrics
          ? text('Fewer account values', '收起账户数值')
          : text('Cash, P&L & funding', '现金、盈亏与资金费')}
      </button>
      <div className="desk-execution-state">
        <span>
          <Clock3 size={12} /> {t('Account snapshot')}: {date(a?.as_of, true)}
        </span>
        <Status type={risk.data?.halted ? 'bad' : risk.isSuccess ? 'neutral' : 'warning'}>
          {risk.isSuccess
            ? t(risk.data.halted ? 'New risk halted' : 'New risk enabled')
            : t('Risk status unavailable')}
        </Status>
        {typeof a?.economic_status === 'string' && a.economic_status !== 'complete' && (
          <span className="inline-warning">
            {t(
              (
                {
                  funding_pending: 'Funding settlement remains pending',
                  funding_schedule_unavailable: 'Funding schedule needs attention',
                } as Record<string, string>
              )[a.economic_status] ?? 'Monitoring incomplete',
            )}
          </span>
        )}
      </div>
      <div className={`execution-layout desk-execution-layout${ticketOpen ? ' has-ticket' : ''}`}>
        <div className="execution-main">
          <section className="pro-panel account-book-panel">
            <div className="section-heading">
              <h2>{t('Portfolio')}</h2>
              <button
                className="text-button"
                disabled={!a?.positions?.length}
                onClick={() =>
                  a?.positions && downloadCsv(a.positions, `tidebench-${source}-positions.csv`)
                }
              >
                {t('Export CSV')}
              </button>
            </div>
            <div className="desk-book-navigation">
              <div
                className="desk-book-tabs"
                role="tablist"
                aria-label={t('Portfolio')}
                onKeyDown={(event) => {
                  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                  event.preventDefault();
                  const next =
                    event.key === 'Home'
                      ? 'positions'
                      : event.key === 'End'
                        ? 'orders'
                        : table === 'orders'
                          ? 'positions'
                          : 'orders';
                  changeView(next);
                  event.currentTarget
                    .querySelectorAll<HTMLButtonElement>('[role="tab"]')
                    [next === 'positions' ? 0 : 1]?.focus();
                }}
              >
                <button
                  role="tab"
                  tabIndex={table === 'orders' ? -1 : 0}
                  aria-selected={table === 'positions'}
                  className={table === 'positions' ? 'active' : ''}
                  onClick={() => changeView('positions')}
                >
                  {t('Positions')}{' '}
                  <span>{account.isSuccess ? (a?.positions?.length ?? '—') : '—'}</span>
                </button>
                <button
                  role="tab"
                  tabIndex={table === 'orders' ? 0 : -1}
                  aria-selected={table === 'orders'}
                  className={table === 'orders' ? 'active' : ''}
                  onClick={() => changeView('orders')}
                >
                  {text('Working orders', '当前挂单')}{' '}
                  <span>
                    {orders.isSuccess
                      ? orders.data.items.filter((order) => order.status === 'pending').length
                      : '—'}
                  </span>
                </button>
              </div>
              <details
                className="desk-view-disclosure"
                open={viewsOpen}
                onToggle={(event) => setViewsOpen(event.currentTarget.open)}
              >
                <summary>
                  {text('Analysis & controls', '分析与控制')}
                  {!['positions', 'orders'].includes(table) && (
                    <span>
                      {t(
                        (
                          {
                            performance: 'Forward performance',
                            contributions: 'Contribution',
                            analytics: 'Exposure & scenarios',
                            managed: 'Managed portfolios',
                            strategies: 'Strategies',
                            releases: 'Paper releases',
                            ledger: 'Ledger',
                          } as Record<string, string>
                        )[table] ?? table,
                      )}
                    </span>
                  )}
                </summary>
                <div className="desk-view-groups">
                  <div>
                    <span>{text('Account analysis', '账户分析')}</span>
                    {[
                      ['performance', 'Forward performance'],
                      ['analytics', 'Exposure & scenarios'],
                      ['contributions', 'Contribution'],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        className={table === key ? 'active' : ''}
                        aria-pressed={table === key}
                        onClick={() => changeView(key)}
                      >
                        {t(label)}
                      </button>
                    ))}
                  </div>
                  <div>
                    <span>{text('Strategy operations', '策略操作')}</span>
                    {[
                      ['managed', 'Managed portfolios'],
                      ['strategies', 'Strategies'],
                      ['releases', 'Paper releases'],
                      ['ledger', 'Ledger'],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        className={table === key ? 'active' : ''}
                        aria-pressed={table === key}
                        onClick={() => changeView(key)}
                      >
                        {t(label)}
                      </button>
                    ))}
                  </div>
                </div>
              </details>
            </div>
            <p className="desk-money-unit">
              {text(
                'Monetary values in USDT. Quantities use the instrument units shown.',
                '金额单位为 USDT；数量使用各品种标明的单位。',
              )}
            </p>
            {table === 'performance' && (
              <Suspense fallback={<Loading />}>
                <ForwardPerformance
                  source={source}
                  initialSnapshotId={
                    initialView.startsWith('performance:') ? initialView.slice(12) : undefined
                  }
                  onSnapshotSelect={(id) => changeView(`performance:${id}`)}
                />
              </Suspense>
            )}
            {table === 'managed' && (
              <ManagedPortfolios
                source={source}
                initialGroupId={initialGroupId}
                initialReleaseId={initialReleaseId}
                onSelectGroup={(id) => changeView(id ? `managed:${id}` : 'managed')}
                onProtectPosition={protectPosition}
                onInspectPositions={() => changeView('positions')}
              />
            )}
            {table === 'contributions' && (
              <Suspense fallback={<Loading />}>
                <Contributions source={source} />
              </Suspense>
            )}
            {table === 'releases' && (
              <Suspense fallback={<Loading />}>
                <ReleaseHistory
                  source={source}
                  initialReleaseId={
                    initialView.startsWith('releases:') ? initialView.slice(9) : undefined
                  }
                  onInspectDeployment={(id) => changeView(`strategies:${id}`)}
                />
              </Suspense>
            )}
            {table === 'analytics' && <PortfolioAnalytics source={source} />}
            {table === 'positions' &&
              (account.isPending ? (
                <Loading />
              ) : account.isError ? (
                <ErrorBox error={account.error} />
              ) : (
                <DataTable
                  rows={a?.positions ?? []}
                  rowKey={(position) => String(position.inst_id)}
                  searchLabel="Search positions"
                  searchKeys={['inst_id', 'side', 'inst_type']}
                  compactColumns={[
                    'inst_id',
                    'quantity',
                    'liquidation_price',
                    'unrealized_pnl',
                    'protect',
                  ]}
                  compactLabel={t('Positions')}
                  empty="No positions"
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
                            {valueText(p.inst_type)} · {valueText(p.side)}
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
                          <small>{p.inst_type === 'SWAP' ? t('Contracts') : t('Base units')}</small>
                        </div>
                      ),
                    },
                    {
                      key: 'entry_price',
                      sortable: true,
                      sortType: 'number',
                      label: 'Entry price',
                      render: (p) => price(p.entry_price),
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
                    { key: 'margin', label: 'Margin', render: (p) => number(p.margin) },
                    {
                      key: 'maintenance_margin',
                      sortable: true,
                      sortType: 'number',
                      label: 'Maintenance margin',
                      render: (p) => number(p.maintenance_margin),
                    },
                    {
                      key: 'liquidation_price',
                      sortable: true,
                      sortType: 'number',
                      label: 'Liquidation price',
                      render: (p) => (
                        <div className="table-stacked">
                          <span>{price(p.liquidation_price)}</span>
                          {p.liquidation_price != null && (
                            <small>{valueText(p.liquidation_price_kind)}</small>
                          )}
                        </div>
                      ),
                    },
                    {
                      key: 'leverage',
                      sortable: true,
                      sortType: 'number',
                      label: 'Leverage',
                      render: (p) => (p.inst_type === 'SWAP' ? `${p.leverage}×` : '—'),
                    },
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
                          onClick={() => protectPosition(p)}
                        >
                          {t('Reduce account position')}
                        </button>
                      ),
                    },
                  ]}
                />
              ))}{' '}
            {table === 'orders' && (
              <div className="desk-order-scope" aria-label={t('Orders')}>
                <button
                  className={orderScope === 'working' ? 'active' : ''}
                  aria-pressed={orderScope === 'working'}
                  onClick={() => setOrderScope('working')}
                >
                  {text('Working', '当前挂单')}
                </button>
                <button
                  className={orderScope === 'all' ? 'active' : ''}
                  aria-pressed={orderScope === 'all'}
                  onClick={() => setOrderScope('all')}
                >
                  {text('All orders & outcomes', '全部订单与结果')}
                </button>
              </div>
            )}
            {table === 'orders' &&
              (orders.isPending ? (
                <Loading />
              ) : orders.isError ? (
                <ErrorBox error={orders.error} />
              ) : (
                <DataTable
                  rows={(orders.data?.items ?? []).filter(
                    (order) => orderScope === 'all' || order.status === 'pending',
                  )}
                  rowKey={(order) => String(order.id)}
                  searchLabel="Search orders"
                  searchKeys={['inst_id', 'side', 'order_type', 'status']}
                  compactColumns={[
                    'inst_id',
                    'side',
                    'quantity',
                    'limit_price',
                    'status',
                    'actions',
                  ]}
                  compactLabel={text('Working orders', '当前挂单')}
                  empty={orderScope === 'working' ? 'No working orders' : 'No orders'}
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
                    { key: 'side', label: 'Side' },
                    { key: 'order_type', label: 'Order type' },
                    {
                      key: 'quantity',
                      sortValue: (order) => order.requested_quantity ?? order.quantity,
                      sortable: true,
                      sortType: 'number',
                      label: 'Requested / filled',
                      render: (o) => (
                        <div className="table-stacked">
                          <span>
                            {quantityText(o.requested_quantity ?? o.quantity)} /{' '}
                            {quantityText(
                              o.filled_quantity ?? (o.status === 'filled' ? o.quantity : '0'),
                            )}
                          </span>
                          {o.canceled_quantity != null && (
                            <small>
                              {t('Canceled remainder')}: {quantityText(o.canceled_quantity)}
                            </small>
                          )}
                        </div>
                      ),
                    },
                    {
                      key: 'limit_price',
                      sortValue: (order) =>
                        order.order_type === 'limit'
                          ? order.limit_price
                          : order.order_type === 'stop_market'
                            ? order.stop_price
                            : null,
                      sortable: true,
                      sortType: 'number',
                      label: 'Limit / trigger',
                      render: (o) =>
                        o.order_type === 'limit'
                          ? price(o.limit_price)
                          : o.order_type === 'stop_market'
                            ? price(o.stop_price)
                            : '—',
                    },
                    {
                      key: 'price',
                      sortable: true,
                      sortType: 'number',
                      label: 'Fill price',
                      render: (o) => (o.status === 'filled' ? price(o.price) : '—'),
                    },
                    {
                      key: 'fee',
                      sortable: true,
                      sortType: 'number',
                      label: 'Fee',
                      render: (o) => (o.status === 'filled' ? number(o.fee, 4) : '—'),
                    },
                    {
                      key: 'status',
                      label: 'Status',
                      render: (o) => (
                        <Status
                          type={
                            o.status === 'filled'
                              ? 'good'
                              : o.status === 'rejected'
                                ? 'bad'
                                : 'neutral'
                          }
                        >
                          {valueText(o.status)}
                        </Status>
                      ),
                    },
                    {
                      key: 'actions',
                      sticky: 'action',
                      label: 'Actions',
                      render: (o) => (
                        <div className="table-actions">
                          {o.status === 'pending' && (
                            <button
                              className="text-button"
                              disabled={!canOperate || cancel.isPending}
                              onClick={() => cancel.mutate(String(o.id))}
                            >
                              {t('Cancel')}
                            </button>
                          )}
                          <JsonDetails value={o} />
                        </div>
                      ),
                    },
                  ]}
                />
              ))}
            {table === 'ledger' &&
              (ledger.isPending ? (
                <Loading />
              ) : ledger.isError ? (
                <ErrorBox error={ledger.error} />
              ) : (
                <DataTable
                  rows={ledger.data?.items ?? []}
                  empty="No ledger entries"
                  columns={[
                    { key: 'ts', label: 'Time', render: (l) => date(Number(l.ts)) },
                    { key: 'asset', label: 'Asset' },
                    { key: 'account', label: 'Account' },
                    { key: 'debit', label: 'Debit', render: (l) => number(l.debit, 6) },
                    { key: 'credit', label: 'Credit', render: (l) => number(l.credit, 6) },
                    { key: 'memo', label: 'Memo' },
                    { key: 'reference', label: 'Reference' },
                  ]}
                />
              ))}
            {table === 'strategies' &&
              (deployments.isPending ? (
                <Loading />
              ) : deployments.isError ? (
                <ErrorBox error={deployments.error} />
              ) : (
                <>
                  {initialDeploymentId &&
                    !deployments.data?.items.some((d) => d.id === initialDeploymentId) && (
                      <ErrorBox
                        error={
                          new Error(
                            language === 'zh-CN'
                              ? '指定的策略部署在此账户中不可用。'
                              : 'Selected deployment is unavailable in this account.',
                          )
                        }
                      />
                    )}
                  <DataTable
                    rows={[...(deployments.data?.items ?? [])].sort(
                      (a, b) =>
                        Number(b.id === initialDeploymentId) - Number(a.id === initialDeploymentId),
                    )}
                    empty="No strategy deployments"
                    columns={[
                      {
                        key: 'inst_id',
                        label: 'Market',
                        render: (d) => (
                          <div className="table-stacked">
                            <strong data-deployment-id={d.id}>{d.inst_id}</strong>
                            <small>
                              {d.bar} · {d.direction} · {d.leverage}×
                            </small>
                            <small>{d.id}</small>
                            {d.id === initialDeploymentId && (
                              <Status type="neutral">
                                {language === 'zh-CN' ? '指定部署' : 'Selected deployment'}
                              </Status>
                            )}
                            {!!d.group_id && (
                              <small>
                                {t('Portfolio group')}:{' '}
                                {groups.data?.items.find((g) => g.id === d.group_id)?.manifest
                                  ?.name ?? String(d.group_id)}
                              </small>
                            )}
                          </div>
                        ),
                      },
                      {
                        key: 'status',
                        label: 'Status',
                        render: (d) => (
                          <Status type={d.status === 'running' ? 'good' : 'neutral'}>
                            {d.status}
                          </Status>
                        ),
                      },
                      {
                        key: 'last_bar',
                        label: 'Last evaluation',
                        render: (d) => date(d.last_bar),
                      },
                      {
                        key: 'last_error',
                        label: 'Last error',
                        render: (d) => <span className="negative">{d.last_error ?? '—'}</span>,
                      },
                      {
                        key: 'actions',
                        label: 'Actions',
                        render: (d) => (
                          <div className="table-actions">
                            {!!d.group_id && (
                              <button
                                className="text-button"
                                onClick={() => changeView(`managed:${String(d.group_id)}`)}
                              >
                                {t('Inspect group & recovery')}
                              </button>
                            )}
                            {d.status === 'running' && (
                              <button
                                className="text-button"
                                disabled={!canOperate || stop.isPending}
                                onClick={() =>
                                  d.group_id
                                    ? setStopGroup({
                                        id: String(d.group_id),
                                        group: groups.data?.items.find((g) => g.id === d.group_id),
                                      })
                                    : stop.mutate(d.id)
                                }
                              >
                                <Square size={11} />
                                {t(d.group_id ? 'Stop whole portfolio' : 'Stop')}
                              </button>
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                </>
              ))}
            {cancel.isError && <ErrorBox error={cancel.error} />}
            {stop.isError && <ErrorBox error={stop.error} />}
          </section>
          {source === 'example' && (
            <details className="desk-account-details">
              <summary>{t('Synthetic market clock')}</summary>
              <SimulationClock />
            </details>
          )}
          <details className="desk-account-details">
            <summary>
              {t('Account accounting')} <span>{t('Details')}</span>
            </summary>
            <section className="pro-panel account-detail-panel">
              <div className="section-heading">
                <h2>{t('Account accounting')}</h2>
                <span className="quiet-copy">{date(a?.as_of)}</span>
              </div>
              <RecordGrid
                value={{
                  cash: a?.cash,
                  realized_pnl: a?.realized_pnl,
                  fees_paid: a?.fees_paid,
                  funding_paid: a?.funding_paid,
                  insurance_debt: a?.insurance_debt,
                  reserved_cash: a?.reserved_cash,
                  valuation_status: a?.valuation_status,
                }}
              />
              <JsonDetails value={a} label="Details" />
            </section>
          </details>
          <details className="desk-account-details desk-risk-controls">
            <summary>
              {t('Risk limits')}{' '}
              <Status type={risk.data?.halted ? 'bad' : 'neutral'}>
                {risk.isSuccess
                  ? t(risk.data.halted ? 'Execution halted' : 'New risk enabled')
                  : t('Risk status unavailable')}
              </Status>
            </summary>
            <ExecutionRisk source={source} />
          </details>
        </div>
        {ticketOpen && (
          <aside
            id="desk-order-ticket"
            ref={ticketRef}
            className="pro-panel execution-ticket desk-order-ticket"
            aria-label={t('Submit order')}
          >
            <div className="section-heading">
              <h2 tabIndex={-1}>{t('Submit order')}</h2>
              <button
                className="icon-button"
                aria-label={text('Close order ticket', '关闭订单面板')}
                onClick={closeTicket}
              >
                <X size={18} />
              </button>
            </div>
            <p className="desk-ticket-context">
              {symbol} · {a?.execution_mode ?? '—'} ·{' '}
              {source === 'example' ? t('Example · synthetic') : t('OKX public')}
            </p>
            {market.data && (
              <div className="ticket-quotes">
                <div>
                  <span>{t('Last price')}</span>
                  <strong>{price(market.data.last, instrument.tick_size)}</strong>
                </div>
                <div>
                  <span>
                    {t('Bid')} / {t('Ask')}
                  </span>
                  <strong>
                    {price(market.data.bid, instrument.tick_size)} /{' '}
                    {price(market.data.ask, instrument.tick_size)}
                  </strong>
                </div>
                {product === 'SWAP' && (
                  <>
                    <div>
                      <span>{t('Mark price')}</span>
                      <strong>{price(market.data.mark, instrument.tick_size)}</strong>
                    </div>
                    <div>
                      <span>{t('Funding rate')}</span>
                      <strong>
                        {market.data.funding_rate == null
                          ? '—'
                          : `${number(Number(market.data.funding_rate) * 100, 4)}%`}
                      </strong>
                    </div>
                    <div>
                      <span>{t('Next funding')}</span>
                      <strong>{date(Number(market.data.next_funding_time))}</strong>
                    </div>
                  </>
                )}
                <small>
                  {date(Number(market.data.ts))} ·{' '}
                  {source === 'example' ? t('Example · synthetic') : 'OKX REST'}
                </small>
              </div>
            )}
            {source === 'okx' && market.data && now - Number(market.data.ts) >= 15000 && (
              <p className="inline-warning dataset-warning">
                {t('Quote is stale. New risk may be rejected.')}
              </p>
            )}
            {market.isPending && <Loading label="Loading market snapshot…" />}
            {!canOperate && (
              <p className="read-only-note">{t('Your role has read-only access.')}</p>
            )}
            <form
              className="compact-form"
              onSubmit={(e) => {
                e.preventDefault();
                preview.mutate(body);
              }}
            >
              <ProductSymbol
                source={source}
                value={symbol}
                onChange={selectSymbol}
                product={product}
                onProductChange={setProduct}
              />
              <div className="side-switch">
                <button
                  type="button"
                  className={side === 'buy' ? 'active buy' : ''}
                  onClick={() => setSide('buy')}
                >
                  {t('Buy')}
                </button>
                <button
                  type="button"
                  className={side === 'sell' ? 'active sell' : ''}
                  onClick={() => setSide('sell')}
                >
                  {t('Sell')}
                </button>
              </div>
              <Field label="Order type">
                <select
                  value={orderType}
                  onChange={(e) => setOrderType(e.target.value as OrderRequest['order_type'])}
                >
                  <option value="market">{t('Market order')}</option>
                  <option value="limit">{t('Limit order')}</option>
                  <option value="stop_market">{t('Stop market order')}</option>
                </select>
              </Field>
              <BudgetSizing
                source={source}
                symbol={symbol}
                product={product}
                side={side}
                orderType={orderType}
                limitPrice={limitPrice}
                market={market.data}
                now={now}
                onApply={setQuantity}
              />
              <Field label="Quantity" hint={product === 'SWAP' ? t('Contracts') : t('Base units')}>
                <input
                  required
                  type="number"
                  min={String(instrument.min_size ?? '0.00000001')}
                  step={String(instrument.lot_size ?? 'any')}
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </Field>
              {orderType === 'limit' && (
                <Field label="Limit price">
                  <input
                    required
                    type="number"
                    min={String(instrument.tick_size ?? '0.00000000000000000001')}
                    step={String(instrument.tick_size ?? 'any')}
                    value={limitPrice}
                    onChange={(e) => setLimitPrice(e.target.value)}
                  />
                </Field>
              )}
              {orderType === 'stop_market' && (
                <Field label="Stop price">
                  <input
                    required
                    type="number"
                    min={String(instrument.tick_size ?? '0.00000000000000000001')}
                    step={String(instrument.tick_size ?? 'any')}
                    value={stopPrice}
                    onChange={(e) => setStopPrice(e.target.value)}
                  />
                </Field>
              )}
              {product === 'SWAP' && (
                <>
                  <Field label="Leverage">
                    <input
                      required
                      type="number"
                      min="1"
                      max={risk.data?.max_leverage ?? 50}
                      value={leverage}
                      onChange={(e) => setLeverage(Number(e.target.value))}
                    />
                  </Field>
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={reduceOnly}
                      onChange={(e) => setReduceOnly(e.target.checked)}
                    />
                    <span>{t('Reduce only')}</span>
                  </label>
                  <div className="ticket-mode">
                    {t('Margin mode')}: {t('Isolated')}
                  </div>
                </>
              )}
              {market.isError && <ErrorBox error={market.error} />}
              <JsonDetails value={market.data} label="Instrument rules" />
              {preview.isError && <ErrorBox error={preview.error} />}
              <button
                className="button button-secondary full-width"
                disabled={!canOperate || !quantity || preview.isPending || riskBlocksOrder}
              >
                {preview.isPending ? <Loader2 size={14} className="spin" /> : <Check size={14} />}{' '}
                {t('Preview order')}
              </button>
            </form>
            {previewMatches && (
              <div className="order-preview">
                <h3>{t('Order preview')}</h3>
                <p className="preview-asof">
                  {t('Market snapshot')}: {date(Number(preview.data.market_snapshot?.ts))}
                </p>
                <RecordGrid
                  value={{
                    estimated_price: preview.data.estimated_price,
                    notional: preview.data.notional,
                    fee: preview.data.fee,
                    required_margin: preview.data.required_margin,
                    estimated_cash_after: preview.data.estimated_cash_after,
                  }}
                />
                {preview.data.warnings?.map((w, i) => (
                  <p className="inline-warning" key={i}>
                    <CircleAlert size={13} />
                    {w}
                  </p>
                ))}
                <button
                  type="button"
                  className="button button-citrus full-width"
                  disabled={!canOperate || order.isPending || riskBlocksOrder}
                  onClick={submit}
                >
                  {order.isPending ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}{' '}
                  {t('Submit order')}
                </button>
              </div>
            )}
            {order.isError && <ErrorBox error={order.error} />}
            <p className="form-footnote pro-form-note">
              {t('Account and execution mode are reported by the server.')}{' '}
              {a?.execution_mode === 'local-paper' &&
                t('Local simulated execution. No real funds are traded.')}
            </p>
          </aside>
        )}
      </div>
      {deployOpen && (
        <div className="modal-backdrop" onClick={closeDeployment}>
          <section
            className="wide-dialog deployment-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('Deploy strategy')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <h2>{t('Deploy strategy')}</h2>
              <button className="icon-button" aria-label={t('Close')} onClick={closeDeployment}>
                <X size={18} />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                deploy.mutate({
                  task: deployReceipt.capture(),
                  body: {
                    source,
                    inst_id: symbol,
                    bar,
                    strategy,
                    direction: product === 'SPOT' ? 'long_only' : direction,
                    leverage: product === 'SPOT' ? 1 : leverage,
                    allocation: strategy.allocation,
                  },
                });
              }}
            >
              <ProductSymbol
                source={source}
                value={symbol}
                onChange={selectSymbol}
                product={product}
                onProductChange={setProduct}
              />
              <div className="form-grid">
                <Field label="Interval">
                  <select value={bar} onChange={(e) => setBar(e.target.value)}>
                    {bars.map((b) => (
                      <option key={b}>{b}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Direction">
                  <select
                    value={direction}
                    disabled={product === 'SPOT'}
                    onChange={(e) => setDirection(e.target.value as Direction)}
                  >
                    <option value="long_only">{t('Long only')}</option>
                    <option value="short_only">{t('Short only')}</option>
                    <option value="long_short">{t('Long / short')}</option>
                  </select>
                </Field>
              </div>
              {product === 'SWAP' && (
                <Field label="Leverage">
                  <input
                    required
                    type="number"
                    min="1"
                    max={risk.data?.max_leverage ?? 50}
                    value={leverage}
                    onChange={(e) => setLeverage(Number(e.target.value))}
                  />
                </Field>
              )}
              <StrategyFields professional value={strategy} onChange={setStrategy} />
              <p className="form-footnote pro-form-note">
                {t(
                  'Start requires a flat market with no pending orders. Close positions and cancel orders first.',
                )}
              </p>
              {deploy.isError && deployReceipt.owns(deploy.variables?.task) && (
                <ErrorBox error={deploy.error} />
              )}
              <button
                className="button button-citrus full-width"
                disabled={
                  !canOperate ||
                  (deploy.isPending && deployReceipt.owns(deploy.variables?.task)) ||
                  risk.data?.halted
                }
              >
                <Play size={14} />
                {t('Start strategy')}
              </button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
export function ExecutionRisk({
  source,
  analytics = false,
}: {
  source: Source;
  analytics?: boolean;
}) {
  const { t } = useI18n();
  const canOperate = canManageRisk(useSession()?.user?.role);
  const qc = useQueryClient();
  const risk = useQuery({ queryKey: ['pro-risk', source], queryFn: () => proApi.risk(source) });
  const [limits, setLimits] = useState({
    max_order_notional: '2500',
    max_gross_exposure_pct: 100,
    max_leverage: 3,
    max_daily_loss_pct: 5,
  });
  const [dirty, setDirty] = useState(false);
  const patchLimits = (patch: Partial<typeof limits>) => {
    setDirty(true);
    setLimits((v) => ({ ...v, ...patch }));
  };
  const [reason, setReason] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (risk.data && !dirty)
      setLimits({
        max_order_notional: risk.data.max_order_notional,
        max_gross_exposure_pct: risk.data.max_gross_exposure_pct,
        max_leverage: risk.data.max_leverage,
        max_daily_loss_pct: risk.data.max_daily_loss_pct,
      });
  }, [risk.data, dirty]);
  const saved = (r: ProRisk) => {
    qc.setQueryData(['pro-risk', source], r);
    void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    setNotice(t('Saved'));
  };
  const save = useMutation({
    mutationFn: proApi.saveRisk,
    onSuccess: (r) => {
      setDirty(false);
      saved(r);
    },
  });
  const halt = useMutation({
    mutationFn: proApi.halt,
    onSuccess: (r) => {
      saved(r);
      setReason('');
    },
  });
  return (
    <>
      <section className="pro-panel execution-risk">
        <div className="section-heading">
          <h2>{t('Risk limits')}</h2>
          {risk.data && (
            <Status type={risk.data.halted ? 'bad' : 'good'}>
              {t(risk.data.halted ? 'Execution halted' : 'Execution enabled')}
            </Status>
          )}
        </div>
        <ActionNote text={notice} />
        {risk.isError ? (
          <ErrorBox error={risk.error} />
        ) : risk.isPending ? (
          <Loading />
        ) : (
          <>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate({ source, ...limits });
              }}
            >
              <div className="risk-input-grid">
                <Field label="Maximum order notional">
                  <input
                    required
                    type="number"
                    min="1"
                    step="0.01"
                    value={limits.max_order_notional}
                    onChange={(e) => patchLimits({ max_order_notional: e.target.value })}
                  />
                </Field>
                <Field label="Maximum gross exposure">
                  <div className="input-suffix">
                    <input
                      required
                      type="number"
                      min="1"
                      max="1000"
                      value={limits.max_gross_exposure_pct}
                      onChange={(e) =>
                        patchLimits({ max_gross_exposure_pct: Number(e.target.value) })
                      }
                    />
                    <span>%</span>
                  </div>
                </Field>
                <Field label="Maximum leverage">
                  <input
                    required
                    type="number"
                    min="1"
                    max="50"
                    value={limits.max_leverage}
                    onChange={(e) => patchLimits({ max_leverage: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Maximum daily loss">
                  <div className="input-suffix">
                    <input
                      required
                      type="number"
                      min="0.1"
                      max="50"
                      step="0.1"
                      value={limits.max_daily_loss_pct}
                      onChange={(e) => patchLimits({ max_daily_loss_pct: Number(e.target.value) })}
                    />
                    <span>%</span>
                  </div>
                </Field>
              </div>
              <button className="button button-secondary" disabled={!canOperate || save.isPending}>
                {t('Save limits')}
              </button>
              {save.isError && <ErrorBox error={save.error} />}
            </form>
            <Suspense fallback={<Loading />}>
              <AccountCapitalPolicy
                source={source}
                onSaved={() => setNotice(t('Capital policy saved'))}
              />
            </Suspense>
            <form
              className="halt-form"
              onSubmit={(e) => {
                e.preventDefault();
                halt.mutate({ source, active: !risk.data?.halted, reason });
              }}
            >
              <Field label="Reason">
                <input
                  required
                  minLength={3}
                  maxLength={300}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </Field>
              <button
                className={`button ${risk.data.halted ? 'button-citrus' : 'button-danger'}`}
                disabled={!canOperate || halt.isPending || reason.trim().length < 3}
              >
                {risk.data.halted ? <Play size={13} /> : <Square size={13} />}{' '}
                {t(risk.data.halted ? 'Resume execution' : 'Halt execution')}
              </button>
              {halt.isError && <ErrorBox error={halt.error} />}
            </form>
          </>
        )}
      </section>
      {analytics && <PortfolioAnalytics source={source} />}
    </>
  );
}
