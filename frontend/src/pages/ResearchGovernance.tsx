import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Play, Plus } from 'lucide-react';
import { useState } from 'react';
import type { Source } from '../api';
import { proApi } from '../proApi';
import { useSession } from '../components/AuthGate';
import { DataTable, JsonDetails, RecordGrid } from '../components/ProWorkspace';
import { ErrorBox, Field, Loading, PageHeading, Status } from '../components/workspace';
import { useI18n } from '../lib/i18n';
import { date } from '../lib/format';
import { canResearch } from '../lib/permissions';

const utc = (ts?: number) => (ts ? new Date(ts).toISOString().slice(0, 16) : '');
export default function ResearchGovernance({
  source,
  onRun,
}: {
  source: Source;
  onRun: (id: string) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canResearch(useSession()?.user?.role);
  const projects = useQuery({ queryKey: ['strategy-projects'], queryFn: proApi.strategies });
  const [selected, setSelected] = useState('');
  const projectId = selected || projects.data?.items[0]?.id;
  const project = useQuery({
    queryKey: ['strategy-project', projectId],
    queryFn: () => proApi.strategy(projectId!),
    enabled: !!projectId,
  });
  const evidence = useQuery({
    queryKey: ['research-governance', projectId],
    queryFn: () => proApi.governance(projectId!),
    enabled: !!projectId,
  });
  const holdouts = useQuery({ queryKey: ['research-holdouts'], queryFn: proApi.holdouts });
  const datasets = useQuery({
    queryKey: ['pro-datasets'],
    queryFn: proApi.datasets,
    refetchOnMount: 'always',
    refetchInterval: 10000,
  });
  const [editing, setEditing] = useState(false);
  const [versionId, setVersionId] = useState('');
  const version = project.data?.versions?.find((v) => v.id === versionId);
  const [datasetId, setDatasetId] = useState('');
  const dataset = datasets.data?.items.find((d) => d.id === datasetId);
  const [name, setName] = useState('');
  const [benchmark, setBenchmark] = useState('');
  const [rejection, setRejection] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [fee, setFee] = useState('10');
  const [slip, setSlip] = useState('5');
  const [mark, setMark] = useState('');
  const [funding, setFunding] = useState('');
  const seal = useMutation({
    mutationFn: () =>
      proApi.sealHoldout({
        name,
        strategy_version_id: versionId,
        dataset_id: datasetId,
        start_ts: Date.parse(start + 'Z'),
        end_ts: Date.parse(end + 'Z'),
        benchmark,
        rejection_plan: rejection,
        fee_bps: fee,
        slippage_bps: slip,
        ...(mark ? { mark_dataset_id: mark } : {}),
        ...(funding ? { funding_dataset_id: funding } : {}),
      }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ['research-holdouts'] });
      void qc.invalidateQueries({ queryKey: ['research-governance'] });
    },
  });
  const evaluate = useMutation({
    mutationFn: proApi.evaluateHoldout,
    onSuccess: (run) => {
      void qc.invalidateQueries({ queryKey: ['pro-runs'] });
      void qc.invalidateQueries({ queryKey: ['research-holdouts'] });
      void qc.invalidateQueries({ queryKey: ['research-governance'] });
      onRun(run.id);
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="RESEARCH GOVERNANCE"
        title="Research governance"
        description="Recorded trials, pre-registered rejection plans and one-use holdout evaluations."
      >
        <button
          className="button button-citrus"
          disabled={!canOperate || !projectId}
          onClick={() => {
            seal.reset();
            setEditing(true);
          }}
        >
          <Plus size={14} />
          {t('Register holdout')}
        </button>
      </PageHeading>
      <p className="quiet-copy">
        {t(
          'The seal controls this research workflow. Public data, raw dataset inspection and external experiments remain observable outside this protocol; statistical blindness cannot be guaranteed.',
        )}
      </p>
      <Field label="Strategy project">
        <select
          value={projectId ?? ''}
          onChange={(e) => {
            setSelected(e.target.value);
            setVersionId('');
            setEditing(false);
          }}
        >
          <option value="">{t('Choose a strategy project')}</option>
          {projects.data?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      {projects.isError && <ErrorBox error={projects.error} />}
      {evidence.isError && <ErrorBox error={evidence.error} />}
      {evidence.data && (
        <section className="pro-panel">
          <h2>{t('Project trial history')}</h2>
          <RecordGrid
            value={{
              recorded_runs: evidence.data.run_count,
              candidate_configurations: evidence.data.candidate_configurations,
            }}
          />
          <p className="quiet-copy">
            {t(
              'Counts include recorded queued, failed and replayed version-bound runs. Reusing a holdout or renaming a project does not create new independent evidence. External trials are outside this count.',
            )}
          </p>
          <DataTable
            rows={(evidence.data.items ?? []) as Record<string, unknown>[]}
            columns={[
              { key: 'id', label: 'Run' },
              { key: 'status', label: 'Status' },
              { key: 'candidate_configurations', label: 'Candidates' },
              { key: 'created_at', label: 'Created', render: (r) => date(Number(r.created_at)) },
              { key: 'details', label: 'Evidence', render: (r) => <JsonDetails value={r} /> },
            ]}
          />
        </section>
      )}
      {editing && (
        <section className="pro-panel holdout-editor">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              seal.mutate();
            }}
          >
            <h2>{t('Freeze the evaluation contract')}</h2>
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
              <Field label="Strategy version">
                <select
                  required
                  value={versionId}
                  onChange={(e) => {
                    setVersionId(e.target.value);
                    setDatasetId('');
                  }}
                >
                  <option value="">{t('Choose an immutable version')}</option>
                  {project.data?.versions?.map((v) => (
                    <option key={v.id} value={v.id}>
                      v{v.revision} · {v.definition.product} · {v.definition.bar} ·{' '}
                      {v.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Dataset">
              <select
                required
                value={datasetId}
                onChange={(e) => {
                  setDatasetId(e.target.value);
                  const d = datasets.data?.items.find((d) => d.id === e.target.value);
                  setStart(utc(d?.start));
                  setEnd(utc(d?.end));
                }}
              >
                <option value="">{t('Select a dataset')}</option>
                {datasets.data?.items
                  .filter(
                    (d) =>
                      d.source === source &&
                      d.kind === 'trade' &&
                      version &&
                      d.bar === version.definition.bar &&
                      (d.inst_id.endsWith('-SWAP') ? 'SWAP' : 'SPOT') ===
                        version.definition.product,
                  )
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.inst_id} · {d.bar} · {d.id.slice(0, 8)}
                    </option>
                  ))}
              </select>
            </Field>
            <div className="form-grid">
              <Field label="UTC start">
                <input
                  required
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </Field>
              <Field label="UTC end">
                <input
                  required
                  type="datetime-local"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </Field>
            </div>
            {version?.definition.product === 'SWAP' && (
              <div className="form-grid">
                <Field label="Mark dataset">
                  <select required value={mark} onChange={(e) => setMark(e.target.value)}>
                    <option value="" />
                    {datasets.data?.items
                      .filter(
                        (d) =>
                          d.source === source &&
                          d.inst_id === dataset?.inst_id &&
                          d.kind === 'mark',
                      )
                      .map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.id.slice(0, 12)}
                        </option>
                      ))}
                  </select>
                </Field>
                <Field label="Funding dataset">
                  <select required value={funding} onChange={(e) => setFunding(e.target.value)}>
                    <option value="" />
                    {datasets.data?.items
                      .filter(
                        (d) =>
                          d.source === source &&
                          d.inst_id === dataset?.inst_id &&
                          d.kind === 'funding',
                      )
                      .map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.id.slice(0, 12)}
                        </option>
                      ))}
                  </select>
                </Field>
              </div>
            )}
            <Field label="Pre-registered benchmark">
              <input
                required
                minLength={4}
                maxLength={1000}
                value={benchmark}
                onChange={(e) => setBenchmark(e.target.value)}
              />
            </Field>
            <Field
              label="Rejection plan"
              hint="Specify the outcome that would reject the hypothesis before inspecting the holdout result."
            >
              <textarea
                className="study-hypothesis-input"
                required
                minLength={20}
                maxLength={4000}
                rows={4}
                value={rejection}
                onChange={(e) => setRejection(e.target.value)}
              />
            </Field>
            <div className="form-grid">
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
            </div>
            <p className="quiet-copy">
              {t(
                'Registration freezes the version, data interval, benchmark, costs and rejection plan. Each seal admits one evaluation; immutable replay remains available. Prior research exposure or indicator warmup overlap blocks registration or access.',
              )}
            </p>
            {seal.isError && <ErrorBox error={seal.error} />}
            <div className="toolbar">
              <button className="button button-citrus" disabled={!canOperate || seal.isPending}>
                <Lock size={14} />
                {t('Seal holdout')}
              </button>
              <button
                type="button"
                className="button button-ghost"
                onClick={() => setEditing(false)}
              >
                {t('Cancel')}
              </button>
            </div>
          </form>
        </section>
      )}
      <section className="pro-panel">
        <h2>{t('Registered holdouts')}</h2>
        {evaluate.isError && <ErrorBox error={evaluate.error} />}
        {holdouts.isPending ? (
          <Loading />
        ) : holdouts.isError ? (
          <ErrorBox error={holdouts.error} />
        ) : (
          <DataTable
            rows={
              holdouts.data?.items.filter(
                (h) => h.source === source && h.project_id === projectId,
              ) ?? []
            }
            empty="No registered holdouts"
            columns={[
              { key: 'name', label: 'Holdout', render: (h) => h.plan.name },
              { key: 'inst_id', label: 'Market' },
              { key: 'start_ts', label: 'UTC start', render: (h) => date(h.start_ts) },
              { key: 'end_ts', label: 'UTC end', render: (h) => date(h.end_ts) },
              {
                key: 'status',
                label: 'Status',
                render: (h) => <Status type="neutral">{t(h.status)}</Status>,
              },
              {
                key: 'actions',
                label: 'Actions',
                render: (h) => (
                  <div className="table-actions">
                    {h.status === 'sealed' ? (
                      <button
                        className="text-button"
                        disabled={!canOperate || evaluate.isPending}
                        onClick={() => evaluate.mutate(h)}
                      >
                        <Play size={12} />
                        {t('Evaluate once')}
                      </button>
                    ) : (
                      h.run_id && (
                        <button className="text-button" onClick={() => onRun(h.run_id!)}>
                          {t('Open research')}
                        </button>
                      )
                    )}
                    <JsonDetails value={h} label="Frozen plan" />
                  </div>
                ),
              },
            ]}
          />
        )}
      </section>
    </>
  );
}
