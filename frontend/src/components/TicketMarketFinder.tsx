import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, Star } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import { useI18n } from '../lib/i18n';
import { useWorkspacePreferences } from './WorkspacePreferences';
import { ErrorBox } from './workspace';

export default function TicketMarketFinder({
  source,
  product,
  value,
  onSelect,
}: {
  source: Source;
  product: 'SPOT' | 'SWAP';
  value: string;
  onSelect: (symbol: string) => void;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const p = useWorkspacePreferences();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const id = useId();
  const instruments = useQuery({
    queryKey: ['pro-instruments', source, product],
    queryFn: () => proApi.instruments(source, product),
    staleTime: 300000,
  });
  const live = (instruments.data?.items ?? [])
    .filter((i) => !i.state || i.state === 'live')
    .map((i) => String(i.inst_id));
  const needle = query.toUpperCase().trim().replaceAll('/', '-');
  const recent = [...new Set([...p.favorites, ...p.recentMarkets])]
    .filter((s) => live.includes(s))
    .slice(0, 4);
  const choices = needle
    ? live.filter((s) => s.toUpperCase().includes(needle)).slice(0, 6)
    : recent;
  const active = Math.min(selected, Math.max(0, choices.length - 1));
  const choose = (symbol: string) => {
    if (!live.includes(symbol)) return;
    onSelect(symbol);
    p.rememberMarket(symbol);
    setQuery('');
    setSelected(0);
  };
  return (
    <section
      className="ticket-market-finder"
      aria-label={text('Find a ticket market', '查找订单市场')}
    >
      <label htmlFor={id}>
        <Search size={13} />
        {text('Find a market', '查找市场')}
      </label>
      <div className="ticket-market-search-row">
        <input
          id={id}
          type="search"
          role="combobox"
          aria-label={text('Search ticket markets', '搜索订单市场')}
          aria-controls={`${id}-results`}
          aria-expanded={choices.length > 0}
          aria-activedescendant={choices.length ? `${id}-option-${active}` : undefined}
          autoComplete="off"
          value={query}
          placeholder={text('Symbol · e.g. BTC or ETH', '品种 · 如 BTC 或 ETH')}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelected(0);
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              e.preventDefault();
              setSelected(
                (active + (e.key === 'ArrowDown' ? 1 : -1) + choices.length) %
                  Math.max(1, choices.length),
              );
            }
            if (e.key === 'Enter') {
              e.preventDefault();
              if (choices[active]) choose(choices[active]);
            }
            if (e.key === 'Escape') {
              e.preventDefault();
              e.stopPropagation();
              setQuery('');
            }
          }}
        />
        <button
          className="icon-button"
          type="button"
          aria-label={`${p.favorites.includes(value) ? text('Unfavorite', '取消收藏') : text('Favorite', '收藏')} ${value}`}
          aria-pressed={p.favorites.includes(value)}
          disabled={!live.includes(value)}
          onClick={() => p.toggleFavorite(value)}
        >
          <Star size={15} fill={p.favorites.includes(value) ? 'currentColor' : 'none'} />
        </button>
      </div>
      {instruments.isError && (
        <ErrorBox error={instruments.error} onRetry={() => void instruments.refetch()} />
      )}
      {!needle && choices.length > 0 && (
        <span className="ticket-market-list-heading">
          {text('Favorites and recent markets', '收藏与最近市场')}
        </span>
      )}
      <div
        id={`${id}-results`}
        role="listbox"
        aria-label={text('Matching ticket markets', '匹配的订单市场')}
        className="ticket-market-results"
      >
        {choices.map((s, i) => (
          <button
            key={s}
            id={`${id}-option-${i}`}
            type="button"
            role="option"
            aria-selected={i === active}
            onClick={() => choose(s)}
          >
            {s}
            {p.favorites.includes(s) && <Star size={10} />}
          </button>
        ))}
      </div>
      {needle && !instruments.isPending && !instruments.isError && choices.length === 0 && (
        <p role="status">
          {text('No live market matches this product.', '当前产品没有匹配的可交易市场。')}
        </p>
      )}
    </section>
  );
}
