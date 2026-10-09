import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ColorType, LineSeries, createChart, type UTCTimestamp } from 'lightweight-charts';
import type { Source } from '../api';
import type { RecordData } from '../proApi';
import { proApi } from '../proApi';
import { date, number } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { DataTable, JsonDetails } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Metric, Field, Status } from './workspace';
import { useSession } from './AuthGate';
import { canTrade } from '../lib/permissions';

export default function ForwardPerformance({ source }: { source: Source }) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const mayFreeze = canTrade(useSession()?.user?.role);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [requested, setRequested] = useState<RecordData>({});
  const [resolved, setResolved] = useState<RecordData>();
  const [snapshotBefore, setSnapshotBefore] = useState<string>();
  const [snapshot, setSnapshot] = useState<RecordData>();
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const freeze = useMutation({
    mutationFn: proApi.freezePerformance,
    onSuccess: (data) => {
      if (data.source === sourceRef.current) setSnapshot(data);
      void snapshots.refetch();
    },
  });
  const verify = useMutation({ mutationFn: proApi.verifyPerformance });
  const ref = useRef<HTMLDivElement>(null);
  const [before, setBefore] = useState<number>();
  const [decisionBefore, setDecisionBefore] = useState<number>();
  useEffect(() => {
    setBefore(undefined);
    setRequested({});
    setResolved(undefined);
    setStart('');
    setEnd('');
    setSnapshotBefore(undefined);
    setDecisionBefore(undefined);
    setSelected('');
    setSnapshot(undefined);
    freeze.reset();
    verify.reset();
  }, [source]);
  const report = useQuery({
    queryKey: ['forward-performance', source, before, resolved ?? requested],
    queryFn: () => proApi.performance(source, before, resolved ?? requested),
    refetchInterval: before || Object.keys(requested).length ? false : 5000,
  });
  useEffect(() => {
    const window = report.data?.window;
    if (
      !resolved &&
      requested.observed_start !== undefined &&
      window &&
      Number(report.data?.summary.observations) > 0
    )
      setResolved({
        window_start: window.range_start_id,
        window_end: window.range_end_id,
        max_gap_ms: window.max_gap_ms,
      });
  }, [report.data, requested, resolved]);
  const snapshots = useQuery({
    queryKey: ['performance-snapshots', source, snapshotBefore],
    queryFn: () => proApi.performanceSnapshots(source, snapshotBefore),
  });
  const openSnapshot = useMutation({
    mutationFn: proApi.performanceSnapshot,
    onSuccess: (data) => {
      if (data.source === sourceRef.current) setSnapshot(data);
      verify.reset();
    },
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
  const targets = (report.data?.acceptance.targets ?? {}) as RecordData;
  const criteria: Record<string, [string, string]> = {
    public_okx_observations: [source + ' · ' + number(summary?.market_updates, 0), 'OKX · ≥ 2'],
    market_clock_progress: [number(summary?.market_wall_ratio, 3), '0.5–2.0'],
    bound_observation_clocks: [
      number(summary?.legacy_clock_observations, 0),
      '0 legacy · ≥ 1 observation',
    ],
    real_wall_duration: [
      number(Number(summary?.wall_elapsed_ms ?? 0) / 86400000, 3) + ' d',
      '≥ ' + number(Number(targets.min_wall_ms ?? 0) / 86400000, 0) + ' d',
    ],
    observation_count: [
      number(summary?.observations, 0),
      '≥ ' + number(targets.min_observations, 0),
    ],
    observation_coverage: [
      number(summary?.wall_coverage_pct, 2) + '%',
      '≥ ' + number(targets.min_coverage_pct, 2) + '%',
    ],
    economic_coverage: [
      number(summary?.economic_wall_coverage_pct, 2) + '%',
      '≥ ' + number(targets.min_economic_coverage_pct, 2) + '%',
    ],
    fresh_final_observation: [
      summary?.last_observed_at == null
        ? '—'
        : number(
            (Number(report.data?.acceptance.as_of) - Number(summary.last_observed_at)) / 1000,
            1,
          ) + ' s',
      '0–' + number(Number(report.data?.window.max_gap_ms ?? 0) / 1000, 0) + ' s',
    ],
    no_pending_funding: [number(summary?.pending_funding_observations, 0), '0'],
    observed_recovery: [
      number(
        Object.values((summary?.recovery_counts ?? {}) as RecordData).reduce<number>(
          (total, count) => total + Number(count),
          0,
        ),
        0,
      ),
      '≥ ' + number(targets.min_recoveries, 0),
    ],
    no_clock_regression: [number(summary?.clock_regressions, 0), '0'],
    no_financial_discontinuity: [number(summary?.financial_discontinuities, 0), '0'],
  };
  const frozenBody = snapshot?.body as RecordData | undefined;
  const frozenSummary = frozenBody?.summary as RecordData | undefined;
  const frozenWindow = frozenBody?.window as RecordData | undefined;
  return (
    <>
      <h3>{t('Account observation window')}</h3>
      <p className="quiet-copy">
        {t(
          'These are shared-account results, including manual activity and every strategy. A selected observation window is not a strategy return.',
        )}
      </p>
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          setBefore(undefined);
          setResolved(undefined);
          setSnapshot(undefined);
          verify.reset();
          setRequested({
            observed_start: Date.parse(start + 'Z'),
            observed_end: Date.parse(end + 'Z'),
          });
        }}
      >
        <Field label="Observed from (UTC)">
          <input
            required
            type="datetime-local"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </Field>
        <Field label="Observed until (UTC, exclusive)">
          <input
            required
            type="datetime-local"
            value={end}
            min={start}
            onChange={(event) => setEnd(event.target.value)}
          />
        </Field>
        <div className="toolbar">
          <button className="button button-secondary">{t('Review fixed account window')}</button>
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setBefore(undefined);
              setRequested({});
              setResolved(undefined);
              setStart('');
              setEnd('');
            }}
          >
            {t('All account observations')}
          </button>
        </div>
      </form>
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
          <section aria-label={t('Observation acceptance')} className="forward-acceptance">
            <div className="section-heading">
              <h3>{t('Observation acceptance')}</h3>
              <Status type={report.data?.acceptance.passed ? 'good' : 'warning'}>
                {t(
                  report.data?.acceptance.passed
                    ? 'Observed targets met'
                    : 'Observation targets not met',
                )}
              </Status>
            </div>
            <p className="quiet-copy">
              {date(Number(summary?.first_observed_at))} → {date(Number(summary?.last_observed_at))}{' '}
              · {t('Actual observation time, not accelerated market time')}
            </p>
            <DataTable
              rows={Object.entries((report.data?.acceptance.checks ?? {}) as RecordData).map(
                ([criterion, passed]) => ({
                  criterion,
                  passed,
                  actual: criteria[criterion]?.[0] ?? '—',
                  target: criteria[criterion]?.[1] ?? '—',
                }),
              )}
              columns={[
                { key: 'criterion', label: 'Criterion', render: (row) => t(String(row.criterion)) },
                { key: 'actual', label: 'Observed value' },
                { key: 'target', label: 'Required value' },
                {
                  key: 'passed',
                  label: 'Result',
                  render: (row) => (
                    <Status type={row.passed ? 'neutral' : 'warning'}>
                      {t(row.passed ? 'Met' : 'Not met')}
                    </Status>
                  ),
                },
              ]}
            />
            <p className="quiet-copy">
              {t(
                'Passing these observations does not establish profitable edge, venue capacity or HTTP availability.',
              )}
            </p>
          </section>
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
            <section aria-label={t('Selected frozen account window')}>
              <h3>{t('Selected frozen account window')}</h3>
              <p className="quiet-copy">
                {t('This saved report is separate from the current observation metrics above.')} ·{' '}
                {String(frozenWindow?.range_start_id ?? '—')}–
                {String(frozenWindow?.range_end_id ?? '—')}
                <br />
                {date(Number(frozenSummary?.first_observed_at))} →{' '}
                {date(Number(frozenSummary?.last_observed_at))}
              </p>
              <div className="pro-metric-strip">
                <Metric
                  label="Observed net P&L"
                  value={number(frozenSummary?.net_pnl)}
                  unit="USDT"
                />
                <Metric
                  label="Window observations"
                  value={number(frozenSummary?.observations, 0)}
                />
                <Metric
                  label="Economic coverage"
                  value={number(frozenSummary?.economic_wall_coverage_pct, 2)}
                  unit="%"
                />
              </div>
              <p className="quiet-copy">
                {text('Frozen evidence', '已冻结证据')} · {String(snapshot.id).slice(0, 12)} ·{' '}
                {date(Number(snapshot.created_at), true)}{' '}
                <JsonDetails value={snapshot} label={text('Frozen report', '冻结报告')} />
              </p>
            </section>
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
      <section className="forward-frozen-history" aria-label={t('Frozen account windows')}>
        <h3>{t('Frozen account windows')}</h3>
        <p className="quiet-copy">
          {t(
            'Saved content hashes are checked when listed. Full recomputation is a separate verification action; later observations do not change a frozen report.',
          )}
        </p>
        {snapshots.isPending ? (
          <Loading />
        ) : snapshots.isError ? (
          <ErrorBox error={snapshots.error} />
        ) : (
          <DataTable
            rows={snapshots.data?.items ?? []}
            empty="No frozen account windows"
            columns={[
              {
                key: 'created_at',
                label: 'Frozen at',
                render: (row) => date(Number(row.created_at)),
              },
              {
                key: 'status',
                label: 'Stored content',
                render: (row) =>
                  t(
                    row.status === 'available'
                      ? 'Hash valid · not recomputed'
                      : 'Evidence unavailable',
                  ),
              },
              {
                key: 'actions',
                label: 'Actions',
                render: (row) => (
                  <button
                    className="text-button"
                    disabled={row.status !== 'available' || openSnapshot.isPending}
                    onClick={() => openSnapshot.mutate(String(row.id))}
                  >
                    {t('Open frozen window')}
                  </button>
                ),
              },
            ]}
          />
        )}
        {snapshots.data?.next_before && (
          <button
            className="text-button"
            onClick={() => setSnapshotBefore(snapshots.data?.next_before)}
          >
            {t('Older frozen windows')}
          </button>
        )}
        {snapshotBefore && (
          <button className="text-button" onClick={() => setSnapshotBefore(undefined)}>
            {t('Latest')}
          </button>
        )}
        {openSnapshot.isError && <ErrorBox error={openSnapshot.error} />}
      </section>
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
