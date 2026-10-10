import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { Empty, ErrorBox } from './workspace';
import { date, number, quantityText } from '../lib/format';
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
            <dd title={valueText(v)}>
              {(key.endsWith('_at') || key.endsWith('_ts') || key === 'as_of') &&
              typeof v === 'number'
                ? date(v, true)
                : typeof v === 'number' ||
                    (typeof v === 'string' && /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(v))
                  ? quantityText(v)
                  : valueText(v)}
            </dd>
          </div>
        ))}
    </dl>
  );
}
export type DataTableColumn<T> = {
  key: string;
  label: string;
  render?: (row: T) => ReactNode;
  className?: string;
  sortable?: boolean;
  sortValue?: (row: T) => unknown;
  sortType?: 'number' | 'text';
  sticky?: 'identity' | 'action';
};
type TableSort = { key: string; direction: 'asc' | 'desc' };
function tableValue(row: RecordData, key: string): unknown {
  return key.split('.').reduce<unknown>((value, part) => {
    if (!value || typeof value !== 'object' || !Object.hasOwn(value, part)) return undefined;
    return (value as RecordData)[part];
  }, row);
}
function numericTableValue(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function compareTableValues(
  left: unknown,
  right: unknown,
  type: 'number' | 'text',
  direction: 'asc' | 'desc',
) {
  const a = type === 'number' ? numericTableValue(left) : left == null ? null : valueText(left);
  const b = type === 'number' ? numericTableValue(right) : right == null ? null : valueText(right);
  // Unknown financial values stay last in both directions; zero is a real value.
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  const difference =
    type === 'number'
      ? Number(a) - Number(b)
      : String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' });
  return direction === 'asc' ? difference : -difference;
}
export function DataTable<T extends RecordData>({
  rows,
  columns,
  empty = 'No records',
  rowKey,
  searchKeys,
  searchLabel = 'Search table',
  compactColumns,
  compactLabel = 'Table records',
}: {
  rows: T[];
  columns: DataTableColumn<T>[];
  empty?: string;
  // The index is the original input index, independent of sorting and filtering.
  rowKey?: (r: T, index: number) => string;
  searchKeys?: string[];
  searchLabel?: string;
  // Column keys that remain visible on narrow screens. Other fields expand in place.
  compactColumns?: string[];
  compactLabel?: string;
}) {
  const { t } = useI18n();
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<TableSort>();
  const scroll = useRef<HTMLDivElement>(null);
  const compactScroll = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  const sortable = columns.filter((column) => column.sortable);
  const sortColumn = sortable.find((column) => column.key === sort?.key);
  const searchable = !!searchKeys?.length;
  const compactKeys = compactColumns?.length
    ? [
        ...new Set([
          ...compactColumns,
          ...columns.filter((c) => c.sticky === 'action').map((c) => c.key),
        ]),
      ]
    : [];
  const compact = compactKeys
    .map((key) => columns.find((column) => column.key === key))
    .filter((column): column is DataTableColumn<T> => !!column);
  const secondary = columns.filter((column) => !compactKeys.includes(column.key));
  const desk =
    searchable || sortable.length > 0 || compact.length > 0 || columns.some((c) => c.sticky);
  const filtered = useMemo(() => {
    const needle = searchable ? query.trim().toLocaleLowerCase() : '';
    const entries = rows.map((row, index) => ({
      row,
      index,
      key: rowKey?.(row, index) ?? String(row.id ?? index),
    }));
    const matching = needle
      ? entries.filter(({ row }) =>
          searchKeys!.some((key) =>
            valueText(tableValue(row, key)).toLocaleLowerCase().includes(needle),
          ),
        )
      : entries;
    if (sort && sortColumn) {
      matching.sort((a, b) => {
        const comparison = compareTableValues(
          sortColumn.sortValue ? sortColumn.sortValue(a.row) : tableValue(a.row, sortColumn.key),
          sortColumn.sortValue ? sortColumn.sortValue(b.row) : tableValue(b.row, sortColumn.key),
          sortColumn.sortType ?? 'text',
          sort.direction,
        );
        return comparison || a.index - b.index;
      });
    }
    return matching;
  }, [rows, rowKey, query, searchable, searchKeys, sort, sortColumn]);
  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    const update = () => setOverflow(element.scrollWidth > element.clientWidth + 1);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, [rows.length, filtered.length, columns.length]);
  const pageSize = 100;
  const pageCount = Math.ceil(filtered.length / pageSize);
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));
  const offset = currentPage * pageSize;
  const visible = filtered.slice(offset, offset + pageSize);
  const changePage = (next: number) => {
    setPage(next);
    scroll.current?.scrollTo({ top: 0 });
    compactScroll.current?.scrollTo({ top: 0 });
  };
  const changeSort = (next?: TableSort) => {
    setSort(next);
    changePage(0);
  };
  const clearSearch = () => {
    setQuery('');
    changePage(0);
  };
  const cellClass = (column: DataTableColumn<T>) =>
    [
      column.className,
      column.sticky ? `desk-sticky-${column.sticky}` : '',
      column.sortType === 'number' ? 'desk-numeric' : '',
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const cellValue = (column: DataTableColumn<T>, row: T) =>
    column.render ? column.render(row) : valueText(tableValue(row, column.key));
  return (
    <div
      className={`data-table-region${desk ? ' desk-table-region' : ''}${compact.length ? ' has-compact-records' : ''}`}
    >
      {rows.length > 0 && (searchable || (compact.length > 0 && sortable.length > 0)) && (
        <div className={`desk-table-tools${searchable ? '' : ' compact-sort-only'}`}>
          {searchable && (
            <label className="desk-table-search">
              <span>{t(searchLabel)}</span>
              <input
                type="search"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  changePage(0);
                }}
                aria-label={t(searchLabel)}
                placeholder={t(searchLabel)}
              />
            </label>
          )}
          {compact.length > 0 && sortable.length > 0 && (
            <label className="desk-compact-sort">
              <span>{t('Sort by')}</span>
              <select
                aria-label={t('Sort by')}
                value={sortColumn && sort ? JSON.stringify([sort.key, sort.direction]) : ''}
                onChange={(event) => {
                  if (!event.target.value) changeSort();
                  else {
                    const [key, direction] = JSON.parse(event.target.value) as [
                      string,
                      'asc' | 'desc',
                    ];
                    changeSort({ key, direction });
                  }
                }}
              >
                <option value="">{t('Original order')}</option>
                {sortable.flatMap((column) =>
                  (['asc', 'desc'] as const).map((direction) => (
                    <option
                      key={`${column.key}:${direction}`}
                      value={JSON.stringify([column.key, direction])}
                    >
                      {t(column.label)} · {t(direction === 'asc' ? 'Ascending' : 'Descending')}
                    </option>
                  )),
                )}
              </select>
            </label>
          )}
          {searchable && (
            <span className="desk-table-count" role="status" aria-live="polite">
              {number(filtered.length, 0)} / {number(rows.length, 0)} {t('Rows')}
            </span>
          )}
        </div>
      )}
      {!rows.length ? (
        <Empty title={empty}>{t('No records')}</Empty>
      ) : !filtered.length ? (
        <div className="desk-table-no-match" role="status">
          <span>{t('No matching records')}</span>
          <button type="button" className="text-button" onClick={clearSearch}>
            {t('Clear search')}
          </button>
        </div>
      ) : (
        <>
          <div
            ref={scroll}
            className="table-scroll dense-table desk-table-scroll"
            tabIndex={0}
            role="region"
            aria-label={t('Scrollable data table')}
          >
            <table>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th
                      className={cellClass(column)}
                      key={column.key}
                      scope="col"
                      aria-sort={
                        column.sortable
                          ? sort?.key === column.key
                            ? sort.direction === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                          : undefined
                      }
                    >
                      {column.sortable ? (
                        <button
                          type="button"
                          className="desk-sort-button"
                          onClick={() =>
                            changeSort(
                              sort?.key !== column.key
                                ? { key: column.key, direction: 'asc' }
                                : sort.direction === 'asc'
                                  ? { key: column.key, direction: 'desc' }
                                  : undefined,
                            )
                          }
                        >
                          {t(column.label)}
                          <span aria-hidden="true">
                            {sort?.key === column.key
                              ? sort.direction === 'asc'
                                ? '↑'
                                : '↓'
                              : '↕'}
                          </span>
                        </button>
                      ) : (
                        t(column.label)
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map(({ row, key }) => (
                  <tr key={key} data-row-key={key}>
                    {columns.map((column) => (
                      <td className={cellClass(column)} key={column.key}>
                        {cellValue(column, row)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {compact.length > 0 && (
            <div
              ref={compactScroll}
              className="desk-compact-records"
              role="list"
              tabIndex={0}
              aria-label={t(compactLabel)}
            >
              {visible.map(({ row, key }) => (
                <div className="desk-compact-row" role="listitem" key={key} data-row-key={key}>
                  <div className="desk-compact-summary">
                    {compact.map((column) => (
                      <div
                        className={`desk-compact-cell${column.sticky ? ` is-${column.sticky}` : ''}`}
                        key={column.key}
                        data-column={column.key}
                      >
                        <span className="desk-compact-label">{t(column.label)}</span>
                        <div className="desk-compact-value">{cellValue(column, row)}</div>
                      </div>
                    ))}
                  </div>
                  {secondary.length > 0 && (
                    <details className="desk-compact-details">
                      <summary>{t('More details')}</summary>
                      <dl>
                        {secondary.map((column) => (
                          <div key={column.key}>
                            <dt>{t(column.label)}</dt>
                            <dd>{cellValue(column, row)}</dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
          {overflow && (
            <p className="table-overflow-hint">
              {t(
                'Scroll horizontally for more columns. Keyboard: focus the table and use arrow keys.',
              )}
            </p>
          )}
          {pageCount > 1 && (
            <div className="table-pagination">
              <span role="status" aria-live="polite">
                {number(offset + 1, 0)}–{number(Math.min(offset + pageSize, filtered.length), 0)} /{' '}
                {number(filtered.length, 0)} {t('Rows')}
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
        </>
      )}
    </div>
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
            if (items[next].key !== value) onChange(items[next].key);
          }}
          tabIndex={value === item.key ? 0 : -1}
          onClick={() => {
            if (item.key !== value) onChange(item.key);
          }}
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
