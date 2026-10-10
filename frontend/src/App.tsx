import { useQuery } from '@tanstack/react-query';
import { FlaskConical } from 'lucide-react';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { Bar, Source } from './api';
import { api } from './api';
import AuthGate from './components/AuthGate';
import WorkspaceViewBoundary from './components/WorkspaceViewBoundary';
import { WorkspaceTabs } from './components/ProWorkspace';
import { ErrorBox, Loading, PageHeading, SourceBadge } from './components/workspace';
import { MarketSearch, Sidebar, Topbar } from './components/WorkspaceShell';
import type { Page } from './lib/config';
import { useWorkspaceLocation, validView, type ResearchDataIntent } from './lib/workspaceLocation';
import WorkspaceJourney from './components/WorkspaceJourney';
import WorkspaceActivity from './components/WorkspaceActivity';
import type { ResearchInputs } from './proApi';
import { proApi } from './proApi';
import { useDialogFocus, useMediaQuery } from './lib/hooks';
import { LanguageProvider, useI18n } from './lib/i18n';
const Overview = lazy(() => import('./pages/Overview'));
const ClassicPaper = lazy(() => import('./pages/Paper'));
const ClassicResearch = lazy(() => import('./pages/Research'));
import SettingsPage from './pages/Settings';
const DataLibrary = lazy(() => import('./pages/DataLibrary'));
const Portfolio = lazy(() => import('./pages/Portfolio'));
const ExecutionRisk = lazy(() =>
  import('./pages/Portfolio').then((module) => ({ default: module.ExecutionRisk })),
);
const Operations = lazy(() => import('./pages/Operations'));
const ProResearch = lazy(() => import('./pages/ProResearch'));
const PortfolioResearch = lazy(() => import('./pages/PortfolioResearch'));
const ResearchGovernance = lazy(() => import('./pages/ResearchGovernance'));
const Strategies = lazy(() => import('./pages/Strategies'));

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
  const [location, setLocation] = useWorkspaceLocation();
  const { page, source } = location;
  const currentLocation = useRef(location);
  currentLocation.current = location;
  const symbol = location.symbol ?? 'BTC-USDT';
  const setSymbol = (symbol: string) => setLocation({ ...location, symbol }, true);
  const [bar, setBar] = useState<Bar>('1H');
  const researchTab = page === 'research' ? (location.view ?? 'advanced') : 'advanced';
  const setResearchTab = (view: string) =>
    setLocation({ ...location, page: 'research', view, runId: undefined });
  const [manualInputs, setManualInputs] = useState<{ source: Source; inputs: ResearchInputs }>();
  const preparedPackage = useQuery({
    queryKey: ['workspace-package', source, location.packageId],
    queryFn: async () => {
      const item = await proApi.package(location.packageId!);
      if (item.source !== source)
        throw new Error(t('The selected package belongs to another data source.'));
      return item;
    },
    enabled: !!location.packageId && (page === 'research' || page === 'data'),
  });
  const strategyVersion = useQuery({
    queryKey: ['workspace-strategy-version', location.versionId],
    queryFn: () => proApi.strategyVersion(location.versionId!),
    enabled: !!location.versionId && page === 'research' && researchTab === 'advanced',
  });
  const selectedInputs = location.packageId
    ? preparedPackage.data?.research_inputs
    : manualInputs?.source === source
      ? manualInputs.inputs
      : undefined;
  const setSource = (next: Source) => {
    setManualInputs(undefined);
    setLocation({
      page,
      source: next,
      view: validView(page, location.view?.split(':')[0]),
      symbol,
    });
  };
  const navigate = (nextPage: Page, requested?: string) => {
    let next = { page: nextPage, source, symbol, view: validView(nextPage, requested) };
    if (nextPage === 'research' && requested?.startsWith('run:')) {
      setLocation({ ...next, view: 'advanced', runId: requested.slice(4) });
    } else if (nextPage === 'research' && requested?.startsWith('portfolio:')) {
      setLocation({ ...next, view: 'portfolio', runId: requested.slice(10) });
    } else if (nextPage === 'data' && requested?.startsWith('package:')) {
      setLocation({ ...next, view: 'packages', packageId: requested.slice(8) });
    } else if (nextPage === 'data' && page === 'research') {
      setLocation({
        ...location,
        ...next,
        returnTo: researchTab === 'portfolio' ? 'portfolio' : 'advanced',
      });
    } else {
      setLocation(next);
    }
    setMobileNav(false);
    window.scrollTo(0, 0);
  };
  const openResearchData = (intent?: ResearchDataIntent) => {
    setLocation({
      ...location,
      page: 'data',
      view: 'packages',
      returnTo: researchTab === 'portfolio' ? 'portfolio' : 'advanced',
      dataIntent: intent?.source === source ? intent : undefined,
    });
    window.scrollTo(0, 0);
  };
  const lastPage = useRef(page);
  useEffect(() => {
    if (lastPage.current !== page) {
      document.getElementById('main-content')?.focus({ preventScroll: true });
      lastPage.current = page;
    }
  }, [page]);
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
        <main id="main-content" tabIndex={-1} className={`page-content page-${page}`}>
          <div className="source-bar">
            {page === 'operations' ? (
              <span className="scope-note">
                {t(
                  'Operations covers all data sources. Liquidity observations use public OKX data.',
                )}
              </span>
            ) : (
              <SourceBadge source={source} />
            )}
            <div className="source-control">
              <span>
                {t(page === 'operations' ? 'Source for research and execution' : 'Data source')}
              </span>
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
          <WorkspaceActivity source={source} navigate={navigate} />
          {source === 'example' && page !== 'operations' && (
            <div className="example-notice">
              <FlaskConical size={14} />
              <span>
                {t(
                  'Synthetic example data. Prices and results are illustrative; this paper account is separate from OKX.',
                )}
              </span>
            </div>
          )}
          {page === 'research' && preparedPackage.data?.ready === false ? (
            <div>
              <ErrorBox
                error={
                  new Error(
                    t(
                      'This research package is not ready. Inspect its preparation before binding it to a study.',
                    ),
                  )
                }
                onRetry={() => void preparedPackage.refetch()}
              />
              <button
                className="button button-secondary"
                onClick={() => navigate('data', 'package:' + location.packageId)}
              >
                {t('Inspect research package')}
              </button>
            </div>
          ) : page === 'research' && (strategyVersion.isError || preparedPackage.isError) ? (
            <ErrorBox
              error={strategyVersion.error ?? preparedPackage.error}
              onRetry={() => {
                void strategyVersion.refetch();
                void preparedPackage.refetch();
              }}
            />
          ) : page === 'research' &&
            ((!!location.versionId && strategyVersion.isPending && researchTab === 'advanced') ||
              (!!location.packageId && preparedPackage.isPending)) ? (
            <Loading />
          ) : (
            <WorkspaceViewBoundary key={`${page}:${researchTab}:${source}`}>
              <Suspense fallback={<Loading />}>
                {page === 'overview' && (
                  <Overview
                    key={source}
                    source={source}
                    marketRequested={location.view === 'market'}
                    journey={<WorkspaceJourney source={source} navigate={navigate} />}
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
                        { key: 'advanced', label: 'Single strategy' },
                        { key: 'portfolio', label: 'Portfolio research' },
                        { key: 'governance', label: 'Research governance' },
                        { key: 'classic', label: 'Legacy research' },
                      ]}
                    />
                    {researchTab === 'governance' ? (
                      <ResearchGovernance
                        source={source}
                        onPortfolioRun={(id) => {
                          navigate('research', 'portfolio:' + id);
                        }}
                        onRun={(id) => {
                          navigate('research', 'run:' + id);
                        }}
                      />
                    ) : researchTab === 'portfolio' ? (
                      <PortfolioResearch
                        key={source}
                        source={source}
                        initialRunId={location.runId}
                        initialInputs={selectedInputs}
                        onRunSelect={(id) => setLocation({ ...location, runId: id || undefined })}
                        onClearDraft={() => {
                          setManualInputs(undefined);
                          setLocation(
                            {
                              ...location,
                              runId: undefined,
                              versionId: undefined,
                              packageId: undefined,
                            },
                            true,
                          );
                        }}
                        onData={openResearchData}
                        onExecution={(id) =>
                          navigate('execution', id ? 'managed:' + id : 'managed')
                        }
                      />
                    ) : researchTab === 'advanced' ? (
                      <ProResearch
                        key={source}
                        source={source}
                        initialRunId={location.runId}
                        onRunSelect={(id) => setLocation({ ...location, runId: id || undefined })}
                        onClearDraft={() => {
                          setManualInputs(undefined);
                          setLocation(
                            {
                              ...location,
                              runId: undefined,
                              versionId: undefined,
                              packageId: undefined,
                            },
                            true,
                          );
                        }}
                        initialInputs={selectedInputs}
                        initialStrategyVersion={strategyVersion.data}
                        onClearStrategyVersion={() =>
                          setLocation({ ...location, versionId: undefined }, true)
                        }
                        onOpenData={openResearchData}
                        onOpenExecution={(id) =>
                          navigate('execution', id ? 'strategies:' + id : 'strategies')
                        }
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
                    source={source}
                    onResearch={(version) => {
                      setLocation({
                        page: 'research',
                        source,
                        view: 'advanced',
                        versionId: version.id,
                        symbol,
                      });
                    }}
                  />
                )}
                {execution && (
                  <>
                    <WorkspaceTabs
                      value={page === 'risk' ? 'risk' : 'portfolio'}
                      onChange={(value) => navigate(value === 'risk' ? 'risk' : 'execution')}
                      items={[
                        { key: 'portfolio', label: 'Account & orders' },
                        { key: 'risk', label: 'Risk & limits' },
                      ]}
                    />
                    {page === 'execution' && (
                      <Portfolio
                        key={source}
                        source={source}
                        initialView={location.view}
                        initialSymbol={symbol}
                        onSymbolChange={setSymbol}
                        onViewChange={(view) => setLocation({ ...location, view })}
                      />
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
                    initialView={location.view}
                    initialPackageId={location.packageId}
                    onViewChange={(view) => setLocation({ ...location, view })}
                    returnTo={location.returnTo}
                    preparation={location.dataIntent}
                    onReturn={() =>
                      setLocation({
                        ...location,
                        page: 'research',
                        view: location.returnTo ?? 'advanced',
                        returnTo: undefined,
                        dataIntent: undefined,
                      })
                    }
                    onResearch={(inputs) => {
                      if (currentLocation.current !== location || location.page !== 'data') return;
                      setManualInputs({ source, inputs });
                      setLocation({
                        ...location,
                        page: 'research',
                        view: location.returnTo ?? 'advanced',
                        runId: undefined,
                        packageId: inputs.package_id,
                        returnTo: undefined,
                        dataIntent: undefined,
                      });
                    }}
                  />
                )}
                {page === 'operations' && (
                  <Operations
                    initialView={location.view}
                    onViewChange={(view) => setLocation({ ...location, view })}
                  />
                )}
                {page === 'settings' && (
                  <SettingsPage system={system.data} source={source} setSource={setSource} />
                )}
              </Suspense>
            </WorkspaceViewBoundary>
          )}
          <footer className="page-footer">
            <span>
              Tidebench <span className="footer-dot">·</span> {t('Self-hosted')}
            </span>
            <span>{t('Local simulated execution. No real funds are traded.')}</span>
          </footer>
        </main>
      </div>
      <MarketSearch
        source={source}
        onSelect={(symbol, target) => {
          setLocation({
            page: target,
            source,
            symbol,
            view: target === 'execution' ? 'order' : 'market',
          });
          setSearchOpen(false);
          setSearch('');
        }}
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
