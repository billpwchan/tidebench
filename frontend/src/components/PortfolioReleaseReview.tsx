import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Play, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { proApi } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Status } from './workspace';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { number } from '../lib/format';

const acknowledgement: Record<string, string> = {
  holdout_rejected:
    'This final holdout failed its pre-registered criteria. Paper deployment does not turn it into positive evidence.',
  holdout_inconclusive:
    'This final holdout has insufficient observations or fills for its pre-registered assessment.',
  sequential_leg_risk:
    'Legs fill sequentially. A failed leg triggers group reduction, which can also fail and retain inventory.',
  execution_risk_difference: 'Account exposure and loss limits differ from this research scenario.',
  execution_cost_difference: 'The paper account cost policy differs from this research scenario.',
  no_oos_evidence: 'This study has no independent out-of-sample evaluation.',
};
const blockers: Record<string, string> = {
  execution_halted: 'New risk is halted',
  existing_inventory: 'Existing inventory must be closed',
  pending_orders: 'Cancel pending orders in the portfolio markets',
  strategy_ownership: 'Another strategy owns a portfolio market',
  deployment_limit: 'The workspace has insufficient strategy slots',
  leverage_limit: 'A portfolio leg exceeds the account leverage limit',
};
export default function PortfolioReleaseReview({
  runId,
  bound,
  onExecution,
}: {
  runId: string;
  bound: boolean;
  onExecution: () => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const allowed = canTrade(useSession()?.user?.role);
  const [review, setReview] = useState('');
  const [checks, setChecks] = useState<string[]>([]);
  const preview = useMutation({
    mutationFn: () => proApi.previewPortfolioRelease(runId),
    onSuccess: () => {
      setChecks([]);
      approve.reset();
      activate.reset();
    },
  });
  const approve = useMutation({
    mutationFn: () =>
      proApi.approvePortfolioRelease({
        run_id: runId,
        preview_hash: preview.data!.preview_hash,
        review,
        acknowledgements: checks,
      }),
  });
  const activate = useMutation({
    mutationFn: () => proApi.activatePortfolioRelease(approve.data!.id),
    onSuccess: () => {
      for (const key of [
        'managed-portfolios',
        'portfolio-releases',
        'pro-deployments',
        'pro-account',
      ])
        void qc.invalidateQueries({ queryKey: [key] });
    },
  });
  const p = preview.data;
  return (
    <section className="portfolio-release-review">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('RESEARCH → PAPER')}</p>
          <h3>{t('Review the complete portfolio')}</h3>
        </div>
        <button
          className="button button-secondary"
          disabled={!allowed || !bound || preview.isPending}
          onClick={() => preview.mutate()}
        >
          <ShieldCheck size={14} />
          {t('Review portfolio release')}
        </button>
      </div>
      {!bound && (
        <p className="quiet-copy">
          {t('Revise this legacy study to save an immutable portfolio version before release.')}
        </p>
      )}
      {preview.isError && <ErrorBox error={preview.error} />}
      {p && (
        <>
          <p className="quiet-copy">
            {t(
              'Targets use current account equity and shared cash. No separate capital account is created for this portfolio.',
            )}
          </p>
          <RecordGrid
            value={{
              capital_pct: p.definition.capital_pct,
              max_residual_pct: p.definition.max_residual_pct,
              failure_policy: t('Reduce the group on failure'),
              bar: p.definition.bar,
            }}
          />
          <h3>{t('Research evidence')}</h3>
          <RecordGrid value={p.research_evidence} />
          <h3>{t('Account risk policy')}</h3>
          <RecordGrid value={p.risk_policy} />
          <DataTable
            rows={p.definition.legs}
            columns={[
              { key: 'inst_id', label: 'Market' },
              {
                key: 'weight',
                label: 'Target weight',
                render: (r) => `${number(Number(r.weight) * 100)}%`,
              },
              { key: 'leverage', label: 'Leverage' },
              { key: 'direction', label: 'Direction' },
            ]}
          />
          {p.cost_differences.length > 0 && (
            <DataTable
              rows={p.cost_differences}
              columns={[
                { key: 'field', label: 'Cost' },
                { key: 'research', label: 'Research' },
                { key: 'execution', label: 'Execution' },
              ]}
            />
          )}
          {(p.risk_differences?.length ?? 0) > 0 && (
            <DataTable
              rows={p.risk_differences!}
              columns={[
                { key: 'field', label: 'Risk control' },
                { key: 'research', label: 'Research' },
                { key: 'execution', label: 'Execution' },
              ]}
            />
          )}
          {p.blockers.length > 0 && (
            <div className="action-note bad">
              {p.blockers.map((b) => (
                <p key={b}>{t(blockers[b] ?? b)}</p>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              approve.mutate();
            }}
          >
            <Field label="Review note">
              <textarea
                required
                minLength={12}
                maxLength={2000}
                rows={3}
                disabled={!!approve.data}
                value={review}
                onChange={(e) => setReview(e.target.value)}
              />
            </Field>
            {p.required_acknowledgements.map((a) => (
              <label key={a} className="checkbox-field">
                <input
                  type="checkbox"
                  checked={checks.includes(a)}
                  disabled={!!approve.data}
                  onChange={(e) =>
                    setChecks((c) => (e.target.checked ? [...c, a] : c.filter((x) => x !== a)))
                  }
                />
                <span>{t(acknowledgement[a] ?? a)}</span>
              </label>
            ))}
            {!approve.data && (
              <button
                className="button button-secondary"
                disabled={
                  !allowed ||
                  approve.isPending ||
                  p.blockers.length > 0 ||
                  !p.required_acknowledgements.every((a) => checks.includes(a))
                }
              >
                {t('Approve portfolio release')}
              </button>
            )}
          </form>
          {approve.isError && <ErrorBox error={approve.error} />}
          {approve.data && (
            <div className="portfolio-release-actions">
              <Status type="good">{t('Approval saved')}</Status>
              <button
                className="button button-citrus"
                disabled={!allowed || activate.isPending || !!activate.data}
                onClick={() => activate.mutate()}
              >
                <Play size={14} />
                {t('Activate managed paper portfolio')}
              </button>
            </div>
          )}
          {activate.isError && <ErrorBox error={activate.error} />}
          {activate.data && (
            <div className="action-note">
              <p>{t('Portfolio activated. All markets are owned by one managed group.')}</p>
              <button className="text-button" onClick={onExecution}>
                {t('Open managed portfolios')}
              </button>
            </div>
          )}
          <JsonDetails value={p} label="Approval evidence" />
        </>
      )}
    </section>
  );
}
