import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Copy, GitBranch, Plus, Save } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Source } from '../api';
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
import { researchDraftKey, useResearchDraft } from '../lib/researchDraft';
import {
  authoringDraftFromVersion,
  newStrategyAuthoringDraft,
  normalizeAuthoringStrategy,
  readProgramRules,
  validStrategyAuthoringDraft,
  type StrategyAuthoringDraft,
} from '../lib/strategyAuthoringDraft';
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
  source,
  onResearch,
}: {
  source: Source;
  onResearch: (version: StrategyVersion) => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const session = useSession();
  const canOperate = canResearch(session?.user?.role);
  const userId = session?.user?.id ?? session?.user?.username ?? 'service';
  const scope = `${window.location.origin}:${userId}:${source}`;
  const draft = useResearchDraft(
    'strategy-authoring',
    userId,
    source,
    newStrategyAuthoringDraft,
    validStrategyAuthoringDraft,
  );
  const [name, setName] = draft.field('name');
  const [hypothesis, setHypothesis] = draft.field('hypothesis');
  const [strategy, setStrategy] = draft.field('strategy');
  const [product, setProduct] = draft.field('product');
  const [bar, setBar] = draft.field('bar');
  const [direction, setDirection] = draft.field('direction');
  const [leverage, setLeverage] = draft.field('leverage');
  const [programDraft, setProgramDraft] = draft.field('programDraft');
  const parent = draft.value.parent;
  const projects = useQuery({ queryKey: ['strategy-projects', scope], queryFn: proApi.strategies });
  type View = {
    scope: string;
    mode: 'library' | 'editor' | 'versions';
    selected?: string;
    version?: StrategyVersion;
  };
  const defaultView = (): View => ({
    scope,
    mode: draft.hasDraft ? 'editor' : 'library',
    selected: draft.hasDraft ? draft.value.parent?.projectId : undefined,
  });
  const [viewState, setViewState] = useState<View>(defaultView);
  const view = viewState.scope === scope ? viewState : defaultView();
  const updateView = (patch: Partial<View>) =>
    setViewState((old) => ({ ...(old.scope === scope ? old : defaultView()), ...patch, scope }));
  const editing = view.mode === 'editor';
  const libraryOpen = view.mode === 'library';
  const activeId = view.selected ?? projects.data?.items[0]?.id;
  const project = useQuery({
    queryKey: ['strategy-project', activeId, scope],
    queryFn: () => proApi.strategy(activeId!),
    enabled: !!activeId,
  });
  const [replacement, setReplacement] = useState<{
    scope: string;
    value: StrategyAuthoringDraft;
  }>();
  const pendingReplacement = replacement?.scope === scope ? replacement : undefined;
  const [savedNotice, setSavedNotice] = useState<{
    scope: string;
    revision: number;
    keptEdits: boolean;
  }>();
  const notice = savedNotice?.scope === scope ? savedNotice : undefined;
  const activeVersion =
    view.version?.project_id === activeId ? view.version : project.data?.versions?.[0];
  const current = useRef({ scope, draft });
  current.current = { scope, draft };
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  type Submission = {
    scope: string;
    storageKey: string;
    value: StrategyAuthoringDraft;
    fingerprint: string;
  };
  const programRules = strategy.kind === 'program' ? readProgramRules(programDraft) : undefined;
  const invalidProgram = strategy.kind === 'program' && !programRules;
  const save = useMutation({
    mutationFn: async ({ value }: Submission) => {
      const definition: StrategyDefinition = {
        schema_version: 1,
        product: value.product,
        bar: value.bar,
        strategy: {
          ...normalizeAuthoringStrategy(value.strategy),
          rules: value.strategy.kind === 'program' ? readProgramRules(value.programDraft)! : [],
        },
        direction: value.product === 'SPOT' ? 'long_only' : value.direction,
        leverage: String(value.product === 'SPOT' ? 1 : value.leverage),
      };
      if (value.parent)
        return proApi.createStrategyVersion(value.parent.projectId, {
          hypothesis: value.hypothesis,
          definition,
          parent_id: value.parent.id,
        });
      const created = await proApi.createStrategy({
        name: value.name,
        hypothesis: value.hypothesis,
        definition,
      });
      if (!created.version)
        throw new Error(
          t('The server did not return a saved strategy version. Your draft is retained.'),
        );
      return created.version!;
    },
    onSuccess: async (saved, submitted) => {
      if (mounted.current && current.current.scope === submitted.scope) {
        const latest = current.current.draft;
        const keptEdits = JSON.stringify(latest.value) !== submitted.fingerprint;
        setSavedNotice({ scope: submitted.scope, revision: saved.revision, keptEdits });
        if (!keptEdits) {
          latest.clear();
          setReplacement(undefined);
          setViewState({
            scope: submitted.scope,
            mode: 'versions',
            selected: saved.project_id,
            version: saved,
          });
        }
      } else {
        // A response may arrive after navigation or a user/source change. Only
        // remove the exact submitted snapshot, never a draft written since then.
        try {
          const raw = sessionStorage.getItem(submitted.storageKey);
          const stored = raw ? JSON.parse(raw) : undefined;
          if (stored?.schema === 1 && JSON.stringify(stored.value) === submitted.fingerprint)
            sessionStorage.removeItem(submitted.storageKey);
        } catch {
          /* Saving succeeded; unavailable local storage must not turn it into a failed version. */
        }
      }
      await qc.invalidateQueries({ queryKey: ['strategy-projects'] });
      await qc.invalidateQueries({ queryKey: ['strategy-project', saved.project_id] });
    },
  });
  const startDraft = (value: StrategyAuthoringDraft) => {
    draft.flush(value);
    updateView({ mode: 'editor' });
    setReplacement(undefined);
    setSavedNotice(undefined);
    save.reset();
  };
  const requestDraft = (value: StrategyAuthoringDraft) => {
    if (save.isPending) return;
    const hasMeaningfulDraft =
      draft.hasDraft && JSON.stringify(draft.value) !== JSON.stringify(newStrategyAuthoringDraft());
    if (hasMeaningfulDraft) setReplacement({ scope, value });
    else startDraft(value);
  };
  const edit = (existing?: StrategyVersion) => {
    requestDraft(
      existing
        ? authoringDraftFromVersion(existing, project.data?.name ?? '')
        : newStrategyAuthoringDraft(),
    );
  };
  const useRecipe = (id: string) => {
    const recipe = recipes.find((item) => item.id === id);
    if (!recipe) return;
    requestDraft({
      ...newStrategyAuthoringDraft(),
      name: recipe.name,
      hypothesis: recipe.hypothesis,
      strategy: { ...recipe.definition.strategy },
      product: recipe.definition.product,
      bar: recipe.definition.bar,
      direction: recipe.definition.direction,
      leverage: Number(recipe.definition.leverage),
      programDraft: recipe.definition.strategy.rules
        ? JSON.stringify(recipe.definition.strategy.rules, null, 2)
        : '',
    });
  };
  return (
    <>
      <PageHeading
        eyebrow="STRATEGY REGISTRY"
        title="Strategies"
        description="Economic hypotheses, immutable versions and traceable research."
      >
        <button
          className="button button-citrus"
          disabled={!canOperate || save.isPending}
          onClick={() => edit()}
        >
          <Plus size={14} />
          {t('New strategy')}
        </button>
      </PageHeading>
      {draft.hasDraft && (
        <div
          className="draft-status strategy-draft-status"
          role="region"
          aria-label={t('Strategy authoring draft')}
        >
          <div>
            <strong>
              {t(draft.restoredAt ? 'Strategy draft restored' : 'Unsaved strategy draft')}
            </strong>
            <p className="quiet-copy">
              {t(
                draft.problem === 'unavailable'
                  ? 'This draft is only in memory. Save a version before leaving this page.'
                  : 'Automatically kept in this browser tab for this user and source. Save version creates an immutable registry record.',
              )}
            </p>
            {parent && (
              <p className="quiet-copy">
                {parent.name} · {t('Parent version')} {parent.revision} · <code>{parent.id}</code>
              </p>
            )}
          </div>
          <div className="toolbar">
            {!editing && (
              <button className="text-button" onClick={() => updateView({ mode: 'editor' })}>
                {t('Resume strategy draft')}
              </button>
            )}
            <button
              type="button"
              className="text-button"
              disabled={save.isPending}
              onClick={() => {
                draft.clear();
                setReplacement(undefined);
                setSavedNotice(undefined);
                save.reset();
                updateView({ mode: activeId ? 'versions' : 'library' });
              }}
            >
              {t('Clear strategy draft')}
            </button>
          </div>
        </div>
      )}
      {draft.problem && (
        <p role="alert" className="inline-warning">
          {t(
            draft.problem === 'invalid'
              ? 'A damaged strategy draft was discarded. Saved strategy versions are unchanged.'
              : 'The strategy draft could not be saved in this browser. Keep this page open until you save a version.',
          )}
        </p>
      )}
      {pendingReplacement && (
        <div
          className="draft-status strategy-draft-replacement"
          role="region"
          aria-label={t('Replace strategy draft')}
        >
          <div>
            <p>
              {t(
                'You have an unsaved strategy draft. Replacing it discards its local edits; saved versions are unchanged.',
              )}
            </p>
            <p className="quiet-copy">
              {t('Current draft')}: {name || parent?.name || t('New strategy')} · {product} · {bar}
            </p>
            <p className="quiet-copy">
              {t('Replacement draft')}: {pendingReplacement.value.name || t('New strategy')} ·{' '}
              {pendingReplacement.value.product} · {pendingReplacement.value.bar} ·{' '}
              {pendingReplacement.value.strategy.kind}
            </p>
            {pendingReplacement.value.parent && (
              <p className="quiet-copy">
                {t('Parent version')} {pendingReplacement.value.parent.revision} ·{' '}
                <code>{pendingReplacement.value.parent.id}</code>
              </p>
            )}
            <p className="quiet-copy">
              {pendingReplacement.value.hypothesis || t('Empty hypothesis')}
            </p>
          </div>
          <div className="toolbar">
            <button
              type="button"
              className="button button-secondary"
              onClick={() => {
                setReplacement(undefined);
                updateView({ mode: 'editor' });
              }}
            >
              {t('Keep current draft')}
            </button>
            <button
              type="button"
              className="button button-ghost"
              disabled={save.isPending}
              onClick={() => startDraft(pendingReplacement.value)}
            >
              {t('Replace draft')}
            </button>
          </div>
        </div>
      )}
      {notice && (
        <p role="status" className="quiet-copy">
          {t('Version')} {notice.revision}{' '}
          {t(
            notice.keptEdits
              ? 'saved. Your newer edits remain in the local draft.'
              : 'saved. The submitted local draft was cleared.',
          )}
        </p>
      )}
      <div className="workspace-tabs" role="group" aria-label={t('Strategy view')}>
        <button
          className={`text-button${libraryOpen ? ' active' : ''}`}
          onClick={() => updateView({ mode: 'library' })}
        >
          {t('Research library')}
        </button>
        <button
          className={`text-button${!libraryOpen ? ' active' : ''}`}
          onClick={() => updateView({ mode: 'versions' })}
        >
          {t('My strategy versions')}
        </button>
      </div>
      {libraryOpen ? (
        <StrategyResearchLibrary canCreate={canOperate} onUse={useRecipe} />
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
                    updateView({ selected: item.id, version: undefined, mode: 'versions' });
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
                  if (!canOperate || save.isPending || invalidProgram) return;
                  const value = structuredClone(draft.value);
                  save.mutate({
                    scope,
                    storageKey: researchDraftKey(userId, source, 'strategy-authoring'),
                    value,
                    fingerprint: JSON.stringify(value),
                  });
                }}
              >
                <div className="section-heading">
                  <h2>{t(parent ? 'New version' : 'New strategy')}</h2>
                  <button
                    type="button"
                    className="button button-ghost"
                    onClick={() => updateView({ mode: 'versions' })}
                  >
                    {t('View saved versions')}
                  </button>
                </div>
                {!parent && (
                  <Field
                    label="Research starting point"
                    hint="Reference hypotheses with failure criteria; no investment edge is claimed."
                  >
                    <select
                      value=""
                      disabled={save.isPending}
                      onChange={(e) => useRecipe(e.target.value)}
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
                <StrategyFields
                  professional
                  value={strategy}
                  onChange={setStrategy}
                  programDraft={programDraft}
                  onProgramDraftChange={setProgramDraft}
                />
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
                {invalidProgram && (
                  <p role="alert" className="inline-warning">
                    {t('The rule program must contain one to four valid rules before saving.')}
                  </p>
                )}
                {save.isError && save.variables?.scope === scope && <ErrorBox error={save.error} />}
                <button
                  className="button button-citrus"
                  disabled={!canOperate || save.isPending || invalidProgram}
                >
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
                    disabled={!canOperate || save.isPending}
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
                        <button className="table-link" onClick={() => updateView({ version: row })}>
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
