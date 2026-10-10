import type { RecordData } from '../proApi';

type Decimal = { units: bigint; scale: bigint };
function decimal(value: unknown): Decimal | undefined {
  const raw = String(value ?? '').replace(/^\./, '0.');
  if (raw.length > 64) return;
  const match = /^(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(raw);
  if (!match) return;
  const exponent = Number(match[3] ?? 0);
  if (Math.abs(exponent) > 18) return;
  const digits = (match[2]?.length ?? 0) - exponent;
  if (digits > 24) return;
  const units = BigInt(match[1] + (match[2] ?? ''));
  return digits < 0
    ? { units: units * 10n ** BigInt(-digits), scale: 1n }
    : { units, scale: 10n ** BigInt(digits) };
}
const multiply = (a: Decimal, b: Decimal): Decimal => ({
  units: a.units * b.units,
  scale: a.scale * b.scale,
});
function text(value: Decimal): string {
  const places = value.scale.toString().length - 1;
  const raw = value.units.toString().padStart(places + 1, '0');
  return places ? (raw.slice(0, -places) + '.' + raw.slice(-places)).replace(/\.?0+$/, '') : raw;
}
/** Advisory native size only. The server still checks price, fees, margin and risk. */
export function sizeNotional(
  budget: string,
  quote: unknown,
  instrument: RecordData,
  product: 'SPOT' | 'SWAP',
) {
  const amount = decimal(budget),
    price = decimal(quote),
    lot = decimal(instrument.lot_size),
    minimum = decimal(instrument.min_size);
  if (!amount?.units || !price?.units || !lot?.units || !minimum?.units) return;
  let base: Decimal = { units: 1n, scale: 1n };
  if (product === 'SWAP') {
    const value = decimal(instrument.ct_val),
      multiplier = decimal(instrument.ct_mult);
    if (
      !value?.units ||
      !multiplier?.units ||
      multiplier.units !== multiplier.scale ||
      instrument.ct_val_ccy !== instrument.base ||
      instrument.quote !== 'USDT' ||
      instrument.settle_ccy !== 'USDT' ||
      (instrument.ct_type && instrument.ct_type !== 'linear')
    )
      return;
    base = multiply(value, multiplier);
  }
  const unitNotional = multiply(base, price);
  const lotNotional = multiply(lot, unitNotional);
  const lots = (amount.units * lotNotional.scale) / (amount.scale * lotNotional.units);
  const quantity = { units: lots * lot.units, scale: lot.scale };
  if (!lots || quantity.units * minimum.scale < minimum.units * quantity.scale) return;
  const notional = multiply(quantity, unitNotional);
  const difference = {
    units: amount.units * notional.scale - notional.units * amount.scale,
    scale: amount.scale * notional.scale,
  };
  return {
    quantity: text(quantity),
    notional: text(notional),
    unused: text(difference),
    referencePrice: text(price),
  };
}
