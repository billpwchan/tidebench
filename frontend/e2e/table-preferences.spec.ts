import { test, expect, type Locator, type Page } from '@playwright/test';
import { desk } from './desk-review-fixture';

const prefix = 'tidebench:table-preferences:v1:';

test('perpetual liquidation risk becomes a protected visible field when the book includes contracts', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  const spotTable = positionTable(page);
  const spotSettings = await openColumns(spotTable);
  await spotSettings
    .getByRole('checkbox', { name: 'Show column: Liquidation price', exact: true })
    .uncheck();
  const spotKey = await storageKey(spotTable);
  await page.route('**/api/v1/pro/execution/account?*', (route) =>
    route.fulfill({
      json: {
        source: 'example',
        equity: '6000',
        cash: '6000',
        available_cash: '5966',
        used_margin: '34',
        as_of: Date.now(),
        economic_status: 'complete',
        valuation_status: 'example',
        execution_mode: 'local-paper',
        positions: [
          {
            inst_id: 'BTC-USDT-SWAP',
            inst_type: 'SWAP',
            side: 'long',
            quantity: '100',
            mark: '100',
            entry_price: '104',
            margin: '34',
            maintenance_margin: '0.5',
            liquidation_price: '70.5',
            liquidation_price_kind: 'isolated_estimate',
            leverage: 3,
            unrealized_pnl: '-4',
            as_of: Date.now(),
          },
        ],
      },
    }),
  );
  await page.reload();
  const swapTable = positionTable(page);
  await expect(swapTable).toContainText('70.5');
  const swapSettings = await openColumns(swapTable);
  const liquidation = swapSettings.getByRole('checkbox', {
    name: 'Show column: Liquidation price',
    exact: true,
  });
  await expect(liquidation).toBeChecked();
  await expect(liquidation).toBeDisabled();
  expect(await storageKey(swapTable)).not.toBe(spotKey);
  expect(state.mutations).toEqual([]);
});
function positionTable(page: Page) {
  return page
    .locator('.data-table-region')
    .filter({ has: page.getByLabel('Search positions', { exact: true }) });
}
async function openColumns(table: Locator) {
  const summary = table.locator('.table-preferences > summary');
  if (!(await summary.evaluate((element) => (element.parentElement as HTMLDetailsElement).open))) {
    await summary.focus();
    await summary.press('Enter');
  }
  const settings = table.getByRole('group', { name: 'Column settings', exact: true });
  await expect(settings).toBeVisible();
  return settings;
}
async function storageKey(table: Locator) {
  const key = await table.getAttribute('data-table-preference-key');
  expect(key).toMatch(/^tidebench:table-preferences:v1:/);
  return key!;
}
function records(table: Locator, mobile: boolean) {
  return table.locator(mobile ? '.desk-compact-records' : '.dense-table tbody');
}
async function descendingPnl(table: Locator, mobile: boolean) {
  if (mobile)
    await table
      .getByLabel('Sort by', { exact: true })
      .selectOption(JSON.stringify(['unrealized_pnl', 'desc']));
  else {
    await table.getByRole('button', { name: 'Unrealized P&L', exact: true }).click();
    await table.getByRole('button', { name: 'Unrealized P&L', exact: true }).click();
  }
}

