import { useI18n } from '../lib/i18n';
import { number } from '../lib/format';

export function executionPolicyName(contract?: string) {
  if (contract === 'reduce_group_v2_allowance') return 'Bounded allowance replans';
  if (contract === 'reduce_group_v1') return 'Frozen orders';
  return contract ? 'Unrecognized execution policy' : 'Legacy execution policy unbound';
}

export default function PortfolioExecutionPolicy({
  contract,
  residual,
}: {
  contract?: string;
  residual?: unknown;
}) {
  const { t } = useI18n();
  const bound = ['reduce_group_v1', 'reduce_group_v2_allowance'].includes(contract ?? '');
  return (
    <div
      className="portfolio-execution-policy"
      role="note"
      aria-label={t('Portfolio execution policy')}
    >
      <p className="quiet-copy">
        <strong>{t(executionPolicyName(contract))}</strong>
        {bound && (
          <>
            {' '}
            · {t('Maximum execution residual %')}: {number(residual)}%
          </>
        )}
      </p>
      <p className="quiet-copy">
        {t(
          contract === 'reduce_group_v2_allowance'
            ? 'Targets and original commands stay frozen. A fresh, complete reduction in shared account allowance can replace only unfilled additions with smaller immutable commands, at most three times, under unchanged captured risk and capital policies.'
            : contract === 'reduce_group_v1'
              ? 'Targets and original commands stay frozen. If reduced shared account allowance prevents a frozen addition, cancel pending additions and reduce the group.'
              : 'No supported shared execution policy is bound to this evidence. Save a reviewed revision to choose a policy; existing versions and results are not converted.',
        )}
      </p>
      {bound && (
        <p className="quiet-copy">
          {t(
            'Funding, valuation, policy changes and hard order, gross, asset and new-leg minimum guards are not relaxed. The original residual limit still applies; an unsuccessful plan enters group reduction, which may also fail.',
          )}
        </p>
      )}
    </div>
  );
}
