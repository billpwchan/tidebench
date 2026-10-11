import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useSession } from './AuthGate';
import type { Source } from '../api';
import type { WorkspaceLocation } from '../lib/workspaceLocation';
import { readWorkspaceLocation, workspaceHash } from '../lib/workspaceLocation';

export type DisplayLayout = {
  density: 'comfortable' | 'compact';
  monitorLayout: 'balanced' | 'focus';
  ticketWidth: 'standard' | 'wide';
};
export type SavedWorkspace = {
  id: string;
  name: string;
  location: WorkspaceLocation;
  layout: DisplayLayout;
};
type Preferences = DisplayLayout & {
  version: 1;
  favorites: string[];
  recentMarkets: string[];
  workspaces: SavedWorkspace[];
};
type PreferenceContext = Preferences & {
  scopeKey: string;
  page: string;
  storageError: string | null;
  update: (patch: Partial<DisplayLayout>) => void;
  rememberMarket: (symbol: string) => void;
  toggleFavorite: (symbol: string) => void;
  saveWorkspace: (name: string, location: WorkspaceLocation) => void;
  removeWorkspace: (id: string) => void;
  restoreLayout: (layout: DisplayLayout) => void;
  reset: () => void;
};
const defaults: Preferences = {
  version: 1,
  density: 'comfortable',
  monitorLayout: 'balanced',
  ticketWidth: 'standard',
  favorites: [],
  recentMarkets: [],
  workspaces: [],
};
const symbolIsValid = (s: unknown): s is string =>
  typeof s === 'string' && /^[A-Za-z0-9._-]{1,100}$/.test(s);
const layoutIsValid = (p: unknown): p is DisplayLayout =>
  !!p &&
  typeof p === 'object' &&
  ['comfortable', 'compact'].includes((p as DisplayLayout).density) &&
  ['balanced', 'focus'].includes((p as DisplayLayout).monitorLayout) &&
  ['standard', 'wide'].includes((p as DisplayLayout).ticketWidth);
