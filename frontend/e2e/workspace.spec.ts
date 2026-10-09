import { expect, test as base, type Page, type APIRequestContext } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const test = base.extend({
  request: async ({ context }, use) => {
    await use(context.request);
  },
});

const credentials = {
  username: 'browserqa',
  password: 'isolated-browser-password-123',
  display_name: 'Example workspace',
};
async function navigate(page: Page, name: string) {
  const opener = page.getByRole('button', { name: /^(Open navigation|展开导航)$/ });
  if (await opener.isVisible()) await opener.click();
  await page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
}
async function login(request: APIRequestContext) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  const response = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: status.setup_required
        ? credentials
        : { username: credentials.username, password: credentials.password },
    },
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()).csrf_token as string;
}
test.beforeEach(async ({ page, request }) => {
  const csrf = await login(request);
  await request.post('/api/v1/pro/execution/halt', {
    data: { source: 'example', active: false, reason: 'Isolated browser verification' },
    headers: { 'X-CSRF-Token': csrf },
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source') === 'okx')
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: 'test_outage',
            message: 'OKX is unavailable in this isolated browser test.',
          },
        },
      });
    else await route.continue();
  });
  await page.goto('/');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.locator('.example-notice')).toContainText('Synthetic example data.');
});

test('strategy version flows through research approval, activation and observed performance', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  const end = 1767225600000;
  const job = await (
    await request.post('/api/v1/pro/catalog/jobs', {
      headers,
      data: {
        source: 'example',
        inst_id: 'DOGE-USDT',
        kind: 'trade',
        bar: '1H',
        start: end - 96 * 3600000,
        end,
      },
    })
  ).json();
  let dataset = '';
  await expect
    .poll(async () => {
      const found = (await (await request.get('/api/v1/pro/catalog/jobs')).json()).items.find(
        (item: { id: string }) => item.id === job.id,
      );
      dataset = found?.dataset_id;
      return found?.status;
    })
    .toBe('completed');
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await navigate(page, 'Strategies');
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page.getByLabel('Research starting point', { exact: true }).selectOption('trend');
  await expect(page.getByLabel('Strategy name', { exact: true })).toHaveValue(
    'Slow trend with a loss budget',
  );
  await page
    .getByLabel('Strategy name', { exact: true })
    .fill(`Browser trend ${testInfo.project.name}`);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('A persistent trend should survive measured costs; invalidate when the holdout fails.');
  await page.getByLabel('Strategy', { exact: true }).selectOption('buy_hold');
  await page.getByText('Exits & loss budget', { exact: true }).click();
  await page.getByLabel('Maximum holding closes', { exact: true }).fill('2');
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  await expect(page.getByText('Immutable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Research this version', exact: true }).click();
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await page.getByLabel('Dataset', { exact: true }).selectOption(dataset);
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  const run = await (await queued).json();
  expect(run.config.strategy_version_id).toBeTruthy();
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/pro/research/runs/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await page.getByRole('button', { name: 'Review paper release', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review paper release', exact: true });
  await dialog
    .getByLabel('Release review', { exact: true })
    .fill('Reviewed finite-sample evidence and close-based holding limits; local simulation only.');
  for (const checkbox of await dialog.getByRole('checkbox').all()) await checkbox.check();
  await dialog.getByRole('button', { name: 'Approve paper release', exact: true }).click();
  const activation = page.waitForResponse(
    (r) => r.url().endsWith('/activate') && r.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Activate paper release', exact: true }).click();
  const release = await (await activation).json();
  expect(release.status).toBe('deployed');
  await dialog.getByRole('button', { name: 'Inspect deployment', exact: true }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(
              `/api/v1/pro/execution/deployments/${release.deployment_id}/decisions`,
            )
          ).json()
        ).items.length,
      { timeout: 30000 },
    )
    .toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Step 1 hour', exact: true }).click();
  await page.getByRole('button', { name: 'Step 1 hour', exact: true }).click();
  await expect
    .poll(
      async () => {
        const decisions = (
          await (
            await request.get(
              `/api/v1/pro/execution/deployments/${release.deployment_id}/decisions`,
            )
          ).json()
        ).items;
        return decisions.some(
          (item: { indicators: { exit_reason?: string } }) =>
            item.indicators.exit_reason === 'maximum_holding_closes',
        );
      },
      { timeout: 30000 },
    )
    .toBeTruthy();
  await page.getByRole('tab', { name: 'Forward performance', exact: true }).click();
  await expect(page.getByText('Decision journal', { exact: true })).toBeVisible();
  await page
    .getByLabel('Deployment decisions', { exact: true })
    .selectOption(release.deployment_id);
  await expect(page.getByText('maximum_holding_closes', { exact: true }).first()).toBeVisible();
  if (testInfo.project.name === 'desktop') {
    await mkdir('../docs/assets', { recursive: true });
    await page.screenshot({
      path: '../docs/assets/strategy-performance.png',
      animations: 'disabled',
    });
  }
  expect(errors).toEqual([]);
  await request.post(`/api/v1/pro/execution/deployments/${release.deployment_id}/stop`, {
    headers,
  });
  const account = await (await request.get('/api/v1/pro/execution/account?source=example')).json();
  const position = account.positions.find((p: { inst_id: string }) => p.inst_id === 'DOGE-USDT');
  if (position) {
    const closed = await request.post('/api/v1/pro/execution/orders', {
      headers: { ...headers, 'Idempotency-Key': `browser-cleanup-${release.id}` },
      data: {
        source: 'example',
        inst_id: 'DOGE-USDT',
        side: 'sell',
        quantity: position.quantity,
        leverage: 1,
        reduce_only: true,
        order_type: 'market',
        margin_mode: 'isolated',
      },
    });
    expect(closed.ok()).toBeTruthy();
  }
});

