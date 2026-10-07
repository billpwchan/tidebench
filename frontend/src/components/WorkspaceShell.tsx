import type { UseQueryResult } from '@tanstack/react-query';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  HelpCircle,
  Layers3,
  Menu,
  Search,
  Settings2,
  ShieldCheck,
  Wallet,
  X,
} from 'lucide-react';
import type { System } from '../api';
import { Logo } from '../components/workspace';
import type { Page } from '../lib/config';
import { symbols } from '../lib/config';

export const pages: { id: Page; label: string; icon: typeof Activity }[] = [
  { id: 'overview', label: 'Overview', icon: Layers3 },
  { id: 'research', label: 'Research', icon: FlaskConical },
  { id: 'paper', label: 'Paper desk', icon: Wallet },
  { id: 'risk', label: 'Risk & activity', icon: ShieldCheck },
  { id: 'settings', label: 'Settings', icon: Settings2 },
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
  return (
    <aside className={`sidebar ${mobileNav ? 'is-open' : ''}`}>
      <a className="brand" href="#overview" onClick={() => navigate('overview')}>
        <Logo />
        <span>
          tidebench<span className="brand-period">.</span>
        </span>
      </a>
      <div className="workspace-selector">
        <span className="workspace-avatar">P</span>
        <div>
          <strong>Personal workspace</strong>
          <span>Self-hosted · v{system.data?.version ?? '0.1.0'}</span>
        </div>
        <ChevronDown size={13} />
      </div>
      <span className="nav-label">WORKSPACE</span>
      <nav aria-label="Main navigation">
        {pages.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`nav-item ${page === id ? 'active' : ''}`}
            onClick={() => navigate(id)}
            aria-label={label}
            aria-current={page === id ? 'page' : undefined}
          >
            <Icon size={18} strokeWidth={1.65} />
            <span>{label}</span>
            {id === 'research' && (
              <span className="nav-shortcut" aria-hidden="true">
                R
              </span>
            )}
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="side-note">
          <span className="side-note-icon">
            <ShieldCheck size={19} />
          </span>
          <strong>
            Your research.
            <br />
            Your infrastructure.
          </strong>
          <p>Data and execution, with a clear audit trail.</p>
        </div>
        <button className="sidebar-help" onClick={() => navigate('settings')}>
          <HelpCircle size={16} />
          <span>Workspace settings</span>
          <ArrowUpRight size={13} />
        </button>
        <div className="sidebar-status">
          <span className={`connection-dot ${system.isSuccess ? 'connected' : ''}`} />
          <span>
            {system.isSuccess
              ? 'Local server connected'
              : system.isError
                ? 'Local server unavailable'
                : 'Connecting to server'}
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
}: {
  page: Page;
  setMobileNav: (v: boolean) => void;
  setSearchOpen: (v: boolean) => void;
}) {
  return (
    <header className="topbar">
      <div className="breadcrumbs">
        <button
          className="icon-button mobile-menu"
          aria-label="Open navigation"
          onClick={() => setMobileNav(true)}
        >
          <Menu size={20} />
        </button>
        <span>Workspace</span>
        <ChevronRight size={12} />
        <strong>{pages.find((p) => p.id === page)?.label}</strong>
      </div>
      <div className="topbar-actions">
        <button
          className="search-trigger"
          onClick={() => setSearchOpen(true)}
          aria-label="Search markets, Command K"
        >
          <Search size={15} />
          <span>Find a market</span>
          <kbd>⌘ K</kbd>
        </button>
        <span className="topbar-divider" />
        <span className="execution-badge">
          <span />
          Local paper
        </span>
        <span className="user-avatar" title="Local workspace">
          P
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
                placeholder="Search markets…"
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