const cleanLayout = (layout: DisplayLayout): DisplayLayout => ({
  density: layout.density,
  monitorLayout: layout.monitorLayout,
  ticketWidth: layout.ticketWidth,
});
function safeLocation(location: WorkspaceLocation, source: Source): WorkspaceLocation {
  const parsed = readWorkspaceLocation(workspaceHash(location), source);
  if (!parsed || parsed.source !== source) throw new Error('Invalid workspace source.');
  // Read-only record selection is retained; drafts and protection instructions are not.
  if (['execution', 'paper', 'risk'].includes(parsed.page)) {
    parsed.page = 'execution';
    parsed.view = [
      'positions',
      'orders',
      'analytics',
      'performance',
      'managed',
      'strategies',
      'ledger',
      'releases',
      'portfolio-release',
      'contributions',
    ].includes(parsed.view?.split(':')[0] ?? '')
      ? parsed.view
      : 'positions';
    parsed.runId = undefined;
    parsed.versionId = undefined;
  }
  parsed.dataIntent = undefined;
  parsed.portfolioRecipe = undefined;
  parsed.returnTo = undefined;
  return parsed;
}
function readPreferences(scopeKey: string, source: Source) {
  try {
    const raw = localStorage.getItem(scopeKey);
    if (!raw) return { preferences: { ...defaults }, error: null };
    if (raw.length > 32768) throw new Error('Oversized display preferences.');
    const p = JSON.parse(raw) as Preferences;
    if (
      p.version !== 1 ||
      !layoutIsValid(p) ||
      !Array.isArray(p.favorites) ||
      !Array.isArray(p.recentMarkets) ||
      !Array.isArray(p.workspaces) ||
      p.favorites.length > 20 ||
      p.recentMarkets.length > 8 ||
      p.workspaces.length > 8 ||
      !p.favorites.every(symbolIsValid) ||
      !p.recentMarkets.every(symbolIsValid)
    )
      throw new Error('Invalid preferences.');
    const workspaces = p.workspaces.map((w) => {
      if (
        !symbolIsValid(w.id) ||
        typeof w.name !== 'string' ||
        !w.name.trim() ||
        w.name.length > 40 ||
        !layoutIsValid(w.layout)
      )
        throw new Error('Invalid saved workspace.');
      return {
        id: w.id,
        name: w.name,
        layout: cleanLayout(w.layout),
        location: safeLocation(w.location, source),
      };
    });
    return {
      preferences: {
        version: 1 as const,
        density: p.density,
        monitorLayout: p.monitorLayout,
        ticketWidth: p.ticketWidth,
        favorites: [...new Set(p.favorites)],
        recentMarkets: [...new Set(p.recentMarkets)],
        workspaces,
      },
      error: null,
    };
  } catch {
    return {
      preferences: { ...defaults },
      error: 'Display preferences could not be restored. Default layout is in use.',
    };
  }
}
const PreferenceContext = createContext<PreferenceContext>({
  ...defaults,
  scopeKey: 'tidebench:display:v1:local:okx',
  page: 'overview',
  storageError: null,
  update: () => {},
  rememberMarket: () => {},
  toggleFavorite: () => {},
  saveWorkspace: () => {},
  removeWorkspace: () => {},
  restoreLayout: () => {},
  reset: () => {},
});
export const useWorkspacePreferences = () => useContext(PreferenceContext);
export function WorkspacePreferencesProvider({
  source,
  page,
  children,
}: {
  source: Source;
  page: string;
  children: ReactNode;
}) {
  const session = useSession();
  const scopeKey = `tidebench:display:v1:${encodeURIComponent(session?.user?.id ?? session?.user?.username ?? 'local')}:${source}`;
  const [state, setState] = useState(() => ({ scopeKey, ...readPreferences(scopeKey, source) }));
  const active =
    state.scopeKey === scopeKey ? state : { scopeKey, ...readPreferences(scopeKey, source) };
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    setState((current) => (current.scopeKey === scopeKey ? current : activeRef.current));
  }, [scopeKey]);
  useEffect(() => {
    const restore = (event: StorageEvent) => {
      if (event.key !== scopeKey && event.key !== null) return;
      const next = { scopeKey, ...readPreferences(scopeKey, source) };
      activeRef.current = next;
      setState(next);
    };
    window.addEventListener('storage', restore);
    return () => window.removeEventListener('storage', restore);
  }, [scopeKey, source]);
  useEffect(() => {
    document.body.dataset.density = active.preferences.density;
    document.body.dataset.monitorLayout = active.preferences.monitorLayout;
    document.body.dataset.ticketWidth = active.preferences.ticketWidth;
    return () => {
      delete document.body.dataset.density;
      delete document.body.dataset.monitorLayout;
      delete document.body.dataset.ticketWidth;
    };
  }, [
    active.preferences.density,
    active.preferences.monitorLayout,
    active.preferences.ticketWidth,
  ]);
  const write = (change: (current: Preferences) => Preferences) => {
    const current =
      activeRef.current.scopeKey === scopeKey
        ? activeRef.current.preferences
        : readPreferences(scopeKey, source).preferences;
    const preferences = change(current);
    let error = null;
    try {
      localStorage.setItem(scopeKey, JSON.stringify(preferences));
    } catch {
      error =
        'Display preferences cannot be saved in this browser. Changes apply to this session only.';
    }
    const next = { scopeKey, preferences, error };
    activeRef.current = next;
    setState(next);
  };
  const value: PreferenceContext = {
    ...active.preferences,
    scopeKey,
    page,
    storageError: active.error,
    update: (patch) =>
      write((current) => {
        const next = { ...current, ...patch };
        return layoutIsValid(next) ? { ...current, ...cleanLayout(next) } : current;
      }),
    restoreLayout: (layout) => {
      if (layoutIsValid(layout)) write((current) => ({ ...current, ...cleanLayout(layout) }));
    },
    rememberMarket: (symbol) => {
      if (symbolIsValid(symbol) && active.preferences.recentMarkets[0] !== symbol)
        write((current) => ({
          ...current,
          recentMarkets: [symbol, ...current.recentMarkets.filter((s) => s !== symbol)].slice(0, 8),
        }));
    },
    toggleFavorite: (symbol) => {
      if (!symbolIsValid(symbol)) return;
      write((current) => ({
        ...current,
        favorites: current.favorites.includes(symbol)
          ? current.favorites.filter((s) => s !== symbol)
          : [...current.favorites, symbol].slice(-20),
      }));
    },
    saveWorkspace: (name, location) => {
      const cleanName = name.trim().slice(0, 40);
      if (!cleanName) return;
      const safe = safeLocation(location, source);
      write((current) => {
        const existing = current.workspaces.find((w) => w.name === cleanName);
        const workspace = {
          id: existing?.id ?? crypto.randomUUID(),
          name: cleanName,
          location: safe,
          layout: cleanLayout(current),
        };
        return {
          ...current,
          workspaces: [...current.workspaces.filter((w) => w.id !== workspace.id), workspace].slice(
            -8,
          ),
        };
      });
    },
    removeWorkspace: (id) =>
      write((current) => ({
        ...current,
        workspaces: current.workspaces.filter((w) => w.id !== id),
      })),
    reset: () => write(() => ({ ...defaults })),
  };
  return <PreferenceContext.Provider value={value}>{children}</PreferenceContext.Provider>;
}
