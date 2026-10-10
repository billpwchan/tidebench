import { useCallback, useEffect, useState } from 'react';
import type { Source } from '../api';
import type { Page } from './config';

export type ResearchDataIntent = {
  source: Source;
  product: 'SPOT' | 'SWAP';
  bar: string;
  instId?: string;
  startTs?: number;
  endTs?: number;
};
export type WorkspaceLocation = {
  page: Page;
  source: Source;
  view?: string;
  runId?: string;
  versionId?: string;
  packageId?: string;
  returnTo?: 'advanced' | 'portfolio';
  symbol?: string;
  dataIntent?: ResearchDataIntent;
};
const pages: Page[] = [
  'overview',
  'strategies',
  'research',
  'execution',
  'paper',
  'risk',
  'data',
  'operations',
  'settings',
];
const views: Partial<Record<Page, string[]>> = {
  overview: ['market'],
  research: ['advanced', 'portfolio', 'governance', 'classic'],
  execution: [
    'positions',
    'order',
    'orders',
    'ledger',
    'strategies',
    'managed',
    'releases',
    'portfolio-release',
    'performance',
    'contributions',
    'analytics',
    'protect',
  ],
  data: ['packages', 'raw', 'instruments'],
  operations: [
    'feeds',
    'incidents',
    'liquidity',
    'workers',
    'jobs',
    'checkpoints',
    'backups',
    'audit',
    'metrics',
  ],
};
const defaults: Partial<Record<Page, string>> = {
  research: 'advanced',
  execution: 'positions',
  data: 'packages',
  operations: 'feeds',
};
const identifier = (value: string | null) =>
  value && /^[a-zA-Z0-9._-]{1,160}$/.test(value) ? value : undefined;
export function validView(page: Page, value?: string) {
  if (!value || value.length > 220 || !/^[a-zA-Z0-9:._-]+$/.test(value)) return defaults[page];
  return views[page]?.includes(value.split(':')[0]) ? value : defaults[page];
}
export function readWorkspaceLocation(
  hash: string,
  fallback: Source,
): WorkspaceLocation | undefined {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  if (!pages.includes(path as Page)) return undefined;
  const page = path as Page;
  const params = new URLSearchParams(query);
  const source = params.get('source');
  const resolvedSource = source === 'okx' || source === 'example' ? source : fallback;
  const dataProduct = params.get('dataProduct');
  const dataBar = params.get('dataBar');
  const timestamp = (name: string) => {
    const raw = params.get(name);
    const value = raw && /^\d{1,16}$/.test(raw) ? Number(raw) : NaN;
    return Number.isSafeInteger(value) && value > 0 && value < 253402300800000 ? value : undefined;
  };
  const dataIntent: ResearchDataIntent | undefined =
    (dataProduct === 'SPOT' || dataProduct === 'SWAP') &&
    ['1m', '5m', '15m', '1H', '4H', '1Dutc'].includes(dataBar ?? '')
      ? {
          source: resolvedSource,
          product: dataProduct,
          bar: dataBar!,
          instId: identifier(params.get('dataMarket')),
          startTs: timestamp('dataStart'),
          endTs: timestamp('dataEnd'),
        }
      : undefined;
  return {
    page,
    source: resolvedSource,
    view: validView(page, params.get('view') ?? undefined),
    runId: identifier(params.get('run')),
    versionId: identifier(params.get('version')),
    packageId: identifier(params.get('package')),
    returnTo:
      params.get('return') === 'portfolio'
        ? 'portfolio'
        : params.get('return') === 'advanced'
          ? 'advanced'
          : undefined,
    symbol: identifier(params.get('symbol')),
    dataIntent,
  };
}
export function workspaceHash(location: WorkspaceLocation) {
  const params = new URLSearchParams({ source: location.source });
  const view = validView(location.page, location.view);
  if (view) params.set('view', view);
  if (location.runId) params.set('run', location.runId);
  if (location.versionId) params.set('version', location.versionId);
  if (location.packageId) params.set('package', location.packageId);
  if (location.returnTo) params.set('return', location.returnTo);
  if (location.symbol) params.set('symbol', location.symbol);
  if (location.dataIntent?.source === location.source) {
    const intent = location.dataIntent;
    params.set('dataProduct', intent.product);
    params.set('dataBar', intent.bar);
    if (intent.instId) params.set('dataMarket', intent.instId);
    if (intent.startTs) params.set('dataStart', String(intent.startTs));
    if (intent.endTs) params.set('dataEnd', String(intent.endTs));
  }
  return '#' + location.page + '?' + params.toString();
}
function preferredSource(): Source {
  try {
    return localStorage.getItem('tidebench:source') === 'example' ? 'example' : 'okx';
  } catch {
    return 'okx';
  }
}
export function useWorkspaceLocation() {
  const [location, setState] = useState<WorkspaceLocation>(
    () =>
      readWorkspaceLocation(window.location.hash, preferredSource()) ?? {
        page: 'overview',
        source: preferredSource(),
      },
  );
  useEffect(() => {
    const sync = () => {
      const next =
        readWorkspaceLocation(window.location.hash, preferredSource()) ??
        (!window.location.hash
          ? { page: 'overview' as Page, source: preferredSource() }
          : undefined);
      if (next) {
        // Bind a legacy entry to its source before a later source change alters the preference.
        const hash = workspaceHash(next);
        if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
        setState(next);
      }
    };
    sync();
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    return () => {
      window.removeEventListener('hashchange', sync);
      window.removeEventListener('popstate', sync);
    };
  }, []);
  const setLocation = useCallback((next: WorkspaceLocation, replace = false) => {
    const hash = workspaceHash(next);
    if (window.location.hash !== hash) {
      window.history[replace ? 'replaceState' : 'pushState'](null, '', hash);
      window.dispatchEvent(new Event('tidebench:navigation'));
    }
    setState(next);
    try {
      localStorage.setItem('tidebench:source', next.source);
    } catch {
      /* URL remains authoritative. */
    }
  }, []);
  return [location, setLocation] as const;
}
