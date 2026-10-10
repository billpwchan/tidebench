import { expandResearchPolicy } from './desk-helpers';
import { expect, test, type Page } from '@playwright/test';

const HOUR = 3600000;
const end = Date.UTC(2025, 1, 1);
const strategy = {
  kind: 'sma_cross',
  fast: 12,
  slow: 26,
  rsi_period: 14,
  entry: '30',
  exit: '60',
  allocation: '.25',
};
const datasetId = 'a'.repeat(32);
const packageId = 'b'.repeat(32);
const versionId = 'c'.repeat(32);
const runId = 'd'.repeat(32);
const secondPackageId = 'e'.repeat(32);
// Match package_response() rather than a reduced handoff-only fixture.
const inputs = {
  source: 'example',
  dataset_id: datasetId,
  start_ts: end - 120 * HOUR,
  end_ts: end,
  package_id: packageId,
  package_manifest_hash: 'f'.repeat(64),
};
const datasets = [
  {
    id: datasetId,
    source: 'example',
    inst_id: 'BTC-USDT',
    kind: 'trade',
    bar: '1H',
    start: inputs.start_ts,
    end,
    content_hash: 'a'.repeat(64),
  },
];
const packages = [
  {
    id: packageId,
    source: 'example',
    inst_id: 'BTC-USDT',
    bar: '1H',
    start: inputs.start_ts,
    end,
    status: 'ready',
    ready: true,
    components: [],
    blockers: [],
    research_inputs: inputs,
  },
  {
    id: secondPackageId,
    source: 'example',
    inst_id: 'ETH-USDT',
    bar: '1H',
    start: inputs.start_ts,
    end,
    status: 'ready',
    ready: true,
    components: [],
    blockers: [],
    research_inputs: { ...inputs, package_id: secondPackageId },
  },
];
const version = {
  id: versionId,
  project_id: '1'.repeat(32),
  revision: 1,
  hypothesis: 'Persist the selected hypothesis while preparing data.',
  definition: {
    schema_version: 1,
    product: 'SPOT',
    bar: '1H',
    // StrategyDefinition.record() returns every canonical strategy field.
    strategy: {
      ...strategy,
      fast: 17,
      slow: 77,
      allocation: '0.25',
      atr_period: 14,
      efficiency_max: '0.35',
      max_bar_vol_pct: '5',
      max_holding_bars: 0,
      momentum_entry: '0.5',
      momentum_horizons: [42, 84, 168],
      reversion_trend_window: 84,
      risk_per_trade_pct: '0',
      rules: [],
      stop_loss_pct: '0',
      take_profit_pct: '0',
      trailing_stop_pct: '0',
      vol_window: 42,
      window: 20,
      z_entry: '2',
      z_exit: '0.5',
    },
    direction: 'long_only',
    leverage: '1',
  },
  implementation: {},
  content_hash: '1'.repeat(64),
  created_by: 'test',
  created_at: end,
};
const runConfig = {
  dataset_id: datasetId,
  start_ts: inputs.start_ts,
  end_ts: end,
  strategy: { ...strategy, fast: 8, slow: 40 },
  direction: 'long_only',
  leverage: 1,
  initial_cash: '12000',
  fee_bps: '20',
  slippage_bps: '7',
  liquidation_fee_bps: '50',
  mode: 'train_test',
  options: { train_fraction: 0.6, purge_bars: 4, grid: { fast: [8, 12], slow: [40, 60] } },
};

async function navigate(page: Page, name: string) {
  const destination = page.getByRole('navigation').getByRole('button', { name, exact: true });
  await destination.waitFor({ state: 'attached' });
  const opener = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await opener.isVisible()) await opener.click();
  await destination.click();
}
async function mockWorkspace(page: Page, options: { emptyDatasets?: boolean } = {}) {
  const state = {
    user: 'journey-user-a',
    datasetsReady: !options.emptyDatasets,
    createdRun: undefined as any,
    createdPortfolio: undefined as any,
  };
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    const method = route.request().method();
    let json: unknown = { items: [] };
    if (path === '/auth/status')
      json = {
        auth_required: true,
        setup_required: false,
        authenticated: true,
        user: { id: state.user, username: state.user, role: 'admin' },
      };
    else if (path === '/system')
      json = { version: '0.12.0', execution: 'local-simulation', build_sha: 'journey-test' };
    else if (path === '/pro/catalog/datasets')
      json = { items: state.datasetsReady ? datasets : [] };
    else if (path === '/pro/catalog/packages' && method === 'GET')
      json = { items: url.searchParams.get('source') === 'okx' ? [] : packages };
    else if (path === '/pro/catalog/packages/' + packageId) {
      state.datasetsReady = true;
      json = packages[0];
    } else if (path === '/pro/catalog/packages/' + secondPackageId) {
      state.datasetsReady = true;
      json = packages[1];
    } else if (path.endsWith('/research-inputs')) {
      state.datasetsReady = true;
      json = path.includes(secondPackageId) ? packages[1].research_inputs : inputs;
    } else if (path === '/pro/strategy-versions/' + versionId) json = version;
    else if (path === '/pro/research/runs' && method === 'GET')
      json = {
        items: [
          {
            id: runId,
            source: 'example',
            status: 'failed',
            config: runConfig,
            created_at: end,
            error: 'Captured research failure',
          },
        ],
        next_cursor: null,
      };
    else if (path === '/pro/research/runs/' + runId)
      json = {
        id: runId,
        source: 'example',
        status: 'failed',
        config: runConfig,
        created_at: end,
        error: 'Captured research failure',
      };
    else if (path === '/pro/research/runs' && method === 'POST') {
      state.createdRun = route.request().postDataJSON();
      json = {
        id: '2'.repeat(32),
        source: 'example',
        status: 'queued',
        config: state.createdRun,
        created_at: end,
      };
    } else if (path === '/pro/research/runs/' + '2'.repeat(32))
      json = {
        id: '2'.repeat(32),
        source: 'example',
        status: 'queued',
        config: state.createdRun,
        created_at: end,
      };
    else if (path === '/pro/portfolio-strategies' && method === 'POST')
      json = { id: '3'.repeat(32), version: { id: '4'.repeat(32), project_id: '3'.repeat(32) } };
    else if (path === '/pro/research/portfolios' && method === 'POST') {
      state.createdPortfolio = route.request().postDataJSON();
      json = {
        id: '5'.repeat(32),
        source: 'example',
        status: 'queued',
        config: state.createdPortfolio,
        manifest: {},
        created_at: end,
      };
    } else if (path === '/pro/research/portfolios/' + '5'.repeat(32))
      json = {
        id: '5'.repeat(32),
        source: 'example',
        status: 'queued',
        config: state.createdPortfolio,
        manifest: {},
        created_at: end,
      };
    await route.fulfill({ status: 200, json });
  });
  return state;
}

