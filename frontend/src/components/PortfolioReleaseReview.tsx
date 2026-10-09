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
import PortfolioExecutionPolicy from './PortfolioExecutionPolicy';
import { LiquidityReleaseEvidence } from './LiquidityEvidence';

const acknowledgement: Record<string, string> = {
  holdout_rejected:
    'This final holdout failed its pre-registered criteria. Paper deployment does not turn it into positive evidence.',
  holdout_inconclusive:
    'This final holdout has insufficient observations or fills for its pre-registered assessment.',
  sequential_leg_risk:
    'Legs fill sequentially. A failed leg triggers group reduction, which can also fail and retain inventory.',
  execution_risk_difference:
    'Account order, exposure, concentration or loss limits differ from this research scenario.',
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
  research_execution_failed: 'This simulation is not eligible for paper deployment.',
  research_economics_incomplete:
    'Unresolved inventory or lifecycle evidence prevents complete research accounting.',
  historical_lifecycle_forward_unsupported:
    'This historical lifecycle scenario cannot be deployed by the current-market paper controller.',
  account_capital_valuation:
    'Complete fresh account valuation is required before reserving portfolio capital.',
  account_capital_overcommitted: 'Declared portfolio capital would exceed the account budget.',
  account_promised_gross_limit: 'Promised gross exposure would exceed the account limit.',
  account_base_asset_limit:
    'Combined spot and perpetual exposure would exceed an underlying asset budget.',
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
          <PortfolioExecutionPolicy
            contract={p.definition.execution_contract}
            residual={p.definition.max_residual_pct}
          />
          {p.capital_admission && (
            <section className="capital-admission" aria-label={t('Account capital admission')}>
              <h3>{t('Account capital admission')}</h3>
              <RecordGrid
                value={{
                  [t('Committed capital')]: `${number(p.capital_admission.committed_capital_pct)}%`,
                  [t('Proposed capital')]: `${number(p.capital_admission.proposed_capital_pct)}%`,
                  [t('Projected committed capital')]:
                    `${number(p.capital_admission.projected_committed_capital_pct)}%`,
                  [t('Remaining declared capital')]:
                    `${number(p.capital_admission.remaining_declared_capital_pct)}%`,
                  [t('Projected promised gross exposure')]:
                    `${number(p.capital_admission.projected_promised_gross_pct)}%`,
                }}
              />
              <DataTable
                rows={Object.entries(p.capital_admission.projected_base_asset_gross_pct).map(
                  ([asset, exposure]) => ({ asset, exposure }),
                )}
                columns={[
                  { key: 'asset', label: 'Underlying asset' },
                  {
                    key: 'exposure',
                    label: 'Projected promised gross exposure',
                    render: (row) => `${number(row.exposure)}%`,
                  },
                  {
                    key: 'limit',
                    label: 'Underlying asset gross limit (%)',
                    render: () =>
                      `${number(p.capital_admission!.policy.max_base_asset_gross_pct)}%`,
                  },
                ]}
              />
              <p className="quiet-copy">
                {t(
                  'Absolute spot and perpetual exposure share one budget per underlying. Opposite directions are not netted. Declared commitments are checked before activation; actual capital use is checked again for new risk orders.',
                )}
              </p>
              <p className="quiet-copy">
                {t(
                  'Stopping a group retains its commitment until inventory, working orders and deferred funding are cleared.',
                )}
              </p>
            </section>
          )}
          <LiquidityReleaseEvidence
            symbols={p.definition.legs.map((leg) => leg.inst_id)}
            source={p.source}
            review={p.liquidity_review}
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
                {
                  key: 'field',
                  label: 'Cost',
                  render: (row) =>
                    t(
                      (
                        {
                          fee_bps: 'Fee (bps)',
                          slippage_bps: 'Slippage (bps)',
                          liquidation_fee_bps: 'Liquidation fee (bps)',
                        } as Record<string, string>
                      )[String(row.field)] ?? String(row.field),
                    ),
                },
                { key: 'research', label: 'Research' },
                { key: 'execution', label: 'Execution' },
              ]}
            />
          )}
          {(p.risk_differences?.length ?? 0) > 0 && (
            <DataTable
              rows={p.risk_differences!}
              columns={[
                {
                  key: 'field',
                  label: 'Risk control',
                  render: (row) =>
                    t(
                      (
                        {
                          lifecycle_execution_support: 'Lifecycle execution support',
                          max_order_notional: 'Maximum order notional (USDT)',
                          max_base_asset_gross_pct: 'Underlying asset gross limit (%)',
                          max_gross_exposure_pct: 'Gross exposure limit %',
                          max_daily_loss_pct: 'Daily loss limit %',
                        } as Record<string, string>
                      )[String(row.field)] ?? String(row.field),
                    ),
                },
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
