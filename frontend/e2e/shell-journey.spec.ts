import { expect, test, type Page } from '@playwright/test';

type Item = Record<string, unknown>;
type FixtureState = {
  catalogFailure: boolean;
  emptyCatalog: boolean;
  taskFailure: boolean;
  packages: Item[];
  runs: Item[];
  portfolioRuns: Item[];
  releases: Item[];
  portfolioReleases: Item[];
  deployments: Item[];
  groups: Item[];
  mutations: string[];
  runsNextCursor?: string;
  account?: Item;
  analytics?: Item;
  accountReads: number;
  analyticsReads: number;
  holdAnalytics: boolean;
  releaseAnalytics?: () => void;
};

async function openFixture(
  page: Page,
  overrides: Partial<FixtureState> = {},
  entry: 'overview' | 'execution' = 'overview',
) {
  const state: FixtureState = {
    catalogFailure: false,
    emptyCatalog: false,
    taskFailure: false,
    packages: [],
    runs: [],
    portfolioRuns: [],
    releases: [],
    portfolioReleases: [],
    deployments: [],
    groups: [],
    mutations: [],
    accountReads: 0,
    analyticsReads: 0,
    holdAnalytics: false,
    ...overrides,
  };
  await page.addInitScript(() => {
    localStorage.setItem('tidebench:source', 'example');
    localStorage.setItem('tidebench:language', 'en');
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    const source = url.searchParams.get('source') ?? 'example';
    if (route.request().method() !== 'GET') {
      state.mutations.push(`${route.request().method()} ${path}`);
      return route.fulfill({
        status: 405,
        json: { error: { message: 'This fixture permits reads only.' } },
      });
    }
    const items = (rows: Item[]) => ({
      items: rows.filter((row) => !row.source || row.source === source),
    });
    const accountSnapshot = () =>
      state.account ?? {
        source,
        equity: '10000',
        available_cash: '10000',
        cash: '10000',
        positions: [],
        valuation_status: source === 'example' ? 'example' : 'fresh',
        economic_status: 'complete',
        execution_mode: 'local-paper',
        as_of: Date.now(),
      };
    let body: unknown = { items: [] };
    if (path === '/auth/status')
      body = {
        auth_required: true,
        setup_required: false,
        authenticated: true,
        user: { id: 'shell-fixture-admin', username: 'shell-fixture', role: 'admin' },
      };
    else if (path === '/system')
      body = {
        version: '0.11.0',
        execution: 'local-paper',
        market_region: 'test',
        capabilities: [],
        time: Date.now(),
      };
    else if (path === '/pro/catalog/instruments') {
      if (state.catalogFailure)
        return route.fulfill({
          status: 502,
          json: { error: { message: 'Isolated directory outage.' } },
        });
      const asset = source === 'example' ? 'ADA' : 'AVAX';
      const symbol = `${asset}-USDT${url.searchParams.get('inst_type') === 'SWAP' ? '-SWAP' : ''}`;
      body = {
        items: state.emptyCatalog
          ? []
          : [
              { inst_id: symbol, state: 'live' },
              { inst_id: 'OLD-USDT', state: 'suspend' },
            ],
      };
    } else if (path === '/pro/catalog/packages') body = items(state.packages);
    else if (path.startsWith('/pro/catalog/packages/'))
      body = state.packages.find((item) => item.id === path.split('/').at(-1));
    else if (path === '/pro/research/runs')
      body = { ...items(state.runs), next_cursor: state.runsNextCursor ?? null };
    else if (path.startsWith('/pro/research/runs/'))
      body = state.runs.find((item) => item.id === path.split('/').at(-1));
    else if (path === '/pro/research/portfolios') body = items(state.portfolioRuns);
    else if (path.startsWith('/pro/research/portfolios/'))
      body = state.portfolioRuns.find((item) => item.id === path.split('/').at(-1));
    else if (path === '/pro/execution/releases') body = items(state.releases);
    else if (path === '/pro/execution/portfolio-releases') {
      if (state.taskFailure)
        return route.fulfill({
          status: 502,
          json: { error: { message: 'Isolated task snapshot outage.' } },
        });
      body = items(state.portfolioReleases);
    } else if (path === '/pro/execution/deployments') body = items(state.deployments);
    else if (path === '/pro/execution/portfolios') body = items(state.groups);
    else if (path === '/pro/execution/account') {
      state.accountReads += 1;
      body = accountSnapshot();
    } else if (path === '/pro/execution/analytics') {
      state.analyticsReads += 1;
      if (state.holdAnalytics)
        await new Promise<void>((resolve) => {
          const previousRelease = state.releaseAnalytics;
          state.releaseAnalytics = () => {
            previousRelease?.();
            resolve();
          };
        });
      body = state.analytics ?? {
        source,
        status: 'available',
        as_of: Date.now(),
        summary: { gross_notional: '0', net_notional: '0' },
        assets: [],
        markets: [],
        positions: [],
        scenarios: [],
        issues: [],
        input_snapshot: { account: accountSnapshot() },
      };
    } else if (path === '/pro/execution/risk') body = { source, halted: false };
    else if (path === '/pro/execution/clock')
      body = { market_ts: Date.UTC(2025, 0, 1), speed: 0, paused: true, revision: 1 };
    else if (path === '/pro/ops')
      body = { health: { status: 'ok' }, jobs: [], workers: [], feeds: [], incidents: [] };
    else if (path === '/pro/market')
      body = {
        inst_id: url.searchParams.get('inst_id'),
        source,
        ts: Date.now(),
        last: '1',
        bid: '0.999',
        ask: '1.001',
        instrument: {
          inst_id: url.searchParams.get('inst_id'),
          lot_size: '1',
          min_size: '1',
          tick_size: '0.001',
          state: 'live',
        },
      };
    return route.fulfill({ json: body ?? { items: [] } });
  });
  await page.goto(entry === 'overview' ? '/' : '/#execution?source=example&view=analytics');
  if (entry === 'overview')
    await expect(page.getByRole('region', { name: 'Next actions', exact: true })).toBeVisible();
  else
    await expect(
      page.getByRole('heading', { name: 'Portfolio', exact: true, level: 1 }),
    ).toBeVisible();
  return state;
}

