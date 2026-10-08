import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Copy, GitBranch, Plus, Save } from 'lucide-react';
import { useState } from 'react';
import type { Strategy } from '../api';
import { defaultStrategy } from '../api';
import { useSession } from '../components/AuthGate';
import { DataTable, RecordGrid } from '../components/ProWorkspace';
import {
  ErrorBox,
  Field,
  Loading,
  PageHeading,
  Status,
  StrategyFields,
} from '../components/workspace';
import { canResearch } from '../lib/permissions';
import { date } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { proApi } from '../proApi';
import type { Direction, StrategyDefinition, StrategyVersion } from '../proApi';
import recipeData from '../../../examples/strategies.json';
import StrategyResearchLibrary from '../components/StrategyResearchLibrary';

const recipes = recipeData as unknown as {
  id: string;
  name: string;
  hypothesis: string;
  definition: StrategyDefinition;
}[];

export default function Strategies({
  onResearch,
}: {
  onResearch: (version: StrategyVersion) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const canOperate = canResearch(useSession()?.user?.role);
  const projects = useQuery({ queryKey: ['strategy-projects'], queryFn: proApi.strategies });
  const [selected, setSelected] = useState<string>();
  const activeId = selected ?? projects.data?.items[0]?.id;
  const project = useQuery({
    queryKey: ['strategy-project', activeId],
    queryFn: () => proApi.strategy(activeId!),
    enabled: !!activeId,
  });
  const [editing, setEditing] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [parent, setParent] = useState<StrategyVersion>();
  const [name, setName] = useState('');
  const [hypothesis, setHypothesis] = useState('');
  const [strategy, setStrategy] = useState<Strategy>({ ...defaultStrategy });
  const [product, setProduct] = useState<'SPOT' | 'SWAP'>('SPOT');
  const [bar, setBar] = useState('1H');
  const [direction, setDirection] = useState<Direction>('long_only');
  const [leverage, setLeverage] = useState(1);
  const [version, setVersion] = useState<StrategyVersion>();
  const activeVersion = version?.project_id === activeId ? version : project.data?.versions?.[0];
  const save = useMutation({
    mutationFn: async () => {
      const definition: StrategyDefinition = {
        schema_version: 1,
        product,
        bar,
        strategy,
        direction: product === 'SPOT' ? 'long_only' : direction,
        leverage: String(product === 'SPOT' ? 1 : leverage),
      };
      if (parent)
        return proApi.createStrategyVersion(parent.project_id, {
          hypothesis,
          definition,
          parent_id: parent.id,
        });
      const created = await proApi.createStrategy({ name, hypothesis, definition });
      return created.version!;
    },
    onSuccess: async (saved) => {
      setSelected(saved.project_id);
      setVersion(saved);
      setEditing(false);
      await qc.invalidateQueries({ queryKey: ['strategy-projects'] });
      await qc.invalidateQueries({ queryKey: ['strategy-project', saved.project_id] });
    },
  });
  const edit = (existing?: StrategyVersion) => {
    setLibraryOpen(false);
    save.reset();
    setParent(existing);
    setName(existing ? (project.data?.name ?? '') : '');
    setHypothesis(existing?.hypothesis ?? '');
    setStrategy({ ...(existing?.definition.strategy ?? defaultStrategy) });
    setProduct(existing?.definition.product ?? 'SPOT');
    setBar(existing?.definition.bar ?? '1H');
    setDirection(existing?.definition.direction ?? 'long_only');
    setLeverage(Number(existing?.definition.leverage ?? 1));
    setEditing(true);
  };
  return (
    <>
      <PageHeading
        eyebrow="STRATEGY REGISTRY"
        title="Strategies"
        description="Economic hypotheses, immutable versions and traceable research."
      >
        <button className="button button-citrus" disabled={!canOperate} onClick={() => edit()}>
          <Plus size={14} />
          {t('New strategy')}
        </button>
      </PageHeading>
      <div className="workspace-tabs" role="group" aria-label={t('Strategy view')}>
        <button
          className={`text-button${libraryOpen ? ' active' : ''}`}
          onClick={() => setLibraryOpen(true)}
        >
          {t('Research library')}
        </button>
        <button
          className={`text-button${!libraryOpen ? ' active' : ''}`}
          onClick={() => setLibraryOpen(false)}
        >
          {t('My strategy versions')}
        </button>
      </div>
      {libraryOpen ? (
        <StrategyResearchLibrary
          canCreate={canOperate}
          onUse={(id) => {
            const recipe = recipes.find((item) => item.id === id);
            if (!recipe) return;
            edit();
            setName(recipe.name);
            setHypothesis(recipe.hypothesis);
            setStrategy({ ...recipe.definition.strategy });
            setProduct(recipe.definition.product);
            setBar(recipe.definition.bar);
            setDirection(recipe.definition.direction);
            setLeverage(Number(recipe.definition.leverage));
          }}
        />
      ) : (
        <div className="strategy-workspace">
          <aside className="strategy-library" aria-label={t('Strategy projects')}>
            <h2>{t('Strategy projects')}</h2>
            {projects.isPending ? (
              <Loading />
            ) : projects.isError ? (
              <ErrorBox error={projects.error} />
            ) : projects.data?.items.length ? (
              projects.data.items.map((item) => (
                <button
                  className={`strategy-project${item.id === activeId ? ' active' : ''}`}
                  key={item.id}
                  onClick={() => {
                    setSelected(item.id);
                    setVersion(undefined);
                    setEditing(false);
                  }}
                >
                  <strong>{item.name}</strong>
                  <span>
                    v{item.latest_revision} · {item.version_count} {t('versions')}
                  </span>
                </button>
              ))
            ) : (
              <p className="quiet-copy">
                {t('Create a hypothesis and its first immutable version.')}
              </p>
            )}
          </aside>
          <section className="strategy-detail pro-panel">
            {editing ? (
              <form
                className="strategy-editor"
                onSubmit={(e) => {
                  e.preventDefault();
                  save.mutate();
                }}
              >
                <div className="section-heading">
                  <h2>{t(parent ? 'New version' : 'New strategy')}</h2>
                  <button
                    type="button"
                    className="button button-ghost"
                    onClick={() => setEditing(false)}
                  >
                    {t('Cancel')}
                  </button>
                </div>
                {!parent && (
                  <Field
                    label="Research starting point"
                    hint="Reference hypotheses with failure criteria; no investment edge is claimed."
                  >
                    <select
                      defaultValue=""
                      onChange={(e) => {
                        const recipe = recipes.find((r) => r.id === e.target.value);
                        if (!recipe) return;
                        setName(recipe.name);
                        setHypothesis(recipe.hypothesis);
                        setStrategy({ ...recipe.definition.strategy });
                        setProduct(recipe.definition.product);
                        setBar(recipe.definition.bar);
                        setDirection(recipe.definition.direction);
                        setLeverage(Number(recipe.definition.leverage));
                      }}
                    >
                      <option value="">{t('Custom hypothesis')}</option>
                      {recipes.map((r) => (
                        <option key={r.id} value={r.id}>
                          {t(r.name)}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {!parent && (
                  <Field label="Strategy name">
                    <input
                      required
                      minLength={2}
                      maxLength={100}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </Field>
                )}
                <Field
                  label="Economic hypothesis"
                  hint="Describe why the effect may exist, when it should fail, and what evidence would reject it."
                >
                  <textarea
                    required
                    minLength={12}
                    maxLength={4000}
                    rows={4}
                    value={hypothesis}
                    onChange={(e) => setHypothesis(e.target.value)}
                  />
                </Field>
                <div className="form-grid">
                  <Field label="Product">
                    <select
                      value={product}
                      onChange={(e) => setProduct(e.target.value as 'SPOT' | 'SWAP')}
                    >
                      <option value="SPOT">{t('Spot')}</option>
                      <option value="SWAP">{t('USDT perpetual')}</option>
                    </select>
                  </Field>
                  <Field label="Interval">
                    <select value={bar} onChange={(e) => setBar(e.target.value)}>
                      {['1m', '5m', '15m', '1H', '4H', '1Dutc'].map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                  </Field>
                </div>
                <StrategyFields professional value={strategy} onChange={setStrategy} />
                {product === 'SWAP' && (
                  <div className="form-grid">
                    <Field label="Direction">
                      <select
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
                        min={1}
                        max={50}
                        step={1}
                        value={leverage}
                        onChange={(e) => setLeverage(Number(e.target.value))}
                      />
                    </Field>
                  </div>
                )}
                <p className="quiet-copy">
                  {t(
                    'Saving creates an immutable version. Existing research and deployments retain their original definition.',
                  )}
                </p>
                {save.isError && <ErrorBox error={save.error} />}
                <button className="button button-citrus" disabled={!canOperate || save.isPending}>
                  <Save size={14} />
                  {t('Save version')}
                </button>
              </form>
            ) : project.isPending && activeId ? (
              <Loading />
            ) : project.isError ? (
              <ErrorBox error={project.error} />
            ) : activeVersion ? (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">{project.data?.name}</p>
                    <h2>
                      {t('Version')} {activeVersion.revision}
                    </h2>
                  </div>
                  <Status type="neutral">{t('Immutable')}</Status>
                </div>
                <p className="strategy-hypothesis">{activeVersion.hypothesis}</p>
                <div className="toolbar">
                  <button
                    className="button button-citrus"
                    disabled={!canOperate}
                    onClick={() => onResearch(activeVersion)}
                  >
                    <ArrowUpRight size={14} />
                    {t('Research this version')}
                  </button>
                  <button
                    className="button button-secondary"
                    disabled={!canOperate}
                    onClick={() => edit(activeVersion)}
                  >
                    <Copy size={14} />
                    {t('Create new version')}
                  </button>
                </div>
                <RecordGrid
                  value={{
                    product: activeVersion.definition.product,
                    interval: activeVersion.definition.bar,
                    direction: activeVersion.definition.direction,
                    leverage: activeVersion.definition.leverage,
                    created_by: activeVersion.created_by,
                    created_at: date(activeVersion.created_at),
                  }}
                />
                <div className="strategy-contract">
                  <h3>{t('Definition')}</h3>
                  <RecordGrid value={activeVersion.definition.strategy} />
                  <code className="content-hash">{activeVersion.content_hash}</code>
                </div>
                <h3>
                  <GitBranch size={15} /> {t('Version history')}
                </h3>
                <DataTable
                  rows={project.data?.versions ?? []}
                  columns={[
                    { key: 'revision', label: 'Version', render: (row) => `v${row.revision}` },
                    {
                      key: 'kind',
                      label: 'Strategy',
                      render: (row) => row.definition.strategy.kind,
                    },
                    { key: 'created_at', label: 'Created', render: (row) => date(row.created_at) },
                    {
                      key: 'inspect',
                      label: 'Inspect',
                      render: (row) => (
                        <button className="table-link" onClick={() => setVersion(row)}>
                          {t('Inspect')}
                        </button>
                      ),
                    },
                  ]}
                />
              </>
            ) : (
              <div className="empty-state">
                <GitBranch size={28} />
                <h2>{t('Start with a hypothesis')}</h2>
                <p>{t('Create a hypothesis and its first immutable version.')}</p>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}
