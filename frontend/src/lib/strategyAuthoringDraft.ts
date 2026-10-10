import { defaultStrategy, type Strategy } from '../api';
import type { Direction, StrategyVersion } from '../proApi';
import { isDraftRecord } from './researchDraft';

export type StrategyAuthoringDraft = {
  parent: { id: string; projectId: string; revision: number; name: string } | null;
  name: string;
  hypothesis: string;
  strategy: Strategy;
  product: 'SPOT' | 'SWAP';
  bar: string;
  direction: Direction;
  leverage: number;
  // Keep the original text even when JSON cannot be parsed into rules.
  programDraft: string;
};
export const newStrategyAuthoringDraft = (): StrategyAuthoringDraft => ({
  parent: null,
  name: '',
  hypothesis: '',
  strategy: { ...defaultStrategy },
  product: 'SPOT',
  bar: '1H',
  direction: 'long_only',
  leverage: 1,
  programDraft: '',
});
export function authoringDraftFromVersion(
  version: StrategyVersion,
  name: string,
): StrategyAuthoringDraft {
  const strategy = normalizeAuthoringStrategy(version.definition.strategy);
  return {
    parent: { id: version.id, projectId: version.project_id, revision: version.revision, name },
    name,
    hypothesis: version.hypothesis,
    strategy,
    product: version.definition.product,
    bar: version.definition.bar,
    direction: version.definition.direction,
    leverage: Number(version.definition.leverage),
    programDraft: strategy.rules ? JSON.stringify(strategy.rules, null, 2) : '',
  };
}

const kinds = [
  'sma_cross',
  'rsi_reversion',
  'buy_hold',
  'close_breakout',
  'zscore_reversion',
  'ts_momentum',
  'regime_reversion',
  'program',
];
const numericKeys = [
  'fast',
  'slow',
  'rsi_period',
  'window',
  'atr_period',
  'max_holding_bars',
  'vol_window',
  'reversion_trend_window',
];
const stringKeys = [
  'entry',
  'exit',
  'allocation',
  'z_entry',
  'z_exit',
  'stop_loss_pct',
  'take_profit_pct',
  'trailing_stop_pct',
  'risk_per_trade_pct',
  'momentum_entry',
  'max_bar_vol_pct',
  'efficiency_max',
];
const onlyKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).every((key) => keys.includes(key));
const finite = (value: unknown) => typeof value === 'number' && Number.isFinite(value);
const text = (value: unknown, max: number) => typeof value === 'string' && value.length <= max;

export function normalizeAuthoringStrategy(value: Strategy): Strategy {
  const normalized = { ...defaultStrategy, ...value };
  // Older/cropped definitions may omit base controls. Null optional lifecycle
  // settings represent absence; omit them so the existing backend defaults,
  // rather than invalid null fields, are used when a new revision is saved.
  for (const key of ['fast', 'slow', 'rsi_period', 'entry', 'exit', 'allocation'] as const)
    if (normalized[key] == null) Object.assign(normalized, { [key]: defaultStrategy[key] });
  for (const key of [...numericKeys, ...stringKeys, 'momentum_horizons', 'rules'])
    if ((normalized as Record<string, unknown>)[key] == null)
      delete (normalized as Record<string, unknown>)[key];
  return normalized;
}

