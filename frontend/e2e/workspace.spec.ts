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
  const opener = page.getByRole('button', { name: 'Open navigation' });
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
  await navigate(page, 'Data library');
  await page.getByRole('tab', { name: 'Instrument evidence', exact: true }).click();
  const desk = page.getByRole('region', { name: 'Instrument evidence', exact: true });
  const captured = page.waitForResponse(
    (r) => r.url().includes('/catalog/instrument-observations?') && r.request().method() === 'POST',
  );
  await desk.getByRole('button', { name: 'Capture current observation', exact: true }).click();
  const response = await captured;
  expect(response.status()).toBe(201);
  const first = await response.json();
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