test('columns, search and numeric sorting restore while expanded financial details remain transient', async ({
  page,
}, info) => {
  const state = await desk(page, 'execution');
  const table = positionTable(page);
  const mobile = info.project.name === 'mobile';
  await expect(table).toBeVisible();
  await descendingPnl(table, mobile);
  await table.getByLabel('Search positions', { exact: true }).fill('ASSET');
  const settings = await openColumns(table);
  const originalOrder = await settings
    .locator('li[data-column]')
    .evaluateAll((items) => items.map((item) => item.getAttribute('data-column')));
  for (const label of ['Market', 'Quantity', 'Unrealized P&L', 'Actions']) {
    const checkbox = settings.getByRole('checkbox', { name: `Show column: ${label}`, exact: true });
    await expect(checkbox).toBeChecked();
    await expect(checkbox).toBeDisabled();
  }
  await settings.getByRole('checkbox', { name: 'Show column: Entry price', exact: true }).uncheck();
  const move = settings.getByRole('button', { name: 'Move column up: Margin', exact: true });
  await move.focus();
  await move.press('Enter');
  await expect(move).toBeFocused();
  const changedOrder = await settings
    .locator('li[data-column]')
    .evaluateAll((items) => items.map((item) => item.getAttribute('data-column')));
  expect(changedOrder.indexOf('margin')).toBeLessThan(changedOrder.indexOf('mark'));
  expect(changedOrder.at(-1)).toBe('protect');
  await expect(
    settings.getByRole('button', { name: 'Move column down: Unrealized P&L', exact: true }),
  ).toBeDisabled();
  await expect(
    settings.getByRole('button', { name: 'Move column up: Market', exact: true }),
  ).toBeDisabled();
  await table.locator('.table-preferences > summary').press('Enter');
  const first = records(table, mobile).locator('[data-row-key]').first();
  await expect(first).toHaveAttribute('data-row-key', 'ASSET11-USDT');
  await expect(first).toContainText('7.00');
  if (mobile) {
    await first.locator('.desk-compact-details > summary').click();
    await expect(first.locator('.desk-compact-details')).toHaveAttribute('open', '');
    await expect(first.locator('dt').filter({ hasText: /^Entry price$/ })).toHaveCount(0);
    const keys = await first
      .locator('.desk-compact-summary [data-column]')
      .evaluateAll((items) => items.map((item) => item.getAttribute('data-column')));
    for (const key of ['quantity', 'unrealized_pnl'])
      expect(keys.indexOf(key)).toBeLessThan(keys.indexOf('protect'));
  } else {
    await first.getByRole('button', { name: /^More details/ }).click();
    await expect(first.getByRole('button', { name: /^More details/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    await expect(
      table.locator('.desk-row-details dt').filter({ hasText: /^Entry price$/ }),
    ).toHaveCount(0);
  }
  const key = await storageKey(table);
  const saved = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!), key);
  expect(Object.keys(saved).sort()).toEqual(['hidden', 'order', 'query', 'sort', 'version']);
  expect(saved.hidden).toEqual(['entry_price']);
  expect(saved.sort).toEqual({ key: 'unrealized_pnl', direction: 'desc' });
  await page.reload();
  await expect(table.getByLabel('Search positions', { exact: true })).toHaveValue('ASSET');
  await expect(records(table, mobile).locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'ASSET11-USDT',
  );
  if (mobile) await expect(table.locator('.desk-compact-details[open]')).toHaveCount(0);
  else await expect(table.locator('.desk-row-details')).toHaveCount(0);
  const restored = await openColumns(table);
  await expect(
    restored.getByRole('checkbox', { name: 'Show column: Entry price', exact: true }),
  ).not.toBeChecked();
  expect(
    await restored
      .locator('li[data-column]')
      .evaluateAll((items) => items.map((item) => item.getAttribute('data-column'))),
  ).toEqual(changedOrder);
  await restored.getByRole('button', { name: 'Restore table defaults', exact: true }).click();
  await expect(table.getByLabel('Search positions', { exact: true })).toHaveValue('');
  await expect(records(table, mobile).locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'BTC-USDT',
  );
  await expect(
    restored.getByRole('checkbox', { name: 'Show column: Entry price', exact: true }),
  ).toBeChecked();
  expect(
    await restored
      .locator('li[data-column]')
      .evaluateAll((items) => items.map((item) => item.getAttribute('data-column'))),
  ).toEqual(originalOrder);
  expect(await page.evaluate((key) => localStorage.getItem(key), key)).toBeNull();
  expect(state.mutations).toEqual([]);
});

test('table preferences are isolated by user, source and page without modifying account data', async ({
  page,
}) => {
  const state = await desk(page, 'overview');
  await positionTable(page).getByLabel('Search positions', { exact: true }).fill('BTC');
  const overviewKey = await storageKey(positionTable(page));
  await page.goto('/#execution?source=example&view=positions');
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue('');
  await positionTable(page).getByLabel('Search positions', { exact: true }).fill('ASSET1');
  const executionKey = await storageKey(positionTable(page));
  expect(executionKey).not.toBe(overviewKey);
  await page.goto('/#overview?source=example');
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue(
    'BTC',
  );
  await page.goto('/#execution?source=okx&view=positions');
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue('');
  expect(await storageKey(positionTable(page))).not.toBe(executionKey);
  await page.goto('/#execution?source=example&view=positions');
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue(
    'ASSET1',
  );
  state.session = 'other-table-user';
  await page.reload();
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue('');
  expect(await storageKey(positionTable(page))).not.toBe(executionKey);
  state.session = 'desk-usability';
  await page.reload();
  await expect(positionTable(page).getByLabel('Search positions', { exact: true })).toHaveValue(
    'ASSET1',
  );
  expect(state.mutations).toEqual([]);
});

test('different table schemas on the same page restore their own layout and query', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  const positions = positionTable(page);
  await positions.getByLabel('Search positions', { exact: true }).fill('BTC');
  const settings = await openColumns(positions);
  await settings.getByRole('checkbox', { name: 'Show column: Entry price', exact: true }).uncheck();
  const positionKey = await storageKey(positions);
  await page.goto('/#execution?source=example&view=orders');
  const orders = page
    .locator('.data-table-region')
    .filter({ has: page.getByLabel('Search orders', { exact: true }) });
  await expect(orders.getByLabel('Search orders', { exact: true })).toHaveValue('');
  const orderSettings = await openColumns(orders);
  await expect(
    orderSettings.getByRole('checkbox', { name: 'Show column: Side', exact: true }),
  ).toBeChecked();
  const orderKey = await storageKey(orders);
  const scopes = [positionKey, orderKey].map(
    (key) => JSON.parse(decodeURIComponent(key.slice(prefix.length))) as string[],
  );
  expect(scopes[0].slice(0, 2)).toEqual(scopes[1].slice(0, 2));
  expect(scopes[0][3]).not.toEqual(scopes[1][3]);
  await orders.getByLabel('Search orders', { exact: true }).fill('ETH');
  await page.goto('/#execution?source=example&view=positions');
  await expect(positions.getByLabel('Search positions', { exact: true })).toHaveValue('BTC');
  const restored = await openColumns(positions);
  await expect(
    restored.getByRole('checkbox', { name: 'Show column: Entry price', exact: true }),
  ).not.toBeChecked();
  await page.goto('/#execution?source=example&view=orders');
  await expect(orders.getByLabel('Search orders', { exact: true })).toHaveValue('ETH');
  expect(state.mutations).toEqual([]);
});

