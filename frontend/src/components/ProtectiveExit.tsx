import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, RefreshCw, ShieldMinus, X } from 'lucide-react';
import { useRef, useState } from 'react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import type { OrderRequest, RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number, price, quantityText } from '../lib/format';
import { useDialogFocus } from '../lib/hooks';
import { canTrade } from '../lib/permissions';
import { useSession } from './AuthGate';
import { DataTable, RecordGrid } from './ProWorkspace';
import { ErrorBox, Field, Loading, Status } from './workspace';
import './ProtectiveExit.css';

const absolute = (value: unknown) => String(value ?? '').replace(/^-/, '');
const positionIdentity = (position?: RecordData) =>
  JSON.stringify(
    position && {
      inst_id: position.inst_id,
      quantity: position.quantity,
      entry_price: position.entry_price,
      leverage: position.leverage,
      generation: (position.instrument as RecordData | undefined)?.position_generation,
    },
  );

export default function ProtectiveExit({
  source,
  position,
  onClose,
}: {
  source: Source;
  position: RecordData;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const allowed = canTrade(useSession()?.user?.role);
  const qc = useQueryClient();
  const symbol = String(position.inst_id);
  const [reviewed, setReviewed] = useState(position);
  const [quantity, setQuantity] = useState(absolute(position.quantity));
  const command = useRef<{ payload: string; key: string } | null>(null);
  const account = useQuery({
    queryKey: ['pro-account', source],
    queryFn: () => proApi.account(source),
    refetchInterval: 2000,
  });
  const deployments = useQuery({
    queryKey: ['pro-deployments', source],
    queryFn: () => proApi.deployments(source),
    refetchInterval: 2000,
  });
  const orders = useQuery({
    queryKey: ['pro-orders', source],
    queryFn: () => proApi.orders(source),
    refetchInterval: 2000,
  });
  const contributions = useQuery({
    queryKey: ['contributions', source],
    queryFn: () => proApi.contributions(source),
    refetchInterval: 2000,
  });
  const current = account.data?.positions?.find((item) => item.inst_id === symbol);
  const controllers = (deployments.data?.items ?? []).filter(
    (item) => item.inst_id === symbol && item.status === 'running',
  );
  const entries = (orders.data?.items ?? []).filter(
    (item) => item.inst_id === symbol && item.status === 'pending' && item.reduce_only !== true,
  );
  const complete = [account, deployments, orders].every((query) => query.isSuccess);
  const stopped = complete && !controllers.length && !entries.length;
  const changed = positionIdentity(current) !== positionIdentity(reviewed);
  const body: OrderRequest = {
    source,
    inst_id: symbol,
    side: String(reviewed.quantity).startsWith('-') ? 'buy' : 'sell',
    quantity,
    leverage: Number(reviewed.leverage ?? 1),
    reduce_only: true,
    margin_mode: 'isolated',
    order_type: 'market',
  };
  const payload = JSON.stringify(body);
  const identity = positionIdentity(reviewed);
  const refresh = async () => {
    await Promise.all(
      [
        'pro-account',
        'pro-orders',
        'pro-ledger',
        'pro-deployments',
        'managed-portfolios',
        'contributions',
        'portfolio-analytics',
        'forward-performance',
        'pro-ops',
      ].map((key) => qc.invalidateQueries({ queryKey: [key] })),
    );
  };
  const preview = useMutation({
    mutationFn: async () => ({ result: await proApi.previewOrder(body), payload, identity }),
  });
  const matches =
    preview.data?.payload === payload && preview.data.identity === identity && !changed;
  const prepare = useMutation({
    mutationFn: async () => {
      const [active, working] = await Promise.all([
        proApi.deployments(source),
        proApi.orders(source),
      ]);
      for (const controller of active.items.filter(
        (item) => item.inst_id === symbol && item.status === 'running',
      ))
        await proApi.stop(controller.id);
      for (const entry of working.items.filter(
        (item) => item.inst_id === symbol && item.status === 'pending' && item.reduce_only !== true,
      ))
        await proApi.cancelOrder(String(entry.id));
    },
    onSuccess: async () => {
      preview.reset();
      await refresh();
    },
    onError: refresh,
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (!matches || !stopped) throw new Error(t('Refresh the position and preview again.'));
      const [latestAccount, active, working] = await Promise.all([
        proApi.account(source),
        proApi.deployments(source),
        proApi.orders(source),
      ]);
      if (
        positionIdentity(latestAccount.positions?.find((item) => item.inst_id === symbol)) !==
        identity
      )
        throw new Error(t('Inventory changed. Refresh the position and preview again.'));
      if (
        active.items.some((item) => item.inst_id === symbol && item.status === 'running') ||
        working.items.some(
          (item) =>
            item.inst_id === symbol && item.status === 'pending' && item.reduce_only !== true,
        )
      )
        throw new Error(
          t('Prepare protection again; a controller or entry order is still active.'),
        );
      if (command.current?.payload !== payload)
        command.current = { payload, key: crypto.randomUUID() };
      return proApi.order(body, command.current.key);
    },
    onSuccess: refresh,
    onError: refresh,
  });
  const busy = prepare.isPending || submit.isPending;
  useDialogFocus(true, '.protection-dialog', () => {
    if (!busy) onClose();
  });
  const owners: RecordData[] | undefined = contributions.data
    ? contributions.data.owners.flatMap((owner) =>
        owner.markets
          .filter((market) => market.inst_id === symbol && Number(market.quantity) !== 0)
          .map((market) => ({ ...market, owner: owner.owner })),
      )
    : undefined;
  const updatePosition = async () => {
    const result = await account.refetch();
    const next = result.data?.positions?.find((item) => item.inst_id === symbol);
    if (next) {
      setReviewed(next);
      setQuantity(absolute(next.quantity));
    }
    preview.reset();
    submit.reset();
  };
  return (
    <div
      className="modal-backdrop"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <section
        className="wide-dialog protection-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t('Reduce account position')}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">{t('PROTECTIVE EXECUTION')}</p>
            <h2>{t('Reduce account position')}</h2>
            <p className="protection-market">
              {symbol}{' '}
              <span>
                {t(source === 'example' ? 'Example · synthetic' : 'OKX public')} ·{' '}
                {t('Local paper')}
              </span>
            </p>
          </div>
          <button className="icon-button" aria-label={t('Close')} disabled={busy} onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <p className="quiet-copy">
          {t(
            'This reduces the shared account position. Reductions are allocated across its current inventory owners; they do not close only the selected portfolio.',
          )}
        </p>
        <div className="protection-position">
          <div>
            <span>{t('Current account net quantity')}</span>
            <strong>
              {current
                ? quantityText(current.quantity)
                : account.isPending
                  ? '…'
                  : account.isError
                    ? '—'
                    : '0'}{' '}
              <small>{t(position.inst_type === 'SWAP' ? 'Contracts' : 'Base units')}</small>
            </strong>
          </div>
          <div>
            <span>{t('Exit direction')}</span>
            <strong>
              {t(body.side === 'buy' ? 'Buy to reduce a short' : 'Sell to reduce a long')}
            </strong>
          </div>
        </div>
        {account.isError && <ErrorBox error={account.error} />}
        {deployments.isError && <ErrorBox error={deployments.error} />}
        {orders.isError && <ErrorBox error={orders.error} />}
        <section className="protection-step">
          <div className="section-heading">
            <h3>{t('1 · Prevent automatic re-entry')}</h3>
            <Status type={stopped ? 'neutral' : 'warning'}>
              {t(
                stopped
                  ? 'Controllers stopped · no working entries'
                  : 'Protection preparation required',
              )}
            </Status>
          </div>
          <p className="quiet-copy">
            {t(
              'Stop every controller in this market and cancel its working entry orders before reducing. Stopping a managed leg stops its entire group and retains all other inventory.',
            )}
          </p>
          {controllers.length > 0 && (
            <p>
              {controllers.length} {t('active market controllers')}
            </p>
          )}
          {entries.length > 0 && (
            <p>
              {entries.length} {t('working entry orders')}
            </p>
          )}
          {!stopped && (
            <button
              className="button button-secondary"
              disabled={!allowed || !complete || busy}
              onClick={() => prepare.mutate()}
            >
              {prepare.isPending ? (
                <Loader2 size={14} className="spin" />
              ) : (
                <ShieldMinus size={14} />
              )}
              {t('Stop controllers and cancel entries')}
            </button>
          )}
          {prepare.isError && <ErrorBox error={prepare.error} />}
        </section>
        {current && (
          <section className="protection-step">
            <h3>{t('2 · Preview a reduce-only exit')}</h3>
            {changed && (
              <p className="inline-warning">
                {t('Inventory changed. Refresh the position and preview again.')}
              </p>
            )}
            <div className="protection-quantity">
              <Field
                label="Exit quantity"
                hint={position.inst_type === 'SWAP' ? t('Contracts') : t('Base units')}
              >
                <input
                  type="text"
                  inputMode="decimal"
                  value={quantity}
                  disabled={busy || !!submit.data}
                  onChange={(event) => {
                    setQuantity(event.target.value);
                    submit.reset();
                  }}
                />
              </Field>
              <button className="text-button" disabled={busy} onClick={() => void updatePosition()}>
                <RefreshCw size={13} />
                {t('Refresh to full current quantity')}
              </button>
            </div>
            <p className="quiet-copy">
              {t(
                'Market exit · reduce-only locked. The transaction rejects an oversize exit or a changed direction; it cannot open or reverse a position.',
              )}
            </p>
            <button
              className="button button-secondary"
              disabled={
                !allowed ||
                !stopped ||
                changed ||
                busy ||
                preview.isPending ||
                !quantity ||
                !!submit.data
              }
              onClick={() => preview.mutate()}
            >
              {preview.isPending ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
              {t('Preview protective exit')}
            </button>
            {preview.isError && <ErrorBox error={preview.error} />}
            {matches && (
              <div className="protection-preview">
                <RecordGrid
                  value={{
                    [t('Estimated exit price')]: price(preview.data!.result.estimated_price),
                    [t('Exit notional (USDT)')]: number(preview.data!.result.notional),
                    [t('Estimated fee (USDT)')]: number(preview.data!.result.fee),
                    [t('Quote observed at')]: date(Number(preview.data!.result.market_snapshot.ts)),
                  }}
                />
                {preview.data!.result.warnings.map((warning, index) => (
                  <p className="inline-warning" key={index}>
                    {t(warning)}
                  </p>
                ))}
                {!submit.data && (
                  <button
                    className="button button-dark"
                    disabled={!allowed || !stopped || busy || changed}
                    onClick={() => submit.mutate()}
                  >
                    {submit.isPending ? (
                      <Loader2 size={14} className="spin" />
                    ) : (
                      <ShieldMinus size={14} />
                    )}
                    {t('Submit reduce-only exit')}
                  </button>
                )}
              </div>
            )}
            {submit.isError && <ErrorBox error={submit.error} />}
          </section>
        )}
        {submit.data && (
          <div className="protection-receipt" role="status">
            <Check size={16} />
            <div>
              <strong>{t('Protective exit filled')}</strong>
              <p>
                {String(submit.data.id)} · {quantityText(submit.data.quantity)}{' '}
                {t(position.inst_type === 'SWAP' ? 'Contracts' : 'Base units')}
              </p>
              <p>
                {t(
                  'Controllers remain stopped. Review remaining positions and working protective orders before starting a new release.',
                )}
              </p>
            </div>
          </div>
        )}
        {!current && account.isSuccess && (
          <p className="protection-receipt">
            {t('No position remains in this market. No exit order will be submitted.')}
          </p>
        )}
        <details className="protection-owners">
          <summary>{t('Affected inventory owners')}</summary>
          {contributions.isPending ? (
            <Loading />
          ) : contributions.isError ? (
            <ErrorBox error={contributions.error} />
          ) : owners ? (
            <DataTable
              rows={owners}
              columns={[
                { key: 'owner', label: 'Inventory owner' },
                { key: 'quantity', label: 'Quantity', render: (row) => quantityText(row.quantity) },
              ]}
              empty="No current inventory"
            />
          ) : (
            <p>
              {t(
                'Ownership is not verified. Protective account reductions remain available; attribution requires separate recovery.',
              )}
            </p>
          )}
        </details>
      </section>
    </div>
  );
}
