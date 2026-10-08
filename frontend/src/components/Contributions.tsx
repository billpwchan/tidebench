import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Download } from 'lucide-react';
import type { Source } from '../api';
import { downloadCsv } from '../api';
import { proApi } from '../proApi';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { Empty, ErrorBox, Loading, Status } from './workspace';
import { useI18n } from '../lib/i18n';
import { date, number, quantityText } from '../lib/format';

export default function Contributions({ source }: { source: Source }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState('');
  const report = useQuery({
    queryKey: ['contributions', source],
    queryFn: () => proApi.contributions(source),
    refetchInterval: 5000,
  });
  const groups = useQuery({
    queryKey: ['managed-portfolios', source],
    queryFn: () => proApi.managedPortfolios(source),
    refetchInterval: 10000,
  });
  const name = (owner: string) =>
    owner === 'legacy'
      ? t('Unattributed legacy')
      : owner.startsWith('portfolio:')
        ? (groups.data?.items.find((g) => g.id === owner.slice(10))?.manifest?.name ??
          `${t('Portfolio')} ${owner.slice(10, 18)}`)
        : owner.startsWith('manual:')
          ? `${t('Manual')} · ${owner.slice(7)}`
          : `${t('Strategy')} ${owner.split(':')[1]?.slice(0, 8)}`;
  if (report.isPending) return <Loading />;
  if (report.isError) return <ErrorBox error={report.error} />;
  const r = report.data;
  const owner = r.owners.find((o) => o.owner === selected);
  return (
    <section className="contribution-workspace">
      <div className="section-heading">
        <div>
          <h2>{t('Economic contribution')}</h2>
          <p className="quiet-copy">
            {t('Actual monetary P&L by inventory owner, reconciled to the shared account.')}
          </p>
        </div>
        <button
          className="button button-secondary"
          disabled={!r.owners.length}
          onClick={() =>
            downloadCsv(
              r.owners.map((o) => ({
                owner: o.owner,
                name: name(o.owner),
                realized_pnl: o.realized_pnl,
                unrealized_pnl: o.unrealized_pnl,
                fees_paid: o.fees_paid,
                funding_paid: o.funding_paid,
                net_pnl: o.net_pnl,
              })),
              `tidebench-contributions-${source}.csv`,
            )
          }
        >
          <Download size={14} />
          {t('Export CSV')}
        </button>
      </div>
      <div className="portfolio-release-actions">
        <Status type={r.reconciled ? 'good' : 'bad'}>
          {t(r.reconciled ? 'Reconciled with account' : 'Reconciliation requires attention')}
        </Status>
        <span className="quiet-copy">
          {date(r.as_of, true)} · {t(r.valuation_status)}
        </span>
      </div>
      <RecordGrid
        value={{
          net_pnl: r.totals.net_pnl,
          realized_pnl: r.totals.realized_pnl,
          unrealized_pnl: r.totals.unrealized_pnl,
          funding_paid: r.totals.funding_paid,
          fees_paid: r.totals.fees_paid,
          reconciliation_delta: r.reconciliation_delta,
        }}
      />
      <p className="quiet-copy">
        {t(
          'Net P&L includes realized and unrealized contribution, less funding. Fees are already included in P&L; insurance debt is not deducted twice. These are shared-account contributions, not independent strategy returns.',
        )}
      </p>
      {!r.owners.length ? (
        <Empty title="No contribution history yet">
          {t(
            'New fills and funding settlements create transactional owner evidence. Pre-upgrade balances remain explicitly unattributed.',
          )}
        </Empty>
      ) : (
        <DataTable
          rows={r.owners}
          columns={[
            {
              key: 'owner',
              label: 'Inventory owner',
              render: (o) => (
                <button className="text-button" onClick={() => setSelected(o.owner)}>
                  {name(o.owner)}
                </button>
              ),
            },
            { key: 'realized_pnl', label: 'Realized P&L', render: (o) => number(o.realized_pnl) },
            {
              key: 'unrealized_pnl',
              label: 'Unrealized P&L',
              render: (o) => (o.unrealized_pnl === null ? '—' : number(o.unrealized_pnl)),
            },
            { key: 'funding_paid', label: 'Funding paid', render: (o) => number(o.funding_paid) },
            { key: 'fees_paid', label: 'Fees paid', render: (o) => number(o.fees_paid) },
            {
              key: 'net_pnl',
              label: 'Net contribution',
              render: (o) => (
                <strong className={Number(o.net_pnl) >= 0 ? 'positive' : 'negative'}>
                  {o.net_pnl === null ? '—' : number(o.net_pnl)}
                </strong>
              ),
            },
          ]}
        />
      )}
      {owner && (
        <section className="portfolio-release-review">
          <div className="section-heading">
            <h3>{name(owner.owner)}</h3>
            <button className="text-button" onClick={() => setSelected('')}>
              {t('Close')}
            </button>
          </div>
          <DataTable
            rows={owner.markets}
            columns={[
              { key: 'inst_id', label: 'Market' },
              {
                key: 'quantity',
                label: 'Virtual quantity',
                render: (o) => quantityText(o.quantity),
              },
              {
                key: 'entry_notional',
                label: 'Remaining entry costs',
                render: (o) => number(o.entry_notional),
              },
              { key: 'realized', label: 'Realized P&L', render: (o) => number(o.realized) },
              {
                key: 'unrealized_pnl',
                label: 'Unrealized P&L',
                render: (o) => (o.unrealized_pnl === null ? '—' : number(o.unrealized_pnl)),
              },
            ]}
          />
          <p className="quiet-copy">
            {t(
              'Reductions allocate existing quantity proportionally across owners. Virtual sleeve quantities describe attribution and are not separate executable positions.',
            )}
          </p>
          <OwnerEvents key={owner.owner} source={source} owner={owner.owner} />
        </section>
      )}
      <JsonDetails value={r} label="Contribution reconciliation evidence" />
    </section>
  );
}

