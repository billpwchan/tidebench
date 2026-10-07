import type { UTCTimestamp } from 'lightweight-charts';
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import type { Candle, Result } from './api';

type Props = {
  candles?: Candle[];
  result?: Result;
  mode?: 'price' | 'equity' | 'drawdown';
  height?: number;
};
export default function Chart({ candles, result, mode = 'price', height = 380 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const node = ref.current;
    const chart = createChart(node, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#8b9693',
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif',
        fontSize: 11,
        attributionLogo: true,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: '#edf0ef' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.18 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#a3ada7', labelBackgroundColor: '#293e36' },
        horzLine: { color: '#a3ada7', labelBackgroundColor: '#293e36' },
      },
      handleScroll: true,
      handleScale: true,
    });
    const time = (ts: number) => Math.floor(ts / 1000) as UTCTimestamp;
    if (mode === 'price' && candles?.length) {
      const bars = candles.filter((c) => c.confirmed).sort((a, b) => a.ts - b.ts);
      const series = chart.addSeries(CandlestickSeries, {
        upColor: '#5f8c78',
        downColor: '#be756b',
        borderVisible: false,
        wickUpColor: '#5f8c78',
        wickDownColor: '#be756b',
        priceLineVisible: true,
        lastValueVisible: true,
      });
      series.setData(
        bars.map((c) => ({
          time: time(c.ts),
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
        })),
      );
      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        lastValueVisible: false,
        priceLineVisible: false,
      });
      chart
        .priceScale('volume')
        .applyOptions({ scaleMargins: { top: 0.85, bottom: 0 }, visible: false });
      volume.setData(
        bars.map((c) => ({
          time: time(c.ts),
          value: Number(c.volume),
          color: Number(c.close) >= Number(c.open) ? '#b4c8bd80' : '#d8b5ae80',
        })),
      );
      for (const [period, color] of [
        [12, '#a5b15b'],
        [26, '#929fae'],
      ] as const) {
        const avg = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        avg.setData(
          bars.flatMap((c, i) =>
            i >= period - 1
              ? [
                  {
                    time: time(c.ts),
                    value:
                      bars.slice(i - period + 1, i + 1).reduce((s, b) => s + Number(b.close), 0) /
                      period,
                  },
                ]
              : [],
          ),
        );
      }
    } else if (result?.equity.length) {
      if (mode === 'equity') {
        const benchmark = chart.addSeries(LineSeries, {
          color: '#a5afb6',
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          lineStyle: 2,
        });
        benchmark.setData(
          result.equity.map((p) => ({ time: time(p.ts), value: Number(p.benchmark) })),
        );
      }
      const line = chart.addSeries(LineSeries, {
        color: mode === 'drawdown' ? '#b8756a' : '#315e4c',
        lineWidth: 2,
        priceLineVisible: false,
        crosshairMarkerRadius: 4,
      });
      line.setData(
        result.equity.map((p) => ({
          time: time(p.ts),
          value: mode === 'drawdown' ? p.drawdown_pct : Number(p.equity),
        })),
      );
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) chart.applyOptions({ width });
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candles, result, mode, height]);
  return (
    <div
      ref={ref}
      className="financial-chart"
      role="img"
      aria-label={
        mode === 'price'
          ? 'Candlestick price chart with actual confirmed bars, volume, and 12 and 26 bar moving averages'
          : mode === 'drawdown'
            ? 'Strategy drawdown over the selected backtest'
            : 'Strategy and buy-and-hold benchmark equity over the selected backtest'
      }
      style={{ height }}
    />
  );
}
