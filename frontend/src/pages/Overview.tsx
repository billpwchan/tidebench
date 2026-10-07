import { useQuery } from '@tanstack/react-query';
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  CircleAlert,
  Clock3,
  FlaskConical,
  Plus,
} from 'lucide-react';
import type { Bar, Source, Ticker } from '../api';
import { api } from '../api';
import Chart from '../Chart';
import {
  BarSwitch,
  Empty,
  ErrorBox,
  Loading,
  Metric,
  PageHeading,
  SourceBadge,
  Status,
  SymbolSelect,
} from '../components/workspace';
import type { Page } from '../lib/config';
import { symbols } from '../lib/config';
import { compact, date, nameOf, number, percent, price, tone } from '../lib/format';
import { useNow } from '../lib/hooks';
import { proApi } from '../proApi';
import type { ProResult, ProRun, RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';

const runReturn = (r: ProRun) =>
  r.summary?.total_return_pct ??
  (r.summary?.metrics as RecordData | undefined)?.total_return_pct ??
  (r.summary?.oos_summary as RecordData | undefined)?.median_return_pct ??
  r.summary?.median_return_pct ??
  r.result?.metrics?.total_return_pct ??
  (r.result?.result as ProResult | undefined)?.metrics?.total_return_pct ??
  (r.result?.oos_summary as RecordData | undefined)?.median_return_pct;
export default function Overview({
  source,
  symbol,
  setSymbol,
  bar,
  setBar,
  tickers,
  tickerError,
  tickerLoading,
  retryTickers,
  onExample,
  navigate,
}: {
  source: Source;
  symbol: string;
  setSymbol: (s: string) => void;
  bar: Bar;
  setBar: (b: Bar) => void;
  tickers?: Ticker[];
  tickerError: unknown;
  tickerLoading: boolean;
  retryTickers: () => void;
  onExample: () => void;
  navigate: (p: Page) => void;
}) {
  const candles = useQuery({
    queryKey: ['candles', source, symbol, bar, 240],
    queryFn: () => api.candles(source, symbol, bar, 240),
    refetchInterval: source === 'okx' ? 30000 : false,
  });
  const { t } = useI18n();
  const runs = useQuery({ queryKey: ['pro-runs'], queryFn: proApi.runs });
  const account = useQuery({
    queryKey: ['pro-account', source],
    queryFn: () => proApi.account(source),
    refetchInterval: 5000,
  });
  const risk = useQuery({
    queryKey: ['pro-risk', source],
    queryFn: () => proApi.risk(source),
    refetchInterval: 5000,
  });
  const ops = useQuery({ queryKey: ['pro-ops'], queryFn: proApi.ops, refetchInterval: 10000 });
  const now = useNow();
  const ticker = tickers?.find((t) => t.inst_id === symbol);
  const quoteAge = ticker ? Math.max(0, now - ticker.ts) : null;
  const quoteStale = source === 'okx' && quoteAge !== null && quoteAge >= 15000;
  const sourceRuns = runs.data?.items.filter((r) => r.source === source) ?? [];
  const completed = sourceRuns.filter((r) => r.status === 'completed');
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE OVERVIEW"
        title="Market overview"
        description="Market snapshots, portfolio exposure, research, and service health."
      >
        <button className="button button-dark" onClick={() => navigate('research')}>
          <Plus size={16} />
          {t('Research')}
          <ArrowUpRight size={15} />
        </button>
      </PageHeading>
      <div className="desk-health-strip">
        <button onClick={() => navigate('execution')}>
          <span>{t('Account equity')}</span>
          <strong>
            {number(account.data?.equity)} <small>USDT</small>
          </strong>
          <Status type={account.data?.valuation_status === 'fresh' ? 'good' : 'neutral'}>
            {account.data?.valuation_status ?? 'unavailable'}
          </Status>
        </button>
        <button onClick={() => navigate('execution')}>
          <span>{t('Used margin')}</span>
          <strong>
            {number(account.data?.used_margin)} <small>USDT</small>
          </strong>
          <small>
            {t('Maintenance margin')}: {number(account.data?.maintenance_margin)}
          </small>
        </button>
        <button onClick={() => navigate('risk')}>
          <span>{t('Risk')}</span>
          <strong>
            {risk.data ? t(risk.data.halted ? 'Execution halted' : 'Execution enabled') : '—'}
          </strong>
          <small>
            {t('Maximum leverage')}: {risk.data ? `${risk.data.max_leverage}×` : '—'}
          </small>
        </button>
        <button onClick={() => navigate('operations')}>
          <span>{t('Service health')}</span>
          <strong>
            {ops.data?.health
              ? String(
                  typeof ops.data.health === 'string'
                    ? ops.data.health
                    : (ops.data.health.status ?? '—'),
                )
              : '—'}
          </strong>
          <small>
            {t('Operations')} <ChevronRight size={11} />
          </small>
        </button>
      </div>
      {(account.isError || risk.isError || ops.isError) && (
        <ErrorBox
          error={account.error ?? risk.error ?? ops.error}
          onRetry={() => {
            void account.refetch();
            void risk.refetch();
            void ops.refetch();
          }}
        />
      )}
      <div className="overview-layout">
        <section className="market-canvas">
          <div className="market-hero">
            <div>
              <div className="instrument-label">
                <span className="coin-icon coin-btc">
                  {symbol === 'BTC-USDT' ? '₿' : symbol.slice(0, 1)}
                </span>
                <SymbolSelect value={symbol} onChange={setSymbol} />
                <span className="spot-label">SPOT</span>
              </div>
              <div className="market-price">
                {price(ticker?.last)}
                <span>USDT</span>
              </div>
              <div className={`market-change ${tone(ticker?.change_pct)}`}>
                {ticker && (
                  <>
                    {ticker.change_pct >= 0 ? (
                      <ArrowUpRight size={15} />
                    ) : (
                      <ArrowDownLeft size={15} />
                    )}
                    <span>{percent(ticker.change_pct)}</span>
                    <small>past 24 hours</small>
                  </>
                )}
              </div>
              {ticker && (
                <div className={`quote-age ${quoteStale ? 'stale' : ''}`}>
                  <Clock3 size={11} />
                  {source === 'example'
                    ? 'Fixed example quote'
                    : `Exchange as of ${date(ticker.ts)} · ${Math.floor((quoteAge ?? 0) / 1000)}s old`}
                </div>
              )}
              {quoteStale && (
                <p className="inline-warning">
                  Quote is at least 15 seconds old. Paper execution may be rejected until a fresh
                  quote arrives.
                </p>
              )}
            </div>
            <div className="market-range">
              <span className="eyebrow">CANDLE INTERVAL</span>
              <BarSwitch value={bar} onChange={setBar} />
            </div>
          </div>
          <div className="chart-heading">
            <div className="chart-legend">
              <span>
                <i className="legend-swatch candles" />
                Price
              </span>
              <span>
                <i className="legend-swatch ma-fast" />
                SMA 12
              </span>
              <span>
                <i className="legend-swatch ma-slow" />
                SMA 26
              </span>
            </div>
            <span className="chart-denomination">USDT</span>
          </div>
          {candles.isPending ? (
            <Loading label="Fetching confirmed market candles…" />
          ) : candles.isError ? (
            <ErrorBox
              error={candles.error}
              onRetry={() => void candles.refetch()}
              onExample={source === 'okx' ? onExample : undefined}
            />
          ) : candles.data.candles.some((c) => c.confirmed) ? (
            <Chart candles={candles.data.candles} height={350} />
          ) : (
            <Empty title="No confirmed candles">Try another interval or trading pair.</Empty>
          )}
          <div className="chart-provenance">
            <span>
              <span className="connection-dot" />
              {source === 'example' ? 'Fixed synthetic dataset' : 'Confirmed candles · OKX REST'}
            </span>
            <span>
              {candles.data
                ? `${candles.data.candles.filter((c) => c.confirmed).length} bars · updated ${date(candles.data.fetched_at)}`
                : 'Waiting for market data'}
            </span>
          </div>
          {candles.data?.warning && (
            <p className="inline-warning">
              <CircleAlert size={13} />
              {candles.data.warning}
            </p>
          )}
          <div className="market-stat-strip">
            <Metric label="24h high" value={price(ticker?.high_24h)} />
            <Metric label="24h low" value={price(ticker?.low_24h)} />
            <Metric label="24h volume" value={compact(ticker?.volume_24h)} unit="USDT" />
            <button className="research-market-button" onClick={() => navigate('research')}>
              Research this market
              <ArrowRight size={17} />
            </button>
          </div>
        </section>
        <aside className="watchlist-panel">
          <div className="section-heading">
            <h2>Watchlist</h2>
            <span className="count-label">05 MARKETS</span>
          </div>
          <p className="section-description">A focused view of the spot market.</p>
          <div className="watchlist-column-labels">
            <span>ASSET</span>
            <span>PRICE / 24H</span>
          </div>
          {tickerLoading ? (
            <Loading label="Loading markets…" />
          ) : tickerError ? (
            <ErrorBox
              error={tickerError}
              onRetry={retryTickers}
              onExample={source === 'okx' ? onExample : undefined}
            />
          ) : (
            symbols.map((s, i) => {
              const t = tickers?.find((x) => x.inst_id === s);
              return (
                <button
                  key={s}
                  className={`watchlist-row ${symbol === s ? 'selected' : ''}`}
                  onClick={() => setSymbol(s)}
                  aria-pressed={symbol === s}
                >
                  <span className={`coin-icon coin-${i}`}>
                    {s === 'BTC-USDT' ? '₿' : s.slice(0, 1)}
                  </span>
                  <span className="watchlist-asset">
                    <strong>{s.split('-')[0]}</strong>
                    <small>
                      {
                        (
                          {
                            BTC: 'Bitcoin',
                            ETH: 'Ethereum',
                            SOL: 'Solana',
                            OKB: 'OKB',
                            DOGE: 'Dogecoin',
                          } as Record<string, string>
                        )[s.split('-')[0]]
                      }
                    </small>
                  </span>
                  <span className="watchlist-price">
                    <strong>{price(t?.last)}</strong>
                    <small className={tone(t?.change_pct)}>{percent(t?.change_pct)}</small>
                  </span>
                  {symbol === s && <span className="watchlist-indicator" />}
                </button>
              );
            })
          )}
          <div className="watchlist-note">
            <Clock3 size={14} />
            <span>
              {source === 'example'
                ? 'Fixed example prices. No live feed.'
                : 'REST snapshots refresh every 5 seconds. Check quote age before execution.'}
            </span>
          </div>
          <div className="watchlist-ops">
            <h3>{t('Workspace')}</h3>
            <button className="summary-link" onClick={() => navigate('data')}>
              <span>{t('Data library')}</span>
              <ArrowUpRight size={14} />
            </button>
            <button className="summary-link" onClick={() => navigate('operations')}>
              <span>
                {t('Feeds')} / {t('Jobs')}
              </span>
              <ArrowUpRight size={14} />
            </button>
            <button className="summary-link" onClick={() => navigate('execution')}>
              <span>{t('Positions')}</span>
              <strong>{account.data?.positions?.length ?? '—'}</strong>
            </button>
          </div>
        </aside>
      </div>
      <div className="overview-lower">
        <section className="recent-section">
          <div className="section-heading">
            <div>
              <h2>{t('Research runs')}</h2>
              <p className="section-description">{t('Run history')}</p>
            </div>
            <button className="text-button" onClick={() => navigate('research')}>
              {t('Research')}
              <ArrowRight size={14} />
            </button>
          </div>
          {runs.isPending ? (
            <Loading />
          ) : runs.isError ? (
            <ErrorBox error={runs.error} onRetry={() => void runs.refetch()} />
          ) : sourceRuns.length ? (
            <div className="recent-runs">
              {sourceRuns.slice(0, 3).map((r) => (
                <button key={r.id} onClick={() => navigate('research')}>
                  <span className="run-icon">
                    <FlaskConical size={17} />
                  </span>
                  <span className="recent-run-name">
                    <strong>{nameOf(r.config.strategy.kind)}</strong>
                    <small>
                      {r.config.direction} · {r.config.mode} · {date(r.created_at)}
                    </small>
                  </span>
                  <Status
                    type={
                      r.status === 'completed' ? 'good' : r.status === 'failed' ? 'bad' : 'neutral'
                    }
                  >
                    {r.status}
                  </Status>
                  <span className={tone(runReturn(r))}>
                    {r.result ? percent(runReturn(r)) : '—'}
                  </span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          ) : (
            <div className="recent-empty">
              <FlaskConical size={23} strokeWidth={1.4} />
              <div>
                <strong>{t('No research runs')}</strong>
                <p>{t('Choose an immutable dataset and submit a research configuration.')}</p>
              </div>
              <button className="text-button" onClick={() => navigate('research')}>
                {t('Start research')}
                <ArrowRight size={14} />
              </button>
            </div>
          )}
        </section>
        <section className="workspace-summary">
          <div className="section-heading">
            <h2>{t('Portfolio')}</h2>
            <span className="subtle-tag">LOCAL PAPER</span>
          </div>
          <div className="summary-row">
            <span>{t('Account equity')}</span>
            <strong>
              {account.data ? number(account.data.equity) : '—'} <small>USDT</small>
            </strong>
          </div>
          <div className="summary-row">
            <span>{t('Research runs')}</span>
            <strong>{runs.data ? completed.length : '—'}</strong>
          </div>
          <div className="summary-row">
            <span>Market data</span>
            <SourceBadge source={source} />
          </div>
          <p className="quiet-copy">
            {t('Account and execution mode are reported by the server.')}
          </p>
        </section>
      </div>
    </>
  );
}
