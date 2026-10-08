import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Check,
  CircleAlert,
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
import ForwardPerformance from '../components/ForwardPerformance';
import ManagedPortfolios from '../components/ManagedPortfolios';
import Contributions from '../components/Contributions';
import SimulationClock from '../components/SimulationClock';
import ReleaseHistory from '../components/ReleaseHistory';
import PortfolioAnalytics from '../components/PortfolioAnalytics';
import { proApi } from '../proApi';
import type { Direction, OrderRequest, ProRisk, RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { canTrade, canManageRisk } from '../lib/permissions';
import { bars } from '../lib/config';
import { date, number, price, quantityText, tone } from '../lib/format';
import { useDialogFocus, useNow } from '../lib/hooks';
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
  WorkspaceTabs,
  valueText,
} from '../components/ProWorkspace';

export default function Portfolio({
  source,
  initialView = 'positions',
}: {
  source: Source;
  initialView?: string;
}) {
  const { t } = useI18n();
  const canOperate = canTrade(useSession()?.user?.role);
  const qc = useQueryClient();
  const now = useNow();
  const [table, setTable] = useState(initialView);
  useEffect(() => setTable(initialView), [initialView]);
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [leverage, setLeverage] = useState(1);
  const [reduceOnly, setReduceOnly] = useState(false);
  const [orderType, setOrderType] = useState<OrderRequest['order_type']>('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [strategy, setStrategy] = useState<Strategy>({ ...defaultStrategy });
  const [direction, setDirection] = useState<Direction>('long_only');
  const [bar, setBar] = useState('1H');
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
  const stop = useMutation({ mutationFn: proApi.stop, onSuccess: refresh });
  const deploy = useMutation({
    mutationFn: proApi.deploy,
    onSuccess: () => {
      setDeployOpen(false);
      setNotice(t('Strategy deployed'));
      refresh();
    },
  });
  const submit = () => {
    if (!previewMatches) return;
    if (idempotency.current?.payload !== payload)
      idempotency.current = { payload, key: crypto.randomUUID() };
    order.mutate({ data: body, key: idempotency.current.key });
  };
  useDialogFocus(deployOpen, '.deployment-dialog', () => setDeployOpen(false));
  const a = account.data;
  const instrument = (market.data?.instrument ?? {}) as RecordData;
  return (
    <>
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
          onClick={() => setDeployOpen(true)}
        >
          <Plus size={14} />
          {t('Deploy strategy')}
        </button>
      </PageHeading>
      {source === 'example' && <SimulationClock />}
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
        <div className="pro-metric-strip account-metrics">
          <Metric
            label="Account equity"
            value={number(a?.equity)}
            unit="USDT"
            note={a?.valuation_status}
          />
          <Metric label="Available cash" value={number(a?.available_cash)} unit="USDT" />
          <Metric label="Used margin" value={number(a?.used_margin)} unit="USDT" />
          <Metric label="Maintenance margin" value={number(a?.maintenance_margin)} unit="USDT" />
          <Metric
            label="Unrealized P&L"
            value={number(a?.unrealized_pnl)}
            className={tone(a?.unrealized_pnl)}
          />
          <Metric label="Funding paid" value={number(a?.funding_paid)} unit="USDT" />
        </div>
      )}
      <div
        className={`execution-layout${['managed', 'contributions'].includes(table) ? ' execution-evidence-layout' : ''}`}
      >
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
            <WorkspaceTabs
              value={table}
              onChange={setTable}
              items={[
                { key: 'positions', label: 'Positions' },
                { key: 'performance', label: 'Forward performance' },
                { key: 'contributions', label: 'Contribution' },
                { key: 'managed', label: 'Managed portfolios' },
                { key: 'analytics', label: 'Exposure & scenarios' },
                { key: 'orders', label: 'Orders' },
                { key: 'ledger', label: 'Ledger' },
                { key: 'strategies', label: 'Strategies' },
                { key: 'releases', label: 'Paper releases' },
              ]}
            />
            {table === 'performance' && <ForwardPerformance source={source} />}
            {table === 'managed' && <ManagedPortfolios source={source} />}
            {table === 'contributions' && <Contributions source={source} />}
            {table === 'releases' && <ReleaseHistory source={source} />}
            {table === 'analytics' && <PortfolioAnalytics source={source} />}
            {table === 'positions' &&
              (account.isPending ? (
                <Loading />
              ) : account.isError ? (
                <ErrorBox error={account.error} />
              ) : (
                <DataTable
                  rows={a?.positions ?? []}
                  empty="No positions"
                  columns={[
                    {
                      key: 'inst_id',
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
                      label: 'Entry price',
                      render: (p) => price(p.entry_price),
                    },
                    {
                      key: 'mark',
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
                      label: 'Maintenance margin',
                      render: (p) => number(p.maintenance_margin),
                    },
                    {
                      key: 'liquidation_price',
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
                      label: 'Leverage',
                      render: (p) => (p.inst_type === 'SWAP' ? `${p.leverage}×` : '—'),
                    },
                    {
                      key: 'unrealized_pnl',
                      label: 'Unrealized P&L',
                      render: (p) => (
                        <span className={tone(p.unrealized_pnl)}>{number(p.unrealized_pnl)}</span>
                      ),
                    },
                  ]}
                />
              ))}{' '}
            {table === 'orders' &&
              (orders.isPending ? (
                <Loading />
              ) : orders.isError ? (
                <ErrorBox error={orders.error} />
              ) : (
                <DataTable
                  rows={orders.data?.items ?? []}
                  empty="No orders"
                  columns={[
                    {
                      key: 'inst_id',
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
                    { key: 'quantity', label: 'Quantity', render: (o) => quantityText(o.quantity) },
                    {
                      key: 'limit_price',
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
                      label: 'Fill price',
                      render: (o) => (o.status === 'filled' ? price(o.price) : '—'),
                    },
                    {
                      key: 'fee',
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
                <DataTable
                  rows={deployments.data?.items ?? []}
                  empty="No strategy deployments"
                  columns={[
                    {
                      key: 'inst_id',
                      label: 'Market',
                      render: (d) => (
                        <div className="table-stacked">
                          <strong>{d.inst_id}</strong>
                          <small>
                            {d.bar} · {d.direction} · {d.leverage}×
                          </small>
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
                    { key: 'last_bar', label: 'Last evaluation', render: (d) => date(d.last_bar) },
                    {
                      key: 'last_error',
                      label: 'Last error',
                      render: (d) => <span className="negative">{d.last_error ?? '—'}</span>,
                    },
                    {
                      key: 'actions',
                      label: 'Actions',
                      render: (d) =>
                        d.status === 'running' ? (
                          <button
                            className="text-button"
                            disabled={!canOperate || stop.isPending}
                            onClick={() => stop.mutate(d.id)}
                          >
                            <Square size={11} />
                            {t('Stop')}
                          </button>
                        ) : null,
                    },
                  ]}
                />
              ))}
            {cancel.isError && <ErrorBox error={cancel.error} />}
            {stop.isError && <ErrorBox error={stop.error} />}
          </section>
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
          <ExecutionRisk source={source} />
        </div>
        {!['managed', 'contributions'].includes(table) && (
          <aside className="pro-panel execution-ticket">
            <div className="section-heading">
              <h2>{t('Submit order')}</h2>
              <Activity size={17} />
            </div>
            {market.data && (
              <div className="ticket-quotes">
                <div>
                  <span>{t('Last price')}</span>
                  <strong>{price(market.data.last)}</strong>
                </div>
                <div>
                  <span>
                    {t('Bid')} / {t('Ask')}
                  </span>
                  <strong>
                    {price(market.data.bid)} / {price(market.data.ask)}
                  </strong>
                </div>
                {product === 'SWAP' && (
                  <>
                    <div>
                      <span>{t('Mark price')}</span>
                      <strong>{price(market.data.mark)}</strong>
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
                onChange={setSymbol}
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
                    min="0.00000001"
                    step="any"
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
                    min="0.00000001"
                    step="any"
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
        <div className="modal-backdrop" onClick={() => setDeployOpen(false)}>
          <section
            className="wide-dialog deployment-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('Deploy strategy')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <h2>{t('Deploy strategy')}</h2>
              <button
                className="icon-button"
                aria-label={t('Close')}
                onClick={() => setDeployOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                deploy.mutate({
                  source,
                  inst_id: symbol,
                  bar,
                  strategy,
                  direction: product === 'SPOT' ? 'long_only' : direction,
                  leverage: product === 'SPOT' ? 1 : leverage,
                  allocation: strategy.allocation,
                });
              }}
            >
              <ProductSymbol
                source={source}
                value={symbol}
                onChange={setSymbol}
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
              {deploy.isError && <ErrorBox error={deploy.error} />}
              <button
                className="button button-citrus full-width"
                disabled={!canOperate || deploy.isPending || risk.data?.halted}
              >
                <Play size={14} />
                {t('Start strategy')}
              </button>
            </form>
          </section>
        </div>
      )}
    </>
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
