import type { UseQueryResult } from '@tanstack/react-query';
import {
  Activity,
  Database,
  Gauge,
  ArrowRight,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  GitBranch,
  HelpCircle,
  Layers3,
  Menu,
  Search,
  Settings2,
  Wallet,
  X,
} from 'lucide-react';
import type { System } from '../api';
import { Logo } from '../components/workspace';
import type { Page } from '../lib/config';
import { symbols } from '../lib/config';
import { useI18n } from '../lib/i18n';
import { useMediaQuery } from '../lib/hooks';
import { LanguageSelect, useSession } from './AuthGate';

export const pages: { id: Page; label: string; icon: typeof Activity; group: string }[] = [
  { id: 'overview', label: 'Overview', icon: Layers3, group: 'Research' },
  { id: 'strategies', label: 'Strategies', icon: GitBranch, group: 'Research' },
  { id: 'research', label: 'Research', icon: FlaskConical, group: 'Research' },
  { id: 'execution', label: 'Execution', icon: Wallet, group: 'Execution' },
  { id: 'data', label: 'Data library', icon: Database, group: 'Workspace' },
  { id: 'operations', label: 'Operations', icon: Gauge, group: 'Workspace' },
  { id: 'settings', label: 'Settings', icon: Settings2, group: 'Workspace' },
];

export function Sidebar({
  mobileNav,
  page,
  navigate,
  system,
}: {
  mobileNav: boolean;
  page: Page;
  navigate: (p: Page) => void;
  system: UseQueryResult<System, Error>;
}) {
  const { t } = useI18n();
  const session = useSession();
  const compact = useMediaQuery('(max-width: 850px)');
  const activePage = page === 'paper' || page === 'risk' ? 'execution' : page;
  return (
    <aside
      className={`sidebar ${mobileNav ? 'is-open' : ''}`}
      inert={compact && !mobileNav}
      role={compact && mobileNav ? 'dialog' : undefined}
      aria-modal={compact && mobileNav ? true : undefined}
      aria-label={compact ? t('Main navigation') : undefined}
    >
      <a className="brand" href="#overview" onClick={() => navigate('overview')}>
        <Logo />
        <span>
          tidebench<span className="brand-period">.</span>
        </span>
      </a>
      <div className="workspace-selector">
        <span className="workspace-avatar">
          {(session?.user?.display_name ?? session?.user?.username ?? 'W')
            .slice(0, 1)
            .toUpperCase()}
        </span>
        <div>
          <strong>{t('Personal workspace')}</strong>
          <span>
            {t('Self-hosted')} · {system.data?.version ? `v${system.data.version}` : '—'}
          </span>
        </div>
        <ChevronDown size={13} />
      </div>
      <nav aria-label="Main navigation">
        {['Research', 'Execution', 'Workspace'].map((group) => (
          <div className="nav-group" key={group}>
            <span className="nav-label">{t(group)}</span>
            {pages
              .filter((p) => p.group === group)
              .map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  className={`nav-item ${activePage === id ? 'active' : ''}`}
                  onClick={() => navigate(id)}
                  aria-label={t(label)}
                  aria-current={activePage === id ? 'page' : undefined}
                >
                  <Icon size={18} strokeWidth={1.65} />
                  <span>{t(label)}</span>
                </button>
              ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-session">
          <span className="eyebrow">{t('Session')}</span>
          <strong>{session?.user?.display_name ?? session?.user?.username ?? '—'}</strong>
          <span>{session?.user?.role ?? '—'}</span>
        </div>
        <button className="sidebar-help" onClick={() => navigate('settings')}>
          <HelpCircle size={16} />
          <span>{t('Settings')}</span>
          <ArrowUpRight size={13} />
        </button>
        <div className="sidebar-status">
          <span className={`connection-dot ${system.isSuccess ? 'connected' : ''}`} />
          <span>
            {system.isSuccess
              ? t('Local server connected')
              : system.isError
                ? t('Local server unavailable')
                : t('Connecting to server')}
          </span>
        </div>
      </div>
    </aside>
  );
}

export function Topbar({
  page,
  setMobileNav,
  setSearchOpen,
  executionMode,
}: {
  page: Page;
  setMobileNav: (v: boolean) => void;
  setSearchOpen: (v: boolean) => void;
  executionMode?: string;
}) {
  const { t } = useI18n();
  const session = useSession();
  const currentPage = page === 'paper' || page === 'risk' ? 'execution' : page;
  return (
    <header className="topbar">
      <div className="breadcrumbs">
        <button
          className="icon-button mobile-menu"
          aria-label={t('Open navigation')}
          onClick={() => setMobileNav(true)}
        >
          <Menu size={20} />
        </button>
        <span>{t('Workspace')}</span>
        <ChevronRight size={12} />
        <strong>{t(pages.find((p) => p.id === currentPage)?.label ?? 'Workspace')}</strong>
      </div>
      <div className="topbar-actions">
        <button
          className="search-trigger"
          onClick={() => setSearchOpen(true)}
          aria-label={t('Search markets, Command K')}
        >
          <Search size={15} />
          <span>{t('Find a market')}</span>
          <kbd>⌘ K</kbd>
        </button>
        <span className="topbar-divider" />
        <span className="execution-badge">
          <span />
          {executionMode === 'local-paper' ? t('Local paper') : (executionMode ?? '—')}
        </span>
        <LanguageSelect />
        <span
          className="user-avatar"
          title={session?.user?.display_name ?? session?.user?.username}
        >
          {(session?.user?.display_name ?? session?.user?.username ?? 'W')
            .slice(0, 1)
            .toUpperCase()}
        </span>
      </div>
    </header>
  );
}

export function MarketSearch({
  searchOpen,
  setSearchOpen,
  search,
  setSearch,
  setSymbol,
  navigate,
}: {
  searchOpen: boolean;
  setSearchOpen: (v: boolean) => void;
  search: string;
  setSearch: (v: string) => void;
  setSymbol: (v: string) => void;
  navigate: (p: Page) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      {searchOpen && (
        <div className="modal-backdrop" onClick={() => setSearchOpen(false)}>
          <section
            className="search-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Find a market"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="search-dialog-input">
              <Search size={20} />
              <input
                autoFocus
                placeholder={t('Search markets…')}
                aria-label="Search market symbol"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <button
                className="icon-button"
                onClick={() => setSearchOpen(false)}
                aria-label="Close search"
              >
                <X size={18} />
              </button>
            </div>
            <div className="search-results">
              {symbols
                .filter((s) => s.toLowerCase().includes(search.toLowerCase()))
                .map((s) => (
                  <button
                    key={s}
                    onClick={() => {
                      setSymbol(s);
                      navigate('overview');
                      setSearchOpen(false);
                      setSearch('');
                    }}
                  >
                    <span className="coin-icon">{s.slice(0, 1)}</span>
                    <span>
                      <strong>{s.split('-')[0]}</strong>
                      <small>{s}</small>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                ))}
              {!symbols.some((s) => s.toLowerCase().includes(search.toLowerCase())) && (
                <p>No matching markets.</p>
              )}
            </div>
            <div className="search-dialog-footer">
              <span>OKX spot universe</span>
              <span>
                <kbd>esc</kbd> to close
              </span>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
