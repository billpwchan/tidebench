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

test('unified portfolio order preview, fill and persistent risk halt', async ({
  page,
  request,
}, testInfo) => {
  await navigate(page, 'Execution');
  await page.getByLabel('Quantity', { exact: false }).fill('0.001');
  await page.getByRole('button', { name: 'Preview order', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Order preview', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Submit order', exact: true }).click();
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
