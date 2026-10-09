import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ColorType, LineSeries, createChart, type UTCTimestamp } from 'lightweight-charts';
import type { Source } from '../api';
import type { RecordData } from '../proApi';
import { proApi } from '../proApi';
import { date, number } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { DataTable, JsonDetails } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Metric } from './workspace';
import { useSession } from './AuthGate';
import { canTrade } from '../lib/permissions';

export default function ForwardPerformance({ source }: { source: Source }) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const mayFreeze = canTrade(useSession()?.user?.role);
  const [snapshot, setSnapshot] = useState<RecordData>();
  const freeze = useMutation({
    mutationFn: proApi.freezePerformance,
    onSuccess: (data) => setSnapshot(data),
  });
  const verify = useMutation({ mutationFn: proApi.verifyPerformance });
  const ref = useRef<HTMLDivElement>(null);
  const [before, setBefore] = useState<number>();
  const [decisionBefore, setDecisionBefore] = useState<number>();
  useEffect(() => {
    setBefore(undefined);
    setDecisionBefore(undefined);
    setSelected('');
    setSnapshot(undefined);
    freeze.reset();
    verify.reset();
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
      const time = Math.floor((item.observed_at || item.market_ts) / 1000) as UTCTimestamp;
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
        {text(
          'Statistics cover the complete observation window. The chart and detail table display the current page of up to 500 records; paging does not change the measured window. Missing prices or unsettled funding remain gaps.',
          '统计覆盖完整观察窗口。图表和明细每页最多展示 500 条，翻页不会改变统计范围。缺失价格与未结资金费率保留为缺口。',
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
          <div className="pro-metric-strip">
            <Metric
              label={text('Window observations', '窗口观察数')}
              value={number(summary?.observations, 0)}
            />
            <Metric
              label={text('Actual elapsed time', '真实经过时间')}
              value={number(Number(summary?.wall_elapsed_ms ?? 0) / 3600000, 2)}
              unit="h"
            />
            <Metric
              label={text('Observed coverage', '观察覆盖率')}
              value={number(summary?.wall_coverage_pct, 2)}
              unit="%"
            />
            <Metric
              label={text('Economic coverage', '经济状态完整覆盖率')}
              value={number(summary?.economic_wall_coverage_pct, 2)}
              unit="%"
            />
          </div>
          <div className="toolbar">
            {mayFreeze && (
              <button
                className="text-button"
                disabled={freeze.isPending || !Number(summary?.observations)}
                onClick={() => {
                  verify.reset();
                  freeze.mutate({
                    source,
                    window_start: report.data?.window.range_start_id,
                    window_end: report.data?.window.range_end_id,
                    max_gap_ms: report.data?.window.max_gap_ms,
                  });
                }}
              >
                {freeze.isPending
                  ? text('Freezing…', '正在冻结…')
                  : text('Freeze window evidence', '冻结窗口证据')}
              </button>
            )}
            <JsonDetails
              value={{ window: report.data?.window, summary, acceptance: report.data?.acceptance }}
              label={text('Coverage and measurement rules', '覆盖情况与统计规则')}
            />
            {snapshot && (
              <button
                className="text-button"
                disabled={verify.isPending}
                onClick={() => verify.mutate(String(snapshot.id))}
              >
                {text('Verify frozen window', '验证冻结窗口')}
              </button>
            )}
          </div>
          {freeze.isError && <ErrorBox error={freeze.error} />}
          {verify.isError && <ErrorBox error={verify.error} />}
          {snapshot && (
            <p className="quiet-copy">
              {text('Frozen evidence', '已冻结证据')} · {String(snapshot.id).slice(0, 12)} ·{' '}
              {date(Number(snapshot.created_at), true)}{' '}
              <JsonDetails value={snapshot} label={text('Frozen report', '冻结报告')} />
            </p>
          )}
          {verify.data && (
            <p className={verify.data.verified ? 'quiet-copy' : 'warning-banner'}>
              {verify.data.verified
                ? text(
                    'Recorded observation window and hashes match the frozen evidence.',
                    '观察窗口与哈希均匹配冻结证据。',
                  )
                : text(
                    'The recorded observation window differs from the frozen evidence.',
                    '当前观察窗口与冻结证据不一致。',
                  )}{' '}
              <JsonDetails value={verify.data} label={text('Verification result', '验证结果')} />
            </p>
          )}
          {summary?.external_flow_periods != null && Number(summary.external_flow_periods) > 0 && (
            <p className="quiet-copy">
              {text(
                'Cash flows without a flow-time valuation leave time-weighted return unreported. The boundary-adjusted estimate is available in the evidence.',
                '资金流发生时没有对应估值，时间加权收益暂不报告。边界调整估计值保留在证据中。',
              )}
            </p>
          )}
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