// Validate controlled-field types, not whether unfinished authoring is ready to
// submit. Invalid numeric inputs and raw rule text must remain editable drafts.
export function validStrategyAuthoringDraft(value: unknown): value is StrategyAuthoringDraft {
  if (
    !isDraftRecord(value) ||
    !onlyKeys(value, [
      'parent',
      'name',
      'hypothesis',
      'strategy',
      'product',
      'bar',
      'direction',
      'leverage',
      'programDraft',
    ])
  )
    return false;
  const strategy = value.strategy;
  if (
    !isDraftRecord(strategy) ||
    !onlyKeys(strategy, ['kind', ...numericKeys, ...stringKeys, 'momentum_horizons', 'rules'])
  )
    return false;
  if (
    !kinds.includes(String(strategy.kind)) ||
    !['fast', 'slow', 'rsi_period'].every((key) => finite(strategy[key])) ||
    !['entry', 'exit', 'allocation'].every((key) => text(strategy[key], 1000))
  )
    return false;
  if (
    !numericKeys.every((key) => strategy[key] == null || finite(strategy[key])) ||
    !stringKeys.every((key) => strategy[key] == null || text(strategy[key], 1000))
  )
    return false;
  if (
    strategy.momentum_horizons != null &&
    (!Array.isArray(strategy.momentum_horizons) || !strategy.momentum_horizons.every(finite))
  )
    return false;
  // Parsed but semantically invalid JSON is retained too. The shared storage
  // hook bounds nested values; validProgramRules separately gates submission.
  if (strategy.rules != null && !Array.isArray(strategy.rules)) return false;
  if (value.parent !== null) {
    if (
      !isDraftRecord(value.parent) ||
      !onlyKeys(value.parent, ['id', 'projectId', 'revision', 'name']) ||
      !/^[a-f0-9]{32}$/.test(String(value.parent.id)) ||
      !/^[a-f0-9]{32}$/.test(String(value.parent.projectId)) ||
      !finite(value.parent.revision) ||
      !Number.isInteger(value.parent.revision) ||
      (value.parent.revision as number) < 1 ||
      !text(value.parent.name, 100)
    )
      return false;
  }
  return (
    text(value.name, 100) &&
    text(value.hypothesis, 4000) &&
    ['SPOT', 'SWAP'].includes(String(value.product)) &&
    ['1m', '5m', '15m', '1H', '4H', '1Dutc'].includes(String(value.bar)) &&
    ['long_only', 'short_only', 'long_short'].includes(String(value.direction)) &&
    finite(value.leverage) &&
    text(value.programDraft, 1024 * 1024)
  );
}

const features = [
  'close',
  'volume',
  'fast_sma',
  'slow_sma',
  'rsi',
  'zscore',
  'channel_upper',
  'channel_lower',
  'atr',
];
function validOperand(value: unknown): boolean {
  if (!isDraftRecord(value) || !onlyKeys(value, ['feature', 'constant'])) return false;
  const hasFeature = value.feature !== undefined && value.feature !== null;
  const hasConstant = value.constant !== undefined && value.constant !== null;
  if (hasFeature === hasConstant) return false;
  if (hasFeature) return typeof value.feature === 'string' && features.includes(value.feature);
  return (
    (typeof value.constant === 'number' ||
      (typeof value.constant === 'string' && value.constant.trim() !== '')) &&
    Number.isFinite(Number(value.constant)) &&
    Math.abs(Number(value.constant)) <= 1e12
  );
}
export function validProgramRules(value: unknown): value is Record<string, unknown>[] {
  return (
    Array.isArray(value) &&
    value.length >= 1 &&
    value.length <= 4 &&
    value.every(
      (rule) =>
        isDraftRecord(rule) &&
        onlyKeys(rule, ['tag', 'signal', 'conditions']) &&
        typeof rule.tag === 'string' &&
        rule.tag.length >= 1 &&
        rule.tag.length <= 80 &&
        [-1, 0, 1].includes(rule.signal as number) &&
        Array.isArray(rule.conditions) &&
        rule.conditions.length >= 1 &&
        rule.conditions.length <= 4 &&
        rule.conditions.every(
          (condition: unknown) =>
            isDraftRecord(condition) &&
            onlyKeys(condition, ['left', 'op', 'right']) &&
            ['gt', 'ge', 'lt', 'le'].includes(String(condition.op)) &&
            validOperand(condition.left) &&
            validOperand(condition.right),
        ),
    )
  );
}
export function readProgramRules(raw: string): Record<string, unknown>[] | undefined {
  try {
    const value: unknown = JSON.parse(raw);
    return validProgramRules(value) ? value : undefined;
  } catch {
    return undefined;
  }
}
