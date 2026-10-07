import { expect, test, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

async function navigate(page: Page, name: string) {
  const button = page.getByRole('navigation').getByRole('button', { name, exact: true });
  const openNavigation = page.getByRole('button', { name: 'Open navigation' });
  if (await openNavigation.isVisible()) await openNavigation.click();
  await button.click();
}

test.beforeEach(async ({ page, request }) => {
  // The isolated browser test database is disposable; no real exchange requests or funds are involved.
  await request.post('/api/v1/risk/kill-switch', {
    data: { source: 'example', active: false, reason: 'Isolated browser test setup' },
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source') === 'okx') {
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: 'test_outage',
            message: 'OKX is unavailable in this isolated browser test.',
          },
        },
      });
    } else await route.continue();
  });
  await page.goto('/');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.getByText('Fixed synthetic dataset', { exact: true })).toBeVisible();
});

test('saved research, costs, snapshot replay and a real JSON download', async ({
  page,
  request,
}, testInfo) => {
  const capture =
    process.env.TIDEBENCH_CAPTURE_ASSETS === '1' && testInfo.project.name === 'desktop';
  if (capture) {
    await mkdir('../docs/assets', { recursive: true });
    await expect(page.locator('.financial-chart canvas').first()).toBeVisible();
    await page.screenshot({ path: '../docs/assets/workspace.png' });
  }
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  await navigate(page, 'Research');
  await page.getByLabel('Candle count', { exact: true }).fill('100');
  await page.getByLabel('Fast window', { exact: true }).fill('8');
  await page.getByLabel('Slow window', { exact: true }).fill('21');
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/backtests') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run backtest', exact: true }).click();
  const created = await (await queued).json();
  await expect
    .poll(async () => (await (await request.get(`/api/v1/backtests/${created.id}`)).json()).status)
    .toBe('completed');
  await expect(page.getByRole('button', { name: 'Export JSON', exact: true })).toBeEnabled();
  await expect(page.getByText('insufficient_sample', { exact: false })).toBeVisible();
  if (capture) {
    await page
      .getByRole('heading', { name: 'Strategy research', exact: true })
      .scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: '../docs/assets/research.png' });
  }
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export JSON', exact: true }).click();
  expect((await download).suggestedFilename()).toBe(`tidebench-${created.id}.json`);
  const original = await (await request.get(`/api/v1/backtests/${created.id}`)).json();
  const replay = page.waitForResponse((r) =>
    r.url().endsWith(`/api/v1/backtests/${created.id}/replay`),
  );
  await page.getByRole('button', { name: 'Replay snapshot', exact: true }).click();
  const replayRun = await (await replay).json();
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/backtests/${replayRun.id}`)).json()).status,
    )
    .toBe('completed');
  const reproduced = await (await request.get(`/api/v1/backtests/${replayRun.id}`)).json();
  expect(reproduced.result).toEqual(original.result);
  expect(reproduced.manifest.dataset_hash).toEqual(original.manifest.dataset_hash);
  await page.getByRole('button', { name: 'Provenance', exact: true }).click();
  await expect(page.locator('.data-inspector')).toContainText(reproduced.manifest.dataset_hash);
  expect(pageErrors).toEqual([]);
});

test('paper fill, persistent risk halt, resume, and strategy start/stop', async ({
  page,
  request,
}) => {
  await navigate(page, 'Paper desk');
  await page.getByLabel('Quantity', { exact: false }).fill('0.001');
  await page.getByRole('button', { name: 'Submit paper order', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Bought' })).toBeVisible();
  const account = await (await request.get('/api/v1/paper/account?source=example')).json();
  expect(Number(account.cash)).toBeLessThan(10000);
  expect(account.positions.some((p: { inst_id: string }) => p.inst_id === 'BTC-USDT')).toBeTruthy();
  await navigate(page, 'Risk & activity');
  await page.getByLabel('Reason for halt').fill('Browser verification halt');
  await page.getByRole('button', { name: 'Halt paper desk', exact: true }).click();
  await expect(page.getByText('EXECUTION HALTED', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('EXECUTION HALTED', { exact: true })).toBeVisible();
  await page.getByLabel('Reason to resume').fill('Browser verification complete');
  await page.getByRole('button', { name: 'Resume paper desk', exact: true }).click();
  await expect(page.getByText('EXECUTION ENABLED', { exact: true })).toBeVisible();
  await navigate(page, 'Paper desk');
  await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Deploy a strategy' })).toBeVisible();
  await page.getByRole('button', { name: 'Start paper strategy', exact: true }).click();
  await expect(page.getByText('Paper strategy deployed.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(page.getByText('Strategy stopped.', { exact: false })).toBeVisible();
});

test('source failures stay explicit and layout is usable at the viewport', async ({ page }) => {
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(
    page.getByRole('alert').filter({ hasText: 'OKX is unavailable' }).first(),
  ).toBeVisible();
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('okx');
  await page.getByRole('button', { name: 'Use synthetic example' }).first().click();
  await expect(page.getByLabel('Market source', { exact: true })).toHaveValue('example');
  await expect(page.getByText('Fixed synthetic dataset', { exact: true })).toBeVisible();
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
  await expect(page.getByLabel('Trading pair')).toHaveValue('ETH-USDT');
});
