import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowDownToLine,
  Check,
  FlaskConical,
  GitCompareArrows,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from 'lucide-react';
import { defaultStrategy, downloadCsv } from '../api';
import type { Source } from '../api';
import { useSession } from '../components/AuthGate';
import { canResearch } from '../lib/permissions';
import { proApi } from '../proApi';
import type {
  Direction,
  ProResult,
  ProRun,
  ProRunConfig,
  RecordData,
  ResearchInputs,
  ResearchMode,
  StrategyVersion,
} from '../proApi';
import { useI18n } from '../lib/i18n';
import {
  inputHandoffKey,
  isDraftRecord,
  matchesDraftShape,
  normalizeResearchInputs,
  useResearchDraft,
} from '../lib/researchDraft';
import { date, nameOf, number, percent, price, quantityText } from '../lib/format';
import {
  ActionNote,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Metric,
  PageHeading,
  Status,
  StrategyFields,
} from '../components/workspace';
import {
  DataTable,
  JsonDetails,
  RecordGrid,
  WorkspaceTabs,
  valueText,
} from '../components/ProWorkspace';
import ResearchChart from '../components/ResearchChart';
import ResearchRelease from '../components/ResearchRelease';
import ResearchCancellationStatus from '../components/ResearchCancellationStatus';

const arrayRecords = (v: unknown): RecordData[] =>
  Array.isArray(v) ? (v.filter((x) => x !== null && typeof x === 'object') as RecordData[]) : [];
const utcInput = (ts: unknown) =>
  typeof ts === 'number' && Number.isFinite(ts) ? new Date(ts).toISOString().slice(0, 16) : '';
const candidates = (s: string) =>
  s
    .split(',')
    .map((x) => Number(x.trim()))
    .filter(Number.isFinite);
const modeNames: Record<ResearchMode, string> = {
  single: 'Single run',
  train_test: 'Train / test',
  walk_forward: 'Walk-forward',
  grid: 'Parameter grid',
  cost_stress: 'Cost stress',
};
const newAdvancedDraft = () => ({
  datasetId: '',
  windowStart: '',
  windowEnd: '',
  markId: '',
  fundingId: '',
  preparedInputs: undefined as ResearchInputs | undefined,
  strategy: { ...defaultStrategy },
  programDraft: undefined as string | undefined,
  versionId: undefined as string | undefined,
  direction: 'long_only' as Direction,
  leverage: 1,
  capital: '10000',
  fee: '10',
  slippage: '5',
  liqFee: '50',
  mode: 'single' as ResearchMode,
  selectOnTraining: false,
  trainFraction: 0.7,
  trainBars: 240,
  testBars: 120,
  stepBars: 120,
  purgeBars: 26,
  fastGrid: '8,12,20',
  slowGrid: '26,50,100',
  rsiGrid: '7,14,21',
  entryGrid: '25,30',
  exitGrid: '65,70',
  allocationGrid: '0.25,0.5,0.75',
  feeGrid: '5,10,20',
  slipGrid: '0,5,15',
  lastInputsKey: '',
  lastVersionId: '',
});
type AdvancedDraft = ReturnType<typeof newAdvancedDraft>;
const validAdvancedDraft = (value: unknown, source: Source): value is AdvancedDraft =>
  matchesDraftShape(value, newAdvancedDraft()) &&
  isDraftRecord(value) &&
  Object.keys(value).every((key) => Object.hasOwn(newAdvancedDraft(), key)) &&
  (value.versionId === undefined || typeof value.versionId === 'string') &&
  (value.programDraft === undefined || typeof value.programDraft === 'string') &&
  ['long_only', 'short_only', 'long_short'].includes(String(value.direction)) &&
  Object.hasOwn(modeNames, String(value.mode)) &&
  (value.preparedInputs === undefined ||
    normalizeResearchInputs(value.preparedInputs, source) !== undefined);
