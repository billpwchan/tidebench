import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { proApi, type PortfolioReleasePreview, type RecordData } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Status } from './workspace';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { date, number } from '../lib/format';
import PortfolioResearchSummary from './PortfolioResearchSummary';
import PortfolioExecutionPolicy from './PortfolioExecutionPolicy';
import { LiquidityReleaseEvidence } from './LiquidityEvidence';
import { useReceiptOwnership, type ReceiptTask } from '../lib/receiptOwnership';

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
  onExecution: (id?: string) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const allowed = canTrade(useSession()?.user?.role);
  const [review, setReview] = useState('');
  const [checks, setChecks] = useState<string[]>([]);
  const receipt = useReceiptOwnership(runId);
  const preview = useMutation({
    mutationFn: (_task: ReceiptTask) => proApi.previewPortfolioRelease(runId),
    onSuccess: (_result, task) => {
      if (!receipt.owns(task)) return;
      setChecks([]);
    },
  });
  const approve = useMutation({
    mutationFn: (_task: ReceiptTask) =>
      proApi.approvePortfolioRelease({
        run_id: runId,
        preview_hash: preview.data!.preview_hash,
        review,
        acknowledgements: checks,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['portfolio-releases'] });
    },
  });
  const activate = useMutation({
    mutationFn: ({ id }: { id: string; task: ReceiptTask }) => proApi.activatePortfolioRelease(id),
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
  const reviewPreview = () => {
    receipt.invalidate();
    approve.reset();
    activate.reset();
    preview.mutate(receipt.capture('preview'));
  };
  const p = receipt.owns(preview.variables) ? preview.data : undefined;
  const approved = receipt.owns(approve.variables) ? approve.data : undefined;
  const activated = receipt.owns(activate.variables?.task) ? activate.data : undefined;
  const previewPending = preview.isPending && receipt.owns(preview.variables);
  const approvePending = approve.isPending && receipt.owns(approve.variables);
  const activatePending = activate.isPending && receipt.owns(activate.variables?.task);
  return (
    <section className="portfolio-release-review">
      <div className="section-heading">
        <div>
          <p className="eyebrow">{t('RESEARCH → PAPER')}</p>
          <h3>{t('Review the complete portfolio')}</h3>
        </div>
        <button
          className="button button-secondary"
          disabled={!allowed || !bound || previewPending}
          onClick={reviewPreview}
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
      {preview.isError && receipt.owns(preview.variables) && <ErrorBox error={preview.error} />}
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
          <ReleaseResearchEvidence preview={p} />
          {p.capital_admission && <CapitalAdmissionEvidence preview={p} />}
          <LiquidityReleaseEvidence
            symbols={p.definition.legs.map((leg) => leg.inst_id)}
            source={p.source}
            review={p.liquidity_review}
          />
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
              approve.mutate(receipt.capture('approval'));
            }}
          >
            <Field label="Review note">
              <textarea
                required
                minLength={12}
                maxLength={2000}
                rows={3}
                disabled={!!approved || approvePending}
                value={review}
                onChange={(e) => setReview(e.target.value)}
              />
            </Field>
            {p.required_acknowledgements.map((a) => (
              <label key={a} className="checkbox-field">
                <input
                  type="checkbox"
                  checked={checks.includes(a)}
                  disabled={!!approved || approvePending}
                  onChange={(e) =>
                    setChecks((c) => (e.target.checked ? [...c, a] : c.filter((x) => x !== a)))
                  }
                />
                <span>{t(acknowledgement[a] ?? a)}</span>
              </label>
            ))}
            {!approved && (
              <button
                className="button button-secondary"
                disabled={
                  !allowed ||
                  approvePending ||
                  p.blockers.length > 0 ||
                  !p.metrics ||
                  !p.result_hash ||
                  !p.version_id ||
                  !p.required_acknowledgements.every((a) => checks.includes(a))
                }
              >
                {t('Approve portfolio release')}
              </button>
            )}
          </form>
          {approve.isError && receipt.owns(approve.variables) && <ErrorBox error={approve.error} />}
          {approved && (
            <div className="portfolio-release-actions">
              <Status type="good">{t('Approval saved')}</Status>
              <button
                className="button button-citrus"
                disabled={!allowed || activatePending || !!activated}
                onClick={() =>
                  activate.mutate({ id: approved.id, task: receipt.capture('activation') })
                }
              >
                <Play size={14} />
                {t('Activate managed paper portfolio')}
              </button>
            </div>
          )}
          {activate.isError && receipt.owns(activate.variables?.task) && (
            <ErrorBox error={activate.error} />
          )}
          {activated && (
            <div className="action-note">
              <p>{t('Portfolio activated. All markets are owned by one managed group.')}</p>
              <button
                className="text-button"
                onClick={() => onExecution(activated.group_id ?? undefined)}
              >
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

const percentage = (value: unknown) => (value == null ? '—' : `${number(value)}%`);

function CapitalAdmissionEvidence({ preview: p }: { preview: PortfolioReleasePreview }) {
  const { t } = useI18n();
  const admission = p.capital_admission!;
  const actual = admission.actual_admission;
  const projected = actual ? Number(actual.capital_committed_or_used_pct) : null;
  const limit = Number(admission.policy.capital_limit_pct);
  const proposed = actual?.owners.find((row) => row.owner === 'proposed');
  const proposedEffective = proposed
    ? Math.max(Number(proposed.actual_capital_pct), Number(proposed.promised_capital_pct))
    : null;
  const currentEffective =
    projected != null && proposedEffective != null ? projected - proposedEffective : null;
  const assets = actual?.base_asset_gross_pct;
  return (
    <section className="capital-admission" aria-label={t('Account capital admission')}>
      <h3>{t('Effective account capital budget')}</h3>
      {actual ? (
        <>
          <p className="snapshot-footnote">
            {t('Account valuation used for this review')}: {date(actual.as_of, true)}
            {' · '}
            {t(actual.valuation_status ?? 'unavailable')}
            {' · '}
            {t('Source')}: {t(p.source === 'example' ? 'Example · synthetic' : 'OKX public')}
          </p>
          {actual.as_of == null && (
            <p className="inline-warning">
              {t('The preview does not provide an account valuation timestamp.')}
            </p>
          )}
          <RecordGrid
            value={{
              [t('Account equity basis')]:
                actual.equity == null ? '—' : `${number(actual.equity)} USDT`,
              [t('Existing effective capital')]: percentage(currentEffective),
              [t('Proposed capital')]: percentage(admission.proposed_capital_pct),
              [t('Projected effective capital')]: percentage(projected),
              [t('Remaining budget after proposal')]: percentage(
                projected == null ? null : Math.max(0, limit - projected),
              ),
              [t('Account capital limit')]: percentage(limit),
              [t('Projected effective gross exposure')]: percentage(
                actual.gross_committed_or_used_pct,
              ),
              ...(projected != null && projected > limit
                ? {
                    [t('Budget excess')]: percentage(projected - limit),
                  }
                : {}),
            }}
          />
          <p className="quiet-copy">
            {t(
              'Effective capital sums the greater of actual use including pending new-risk orders and the declared promise for each owner. Percentages use this captured account equity. The proposed portfolio is included below.',
            )}
          </p>
          <DataTable
            rows={actual.owners}
            columns={[
              {
                key: 'owner',
                label: 'Inventory owner',
                render: (row) => (row.owner === 'proposed' ? t('Proposed portfolio') : row.owner),
              },
              {
                key: 'actual_capital_pct',
                label: 'Actual use including pending orders (%)',
                render: (row) => percentage(row.actual_capital_pct),
              },
              {
                key: 'promised_capital_pct',
                label: 'Declared promise (%)',
                render: (row) => percentage(row.promised_capital_pct),
              },
              {
                key: 'effective_capital_pct',
                label: 'Effective capital (%)',
                render: (row) =>
                  percentage(
                    Math.max(Number(row.actual_capital_pct), Number(row.promised_capital_pct)),
                  ),
              },
            ]}
          />
          <DataTable
            rows={Object.entries(assets ?? {}).map(([asset, exposure]) => ({ asset, exposure }))}
            columns={[
              { key: 'asset', label: 'Underlying asset' },
              {
                key: 'exposure',
                label: 'Projected effective gross exposure',
                render: (row) => percentage(row.exposure),
              },
              {
                key: 'limit',
                label: 'Underlying asset gross limit (%)',
                render: () => percentage(admission.policy.max_base_asset_gross_pct),
              },
            ]}
          />
        </>
      ) : (
        <p className="inline-warning">
          {t(
            'Effective account usage is unavailable in this preview. Declared promises alone do not establish remaining risk capacity.',
          )}
        </p>
      )}
      <p className="quiet-copy">
        {t(
          'Capital promises and budget headroom are not available cash. Spot inventory uses marked value; perpetual capital uses posted initial margin. Gross limits also apply without direction netting.',
        )}
      </p>
      <details>
        <summary>{t('Declared portfolio commitments')}</summary>
        <RecordGrid
          value={{
            [t('Committed capital')]: percentage(admission.committed_capital_pct),
            [t('Projected committed capital')]: percentage(
              admission.projected_committed_capital_pct,
            ),
            [t('Remaining declared capital')]: percentage(admission.remaining_declared_capital_pct),
            [t('Projected promised gross exposure')]: percentage(
              admission.projected_promised_gross_pct,
            ),
          }}
        />
      </details>
      <p className="quiet-copy">
        {t(
          'Stopping a group retains its commitment until inventory, working orders and deferred funding are cleared.',
        )}
      </p>
    </section>
  );
}

function ReleaseResearchEvidence({ preview: p }: { preview: PortfolioReleasePreview }) {
  const { t } = useI18n();
  const study = useQuery({
    queryKey: ['portfolio-run', p.run_id],
    queryFn: () => proApi.portfolioRun(p.run_id),
  });
  const matchingStudy =
    study.data?.manifest.result_hash === p.result_hash &&
    study.data.config.portfolio_version_id === p.version_id
      ? study.data
      : undefined;
  const metrics = p.metrics ?? {};
  const evaluation = p.evaluation ?? {};
  const mode = String(evaluation.mode ?? 'full');
  const rejection = evaluation.rejection as RecordData | undefined;
  const rejectionStatus = rejection?.status ?? p.research_evidence.rejection_status;
  const checks = Array.isArray(rejection?.checks) ? (rejection.checks as RecordData[]) : [];
  const scope =
    mode === 'sealed_holdout'
      ? t('One-use portfolio holdout')
      : mode === 'train_test'
        ? t('Chronological test · not a one-use holdout')
        : t('Development window · not independent evidence');
  const windows: RecordData[] =
    mode === 'train_test'
      ? [
          {
            window: t('Development'),
            start: evaluation.train_start,
            end: evaluation.train_end,
            ...((evaluation.train_metrics as RecordData) ?? {}),
          },
          {
            window: t('Independent test'),
            start: evaluation.test_start,
            end: evaluation.test_end,
            ...metrics,
          },
        ]
      : [
          {
            window: scope,
            start: evaluation.test_start ?? matchingStudy?.manifest.start,
            end: evaluation.test_end ?? matchingStudy?.manifest.end,
            ...metrics,
          },
        ];
  const financialWindow = windows.at(-1)!;
  return (
    <section className="release-research-evidence" aria-label={t('Research decision evidence')}>
      <h3>{t('Research decision evidence')}</h3>
      <p className="strategy-hypothesis">{p.hypothesis}</p>
      <RecordGrid
        value={{
          [t('Immutable portfolio version')]: p.version_id,
          [t('Research run')]: p.run_id,
          [t('Evaluation scope')]: scope,
          [t('Financial window start')]: date(Number(financialWindow.start), true),
          [t('Financial window end')]: date(Number(financialWindow.end), true),
          [t('Research source')]: t(p.source === 'example' ? 'Example · synthetic' : 'OKX public'),
        }}
      />
      <p className="quiet-copy">
        {t(
          'These metrics belong to the financial evaluation window above, not the live paper account.',
        )}
      </p>
      {(!p.metrics || !p.result_hash || !p.version_id) && (
        <p className="inline-warning">
          {t('Bound research evidence is incomplete. Refresh the review before approval.')}
        </p>
      )}
      <PortfolioResearchSummary metrics={metrics} />
      <RecordGrid
        value={{
          [t('Final equity (USDT)')]:
            metrics.final_equity == null ? '—' : `${number(metrics.final_equity)} USDT`,
          [t('Funding (USDT)')]:
            metrics.funding_paid == null ? '—' : `${number(metrics.funding_paid)} USDT`,
          [t('Recorded fills')]: metrics.orders,
        }}
      />
      <DataTable
        rows={windows}
        columns={[
          { key: 'window', label: 'Window' },
          { key: 'start', label: 'Start', render: (row) => date(Number(row.start), true) },
          { key: 'end', label: 'End', render: (row) => date(Number(row.end), true) },
          {
            key: 'total_return_pct',
            label: 'Net return %',
            render: (row) => number(row.total_return_pct),
          },
          {
            key: 'max_drawdown_pct',
            label: 'Max drawdown %',
            render: (row) => number(row.max_drawdown_pct),
          },
          { key: 'fees_paid', label: 'Fees (USDT)', render: (row) => number(row.fees_paid) },
        ]}
      />
      {financialWindow.start == null || financialWindow.end == null ? (
        <p className="inline-warning">
          {t(
            'Evaluation window boundaries are unavailable. Inspect the bound input manifest before review.',
          )}
        </p>
      ) : null}
      {study.isError && <ErrorBox error={study.error} onRetry={() => void study.refetch()} />}
      <div className="portfolio-release-actions">
        <span>{t('Pre-registered rejection assessment')}</span>
        <Status
          type={
            rejectionStatus === 'passed'
              ? 'good'
              : rejectionStatus === 'rejected'
                ? 'bad'
                : 'warning'
          }
        >
          {t(
            rejectionStatus == null
              ? 'No pre-registered rejection assessment'
              : String(rejectionStatus),
          )}
        </Status>
      </div>
      <p className="quiet-copy">
        {t(
          'A completed computation or paper approval does not establish investment edge. Rejected or inconclusive research remains visible when approved for supervised paper study.',
        )}
      </p>
      {!!checks.length && (
        <DataTable
          rows={checks}
          columns={[
            {
              key: 'metric',
              label: 'Criterion',
              render: (row) =>
                t(
                  (
                    {
                      return_vs_cash_pct: 'Return above cash (%)',
                      max_drawdown_pct: 'Maximum drawdown (%)',
                      zero_debt: 'Insurance debt (USDT)',
                      execution_status: 'Execution state',
                      lifecycle_economics: 'Lifecycle economics',
                    } as Record<string, string>
                  )[String(row.metric)] ?? String(row.metric),
                ),
            },
            {
              key: 'actual',
              label: 'Actual',
              render: (row) =>
                ['execution_status', 'lifecycle_economics'].includes(String(row.metric))
                  ? t(String(row.actual))
                  : number(row.actual, 4),
            },
            {
              key: 'threshold',
              label: 'Threshold',
              render: (row) =>
                ['execution_status', 'lifecycle_economics'].includes(String(row.metric))
                  ? t(String(row.threshold))
                  : number(row.threshold, 4),
            },
            {
              key: 'passed',
              label: 'Assessment',
              render: (row) => (
                <Status type={row.passed === true ? 'good' : 'bad'}>
                  {t(row.passed === true ? 'passed' : 'rejected')}
                </Status>
              ),
            },
          ]}
        />
      )}
      <JsonDetails
        value={{ research_evidence: p.research_evidence, evaluation }}
        label="Research evidence"
      />
    </section>
  );
}
