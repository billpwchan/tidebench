import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Database, Download, Loader2, RefreshCw, Square, X } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { Dataset, Job, ResearchInputs } from '../proApi';
import { useSession } from '../components/AuthGate';
import { canResearch } from '../lib/permissions';
import DatasetImport from '../components/DatasetImport';
import ResearchPackages from '../components/ResearchPackages';
import { useI18n } from '../lib/i18n';
import { bars } from '../lib/config';
import { useDialogFocus } from '../lib/hooks';
import { date, number } from '../lib/format';
import {
  ActionNote,
  Empty,
  ErrorBox,
  Field,
  Loading,
  PageHeading,
  Status,
} from '../components/workspace';
import {
  DataTable,
  JsonDetails,
  ProductSymbol,
  RecordGrid,
  valueText,
  WorkspaceTabs,
} from '../components/ProWorkspace';

const utcInput = (time: number) => new Date(time).toISOString().slice(0, 16);
const datasetRows = (d: Dataset) =>
  d.quality?.records ?? d.rows ?? d.count ?? d.candle_count ?? d.row_count;
const datasetStart = (d: Dataset) => Number(d.start ?? d.start_ts ?? d.first_ts ?? 0);
const datasetEnd = (d: Dataset) => Number(d.end ?? d.end_ts ?? d.last_ts ?? 0);
export default function DataLibrary({
  source,
  onResearch,
}: {
  source: Source;
  onResearch: (inputs: ResearchInputs) => void;
}) {
  const { t } = useI18n();
  const canOperate = canResearch(useSession()?.user?.role);
  const qc = useQueryClient();
  const [catalogTab, setCatalogTab] = useState('packages');
  const [inputTab, setInputTab] = useState('download');
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [kind, setKind] = useState('trade');
  const [bar, setBar] = useState('1H');
  const [start, setStart] = useState(() =>
    utcInput(Math.floor(Date.now() / 3600000) * 3600000 - 30 * 86400000),
  );
  const [end, setEnd] = useState(() => utcInput(Math.floor(Date.now() / 3600000) * 3600000));
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  useDialogFocus(!!selected, '.dataset-dialog', () => setSelected(null));
  const datasets = useQuery({
    queryKey: ['pro-datasets'],
    queryFn: proApi.datasets,
    refetchInterval: 15000,
  });
  const jobs = useQuery({
    queryKey: ['pro-jobs'],
    queryFn: proApi.jobs,
    refetchInterval: (q) =>
      q.state.data?.items.some((j) => ['queued', 'running', 'cancelling'].includes(j.status))
        ? 1500
        : 10000,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['pro-jobs'] });
    void qc.invalidateQueries({ queryKey: ['pro-datasets'] });
  };
  const create = useMutation({
    mutationFn: proApi.createJob,
    onSuccess: () => {
      setNotice(t('Job created'));
      refresh();
    },
  });
  const cancel = useMutation({ mutationFn: proApi.cancelJob, onSuccess: refresh });
  const visibleDatasets = datasets.data?.items.filter((d) => d.source === source) ?? [];
  const visibleJobs = jobs.data?.items.filter((j) => !j.source || j.source === source) ?? [];
  const submit = () => {
    const startTs = Date.parse(`${start}Z`),
      endTs = Date.parse(`${end}Z`);
    if (!Number.isFinite(startTs) || !Number.isFinite(endTs) || endTs <= startTs) {
      setValidation(t('End time must be after start time.'));
      return;
    }
    const interval = (
      {
        '1m': 60000,
        '5m': 300000,
        '15m': 900000,
        '1H': 3600000,
        '4H': 14400000,
        '1Dutc': 86400000,
      } as Record<string, number>
    )[bar];
    if (kind !== 'funding' && (startTs % interval || endTs % interval)) {
      setValidation(t('Range boundaries must align to the selected UTC interval.'));
      return;
    }
    setValidation(null);
    create.mutate({ source, inst_id: symbol, kind, bar, start: startTs, end: endTs });
  };
  return (
    <>
      <PageHeading
        eyebrow="DATA CATALOG"
        title="Data library"
        description="Versioned market inputs, download jobs, and measured data quality."
      >
        <button className="button button-secondary" onClick={refresh}>
          <RefreshCw size={14} />
          {t('Refresh')}
        </button>
      </PageHeading>
      <WorkspaceTabs
        value={catalogTab}
        onChange={setCatalogTab}
        items={[
          { key: 'packages', label: 'Research packages' },
          { key: 'raw', label: 'Raw datasets & imports' },
        ]}
      />
      {catalogTab === 'packages' && (
        <ResearchPackages
          source={source}
          onResearch={onResearch}
          onOpenRaw={() => setCatalogTab('raw')}
        />
      )}
      {catalogTab === 'raw' && (
        <>
          <ActionNote text={notice} />
          <div className="data-library-layout">
            <section className="pro-panel data-catalog-panel">
              <div className="section-heading">
                <h2>{t('Dataset versions')}</h2>
                <span className="subtle-tag">
                  {visibleDatasets.length} {t('Dataset')}
                </span>
              </div>
              {datasets.isPending ? (
                <Loading />
              ) : datasets.isError ? (
                <ErrorBox error={datasets.error} onRetry={() => void datasets.refetch()} />
              ) : (
                <DataTable
                  rows={visibleDatasets}
                  empty="No datasets yet"
                  columns={[
                    {
                      key: 'inst_id',
                      label: 'Market',
                      render: (d) => (
                        <div className="table-stacked">
                          <strong>{d.inst_id}</strong>
                          <small>
                            {d.kind} · {d.bar}
                          </small>
                          <small>
                            {d.transport === 'user_import'
                              ? t('Imported')
                              : d.source === 'example'
                                ? t('Example · synthetic')
                                : 'OKX REST'}
                          </small>
                        </div>
                      ),
                    },
                    {
                      key: 'range',
                      label: 'Coverage',
                      render: (d) => (
                        <div className="table-stacked">
                          <span>{date(datasetStart(d), true)}</span>
                          <small>{date(datasetEnd(d), true)}</small>
                        </div>
                      ),
                    },
                    { key: 'rows', label: 'Rows', render: (d) => number(datasetRows(d), 0) },
                    {
                      key: 'quality',
                      label: 'Quality',
                      render: (d) => (
                        <Status type={d.quality?.complete === true ? 'good' : 'warning'}>
                          {d.quality?.complete === true ? 'complete' : 'incomplete'}
                        </Status>
                      ),
                    },
                    {
                      key: 'version',
                      label: 'Version',
                      render: (d) => (
                        <code>
                          {d.version ? `v${d.version} · ` : ''}
                          {String(
                            d.content_hash ?? d.dataset_hash ?? d.hash ?? d.version ?? d.id,
                          ).slice(0, 10)}
                        </code>
                      ),
                    },
                    {
                      key: 'actions',
                      label: 'Actions',
                      render: (d) => (
                        <div className="table-actions">
                          <button className="text-button" onClick={() => setSelected(d)}>
                            {t('Inspect')}
                          </button>
                          {d.kind === 'trade' && (
                            <button
                              className="text-button"
                              onClick={() => onResearch({ dataset_id: d.id })}
                            >
                              {t('Research')}
                              <ArrowRight size={12} />
                            </button>
                          )}
                        </div>
                      ),
                    },
                  ]}
                />
              )}
            </section>
            <aside className="pro-panel data-download-panel">
              <div className="section-heading">
                <h2>{t('Add market data')}</h2>
                <Download size={18} />
              </div>
              <WorkspaceTabs
                value={inputTab}
                onChange={setInputTab}
                items={[
                  { key: 'download', label: 'Download' },
                  { key: 'import', label: 'Import JSON' },
                ]}
              />
              {inputTab === 'download' ? (
                <form
                  className="compact-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    submit();
                  }}
                >
                  <ProductSymbol
                    source={source}
                    value={symbol}
                    onChange={setSymbol}
                    product={product}
                    onProductChange={(p) => {
                      setProduct(p);
                      if (p === 'SPOT') setKind('trade');
                    }}
                  />
                  <div className="form-grid">
                    <Field label="Kind">
                      <select value={kind} onChange={(e) => setKind(e.target.value)}>
                        <option value="trade">{t('Trade candles')}</option>
                        <option value="mark" disabled={product === 'SPOT'}>
                          {t('Mark price')}
                        </option>
                        <option value="index" disabled={product === 'SPOT'}>
                          {t('Index price')}
                        </option>
                        <option value="funding" disabled={product === 'SPOT'}>
                          {t('Funding rates')}
                        </option>
                      </select>
                    </Field>
                    <Field label="Interval">
                      <select
                        value={bar}
                        onChange={(e) => {
                          const b = e.target.value;
                          setBar(b);
                          const ms = (
                            {
                              '1m': 60000,
                              '5m': 300000,
                              '15m': 900000,
                              '1H': 3600000,
                              '4H': 14400000,
                              '1Dutc': 86400000,
                            } as Record<string, number>
                          )[b];
                          setStart((v) => utcInput(Math.floor(Date.parse(`${v}Z`) / ms) * ms));
                          setEnd((v) => utcInput(Math.floor(Date.parse(`${v}Z`) / ms) * ms));
                        }}
                      >
                        {['1m', '5m', ...bars].map((b) => (
                          <option key={b} value={b}>
                            {b}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  <Field label="Start (UTC)">
                    <input
                      required
                      type="datetime-local"
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                    />
                  </Field>
                  <Field label="End (UTC)">
                    <input
                      required
                      type="datetime-local"
                      value={end}
                      onChange={(e) => setEnd(e.target.value)}
                    />
                  </Field>
                  <p className="range-hint">
                    {t(
                      'Range is start-inclusive and end-exclusive. UTC boundaries must align to the selected interval.',
                    )}
                  </p>
                  <div className="download-source">
                    <Database size={13} />
                    <span>{source === 'okx' ? 'OKX public REST' : t('Example · synthetic')}</span>
                  </div>
                  {(create.isError || validation) && (
                    <ErrorBox error={validation ? new Error(validation) : create.error} />
                  )}
                  <button
                    type="submit"
                    className="button button-citrus full-width"
                    disabled={!canOperate || create.isPending}
                  >
                    {create.isPending ? (
                      <Loader2 size={14} className="spin" />
                    ) : (
                      <Download size={14} />
                    )}{' '}
                    {t('Create download job')}
                  </button>
                </form>
              ) : (
                <DatasetImport
                  source={source}
                  onImported={(dataset) => {
                    setNotice(`${t('Dataset imported')} · ${dataset.id.slice(0, 12)}`);
                    refresh();
                    setSelected(dataset);
                  }}
                />
              )}
            </aside>
          </div>
          <section className="pro-panel jobs-panel">
            <div className="section-heading">
              <h2>{t('Download jobs')}</h2>
              <span className="quiet-copy">
                {t('Download progress is measured by the server. Unknown totals remain unknown.')}
              </span>
            </div>
            {jobs.isPending ? (
              <Loading />
            ) : jobs.isError ? (
              <ErrorBox error={jobs.error} onRetry={() => void jobs.refetch()} />
            ) : visibleJobs.length ? (
              <div className="job-list">
                {visibleJobs.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    onCancel={() => cancel.mutate(job.id)}
                    pending={!canOperate || cancel.isPending}
                  />
                ))}
              </div>
            ) : (
              <Empty title="Download history is empty">
                {t('Download a date range to create an immutable research dataset.')}
              </Empty>
            )}
            {cancel.isError && <ErrorBox error={cancel.error} />}
          </section>
        </>
      )}
      {selected && (
        <div className="modal-backdrop" onClick={() => setSelected(null)}>
          <section
            className="wide-dialog dataset-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('Dataset')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <div>
                <div className="eyebrow">{selected.id}</div>
                <h2>
                  {selected.inst_id} · {selected.kind} · {selected.bar}
                </h2>
              </div>
              <button
                className="icon-button"
                aria-label={t('Close')}
                onClick={() => setSelected(null)}
              >
                <X size={19} />
              </button>
            </div>
            <RecordGrid
              value={{
                source: selected.source,
                rows: datasetRows(selected),
                start: date(datasetStart(selected), true),
                end: date(datasetEnd(selected), true),
                hash:
                  selected.content_hash ??
                  selected.dataset_hash ??
                  selected.hash ??
                  selected.version,
              }}
            />
            {!!selected.warning && <p className="inline-warning">{String(selected.warning)}</p>}
            <h3>{t('Data quality')}</h3>
            <RecordGrid value={selected.quality} />
            {Array.isArray(selected.quality?.gaps) && selected.quality.gaps.length > 0 && (
              <JsonDetails value={selected.quality.gaps} label="Gaps" open />
            )}
            <JsonDetails
              value={selected.rules ?? selected.instrument ?? selected.metadata}
              label="Instrument rules"
              open
            />
            <JsonDetails value={selected} label="Manifest" />
            {selected.kind === 'trade' && (
              <button
                className="button button-dark"
                onClick={() => {
                  onResearch({ dataset_id: selected.id });
                  setSelected(null);
                }}
              >
                {t('Research')}
                <ArrowRight size={14} />
              </button>
            )}
          </section>
        </div>
      )}
    </>
  );
}
function JobRow({ job, onCancel, pending }: { job: Job; onCancel: () => void; pending: boolean }) {
  const { t } = useI18n();
  const statusType =
    job.status === 'completed'
      ? 'good'
      : job.status === 'failed'
        ? 'bad'
        : ['canceled', 'cancelled', 'degraded'].includes(job.status)
          ? 'warning'
          : 'neutral';
  const progress = typeof job.progress === 'number' ? job.progress : null;
  return (
    <div className="job-row">
      <div className="job-title">
        <strong>{job.inst_id ?? job.id}</strong>
        <span>
          {job.kind} · {job.bar}
        </span>
        <small>{date(job.created_at)}</small>
      </div>
      <div className="job-progress">
        <Status type={statusType}>{job.status}</Status>
        {progress !== null && (
          <>
            <progress max={progress > 1 ? 100 : 1} value={progress} />
            <small>{number(progress > 1 ? progress : progress * 100, 1)}%</small>
          </>
        )}
        <small>
          {t('Rows')}: {valueText(job.received ?? job.rows ?? job.candles)}
          {job.expected != null ? ` / ${job.expected}` : ''}
          {job.pages !== undefined ? ` · ${job.pages} pages` : ''}
        </small>
        {job.error && <span className="negative">{job.error}</span>}
      </div>
      <div className="job-actions">
        {['queued', 'running', 'cancelling'].includes(job.status) && (
          <button
            className="button button-small button-secondary"
            disabled={pending || job.status === 'cancelling'}
            onClick={onCancel}
          >
            <Square size={11} />
            {t('Cancel job')}
          </button>
        )}
        <JsonDetails value={job} />
      </div>
    </div>
  );
}