function AutoTable({ rows }: { rows: RecordData[] }) {
  return (
    <DataTable<RecordData>
      rows={rows}
      columns={Object.keys(rows[0] ?? {})
        .filter((k) => typeof rows[0][k] !== 'object' || rows[0][k] === null)
        .slice(0, 10)
        .map((key) => ({
          key,
          label: key.replaceAll('_', ' ').toUpperCase(),
          render: (r) => (key === 'ts' ? date(Number(r[key])) : valueText(r[key])),
        }))}
    />
  );
}
export default function ProResearch({
  source,
  initialInputs,
  initialRunId,
  initialStrategyVersion,
  onClearStrategyVersion,
  onOpenData,
  onOpenExecution,
  onRunSelect,
  onClearDraft,
}: {
  source: Source;
  initialInputs?: ResearchInputs;
  initialRunId?: string;
  initialStrategyVersion?: StrategyVersion;
  onClearStrategyVersion: () => void;
  onOpenData: () => void;
  onOpenExecution: (id?: string) => void;
  onRunSelect?: (id: string) => void;
  onClearDraft?: () => void;
}) {
  const { t } = useI18n();
  const session = useSession();
  const canOperate = canResearch(session?.user?.role);
  const userId = session?.user?.id ?? session?.user?.username ?? 'service';
  const draft = useResearchDraft(
    'advanced',
    userId,
    source,
    newAdvancedDraft,
    (value): value is AdvancedDraft => validAdvancedDraft(value, source),
  );
  const latestDraft = useRef({ source, userId, value: draft.value });
  latestDraft.current = { source, userId, value: draft.value };
  const editorGeneration = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      editorGeneration.current += 1;
    };
  }, []);
  const [submittedRun, setSubmittedRun] = useState<{
    source: Source;
    userId: string;
    id: string;
  }>();
  const [lastInputsKey, setLastInputsKey] = draft.field('lastInputsKey');
  const [lastVersionId, setLastVersionId] = draft.field('lastVersionId');
  const qc = useQueryClient();
  const [datasetId, setDatasetId] = draft.field('datasetId');
  const [windowStart, setWindowStart] = draft.field('windowStart');
  const [windowEnd, setWindowEnd] = draft.field('windowEnd');
  const [validation, setValidation] = useState<string | null>(null);
  const [markId, setMarkId] = draft.field('markId');
  const [fundingId, setFundingId] = draft.field('fundingId');
  const [preparedInputs, setPreparedInputs] = draft.field('preparedInputs');
  const [strategy, setStrategy] = draft.field('strategy');
  const [programDraft, setProgramDraft] = draft.field('programDraft');
  const [versionId, setVersionId] = draft.field('versionId');
  useEffect(() => {
    if (initialStrategyVersion && initialStrategyVersion.id !== lastVersionId) {
      setVersionId(initialStrategyVersion.id);
      setStrategy({ ...initialStrategyVersion.definition.strategy });
      setProgramDraft(undefined);
      setDirection(initialStrategyVersion.definition.direction);
      setLeverage(Number(initialStrategyVersion.definition.leverage));
      setLastVersionId(initialStrategyVersion.id);
    }
  }, [initialStrategyVersion?.id]);
  const [direction, setDirection] = draft.field('direction');
  const [leverage, setLeverage] = draft.field('leverage');
  const [capital, setCapital] = draft.field('capital');
  const [fee, setFee] = draft.field('fee');
  const [slippage, setSlippage] = draft.field('slippage');
  const [liqFee, setLiqFee] = draft.field('liqFee');
  const [mode, setMode] = draft.field('mode');
  const [selectOnTraining, setSelectOnTraining] = draft.field('selectOnTraining');
  const oosMode = mode === 'train_test' || mode === 'walk_forward';
  const showGrid = mode === 'grid' || (oosMode && selectOnTraining);
  const [trainFraction, setTrainFraction] = draft.field('trainFraction');
  const [trainBars, setTrainBars] = draft.field('trainBars');
  const [testBars, setTestBars] = draft.field('testBars');
  const [stepBars, setStepBars] = draft.field('stepBars');
  const [purgeBars, setPurgeBars] = draft.field('purgeBars');
  const [fastGrid, setFastGrid] = draft.field('fastGrid');
  const [slowGrid, setSlowGrid] = draft.field('slowGrid');
  const [rsiGrid, setRsiGrid] = draft.field('rsiGrid');
  const [entryGrid, setEntryGrid] = draft.field('entryGrid');
  const [exitGrid, setExitGrid] = draft.field('exitGrid');
  const [allocationGrid, setAllocationGrid] = draft.field('allocationGrid');
  const [feeGrid, setFeeGrid] = draft.field('feeGrid');
  const [slipGrid, setSlipGrid] = draft.field('slipGrid');
  const [selected, setSelected] = useState(initialRunId ?? '');
  const selectedContext = useRef(initialRunId ?? '');
  const [detailTab, setDetailTab] = useState('fills');
  const [variantKey, setVariantKey] = useState('');
  const [chartMetric, setChartMetric] = useState<'equity' | 'drawdown'>('equity');
  const [checked, setChecked] = useState<string[]>([]);
  const [comparison, setComparison] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [exportError, setExportError] = useState(false);
  const initialSelection = useRef(initialRunId);
  useEffect(() => {
    if (initialSelection.current !== initialRunId) {
      initialSelection.current = initialRunId;
      editorGeneration.current += 1;
      selectedContext.current = initialRunId ?? '';
      setSelected(initialRunId ?? '');
      setVariantKey('');
    }
  }, [initialRunId]);
  const datasets = useQuery({
    queryKey: ['pro-datasets'],
    queryFn: proApi.datasets,
    refetchOnMount: 'always',
  });
  const runs = useInfiniteQuery({
    queryKey: ['pro-runs', source, 'catalog'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => proApi.runs(source, pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 15000,
  });
  const catalog = datasets.data?.items.filter((d) => d.source === source) ?? [];
  const tradeDatasets = catalog.filter((d) => d.kind === 'trade');
  const dataset = tradeDatasets.find((d) => d.id === datasetId);
  const isSwap = !!dataset?.inst_id.endsWith('-SWAP');
  useEffect(() => {
    if (dataset) {
      setWindowStart((v) => v || utcInput(dataset.start));
      setWindowEnd((v) => v || utcInput(dataset.end));
    }
  }, [dataset?.id, dataset?.start, dataset?.end]);
  const visibleRuns =
    runs.data?.pages
      .flatMap((p) => p.items)
      .filter(
        (r) =>
          r.source === source || (!r.source && catalog.some((d) => d.id === r.config.dataset_id)),
      ) ?? [];
  useEffect(() => {
    if (initialInputs && inputHandoffKey(initialInputs) !== lastInputsKey) {
      const inputs = normalizeResearchInputs(initialInputs, source);
      if (!inputs) return;
      setDatasetId(inputs.dataset_id);
      setMarkId(inputs.mark_dataset_id ?? '');
      setFundingId(inputs.funding_dataset_id ?? '');
      setWindowStart(utcInput(inputs.start_ts));
      setWindowEnd(utcInput(inputs.end_ts));
      setPreparedInputs(inputs);
      setLastInputsKey(inputHandoffKey(initialInputs));
    }
  }, [initialInputs, source]);
  const activeId = selected || undefined;
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const previousActiveId = useRef(activeId);
  useEffect(() => {
    const changed = previousActiveId.current !== activeId;
    previousActiveId.current = activeId;
    if (!activeId || !changed) return;
    const frame = requestAnimationFrame(() => resultHeading.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [activeId]);
  const run = useQuery({
    queryKey: ['pro-run', source, activeId, variantKey],
    queryFn: async () => {
      const item = await proApi.run(activeId!, variantKey);
      if (item.source && item.source !== source)
        throw new Error(t('The selected run belongs to another data source.'));
      return item;
    },
    enabled: !!activeId,
    refetchInterval: (q) =>
      q.state.data && ['queued', 'running'].includes(q.state.data.status) ? 1000 : false,
  });
  useEffect(() => {
    if (run.data && ['completed', 'failed', 'cancelled'].includes(run.data.status))
      void qc.invalidateQueries({ queryKey: ['pro-runs'] });
  }, [run.data?.id, run.data?.status, qc]);
  type RequestContext = {
    task: 'create' | 'replay';
    source: Source;
    userId: string;
    id: string;
    generation: number;
    fingerprint: string;
  };
  const beginRequest = (task: RequestContext['task']): RequestContext => {
    editorGeneration.current += 1;
    return {
      task,
      source,
      userId,
      id: selectedContext.current,
      generation: editorGeneration.current,
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
    editorGeneration.current === request.generation &&
    selectedContext.current === request.id;
  const currentRequest = (request?: RequestContext) =>
    currentTask(request) && JSON.stringify(latestDraft.current.value) === request!.fingerprint;
  const created = (r: ProRun, submitted: { request: RequestContext }) => {
    qc.setQueryData(['pro-run', submitted.request.source, r.id], r);
    void qc.invalidateQueries({ queryKey: ['pro-runs'] });
    void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    if (!currentRequest(submitted.request)) {
      if (sameScope(submitted.request))
        setSubmittedRun({
          source: submitted.request.source,
          userId: submitted.request.userId,
          id: r.id,
        });
      return;
    }
    selectRun(r.id);
    setSubmittedRun(undefined);
    setNotice(null);
  };
  const create = useMutation({
    mutationFn: ({ config }: { request: RequestContext; config: ProRunConfig }) =>
      proApi.createRun(config),
    onSuccess: created,
  });
  const replay = useMutation({
    mutationFn: ({ request }: { request: RequestContext }) => proApi.replay(request.id),
    onSuccess: created,
  });
  const cancel = useMutation({
    mutationFn: proApi.cancelRun,
    onSuccess: (item) => {
      qc.setQueriesData({ queryKey: ['pro-run', source, item.id] }, item);
      void qc.invalidateQueries({ queryKey: ['pro-runs'] });
      void qc.invalidateQueries({ queryKey: ['pro-ops'] });
    },
  });
  const cancellationRequested =
    (run.data?.manifest?.cancellation as RecordData | undefined)?.state === 'requested';
  const compare = useQuery({
    queryKey: ['pro-compare', comparison],
    queryFn: () => proApi.compare(comparison),
    enabled: comparison.length >= 2,
  });
  const busy =
    (create.isPending && currentTask(create.variables?.request)) ||
    (replay.isPending && currentTask(replay.variables?.request)) ||
    ['queued', 'running'].includes(run.data?.status ?? '');
  const selectRun = (id: string) => {
    editorGeneration.current += 1;
    selectedContext.current = id;
    setSelected(id);
    setVariantKey('');
    onRunSelect?.(id);
  };
  const selectVariant = (key: string) => {
    editorGeneration.current += 1;
    setVariantKey(key);
  };
  const load = (r: ProRun) => {
    selectRun(r.id);
    const c = r.config;
    setPreparedInputs(
      c.package_id
        ? {
            dataset_id: c.dataset_id,
            mark_dataset_id: c.mark_dataset_id,
            funding_dataset_id: c.funding_dataset_id,
            start_ts: c.start_ts,
            end_ts: c.end_ts,
            package_id: c.package_id,
            package_manifest_hash: c.package_manifest_hash,
          }
        : undefined,
    );
    setDatasetId(c.dataset_id);
    const input = catalog.find((d) => d.id === c.dataset_id);
    setWindowStart(utcInput(c.start_ts ?? input?.start));
    setWindowEnd(utcInput(c.end_ts ?? input?.end));
    setMarkId(c.mark_dataset_id ?? '');
    setFundingId(c.funding_dataset_id ?? '');
    setStrategy({ ...c.strategy });
    setProgramDraft(undefined);
    setVersionId(c.strategy_version_id ?? undefined);
    setDirection(c.direction);
    setLeverage(c.leverage);
    setCapital(c.initial_cash);
    setFee(c.fee_bps);
    setSlippage(c.slippage_bps);
    setLiqFee(c.liquidation_fee_bps);
    setMode(c.mode);
    const o = c.options;
    if (typeof o.train_fraction === 'number') setTrainFraction(o.train_fraction);
    if (typeof o.train_bars === 'number') setTrainBars(o.train_bars);
    if (typeof o.test_bars === 'number') setTestBars(o.test_bars);
    if (typeof o.step_bars === 'number') setStepBars(o.step_bars);
    if (typeof o.purge_bars === 'number') setPurgeBars(o.purge_bars);
    const grid = o.grid as Record<string, number[]> | undefined;
    if (Array.isArray(grid?.fast)) setFastGrid(grid.fast.join(','));
    if (Array.isArray(grid?.slow)) setSlowGrid(grid.slow.join(','));
    if (Array.isArray(grid?.rsi_period)) setRsiGrid(grid.rsi_period.join(','));
    if (Array.isArray(grid?.entry)) setEntryGrid(grid.entry.join(','));
    if (Array.isArray(grid?.exit)) setExitGrid(grid.exit.join(','));
    if (Array.isArray(grid?.allocation)) setAllocationGrid(grid.allocation.join(','));
    setSelectOnTraining(!!grid && (c.mode === 'train_test' || c.mode === 'walk_forward'));
    if (Array.isArray(o.fee_bps)) setFeeGrid(o.fee_bps.join(','));
    if (Array.isArray(o.slippage_bps)) setSlipGrid(o.slippage_bps.join(','));
  };
  const packageAttached =
    !!preparedInputs?.package_id &&
    preparedInputs.dataset_id === datasetId &&
    (preparedInputs.mark_dataset_id ?? '') === markId &&
    (preparedInputs.funding_dataset_id ?? '') === fundingId &&
    preparedInputs.start_ts === Date.parse(`${windowStart}Z`) &&
    preparedInputs.end_ts === Date.parse(`${windowEnd}Z`);
  const boundVersion = useQuery({
    queryKey: ['strategy-version', versionId],
    queryFn: () => proApi.strategyVersion(versionId!),
    enabled: !!versionId,
  });
  const submit = () => {
    const startTs = Date.parse(`${windowStart}Z`),
      endTs = Date.parse(`${windowEnd}Z`);
    const interval = (
      {
        '1m': 60000,
        '5m': 300000,
        '15m': 900000,
        '1H': 3600000,
        '4H': 14400000,
        '1Dutc': 86400000,
      } as Record<string, number>
    )[dataset?.bar ?? ''];
    if (
      !dataset ||
      !Number.isFinite(startTs) ||
      !Number.isFinite(endTs) ||
      startTs >= endTs ||
      startTs < Number(dataset.start) ||
      endTs > Number(dataset.end)
    ) {
      setValidation(t('Research window must be within dataset coverage.'));
      return;
    }
    if (startTs % interval || endTs % interval) {
      setValidation(t('Range boundaries must align to the selected UTC interval.'));
      return;
    }
    if (
      versionId &&
      (!boundVersion.data ||
        boundVersion.data.definition.bar !== dataset.bar ||
        boundVersion.data.definition.product !== (isSwap ? 'SWAP' : 'SPOT'))
    ) {
      setValidation(t('Choose data matching the strategy version product and interval.'));
      return;
    }
    setValidation(null);
    let options: RecordData = {};
    if (mode === 'train_test') options = { train_fraction: trainFraction, purge_bars: purgeBars };
    if (mode === 'walk_forward')
      options = {
        train_bars: trainBars,
        test_bars: testBars,
        step_bars: stepBars,
        purge_bars: purgeBars,
      };
    if (showGrid)
      options = {
        ...options,
        grid:
          strategy.kind === 'sma_cross'
            ? { fast: candidates(fastGrid), slow: candidates(slowGrid) }
            : strategy.kind === 'rsi_reversion'
              ? {
                  rsi_period: candidates(rsiGrid),
                  entry: candidates(entryGrid),
                  exit: candidates(exitGrid),
                }
              : { allocation: candidates(allocationGrid) },
      };
    if (mode === 'cost_stress')
      options = { fee_bps: candidates(feeGrid), slippage_bps: candidates(slipGrid) };
    const matchesPackage =
      preparedInputs?.package_id &&
      preparedInputs.dataset_id === datasetId &&
      (preparedInputs.mark_dataset_id ?? '') === markId &&
      (preparedInputs.funding_dataset_id ?? '') === fundingId &&
      preparedInputs.start_ts === startTs &&
      preparedInputs.end_ts === endTs;
    const config: ProRunConfig = {
      strategy_version_id: versionId,
      ...(matchesPackage
        ? {
            package_id: preparedInputs.package_id,
            package_manifest_hash: preparedInputs.package_manifest_hash,
          }
        : {}),
      dataset_id: datasetId,
      start_ts: startTs,
      end_ts: endTs,
      ...(markId ? { mark_dataset_id: markId } : {}),
      ...(fundingId ? { funding_dataset_id: fundingId } : {}),
      strategy,
      direction: isSwap ? direction : 'long_only',
      initial_cash: capital,
      leverage: isSwap ? leverage : 1,
      fee_bps: fee,
      slippage_bps: slippage,
      liquidation_fee_bps: liqFee,
      mode,
      options,
    };
    create.mutate({ request: beginRequest('create'), config: structuredClone(config) });
  };
  const plan = run.data?.result;
  const experiments = arrayRecords(plan?.experiments);
  const folds = arrayRecords(plan?.folds);
  const variants: { key: string; label: string; scope: string; result: ProResult }[] = [];
  if (plan?.metrics)
    variants.push({ key: 'single', label: t('Single run'), scope: 'single', result: plan });
  if (plan?.result && typeof plan.result === 'object')
    variants.push({
      key: 'single',
      label: t('Single run'),
      scope: 'single',
      result: plan.result as ProResult,
    });
  for (const experiment of experiments)
    if (experiment.result && typeof experiment.result === 'object')
      variants.push({
        key: String(experiment.id),
        label: String(experiment.id),
        scope: run.data?.config.mode === 'grid' ? 'in_sample' : 'cost_sensitivity',
        result: experiment.result as ProResult,
      });
  for (const fold of folds) {
    if (fold.test_result && typeof fold.test_result === 'object')
      variants.push({
        key: `${fold.id}:test`,
        label: `${fold.id} · ${t('Out-of-sample')}`,
        scope: 'out_of_sample',
        result: fold.test_result as ProResult,
      });
    for (const training of arrayRecords(fold.training_experiments))
      if (training.result && typeof training.result === 'object')
        variants.push({
          key: `${fold.id}:${training.id}`,
          label: `${fold.id} · ${t('Training')} · ${training.id}`,
          scope: 'training',
          result: training.result as ProResult,
        });
  }
  const variant = variants.find((v) => v.key === variantKey) ?? variants[0];
  const result = variant?.result;
  const metrics = result?.metrics ?? {};
  const fills = arrayRecords(result?.fills ?? result?.trades);
  const scenarios: RecordData[] = experiments.map((e) => {
    const parameters = e.parameters as RecordData;
    const strategyParameters = parameters?.strategy as RecordData;
    const metrics = (e.result as ProResult)?.metrics;
    return {
      id: e.id,
      fast: strategyParameters?.fast,
      slow: strategyParameters?.slow,
      rsi_period: strategyParameters?.rsi_period,
      entry: strategyParameters?.entry,
      exit: strategyParameters?.exit,
      allocation: strategyParameters?.allocation,
      fee_bps: parameters?.fee_bps,
      slippage_bps: parameters?.slippage_bps,
      ...metrics,
    };
  });
  const foldRows: RecordData[] = folds.map((f) => ({
    ...f,
    ...(f.test_result as ProResult)?.metrics,
  }));
  const equity = arrayRecords(result?.equity);
  const exportRun = async () => {
    if (!activeId) return;
    try {
      await proApi.exportRun(activeId);
      setNotice(t('Download'));
      setExportError(false);
    } catch (error) {
      setNotice((error as Error).message);
      setExportError(true);
    }
  };
  const runHoldoutId =
    run.data && 'holdout_id' in run.data.config && typeof run.data.config.holdout_id === 'string'
      ? run.data.config.holdout_id
      : undefined;
  const results = (
    <div
      key="results"
      className="pro-research-main"
      data-research-region="results"
      role={activeId ? undefined : 'complementary'}
      aria-labelledby={activeId ? 'research-result-heading' : 'research-runs-heading'}
    >
      {activeId && (
        <section className="pro-panel pro-result-panel">
          <div className="section-heading">
            <div>
              <h2 id="research-result-heading" ref={resultHeading} tabIndex={-1}>
                {run.data ? t(nameOf(run.data.config.strategy.kind)) : t('Research result')}
              </h2>
              <p className="section-description">
                {run.data
                  ? `${run.data.id.slice(0, 12)} · ${t(modeNames[run.data.config.mode])} · ${date(run.data.created_at)}`
                  : t('Choose an immutable dataset and submit a research configuration.')}
              </p>
              {(run.data?.config.strategy_version_id || runHoldoutId) && (
                <div className="research-run-identity">
                  {run.data?.config.strategy_version_id && (
                    <p className="section-description research-result-context">
                      <span>{t('Run strategy version')}</span>{' '}
                      <code>{run.data.config.strategy_version_id}</code>
                    </p>
                  )}
                  {runHoldoutId && (
                    <p className="section-description research-result-context">
                      <span>{t('Run holdout')}</span> <code>{runHoldoutId}</code>
                    </p>
                  )}
                </div>
              )}
            </div>
            {run.data && (
              <Status
                type={
                  run.data.status === 'completed'
                    ? 'good'
                    : run.data.status === 'failed'
                      ? 'bad'
                      : 'neutral'
                }
              >
                {run.data.status}
              </Status>
            )}
          </div>
          {run.isError ? (
            <ErrorBox error={run.error} onRetry={() => void run.refetch()} />
          ) : datasets.isPending || runs.isPending || (!!activeId && run.isPending) ? (
            <Loading />
          ) : ['queued', 'running'].includes(run.data?.status ?? '') ? (
            <Loading label="Calculating research scenarios…" />
          ) : run.data?.status === 'failed' ? (
            <ErrorBox error={new Error(run.data.error ?? 'Research failed')} />
          ) : run.data?.status === 'cancelled' ? (
            <Empty title="Research cancelled">
              {t(
                'The worker has stopped this run. Its configuration and trial record remain saved; run research again to create a new trial.',
              )}
            </Empty>
          ) : plan ? (
            <>
              {plan.oos_summary && (
                <div className="oos-summary">
                  <h3>{t('Out-of-sample summary')}</h3>
                  <RecordGrid value={plan.oos_summary as RecordData} />
                  <p>
                    {t(
                      'Each fold starts with an independent account. Returns are not compounded into one equity curve.',
                    )}
                  </p>
                </div>
              )}
              {variants.length > 1 && (
                <div className="research-result-selector">
                  <Field label="Inspect experiment">
                    <select
                      aria-label={t('Inspect experiment')}
                      value={variant?.key ?? ''}
                      onChange={(e) => selectVariant(e.target.value)}
                    >
                      {variants.map((v) => (
                        <option key={v.key} value={v.key}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Status type={variant?.scope === 'out_of_sample' ? 'good' : 'warning'}>
                    {variant?.scope ?? 'single'}
                  </Status>
                </div>
              )}
              <div className="pro-metric-strip">
                <Metric
                  label="Net return"
                  value={percent(metrics.total_return_pct ?? metrics.net_return_pct)}
                />
                <Metric
                  label="Max drawdown"
                  value={
                    metrics.max_drawdown_pct == null
                      ? '—'
                      : percent(-Math.abs(Number(metrics.max_drawdown_pct)))
                  }
                  className="negative"
                />
                <Metric
                  label="Sharpe ratio"
                  value={number(metrics.sharpe)}
                  note={
                    metrics.sharpe != null
                      ? 'UTC daily returns · sqrt(365) annualization'
                      : (metrics.metric_reasons as RecordData | undefined)?.sharpe ===
                          'insufficient_complete_utc_days'
                        ? `${number(metrics.complete_utc_days, 0)} ${t('complete UTC days; 30 required')}`
                        : t('Unavailable: return variation is insufficient.')
                  }
                />
                <Metric label="Final equity" value={number(metrics.final_equity)} />
                <Metric
                  label="Fills"
                  value={valueText(metrics.fills ?? metrics.trades ?? metrics.fill_count)}
                />
                <Metric
                  label="Funding"
                  value={number(metrics.funding_paid ?? metrics.funding_pnl)}
                />
              </div>
              <div className="chart-heading">
                <div className="segmented">
                  <button
                    className={chartMetric === 'equity' ? 'selected' : ''}
                    aria-pressed={chartMetric === 'equity'}
                    onClick={() => setChartMetric('equity')}
                  >
                    {t('Equity curve')} · USDT
                  </button>
                  <button
                    className={chartMetric === 'drawdown' ? 'selected' : ''}
                    aria-pressed={chartMetric === 'drawdown'}
                    onClick={() => setChartMetric('drawdown')}
                  >
                    {t('Drawdown')} · %
                  </button>
                </div>
                <span className="quiet-copy">{t(modeNames[run.data!.config.mode])}</span>
              </div>
              <ResearchChart rows={equity} metric={chartMetric} />
              <WorkspaceTabs
                value={detailTab}
                onChange={setDetailTab}
                items={[
                  { key: 'fills', label: 'Fills' },
                  { key: 'signals', label: 'Signals & orders' },
                  { key: 'trips', label: 'Round trips' },
                  { key: 'folds', label: 'Folds' },
                  { key: 'scenarios', label: 'Scenarios' },
                  { key: 'costs', label: 'Cost attribution' },
                  { key: 'manifest', label: 'Provenance' },
                ]}
              />
              {detailTab === 'fills' && (
                <>
                  <div className="detail-action-bar">
                    <span>
                      {fills.length} {t('Fills')}
                    </span>
                    <button
                      className="text-button"
                      disabled={!fills.length}
                      onClick={() => downloadCsv(fills, `tidebench-${activeId}-fills.csv`)}
                    >
                      <ArrowDownToLine size={13} />
                      {t('Export CSV')}
                    </button>
                  </div>
                  <DataTable
                    rows={fills}
                    columns={[
                      { key: 'ts', label: 'Time', render: (r) => date(Number(r.ts)) },
                      { key: 'side', label: 'Side' },
                      {
                        key: 'quantity',
                        label: 'Quantity',
                        render: (r) => quantityText(r.quantity),
                      },
                      { key: 'price', label: 'Fill price', render: (r) => price(r.price) },
                      { key: 'notional', label: 'Notional', render: (r) => number(r.notional) },
                      { key: 'fee', label: 'Fee', render: (r) => number(r.fee, 4) },
                      { key: 'reason', label: 'Reason' },
                      { key: 'signal_id', label: 'Signal' },
                    ]}
                  />
                  <JsonDetails value={fills} label="Details" />
                </>
              )}
              {detailTab === 'signals' && (
                <>
                  <AutoTable rows={arrayRecords(result?.signals)} />
                  <h3 className="result-subheading">{t('Orders')}</h3>
                  <AutoTable rows={arrayRecords(result?.orders)} />
                </>
              )}
              {detailTab === 'trips' && (
                <>
                  <AutoTable rows={arrayRecords(result?.round_trips)} />
                  <JsonDetails value={result?.round_trips} label="Details" />
                </>
              )}
              {detailTab === 'folds' && (
                <>
                  <DataTable
                    rows={foldRows}
                    columns={[
                      {
                        key: 'id',
                        label: 'Fold',
                        render: (r) => (
                          <button
                            className="text-button"
                            onClick={() => selectVariant(`${r.id}:test`)}
                          >
                            {String(r.id)}
                          </button>
                        ),
                      },
                      {
                        key: 'train_start_ts',
                        label: 'Training start',
                        render: (r) => date(Number(r.train_start_ts)),
                      },
                      {
                        key: 'test_start_ts',
                        label: 'Test start',
                        render: (r) => date(Number(r.test_start_ts)),
                      },
                      {
                        key: 'test_end_ts',
                        label: 'Test end',
                        render: (r) => date(Number(r.test_end_ts)),
                      },
                      {
                        key: 'total_return_pct',
                        label: 'Net return',
                        render: (r) => percent(r.total_return_pct),
                      },
                      {
                        key: 'max_drawdown_pct',
                        label: 'Max drawdown',
                        render: (r) =>
                          r.max_drawdown_pct == null
                            ? '—'
                            : percent(-Math.abs(Number(r.max_drawdown_pct))),
                      },
                      {
                        key: 'selected_strategy',
                        label: 'Selected parameters',
                        render: (r) => (
                          <JsonDetails
                            value={{
                              selected_strategy: r.selected_strategy,
                              training_comparison: r.training_comparison,
                            }}
                          />
                        ),
                      },
                    ]}
                  />
                  <JsonDetails value={plan.oos_summary} label="Out-of-sample summary" />
                </>
              )}
              {detailTab === 'scenarios' && (
                <>
                  <DataTable
                    rows={scenarios}
                    columns={[
                      {
                        key: 'id',
                        label: 'Experiment',
                        render: (r) => (
                          <button
                            className="text-button"
                            onClick={() => selectVariant(String(r.id))}
                          >
                            {String(r.id)}
                          </button>
                        ),
                      },
                      ...(run.data?.config.mode === 'grid' &&
                      run.data.config.strategy.kind === 'sma_cross'
                        ? [
                            { key: 'fast', label: 'Fast window' },
                            { key: 'slow', label: 'Slow window' },
                          ]
                        : []),
                      ...(run.data?.config.mode === 'grid' &&
                      run.data.config.strategy.kind === 'rsi_reversion'
                        ? [
                            { key: 'rsi_period', label: 'RSI period' },
                            { key: 'entry', label: 'Entry below' },
                            { key: 'exit', label: 'Exit above' },
                          ]
                        : []),
                      ...(run.data?.config.mode === 'grid' &&
                      run.data.config.strategy.kind === 'buy_hold'
                        ? [{ key: 'allocation', label: 'Capital allocation' }]
                        : []),
                      { key: 'fee_bps', label: 'Fee' },
                      { key: 'slippage_bps', label: 'Slippage' },
                      {
                        key: 'total_return_pct',
                        label: 'Net return',
                        render: (r) => percent(r.total_return_pct),
                      },
                      {
                        key: 'max_drawdown_pct',
                        label: 'Max drawdown',
                        render: (r) =>
                          r.max_drawdown_pct == null
                            ? '—'
                            : percent(-Math.abs(Number(r.max_drawdown_pct))),
                      },
                      { key: 'sharpe', label: 'Sharpe ratio', render: (r) => number(r.sharpe) },
                    ]}
                  />
                  <JsonDetails value={scenarios} label="Details" />
                </>
              )}
              {detailTab === 'costs' && (
                <>
                  <RecordGrid
                    value={
                      result?.attribution ??
                      result?.costs ?? {
                        fees_paid: metrics.fees_paid,
                        funding_paid: metrics.funding_paid,
                        realized_pnl: metrics.realized_pnl,
                        insurance_shortfall: metrics.insurance_shortfall,
                        turnover: metrics.turnover,
                        exposure_pct: metrics.exposure_pct,
                      }
                    }
                  />
                  <h3 className="result-subheading">{t('Funding')}</h3>
                  <AutoTable rows={arrayRecords(result?.funding)} />
                  <JsonDetails value={metrics} label="Metrics" />
                  <JsonDetails value={result} label="Details" />
                </>
              )}
              {detailTab === 'manifest' && (
                <>
                  <JsonDetails
                    value={run.data?.manifest ?? result?.provenance}
                    label="Manifest"
                    open
                  />
                  <JsonDetails value={result?.assumptions} label="Assumptions" open />
                </>
              )}
            </>
          ) : (
            <div className="research-onboarding">
              <Empty title="No result selected" icon={FlaskConical}>
                {t('Choose a saved run to review, or configure and run a new study.')}
              </Empty>
            </div>
          )}
        </section>
      )}
      <section className="pro-panel run-catalog-panel">
        <div className="section-heading">
          <h2 id="research-runs-heading">{t('Research runs')}</h2>
          <button
            className="button button-small button-secondary"
            disabled={checked.length < 2}
            onClick={() => setComparison([...checked])}
          >
            <GitCompareArrows size={13} />
            {t('Compare selected')} ({checked.length})
          </button>
        </div>
        {runs.isPending ? (
          <Loading />
        ) : runs.isError ? (
          <ErrorBox error={runs.error} onRetry={() => void runs.refetch()} />
        ) : !activeId ? (
          visibleRuns.length ? (
            <div className="research-run-list">
              {visibleRuns.map((item) => (
                <div className="research-run-card" key={item.id}>
                  <div className="research-run-card-heading">
                    <input
                      type="checkbox"
                      aria-label={`Compare ${item.id}`}
                      disabled={
                        item.status !== 'completed' ||
                        (!checked.includes(item.id) && checked.length >= 8)
                      }
                      checked={checked.includes(item.id)}
                      onChange={(event) =>
                        setChecked(
                          event.target.checked
                            ? [...checked, item.id]
                            : checked.filter((id) => id !== item.id),
                        )
                      }
                    />
                    <button className="text-button" onClick={() => selectRun(item.id)}>
                      {item.id.slice(0, 10)}
                    </button>
                    <Status
                      type={
                        item.status === 'completed'
                          ? 'good'
                          : item.status === 'failed'
                            ? 'bad'
                            : 'neutral'
                      }
                    >
                      {item.status}
                    </Status>
                  </div>
                  <p className="research-run-card-meta">
                    {t(modeNames[item.config.mode])} · {date(item.created_at)}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="research-run-empty">{t('No research runs')}</p>
          )
        ) : (
          <DataTable
            rows={visibleRuns}
            empty="No research runs"
            columns={[
              {
                key: 'select',
                label: '',
                render: (r) => (
                  <input
                    type="checkbox"
                    aria-label={`Compare ${r.id}`}
                    disabled={
                      r.status !== 'completed' || (!checked.includes(r.id) && checked.length >= 8)
                    }
                    checked={checked.includes(r.id)}
                    onChange={(e) =>
                      setChecked(
                        e.target.checked ? [...checked, r.id] : checked.filter((id) => id !== r.id),
                      )
                    }
                  />
                ),
              },
              {
                key: 'id',
                label: 'Selected run',
                render: (r) => (
                  <button className="text-button" onClick={() => selectRun(r.id)}>
                    {r.id.slice(0, 10)}
                    {activeId === r.id && <Check size={12} />}
                  </button>
                ),
              },
              { key: 'mode', label: 'Mode', render: (r) => t(modeNames[r.config.mode]) },
              {
                key: 'status',
                label: 'Status',
                render: (r) => (
                  <Status
                    type={
                      r.status === 'completed' ? 'good' : r.status === 'failed' ? 'bad' : 'neutral'
                    }
                  >
                    {r.status}
                  </Status>
                ),
              },
              { key: 'created_at', label: 'Created', render: (r) => date(r.created_at) },
              {
                key: 'return',
                label: 'Return / scope',
                render: (r) => (
                  <div className="table-stacked">
                    <span>
                      {percent(
                        r.summary?.total_return_pct ??
                          (r.summary?.metrics as RecordData | undefined)?.total_return_pct ??
                          (r.summary?.oos_summary as RecordData | undefined)?.median_return_pct ??
                          r.summary?.median_return_pct ??
                          r.result?.metrics?.total_return_pct ??
                          (r.result?.result as ProResult | undefined)?.metrics?.total_return_pct ??
                          (r.result?.oos_summary as RecordData | undefined)?.median_return_pct,
                      )}
                    </span>
                    {['train_test', 'walk_forward'].includes(r.config.mode) && (
                      <small>{t('Median test return')}</small>
                    )}
                    {['grid', 'cost_stress'].includes(r.config.mode) && (
                      <small>{t('Inspect scenarios')}</small>
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
        {runs.hasNextPage && (
          <div className="catalog-more">
            <button
              className="button button-secondary"
              disabled={runs.isFetchingNextPage}
              onClick={() => void runs.fetchNextPage()}
            >
              {runs.isFetchingNextPage && <Loader2 size={13} className="spin" />}
              {t('Load older runs')}
            </button>
          </div>
        )}
      </section>
      {comparison.length >= 2 && (
        <section className="pro-panel comparison-panel">
          <div className="section-heading">
            <h2>{t('Run comparison')}</h2>
            <button className="text-button" onClick={() => setComparison([])}>
              {t('Close')}
            </button>
          </div>
          {compare.isPending ? (
            <Loading />
          ) : compare.isError ? (
            <ErrorBox error={compare.error} />
          ) : (
            <>
              {!!compare.data.warning && (
                <p className="inline-warning">{String(compare.data.warning)}</p>
              )}
              <DataTable
                rows={arrayRecords(compare.data.items ?? compare.data.runs)}
                columns={[
                  {
                    key: 'id',
                    label: 'Selected run',
                    render: (r) => String(r.id).slice(0, 12),
                  },
                  {
                    key: 'config',
                    label: 'Mode',
                    render: (r) => valueText((r.config as RecordData)?.mode),
                  },
                  { key: 'source', label: 'Source' },
                  {
                    key: 'return',
                    label: 'Net return',
                    render: (r) => {
                      const p = r.result as ProResult;
                      const m = (p?.result as ProResult)?.metrics ?? p?.metrics;
                      return m ? percent(m.total_return_pct) : '—';
                    },
                  },
                  {
                    key: 'oos',
                    label: 'Out-of-sample summary',
                    render: (r) => <JsonDetails value={(r.result as ProResult)?.oos_summary} />,
                  },
                  {
                    key: 'details',
                    label: 'Assumptions',
                    render: (r) => <JsonDetails value={r.config} />,
                  },
                ]}
              />
              <JsonDetails value={compare.data} label="Details" />
            </>
          )}
        </section>
      )}
    </div>
  );
  const configuration = (
    <section
      key="configuration"
      className="pro-panel pro-research-form"
      data-research-region="configuration"
      aria-labelledby="research-configuration-heading"
    >
      <div className="section-heading">
        <h2 id="research-configuration-heading">{t('Research configuration')}</h2>
        {run.data && (
          <button type="button" className="text-button" onClick={() => load(run.data!)}>
            {t('Use selected run configuration')}
          </button>
        )}
      </div>
      <form
        className="compact-form"
        onChangeCapture={() => {
          editorGeneration.current += 1;
        }}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {datasets.isError && <ErrorBox error={datasets.error} />}
        <Field label="Dataset">
          <select
            required
            aria-label={t('Dataset')}
            value={datasetId}
            onChange={(e) => {
              setDatasetId(e.target.value);
              const d = tradeDatasets.find((x) => x.id === e.target.value);
              setWindowStart(utcInput(d?.start));
              setWindowEnd(utcInput(d?.end));
              setMarkId('');
              setFundingId('');
            }}
          >
            <option value="">{t('Select a dataset')}</option>
            {tradeDatasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.inst_id} · {d.bar} ·{' '}
                {String(d.content_hash ?? d.dataset_hash ?? d.hash ?? d.id).slice(0, 7)}
              </option>
            ))}
          </select>
        </Field>
        <button
          type="button"
          className="text-button"
          onClick={() => {
            draft.flush();
            onOpenData();
          }}
        >
          {t('Open Data library')}
        </button>
        {dataset && (
          <div className="selected-dataset">
            <span>
              {dataset.inst_id} ·{' '}
              {dataset.transport === 'user_import' ? t('Imported') : dataset.source.toUpperCase()}
            </span>
            <span>
              {date(Number(dataset.start ?? dataset.start_ts))} —{' '}
              {date(Number(dataset.end ?? dataset.end_ts))}
            </span>
          </div>
        )}
        {!!dataset?.warning && (
          <p className="inline-warning dataset-warning">{String(dataset.warning)}</p>
        )}
        {dataset && (
          <div className="research-date-range">
            <Field label="Start (UTC)">
              <input
                required
                type="datetime-local"
                min={utcInput(dataset.start)}
                max={utcInput(dataset.end)}
                value={windowStart}
                onChange={(e) => setWindowStart(e.target.value)}
              />
            </Field>
            <Field label="End (UTC)">
              <input
                required
                type="datetime-local"
                min={utcInput(dataset.start)}
                max={utcInput(dataset.end)}
                value={windowEnd}
                onChange={(e) => setWindowEnd(e.target.value)}
              />
            </Field>
          </div>
        )}
        {dataset && (
          <p className="range-hint">
            {t(
              'Maximum 98,000 selected bars. Up to 2,000 preceding bars are captured for indicator warmup.',
            )}
          </p>
        )}
        {isSwap && (
          <>
            <div className="form-grid">
              <Field label="Mark dataset">
                <select value={markId} onChange={(e) => setMarkId(e.target.value)}>
                  <option value="">{t('None')}</option>
                  {catalog
                    .filter((d) => d.inst_id === dataset?.inst_id && d.kind === 'mark')
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.bar} · {d.id.slice(0, 8)}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Funding dataset">
                <select value={fundingId} onChange={(e) => setFundingId(e.target.value)}>
                  <option value="">{t('None')}</option>
                  {catalog
                    .filter((d) => d.inst_id === dataset?.inst_id && d.kind === 'funding')
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.id.slice(0, 8)}
                      </option>
                    ))}
                </select>
              </Field>
            </div>
            <div className="form-grid">
              <Field label="Direction">
                <select
                  disabled={!!versionId}
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as Direction)}
                >
                  <option value="long_only">{t('Long only')}</option>
                  <option value="short_only">{t('Short only')}</option>
                  <option value="long_short">{t('Long / short')}</option>
                </select>
              </Field>
              <Field label="Leverage">
                <input
                  required
                  type="number"
                  min="1"
                  max="50"
                  step="1"
                  disabled={!!versionId}
                  value={leverage}
                  onChange={(e) => setLeverage(Number(e.target.value))}
                />
              </Field>
            </div>
          </>
        )}
        <fieldset className="strategy-bound-fields" disabled={!!versionId}>
          <StrategyFields
            professional
            value={strategy}
            onChange={setStrategy}
            programDraft={programDraft}
            onProgramDraftChange={setProgramDraft}
          />
        </fieldset>
        <Field label="Initial capital">
          <div className="input-suffix">
            <input
              required
              type="number"
              min="1"
              step="0.01"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
            />
            <span>USDT</span>
          </div>
        </Field>
        <div className="form-grid">
          <Field label="Fee">
            <div className="input-suffix">
              <input
                required
                type="number"
                min="0"
                step="0.1"
                value={fee}
                onChange={(e) => setFee(e.target.value)}
              />
              <span>bps</span>
            </div>
          </Field>
          <Field label="Slippage">
            <div className="input-suffix">
              <input
                required
                type="number"
                min="0"
                step="0.1"
                value={slippage}
                onChange={(e) => setSlippage(e.target.value)}
              />
              <span>bps</span>
            </div>
          </Field>
        </div>
        {isSwap && (
          <Field label="Liquidation fee">
            <div className="input-suffix">
              <input
                required
                type="number"
                min="0"
                step="0.1"
                value={liqFee}
                onChange={(e) => setLiqFee(e.target.value)}
              />
              <span>bps</span>
            </div>
          </Field>
        )}
        <Field label="Mode">
          <select value={mode} onChange={(e) => setMode(e.target.value as ResearchMode)}>
            {Object.entries(modeNames).map(([key, label]) => (
              <option key={key} value={key}>
                {t(label)}
              </option>
            ))}
          </select>
        </Field>
        {mode === 'train_test' && (
          <Field label="Training fraction">
            <input
              required
              type="number"
              min="0.1"
              max="0.9"
              step="0.05"
              value={trainFraction}
              onChange={(e) => setTrainFraction(Number(e.target.value))}
            />
          </Field>
        )}
        {mode === 'train_test' && (
          <Field label="Purge bars">
            <input
              required
              type="number"
              min="0"
              value={purgeBars}
              onChange={(e) => setPurgeBars(Number(e.target.value))}
            />
          </Field>
        )}
        {mode === 'walk_forward' && (
          <div className="form-grid">
            <Field label="Training bars">
              <input
                required
                type="number"
                min="30"
                value={trainBars}
                onChange={(e) => setTrainBars(Number(e.target.value))}
              />
            </Field>
            <Field label="Test bars">
              <input
                required
                type="number"
                min="10"
                value={testBars}
                onChange={(e) => setTestBars(Number(e.target.value))}
              />
            </Field>
            <Field label="Step bars">
              <input
                required
                type="number"
                min="1"
                value={stepBars}
                onChange={(e) => setStepBars(Number(e.target.value))}
              />
            </Field>
            <Field label="Purge bars">
              <input
                required
                type="number"
                min="0"
                value={purgeBars}
                onChange={(e) => setPurgeBars(Number(e.target.value))}
              />
            </Field>
          </div>
        )}
        {oosMode && (
          <Field
            label="Parameter selection"
            hint="Candidates are ranked on training data only. Test data is reserved for evaluation."
          >
            <select
              value={selectOnTraining ? 'training' : 'fixed'}
              onChange={(e) => setSelectOnTraining(e.target.value === 'training')}
            >
              <option value="fixed">{t('Fixed parameters')}</option>
              <option value="training">{t('Choose on training data')}</option>
            </select>
          </Field>
        )}
        {showGrid && strategy.kind === 'sma_cross' && (
          <div className="form-grid">
            <Field label="Fast windows">
              <input required value={fastGrid} onChange={(e) => setFastGrid(e.target.value)} />
            </Field>
            <Field label="Slow windows">
              <input required value={slowGrid} onChange={(e) => setSlowGrid(e.target.value)} />
            </Field>
          </div>
        )}
        {showGrid && strategy.kind === 'rsi_reversion' && (
          <>
            <Field label="RSI periods">
              <input required value={rsiGrid} onChange={(e) => setRsiGrid(e.target.value)} />
            </Field>
            <div className="form-grid">
              <Field label="Entry thresholds">
                <input required value={entryGrid} onChange={(e) => setEntryGrid(e.target.value)} />
              </Field>
              <Field label="Exit thresholds">
                <input required value={exitGrid} onChange={(e) => setExitGrid(e.target.value)} />
              </Field>
            </div>
          </>
        )}
        {showGrid && strategy.kind === 'buy_hold' && (
          <Field label="Allocation fractions" hint="Comma-separated fractions, from 0.01 to 1.">
            <input
              required
              value={allocationGrid}
              onChange={(e) => setAllocationGrid(e.target.value)}
            />
          </Field>
        )}
        {mode === 'cost_stress' && (
          <>
            <Field label="Fee scenarios (bps)">
              <input required value={feeGrid} onChange={(e) => setFeeGrid(e.target.value)} />
            </Field>
            <Field label="Slippage scenarios (bps)">
              <input required value={slipGrid} onChange={(e) => setSlipGrid(e.target.value)} />
            </Field>
          </>
        )}
        {validation && <ErrorBox error={new Error(validation)} />}
        {create.isError && currentTask(create.variables?.request) && (
          <ErrorBox error={create.error} />
        )}
        <button
          type="submit"
          className="button button-citrus full-width"
          disabled={
            !canOperate ||
            !datasetId ||
            busy ||
            (strategy.kind === 'program' && !strategy.rules?.length)
          }
        >
          {busy ? <Loader2 size={15} className="spin" /> : <Play size={14} />} {t('Run research')}
        </button>
        <p className="form-footnote pro-form-note">
          {t('Training results are not test results. Inspect every fold and cost assumption.')}
        </p>
      </form>
      {replay.isError && currentTask(replay.variables?.request) && (
        <ErrorBox error={replay.error} />
      )}
    </section>
  );
  return (
    <>
      <PageHeading
        eyebrow="RESEARCH"
        title="Research"
        description="Versioned inputs, spot and perpetual strategies, and out-of-sample evaluation."
      >
        <button
          className="button button-secondary"
          disabled={!canOperate}
          onClick={() => {
            draft.clear({
              lastInputsKey: inputHandoffKey(initialInputs),
              lastVersionId: initialStrategyVersion?.id ?? '',
            });
            selectRun('');
            if (onClearDraft) onClearDraft();
            else onClearStrategyVersion();
            setNotice(null);
            setValidation(null);
            create.reset();
          }}
        >
          {t('New research')}
        </button>
        <button
          className="button button-secondary"
          disabled={!plan}
          onClick={() => void exportRun()}
        >
          <ArrowDownToLine size={14} />
          {t('Export JSON')}
        </button>
        <button
          className="button button-secondary"
          disabled={!canOperate || !plan || busy}
          onClick={() => activeId && replay.mutate({ request: beginRequest('replay') })}
        >
          <RefreshCw size={14} />
          {t('Replay snapshot')}
        </button>
        {run.data && ['queued', 'running'].includes(run.data.status) && (
          <button
            className="button button-secondary"
            disabled={
              !canOperate ||
              (cancel.isPending && cancel.variables === activeId) ||
              cancellationRequested
            }
            onClick={() => activeId && cancel.mutate(activeId)}
          >
            <Square size={14} />
            {t(cancellationRequested ? 'Cancellation requested' : 'Cancel research')}
          </button>
        )}
        {run.data?.status === 'completed' && variant && (
          <ResearchRelease
            key={`${run.data.id}:${variant.key}`}
            run={run.data}
            variant={variant.key}
            onExecution={onOpenExecution}
          />
        )}
      </PageHeading>
      <ActionNote text={notice} error={exportError} />
      {submittedRun?.source === source &&
        submittedRun.userId === userId &&
        submittedRun.id !== activeId && (
          <div className="action-note" role="status">
            <span>
              {t('Earlier research was submitted. Your current configuration is unchanged.')}
            </span>{' '}
            <button className="text-button" onClick={() => selectRun(submittedRun.id)}>
              {t('View submitted research')}
            </button>
          </div>
        )}
      {cancel.isError && cancel.variables === activeId && <ErrorBox error={cancel.error} />}
      <ResearchCancellationStatus
        requested={cancellationRequested}
        recovery={(run.data?.manifest?.cancellation as RecordData | undefined)?.recovery}
        disabled={!canOperate || (cancel.isPending && cancel.variables === activeId)}
        onRetry={() => activeId && cancel.mutate(activeId)}
      />
      <div className="prepared-input-note" role="status">
        <span>
          {draft.restoredAt
            ? `${t('Research draft restored')} · ${date(draft.restoredAt, true)}`
            : t('Research configuration is saved in this browser session.')}
        </span>
        <button
          className="text-button"
          onClick={() => {
            draft.clear({
              lastInputsKey: inputHandoffKey(initialInputs),
              lastVersionId: initialStrategyVersion?.id ?? '',
            });
            selectRun('');
            if (onClearDraft) onClearDraft();
            else onClearStrategyVersion();
            setValidation(null);
            create.reset();
          }}
        >
          {t('Clear research draft')}
        </button>
      </div>
      {draft.problem && (
        <p role="alert" className="inline-warning">
          {t(
            draft.problem === 'invalid'
              ? 'A damaged research draft was discarded. Saved research evidence is unchanged.'
              : 'The research draft could not be saved. Keep this page open or export your configuration before leaving.',
          )}
        </p>
      )}

      {versionId && (
        <div className="prepared-input-note">
          <span>{t('Bound strategy version')}</span>
          <code>{versionId.slice(0, 12)}</code>
          <button
            className="text-button"
            onClick={() => {
              setVersionId(undefined);
              onClearStrategyVersion();
            }}
          >
            {t('Detach for exploratory research')}
          </button>
        </div>
      )}
      {preparedInputs?.package_id && (
        <p className="prepared-input-note">
          <span>
            {t(packageAttached ? 'Prepared research package' : 'Package inputs modified')}
          </span>
          <code>{preparedInputs.package_id.slice(0, 12)}</code>
          <span>
            {t(
              packageAttached
                ? 'Exact dataset versions and the prepared UTC window are selected. Editing inputs detaches the package manifest.'
                : 'Dataset or window changes have detached the package manifest. The run will use the explicitly selected raw versions.',
            )}
          </span>
        </p>
      )}
      <div className={`pro-research-layout${activeId ? '' : ' research-config-first'}`}>
        {activeId ? [results, configuration] : [configuration, results]}
      </div>
    </>
  );
}
export { arrayRecords };