test('versioned research, saved results, replay and JSON export', async ({
  page,
  request,
}, testInfo) => {
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const end = 1767225600000;
  const job = await (
    await request.post('/api/v1/pro/catalog/jobs', {
      data: {
        source: 'example',
        inst_id: 'BTC-USDT',
        kind: 'trade',
        bar: '1H',
        start: end - 240 * 3600000,
        end,
      },
      headers: { 'X-CSRF-Token': csrf },
    })
  ).json();
  let dataset = '';
  await expect
    .poll(async () => {
      const jobs = (await (await request.get('/api/v1/pro/catalog/jobs')).json()).items;
      const found = jobs.find((j: { id: string }) => j.id === job.id);
      dataset = found?.dataset_id;
      return found?.status;
    })
    .toBe('completed');
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  await navigate(page, 'Research');
  await page.getByRole('button', { name: 'New research', exact: true }).click();
  await page.getByLabel('Dataset', { exact: true }).selectOption(dataset);
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  const run = await (await queued).json();
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/pro/research/runs/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await expect(page.getByRole('button', { name: 'Export JSON', exact: true })).toBeEnabled();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop') {
    await mkdir('../docs/assets', { recursive: true });
    await expect(page.locator('.pro-result-panel canvas').first()).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: '../docs/assets/research.png', animations: 'disabled' });
  }
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON', exact: true }).click();
  expect((await download).suggestedFilename()).toBe(`tidebench-research-${run.id}.json`);
  const replay = page.waitForResponse((r) =>
    r.url().endsWith(`/pro/research/runs/${run.id}/replay`),
  );
  await page.getByRole('button', { name: 'Replay snapshot', exact: true }).click();
  const replayed = await (await replay).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/runs/${replayed.id}`)).json()).status,
    )
    .toBe('completed');
  const a = await (await request.get(`/api/v1/pro/research/runs/${run.id}`)).json();
  const b = await (await request.get(`/api/v1/pro/research/runs/${replayed.id}`)).json();
  expect(b.result).toEqual(a.result);
  // Exercise the rendered OOS configuration, not a hand-crafted engine request.
  await page.getByLabel('Mode', { exact: true }).selectOption('train_test');
  await page.getByLabel('Parameter selection', { exact: true }).selectOption('training');
  await page.getByLabel('Fast windows', { exact: true }).fill('5,8');
  await page.getByLabel('Slow windows', { exact: true }).fill('20');
  const selectedRequest = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  const selectedRun = await (await selectedRequest).json();
  expect(selectedRun.config.options.grid).toEqual({ fast: [5, 8], slow: [20] });
  await expect
    .poll(async () => {
      const selectedResult = await (
        await request.get(`/api/v1/pro/research/runs/${selectedRun.id}`)
      ).json();
      return selectedResult.status;
    })
    .toBe('completed');
  const selectedResult = await (
    await request.get(`/api/v1/pro/research/runs/${selectedRun.id}`)
  ).json();
  expect(selectedResult.result.folds[0].training_experiments).toHaveLength(2);
  await page.getByLabel('Mode', { exact: true }).selectOption('walk_forward');
  await expect(page.getByLabel('Fast windows', { exact: true })).toHaveValue('5,8');
  await page.getByLabel('Parameter selection', { exact: true }).selectOption('fixed');
  await expect(page.getByLabel('Fast windows', { exact: true })).toHaveCount(0);
  expect(pageErrors).toEqual([]);
});

test('shared-capital portfolio study and one-use holdout governance', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60000);
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  const end = 1767225600000;
  const packages = [];
  for (const symbol of ['BTC-USDT', 'ETH-USDT']) {
    const response = await request.post('/api/v1/pro/catalog/packages', {
      headers,
      data: { source: 'example', inst_id: symbol, bar: '1H', start: end - 120 * 3600000, end },
    });
    expect(response.ok()).toBeTruthy();
    const created = await response.json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/pro/catalog/packages/${created.id}`)).json()).ready,
      )
      .toBeTruthy();
    packages.push(created.id);
  }
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page
    .getByLabel('Study name', { exact: true })
    .fill(`Shared capital ${testInfo.project.name}`);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill('A fixed two-market basket must pay all costs from a shared finite cash balance.');
  await page.getByLabel('Package 1', { exact: true }).selectOption(packages[0]);
  await page.getByLabel('Package 2', { exact: true }).selectOption(packages[1]);
  await page.getByLabel('Evaluation', { exact: true }).selectOption('train_test');
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/portfolios') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  const portfolio = await (await queued).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${portfolio.id}`)).json())
          .status,
    )
    .toBe('completed');
  await expect(
    page.getByRole('heading', { name: `Shared capital ${testInfo.project.name}`, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('Financial records below cover the independent test window.', { exact: false }),
  ).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON', exact: true }).click();
  expect((await download).suggestedFilename()).toContain(portfolio.id);
  await page.getByRole('tab', { name: 'Orders', exact: true }).click();
  await expect(
    page
      .locator('.portfolio-study-result')
      .getByRole('cell', { name: 'BTC-USDT', exact: true })
      .first(),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: `../docs/assets/portfolio-research${testInfo.project.name === 'mobile' ? '-mobile' : ''}.png`,
    animations: 'disabled',
  });

  const offset = testInfo.project.name === 'desktop' ? 200 : 1200;
  const holdoutEnd = end + offset * 3600000;
  const project = await (
    await request.post('/api/v1/pro/strategies', {
      headers,
      data: {
        name: `Sealed hypothesis ${testInfo.project.name}`,
        hypothesis: 'Reject a strategy whose unseen-window net return is nonpositive after costs.',
        definition: {
          schema_version: 1,
          product: 'SPOT',
          bar: '1H',
          strategy: { kind: 'buy_hold' },
          direction: 'long_only',
          leverage: '1',
        },
      },
    })
  ).json();
  expect(project.version.id).toBeTruthy();
  const job = await (
    await request.post('/api/v1/pro/catalog/jobs', {
      headers,
      data: {
        source: 'example',
        inst_id: 'OKB-USDT',
        kind: 'trade',
        bar: '1H',
        start: holdoutEnd - 48 * 3600000,
        end: holdoutEnd,
      },
    })
  ).json();
  let dataset = '';
  await expect
    .poll(async () => {
      const found = (await (await request.get('/api/v1/pro/catalog/jobs')).json()).items.find(
        (item: { id: string }) => item.id === job.id,
      );
      dataset = found?.dataset_id;
      return found?.status;
    })
    .toBe('completed');
  await page.getByRole('tab', { name: 'Research governance', exact: true }).click();
  await page.getByLabel('Strategy project', { exact: true }).selectOption(project.id);
  await page.getByRole('button', { name: 'Register holdout', exact: true }).click();
  await page
    .getByLabel('Holdout name', { exact: true })
    .fill(`Untouched window ${testInfo.project.name}`);
  await page.getByLabel('Strategy version', { exact: true }).selectOption(project.version.id);
  await page.getByLabel('Dataset', { exact: true }).selectOption(dataset);
  await page
    .getByLabel('Pre-registered benchmark', { exact: true })
    .fill('USDT cash; the buy-and-hold reference is also reported.');
  await page
    .getByLabel('Rejection plan', { exact: true })
    .fill(
      'Reject if net return is nonpositive or drawdown exceeds 10%; do not tune on this result.',
    );
  const sealing = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/holdouts') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Seal holdout', exact: true }).click();
  const seal = await (await sealing).json();
  expect(seal.status).toBe('sealed');
  await page.getByRole('button', { name: 'Evaluate once', exact: true }).click();
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await expect
    .poll(async () => {
      const list = (await (await request.get('/api/v1/pro/research/holdouts')).json()).items;
      return list.find((h: { id: string }) => h.id === seal.id)?.status;
    })
    .toBe('consumed');
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByText('已绑定策略版本', { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
});

test('versioned portfolio release, managed execution and reconciled owner contribution', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  const cleanup = async () => {
    const deployments = (
      await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
    ).items;
    for (const d of deployments.filter(
      (d: { status: string; inst_id: string }) =>
        d.status === 'running' && ['BTC-USDT', 'ETH-USDT'].includes(d.inst_id),
    ))
      expect(
        (await request.post(`/api/v1/pro/execution/deployments/${d.id}/stop`, { headers })).ok(),
      ).toBeTruthy();
    const account = await (
      await request.get('/api/v1/pro/execution/account?source=example')
    ).json();
    for (const p of account.positions.filter((p: { inst_id: string }) =>
      ['BTC-USDT', 'ETH-USDT'].includes(p.inst_id),
    )) {
      const response = await request.post('/api/v1/pro/execution/orders', {
        headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() },
        data: {
          source: 'example',
          inst_id: p.inst_id,
          side: Number(p.quantity) > 0 ? 'sell' : 'buy',
          quantity: String(Math.abs(Number(p.quantity))),
          leverage: p.leverage,
          reduce_only: true,
          order_type: 'market',
          margin_mode: 'isolated',
        },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
    }
  };
  await cleanup();
  const end = 1767225600000;
  const packages = [];
  for (const inst_id of ['BTC-USDT', 'ETH-USDT']) {
    const response = await request.post('/api/v1/pro/catalog/packages', {
      headers,
      data: { source: 'example', inst_id, bar: '1H', start: end - 120 * 3600000, end },
    });
    expect(response.ok()).toBeTruthy();
    const p = await response.json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/pro/catalog/packages/${p.id}`)).json()).ready,
      )
      .toBeTruthy();
    packages.push(p.id);
  }
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Portfolio starting point', { exact: true }).selectOption('basket');
  await expect(page.getByLabel('Study name', { exact: true })).toHaveValue(
    'BTC / ETH reserve-aware basket',
  );
  const title = `Managed basket ${testInfo.project.name}`;
  await page.getByLabel('Study name', { exact: true }).fill(title);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill(
      'A small two-market sleeve shares actual cash and retains traceable failed-leg controls.',
    );
  await page.getByLabel('Package 1', { exact: true }).selectOption(packages[0]);
  await page.getByLabel('Package 2', { exact: true }).selectOption(packages[1]);
  await page.getByLabel('Capital allocation %', { exact: true }).fill('20');
  await page.getByLabel('Evaluation', { exact: true }).selectOption('train_test');
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/portfolios') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  const run = await (await queued).json();
  expect(run.config.portfolio_version_id).toBeTruthy();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await page.getByRole('button', { name: 'Review portfolio release', exact: true }).click();
  const review = page.locator('.portfolio-release-review');
  await review
    .getByLabel('Review note', { exact: true })
    .fill(
      'Reviewed shared account capital, residual limits, sequential fills and failed compensation.',
    );
  for (const checkbox of await review.getByRole('checkbox').all()) await checkbox.check();
  await review.getByRole('button', { name: 'Approve portfolio release', exact: true }).click();
  const activating = page.waitForResponse(
    (r) => r.url().includes('/portfolio-releases/') && r.url().endsWith('/activate'),
  );
  await review
    .getByRole('button', { name: 'Activate managed paper portfolio', exact: true })
    .click();
  const released = await (await activating).json();
  expect(released.status).toBe('deployed');
  await review.getByRole('button', { name: 'Open managed portfolios', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Managed portfolios', exact: true }),
  ).toBeVisible();
  await expect
    .poll(
      async () => {
        const response = await (
          await request.get(`/api/v1/pro/execution/portfolios/${released.group_id}/batches`)
        ).json();
        return response.items[0]?.status;
      },
      { timeout: 40000 },
    )
    .toBe('completed');
  await expect(
    page.getByRole('heading', { name: 'Persisted execution batches', exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator('.managed-group-evidence')
      .getByRole('cell', { name: 'BTC-USDT', exact: true })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole('columnheader', { name: 'Frozen target', exact: true }),
  ).toBeVisible();
  const actualRow = page
    .locator('.managed-group-evidence')
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'BTC-USDT', exact: true }) })
    .first();
  await expect(actualRow.getByRole('cell').nth(3)).not.toHaveText('0');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await page.screenshot({
    path: `../docs/assets/managed-portfolios${testInfo.project.name === 'mobile' ? '-mobile' : ''}.png`,
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('tab', { name: 'Contribution', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Economic contribution', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Reconciled with account', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible();
  await page.getByRole('button', { name: title, exact: true }).click();
  await expect(
    page.getByRole('columnheader', { name: 'Virtual quantity', exact: true }),
  ).toBeVisible();
  const downloading = page.waitForEvent('download');
  await page
    .locator('.contribution-workspace')
    .getByRole('button', { name: 'Export CSV', exact: true })
    .click();
  expect((await downloading).suggestedFilename()).toContain('contributions');
  await page.screenshot({
    path: `../docs/assets/contributions${testInfo.project.name === 'mobile' ? '-mobile' : ''}.png`,
    fullPage: true,
    animations: 'disabled',
  });
  await page.getByRole('tab', { name: 'Managed portfolios', exact: true }).click();
  await page.getByRole('button', { name: 'Stop whole portfolio', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/execution/portfolios/${released.group_id}`)).json())
          .status,
    )
    .toBe('stopped');
  const inventory = await (
    await request.get('/api/v1/pro/execution/account?source=example')
  ).json();
  expect(
    inventory.positions.filter((p: { inst_id: string }) =>
      ['BTC-USDT', 'ETH-USDT'].includes(p.inst_id),
    ).length,
  ).toBe(2);
  await cleanup();
  await page.getByRole('tab', { name: 'Contribution', exact: true }).click();
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByRole('heading', { name: '经济贡献', exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
});

