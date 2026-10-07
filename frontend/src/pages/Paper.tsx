import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  ArrowUpRight,
  Clock3,
  Loader2,
  Play,
  Plus,
  ShieldOff,
  Square,
  Wallet,
  X,
} from 'lucide-react';
import type { FormEvent } from 'react';
import { useRef, useState } from 'react';
import type { Bar, Deployment, Source, Strategy, Ticker } from '../api';
import { api, defaultStrategy } from '../api';
import {
  ActionNote,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Metric,
  PageHeading,
  Status,
  StrategyFields,
  SymbolSelect,
} from '../components/workspace';
import { bars } from '../lib/config';
import { barLabel, date, nameOf, number, price, quantityText, tone } from '../lib/format';
import { useDialogFocus, useNow } from '../lib/hooks';

export default function Paper({
  source,
  symbol,
  setSymbol,
  ticker,
}: {
  source: Source;
  symbol: string;
  setSymbol: (s: string) => void;
  ticker?: Ticker;
}) {
  const qc = useQueryClient();
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [table, setTable] = useState<'positions' | 'orders'>('positions');
  const [notice, setNotice] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<Strategy>({ ...defaultStrategy });
  const [deploymentBar, setDeploymentBar] = useState<Bar>('1H');
  const [showDeploy, setShowDeploy] = useState(false);
  const orderKey = useRef<{ payload: string; key: string } | null>(null);
  const account = useQuery({
    queryKey: ['account', source],
    queryFn: () => api.account(source),
    refetchInterval: 20000,
  });
  const orders = useQuery({
    queryKey: ['orders', source],
    queryFn: () => api.orders(source),
    refetchInterval: 20000,
  });
  const deployments = useQuery({
    queryKey: ['deployments', source],
    queryFn: () => api.deployments(source),
    refetchInterval: 10000,
  });
  const risk = useQuery({ queryKey: ['risk', source], queryFn: () => api.risk(source) });
  const instruments = useQuery({
    queryKey: ['instruments', source],
    queryFn: () => api.instruments(source),
  });
  const invalidate = () => {
    for (const key of ['account', 'orders', 'audit', 'deployments'])
      void qc.invalidateQueries({ queryKey: [key, source] });
  };
  const order = useMutation({
    mutationFn: ({
      data,
      key,
    }: {
      data: { source: Source; inst_id: string; side: 'buy' | 'sell'; quantity: string };
      key: string;
    }) => api.createOrder(data, key),
    onSuccess: (o) => {
      orderKey.current = null;
      setNotice(
        `${o.side === 'buy' ? 'Bought' : 'Sold'} ${o.quantity} ${o.inst_id.split('-')[0]} at ${price(o.price)} USDT. This is a local paper fill.`,
      );
      setQuantity('');
      invalidate();
    },
  });
  const deploy = useMutation({
    mutationFn: api.deploy,
    onSuccess: () => {
      setShowDeploy(false);
      setNotice('Paper strategy deployed. It will evaluate the newest confirmed candle.');
      invalidate();
    },
  });
  const stop = useMutation({
    mutationFn: api.stop,
    onSuccess: () => {
      setNotice('Strategy stopped. Existing positions remain in the paper account.');
      invalidate();
    },
  });
  const submitOrder = (e: FormEvent) => {
    e.preventDefault();
    const data = { source, inst_id: symbol, side, quantity };
    const payload = JSON.stringify(data);
    if (orderKey.current?.payload !== payload)
      orderKey.current = { payload, key: crypto.randomUUID() };
    order.mutate({ data, key: orderKey.current!.key });
  };
  useDialogFocus(showDeploy, '.config-dialog', () => setShowDeploy(false));
  const now = useNow();
  const quoteAge = ticker ? Math.max(0, now - ticker.ts) : null;
  const instrument = instruments.data?.items.find((i) => i.inst_id === symbol);
  const a = account.data;
  const estimate = Number(quantity) * Number(side === 'buy' ? ticker?.ask : ticker?.bid);
  const totalPnl = a ? Number(a.realized_pnl) + Number(a.unrealized_pnl ?? 0) : null;
  return (
    <>
      <PageHeading
        eyebrow="EXECUTION, WITHOUT EXPOSURE"
        title="Paper desk"
        description="A persistent simulated account. Real rules. No real funds."
      >
        <button className="button button-dark" onClick={() => setShowDeploy((v) => !v)}>
          <Plus size={16} />
          {showDeploy ? 'Close setup' : 'Deploy strategy'}
        </button>
      </PageHeading>
      <ActionNote text={notice} />
      {risk.data?.kill_switch && (
        <div className="halt-notice">
          <ShieldOff size={18} />
          <div>
            <strong>Execution is halted.</strong>
            <p>The kill switch blocks all fills. Existing positions have not been liquidated.</p>
          </div>
        </div>
      )}
      {account.isError ? (
        <ErrorBox error={account.error} onRetry={() => void account.refetch()} />
      ) : (
        <div className="paper-metrics">
          <Metric
            label="Account equity"
            value={a ? number(a.equity) : '—'}
            unit="USDT"
            note={a ? `Valuation: ${a.valuation_status}` : 'Loading balances'}
          />
          <Metric
            label="Available cash"
            value={a ? number(a.cash) : '—'}
            unit="USDT"
            note="Unallocated paper capital"
          />
          <Metric
            label="Total P&L"
            value={a?.equity !== null ? number(totalPnl) : '—'}
            unit="USDT"
            className={tone(totalPnl)}
            note={a ? `${number(a.realized_pnl)} realized` : '—'}
          />
          <Metric
            label="Fees paid"
            value={a ? number(a.fees_paid) : '—'}
            unit="USDT"
            note="10 bps per simulated fill"
          />
        </div>
      )}
      <div className="paper-layout">
        <div>
          <section className="positions-panel">
            <div className="detail-tabs-row">
              <div className="underline-tabs">
                <button
                  className={table === 'positions' ? 'active' : ''}
                  onClick={() => setTable('positions')}
                >
                  Positions<span className="tab-count">{a?.positions.length ?? 0}</span>
                </button>
                <button
                  className={table === 'orders' ? 'active' : ''}
                  onClick={() => setTable('orders')}
                >
                  Order history<span className="tab-count">{orders.data?.items.length ?? 0}</span>
                </button>
              </div>
              <span className="subtle-tag">{source.toUpperCase()} ACCOUNT</span>
            </div>
            {table === 'positions' ? (
              account.isPending ? (
                <Loading />
              ) : a?.positions.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>MARKET</th>
                        <th>QUANTITY</th>
                        <th>AVG. COST</th>
                        <th>MARK</th>
                        <th>VALUE (USDT)</th>
                        <th>UNREALIZED P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.positions.map((p) => (
                        <tr key={p.inst_id}>
                          <td>
                            <strong>{p.inst_id}</strong>
                          </td>
                          <td>{quantityText(p.quantity)}</td>
                          <td>{price(p.avg_cost)}</td>
                          <td>{price(p.mark)}</td>
                          <td>{number(p.market_value)}</td>
                          <td className={tone(p.unrealized_pnl)}>{number(p.unrealized_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty title="Room for your first position" icon={Wallet}>
                  Place a paper order or deploy a strategy. Your cash and positions persist across
                  restarts.
                </Empty>
              )
            ) : orders.isPending ? (
              <Loading />
            ) : orders.isError ? (
              <ErrorBox error={orders.error} />
            ) : orders.data.items.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>MARKET / TIME</th>
                      <th>SIDE</th>
                      <th>QUANTITY</th>
                      <th>FILL PRICE</th>
                      <th>FEE (USDT)</th>
                      <th>ORIGIN</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.data.items.map((o) => (
                      <tr key={o.id}>
                        <td>
                          <strong>{o.inst_id}</strong>
                          <small className="table-subline">{date(o.created_at)}</small>
                        </td>
                        <td>
                          <span className={`side-tag ${o.side}`}>{o.side.toUpperCase()}</span>
                        </td>
                        <td>{quantityText(o.quantity)}</td>
                        <td>{price(o.price)}</td>
                        <td>{number(o.fee, 4)}</td>
                        <td>
                          <span className="subtle-tag">{o.origin}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="No paper orders yet">
                Filled manual and strategy orders will appear here with their costs.
              </Empty>
            )}
          </section>
          <section className="deployment-section">
            <div className="section-heading">
              <div>
                <h2>Strategy deployments</h2>
                <p className="section-description">
                  Controlled automation on your local paper account.
                </p>
              </div>
              <span className="subtle-tag">
                {deployments.data?.items.filter((d) => d.status === 'running').length ?? 0} RUNNING
              </span>
            </div>
            {source === 'example' && (
              <p className="inline-warning">
                <Clock3 size={14} />
                Example data is fixed. Each deployment evaluates once, then waits for a new
                confirmed bar.
              </p>
            )}
            {deployments.isPending ? (
              <Loading />
            ) : deployments.isError ? (
              <ErrorBox error={deployments.error} />
            ) : deployments.data.items.length ? (
              <div className="deployment-list">
                {deployments.data.items.map((d) => (
                  <DeploymentRow
                    key={d.id}
                    deployment={d}
                    onStop={() => stop.mutate(d.id)}
                    stopping={stop.isPending}
                  />
                ))}
              </div>
            ) : (
              <div className="recent-empty">
                <Activity size={25} strokeWidth={1.4} />
                <div>
                  <strong>Start with a strategy you understand.</strong>
                  <p>Deploy a tested configuration and inspect each resulting fill.</p>
                </div>
              </div>
            )}
            {stop.isError && <ErrorBox error={stop.error} />}
          </section>
        </div>
        <aside className="order-ticket">
          <div className="inspector-heading">
            <span className="inspector-icon">
              <ArrowUpRight size={20} />
            </span>
            <div>
              <h2>Place a paper order</h2>
              <p>Local simulated market execution.</p>
            </div>
          </div>
          <form className="inspector-body" onSubmit={submitOrder}>
            <div className="side-switch">
              <button
                type="button"
                className={side === 'buy' ? 'active buy' : ''}
                onClick={() => setSide('buy')}
              >
                Buy
              </button>
              <button
                type="button"
                className={side === 'sell' ? 'active sell' : ''}
                onClick={() => setSide('sell')}
              >
                Sell
              </button>
            </div>
            <Field label="Market">
              <SymbolSelect value={symbol} onChange={setSymbol} />
            </Field>
            <div className="ticket-quote">
              <span>{side === 'buy' ? 'Ask' : 'Bid'} snapshot</span>
              <strong>
                {price(side === 'buy' ? ticker?.ask : ticker?.bid)} <small>USDT</small>
              </strong>
            </div>
            {ticker && (
              <p
                className={`quote-age ${source === 'okx' && (quoteAge ?? 0) >= 15000 ? 'stale' : ''}`}
              >
                {source === 'example'
                  ? 'Fixed synthetic quote'
                  : `Exchange quote · ${Math.floor((quoteAge ?? 0) / 1000)}s old`}
              </p>
            )}
            <Field
              label="Quantity"
              hint={
                instrument
                  ? `${symbol.split('-')[0]} · minimum ${instrument.min_size} · increment ${instrument.lot_size}`
                  : symbol.split('-')[0]
              }
            >
              <input
                required
                type="number"
                min={instrument?.min_size ?? '0.00000001'}
                step={instrument?.lot_size ?? 'any'}
                placeholder="0.00"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
            </Field>
            <div className="ticket-summary">
              <div>
                <span>Indicative notional</span>
                <strong>
                  {quantity && Number.isFinite(estimate) ? number(estimate) : '—'} USDT
                </strong>
              </div>
              <div>
                <span>Execution costs</span>
                <span>10 bps fee + 5 bps slippage</span>
              </div>
              <div>
                <span>Account source</span>
                <span>{source === 'example' ? 'Synthetic example' : 'OKX public quotes'}</span>
              </div>
            </div>
            <p className="form-footnote">
              The server fetches a quote on submission. The final fill can differ from this
              snapshot.
            </p>
            {order.isError && <ErrorBox error={order.error} />}
            <button
              className={`button full-width ${side === 'buy' ? 'button-citrus' : 'button-dark'}`}
              type="submit"
              disabled={order.isPending || risk.data?.kill_switch || !quantity}
              aria-label="Submit paper order"
            >
              {order.isPending ? <Loader2 size={15} className="spin" /> : <Plus size={15} />}
              {order.isPending
                ? 'Submitting…'
                : `${side === 'buy' ? 'Buy' : 'Sell'} ${symbol.split('-')[0]} · paper`}
            </button>
            <p className="inspector-disclaimer">
              This is not OKX Demo Trading. No exchange order is sent.
            </p>
          </form>
        </aside>
      </div>
      {showDeploy && (
        <div className="modal-backdrop" onClick={() => setShowDeploy(false)}>
          <section
            className="config-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="deploy-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <div>
                <div className="eyebrow">LOCAL PAPER EXECUTION</div>
                <h2 id="deploy-title">Deploy a strategy</h2>
              </div>
              <button
                className="icon-button"
                onClick={() => setShowDeploy(false)}
                aria-label="Close deployment setup"
              >
                <X size={20} />
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                deploy.mutate({ source, inst_id: symbol, bar: deploymentBar, strategy });
              }}
            >
              <div className="form-grid">
                <Field label="Market">
                  <SymbolSelect value={symbol} onChange={setSymbol} />
                </Field>
                <Field label="Interval">
                  <select
                    value={deploymentBar}
                    onChange={(e) => setDeploymentBar(e.target.value as Bar)}
                  >
                    {bars.map((b) => (
                      <option key={b} value={b}>
                        {barLabel(b)}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <StrategyFields value={strategy} onChange={setStrategy} />
              <p className="form-footnote">
                Evaluates new confirmed bars only. No historical fills are created. The same account
                risk limits apply.
              </p>
              {deploy.isError && <ErrorBox error={deploy.error} />}
              <button
                className="button button-citrus full-width"
                disabled={deploy.isPending || risk.data?.kill_switch}
                aria-label="Start paper strategy"
              >
                {deploy.isPending ? <Loader2 size={16} className="spin" /> : <Play size={15} />}
                Deploy to local paper
              </button>
            </form>
          </section>
        </div>
      )}
    </>
  );
}

function DeploymentRow({
  deployment: d,
  onStop,
  stopping,
}: {
  deployment: Deployment;
  onStop: () => void;
  stopping: boolean;
}) {
  return (
    <div className="deployment-row">
      <span className="run-icon">
        <Activity size={18} />
      </span>
      <div className="deployment-info">
        <strong>{nameOf(d.strategy.kind)}</strong>
        <small>
          {d.inst_id} · {barLabel(d.bar)} · {Math.round(Number(d.strategy.allocation) * 100)}%
          allocation
        </small>
        {d.last_error && <p className="negative">{d.last_error}</p>}
      </div>
      <div className="deployment-state">
        <Status type={d.status === 'running' ? 'good' : 'neutral'}>{d.status}</Status>
        <small>{d.last_bar ? `Last bar ${date(d.last_bar)}` : 'Awaiting first evaluation'}</small>
      </div>
      {d.status === 'running' && (
        <button
          className="button button-small button-secondary"
          onClick={onStop}
          disabled={stopping}
        >
          <Square size={12} />
          Stop
        </button>
      )}
    </div>
  );
}
