import { useCallback, useEffect, useState } from 'react';
import type { Source } from '../api';
import type { Page } from './config';

export type WorkspaceLocation = {
  page: Page;
  source: Source;
  view?: string;
  runId?: string;
  versionId?: string;
  packageId?: string;
  returnTo?: 'advanced' | 'portfolio';
  symbol?: string;
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
  return {
    page,
    source: source === 'okx' || source === 'example' ? source : fallback,
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
