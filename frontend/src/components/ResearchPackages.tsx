import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Database, Download, Loader2, RefreshCw, Square } from 'lucide-react';
import type { Source } from '../api';
import { downloadBlob } from '../api';
import { proApi } from '../proApi';
import type { DataPackage, Dataset, ResearchInputs } from '../proApi';
import { useSession } from './AuthGate';
import { canResearch } from '../lib/permissions';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { ActionNote, Empty, ErrorBox, Field, Loading, Status } from './workspace';
import { JsonDetails, ProductSymbol, valueText } from './ProWorkspace';

const intervals: Record<string, number> = {
  '1m': 60000,
  '5m': 300000,
  '15m': 900000,
  '1H': 3600000,
  '4H': 14400000,
  '1Dutc': 86400000,
};
const utcInput = (time: number) => new Date(time).toISOString().slice(0, 16);
export default function ResearchPackages({
  source,
  onResearch,
  onOpenRaw,
}: {
  source: Source;
  onResearch: (inputs: ResearchInputs) => void;
  onOpenRaw: () => void;
}) {
  const { t } = useI18n();
  const canOperate = canResearch(useSession()?.user?.role);
  const qc = useQueryClient();
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [bar, setBar] = useState('1H');
  const [start, setStart] = useState(() =>
    utcInput(
      source === 'example'
        ? Date.UTC(2025, 0, 1)
        : Math.floor(Date.now() / 3600000) * 3600000 - 30 * 86400000,
    ),
  );
  const [end, setEnd] = useState(() =>
    utcInput(
      source === 'example' ? Date.UTC(2025, 0, 31) : Math.floor(Date.now() / 3600000) * 3600000,
    ),
  );
  const [includeIndex, setIncludeIndex] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestKey = useRef<{ payload: string; key: string } | null>(null);
  const packages = useQuery({
    queryKey: ['pro-packages', source],
    queryFn: () => proApi.packages(source),
    refetchInterval: (q) =>
      q.state.data?.items.some((p) => ['queued', 'running', 'preparing'].includes(p.status))
        ? 1500
        : 10000,
  });
  const datasets = useQuery({
    queryKey: ['pro-datasets'],
    queryFn: proApi.datasets,
    refetchInterval: 15000,
  });
  const refresh = () => {
    for (const key of ['pro-packages', 'pro-datasets', 'pro-jobs', 'pro-ops'])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const create = useMutation({
    mutationFn: proApi.preparePackage,
    onSuccess: () => {
      requestKey.current = null;
      setNotice(t('Research package queued'));
      refresh();
    },
  });
  const items = packages.data?.items.filter((p) => p.source === source) ?? [];
  const submit = () => {
    const startTs = Date.parse(`${start}Z`),
      endTs = Date.parse(`${end}Z`);
    if (!Number.isFinite(startTs) || !Number.isFinite(endTs) || endTs <= startTs) {
      setValidation(t('End time must be after start time.'));
      return;
    }
    if (startTs % intervals[bar] || endTs % intervals[bar]) {
      setValidation(t('Range boundaries must align to the selected UTC interval.'));
      return;
    }
    setValidation(null);
    setNotice(null);
    const body = {
      source,
      inst_id: symbol,
      bar,
      start: startTs,
      end: endTs,
      include_index: product === 'SWAP' && includeIndex,
    };
    const payload = JSON.stringify(body);
    if (requestKey.current?.payload !== payload)
      requestKey.current = { payload, key: crypto.randomUUID() };
    create.mutate({ ...body, idempotency_key: requestKey.current.key });
  };
  return (
    <>
      <ActionNote text={notice} />
      <div className="research-package-layout">
        <section className="pro-panel package-library">
          <div className="section-heading">
            <div>
              <h2>{t('Research packages')}</h2>
              <p className="section-description">
                {t('One versioned package brings together the inputs required for a research run.')}
              </p>
            </div>
            <span className="subtle-tag">
              {items.filter((p) => p.ready).length} {t('ready')}
            </span>
          </div>
          {packages.isPending ? (
            <Loading />
          ) : packages.isError ? (
            <ErrorBox error={packages.error} onRetry={() => void packages.refetch()} />
          ) : items.length ? (
            <div className="package-list">
              {items.map((p) => (
                <PackageRow
                  key={p.id}
                  item={p}
                  datasets={datasets.data?.items.filter((d) => d.source === source) ?? []}
                  onResearch={onResearch}
                  onRefresh={refresh}
                  canOperate={canOperate}
                />
              ))}
            </div>
          ) : (
            <Empty title="No research packages">
              <span>
                {t('Select a market and UTC window. Perpetual inputs are prepared together.')}
              </span>
            </Empty>
          )}
          <button className="text-button package-raw-link" onClick={onOpenRaw}>
            {t('Open raw datasets & imports')}
            <ArrowRight size={13} />
          </button>
        </section>
        <aside className="pro-panel package-form">
          <div className="section-heading">
            <h2>{t('Prepare research data')}</h2>
            <Database size={17} />
          </div>
          <form
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
              onProductChange={setProduct}
            />
            <Field label="Interval">
              <select
                value={bar}
                onChange={(e) => {
                  const next = e.target.value;
                  setBar(next);
                  setStart((v) =>
                    utcInput(Math.floor(Date.parse(`${v}Z`) / intervals[next]) * intervals[next]),
                  );
                  setEnd((v) =>
                    utcInput(Math.floor(Date.parse(`${v}Z`) / intervals[next]) * intervals[next]),
                  );
                }}
              >
                {Object.keys(intervals).map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            </Field>
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
            <div className="package-input-plan">
              <span>{t('Required inputs')}</span>
              <strong>
                {product === 'SPOT'
                  ? t('Trade candles')
                  : `${t('Trade candles')} + ${t('Mark price')} + ${t('Funding rates')}`}
              </strong>
              {product === 'SWAP' && (
                <small>
                  {t(
                    'Funding settlement marks are captured and validated before the package is ready.',
                  )}
                </small>
              )}
            </div>
            {product === 'SWAP' && (
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={includeIndex}
                  onChange={(e) => setIncludeIndex(e.target.checked)}
                />
                {t('Include index prices for context')}
              </label>
            )}
            {source === 'example' && (
              <p className="inline-warning">
                {t('Synthetic package. Fixed example prices are not OKX history.')}
              </p>
            )}
            {(validation || create.isError) && (
              <ErrorBox error={validation ? new Error(validation) : create.error} />
            )}
            <button
              className="button button-citrus full-width"
              disabled={!canOperate || create.isPending}
            >
              {create.isPending ? <Loader2 className="spin" size={14} /> : <Download size={14} />}{' '}
              {t('Prepare research package')}
            </button>
            <p className="form-footnote pro-form-note">
              {t(
                'Coverage gaps or missing funding settlement prices block research readiness. Inspect the reason before importing a replacement.',
              )}
            </p>
          </form>
        </aside>
      </div>
    </>
  );
}
function PackageRow({
  item,
  datasets,
  onResearch,
  onRefresh,
  canOperate,
}: {
  item: DataPackage;
  datasets: Dataset[];
  onResearch: (inputs: ResearchInputs) => void;
  onRefresh: () => void;
  canOperate: boolean;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [selections, setSelections] = useState<Record<string, string>>({});
  const replacementKey = useRef<{ payload: string; key: string } | null>(null);
  const cancel = useMutation({ mutationFn: proApi.cancelPackage, onSuccess: onRefresh });
  const retry = useMutation({ mutationFn: proApi.retryPackage, onSuccess: onRefresh });
  const open = useMutation({
    mutationFn: () => proApi.packageInputs(item.id),
    onSuccess: async (inputs) => {
      await qc.fetchQuery({ queryKey: ['pro-datasets'], queryFn: proApi.datasets, staleTime: 0 });
      onResearch({
        dataset_id: inputs.dataset_id,
        mark_dataset_id: inputs.mark_dataset_id,
        funding_dataset_id: inputs.funding_dataset_id,
        start_ts: inputs.start_ts,
        end_ts: inputs.end_ts,
        package_id: inputs.package_id,
        package_manifest_hash: inputs.package_manifest_hash,
      });
    },
  });
  const replace = useMutation({
    mutationFn: proApi.preparePackage,
    onSuccess: () => {
      replacementKey.current = null;
      onRefresh();
    },
  });
  const manifest = useMutation({
    mutationFn: () => proApi.packageManifest(item.id),
    onSuccess: (data) =>
      downloadBlob(
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
        `tidebench-package-${item.id}.json`,
      ),
  });
  const busy = cancel.isPending || retry.isPending || replace.isPending;
  const progress =
    typeof item.progress === 'number' ? Math.max(0, Math.min(1, item.progress)) : null;
  const kinds = [
    ...(item.inst_id.endsWith('-SWAP') ? ['trade', 'mark', 'funding'] : ['trade']),
    ...(item.include_index ? ['index'] : []),
  ];
  const candidate = (kind: string) =>
    datasets.filter(
      (d) =>
        d.inst_id === item.inst_id &&
        (!d.region || !item.region || d.region === item.region) &&
        d.kind === kind &&
        d.bar === item.bar &&
        Number(d.start ?? d.start_ts) === item.start &&
        Number(d.end ?? d.end_ts) === item.end &&
        d.transport === 'user_import',
    );
  return (
    <article className="package-row">
      <div className="package-row-heading">
        <div>
          <strong>{item.inst_id}</strong>
          <span>
            {item.bar} · {item.source === 'example' ? t('Example · synthetic') : 'OKX'}
          </span>
          <small>
            {date(item.start, true)} → {date(item.end, true)}
          </small>
        </div>
        <Status
          type={
            item.ready ? 'good' : ['failed', 'blocked'].includes(item.status) ? 'bad' : 'neutral'
          }
        >
          {t(item.status)}
        </Status>
      </div>
      <div className="package-components">
        {(item.components ?? []).map((c) => (
          <div key={c.kind}>
            <span>
              {t(
                c.kind === 'trade'
                  ? 'Trade candles'
                  : c.kind === 'mark'
                    ? 'Mark price'
                    : c.kind === 'funding'
                      ? 'Funding rates'
                      : 'Index price',
              )}
            </span>
            <Status type={c.status === 'failed' ? 'bad' : 'neutral'}>{t(c.status)}</Status>
            {typeof c.progress === 'number' && (
              <progress
                aria-label={`${c.kind} ${t('Progress')}`}
                value={Math.max(0, Math.min(1, c.progress))}
                max={1}
              />
            )}
          </div>
        ))}
      </div>
      {progress !== null && !item.ready && (
        <div className="package-overall-progress">
          <progress max={1} value={progress} aria-label={t('Package progress')} />
          <small>{number(progress * 100, 1)}%</small>
        </div>
      )}
      {!!item.blockers?.length && (
        <ul className="package-blockers">
          {item.blockers.map((b, i) => (
            <li key={`${b.code}-${i}`}>
              <code>{b.code}</code>
              <span>{b.message}</span>
            </li>
          ))}
        </ul>
      )}
      {item.status === 'preparing' && (
        <p className="snapshot-footnote">
          {t('Capturing funding settlement marks…')} {valueText(item.funding_marks?.captured)} /{' '}
          {valueText(item.funding_marks?.total)}
        </p>
      )}
      <div className="package-row-actions">
        <code title={item.manifest_hash ?? item.id}>
          {(item.manifest_hash ?? item.id).slice(0, 12)}
        </code>
        <div>
          <button
            className="text-button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {t(expanded ? 'Hide details' : 'Inspect')}
          </button>
          {['queued', 'running', 'preparing', 'failed', 'blocked'].includes(item.status) && (
            <button
              className="text-button"
              disabled={!canOperate || busy}
              onClick={() => cancel.mutate(item.id)}
            >
              <Square size={11} />
              {t('Cancel package')}
            </button>
          )}
          {item.status === 'failed' && (
            <button
              className="text-button"
              disabled={!canOperate || busy}
              onClick={() => retry.mutate(item.id)}
            >
              <RefreshCw size={12} />
              {t('Retry package')}
            </button>
          )}
          <button
            className="button button-secondary"
            disabled={!item.ready || open.isPending}
            onClick={() => open.mutate()}
          >
            {open.isPending ? <Loader2 className="spin" size={12} /> : <ArrowRight size={12} />}{' '}
            {t('Open in research')}
          </button>
        </div>
      </div>
      {[cancel, retry, replace, open, manifest]
        .filter((m) => m.isError)
        .map((m, i) => (
          <ErrorBox key={i} error={m.error} />
        ))}
      {expanded && (
        <div className="package-inspection">
          <JsonDetails value={item} label="Package manifest" open />
          {item.ready && (
            <button
              className="text-button"
              disabled={manifest.isPending}
              onClick={() => manifest.mutate()}
            >
              {t('Export manifest')}
            </button>
          )}
          {item.status === 'blocked' && (
            <form
              className="package-repair-form"
              onSubmit={(e) => {
                e.preventDefault();
                const dataset_ids = Object.fromEntries(
                  Object.entries(selections).filter(([, id]) => id),
                );
                const body = {
                  source: item.source,
                  inst_id: item.inst_id,
                  bar: item.bar,
                  start: item.start,
                  end: item.end,
                  include_index: !!item.include_index,
                  dataset_ids,
                };
                const payload = JSON.stringify(body);
                if (replacementKey.current?.payload !== payload)
                  replacementKey.current = { payload, key: crypto.randomUUID() };
                replace.mutate({ ...body, idempotency_key: replacementKey.current.key });
              }}
            >
              <h3>{t('Create a new package from explicit imports')}</h3>
              <p className="range-hint">
                {t(
                  'Import externally sourced records first, then select exact versions. The blocked package remains unchanged.',
                )}
              </p>
              {kinds.map((kind) => (
                <Field
                  key={kind}
                  label={
                    kind === 'trade'
                      ? 'Trade candles'
                      : kind === 'mark'
                        ? 'Mark price'
                        : 'Funding rates'
                  }
                >
                  <select
                    value={selections[kind] ?? ''}
                    onChange={(e) => setSelections((v) => ({ ...v, [kind]: e.target.value }))}
                  >
                    <option value="">{t('Keep downloaded component')}</option>
                    {candidate(kind).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.id.slice(0, 12)} · {date(d.created_at)} ·{' '}
                        {d.quality?.complete === true ? t('complete') : t('incomplete')}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
              <button
                className="button button-secondary"
                disabled={!canOperate || busy || !Object.values(selections).some(Boolean)}
              >
                {t('Create replacement package')}
              </button>
            </form>
          )}
        </div>
      )}
    </article>
  );
}