async function navigateFixture(page: Page, name: string) {
  const destination = page.getByRole('navigation').getByRole('button', { name, exact: true });
  await destination.waitFor({ state: 'attached' });
  const opener = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await opener.isVisible()) await opener.click();
  await destination.click();
}
const overviewMetric = (page: Page, label: string) =>
  page
    .locator('.trader-metrics .metric')
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator('.metric-value');
const snapshotAccount = (values: Item = {}): Item => ({
  source: 'example',
  execution_mode: 'local-paper',
  cash: '10000',
  available_cash: '10000',
  equity: '10000',
  used_margin: '0',
  maintenance_margin: '0',
  unrealized_pnl: '0',
  valuation_status: 'example',
  economic_status: 'complete',
  positions: [],
  as_of: Date.now(),
  ...values,
});
const snapshotReport = (account: Item, values: Item = {}): Item => ({
  source: 'example',
  status: 'available',
  as_of: account.as_of,
  summary: { gross_notional: '0', net_notional: '0', equity: account.equity },
  assets: [],
  markets: [],
  positions: [],
  scenarios: [],
  issues: [],
  input_snapshot: { account },
  ...values,
});

async function search(page: Page, symbol: string) {
  await page.getByRole('button', { name: 'Search markets, Command K' }).click();
  await page.getByLabel('Search market symbol', { exact: true }).fill(symbol);
  return page.getByRole('dialog', { name: 'Find a market', exact: true });
}

const researchConfig = {
  dataset_id: 'fixture-dataset',
  mode: 'single',
  strategy: {
    kind: 'sma_cross',
    fast: 12,
    slow: 26,
    rsi_period: 14,
    entry: '30',
    exit: '70',
    allocation: '.5',
  },
  direction: 'long_only',
  leverage: 1,
  initial_cash: '10000',
  fee_bps: '10',
  slippage_bps: '5',
  liquidation_fee_bps: '50',
  options: {},
};

