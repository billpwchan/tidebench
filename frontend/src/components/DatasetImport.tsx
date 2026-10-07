import { useState } from 'react';
import type { ChangeEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { FileJson, Loader2, Upload } from 'lucide-react';
import type { Source } from '../api';
import { downloadBlob } from '../api';
import { proApi } from '../proApi';
import type { Dataset, RecordData } from '../proApi';
import { useSession } from './AuthGate';
import { canResearch } from '../lib/permissions';
import { useI18n } from '../lib/i18n';
import { Field, ErrorBox, Status } from './workspace';
import { JsonDetails, ProductSymbol } from './ProWorkspace';
const intervalMs: Record<string, number> = {
  '1m': 60000,
  '5m': 300000,
  '15m': 900000,
  '1H': 3600000,
  '4H': 14400000,
  '1Dutc': 86400000,
};
const utcInput = (time: number) => new Date(time).toISOString().slice(0, 16);
const object = (value: unknown): value is RecordData =>
  !!value && typeof value === 'object' && !Array.isArray(value);
export default function DatasetImport({
  source,
  onImported,
}: {
  source: Source;
  onImported: (dataset: Dataset) => void;
}) {
  const { t } = useI18n();
  const canOperate = canResearch(useSession()?.user?.role);
  const [fileName, setFileName] = useState('');
  const [records, setRecords] = useState<RecordData[]>([]);
  const [attribution, setAttribution] = useState<RecordData>({});
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [symbol, setSymbol] = useState('BTC-USDT');
  const [kind, setKind] = useState('trade');
  const [bar, setBar] = useState('1H');
  const [start, setStart] = useState(() =>
    utcInput(Math.floor(Date.now() / 3600000) * 3600000 - 30 * 86400000),
  );
  const [end, setEnd] = useState(() => utcInput(Math.floor(Date.now() / 3600000) * 3600000));
  const [provider, setProvider] = useState('');
  const [coverage, setCoverage] = useState('partial');
  const [realized, setRealized] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const upload = useMutation({
    mutationFn: proApi.importDataset,
    onSuccess: (dataset) => {
      onImported(dataset);
      setRecords([]);
      setFileName('');
    },
  });
  const choose = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(null);
    setReading(true);
    setRecords([]);
    upload.reset();
    try {
      if (file.size > 10 * 1024 * 1024)
        throw new Error(t('Import files must be smaller than 10 MiB.'));
      const parsed: unknown = JSON.parse(await file.text());
      if (!Array.isArray(parsed) && !object(parsed))
        throw new Error(t('Use a JSON records array or dataset envelope.'));
      const rows = Array.isArray(parsed) ? parsed : parsed.records;
      if (!Array.isArray(rows) || !rows.length || rows.length > 250000 || !rows.every(object))
        throw new Error(t('The file must contain between 1 and 250000 record objects.'));
      if (object(parsed)) {
        if (parsed.source !== undefined && parsed.source !== source)
          throw new Error(
            t(
              'File source differs from the selected market source. Switch the source before importing.',
            ),
          );
        if (typeof parsed.inst_id === 'string') {
          setSymbol(parsed.inst_id);
          setProduct(parsed.inst_id.endsWith('-SWAP') ? 'SWAP' : 'SPOT');
        }
        if (typeof parsed.kind === 'string') setKind(parsed.kind);
        if (typeof parsed.bar === 'string') setBar(parsed.bar);
        if (typeof parsed.start === 'number') setStart(utcInput(parsed.start));
        if (typeof parsed.end === 'number') setEnd(utcInput(parsed.end));
        const provenance = object(parsed.provenance) ? parsed.provenance : {};
        setAttribution(provenance);
        setProvider(typeof provenance.provider === 'string' ? provenance.provider : '');
        setRealized(provenance.rate_kind === 'realized');
        setCoverage(
          object(provenance.coverage) && provenance.coverage.complete === true
            ? 'complete'
            : 'partial',
        );
      } else {
        setAttribution({});
        setProvider('');
        setCoverage('partial');
        setRealized(false);
      }
      setRecords(rows);
      setFileName(file.name);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('Invalid JSON file'));
      setFileName('');
    } finally {
      setReading(false);
      event.target.value = '';
    }
  };
  const submit = () => {
    const a = Date.parse(`${start}Z`),
      b = Date.parse(`${end}Z`);
    setError(null);
    if (!records.length || !provider.trim()) {
      setError(t('Choose a file and provide its data provider.'));
      return;
    }
    if (!Number.isFinite(a) || !Number.isFinite(b) || a >= b) {
      setError(t('End time must be after start time.'));
      return;
    }
    if (kind !== 'funding' && (a % intervalMs[bar] || b % intervalMs[bar])) {
      setError(t('Range boundaries must align to the selected UTC interval.'));
      return;
    }
    if (kind === 'funding' && !realized) {
      setError(t('Funding imports must contain realized settlement rates.'));
      return;
    }
    upload.mutate({
      source,
      inst_id: symbol,
      kind,
      bar,
      start: a,
      end: b,
      records,
      provenance: {
        ...attribution,
        provider: provider.trim(),
        coverage: { start: a, end: b, complete: coverage === 'complete' },
        ...(kind === 'funding' ? { rate_kind: 'realized' } : {}),
      },
    });
  };
  return (
    <form
      className="compact-form dataset-import-form"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label className="file-drop">
        <FileJson size={22} />
        <strong>{t('Choose JSON file')}</strong>
        <span>{t('Dataset envelope or record objects')}</span>
        <input
          type="file"
          accept="application/json,.json"
          aria-label={t('Choose JSON file')}
          onChange={(e) => void choose(e)}
          disabled={reading || upload.isPending}
        />
      </label>
      {reading && (
        <p className="import-file-summary">
          <Loader2 size={13} className="spin" />
          {t('Reading file…')}
        </p>
      )}
      {fileName && (
        <div className="import-file-summary">
          <strong>{fileName}</strong>
          <Status>{`${records.length} ${t('Rows')}`}</Status>
        </div>
      )}
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
            <option value="index">{t('Index price')}</option>
            <option value="funding" disabled={product === 'SPOT'}>
              {t('Funding rates')}
            </option>
          </select>
        </Field>
        <Field label="Interval">
          <select value={bar} onChange={(e) => setBar(e.target.value)}>
            {Object.keys(intervalMs).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Start (UTC)">
        <input
          type="datetime-local"
          required
          value={start}
          onChange={(e) => setStart(e.target.value)}
        />
      </Field>
      <Field label="End (UTC)">
        <input
          type="datetime-local"
          required
          value={end}
          onChange={(e) => setEnd(e.target.value)}
        />
      </Field>
      <Field label="Data provider">
        <input
          required
          maxLength={256}
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          placeholder={t('Provider or archival source')}
        />
      </Field>
      <Field label="Coverage declaration">
        <select value={coverage} onChange={(e) => setCoverage(e.target.value)}>
          <option value="partial">{t('Partial or unverified coverage')}</option>
          <option value="complete">{t('Provider declares complete coverage')}</option>
        </select>
      </Field>
      {kind === 'funding' && (
        <label className="checkbox-field">
          <input
            type="checkbox"
            required
            checked={realized}
            onChange={(e) => setRealized(e.target.checked)}
          />
          <span>{t('These are realized funding settlement rates.')}</span>
        </label>
      )}
      <p className="range-hint">
        {t(
          'Imports retain provider attribution. Coverage declarations are not exchange verification.',
        )}
      </p>
      <JsonDetails
        value={{ source, inst_id: symbol, kind, bar, provenance: attribution }}
        label="Import metadata"
      />
      <button
        type="button"
        className="text-button"
        onClick={() => {
          const a = Date.parse(`${start}Z`),
            b = Date.parse(`${end}Z`);
          downloadBlob(
            new Blob(
              [
                JSON.stringify(
                  {
                    source,
                    inst_id: symbol,
                    kind,
                    bar,
                    start: a,
                    end: b,
                    records: [],
                    provenance: {
                      provider: '',
                      coverage: { start: a, end: b, complete: false },
                      ...(kind === 'funding' ? { rate_kind: 'realized' } : {}),
                    },
                  },
                  null,
                  2,
                ),
              ],
              { type: 'application/json' },
            ),
            `tidebench-${symbol}-${kind}-import-template.json`,
          );
        }}
      >
        {t('Download JSON template')}
      </button>
      <details className="import-format">
        <summary>{t('JSON format')}</summary>
        <p>
          {t(
            'Candles require ts, open, high, low, close, volume and confirmed: true. Funding requires ts and rate. Prices and quantities should be decimal strings; timestamps are UTC milliseconds.',
          )}
        </p>
      </details>
      {(error || upload.isError) && <ErrorBox error={error ? new Error(error) : upload.error} />}
      <button
        className="button button-citrus full-width"
        disabled={!canOperate || !records.length || upload.isPending || reading}
      >
        {upload.isPending ? <Loader2 size={14} className="spin" /> : <Upload size={14} />}{' '}
        {t('Import dataset')}
      </button>
    </form>
  );
}
