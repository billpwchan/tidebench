import { useState } from 'react';
import type { Source } from '../api';
import type { RecordData } from '../proApi';
import { sizeNotional } from '../lib/orderSizing';
import { useI18n } from '../lib/i18n';
import { number, price } from '../lib/format';

export default function BudgetSizing({
  source,
  symbol,
  product,
  side,
  orderType,
  market,
  now,
  onApply,
}: {
  source: Source;
  symbol: string;
  product: 'SPOT' | 'SWAP';
  side: 'buy' | 'sell';
  orderType: string;
  limitPrice: string;
  market?: RecordData;
  now: number;
  onApply: (quantity: string) => void;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const [budget, setBudget] = useState('');
  const instrument = (market?.instrument ?? {}) as RecordData;
  const timestamp = Number(market?.ts);
  const fresh =
    Number.isFinite(timestamp) &&
    timestamp > 0 &&
    (source === 'example' || (timestamp <= now + 2000 && now - timestamp < 15000));
  const reference = side === 'buy' ? market?.ask : market?.bid;
  const sizing =
    orderType === 'market' && fresh && instrument.inst_id === symbol
      ? sizeNotional(budget, reference, instrument, product)
      : undefined;
  return (
    <details className="budget-sizing">
      <summary>{text('Size by USDT notional', '按 USDT 名义金额计算数量')}</summary>
      {orderType !== 'market' && (
        <p className="field-hint">
          {text(
            'Budget sizing is available for market orders. Limit and stop orders require explicit native quantity because their fill prices and reserved margin differ.',
            '预算换算适用于市价单。限价单与止损单的成交价格和资金预留不同，请输入明确的原生数量。',
          )}
        </p>
      )}
      <label className="field">
        <span>{text('Notional budget (USDT)', '名义金额预算（USDT）')}</span>
        <input
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={budget}
          onChange={(event) => setBudget(event.target.value)}
        />
      </label>
      {sizing ? (
        <div className="budget-estimate" role="status">
          <strong>
            {price(sizing.quantity)}{' '}
            {text(
              product === 'SWAP' ? 'contracts' : 'base units',
              product === 'SWAP' ? '张合约' : '基础单位',
            )}
          </strong>
          <span>
            {text('Reference price', '参考价格')}:{' '}
            {price(sizing.referencePrice, instrument.tick_size)} · {text('Notional', '名义金额')}:{' '}
            {number(sizing.notional)} USDT
          </span>
          <span>
            {text('Unused after lot rounding', '按手数取整后的余额')}: {price(sizing.unused)} USDT
          </span>
        </div>
      ) : (
        budget && (
          <p className="field-hint">
            {text(
              'A fresh quote, verified contract rules and at least one minimum lot are required.',
              '需要新鲜报价、已确认的合约规则，且预算足够一个最小手数。',
            )}
          </p>
        )
      )}
      <p className="field-hint">
        {text(
          'This is a notional estimate before fees and slippage. Margin and risk are checked in the server preview. Applying a size does not submit an order.',
          '这是未计手续费与滑点的名义金额估计。保证金与风险由服务端预览检查。应用数量不会提交订单。',
        )}
      </p>
      <button
        type="button"
        className="button button-secondary full-width"
        disabled={!sizing}
        onClick={() => sizing && onApply(sizing.quantity)}
      >
        {text('Use calculated quantity', '应用计算数量')}
      </button>
    </details>
  );
}