test('returning from execution keeps money and positions paired with the exposure capture during refresh', async ({
  page,
}) => {
  const oldAccount = snapshotAccount({
    equity: '9987.78',
    cash: '7493.85',
    available_cash: '7493.85',
    unrealized_pnl: '-4.02',
    positions: [
      {
        inst_id: 'ADA-USDT',
        inst_type: 'SPOT',
        side: 'long',
        quantity: '2493.93',
        mark: '1',
        market_value: '2493.93',
        margin: '0',
        unrealized_pnl: '-4.02',
        as_of: Date.now(),
      },
    ],
  });
  const oldReport = snapshotReport(oldAccount, {
    summary: { gross_notional: '2493.93', net_notional: '2493.93', equity: '9987.78' },
    assets: [
      {
        asset: 'ADA',
        long_notional: '2493.93',
        short_notional: '0',
        gross_notional: '2493.93',
        net_notional: '2493.93',
        gross_share_pct: '100',
      },
    ],
  });
  const state = await openFixture(page, { account: oldAccount, analytics: oldReport }, 'execution');
  await expect(page.locator('.account-metrics')).toContainText('9,987.78');
  await expect(page.locator('.portfolio-analytics .analytics-metrics')).toContainText('2,493.93');
  const accountReads = state.accountReads;
  const analyticsReads = state.analyticsReads;
  const captured = snapshotAccount({ equity: '9995', cash: '9995', available_cash: '9995' });
  // The standalone account can be later than the analysis capture. It cannot be spliced into it.
  state.account = snapshotAccount({ equity: '9996', cash: '9996', available_cash: '9996' });
  state.analytics = snapshotReport(captured);
  state.holdAnalytics = true;
  const accountResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/pro/execution/account'),
  );
  await navigateFixture(page, 'Overview');
  expect((await (await accountResponse).json()).equity).toBe('9996');
  await expect.poll(() => state.accountReads).toBeGreaterThan(accountReads);
  await expect.poll(() => state.analyticsReads).toBeGreaterThan(analyticsReads);
  await expect(overviewMetric(page, 'Account equity')).toContainText('9,987.78');
  await expect(overviewMetric(page, 'Available cash')).toContainText('7,493.85');
  await expect(overviewMetric(page, 'Unrealized P&L')).toContainText('-4.02');
  await expect(overviewMetric(page, 'Gross exposure')).toContainText('2,493.93');
  await expect(page.locator('.overview-book')).toContainText('ADA-USDT');
  await expect(page.locator('.trader-metrics')).not.toContainText('9,996.00');
  state.holdAnalytics = false;
  state.releaseAnalytics?.();
  await expect(overviewMetric(page, 'Account equity')).toContainText('9,995.00');
  await expect(overviewMetric(page, 'Available cash')).toContainText('9,995.00');
  await expect(overviewMetric(page, 'Unrealized P&L')).toHaveText(/^\s*0\.00\s*USDT\s*$/);
  await expect(overviewMetric(page, 'Gross exposure')).toHaveText(/^\s*0\.00\s*USDT\s*$/);
  await expect(overviewMetric(page, 'Net exposure')).toHaveText(/^\s*0\.00\s*USDT\s*$/);
  await expect(page.locator('.overview-book')).toContainText('No open positions');
  await expect(page.locator('.overview-book')).not.toContainText('ADA-USDT');
  await expect(page.locator('.overview-exposures')).toContainText('No asset exposure');
  await expect(page.locator('.trader-metrics')).not.toContainText('9,996.00');
  await expect(
    page.getByText('Account and exposure values share one captured snapshot.', { exact: true }),
  ).toBeVisible();
  expect(state.mutations).toEqual([]);
});

