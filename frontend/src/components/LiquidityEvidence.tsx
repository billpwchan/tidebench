import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import { proApi, type LiquidityCalibration, type RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { canResearch } from '../lib/permissions';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Loading, Status } from './workspace';

const markets = [
  'BTC-USDT',
  'ETH-USDT',
  'SOL-USDT',
  'BTC-USDT-SWAP',
  'ETH-USDT-SWAP',
  'SOL-USDT-SWAP',
];
const money = (v: unknown) => (v === null || v === undefined ? '—' : `${number(v, 0)} USDT`);
const basis =
  'Cached displayed depth is an observation, not a guaranteed fill. Fees, queue position, replenishment and market impact are excluded. Historical research costs remain scenarios.';
const statuses: Record<string, string> = {
  no_samples: 'No depth observations',
  unsupported: 'Unsupported evidence',
  clock_ahead: 'Exchange clock ahead',
  insufficient_evidence: 'Insufficient observations',
  observational_pass: 'Observed window passed',
  exceeds_limits: 'Observed costs exceed limits',
  stale: 'Evidence is stale',
  supported: 'Book evaluated',
  complete: 'Displayed depth covers size',
  depth_exhausted: 'Displayed depth exhausted',
  no_causal_capture: 'No prior fresh capture',
  matched: 'Prior capture matched',
};

function CalibrationDetail({ report }: { report: LiquidityCalibration }) {
  const { t } = useI18n();
  const edges = report.uncovered_edges as { start_ms: number | null; end_ms: number | null };
  return (
    <section className="liquidity-report">
      <div className="section-heading">
        <h3>{t('Frozen cost and depth review')}</h3>
        <Status type="neutral">
          {t(statuses[report.current_review_status ?? report.status] ?? report.status)}
        </Status>
      </div>
      <RecordGrid
        value={{
          [t('Observed window')]:
            report.observed_window.start === null
              ? '—'
              : `${date(report.observed_window.start, true)} → ${date(report.observed_window.end!, true)}`,
          [t('Window captures / selected preview')]:
            `${report.selection_audit.all_available_count} / ${report.selection_audit.selected_count}`,
          [t('Additional captures included')]: report.selection_audit.omitted_capture_ids.length,
          [t('Independent book observations')]:
            `${report.independent_book_count} / ${report.input.minimum_samples}`,
          [t('Unobserved window start / end (seconds)')]:
            `${number(edges.start_ms === null ? null : edges.start_ms / 1000)} / ${number(edges.end_ms === null ? null : edges.end_ms / 1000)}`,
          [t('Independent elapsed time')]:
            `${number(report.independent_window.elapsed_ms / 60000)} ${t('minutes')}`,
          [t('Largest observed passing size')]: money(report.approved_observed_notional),
          [t('Declared child / sleeve')]:
            `${money(report.declared_child_notional)} / ${money(report.declared_sleeve_notional)}`,
          [t('Historical capacity')]: t('Unknown'),
        }}
      />
      <p className="quiet-copy">
        {t(
          'Approval is limited to every selected snapshot and both sides in this observed window. A stale report cannot establish current liquidity. Quantiles describe this sample; they do not prove a live execution model.',
        )}
      </p>
      <DataTable
        rows={report.scenarios}
        columns={[
          { key: 'notional', label: 'Scenario notional', render: (r) => money(r.notional) },
          {
            key: 'pass',
            label: 'Every sample, both sides',
            render: (r) => t(r.all_samples_pass ? 'Passed' : 'Not passed'),
          },
          {
            key: 'buy',
            label: 'Buy worst / P95 (bps)',
            render: (r) =>
              `${number(r.sides.buy.shortfall_bps.worst)} / ${number(r.sides.buy.shortfall_bps.p95)}`,
          },
          {
            key: 'sell',
            label: 'Sell worst / P95 (bps)',
            render: (r) =>
              `${number(r.sides.sell.shortfall_bps.worst)} / ${number(r.sides.sell.shortfall_bps.p95)}`,
          },
          {
            key: 'participation',
            label: 'Buy / sell worst depth share (%)',
            render: (r) =>
              `${number(r.sides.buy.participation_pct.worst)} / ${number(r.sides.sell.participation_pct.worst)}`,
          },
        ]}
      />
      <JsonDetails label="Frozen assumptions, captures and hashes" value={report} />
    </section>
  );
}

