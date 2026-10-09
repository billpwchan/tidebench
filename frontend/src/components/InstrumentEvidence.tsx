import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import type { Source } from '../api';
import { downloadBlob } from '../api';
import { proApi } from '../proApi';
import type { InstrumentMember, InstrumentObservation } from '../proApi';
import { useSession } from './AuthGate';
import { canResearch } from '../lib/permissions';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { Empty, ErrorBox, Field, Loading, Status } from './workspace';
import { DataTable, JsonDetails, valueText } from './ProWorkspace';

const coverageLabels: Record<string, string> = {
  point_observation: 'Observed at this time',
  bounded_carry_forward_assumption: 'Carried forward within chosen age',
  unknown: 'Unknown coverage',
};
const reasonLabels: Record<string, string> = {
  missing_tickSz: 'Missing tick size',
  missing_lotSz: 'Missing lot size',
  missing_minSz: 'Missing minimum size',
  missing_ctType: 'Missing contract type',
  missing_settleCcy: 'Missing settlement currency',
  missing_ctVal: 'Missing contract face value',
  missing_ctMult: 'Missing contract multiplier',
  missing_ctValCcy: 'Missing face-value currency',
  outside_supported_usdt_scope: 'Outside supported USDT scope',
  duplicate_instrument: 'Conflicting instrument rows',
  unsupported_settlement_or_contract: 'Unsupported settlement or contract',
  unsupported_contract_units: 'Unsupported contract units',
  invalid_upstream_data: 'Invalid source rule',
  invalid_row: 'Invalid source row',
  before_announced_listing: 'Before announced listing',
  at_or_after_announced_expiry: 'At or after announced expiry',
  observation_stale: 'Observation too old',
  state_preopen: 'Not yet open',
  state_suspend: 'Suspended',
  state_rebase: 'Rebase in progress',
};
const utcInput = (ts: number) => new Date(ts).toISOString().slice(0, 23);
export default function InstrumentEvidence({ source }: { source: Source }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canResearch(useSession()?.user?.role);
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [selected, setSelected] = useState('');
  const [asOf, setAsOf] = useState(() => utcInput(Date.now()));
  const [maxAge, setMaxAge] = useState(3600000);
  const [query, setQuery] = useState<{ time: number; age: number }>();
  const [validation, setValidation] = useState('');
  const [search, setSearch] = useState('');
  const [row, setRow] = useState<InstrumentMember>();
  const observations = useQuery({
    queryKey: ['instrument-observations', source, product],
    queryFn: () => proApi.instrumentObservations(source, product),
  });
  const items = observations.data?.items ?? [];
  const active = selected || items[0]?.id;
  const detail = useQuery({
    queryKey: ['instrument-observation', active],
    queryFn: () => proApi.instrumentObservation(active!),
    enabled: !!active,
  });
  const universe = useQuery({
    queryKey: ['instrument-universe', source, product, query],
    queryFn: () => proApi.instrumentUniverse(source, product, query!.time, query!.age),
    enabled: !!query,
  });
  const reviewed = query ? universe.data?.observation : detail.data;
  const members = (query ? universe.data?.members : detail.data?.members) ?? [];
  const previous = items[items.findIndex((item) => item.id === active) + 1]?.id;
  const changes = useQuery({
    queryKey: ['instrument-diff', active, previous],
    queryFn: () => proApi.instrumentDiff(active!, previous!),
    enabled: !!active && !!previous && !query,
  });
  const capture = useMutation({
    mutationFn: () => proApi.captureInstruments(source, product),
    onSuccess: async (snapshot) => {
      const key = ['instrument-observations', source, product];
      // An older in-flight list must not erase the capture the operator selected.
      await qc.cancelQueries({ queryKey: key });
      qc.setQueryData<{ items: InstrumentObservation[] }>(key, (prior) => ({
        items: [snapshot, ...(prior?.items ?? []).filter((item) => item.id !== snapshot.id)].slice(
          0,
          100,
        ),
      }));
      qc.setQueryData(['instrument-observation', snapshot.id], snapshot);
      setSelected(snapshot.id);
      setAsOf(utcInput(snapshot.received_at));
      setQuery(undefined);
      setRow(undefined);
      await qc.invalidateQueries({ queryKey: ['instrument-observations', source, product] });
    },
  });
  const exportSnapshot = useMutation({
    mutationFn: () => proApi.exportInstrumentObservation(reviewed!.id),
    onSuccess: (snapshot) =>
      downloadBlob(
        new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' }),
        `instrument-observation-${snapshot.observation.id}.json`,
      ),
  });
  const rawReview = useQuery({
    queryKey: ['instrument-observation', reviewed?.id],
    queryFn: () => proApi.instrumentObservation(reviewed!.id),
    enabled: !!reviewed?.id && !!row,
  });
  const visible = members.filter((m) =>
    `${m.inst_id ?? ''} ${m.state ?? ''} ${m.eligibility} ${m.reasons.join(' ')}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const selectSnapshot = (id: string) => {
    setSelected(id);
    setQuery(undefined);
    setRow(undefined);
    setValidation('');
    const snapshot = items.find((item) => item.id === id);
    if (snapshot) setAsOf(utcInput(snapshot.received_at));
  };
  return (
    <section className="pro-panel instrument-evidence" aria-label={t('Instrument evidence')}>
      <div className="section-heading">
        <div>
          <h2>{t('Instrument observations')}</h2>
          <p className="quiet-copy">
            {t(
              'Preserve what the endpoint returned, when it became known and which rules remain unavailable.',
            )}
          </p>
        </div>
        <button
          className="button button-secondary"
          disabled={!canOperate || capture.isPending}
          onClick={() => capture.mutate()}
        >
          <RefreshCw size={14} />
          {t(capture.isPending ? 'Capturing observation…' : 'Capture current observation')}
        </button>
      </div>
      <p className="muted-copy">
        {t(
          'Forward observations do not reconstruct a complete historical trading universe. Absence does not prove delisting or supply a settlement price.',
        )}
      </p>
      <div className="instrument-controls">
        <Field label="Instrument product">
          <select
            value={product}
            disabled={capture.isPending}
            onChange={(e) => {
              setProduct(e.target.value as 'SPOT' | 'SWAP');
              setSelected('');
              setQuery(undefined);
              setRow(undefined);
              capture.reset();
            }}
          >
            <option value="SPOT">{t('Spot')}</option>
            <option value="SWAP">{t('USDT perpetual')}</option>
          </select>
        </Field>
        <Field
          label="Stored observation"
          hint="Latest 100 observations; time review searches all retained observations."
        >
          <select
            value={active ?? ''}
            disabled={!items.length || capture.isPending}
            onChange={(e) => selectSnapshot(e.target.value)}
          >
            {!items.length && <option value="">{t('No observations yet')}</option>}
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {date(item.received_at, true)} · {item.id.slice(0, 8)} · {item.row_count}{' '}
                {t('Rows')}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {capture.isError && <ErrorBox error={capture.error} />}
      {observations.isError && <ErrorBox error={observations.error} />}
      <form
        className="instrument-controls instrument-time-review"
        onSubmit={(e) => {
          e.preventDefault();
          const time = Date.parse(`${asOf}Z`);
          if (!Number.isFinite(time) || time <= 0) {
            setValidation('Choose a valid UTC review time.');
            return;
          }
          setValidation('');
          setQuery({ time, age: maxAge });
          setRow(undefined);
        }}
      >
        <Field label="Review time (UTC)">
          <input
            type="datetime-local"
            step="0.001"
            disabled={capture.isPending}
            required
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
          />
        </Field>
        <Field label="Maximum observation age">
          <select
            value={maxAge}
            disabled={capture.isPending}
            onChange={(e) => setMaxAge(Number(e.target.value))}
          >
            <option value={0}>{t('Exact observed time only')}</option>
            <option value={300000}>{t('5 minutes')}</option>
            <option value={3600000}>{t('1 hour')}</option>
            <option value={86400000}>{t('24 hours')}</option>
          </select>
        </Field>
        <button type="submit" className="button button-secondary" disabled={capture.isPending}>
          {t('Review known information')}
        </button>
        {query && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setQuery(undefined);
              setRow(undefined);
            }}
          >
            {t('Return to stored observation')}
          </button>
        )}
      </form>
      {validation && (
        <p className="inline-error" role="alert">
          {t(validation)}
        </p>
      )}
      {query && universe.isError && <ErrorBox error={universe.error} />}
      {(query && universe.isPending) ||
      (!query && (observations.isPending || (!!active && detail.isPending))) ? (
        <Loading />
      ) : query && !universe.data?.observation ? (
        <Empty title="No prior observation">
          {t(
            'No retained snapshot was known at this time. Older listing timestamps do not establish historical coverage.',
          )}
        </Empty>
      ) : !active && !query ? (
        <Empty title="No observations yet">
          {t(
            'Capture a current response or prepare a research package to begin retaining forward evidence.',
          )}
        </Empty>
      ) : (
        <>
          {!query && detail.isError && <ErrorBox error={detail.error} />}
          {reviewed && (
            <div className="instrument-summary">
              <div>
                <span className="eyebrow">{t('Evidence known at')}</span>
                <strong>{new Date(reviewed.received_at).toISOString()}</strong>
                <small>
                  {reviewed.region} · {reviewed.inst_type} · {number(reviewed.row_count, 0)}{' '}
                  {t('Raw rows')} · {number(reviewed.supported_count, 0)}{' '}
                  {t('Within supported scope')}
                </small>
              </div>
              <Status type={query && universe.data?.coverage === 'unknown' ? 'warning' : 'neutral'}>
                {t(
                  coverageLabels[
                    query ? (universe.data?.coverage ?? 'unknown') : 'point_observation'
                  ],
                )}
              </Status>
              <button
                className="text-button"
                disabled={exportSnapshot.isPending}
                onClick={() => exportSnapshot.mutate()}
              >
                <Download size={13} />
                {t('Export original observation')}
              </button>
            </div>
          )}
          {query && universe.data?.coverage === 'bounded_carry_forward_assumption' && (
            <p className="quiet-copy">
              {t(
                'Eligibility carries the last observed information forward within your chosen age. It does not prove continuous tradability between observations.',
              )}
            </p>
          )}
          {query && universe.data?.coverage === 'unknown' && (
            <p className="inline-error" role="status">
              {t(
                'The last observation is older than the allowed age. Every member remains unknown.',
              )}
            </p>
          )}
          {exportSnapshot.isError && <ErrorBox error={exportSnapshot.error} />}
          <Field label="Filter observed instruments">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('Market, state or missing rule')}
            />
          </Field>
          <DataTable
            rows={visible}
            rowKey={(m) => String(m.row)}
            columns={[
              {
                key: 'inst_id',
                label: 'Market',
                render: (m) => <strong>{m.inst_id ?? t('Invalid row')}</strong>,
              },
              { key: 'state', label: 'Observed state', render: (m) => valueText(m.state) },
              {
                key: 'eligibility',
                label: 'Eligibility',
                render: (m) => (
                  <Status
                    type={
                      m.eligibility === 'eligible'
                        ? 'good'
                        : m.eligibility === 'ineligible'
                          ? 'bad'
                          : 'warning'
                    }
                  >
                    {m.eligibility === 'eligible'
                      ? 'Eligible'
                      : m.eligibility === 'ineligible'
                        ? 'Ineligible'
                        : 'Unknown'}
                  </Status>
                ),
              },
              {
                key: 'rules',
                label: 'Lot / minimum / tick',
                render: (m) => (
                  <span>
                    {m.metadata
                      ? ['lot_size', 'min_size', 'tick_size']
                          .map((k) => valueText(m.metadata?.[k]))
                          .join(' / ')
                      : '—'}
                  </span>
                ),
              },
              {
                key: 'list_time',
                label: 'Announced listing',
                render: (m) => date(m.list_time, true),
              },
              {
                key: 'expiry_time',
                label: 'Announced expiry',
                render: (m) => date(m.expiry_time, true),
              },
              {
                key: 'reasons',
                label: 'Availability',
                render: (m) =>
                  m.reasons.map((reason) => t(reasonLabels[reason] ?? reason)).join(', ') ||
                  t('Rules available'),
              },
              {
                key: 'inspect',
                label: 'Actions',
                render: (m) => (
                  <button className="text-button" onClick={() => setRow(m)}>
                    {t('Inspect raw row')}
                  </button>
                ),
              },
            ]}
            empty="No matching observed instruments"
          />
          {row && (
            <div className="instrument-row-review">
              <div className="section-heading">
                <h3>{row.inst_id ?? t('Invalid row')}</h3>
                <button className="text-button" onClick={() => setRow(undefined)}>
                  {t('Close')}
                </button>
              </div>
              {rawReview.isPending ? (
                <Loading />
              ) : rawReview.isError ? (
                <ErrorBox error={rawReview.error} />
              ) : (
                <JsonDetails
                  label="Raw instrument row"
                  open
                  value={{ raw: rawReview.data?.rows?.[row.row], interpretation: row }}
                />
              )}
            </div>
          )}
          {!query && previous && (
            <div className="instrument-changes">
              <h3>{t('Changes since previous observation')}</h3>
              <p className="quiet-copy">
                {t(
                  'First observed and not observed describe these two responses. Neither establishes an original listing or delisting event.',
                )}
              </p>
              {changes.isPending ? (
                <Loading />
              ) : changes.isError ? (
                <ErrorBox error={changes.error} />
              ) : (
                <DataTable
                  rows={changes.data?.changes ?? []}
                  columns={[
                    { key: 'inst_id', label: 'Market' },
                    {
                      key: 'change',
                      label: 'Observation change',
                      render: (m) =>
                        t(
                          m.change === 'first_observed'
                            ? 'First observed'
                            : m.change === 'not_observed'
                              ? 'Not observed'
                              : 'Changed',
                        ),
                    },
                    {
                      key: 'fields',
                      label: 'Changed fields',
                      render: (m) => (Array.isArray(m.fields) ? m.fields.join(', ') : '—'),
                    },
                  ]}
                  empty="No changes in the observed payload"
                />
              )}
            </div>
          )}
          {reviewed && (
            <JsonDetails
              label="Observation identity and coverage"
              value={{
                observation_id: reviewed.id,
                payload_hash: reviewed.payload_hash,
                content_hash: reviewed.content_hash,
                parser_version: reviewed.parser_version,
                transport: reviewed.transport,
                counts: reviewed.counts,
                as_of: query?.time,
                max_age_ms: query?.age,
                historical_completeness: false,
                absence_policy: 'unknown_not_delisted',
              }}
            />
          )}
        </>
      )}
    </section>
  );
}
