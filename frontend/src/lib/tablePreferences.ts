import { useEffect, useMemo, useRef, useState } from 'react';

export type TableSort = { key: string; direction: 'asc' | 'desc' };
type ColumnSchema = {
  key: string;
  sortable?: boolean;
  sortType?: 'number' | 'text';
  sticky?: 'identity' | 'action';
};
export type TablePreferences = {
  version: 1;
  order: string[];
  hidden: string[];
  query: string;
  sort: TableSort | null;
};
export const TABLE_PREFERENCE_PREFIX = 'tidebench:table-preferences:v1:';
export const TABLE_SEARCH_LIMIT = 512;
const changedEvent = 'tidebench:table-preferences-changed';
const invalidMessage =
  'Saved table preferences are invalid. Default columns, search and order are in use.';
const unavailableMessage =
  'Table preferences cannot be saved in this browser. Changes apply to this session only.';
const identityKeys = new Set([
  'symbol',
  'inst_id',
  'instrument',
  'market',
  'asset',
  'currency',
  'ccy',
  'name',
]);
const actionKeys = new Set(['action', 'actions', 'select', 'controls', 'operations']);
const financialKeys = new Set([
  'quantity',
  'equity',
  'cash',
  'available_cash',
  'balance',
  'gross_notional',
  'net_notional',
  'notional',
  'market_value',
  'unrealized_pnl',
  'realized_pnl',
  'pnl',
  'limit_price',
  'mark',
  'status',
]);

export function tablePreferenceSchema(
  columns: ColumnSchema[],
  searchKeys?: string[],
  requiredColumns?: string[],
) {
  const keys = columns.map((column) => column.key);
  const actions = columns
    .filter(
      (column) =>
        column.sticky === 'action' || actionKeys.has(column.key.split('.').at(-1)!.toLowerCase()),
    )
    .map((column) => column.key);
  const fixed = columns
    .filter((column) => {
      const key = column.key.split('.').at(-1)!.toLowerCase();
      return (
        !!column.sticky ||
        key === 'id' ||
        key.endsWith('_id') ||
        identityKeys.has(key) ||
        actionKeys.has(key)
      );
    })
    .map((column) => column.key);
  const required = keys.filter(
    (key) =>
      fixed.includes(key) ||
      requiredColumns?.includes(key) ||
      (requiredColumns === undefined && financialKeys.has(key.split('.').at(-1)!.toLowerCase())),
  );
  if (!required.length && keys.length) required.push(keys[0]);
  const sortable = columns.filter((column) => column.sortable).map((column) => column.key);
  const valid =
    keys.every((key) => typeof key === 'string' && key.length > 0) &&
    new Set(keys).size === keys.length;
  return {
    keys,
    fixed,
    required,
    actions,
    sortable,
    searchable: !!searchKeys?.length,
    valid,
    // Labels and responsive presentation lists are intentionally absent. A locale,
    // ticket or viewport change must not create a different table schema.
    signature: JSON.stringify({
      columns: columns.map((column) => [
        column.key,
        !!column.sortable,
        column.sortType ?? 'text',
        column.sticky ?? null,
      ]),
      searchKeys: searchKeys ?? [],
      required,
      fixed,
    }),
  };
}
type TableSchema = ReturnType<typeof tablePreferenceSchema>;
function defaults(schema: TableSchema): TablePreferences {
  return { version: 1, order: [...schema.keys], hidden: [], query: '', sort: null };
}
function exactKeys(value: Record<string, unknown>, keys: string[]) {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}
function validPreferences(value: unknown, schema: TableSchema): value is TablePreferences {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const p = value as Record<string, unknown>;
  if (
    !exactKeys(p, ['version', 'order', 'hidden', 'query', 'sort']) ||
    p.version !== 1 ||
    typeof p.query !== 'string' ||
    p.query.length > TABLE_SEARCH_LIMIT ||
    (!schema.searchable && p.query !== '') ||
    !Array.isArray(p.order) ||
    !Array.isArray(p.hidden)
  )
    return false;
  const order = p.order as unknown[],
    hidden = p.hidden as unknown[];
  if (
    order.length !== schema.keys.length ||
    new Set(order).size !== order.length ||
    !order.every((key) => typeof key === 'string' && schema.keys.includes(key)) ||
    !schema.fixed.every((key) => order.indexOf(key) === schema.keys.indexOf(key)) ||
    new Set(hidden).size !== hidden.length ||
    !hidden.every(
      (key) =>
        typeof key === 'string' && schema.keys.includes(key) && !schema.required.includes(key),
    )
  )
    return false;
  if (p.sort === null) return true;
  if (!p.sort || typeof p.sort !== 'object' || Array.isArray(p.sort)) return false;
  const sort = p.sort as Record<string, unknown>;
  return (
    exactKeys(sort, ['key', 'direction']) &&
    typeof sort.key === 'string' &&
    schema.sortable.includes(sort.key) &&
    !hidden.includes(sort.key) &&
    (sort.direction === 'asc' || sort.direction === 'desc')
  );
}
type PreferenceState = { key: string; preferences: TablePreferences; error: string | null };
function readPreferences(key: string, schema: TableSchema): PreferenceState {
  const fallback = { key, preferences: defaults(schema), error: null as string | null };
  if (!schema.valid)
    return { ...fallback, error: 'Table columns are invalid. Default table layout is in use.' };
  if (typeof window === 'undefined') return fallback;
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(key);
  } catch {
    return { ...fallback, error: unavailableMessage };
  }
  if (raw === null) return fallback;
  try {
    if (raw.length > 32768) throw new Error('Oversized table preferences.');
    const preferences: unknown = JSON.parse(raw);
    if (!validPreferences(preferences, schema)) throw new Error('Invalid table preferences.');
    return { key, preferences, error: null };
  } catch {
    return { ...fallback, error: invalidMessage };
  }
}

