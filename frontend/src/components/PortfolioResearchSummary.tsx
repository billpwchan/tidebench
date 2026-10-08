import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { number } from '../lib/format';
import { JsonDetails } from './ProWorkspace';

export default function PortfolioResearchSummary({ metrics }: { metrics: RecordData }) {
  const { t } = useI18n();
  return (
    <section aria-label={t('Portfolio performance')}>
      <div className="risk-evidence-metrics portfolio-performance-metrics">
        {(
          [
            ['Net return', 'total_return_pct', '%'],
            ['Maximum drawdown', 'max_drawdown_pct', '%'],
            ['Realized account volatility', 'realized_annual_vol_pct', '%'],
            ['Mean gross account exposure', 'mean_gross_exposure_pct', '%'],
            ['Executed turnover', 'turnover', '×'],
            ['Fees paid', 'fees_paid', ' USDT'],
          ] as const
        ).map(([label, key, unit]) => (
          <div key={key}>
            <span>{t(label)}</span>
            <strong>{metrics[key] == null ? '—' : `${number(metrics[key], 2)}${unit}`}</strong>
          </div>
        ))}
      </div>
      <p className="quiet-copy">
        {t(
          'Net equity includes trading fees, funding and marked open inventory. Turnover is executed absolute notional divided by initial account equity. Volatility uses complete UTC daily returns, annualized with 365 days.',
        )}
      </p>
      {metrics.realized_annual_vol_pct == null && (
        <p className="quiet-copy">
          {t(
            'Annualized volatility needs at least 30 complete UTC days and positive equity. Short samples remain unreported.',
          )}
        </p>
      )}
      <JsonDetails value={metrics} label="Full performance metrics" />
    </section>
  );
}
