import { useState } from 'react';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { DataTable, JsonDetails } from './ProWorkspace';

export default function PortfolioRiskEvidence({ decisions }: { decisions: RecordData[] }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState('');
  const rows = decisions.filter((d) => !!d.risk_evidence);
  if (!rows.length) return null;
  const row = rows.find((d) => String(d.ts) === selected) ?? rows[rows.length - 1];
  const evidence = row.risk_evidence as RecordData;
  const weights = (evidence.weights ?? {}) as RecordData;
  const vol = (evidence.floored_asset_vol_pct ?? {}) as RecordData;
  const contribution = (evidence.risk_contribution_pct ?? {}) as RecordData;
  return (
    <section className="portfolio-risk-evidence" aria-label={t('Portfolio risk evidence')}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">{t('Portfolio risk budget')}</span>
          <h3>{t('What sets the position size')}</h3>
        </div>
        {rows.length > 1 && (
          <select
            aria-label={t('Risk decision')}
            value={String(row.ts)}
            onChange={(e) => setSelected(e.target.value)}
          >
            {rows.map((d) => (
              <option key={String(d.ts)} value={String(d.ts)}>
                {date(Number(d.ts), true)}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="risk-evidence-metrics">
        {[
          ['Modeled sleeve volatility', evidence.modeled_vol_pct],
          ['Stressed sleeve volatility', evidence.stressed_vol_pct],
          ['Cash allocation', Number(evidence.cash_weight) * 100],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <span>{t(String(label))}</span>
            <strong>{value == null ? '—' : `${number(value, 2)}%`}</strong>
          </div>
        ))}
      </div>
      {(row.rebalance_due === false || row.exit_only === true) && (
        <p className="quiet-copy">
          {t(
            'Between scheduled rebalances: risk weights are a model reference. Recorded quantity targets retain inventory except protective reductions.',
          )}
        </p>
      )}
      <p className="quiet-copy">
        {t(
          'Risk is estimated from confirmed closes available at this decision. Values describe allocated capital before fills; target risk is not a guarantee. Costs, lot residuals, price drift and correlation changes affect the result.',
        )}
      </p>
      {evidence.reason === 'insufficient_history' ? (
        <p>{t('Waiting for the full risk estimation window; target weights remain zero.')}</p>
      ) : (
        <DataTable
          rows={Object.entries(weights).map(([symbol, weight]) => ({
            symbol,
            weight: Number(weight) * 100,
            vol: vol[symbol],
            contribution: contribution[symbol],
          }))}
          columns={[
            { key: 'symbol', label: 'Market' },
            { key: 'weight', label: 'Target weight (%)', render: (r) => number(r.weight, 2) },
            { key: 'vol', label: 'Floored asset volatility (%)', render: (r) => number(r.vol, 2) },
            {
              key: 'contribution',
              label: 'Risk contribution (vol pts)',
              render: (r) =>
                number(Math.abs(Number(r.contribution)) < 1e-8 ? 0 : r.contribution, 2),
            },
          ]}
        />
      )}
      <JsonDetails value={evidence} label="Covariance and decision evidence" />
    </section>
  );
}
