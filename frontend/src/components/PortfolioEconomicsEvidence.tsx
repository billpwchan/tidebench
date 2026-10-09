import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { number } from '../lib/format';
import { DataTable, JsonDetails } from './ProWorkspace';

export default function PortfolioEconomicsEvidence({ evidence }: { evidence?: RecordData }) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  if (!evidence) return null;
  const passive = evidence.passive_reference as RecordData | undefined;
  const exposure = evidence.exposure_comparison as RecordData | undefined;
  const markets = evidence.market_contributions as RecordData | undefined;
  const amount = (value: unknown) => (value == null ? '—' : `${number(value, 2)} USDT`);
  return (
    <section
      className="portfolio-economics-evidence"
      aria-label={text('Sources of P&L', '收益构成')}
    >
      <div className="section-heading">
        <h3>{text('Sources of P&L', '收益构成')}</h3>
        <span className="quiet-copy">
          {text('Actual filled quantities · monetary reconciliation', '实际成交数量 · 金额对账')}
        </span>
      </div>
      <div className="risk-evidence-metrics portfolio-performance-metrics">
        {[
          [
            text('Price before modeled quote costs', '计入模型成交成本前的价格损益'),
            evidence.price_pnl_before_recorded_quote_shortfall,
          ],
          [text('Modeled quote costs', '模型成交成本'), evidence.modeled_quote_shortfall_pnl],
          [text('Trading fees', '交易费用'), evidence.fees_pnl],
          [text('Funding contribution', '资金费率损益'), evidence.funding_pnl],
          [text('Net P&L', '净损益'), evidence.net_pnl],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <span>{String(label)}</span>
            <strong>{amount(value)}</strong>
          </div>
        ))}
      </div>
      {evidence.status === 'incomplete' && (
        <p className="warning-banner">
          {text(
            'Unsettled obligations or incomplete valuation prevent a complete P&L explanation.',
            '未结义务或估值缺口使完整损益暂不可核定。',
          )}
        </p>
      )}
      {markets?.status === 'available' && (
        <DataTable<RecordData>
          rows={markets.markets as RecordData[]}
          columns={[
            { key: 'inst_id', label: text('Market', '交易市场') },
            {
              key: 'price_pnl_before_fees_and_funding',
              label: text('Price P&L', '价格损益'),
              render: (row) => amount(row.price_pnl_before_fees_and_funding),
            },
            { key: 'fees_pnl', label: text('Fees', '费用'), render: (row) => amount(row.fees_pnl) },
            {
              key: 'funding_pnl',
              label: text('Funding', '资金费率'),
              render: (row) => amount(row.funding_pnl),
            },
            {
              key: 'net_pnl',
              label: text('Net P&L', '净损益'),
              render: (row) => amount(row.net_pnl),
            },
          ]}
        />
      )}
      <p className="quiet-copy">
        {text(
          'Cash reference: 0% with zero interest. The passive reference enters at the first next open, includes entry fees, slippage and quantity rounding, and retains its inventory.',
          '现金基准：零利息、收益 0%。被动基准从首个次期开盘进入，包含入场费用、滑点及数量取整，并保留期末持仓。',
        )}
      </p>
      {passive?.status === 'available' ? (
        <p className="quiet-copy">
          {text('Passive return', '被动基准收益')} {number(passive.return_pct, 2)}% ·{' '}
          {text('Return difference', '收益差')} {number(evidence.excess_return_vs_passive_pct, 2)}%
          · {text('Mean gross exposure: strategy / passive', '平均 gross 敞口：策略 / 被动')}{' '}
          {number(exposure?.strategy_mean_gross_pct, 2)}% /{' '}
          {number(exposure?.passive_mean_gross_pct, 2)}%
        </p>
      ) : (
        <p className="quiet-copy">
          {text(
            'A matching passive reference is unavailable for this lifecycle or sample. Cash remains the declared reference.',
            '该生命周期或样本暂无对应被动基准，保留已声明的现金基准。',
          )}
        </p>
      )}
      <p className="quiet-copy">
        {text(
          'Exposure differs between the strategy and passive reference. A perpetual price proxy excludes funding and financing; these comparisons and descriptive beta do not establish alpha.',
          '策略与被动基准的实际敞口不同。永续价格代理基准不含资金费率和融资；收益差与描述性 beta 不能证明 alpha。',
        )}
      </p>
      <JsonDetails
        value={evidence}
        label={text('Economic evidence and reference rules', '经济证据与基准规则')}
      />
    </section>
  );
}
