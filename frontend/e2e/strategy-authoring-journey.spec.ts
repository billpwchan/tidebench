import { expect, test, type Page } from '@playwright/test';

const firstProjectId = 'a'.repeat(32);
const secondProjectId = 'b'.repeat(32);
const latestVersionId = 'c'.repeat(32);
const olderVersionId = 'd'.repeat(32);
const createdProjectId = 'f'.repeat(32);
const createdVersionId = '1'.repeat(32);
const strategy = {
  kind: 'sma_cross',
  fast: 12,
  slow: 26,
  rsi_period: 14,
  entry: '30',
  exit: '60',
  allocation: '.25',
};
const definition = {
  schema_version: 1,
  product: 'SPOT',
  bar: '1H',
  strategy,
  direction: 'long_only',
  leverage: '1',
};
const latest = {
  id: latestVersionId,
  project_id: firstProjectId,
  revision: 2,
  hypothesis: 'The current immutable hypothesis has a measured rejection criterion.',
  definition,
  implementation: {},
  content_hash: 'a'.repeat(64),
  created_by: 'fixture',
  created_at: Date.UTC(2025, 1, 2),
};
const older = {
  ...latest,
  id: olderVersionId,
  revision: 1,
  hypothesis: 'The historical hypothesis is separate from current unsaved edits.',
  definition: { ...definition, strategy: { ...strategy, fast: 7, slow: 42 } },
  created_at: Date.UTC(2025, 1, 1),
};
const newName = 'Unfinished authoring';
const newHypothesis =
  'Retain this mechanism and its rejection criteria while preparing research inputs.';
const draftKey = (user: string, source = 'example') =>
  `tidebench:research-draft:v1:${encodeURIComponent(user)}:${source}:strategy-authoring`;

// Captured from StrategyDefinition.model_validate(...).record(), including all
// ProStrategyInput defaults and the nullable inactive side of each operand.
const backendProgramStrategy = {
  kind: 'program',
  fast: 12,
  slow: 26,
  rsi_period: 14,
  entry: '30',
  exit: '60',
  allocation: '0.25',
  window: 20,
  z_entry: '2',
  z_exit: '0.5',
  atr_period: 14,
  momentum_horizons: [42, 84, 168],
  momentum_entry: '0.5',
  vol_window: 42,
  max_bar_vol_pct: '5',
  efficiency_max: '0.35',
  reversion_trend_window: 84,
  stop_loss_pct: '0',
  take_profit_pct: '0',
  trailing_stop_pct: '0',
  max_holding_bars: 0,
  risk_per_trade_pct: '0',
  rules: [
    {
      conditions: [
        {
          left: { feature: 'fast_sma', constant: null },
          op: 'gt',
          right: { feature: null, constant: '3' },
        },
      ],
      signal: 1,
      tag: 'nullable-operands',
    },
  ],
};

async function navigate(page: Page, name: string) {
  const destination = page.getByRole('navigation').getByRole('button', { name, exact: true });
  await destination.waitFor({ state: 'attached' });
  const opener = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await opener.isVisible()) await opener.click();
  await destination.click();
}

