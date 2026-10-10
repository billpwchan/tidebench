import PortfolioRiskEvidence from './PortfolioRiskEvidence';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Play, Square, X } from 'lucide-react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { ManagedPortfolio, RecordData } from '../proApi';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid, valueText } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Status } from './workspace';
import { date, number, quantityText } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { canTrade } from '../lib/permissions';
import { useDialogFocus } from '../lib/hooks';
import { useReceiptOwnership, type ReceiptTask } from '../lib/receiptOwnership';

type ProtectionCallbacks = {
  onProtectPosition?: (position: RecordData) => void;
  onInspectPositions?: () => void;
};

export default function ManagedPortfolios({
  source,
  initialGroupId,
  onProtectPosition,
  onInspectPositions,
  onSelectGroup,
  initialReleaseId,
}: {
  source: Source;
  initialGroupId?: string;
  initialReleaseId?: string;
  onSelectGroup?: (id: string) => void;
} & ProtectionCallbacks) {
  const { t, language } = useI18n();
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
  const [selected, setSelected] = useState(initialGroupId ?? '');
  const receipt = useReceiptOwnership(
    JSON.stringify([source, initialGroupId, initialReleaseId, selected]),
  );
  useEffect(() => setSelected(initialGroupId ?? ''), [initialGroupId]);
  const group = selected
    ? groups.data?.items.find((g) => g.id === selected)
    : groups.data?.items[0];
  const refresh = () => {
    for (const key of [
      'managed-portfolios',
      'portfolio-releases',
      'pro-deployments',
      'pro-account',
      'contributions',
    ])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const activate = useMutation({
    mutationFn: ({ id }: { id: string; task: ReceiptTask }) => proApi.activatePortfolioRelease(id),
    onSuccess: (r, submitted) => {
      refresh();
      if (
        receipt.owns(submitted.task) &&
        r.id === submitted.id &&
        r.source === source &&
        r.group_id
      ) {
        setSelected(r.group_id);
        onSelectGroup?.(r.group_id);
      }
    },
  });
  const approved = releases.data?.items.filter((r) => r.status === 'approved') ?? [];
  const selectedRelease = releases.data?.items.find((r) => r.id === initialReleaseId);
  const releaseGroup = groups.data?.items.find((g) => g.id === selectedRelease?.group_id);
  const currentGroupState = groups.isError
    ? (language === 'zh-CN' ? '不可用' : 'Unavailable') +
      (releaseGroup
        ? ` · ${language === 'zh-CN' ? '最后已知状态' : 'Last known'}: ${t(releaseGroup.status)}`
        : '')
    : groups.isPending
      ? t('Loading…')
      : releaseGroup
        ? t(releaseGroup.status)
        : language === 'zh-CN'
          ? '不可用'
          : 'Unavailable';
  const releaseTable = (rows: typeof approved) => (
    <DataTable
      rows={rows}
      columns={[
        { key: 'name', label: 'Portfolio', render: (r) => r.approval.preview.name },
        { key: 'id', label: 'Release' },
        { key: 'reviewer', label: 'Reviewed by', render: (r) => r.approval.actor },
        { key: 'created_at', label: 'Approved', render: (r) => date(r.created_at, true) },
        {
          key: 'action',
          label: 'Actions',
          render: (r) => (
            <button
              className="button button-secondary"
              disabled={!allowed || (activate.isPending && receipt.owns(activate.variables?.task))}
              onClick={() => activate.mutate({ id: r.id, task: receipt.capture() })}
            >
              <Play size={13} />
              {t('Activate')}
            </button>
          ),
        },
      ]}
    />
  );
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
      {initialReleaseId && (
        <section className="portfolio-release-review">
          <h3>{language === 'zh-CN' ? '指定组合审批' : 'Selected portfolio release'}</h3>
          {releases.isPending ? (
            <Loading />
          ) : releases.isSuccess && !selectedRelease ? (
            <ErrorBox
              error={
                new Error(
                  language === 'zh-CN'
                    ? '指定的组合审批在此账户中不可用。'
                    : 'Selected portfolio release is unavailable in this account.',
                )
              }
            />
          ) : selectedRelease?.status === 'approved' ? (
            releaseTable([selectedRelease])
          ) : selectedRelease ? (
            <>
              <RecordGrid value={{ release: selectedRelease.id, source }} />
              <dl className="record-grid">
                <div>
                  <dt>{language === 'zh-CN' ? '审批记录' : 'Approval record'}</dt>
                  <dd>
                    {selectedRelease.status === 'deployed'
                      ? language === 'zh-CN'
                        ? '已记录激活'
                        : 'Activation recorded'
                      : t(selectedRelease.status)}
                  </dd>
                </div>
                <div>
                  <dt>{language === 'zh-CN' ? '当前组合状态' : 'Current group state'}</dt>
                  <dd>{currentGroupState}</dd>
                </div>
              </dl>
              {selectedRelease.group_id && (
                <button
                  className="text-button"
                  onClick={() => {
                    receipt.invalidate();
                    setSelected(selectedRelease.group_id!);
                    onSelectGroup?.(selectedRelease.group_id!);
                  }}
                >
                  {t('Inspect group & recovery')}
                </button>
              )}
            </>
          ) : null}
        </section>
      )}
      {groups.isError ? (
        <ErrorBox error={groups.error} />
      ) : groups.isPending ? (
        <Loading />
      ) : selected && !group ? (
        <>
          <ErrorBox
            error={
              new Error(
                language === 'zh-CN'
                  ? '指定的组合在此账户中不可用。'
                  : 'Selected portfolio is unavailable in this account.',
              )
            }
          />
          <button
            className="text-button"
            onClick={() => {
              receipt.invalidate();
              setSelected('');
              onSelectGroup?.('');
            }}
          >
            {language === 'zh-CN' ? '查看全部组合' : 'Show all portfolios'}
          </button>
        </>
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
                onClick={() => {
                  receipt.invalidate();
                  setSelected(g.id);
                  onSelectGroup?.(g.id);
                }}
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
          <GroupEvidence
            key={group.id}
            group={group}
            allowed={allowed}
            onChange={refresh}
            onProtectPosition={onProtectPosition}
            onInspectPositions={onInspectPositions}
          />
        </>
      )}
      {releases.isError && <ErrorBox error={releases.error} />}
      {approved.filter((r) => r.id !== initialReleaseId).length > 0 && (
        <section className="portfolio-release-review">
          <h3>{t('Approved portfolios awaiting activation')}</h3>
          {releaseTable(approved.filter((r) => r.id !== initialReleaseId))}
        </section>
      )}
      {activate.isError && receipt.owns(activate.variables?.task) && (
        <ErrorBox error={activate.error} />
      )}
    </div>
  );
}

