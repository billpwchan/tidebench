import { useQuery } from '@tanstack/react-query';
import { RefreshCw, X } from 'lucide-react';
import type { Source } from '../api';
import { proApi, type RecordData } from '../proApi';
import { date, number, price } from '../lib/format';
import { useDialogFocus, useNow } from '../lib/hooks';
import { useI18n } from '../lib/i18n';
import { JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Loading, Status } from './workspace';
import './market-inspector.css';

export default function MarketInspector({
  source,
  symbol,
  onClose,
}: {
  source: Source;
  symbol: string;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  useDialogFocus(true, '.market-inspector', onClose);
  const now = useNow();
  const market = useQuery({
    queryKey: ['pro-market', source, symbol],
    queryFn: () => proApi.market(source, symbol),
    refetchOnMount: 'always',
    refetchInterval: 5000,
  });
  const m = market.data;
  const instrument: RecordData =
    m?.instrument && typeof m.instrument === 'object' && !Array.isArray(m.instrument)
      ? (m.instrument as RecordData)
      : {};
  const identityMatches =
    !!m && m.inst_id === symbol && m.source === source && instrument.inst_id === symbol;
  const ts = Number(m?.ts);
  const validTime = Number.isFinite(ts) && ts > 0 && (source === 'example' || ts <= now + 2000);
  const fresh = validTime && (source === 'example' || now - ts < 15000);
  const swap = symbol.endsWith('-SWAP');
  const clockNote = (value: unknown) => {
    const clock = Number(value);
    if (!Number.isFinite(clock) || clock <= 0 || (source === 'okx' && clock > now + 2000))
      return text('As of unavailable', '时间未核验');
    return source === 'example'
      ? date(clock, true)
      : `${date(clock, true)} · ${Math.max(0, Math.floor((now - clock) / 1000))}s ${t('old')}`;
  };
  return (
    <div
      className="market-inspector-scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="market-inspector"
        role="dialog"
        aria-modal="true"
        aria-labelledby="market-inspector-title"
      >
        <header className="market-inspector-heading">
          <div>
            <span>
              {source === 'example' ? text('Synthetic example', '合成示例') : 'OKX public'} ·{' '}
              {text('Read only', '只读')}
            </span>
            <h2 id="market-inspector-title">{text('Market inspector', '市场查看')}</h2>
          </div>
          <button
            className="icon-button"
            aria-label={text('Close market inspector', '关闭市场查看')}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <p className="market-inspector-purpose">
          {text(
            'Your order draft and book filters stay in place.',
            '订单草稿与账簿筛选保留在原处。',
          )}
        </p>
        <div className="market-inspector-identity">
          <Field label="Market">
            <input readOnly value={symbol} />
          </Field>
          <Field label="Product">
            <input readOnly value={swap ? text('USDT perpetual', 'USDT 永续') : t('Spot')} />
          </Field>
        </div>
        {market.isPending ? (
          <Loading />
        ) : market.isError ? (
          <ErrorBox error={market.error} onRetry={() => void market.refetch()} />
        ) : !identityMatches ? (
          <ErrorBox
            error={
              new Error(
                text('The snapshot does not match this market.', '行情快照与当前品种不一致。'),
              )
            }
            onRetry={() => void market.refetch()}
          />
        ) : (
          <>
            <div className="market-inspector-status">
              <Status type={fresh && !market.isError ? 'neutral' : 'warning'}>
                {source === 'example'
                  ? text('Illustrative snapshot', '示意快照')
                  : fresh
                    ? text('Current ticker snapshot', '当前最新成交与盘口快照')
                    : text('Snapshot needs refresh', '快照需要刷新')}
              </Status>
              <button
                className="text-button"
                disabled={market.isFetching}
                onClick={() => void market.refetch()}
              >
                <RefreshCw size={13} />
                {t('Refresh')}
              </button>
            </div>
            <dl className="market-inspector-quotes">
              {[
                [t('Last price'), price(m?.last, instrument.tick_size)],
                [t('Bid'), price(m?.bid, instrument.tick_size)],
                [t('Ask'), price(m?.ask, instrument.tick_size)],
                ...(swap
                  ? [
                      [t('Mark price'), price(m?.mark, instrument.tick_size)],
                      [
                        t('Funding rate'),
                        m?.funding_rate == null
                          ? '—'
                          : `${number(Number(m.funding_rate) * 100, 4)}%`,
                      ],
                    ]
                  : []),
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                  {label === t('Mark price') && (
                    <small className="market-field-clock">{clockNote(m?.mark_ts)}</small>
                  )}
                  {label === t('Funding rate') && (
                    <small className="market-field-clock">{clockNote(m?.funding_ts)}</small>
                  )}
                </div>
              ))}
            </dl>
            <p className="snapshot-footnote">
              {t('Exchange as of')}: {validTime ? date(ts, true) : '—'} ·{' '}
              {source === 'example'
                ? text('Logical example clock', '示例逻辑时钟')
                : validTime
                  ? `${Math.max(0, Math.floor((now - ts) / 1000))}s ${t('old')}`
                  : text('Time unavailable', '时间不可用')}
            </p>
            {!fresh && source === 'okx' && (
              <p role="status" className="inline-warning">
                {text(
                  'Treat this as stale context. Order preview verifies fresh data independently.',
                  '此处行情不能视为新鲜数据；订单预览会独立核验。',
                )}
              </p>
            )}
            {swap && (
              <p className="snapshot-footnote">
                {text(
                  'Mark and funding publish on independent clocks; their times are shown with each value.',
                  '标记价格与资金费率使用各自发布时间，时效分别列在数值下。',
                )}
                {m?.next_funding_time != null && (
                  <>
                    <br />
                    {text('Next funding', '下次资金费结算')}:{' '}
                    {date(Number(m.next_funding_time), true)}
                  </>
                )}
              </p>
            )}
            <section className="market-inspector-rules">
              <h3>{t('Instrument rules')}</h3>
              <RecordGrid
                value={{
                  [t('Tick size')]: instrument.tick_size,
                  [t('Lot size')]: instrument.lot_size,
                  [t('Minimum size')]: instrument.min_size,
                  ...(swap
                    ? {
                        [text('Contract value', '合约面值')]: instrument.ct_val,
                        [text('Contract value currency', '面值币种')]: instrument.ct_val_ccy,
                        [text('Settlement currency', '结算币种')]: instrument.settle_ccy,
                      }
                    : {}),
                }}
              />
            </section>
            <JsonDetails value={m} label="Raw snapshot" />
          </>
        )}
        <footer>
          {source === 'example'
            ? text(
                'Synthetic prices and rules are illustrative. Local paper execution.',
                '合成价格与规则仅为示意；执行范围为本地模拟。',
              )
            : text(
                'Public market context. Execution remains local paper.',
                '公开市场信息；执行范围为本地模拟。',
              )}
        </footer>
      </section>
    </div>
  );
}
