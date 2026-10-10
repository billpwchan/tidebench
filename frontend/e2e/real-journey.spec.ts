import { expandSnapshotBasis } from './desk-helpers';
import { expect, test as base, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';

const test = base.extend({ request: async ({ context }, use) => use(context.request) });
async function navigate(page: Page, name: string) {
  const target = page.getByRole('navigation').getByRole('button', { name, exact: true });
  await target.waitFor({ state: 'attached' });
  const open = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await open.isVisible()) await open.click();
  await target.click();
}

test('real saved strategy survives data preparation, reload, approval and exact-controller inspection', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90000);
  const status = await (await request.get('/api/v1/auth/status')).json();
  const login = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: {
        username: 'browserqa',
        password: 'isolated-browser-password-123',
        ...(status.setup_required ? { display_name: 'Isolated workflow verification' } : {}),
      },
    },
  );
  expect(login.ok(), await login.text()).toBeTruthy();
  const headers = { 'X-CSRF-Token': (await login.json()).csrf_token };
  await request.post('/api/v1/pro/execution/halt', {
    headers,
    data: { source: 'example', active: false, reason: 'Isolated workflow verification' },
  });
  await page.route('**/api/v1/**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('source') === 'okx')
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: 'isolated',
            message: 'Public network excluded from this synthetic workflow.',
          },
        },
      });
    else await route.continue();
  });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/#strategies?source=example');
  await page.getByLabel('Interface language', { exact: true }).selectOption('en');
  await page.getByRole('button', { name: 'New strategy', exact: true }).click();
  await page
    .getByLabel('Strategy name', { exact: true })
    .fill(`Workflow persistence ${info.project.name}`);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill(
      'A deliberately simple buy-and-hold control tests binding and execution semantics, not profitable alpha.',
    );
  await page.getByLabel('Strategy', { exact: true }).selectOption('buy_hold');
  const saved = page.waitForResponse(
    (r) => r.url().endsWith('/pro/strategies') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save version', exact: true }).click();
  const project = await (await saved).json();
  await expect(page.getByText('Immutable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Research this version', exact: true }).click();
  await page.getByLabel('Fee', { exact: true }).fill('13');
  await page.getByLabel('Slippage', { exact: true }).fill('7');
  await page.getByRole('button', { name: 'Open Data library', exact: true }).click();
  await expect(
    page.getByText('Preparing data for your strategy draft', { exact: true }),
  ).toBeVisible();
  await page.getByLabel('Market', { exact: true }).selectOption('DOGE-USDT');
  await page.getByLabel('Start (UTC)', { exact: true }).fill('2025-12-22T00:00');
  await page.getByLabel('End (UTC)', { exact: true }).fill('2026-01-01T00:00');
  const prepared = page.waitForResponse(
    (r) => r.url().endsWith('/pro/catalog/packages') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Prepare research package', exact: true }).click();
  const packageResponse = await prepared;
  expect(packageResponse.status()).toBe(202);
  const queuedPackage = await packageResponse.json();
  let pkg: any;
  await expect
    .poll(async () => {
      pkg = await (await request.get(`/api/v1/pro/catalog/packages/${queuedPackage.id}`)).json();
      return pkg.ready;
    })
    .toBeTruthy();
  const row = page.locator('.package-row').filter({ hasText: pkg.manifest_hash.slice(0, 12) });
  await row.getByRole('button', { name: 'Open in research', exact: true }).click();
  await expect(page).toHaveURL(new RegExp('#research\\?.*package=' + pkg.id));
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Bound strategy version', { exact: true })).toBeVisible();
  expect(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('version')).toBe(
    project.version.id,
  );
  await expect(page.getByLabel('Dataset', { exact: true })).toHaveValue(
    pkg.research_inputs.dataset_id,
  );
  await expect(page.getByLabel('Fee', { exact: true })).toHaveValue('13');
  await expect(page.getByLabel('Slippage', { exact: true })).toHaveValue('7');
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1') {
    const dir = process.env.TIDEBENCH_BROWSER_ASSET_DIR ?? '../docs/assets';
    await mkdir(dir, { recursive: true });
    await page.screenshot({
      path: join(dir, `journey-data-return-${info.project.name}.png`),
      animations: 'disabled',
      fullPage: true,
    });
  }
  const submitted = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run research', exact: true }).click();
  const runResponse = await submitted;
  expect(runResponse.status()).toBe(202);
  const run = await runResponse.json();
  expect(run.config.strategy_version_id).toBe(project.version.id);
  expect(run.config.package_manifest_hash).toBe(pkg.manifest_hash);
  expect(run.config.fee_bps).toBe('13');
  expect(run.config.slippage_bps).toBe('7');
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/pro/research/runs/${run.id}`)).json()).status,
    )
    .toBe('completed');
  await page.getByRole('button', { name: 'Review paper release', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review paper release', exact: true });
  await dialog
    .getByLabel('Release review', { exact: true })
    .fill(
      'Synthetic end-to-end workflow verification: identity, cost binding, approval and controller state. No alpha claim.',
    );
  for (const box of await dialog.getByRole('checkbox').all()) await box.check();
  await dialog.getByRole('button', { name: 'Approve paper release', exact: true }).click();
  const activated = page.waitForResponse(
    (r) => r.url().endsWith('/activate') && r.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Activate paper release', exact: true }).click();
  const release = await (await activated).json();
  expect(release.status).toBe('deployed');
  await dialog.getByRole('button', { name: 'Inspect deployment', exact: true }).click();
  expect(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('view')).toBe(
    `strategies:${release.deployment_id}`,
  );
  await page.reload();
  const controller = page
    .locator(`[data-deployment-id="${release.deployment_id}"]`)
    .locator('xpath=ancestor::tr');
  await expect(controller.getByText('Selected deployment', { exact: true })).toBeVisible();
  await expect(controller.getByText('running', { exact: true })).toBeVisible();
  let inventory: any;
  await expect
    .poll(async () => {
      const account = await (
        await request.get('/api/v1/pro/execution/account?source=example')
      ).json();
      inventory = account.positions.find(
        (item: { inst_id: string }) => item.inst_id === 'DOGE-USDT',
      );
      return !!inventory;
    })
    .toBeTruthy();
  await controller.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(controller.getByText('stopped', { exact: true })).toBeVisible();
  const retained = await (await request.get('/api/v1/pro/execution/account?source=example')).json();
  expect(
    retained.positions.find((item: { inst_id: string }) => item.inst_id === 'DOGE-USDT').quantity,
  ).toBe(inventory.quantity);
  // Cleanup touches only Playwright's disposable store; it is a separate economic action.
  const cleanup = await request.post('/api/v1/pro/execution/orders', {
    headers: { ...headers, 'Idempotency-Key': `workflow-cleanup-${release.id}` },
    data: {
      source: 'example',
      inst_id: 'DOGE-USDT',
      side: 'sell',
      quantity: inventory.quantity,
      leverage: 1,
      reduce_only: true,
      order_type: 'market',
      margin_mode: 'isolated',
    },
  });
  expect(cleanup.ok(), await cleanup.text()).toBeTruthy();

  await navigate(page, 'Overview');
  await expect(page.getByRole('heading', { name: 'Next actions', exact: true })).toBeVisible();
  await expandSnapshotBasis(page);
  await expect(
    page.getByText('Account and exposure values share one captured snapshot.', { exact: true }),
  ).toBeVisible();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1') {
    await expect(page.locator('.workspace-journey .journey-status')).toBeVisible();
    const dir = process.env.TIDEBENCH_BROWSER_ASSET_DIR ?? '../docs/assets';
    await mkdir(dir, { recursive: true });
    await page.screenshot({
      path: join(dir, `journey-overview-${info.project.name}.png`),
      animations: 'disabled',
    });
  }
  expect(errors).toEqual([]);
});