function GroupEvidence(
  props: { group: ManagedPortfolio; allowed: boolean; onChange: () => void } & ProtectionCallbacks,
) {
  const { language } = useI18n();
  const [confirmStop, setConfirmStop] = useState(false);
  const [stopped, setStopped] = useState(false);
  const stop = () => setConfirmStop(true);
  return (
    <>
      {confirmStop && (
        <StopPortfolioDialog
          source={props.group.source}
          groupId={props.group.id}
          name={props.group.manifest?.name}
          markets={props.group.manifest?.legs.map((leg) => leg.inst_id)}
          onClose={() => setConfirmStop(false)}
          onStopped={() => {
            setConfirmStop(false);
            setStopped(true);
            props.onChange();
          }}
        />
      )}
      {stopped && (
        <p className="action-note" role="status">
          {language === 'zh-CN'
            ? '整个组合已停止。已成交库存仍保留在账户中；停止不代表清仓。'
            : 'Whole portfolio stopped. Filled inventory remains in the account; stopping does not close positions.'}
        </p>
      )}
      {props.group.manifest ? (
        <VerifiedGroupEvidence
          {...props}
          onStop={stop}
          group={{ ...props.group, manifest: props.group.manifest }}
        />
      ) : (
        <GroupIntegrityError {...props} onStop={stop} />
      )}
    </>
  );
}