for (const invalid of [
  'wrong report source',
  'wrong account source',
  'missing account capture',
  'malformed captured positions',
]) {
  test(`overview rejects ${invalid} and recovers only from a matching capture`, async ({
    page,
  }) => {
    const captured = snapshotAccount({ equity: '9995', cash: '9995', available_cash: '9995' });
    const invalidReport = snapshotReport(captured, {
      assets: [{ asset: 'FOREIGN_CAPTURE_ASSET', gross_notional: '0', net_notional: '0' }],
    });
    if (invalid === 'wrong report source') invalidReport.source = 'okx';
    if (invalid === 'wrong account source')
      invalidReport.input_snapshot = { account: { ...captured, source: 'okx' } };
    if (invalid === 'missing account capture') invalidReport.input_snapshot = {};
    if (invalid === 'malformed captured positions')
      invalidReport.input_snapshot = { account: { ...captured, positions: {} } };
    const state = await openFixture(page, { account: snapshotAccount(), analytics: invalidReport });
    await expect(overviewMetric(page, 'Account equity')).toContainText('10,000.00');
    await expect(overviewMetric(page, 'Gross exposure')).toHaveText(/^\s*—\s*USDT\s*$/);
    await expect(overviewMetric(page, 'Net exposure')).toHaveText(/^\s*—\s*USDT\s*$/);
    await expect(page.locator('.overview-economic-state')).toContainText('Monitoring incomplete');
    await expect(page.locator('.overview-economic-state')).not.toContainText(
      'No known unresolved conditions',
    );
    await expect(page.locator('.overview-exposures')).toContainText('Exposure unavailable');
    await expect(page.locator('.overview-exposures')).not.toContainText('FOREIGN_CAPTURE_ASSET');
    await expect(
      page.getByText('Account and exposure values share one captured snapshot.', { exact: true }),
    ).toHaveCount(0);
    state.analytics = snapshotReport(captured);
    await page.getByRole('button', { name: 'Refresh snapshot', exact: true }).click();
    await expect(overviewMetric(page, 'Account equity')).toContainText('9,995.00');
    await expect(overviewMetric(page, 'Gross exposure')).toHaveText(/^\s*0\.00\s*USDT\s*$/);
    await expect(page.locator('.overview-economic-state')).toContainText(
      'No known unresolved conditions',
    );
    expect(state.mutations).toEqual([]);
  });
}

for (const equityShape of ['null', 'missing']) {
  test(`captured ${equityShape} economic equity and unknown money stay unavailable despite model and cached account values`, async ({
    page,
  }) => {
    const captured = snapshotAccount({
      equity: null,
      cash: '5000',
      available_cash: null,
      used_margin: null,
      maintenance_margin: null,
      unrealized_pnl: null,
      equity_before_pending_funding: '8888',
      economic_status: 'funding_pending',
    });
    if (equityShape === 'missing') delete captured.equity;
    const state = await openFixture(page, {
      account: snapshotAccount(),
      analytics: snapshotReport(captured, {
        summary: {
          reported_equity: null,
          equity: '8888',
          available_cash: '8888',
          gross_notional: '0',
          net_notional: '0',
        },
      }),
    });
    for (const label of ['Account equity', 'Available cash', 'Used margin', 'Unrealized P&L']) {
      await expect(overviewMetric(page, label)).toHaveText(/^\s*—\s*USDT\s*$/);
    }
    await expect(page.locator('.trader-metrics')).toContainText('Maintenance margin: —');
    await expect(page.locator('.trader-metrics')).not.toContainText('8,888.00');
    await expect(page.locator('.trader-metrics')).not.toContainText('10,000.00');
    await expect(overviewMetric(page, 'Gross exposure')).toHaveText(/^\s*0\.00\s*USDT\s*$/);
    await expect(page.locator('.overview-economic-state')).toContainText('Action required');
    await expect(page.locator('.overview-funding-notice')).toContainText(
      'Funding settlement remains pending',
    );
    await expect(page.locator('.overview-book')).toContainText('No open positions');
    expect(state.mutations).toEqual([]);
  });
}

