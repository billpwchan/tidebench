import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails } from './ProWorkspace';
import { ErrorBox, Loading, Status } from './workspace';
import { date } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';

export default function ReleaseHistory({ source }: { source: Source }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canTrade(useSession()?.user?.role);
  const releases = useQuery({
    queryKey: ['paper-releases', source],
    queryFn: () => proApi.releases(source),
  });
  const activate = useMutation({
    mutationFn: proApi.activateRelease,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['paper-releases'] });
      void qc.invalidateQueries({ queryKey: ['pro-deployments'] });
    },
  });
  return (
    <>
      <p className="quiet-copy">
        {t(
          'Saved approvals retain the exact candidate, implementation, costs and risk policy. Activation rechecks current conditions.',
        )}
      </p>
      {activate.isError && <ErrorBox error={activate.error} />}
      {releases.isPending ? (
        <Loading />
      ) : releases.isError ? (
        <ErrorBox error={releases.error} />
      ) : (
        <DataTable
          rows={releases.data?.items ?? []}
          empty="No paper releases"
          columns={[
            { key: 'approved_at', label: 'Approved', render: (r) => date(r.approved_at) },
            { key: 'market', label: 'Market', render: (r) => String(r.config.inst_id) },
            { key: 'selection', label: 'Candidate', render: (r) => r.preview.selection },
            { key: 'review', label: 'Release review' },
            {
              key: 'status',
              label: 'Status',
              render: (r) => (
                <Status type={r.status === 'deployed' ? 'good' : 'neutral'}>{t(r.status)}</Status>
              ),
            },
            {
              key: 'actions',
              label: 'Actions',
              render: (r) => (
                <div className="table-actions">
                  {r.status === 'approved' && (
                    <button
                      className="text-button"
                      disabled={!canOperate || activate.isPending}
                      onClick={() => activate.mutate(r.id)}
                    >
                      <Play size={12} />
                      {t('Activate release')}
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