test('advanced draft survives data navigation and refresh, and viewing a result requires explicit configuration reuse', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.goto('/#research?source=example&view=advanced');
  await page.getByLabel('Mode', { exact: true }).selectOption('walk_forward');
  await page.getByLabel('Training bars', { exact: true }).fill('150');
  await page.getByLabel('Test bars', { exact: true }).fill('45');
  await page.getByLabel('Step bars', { exact: true }).fill('30');
  await page.getByLabel('Parameter selection', { exact: true }).selectOption('training');
  await page.getByLabel('Fast windows', { exact: true }).fill('9,15');
  await page.getByLabel('Fee', { exact: true }).fill('31');
  await navigate(page, 'Data library');
  await navigate(page, 'Research');
  await expect(page.getByLabel('Training bars', { exact: true })).toHaveValue('150');
  await expect(page.getByLabel('Fast windows', { exact: true })).toHaveValue('9,15');
  await page.reload();
  await expect(page.getByText(/Research draft restored/)).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('31');
  await page.getByRole('button', { name: runId.slice(0, 10), exact: true }).click();
  await expect(page).toHaveURL(/run=dddd/);
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('31');
  await expect(page.getByText('Captured research failure')).toBeVisible();
  await page.getByRole('button', { name: 'Use selected run configuration', exact: true }).click();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('20');
  await expect(page.getByLabel('Parameter selection', { exact: true })).toHaveValue('training');
  await expect(page.getByLabel('Fast windows', { exact: true })).toHaveValue('8,12');
});

test('strategy identity and edited costs survive choosing a prepared package before running', async ({
  page,
}) => {
  const state = await mockWorkspace(page, { emptyDatasets: true });
  await page.goto('/#research?source=example&view=advanced&version=' + versionId);
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await expect(page.locator('[aria-label="Bound strategy definition"]')).toContainText('17');
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveCount(0);
  await page.getByLabel('Fee', { exact: true }).fill('23');
  await page.getByLabel('Slippage', { exact: true }).fill('7');
  await page.getByRole('button', { name: 'Open Data library', exact: true }).click();
  await page
    .locator('.package-row')
    .filter({ hasText: 'BTC-USDT' })
    .getByRole('button', { name: 'Open in research', exact: true })
    .click();
  await expect(page).toHaveURL(/package=bbbb/);
  await expect(page).toHaveURL(/version=cccc/);
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('23');
  await expect(page.getByLabel('Slippage', { exact: true })).toHaveValue('7');
  // Exercise restoration of the complete backend DTO, including its source.
  await page.reload();
  await expect(page.getByText(/Research draft restored/)).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('23');
  await expect(page.getByLabel('Slippage', { exact: true })).toHaveValue('7');
  await expect(page.locator('[aria-label="Bound strategy definition"]')).toContainText('17');
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveValue(datasetId);
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  await expect.poll(() => state.createdRun?.strategy_version_id).toBe(versionId);
  expect(state.createdRun.package_id).toBe(packageId);
  expect(state.createdRun.fee_bps).toBe('23');
  expect(state.createdRun.slippage_bps).toBe('7');
});

test('portfolio data handoffs fill two legs without losing hypothesis, weights, or execution controls', async ({
  page,
}) => {
  const state = await mockWorkspace(page);
  await page.goto('/#research?source=example&view=portfolio');
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Study name', { exact: true }).fill('Two market draft');
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('Carry this hypothesis and capital controls through both data preparations.');
  await page.getByLabel('Notional weight %', { exact: true }).nth(0).fill('30');
  await expandResearchPolicy(page);
  await page.getByLabel('Maximum order notional (USDT)', { exact: true }).fill('1200');
  await page.getByRole('button', { name: 'Prepare data', exact: true }).click();
  await page
    .locator('.package-row')
    .filter({ hasText: 'BTC-USDT' })
    .getByRole('button', { name: 'Open in research', exact: true })
    .click();
  await expect(page).toHaveURL(/view=portfolio/);
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue('Two market draft');
  await expect(page.getByLabel('Package 1', { exact: true })).toHaveValue(packageId);
  await expect(page.getByLabel('Notional weight %', { exact: true }).nth(0)).toHaveValue('30');
  await page.getByRole('button', { name: 'Prepare data', exact: true }).click();
  await page
    .locator('.package-row')
    .filter({ hasText: 'ETH-USDT' })
    .getByRole('button', { name: 'Open in research', exact: true })
    .click();
  await expect(page.getByLabel('Package 2', { exact: true })).toHaveValue(secondPackageId);
  await page.reload();
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue('Two market draft');
  await expect(page.getByLabel('Maximum order notional (USDT)', { exact: true })).toHaveValue(
    '1200',
  );
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  await expect.poll(() => state.createdPortfolio?.name).toBe('Two market draft');
  expect(state.createdPortfolio.legs.map((leg: any) => leg.package_id)).toEqual([
    packageId,
    secondPackageId,
  ]);
  expect(state.createdPortfolio.legs[0].weight).toBe('0.3');
  expect(state.createdPortfolio.legs[0]).not.toHaveProperty('inst_id');
  expect(state.createdPortfolio.max_order_notional).toBe('1200');
});

test('drafts stay separate by source and user, and explicit clear removes the current draft', async ({
  page,
}) => {
  const state = await mockWorkspace(page);
  await page.goto('/#research?source=example&view=advanced');
  await page.getByLabel('Fee', { exact: true }).fill('33');
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('33');
  state.user = 'journey-user-b';
  await page.reload();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
  state.user = 'journey-user-a';
  await page.reload();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('33');
  await page.getByRole('button', { name: 'Clear research draft', exact: true }).click();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem('tidebench:research-draft:v1:journey-user-a:example:advanced'),
    ),
  ).toBeNull();
});

