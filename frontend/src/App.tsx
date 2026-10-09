import { useQuery } from '@tanstack/react-query';
import { FlaskConical } from 'lucide-react';
import { lazy, Suspense, useEffect, useState } from 'react';
import type { Bar, Source } from './api';
import { api } from './api';
import AuthGate from './components/AuthGate';
import WorkspaceViewBoundary from './components/WorkspaceViewBoundary';
import { WorkspaceTabs } from './components/ProWorkspace';
import { Loading, PageHeading, SourceBadge } from './components/workspace';
import { MarketSearch, Sidebar, Topbar } from './components/WorkspaceShell';
import type { Page } from './lib/config';
import type { ResearchInputs, StrategyVersion } from './proApi';
import { useDialogFocus, useMediaQuery } from './lib/hooks';
import { LanguageProvider, useI18n } from './lib/i18n';
import Overview from './pages/Overview';
import ClassicPaper from './pages/Paper';
import ClassicResearch from './pages/Research';
import SettingsPage from './pages/Settings';
import DataLibrary from './pages/DataLibrary';
const Portfolio = lazy(() => import('./pages/Portfolio'));
const ExecutionRisk = lazy(() =>
  import('./pages/Portfolio').then((module) => ({ default: module.ExecutionRisk })),
);
const Operations = lazy(() => import('./pages/Operations'));
const ProResearch = lazy(() => import('./pages/ProResearch'));
const PortfolioResearch = lazy(() => import('./pages/PortfolioResearch'));
const ResearchGovernance = lazy(() => import('./pages/ResearchGovernance'));
const Strategies = lazy(() => import('./pages/Strategies'));

