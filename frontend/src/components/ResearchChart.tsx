import { useEffect, useRef } from 'react';
import { ColorType, CrosshairMode, LineSeries, createChart } from 'lightweight-charts';
import type { UTCTimestamp } from 'lightweight-charts';
import type { RecordData } from '../proApi';
import { Empty } from './workspace';
import { useI18n } from '../lib/i18n';
export default function ResearchChart({
  rows,
  metric = 'equity',
}: {
  rows: RecordData[];
  metric?: 'equity' | 'drawdown';
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const observations = new Map<number, { time: UTCTimestamp; value: number; benchmark: unknown }>();
  for (const row of rows) {
    const ts = Number(row.ts ?? row.timestamp);
    const raw = metric === 'drawdown' ? row.drawdown_pct : row.equity;
    const value = Number(raw);
    if (Number.isFinite(ts) && Number.isFinite(value) && raw !== null && raw !== undefined)
      observations.set(Math.floor(ts / 1000), {
        time: Math.floor(ts / 1000) as UTCTimestamp,
        value: metric === 'drawdown' ? -Math.abs(value) : value,
        benchmark: row.benchmark,
      });
  }
  const data = [...observations.values()].sort((a, b) => Number(a.time) - Number(b.time));
  useEffect(() => {
    if (!ref.current || !data.length) return;
    const chart = createChart(ref.current, {
      height: 300,
      layout: {
        background: { type: ColorType.Solid, color: '#fff' },
        textColor: '#73847b',
        fontSize: 11,
        attributionLogo: true,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#edf1ef' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chart
      .addSeries(LineSeries, {
        color: metric === 'drawdown' ? '#b76155' : '#345f4b',
        lineWidth: 2,
        priceLineVisible: false,
        ...(metric === 'drawdown'
          ? { priceFormat: { type: 'percent' as const, precision: 2, minMove: 0.01 } }
          : {}),
      })
      .setData(data.map(({ time, value }) => ({ time, value })));
    if (metric === 'equity') {
      const benchmark = data.filter(
        (r) =>
          r.benchmark !== undefined && r.benchmark !== null && Number.isFinite(Number(r.benchmark)),
      );
      if (benchmark.length)
        chart
          .addSeries(LineSeries, {
            color: '#94a4af',
            lineWidth: 1,
            lineStyle: 2,
            priceLineVisible: false,
            lastValueVisible: false,
          })
          .setData(benchmark.map((r) => ({ time: r.time, value: Number(r.benchmark) })));
    }
    chart.timeScale().fitContent();
    const ro = new ResizeObserver((entries) => {
      if (entries[0]?.contentRect.width)
        chart.applyOptions({ width: entries[0].contentRect.width });
    });
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [rows, metric]);
  return data.length ? (
    <div className="research-chart-wrap">
      <div className="research-chart-legend">
        <span>
          <i className={metric === 'drawdown' ? 'legend-drawdown' : 'legend-strategy'} />
          {t(metric === 'drawdown' ? 'Strategy drawdown' : 'Strategy equity')}
        </span>
        {metric === 'equity' && data.some((r) => r.benchmark != null) && (
          <span
            title={t(
              'Cost-adjusted unlevered underlying buy & hold. Perpetual funding is excluded.',
            )}
          >
            <i className="legend-benchmark" />
            {t('Unlevered buy & hold')}
          </span>
        )}
      </div>
      <div
        ref={ref}
        className="financial-chart pro-equity-chart"
        role="img"
        aria-label={t(
          metric === 'drawdown'
            ? 'Research drawdown curve'
            : 'Research equity and benchmark curves',
        )}
      />
    </div>
  ) : (
    <Empty title="No equity observations">
      No server-calculated equity series is available for this result.
    </Empty>
  );
}
