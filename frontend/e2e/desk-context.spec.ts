import { test, expect } from '@playwright/test';
import { desk } from './desk-review-fixture';

test('market inspection retains execution draft, market, book filter and focus without writes', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await page.getByLabel('Order type', { exact: true }).selectOption('limit');
  await page.getByLabel('Quantity', { exact: false }).fill('0.0123');
  await page.getByLabel('Limit price', { exact: true }).fill('87');
  await page.getByLabel('Search positions', { exact: true }).fill('BTC');
  const previousUrl = page.url();
  await page.getByRole('button', { name: 'Search markets, Command K', exact: true }).click();
  const search = page.getByRole('dialog', { name: 'Find a market', exact: true });
  await search.getByLabel('Search market symbol').fill('ETH');
  await search.getByRole('button', { name: 'Inspect ETH-USDT-SWAP', exact: true }).click();
  const inspector = page.getByRole('dialog', { name: 'Market inspector', exact: true });
  await expect(inspector.getByLabel('Market', { exact: true })).toHaveValue('ETH-USDT-SWAP');
  await expect(inspector).toContainText('100.1');
  expect(page.url()).toBe(previousUrl);
  if ((await page.viewportSize())!.width > 720)
    await page.screenshot({
      path: '../docs/assets/desk-inspector-v0.14-desktop.png',
      animations: 'disabled',
    });
  await page.getByRole('button', { name: 'Close market inspector', exact: true }).click();
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('BTC-USDT');
  await expect(page.getByLabel('Quantity', { exact: false })).toHaveValue('0.0123');
  await expect(page.getByLabel('Limit price', { exact: true })).toHaveValue('87');
  await expect(page.getByLabel('Search positions', { exact: true })).toHaveValue('BTC');
  expect(state.mutations).toEqual([]);
});

test('position inspection closes to its originating record without writes', async ({ page }) => {
  const state = await desk(page);
  const book = page.locator('.overview-book');
  const row = book.locator('[data-row-key="BTC-USDT"]').filter({ visible: true });
  const open = row.getByRole('button', { name: 'Inspect BTC-USDT', exact: true });
  await open.click();
  const inspector = page.getByRole('dialog', { name: 'Market inspector', exact: true });
  await expect(inspector).toContainText('Illustrative snapshot');
  await page.keyboard.press('Escape');
  await expect(inspector).toHaveCount(0);
  await expect(open).toBeFocused();
  expect(state.mutations).toEqual([]);
});