function OwnerEvents({ source, owner }: { source: Source; owner: string }) {
  const { t } = useI18n();
  const [before, setBefore] = useState<number>();
  const [selected, setSelected] = useState<number>();
  const events = useQuery({
    queryKey: ['contribution-events', source, owner, before],
    queryFn: () => proApi.contributionEvents(source, owner, before),
    refetchInterval: 5000,
  });
  const rows = events.data?.items ?? [];
  const current = rows.find((e) => e.id === selected) ?? rows[0];
  return (
    <section className="contribution-events">
      <div className="section-heading">
        <h3>{t('Contribution event history')}</h3>
        <div className="portfolio-release-actions">
          {before !== undefined && (
            <button className="text-button" onClick={() => setBefore(undefined)}>
              {t('Latest')}
            </button>
          )}
          {rows.length >= 100 && (
            <button className="text-button" onClick={() => setBefore(rows.at(-1)!.id)}>
              {t('Older events')}
            </button>
          )}
        </div>
      </div>
      {events.isError ? (
        <ErrorBox error={events.error} />
      ) : events.isPending ? (
        <Loading />
      ) : (
        <DataTable
          rows={rows}
          columns={[
            {
              key: 'reference',
              label: 'Event reference',
              render: (r) => (
                <button
                  className="text-button"
                  title={String(r.reference)}
                  onClick={() => setSelected(r.id)}
                >
                  {String(r.reference).slice(0, 12)}…
                </button>
              ),
            },
            { key: 'kind', label: 'Kind', render: (r) => t(String(r.kind)) },
            { key: 'inst_id', label: 'Market' },
            {
              key: 'created_at',
              label: 'Recorded',
              render: (r) => date(Number(r.created_at), true),
            },
            {
              key: 'price',
              label: 'Actual fill',
              render: (r) => (r.order ? number(r.order.price, 8) : '—'),
            },
          ]}
        />
      )}
      {current && (
        <JsonDetails key={current.id} value={current} label="Contribution event and actual fill" />
      )}
    </section>
  );
}