test('unified portfolio order preview, fill and persistent risk halt', async ({
  page,
  request,
}, testInfo) => {
  await navigate(page, 'Execution');
  await page.getByLabel('Quantity', { exact: false }).fill('0.001');
  await page.getByRole('button', { name: 'Preview order', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Order preview', exact: true })).toBeVisible();
  let releaseSubmit!: () => void;
  let submitted!: () => void;
  const submissionStarted = new Promise<void>((resolve) => {
    submitted = resolve;
  });
  const submissionGate = new Promise<void>((resolve) => {
    releaseSubmit = resolve;
  });
  await page.route('**/pro/execution/orders', async (route) => {
    if (route.request().method() === 'POST') {
      submitted();
      await submissionGate;
    }
    await route.continue();
  });
  const spotSubmission = page.waitForResponse(
    (r) => r.url().endsWith('/pro/execution/orders') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Submit order', exact: true }).click();
  await submissionStarted;
  await page.getByLabel('Quantity', { exact: false }).fill('0.002');
  releaseSubmit();
  expect((await spotSubmission).ok()).toBeTruthy();
  await expect(page.getByRole('status').filter({ hasText: 'Order submitted' })).toBeVisible();
  await expect(page.getByLabel('Quantity', { exact: false })).toHaveValue('0.002');
  await page.unroute('**/pro/execution/orders');
  await expect
    .poll(async () =>
      Number(
        (await (await request.get('/api/v1/pro/execution/account?source=example')).json()).cash,
      ),
    )
    .toBeLessThan(10000);
  await page.getByLabel('Product', { exact: true }).selectOption('SWAP');
  await page.getByLabel('Quantity', { exact: false }).fill('1');
  await page.getByLabel('Leverage', { exact: true }).fill('3');
  await page.getByRole('button', { name: 'Preview order', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Order preview', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Submit order', exact: true }).click();
  await expect(page.getByRole('cell').getByText('BTC-USDT-SWAP', { exact: true })).toBeVisible();
  await expect(page.getByText('Account equity', { exact: true })).toBeVisible();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop') {
    await expect(page.getByText('Loading workspace data…', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Loading market snapshot…', { exact: true })).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: '../docs/assets/workspace.png', animations: 'disabled' });
  }
  await page.getByRole('tab', { name: 'Exposure & scenarios', exact: true }).click();
  await expect(page.locator('.metric-label').filter({ hasText: /^Gross exposure$/ })).toBeVisible();
  await page.getByRole('tab', { name: 'Stress scenarios', exact: true }).click();
  await page.getByLabel('Scenario name', { exact: true }).fill('Browser stress');
  await page.getByLabel('Parallel price change (%)', { exact: true }).fill('-20');
  await page.getByRole('button', { name: 'Calculate scenario', exact: true }).click();
  await expect(page.getByText('Captured custom scenario', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Browser stress', exact: true })).toBeVisible();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop') {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: '../docs/assets/portfolio-risk.png',
      fullPage: true,
      animations: 'disabled',
    });
  }
  await navigate(page, 'Overview');
  await expect(page.getByRole('heading', { name: 'Trading overview', exact: true })).toBeVisible();
  await expect(page.getByRole('cell').getByText('BTC-USDT-SWAP', { exact: true })).toBeVisible();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop') {
    await expect(page.getByText('Loading account state…', { exact: true })).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: '../docs/assets/overview.png', animations: 'disabled' });
  }
  await navigate(page, 'Execution');
  await page.getByRole('tab', { name: 'Risk', exact: true }).click();
  await page.getByLabel('Reason', { exact: true }).fill('Browser verification halt');
  await page.getByRole('button', { name: 'Halt execution', exact: true }).click();
  await page.reload();
  await page.getByRole('tab', { name: 'Risk', exact: true }).click();
  await expect(page.getByText('Execution halted', { exact: true })).toBeVisible();
});