export function StopPortfolioDialog({
  source,
  groupId,
  name,
  markets,
  onClose,
  onStopped,
}: {
  source: Source;
  groupId: string;
  name?: string;
  markets?: string[];
  onClose: () => void;
  onStopped: () => void;
}) {
  const { t, language } = useI18n();
  const allowed = canTrade(useSession()?.user?.role);
  const stop = useMutation({
    mutationFn: async () => {
      const result = await proApi.stopPortfolio(groupId);
      if (result.id !== groupId || result.source !== source || result.status !== 'stopped')
        throw new Error(
          language === 'zh-CN'
            ? '组合尚未确认停止，请检查当前组合状态。'
            : 'Portfolio stop is not confirmed. Inspect the current group state.',
        );
      return result;
    },
    onSuccess: onStopped,
  });
  const close = () => {
    if (!stop.isPending) onClose();
  };
  useDialogFocus(true, '.portfolio-stop-dialog', close);
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="wide-dialog portfolio-stop-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t('Stop whole portfolio')}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <h2>{t('Stop whole portfolio')}</h2>
          <button
            className="icon-button"
            aria-label={t('Close')}
            disabled={stop.isPending}
            onClick={close}
          >
            <X size={18} />
          </button>
        </div>
        <strong>{name ?? groupId}</strong>
        <RecordGrid value={{ source, portfolio_group: groupId }} />
        <p>
          {language === 'zh-CN'
            ? '此操作会停止整个组合的所有市场控制器，而不只是选中的单腿。'
            : 'This stops every market controller in the whole portfolio, including all other legs.'}
        </p>
        {markets?.length ? (
          <p>{markets.join(' · ')}</p>
        ) : (
          <p className="inline-warning">
            {language === 'zh-CN'
              ? '组合市场证据不可用；停止仍作用于整个组。'
              : 'Portfolio market evidence is unavailable; stopping still applies to the whole group.'}
          </p>
        )}
        <p className="inline-warning">
          {language === 'zh-CN'
            ? '已成交库存保留在账户中。此操作不会清仓；仍需查看账户仓位并执行只减仓退出。'
            : 'Filled inventory stays in the account. This does not close positions; inspect account positions and use reduce-only exits separately.'}
        </p>
        {stop.isError && <ErrorBox error={stop.error} />}
        <div className="toolbar">
          <button
            className="button button-danger"
            disabled={!allowed || stop.isPending}
            onClick={() => stop.mutate()}
          >
            <Square size={13} />
            {language === 'zh-CN' ? '确认停止整个组合' : 'Confirm stop whole portfolio'}
          </button>
          <button className="button button-secondary" disabled={stop.isPending} onClick={close}>
            {t('Cancel')}
          </button>
        </div>
      </section>
    </div>
  );
}

function GroupIntegrityError({
  group,
  allowed,
  onInspectPositions,
  onStop,
}: {
  group: ManagedPortfolio;
  allowed: boolean;
  onChange: () => void;
  onStop: () => void;
} & ProtectionCallbacks) {
  const { t } = useI18n();
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
            disabled={!allowed || group.status === 'stopped'}
            onClick={onStop}
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
      <p className="inline-warning">
        {t(
          'Group ownership is unavailable. Account net positions remain separate and can be inspected for protection.',
        )}
      </p>
      {onInspectPositions && (
        <button type="button" className="button button-secondary" onClick={onInspectPositions}>
          {t('Inspect account positions')}
        </button>
      )}
      {group.last_error && <ErrorBox error={new Error(group.last_error)} />}
    </section>
  );
}