export default function LiquidityEvidencePanel() {
  const { t } = useI18n();
  const allowed = canResearch(useSession()?.user?.role);
  const qc = useQueryClient();
  const [market, setMarket] = useState('BTC-USDT');
  const [windowMinutes, setWindowMinutes] = useState('30');
  const [shortfall, setShortfall] = useState('10');
  const [participation, setParticipation] = useState('10');
  const [reportId, setReportId] = useState<string | null>(null);
  const [rawId, setRawId] = useState<string | null>(null);
  const [paperOpen, setPaperOpen] = useState(false);
  const captures = useQuery({
    queryKey: ['liquidity-captures', market],
    queryFn: () => proApi.liquidityCaptures(market),
    refetchInterval: 15000,
  });
  const reports = useQuery({
    queryKey: ['liquidity-reports', market],
    queryFn: () => proApi.liquidityCalibrations(market),
    refetchInterval: 15000,
  });
  const raw = useQuery({
    queryKey: ['liquidity-raw', rawId],
    queryFn: () => proApi.liquidityCapture(rawId!),
    enabled: !!rawId,
  });
  const paper = useQuery({
    queryKey: ['liquidity-paper', market],
    queryFn: () => proApi.liquidityPaperComparison(market),
    enabled: paperOpen,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['liquidity-captures', market] });
    void qc.invalidateQueries({ queryKey: ['liquidity-reports', market] });
  };
  const capture = useMutation({
    mutationFn: () => proApi.captureLiquidity(market),
    onSuccess: () => {
      setRawId(null);
      refresh();
    },
  });
  const minutes = Number(windowMinutes);
  const included = useMemo(
    () =>
      (captures.data?.items ?? []).filter(
        (c) =>
          Number.isFinite(minutes) &&
          minutes > 0 &&
          c.known_at >= Date.now() - minutes * 60000 &&
          c.known_at <= Date.now(),
      ),
    [captures.data, minutes],
  );
  const freeze = useMutation({
    mutationFn: () =>
      proApi.calibrateLiquidity({
        inst_id: market,
        capture_ids: included.map((c) => c.id),
        max_shortfall_bps: shortfall,
        max_participation_pct: participation,
        window_start: Date.now() - minutes * 60000,
        window_end: Date.now(),
      }),
    onSuccess: (r) => {
      setReportId(r.id);
      refresh();
    },
  });
  const latest = captures.data?.items[0];
  const report = reports.data?.items.find((r) => r.id === reportId) ?? reports.data?.items[0];
  const valid =
    Number(shortfall) > 0 &&
    Number(shortfall) <= 1000 &&
    Number(participation) > 0 &&
    Number(participation) <= 100;
  return (
    <div className="liquidity-workspace">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('PUBLIC OKX · DISPLAYED L2')}</p>
          <h2>{t('Cost and capacity evidence')}</h2>
        </div>
        <button className="button button-secondary" onClick={refresh}>
          <RefreshCw size={14} />
          {t('Refresh')}
        </button>
      </div>
      <p className="quiet-copy">{t(basis)}</p>
      <div className="liquidity-controls">
        <Field label="Observed market">
          <select
            value={market}
            onChange={(e) => {
              setMarket(e.target.value);
              setReportId(null);
              setRawId(null);
              freeze.reset();
              capture.reset();
            }}
          >
            {markets.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </Field>
        <button
          className="button button-primary"
          disabled={!allowed || capture.isPending}
          onClick={() => capture.mutate()}
        >
          <Download size={14} />
          {t(capture.isPending ? 'Capturing public book…' : 'Capture public book')}
        </button>
      </div>
      {(captures.isError || reports.isError || capture.isError || freeze.isError) && (
        <ErrorBox error={captures.error ?? reports.error ?? capture.error ?? freeze.error} />
      )}
      {captures.isPending ? (
        <Loading />
      ) : latest ? (
        <section className="liquidity-current">
          <div className="section-heading">
            <h3>{t('Latest immutable observation')}</h3>
            <Status type="neutral">
              {t(statuses[latest.evidence.status] ?? latest.evidence.status)}
            </Status>
          </div>
          <RecordGrid
            value={{
              [t('Captured')]: date(latest.received_at, true),
              [t('Exchange book age at capture (ms)')]: latest.evidence.book_age_at_capture_ms,
              [t('Current sample age (seconds)')]: number(latest.current_age_ms! / 1000),
              [t('Mid price')]: latest.evidence.mid,
              [t('Spread (bps)')]: latest.evidence.spread_bps,
              [t('Quantity unit')]: t(
                latest.evidence.metadata?.quantity_unit === 'base_asset'
                  ? 'Base asset units'
                  : latest.evidence.metadata?.quantity_unit === 'contracts'
                    ? 'Contract count'
                    : 'Unknown',
              ),
            }}
          />
          {latest.evidence.reason && (
            <p className="action-note action-note-error">{latest.evidence.reason}</p>
          )}
          <DataTable
            rows={latest.evidence.scenarios}
            columns={[
              {
                key: 'requested_notional',
                label: 'Scenario notional',
                render: (r) => money(r.requested_notional),
              },
              { key: 'side', label: 'Side', render: (r) => t(r.side === 'buy' ? 'Buy' : 'Sell') },
              {
                key: 'status',
                label: 'Displayed coverage',
                render: (r) => t(statuses[r.status] ?? r.status),
              },
              { key: 'vwap', label: 'Walking VWAP', render: (r) => number(r.vwap, 6) },
              {
                key: 'shortfall_bps',
                label: 'Mid shortfall (bps)',
                render: (r) => number(r.shortfall_bps),
              },
              {
                key: 'participation_pct',
                label: 'Captured side depth share (%)',
                render: (r) => number(r.participation_pct),
              },
              {
                key: 'unfilled_quantity',
                label: 'Unfilled quantity',
                render: (r) => number(r.unfilled_quantity, 6),
              },
            ]}
          />
          <button className="text-button" onClick={() => setRawId(rawId ? null : latest.id)}>
            {t(rawId ? 'Close raw depth evidence' : 'Inspect raw depth and metadata')}
          </button>
          {rawId &&
            (raw.isPending ? (
              <Loading />
            ) : raw.isError ? (
              <ErrorBox error={raw.error} />
            ) : (
              <JsonDetails value={raw.data} label="Canonical data and content hashes" open />
            ))}
        </section>
      ) : (
        <p className="quiet-copy">
          {t(
            'No public depth has been captured for this market. Capture an observation to inspect real size and cost constraints.',
          )}
        </p>
      )}
      <section className="liquidity-freeze">
        <h3>{t('Freeze an observed window')}</h3>
        <p className="quiet-copy">
          {t(
            'The baseline requires 12 independent book timestamps, at least five minutes, gaps no longer than one minute and book age at capture no greater than two seconds. Cached timestamps never create extra observations.',
          )}
        </p>
        <div className="liquidity-controls">
          <Field label="Lookback window (minutes)">
            <input
              type="number"
              min="5"
              max="1440"
              value={windowMinutes}
              onChange={(e) => setWindowMinutes(e.target.value)}
            />
          </Field>
          <Field label="Maximum mid shortfall (bps)">
            <input
              type="number"
              min="0.01"
              max="1000"
              step="0.01"
              value={shortfall}
              onChange={(e) => setShortfall(e.target.value)}
            />
          </Field>
          <Field label="Maximum captured depth share (%)">
            <input
              type="number"
              min="0.01"
              max="100"
              step="0.01"
              value={participation}
              onChange={(e) => setParticipation(e.target.value)}
            />
          </Field>
          <button
            className="button button-secondary"
            disabled={
              !allowed ||
              !valid ||
              !Number.isFinite(minutes) ||
              minutes < 5 ||
              minutes > 1440 ||
              freeze.isPending
            }
            onClick={() => freeze.mutate()}
          >
            {t(freeze.isPending ? 'Freezing evidence…' : 'Freeze cost and depth review')}
          </button>
        </div>
        <p className="quiet-copy">
          {t('Selected captures')} · {included.length} / {captures.data?.items.length ?? 0} ·{' '}
          {t(
            'Preview shows the latest 100. The frozen review includes every available capture inside the declared window; additional captures are recorded in the selection audit.',
          )}
        </p>
      </section>
      {report && <CalibrationDetail report={report} />}
      {(reports.data?.items.length ?? 0) > 1 && (
        <Field label="Previous frozen review">
          <select value={report?.id} onChange={(e) => setReportId(e.target.value)}>
            {reports.data!.items.map((r) => (
              <option key={r.id} value={r.id}>
                {date(r.created_at, true)} ·{' '}
                {t(statuses[r.current_review_status ?? r.status] ?? r.status)} · {r.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </Field>
      )}
      <section className="liquidity-paper">
        <button className="text-button" onClick={() => setPaperOpen((v) => !v)}>
          {t(paperOpen ? 'Close local paper comparison' : 'Compare local paper fills')}
        </button>
        {paperOpen && (
          <>
            <p className="quiet-copy">
              {t(
                'Only synthetic local paper orders are compared with a capture already known before their fill and within two seconds. Model-minus-walk includes intervening price movement. Missing causal captures remain unmatched; these observations are not live execution validation.',
              )}
            </p>
            {paper.isPending ? (
              <Loading />
            ) : paper.isError ? (
              <ErrorBox error={paper.error} />
            ) : (
              <DataTable
                rows={paper.data?.items ?? []}
                columns={[
                  {
                    key: 'order_id',
                    label: 'Order reference',
                    render: (r) => String(r.order_id).slice(0, 10),
                  },
                  {
                    key: 'filled_at',
                    label: 'Local paper filled',
                    render: (r) => date(Number(r.filled_at), true),
                  },
                  {
                    key: 'status',
                    label: 'Evidence status',
                    render: (r) => t(statuses[String(r.status)] ?? String(r.status)),
                  },
                  {
                    key: 'paper_shortfall_bps',
                    label: 'Paper versus prior mid (bps)',
                    render: (r) => number(r.paper_shortfall_bps),
                  },
                  {
                    key: 'model_minus_walk_bps',
                    label: 'Paper minus displayed walk (bps)',
                    render: (r) => number(r.model_minus_walk_bps),
                  },
                ]}
                empty="No local paper fills in this market"
              />
            )}
          </>
        )}
      </section>
    </div>
  );
}

export function LiquidityReleaseEvidence({
  symbols,
  source,
  review,
}: {
  symbols: string[];
  source: string;
  review?: RecordData;
}) {
  const { t } = useI18n();
  const pins = (Array.isArray(review?.reports) ? review.reports : []) as RecordData[];
  const pinIdentity = pins.map((p) => `${p.id}:${p.content_hash}`).join(',');
  const reports = useQuery({
    queryKey: [
      'liquidity-release-reports',
      source,
      symbols.join(','),
      review ? pinIdentity : 'unbound',
    ],
    enabled: source === 'okx',
    queryFn: () =>
      Promise.all(
        symbols.map(async (symbol) => {
          const pin = pins.find((p) => p.inst_id === symbol);
          if (review && !pin) return { symbol, report: undefined, pin: undefined };
          const report = pin
            ? await proApi.liquidityCalibration(String(pin.id))
            : (await proApi.liquidityCalibrations(symbol, 1)).items[0];
          if (
            pin &&
            (!report || report.content_hash !== pin.content_hash || report.inst_id !== symbol)
          )
            throw new Error(t('Pinned liquidity review failed content verification.'));
          return { symbol, report, pin };
        }),
      ),
  });
  if (source !== 'okx')
    return (
      <p className="quiet-copy">
        {t(
          'Example-source research has no OKX liquidity calibration. Public OKX depth reviews do not certify synthetic market fills.',
        )}
      </p>
    );
  return (
    <section className="liquidity-release-evidence">
      <h3>{t('Observed execution cost and depth')}</h3>
      <p className="quiet-copy">{t(basis)}</p>
      <p className="quiet-copy">
        {t(
          review
            ? 'These immutable reports are pinned to this release review. Current freshness is shown separately from the reviewed snapshot.'
            : 'This legacy preview has no pinned liquidity reports. Latest evidence is unbound information and is not part of its approval.',
        )}
      </p>
      {reports.isPending ? (
        <Loading />
      ) : reports.isError ? (
        <ErrorBox error={reports.error} />
      ) : (
        <DataTable
          rows={
            (reports.data ?? []) as (RecordData & {
              symbol: string;
              report?: LiquidityCalibration;
              pin?: RecordData;
            })[]
          }
          columns={[
            { key: 'symbol', label: 'Market' },
            {
              key: 'frozen_status',
              label: 'Status at release review',
              render: (r) =>
                r.pin
                  ? t(statuses[String(r.pin.status_at_freeze)] ?? String(r.pin.status_at_freeze))
                  : t(review ? 'No pinned review' : 'Unbound latest evidence'),
            },
            {
              key: 'status',
              label: 'Current evidence status',
              render: (r) =>
                t(statuses[r.report?.current_review_status ?? r.report?.status ?? 'no_samples']),
            },
            {
              key: 'size',
              label: 'Largest observed passing size',
              render: (r) => money(r.report?.approved_observed_notional),
            },
            {
              key: 'count',
              label: 'Independent book observations',
              render: (r) => r.report?.independent_book_count ?? '—',
            },
            {
              key: 'window',
              label: 'Last observed',
              render: (r) =>
                r.report?.observed_window.end ? date(r.report.observed_window.end, true) : '—',
            },
            {
              key: 'report',
              label: 'Immutable review',
              render: (r) =>
                r.report ? <JsonDetails value={r.report} label="Inspect evidence" /> : '—',
            },
          ]}
        />
      )}
    </section>
  );
}
