import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowDownToLine, FlaskConical, Loader2, RefreshCw } from 'lucide-react';
import type { Source } from '../api';
import { downloadBlob } from '../api';
import { proApi } from '../proApi';
import type { RecordData } from '../proApi';
import { date, number, price, quantityText, tone } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { ErrorBox, Field, Loading, Metric, Status } from './workspace';
import { DataTable, JsonDetails, valueText, WorkspaceTabs } from './ProWorkspace';
const pct = (v: unknown) => (v == null ? '—' : `${number(v, 2)}%`);
const ratio = (v: unknown) => (v == null ? '—' : `${number(v, 2)}×`);
const records = (v: unknown): RecordData[] => (Array.isArray(v) ? (v as RecordData[]) : []);
export default function PortfolioAnalytics({ source }: { source: Source }) {
  const { t } = useI18n();
  const [tab, setTab] = useState('assets');
  const [scenarioName, setScenarioName] = useState('');
  const [name, setName] = useState('Custom shock');
  const [parallel, setParallel] = useState('-10');
  const [overrideType, setOverrideType] = useState('none');
  const [overrideKey, setOverrideKey] = useState('');
  const [overridePct, setOverridePct] = useState('-20');
  const query = useQuery({
    queryKey: ['portfolio-analytics', source],
    queryFn: () => proApi.analytics(source),
    refetchInterval: 10000,
  });
  const custom = useMutation({
    mutationFn: proApi.analyzeScenarios,
    onSuccess: () => setScenarioName(''),
  });
  const data = custom.data ?? query.data;
  const s = data?.summary;
  const scenario = data?.scenarios.find((r) => r.name === scenarioName) ?? data?.scenarios[0];
  const scenarioRows = records(scenario?.positions);
  const overrides =
    overrideType === 'asset'
      ? (query.data?.assets ?? []).map((r) => String(r.asset))
      : (query.data?.markets ?? []).map((r) => String(r.inst_id));
  const refresh = () => {
    custom.reset();
    setScenarioName('');
    void query.refetch();
  };
  return (
    <section className="pro-panel portfolio-analytics">
      <div className="section-heading">
        <div>
          <h2>{t('Portfolio exposure & scenarios')}</h2>
          <p className="section-description">
            {t(
              'Combined spot and perpetual exposure, valued from a captured account and market snapshot.',
            )}
          </p>
        </div>
        <button type="button" className="text-button" onClick={refresh}>
          <RefreshCw size={13} />
          {t('Refresh snapshot')}
        </button>
      </div>
      {query.isPending && !data ? (
        <Loading />
      ) : query.isError && !data ? (
        <ErrorBox error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        data && (
          <>
            <div className="analytics-capture-line">
              <Status
                type={
                  query.isError && !custom.data
                    ? 'warning'
                    : data.status === 'available'
                      ? 'neutral'
                      : 'warning'
                }
              >
                {t(data.status)}
              </Status>
              <span>
                {t(
                  custom.data
                    ? 'Captured custom scenario'
                    : query.isError
                      ? 'Last captured snapshot · refresh unavailable'
                      : 'Current risk snapshot',
                )}{' '}
                · {date(data.as_of ?? data.as_of_ms, true)}
              </span>
              <code>{valueText((data.captured_scenario as RecordData | undefined)?.id)}</code>
            </div>
            {query.isError && <ErrorBox error={query.error} onRetry={() => void query.refetch()} />}
            {data.status !== 'available' && (
              <p className="inline-warning">
                {t(
                  'Prices, rules or funding reconciliation are incomplete. Unknown values remain unavailable; they are not zero risk.',
                )}
              </p>
            )}
            {!!data.issues?.length && (
              <ul className="analytics-issues">
                {data.issues.map((issue, i) => (
                  <li key={i}>
                    {typeof issue === 'string' ? (
                      issue
                    ) : (
                      <>
                        <code>{valueText(issue.code)}</code> {valueText(issue.inst_id)} ·{' '}
                        {valueText(issue.message)}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="analytics-metrics">
              <Metric label="Gross exposure" value={number(s?.gross_notional)} unit="USDT" />
              <Metric label="Net exposure" value={number(s?.net_notional)} unit="USDT" />
              <Metric label="Gross leverage" value={ratio(s?.gross_leverage)} />
              <Metric label="Largest asset share" value={pct(s?.largest_asset_share_pct)} />
              <Metric
                label="Concentration HHI"
                value={number(s?.concentration_hhi, 4)}
                note="0–1 · gross notional shares"
              />
            </div>
            <WorkspaceTabs
              value={tab}
              onChange={setTab}
              items={[
                { key: 'assets', label: 'Asset exposure' },
                { key: 'markets', label: 'Market exposure' },
                { key: 'positions', label: 'Margin buffers' },
                { key: 'scenarios', label: 'Stress scenarios' },
              ]}
            />
            {(tab === 'assets' || tab === 'markets') && (
              <DataTable
                rows={tab === 'assets' ? data.assets : data.markets}
                empty="No asset exposure"
                columns={[
                  {
                    key: 'market',
                    label: tab === 'assets' ? 'Asset' : 'Market',
                    render: (r) => (
                      <div className="table-stacked">
                        <strong>{valueText(r.asset ?? r.inst_id)}</strong>
                        <small>{t(valueText(r.status))}</small>
                      </div>
                    ),
                  },
                  {
                    key: 'long_notional',
                    label: 'Long exposure',
                    render: (r) => number(r.long_notional),
                  },
                  {
                    key: 'short_notional',
                    label: 'Short exposure',
                    render: (r) => number(r.short_notional),
                  },
                  {
                    key: 'gross_notional',
                    label: 'Gross exposure',
                    render: (r) => number(r.gross_notional),
                  },
                  {
                    key: 'net_notional',
                    label: 'Net exposure',
                    render: (r) => number(r.net_notional),
                  },
                  {
                    key: 'gross_share_pct',
                    label: 'Gross share',
                    render: (r) => pct(r.gross_share_pct),
                  },
                ]}
              />
            )}
            {tab === 'positions' && (
              <>
                <DataTable
                  rows={data.positions}
                  empty="No positions"
                  columns={[
                    {
                      key: 'inst_id',
                      label: 'Market',
                      render: (r) => (
                        <div className="table-stacked">
                          <strong>{valueText(r.inst_id)}</strong>
                          <small>
                            {valueText(r.inst_type)} · {t(valueText(r.risk_status))}
                          </small>
                        </div>
                      ),
                    },
                    { key: 'quantity', label: 'Quantity', render: (r) => quantityText(r.quantity) },
                    { key: 'mark', label: 'Mark', render: (r) => price(r.mark) },
                    {
                      key: 'equity_component',
                      label: 'Position equity',
                      render: (r) => number(r.equity_component),
                    },
                    {
                      key: 'maintenance_required',
                      label: 'Maintenance + close fees',
                      render: (r) => number(r.maintenance_required),
                    },
                    {
                      key: 'maintenance_buffer',
                      label: 'Maintenance buffer',
                      render: (r) => (
                        <span className={tone(r.maintenance_buffer)}>
                          {number(r.maintenance_buffer)}
                        </span>
                      ),
                    },
                    {
                      key: 'maintenance_coverage',
                      label: 'Maintenance coverage',
                      render: (r) => ratio(r.maintenance_coverage),
                    },
                    {
                      key: 'liquidation_distance_pct',
                      label: 'Estimated liquidation distance',
                      render: (r) => pct(r.liquidation_distance_pct),
                    },
                    {
                      key: 'maintenance_breach',
                      label: 'Maintenance breach',
                      render: (r) =>
                        r.maintenance_breach == null ? '—' : t(r.maintenance_breach ? 'Yes' : 'No'),
                    },
                  ]}
                />
                <p className="snapshot-footnote">
                  {t(
                    'Liquidation distances use captured tiers and fee reserves. They are model estimates, not exchange liquidation guarantees.',
                  )}
                </p>
              </>
            )}
            {tab === 'scenarios' && (
              <>
                <div className="scenario-method-note">
                  <FlaskConical size={15} />
                  <p>
                    {t(
                      'Deterministic price shocks, not VaR or a probability forecast. Positions and tiers are frozen. Breached isolated perpetual positions are assumed to close fully; this does not predict exchange liquidation execution.',
                    )}
                  </p>
                </div>
                <DataTable
                  rows={data.scenarios}
                  columns={[
                    {
                      key: 'name',
                      label: 'Scenario',
                      render: (r) => (
                        <button
                          className="text-button"
                          onClick={() => setScenarioName(String(r.name))}
                        >
                          {valueText(r.name)}
                        </button>
                      ),
                    },
                    {
                      key: 'parallel_pct',
                      label: 'Parallel shock',
                      render: (r) => pct(r.parallel_pct),
                    },
                    {
                      key: 'pre_liquidation_equity',
                      label: 'Shocked equity',
                      render: (r) => number(r.pre_liquidation_equity),
                    },
                    {
                      key: 'equity_change',
                      label: 'Equity change',
                      render: (r) => (
                        <div className="table-stacked">
                          <span className={tone(r.equity_change)}>{number(r.equity_change)}</span>
                          <small>{pct(r.equity_change_pct)}</small>
                        </div>
                      ),
                    },
                    {
                      key: 'post_full_liquidation_equity',
                      label: 'After modeled liquidations',
                      render: (r) => number(r.post_full_liquidation_equity),
                    },
                    {
                      key: 'incremental_liability',
                      label: 'Additional liability',
                      render: (r) => number(r.incremental_liability),
                    },
                    {
                      key: 'liquidations',
                      label: 'Liquidation triggers',
                      render: (r) => number(r.liquidations, 0),
                    },
                    {
                      key: 'status',
                      label: 'Status',
                      render: (r) => (
                        <Status type={r.status === 'available' ? 'neutral' : 'warning'}>
                          {t(valueText(r.status))}
                        </Status>
                      ),
                    },
                  ]}
                />
                <form
                  className="custom-scenario-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    custom.mutate({
                      source,
                      scenarios: [
                        {
                          name,
                          parallel_pct: parallel,
                          ...(overrideType === 'asset' && overrideKey
                            ? { asset_pct: { [overrideKey]: overridePct } }
                            : {}),
                          ...(overrideType === 'market' && overrideKey
                            ? { market_pct: { [overrideKey]: overridePct } }
                            : {}),
                        },
                      ],
                    });
                  }}
                >
                  <div>
                    <h3>{t('Custom price shock')}</h3>
                    <p className="range-hint">
                      {t(
                        'Market overrides replace asset overrides, which replace the parallel shock. Percentage changes are not added.',
                      )}
                    </p>
                  </div>
                  <div className="custom-scenario-fields">
                    <Field label="Scenario name">
                      <input
                        required
                        maxLength={80}
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                      />
                    </Field>
                    <Field label="Parallel price change (%)">
                      <input
                        required
                        type="number"
                        min="-99.9999"
                        max="1000"
                        step="any"
                        value={parallel}
                        onChange={(e) => setParallel(e.target.value)}
                      />
                    </Field>
                    <Field label="Override">
                      <select
                        value={overrideType}
                        onChange={(e) => {
                          setOverrideType(e.target.value);
                          setOverrideKey('');
                        }}
                      >
                        <option value="none">{t('None')}</option>
                        <option value="asset">{t('Asset')}</option>
                        <option value="market">{t('Market')}</option>
                      </select>
                    </Field>
                    {overrideType !== 'none' && (
                      <>
                        <Field label={overrideType === 'asset' ? 'Asset' : 'Market'}>
                          <select
                            required
                            value={overrideKey}
                            onChange={(e) => setOverrideKey(e.target.value)}
                          >
                            <option value="">{t('Select exposure')}</option>
                            {overrides.map((k) => (
                              <option key={k}>{k}</option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Override price change (%)">
                          <input
                            required
                            type="number"
                            min="-99.9999"
                            max="1000"
                            step="any"
                            value={overridePct}
                            onChange={(e) => setOverridePct(e.target.value)}
                          />
                        </Field>
                      </>
                    )}
                  </div>
                  <div className="scenario-form-actions">
                    <button className="button button-dark" disabled={custom.isPending}>
                      {custom.isPending ? (
                        <Loader2 size={13} className="spin" />
                      ) : (
                        <FlaskConical size={13} />
                      )}{' '}
                      {t('Calculate scenario')}
                    </button>
                    {custom.data && (
                      <button className="text-button" type="button" onClick={refresh}>
                        {t('Return to default scenarios')}
                      </button>
                    )}
                    <span>{t('Read-only calculation. No orders are submitted.')}</span>
                  </div>
                  {custom.isError && <ErrorBox error={custom.error} />}
                </form>
                {scenario && (
                  <div className="scenario-position-detail">
                    <div className="section-heading">
                      <h3>
                        {t('Scenario position attribution')} · {valueText(scenario.name)}
                      </h3>
                      <Status type={scenario.status === 'available' ? 'neutral' : 'warning'}>
                        {t(valueText(scenario.status))}
                      </Status>
                    </div>
                    <DataTable
                      rows={scenarioRows}
                      empty="No positions"
                      columns={[
                        { key: 'inst_id', label: 'Market' },
                        {
                          key: 'shock_pct',
                          label: 'Applied shock',
                          render: (r) => (
                            <div className="table-stacked">
                              <span>{pct(r.shock_pct)}</span>
                              <small>{t(valueText(r.shock_origin))}</small>
                            </div>
                          ),
                        },
                        {
                          key: 'shocked_mark',
                          label: 'Shocked mark',
                          render: (r) => price(r.shocked_mark),
                        },
                        {
                          key: 'pnl_change',
                          label: 'P&L change',
                          render: (r) => number(r.pnl_change),
                        },
                        {
                          key: 'maintenance_breach',
                          label: 'Maintenance breach',
                          render: (r) =>
                            r.maintenance_breach == null
                              ? '—'
                              : t(r.maintenance_breach ? 'Yes' : 'No'),
                        },
                        {
                          key: 'liquidation_fee',
                          label: 'Liquidation fee',
                          render: (r) => number(r.liquidation_fee),
                        },
                        {
                          key: 'incremental_liability',
                          label: 'Additional liability',
                          render: (r) => number(r.incremental_liability),
                        },
                      ]}
                    />
                    <JsonDetails value={scenario.issues} label="Scenario limitations" />
                  </div>
                )}
              </>
            )}
            <div className="analytics-footer">
              <JsonDetails value={data.assumptions} label="Model assumptions" />
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  downloadBlob(
                    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
                    `tidebench-risk-snapshot-${data.as_of ?? Date.now()}.json`,
                  )
                }
              >
                <ArrowDownToLine size={12} />
                {t('Export risk snapshot')}
              </button>
            </div>
          </>
        )
      )}
    </section>
  );
}