test('invalid and unavailable draft storage remains recoverable without changing saved research', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'tidebench:research-draft:v1:journey-user-a:example:advanced',
      JSON.stringify({
        schema: 1,
        userId: 'journey-user-a',
        source: 'example',
        form: 'advanced',
        savedAt: Date.now(),
        value: { strategy: null },
      }),
    );
  });
  await page.goto('/#research?source=example&view=advanced');
  await expect(
    page.getByText(
      'A damaged research draft was discarded. Saved research evidence is unchanged.',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith('tidebench:research-draft:'))
        throw new DOMException('Quota reached', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  });
  await page.getByLabel('Fee', { exact: true }).fill('41');
  await expect(
    page.getByText(
      'The research draft could not be saved. Keep this page open or export your configuration before leaving.',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('41');
  await expect(page.getByRole('button', { name: runId.slice(0, 10), exact: true })).toBeVisible();
});

test('malformed draft JSON is removed and reported as damaged content', async ({ page }) => {
  await mockWorkspace(page);
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'tidebench:research-draft:v1:journey-user-a:example:advanced',
      '{"schema":1,"value":',
    );
  });
  await page.goto('/#research?source=example&view=advanced');
  await expect(
    page.getByText(
      'A damaged research draft was discarded. Saved research evidence is unchanged.',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem('tidebench:research-draft:v1:journey-user-a:example:advanced'),
    ),
  ).toBeNull();
});

test('invalid rule-program text survives navigation and refresh until the research draft is cleared', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.goto('/#research?source=example&view=advanced');
  await page.getByLabel('Dataset', { exact: true }).selectOption(datasetId);
  await page.getByLabel('Strategy', { exact: true }).selectOption('program');
  const invalid = '[{"tag": "unfinished",';
  await page.getByLabel('Ordered signal rules', { exact: true }).fill(invalid);
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeDisabled();
  await navigate(page, 'Data library');
  await navigate(page, 'Research');
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(invalid);
  await page.reload();
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(invalid);
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Clear research draft', exact: true }).click();
  await expect(page.getByLabel('Strategy', { exact: true })).toHaveValue('sma_cross');
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveCount(0);
});

test('portfolio rule-program draft is local UI state and invalid text remains blocked after data preparation', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.goto('/#research?source=example&view=portfolio');
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Construction', { exact: true }).selectOption('independent_signals');
  await page
    .locator('.portfolio-study-leg')
    .first()
    .getByText('Signal policy', { exact: true })
    .click();
  await page
    .locator('.portfolio-study-leg')
    .first()
    .getByLabel('Strategy', { exact: true })
    .selectOption('program');
  const invalid = '[{"tag": "portfolio-unfinished",';
  await page.getByLabel('Ordered signal rules', { exact: true }).fill(invalid);
  await page.getByRole('button', { name: 'Prepare data', exact: true }).click();
  await page
    .locator('.package-row')
    .filter({ hasText: 'BTC-USDT' })
    .getByRole('button', { name: 'Open in research', exact: true })
    .click();
  await page
    .locator('.portfolio-study-leg')
    .first()
    .getByText('Signal policy', { exact: true })
    .click();
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(invalid);
  await expect(
    page.getByRole('button', { name: 'Run portfolio research', exact: true }),
  ).toBeDisabled();
  await page.reload();
  await page
    .locator('.portfolio-study-leg')
    .first()
    .getByText('Signal policy', { exact: true })
    .click();
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(invalid);
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
});

async function mockCancellation(page: Page, portfolio = false) {
  const workspace = await mockWorkspace(page);
  const state = {
    status: 'running',
    cancellation: '',
    recovery: undefined as Record<string, unknown> | undefined,
    checks: 0,
    rejectNext: true,
    hold: false,
    waiting: false,
    unblock: () => {},
  };
  const otherId = '6'.repeat(32);
  const config = portfolio
    ? {
        name: 'Cancellation book',
        hypothesis: 'Retain the exact trial record after cancelling this running portfolio study.',
        mode: 'fixed_weights',
        legs: packages.map((item) => ({
          package_id: item.id,
          weight: '.25',
          leverage: '1',
          direction: 'long_only',
          strategy,
          lifecycle_events: [],
        })),
        initial_cash: '10000',
        fee_bps: '10',
        slippage_bps: '5',
        rebalance_bars: 24,
        lookback: 20,
        top_k: 1,
        carry_threshold: '0',
        capital_pct: '50',
        max_gross_pct: 100,
        max_daily_loss_pct: 5,
        evaluation: 'full',
        train_pct: 70,
        embargo_bars: 1,
        execution_contract: 'reduce_group_v2_allowance',
        max_residual_pct: '2',
      }
    : runConfig;
  const record = () => ({
    id: runId,
    source: 'example',
    status: state.status,
    config,
    progress: 0.1,
    created_at: end,
    manifest: state.cancellation
      ? {
          cancellation: {
            state: state.cancellation,
            actor: 'journey-user-a',
            requested_at: end,
            recovery: state.recovery,
          },
        }
      : {},
  });
  const prefix = portfolio ? '/pro/research/portfolios' : '/pro/research/runs';
  await page.route('**/api/v1/pro/research/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '');
    if (path === prefix + '/' + runId + '/cancel') {
      state.checks += 1;
      if (state.rejectNext) {
        state.rejectNext = false;
        await route.fulfill({
          status: 409,
          json: {
            error: {
              code: 'cancel_conflict',
              message: 'Retry cancellation after a temporary worker conflict.',
            },
          },
        });
        return;
      }
      if (state.hold)
        await new Promise<void>((resolve) => {
          state.waiting = true;
          state.unblock = resolve;
        });
      state.cancellation = 'requested';
      await route.fulfill({ status: 202, json: record() });
    } else if (path === prefix && route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        json: {
          items: [
            record(),
            ...(portfolio
              ? []
              : [
                  {
                    id: otherId,
                    source: 'example',
                    status: 'failed',
                    config: runConfig,
                    created_at: end,
                    error: 'A different selected run failed.',
                  },
                ]),
          ],
          next_cursor: null,
        },
      });
    } else if (path === prefix + '/' + runId) await route.fulfill({ status: 200, json: record() });
    else if (!portfolio && path === prefix + '/' + otherId)
      await route.fulfill({
        status: 200,
        json: {
          id: otherId,
          source: 'example',
          status: 'failed',
          config: runConfig,
          created_at: end,
          error: 'A different selected run failed.',
        },
      });
    else await route.fallback();
  });
  return { state, workspace, otherId, config };
}

