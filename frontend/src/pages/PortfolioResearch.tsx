import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus, Play, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Source, Strategy } from '../api';
import { defaultStrategy, downloadBlob } from '../api';
import { proApi } from '../proApi';
import { useSession } from '../components/AuthGate';
import { DataTable, JsonDetails, RecordGrid, WorkspaceTabs } from '../components/ProWorkspace';
import ResearchChart from '../components/ResearchChart';
import {
  Empty,
  ErrorBox,
  Field,
  Loading,
  PageHeading,
  Status,
  StrategyFields,
  StrategyExitFields,
} from '../components/workspace';
import { date, number } from '../lib/format';
import type { RecordData } from '../proApi';
import { useI18n } from '../lib/i18n';
import { canResearch } from '../lib/permissions';
import PortfolioReleaseReview from '../components/PortfolioReleaseReview';
import type { PortfolioDefinition, PortfolioVersion } from '../proApi';
import portfolioRecipeData from '../../../examples/portfolios.json';

const recipes = portfolioRecipeData as unknown as {
  id: string;
  name: string;
  hypothesis: string;
  definition: PortfolioDefinition;
}[];

type Leg = {
  package_id: string;
  weight: string;
  leverage: string;
  direction: string;
  strategy: Strategy;
};
const newLeg = (): Leg => ({
  package_id: '',
  weight: '.5',
  leverage: '1',
  direction: 'long_only',
  strategy: { ...defaultStrategy },
});
export default function PortfolioResearch({
  source,
  onData,
  onExecution,
  initialRunId,
}: {
  source: Source;
  onData: () => void;
  onExecution: () => void;
  initialRunId?: string;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canResearch(useSession()?.user?.role);
  const packages = useQuery({
    queryKey: ['pro-packages', source],
    queryFn: () => proApi.packages(source),
    refetchOnMount: 'always',
    refetchInterval: 10000,
  });
  const runs = useQuery({
    queryKey: ['portfolio-runs', source],
    queryFn: () => proApi.portfolioRuns(source),
    refetchInterval: 5000,
  });
  const [active, setActive] = useState(initialRunId ?? '');
  useEffect(() => {
    if (initialRunId) setActive(initialRunId);
  }, [initialRunId]);
  const id = active || runs.data?.items[0]?.id;
  const run = useQuery({
    queryKey: ['portfolio-run', id],
    queryFn: () => proApi.portfolioRun(id!),
    enabled: !!id,
    refetchInterval: (q) =>
      ['queued', 'running'].includes(q.state.data?.status ?? '') ? 1500 : false,
  });
  useEffect(() => {
    if (run.data?.status === 'completed' || run.data?.status === 'failed')
      void qc.invalidateQueries({ queryKey: ['portfolio-runs', source] });
  }, [run.data?.status, source, qc]);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [hypothesis, setHypothesis] = useState('');
  const [mode, setMode] = useState('fixed_weights');
  const [legs, setLegs] = useState<Leg[]>([newLeg(), newLeg()]);
  const [projectId, setProjectId] = useState('');
  const [recipeId, setRecipeId] = useState('');
  const [parentId, setParentId] = useState('');
  const [capitalPct, setCapitalPct] = useState('100');
  const [residualPct, setResidualPct] = useState('2');
  const [carryThreshold, setCarryThreshold] = useState('0');
  const projects = useQuery({
    queryKey: ['portfolio-projects'],
    queryFn: proApi.portfolioProjects,
  });
  const [cash, setCash] = useState('10000');
  const [fee, setFee] = useState('10');
  const [slip, setSlip] = useState('5');
  const [rebalance, setRebalance] = useState(24);
  const [lookback, setLookback] = useState(20);
  const [topK, setTopK] = useState(1);
  const [gross, setGross] = useState(200);
  const [daily, setDaily] = useState(5);
  const [evaluation, setEvaluation] = useState('full');
  const [trainPct, setTrainPct] = useState(70);
  const [embargo, setEmbargo] = useState(1);
  const [tab, setTab] = useState('overview');
  const create = useMutation({
    mutationFn: async () => {
      const selected = legs.map((leg) => packages.data?.items.find((p) => p.id === leg.package_id));
      if (selected.some((p) => !p))
        throw new Error(t('Select a ready package for every portfolio leg.'));
      const definition: PortfolioDefinition = {
        bar: selected[0]!.bar,
        mode,
        capital_pct: capitalPct,
        rebalance_bars: rebalance,
        lookback,
        top_k: topK,
        carry_threshold: carryThreshold,
        max_residual_pct: residualPct,
        failure_policy: 'reduce_group',
        legs: legs.map((leg, i) => ({
          inst_id: selected[i]!.inst_id,
          weight: leg.weight,
          leverage: leg.leverage,
          direction: leg.direction,
          strategy: leg.strategy,
        })),
      };
      let version: PortfolioVersion;
      if (projectId)
        version = await proApi.createPortfolioVersion(projectId, {
          hypothesis,
          definition,
          ...(parentId ? { parent_id: parentId } : {}),
        });
      else {
        const saved = await proApi.createPortfolioProject({ name, hypothesis, definition });
        setProjectId(saved.id);
        version = saved.version!;
      }
      setParentId(version.id);
      void qc.invalidateQueries({ queryKey: ['portfolio-projects'] });
      return proApi.createPortfolioRun({
        name,
        hypothesis,
        mode,
        legs,
        capital_pct: capitalPct,
        portfolio_version_id: version.id,
        initial_cash: cash,
        fee_bps: fee,
        slippage_bps: slip,
        rebalance_bars: rebalance,
        lookback,
        top_k: topK,
        carry_threshold: carryThreshold,
        max_gross_pct: gross,
        max_daily_loss_pct: daily,
        evaluation,
        train_pct: trainPct,
        embargo_bars: embargo,
      });
    },
    onSuccess: (r) => {
      setActive(r.id);
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ['portfolio-runs', source] });
    },
  });
  const replay = useMutation({
    mutationFn: () => proApi.replayPortfolio(id!),
    onSuccess: (r) => {
      setActive(r.id);
      void qc.invalidateQueries({ queryKey: ['portfolio-runs', source] });
      void qc.invalidateQueries({ queryKey: ['portfolio-governance'] });
    },
  });
  const loadProject = useMutation({
    mutationFn: async (id: string) => {
      const project = await proApi.portfolioProject(id);
      const version = project.versions![0];
      const d = version.definition;
      setProjectId(project.id);
      setParentId(version.id);
      setName(project.name);
      setHypothesis(version.hypothesis);
      setMode(d.mode);
      setCapitalPct(d.capital_pct);
      setResidualPct(d.max_residual_pct);
      setCarryThreshold(d.carry_threshold);
      setRebalance(d.rebalance_bars);
      setLookback(d.lookback);
      setTopK(d.top_k);
      const ready = packages.data?.items.filter((p) => p.ready && p.bar === d.bar) ?? [];
      const first = ready.find((p) => p.inst_id === d.legs[0].inst_id);
      setLegs(
        d.legs.map((leg) => ({
          ...leg,
          package_id:
            ready.find(
              (p) => p.inst_id === leg.inst_id && p.start === first?.start && p.end === first?.end,
            )?.id ?? '',
        })),
      );
    },
  });
  const revise = useMutation({
    mutationFn: async () => {
      const config = run.data!.config;
      const versionId = config.portfolio_version_id as string | undefined;
      if (versionId) {
        const v = await proApi.portfolioVersion(versionId);
        setProjectId(v.project_id);
        setParentId(v.id);
        setResidualPct(v.definition.max_residual_pct);
      } else {
        setProjectId('');
        setParentId('');
      }
      setName(String(config.name));
      setHypothesis(String(config.hypothesis));
      setMode(String(config.mode));
      setLegs(config.legs as Leg[]);
      setCapitalPct(String(config.capital_pct ?? 100));
      setCarryThreshold(String(config.carry_threshold));
      setCash(String(config.initial_cash));
      setFee(String(config.fee_bps));
      setSlip(String(config.slippage_bps));
      setRebalance(Number(config.rebalance_bars));
      setLookback(Number(config.lookback));
      setTopK(Number(config.top_k));
      setGross(Number(config.max_gross_pct));
      setDaily(Number(config.max_daily_loss_pct));
      setEvaluation(String(config.evaluation));
      setTrainPct(Number(config.train_pct));
      setEmbargo(Number(config.embargo_bars));
      create.reset();
      setEditing(true);
    },
  });
  const patch = (i: number, value: Partial<Leg>) =>
    setLegs((old) => old.map((leg, index) => (index === i ? { ...leg, ...value } : leg)));
  const ready = packages.data?.items.filter((p) => p.ready) ?? [];
  const first = ready.find((p) => p.id === legs[0].package_id);
  const choices = ready.filter(
    (p) => !first || (p.bar === first.bar && p.start === first.start && p.end === first.end),
  );
  const plan = run.data?.result;
  const evaluationEvidence = plan?.evaluation as RecordData | undefined;
  const sealedAssessment = String(
    (evaluationEvidence?.rejection as RecordData)?.status ?? 'unavailable',
  );
  const trainingMetrics = evaluationEvidence?.train_metrics as RecordData | undefined;
  return (
    <>
      <PageHeading
        eyebrow="SHARED CAPITAL RESEARCH"
        title="Portfolio research"
        description="One cash budget across spot and perpetual legs, causal decisions and a reconciled native-asset journal."
      >
        <button className="button button-secondary" onClick={onData}>
          {t('Prepare data')}
        </button>
        <button
          className="button button-citrus"
          disabled={!canOperate}
          onClick={() => {
            create.reset();
            setProjectId('');
            setParentId('');
            setRecipeId('');
            setEditing(true);
          }}
        >
          <Plus size={14} />
          {t('New portfolio study')}
        </button>
      </PageHeading>
      {editing ? (
        <section className="pro-panel portfolio-study-editor">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <div className="section-heading">
              <h2>{t('Portfolio hypothesis')}</h2>
              <button
                type="button"
                className="icon-button"
                aria-label={t('Close')}
                onClick={() => setEditing(false)}
              >
                <X size={18} />
              </button>
            </div>
            {!projectId && (
              <Field
                label="Portfolio starting point"
                hint="Reference hypotheses with failure criteria; no investment edge is claimed."
              >
                <select
                  value={recipeId}
                  onChange={(e) => {
                    setRecipeId(e.target.value);
                    const recipe = recipes.find((r) => r.id === e.target.value);
                    if (!recipe) return;
                    const d = recipe.definition;
                    setName(recipe.name);
                    setHypothesis(recipe.hypothesis);
                    setMode(d.mode);
                    setCapitalPct(d.capital_pct);
                    setResidualPct(d.max_residual_pct);
                    setCarryThreshold(d.carry_threshold);
                    setRebalance(d.rebalance_bars);
                    setLookback(d.lookback);
                    setTopK(d.top_k);
                    setEvaluation('train_test');
                    const matching = ready.filter((p) => p.bar === d.bar);
                    const anchor = matching.find((p) => p.inst_id === d.legs[0].inst_id);
                    setLegs(
                      d.legs.map((leg) => ({
                        weight: leg.weight,
                        leverage: leg.leverage,
                        direction: leg.direction,
                        strategy: { ...leg.strategy },
                        package_id:
                          matching.find(
                            (p) =>
                              p.inst_id === leg.inst_id &&
                              p.start === anchor?.start &&
                              p.end === anchor?.end,
                          )?.id ?? '',
                      })),
                    );
                  }}
                >
                  <option value="">{t('Custom hypothesis')}</option>
                  {recipes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {t(r.name)}
                    </option>
                  ))}
                </select>
                {recipeId && (
                  <p className="quiet-copy">
                    {recipes
                      .find((r) => r.id === recipeId)
                      ?.definition.legs.map((l) => l.inst_id)
                      .join(' · ')}{' '}
                    · 1H
                  </p>
                )}
              </Field>
            )}
            <div className="form-grid">
              <Field label="Saved portfolio">
                <select
                  value={projectId}
                  disabled={loadProject.isPending}
                  onChange={(e) =>
                    e.target.value
                      ? loadProject.mutate(e.target.value)
                      : (setProjectId(''), setParentId(''))
                  }
                >
                  <option value="">{t('New portfolio definition')}</option>
                  {projects.data?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · v{p.latest_revision}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Study name">
                <input
                  required
                  minLength={2}
                  maxLength={100}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Construction">
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="fixed_weights">{t('Fixed notional weights')}</option>
                  <option value="independent_signals">
                    {t('Independent signals, shared capital')}
                  </option>
                  <option value="momentum">{t('Positive momentum rotation')}</option>
                  <option value="funding_carry">{t('Lagged funding carry')}</option>
                </select>
              </Field>
            </div>
            <Field label="Economic hypothesis">
              <textarea
                className="study-hypothesis-input"
                required
                minLength={12}
                maxLength={4000}
                rows={3}
                value={hypothesis}
                onChange={(e) => setHypothesis(e.target.value)}
              />
            </Field>
            <p className="quiet-copy">
              {t(
                'Use ready packages with identical source, interval and UTC window. Weights are signed notional / account equity. Spot weights must be nonnegative.',
              )}
            </p>
            {packages.isError && <ErrorBox error={packages.error} />}
            {!ready.length && (
              <Empty title="No ready packages">
                {t('Prepare at least two aligned market packages in the data library.')}
              </Empty>
            )}
            {legs.map((leg, i) => (
              <div className="portfolio-study-leg" key={i}>
                <div className="section-heading">
                  <h3>
                    {t('Leg')} {i + 1}
                  </h3>
                  {legs.length > 2 && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setLegs((old) => old.filter((_, index) => index !== i))}
                    >
                      {t('Remove')}
                    </button>
                  )}
                </div>
                <div className="portfolio-leg-fields">
                  <Field label={`Package ${i + 1}`}>
                    <select
                      required
                      value={leg.package_id}
                      onChange={(e) => patch(i, { package_id: e.target.value })}
                    >
                      <option value="">{t('Choose a ready package')}</option>
                      {(i === 0 ? ready : choices).map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.inst_id} · {p.bar} · {date(p.start)} · {p.id.slice(0, 7)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Notional weight %">
                    <input
                      required
                      type="number"
                      min={-500}
                      max={500}
                      step={0.1}
                      value={Number(leg.weight) * 100}
                      onChange={(e) => patch(i, { weight: String(Number(e.target.value) / 100) })}
                    />
                  </Field>
                  <Field label="Leverage">
                    <input
                      required
                      type="number"
                      min={1}
                      max={50}
                      value={leg.leverage}
                      onChange={(e) => patch(i, { leverage: e.target.value })}
                    />
                  </Field>
                </div>
                {mode !== 'independent_signals' && (
                  <StrategyExitFields
                    value={leg.strategy}
                    onChange={(strategy) => patch(i, { strategy })}
                  />
                )}
                {mode === 'independent_signals' && (
                  <details>
                    <summary>{t('Signal policy')}</summary>
                    <Field label="Direction">
                      <select
                        value={leg.direction}
                        onChange={(e) => patch(i, { direction: e.target.value })}
                      >
                        <option value="long_only">{t('Long only')}</option>
                        <option value="short_only">{t('Short only')}</option>
                        <option value="long_short">{t('Long / short')}</option>
                      </select>
                    </Field>
                    <StrategyFields
                      professional
                      showAllocation={false}
                      value={leg.strategy}
                      onChange={(strategy) => patch(i, { strategy })}
                    />
                  </details>
                )}
              </div>
            ))}
            <button
              type="button"
              className="text-button"
              disabled={legs.length >= 10}
              onClick={() => setLegs((old) => [...old, newLeg()])}
            >
              <Plus size={13} />
              {t('Add market leg')}
            </button>
            {mode === 'funding_carry' && (
              <p className="inline-warning">
                {t(
                  'Carry requires exactly two ordered legs: spot long, matching perpetual short, equal absolute weights. Only prior realized funding is used; positive funding is not guaranteed to persist.',
                )}
              </p>
            )}
            <div className="form-grid portfolio-study-policy">
              <Field label="Evaluation">
                <select value={evaluation} onChange={(e) => setEvaluation(e.target.value)}>
                  <option value="full">{t('Full window development')}</option>
                  <option value="train_test">{t('Independent chronological test')}</option>
                </select>
              </Field>
              {evaluation === 'train_test' && (
                <>
                  <Field label="Training window %">
                    <input
                      required
                      type="number"
                      min={50}
                      max={85}
                      value={trainPct}
                      onChange={(e) => setTrainPct(Number(e.target.value))}
                    />
                  </Field>
                  <Field label="Embargo bars">
                    <input
                      required
                      type="number"
                      min={0}
                      max={400}
                      value={embargo}
                      onChange={(e) => setEmbargo(Number(e.target.value))}
                    />
                  </Field>
                </>
              )}
              <Field label="Capital allocation %">
                <input
                  required
                  type="number"
                  min={0.01}
                  max={100}
                  step="any"
                  value={capitalPct}
                  onChange={(e) => setCapitalPct(e.target.value)}
                />
              </Field>
              <Field label="Maximum execution residual %">
                <input
                  required
                  type="number"
                  min={0.01}
                  max={100}
                  step="any"
                  value={residualPct}
                  onChange={(e) => setResidualPct(e.target.value)}
                />
              </Field>
              {mode === 'funding_carry' && (
                <Field label="Prior funding threshold">
                  <input
                    required
                    type="number"
                    min={-0.01}
                    max={0.01}
                    step="any"
                    value={carryThreshold}
                    onChange={(e) => setCarryThreshold(e.target.value)}
                  />
                </Field>
              )}
              <Field label="Initial capital">
                <input
                  required
                  type="number"
                  min={1}
                  max={1000000000}
                  value={cash}
                  onChange={(e) => setCash(e.target.value)}
                />
              </Field>
              <Field label="Rebalance every N bars">
                <input
                  required
                  type="number"
                  min={1}
                  max={1000}
                  value={rebalance}
                  onChange={(e) => setRebalance(Number(e.target.value))}
                />
              </Field>
              <Field label="Fee (bps)">
                <input
                  required
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={fee}
                  onChange={(e) => setFee(e.target.value)}
                />
              </Field>
              <Field label="Slippage (bps)">
                <input
                  required
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={slip}
                  onChange={(e) => setSlip(e.target.value)}
                />
              </Field>
              <Field label="Gross exposure limit %">
                <input
                  required
                  type="number"
                  min={1}
                  max={1000}
                  value={gross}
                  onChange={(e) => setGross(Number(e.target.value))}
                />
              </Field>
              <Field label="Daily loss limit %">
                <input
                  required
                  type="number"
                  min={0.1}
                  max={50}
                  step={0.1}
                  value={daily}
                  onChange={(e) => setDaily(Number(e.target.value))}
                />
              </Field>
              {mode === 'momentum' && (
                <>
                  <Field label="Lookback window">
                    <input
                      required
                      type="number"
                      min={2}
                      max={400}
                      value={lookback}
                      onChange={(e) => setLookback(Number(e.target.value))}
                    />
                  </Field>
                  <Field label="Top K markets">
                    <input
                      required
                      type="number"
                      min={1}
                      max={legs.length}
                      value={topK}
                      onChange={(e) => setTopK(Number(e.target.value))}
                    />
                  </Field>
                </>
              )}
            </div>
            <p className="quiet-copy">
              {t(
                'This form captures current instrument rules. Attributed point-in-time rule events are supported through the API. The chosen universe is explicit; no historical listing coverage is inferred. Multi-leg fills are sequential, with residuals and rejections reported.',
              )}
            </p>
            {loadProject.isError && <ErrorBox error={loadProject.error} />}
            <p className="quiet-copy">
              {t(
                'Running research first saves an immutable portfolio version. Changes create a revision; identical definitions reuse the saved version.',
              )}
            </p>
            {create.isError && <ErrorBox error={create.error} />}
            <button
              className="button button-citrus"
              disabled={!canOperate || create.isPending || legs.some((l) => !l.package_id)}
            >
              <Play size={14} />
              {t('Run portfolio research')}
            </button>
          </form>
        </section>
      ) : (
        <div className="portfolio-research-layout">
          <aside className="portfolio-study-history">
            <h2>{t('Saved portfolio studies')}</h2>
            {runs.isError ? (
              <ErrorBox error={runs.error} />
            ) : runs.isPending ? (
              <Loading />
            ) : (
              runs.data?.items.map((r) => (
                <button
                  key={r.id}
                  className={`strategy-project${r.id === id ? ' active' : ''}`}
                  onClick={() => setActive(r.id)}
                >
                  <strong>{String(r.config.name)}</strong>
                  <span>
                    {t(r.status)} · {date(r.created_at)}
                  </span>
                </button>
              ))
            )}
          </aside>
          <section className="pro-panel portfolio-study-result">
            {!id ? (
              <Empty title="Research portfolios with one book">
                {t(
                  'Create an aligned multi-market study to evaluate a common cash balance, rebalance costs and leg risk.',
                )}
              </Empty>
            ) : run.isPending ? (
              <Loading />
            ) : run.isError ? (
              <ErrorBox error={run.error} />
            ) : (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">{String(run.data?.config.mode)}</p>
                    <h2>{String(run.data?.config.name)}</h2>
                  </div>
                  <Status
                    type={
                      run.data?.status === 'completed'
                        ? 'good'
                        : run.data?.status === 'failed'
                          ? 'bad'
                          : 'neutral'
                    }
                  >
                    {t(run.data?.status ?? '')}
                  </Status>
                </div>
                <p className="strategy-hypothesis">{String(run.data?.config.hypothesis)}</p>
                <div className="portfolio-release-actions">
                  <button
                    className="button button-secondary"
                    disabled={!canOperate || revise.isPending}
                    onClick={() => revise.mutate()}
                  >
                    {t('Revise & research')}
                  </button>
                  {run.data?.config.portfolio_version_id ? (
                    <span className="quiet-copy">
                      {t('Immutable portfolio version')} ·{' '}
                      {String(run.data.config.portfolio_version_id).slice(0, 8)}
                    </span>
                  ) : null}
                </div>
                {revise.isError && <ErrorBox error={revise.error} />}
                {replay.isError && <ErrorBox error={replay.error} />}
                {run.data?.status === 'completed' && !!run.data.manifest.input_artifact && (
                  <div className="toolbar">
                    <button
                      className="text-button"
                      disabled={!canOperate || replay.isPending}
                      onClick={() => replay.mutate()}
                    >
                      {t('Replay frozen inputs')}
                    </button>
                    {run.data.manifest.replay_verified === true && (
                      <Status type="good">{t('verified')}</Status>
                    )}
                    {!!run.data.manifest.replay_of && (
                      <span className="quiet-copy">
                        {t('Reproduction · not independent evidence')}
                      </span>
                    )}
                  </div>
                )}
                {evaluationEvidence?.mode === 'sealed_holdout' && (
                  <section className="sealed-evaluation">
                    <div className="section-heading">
                      <span className="eyebrow">{t('One-use portfolio holdout')}</span>
                      <Status
                        type={
                          sealedAssessment === 'passed'
                            ? 'good'
                            : sealedAssessment === 'rejected'
                              ? 'bad'
                              : 'warning'
                        }
                      >
                        {t(sealedAssessment)}
                      </Status>
                    </div>
                    <RecordGrid
                      value={{
                        Benchmark: t('Cash · 0%'),
                        'Final window start': date(Number(evaluationEvidence.test_start), true),
                        'Final window end': date(Number(evaluationEvidence.test_end), true),
                        warmup_bars: evaluationEvidence.warmup_bars,
                      }}
                    />
                    <p className="quiet-copy">
                      {t(
                        'Warmup initializes indicators only. Financial records cover the final window from flat inventory. Replay reproduces the same primary evaluation.',
                      )}
                    </p>
                    <DataTable
                      rows={
                        ((evaluationEvidence.rejection as RecordData)?.checks ?? []) as RecordData[]
                      }
                      columns={[
                        {
                          key: 'metric',
                          label: 'Criterion',
                          render: (r) =>
                            t(
                              (
                                {
                                  return_vs_cash_pct: 'Return above cash (%)',
                                  max_drawdown_pct: 'Maximum drawdown (%)',
                                  zero_debt: 'Insurance debt (USDT)',
                                } as Record<string, string>
                              )[String(r.metric)] ?? String(r.metric),
                            ),
                        },
                        {
                          key: 'actual',
                          label: 'Actual',
                          render: (r) => (
                            <span title={String(r.actual)}>{number(r.actual, 4)}</span>
                          ),
                        },
                        {
                          key: 'threshold',
                          label: 'Threshold',
                          render: (r) => (
                            <span title={String(r.threshold)}>{number(r.threshold, 4)}</span>
                          ),
                        },
                        {
                          key: 'passed',
                          label: 'Assessment',
                          render: (r) => (
                            <Status type={r.passed === true ? 'good' : 'bad'}>
                              {t(r.passed === true ? 'passed' : 'rejected')}
                            </Status>
                          ),
                        },
                      ]}
                    />
                    <JsonDetails value={evaluationEvidence} label="Evaluation contract" />
                  </section>
                )}

                {run.data?.status === 'completed' && (
                  <PortfolioReleaseReview
                    key={run.data.id}
                    runId={run.data.id}
                    bound={!!run.data.config.portfolio_version_id}
                    onExecution={onExecution}
                  />
                )}
                {run.data?.error && <ErrorBox error={new Error(run.data.error)} />}
                {['queued', 'running'].includes(run.data?.status ?? '') && (
                  <p className="quiet-copy">
                    {t('Research progress')} · {Math.round((run.data?.progress ?? 0) * 100)}%
                  </p>
                )}
                {plan && (
                  <>
                    <div className="toolbar">
                      <button
                        className="text-button"
                        onClick={() =>
                          downloadBlob(
                            new Blob([JSON.stringify(run.data, null, 2)], {
                              type: 'application/json',
                            }),
                            `tidebench-portfolio-${id}.json`,
                          )
                        }
                      >
                        <Download size={13} />
                        {t('Export JSON')}
                      </button>
                      <JsonDetails value={run.data?.manifest} label="Input manifest" />
                    </div>
                    <RecordGrid value={plan.metrics} />
                    {evaluationEvidence?.mode === 'train_test' && (
                      <>
                        <p className="quiet-copy">
                          {t(
                            'Financial records below cover the independent test window. Construction is fixed before evaluation; each window starts with flat inventory and independent capital.',
                          )}
                        </p>
                        <DataTable<RecordData>
                          rows={[
                            {
                              window: t('Development'),
                              start: evaluationEvidence.train_start,
                              end: evaluationEvidence.train_end,
                              ...trainingMetrics,
                            },
                            {
                              window: t('Independent test'),
                              start: evaluationEvidence.test_start,
                              end: evaluationEvidence.test_end,
                              ...plan.metrics,
                            },
                          ]}
                          columns={[
                            { key: 'window', label: 'Window' },
                            {
                              key: 'start',
                              label: 'Start',
                              render: (r) => date(Number(r.start), true),
                            },
                            { key: 'end', label: 'End', render: (r) => date(Number(r.end), true) },
                            {
                              key: 'return',
                              label: 'Net return %',
                              render: (r) => number(r.total_return_pct),
                            },
                            {
                              key: 'drawdown',
                              label: 'Max drawdown %',
                              render: (r) => number(r.max_drawdown_pct),
                            },
                            {
                              key: 'fees',
                              label: 'Fees (USDT)',
                              render: (r) => number(r.fees_paid),
                            },
                          ]}
                        />
                        <JsonDetails
                          value={evaluationEvidence}
                          label="Evaluation windows and training evidence"
                        />
                      </>
                    )}
                    <WorkspaceTabs
                      value={tab}
                      onChange={setTab}
                      items={[
                        { key: 'overview', label: 'Equity' },
                        { key: 'decisions', label: 'Decisions' },
                        { key: 'orders', label: 'Orders' },
                        { key: 'ledger', label: 'Ledger' },
                        { key: 'rejections', label: 'Execution rejections' },
                      ]}
                    />
                    {tab === 'overview' && (
                      <>
                        <ResearchChart rows={plan.equity} />
                        <JsonDetails value={plan.assumptions} label="Model assumptions" />
                      </>
                    )}
                    {tab === 'decisions' && (
                      <DataTable
                        rows={plan.decisions}
                        columns={[
                          { key: 'ts', label: 'Decision time', render: (r) => date(Number(r.ts)) },
                          { key: 'weights', label: 'Target weights' },
                          { key: 'cash_scale', label: 'Cash scale' },
                          {
                            key: 'details',
                            label: 'Evidence',
                            render: (r) => <JsonDetails value={r} />,
                          },
                        ]}
                      />
                    )}
                    {tab === 'orders' && (
                      <DataTable
                        rows={plan.orders}
                        columns={[
                          {
                            key: 'quote_ts',
                            label: 'Market time',
                            render: (r) => date(Number(r.quote_ts)),
                          },
                          { key: 'inst_id', label: 'Market' },
                          { key: 'side', label: 'Side' },
                          { key: 'quantity', label: 'Quantity' },
                          { key: 'price', label: 'Fill price' },
                          { key: 'fee', label: 'Fee' },
                          {
                            key: 'details',
                            label: 'Evidence',
                            render: (r) => <JsonDetails value={r} />,
                          },
                        ]}
                      />
                    )}
                    {tab === 'ledger' && (
                      <DataTable
                        rows={plan.ledger}
                        columns={[
                          { key: 'tx_id', label: 'Transaction' },
                          { key: 'asset', label: 'Asset' },
                          { key: 'account', label: 'Account' },
                          { key: 'debit', label: 'Debit' },
                          { key: 'credit', label: 'Credit' },
                        ]}
                      />
                    )}
                    {tab === 'rejections' && (
                      <DataTable
                        rows={plan.execution_rejections}
                        empty="No execution rejections"
                        columns={[
                          { key: 'ts', label: 'Market time', render: (r) => date(Number(r.ts)) },
                          { key: 'inst_id', label: 'Market' },
                          { key: 'code', label: 'Reason' },
                          { key: 'message', label: 'Details' },
                        ]}
                      />
                    )}
                  </>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