test('market inspector flags future public timestamps and never exposes a submit action', async ({
  page,
}) => {
  const state = await desk(page, 'execution', true, 'okx');
  state.future = 60000;
  await page.getByRole('button', { name: 'Search markets, Command K', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Find a market', exact: true })
    .getByRole('button', { name: 'Inspect BTC-USDT', exact: true })
    .click();
  const inspector = page.getByRole('dialog', { name: 'Market inspector', exact: true });
  await expect(inspector).toContainText('Snapshot needs refresh');
  await expect(inspector.getByRole('button', { name: /Submit|Trade|Preview/ })).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test('a mobile first fold contains a full position and unrealized P&L', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 664 });
  await desk(page);
  const record = page.locator('.overview-book .desk-compact-row[data-row-key="BTC-USDT"]');
  await expect(record).toBeVisible();
  const pnl = record.locator('[data-column="unrealized_pnl"]');
  const quantity = record.locator('[data-column="quantity"]');
  for (const field of [pnl, quantity, record]) {
    const bounds = await field.boundingBox();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(664);
  }
  for (const button of [
    record.getByRole('button', { name: 'Inspect BTC-USDT', exact: true }),
    page.getByRole('button', { name: 'Open navigation', exact: true }),
    page.getByRole('button', { name: 'Search markets, Command K', exact: true }),
  ]) {
    const bounds = await button.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(
    page.locator('.trader-metrics .metric').filter({ hasText: 'Unrealized P&L' }),
  ).toBeVisible();
  await page.screenshot({
    path: '../docs/assets/desk-overview-v0.14-mobile.png',
    animations: 'disabled',
  });
});

test('desktop ticket keeps exact position P&L inside the visible book and all secondary fields inspectable', async ({
  page,
}, info) => {
  if (info.project.name === 'mobile') test.skip();
  const state = await desk(page, 'execution');
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  for (const width of [1440, 1200]) {
    await page.setViewportSize({ width, height: 1000 });
    const table = page.locator('.account-book-panel .desk-table-scroll');
    const bounds = await table.boundingBox();
    const row = table.locator('tr[data-row-key="BTC-USDT"]');
    const pnl = row.locator('.desk-numeric').last();
    const cell = await pnl.boundingBox();
    expect(cell!.x).toBeGreaterThanOrEqual(bounds!.x);
    expect(cell!.x + cell!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
    await row.getByRole('button', { name: 'More details BTC-USDT', exact: true }).click();
    await expect(table.locator('[data-details-for="BTC-USDT"]')).toContainText('Entry price');
    await expect(table.locator('[data-details-for="BTC-USDT"]')).toContainText(
      'Maintenance margin',
    );
    expect(state.mutations).toEqual([]);
    if (width === 1440)
      await page.screenshot({
        path: '../docs/assets/desk-execution-v0.14-desktop.png',
        animations: 'disabled',
      });
    await row.getByRole('button', { name: 'More details BTC-USDT', exact: true }).click();
    await expect(table.locator('[data-details-for="BTC-USDT"]')).toHaveCount(0);
    if (width === 1200)
      await page.screenshot({
        path: '../docs/assets/desk-execution-v0.14-1200.png',
        fullPage: true,
        animations: 'disabled',
      });
  }
});

test('desktop overview records retain exact values while repeated metadata lives in details', async ({
  page,
}, info) => {
  if (info.project.name === 'mobile') test.skip();
  await desk(page);
  const table = page.locator('.overview-book .desk-table-scroll');
  await expect(table.getByRole('columnheader', { name: /Unrealized P&L/ })).toBeVisible();
  await table.getByRole('button', { name: 'More details BTC-USDT', exact: true }).click();
  await expect(table.locator('[data-details-for="BTC-USDT"]')).toContainText('Mark');
  await page.screenshot({
    path: '../docs/assets/desk-overview-v0.14-desktop.png',
    animations: 'disabled',
  });
});

test('cancellation retains the exact server result after its working row disappears', async ({
  page,
}) => {
  await desk(page);
  const source = 'example';
  const order = {
    id: 'cancel-evidence',
    source,
    inst_id: 'BTC-USDT',
    side: 'buy',
    quantity: '0.1',
    requested_quantity: '0.1',
    filled_quantity: '0.025',
    canceled_quantity: '0.075',
    order_type: 'limit',
    limit_price: '80',
    status: 'canceled',
    created_at: Date.now(),
  };
  let canceled = false;
  await page.route('**/api/v1/pro/execution/orders**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/cancel')) {
      expect(route.request().method()).toBe('POST');
      canceled = true;
      await route.fulfill({ json: order });
    } else
      await route.fulfill({
        json: { items: [{ ...order, status: canceled ? 'canceled' : 'pending' }] },
      });
  });
  await page.reload();
  await page
    .locator('.overview-book')
    .getByRole('tab', { name: /Pending orders/ })
    .click();
  const visible = page
    .locator('.overview-book [data-row-key="cancel-evidence"]')
    .filter({ visible: true });
  await visible.getByRole('button', { name: 'Cancel order', exact: true }).click();
  const receipt = page.getByRole('status', { name: 'Cancellation result', exact: true });
  await expect(receipt).toContainText('cancel-evidence');
  await expect(receipt).toContainText('0.025');
  await expect(receipt).toContainText('0.075');
  await expect(visible).toHaveCount(0);
  await receipt.getByRole('button', { name: 'Inspect this order', exact: true }).click();
  await expect(page).toHaveURL(/view=orders%3Acancel-evidence/);
  await expect(page.getByRole('region', { name: 'Selected order', exact: true })).toContainText(
    'cancel-evidence',
  );
});