async function openFixture(page: Page) {
  const state = {
    user: 'author-user-a',
    saveFailure: false,
    holdSave: false,
    releaseSave: undefined as (() => void) | undefined,
    registrations: [] as { path: string; body: any }[],
    otherMutations: [] as string[],
    projects: [
      {
        id: firstProjectId,
        name: 'Saved hypothesis',
        latest_revision: 2,
        version_count: 2,
        created_by: 'fixture',
        created_at: latest.created_at,
        versions: [latest, older],
      },
      {
        id: secondProjectId,
        name: 'Another saved hypothesis',
        latest_revision: 1,
        version_count: 1,
        created_by: 'fixture',
        created_at: older.created_at,
        versions: [{ ...older, id: 'e'.repeat(32), project_id: secondProjectId }],
      },
    ] as any[],
  };
  await page.addInitScript(() => {
    localStorage.setItem('tidebench:source', 'example');
    localStorage.setItem('tidebench:language', 'en');
  });
  // Every API request is intercepted; only immutable registry writes are
  // simulated. Research, order, controller and account mutations are rejected.
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1', '');
    const source = url.searchParams.get('source') ?? 'example';
    let body: unknown = { items: [] };
    if (request.method() !== 'GET') {
      if (
        request.method() === 'POST' &&
        (path === '/pro/strategies' || /^\/pro\/strategies\/[a-f0-9]{32}\/versions$/.test(path))
      ) {
        const submitted = request.postDataJSON();
        state.registrations.push({ path, body: submitted });
        if (state.holdSave)
          await new Promise<void>((resolve) => {
            state.releaseSave = resolve;
          });
        if (state.saveFailure)
          return route.fulfill({
            status: 422,
            json: {
              error: {
                code: 'fixture_validation',
                message: 'Isolated registry validation failure.',
              },
            },
          });
        const existing = state.projects.find((item) => path.includes(item.id));
        const saved = {
          ...latest,
          id: createdVersionId,
          project_id: existing?.id ?? createdProjectId,
          revision: existing ? existing.latest_revision + 1 : 1,
          parent_id: submitted.parent_id ?? null,
          hypothesis: submitted.hypothesis,
          definition: submitted.definition,
        };
        if (existing) {
          existing.versions.unshift(saved);
          existing.latest_revision = saved.revision;
          existing.version_count += 1;
          body = saved;
        } else {
          const project = {
            id: createdProjectId,
            name: submitted.name,
            latest_revision: 1,
            version_count: 1,
            created_by: state.user,
            created_at: latest.created_at,
            versions: [saved],
          };
          state.projects.unshift(project);
          body = { ...project, version: saved };
        }
      } else {
        state.otherMutations.push(`${request.method()} ${path}`);
        return route.fulfill({
          status: 405,
          json: { error: { message: 'Only isolated immutable strategy writes are permitted.' } },
        });
      }
    } else if (path === '/auth/status')
      body = {
        auth_required: true,
        setup_required: false,
        authenticated: true,
        user: { id: state.user, username: state.user, role: 'admin' },
      };
    else if (path === '/system')
      body = {
        version: '0.12.0',
        execution: 'local-paper',
        market_region: 'test',
        capabilities: [],
        time: Date.now(),
      };
    else if (path === '/pro/strategies') body = { items: state.projects };
    else if (/^\/pro\/strategies\/[a-f0-9]{32}$/.test(path))
      body = state.projects.find((item) => path.endsWith(item.id));
    else if (path === '/pro/research/runs') body = { items: [], next_cursor: null };
    else if (path === '/pro/execution/account')
      body = {
        source,
        equity: '10000',
        available_cash: '10000',
        cash: '10000',
        positions: [],
        valuation_status: 'example',
        economic_status: 'complete',
        execution_mode: 'local-paper',
        as_of: Date.now(),
      };
    else if (path === '/pro/execution/analytics')
      body = {
        status: 'available',
        summary: {},
        assets: [],
        markets: [],
        positions: [],
        scenarios: [],
        issues: [],
      };
    else if (path === '/pro/execution/risk') body = { source, halted: false };
    else if (path === '/pro/ops')
      body = { health: { status: 'ok' }, jobs: [], workers: [], feeds: [], incidents: [] };
    return route.fulfill({ json: body ?? { items: [] } });
  });
  await page.goto('/#strategies?source=example');
  await expect(
    page.getByRole('heading', { name: 'Strategies', exact: true, level: 1 }),
  ).toBeVisible();
  return state;
}

async function authorDraft(page: Page) {
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page.getByLabel('Strategy name', { exact: true }).fill(newName);
  await page.getByLabel('Economic hypothesis', { exact: true }).fill(newHypothesis);
  await page.getByLabel('Fast window', { exact: true }).fill('19');
  await page.getByLabel('Product', { exact: true }).selectOption('SWAP');
  await page.getByLabel('Direction', { exact: true }).selectOption('long_short');
  await page.getByLabel('Leverage', { exact: true }).fill('3');
}

test('a pristine new strategy accepts its first template immediately while authored template edits require explicit replacement', async ({
  page,
}) => {
  const state = await openFixture(page);
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page.getByLabel('Research starting point', { exact: true }).selectOption('trend');
  const replacement = page.getByRole('region', { name: 'Replace strategy draft', exact: true });
  await expect(replacement).toHaveCount(0);
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'Slow trend with a loss budget',
  );
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(
    /^Persistent price trends may offset turnover costs\./,
  );
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('20');
  await expect(page.getByLabel('Slow window', { exact: true })).toHaveValue('80');
  await page.getByLabel('Strategy name', { exact: true }).fill('Authored template draft');
  await page.getByLabel('Economic hypothesis', { exact: true }).fill(newHypothesis);
  await page.getByLabel('Research starting point', { exact: true }).selectOption('reversion');
  await expect(replacement).toContainText('Authored template draft');
  await expect(replacement).toContainText('Bounded RSI reversion');
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'Authored template draft',
  );
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(newHypothesis);
  await replacement.getByRole('button', { name: 'Keep current draft', exact: true }).click();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'Authored template draft',
  );
  await page.getByLabel('Research starting point', { exact: true }).selectOption('reversion');
  await replacement.getByRole('button', { name: 'Replace draft', exact: true }).click();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'Bounded RSI reversion',
  );
  await expect(page.getByLabel('Strategy', { exact: true })).toHaveValue('rsi_reversion');
  expect(state.registrations).toEqual([]);
  expect(state.otherMutations).toEqual([]);
});

