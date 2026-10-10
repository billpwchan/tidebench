import { CircleAlert, Clock3, Check } from 'lucide-react';
import type { OrderRequest, RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, price } from '../lib/format';
import { ErrorBox, Loading, Status } from './workspace';
import { JsonDetails, valueText } from './ProWorkspace';
import './OrderReceipt.css';

export type OrderSubmissionReceipt = {
  request?: OrderRequest;
  response?: RecordData;
  phase: 'submitting' | 'received' | 'rejected' | 'unconfirmed';
  intent?: 'submit' | 'cancel';
  executionMode?: string;
  targetId?: string;
  targetSource?: string;
  targetMarket?: string;
  instrument?: RecordData;
  message?: string;
};

const record = (value: unknown): RecordData =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};

export default function OrderReceipt({
  receipt,
  onInspect,
  inspection = false,
  events,
  eventsPending = false,
  eventsError,
  journal,
  journalPending = false,
  journalError,
}: {
  receipt: OrderSubmissionReceipt;
  onInspect?: (id: string) => void;
  inspection?: boolean;
  events?: RecordData[];
  eventsPending?: boolean;
  eventsError?: unknown;
  journal?: RecordData[];
  journalPending?: boolean;
  journalError?: unknown;
}) {
  const { language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const order = receipt.response ?? {};
  const request = receipt.request;
  const instrument = record(order.instrument ?? receipt.instrument);
  const status = typeof order.status === 'string' ? order.status : undefined;
  const id = typeof order.id === 'string' ? order.id : receipt.targetId;
  const market = valueText(order.inst_id ?? request?.inst_id ?? receipt.targetMarket);
  const nativeUnit =
    instrument.quantity_unit === 'contracts' || instrument.inst_type === 'SWAP'
      ? text('contracts', '张')
      : instrument.base
        ? valueText(instrument.base)
        : instrument.quantity_unit === 'base' || instrument.inst_type === 'SPOT'
          ? text('base units', '基础币单位')
          : text('unit not reported', '单位未返回');
  const requested = order.requested_quantity ?? order.quantity ?? request?.quantity;
  // Legacy filled records use quantity as the executed native quantity. Pending
  // records also carry estimated price/fee, so those are never presented as fills.
  const filled = order.filled_quantity ?? (status === 'filled' ? order.quantity : undefined);
  const canceled = order.canceled_quantity;
  const outcome =
    status === 'filled'
      ? text('Filled', '已成交')
      : status === 'pending'
        ? text('Pending · working order', '挂单中 · 等待执行')
        : status === 'rejected' || receipt.phase === 'rejected'
          ? text('Rejected', '已拒绝')
          : status === 'canceled'
            ? text('Canceled', '已取消')
            : receipt.phase === 'submitting'
              ? text('Submitting · awaiting server', '提交中 · 等待服务器')
              : receipt.phase === 'unconfirmed'
                ? receipt.intent === 'cancel'
                  ? text('Cancellation unconfirmed', '撤单结果未确认')
                  : text('Submission unconfirmed', '提交结果未确认')
                : text('Server response received', '已收到服务器回执');
  const notReported = text('Not reported', '未返回');
  const amount = (value: unknown) =>
    value == null || value === '' ? notReported : `${valueText(value)} ${nativeUnit}`;
  const mode = valueText(order.execution_mode ?? receipt.executionMode);
  const reason = order.rejection_reason ?? order.cancellation_reason ?? order.error;
  return (
    <div className={`order-receipt${inspection ? ' is-inspection' : ''}`}>
      <div className="order-receipt-heading">
        <h3 tabIndex={-1}>
          {inspection
            ? text('Inspected order', '正在查看的订单')
            : receipt.intent === 'cancel'
              ? text('Cancellation receipt', '撤单回执')
              : text('Order receipt', '订单回执')}
        </h3>
        <Status
          type={
            status === 'filled'
              ? 'good'
              : status === 'rejected' || receipt.phase === 'rejected'
                ? 'bad'
                : 'neutral'
          }
        >
          {status ?? text('No server order status', '服务器未返回订单状态')}
        </Status>
      </div>
      <p className="order-receipt-outcome" role="status" aria-live="polite">
        {status === 'filled' ? (
          <Check size={15} />
        ) : status === 'rejected' ||
          receipt.phase === 'rejected' ||
          receipt.phase === 'unconfirmed' ? (
          <CircleAlert size={15} />
        ) : (
          <Clock3 size={15} />
        )}
        <strong>{outcome}</strong>
      </p>
      <p className="order-receipt-context">
        {market} · {valueText(order.side ?? request?.side)} ·{' '}
        {valueText(order.order_type ?? request?.order_type)}
        <br />
        {valueText(order.source ?? request?.source ?? receipt.targetSource).toUpperCase()} · {mode}
      </p>
      <dl className="order-receipt-values">
        <div>
          <dt>{text('Requested', '请求数量')}</dt>
          <dd>{amount(requested)}</dd>
        </div>
        <div>
          <dt>{text('Filled quantity', '成交数量')}</dt>
          <dd>{amount(filled)}</dd>
        </div>
        <div>
          <dt>{text('Canceled remainder', '取消余量')}</dt>
          <dd>{amount(canceled)}</dd>
        </div>
        <div>
          <dt>{text('Native unit', '原生单位')}</dt>
          <dd>{nativeUnit}</dd>
        </div>
        <div>
          <dt>{text('Fill price', '成交价')}</dt>
          <dd>
            {status === 'filled' && order.price != null
              ? `${price(order.price, instrument.tick_size)}${instrument.quote ? ' ' + valueText(instrument.quote) : ''}`
              : notReported}
          </dd>
        </div>
        <div>
          <dt>{text('Fees', '费用')}</dt>
          <dd>
            {status === 'filled' && order.fee != null
              ? `${valueText(order.fee)}${instrument.settle_ccy || instrument.quote ? ' ' + valueText(instrument.settle_ccy ?? instrument.quote) : ''}`
              : notReported}
          </dd>
        </div>
        <div className="order-receipt-id">
          <dt>{text('Order ID', '订单 ID')}</dt>
          <dd>{id ?? text('No order ID returned', '未返回订单 ID')}</dd>
        </div>
      </dl>
      {reason != null && <p className="order-receipt-note">{valueText(reason)}</p>}
      {receipt.message && <p className="order-receipt-note">{receipt.message}</p>}
      {!inspection && (
        <p className="order-receipt-note">
          {receipt.intent === 'cancel'
            ? text(
                'Cancellation result for this exact order. The server status above is authoritative.',
                '这是此订单的撤单结果，以上服务器状态为准。',
              )
            : text(
                'Receipt for the submitted request. You can prepare your next order below.',
                '此回执对应已提交的请求。可在下方准备下一笔订单。',
              )}
        </p>
      )}
      {id && onInspect && (
        <button
          type="button"
          className="button button-secondary full-width"
          onClick={() => onInspect(id)}
        >
          {text('Inspect this order', '查看此订单')}
        </button>
      )}
      {inspection && (
        <div className="order-receipt-evidence">
          <p className="order-receipt-note">
            {text(
              'Loaded evidence windows: latest 100 audit events and 500 journal entries. Older matching records may exist.',
              '当前证据窗口：最近 100 条审计事件与 500 条账本记录；更早的匹配记录可能仍存在。',
            )}
          </p>
          <h4>{text('Order events', '订单事件')}</h4>
          {eventsPending ? (
            <Loading />
          ) : eventsError ? (
            <ErrorBox error={eventsError} />
          ) : events?.length ? (
            <ol className="order-receipt-events">
              {events.map((event) => (
                <li key={String(event.id)}>
                  <strong>{valueText(event.kind)}</strong>
                  <time>{date(Number(event.ts ?? event.created_at), true)}</time>
                  <span>{valueText(event.summary)}</span>
                  <JsonDetails value={event.details} label="Event details" />
                </li>
              ))}
            </ol>
          ) : (
            <p className="order-receipt-note">
              {text(
                'No matching events in the available audit history.',
                '当前审计历史中没有此订单的事件。',
              )}
            </p>
          )}
          <details className="order-receipt-journal">
            <summary>
              {text('Ledger entries', '账本记录')}
              {journal ? ` (${journal.length})` : ''}
            </summary>
            {journalPending ? (
              <Loading />
            ) : journalError ? (
              <ErrorBox error={journalError} />
            ) : journal?.length ? (
              <ul>
                {journal.map((entry) => (
                  <li key={String(entry.id)}>
                    {date(Number(entry.ts))} · {valueText(entry.asset)} · {valueText(entry.account)}{' '}
                    · {text('Debit', '借记')} {valueText(entry.debit)} / {text('Credit', '贷记')}{' '}
                    {valueText(entry.credit)}
                    <small>
                      {valueText(entry.memo)} · {valueText(entry.reference)}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="order-receipt-note">
                {text(
                  'No matching entries in the available ledger history.',
                  '当前账本历史中没有此订单的记录。',
                )}
              </p>
            )}
          </details>
          <JsonDetails value={order} label="Server order record" />
        </div>
      )}
    </div>
  );
}