test('perpetual market context separates the ticker clock from mark and funding clocks', async ({
  page,
}) => {
  await desk(page, 'execution', true, 'okx');
  const now = Date.now();
  await page.route('**/api/v1/pro/market?**', async (route) =>
    route.fulfill({
      json: {
        source: 'okx',
        inst_id: 'ETH-USDT-SWAP',
        ts: now,
        last: '100',
        bid: '100',
        ask: '100.1',
        mark: '99',
        mark_ts: now - 60000,
        funding_rate: '0.001',
        funding_ts: now - 3600000,
        instrument: { inst_id: 'ETH-USDT-SWAP', inst_type: 'SWAP', tick_size: '0.1' },
      },
    }),
  );
  await page.getByRole('button', { name: 'Search markets, Command K', exact: true }).click();
  await page.getByRole('button', { name: 'Inspect ETH-USDT-SWAP', exact: true }).click();
  const inspector = page.getByRole('dialog', { name: 'Market inspector', exact: true });
  await expect(inspector).toContainText('Current ticker snapshot');
  const clocks = inspector.locator('.market-field-clock');
  await expect(clocks.nth(0)).toContainText(/6\ds old/);
  await expect(clocks.nth(1)).toContainText(/360\ds old/);
  await expect(inspector).toContainText('independent clocks');
});

test('a perpetual ticket keeps P&L and liquidation visible at both desktop widths', async ({
  page,
}, info) => {
  if (info.project.name === 'mobile') test.skip();
  await desk(page, 'execution', false);
  const now = Date.now();
  await page.route('**/api/v1/pro/execution/account?**', (route) =>
    route.fulfill({
      json: {
        source: 'example',
        equity: '9996',
        cash: '10000',
        available_cash: '9962',
        gross_notional: '100',
        used_margin: '34',
        maintenance_margin: '0.5',
        unrealized_pnl: '-4',
        as_of: now,
        valuation_status: 'example',
        economic_status: 'complete',
        execution_mode: 'local-paper',
        positions: [
          {
            inst_id: 'BTC-USDT-SWAP',
            inst_type: 'SWAP',
            side: 'long',
            quantity: '100',
            entry_price: '104',
            mark: '100',
            margin: '34',
            maintenance_margin: '0.5',
            liquidation_price: '70.5',
            liquidation_price_kind: 'isolated_estimate',
            leverage: 3,
            unrealized_pnl: '-4',
            as_of: now,
          },
        ],
      },
    }),
  );
  await page.reload();
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  for (const width of [1440, 1200]) {
    await page.setViewportSize({ width, height: 1000 });
    const table = page.locator('.account-book-panel .desk-table-scroll');
    const bounds = await table.boundingBox();
    const row = table.locator('tr[data-row-key="BTC-USDT-SWAP"]');
    await expect(row).toContainText('70.5');
    await expect(row).toContainText('-4.00');
    for (const cell of await row.locator('.desk-numeric').all()) {
      const box = await cell.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
    }
  }
});

