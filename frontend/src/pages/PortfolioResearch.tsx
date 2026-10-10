import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus, Play, Square, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Source, Strategy } from '../api';
import { defaultStrategy, downloadBlob } from '../api';
import { proApi } from '../proApi';
import { useSession } from '../components/AuthGate';
import { DataTable, JsonDetails, RecordGrid, WorkspaceTabs } from '../components/ProWorkspace';
import ResearchChart from '../components/ResearchChart';
import PortfolioRiskEvidence from '../components/PortfolioRiskEvidence';
import LifecycleEvidence, {
  LifecycleImport,
  LifecycleScenario,
} from '../components/LifecycleEvidence';
import PortfolioResearchSummary from '../components/PortfolioResearchSummary';
import PortfolioEconomicsEvidence from '../components/PortfolioEconomicsEvidence';
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
import ResearchCancellationStatus from '../components/ResearchCancellationStatus';
import PortfolioExecutionPolicy, {
  executionPolicyName,
} from '../components/PortfolioExecutionPolicy';
import {
  inputHandoffKey,
  isDraftRecord,
  matchesDraftShape,
  useResearchDraft,
} from '../lib/researchDraft';
import type {
  DataPackage,
  PortfolioDefinition,
  PortfolioResearchRun,
  PortfolioVersion,
  ResearchInputs,
} from '../proApi';
import portfolioRecipeData from '../../../examples/portfolios.json';

const recipes = portfolioRecipeData as unknown as {
  id: string;
  name: string;
  hypothesis: string;
  definition: PortfolioDefinition;
}[];

type Leg = {
  inst_id?: string;
  programDraft?: string;
  package_id: string;
  weight: string;
  leverage: string;
  direction: string;
  strategy: Strategy;
  lifecycle_events?: RecordData[];
  rule_events?: RecordData[];
};
const newLeg = (): Leg => ({
  inst_id: '',
  programDraft: undefined,
  package_id: '',
  weight: '.5',
  leverage: '1',
  direction: 'long_only',
  strategy: { ...defaultStrategy },
  lifecycle_events: [],
});
const newPortfolioDraft = () => ({
  editing: false,
  name: '',
  hypothesis: '',
  mode: 'fixed_weights',
  universeMode: 'static',
  lifecycleWarmup: 2,
  legs: [newLeg(), newLeg()],
  projectId: '',
  recipeId: '',
  parentId: '',
  capitalPct: '100',
  residualPct: '2',
  executionContract: 'reduce_group_v2_allowance' as NonNullable<
    PortfolioDefinition['execution_contract']
  >,
  legacyUpgrade: false,
  carryThreshold: '0',
  carryConfig: {
    carry_window: 1,
    carry_cost_settlements: 0,
    carry_buffer_bps: '0',
    carry_max_age_hours: 0,
  },
  riskConfig: {
    risk_window: 84,
    vol_target_pct: '20',
    vol_floor_pct: '20',
    covariance_shrinkage: '.25',
    correlation_stress: '.75',
  },
  cash: '10000',
  fee: '10',
  slip: '5',
  rebalance: 24,
  lookback: 20,
  topK: 1,
  gross: 200,
  maxOrder: '2500',
  maxBaseGross: '100',
  daily: 5,
  evaluation: 'full',
  trainPct: 70,
  embargo: 1,
  dataLeg: -1,
  lastInputsKey: '',
});
type PortfolioDraft = ReturnType<typeof newPortfolioDraft>;
const validPortfolioDraft = (value: unknown): value is PortfolioDraft =>
  matchesDraftShape(value, newPortfolioDraft()) &&
  isDraftRecord(value) &&
  Object.keys(value).every((key) => Object.hasOwn(newPortfolioDraft(), key)) &&
  Array.isArray(value.legs) &&
  value.legs.length >= 2 &&
  value.legs.length <= 10 &&
  value.legs.every(
    (leg) =>
      isDraftRecord(leg) &&
      (leg.inst_id === undefined || typeof leg.inst_id === 'string') &&
      (leg.programDraft === undefined || typeof leg.programDraft === 'string') &&
      (leg.rule_events === undefined ||
        (Array.isArray(leg.rule_events) && leg.rule_events.every(isDraftRecord))) &&
      ['long_only', 'short_only', 'long_short'].includes(String(leg.direction)),
  ) &&
  ['fixed_weights', 'independent_signals', 'momentum', 'risk_momentum', 'funding_carry'].includes(
    String(value.mode),
  ) &&
  ['static', 'historical_lifecycle'].includes(String(value.universeMode)) &&
  ['reduce_group_v1', 'reduce_group_v2_allowance'].includes(String(value.executionContract));