test('data download, operations, source failure and responsive layout', async ({ page }) => {
  await page.goto('/');
  const skip = page.getByRole('link', { name: 'Skip to content', exact: true });
  await expect(skip).toBeAttached();
  await page.keyboard.press('Tab');
  await expect(skip).toBeFocused();
  expect((await skip.boundingBox())!.y).toBeGreaterThanOrEqual(0);
  await page.keyboard.press('Enter');
  await navigate(page, 'Data library');
  await expect(page.getByRole('heading', { name: 'Data library', exact: true })).toBeVisible();
  await navigate(page, 'Operations');
  await page.getByRole('tab', { name: 'Backups', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create backup', exact: true })).toBeEnabled();
  await navigate(page, 'Overview');
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(
    page.getByRole('alert').filter({ hasText: 'OKX is unavailable' }).first(),
  ).toBeVisible();
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('example');
  const dimensions = await page.evaluate(() => ({
    width: innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.width + 1);
  await page.getByRole('button', { name: 'Search markets, Command K' }).click();
  await page.getByLabel('Search market symbol').fill('ETH');
  await page
    .getByRole('dialog', { name: 'Find a market' })
    .getByRole('button')
    .filter({ hasText: 'ETH' })
    .click();
  await page.getByRole('button', { name: /Market context/ }).click();
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ETH-USDT');
});

test('one-click perpetual research package preserves all input versions', async ({
  page,
  request,
}, testInfo) => {
  await navigate(page, 'Data library');
  await page.getByLabel('Product', { exact: true }).selectOption('SWAP');
  await page.getByLabel('Start (UTC)', { exact: true }).fill('2025-12-22T00:00');
  await page.getByLabel('End (UTC)', { exact: true }).fill('2026-01-01T00:00');
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/pro/catalog/packages') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Prepare research package', exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(202);
  const queued = await response.json();
  let ready;
  await expect
    .poll(async () => {
      ready = await (await request.get(`/api/v1/pro/catalog/packages/${queued.id}`)).json();
      return ready.status;
    })
    .toBe('ready');
  const row = page.locator('.package-row').filter({ hasText: ready.manifest_hash.slice(0, 12) });
  await expect(row.getByRole('button', { name: 'Open in research', exact: true })).toBeEnabled();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop') {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: '../docs/assets/data-packages.png', animations: 'disabled' });
  }
  await row.getByRole('button', { name: 'Open in research', exact: true }).click();
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveValue(
    ready.research_inputs.dataset_id,
  );
  await expect(page.getByLabel('Mark dataset', { exact: true })).toHaveValue(
    ready.research_inputs.mark_dataset_id,
  );
  await expect(page.getByLabel('Funding dataset', { exact: true })).toHaveValue(
    ready.research_inputs.funding_dataset_id,
  );
  const submitted = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  const run = await (await submitted).json();
  expect(run.config.package_manifest_hash).toBe(ready.manifest_hash);
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/pro/research/runs/${run.id}`)).json()).status,
    )
    .toBe('completed');
});

test('portfolio holdout captures a final contract, evaluates once and replays frozen evidence', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  const end = Date.parse(
    testInfo.project.name === 'desktop' ? '2026-05-01T00:00:00Z' : '2026-06-01T00:00:00Z',
  );
  const HOUR = 3600000;
  const packages: Record<string, string> = {};
  for (const inst_id of ['BTC-USDT', 'ETH-USDT']) {
    const response = await request.post('/api/v1/pro/catalog/packages', {
      headers,
      data: { source: 'example', inst_id, bar: '1H', start: end - 100 * HOUR, end },
    });
    expect(response.ok()).toBeTruthy();
    const p = await response.json();
    packages[inst_id] = p.id;
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/pro/catalog/packages/${p.id}`)).json()).ready,
      )
      .toBeTruthy();
  }
  const projectResponse = await request.post('/api/v1/pro/portfolio-strategies', {
    headers,
    data: {
      name: `Frozen basket ${testInfo.project.name}`,
      hypothesis:
        'Shared cash and measured costs must survive a final chronological interval without retuning.',
      definition: {
        capital_pct: '20',
        max_residual_pct: '3',
        rebalance_bars: 4,
        legs: ['BTC-USDT', 'ETH-USDT'].map((inst_id) => ({
          inst_id,
          weight: '.5',
          strategy: { kind: 'buy_hold' },
        })),
      },
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Research governance', exact: true }).click();
  await page.getByRole('tab', { name: 'Portfolios', exact: true }).click();
  await page.getByLabel('Portfolio project', { exact: true }).selectOption(project.id);
  await page.getByRole('button', { name: 'Register portfolio holdout', exact: true }).click();
  await page
    .getByLabel('Holdout name', { exact: true })
    .fill(`Final basket ${testInfo.project.name}`);
  await page.getByLabel('Portfolio version', { exact: true }).selectOption(project.version.id);
  for (const symbol of ['BTC-USDT', 'ETH-USDT'])
    await page.getByLabel(`Package · ${symbol}`, { exact: true }).selectOption(packages[symbol]);
  await page
    .getByLabel('UTC final start', { exact: true })
    .fill(new Date(end - 40 * HOUR).toISOString().slice(0, 16));
  await page
    .getByLabel('UTC final end', { exact: true })
    .fill(new Date(end).toISOString().slice(0, 16));
  await page.getByLabel('Maximum order notional (USDT)', { exact: true }).fill('1800');
  await page.getByLabel('Underlying asset gross limit (%)', { exact: true }).fill('80');
  await page.getByLabel('Minimum return versus cash %', { exact: true }).fill('0');
  await page.getByLabel('Maximum accepted drawdown %', { exact: true }).fill('10');
  await page
    .getByLabel('Rejection plan', { exact: true })
    .fill(
      'Reject when fixed return, drawdown or debt criteria fail. Do not tune the portfolio on this final window.',
    );
  const previewPromise = page.waitForResponse(
    (r) => r.url().endsWith('/portfolio-holdouts/preview') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Capture evaluation preview', exact: true }).click();
  const previewResponse = await previewPromise;
  expect(previewResponse.ok()).toBeTruthy();
  const preview = await previewResponse.json();
  expect(preview.plan.warmup_bars).toBe(20);
  expect(preview.plan.definition.failure_policy).toBe('reduce_group');
  expect(preview.plan.definition.execution_contract).toBe('reduce_group_v1');
  expect(preview.plan.definition.max_residual_pct).toBe('3');
  expect(preview.plan.test_config.max_order_notional).toBe('1800');
  expect(preview.plan.test_config.max_base_asset_gross_pct).toBe('80');
  expect(preview.input_hash).toMatch(/^[a-f0-9]{64}$/);
  await expect(
    page.getByRole('heading', { name: 'Review the captured contract', exact: true }),
  ).toBeVisible();
  const sealPromise = page.waitForResponse(
    (r) => r.url().endsWith('/portfolio-holdouts') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Seal captured portfolio', exact: true }).click();
  expect((await sealPromise).ok()).toBeTruthy();
  const evaluationPromise = page.waitForResponse(
    (r) => r.url().endsWith(`/${preview.id}/evaluate`) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Evaluate sealed portfolio', exact: true }).click();
  const run = await (await evaluationPromise).json();
  expect(run.config.failure_policy).toBe('reduce_group');
  expect(run.config.execution_contract).toBe('reduce_group_v1');
  expect(run.config.max_residual_pct).toBe('3');
  expect(run.config.max_order_notional).toBe('1800');
  expect(run.config.max_base_asset_gross_pct).toBe('80');
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await expect(page.getByText('One-use portfolio holdout', { exact: true })).toBeVisible();
  await expect(
    page.locator('.sealed-evaluation .status-bad').getByText('rejected', { exact: true }).first(),
  ).toBeVisible();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1') {
    await mkdir('../docs/assets', { recursive: true });
    await page.screenshot({
      path: `../docs/assets/portfolio-holdout-${testInfo.project.name}.png`,
      fullPage: true,
      animations: 'disabled',
    });
  }
  const repeat = await request.post(
    `/api/v1/pro/research/portfolio-holdouts/${preview.id}/evaluate`,
    { headers, data: { plan_hash: preview.plan_hash } },
  );
  expect((await repeat.json()).id).toBe(run.id);
  const replayPromise = page.waitForResponse(
    (r) => r.url().endsWith(`/${run.id}/replay`) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Replay frozen inputs', exact: true }).click();
  const replay = await (await replayPromise).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${replay.id}`)).json()).manifest
          .replay_verified,
    )
    .toBe(true);
  const evidence = await (
    await request.get(`/api/v1/pro/research/portfolio-governance/${project.id}`)
  ).json();
  expect(evidence.recorded_attempts).toBe(2);
  expect(evidence.primary_evaluations).toBe(1);
  expect(evidence.replay_attempts).toBe(1);
  const releasePreview = page.waitForResponse(
    (r) => r.url().endsWith('/portfolio-releases/preview') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Review portfolio release', exact: true }).click();
  expect((await (await releasePreview).json()).required_acknowledgements).toContain(
    'holdout_rejected',
  );
  const rejectionAcknowledgement = page.getByRole('checkbox', {
    name: 'This final holdout failed its pre-registered criteria. Paper deployment does not turn it into positive evidence.',
    exact: true,
  });
  await expect(rejectionAcknowledgement).toBeVisible();
  await expect(rejectionAcknowledgement).not.toBeChecked();
  await expect(page.locator('.error-box')).toHaveCount(0);
  expect(pageErrors).toEqual([]);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});

test('a failed view download preserves navigation and recovers after explicit reload', async ({
  page,
  request,
}) => {
  expect((await request.get('/')).headers()['cache-control']).toBe('no-cache');
  const chunk = '**/assets/Strategies-*.js';
  await page.route(chunk, (route) => route.abort('failed'));
  await navigate(page, 'Strategies');
  await expect(
    page.getByRole('heading', { name: 'This view could not be loaded', exact: true }),
  ).toBeVisible();
  await navigate(page, 'Overview');
  await expect(page.getByRole('heading', { name: 'Trading overview', exact: true })).toBeVisible();
  await expect(page.locator('.workspace-view-failure')).toHaveCount(0);
  await navigate(page, 'Strategies');
  await expect(page.getByRole('button', { name: 'Reload workspace', exact: true })).toBeVisible();
  await page.unroute(chunk);
  await page.getByRole('button', { name: 'Reload workspace', exact: true }).click();
  await expect(page.getByRole('button', { name: 'New strategy', exact: true })).toBeVisible();
  await expect(page.locator('.workspace-view-failure')).toHaveCount(0);
});

test('instrument observations expose causal coverage, exact exports and current-product scope', async ({
  page,
}) => {
  let releaseList!: () => void;
  let listFetched!: () => void;
  const listGate = new Promise<void>((resolve) => (releaseList = resolve));
  const staleListReady = new Promise<void>((resolve) => (listFetched = resolve));
  let held = false;
  // Return a genuine pre-capture list late, as happens on a slow connection.
  await page.route('**/pro/catalog/instrument-observations?*', async (route) => {
    const url = new URL(route.request().url());
    if (
      route.request().method() === 'GET' &&
      url.searchParams.get('source') === 'example' &&
      !held
    ) {
      held = true;
      const response = await route.fetch();
      listFetched();
      await listGate;
      await route.fulfill({ response });
    } else await route.fallback();
  });
  await navigate(page, 'Data library');
  await page.getByRole('tab', { name: 'Instrument evidence', exact: true }).click();
  await staleListReady;
  const desk = page.getByRole('region', { name: 'Instrument evidence', exact: true });
  const captured = page.waitForResponse(
    (r) => r.url().includes('/catalog/instrument-observations?') && r.request().method() === 'POST',
  );
  await desk.getByRole('button', { name: 'Capture current observation', exact: true }).click();
  const response = await captured;
  expect(response.status()).toBe(201);
  const first = await response.json();
  try {
    await expect(desk.getByLabel('Stored observation', { exact: true })).toHaveValue(first.id);
  } finally {
    releaseList();
  }
  await expect(desk.getByLabel('Stored observation', { exact: true })).toHaveValue(first.id);
  await expect(desk.getByText('Observed at this time', { exact: true })).toBeVisible();
  await desk.getByLabel('Filter observed instruments', { exact: true }).fill('BTC-USDT');
  await expect(desk.getByRole('cell', { name: 'BTC-USDT', exact: true })).toBeVisible();
  await desk.getByRole('button', { name: 'Inspect raw row', exact: true }).click();
  await expect(
    desk.getByRole('button', { name: 'Raw instrument row', exact: true }),
  ).toHaveAttribute('aria-expanded', 'true');
  const downloaded = page.waitForEvent('download');
  await desk.getByRole('button', { name: 'Export original observation', exact: true }).click();
  expect((await downloaded).suggestedFilename()).toBe(`instrument-observation-${first.id}.json`);
  await desk.getByLabel('Filter observed instruments', { exact: true }).fill('');
  await desk.getByLabel('Review time (UTC)', { exact: true }).fill('2020-01-01T00:00');
  await desk.getByRole('button', { name: 'Review known information', exact: true }).click();
  await expect(desk.getByText('No prior observation', { exact: true })).toBeVisible();
  await desk
    .getByLabel('Review time (UTC)', { exact: true })
    .fill(new Date(first.received_at + 7200000).toISOString().slice(0, 16));
  await desk.getByRole('button', { name: 'Review known information', exact: true }).click();
  await expect(desk.getByText('Unknown coverage', { exact: true })).toBeVisible();
  await expect(
    desk.getByText(
      'The last observation is older than the allowed age. Every member remains unknown.',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(desk.getByRole('cell', { name: 'Unknown', exact: true })).toHaveCount(5);
  await desk.getByRole('button', { name: 'Return to stored observation', exact: true }).click();
  await desk.getByLabel('Instrument product', { exact: true }).selectOption('SWAP');
  const swapCaptured = page.waitForResponse(
    (r) => r.url().includes('/catalog/instrument-observations?') && r.request().method() === 'POST',
  );
  await desk.getByRole('button', { name: 'Capture current observation', exact: true }).click();
  const swap = await (await swapCaptured).json();
  await expect(desk.getByLabel('Stored observation', { exact: true })).toHaveValue(swap.id);
  await expect(desk.getByRole('cell', { name: 'BTC-USDT-SWAP', exact: true })).toBeVisible();
  await expect(desk.getByRole('cell', { name: 'BTC-USDT', exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1')
    await page.screenshot({
      path: `../docs/assets/instrument-evidence-${test.info().project.name}.png`,
      fullPage: true,
      animations: 'disabled',
    });
});

test('research library loads executable hypotheses and bilingual cost-aware carry notes', async ({
  page,
}, testInfo) => {
  await navigate(page, 'Strategies');
  const library = page.getByRole('region', { name: 'Strategy research library' });
  await expect(library.getByRole('heading', { name: 'Multi-horizon momentum' })).toBeVisible();
  await expect(
    library.getByText('Moskowitz, Ooi & Pedersen · 2012', { exact: true }),
  ).toHaveAttribute('href', /aqr\.com/);
  const assetDir = process.env.TIDEBENCH_BROWSER_ASSET_DIR ?? '../docs/assets';
  await mkdir(assetDir, { recursive: true });
  if (testInfo.project.name === 'desktop')
    await page.screenshot({
      path: `${assetDir}/strategy-library-desktop.png`,
      fullPage: true,
      animations: 'disabled',
    });
  await library.getByRole('button', { name: 'Use this hypothesis', exact: true }).click();
  await expect(page.getByLabel('Strategy', { exact: true })).toHaveValue('ts_momentum');
  await expect(page.getByLabel('Interval', { exact: true })).toHaveValue('4H');
  await expect(page.getByLabel('Momentum horizon 3', { exact: true })).toHaveValue('168');
  const saved = page.waitForResponse(
    (r) => r.url().endsWith('/strategies') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  const record = await (await saved).json();
  expect(record.version.definition.strategy.momentum_horizons).toEqual([42, 84, 168]);
  await page.getByRole('button', { name: 'Research library', exact: true }).click();
  await library.getByRole('button', { name: /Cost-aware funding carry/ }).click();
  await expect(library.getByText(/positive 1 bp rate does not pass/)).toBeVisible();
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
  await page.reload();
  const zh = page.getByRole('region', { name: '策略研究库' });
  await zh.getByRole('button', { name: /考虑成本的资金费率配对/ }).click();
  await expect(zh.getByText(/正的 1 bp 费率仍不达标/)).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBeTruthy();
  if (testInfo.project.name === 'mobile')
    await page.screenshot({
      path: `${assetDir}/strategy-library-mobile-zh.png`,
      fullPage: true,
      animations: 'disabled',
    });
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'en'));
});

test('risk-budgeted portfolio recipe persists controls and explains causal position sizes', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const csrf = (await (await request.get('/api/v1/auth/status')).json()).csrf_token;
  const headers = { 'X-CSRF-Token': csrf };
  const end = 1767225600000;
  for (const symbol of ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']) {
    const response = await request.post('/api/v1/pro/catalog/packages', {
      headers,
      data: { source: 'example', inst_id: symbol, bar: '4H', start: end - 180 * 4 * 3600000, end },
    });
    expect(response.ok()).toBeTruthy();
    const created = await response.json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/pro/catalog/packages/${created.id}`)).json()).ready,
      )
      .toBeTruthy();
  }
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page.getByLabel('Portfolio starting point', { exact: true }).selectOption('risk-rotation');
  await expect(page.getByLabel('Risk estimation bars', { exact: true })).toHaveValue('84');
  await page.getByLabel('Maximum execution residual %', { exact: true }).fill('3');
  await page.getByLabel('Maximum order notional (USDT)', { exact: true }).fill('1750');
  await page.getByLabel('Underlying asset gross limit (%)', { exact: true }).fill('75');
  await page.getByLabel('Sleeve volatility target (%)', { exact: true }).fill('15');
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/portfolios') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  const response = await queued;
  expect(response.ok()).toBeTruthy();
  const run = await response.json();
  expect(run.config.mode).toBe('risk_momentum');
  expect(run.config.vol_target_pct).toBe('15');
  expect(run.config.failure_policy).toBe('reduce_group');
  expect(run.config.execution_contract).toBe('reduce_group_v1');
  expect(run.config.max_residual_pct).toBe('3');
  expect(run.config.max_order_notional).toBe('1750');
  expect(run.config.max_base_asset_gross_pct).toBe('75');
  const savedVersion = await (
    await request.get(`/api/v1/pro/portfolio-versions/${run.config.portfolio_version_id}`)
  ).json();
  expect(savedVersion.definition.failure_policy).toBe(run.config.failure_policy);
  expect(savedVersion.definition.execution_contract).toBe(run.config.execution_contract);
  expect(savedVersion.definition.max_residual_pct).toBe(run.config.max_residual_pct);
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await expect(
    page.getByRole('heading', { name: 'What sets the position size', exact: true }),
  ).toBeVisible();
  await page.getByLabel('Risk decision', { exact: true }).selectOption({ index: 0 });
  await expect(page.getByText('Stressed sleeve volatility', { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await page.locator('.portfolio-risk-evidence').scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `../docs/assets/portfolio-risk-budget-${testInfo.project.name}.png`,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'Revise & research', exact: true }).click();
  await expect(page.getByLabel('Sleeve volatility target (%)', { exact: true })).toHaveValue('15');
  await expect(page.getByLabel('Stress correlation (0–1)', { exact: true })).toHaveValue('0.75');
  await expect(page.getByLabel('Maximum execution residual %', { exact: true })).toHaveValue('3');
  await expect(page.getByLabel('Maximum order notional (USDT)', { exact: true })).toHaveValue(
    '1750',
  );
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
  await page.reload();
  await navigate(page, '策略研究');
  await page.getByRole('tab', { name: '组合研究', exact: true }).click();
  await expect(page.getByRole('heading', { name: '仓位规模的依据', exact: true })).toBeVisible();
  await expect(page.getByText('最大回撤', { exact: true })).toBeVisible();
  await expect(page.getByText('分配资金压力波动率', { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  if (testInfo.project.name === 'mobile') {
    await page.locator('.portfolio-risk-evidence').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: '../docs/assets/portfolio-risk-budget-mobile.png',
      animations: 'disabled',
    });
  }
});

test('trading desk surfaces unresolved group risk even when members and service health are normal', async ({
  page,
}, testInfo) => {
  const now = Date.now();
  let recovered = false;
  const manifest = (name: string, ids: string[] = []) => ({
    name,
    version_revision: 1,
    definition: { construction_mode: 'static', capital_pct: '40', bar: '1H' },
    legs: ids.map((id, index) => ({
      deployment_id: id,
      inst_id: index === 0 ? 'BTC-USDT' : 'ETH-USDT',
      weight: '.5',
      leverage: '1',
      direction: 'long_only',
    })),
  });
  const groupRows = () => [
    {
      id: 'normal-first',
      source: 'example',
      status: 'running',
      manifest: manifest('Normal first group'),
      created_at: now,
    },
    {
      id: 'blocked-group',
      source: 'example',
      status: recovered ? 'stopped' : 'compensating',
      manifest: manifest('Blocked recovery basket', ['member-btc', 'member-eth']),
      created_at: now - 86_400_000,
      last_error: 'Historical leg failure retained for audit',
      attention: recovered
        ? null
        : {
            phase: 'compensating',
            since: now - 4_200_000,
            as_of: now,
            error: 'Reduction could not complete; current inventory remains exposed.',
            inventory_notional: '1250',
            valuation_status: 'fresh',
            inventory: [{ inst_id: 'BTC-USDT', quantity: '.02', market_value: '1250' }],
            residual_notional: '1250',
            batch_id: 'blocked-batch',
            incident_id: 'group-incident',
          },
    },
    {
      id: 'preparation-group',
      source: 'example',
      status: recovered ? 'stopped' : 'running',
      manifest: manifest('History preparation basket'),
      created_at: now - 86_400_000,
      last_error: recovered ? 'Old history preparation failure' : 'Confirmed history is missing.',
      attention: null,
    },
    {
      id: 'old-stopped',
      source: 'example',
      status: 'stopped',
      manifest: manifest('Historical stopped basket'),
      created_at: now - 86_400_000,
      last_error: 'Old completed failure',
      attention: null,
    },
  ];
  const incidentRows = () => [
    {
      id: 'group-incident',
      kind: 'managed_portfolio',
      subject: 'blocked-group',
      status: recovered ? 'resolved' : 'acknowledged',
      first_seen: now - 4_200_000,
      last_seen: now,
      details: { source: 'example', error: 'Reduction could not complete.' },
      ack_actor: 'qaoperator',
      ack_at: now - 60_000,
      ack_reason: 'Investigating retained inventory; acknowledgement does not assert recovery.',
    },
    {
      id: 'other-source',
      kind: 'managed_portfolio',
      subject: 'other-source-group',
      status: 'open',
      first_seen: now - 60_000,
      last_seen: now,
      details: { source: 'okx', error: 'Other-source-only error' },
    },
    {
      id: 'resolved-old',
      kind: 'managed_portfolio',
      subject: 'old-stopped',
      status: 'resolved',
      first_seen: now - 86_400_000,
      last_seen: now,
      details: { source: 'example', error: 'Old resolved error' },
    },
    {
      id: 'shared-backup',
      kind: 'backup_failure',
      subject: 'workspace',
      status: recovered ? 'resolved' : 'open',
      first_seen: now - 60_000,
      last_seen: now,
      details: { error: 'Shared workspace backup failure' },
    },
  ];
  await page.route('**/api/v1/pro/ops', (route) =>
    route.fulfill({
      json: {
        health: { status: 'ok', checks: { database: 'ok', workers: 'ok' } },
        incidents: incidentRows(),
        feeds: [],
        jobs: [],
        workers: [],
        storage: {},
      },
    }),
  );
  await page.route('**/api/v1/pro/execution/portfolios?*', (route) =>
    route.fulfill({ json: { items: groupRows() } }),
  );
  const [accountFixture, analyticsFixture] = await Promise.all([
    page.request
      .get('/api/v1/pro/execution/account?source=example')
      .then((response) => response.json()),
    page.request
      .get('/api/v1/pro/execution/analytics?source=example')
      .then((response) => response.json()),
  ]);
  await page.route('**/api/v1/pro/execution/account?*', (route) =>
    route.fulfill({
      json: {
        ...accountFixture,
        equity: '10000',
        available_cash: recovered ? '10000' : '8750',
        cash: recovered ? '10000' : '8750',
        positions: recovered
          ? []
          : [
              {
                inst_id: 'BTC-USDT',
                inst_type: 'SPOT',
                side: 'long',
                quantity: '.02',
                mark: '62500',
                market_value: '1250',
                margin: '0',
                unrealized_pnl: '0',
                as_of: now,
              },
            ],
      },
    }),
  );
  await page.route('**/api/v1/pro/execution/analytics?*', (route) =>
    route.fulfill({
      json: {
        ...analyticsFixture,
        status: 'available',
        summary: {
          ...analyticsFixture.summary,
          gross_notional: recovered ? '0' : '1250',
          net_notional: recovered ? '0' : '1250',
        },
        assets: recovered
          ? []
          : [
              {
                asset: 'BTC',
                long_notional: '1250',
                short_notional: '0',
                gross_notional: '1250',
                net_notional: '1250',
                gross_share_pct: '100',
              },
            ],
      },
    }),
  );
  await page.route('**/api/v1/pro/execution/portfolios/*/batches*', (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await page.route('**/api/v1/pro/execution/deployments?*', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 'member-btc',
            group_id: 'blocked-group',
            source: 'example',
            inst_id: 'BTC-USDT',
            status: 'running',
            last_error: null,
          },
          {
            id: 'member-eth',
            group_id: 'blocked-group',
            source: 'example',
            inst_id: 'ETH-USDT',
            status: 'running',
            last_error: null,
          },
        ],
      },
    }),
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  const desk = page.getByRole('region', { name: 'Unresolved conditions', exact: true });
  await expect(desk.locator('.desk-issue')).toHaveCount(3);
  const group = desk.locator('[data-issue-id="group:blocked-group"]');
  await expect(group.getByRole('heading', { name: 'Blocked recovery basket' })).toBeVisible();
  await expect(group).toContainText('Acknowledged · unresolved');
  await expect(group).toContainText('1 hour 10 minutes');
  await expect(group.locator('.desk-issue-amount')).toContainText('1,250.00');
  await expect(group).toContainText('0.02 Base units');
  await expect(desk).toContainText('History preparation basket');
  await expect(desk).toContainText('Duration unavailable');
  await expect(desk).toContainText('Unvalued inventory is not zero exposure.');
  await expect(desk).toContainText('Shared workspace');
  await expect(desk).not.toContainText('Other-source-only error');
  await expect(desk).not.toContainText('Historical stopped basket');
  await expect(page.locator('.overview-economic-state')).toContainText('Action required');
  await expect(page.locator('.service-health-state')).toContainText('ok');
  await expect(
    page.locator('.overview-strategies').getByText('Active portfolio groups').locator('..'),
  ).toContainText('3');
  await expect(
    page.locator('.overview-strategies').getByText('Active managed legs').locator('..'),
  ).toContainText('2');
  await expect(
    page.locator('.overview-strategies').getByText('Active standalone strategies').locator('..'),
  ).toContainText('0');
  await group.getByRole('button', { name: 'Inspect group & recovery', exact: true }).click();
  await expect(
    page
      .locator('.managed-group-evidence')
      .getByRole('heading', { name: 'Blocked recovery basket', exact: true }),
  ).toBeVisible();
  await navigate(page, 'Overview');
  await desk
    .locator('[data-issue-id="group:blocked-group"]')
    .getByRole('button', { name: 'Inspect incident & response', exact: true })
    .click();
  const incident = page.getByRole('region', { name: 'Selected incident', exact: true });
  await expect(incident).toContainText('blocked-group');
  await expect(incident).toContainText('Investigating retained inventory');
  await expect(page.getByRole('tab', { name: 'Incidents', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await navigate(page, 'Overview');
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
  await page.reload();
  const zh = page.getByRole('region', { name: '未解决事项', exact: true });
  await expect(zh).toContainText('已响应 · 尚未恢复');
  await expect(zh).toContainText('当前组内持仓');
  await expect(zh).toContainText('1 小时 10 分钟');
  await expect(zh).toContainText('未估值的持仓不代表零敞口。');
  await expect(page.locator('.overview-economic-state')).toContainText('需要处理');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  if (testInfo.project.name === 'mobile') {
    await mkdir('../docs/assets', { recursive: true });
    await zh.screenshot({
      path: '../docs/assets/trading-desk-incident-mobile.png',
      animations: 'disabled',
    });
  }
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'en'));
  await page.reload();
  if (testInfo.project.name === 'desktop') {
    await expect(
      page
        .getByRole('region', { name: 'Unresolved conditions', exact: true })
        .locator('.desk-issue'),
    ).toHaveCount(3);
    await expect(page.locator('.overview-economic-state')).toContainText('Action required');
    await expect(
      page.locator('.trader-metrics .metric').filter({ hasText: 'Gross exposure' }),
    ).toContainText('1,250.00');
    await expect(
      page.locator('.overview-strategies').getByText('Active portfolio groups').locator('..'),
    ).toContainText('3');
    await mkdir('../docs/assets', { recursive: true });
    await page.screenshot({
      path: '../docs/assets/trading-desk-incident-desktop.png',
      fullPage: false,
      animations: 'disabled',
    });
  }
  recovered = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Unresolved conditions', exact: true }),
  ).toHaveCount(0);
});