function VerifiedGroupEvidence({
  group,
  allowed,
  onProtectPosition,
  onInspectPositions,
  onStop,
}: {
  group: ManagedPortfolio & { manifest: NonNullable<ManagedPortfolio['manifest']> };
  allowed: boolean;
  onChange: () => void;
  onStop: () => void;
} & ProtectionCallbacks) {
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
  const contributions = useQuery({
    queryKey: ['contributions', group.source],
    queryFn: () => proApi.contributions(group.source),
    refetchInterval: 5000,
  });
  // A successful report has verified sleeve hashes and economic quantities.
  // Monetary P&L can remain unavailable when marks or funding are incomplete.
  const ownershipKnown = contributions.isSuccess;
  const groupOwner = `portfolio:${group.id}`;
  const owner = ownershipKnown
    ? contributions.data.owners.find((row) => row.owner === groupOwner)
    : undefined;
  const ownedQuantity = (symbol: string) =>
    owner?.markets.find((row) => row.inst_id === symbol)?.quantity ?? '0';
  const inventoryOwners = (symbol: string) =>
    ownershipKnown
      ? contributions.data.owners.flatMap((row) =>
          row.markets
            .filter((market) => market.inst_id === symbol && Number(market.quantity) !== 0)
            .map((market) => ({ owner: row.owner, quantity: market.quantity })),
        )
      : [];
  const batch = history.data?.items.find((b) => b.id === selectedBatch) ?? history.data?.items[0];
  const adjustments = [
    ...(batch?.body.reduction_skips ?? []),
    ...(batch?.additions?.skipped ?? []),
  ].filter((row) => row.code === 'rebalance_minimum');
  const riskDecision = batch?.body.risk_evidence
    ? [
        {
          risk_evidence: batch.body.risk_evidence,
          ts: batch.body.available_at,
          rebalance_due: batch.body.rebalance_due,
        },
      ]
    : [];
  const active = ['running', 'compensating', 'failed'].includes(group.status);
  return (
    <section className="managed-group-evidence">
      <PortfolioRiskEvidence decisions={riskDecision} />
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
            disabled={!allowed || !active}
            onClick={onStop}
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
            label: 'Group-owned inventory',
            render: (r) =>
              contributions.isPending
                ? '…'
                : ownershipKnown
                  ? quantityText(ownedQuantity(r.inst_id))
                  : t('Unknown'),
          },
          {
            key: 'account_quantity',
            label: 'Account net inventory',
            render: (r) => {
              const position = account.data?.positions?.find((p) => p.inst_id === r.inst_id);
              return account.isPending
                ? '…'
                : account.isError
                  ? t('Unknown')
                  : quantityText(position?.quantity ?? '0');
            },
          },
          {
            key: 'inventory_owners',
            label: 'Current inventory owners',
            render: (r) =>
              ownershipKnown ? (
                <div className="table-stacked">
                  {inventoryOwners(r.inst_id).length ? (
                    inventoryOwners(r.inst_id).map((row) => (
                      <small key={row.owner}>
                        {row.owner} · {quantityText(row.quantity)}
                      </small>
                    ))
                  ) : (
                    <span>{t('No inventory')}</span>
                  )}
                </div>
              ) : (
                t('Unknown')
              ),
          },
          {
            key: 'unit',
            label: 'Units',
            render: (r) => t(r.inst_id.endsWith('-SWAP') ? 'Contracts' : 'Base units'),
          },
          ...(onProtectPosition
            ? [
                {
                  key: 'protection',
                  label: 'Actions',
                  render: (r: (typeof group.manifest.legs)[number]) => {
                    const position = account.data?.positions?.find((p) => p.inst_id === r.inst_id);
                    if (
                      !ownershipKnown ||
                      Number(ownedQuantity(r.inst_id)) === 0 ||
                      account.isError ||
                      !position ||
                      Number(position.quantity) === 0
                    )
                      return '—';
                    return (
                      <button
                        type="button"
                        className="button button-secondary"
                        disabled={!allowed}
                        onClick={() => onProtectPosition(position)}
                      >
                        {t('Reduce account net position')}
                      </button>
                    );
                  },
                },
              ]
            : []),
        ]}
      />
      <p className="snapshot-footnote">
        {t('Inventory ownership observed')}: {date(contributions.data?.as_of, true)}
        {' · '}
        {t('Account snapshot')}: {date(account.data?.as_of, true)}
      </p>
      <p className="quiet-copy">
        {t(
          'Reducing an account net position affects every current inventory owner in that market. It does not exclusively close this portfolio.',
        )}
      </p>
      {ownershipKnown && !contributions.data.reconciled && (
        <p className="inline-warning">
          {t(
            'Group inventory quantities are verified; monetary valuation or reconciliation remains incomplete.',
          )}
        </p>
      )}
      {!ownershipKnown && !contributions.isPending && (
        <p className="inline-warning">
          {t(
            'Group ownership is unavailable. Account net positions remain separate and can be inspected for protection.',
          )}
        </p>
      )}
      {contributions.isError && (
        <ErrorBox error={contributions.error} onRetry={() => void contributions.refetch()} />
      )}
      {account.isError && <ErrorBox error={account.error} />}
      {onInspectPositions && (
        <button type="button" className="text-button" onClick={onInspectPositions}>
          {t('Inspect account positions')}
        </button>
      )}
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