test('fresh workspace offers a concrete data-first action without suggesting eligibility', async ({
  page,
}) => {
  const state = await openFixture(page);
  const next = page.getByRole('region', { name: 'Next actions', exact: true });
  await expect(next).toContainText('Prepare your first research package');
  await expect(next).toContainText('Completed research does not establish paper eligibility');
  await next.getByRole('button', { name: 'Prepare research data', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Data library', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Research packages', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(state.mutations).toEqual([]);
});

test('catalog search finds an asset beyond the old five symbols and locks the selected perpetual into its draft', async ({
  page,
}) => {
  const state = await openFixture(page);
  const directory = await search(page, 'ADA');
  await expect(
    directory.getByRole('button', { name: 'Inspect ADA-USDT', exact: true }),
  ).toBeVisible();
  await expect(
    directory.getByRole('button', { name: 'Inspect ADA-USDT-SWAP', exact: true }),
  ).toBeVisible();
  await directory.getByLabel('Search market symbol', { exact: true }).fill('OLD');
  await expect(directory).toContainText('No matching markets.');
  await expect(
    directory.getByRole('button', { name: 'Inspect OLD-USDT', exact: true }),
  ).toHaveCount(0);
  await directory.getByLabel('Search market symbol', { exact: true }).fill('ADA');
  await directory.getByRole('button', { name: 'Inspect ADA-USDT-SWAP', exact: true }).click();
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ADA-USDT-SWAP');
  await expect(page.getByLabel('Market', { exact: true })).toBeVisible();
  const second = await search(page, 'ADA-USDT-SWAP');
  await second.getByRole('button', { name: 'Trade ADA-USDT-SWAP', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Portfolio', exact: true, level: 1 }),
  ).toBeVisible();
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ADA-USDT-SWAP');
  expect(state.mutations).toEqual([]);
});

test('market directory distinguishes an outage, a successful empty source and source-specific results', async ({
  page,
}) => {
  const state = await openFixture(page, { catalogFailure: true });
  const directory = await search(page, 'ADA');
  await expect(directory).toContainText('Isolated directory outage.');
  await expect(directory.getByRole('button', { name: 'Trade ADA-USDT', exact: true })).toHaveCount(
    0,
  );
  state.catalogFailure = false;
  await directory.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(
    directory.getByRole('button', { name: 'Inspect ADA-USDT', exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Search markets, Command K' })).toBeFocused();
  state.emptyCatalog = true;
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  const empty = await search(page, '');
  await expect(empty).toContainText('This source reports no available USDT markets.');
  state.emptyCatalog = false;
  await page.keyboard.press('Escape');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await search(page, 'ADA');
  await expect(
    directory.getByRole('button', { name: 'Inspect ADA-USDT', exact: true }),
  ).toBeVisible();
  await expect(
    directory.getByRole('button', { name: 'Inspect AVAX-USDT', exact: true }),
  ).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test('active work exposes actual progress and resumes the exact saved research after reload', async ({
  page,
}) => {
  const run = {
    id: 'current-ada-research',
    source: 'example',
    status: 'running',
    progress: 0.35,
    config: researchConfig,
    created_at: Date.now(),
  };
  const state = await openFixture(page, {
    packages: [
      {
        id: 'current-ada-package',
        source: 'example',
        inst_id: 'ADA-USDT',
        bar: '1H',
        start: Date.UTC(2025, 0, 1),
        end: Date.UTC(2025, 0, 31),
        status: 'running',
        ready: false,
        progress: 0.4,
        components: [],
        blockers: [],
      },
    ],
    runs: [{ ...run, id: 'previous-btc-research', status: 'completed', progress: 1 }, run],
  });
  const next = page.getByRole('region', { name: 'Next actions', exact: true });
  await expect(next.locator('[data-task="package:current-ada-package"]')).toContainText('40%');
  const task = next.locator('[data-task="run:current-ada-research"]');
  await expect(task).toContainText('35%');
  await task.getByRole('button', { name: 'Open research task', exact: true }).click();
  await expect(page.locator('.pro-result-panel')).toContainText('current-ada-');
  await expect(page).toHaveURL(/current-ada-research/);
  await page.reload();
  await expect(page.locator('.pro-result-panel')).toContainText('current-ada-');
  expect(state.mutations).toEqual([]);
});

test('an incomplete task snapshot blocks next-step claims and recovers on explicit retry', async ({
  page,
}) => {
  const state = await openFixture(page, { taskFailure: true });
  const next = page.getByRole('region', { name: 'Next actions', exact: true });
  await expect(next).toContainText('Task state is incomplete.');
  await expect(
    next.getByRole('button', { name: 'Prepare research data', exact: true }),
  ).toHaveCount(0);
  state.taskFailure = false;
  await next.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(next).toContainText('Prepare your first research package');
  expect(state.mutations).toEqual([]);
});

test('historical failures cannot bury active work and both task lists expose their remainder', async ({
  page,
}) => {
  const state = await openFixture(page, {
    runsNextCursor: 'older-fixture-history',
    runs: [
      ...Array.from({ length: 8 }, (_, i) => ({
        id: `failed-old-run-${i}`,
        source: 'example',
        status: 'failed',
        error: 'Historical fixture failure.',
        config: researchConfig,
        created_at: i,
      })),
      ...Array.from({ length: 8 }, (_, i) => ({
        id: `active-current-run-${i}`,
        source: 'example',
        status: 'running',
        config: researchConfig,
        progress: 0.1,
        created_at: Date.now() - i,
      })),
    ],
  });
  const next = page.getByRole('region', { name: 'Next actions', exact: true });
  const current = next.locator('ol.journey-list').first();
  await expect(current.locator('.journey-item')).toHaveCount(6);
  await expect(current).toContainText('active-current-run-0');
  await expect(current).not.toContainText('failed-old-run-');
  await next.getByRole('button', { name: 'Show all 8 tasks', exact: true }).click();
  await expect(current.locator('.journey-item')).toHaveCount(8);
  await expect(next).toContainText('Older runs may not be shown.');
  const history = next.locator('.journey-history');
  await history.locator('summary').click();
  await expect(history.locator('.journey-item')).toHaveCount(2);
  await expect(history.locator('.journey-item').first()).toContainText('failed-old-run-7');
  await history.getByRole('button', { name: 'Show all 8 failed tasks', exact: true }).click();
  await expect(history.locator('.journey-item')).toHaveCount(8);
  expect(state.mutations).toEqual([]);
});

test('next actions and market search follow the current source and interface language', async ({
  page,
}) => {
  const state = await openFixture(page, {
    packages: [
      {
        id: 'example-ready-input',
        source: 'example',
        inst_id: 'ADA-USDT',
        status: 'ready',
        ready: true,
        components: [],
        blockers: [],
      },
      {
        id: 'okx-ready-input',
        source: 'okx',
        inst_id: 'AVAX-USDT',
        status: 'ready',
        ready: true,
        components: [],
        blockers: [],
      },
    ],
  });
  const next = page.getByRole('region', { name: 'Next actions', exact: true });
  await expect(next).toContainText('example-ready-input');
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(next).toContainText('okx-ready-input');
  await expect(next).not.toContainText('example-ready-input');
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  const zhNext = page.getByRole('region', { name: '下一步操作', exact: true });
  await expect(zhNext).toContainText('研究输入已就绪');
  await expect(zhNext.getByRole('button', { name: '打开已准备输入', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '搜索市场，Command K', exact: true }).click();
  const directory = page.getByRole('dialog', { name: '搜索市场', exact: true });
  await directory.getByLabel('搜索交易对', { exact: true }).fill('AVAX');
  await expect(
    directory.getByRole('button', { name: '查看 AVAX-USDT-SWAP', exact: true }),
  ).toBeVisible();
  await expect(
    directory.getByRole('button', { name: '交易 AVAX-USDT-SWAP', exact: true }),
  ).toBeVisible();
  await expect(directory).toContainText('现货与 USDT 永续');
  expect(state.mutations).toEqual([]);
});

test('source and subtab navigation survives browser back, forward and reload', async ({ page }) => {
  const state = await openFixture(page);
  await expect(page).toHaveURL(/#overview\?source=example(?:&|$)/);
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await page.goBack();
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('example');
  await page
    .getByRole('region', { name: 'Next actions', exact: true })
    .getByRole('button', { name: 'Prepare research data', exact: true })
    .click();
  await expect(page.locator('#main-content')).toBeFocused();
  await page.getByRole('tab', { name: 'Raw datasets & imports', exact: true }).click();
  await expect(page).toHaveURL(/view=raw/);
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(page).toHaveURL(/source=okx/);
  await page.goBack();
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('example');
  await expect(
    page.getByRole('tab', { name: 'Raw datasets & imports', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  await page.goBack();
  await expect(page.getByRole('tab', { name: 'Research packages', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.goForward();
  await expect(
    page.getByRole('tab', { name: 'Raw datasets & imports', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  await page.reload();
  await expect(
    page.getByRole('tab', { name: 'Raw datasets & imports', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  const nav = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await nav.isVisible()) await nav.click();
  await page.getByRole('link', { name: 'tidebench.' }).click();
  await expect(page).toHaveURL(/#overview\?source=example(?:&|$)/);
  await page.goBack();
  await expect(
    page.getByRole('tab', { name: 'Raw datasets & imports', exact: true }),
  ).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('example');
  expect(state.mutations).toEqual([]);
});