for (const departure of ['source', 'session'])
  test(`an overview cancellation cannot deliver into another ${departure}`, async ({ page }) => {
    const state = await desk(page);
    let release: (() => void) | undefined;
    let requested = false;
    await page.route('**/api/v1/pro/execution/orders/order-0/cancel', async (route) => {
      requested = true;
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      await route.fulfill({
        json: {
          id: 'order-0',
          source: 'example',
          inst_id: 'BTC-USDT',
          status: 'canceled',
          requested_quantity: '0.01',
          filled_quantity: '0',
          canceled_quantity: '0.01',
        },
      });
    });
    await page.getByRole('tab', { name: 'Pending orders 300', exact: true }).click();
    const row = page.locator('.overview-book [data-row-key="order-0"]').filter({ visible: true });
    await row.getByRole('button', { name: 'Cancel order', exact: true }).click();
    await expect.poll(() => requested && !!release).toBeTruthy();
    if (departure === 'source')
      await page.getByLabel('Market source', { exact: true }).selectOption('okx');
    else {
      state.session = 'second-overview-user';
      await page.evaluate(() => window.dispatchEvent(new Event('tidebench:auth-required')));
      await expect(page.locator('.sidebar-session strong')).toHaveText(state.session);
    }
    const response = page.waitForResponse(
      (r) => r.url().includes('/orders/order-0/cancel') && r.request().method() === 'POST',
    );
    release!();
    await (await response).finished();
    await expect(page.locator('.desk-cancel-receipt')).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

test('market inspector keeps keyboard focus after retry replaces the focused control', async ({
  page,
}) => {
  await desk(page, 'execution');
  let failed = true;
  await page.route('**/api/v1/pro/market?**', async (route) => {
    if (failed)
      await route.fulfill({
        status: 503,
        json: { error: { message: 'Isolated market read failure' } },
      });
    else await route.fallback();
  });
  await page.getByRole('button', { name: 'Search markets, Command K', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Find a market', exact: true })
    .getByRole('button', { name: 'Inspect ETH-USDT-SWAP', exact: true })
    .click();
  const inspector = page.getByRole('dialog', { name: 'Market inspector', exact: true });
  const retry = inspector.getByRole('button', { name: 'Retry', exact: true });
  await expect(retry).toBeVisible();
  failed = false;
  await retry.click();
  await expect(inspector).toContainText('100.1');
  await expect(retry).toHaveCount(0);
  await page.keyboard.press('Tab');
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest('.market-inspector')))
    .toBeTruthy();
  await page.keyboard.press('Shift+Tab');
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest('.market-inspector')))
    .toBeTruthy();
});

test('a non-filled order with missing fill quantity stays unknown rather than zero', async ({
  page,
}) => {
  await desk(page, 'execution', false);
  await page.route('**/api/v1/pro/execution/orders?**', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 'unknown-fill',
            source: 'example',
            inst_id: 'BTC-USDT',
            side: 'buy',
            quantity: '0.01',
            order_type: 'limit',
            limit_price: '80',
            status: 'pending',
            created_at: Date.now(),
          },
        ],
      },
    }),
  );
  await page.goto('/#execution?source=example&view=orders%3Aunknown-fill');
  const row = page.locator('[data-row-key="unknown-fill"]').filter({ visible: true });
  await expect(row).toContainText('0.01 / —');
  const receipt = page.getByRole('region', { name: 'Selected order', exact: true });
  await expect(receipt).toContainText('Not reported');
});

test('manual deployment receipt cannot enter another authenticated session', async ({ page }) => {
  const state = await desk(page, 'execution', false);
  let release: (() => void) | undefined;
  await page.route('**/api/v1/pro/execution/deployments', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON();
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({
      json: {
        id: 'old-session-deployment',
        source: body.source,
        inst_id: body.inst_id,
        status: 'running',
      },
    });
  });
  await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
  const dialog = page.locator('.deployment-dialog');
  await dialog.getByRole('button', { name: 'Start strategy', exact: true }).click();
  await expect.poll(() => !!release).toBeTruthy();
  state.session = 'new-deploy-session';
  await page.evaluate(() => window.dispatchEvent(new Event('tidebench:auth-required')));
  await expect(page.locator('.sidebar-session strong')).toHaveText(state.session);
  const response = page.waitForResponse(
    (r) => r.url().endsWith('/pro/execution/deployments') && r.request().method() === 'POST',
  );
  release!();
  await (await response).finished();
  await expect(dialog).toBeVisible();
  await expect(page).not.toHaveURL(/old-session-deployment/);
  await expect(page.getByRole('status').filter({ hasText: 'Strategy deployed' })).toHaveCount(0);
});