test('strategy authoring survives navigation and refresh; historical inspection requires explicit draft replacement', async ({
  page,
}) => {
  const state = await openFixture(page);
  await authorDraft(page);
  await navigate(page, 'Data library');
  await navigate(page, 'Strategies');
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(newName);
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('19');
  await page.reload();
  await expect(page.getByText('Strategy draft restored', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(newHypothesis);
  await expect(page.getByLabel('Direction', { exact: true })).toHaveValue('long_short');
  await expect(page.getByLabel('Leverage', { exact: true })).toHaveValue('3');
  await page.getByRole('button', { name: 'View saved versions', exact: true }).click();
  await page
    .getByRole('row')
    .filter({ hasText: 'v1' })
    .getByRole('button', { name: 'Inspect', exact: true })
    .click();
  await expect(page.locator('.strategy-hypothesis')).toBeVisible();
  await expect(page.locator('.strategy-hypothesis')).toHaveText(older.hypothesis);
  await page.getByRole('button', { name: 'Create new version', exact: true }).click();
  const replacement = page.getByRole('region', { name: 'Replace strategy draft', exact: true });
  await expect(replacement).toContainText(newName);
  await expect(replacement).toContainText(olderVersionId);
  await expect(replacement).toContainText(older.hypothesis);
  await replacement.getByRole('button', { name: 'Keep current draft', exact: true }).click();
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('19');
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(newHypothesis);
  await page.getByRole('button', { name: 'View saved versions', exact: true }).click();
  await page.getByRole('button', { name: 'Create new version', exact: true }).click();
  await replacement.getByRole('button', { name: 'Replace draft', exact: true }).click();
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(
    older.hypothesis,
  );
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('7');
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('A revised historical hypothesis must still preserve its exact parent binding.');
  await page.reload();
  await expect(
    page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
  ).toContainText(olderVersionId);
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(
    page.getByText('saved. The submitted local draft was cleared.', { exact: false }),
  ).toBeVisible();
  expect(state.registrations).toHaveLength(1);
  expect(state.registrations[0].path).toBe(`/pro/strategies/${firstProjectId}/versions`);
  expect(state.registrations[0].body.parent_id).toBe(olderVersionId);
  expect(state.registrations[0].body.definition.strategy.fast).toBe(7);
  expect(state.otherMutations).toEqual([]);
});

test('restoring a revision draft highlights its exact second project and saves to that project and parent', async ({
  page,
}) => {
  const state = await openFixture(page);
  const projects = page.getByRole('complementary', { name: 'Strategy projects', exact: true });
  await page.getByRole('button', { name: 'My strategy versions', exact: true }).click();
  await projects.getByRole('button', { name: /^Another saved hypothesis/ }).click();
  await page.getByRole('button', { name: 'Create new version', exact: true }).click();
  const hypothesis =
    'An edited second-project revision must preserve its original project and exact parent.';
  await page.getByLabel('Economic hypothesis', { exact: true }).fill(hypothesis);
  await page.getByLabel('Fast window', { exact: true }).fill('18');
  await navigate(page, 'Data library');
  await navigate(page, 'Strategies');
  await page.reload();
  const draft = page.getByRole('region', { name: 'Strategy authoring draft', exact: true });
  await expect(draft).toContainText('Another saved hypothesis');
  await expect(draft).toContainText('e'.repeat(32));
  await expect(projects.getByRole('button', { name: /^Another saved hypothesis/ })).toHaveClass(
    /\bactive\b/,
  );
  await expect(projects.getByRole('button', { name: /^Saved hypothesis/ })).not.toHaveClass(
    /\bactive\b/,
  );
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(hypothesis);
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('18');
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(
    page.getByText('saved. The submitted local draft was cleared.', { exact: false }),
  ).toBeVisible();
  expect(state.registrations).toHaveLength(1);
  expect(state.registrations[0].path).toBe(`/pro/strategies/${secondProjectId}/versions`);
  expect(state.registrations[0].body.parent_id).toBe('e'.repeat(32));
  expect(state.registrations[0].body.hypothesis).toBe(hypothesis);
  expect(state.registrations[0].body.definition.strategy.fast).toBe(18);
  await expect(projects.getByRole('button', { name: /^Another saved hypothesis/ })).toHaveClass(
    /\bactive\b/,
  );
  expect(state.otherMutations).toEqual([]);
});

test('invalid rule JSON remains verbatim through navigation and refresh and cannot create a saved version', async ({
  page,
}) => {
  const state = await openFixture(page);
  await authorDraft(page);
  await page.getByLabel('Strategy', { exact: true }).selectOption('program');
  const raw = '[\n  { "tag": "unfinished", "conditions": [\n';
  await page.getByLabel('Ordered signal rules', { exact: true }).fill(raw);
  await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeDisabled();
  await navigate(page, 'Data library');
  await navigate(page, 'Strategies');
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(raw);
  await page.reload();
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(raw);
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  await page.getByLabel('Ordered signal rules', { exact: true }).fill('[1]');
  await page.reload();
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue('[1]');
  await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeDisabled();
  expect(state.registrations).toEqual([]);
  await page.getByRole('button', { name: 'Load trend filter example', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
  ).toHaveCount(0);
  expect(state.registrations).toHaveLength(1);
  expect(state.registrations[0].body.definition.strategy.rules[0].tag).toBe('trend-long');
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), draftKey(state.user)),
  ).toBeNull();
  expect(state.otherMutations).toEqual([]);
});

