import { ArrowRight, CircleAlert, Clock3 } from 'lucide-react';
import type { Source } from '../api';
import type { Page } from '../lib/config';
import type { DeskIssue } from '../lib/deskAttention';
import { date, number, quantityText } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { Status } from './workspace';

function elapsed(since: number | undefined, now: number, t: (value: string) => string) {
  if (!since || !Number.isFinite(since)) return t('Duration unavailable');
  const minutes = Math.max(0, Math.floor((now - since) / 60000));
  if (minutes < 1) return t('Under one minute');
  if (minutes < 60) return `${minutes} ${t(minutes === 1 ? 'minute' : 'minutes')}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24)
    return `${hours} ${t(hours === 1 ? 'hour' : 'hours')} ${minutes % 60} ${t(minutes % 60 === 1 ? 'minute' : 'minutes')}`;
  return `${Math.floor(hours / 24)} ${t(Math.floor(hours / 24) === 1 ? 'day' : 'days')} ${hours % 24} ${t(hours % 24 === 1 ? 'hour' : 'hours')}`;
}

export default function DeskAttention({
  issues,
  source,
  now,
  navigate,
}: {
  issues: DeskIssue[];
  source: Source;
  now: number;
  navigate: (page: Page, view?: string) => void;
}) {
  const { t } = useI18n();
  if (!issues.length) return null;
  const critical = issues.some((issue) => issue.severity === 'bad');
  return (
    <section
      className={`desk-attention${critical ? '' : ' desk-attention-warning'}`}
      aria-label={t('Unresolved conditions')}
    >
      <div className="desk-attention-heading">
        <CircleAlert size={17} />
        <div>
          <h2>{t(critical ? 'Resolve before increasing risk' : 'Review unresolved conditions')}</h2>
          <p>{t('Acknowledged conditions remain here until recovery is observed.')}</p>
        </div>
        <span className="subtle-tag">
          {issues.length} {t('unresolved')}
        </span>
      </div>
      <div className="desk-issue-list">
        {issues.map((issue) => (
          <article
            className={`desk-issue desk-issue-${issue.severity}`}
            key={issue.id}
            data-issue-id={issue.id}
          >
            <div className="desk-issue-context">
              <span className="desk-issue-scope">
                {t(
                  issue.scope === 'workspace'
                    ? 'Shared workspace'
                    : source === 'okx'
                      ? 'OKX public'
                      : 'Example · synthetic',
                )}
                {' · '}
                {t(issue.group ? 'Portfolio group' : 'Durable incident')}
              </span>
              <h3>{issue.name}</h3>
              <div className="desk-issue-state">
                <Status type={issue.severity}>{t(issue.phase)}</Status>
                {issue.incident?.status === 'acknowledged' && (
                  <Status type="warning">{t('Acknowledged · unresolved')}</Status>
                )}
                <span>
                  <Clock3 size={11} />
                  {elapsed(issue.since, now, t)}
                </span>
              </div>
              {!!issue.error && <p className="desk-issue-error">{issue.error}</p>}
              {issue.incident?.ack_actor && (
                <p className="snapshot-footnote">
                  {t('Response owner')}: {issue.incident.ack_actor}
                  {' · '}
                  {date(issue.incident.ack_at)}
                </p>
              )}
            </div>
            <div className="desk-issue-inventory">
              {issue.group ? (
                <>
                  <span className="desk-issue-label">{t('Current group inventory')}</span>
                  <strong className="desk-issue-amount">
                    {number(issue.inventoryNotional)} <small>USDT</small>
                  </strong>
                  {issue.inventoryNotional == null && (
                    <p>{t('Unvalued inventory is not zero exposure.')}</p>
                  )}
                  {issue.inventory
                    ?.filter((position) => Number(position.quantity) !== 0)
                    .map((position) => (
                      <p key={position.inst_id} className="desk-inventory-position">
                        <span>{position.inst_id}</span>
                        <span>
                          {quantityText(position.quantity)}{' '}
                          {t(position.inst_id.endsWith('-SWAP') ? 'Contracts' : 'Base units')}
                        </span>
                      </p>
                    ))}
                  {issue.residualNotional != null && (
                    <p>
                      {t('Target deviation')}: {number(issue.residualNotional)} USDT
                    </p>
                  )}
                  {issue.valuationStatus && (
                    <p>
                      {t('Inventory valuation')}: {t(issue.valuationStatus)}
                    </p>
                  )}
                  <p>
                    {t('Observed')}: {date(issue.asOf)}
                  </p>
                </>
              ) : (
                <p>
                  {t('Last observed')}: {date(issue.asOf)}
                </p>
              )}
            </div>
            <div className="desk-issue-actions">
              {issue.group && (
                <button
                  className="text-button"
                  onClick={() => navigate('execution', `managed:${issue.group!.id}`)}
                >
                  {t('Inspect group & recovery')}
                  <ArrowRight size={13} />
                </button>
              )}
              {issue.incident && (
                <button
                  className="text-button"
                  onClick={() => navigate('operations', `incidents:${issue.incident!.id}`)}
                >
                  {t('Inspect incident & response')}
                  <ArrowRight size={13} />
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