const routes: Page[] = [
  'strategies',
  'overview',
  'research',
  'execution',
  'paper',
  'risk',
  'data',
  'operations',
  'settings',
];
export default function App() {
  return (
    <LanguageProvider>
      <AuthGate>
        <Workspace />
      </AuthGate>
    </LanguageProvider>
  );
}
function Workspace() {
  const { t } = useI18n();
  const [page, setPage] = useState<Page>(() => {
    const hash = window.location.hash.slice(1);
    return routes.includes(hash as Page) ? (hash as Page) : 'overview';
  });
  const [source, setSourceState] = useState<Source>(() =>
    localStorage.getItem('tidebench:source') === 'example' ? 'example' : 'okx',
  );
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [bar, setBar] = useState<Bar>('1H');
  const [researchTab, setResearchTab] = useState('advanced');
  const [selectedRunId, setSelectedRunId] = useState<string>();
  const [selectedPortfolioRun, setSelectedPortfolioRun] = useState<string>();
  const [selectedInputs, setSelectedInputs] = useState<ResearchInputs | undefined>();
  const [selectedVersion, setSelectedVersion] = useState<StrategyVersion | undefined>();
  const [executionView, setExecutionView] = useState('positions');
  const [operationsView, setOperationsView] = useState('feeds');
  const [mobileNav, setMobileNav] = useState(false);
  const compactNavigation = useMediaQuery('(max-width: 850px)');
  useEffect(() => {
    if (!compactNavigation) setMobileNav(false);
  }, [compactNavigation]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const system = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 30000 });
  const tickers = useQuery({
    queryKey: ['tickers', source],
    queryFn: () => api.tickers(source),
    refetchInterval: source === 'okx' ? 5000 : false,
    enabled: page === 'paper',
  });
  const setSource = (s: Source) => {
    setSourceState(s);
    setSelectedInputs(undefined);
    setSelectedVersion(undefined);
    setSelectedRunId(undefined);
    setSelectedPortfolioRun(undefined);
    localStorage.setItem('tidebench:source', s);
  };
  const navigate = (p: Page, view?: string) => {
    if (p === 'execution') setExecutionView(view ?? 'positions');
    if (p === 'operations') setOperationsView(view ?? 'feeds');
    setPage(p);
    window.location.hash = p;
    setMobileNav(false);
    window.scrollTo(0, 0);
  };
  useEffect(() => {
    const onHash = () => {
      const p = window.location.hash.slice(1);
      if (routes.includes(p as Page)) setPage(p as Page);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setMobileNav(false);
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
  useDialogFocus(mobileNav, '.sidebar', () => setMobileNav(false));
  const selectedTicker = tickers.data?.items.find((x) => x.inst_id === symbol);
  const execution = page === 'execution' || page === 'paper' || page === 'risk';
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        {t('Skip to content')}
      </a>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-label={t('Close navigation')}
          onClick={() => setMobileNav(false)}
        />
      )}
      <Sidebar mobileNav={mobileNav} page={page} navigate={navigate} system={system} />
      <div className="workspace-main" inert={mobileNav && compactNavigation}>
        <Topbar
          page={page}
          setMobileNav={setMobileNav}
          setSearchOpen={setSearchOpen}
          executionMode={system.data?.execution}
        />
        <main id="main-content" className={`page-content page-${page}`}>
          <div className="source-bar">
            <SourceBadge source={source} />
            <div className="source-control">
              <span>{t('Data source')}</span>
              <select
                aria-label="Market source"
                value={source}
                onChange={(e) => setSource(e.target.value as Source)}
              >
                <option value="okx">{t('OKX public')}</option>
                <option value="example">{t('Example · synthetic')}</option>
              </select>
            </div>
          </div>
          {source === 'example' && (
            <div className="example-notice">
              <FlaskConical size={14} />
              <span>
                {t(
                  'Synthetic example data. Prices and results are illustrative; this paper account is separate from OKX.',
                )}
              </span>
            </div>
          )}
          <WorkspaceViewBoundary key={`${page}:${researchTab}:${source}`}>
            <Suspense fallback={<Loading />}>
              {page === 'overview' && (
                <Overview
                  key={source}
                  source={source}
                  symbol={symbol}
                  setSymbol={setSymbol}
                  navigate={navigate}
                />
              )}
              {page === 'research' && (
                <>
                  <WorkspaceTabs
                    value={researchTab}
                    onChange={setResearchTab}
                    items={[
                      { key: 'advanced', label: 'Advanced' },
                      { key: 'portfolio', label: 'Portfolio research' },
                      { key: 'governance', label: 'Research governance' },
                      { key: 'classic', label: 'Classic' },
                    ]}
                  />
                  {researchTab === 'governance' ? (
                    <ResearchGovernance
                      source={source}
                      onPortfolioRun={(id) => {
                        setSelectedPortfolioRun(id);
                        setResearchTab('portfolio');
                      }}
                      onRun={(id) => {
                        setSelectedVersion(undefined);
                        setSelectedInputs(undefined);
                        setSelectedRunId(id);
                        setResearchTab('advanced');
                      }}
                    />
                  ) : researchTab === 'portfolio' ? (
                    <PortfolioResearch
                      key={source}
                      source={source}
                      initialRunId={selectedPortfolioRun}
                      onData={() => navigate('data')}
                      onExecution={() => navigate('execution', 'managed')}
                    />
                  ) : researchTab === 'advanced' ? (
                    <ProResearch
                      key={source}
                      source={source}
                      initialRunId={selectedRunId}
                      initialInputs={selectedInputs}
                      initialStrategyVersion={selectedVersion}
                      onClearStrategyVersion={() => setSelectedVersion(undefined)}
                      onOpenData={() => navigate('data')}
                      onOpenExecution={() => navigate('execution', 'strategies')}
                    />
                  ) : (
                    <ClassicResearch
                      key={source}
                      source={source}
                      symbol={symbol}
                      setSymbol={setSymbol}
                      bar={bar}
                      setBar={setBar}
                      onExample={() => setSource('example')}
                    />
                  )}
                </>
              )}
              {page === 'strategies' && (
                <Strategies
                  onResearch={(version) => {
                    setSelectedRunId(undefined);
                    setSelectedVersion(version);
                    setResearchTab('advanced');
                    navigate('research');
                  }}
                />
              )}
              {execution && (
                <>
                  <WorkspaceTabs
                    value={page === 'risk' ? 'risk' : 'portfolio'}
                    onChange={(value) => navigate(value === 'risk' ? 'risk' : 'execution')}
                    items={[
                      { key: 'portfolio', label: 'Portfolio' },
                      { key: 'risk', label: 'Risk' },
                    ]}
                  />
                  {page === 'execution' && (
                    <Portfolio key={source} source={source} initialView={executionView} />
                  )}
                  {page === 'paper' && (
                    <ClassicPaper
                      key={source}
                      source={source}
                      symbol={symbol}
                      setSymbol={setSymbol}
                      ticker={selectedTicker}
                    />
                  )}
                  {page === 'risk' && (
                    <>
                      <PageHeading
                        eyebrow="EXECUTION"
                        title="Risk"
                        description="Portfolio limits and durable execution controls."
                      />
                      <ExecutionRisk key={source} source={source} analytics />
                    </>
                  )}
                </>
              )}
              {page === 'data' && (
                <DataLibrary
                  key={source}
                  source={source}
                  onResearch={(inputs) => {
                    setSelectedRunId(undefined);
                    setSelectedVersion(undefined);
                    setSelectedInputs(inputs);
                    setResearchTab('advanced');
                    navigate('research');
                  }}
                />
              )}
              {page === 'operations' && <Operations initialView={operationsView} />}
              {page === 'settings' && (
                <SettingsPage system={system.data} source={source} setSource={setSource} />
              )}
            </Suspense>
          </WorkspaceViewBoundary>
          <footer className="page-footer">
            <span>
              Tidebench <span className="footer-dot">·</span> {t('Self-hosted')}
            </span>
            <span>{t('Local simulated execution. No real funds are traded.')}</span>
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
