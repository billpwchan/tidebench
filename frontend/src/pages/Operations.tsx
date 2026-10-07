import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DatabaseBackup, Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { proApi } from '../proApi';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { useDialogFocus } from '../lib/hooks';
import { ActionNote, ErrorBox, Field, Loading, PageHeading, Status } from '../components/workspace';
import {
  DataTable,
  JsonDetails,
  RecordGrid,
  WorkspaceTabs,
  valueText,
} from '../components/ProWorkspace';
import { useSession } from '../components/AuthGate';

const records = (v: unknown): RecordData[] =>
  Array.isArray(v) ? (v.filter((x) => x && typeof x === 'object') as RecordData[]) : [];
export default function Operations() {
  const { t } = useI18n();
  const session = useSession();
  const admin = session?.user?.role === 'admin';
  const qc = useQueryClient();
  const [tab, setTab] = useState('feeds');
  const [notice, setNotice] = useState<string | null>(null);
  const [verified, setVerified] = useState<RecordData | null>(null);
  const [backup, setBackup] = useState<RecordData | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const ops = useQuery({ queryKey: ['pro-ops'], queryFn: proApi.ops, refetchInterval: 5000 });
  const audit = useQuery({
    queryKey: ['pro-audit'],
    queryFn: proApi.audit,
    refetchInterval: 10000,
    enabled: tab === 'audit',
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    void qc.invalidateQueries({ queryKey: ['pro-audit'] });
  };
  const create = useMutation({
    mutationFn: proApi.createBackup,
    onSuccess: (r) => {
      setNotice(`${t('Backup created')} · ${r.id ?? r.backup_id ?? ''}`);
      refresh();
    },
  });
  const verify = useMutation({ mutationFn: proApi.verifyBackup, onSuccess: (r) => setVerified(r) });
  const restore = useMutation({
    mutationFn: proApi.restore,
    onSuccess: () => {
      setBackup(null);
      setConfirmation('');
      setNotice(t('Restore completed'));
      qc.clear();
      window.location.reload();
    },
  });
  useDialogFocus(!!backup, '.restore-dialog', () => setBackup(null));
  const data = ops.data;
  const health = data?.health;
  const healthRecord = typeof health === 'object' ? health : undefined;
  const healthStatus = typeof health === 'string' ? health : String(healthRecord?.status ?? '—');
  const feeds = records(data?.feeds),
    jobs = records(data?.jobs),
    backups = records(data?.backups),
    checkpoints = records(data?.checkpoints);
  const canRestore =
    admin &&
    !!backup &&
    confirmation === 'RESTORE' &&
    !restore.isPending &&
    verified?.verified === true &&
    verified.valid !== false &&
    verified.ok !== false &&
    verified.id === backup.id;
  return (
    <>
      <PageHeading
        eyebrow="OPERATIONS"
        title="Operations"
        description="Feed health, worker progress, durable state, and recovery controls."
      >
        <button className="button button-secondary" onClick={refresh}>
          <RefreshCw size={14} />
          {t('Refresh')}
        </button>
      </PageHeading>
      <ActionNote text={notice} />
      {ops.isPending ? (
        <Loading />
      ) : ops.isError ? (
        <ErrorBox error={ops.error} onRetry={() => void ops.refetch()} />
      ) : (
        <>
          <div className="ops-summary">
            <section className="pro-panel">
              <div className="section-heading">
                <h2>{t('Service health')}</h2>
                <Status
                  type={['ok', 'healthy', 'ready'].includes(healthStatus) ? 'good' : 'warning'}
                >
                  {healthStatus}
                </Status>
              </div>
              <RecordGrid value={healthRecord} />
              <JsonDetails value={healthRecord?.checks} label="Health checks" open />
            </section>
            <section className="pro-panel">
              <div className="section-heading">
                <h2>{t('Storage')}</h2>
                <DatabaseBackup size={18} />
              </div>
              <RecordGrid value={data?.storage} />
            </section>
          </div>
          <section className="pro-panel ops-workspace">
            <WorkspaceTabs
              value={tab}
              onChange={setTab}
              items={[
                { key: 'feeds', label: 'Feeds' },
                { key: 'jobs', label: 'Jobs' },
                { key: 'checkpoints', label: 'Checkpoints' },
                { key: 'backups', label: 'Backups' },
                { key: 'audit', label: 'Audit' },
                { key: 'metrics', label: 'Operational metrics' },
              ]}
            />
            {tab === 'feeds' && (
              <DataTable
                rows={feeds}
                columns={[
                  {
                    key: 'inst_id',
                    label: 'Market',
                    render: (r) => (
                      <div className="table-stacked">
                        <strong>{valueText(r.inst_id ?? r.name ?? r.source)}</strong>
                        <small>
                          {valueText(r.source)} · {valueText(r.transport)}
                        </small>
                      </div>
                    ),
                  },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (r) => (
                      <Status
                        type={
                          (r.status ?? r.state) === 'fresh' || (r.status ?? r.state) === 'healthy'
                            ? 'good'
                            : ['stale', 'degraded', 'unavailable'].includes(
                                  String(r.status ?? r.state),
                                ) || r.error
                              ? 'warning'
                              : 'neutral'
                        }
                      >
                        {valueText(r.status ?? r.state)}
                      </Status>
                    ),
                  },
                  {
                    key: 'last_exchange_ts',
                    label: 'Last exchange update',
                    render: (r) => date(Number(r.last_exchange_ts ?? r.exchange_ts ?? r.ts)),
                  },
                  {
                    key: 'age_ms',
                    label: 'Age',
                    render: (r) => (r.age_ms == null ? '—' : `${number(r.age_ms, 0)} ms`),
                  },
                  {
                    key: 'errors',
                    label: 'Errors',
                    render: (r) => valueText(r.error ?? r.last_error ?? r.errors),
                  },
                  { key: 'details', label: 'Details', render: (r) => <JsonDetails value={r} /> },
                ]}
              />
            )}
            {tab === 'jobs' && (
              <DataTable
                rows={jobs}
                columns={[
                  {
                    key: 'id',
                    label: 'Job',
                    render: (r) => String(r.id ?? r.name ?? '—').slice(0, 20),
                  },
                  { key: 'source', label: 'Source' },
                  { key: 'kind', label: 'Kind' },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (r) => (
                      <Status
                        type={
                          r.status === 'completed'
                            ? 'good'
                            : r.status === 'failed'
                              ? 'bad'
                              : 'neutral'
                        }
                      >
                        {valueText(r.status ?? r.state)}
                      </Status>
                    ),
                  },
                  { key: 'progress', label: 'Progress' },
                  {
                    key: 'updated_at',
                    label: 'Updated',
                    render: (r) => date(Number(r.updated_at)),
                  },
                  { key: 'error', label: 'Errors' },
                  { key: 'details', label: 'Details', render: (r) => <JsonDetails value={r} /> },
                ]}
              />
            )}
            {tab === 'checkpoints' && (
              <DataTable
                rows={checkpoints}
                empty="No checkpoints"
                columns={[
                  {
                    key: 'job_id',
                    label: 'Job',
                    render: (r) => <code>{String(r.job_id ?? r.id ?? '—').slice(0, 16)}</code>,
                  },
                  { key: 'cursor', label: 'Cursor', render: (r) => date(Number(r.cursor)) },
                  { key: 'rows', label: 'Rows' },
                  { key: 'pages', label: 'Pages' },
                  {
                    key: 'status',
                    label: 'Status',
                    render: (r) => <Status>{valueText(r.status)}</Status>,
                  },
                  { key: 'details', label: 'Details', render: (r) => <JsonDetails value={r} /> },
                ]}
              />
            )}
            {tab === 'backups' && (
              <>
                <div className="detail-action-bar">
                  <span>
                    {backups.length} {t('Backups')}
                  </span>
                  {admin && (
                    <button
                      className="button button-dark"
                      disabled={create.isPending}
                      onClick={() => create.mutate()}
                    >
                      {create.isPending ? (
                        <Loader2 size={14} className="spin" />
                      ) : (
                        <DatabaseBackup size={14} />
                      )}{' '}
                      {t('Create backup')}
                    </button>
                  )}
                </div>
                <DataTable
                  rows={backups}
                  empty="No backups"
                  columns={[
                    { key: 'id', label: 'Backup', render: (r) => valueText(r.id ?? r.backup_id) },
                    {
                      key: 'created_at',
                      label: 'Created',
                      render: (r) => date(Number(r.created_at)),
                    },
                    {
                      key: 'size_bytes',
                      label: 'Bytes',
                      render: (r) => number(r.size_bytes ?? r.size, 0),
                    },
                    {
                      key: 'hash',
                      label: 'Checksum',
                      render: (r) => <code>{String(r.sha256 ?? r.hash ?? '—').slice(0, 15)}</code>,
                    },
                    {
                      key: 'actions',
                      label: 'Actions',
                      render: (r) => (
                        <div className="table-actions">
                          <button
                            className="text-button"
                            disabled={verify.isPending}
                            onClick={() => verify.mutate(String(r.id ?? r.backup_id))}
                          >
                            <ShieldCheck size={12} />
                            {t('Verify backup')}
                          </button>
                          {admin && (
                            <button
                              className="text-button danger-text"
                              disabled={verify.isPending}
                              onClick={() => {
                                setBackup(r);
                                setVerified(null);
                                setConfirmation('');
                                verify.mutate(String(r.id ?? r.backup_id));
                              }}
                            >
                              {t('Restore backup')}
                            </button>
                          )}
                        </div>
                      ),
                    },
                  ]}
                />
                {create.isError && <ErrorBox error={create.error} />}
                {verify.isError && <ErrorBox error={verify.error} />}
                {verified && !backup && (
                  <div className="backup-verification">
                    <h3>{t('Backup verification')}</h3>
                    <RecordGrid value={verified} />
                    <JsonDetails value={verified} />
                  </div>
                )}
              </>
            )}
            {tab === 'audit' &&
              (audit.isPending ? (
                <Loading />
              ) : audit.isError ? (
                <ErrorBox error={audit.error} />
              ) : (
                <DataTable
                  rows={audit.data.items}
                  empty="No audit events"
                  columns={[
                    { key: 'ts', label: 'Time', render: (r) => date(Number(r.ts ?? r.created_at)) },
                    {
                      key: 'actor',
                      label: 'User',
                      render: (r) => valueText(r.actor ?? r.username ?? r.user_id),
                    },
                    { key: 'kind', label: 'Kind' },
                    { key: 'summary', label: 'Details' },
                    {
                      key: 'details',
                      label: 'Inspect',
                      render: (r) => <JsonDetails value={r.details ?? r} />,
                    },
                  ]}
                />
              ))}
            {tab === 'metrics' && (
              <>
                <RecordGrid value={data?.metrics} />
                <h3 className="result-subheading">{t('Execution')}</h3>
                <RecordGrid value={data?.execution as RecordData | undefined} />
                <JsonDetails value={data} label="Details" />
              </>
            )}
          </section>
        </>
      )}
      {backup && (
        <div className="modal-backdrop" onClick={() => setBackup(null)}>
          <section
            className="wide-dialog restore-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={t('Restore backup')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="dialog-heading">
              <h2>{t('Restore backup')}</h2>
              <button
                className="icon-button"
                aria-label={t('Close')}
                onClick={() => setBackup(null)}
              >
                <X size={19} />
              </button>
            </div>
            <p className="restore-warning">
              {t(
                'Restore replaces server state from the selected backup. Active workers are quiesced by the server.',
              )}
            </p>
            <RecordGrid value={backup} />
            {verify.isPending ? (
              <Loading label="Verifying backup…" />
            ) : verified ? (
              <>
                <h3>{t('Backup verification')}</h3>
                <RecordGrid value={verified} />
                <JsonDetails value={verified} />
              </>
            ) : verify.isError ? (
              <ErrorBox error={verify.error} />
            ) : null}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (canRestore) restore.mutate(String(backup.id ?? backup.backup_id));
              }}
            >
              <Field label="Type RESTORE to confirm">
                <input
                  autoComplete="off"
                  spellCheck={false}
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                />
              </Field>
              {restore.isError && <ErrorBox error={restore.error} />}
              <button className="button button-danger full-width" disabled={!canRestore}>
                {restore.isPending ? (
                  <Loader2 size={14} className="spin" />
                ) : (
                  <DatabaseBackup size={14} />
                )}{' '}
                {t('Restore backup')}
              </button>
            </form>
          </section>
        </div>
      )}
    </>
  );
}