export function useTablePreferences({
  scopeKey,
  page,
  preferenceKey,
  schema: inputSchema,
}: {
  scopeKey: string;
  page: string;
  preferenceKey?: string;
  schema: TableSchema;
}) {
  const schema = useMemo(() => inputSchema, [inputSchema.signature]);
  const key = `${TABLE_PREFERENCE_PREFIX}${encodeURIComponent(JSON.stringify([scopeKey, page, preferenceKey ?? 'table', schema.signature]))}`;
  const [state, setState] = useState(() => readPreferences(key, schema));
  const active = state.key === key ? state : readPreferences(key, schema);
  const latest = useRef(active);
  latest.current = active;
  useEffect(() => {
    // A scope switch restores immediately, before the effect runs. Do not
    // overwrite an interaction that has already written the new scope.
    setState((current) => (current.key === key ? current : latest.current));
  }, [key]);
  useEffect(() => {
    const restore = () => {
      const next = readPreferences(key, schema);
      latest.current = next;
      setState(next);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === key || event.key === null) restore();
    };
    const onChanged = (event: Event) => {
      if (event instanceof CustomEvent && event.detail === key) restore();
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener(changedEvent, onChanged);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(changedEvent, onChanged);
    };
  }, [key, schema]);
  const update = (change: (current: TablePreferences) => TablePreferences) => {
    const preferences = change(latest.current.preferences);
    if (!schema.valid || !validPreferences(preferences, schema)) return;
    let error: string | null = null;
    try {
      window.localStorage.setItem(key, JSON.stringify(preferences));
    } catch {
      error = unavailableMessage;
    }
    const next = { key, preferences, error };
    latest.current = next;
    setState(next);
    if (!error) window.dispatchEvent(new CustomEvent(changedEvent, { detail: key }));
  };
  const reset = () => {
    let error: string | null = null;
    try {
      window.localStorage.removeItem(key);
    } catch {
      error = unavailableMessage;
    }
    const next = { key, preferences: defaults(schema), error };
    latest.current = next;
    setState(next);
    if (!error) window.dispatchEvent(new CustomEvent(changedEvent, { detail: key }));
  };
  return {
    storageKey: key,
    preferences: active.preferences,
    storageError: active.error,
    update,
    reset,
  };
}
