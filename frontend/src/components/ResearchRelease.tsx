import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Check, Play, RefreshCw, X } from 'lucide-react';
import { useState } from 'react';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Loading, Status } from './workspace';
import { useDialogFocus } from '../lib/hooks';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { proApi } from '../proApi';
import type { PaperRelease, ProRun } from '../proApi';

const acknowledgementText: Record<string, string> = {
  post_test_selection:
    'This fold is selected after its test results became available. Its displayed test performance is not independent validation of this deployment choice.',
  in_sample_selection:
    'This candidate was selected in sample; its ranking is not out-of-sample evidence.',
  no_oos_evidence:
    'This run has no independent out-of-sample evaluation. Approval starts local simulation only.',
  execution_cost_difference:
    'I reviewed the cost differences. Simulation uses the captured execution policy.',
};
const blockerText: Record<string, string> = {
  existing_inventory: 'Close the existing position before release.',
  pending_orders: 'Cancel pending orders in this market before release.',
  strategy_ownership: 'Stop the existing market strategy before release.',
  execution_halted: 'New risk is halted. Review the risk controls first.',
};

export default function ResearchRelease({
  run,
  variant,
  onExecution,
}: {
  run: ProRun;
  variant: string;
  onExecution: () => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canTrade(useSession()?.user?.role);
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState('');
  const [acknowledgements, setAcknowledgements] = useState<string[]>([]);
  const [approved, setApproved] = useState<PaperRelease>();
  const training = variant.includes(':') && !variant.endsWith(':test');
  const selection = variant.endsWith(':test') ? variant.split(':')[0] : variant || 'single';
  const preview = useMutation({
    mutationFn: () => proApi.previewRelease(run.id, selection),
    onSuccess: () => {
      setAcknowledgements([]);
      setApproved(undefined);
    },
  });
  const approval = useMutation({
    mutationFn: () =>
      proApi.approveRelease({
        run_id: run.id,
        selection,
        preview_hash: preview.data!.preview_hash,
        acknowledgements,
        review,
      }),
    onSuccess: (release) => {
      setApproved(release);
      void qc.invalidateQueries({ queryKey: ['paper-releases'] });
      void qc.invalidateQueries({ queryKey: ['strategy-projects'] });
    },
  });
  const activation = useMutation({
    mutationFn: () => proApi.activateRelease(approved!.id),
    onSuccess: (release) => {
      setApproved(release);
      void qc.invalidateQueries({ queryKey: ['pro-deployments'] });
      void qc.invalidateQueries({ queryKey: ['paper-releases'] });
    },
  });
  useDialogFocus(open, '.release-dialog', () => setOpen(false));
  const start = () => {
    setOpen(true);
    setReview('');
    approval.reset();
    activation.reset();
    setApproved(undefined);
    preview.mutate();
  };
  const data = preview.data;
  return (
    <>
      <button
        className="button button-citrus"
        disabled={!canOperate || training || run.status !== 'completed'}
        title={
          training
            ? t('Select an out-of-sample fold to release its training-selected parameters.')
            : undefined
        }
        onClick={start}
      >
        <ArrowUpRight size={14} />
        {t('Review paper release')}
      </button>
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(false)}>
          <section
            className="wide-dialog release-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('Review paper release')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <div>
                <p className="eyebrow">{t('RESEARCH → PAPER')}</p>
                <h2>{t('Review paper release')}</h2>
              </div>
              <button
                className="icon-button"
                aria-label={t('Close')}
                onClick={() => setOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            {preview.isPending ? (
              <Loading />
            ) : preview.isError ? (
              <ErrorBox error={preview.error} onRetry={() => preview.mutate()} />
            ) : (
              data && (
                <>
                  <p>
                    {t(
                      'The selected research configuration is captured automatically. No parameters are re-entered.',
                    )}
                  </p>
                  <RecordGrid
                    value={{
                      run: run.id,
                      selection: data.selection,
                      evidence: data.selection_scope,
                      market: data.execution_config.inst_id,
                      interval: data.definition.bar,
                      product: data.definition.product,
                      allocation: data.definition.strategy.allocation,
                      leverage: data.definition.leverage,
                    }}
                  />
                  {data.selection_scope === 'training_selected_test_exposed_fold' && (
                    <p className="inline-warning">
                      {t(
                        'The parameters were chosen on training data, but this deployment fold is selected after test results became available. A new independent final evaluation is needed to validate that choice.',
                      )}
                    </p>
                  )}
                  <h3>{t('Research governance')}</h3>
                  <RecordGrid value={data.research_governance as Record<string, unknown>} />
                  <h3>{t('Strategy definition')}</h3>
                  <RecordGrid value={{ ...data.definition.strategy }} />
                  <h3>{t('Captured execution policy')}</h3>
                  <RecordGrid value={data.risk_policy} />
                  {data.cost_differences.length > 0 && (
                    <>
                      <h3>{t('Research / execution cost differences')}</h3>
                      <DataTable
                        rows={data.cost_differences}
                        columns={[
                          { key: 'field', label: 'Cost' },
                          { key: 'research', label: 'Research (bps)' },
                          { key: 'execution', label: 'Execution (bps)' },
                        ]}
                      />
                    </>
                  )}
                  <p className="quiet-copy">{t(data.model_difference)}</p>
                  {data.blockers.length > 0 && (
                    <div className="release-blockers" role="alert">
                      {data.blockers.map((code) => (
                        <p key={code}>{t(blockerText[code] ?? code)}</p>
                      ))}
                    </div>
                  )}
                  {!approved ? (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        approval.mutate();
                      }}
                    >
                      <Field label="Release review">
                        <textarea
                          required
                          minLength={12}
                          maxLength={2000}
                          rows={3}
                          value={review}
                          onChange={(e) => setReview(e.target.value)}
                          placeholder={t(
                            'Record why this version is ready for paper evaluation and which risks remain.',
                          )}
                        />
                      </Field>
                      {data.required_acknowledgements.map((code) => (
                        <label className="release-acknowledgement" key={code}>
                          <input
                            type="checkbox"
                            required
                            checked={acknowledgements.includes(code)}
                            onChange={(e) =>
                              setAcknowledgements((existing) =>
                                e.target.checked
                                  ? [...existing, code]
                                  : existing.filter((v) => v !== code),
                              )
                            }
                          />
                          <span>{t(acknowledgementText[code] ?? code)}</span>
                        </label>
                      ))}
                      {approval.isError && <ErrorBox error={approval.error} />}
                      <div className="toolbar">
                        <button
                          className="button button-citrus"
                          disabled={!canOperate || approval.isPending || data.blockers.length > 0}
                        >
                          <Check size={14} />
                          {t('Approve paper release')}
                        </button>
                        <button
                          type="button"
                          className="button button-secondary"
                          onClick={() => {
                            approval.reset();
                            preview.mutate();
                          }}
                        >
                          <RefreshCw size={14} />
                          {t('Refresh preview')}
                        </button>
                      </div>
                    </form>
                  ) : (
                    <div className="release-approved">
                      <Status type="good">
                        {approved.status === 'deployed' ? t('Deployed') : t('Approved')}
                      </Status>
                      <p>
                        {t('Approval is saved with its research, strategy and policy identities.')}
                      </p>
                      <code className="content-hash">{approved.approval_hash}</code>
                      {activation.isError && <ErrorBox error={activation.error} />}
                      <div className="toolbar">
                        {approved.status === 'deployed' ? (
                          <button
                            className="button button-citrus"
                            onClick={() => {
                              setOpen(false);
                              onExecution();
                            }}
                          >
                            <ArrowUpRight size={14} />
                            {t('Inspect deployment')}
                          </button>
                        ) : (
                          <button
                            className="button button-citrus"
                            disabled={activation.isPending}
                            onClick={() => activation.mutate()}
                          >
                            <Play size={14} />
                            {t('Activate paper release')}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                  <JsonDetails label="Captured release evidence" value={data} />
                </>
              )
            )}
          </section>
        </div>
      )}
    </>
  );
}
