import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Play, Square } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { ManagedPortfolio } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid, valueText } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Status } from './workspace';
import { date, number, quantityText } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';

export default function ManagedPortfolios({ source }: { source: Source }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const allowed = canTrade(useSession()?.user?.role);
  const groups = useQuery({
    queryKey: ['managed-portfolios', source],
    queryFn: () => proApi.managedPortfolios(source),
    refetchInterval: 5000,
  });
  const releases = useQuery({
    queryKey: ['portfolio-releases', source],
    queryFn: () => proApi.portfolioReleases(source),
    refetchInterval: 10000,
  });
  const [selected, setSelected] = useState('');
  const group = groups.data?.items.find((g) => g.id === selected) ?? groups.data?.items[0];
  const refresh = () => {
    for (const key of [
      'managed-portfolios',
      'portfolio-releases',
      'pro-deployments',
      'pro-account',
    ])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const activate = useMutation({
    mutationFn: proApi.activatePortfolioRelease,
    onSuccess: (r) => {
      setSelected(r.group_id!);
      refresh();
    },
  });
  const approved = releases.data?.items.filter((r) => r.status === 'approved') ?? [];
  return (
    <div className="managed-portfolios">
      <div className="section-heading">
        <div>
          <h2>{t('Managed portfolios')}</h2>
          <p className="quiet-copy">
            {t(
              'Reviewed groups share account capital. Targets, sequential fills and failure reductions remain inspectable.',
            )}
          </p>
        </div>
      </div>
      {groups.isError ? (
        <ErrorBox error={groups.error} />
      ) : groups.isPending ? (
        <Loading />
      ) : !group ? (
        <Empty title="No managed portfolio yet">
          {t(
            'Run a versioned portfolio study, review the complete group, then activate local paper trading.',
          )}
        </Empty>
      ) : (
        <>
          <div className="managed-group-list">
            {groups.data?.items.map((g) => (
              <button
                key={g.id}
                className={`strategy-project${g.id === group.id ? ' active' : ''}`}
                onClick={() => setSelected(g.id)}
              >
                <strong>{g.manifest?.name ?? `${t('Portfolio')} ${g.id.slice(0, 8)}`}</strong>
                <span>
                  {g.manifest ? (
                    <>
                      v{g.manifest.version_revision} · {g.manifest.legs.length} {t('legs')} ·{' '}
                    </>
                  ) : (
                    <>{g.integrity_error?.code ?? 'portfolio_evidence_integrity'} · </>
                  )}
                  {t(g.status)} · {date(g.created_at, true)}
                </span>
              </button>
            ))}
          </div>
          <GroupEvidence key={group.id} group={group} allowed={allowed} onChange={refresh} />
        </>
      )}
      {releases.isError && <ErrorBox error={releases.error} />}
      {approved.length > 0 && (
        <section className="portfolio-release-review">
          <h3>{t('Approved portfolios awaiting activation')}</h3>
          <DataTable
            rows={approved}
            columns={[
              { key: 'name', label: 'Portfolio', render: (r) => r.approval.preview.name },
              { key: 'reviewer', label: 'Reviewed by', render: (r) => r.approval.actor },
              { key: 'created_at', label: 'Approved', render: (r) => date(r.created_at, true) },
              {
                key: 'action',
                label: 'Actions',
                render: (r) => (
                  <button
                    className="button button-secondary"
                    disabled={!allowed || activate.isPending}
                    onClick={() => activate.mutate(r.id)}
                  >
                    <Play size={13} />
                    {t('Activate')}
                  </button>
                ),
              },
            ]}
          />
        </section>
      )}
      {activate.isError && <ErrorBox error={activate.error} />}
    </div>
  );
}

function GroupEvidence(props: { group: ManagedPortfolio; allowed: boolean; onChange: () => void }) {
  return props.group.manifest ? (
    <VerifiedGroupEvidence {...props} group={{ ...props.group, manifest: props.group.manifest }} />
  ) : (
    <GroupIntegrityError {...props} />
  );
}

function GroupIntegrityError({
  group,
  allowed,
  onChange,
}: {
  group: ManagedPortfolio;
  allowed: boolean;
  onChange: () => void;
}) {
  const { t } = useI18n();
  const stop = useMutation({
    mutationFn: () => proApi.stopPortfolio(group.id),
    onSuccess: onChange,
  });
  return (
    <section className="managed-group-evidence">
      <div className="section-heading">
        <h3>
          {t('Portfolio')} {group.id.slice(0, 8)}
        </h3>
        <div className="portfolio-release-actions">
          <Status type="bad">{t(group.status)}</Status>
          <button
            className="button button-secondary"
            disabled={!allowed || group.status === 'stopped' || stop.isPending}
            onClick={() => stop.mutate()}
          >
            <Square size={13} />
            {t('Stop whole portfolio')}
          </button>
        </div>
      </div>
      <ErrorBox
        error={
          new Error(group.integrity_error?.message ?? 'Portfolio evidence integrity check failed.')
        }
      />
      <p className="quiet-copy">
        {t(
          'Stopping retains filled inventory. A failed leg reduces the group; blocked reductions remain visible until recovered or stopped.',
        )}
      </p>
      <RecordGrid
        value={{
          id: group.id,
          source: group.source,
          version_id: group.version_id,
          release_id: group.release_id,
          status: t(group.status),
        }}
      />
      {group.last_error && <ErrorBox error={new Error(group.last_error)} />}
      {stop.isError && <ErrorBox error={stop.error} />}
    </section>
  );
}

function VerifiedGroupEvidence({
  group,
  allowed,
  onChange,
}: {
  group: ManagedPortfolio & { manifest: NonNullable<ManagedPortfolio['manifest']> };
  allowed: boolean;
  onChange: () => void;
}) {
  const { t } = useI18n();
  const [before, setBefore] = useState<number>();
  const [selectedBatch, setSelectedBatch] = useState('');
  const history = useQuery({
    queryKey: ['portfolio-batches', group.id, before],
    queryFn: () => proApi.portfolioBatches(group.id, before),
    refetchInterval: 5000,
  });
  const account = useQuery({
    queryKey: ['pro-account', group.source],
    queryFn: () => proApi.account(group.source),
    refetchInterval: 5000,
  });
  const batch = history.data?.items.find((b) => b.id === selectedBatch) ?? history.data?.items[0];
  const adjustments = [
    ...(batch?.body.reduction_skips ?? []),
    ...(batch?.additions?.skipped ?? []),
  ].filter((row) => row.code === 'rebalance_minimum');
  const stop = useMutation({
    mutationFn: () => proApi.stopPortfolio(group.id),
    onSuccess: onChange,
  });
  const active = ['running', 'compensating'].includes(group.status);
  return (
    <section className="managed-group-evidence">
      <div className="section-heading">
        <div>
          <h3>{group.manifest.name}</h3>
          <p className="quiet-copy">
            {t('Last completed decision')} · {group.last_bar ? date(group.last_bar, true) : '—'}
          </p>
        </div>
        <div className="portfolio-release-actions">
          <Status
            type={
              group.status === 'running' ? 'good' : group.status === 'stopped' ? 'neutral' : 'bad'
            }
          >
            {t(group.status)}
          </Status>
          <button
            className="button button-secondary"
            disabled={!allowed || !active || stop.isPending}
            onClick={() => stop.mutate()}
          >
            <Square size={13} />
            {t('Stop whole portfolio')}
          </button>
        </div>
      </div>
      <p className="quiet-copy">
        {t(
          'Stopping retains filled inventory. A failed leg reduces the group; blocked reductions remain visible until recovered or stopped.',
        )}
      </p>
      {group.last_error && <ErrorBox error={new Error(group.last_error)} />}
      {stop.isError && <ErrorBox error={stop.error} />}
      <DataTable
        rows={group.manifest.legs}
        columns={[
          { key: 'inst_id', label: 'Market' },
          {
            key: 'weight',
            label: 'Target weight',
            render: (r) => `${number(Number(r.weight) * 100)}%`,
          },
          { key: 'leverage', label: 'Leverage' },
          {
            key: 'quantity',
            label: 'Actual inventory',
            render: (r) => {
              const position = account.data?.positions?.find((p) => p.inst_id === r.inst_id);
              return account.isPending
                ? '…'
                : account.isError
                  ? '—'
                  : quantityText(position?.quantity ?? '0');
            },
          },
          {
            key: 'unit',
            label: 'Units',
            render: (r) => t(r.inst_id.endsWith('-SWAP') ? 'Contracts' : 'Base units'),
          },
        ]}
      />
      {account.isError && <ErrorBox error={account.error} />}
      <div className="section-heading">
        <h3>{t('Persisted execution batches')}</h3>
        <div className="portfolio-release-actions">
          {before !== undefined && (
            <button
              className="text-button"
              onClick={() => {
                setBefore(undefined);
                setSelectedBatch('');
              }}
            >
              {t('Latest')}
            </button>
          )}
          {(history.data?.items.length ?? 0) >= 30 && (
            <button
              className="text-button"
              onClick={() => {
                setBefore(history.data!.items.at(-1)!.bar);
                setSelectedBatch('');
              }}
            >
              {t('Older batches')}
            </button>
          )}
        </div>
      </div>
      {history.isError ? (
        <ErrorBox error={history.error} />
      ) : history.isPending ? (
        <Loading />
      ) : !batch ? (
        <p className="quiet-copy">{t('Waiting for complete confirmed history across all legs.')}</p>
      ) : (
        <>
          <div className="managed-batch-selector">
            {history.data?.items.map((b) => (
              <button
                key={b.id}
                className={`text-button${b.id === batch.id ? ' selected' : ''}`}
                aria-pressed={b.id === batch.id}
                onClick={() => setSelectedBatch(b.id)}
              >
                {date(b.bar, true)} · {t(b.status)}
              </button>
            ))}
          </div>
          {batch.error && <ErrorBox error={new Error(batch.error)} />}
          <RecordGrid
            value={{
              status: t(batch.status),
              decision_bar: date(batch.bar, true),
              capital: batch.body.capital,
              cash_scale: batch.additions?.cash_scale,
              residual_pct: batch.residuals?.capital_pct,
              rebalance_due: batch.body.rebalance_due,
            }}
          />
          <DataTable
            rows={Object.entries(batch.body.targets).map(([inst_id, quantity]) => ({
              inst_id,
              quantity,
              residual: batch.residuals?.quantities[inst_id],
            }))}
            columns={[
              { key: 'inst_id', label: 'Market' },
              { key: 'quantity', label: 'Frozen target', render: (r) => quantityText(r.quantity) },
              {
                key: 'residual',
                label: 'Quantity residual',
                render: (r) => quantityText(r.residual),
              },
            ]}
          />
          {adjustments.length > 0 && (
            <>
              <p className="quiet-copy">
                {t(
                  'Small adjustments remain as visible residuals within the reviewed limit. Full exits and side changes still reduce inventory.',
                )}
              </p>
              <DataTable
                rows={adjustments}
                columns={[
                  { key: 'inst_id', label: 'Market' },
                  {
                    key: 'requested_quantity',
                    label: 'Deferred adjustment',
                    render: (r) => quantityText(r.requested_quantity),
                  },
                  {
                    key: 'minimum_size',
                    label: 'Minimum order size',
                    render: (r) => quantityText(r.minimum_size),
                  },
                ]}
              />
            </>
          )}
          <DataTable
            rows={batch.commands}
            columns={[
              { key: 'phase', label: 'Phase', render: (r) => t(r.phase) },
              { key: 'market', label: 'Market', render: (r) => valueText(r.payload.inst_id) },
              { key: 'side', label: 'Side', render: (r) => t(valueText(r.payload.side)) },
              {
                key: 'quantity',
                label: 'Quantity',
                render: (r) => quantityText(r.payload.quantity),
              },
              {
                key: 'status',
                label: 'Status',
                render: (r) => (r.order?.status ? t(String(r.order.status)) : t(r.status)),
              },
              {
                key: 'price',
                label: 'Actual fill',
                render: (r) => (r.order ? number(r.order.price, 8) : '—'),
              },
              {
                key: 'quote',
                label: 'Observed quote',
                render: (r) => (r.order ? date(Number(r.order.quote_ts), true) : '—'),
              },
            ]}
          />
          <JsonDetails value={batch} label="Target, command and fill evidence" />
        </>
      )}
      <JsonDetails value={group.manifest} label="Portfolio release identity" />
    </section>
  );
}
