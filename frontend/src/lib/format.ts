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
// Price precision follows the instrument's tick, independently of money formatting.
// Without rules, retain bounded significant digits rather than rounding tiny quotes to zero.
export const price = (v: unknown, tick?: unknown) => {
  const n = num(v);
  if (n === null || !Number.isFinite(n)) return '—';
  const tickNumber = num(tick);
  if (tickNumber !== null && Number.isFinite(tickNumber) && tickNumber > 0) {
    const [mantissa, exponent = '0'] = tickNumber.toString().toLowerCase().split('e');
    const decimals = Math.max(0, (mantissa.split('.')[1]?.length ?? 0) - Number(exponent));
    if (decimals <= 20) {
      const formatted = new Intl.NumberFormat('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(n);
      // A mismatched rule must not turn a positive price into an apparent zero.
      if (n === 0 || Number(formatted.replaceAll(',', '')) !== 0) return formatted;
    }
  }
  return new Intl.NumberFormat('en-US', {
    maximumSignificantDigits: 12,
    notation: n !== 0 && (Math.abs(n) < 1e-12 || Math.abs(n) >= 1e15) ? 'scientific' : 'standard',
  }).format(n);
};
export const quantityText = (v: unknown) => {
  const n = num(v);
  return n === null || !Number.isFinite(n)
    ? '—'
    : new Intl.NumberFormat('en-US', {
        maximumSignificantDigits: 12,
        notation:
          n !== 0 && (Math.abs(n) < 1e-12 || Math.abs(n) >= 1e15) ? 'scientific' : 'standard',
      }).format(n);
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