test('single research cancellation distinguishes retryable errors, requested cleanup, and confirmed cancellation', async ({
  page,
}) => {
  const { state, workspace } = await mockCancellation(page);
  await page.goto('/#research?source=example&view=advanced&run=' + runId);
  await page.getByLabel('Dataset', { exact: true }).selectOption(datasetId);
  await page.getByLabel('Fee', { exact: true }).fill('33');
  await page.getByRole('button', { name: 'Cancel research', exact: true }).click();
  await expect(
    page.getByText('Retry cancellation after a temporary worker conflict.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Cancel research', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Cancellation requested', exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      'Cancellation requested. The worker is still stopping; this run remains active until cleanup completes.',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Research cancelled', exact: true })).toHaveCount(
    0,
  );
  state.status = 'cancelled';
  state.cancellation = 'confirmed';
  await expect(
    page.getByRole('heading', { name: 'Research cancelled', exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('33');
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  await expect.poll(() => workspace.createdRun?.fee_bps).toBe('33');
  await expect(page).toHaveURL(/run=2222/);
});

test('portfolio cancellation preserves the saved trial and exposes confirmed recovery through a new study', async ({
  page,
}) => {
  const { state, workspace } = await mockCancellation(page, true);
  await page.goto('/#research?source=example&view=portfolio&run=' + runId);
  await page.getByRole('button', { name: 'Cancel portfolio research', exact: true }).click();
  await expect(
    page.getByText('Retry cancellation after a temporary worker conflict.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Cancel portfolio research', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Cancellation requested', exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      'Cancellation requested. The worker is still stopping; this run remains active until cleanup completes.',
      { exact: true },
    ),
  ).toBeVisible();
  state.status = 'cancelled';
  state.cancellation = 'confirmed';
  await expect(
    page.getByText(
      'The worker has stopped this run. Its configuration and trial record remain saved; revise and research again to create a new trial.',
      { exact: true },
    ),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Revise & research', exact: true }).click();
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue('Cancellation book');
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  await expect.poll(() => workspace.createdPortfolio?.name).toBe('Cancellation book');
  await expect(page).toHaveURL(/run=5555/);
});

test('a late cancellation response updates its original run without replacing the selected result', async ({
  page,
}) => {
  const { state, otherId } = await mockCancellation(page);
  state.rejectNext = false;
  state.hold = true;
  await page.goto('/#research?source=example&view=advanced&run=' + runId);
  await page.getByRole('button', { name: 'Cancel research', exact: true }).click();
  await page.getByRole('button', { name: otherId.slice(0, 10), exact: true }).click();
  await expect(page).toHaveURL(/run=6666/);
  await expect(page.getByText('A different selected run failed.', { exact: true })).toBeVisible();
  await expect.poll(() => state.waiting).toBe(true);
  state.unblock();
  await expect.poll(() => state.cancellation).toBe('requested');
  await expect(page).toHaveURL(/run=6666/);
  await expect(page.getByText('A different selected run failed.', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Cancellation requested', exact: true }),
  ).toHaveCount(0);
});

for (const portfolio of [false, true]) {
  test(`${portfolio ? 'portfolio' : 'single'} cancellation with unknown previous-worker exit stays active and can recheck the same request`, async ({
    page,
  }) => {
    const { state, workspace } = await mockCancellation(page, portfolio);
    state.rejectNext = false;
    state.cancellation = 'requested';
    state.recovery = {
      state: 'blocked',
      reason: 'Previous worker launch or exit evidence is unavailable.',
    };
    await page.goto(
      '/#research?source=example&view=' + (portfolio ? 'portfolio' : 'advanced') + '&run=' + runId,
    );
    await expect(page.getByText('Cancellation needs exit evidence', { exact: true })).toBeVisible();
    await expect(
      page.getByText(
        "Cancellation is recorded, but the previous worker's exit cannot yet be verified. This run will not restart or publish a result.",
        { exact: true },
      ),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Recheck cancellation', exact: true }).click();
    await expect.poll(() => state.checks).toBe(1);
    await expect(
      page.getByRole('button', { name: 'Cancellation requested', exact: true }),
    ).toBeDisabled();
    expect(workspace.createdRun).toBeUndefined();
    expect(workspace.createdPortfolio).toBeUndefined();
    state.status = 'cancelled';
    state.cancellation = 'confirmed';
    state.recovery = { state: 'resolved' };
    await expect(page.getByText('Cancellation needs exit evidence', { exact: true })).toHaveCount(
      0,
    );
    if (portfolio)
      await expect(
        page.getByText(
          'The worker has stopped this run. Its configuration and trial record remain saved; revise and research again to create a new trial.',
          { exact: true },
        ),
      ).toBeVisible();
    else
      await expect(
        page.getByRole('heading', { name: 'Research cancelled', exact: true }),
      ).toBeVisible();
  });
}

test('advanced research follows the current task in DOM order when preparing a draft or reviewing a selected run', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.goto('/#research?source=example&view=advanced');
  const layout = page.locator('.pro-research-layout');
  const regions = layout.locator(':scope > [data-research-region]');
  await expect(layout).toHaveClass(/research-config-first/);
  await expect(regions.nth(0)).toHaveAttribute('data-research-region', 'configuration');
  await expect(regions.nth(1)).toHaveAttribute('data-research-region', 'results');
  await expect(layout.locator('.pro-result-panel')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Research configuration', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeVisible();
  await page.getByLabel('Fee', { exact: true }).fill('37');
  await page.getByLabel('Strategy', { exact: true }).selectOption('program');
  const invalid = '[{"tag":"unfinished-layout-test",';
  await page.getByLabel('Ordered signal rules', { exact: true }).fill(invalid);
  await page.getByRole('button', { name: runId.slice(0, 10), exact: true }).click();
  await expect(page).toHaveURL(/run=dddd/);
  await expect(layout).not.toHaveClass(/research-config-first/);
  await expect(regions.nth(0)).toHaveAttribute('data-research-region', 'results');
  await expect(regions.nth(1)).toHaveAttribute('data-research-region', 'configuration');
  await expect(page.getByText('Captured research failure', { exact: true })).toBeVisible();
  await expect(page.locator('#research-result-heading')).toBeFocused();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('37');
  await expect(page.getByLabel('Ordered signal rules', { exact: true })).toHaveValue(invalid);
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toBeDisabled();
});

test('an empty research history is a compact sidebar message while configuration comes first', async ({
  page,
}) => {
  await mockWorkspace(page);
  await page.route('**/api/v1/pro/research/runs?*', async (route) => {
    await route.fulfill({ status: 200, json: { items: [], next_cursor: null } });
  });
  await page.goto('/#research?source=example&view=advanced');
  await expect(page.locator('.research-run-empty')).toHaveText('No research runs');
  await expect(page.locator('.pro-research-main .empty-state')).toHaveCount(0);
  await expect(page.locator('.pro-result-panel')).toHaveCount(0);
  await expect(page.locator('.pro-research-layout > :first-child')).toHaveAttribute(
    'data-research-region',
    'configuration',
  );
});

test('a selected historical run shows its evaluated version and holdout independently of the current draft binding', async ({
  page,
}) => {
  await mockWorkspace(page);
  const evaluatedVersion = '9'.repeat(32);
  const holdout = '7'.repeat(32);
  const historicalRun = {
    id: runId,
    source: 'example',
    status: 'completed',
    created_at: end,
    config: { ...runConfig, strategy_version_id: evaluatedVersion, holdout_id: holdout },
    result: { metrics: { final_equity: '12100', fills: 0 }, equity: [], fills: [] },
  };
  await page.route(/\/api\/v1\/pro\/research\/runs(?:\/|\?|$)/, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/runs/' + runId))
      await route.fulfill({ status: 200, json: historicalRun });
    else if (pathname.endsWith('/runs') && route.request().method() === 'GET')
      await route.fulfill({ status: 200, json: { items: [historicalRun], next_cursor: null } });
    else await route.fallback();
  });
  await page.goto('/#research?source=example&view=advanced&version=' + versionId);
  await expect(page.locator('[aria-label="Bound strategy definition"]')).toContainText('17');
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveCount(0);
  await page.getByLabel('Fee', { exact: true }).fill('37');
  await page.getByLabel('Slippage', { exact: true }).fill('11');
  await page.getByLabel('Mode', { exact: true }).selectOption('walk_forward');
  await page.getByRole('button', { name: runId.slice(0, 10), exact: true }).click();
  await expect(page).toHaveURL(/run=dddd/);
  const identity = page.locator('.research-run-identity');
  await expect(identity.getByText('Run strategy version', { exact: true })).toBeVisible();
  await expect(identity.getByText(evaluatedVersion, { exact: true })).toBeVisible();
  await expect(identity.getByText('Run holdout', { exact: true })).toBeVisible();
  await expect(identity.getByText(holdout, { exact: true })).toBeVisible();
  await expect(identity).not.toContainText(versionId);
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await expect(
    page.locator('.prepared-input-note').filter({ hasText: 'Bound strategy version' }),
  ).toContainText(versionId.slice(0, 12));
  await expect(page.locator('[aria-label="Bound strategy definition"]')).toContainText('17');
  await expect(page.getByLabel('Fast window', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('37');
  await expect(page.getByLabel('Slippage', { exact: true })).toHaveValue('11');
  await expect(page.getByLabel('Mode', { exact: true })).toHaveValue('walk_forward');
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(identity.getByText('本次运行策略版本', { exact: true })).toBeVisible();
  await expect(identity.getByText('本次运行封存评估', { exact: true })).toBeVisible();
  await expect(identity).toContainText(evaluatedVersion);
  await expect(identity).toContainText(holdout);
});

test('an explicit portfolio run overrides a restored editor without replacing its draft, including reload, back and data return', async ({
  page,
}) => {
  const { state } = await mockCancellation(page, true);
  state.status = 'completed';
  const historicalUrl = '/#research?source=example&view=portfolio&run=' + runId;
  await page.goto(historicalUrl);
  const result = page.locator('.portfolio-study-result');
  const editor = page.locator('.portfolio-study-editor');
  await expect(
    result.getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Revise & research', exact: true }).click();
  await expect(page).not.toHaveURL(/run=/);
  await expect(editor).toBeVisible();
  await page.getByLabel('Study name', { exact: true }).fill('Keep my revised portfolio');
  await page.getByLabel('Fee (bps)', { exact: true }).fill('37');
  await expandResearchPolicy(page);
  await page.getByLabel('Maximum order notional (USDT)', { exact: true }).fill('1234');
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await page.reload();
  await expect(editor).toBeVisible();
  await page.goto(historicalUrl);
  await expect(
    result.getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await expect(editor).toHaveCount(0);
  await expect(page.getByRole('button', { name: '继续组合草稿', exact: true })).toBeVisible();
  const storedDraft = () =>
    page.evaluate(() => {
      const raw = sessionStorage.getItem(
        'tidebench:research-draft:v1:journey-user-a:example:portfolio',
      );
      return raw ? JSON.parse(raw).value : null;
    });
  expect(await storedDraft()).toMatchObject({
    editing: true,
    name: 'Keep my revised portfolio',
    fee: '37',
    maxOrder: '1234',
  });
  await page.reload();
  await expect(
    result.getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '继续组合草稿', exact: true }).click();
  await expect(page).not.toHaveURL(/run=/);
  await expect(editor).toBeVisible();
  expect(await storedDraft()).toMatchObject({
    name: 'Keep my revised portfolio',
    fee: '37',
    maxOrder: '1234',
  });
  await page.goBack();
  await expect(page).toHaveURL(/run=dddd/);
  await expect(
    result.getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: /Cancellation book/ }).click();
  await expect(
    result.getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '继续组合草稿', exact: true }).click();
  await page.getByRole('button', { name: '准备数据', exact: true }).click();
  await expect(page).toHaveURL(/#data/);
  await page.getByRole('button', { name: '返回研究草稿', exact: true }).click();
  await expect(page).not.toHaveURL(/run=/);
  await expect(editor).toBeVisible();
  expect(await storedDraft()).toMatchObject({
    editing: true,
    name: 'Keep my revised portfolio',
    fee: '37',
    maxOrder: '1234',
  });
});

test('a late portfolio revision response cannot replace a new draft or change its selection', async ({
  page,
}) => {
  const { state, config } = await mockCancellation(page, true);
  state.status = 'completed';
  const portfolioVersion = '8'.repeat(32);
  (config as Record<string, unknown>).portfolio_version_id = portfolioVersion;
  let requested = false;
  let release = () => {};
  await page.route('**/api/v1/pro/portfolio-versions/' + portfolioVersion, async (route) => {
    requested = true;
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({
      status: 200,
      json: {
        id: portfolioVersion,
        project_id: '6'.repeat(32),
        definition: { execution_contract: 'reduce_group_v1', max_residual_pct: '3' },
      },
    });
  });
  await page.goto('/#research?source=example&view=portfolio&run=' + runId);
  await page.getByRole('button', { name: 'Revise & research', exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Study name', { exact: true }).fill('My newer draft');
  await page.getByLabel('Fee (bps)', { exact: true }).fill('47');
  const oldResponse = page.waitForResponse((response) =>
    response.url().endsWith('/portfolio-versions/' + portfolioVersion),
  );
  release();
  await (await oldResponse).finished();
  await expect(page).not.toHaveURL(/run=/);
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue('My newer draft');
  await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('47');
  await expect(page.getByLabel('Execution policy', { exact: true })).toHaveValue(
    'reduce_group_v2_allowance',
  );
});

async function heldApiResponse(
  page: Page,
  path: string,
  method: string,
  json: unknown | ((body: any) => unknown),
) {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const state = { requested: false, body: undefined as any };
  await page.route('**/api/v1/**', async (route) => {
    if (
      new URL(route.request().url()).pathname !== '/api/v1' + path ||
      route.request().method() !== method
    ) {
      await route.fallback();
      return;
    }
    state.body = method === 'GET' ? undefined : route.request().postDataJSON();
    state.requested = true;
    await gate;
    await route.fulfill({
      status: 200,
      json: typeof json === 'function' ? json(state.body) : json,
    });
  });
  return {
    state,
    async finish() {
      const response = page.waitForResponse(
        (item) =>
          new URL(item.url()).pathname === '/api/v1' + path && item.request().method() === method,
      );
      release();
      await (await response).finished();
    },
  };
}

async function prepareEditablePortfolio(page: Page, name = 'Original portfolio submission') {
  await page.goto('/#research?source=example&view=portfolio');
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Study name', { exact: true }).fill(name);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('Keep submitted evidence separate from changes made while the request is pending.');
  await page.getByLabel('Package 1', { exact: true }).selectOption(packageId);
  await page.getByLabel('Package 2', { exact: true }).selectOption(secondPackageId);
  await page.getByLabel('Fee (bps)', { exact: true }).fill('15');
}

const delayedProjectId = '3'.repeat(32);
const delayedVersionId = '4'.repeat(32);
const delayedPortfolioId = '5'.repeat(32);
const delayedDefinition = {
  bar: '1H',
  mode: 'fixed_weights',
  capital_pct: '50',
  rebalance_bars: 24,
  lookback: 20,
  top_k: 1,
  carry_threshold: '0',
  max_residual_pct: '3',
  execution_contract: 'reduce_group_v1',
  failure_policy: 'reduce_group',
  legs: packages.map((item) => ({
    inst_id: item.inst_id,
    weight: '.25',
    leverage: '1',
    direction: 'long_only',
    strategy,
  })),
};
const delayedProject = {
  id: delayedProjectId,
  name: 'Earlier saved portfolio',
  latest_revision: 1,
  version_count: 1,
  versions: [
    {
      id: delayedVersionId,
      project_id: delayedProjectId,
      revision: 1,
      hypothesis: 'An earlier saved hypothesis must not replace later edits.',
      definition: delayedDefinition,
    },
  ],
};

for (const change of ['new', 'edit'] as const) {
  test(`a delayed saved portfolio load preserves a ${change === 'new' ? 'new' : 'later edited'} draft`, async ({
    page,
  }) => {
    await mockWorkspace(page);
    await page.route('**/api/v1/pro/portfolio-strategies', async (route) => {
      if (route.request().method() === 'GET')
        await route.fulfill({ status: 200, json: { items: [delayedProject] } });
      else await route.fallback();
    });
    const held = await heldApiResponse(
      page,
      '/pro/portfolio-strategies/' + delayedProjectId,
      'GET',
      delayedProject,
    );
    await prepareEditablePortfolio(page, 'Keep this loading draft');
    await page.getByLabel('Saved portfolio', { exact: true }).selectOption(delayedProjectId);
    await expect.poll(() => held.state.requested).toBe(true);
    if (change === 'new')
      await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
    await page.getByLabel('Study name', { exact: true }).fill('My current portfolio draft');
    await page.getByLabel('Fee (bps)', { exact: true }).fill('41');
    await held.finish();
    await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
      'My current portfolio draft',
    );
    await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('41');
    await expect(page.getByLabel('Saved portfolio', { exact: true })).toHaveValue('');
    await expect(page).not.toHaveURL(/run=/);
    await page.reload();
    await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
      'My current portfolio draft',
    );
    await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('41');
  });
}

async function delayedPortfolioRun(page: Page) {
  const held = await heldApiResponse(page, '/pro/research/portfolios', 'POST', (body: any) => ({
    id: delayedPortfolioId,
    source: 'example',
    status: 'queued',
    created_at: end,
    config: body,
    manifest: {},
  }));
  await page.route('**/api/v1/pro/research/portfolios/' + delayedPortfolioId, async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        id: delayedPortfolioId,
        source: 'example',
        status: 'queued',
        created_at: end,
        config: held.state.body,
        manifest: {},
      },
    });
  });
  return held;
}

test('portfolio creation keeps its server project and original trial while a newer draft stays independent', async ({
  page,
}) => {
  await mockWorkspace(page);
  const saving = await heldApiResponse(page, '/pro/portfolio-strategies', 'POST', (body: any) => ({
    id: delayedProjectId,
    name: body.name,
    version: {
      id: delayedVersionId,
      project_id: delayedProjectId,
      revision: 1,
      hypothesis: body.hypothesis,
      definition: body.definition,
    },
  }));
  const queuing = await delayedPortfolioRun(page);
  await prepareEditablePortfolio(page);
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  await expect.poll(() => saving.state.requested).toBe(true);
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Study name', { exact: true }).fill('A newer independent portfolio');
  await page.getByLabel('Fee (bps)', { exact: true }).fill('39');
  await saving.finish();
  await expect.poll(() => queuing.state.requested).toBe(true);
  expect(queuing.state.body).toMatchObject({
    name: 'Original portfolio submission',
    fee_bps: '15',
    portfolio_version_id: delayedVersionId,
  });
  expect(queuing.state.body).not.toHaveProperty('request');
  expect(queuing.state.body).not.toHaveProperty('value');
  expect(queuing.state.body.legs[0]).not.toHaveProperty('programDraft');
  const beforeQueue = await page.evaluate(
    () =>
      JSON.parse(
        sessionStorage.getItem('tidebench:research-draft:v1:journey-user-a:example:portfolio')!,
      ).value,
  );
  expect(beforeQueue).toMatchObject({
    name: 'A newer independent portfolio',
    projectId: '',
    parentId: '',
    fee: '39',
  });
  await queuing.finish();
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
    'A newer independent portfolio',
  );
  await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('39');
  await expect(page).not.toHaveURL(/run=/);
  await expect(
    page.getByRole('button', { name: 'View submitted research', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'View submitted research', exact: true }).click();
  await expect(page).toHaveURL(/run=5555/);
  await expect(
    page
      .locator('.portfolio-study-result')
      .getByRole('heading', { name: 'Original portfolio submission', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Resume portfolio draft', exact: true }).click();
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
    'A newer independent portfolio',
  );
  await expect(page.getByLabel('Saved portfolio', { exact: true })).toHaveValue('');
});

test('portfolio creation preserves inputs edited while the original queue response is pending', async ({
  page,
}) => {
  await mockWorkspace(page);
  const queuing = await delayedPortfolioRun(page);
  await prepareEditablePortfolio(page);
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  await expect.poll(() => queuing.state.requested).toBe(true);
  await page.getByLabel('Study name', { exact: true }).fill('An edited in-flight portfolio');
  await page.getByLabel('Fee (bps)', { exact: true }).fill('46');
  await queuing.finish();
  expect(queuing.state.body).toMatchObject({
    name: 'Original portfolio submission',
    fee_bps: '15',
  });
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
    'An edited in-flight portfolio',
  );
  await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('46');
  await expect(page).not.toHaveURL(/run=/);
  await expect(
    page.getByRole('button', { name: 'View submitted research', exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
    'An edited in-flight portfolio',
  );
  await expect(page.getByLabel('Fee (bps)', { exact: true })).toHaveValue('46');
});

test('a delayed portfolio replay cannot take selection after visiting another result and returning to the same run', async ({
  page,
}) => {
  const { config } = await mockCancellation(page, true);
  const otherId = '6'.repeat(32);
  const original = {
    id: runId,
    source: 'example',
    status: 'completed',
    created_at: end,
    config,
    manifest: { input_artifact: { schema_version: 1 } },
    result: {
      execution_contract: config.execution_contract,
      execution_status: 'completed',
      metrics: { final_equity: '10000', total_return_pct: '0', max_drawdown_pct: '0', fills: 0 },
      equity: [],
      decisions: [],
      orders: [],
      ledger: [],
      execution_rejections: [],
    },
  };
  const other = {
    ...original,
    id: otherId,
    config: { ...config, name: 'Another saved portfolio' },
  };
  await page.route(/\/api\/v1\/pro\/research\/portfolios(?:\/|\?|$)/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    if (path === '/api/v1/pro/research/portfolios')
      await route.fulfill({ status: 200, json: { items: [original, other] } });
    else if (path === '/api/v1/pro/research/portfolios/' + runId)
      await route.fulfill({ status: 200, json: original });
    else if (path === '/api/v1/pro/research/portfolios/' + otherId)
      await route.fulfill({ status: 200, json: other });
    else if (path === '/api/v1/pro/research/portfolios/' + delayedPortfolioId)
      await route.fulfill({
        status: 200,
        json: { ...original, id: delayedPortfolioId, status: 'queued', result: null },
      });
    else await route.fallback();
  });
  const replay = await heldApiResponse(
    page,
    '/pro/research/portfolios/' + runId + '/replay',
    'POST',
    { ...original, id: delayedPortfolioId, status: 'queued', result: null },
  );
  await page.goto('/#research?source=example&view=portfolio&run=' + runId);
  await expect(
    page.locator('.portfolio-study-result').getByText('completed', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Equity', exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Replay frozen inputs', exact: true }),
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Replay frozen inputs', exact: true }).click();
  await expect.poll(() => replay.state.requested).toBe(true);
  await page.getByRole('button', { name: /Another saved portfolio/ }).click();
  await expect(page).toHaveURL(/run=6666/);
  await page.getByRole('button', { name: /Cancellation book/ }).click();
  await expect(page).toHaveURL(/run=dddd/);
  await replay.finish();
  await expect(page).toHaveURL(/run=dddd/);
  await expect(
    page
      .locator('.portfolio-study-result')
      .getByRole('heading', { name: 'Cancellation book', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'View submitted research', exact: true }),
  ).toBeVisible();
});

for (const change of ['edit', 'clear', 'source'] as const) {
  test(`a delayed advanced submission preserves the current ${change} intent and retains its submitted configuration`, async ({
    page,
  }) => {
    await mockWorkspace(page);
    const queued = await heldApiResponse(page, '/pro/research/runs', 'POST', (body: any) => ({
      id: '2'.repeat(32),
      source: 'example',
      status: 'queued',
      created_at: end,
      config: body,
    }));
    await page.goto('/#research?source=example&view=advanced');
    await page.getByLabel('Dataset', { exact: true }).selectOption(datasetId);
    await page.getByLabel('Fee', { exact: true }).fill('15');
    await page.getByRole('button', { name: 'Run research', exact: true }).click();
    await expect.poll(() => queued.state.requested).toBe(true);
    if (change === 'edit') await page.getByLabel('Fee', { exact: true }).fill('47');
    else if (change === 'clear')
      await page.getByRole('button', { name: 'Clear research draft', exact: true }).click();
    else await page.getByLabel('Market source', { exact: true }).selectOption('okx');
    await queued.finish();
    expect(queued.state.body.fee_bps).toBe('15');
    expect(queued.state.body).not.toHaveProperty('request');
    await expect(page).not.toHaveURL(/run=/);
    if (change === 'source') {
      await expect(page).toHaveURL(/source=okx/);
      await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('10');
      await page.getByLabel('Market source', { exact: true }).selectOption('example');
      await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('15');
      await expect(page).not.toHaveURL(/run=/);
    } else {
      await expect(page.getByLabel('Fee', { exact: true })).toHaveValue(
        change === 'edit' ? '47' : '10',
      );
      await expect(
        page.getByRole('button', { name: 'View submitted research', exact: true }),
      ).toBeVisible();
    }
  });
}

async function mockCompletedAdvancedResult(
  page: Page,
  record: { id: string } & Record<string, unknown>,
) {
  await page.route(/\/api\/v1\/pro\/research\/runs(?:\/|\?|$)/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') await route.fallback();
    else if (path === '/api/v1/pro/research/runs/' + record.id)
      await route.fulfill({ status: 200, json: record });
    else if (path === '/api/v1/pro/research/runs')
      await route.fulfill({ status: 200, json: { items: [record], next_cursor: null } });
    else await route.fallback();
  });
}

test('a delayed advanced replay cannot navigate after leaving research and returning to a different draft', async ({
  page,
}) => {
  await mockWorkspace(page);
  const completed = {
    id: runId,
    source: 'example',
    status: 'completed',
    created_at: end,
    config: runConfig,
    result: { metrics: { final_equity: '12000', fills: 0 }, equity: [], fills: [] },
  };
  await mockCompletedAdvancedResult(page, completed);
  const replay = await heldApiResponse(page, '/pro/research/runs/' + runId + '/replay', 'POST', {
    ...completed,
    id: '2'.repeat(32),
    status: 'queued',
    result: null,
  });
  await page.goto('/#research?source=example&view=advanced&run=' + runId);
  await page.getByLabel('Fee', { exact: true }).fill('37');
  await expect(
    page.locator('.pro-result-panel').getByText('completed', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Replay snapshot', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Replay snapshot', exact: true }).click();
  await expect.poll(() => replay.state.requested).toBe(true);
  await navigate(page, 'Data library');
  await navigate(page, 'Research');
  await page.getByLabel('Fee', { exact: true }).fill('49');
  await replay.finish();
  await expect(page).not.toHaveURL(/run=/);
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('49');
  await expect(page.locator('.pro-result-panel')).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('49');
});

test('a delayed advanced replay cannot replace candidate inspection after selector, fold, and scenario changes', async ({
  page,
}) => {
  await mockWorkspace(page);
  const candidate = {
    metrics: { total_return_pct: '7', final_equity: '12840', fills: 0 },
    equity: [],
    fills: [],
  };
  const completed = {
    id: runId,
    source: 'example',
    status: 'completed',
    created_at: end,
    config: { ...runConfig, mode: 'walk_forward' },
    result: {
      experiments: [
        { id: 'candidate-a', parameters: { strategy }, result: candidate },
        { id: 'candidate-b', parameters: { strategy }, result: candidate },
      ],
      folds: [{ id: 'fold-1', test_result: candidate }],
    },
  };
  await mockCompletedAdvancedResult(page, completed);
  const replay = await heldApiResponse(page, '/pro/research/runs/' + runId + '/replay', 'POST', {
    ...completed,
    id: '2'.repeat(32),
    status: 'queued',
    result: null,
  });
  await page.goto('/#research?source=example&view=advanced&run=' + runId);
  await expect(
    page.locator('.pro-result-panel').getByText('completed', { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('candidate-a');
  await expect(page.getByRole('button', { name: 'Replay snapshot', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Replay snapshot', exact: true }).click();
  await expect.poll(() => replay.state.requested).toBe(true);
  await page.getByLabel('Inspect experiment', { exact: true }).selectOption('candidate-b');
  await expect(page.getByRole('button', { name: 'Replay snapshot', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Folds', exact: true }).click();
  await page.getByRole('button', { name: 'fold-1', exact: true }).click();
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('fold-1:test');
  await page.getByRole('tab', { name: 'Scenarios', exact: true }).click();
  await page.getByRole('button', { name: 'candidate-a', exact: true }).click();
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('candidate-a');
  await replay.finish();
  await expect(page).toHaveURL(/run=dddd/);
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('candidate-a');
  await expect(page.getByRole('button', { name: 'Replay snapshot', exact: true })).toBeEnabled();
  await expect(
    page.getByRole('button', { name: 'View submitted research', exact: true }),
  ).toBeVisible();
});

for (const change of ['navigate', 'edit preparation'] as const) {
  test(`a delayed package handoff cannot replace the current ${change} intent`, async ({
    page,
  }) => {
    await mockWorkspace(page);
    const held = await heldApiResponse(
      page,
      '/pro/catalog/packages/' + packageId,
      'GET',
      packages[0],
    );
    await page.goto('/#research?source=example&view=advanced');
    await page.getByLabel('Fee', { exact: true }).fill('37');
    await page.getByRole('button', { name: 'Open Data library', exact: true }).click();
    const packageRow = page.locator('.package-row').filter({ hasText: 'BTC-USDT' });
    const open = packageRow.getByRole('button', { name: 'Open in research', exact: true });
    await open.click();
    await expect.poll(() => held.state.requested).toBe(true);
    if (change === 'navigate') {
      await navigate(page, 'Overview');
      await expect(
        page.getByRole('heading', { name: 'Trading overview', exact: true }),
      ).toBeVisible();
    } else {
      await page.getByLabel('Interval', { exact: true }).selectOption('4H');
      await expect(page.getByLabel('Interval', { exact: true })).toHaveValue('4H');
    }
    const currentUrl = page.url();
    await held.finish();
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(currentUrl);
    if (change === 'navigate') {
      await expect(
        page.getByRole('heading', { name: 'Trading overview', exact: true }),
      ).toBeVisible();
      await navigate(page, 'Data library');
    } else {
      await expect(page.getByLabel('Interval', { exact: true })).toHaveValue('4H');
      await expect(open).toBeEnabled();
    }
    // A fresh explicit handoff still works after discarding the obsolete receipt.
    await open.click();
    await expect(page).toHaveURL(/#research/);
    await expect(page).toHaveURL(/package=bbbb/);
    await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('37');
    await expect(page.getByLabel('Dataset', { exact: true })).toHaveValue(datasetId);
  });
}