for (const entry of ['overview', 'execution'])
  test(`${entry} retains an exact unconfirmed cancellation after the row disappears`, async ({
    page,
  }) => {
    await desk(page, entry, false);
    let canceled = false;
    const order = {
      id: 'uncertain-cancel',
      source: 'example',
      inst_id: 'BTC-USDT',
      side: 'buy',
      quantity: '0.01',
      order_type: 'limit',
      limit_price: '80',
      status: 'pending',
      created_at: Date.now(),
    };
    await page.route('**/api/v1/pro/execution/orders**', async (route) => {
      if (new URL(route.request().url()).pathname.endsWith('/cancel')) {
        canceled = true;
        await route.fulfill({
          status: 503,
          json: { error: { message: 'Cancellation response unavailable' } },
        });
      } else await route.fulfill({ json: { items: canceled ? [] : [order] } });
    });
    await page.reload();
    if (entry === 'overview')
      await page.getByRole('tab', { name: 'Pending orders 1', exact: true }).click();
    else await page.getByRole('tab', { name: 'Working orders 1', exact: true }).click();
    const row = page.locator('[data-row-key="uncertain-cancel"]').filter({ visible: true });
    await row
      .getByRole('button', { name: entry === 'overview' ? 'Cancel order' : 'Cancel', exact: true })
      .click();
    const receipt =
      entry === 'overview' ? page.locator('.desk-cancel-receipt') : page.locator('.order-receipt');
    await expect(receipt).toContainText('Cancellation unconfirmed');
    await expect(receipt).toContainText('uncertain-cancel');
    await expect(receipt).toContainText('Cancellation response unavailable');
    await expect(row).toHaveCount(0);
    await receipt.getByRole('button', { name: 'Inspect this order', exact: true }).click();
    await expect(page).toHaveURL(/view=orders%3Auncertain-cancel/);
    await expect(page.getByRole('region', { name: 'Selected order', exact: true })).toContainText(
      'latest 200 terminal orders',
    );
  });

for (const departure of ['source', 'session'])
  test(`strategy stop receipt cannot enter another ${departure}`, async ({ page }) => {
    const state = await desk(page, 'execution', false);
    const deployment = {
      id: 'owned-stop',
      source: 'example',
      inst_id: 'BTC-USDT',
      status: 'running',
      strategy: { kind: 'sma_cross' },
      created_at: Date.now(),
    };
    let release: (() => void) | undefined;
    await page.route('**/api/v1/pro/execution/deployments**', async (route) => {
      if (new URL(route.request().url()).pathname.endsWith('/stop')) {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        await route.fulfill({ json: { ...deployment, status: 'stopped' } });
      } else await route.fulfill({ json: { items: [deployment] } });
    });
    await page.goto('/#execution?source=example&view=strategies%3Aowned-stop');
    await page
      .locator('[data-row-key="owned-stop"]')
      .getByRole('button', { name: 'Stop', exact: true })
      .click();
    await expect.poll(() => !!release).toBeTruthy();
    if (departure === 'source')
      await page.getByLabel('Market source', { exact: true }).selectOption('okx');
    else {
      state.session = 'new-stop-session';
      await page.evaluate(() => window.dispatchEvent(new Event('tidebench:auth-required')));
      await expect(page.locator('.sidebar-session strong')).toHaveText(state.session);
    }
    const response = page.waitForResponse((r) => r.url().includes('/deployments/owned-stop/stop'));
    release!();
    await (await response).finished();
    await expect(page.getByRole('status').filter({ hasText: 'Strategy stopped' })).toHaveCount(0);
  });
