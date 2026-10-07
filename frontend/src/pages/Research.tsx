import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDownToLine,
  ArrowRight,
  Database,
  Layers3,
  Loader2,
  Play,
  RefreshCw,
  Settings2,
} from 'lucide-react';
import type { FormEvent } from 'react';
import { useEffect, useRef, useState } from 'react';
import type { Bar, Run, Source, Strategy } from '../api';
import { api, defaultStrategy, downloadCsv } from '../api';
import Chart from '../Chart';
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
import { barLabel, date, nameOf, number, percent, price, quantityText, tone } from '../lib/format';

export default function Research({
  source,
  symbol,
  setSymbol,
  bar,
  setBar,
  onExample,
}: {
  source: Source;
  symbol: string;
  setSymbol: (s: string) => void;
  bar: Bar;
  setBar: (b: Bar) => void;
  onExample: () => void;
}) {
  const qc = useQueryClient();
  const [strategy, setStrategy] = useState<Strategy>({ ...defaultStrategy });
  const [limit, setLimit] = useState(720);
  const [cash, setCash] = useState('10000');
  const [fee, setFee] = useState('10');
  const [slip, setSlip] = useState('5');
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<'equity' | 'drawdown'>('equity');
  const [resultTab, setResultTab] = useState<'trades' | 'manifest' | 'assumptions'>('trades');
  const [notice, setNotice] = useState<string | null>(null);
  const [exportError, setExportError] = useState(false);
  const runs = useQuery({
    queryKey: ['runs', source],
    queryFn: () => api.runs(source),
    refetchInterval: 10000,
  });
  const activeId = selected ?? runs.data?.items[0]?.id;
  const run = useQuery({
    queryKey: ['run', source, activeId],
    queryFn: () => api.run(activeId!),
    enabled: !!activeId,
    refetchInterval: (q) =>
      q.state.data && ['queued', 'running'].includes(q.state.data.status) ? 1000 : false,
  });
  const candles = useQuery({
    queryKey: ['candles', source, symbol, bar, 240],
    queryFn: () => api.candles(source, symbol, bar, 240),
    enabled: !run.data?.result,
  });
  useEffect(() => {
    if (run.data && ['completed', 'failed'].includes(run.data.status))
      void qc.invalidateQueries({ queryKey: ['runs', source] });
  }, [run.data?.id, run.data?.status, qc, source]);
  const onRun = (r: Run) => {
    setSelected(r.id);
    setNotice(null);
    void qc.invalidateQueries({ queryKey: ['runs', source] });
  };
  const create = useMutation({ mutationFn: api.createRun, onSuccess: onRun });
  const replay = useMutation({ mutationFn: api.replayRun, onSuccess: onRun });
  const hydrated = useRef(false);
  useEffect(() => {
    if (run.data && !hydrated.current) {
      const c = run.data.config;
      setSymbol(c.inst_id);
      setBar(c.bar);
      setStrategy({ ...c.strategy });
      setLimit(c.limit);
      setCash(c.initial_cash);
      setFee(c.fee_bps);
      setSlip(c.slippage_bps);
      hydrated.current = true;
    }
  }, [run.data, setSymbol, setBar]);
  const result = run.data?.result;
  const busy =
    create.isPending ||
    replay.isPending ||
    run.data?.status === 'queued' ||
    run.data?.status === 'running';
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({
      source,
      inst_id: symbol,
      bar,
      limit,
      strategy,
      initial_cash: cash,
      fee_bps: fee,
      slippage_bps: slip,
    });
  };
  const exportRun = async () => {
    if (!activeId) return;
    try {
      await api.exportRun(activeId);
      setNotice('Research bundle downloaded, including the saved dataset and manifest.');
      setExportError(false);
    } catch (e) {
      setNotice((e as Error).message);
      setExportError(true);
    }
  };
  const loadConfig = (r: Run) => {
    setSelected(r.id);
    setSymbol(r.config.inst_id);
    setBar(r.config.bar);
    setStrategy({ ...r.config.strategy });
    setLimit(r.config.limit);
    setCash(r.config.initial_cash);
    setFee(r.config.fee_bps);
    setSlip(r.config.slippage_bps);
  };
  return (
    <>
      <PageHeading
        eyebrow="IDEAS, MEET EVIDENCE"
        title="Strategy research"
        description="A reproducible experiment. Clear assumptions. Every fill accounted for."
      >
        <button
          className="button button-secondary"
          disabled={!result}
          onClick={() => void exportRun()}
          aria-label="Export JSON"
        >
          <ArrowDownToLine size={15} />
          Export JSON
        </button>
      </PageHeading>
      <ActionNote text={notice} error={exportError} />
      <div className="research-layout">
        <div className="research-results">
          <section className="result-canvas">
            <div className="section-heading result-heading">
              <div>
                <div className="eyebrow">{result ? 'BACKTEST RESULT' : 'MARKET CONTEXT'}</div>
                <h2>
                  {result ? nameOf(run.data!.config.strategy.kind) : symbol.replace('-', ' / ')}
                </h2>
                <p className="section-description">
                  {run.data
                    ? `${run.data.config.inst_id} · ${barLabel(run.data.config.bar)} · Run ${run.data.id.slice(0, 8)}`
                    : 'Confirmed candles. Your results appear here after a run.'}
                </p>
              </div>
              {run.data && (
                <Status
                  type={
                    run.data.status === 'completed'
                      ? 'good'
                      : run.data.status === 'failed'
                        ? 'bad'
                        : 'neutral'
                  }
                >
                  {run.data.status}
                </Status>
              )}
            </div>
            {result && (
              <div className="result-metrics">
                <Metric
                  label="Net return"
                  value={percent(result.metrics.total_return_pct)}
                  className={tone(result.metrics.total_return_pct)}
                  note="After fees and slippage"
                />
                <Metric
                  label="Max drawdown"
                  value={percent(-Math.abs(result.metrics.max_drawdown_pct))}
                  className="negative"
                  note="Peak-to-trough decline"
                />
                <Metric
                  label="Sharpe ratio"
                  value={result.metrics.sharpe === null ? '—' : number(result.metrics.sharpe)}
                  note={
                    result.metrics.sharpe === null ? (
                      <span>{result.metrics.sharpe_reason ?? 'Insufficient observations'}</span>
                    ) : (
                      'UTC daily returns · sqrt(365) annualization'
                    )
                  }
                />
                <Metric
                  label="Fills"
                  value={result.metrics.trades}
                  note={`${number(result.metrics.fees_paid)} USDT in fees`}
                />
              </div>
            )}
            <div className="result-chart-toolbar">
              {result ? (
                <>
                  <div className="underline-tabs">
                    <button
                      className={mode === 'equity' ? 'active' : ''}
                      onClick={() => setMode('equity')}
                    >
                      Equity curve
                    </button>
                    <button
                      className={mode === 'drawdown' ? 'active' : ''}
                      onClick={() => setMode('drawdown')}
                    >
                      Drawdown
                    </button>
                  </div>
                  <div className="chart-legend">
                    <span>
                      <i className="legend-swatch strategy" />
                      Strategy
                    </span>
                    {mode === 'equity' && (
                      <span>
                        <i className="legend-swatch ma-slow" />
                        Buy & hold
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <div className="chart-legend">
                    <span>
                      <i className="legend-swatch candles" />
                      Confirmed price
                    </span>
                  </div>
                  <span className="subtle-tag">NO RESULTS YET</span>
                </>
              )}
            </div>
            {run.isError ? (
              <ErrorBox error={run.error} onRetry={() => void run.refetch()} />
            ) : run.data?.status === 'failed' ? (
              <ErrorBox
                error={
                  new Error(
                    run.data.error ?? 'The backtest failed. Adjust your inputs and try again.',
                  )
                }
              />
            ) : busy ? (
              <Loading
                label={
                  run.data?.status === 'running'
                    ? 'Calculating strategy fills and equity…'
                    : 'Preparing a saved dataset for this run…'
                }
              />
            ) : result ? (
              <Chart result={result} mode={mode} height={320} />
            ) : candles.isPending ? (
              <Loading />
            ) : candles.isError ? (
              <ErrorBox
                error={candles.error}
                onRetry={() => void candles.refetch()}
                onExample={source === 'okx' ? onExample : undefined}
              />
            ) : (
              <Chart candles={candles.data?.candles} height={320} />
            )}
            <div className="chart-provenance">
              <span>
                <Database size={12} />
                {result
                  ? 'Saved dataset · reproducible'
                  : 'Market context only · no simulated returns'}
              </span>
              <span>
                {result
                  ? `${date(result.equity[0]?.ts)} — ${date(result.equity.at(-1)?.ts)}`
                  : candles.data
                    ? date(candles.data.fetched_at)
                    : '—'}
              </span>
            </div>
          </section>
          <section className="result-detail">
            <div className="detail-tabs-row">
              <div className="underline-tabs">
                <button
                  className={resultTab === 'trades' ? 'active' : ''}
                  onClick={() => setResultTab('trades')}
                >
                  Fills{result && <span className="tab-count">{result.trades.length}</span>}
                </button>
                <button
                  className={resultTab === 'manifest' ? 'active' : ''}
                  onClick={() => setResultTab('manifest')}
                >
                  Provenance
                </button>
                <button
                  className={resultTab === 'assumptions' ? 'active' : ''}
                  onClick={() => setResultTab('assumptions')}
                >
                  Assumptions
                </button>
              </div>
              {resultTab === 'trades' && (
                <button
                  className="text-button"
                  disabled={!result?.trades.length}
                  onClick={() =>
                    result &&
                    downloadCsv(
                      result.trades.map((t) => ({ ...t, time_utc: new Date(t.ts).toISOString() })),
                      `tidebench-fills-${activeId}.csv`,
                    )
                  }
                >
                  <ArrowDownToLine size={13} />
                  CSV
                </button>
              )}
            </div>
            {!result ? (
              <Empty title="The details matter" icon={Layers3}>
                Your fills, data provenance, and execution assumptions will be saved with each
                completed experiment.
              </Empty>
            ) : resultTab === 'trades' ? (
              result.trades.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>TIME (UTC)</th>
                        <th>SIDE</th>
                        <th>QUANTITY</th>
                        <th>FILL PRICE</th>
                        <th>FEE (USDT)</th>
                        <th>CASH (USDT)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.trades.map((t, i) => (
                        <tr key={`${t.ts}-${i}`}>
                          <td>{date(t.ts).replace(' UTC', '')}</td>
                          <td>
                            <span className={`side-tag ${t.side}`}>{t.side.toUpperCase()}</span>
                          </td>
                          <td>{quantityText(t.quantity)}</td>
                          <td>{price(t.price)}</td>
                          <td>{number(t.fee, 4)}</td>
                          <td>{number(t.cash)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty title="No fills in this period">
                  The strategy did not trigger an executable position change. Try a wider dataset or
                  different parameters.
                </Empty>
              )
            ) : (
              <div className="data-inspector">
                <p>
                  {resultTab === 'manifest'
                    ? 'The saved run manifest identifies exactly what was tested. Export the bundle to replay the input dataset.'
                    : 'Signals use a confirmed close. Execution begins at the next bar open, with explicit costs. These are local simulation assumptions.'}
                </p>
                <pre>
                  {JSON.stringify(
                    resultTab === 'manifest' ? run.data?.manifest : result.assumptions,
                    null,
                    2,
                  )}
                </pre>
                {resultTab === 'assumptions' && (
                  <>
                    <h3>Data quality</h3>
                    <pre>{JSON.stringify(result.quality, null, 2)}</pre>
                  </>
                )}
              </div>
            )}
          </section>
        </div>
        <aside className="research-inspector">
          <form onSubmit={submit}>
            <div className="inspector-heading">
              <span className="inspector-icon">
                <Settings2 size={18} />
              </span>
              <div>
                <h2>Experiment setup</h2>
                <p>Make your assumptions explicit.</p>
              </div>
            </div>
            <div className="inspector-body">
              <div className="form-section-label">
                01 <span>MARKET & DATA</span>
              </div>
              <Field label="Market">
                <SymbolSelect value={symbol} onChange={setSymbol} />
              </Field>
              <div className="form-grid">
                <Field label="Interval">
                  <select value={bar} onChange={(e) => setBar(e.target.value as Bar)}>
                    {bars.map((b) => (
                      <option key={b} value={b}>
                        {barLabel(b)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Candle count">
                  <input
                    type="number"
                    min="60"
                    max="2000"
                    required
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value))}
                  />
                </Field>
              </div>
              <div className="form-section-label">
                02 <span>STRATEGY</span>
              </div>
              <StrategyFields value={strategy} onChange={setStrategy} />
              <div className="form-section-label">
                03 <span>EXECUTION MODEL</span>
              </div>
              <Field label="Initial capital">
                <div className="input-suffix">
                  <input
                    type="number"
                    min="1"
                    step="0.01"
                    required
                    value={cash}
                    onChange={(e) => setCash(e.target.value)}
                  />
                  <span>USDT</span>
                </div>
              </Field>
              <div className="form-grid">
                <Field label="Fee">
                  <div className="input-suffix">
                    <input
                      type="number"
                      min="0"
                      max="1000"
                      step="0.1"
                      required
                      value={fee}
                      onChange={(e) => setFee(e.target.value)}
                    />
                    <span>bps</span>
                  </div>
                </Field>
                <Field label="Slippage">
                  <div className="input-suffix">
                    <input
                      type="number"
                      min="0"
                      max="1000"
                      step="0.1"
                      required
                      value={slip}
                      onChange={(e) => setSlip(e.target.value)}
                    />
                    <span>bps</span>
                  </div>
                </Field>
              </div>
              <p className="form-footnote">
                1 basis point = 0.01%. Costs apply to every simulated fill.
              </p>
              {create.isError && <ErrorBox error={create.error} />}
              <button
                type="submit"
                className="button button-citrus full-width"
                disabled={busy}
                aria-label="Run backtest"
              >
                {busy ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <Play size={15} fill="currentColor" />
                )}
                {busy ? 'Running experiment…' : 'Run backtest'}
                {!busy && <ArrowRight size={16} />}
              </button>
              <p className="inspector-disclaimer">
                Research simulation. Historical results do not predict future performance.
              </p>
            </div>
          </form>
          <div className="run-history">
            <div className="section-heading">
              <h3>Run history</h3>
              <span>{runs.data?.items.length ?? 0}</span>
            </div>
            {runs.isError ? (
              <ErrorBox error={runs.error} />
            ) : !runs.data?.items.length ? (
              <p className="quiet-copy">Your completed and failed experiments will appear here.</p>
            ) : (
              runs.data.items.slice(0, 10).map((r) => (
                <button
                  key={r.id}
                  className={`history-row ${activeId === r.id ? 'active' : ''}`}
                  onClick={() => loadConfig(r)}
                >
                  <div>
                    <strong>
                      {r.config.inst_id} <span>{barLabel(r.config.bar)}</span>
                    </strong>
                    <small>{date(r.created_at)}</small>
                  </div>
                  <span className={tone(r.result?.metrics.total_return_pct)}>
                    {r.result ? percent(r.result.metrics.total_return_pct) : r.status}
                  </span>
                </button>
              ))
            )}
            {result && (
              <button
                className="text-button replay-button"
                disabled={busy}
                onClick={() => activeId && replay.mutate(activeId)}
                aria-label="Replay snapshot"
              >
                <RefreshCw size={13} />
                Replay snapshot
              </button>
            )}
            {replay.isError && <ErrorBox error={replay.error} />}
          </div>
        </aside>
      </div>
    </>
  );
}
