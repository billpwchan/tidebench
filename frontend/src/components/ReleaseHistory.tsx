import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { ErrorBox, Loading, Status } from './workspace';
import { date } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { useReceiptOwnership, type ReceiptTask } from '../lib/receiptOwnership';

export default function ReleaseHistory({
  source,
  initialReleaseId,
  onInspectDeployment,
}: {
  source: Source;
  initialReleaseId?: string;
  onInspectDeployment?: (id: string) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canTrade(useSession()?.user?.role);
  const receipt = useReceiptOwnership(JSON.stringify([source, initialReleaseId]));
  const releases = useQuery({
    queryKey: ['paper-releases', source],
    queryFn: () => proApi.releases(source),
  });
  const deployments = useQuery({
    queryKey: ['pro-deployments', source],
    queryFn: () => proApi.deployments(source),
    refetchInterval: 5000,
  });
  const controllerState = (id?: string) =>
    !id
      ? t('Not activated')
      : deployments.isError
        ? t('Current state unavailable')
        : deployments.isPending
          ? t('Loading…')
          : (deployments.data?.items.find((item) => item.id === id)?.status ??
            t('Controller unavailable'));
  const activate = useMutation({
    mutationFn: ({ id }: { id: string; task: ReceiptTask }) => proApi.activateRelease(id),
    onSuccess: (release, submitted) => {
      void qc.invalidateQueries({ queryKey: ['paper-releases'] });
      void qc.invalidateQueries({ queryKey: ['pro-deployments'] });
      if (receipt.owns(submitted.task) && release.id === submitted.id && release.deployment_id)
        onInspectDeployment?.(release.deployment_id);
    },
  });
  const selected = releases.data?.items.find((item) => item.id === initialReleaseId);
  const rows = selected
    ? [selected, ...releases.data!.items.filter((item) => item.id !== initialReleaseId)]
    : (releases.data?.items ?? []);
  return (
    <>
      <p className="quiet-copy">
        {t(
          'Saved approvals retain the exact candidate, implementation, costs and risk policy. Activation rechecks current conditions.',
        )}
      </p>
      {initialReleaseId && releases.isSuccess && !selected && (
        <ErrorBox
          error={new Error(t('The selected paper approval is unavailable for this source.'))}
        />
      )}
      {selected && (
        <section className="selected-release" aria-label={t('Selected paper approval')}>
          <h3>{t('Selected paper approval')}</h3>
          <RecordGrid
            value={{
              release: selected.id,
              market: selected.config.inst_id,
              status: selected.status === 'deployed' ? t('Activation recorded') : selected.status,
              current_controller_state: controllerState(selected.deployment_id),
              evidence: selected.preview.selection_scope,
              reviewed_by: selected.approved_by,
              approved_at: selected.approved_at,
            }}
          />
          <p>{selected.review}</p>
          <p className="quiet-copy">
            {t(
              'Activation rechecks the current account. Approval alone does not start a controller.',
            )}
          </p>
          <JsonDetails value={selected.preview} label="Approval evidence" />
        </section>
      )}
      {activate.isError && receipt.owns(activate.variables?.task) && (
        <ErrorBox error={activate.error} />
      )}
      {releases.isPending ? (
        <Loading />
      ) : releases.isError ? (
        <ErrorBox error={releases.error} />
      ) : (
        <DataTable
          rows={rows}
          empty="No paper releases"
          columns={[
            { key: 'id', label: 'Release', render: (r) => <code>{r.id}</code> },
            { key: 'approved_at', label: 'Approved', render: (r) => date(r.approved_at) },
            { key: 'market', label: 'Market', render: (r) => String(r.config.inst_id) },
            { key: 'selection', label: 'Candidate', render: (r) => r.preview.selection },
            { key: 'review', label: 'Release review' },
            {
              key: 'status',
              label: 'Status',
              render: (r) => (
                <Status type="neutral">
                  {t(r.status === 'deployed' ? 'Activation recorded' : r.status)}
                </Status>
              ),
            },
            {
              key: 'current_state',
              label: 'Current controller state',
              render: (r) => t(controllerState(r.deployment_id)),
            },
            {
              key: 'actions',
              label: 'Actions',
              render: (r) => (
                <div className="table-actions">
                  {r.status === 'approved' && (
                    <button
                      className="text-button"
                      disabled={
                        !canOperate ||
                        (activate.isPending && receipt.owns(activate.variables?.task))
                      }
                      onClick={() => activate.mutate({ id: r.id, task: receipt.capture() })}
                    >
                      <Play size={12} />
                      {t('Activate release')}
                    </button>
                  )}
                  {r.deployment_id && onInspectDeployment && (
                    <button
                      className="text-button"
                      onClick={() => onInspectDeployment(r.deployment_id!)}
                    >
                      {t('Inspect deployment')}
                    </button>
                  )}
                  <JsonDetails value={r} />
                </div>
              ),
            },
          ]}
        />
      )}
    </>
  );
}
