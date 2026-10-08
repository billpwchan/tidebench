import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ColorType, LineSeries, createChart, type UTCTimestamp } from 'lightweight-charts';
import type { Source } from '../api';
import type { RecordData } from '../proApi';
import { proApi } from '../proApi';
import { date, number } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { DataTable, JsonDetails } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Metric } from './workspace';

export default function ForwardPerformance({ source }: { source: Source }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const [before, setBefore] = useState<number>();
  const [decisionBefore, setDecisionBefore] = useState<number>();
  useEffect(() => {
    setBefore(undefined);
    setDecisionBefore(undefined);
    setSelected('');
  }, [source]);
  const report = useQuery({
    queryKey: ['forward-performance', source, before],
    queryFn: () => proApi.performance(source, before),
    refetchInterval: before ? false : 5000,
  });
  const deployments = useQuery({
    queryKey: ['pro-deployments', source],
    queryFn: () => proApi.deployments(source),
  });
  const [selected, setSelected] = useState('');
  const decisions = useQuery({
    queryKey: ['forward-decisions', selected, decisionBefore],
    queryFn: () => proApi.decisions(selected, decisionBefore),
    enabled: !!selected,
    refetchInterval: decisionBefore ? false : 5000,
  });
  const items = report.data?.items;
  useEffect(() => {
    if (!ref.current || !items?.length) return;
    const chart = createChart(ref.current, {
      height: 280,
      layout: {
        background: { type: ColorType.Solid, color: '#fff' },
        textColor: '#73847b',
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#edf1ef' } },
      timeScale: { timeVisible: true, borderVisible: false },
      rightPriceScale: { borderVisible: false },
    });
    const points = new Map<number, { time: UTCTimestamp; value?: number }>();
    for (const item of items) {
      const time = Math.floor(item.market_ts / 1000) as UTCTimestamp;
      const equity = item.body.equity;
      points.set(
        Number(time),
        equity !== null && ['fresh', 'example'].includes(String(item.body.valuation_status))
          ? { time, value: Number(equity) }
          : { time },
      );
    }
    chart
      .addSeries(LineSeries, { color: '#345f4b', lineWidth: 2, priceLineVisible: false })
      .setData([...points.values()].sort((a, b) => Number(a.time) - Number(b.time)));
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) =>
      chart.applyOptions({ width: entries[0].contentRect.width }),
    );
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [items]);
  const summary = report.data?.summary;
  return (
    <>
      <p className="quiet-copy">
        {t(
          'Actual observations from this workspace. Statistics cover the selected page of up to 500 observations. Missing valuations remain gaps; detail records preserve exact decimals.',
        )}
      </p>
      {report.isPending ? (
        <Loading />
      ) : report.isError ? (
        <ErrorBox error={report.error} />
      ) : (
        <>
          <div className="pro-metric-strip">
            <Metric label="Observed net P&L" value={number(summary?.net_pnl)} unit="USDT" />
            <Metric
              label="Time-weighted return"
              value={summary?.return == null ? '—' : number(Number(summary.return) * 100)}
              unit="%"
            />
            <Metric
              label="Observed drawdown"
              value={
                summary?.max_drawdown == null ? '—' : number(Number(summary.max_drawdown) * 100)
              }
              unit="%"
            />
            <Metric label="Fees in window" value={number(summary?.fees_paid_change)} unit="USDT" />
            <Metric
              label="Funding in window"
              value={number(summary?.funding_paid_change)}
              unit="USDT"
            />
          </div>
          {items?.length ? (
            <div ref={ref} role="img" aria-label={t('Observed account equity')} />
          ) : (
            <Empty title="No forward observations">
              {t('Start a simulation or submit a paper order to begin recording observed equity.')}
            </Empty>
          )}
          <details>
            <summary>{t('Equity observations')}</summary>
            <DataTable
              rows={items ?? []}
              columns={[
                { key: 'market_ts', label: 'Market time', render: (r) => date(r.market_ts) },
                { key: 'equity', label: 'Account equity', render: (r) => number(r.body.equity) },
                {
                  key: 'quality',
                  label: 'Valuation',
                  render: (r) => String(r.body.valuation_status),
                },
                { key: 'detail', label: 'Evidence', render: (r) => <JsonDetails value={r} /> },
              ]}
            />
          </details>
          <div className="toolbar">
            <button className="text-button" disabled={!before} onClick={() => setBefore(undefined)}>
              {t('Latest observations')}
            </button>
            <button
              className="text-button"
              disabled={!report.data?.next_before || report.isFetching}
              onClick={() => setBefore(report.data?.next_before)}
            >
              {t('Older observations')}
            </button>
          </div>
        </>
      )}
      <div className="section-heading forward-decisions-heading">
        <h3>{t('Decision journal')}</h3>
        <select
          aria-label={t('Deployment decisions')}
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value);
            setDecisionBefore(undefined);
          }}
        >
          <option value="">{t('Choose a deployment')}</option>
          {deployments.data?.items.map((d) => (
            <option key={String(d.id)} value={String(d.id)}>
              {String(d.inst_id)} · {String(d.id).slice(0, 8)}
            </option>
          ))}
        </select>
      </div>
      {decisions.isError && <ErrorBox error={decisions.error} />}
      {selected &&
        (decisions.isPending ? (
          <Loading />
        ) : (
          <>
            <DataTable
              rows={decisions.data?.items ?? []}
              empty="No recorded decisions"
              columns={[
                { key: 'bar', label: 'Closed bar', render: (r) => date(Number(r.available_at)) },
                { key: 'signal', label: 'Signal' },
                { key: 'target', label: 'Target' },
                {
                  key: 'reason',
                  label: 'Decision reason',
                  render: (r) =>
                    String(
                      (r.indicators as RecordData | undefined)?.exit_reason ??
                        (r.indicators as RecordData | undefined)?.tag ??
                        'strategy_signal',
                    ),
                },
                {
                  key: 'intent',
                  label: 'Execution state',
                  render: (r) => String((r.intent as RecordData | undefined)?.status ?? '—'),
                },
                {
                  key: 'orders',
                  label: 'Orders',
                  render: (r) => (Array.isArray(r.orders) ? r.orders.length : 0),
                },
                { key: 'new_bars', label: 'New bars' },
                {
                  key: 'content_hash',
                  label: 'Evidence',
                  render: (r) => <JsonDetails value={r} />,
                },
              ]}
            />
            <div className="toolbar">
              <button
                className="text-button"
                disabled={!decisionBefore}
                onClick={() => setDecisionBefore(undefined)}
              >
                {t('Latest decisions')}
              </button>
              <button
                className="text-button"
                disabled={decisions.data?.items.length !== 100 || decisions.isFetching}
                onClick={() => {
                  const oldest = decisions.data?.items.at(-1);
                  if (oldest) setDecisionBefore(Number(oldest.bar));
                }}
              >
                {t('Older decisions')}
              </button>
            </div>
          </>
        ))}
    </>
  );
}