for (const corruption of [
  'required-hidden',
  'duplicate-order',
  'pinned-action-moved',
  'unsafe-extra-fields',
  'malformed',
] as const) {
  test(`${corruption} preferences show a clear warning and fall back to safe defaults`, async ({
    page,
  }) => {
    const state = await desk(page, 'execution');
    const table = positionTable(page);
    await table.getByLabel('Search positions', { exact: true }).fill('BTC');
    const key = await storageKey(table);
    await page.evaluate(
      ({ key, corruption }) => {
        const saved = JSON.parse(localStorage.getItem(key)!);
        if (corruption === 'malformed') return localStorage.setItem(key, '{broken');
        if (corruption === 'required-hidden') saved.hidden = ['inst_id', 'quantity'];
        if (corruption === 'duplicate-order') saved.order[1] = saved.order[0];
        if (corruption === 'pinned-action-moved') {
          const action = saved.order.pop();
          saved.order.splice(saved.order.indexOf('unrealized_pnl'), 0, action);
        }
        if (corruption === 'unsafe-extra-fields') {
          saved.selectedRows = ['BTC-USDT'];
          saved.orderParameters = { quantity: '987654321' };
        }
        localStorage.setItem(key, JSON.stringify(saved));
      },
      { key, corruption },
    );
    await page.reload();
    await expect(table.getByRole('alert')).toContainText('Saved table preferences are invalid');
    await expect(table.getByLabel('Search positions', { exact: true })).toHaveValue('');
    const settings = await openColumns(table);
    await expect(
      settings.getByRole('checkbox', { name: 'Show column: Market', exact: true }),
    ).toBeChecked();
    await expect(
      settings.getByRole('checkbox', { name: 'Show column: Quantity', exact: true }),
    ).toBeDisabled();
    await settings.getByRole('button', { name: 'Restore table defaults', exact: true }).click();
    await expect(table.getByRole('alert')).toHaveCount(0);
    expect(await page.evaluate((key) => localStorage.getItem(key), key)).toBeNull();
    expect(state.mutations).toEqual([]);
  });
}

test('unavailable storage reports session-only changes while keyboard column controls still work', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  await page.addInitScript((prefix) => {
    for (const method of ['getItem', 'setItem', 'removeItem'] as const) {
      const original = Storage.prototype[method];
      Object.defineProperty(Storage.prototype, method, {
        configurable: true,
        value: function (this: Storage, key: string, value?: string) {
          if (key.startsWith(prefix))
            throw new DOMException('Storage unavailable', 'SecurityError');
          return method === 'setItem'
            ? (original as typeof Storage.prototype.setItem).call(this, key, value!)
            : (original as typeof Storage.prototype.getItem).call(this, key);
        },
      });
    }
  }, prefix);
  await page.reload();
  const table = positionTable(page);
  await expect(table.getByRole('alert')).toContainText('Changes apply to this session only');
  await table.getByLabel('Search positions', { exact: true }).fill('BTC');
  const settings = await openColumns(table);
  const optional = settings.getByRole('checkbox', {
    name: 'Show column: Entry price',
    exact: true,
  });
  await optional.focus();
  await optional.press('Space');
  await expect(optional).not.toBeChecked();
  await expect(optional).toBeFocused();
  await expect(table.getByLabel('Search positions', { exact: true })).toHaveValue('BTC');
  await settings.getByRole('button', { name: 'Restore table defaults', exact: true }).click();
  await expect(optional).toBeChecked();
  await expect(table.getByLabel('Search positions', { exact: true })).toHaveValue('');
  await expect(table.getByRole('alert')).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).toBeTruthy();
  expect(state.mutations).toEqual([]);
});

test('empty books keep the compact empty state without column controls', async ({ page }) => {
  await desk(page, 'execution', false);
  await expect(page.getByText('No positions', { exact: true })).toBeVisible();
  await expect(page.locator('.table-preferences')).toHaveCount(0);
  await expect(page.getByLabel('Search positions', { exact: true })).toHaveCount(0);
});
