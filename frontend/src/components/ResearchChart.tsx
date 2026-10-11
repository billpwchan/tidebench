import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
} from 'lightweight-charts';
import type {
  IChartApi,
  ISeriesApi,
  LogicalRange,
  MouseEventParams,
  SeriesType,
  UTCTimestamp,
} from 'lightweight-charts';
import type { RecordData } from '../proApi';
import { date, number, percent } from '../lib/format';
import { Empty } from './workspace';
import { useI18n } from '../lib/i18n';
import './ResearchChart.css';
import { chartPalette } from '../lib/chartPalette';
import type { ReactNode } from 'react';

type ChartMetric = 'equity' | 'drawdown' | 'returns';
type RangePreset = 'all' | '1m' | '3m' | '1y';
type Observation = {
  time: UTCTimestamp;
  equity: number | null;
  benchmark: number | null;
  drawdown: number | null;
  initial: boolean;
};
type ChartPoint = Observation & { value: number | null; benchmarkValue: number | null };
type ChartControls = {
  range: (preset: RangePreset) => void;
  inspect: (index: number) => void;
};

const finite = (raw: unknown): number | null => {
  if (raw === null || raw === undefined || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
};
const sameRange = (a: LogicalRange | null, b: LogicalRange | null) =>
  !!a && !!b && Math.abs(a.from - b.from) < 0.000001 && Math.abs(a.to - b.to) < 0.000001;

export default function ResearchChart({
  rows,
  metric = 'equity',
  initialCash,
  startTs,
  height = 300,
  linkedDrawdown = false,
  title,
  metricControls,
}: {
  rows: RecordData[];
  metric?: ChartMetric;
  initialCash?: unknown;
  startTs?: number;
  height?: number;
  linkedDrawdown?: boolean;
  title?: string;
  metricControls?: ReactNode;
}) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const mainRef = useRef<HTMLDivElement>(null);
  const drawdownRef = useRef<HTMLDivElement>(null);
  const controls = useRef<ChartControls | null>(null);
  const inspectorRef = useRef<HTMLInputElement>(null);
  const inspection = useRef({ keyboard: false, index: null as number | null });
  const savedRange = useRef<LogicalRange | null>(null);
  const savedDomain = useRef('');
  const presetRef = useRef<RangePreset | null>('all');
  const [preset, setPreset] = useState<RangePreset | null>('all');
  const [pointIndex, setPointIndex] = useState<number | null>(null);
  const pointId = useId();
  const summaryId = useId();
  const parsedCash = finite(initialCash);
  const baseline = parsedCash !== null && parsedCash > 0 ? parsedCash : null;
  const displayMetric = metric === 'returns' && baseline === null ? 'equity' : metric;

  const observations = useMemo(() => {
    const values = new Map<number, Observation>();
    for (const row of rows) {
      const ts = finite(row.ts ?? row.timestamp);
      if (ts === null || !Number.isFinite(new Date(ts).getTime())) continue;
      const equity = finite(row.equity);
      const drawdown = finite(row.drawdown_pct);
      if (equity === null && drawdown === null) continue;
      const time = Math.floor(ts / 1000) as UTCTimestamp;
      values.set(time, {
        time,
        equity,
        benchmark: finite(row.benchmark),
        drawdown: drawdown === null ? null : -Math.abs(drawdown),
        initial: false,
      });
    }
    const data = [...values.values()].sort((a, b) => a.time - b.time);
    // This point is the configured starting capital, never the first sampled equity.
    if (
      data.length &&
      baseline !== null &&
      startTs !== undefined &&
      Number.isFinite(startTs) &&
      Number.isFinite(new Date(startTs).getTime()) &&
      Math.floor(startTs / 1000) < data[0].time
    )
      data.unshift({
        time: Math.floor(startTs / 1000) as UTCTimestamp,
        equity: baseline,
        benchmark: data.some((point) => point.benchmark !== null) ? baseline : null,
        drawdown: data.some((point) => point.drawdown !== null) ? 0 : null,
        initial: true,
      });
    return data;
  }, [rows, baseline, startTs]);
  const data = useMemo<ChartPoint[]>(
    () =>
      observations.map((point) => {
        const normalized = (value: number | null) =>
          value === null || baseline === null ? null : finite((value / baseline - 1) * 100);
        return {
          ...point,
          value:
            displayMetric === 'drawdown'
              ? point.drawdown
              : displayMetric === 'returns'
                ? normalized(point.equity)
                : point.equity,
          benchmarkValue:
            displayMetric === 'drawdown'
              ? null
              : displayMetric === 'returns'
                ? normalized(point.benchmark)
                : point.benchmark,
        };
      }),
    [observations, baseline, displayMetric],
  );
  const hasData = data.some((point) => !point.initial && point.value !== null);
  const hasBenchmark = data.some((point) => !point.initial && point.benchmarkValue !== null);
  const showUnderwater =
    linkedDrawdown &&
    displayMetric !== 'drawdown' &&
    data.some((point) => !point.initial && point.drawdown !== null);
  const chartHeight = Number.isFinite(height) ? Math.max(160, height) : 300;

  useEffect(() => {
    if (!mainRef.current || !hasData) return;
    const palette = chartPalette(mainRef.current);
    const domain = `${data[0].time}:${data[data.length - 1].time}:${data.length}`;
    if (savedDomain.current !== domain) {
      savedDomain.current = domain;
      savedRange.current = null;
      presetRef.current = 'all';
      setPreset('all');
      inspection.current = { keyboard: false, index: null };
      setPointIndex(null);
    }
    const chartOptions = (chartHeight: number) => ({
      height: chartHeight,
      layout: {
        background: { type: ColorType.Solid, color: palette.surface },
        textColor: palette.text,
        fontSize: 12,
        attributionLogo: true,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: palette.grid } },
      rightPriceScale: { borderVisible: false, minimumWidth: 88 },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        minBarSpacing: 0.001,
      },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const main = createChart(mainRef.current, {
      ...chartOptions(chartHeight),
      width: Math.max(1, mainRef.current.clientWidth),
    });
    const strategy = main.addSeries(LineSeries, {
      color: displayMetric === 'drawdown' ? palette.drawdown : palette.strategy,
      lineWidth: 2,
      priceLineVisible: false,
      ...(displayMetric !== 'equity'
        ? { priceFormat: { type: 'percent' as const, precision: 2, minMove: 0.01 } }
        : { priceFormat: { type: 'price' as const, precision: 2, minMove: 0.01 } }),
    });
    strategy.setData(
      data.map((point) =>
        point.value === null ? { time: point.time } : { time: point.time, value: point.value },
      ),
    );
    if (hasBenchmark) {
      const benchmark = main.addSeries(LineSeries, {
        color: palette.benchmark,
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        ...(displayMetric === 'returns'
          ? { priceFormat: { type: 'percent' as const, precision: 2, minMove: 0.01 } }
          : {}),
      });
      benchmark.setData(
        data.map((point) =>
          point.benchmarkValue === null
            ? { time: point.time }
            : { time: point.time, value: point.benchmarkValue },
        ),
      );
    }
    const underwater =
      showUnderwater && drawdownRef.current
        ? createChart(drawdownRef.current, {
            ...chartOptions(130),
            width: Math.max(1, drawdownRef.current.clientWidth),
          })
        : null;
    const drawdown = underwater?.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: palette.drawdown,
      bottomLineColor: palette.drawdown,
      topFillColor1: 'rgba(174, 72, 67, 0.02)',
      topFillColor2: 'rgba(174, 72, 67, 0.02)',
      bottomFillColor1: 'rgba(174, 72, 67, 0.04)',
      bottomFillColor2: 'rgba(174, 72, 67, 0.14)',
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: 'percent', precision: 2, minMove: 0.01 },
    });
    drawdown?.setData(
      data.map((point) =>
        point.drawdown === null
          ? { time: point.time }
          : { time: point.time, value: point.drawdown },
      ),
    );

    let syncingRange = false;
    let applyingRange = false;
    let expectedPresetRange: LogicalRange | null = null;
    let crosshairTime: number | null | undefined;
    const indexByTime = new Map(data.map((point, index) => [Number(point.time), index]));
    const inspect = (index: number) => {
      const point = data[index];
      if (!point) return;
      inspection.current = { keyboard: true, index };
      const visible = main.timeScale().getVisibleLogicalRange();
      if (visible && (index < Math.ceil(visible.from) || index > Math.floor(visible.to))) {
        clearPreset();
        const span = Math.min(data.length, Math.max(1, visible.to - visible.from));
        const from = Math.max(-0.5, Math.min(index - 0.5, data.length - 0.5 - span));
        const next = { from, to: from + span } as LogicalRange;
        savedRange.current = next;
        main.timeScale().setVisibleLogicalRange(next);
        underwater?.timeScale().setVisibleLogicalRange(next);
      }
      crosshairTime = point.time;
      setPointIndex(index);
      if (point.value !== null) main.setCrosshairPosition(point.value, point.time, strategy);
      else main.clearCrosshairPosition();
      if (underwater && drawdown && point.drawdown !== null)
        underwater.setCrosshairPosition(point.drawdown, point.time, drawdown);
      else underwater?.clearCrosshairPosition();
    };
    const moveCrosshair = (
      event: MouseEventParams,
      target: IChartApi | null,
      series: ISeriesApi<SeriesType> | undefined,
      value: (point: ChartPoint) => number | null,
    ) => {
      // A programmatic crosshair outside the previous visible range can emit null
      // before the queued range update. It must not replace the keyboard selection.
      if (inspection.current.keyboard) return;
      const time = typeof event.time === 'number' && event.point ? Number(event.time) : null;
      // Programmatic crosshair events can fire again; each timestamp is propagated once.
      if (time === crosshairTime) return;
      crosshairTime = time;
      const index = time === null ? undefined : indexByTime.get(time);
      inspection.current.index = index ?? null;
      setPointIndex(index ?? null);
      const point = index === undefined ? undefined : data[index];
      const price = point ? value(point) : null;
      if (target && series && point && price !== null)
        target.setCrosshairPosition(price, point.time, series);
      else target?.clearCrosshairPosition();
    };
    const onMainCrosshair = (event: MouseEventParams) =>
      moveCrosshair(event, underwater, drawdown, (point) => point.drawdown);
    const onDrawdownCrosshair = (event: MouseEventParams) =>
      moveCrosshair(event, main, strategy, (point) => point.value);
    const synchronizeRange = (target: IChartApi | null, range: LogicalRange | null) => {
      if (!range || syncingRange) return;
      // Resizing can emit an intermediate range before its queued target is applied.
      if (presetRef.current && expectedPresetRange && !sameRange(range, expectedPresetRange))
        return;
      savedRange.current = range;
      if (target && !sameRange(range, target.timeScale().getVisibleLogicalRange())) {
        syncingRange = true;
        target.timeScale().setVisibleLogicalRange(range);
        syncingRange = false;
      }
    };
    const onMainRange = (range: LogicalRange | null) => synchronizeRange(underwater, range);
    const onDrawdownRange = (range: LogicalRange | null) => synchronizeRange(main, range);
    const presetRange = (next: RangePreset): LogicalRange => {
      const days = { all: Infinity, '1m': 30, '3m': 90, '1y': 365 }[next];
      const cutoff = data[data.length - 1].time - days * 86400;
      const first = Math.max(
        0,
        data.findIndex((point) => point.time >= cutoff),
      );
      return { from: first - 0.5, to: data.length - 0.5 } as LogicalRange;
    };
    const applyPreset = (next: RangePreset) => {
      inspection.current = { keyboard: false, index: null };
      crosshairTime = null;
      setPointIndex(null);
      main.clearCrosshairPosition();
      underwater?.clearCrosshairPosition();
      applyingRange = true;
      // Range setters apply on the next frame; do not read the previous range here.
      expectedPresetRange = presetRange(next);
      savedRange.current = expectedPresetRange;
      main.timeScale().setVisibleLogicalRange(expectedPresetRange);
      underwater?.timeScale().setVisibleLogicalRange(expectedPresetRange);
      applyingRange = false;
    };
    const initialRange = savedRange.current ?? presetRange('all');
    savedRange.current = initialRange;
    expectedPresetRange = presetRef.current ? initialRange : null;
    main.timeScale().setVisibleLogicalRange(initialRange);
    underwater?.timeScale().setVisibleLogicalRange(initialRange);
    controls.current = { range: applyPreset, inspect };
    main.subscribeCrosshairMove(onMainCrosshair);
    main.timeScale().subscribeVisibleLogicalRangeChange(onMainRange);
    underwater?.subscribeCrosshairMove(onDrawdownCrosshair);
    underwater?.timeScale().subscribeVisibleLogicalRangeChange(onDrawdownRange);
    const clearPreset = () => {
      if (applyingRange || presetRef.current === null) return;
      presetRef.current = null;
      expectedPresetRange = null;
      setPreset(null);
    };
    let dragOrigin: { x: number; y: number } | null = null;
    const beginDrag = (event: PointerEvent) => {
      inspection.current.keyboard = false;
      dragOrigin = { x: event.clientX, y: event.clientY };
    };
    const drag = (event: PointerEvent) => {
      if (
        dragOrigin &&
        event.buttons &&
        Math.hypot(event.clientX - dragOrigin.x, event.clientY - dragOrigin.y) > 3
      )
        clearPreset();
    };
    const endDrag = () => {
      dragOrigin = null;
    };
    const resumePointerInspection = () => {
      if (document.activeElement !== inspectorRef.current) inspection.current.keyboard = false;
    };
    const surfaces = [mainRef.current, drawdownRef.current].filter(
      (surface): surface is HTMLDivElement => surface !== null,
    );
    for (const surface of surfaces) {
      surface.addEventListener('pointerdown', beginDrag, true);
      surface.addEventListener('pointermove', drag, true);
      surface.addEventListener('pointermove', resumePointerInspection, true);
      surface.addEventListener('pointerup', endDrag, true);
      surface.addEventListener('pointercancel', endDrag, true);
      surface.addEventListener('wheel', clearPreset, { passive: true, capture: true });
      surface.addEventListener('touchmove', clearPreset, { passive: true, capture: true });
      surface.addEventListener('dblclick', clearPreset, true);
    }
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (!width) return;
      const range = expectedPresetRange ?? main.timeScale().getVisibleLogicalRange();
      applyingRange = true;
      main.applyOptions({ width });
      underwater?.applyOptions({ width });
      if (range) {
        main.timeScale().setVisibleLogicalRange(range);
        underwater?.timeScale().setVisibleLogicalRange(range);
      }
      expectedPresetRange = presetRef.current ? range : null;
      applyingRange = false;
    });
    observer.observe(mainRef.current);
    if (inspection.current.keyboard && inspection.current.index !== null)
      inspect(Math.min(inspection.current.index, data.length - 1));
    return () => {
      savedRange.current = main.timeScale().getVisibleLogicalRange();
      controls.current = null;
      observer.disconnect();
      for (const surface of surfaces) {
        surface.removeEventListener('pointerdown', beginDrag, true);
        surface.removeEventListener('pointermove', drag, true);
        surface.removeEventListener('pointermove', resumePointerInspection, true);
        surface.removeEventListener('pointerup', endDrag, true);
        surface.removeEventListener('pointercancel', endDrag, true);
        surface.removeEventListener('wheel', clearPreset, true);
        surface.removeEventListener('touchmove', clearPreset, true);
        surface.removeEventListener('dblclick', clearPreset, true);
      }
      main.unsubscribeCrosshairMove(onMainCrosshair);
      main.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRange);
      underwater?.unsubscribeCrosshairMove(onDrawdownCrosshair);
      underwater?.timeScale().unsubscribeVisibleLogicalRangeChange(onDrawdownRange);
      underwater?.remove();
      main.remove();
    };
  }, [data, displayMetric, hasData, hasBenchmark, showUnderwater, chartHeight]);

  if (!hasData)
    return (
      <Empty title={metric === 'drawdown' ? 'No drawdown observations' : 'No equity observations'}>
        {text(
          'No server-calculated series is available for this result.',
          '此结果没有可用的服务器计算曲线。',
        )}
      </Empty>
    );
  const index = pointIndex === null ? data.length - 1 : Math.min(pointIndex, data.length - 1);
  const point = data[index];
  const percentage = displayMetric !== 'equity';
  const formatValue = (value: number | null) => (percentage ? percent(value) : number(value));
  const strategyLabel =
    displayMetric === 'drawdown'
      ? t('Strategy drawdown')
      : displayMetric === 'returns'
        ? text('Strategy return', '策略收益率')
        : t('Strategy equity');
  const pointDate = date(point.time * 1000, true);
  const readablePoint = `${pointDate} · ${strategyLabel}: ${formatValue(point.value)}${percentage ? '' : ' USDT'}${hasBenchmark ? ` · ${t('Unlevered buy & hold')}: ${formatValue(point.benchmarkValue)}${percentage ? '' : ' USDT'}` : ''}${showUnderwater ? ` · ${t('Strategy drawdown')}: ${percent(point.drawdown)}` : ''}`;

  return (
    <div className="research-chart-wrap research-chart-desk">
      <div className="research-chart-toolbar">
        {title && <h3 className="research-chart-title">{title}</h3>}
        {metricControls && <div className="research-chart-metric-controls">{metricControls}</div>}
        <div className="research-chart-legend">
          <span>
            <i className={displayMetric === 'drawdown' ? 'legend-drawdown' : 'legend-strategy'} />
            {strategyLabel} · {percentage ? '%' : 'USDT'}
          </span>
          {hasBenchmark && (
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
          className="research-chart-ranges"
          role="group"
          aria-label={text('Chart range', '图表范围')}
        >
          {(['all', '1m', '3m', '1y'] as const).map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={preset === key}
              title={
                key === 'all'
                  ? text('Full evaluated window', '完整评估时段')
                  : text(
                      `Last ${{ '1m': 30, '3m': 90, '1y': 365 }[key]} days of the result`,
                      `结果的最后 ${{ '1m': 30, '3m': 90, '1y': 365 }[key]} 天`,
                    )
              }
              onClick={() => {
                presetRef.current = key;
                setPreset(key);
                controls.current?.range(key);
              }}
            >
              {key === 'all' ? text('All', '全部') : key.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {metric === 'returns' && baseline === null && (
        <p className="research-chart-note" role="status">
          {text(
            'Return view needs positive initial capital. Showing equity in USDT.',
            '收益率视图需要正数初始资金。当前显示 USDT 权益。',
          )}
        </p>
      )}

      <div
        ref={mainRef}
        className="financial-chart pro-equity-chart research-chart-canvas"
        role="img"
        aria-label={text('Research performance chart', '研究表现图表')}
        aria-describedby={summaryId}
      />
      <output id={summaryId} className="research-chart-point" aria-live="off">
        <time dateTime={new Date(point.time * 1000).toISOString()}>{pointDate}</time>
        {point.initial && <span>{text('Initial capital', '初始资金')}</span>}
        <span className="research-chart-point-strategy">
          {strategyLabel} <strong>{formatValue(point.value)}</strong>
        </span>
        {hasBenchmark && (
          <span>
            {text('Buy & hold', '买入持有')} <strong>{formatValue(point.benchmarkValue)}</strong>
          </span>
        )}
        {hasBenchmark && point.value !== null && point.benchmarkValue !== null && (
          <span>
            {text('Difference', '差额')}{' '}
            <strong>
              {number(point.value - point.benchmarkValue)} {percentage ? 'pp' : 'USDT'}
            </strong>
          </span>
        )}
        {showUnderwater && (
          <span>
            {t('Drawdown')} <strong>{percent(point.drawdown)}</strong>
          </span>
        )}
      </output>
      {showUnderwater && (
        <div className="research-chart-underwater">
          <div className="research-chart-underwater-heading">
            <span>{t('Strategy drawdown')} · %</span>
            <span>{text('Linked time range', '联动时间范围')}</span>
          </div>
          <div
            ref={drawdownRef}
            className="research-chart-canvas"
            role="img"
            aria-label={text('Linked research drawdown chart', '联动研究回撤图表')}
            aria-describedby={summaryId}
          />
        </div>
      )}
      <label className="research-chart-inspector" htmlFor={pointId}>
        <span>{text('Inspect observation', '查看观测点')}</span>
        <input
          ref={inspectorRef}
          id={pointId}
          type="range"
          min={0}
          max={data.length - 1}
          step={1}
          value={index}
          aria-valuetext={readablePoint}
          aria-describedby={summaryId}
          onFocus={() => {
            inspection.current.keyboard = true;
          }}
          onChange={(event) => controls.current?.inspect(Number(event.target.value))}
        />
        <span>{text('Arrow keys · Home · End', '方向键 · Home · End')}</span>
      </label>
    </div>
  );
}