for (const variant of [
  { name: 'canonical backend defaults and nullable operands', strategy: backendProgramStrategy },
  {
    name: 'legacy omitted base controls and null optional fields',
    strategy: {
      kind: 'program',
      allocation: '.25',
      rules: backendProgramStrategy.rules,
      window: null,
      atr_period: null,
      stop_loss_pct: null,
      take_profit_pct: null,
      momentum_horizons: null,
    },
  },
]) {
  test(`a historical rule version with ${variant.name} retains its exact parent and unsaved parameters after reload`, async ({
    page,
  }) => {
    const state = await openFixture(page);
    state.projects[0].versions[0] = {
      ...latest,
      definition: { ...definition, strategy: variant.strategy },
    };
    await page.reload();
    await page.getByRole('button', { name: 'My strategy versions', exact: true }).click();
    await page.getByRole('button', { name: 'Create new version', exact: true }).click();
    await expect(page.getByLabel('Strategy', { exact: true })).toHaveValue('program');
    await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeEnabled();
    const raw = (
      await page.getByLabel('Ordered signal rules', { exact: true }).inputValue()
    ).replace('nullable-operands', 'unsaved-nullable-operands');
    await page.getByLabel('Ordered signal rules', { exact: true }).fill(raw);
    await page.getByLabel('Lookback window', { exact: true }).fill('36');
    await page.locator('details.strategy-exits > summary').click();
    await page.getByLabel('Stop loss %', { exact: true }).fill('2.5');
    await page
      .getByLabel('Economic hypothesis', { exact: true })
      .fill('A copied rule version retains its null operands and edited exit parameters.');
    await navigate(page, 'Data library');
    await navigate(page, 'Strategies');
    await page.reload();
    await expect(
      page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
    ).toContainText(latestVersionId);
    await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(raw);
    await expect(page.getByLabel('Lookback window', { exact: true })).toHaveValue('36');
    await page.locator('details.strategy-exits > summary').click();
    await expect(page.getByLabel('Stop loss %', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Stop loss %', { exact: true })).toHaveValue('2.5');
    await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Save version', exact: true }).click();
    await expect(
      page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
    ).toHaveCount(0);
    expect(state.registrations).toHaveLength(1);
    expect(state.registrations[0].body.parent_id).toBe(latestVersionId);
    const submitted = state.registrations[0].body.definition.strategy;
    expect(submitted.window).toBe(36);
    expect(submitted.stop_loss_pct).toBe('2.5');
    expect(submitted.fast).toBe(12);
    expect(submitted.rules[0].conditions[0].left.constant).toBeNull();
    expect(submitted.rules[0].conditions[0].right.feature).toBeNull();
    expect(submitted.rules[0].tag).toBe('unsaved-nullable-operands');
    expect(state.otherMutations).toEqual([]);
  });
}

test('strategy drafts are isolated by source and signed-in user; clear removes only the current draft', async ({
  page,
}) => {
  const state = await openFixture(page);
  await authorDraft(page);
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(
    page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page.getByLabel('Strategy name', { exact: true }).fill('Separate OKX draft');
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('A separate source-specific authoring hypothesis must remain distinct.');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(newName);
  state.user = 'author-user-b';
  await page.reload();
  await expect(
    page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page.getByLabel('Strategy name', { exact: true }).fill('Second user draft');
  state.user = 'author-user-a';
  await page.reload();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(newName);
  await page.getByRole('button', { name: 'Clear strategy draft', exact: true }).click();
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), draftKey(state.user)),
  ).toBeNull();
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue('Separate OKX draft');
  state.user = 'author-user-b';
  await page.goto('/#strategies?source=example');
  await page.reload();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue('Second user draft');
  expect(state.registrations).toEqual([]);
  expect(state.otherMutations).toEqual([]);
});

test('an invalid stored draft schema is rejected before filling controls and saved history remains accessible', async ({
  page,
}) => {
  const state = await openFixture(page);
  await page.evaluate((key) => {
    sessionStorage.setItem(
      key,
      JSON.stringify({
        schema: 1,
        userId: 'author-user-a',
        source: 'example',
        form: 'strategy-authoring',
        savedAt: Date.now(),
        value: {
          name: 'Tampered authoring',
          hypothesis: 'Must not enter any controlled fields.',
          parent: null,
          product: 'FUTURES',
          bar: '1H',
          direction: 'long_only',
          leverage: 1,
          programDraft: '',
          strategy: {
            kind: 'sma_cross',
            fast: 'wrong type',
            slow: 26,
            rsi_period: 14,
            entry: '30',
            exit: '60',
            allocation: '.25',
          },
        },
      }),
    );
  }, draftKey(state.user));
  await page.reload();
  await expect(
    page.getByRole('alert').filter({ hasText: 'A damaged strategy draft was discarded.' }),
  ).toBeVisible();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), draftKey(state.user)),
  ).toBeNull();
  await page.getByRole('button', { name: 'My strategy versions', exact: true }).click();
  await expect(page.locator('.strategy-hypothesis')).toBeVisible();
  await expect(page.locator('.strategy-hypothesis')).toHaveText(latest.hypothesis);
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveValue('12');
  expect(state.registrations).toEqual([]);
  expect(state.otherMutations).toEqual([]);
});

