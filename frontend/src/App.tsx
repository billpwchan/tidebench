import { useQuery } from '@tanstack/react-query';
import { FlaskConical } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Bar, Source } from './api';
import { api } from './api';
import { SourceBadge } from './components/workspace';
import { MarketSearch, pages, Sidebar, Topbar } from './components/WorkspaceShell';
import type { Page } from './lib/config';
import { useDialogFocus } from './lib/hooks';
import Overview from './pages/Overview';
import Paper from './pages/Paper';
import Research from './pages/Research';
import RiskPage from './pages/Risk';
import SettingsPage from './pages/Settings';

export default function App() {
  const [page, setPage] = useState<Page>(() => {
    const hash = window.location.hash.slice(1);
    return pages.some((p) => p.id === hash) ? (hash as Page) : 'overview';
  });
  const [source, setSourceState] = useState<Source>(() =>
    localStorage.getItem('tidebench:source') === 'example' ? 'example' : 'okx',
  );
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [bar, setBar] = useState<Bar>('1H');
  const [mobileNav, setMobileNav] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const system = useQuery({ queryKey: ['system'], queryFn: api.system });
  const tickers = useQuery({
    queryKey: ['tickers', source],
    queryFn: () => api.tickers(source),
    refetchInterval: source === 'okx' ? 5000 : false,
  });
  const setSource = (s: Source) => {
    setSourceState(s);
    localStorage.setItem('tidebench:source', s);
  };
  const navigate = (p: Page) => {
    setPage(p);
    window.location.hash = p;
    setMobileNav(false);
    window.scrollTo(0, 0);
  };
  useEffect(() => {
    const onHash = () => {
      const p = window.location.hash.slice(1);
      if (pages.some((x) => x.id === p)) setPage(p as Page);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
      if (e.key === 'Escape') {
        setSearchOpen(false);
        setMobileNav(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useDialogFocus(searchOpen, '.search-dialog', () => setSearchOpen(false));
  const selectedTicker = tickers.data?.items.find((t) => t.inst_id === symbol);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <Sidebar mobileNav={mobileNav} page={page} navigate={navigate} system={system} />
      <div className="workspace-main">
        <Topbar page={page} setMobileNav={setMobileNav} setSearchOpen={setSearchOpen} />
        <main id="main-content" className={`page-content page-${page}`}>
          <div className="source-bar">
            <SourceBadge source={source} />
            <div className="source-control">
              <span>Data source</span>
              <select
                aria-label="Market source"
                value={source}
                onChange={(e) => setSource(e.target.value as Source)}
              >
                <option value="okx">OKX public</option>
                <option value="example">Example · synthetic</option>
              </select>
            </div>
          </div>
          {source === 'example' && (
            <div className="example-notice">
              <FlaskConical size={14} />
              <span>
                Synthetic example data. Prices and results are illustrative; this paper account is
                separate from OKX.
              </span>
            </div>
          )}
          {page === 'overview' && (
            <Overview
              key={source}
              source={source}
              symbol={symbol}
              setSymbol={setSymbol}
              bar={bar}
              setBar={setBar}
              tickers={tickers.data?.items}
              tickerError={tickers.error}
              tickerLoading={tickers.isPending}
              retryTickers={() => void tickers.refetch()}
              onExample={() => setSource('example')}
              navigate={navigate}
            />
          )}
          {page === 'research' && (
            <Research
              key={source}
              source={source}
              symbol={symbol}
              setSymbol={setSymbol}
              bar={bar}
              setBar={setBar}
              onExample={() => setSource('example')}
            />
          )}
          {page === 'paper' && (
            <Paper
              key={source}
              source={source}
              symbol={symbol}
              setSymbol={setSymbol}
              ticker={selectedTicker}
            />
          )}
          {page === 'risk' && <RiskPage key={source} source={source} />}
          {page === 'settings' && (
            <SettingsPage system={system.data} source={source} setSource={setSource} />
          )}
          <footer className="page-footer">
            <span>
              Tidebench <span className="footer-dot">·</span> Evidence before execution.
            </span>
            <span>Local simulated execution. No real funds are traded.</span>
          </footer>
        </main>
      </div>
      <MarketSearch
        searchOpen={searchOpen}
        setSearchOpen={setSearchOpen}
        search={search}
        setSearch={setSearch}
        setSymbol={setSymbol}
        navigate={navigate}
      />
    </div>
  );
}