export default function PortfolioResearch({
  source,
  onData,
  onExecution,
  initialRunId,
  initialInputs,
  onRunSelect,
  onClearDraft,
}: {
  source: Source;
  onData: () => void;
  onExecution: (id?: string) => void;
  initialRunId?: string;
  initialInputs?: ResearchInputs;
  onRunSelect?: (id: string) => void;
  onClearDraft?: () => void;
}) {
  const { t, language } = useI18n();
  const text = (en: string, zh: string) => (language === 'zh-CN' ? zh : en);
  const qc = useQueryClient();
  const session = useSession();
  const canOperate = canResearch(session?.user?.role);
  const userId = session?.user?.id ?? session?.user?.username ?? 'service';
  const draft = useResearchDraft(
    'portfolio',
    userId,
    source,
    newPortfolioDraft,
    validPortfolioDraft,
  );
  const latestDraft = useRef({ source, userId, value: draft.value });
  latestDraft.current = { source, userId, value: draft.value };
  const [submittedRun, setSubmittedRun] = useState<{
    source: Source;
    userId: string;
    id: string;
  }>();
  const [dataLeg, setDataLeg] = draft.field('dataLeg');
  const [lastInputsKey, setLastInputsKey] = draft.field('lastInputsKey');
  const [handoffError, setHandoffError] = useState('');
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
  const revisionGeneration = useRef(0);
  const selectedContext = useRef({ source, id: initialRunId ?? '' });
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      revisionGeneration.current += 1;
    };
  }, []);
  useEffect(() => {
    revisionGeneration.current += 1;
    selectedContext.current = { source, id: initialRunId ?? '' };
    setActive(initialRunId ?? '');
  }, [initialRunId, source]);
  const selectRun = (identifier: string) => {
    revisionGeneration.current += 1;
    selectedContext.current = { source, id: identifier };
    setActive(identifier);
    onRunSelect?.(identifier);
  };
  const id = active || undefined;
  type RequestContext = {
    task: 'load' | 'create' | 'replay' | 'revise';
    source: Source;
    userId: string;
    id: string;
    generation: number;
    fingerprint: string;
  };
  const beginRequest = (task: RequestContext['task']): RequestContext => {
    revisionGeneration.current += 1;
    return {
      task,
      source,
      userId,
      id: selectedContext.current.id,
      generation: revisionGeneration.current,
      fingerprint: JSON.stringify(latestDraft.current.value),
    };
  };
  const sameScope = (request: RequestContext) =>
    mounted.current &&
    latestDraft.current.source === request.source &&
    latestDraft.current.userId === request.userId;
  const currentTask = (request?: RequestContext) =>
    !!request &&
    sameScope(request) &&
    revisionGeneration.current === request.generation &&
    selectedContext.current.id === request.id;
  const currentRequest = (request?: RequestContext) =>
    currentTask(request) && JSON.stringify(latestDraft.current.value) === request!.fingerprint;
  const rememberRun = (item: PortfolioResearchRun, request: RequestContext) => {
    qc.setQueryData(['portfolio-run', item.id], item);
    void qc.invalidateQueries({ queryKey: ['portfolio-runs', request.source] });
    void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    if (!currentRequest(request) && sameScope(request))
      setSubmittedRun({ source: request.source, userId: request.userId, id: item.id });
  };
  const run = useQuery({
    queryKey: ['portfolio-run', id],
    queryFn: async () => {
      const item = await proApi.portfolioRun(id!);
      if (item.source !== source)
        throw new Error(t('The selected run belongs to another data source.'));
      return item;
    },
    enabled: !!id,
    refetchInterval: (q) =>
      ['queued', 'running'].includes(q.state.data?.status ?? '') ? 1500 : false,
  });
  useEffect(() => {
    if (
      run.data?.status === 'completed' ||
      run.data?.status === 'failed' ||
      run.data?.status === 'cancelled'
    )
      void qc.invalidateQueries({ queryKey: ['portfolio-runs', source] });
  }, [run.data?.status, source, qc]);
  const cancel = useMutation({
    mutationFn: proApi.cancelPortfolioRun,
    onSuccess: (item) => {
      qc.setQueryData(['portfolio-run', item.id], item);
      void qc.invalidateQueries({ queryKey: ['portfolio-runs', source] });
      void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    },
  });
  const cancellationRequested =
    (run.data?.manifest?.cancellation as RecordData | undefined)?.state === 'requested';
  const [editing, setEditing] = draft.field('editing');
  // A run in the URL is an explicit inspection request. Its result may
  // temporarily cover an editing draft without replacing that draft's intent.
  const showEditor = editing && !id;
  const openDraft = () => {
    setEditing(true);
    selectRun('');
  };
  const [name, setName] = draft.field('name');
  const [hypothesis, setHypothesis] = draft.field('hypothesis');
  const [mode, setMode] = draft.field('mode');
  const [universeMode, setUniverseMode] = draft.field('universeMode');
  const [lifecycleWarmup, setLifecycleWarmup] = draft.field('lifecycleWarmup');
  const [legs, setLegs] = draft.field('legs');
  const [projectId, setProjectId] = draft.field('projectId');
  const [recipeId, setRecipeId] = draft.field('recipeId');
  const [, setParentId] = draft.field('parentId');
  const [capitalPct, setCapitalPct] = draft.field('capitalPct');
  const [residualPct, setResidualPct] = draft.field('residualPct');
  const [executionContract, setExecutionContract] = draft.field('executionContract');
  const [legacyUpgrade, setLegacyUpgrade] = draft.field('legacyUpgrade');
  const loadExecution = (definition: Partial<PortfolioDefinition>, savedEvidence = true) => {
    setResidualPct(definition.max_residual_pct ?? '2');
    setExecutionContract(definition.execution_contract ?? 'reduce_group_v2_allowance');
    setLegacyUpgrade(savedEvidence && !definition.execution_contract);
  };
  const [carryThreshold, setCarryThreshold] = draft.field('carryThreshold');
  const [carryConfig, setCarryConfig] = draft.field('carryConfig');
  const loadCarry = (d: Partial<PortfolioDefinition>) =>
    setCarryConfig({
      carry_window: d.carry_window ?? 1,
      carry_cost_settlements: d.carry_cost_settlements ?? 0,
      carry_buffer_bps: d.carry_buffer_bps ?? '0',
      carry_max_age_hours: d.carry_max_age_hours ?? 0,
    });
  const [riskConfig, setRiskConfig] = draft.field('riskConfig');
  const loadRisk = (d: Partial<PortfolioDefinition>) =>
    setRiskConfig({
      risk_window: d.risk_window ?? 84,
      vol_target_pct: d.vol_target_pct ?? '20',
      vol_floor_pct: d.vol_floor_pct ?? '20',
      covariance_shrinkage: d.covariance_shrinkage ?? '.25',
      correlation_stress: d.correlation_stress ?? '.75',
    });
  const projects = useQuery({
    queryKey: ['portfolio-projects'],
    queryFn: proApi.portfolioProjects,
  });
  const [cash, setCash] = draft.field('cash');
  const [fee, setFee] = draft.field('fee');
  const [slip, setSlip] = draft.field('slip');
  const [rebalance, setRebalance] = draft.field('rebalance');
  const [lookback, setLookback] = draft.field('lookback');
  const [topK, setTopK] = draft.field('topK');
  const [gross, setGross] = draft.field('gross');
  const [maxOrder, setMaxOrder] = draft.field('maxOrder');
  const [maxBaseGross, setMaxBaseGross] = draft.field('maxBaseGross');
  const [daily, setDaily] = draft.field('daily');
  const [evaluation, setEvaluation] = draft.field('evaluation');
  const [trainPct, setTrainPct] = draft.field('trainPct');
  const [embargo, setEmbargo] = draft.field('embargo');
  const [tab, setTab] = useState('overview');
  type Submission = {
    request: RequestContext;
    value: PortfolioDraft;
    packages: DataPackage[];
    savedVersion?: PortfolioVersion;
  };
  const create = useMutation({
    mutationFn: async (submitted: Submission) => {
      const v = submitted.value;
      const selected = v.legs.map((leg) =>
        submitted.packages.find(
          (p) => p.id === leg.package_id && p.ready && p.source === submitted.request.source,
        ),
      );
      if (selected.some((p) => !p))
        throw new Error(t('Select a ready package for every portfolio leg.'));
      const definition: PortfolioDefinition = {
        bar: selected[0]!.bar,
        mode: v.mode,
        capital_pct: v.capitalPct,
        rebalance_bars: v.rebalance,
        lookback: v.lookback,
        top_k: v.topK,
        carry_threshold: v.carryThreshold,
        ...v.carryConfig,
        ...v.riskConfig,
        max_residual_pct: v.residualPct,
        failure_policy: 'reduce_group',
        execution_contract: v.executionContract,
        legs: v.legs.map((leg, i) => ({
          inst_id: selected[i]!.inst_id,
          weight: leg.weight,
          leverage: leg.leverage,
          direction: leg.direction,
          strategy: leg.strategy,
        })),
      };
      let version: PortfolioVersion;
      if (v.projectId)
        version = await proApi.createPortfolioVersion(v.projectId, {
          hypothesis: v.hypothesis,
          definition,
          ...(v.parentId ? { parent_id: v.parentId } : {}),
        });
      else {
        const saved = await proApi.createPortfolioProject({
          name: v.name,
          hypothesis: v.hypothesis,
          definition,
        });
        qc.setQueryData(['portfolio-project', saved.id], saved);
        version = saved.version!;
      }
      submitted.savedVersion = version;
      qc.setQueryData(['portfolio-version', version.id], version);
      void qc.invalidateQueries({ queryKey: ['portfolio-projects'] });
      const item = await proApi.createPortfolioRun({
        name: v.name,
        hypothesis: v.hypothesis,
        mode: v.mode,
        legs: v.legs.map((leg) => ({
          package_id: leg.package_id,
          weight: leg.weight,
          leverage: leg.leverage,
          direction: leg.direction,
          strategy: leg.strategy,
          ...(leg.rule_events ? { rule_events: leg.rule_events } : {}),
          lifecycle_events:
            v.universeMode === 'historical_lifecycle' ? (leg.lifecycle_events ?? []) : [],
        })),
        universe_mode: v.universeMode,
        lifecycle_warmup_bars: v.lifecycleWarmup,
        capital_pct: v.capitalPct,
        portfolio_version_id: version.id,
        initial_cash: v.cash,
        fee_bps: v.fee,
        slippage_bps: v.slip,
        rebalance_bars: v.rebalance,
        lookback: v.lookback,
        top_k: v.topK,
        carry_threshold: v.carryThreshold,
        ...v.carryConfig,
        ...v.riskConfig,
        failure_policy: 'reduce_group',
        max_residual_pct: v.residualPct,
        execution_contract: v.executionContract,
        max_gross_pct: v.gross,
        max_order_notional: v.maxOrder,
        max_base_asset_gross_pct: v.maxBaseGross,
        max_daily_loss_pct: v.daily,
        evaluation: v.evaluation,
        train_pct: v.trainPct,
        embargo_bars: v.embargo,
      });
      return item;
    },
    onSuccess: (item, submitted) => {
      rememberRun(item, submitted.request);
      if (!currentRequest(submitted.request)) return;
      setProjectId(submitted.savedVersion!.project_id);
      setParentId(submitted.savedVersion!.id);
      selectRun(item.id);
      setEditing(false);
      setSubmittedRun(undefined);
    },
    onError: (_error, submitted) => {
      // The immutable save remains a fact even if queuing the trial fails.
      // Adopt its lineage only when this exact draft is still current.
      if (submitted.savedVersion && currentRequest(submitted.request)) {
        setProjectId(submitted.savedVersion.project_id);
        setParentId(submitted.savedVersion.id);
      }
    },
  });
  const replay = useMutation({
    mutationFn: (request: RequestContext) => proApi.replayPortfolio(request.id),
    onSuccess: (item, request) => {
      rememberRun(item, request);
      void qc.invalidateQueries({ queryKey: ['portfolio-governance'] });
      if (currentRequest(request)) {
        selectRun(item.id);
        setSubmittedRun(undefined);
      }
    },
  });
  const loadProject = useMutation({
    mutationFn: async ({ id, request }: { id: string; request: RequestContext }) => {
      const project = await proApi.portfolioProject(id);
      qc.setQueryData(['portfolio-project', project.id], project);
      if (!currentRequest(request)) return;
      const version = project.versions![0];
      const d = version.definition;
      setProjectId(project.id);
      setParentId(version.id);
      setName(project.name);
      setHypothesis(version.hypothesis);
      setMode(d.mode);
      setUniverseMode('static');
      setLifecycleWarmup(2);
      setCapitalPct(d.capital_pct);
      loadExecution(d);
      setCarryThreshold(d.carry_threshold);
      loadCarry(d);
      loadRisk(d);
      setRebalance(d.rebalance_bars);
      setLookback(d.lookback);
      setTopK(d.top_k);
      const ready =
        packages.data?.items.filter((p) => p.ready && p.source === source && p.bar === d.bar) ?? [];
      const first = ready.find((p) => p.inst_id === d.legs[0].inst_id);
      setLegs(
        d.legs.map((leg) => ({
          ...leg,
          programDraft: undefined,
          lifecycle_events: [],
          package_id:
            ready.find(
              (p) => p.inst_id === leg.inst_id && p.start === first?.start && p.end === first?.end,
            )?.id ?? '',
        })),
      );
    },
  });
  const revise = useMutation({
    mutationFn: async (request: RequestContext) => {
      const config = run.data!.config;
      const versionId = config.portfolio_version_id as string | undefined;
      const version = versionId ? await proApi.portfolioVersion(versionId) : undefined;
      if (version) qc.setQueryData(['portfolio-version', version.id], version);
      if (!currentRequest(request)) return;
      if (version) {
        setProjectId(version.project_id);
        setParentId(version.id);
        loadExecution(version.definition);
      } else {
        setProjectId('');
        setParentId('');
        loadExecution(config as Partial<PortfolioDefinition>);
      }
      setName(String(config.name));
      setHypothesis(String(config.hypothesis));
      setMode(String(config.mode));
      setUniverseMode(String(config.universe_mode ?? 'static'));
      setLifecycleWarmup(Number(config.lifecycle_warmup_bars ?? 2));
      setLegs(
        (config.legs as Leg[]).map((leg) => ({
          ...leg,
          programDraft: undefined,
          inst_id:
            packages.data?.items.find((p) => p.id === leg.package_id)?.inst_id ?? leg.inst_id ?? '',
        })),
      );
      setCapitalPct(String(config.capital_pct ?? 100));
      setCarryThreshold(String(config.carry_threshold));
      loadCarry(config as Partial<PortfolioDefinition>);
      loadRisk(config as Partial<PortfolioDefinition>);
      setCash(String(config.initial_cash));
      setFee(String(config.fee_bps));
      setSlip(String(config.slippage_bps));
      setRebalance(Number(config.rebalance_bars));
      setLookback(Number(config.lookback));
      setTopK(Number(config.top_k));
      setGross(Number(config.max_gross_pct));
      setMaxOrder(String(config.max_order_notional ?? '2500'));
      setMaxBaseGross(String(config.max_base_asset_gross_pct ?? '100'));
      setDaily(Number(config.max_daily_loss_pct));
      setEvaluation(String(config.evaluation));
      setTrainPct(Number(config.train_pct));
      setEmbargo(Number(config.embargo_bars));
      create.reset();
      openDraft();
    },
  });
  const patch = (i: number, value: Partial<Leg>) =>
    setLegs((old) => old.map((leg, index) => (index === i ? { ...leg, ...value } : leg)));
  const ready = packages.data?.items.filter((p) => p.ready && p.source === source) ?? [];
  const first = ready.find((p) => p.id === legs[0].package_id);
  const choices = ready.filter(
    (p) =>
      !first ||
      (p.bar === first.bar &&
        (universeMode === 'historical_lifecycle' ||
          (p.start === first.start && p.end === first.end))),
  );
  useEffect(() => {
    if (!initialInputs || inputHandoffKey(initialInputs) === lastInputsKey || !packages.data)
      return;
    const packageItem = ready.find((p) => p.id === initialInputs.package_id);
    if (!packageItem) {
      setHandoffError(
        text(
          'The returned data must be a ready package from this data source. Prepare a research package to continue.',
          '返回数据必须是当前来源的已就绪研究包，请先准备研究包。',
        ),
      );
      return;
    }
    const matching = legs.findIndex(
      (leg) =>
        (leg.inst_id || ready.find((p) => p.id === leg.package_id)?.inst_id) ===
        packageItem.inst_id,
    );
    const empty = legs.findIndex(
      (leg) => !leg.package_id && (!leg.inst_id || leg.inst_id === packageItem.inst_id),
    );
    const target =
      matching >= 0
        ? matching
        : dataLeg >= 0 &&
            dataLeg < legs.length &&
            (!legs[dataLeg].inst_id || legs[dataLeg].inst_id === packageItem.inst_id)
          ? dataLeg
          : empty;
    if (target < 0) {
      setHandoffError(
        text(
          'This package does not match an existing or empty portfolio leg. Add a leg or select its package explicitly.',
          '该研究包与已有或空组合腿不匹配，请新增组合腿或明确选择研究包。',
        ),
      );
      return;
    }
    const anchor = legs.find((leg, index) => index !== target && leg.package_id);
    const aligned = ready.find((p) => p.id === anchor?.package_id);
    if (
      aligned &&
      (aligned.bar !== packageItem.bar ||
        (universeMode === 'static' &&
          (aligned.start !== packageItem.start || aligned.end !== packageItem.end)))
    ) {
      setHandoffError(
        text(
          'The returned package does not share the interval and UTC window of the other legs. Prepare aligned data before running.',
          '返回研究包的周期或 UTC 时间窗口与其他组合腿不一致，请准备对齐数据。',
        ),
      );
      return;
    }
    setLegs((old) =>
      old.map((leg, index) =>
        index === target
          ? {
              ...leg,
              inst_id: packageItem.inst_id,
              package_id: packageItem.id,
              lifecycle_events: [],
            }
          : leg,
      ),
    );
    setEditing(true);
    setDataLeg(-1);
    setLastInputsKey(inputHandoffKey(initialInputs));
    setHandoffError('');
  }, [initialInputs, packages.data, legs, universeMode, dataLeg, lastInputsKey]);
  const prepareData = () => {
    const target = legs.findIndex((leg) => !leg.package_id);
    draft.flush({ dataLeg: target });
    onData();
  };
  const plan = run.data?.result;
  const economicIncomplete = plan?.economic_state === 'incomplete_lifecycle';
  const executionFailed =
    economicIncomplete || ['failed', 'compensating'].includes(String(plan?.execution_status));
  const executionBound = ['reduce_group_v1', 'reduce_group_v2_allowance'].includes(
    String(plan?.execution_contract),
  );
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
        <button className="button button-secondary" onClick={prepareData}>
          {t('Prepare data')}
        </button>
        <button
          className="button button-citrus"
          disabled={!canOperate}
          onClick={() => {
            create.reset();
            draft.clear();
            openDraft();
            setLastInputsKey(inputHandoffKey(initialInputs));
            setHandoffError('');
            onClearDraft?.();
          }}
        >
          <Plus size={14} />
          {t('New portfolio study')}
        </button>
      </PageHeading>
      <div className="prepared-input-note" role="status">
        <span>
          {draft.restoredAt
            ? `${text('Research draft restored', '已恢复研究草稿')} · ${date(draft.restoredAt, true)}`
            : text(
                'Portfolio configuration is saved in this browser session.',
                '组合研究配置保存在当前浏览器会话中。',
              )}
        </span>
        <button
          className="text-button"
          onClick={() => {
            draft.clear({ lastInputsKey: inputHandoffKey(initialInputs) });
            setHandoffError('');
            create.reset();
            selectRun('');
            onClearDraft?.();
          }}
        >
          {text('Clear research draft', '清除研究草稿')}
        </button>
        {!showEditor && draft.hasDraft && (
          <button className="text-button" onClick={openDraft}>
            {text('Resume portfolio draft', '继续组合草稿')}
          </button>
        )}
      </div>
      {draft.problem && (
        <p role="alert" className="inline-warning">
          {draft.problem === 'invalid'
            ? text(
                'A damaged research draft was discarded. Saved research evidence is unchanged.',
                '已丢弃损坏的研究草稿，已保存研究证据不受影响。',
              )
            : text(
                'The research draft could not be saved. Keep this page open before leaving.',
                '研究草稿无法保存，请保留当前页面。',
              )}
        </p>
      )}
      {handoffError && <ErrorBox error={new Error(handoffError)} />}
      {submittedRun?.source === source &&
        submittedRun.userId === userId &&
        submittedRun.id !== id && (
          <div className="action-note" role="status">
            <span>
              {text(
                'Earlier portfolio research was submitted. Your current draft is unchanged.',
                '之前的组合研究已提交，当前草稿保持不变。',
              )}
            </span>{' '}
            <button className="text-button" onClick={() => selectRun(submittedRun.id)}>
              {text('View submitted research', '查看已提交研究')}
            </button>
          </div>
        )}
      {showEditor ? (
        <section className="pro-panel portfolio-study-editor">
          <form
            onChangeCapture={() => {
              revisionGeneration.current += 1;
            }}
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate({
                request: beginRequest('create'),
                value: structuredClone(draft.value),
                packages: structuredClone(packages.data?.items ?? []),
              });
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
                    setUniverseMode('static');
                    setLifecycleWarmup(2);
                    setCapitalPct(d.capital_pct);
                    loadExecution(d, false);
                    setCarryThreshold(d.carry_threshold);
                    loadCarry(d);
                    loadRisk(d);
                    setRebalance(d.rebalance_bars);
                    setLookback(d.lookback);
                    setTopK(d.top_k);
                    setEvaluation('train_test');
                    const matching = ready.filter((p) => p.bar === d.bar);
                    const anchor = matching.find((p) => p.inst_id === d.legs[0].inst_id);
                    setLegs(
                      d.legs.map((leg) => ({
                        inst_id: leg.inst_id,
                        programDraft: undefined,
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
                  disabled={loadProject.isPending && currentTask(loadProject.variables?.request)}
                  onChange={(e) =>
                    e.target.value
                      ? loadProject.mutate({ id: e.target.value, request: beginRequest('load') })
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
                  <option value="risk_momentum">{t('Risk-budgeted momentum')}</option>
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
            <LifecycleScenario
              mode={universeMode}
              warmup={lifecycleWarmup}
              onMode={setUniverseMode}
              onWarmup={setLifecycleWarmup}
            />
            <p className="quiet-copy">
              {universeMode === 'static' &&
                t(
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
                      onChange={(e) =>
                        patch(i, {
                          package_id: e.target.value,
                          inst_id: ready.find((p) => p.id === e.target.value)?.inst_id ?? '',
                          lifecycle_events: [],
                        })
                      }
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
                {universeMode === 'historical_lifecycle' && (
                  <LifecycleImport
                    symbol={ready.find((p) => p.id === leg.package_id)?.inst_id ?? ''}
                    events={leg.lifecycle_events ?? []}
                    onChange={(events) => patch(i, { lifecycle_events: events })}
                  />
                )}
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
                      programDraft={leg.programDraft}
                      onProgramDraftChange={(programDraft) => patch(i, { programDraft })}
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
              <div className="portfolio-execution-controls">
                <Field label="Execution policy">
                  <select
                    value={executionContract}
                    onChange={(e) =>
                      setExecutionContract(
                        e.target.value as NonNullable<PortfolioDefinition['execution_contract']>,
                      )
                    }
                  >
                    <option value="reduce_group_v2_allowance">
                      {t(executionPolicyName('reduce_group_v2_allowance'))}
                    </option>
                    <option value="reduce_group_v1">
                      {t(executionPolicyName('reduce_group_v1'))}
                    </option>
                  </select>
                </Field>
                <Field
                  label="Maximum execution residual %"
                  hint="Deviation beyond this allocated-capital limit triggers group reduction; recovery fills can also fail."
                >
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
                <PortfolioExecutionPolicy contract={executionContract} residual={residualPct} />
              </div>
              {mode === 'funding_carry' && (
                <>
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
                  {(
                    [
                      ['carry_window', 'Prior settlements', 1, 30],
                      ['carry_cost_settlements', 'Projected settlement count', 0, 300],
                      ['carry_max_age_hours', 'Maximum funding age (hours)', 0, 168],
                      ['carry_buffer_bps', 'Additional carry hurdle (bps)', 0, 1000],
                    ] as const
                  ).map(([key, label, min, max]) => (
                    <Field key={key} label={label}>
                      <input
                        required
                        type="number"
                        min={min}
                        max={max}
                        step={key === 'carry_buffer_bps' ? 'any' : 1}
                        value={carryConfig[key]}
                        onChange={(e) =>
                          setCarryConfig((old) => ({
                            ...old,
                            [key]:
                              key === 'carry_buffer_bps' ? e.target.value : Number(e.target.value),
                          }))
                        }
                      />
                    </Field>
                  ))}
                  <p className="quiet-copy">
                    {t('Four-fill entry/exit cost hurdle')}:{' '}
                    {(
                      4 * (Number(fee) + Number(slip)) +
                      Number(carryConfig.carry_buffer_bps)
                    ).toFixed(2)}{' '}
                    bps.{' '}
                    {t(
                      'Projection is per settlement, not APR. Zero projected settlements retains the legacy rate-only gate; zero maximum age disables freshness checks.',
                    )}
                  </p>
                </>
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
              <Field
                label="Maximum order notional (USDT)"
                hint="A leg is split into at most twenty lot-aligned child orders under this cap. Unexecutable plans enter the shared failure policy."
              >
                <input
                  required
                  type="number"
                  min={0.01}
                  max={1000000000}
                  step="any"
                  value={maxOrder}
                  onChange={(e) => setMaxOrder(e.target.value)}
                />
              </Field>
              <Field
                label="Underlying asset gross limit (%)"
                hint="Combine absolute spot and perpetual exposure to the same underlying without netting directions."
              >
                <input
                  required
                  type="number"
                  min={1}
                  max={1000}
                  step="any"
                  value={maxBaseGross}
                  onChange={(e) => setMaxBaseGross(e.target.value)}
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
              {['momentum', 'risk_momentum'].includes(mode) && (
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
              {t('Shared failure policy')}: <strong>{t('Reduce the group on failure')}</strong>.{' '}
              {t(
                'Research and managed paper execution use the same sequential reduction, addition and compensation policy. A completed research job can still contain a halted simulation.',
              )}
            </p>
            {legacyUpgrade && (
              <p className="inline-warning">
                {t(
                  'This saved evidence has no bound execution policy. Saving this revision explicitly adopts your selected policy; the old version and result stay unchanged.',
                )}
              </p>
            )}
            {mode === 'risk_momentum' && (
              <section className="portfolio-risk-controls">
                <h3>{t('Portfolio risk budget')}</h3>
                <p className="quiet-copy">
                  {t(
                    'Positive momentum selects markets. Inverse volatility sets weights within each leg ceiling; excess stays in cash. The larger of shrunk-covariance risk and a correlation stress sets a common downward scale. Targets refer to allocated capital, before execution.',
                  )}
                </p>
                <div className="form-grid">
                  <Field label="Risk estimation bars">
                    <input
                      type="number"
                      required
                      min={10}
                      max={400}
                      value={riskConfig.risk_window}
                      onChange={(e) =>
                        setRiskConfig({ ...riskConfig, risk_window: Number(e.target.value) })
                      }
                    />
                  </Field>
                  {(
                    [
                      ['vol_target_pct', 'Sleeve volatility target (%)', 0.01, 100],
                      ['vol_floor_pct', 'Asset volatility floor (%)', 0.01, 200],
                      ['covariance_shrinkage', 'Diagonal shrinkage (0–1)', 0, 1],
                      ['correlation_stress', 'Stress correlation (0–1)', 0, 1],
                    ] as const
                  ).map(([key, label, min, max]) => (
                    <Field key={key} label={label}>
                      <input
                        type="number"
                        required
                        min={min}
                        max={max}
                        step="any"
                        value={riskConfig[key]}
                        onChange={(e) => setRiskConfig({ ...riskConfig, [key]: e.target.value })}
                      />
                    </Field>
                  ))}
                </div>
                <p className="quiet-copy">
                  {t(
                    'Long-only, leverage one. Leg weights are ceilings in [0,1]. This is inverse-volatility allocation, not an equal-risk optimizer. Scheduled rebalances, price gaps, residuals and estimation error can exceed the risk target.',
                  )}
                </p>
              </section>
            )}
            <p className="quiet-copy">
              {universeMode === 'static' &&
                t(
                  'This form captures current instrument rules. Attributed point-in-time rule events are supported through the API. The chosen universe is explicit; no historical listing coverage is inferred. Multi-leg fills are sequential, with residuals and rejections reported.',
                )}
            </p>
            {loadProject.isError && currentTask(loadProject.variables?.request) && (
              <ErrorBox error={loadProject.error} />
            )}
            <p className="quiet-copy">
              {t(
                'Running research first saves an immutable portfolio version. Changes create a revision; identical definitions reuse the saved version.',
              )}
            </p>
            {create.isError && currentTask(create.variables?.request) && (
              <ErrorBox error={create.error} />
            )}
            <button
              className="button button-citrus"
              disabled={
                !canOperate ||
                (create.isPending && currentTask(create.variables?.request)) ||
                legs.some(
                  (l) =>
                    !l.package_id || (l.strategy.kind === 'program' && !l.strategy.rules?.length),
                )
              }
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
                  onClick={() => selectRun(r.id)}
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
                        ? executionFailed
                          ? 'warning'
                          : 'good'
                        : run.data?.status === 'failed'
                          ? 'bad'
                          : 'neutral'
                    }
                  >
                    {t(run.data?.status ?? '')}
                  </Status>
                </div>
                {plan && (executionFailed || !executionBound) && (
                  <section
                    className="portfolio-execution-outcome"
                    aria-label={t('Simulation execution state')}
                  >
                    <Status type={executionFailed ? 'bad' : 'warning'}>
                      {economicIncomplete
                        ? text('Incomplete economic result', '经济结果未完成')
                        : t(
                            executionFailed
                              ? String(plan.execution_status)
                              : 'Legacy execution semantics',
                          )}
                    </Status>
                    <p>
                      <strong>
                        {t(
                          executionFailed
                            ? 'This simulation is not eligible for paper deployment.'
                            : 'Revise this legacy study under the shared execution policy.',
                        )}
                      </strong>
                    </p>
                    <p className="quiet-copy">
                      {economicIncomplete
                        ? text(
                            'Lifecycle evidence could not value or settle retained inventory. Final equity is unavailable; inspect eligibility, accounting events and unresolved holdings below.',
                            '生命周期证据无法为保留持仓提供估值或结算，最终权益不可核定。请查看下方市场资格、核算事件和未解决持仓。',
                          )
                        : t(
                            executionFailed
                              ? 'The research computation completed, but a leg failure halted the simulated group or left compensation incomplete. Marked returns include any retained inventory; inspect the execution journal and residuals.'
                              : 'Its saved results remain available for audit. A new immutable revision is required to bind the shared failure and residual policy.',
                          )}
                    </p>
                  </section>
                )}
                <p className="strategy-hypothesis">{String(run.data?.config.hypothesis)}</p>
                <div className="portfolio-release-actions">
                  {run.data && ['queued', 'running'].includes(run.data.status) && (
                    <button
                      className="button button-secondary"
                      disabled={
                        !canOperate ||
                        (cancel.isPending && cancel.variables === id) ||
                        cancellationRequested
                      }
                      onClick={() => id && cancel.mutate(id)}
                    >
                      <Square size={14} />
                      {text(
                        cancellationRequested
                          ? 'Cancellation requested'
                          : 'Cancel portfolio research',
                        cancellationRequested ? '已请求取消' : '取消组合研究',
                      )}
                    </button>
                  )}
                  <button
                    className="button button-secondary"
                    disabled={!canOperate || (revise.isPending && currentTask(revise.variables))}
                    onClick={() => revise.mutate(beginRequest('revise'))}
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
                {revise.isError && currentTask(revise.variables) && (
                  <ErrorBox error={revise.error} />
                )}
                {cancel.isError && cancel.variables === id && <ErrorBox error={cancel.error} />}
                <ResearchCancellationStatus
                  requested={cancellationRequested}
                  recovery={(run.data?.manifest?.cancellation as RecordData | undefined)?.recovery}
                  disabled={!canOperate || (cancel.isPending && cancel.variables === id)}
                  onRetry={() => id && cancel.mutate(id)}
                />
                {run.data?.status === 'cancelled' && (
                  <div className="action-note" role="status">
                    {text(
                      'The worker has stopped this run. Its configuration and trial record remain saved; revise and research again to create a new trial.',
                      'Worker 已停止此任务，配置和试验记录仍保留。修改并重新研究会创建新的试验。',
                    )}
                  </div>
                )}
                {replay.isError && currentTask(replay.variables) && (
                  <ErrorBox error={replay.error} />
                )}
                {run.data?.status === 'completed' && !!run.data.manifest.input_artifact && (
                  <div className="toolbar">
                    <button
                      className="text-button"
                      disabled={!canOperate || (replay.isPending && currentTask(replay.variables))}
                      onClick={() => replay.mutate(beginRequest('replay'))}
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
                                  execution_status: 'Execution state',
                                } as Record<string, string>
                              )[String(r.metric)] ?? String(r.metric),
                            ),
                        },
                        {
                          key: 'actual',
                          label: 'Actual',
                          render: (r) => (
                            <span title={String(r.actual)}>
                              {['execution_status', 'lifecycle_economics'].includes(
                                String(r.metric),
                              )
                                ? t(String(r.actual))
                                : number(r.actual, 4)}
                            </span>
                          ),
                        },
                        {
                          key: 'threshold',
                          label: 'Threshold',
                          render: (r) => (
                            <span title={String(r.threshold)}>
                              {['execution_status', 'lifecycle_economics'].includes(
                                String(r.metric),
                              )
                                ? t(String(r.threshold))
                                : number(r.threshold, 4)}
                            </span>
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
                    bound={!!run.data.config.portfolio_version_id && executionBound}
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
                    <LifecycleEvidence
                      evidence={plan.lifecycle as RecordData | undefined}
                      config={run.data?.config}
                    />
                    <PortfolioExecutionPolicy
                      contract={plan.execution_contract as string | undefined}
                      residual={plan.max_residual_pct}
                    />
                    <PortfolioResearchSummary metrics={plan.metrics} />
                    <PortfolioEconomicsEvidence
                      evidence={plan.economics as RecordData | undefined}
                    />
                    <PortfolioRiskEvidence decisions={plan.decisions ?? []} />
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
