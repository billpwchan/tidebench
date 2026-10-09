import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Play, Plus, X } from 'lucide-react';
import { useState } from 'react';
import type { Source } from '../api';
import type { PortfolioHoldout, PortfolioHoldoutPreview, RecordData } from '../proApi';
import { proApi } from '../proApi';
import { useI18n } from '../lib/i18n';
import { date, number } from '../lib/format';
import { canResearch } from '../lib/permissions';
import { useSession } from './AuthGate';
import { DataTable, JsonDetails, RecordGrid } from './ProWorkspace';
import { Empty, ErrorBox, Field, Loading, PageHeading, Status } from './workspace';
import PortfolioExecutionPolicy from './PortfolioExecutionPolicy';
import { CapturedLifecycleSources, LifecycleImport, LifecycleScenario } from './LifecycleEvidence';

const utc = (ts: number) => new Date(ts).toISOString().slice(0, 16);
const intervals: Record<string, number> = {
  '1m': 60000,
  '5m': 300000,
  '15m': 900000,
  '1H': 3600000,
  '4H': 14400000,
  '1Dutc': 86400000,
};
function FrozenPlan({ holdout }: { holdout: PortfolioHoldout }) {
  const { t } = useI18n();
  const p = holdout.plan;
  return (
    <div className="frozen-portfolio-plan">
      <div className="section-heading">
        <div>
          <span className="eyebrow">{t('CAPTURED EVALUATION CONTRACT')}</span>
          <h3>{p.name}</h3>
        </div>
        <Status>{t(holdout.status)}</Status>
      </div>
      <p className="quiet-copy">{p.hypothesis}</p>
      <RecordGrid
        value={{
          'Final window start': date(p.test_start, true),
          'Final window end': date(p.test_end, true),
          warmup_bars: p.warmup_bars,
          'Market history':
            p.test_config.universe_mode === 'historical_lifecycle'
              ? t('Attributed historical lifecycle')
              : t('Static selected markets'),
          'Per-market listing / resume warmup': p.test_config.lifecycle_warmup_bars ?? 2,
          initial_cash: p.test_config.initial_cash,
          fee_bps: p.test_config.fee_bps,
          slippage_bps: p.test_config.slippage_bps,
          liquidation_fee_bps: p.test_config.liquidation_fee_bps,
          max_gross_pct: p.test_config.max_gross_pct,
          max_daily_loss_pct: p.test_config.max_daily_loss_pct,
          'Maximum order notional (USDT)': p.test_config.max_order_notional,
          'Underlying asset gross limit (%)': p.test_config.max_base_asset_gross_pct,
          'Shared failure policy': t('Reduce the group on failure'),
          'Maximum execution residual %': p.definition.max_residual_pct,
        }}
      />
      <PortfolioExecutionPolicy
        contract={p.definition.execution_contract}
        residual={p.definition.max_residual_pct}
      />
      <DataTable
        rows={p.definition.legs as unknown as RecordData[]}
        columns={[
          { key: 'inst_id', label: 'Market' },
          {
            key: 'weight',
            label: 'Target weight',
            render: (r) => `${number(Number(r.weight) * 100)}%`,
          },
          { key: 'leverage', label: 'Leverage' },
          { key: 'direction', label: 'Direction' },
          {
            key: 'strategy',
            label: 'Strategy',
            render: (r) => String((r.strategy as RecordData)?.kind ?? '—'),
          },
        ]}
      />
      <CapturedLifecycleSources config={p.test_config} />
      <h4>{t('Pre-registered rejection criteria')}</h4>
      <RecordGrid value={p.criteria} />
      <p className="quiet-copy">{p.rejection_plan}</p>
      <p className="quiet-copy">
        {t(
          'Cash benchmark: unchanged initial USDT, zero interest and no trading costs. Warmup has no orders, positions or funding exposure.',
        )}
      </p>
      <JsonDetails
        label="Frozen inputs and contract"
        value={{ plan: p, plan_hash: holdout.plan_hash, input_hash: holdout.input_hash }}
      />
    </div>
  );
}
const defaults = {
  warmup: '20',
  cash: '10000',
  fee: '10',
  slip: '5',
  liquidationFee: '50',
  gross: '200',
  maxOrder: '2500',
  maxBaseGross: '100',
  daily: '5',
  minReturn: '0',
  maxDrawdown: '20',
  minFills: '1',
  minObservations: '20',
};
type NumericKey = keyof typeof defaults;
const numericFields: [NumericKey, string, number, number][] = [
  ['warmup', 'Indicator warmup bars', 0, 2000],
  ['cash', 'Initial cash (USDT)', 100, 1000000000],
  ['fee', 'Fee (bps)', 0, 100],
  ['slip', 'Slippage (bps)', 0, 100],
  ['liquidationFee', 'Liquidation fee (bps)', 0, 500],
  ['gross', 'Maximum gross exposure %', 1, 1000],
  ['daily', 'Daily loss limit %', 0.1, 50],
  ['maxOrder', 'Maximum order notional (USDT)', 0.01, 1000000000],
  ['maxBaseGross', 'Underlying asset gross limit (%)', 1, 1000],
  ['minReturn', 'Minimum return versus cash %', -100, 1000000],
  ['maxDrawdown', 'Maximum accepted drawdown %', 0, 1000],
  ['minFills', 'Minimum executed fills', 0, 1000000],
  ['minObservations', 'Minimum final observations', 2, 20000],
];
export default function PortfolioGovernance({
  source,
  onRun,
}: {
  source: Source;
  onRun: (id: string) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const allowed = canResearch(useSession()?.user?.role);
  const projects = useQuery({
    queryKey: ['portfolio-projects'],
    queryFn: proApi.portfolioProjects,
    refetchOnMount: 'always',
  });
  const [selected, setSelected] = useState('');
  const projectId = selected || projects.data?.items[0]?.id;
  const project = useQuery({
    queryKey: ['portfolio-project', projectId],
    queryFn: () => proApi.portfolioProject(projectId!),
    enabled: !!projectId,
  });
  const report = useQuery({
    queryKey: ['portfolio-governance', projectId],
    queryFn: () => proApi.portfolioGovernance(projectId!),
    enabled: !!projectId,
  });
  const holdouts = useQuery({
    queryKey: ['portfolio-holdouts', source, projectId],
    queryFn: () => proApi.portfolioHoldouts(source, projectId!),
    enabled: !!projectId,
    refetchInterval: 5000,
  });
  const packages = useQuery({
    queryKey: ['pro-packages', source],
    queryFn: () => proApi.packages(source),
    refetchOnMount: 'always',
  });
  const [editing, setEditing] = useState(false);
  const [versionId, setVersionId] = useState('');
  const version = project.data?.versions?.find((v) => v.id === versionId);
  const [packageIds, setPackageIds] = useState<Record<string, string>>({});
  const [name, setName] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [values, setValues] = useState(defaults);
  const [universeMode, setUniverseMode] = useState('static');
  const [lifecycleWarmup, setLifecycleWarmup] = useState(2);
  const [lifecycleEvents, setLifecycleEvents] = useState<Record<string, RecordData[]>>({});
  const [rejection, setRejection] = useState('');
  const [frozen, setFrozen] = useState<PortfolioHoldoutPreview | null>(null);
  const [expanded, setExpanded] = useState('');
  const invalidate = () => {
    for (const key of ['portfolio-holdouts', 'portfolio-governance', 'portfolio-runs'])
      void qc.invalidateQueries({ queryKey: [key] });
  };
  const preview = useMutation({
    mutationFn: () =>
      proApi.previewPortfolioHoldout({
        name,
        portfolio_version_id: versionId,
        package_ids: version!.definition.legs.map((l) => packageIds[l.inst_id]),
        test_start: Date.parse(start + 'Z'),
        test_end: Date.parse(end + 'Z'),
        warmup_bars: Number(values.warmup),
        universe_mode: universeMode,
        lifecycle_warmup_bars: lifecycleWarmup,
        lifecycle_events: universeMode === 'historical_lifecycle' ? lifecycleEvents : {},
        initial_cash: values.cash,
        fee_bps: values.fee,
        slippage_bps: values.slip,
        liquidation_fee_bps: values.liquidationFee,
        max_gross_pct: values.gross,
        max_daily_loss_pct: values.daily,
        max_order_notional: values.maxOrder,
        max_base_asset_gross_pct: values.maxBaseGross,
        benchmark: 'cash',
        rejection_plan: rejection,
        criteria: {
          min_return_vs_cash_pct: values.minReturn,
          max_drawdown_pct: values.maxDrawdown,
          min_trades: Number(values.minFills),
          min_observations: Number(values.minObservations),
          require_zero_debt: true,
        },
      }),
    onSuccess: (p) => setFrozen(p),
  });
  const seal = useMutation({
    mutationFn: () => proApi.sealPortfolioHoldout(frozen!),
    onSuccess: () => {
      setEditing(false);
      setFrozen(null);
      invalidate();
    },
  });
  const evaluate = useMutation({
    mutationFn: proApi.evaluatePortfolioHoldout,
    onSuccess: (r) => {
      invalidate();
      onRun(r.id);
    },
  });
  const choosePackage = (symbol: string, id: string) => {
    const next = { ...packageIds, [symbol]: id };
    setPackageIds(next);
    preview.reset();
    const chosen = version?.definition.legs.map((l) =>
      packages.data?.items.find((p) => p.id === next[l.inst_id]),
    );
    if (chosen?.length && chosen.every(Boolean)) {
      const first =
        (universeMode === 'historical_lifecycle'
          ? Math.min(...chosen.map((p) => p!.start))
          : Math.max(...chosen.map((p) => p!.start))) +
        Number(values.warmup) * intervals[version!.definition.bar];
      const last =
        universeMode === 'historical_lifecycle'
          ? Math.max(...chosen.map((p) => p!.end))
          : Math.min(...chosen.map((p) => p!.end));
      if (first < last) {
        setStart(utc(first));
        setEnd(utc(last));
      }
    }
  };
  const validPackages = version?.definition.legs.every((l) => !!packageIds[l.inst_id]);
  const renderFields = (fields: typeof numericFields) => (
    <div className="form-grid">
      {fields.map(([key, label, min, max]) => (
        <Field key={key} label={label}>
          <input
            type="number"
            min={min}
            max={max}
            step={['warmup', 'minFills', 'minObservations'].includes(key) ? 1 : 'any'}
            required
            value={values[key]}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          />
        </Field>
      ))}
    </div>
  );
  return (
    <>
      <PageHeading
        eyebrow="PORTFOLIO RESEARCH GOVERNANCE"
        title="Portfolio evidence"
        description="Freeze one portfolio, its complete inputs and rejection criteria before the final evaluation."
      >
        <button
          className="button button-citrus"
          disabled={!allowed || !projectId}
          onClick={() => {
            preview.reset();
            seal.reset();
            setFrozen(null);
            setEditing(true);
          }}
        >
          <Plus size={14} />
          {t('Register portfolio holdout')}
        </button>
      </PageHeading>
      <p className="quiet-copy">
        {t(
          'This seal governs research API access across strategies, portfolios, dataset aliases and bar intervals. Public data and external experiments remain outside statistical blindness.',
        )}
      </p>
      <Field label="Portfolio project">
        <select
          value={projectId ?? ''}
          onChange={(e) => {
            setSelected(e.target.value);
            setVersionId('');
            setEditing(false);
            setFrozen(null);
          }}
        >
          <option value="">{t('Choose a portfolio project')}</option>
          {projects.data?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      {projects.isError && <ErrorBox error={projects.error} />}
      {project.isError && <ErrorBox error={project.error} />}
      {report.isError && <ErrorBox error={report.error} />}
      {report.data && (
        <div className="governance-statistics">
          <RecordGrid
            value={{
              recorded_attempts: report.data.recorded_attempts,
              primary_evaluations: report.data.primary_evaluations,
              replay_attempts: report.data.replay_attempts,
              candidate_configurations: report.data.candidate_configurations,
              distinct_configurations: report.data.distinct_configurations,
            }}
          />
          <p className="quiet-copy">
            {t(
              'Counts cover admitted project attempts across versions, including failures and retained trials after restore. Replays reproduce a primary evaluation; they add no independent evidence.',
            )}
          </p>
        </div>
      )}
      {editing && (
        <section className="pro-panel portfolio-holdout-editor">
          <div className="section-heading">
            <h2>{t(frozen ? 'Review the captured contract' : 'Freeze the final evaluation')}</h2>
            <button
              type="button"
              className="icon-button"
              aria-label={t('Close holdout editor')}
              onClick={() => setEditing(false)}
            >
              <X size={16} />
            </button>
          </div>
          {frozen ? (
            <>
              <FrozenPlan holdout={frozen} />
              <p className="quiet-copy">
                {t(
                  'Sealing reserves these final market-time intervals. Evaluation consumes the seal in the same transaction that admits the primary run; a committed failure does not reset it.',
                )}
              </p>
              {seal.isError && <ErrorBox error={seal.error} />}
              <div className="toolbar">
                <button
                  className="button button-citrus"
                  disabled={!allowed || seal.isPending || frozen.blockers.length > 0}
                  onClick={() => seal.mutate()}
                >
                  <Lock size={14} />
                  {t('Seal captured portfolio')}
                </button>
                <button
                  className="text-button"
                  disabled={seal.isPending}
                  onClick={() => {
                    setFrozen(null);
                    preview.reset();
                    seal.reset();
                  }}
                >
                  {t('Revise before sealing')}
                </button>
              </div>
            </>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                preview.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Holdout name">
                  <input
                    required
                    minLength={2}
                    maxLength={100}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <Field label="Portfolio version">
                  <select
                    required
                    value={versionId}
                    onChange={(e) => {
                      setVersionId(e.target.value);
                      setPackageIds({});
                      setStart('');
                      setEnd('');
                      setLifecycleEvents({});
                    }}
                  >
                    <option value="">{t('Choose an immutable version')}</option>
                    {project.data?.versions?.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.revision} · {v.definition.mode} · {v.definition.bar}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <LifecycleScenario
                mode={universeMode}
                warmup={lifecycleWarmup}
                onMode={(value) => {
                  setUniverseMode(value);
                  preview.reset();
                }}
                onWarmup={(value) => {
                  setLifecycleWarmup(value);
                  preview.reset();
                }}
              />
              {version && (
                <div className="holdout-package-selection">
                  <p className="quiet-copy">{version.hypothesis}</p>
                  <p className="quiet-copy">
                    {t('Shared failure policy')}: {t('Reduce the group on failure')} ·{' '}
                    {t('Maximum execution residual %')}:{' '}
                    {number(version.definition.max_residual_pct)}%
                  </p>
                  <PortfolioExecutionPolicy
                    contract={version.definition.execution_contract}
                    residual={version.definition.max_residual_pct}
                  />
                  {!version.definition.execution_contract && (
                    <p className="inline-warning">
                      {t('Revise this legacy study under the shared execution policy.')}
                    </p>
                  )}
                  {version.definition.legs.map((l) => (
                    <Field key={l.inst_id} label={`Package · ${l.inst_id}`}>
                      <select
                        required
                        value={packageIds[l.inst_id] ?? ''}
                        onChange={(e) => choosePackage(l.inst_id, e.target.value)}
                      >
                        <option value="">{t('Select a ready package')}</option>
                        {packages.data?.items
                          .filter(
                            (p) =>
                              p.ready &&
                              p.inst_id === l.inst_id &&
                              p.bar === version.definition.bar,
                          )
                          .map((p) => (
                            <option key={p.id} value={p.id}>
                              {date(p.start)} → {date(p.end)} · {p.id.slice(0, 8)}
                            </option>
                          ))}
                      </select>
                    </Field>
                  ))}
                  {universeMode === 'historical_lifecycle' &&
                    version.definition.legs.map((l) => (
                      <LifecycleImport
                        key={l.inst_id}
                        symbol={l.inst_id}
                        events={lifecycleEvents[l.inst_id] ?? []}
                        onChange={(events) => {
                          setLifecycleEvents((old) => ({ ...old, [l.inst_id]: events }));
                          preview.reset();
                        }}
                      />
                    ))}
                </div>
              )}
              {packages.isError && <ErrorBox error={packages.error} />}
              <div className="form-grid">
                <Field label="UTC final start">
                  <input
                    type="datetime-local"
                    required
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </Field>
                <Field label="UTC final end">
                  <input
                    type="datetime-local"
                    required
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </Field>
              </div>
              {renderFields(numericFields.slice(0, 9))}
              <h3>{t('Pre-registered rejection criteria')}</h3>
              <p className="quiet-copy">
                {t(
                  'Cash benchmark: unchanged initial USDT, zero interest and no trading costs. Warmup has no orders, positions or funding exposure.',
                )}
              </p>
              {renderFields(numericFields.slice(9))}
              <p className="quiet-copy">
                {t(
                  'Zero insurance debt is required. Too few observations or fills produce an inconclusive assessment. These are fixed decision criteria, not a statistical significance test.',
                )}
              </p>
              <Field label="Rejection plan">
                <textarea
                  required
                  minLength={20}
                  maxLength={4000}
                  rows={3}
                  value={rejection}
                  onChange={(e) => setRejection(e.target.value)}
                />
              </Field>
              {preview.isError && <ErrorBox error={preview.error} />}
              <button
                className="button button-citrus"
                disabled={!allowed || !validPackages || preview.isPending}
              >
                <Lock size={14} />
                {t('Capture evaluation preview')}
              </button>
            </form>
          )}
        </section>
      )}
      <section className="pro-panel">
        <h2>{t('Sealed portfolio evaluations')}</h2>
        {holdouts.isPending && projectId ? (
          <Loading />
        ) : holdouts.isError ? (
          <ErrorBox error={holdouts.error} />
        ) : !holdouts.data?.items.length ? (
          <Empty title="No portfolio holdouts">
            {t(
              'Save an immutable portfolio version, prepare every package, then freeze a previously unexposed final interval.',
            )}
          </Empty>
        ) : (
          holdouts.data.items.map((h) => (
            <article className="portfolio-holdout-row" key={h.id}>
              <div className="section-heading">
                <div>
                  <h3>{h.plan.name}</h3>
                  <p className="quiet-copy">
                    {date(h.plan.test_start, true)} → {date(h.plan.test_end, true)} ·{' '}
                    {h.plan.definition.legs.map((l) => l.inst_id).join(' / ')}
                  </p>
                </div>
                <Status>{t(h.status)}</Status>
              </div>
              <div className="toolbar">
                {h.status === 'sealed' ? (
                  <button
                    className="button button-secondary"
                    disabled={!allowed || evaluate.isPending}
                    onClick={() => evaluate.mutate(h)}
                  >
                    <Play size={14} />
                    {t('Evaluate sealed portfolio')}
                  </button>
                ) : h.run_id && h.status !== 'consumed_unavailable' ? (
                  <button className="text-button" onClick={() => onRun(h.run_id!)}>
                    {t('Inspect primary evaluation')}
                  </button>
                ) : (
                  <span className="quiet-copy">
                    {t('Consumed evidence is absent after recovery; this seal remains closed.')}
                  </span>
                )}
                <button
                  className="text-button"
                  aria-expanded={expanded === h.id}
                  onClick={() => setExpanded(expanded === h.id ? '' : h.id)}
                >
                  {t('Evaluation contract')}
                </button>
              </div>
              {expanded === h.id && <FrozenPlan holdout={h} />}
            </article>
          ))
        )}
        {evaluate.isError && <ErrorBox error={evaluate.error} />}
      </section>
      {report.data && (
        <section className="pro-panel">
          <h2>{t('Portfolio trial history')}</h2>
          <DataTable
            rows={report.data.items}
            columns={[
              {
                key: 'run_id',
                label: 'Run',
                render: (r) => (
                  <button
                    className="text-button"
                    disabled={r.status === 'evidence_unavailable'}
                    onClick={() => onRun(String(r.run_id))}
                  >
                    {String(r.run_id).slice(0, 12)}
                  </button>
                ),
              },
              {
                key: 'status',
                label: 'Status',
                render: (r) => <Status>{t(String(r.status))}</Status>,
              },
              { key: 'attempt_type', label: 'Attempt' },
              {
                key: 'candidate_count',
                label: 'Candidates',
                render: (r) => number(r.candidate_count, 0),
              },
              {
                key: 'created_at',
                label: 'Created',
                render: (r) => date(Number(r.created_at), true),
              },
              { key: 'details', label: 'Evidence', render: (r) => <JsonDetails value={r} /> },
            ]}
          />
        </section>
      )}
    </>
  );
}