test('pending funding marks account economics provisional even when service health is ok', async ({
  page,
}) => {
  const accountFixture = await (
    await page.request.get('/api/v1/pro/execution/account?source=example')
  ).json();
  await page.route('**/api/v1/pro/execution/account?*', (route) =>
    route.fulfill({
      json: {
        ...accountFixture,
        economic_status: 'funding_pending',
        equity: null,
        equity_before_pending_funding: '10000',
        pending_funding: [{ inst_id: 'BTC-USDT-SWAP', quantity: '1' }],
      },
    }),
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.locator('.overview-funding-notice')).toContainText(
    'Funding settlement remains pending',
  );
  await expect(page.locator('.overview-economic-state')).toContainText('Action required');
  await expect(
    page.locator('.trader-metrics .metric').filter({ hasText: 'Account equity' }),
  ).toContainText('—');
  await page.getByRole('button', { name: 'Inspect funding & ledger', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Ledger', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('completed research keeps a failed simulated execution visibly ineligible for deployment', async ({
  page,
}) => {
  const run = {
    id: 'qa-execution-failure',
    source: 'example',
    status: 'completed',
    created_at: Date.now(),
    progress: 1,
    config: {
      name: 'Failed execution observation',
      mode: 'fixed_weights',
      hypothesis: 'A completed computation can retain an economic execution failure.',
      portfolio_version_id: 'qa-version',
      execution_contract: 'reduce_group_v1',
    },
    manifest: {},
    result: {
      execution_contract: 'reduce_group_v1',
      failure_policy: 'reduce_group',
      max_residual_pct: '2',
      execution_status: 'compensating',
      metrics: { total_return_pct: '-2', final_equity: '9800', fees_paid: '5' },
      equity: [],
      decisions: [],
      orders: [],
      ledger: [],
      execution_rejections: [],
      assumptions: {},
      evaluation: {
        mode: 'sealed_holdout',
        test_start: Date.now() - 86_400_000,
        test_end: Date.now(),
        rejection: {
          status: 'rejected',
          checks: [
            {
              metric: 'execution_status',
              actual: 'compensating',
              threshold: 'running',
              passed: false,
            },
          ],
        },
      },
    },
  };
  await page.route('**/api/v1/pro/research/portfolios?*', (route) =>
    route.fulfill({ json: { items: [run] } }),
  );
  await page.route('**/api/v1/pro/research/portfolios/qa-execution-failure', (route) =>
    route.fulfill({ json: run }),
  );
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  const outcome = page.getByRole('region', { name: 'Simulation execution state', exact: true });
  await expect(outcome).toContainText('This simulation is not eligible for paper deployment.');
  await expect(outcome).toContainText('Marked returns include any retained inventory');
  await expect(page.locator('.sealed-evaluation')).toContainText('Execution state');
  const check = page.locator('.sealed-evaluation tbody tr').filter({ hasText: 'Execution state' });
  await expect(check).toContainText('compensating');
  await expect(check).toContainText('running');
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
  await page.reload();
  await page.getByRole('tab', { name: '组合研究', exact: true }).click();
  const zh = page.getByRole('region', { name: '模拟执行状态', exact: true });
  await expect(zh).toContainText('此模拟结果不具备模拟部署资格。');
  await expect(zh).toContainText('市值收益包含保留持仓');
  await expect(page.locator('.sealed-evaluation')).toContainText('执行状态');
  await expect(page.locator('.sealed-evaluation')).toContainText('减仓补偿中');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('risk operator can persist the shared underlying capital policy without changing research scenarios', async ({
  page,
  request,
}) => {
  const auth = await (await request.get('/api/v1/auth/status')).json();
  const headers = { 'X-CSRF-Token': auth.csrf_token };
  const initial = await (
    await request.get('/api/v1/pro/execution/capital-policy?source=example')
  ).json();
  try {
    await navigate(page, 'Execution');
    await page.getByRole('tab', { name: 'Risk', exact: true }).click();
    const policy = page.locator('.account-capital-control');
    await expect(
      policy.getByRole('heading', { name: 'Account capital policy', exact: true }),
    ).toBeVisible();
    await expect(
      policy.getByLabel('Underlying asset gross limit (%)', { exact: true }),
    ).toHaveValue(String(initial.max_base_asset_gross_pct));
    await policy.getByLabel('Underlying asset gross limit (%)', { exact: true }).fill('77.5');
    const saved = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname.endsWith('/capital-policy') &&
        response.request().method() === 'PUT',
    );
    await policy.getByRole('button', { name: 'Save capital policy', exact: true }).click();
    const response = await saved;
    expect(response.ok()).toBeTruthy();
    expect((await response.json()).max_base_asset_gross_pct).toBe('77.5');
    const stored = await (
      await request.get('/api/v1/pro/execution/capital-policy?source=example')
    ).json();
    expect(stored.max_base_asset_gross_pct).toBe('77.5');
    await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
    await page.reload();
    await expect(policy.getByRole('heading', { name: '账户资本政策', exact: true })).toBeVisible();
    await expect(policy.getByLabel('标的资产总敞口上限（%）', { exact: true })).toHaveValue('77.5');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  } finally {
    expect(
      (
        await request.put('/api/v1/pro/execution/capital-policy?source=example', {
          headers,
          data: {
            source: 'example',
            max_base_asset_gross_pct: String(initial.max_base_asset_gross_pct),
          },
        })
      ).ok(),
    ).toBeTruthy();
  }
});

test('a flat failed group remains a warning until the operator explicitly stops it', async ({
  page,
}) => {
  let stopped = false;
  const now = Date.now();
  const group = () => ({
    id: 'qa-flat-failure',
    source: 'example',
    status: stopped ? 'stopped' : 'failed',
    created_at: now,
    last_error: 'Preparation failed before any fill.',
    manifest: {
      name: 'Flat failure awaiting disposition',
      version_revision: 1,
      definition: { capital_pct: '20' },
      legs: [],
    },
    attention: stopped
      ? null
      : {
          phase: 'failed',
          since: now - 60_000,
          as_of: now,
          error: 'Preparation failed before any fill.',
          inventory_notional: '0',
          valuation_status: 'fresh',
          inventory: [],
          residual_notional: '0',
        },
  });
  await page.route('**/api/v1/pro/ops', (route) =>
    route.fulfill({
      json: { health: { status: 'ok' }, incidents: [], jobs: [], feeds: [], workers: [] },
    }),
  );
  await page.route('**/api/v1/pro/execution/portfolios?*', (route) =>
    route.fulfill({ json: { items: [group()] } }),
  );
  await page.route('**/api/v1/pro/execution/portfolios/qa-flat-failure/batches*', (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await page.route('**/api/v1/pro/execution/portfolios/qa-flat-failure/stop', (route) => {
    expect(route.request().method()).toBe('POST');
    stopped = true;
    return route.fulfill({ json: group() });
  });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  const desk = page.getByRole('region', { name: 'Unresolved conditions', exact: true });
  const issue = desk.locator('[data-issue-id="group:qa-flat-failure"]');
  await expect(issue).toHaveClass(/desk-issue-warning/);
  await expect(issue).toContainText('0.00 USDT');
  await expect(
    desk.getByRole('heading', { name: 'Review unresolved conditions', exact: true }),
  ).toBeVisible();
  await issue.getByRole('button', { name: 'Inspect group & recovery', exact: true }).click();
  const stop = page.getByRole('button', { name: 'Stop whole portfolio', exact: true });
  await expect(stop).toBeEnabled();
  await stop.click();
  await expect(stop).toBeDisabled();
  await navigate(page, 'Overview');
  await expect(
    page.getByRole('region', { name: 'Unresolved conditions', exact: true }),
  ).toHaveCount(0);
});

test('public depth evidence exposes finite coverage and freezes honest incomplete observations in both languages', async ({
  page,
}) => {
  const capturedAt = Date.now();
  const captureId = 'a'.repeat(32);
  const reportId = 'b'.repeat(32);
  let didCapture = false;
  let didFreeze = false;
  const scenarios = [1000, 2500, 10000, 100000].flatMap((size) =>
    ['buy', 'sell'].map((side) => ({
      side,
      requested_notional: String(size),
      status: size === 1000 ? 'complete' : 'depth_exhausted',
      vwap: side === 'buy' ? '100.15' : '99.85',
      shortfall_bps: '15',
      participation_pct: '100',
      unfilled_quantity: String(Math.max(0, size / 100 - 10)),
    })),
  );
  const capture = {
    id: captureId,
    inst_id: 'BTC-USDT',
    source: 'okx',
    received_at: capturedAt,
    known_at: capturedAt,
    current_age_ms: 50,
    content_hash: 'fixture-depth-hash',
    evidence: {
      status: 'supported',
      book_age_at_capture_ms: 10,
      mid: '100',
      spread_bps: '20',
      metadata: { quantity_unit: 'base_asset' },
      scenarios,
    },
    raw_depth: [
      {
        ts: String(capturedAt - 10),
        asks: [
          ['100.10', '5', '0', '1'],
          ['100.20', '5', '0', '2'],
        ],
        bids: [
          ['99.90', '5', '0', '1'],
          ['99.80', '5', '0', '2'],
        ],
      },
    ],
    raw_metadata: [{ provenance: 'synthetic_fixture_for_browser_acceptance' }],
  };
  const quantiles = { median: '15', p90: '15', p95: '15', worst: '15' };
  const report = {
    id: reportId,
    inst_id: 'BTC-USDT',
    created_at: capturedAt + 1,
    content_hash: 'fixture-report-hash',
    status: 'insufficient_evidence',
    current_review_status: 'insufficient_evidence',
    independent_book_count: 1,
    selection_audit: { all_available_count: 1, selected_count: 1, omitted_capture_ids: [] },
    uncovered_edges: { start_ms: 0, end_ms: 0 },
    approved_observed_notional: null,
    declared_child_notional: '2500',
    declared_sleeve_notional: '10000',
    input: { minimum_samples: 12 },
    observed_window: { start: capturedAt, end: capturedAt },
    independent_window: { elapsed_ms: 0 },
    scenarios: [1000, 2500, 10000, 100000].map((size) => ({
      notional: String(size),
      all_samples_pass: false,
      sides: {
        buy: { shortfall_bps: quantiles, participation_pct: { ...quantiles, worst: '100' } },
        sell: { shortfall_bps: quantiles, participation_pct: { ...quantiles, worst: '100' } },
      },
    })),
  };
  await page.route('**/api/v1/pro/research/liquidity/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/captures') && route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ inst_id: 'BTC-USDT', depth: 400 });
      didCapture = true;
      return route.fulfill({ status: 201, json: capture });
    }
    if (path.endsWith('/captures'))
      return route.fulfill({ json: { items: didCapture ? [capture] : [] } });
    if (path.endsWith('/captures/' + captureId)) return route.fulfill({ json: capture });
    if (path.endsWith('/calibrations') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      expect(body).toMatchObject({
        inst_id: 'BTC-USDT',
        capture_ids: [captureId],
        max_shortfall_bps: '7.5',
        max_participation_pct: '5',
      });
      expect(body.window_end - body.window_start).toBeGreaterThanOrEqual(30 * 60000);
      expect(body.window_end - body.window_start).toBeLessThan(30 * 60000 + 100);
      didFreeze = true;
      return route.fulfill({ status: 201, json: report });
    }
    if (path.endsWith('/calibrations'))
      return route.fulfill({ json: { items: didFreeze ? [report] : [] } });
    if (path.endsWith('/paper-comparison'))
      return route.fulfill({
        json: {
          items: [
            {
              order_id: 'fixture-paper-order',
              filled_at: capturedAt + 100,
              status: 'no_causal_capture',
              paper_shortfall_bps: null,
              model_minus_walk_bps: null,
            },
          ],
        },
      });
    throw new Error('Unexpected fixture liquidity API ' + path);
  });
  await navigate(page, 'Operations');
  await page.getByRole('tab', { name: 'Cost and depth', exact: true }).click();
  const panel = page.locator('.liquidity-workspace');
  await expect(panel).toContainText('No public depth has been captured');
  await panel.getByRole('button', { name: 'Capture public book', exact: true }).click();
  await expect(panel.getByText('Displayed depth exhausted', { exact: true })).toHaveCount(6);
  await expect(panel).toContainText('Historical research costs remain scenarios.');
  await panel.getByRole('button', { name: 'Inspect raw depth and metadata', exact: true }).click();
  await expect(panel.locator('.json-details pre')).toContainText(
    'synthetic_fixture_for_browser_acceptance',
  );
  await expect(panel.locator('.json-details pre')).toContainText('fixture-depth-hash');
  await panel.getByRole('button', { name: 'Close raw depth evidence', exact: true }).click();
  await panel.getByLabel('Maximum mid shortfall (bps)', { exact: true }).fill('7.5');
  await panel.getByLabel('Maximum captured depth share (%)', { exact: true }).fill('5');
  await panel.getByRole('button', { name: 'Freeze cost and depth review', exact: true }).click();
  await expect(panel.locator('.liquidity-report')).toContainText('Insufficient observations');
  await expect(panel.locator('.liquidity-report')).toContainText('1 / 12');
  await expect(panel.locator('.liquidity-report')).toContainText(/Historical capacity/i);
  await panel.getByRole('button', { name: 'Compare local paper fills', exact: true }).click();
  await expect(panel.locator('.liquidity-paper')).toContainText('No prior fresh capture');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.evaluate(() => localStorage.setItem('tidebench:language', 'zh-CN'));
  await page.reload();
  await page.getByRole('tab', { name: '成本与深度', exact: true }).click();
  await expect(panel.getByRole('heading', { name: '成本与容量证据', exact: true })).toBeVisible();
  await expect(panel.getByText('可见深度不足', { exact: true })).toHaveCount(6);
  await expect(panel.locator('.liquidity-report')).toContainText('观测证据不足');
  await expect(panel).toContainText('历史研究成本仍属于情景假设。');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('release depth evidence uses pinned reports and refuses a content hash mismatch', async ({
  page,
}) => {
  const run = {
    id: 'qa-pinned-liquidity',
    source: 'example',
    status: 'completed',
    created_at: Date.now(),
    progress: 1,
    config: {
      name: 'Pinned evidence browser fixture',
      mode: 'fixed_weights',
      hypothesis: 'A release must inspect the specific immutable evidence it reviewed.',
      portfolio_version_id: 'qa-version',
      execution_contract: 'reduce_group_v1',
    },
    manifest: {},
    result: {
      execution_contract: 'reduce_group_v1',
      execution_status: 'running',
      metrics: { total_return_pct: null, final_equity: null, fees_paid: null },
      equity: [],
      decisions: [],
      orders: [],
      ledger: [],
      execution_rejections: [],
      assumptions: {},
    },
  };
  const firstId = 'c'.repeat(32),
    secondId = 'd'.repeat(32);
  let previews = 0;
  let latestRequests = 0;
  const definition = {
    schema_version: 1,
    bar: '1H',
    mode: 'fixed_weights',
    capital_pct: '100',
    max_residual_pct: '2',
    failure_policy: 'reduce_group',
    legs: [{ inst_id: 'BTC-USDT', weight: '1', leverage: '1', direction: 'long_only' }],
  };
  await page.route('**/api/v1/pro/research/portfolios?*', (route) =>
    route.fulfill({ json: { items: [run] } }),
  );
  await page.route('**/api/v1/pro/research/portfolios/qa-pinned-liquidity', (route) =>
    route.fulfill({ json: run }),
  );
  await page.route('**/api/v1/pro/execution/portfolio-releases/preview', (route) => {
    previews++;
    const id = previews === 1 ? firstId : secondId;
    return route.fulfill({
      json: {
        run_id: run.id,
        source: 'okx',
        preview_hash: 'fixture-preview-' + previews,
        definition,
        execution_config: {},
        risk_policy: {},
        research_evidence: {},
        cost_differences: [],
        risk_differences: [],
        required_acknowledgements: [],
        blockers: [],
        liquidity_review: {
          role: 'informational',
          scope: 'fixture immutable review only',
          reports: [
            {
              id,
              inst_id: 'BTC-USDT',
              content_hash: 'expected-' + id,
              status_at_freeze: 'observational_pass',
            },
          ],
          missing_markets: [],
        },
      },
    });
  });
  await page.route('**/api/v1/pro/research/liquidity/calibrations?*', (route) => {
    latestRequests++;
    return route.fulfill({ json: { items: [] } });
  });
  await page.route('**/api/v1/pro/research/liquidity/calibrations/*', (route) => {
    const id = new URL(route.request().url()).pathname.split('/').pop()!;
    return route.fulfill({
      json: {
        id,
        inst_id: 'BTC-USDT',
        content_hash: id === firstId ? 'expected-' + id : 'wrong-hash',
        created_at: Date.now() - 600000,
        status: 'observational_pass',
        current_review_status: 'stale',
        independent_book_count: 12,
        approved_observed_notional: '1000',
        declared_child_notional: '2500',
        declared_sleeve_notional: '10000',
        input: { minimum_samples: 12 },
        observed_window: { start: Date.now() - 930000, end: Date.now() - 600000 },
        independent_window: { elapsed_ms: 330000 },
        selection_audit: { all_available_count: 12, selected_count: 12, omitted_capture_ids: [] },
        uncovered_edges: { start_ms: 0, end_ms: 0 },
        scenarios: [],
      },
    });
  });
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  await page.getByRole('button', { name: 'Review portfolio release', exact: true }).click();
  const evidence = page.locator('.liquidity-release-evidence');
  await expect(evidence).toContainText(
    'These immutable reports are pinned to this release review.',
  );
  await expect(evidence).toContainText('Observed window passed');
  await expect(evidence).toContainText('Evidence is stale');
  await evidence.getByRole('button', { name: 'Inspect evidence', exact: true }).click();
  await expect(evidence.locator('pre')).toContainText(firstId);
  expect(latestRequests).toBe(0);
  await page.getByRole('button', { name: 'Review portfolio release', exact: true }).click();
  await expect(evidence).toContainText('Pinned liquidity review failed content verification.');
  expect(latestRequests).toBe(0);
});