test('registry save failure retains editable authoring and a late successful save does not discard newer edits', async ({
  page,
}) => {
  const state = await openFixture(page);
  await authorDraft(page);
  state.saveFailure = true;
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(
    page.getByText('Isolated registry validation failure.', { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(newName);
  state.saveFailure = false;
  state.holdSave = true;
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect.poll(() => state.registrations.length).toBe(2);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('Newer unsaved changes must outlive the version save already in flight.');
  state.releaseSave!();
  await expect(
    page.getByText(/saved\. Your newer edits remain in the local draft\./),
  ).toBeVisible();
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(
    'Newer unsaved changes must outlive the version save already in flight.',
  );
  await page.reload();
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(
    'Newer unsaved changes must outlive the version save already in flight.',
  );
  expect(state.registrations[1].body.hypothesis).toBe(newHypothesis);
  expect(state.otherMutations).toEqual([]);
});

test('a save response arriving after leaving and resuming the page cannot clear the resumed draft edits', async ({
  page,
}) => {
  const state = await openFixture(page);
  await authorDraft(page);
  state.holdSave = true;
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect.poll(() => state.registrations.length).toBe(1);
  await navigate(page, 'Data library');
  await navigate(page, 'Strategies');
  await page.getByLabel('Strategy name', { exact: true }).fill('New authoring after navigation');
  const response = page.waitForResponse(
    (item) => item.request().method() === 'POST' && item.url().endsWith('/pro/strategies'),
  );
  state.releaseSave!();
  await (await response).finished();
  await expect
    .poll(() =>
      page.evaluate(
        (key) => JSON.parse(sessionStorage.getItem(key)!).value.name,
        draftKey(state.user),
      ),
    )
    .toBe('New authoring after navigation');
  await page.reload();
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'New authoring after navigation',
  );
  expect(state.otherMutations).toEqual([]);
});

test('blocked browser draft storage is visible while authoring remains editable until registry save succeeds', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.endsWith(':strategy-authoring'))
        throw new DOMException('Isolated quota fixture', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  });
  const state = await openFixture(page);
  await authorDraft(page);
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'The strategy draft could not be saved in this browser.' }),
  ).toBeVisible();
  await expect(page.getByLabel('Economic hypothesis', { exact: true })).toHaveValue(newHypothesis);
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Strategy authoring draft', exact: true }),
  ).toHaveCount(0);
  expect(state.registrations).toHaveLength(1);
  expect(state.registrations[0].body.hypothesis).toBe(newHypothesis);
  expect(state.otherMutations).toEqual([]);
});
