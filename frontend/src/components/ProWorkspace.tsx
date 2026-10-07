import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { Empty, ErrorBox } from './workspace';
import { date, number } from '../lib/format';
export function valueText(value: unknown): string {
  if (value === undefined || value === null) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
export function humanKey(key: string) {
  return key.replaceAll('_', ' ').replace(/\b\w/g, (s) => s.toUpperCase());
}
export function scalar(value: unknown, digits = 2) {
  return value === null || value === undefined ? '—' : number(value, digits);
}
export function JsonDetails({
  value,
  label = 'Details',
  open = false,
}: {
  value: unknown;
  label?: string;
  open?: boolean;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(open);
  if (value === undefined || value === null) return null;
  return (
    <div className="json-details">
      <button
        type="button"
        className="text-button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {t(label)}
      </button>
      {expanded && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </div>
  );
}
export function RecordGrid({ value }: { value: RecordData | undefined | null }) {
  const { t } = useI18n();
  return (
    <dl className="record-grid">
      {Object.entries(value ?? {})
        .filter(([, v]) => typeof v !== 'object' || v === null)
        .map(([key, v]) => (
          <div key={key}>
            <dt>{t(humanKey(key))}</dt>
            <dd>
              {(key.endsWith('_at') || key.endsWith('_ts') || key === 'as_of') &&
              typeof v === 'number'
                ? date(v, true)
                : valueText(v)}
            </dd>
          </div>
        ))}
    </dl>
  );
}
type Column<T> = { key: string; label: string; render?: (row: T) => ReactNode; className?: string };
export function DataTable<T extends RecordData>({
  rows,
  columns,
  empty = 'No records',
  rowKey,
}: {
  rows: T[];
  columns: Column<T>[];
  empty?: string;
  rowKey?: (r: T, index: number) => string;
}) {
  const { t } = useI18n();
  const [page, setPage] = useState(0);
  const scroll = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    const update = () => setOverflow(element.scrollWidth > element.clientWidth + 1);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, [rows.length, columns.length]);
  const pageSize = 100;
  const pageCount = Math.ceil(rows.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));
  const offset = currentPage * pageSize;
  const changePage = (next: number) => {
    setPage(next);
    scroll.current?.scrollTo({ top: 0 });
  };
  return rows.length ? (
    <div className="data-table-region">
      <div
        ref={scroll}
        className="table-scroll dense-table"
        tabIndex={0}
        role="region"
        aria-label={t('Scrollable data table')}
      >
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th className={c.className} key={c.key}>
                  {t(c.label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(offset, offset + pageSize).map((r, i) => (
              <tr key={rowKey?.(r, offset + i) ?? String(r.id ?? offset + i)}>
                {columns.map((c) => (
                  <td className={c.className} key={c.key}>
                    {c.render ? c.render(r) : valueText(r[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {overflow && (
        <p className="table-overflow-hint">
          {t('Scroll horizontally for more columns. Keyboard: focus the table and use arrow keys.')}
        </p>
      )}
      {pageCount > 1 && (
        <div className="table-pagination">
          <span role="status" aria-live="polite">
            {number(offset + 1, 0)}–{number(Math.min(offset + pageSize, rows.length), 0)} /{' '}
            {number(rows.length, 0)} {t('Rows')}
          </span>
          <div>
            <button
              type="button"
              className="text-button"
              disabled={currentPage === 0}
              onClick={() => changePage(currentPage - 1)}
            >
              {t('Previous page')}
            </button>
            <button
              type="button"
              className="text-button"
              disabled={currentPage >= pageCount - 1}
              onClick={() => changePage(currentPage + 1)}
            >
              {t('Next page')}
            </button>
          </div>
        </div>
      )}
    </div>
  ) : (
    <Empty title={empty}>{t('No records')}</Empty>
  );
}
export function WorkspaceTabs({
  items,
  value,
  onChange,
}: {
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="workspace-tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={value === item.key}
          className={value === item.key ? 'active' : ''}
          onKeyDown={(e) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
            e.preventDefault();
            const index = items.findIndex((x) => x.key === value);
            const next =
              e.key === 'Home'
                ? 0
                : e.key === 'End'
                  ? items.length - 1
                  : (index + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
            const buttons =
              e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
            buttons?.[next]?.focus();
            onChange(items[next].key);
          }}
          tabIndex={value === item.key ? 0 : -1}
          onClick={() => onChange(item.key)}
        >
          {t(item.label)}
        </button>
      ))}
    </div>
  );
}
export function ProductSymbol({
  source,
  value,
  onChange,
  product,
  onProductChange,
}: {
  source: Source;
  value: string;
  onChange: (v: string) => void;
  product: 'SPOT' | 'SWAP';
  onProductChange: (v: 'SPOT' | 'SWAP') => void;
}) {
  const { t } = useI18n();
  const instruments = useQuery({
    queryKey: ['pro-instruments', source, product],
    queryFn: () => proApi.instruments(source, product),
    staleTime: 300000,
  });
  const markets = [
    ...new Set([
      value,
      ...(instruments.data?.items ?? [])
        .filter((i) => !i.state || i.state === 'live')
        .map((i) => String(i.inst_id)),
    ]),
  ].sort();
  return (
    <div className="form-grid">
      <label className="field">
        <span>{t('Product')}</span>
        <select
          aria-label={t('Product')}
          value={product}
          onChange={(e) => {
            const p = e.target.value as 'SPOT' | 'SWAP';
            onProductChange(p);
            onChange(value.replace('-SWAP', '') + (p === 'SWAP' ? '-SWAP' : ''));
          }}
        >
          <option value="SPOT">{t('Spot')}</option>
          <option value="SWAP">{t('USDT perpetual')}</option>
        </select>
      </label>
      <label className="field">
        <span>{t('Market')}</span>
        <select aria-label={t('Market')} value={value} onChange={(e) => onChange(e.target.value)}>
          {markets.map((id) => {
            return (
              <option key={id} value={id}>
                {id}
              </option>
            );
          })}
        </select>
        {instruments.isPending && <small>{t('Loading instruments…')}</small>}
      </label>
      {instruments.isError && (
        <ErrorBox error={instruments.error} onRetry={() => void instruments.refetch()} />
      )}
    </div>
  );
}
