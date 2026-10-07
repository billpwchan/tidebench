import type { Bar } from '../api';
export const barLabel = (bar: Bar) => (bar === '1Dutc' ? '1D' : bar);
export const num = (v: unknown) => (v === null || v === undefined || v === '' ? null : Number(v));
export const number = (v: unknown, digits = 2) => {
  const n = num(v);
  return n === null || !Number.isFinite(n)
    ? '—'
    : new Intl.NumberFormat('en-US', {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(n);
};
export const price = (v: unknown) => number(v, Number(v) < 1 ? 5 : 2);
export const quantityText = (v: unknown) => {
  const n = num(v);
  return n === null || !Number.isFinite(n)
    ? '—'
    : new Intl.NumberFormat('en-US', { maximumFractionDigits: 8 }).format(n);
};
export const percent = (v: unknown) => {
  const n = num(v);
  return n === null || !Number.isFinite(n) ? '—' : `${n > 0 ? '+' : ''}${number(n)}%`;
};
export const compact = (v: unknown) => {
  const n = num(v);
  return n === null || !Number.isFinite(n)
    ? '—'
    : new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(n);
};
export const date = (ts: number | undefined | null, full = false) =>
  ts
    ? new Intl.DateTimeFormat('en-GB', {
        timeZone: 'UTC',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        ...(full ? { year: 'numeric' as const } : {}),
      }).format(ts) + ' UTC'
    : '—';
export const tone = (v: unknown) => (Number(v) > 0 ? 'positive' : Number(v) < 0 ? 'negative' : '');
export const nameOf = (kind: string) =>
  ({
    sma_cross: 'Moving average crossover',
    rsi_reversion: 'RSI mean reversion',
    buy_hold: 'Buy & hold',
  })[kind] ?? kind;
