import { expect, test, type Page } from '@playwright/test';
import { desk } from './desk-review-fixture';

const displayKey = 'tidebench:display:v1:desk-usability:example';
async function layout(page: Page) {
  await page.getByRole('button', { name: 'Workspace layout and commands', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Workspace layout', exact: true });
  await expect(dialog).toBeVisible();
  return dialog;
}

test('display layout and saved views restore across navigation without restoring an order instruction', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const dialog = await layout(page);
  await dialog.getByLabel('Information density', { exact: true }).selectOption('compact');
  await dialog.getByLabel('Order ticket width', { exact: true }).selectOption('wide');
  await dialog.getByLabel('Workspace name', { exact: true }).fill('Account review');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dialog.getByRole('status')).toContainText('View and layout saved');
  const stored = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!), displayKey);
  expect(stored.workspaces[0].location).toMatchObject({
    page: 'execution',
    source: 'example',
    view: 'positions',
  });
  expect(Object.keys(stored).sort()).toEqual([
    'density',
    'favorites',
    'monitorLayout',
    'recentMarkets',
    'ticketWidth',
    'version',
    'workspaces',
  ]);
  expect(JSON.stringify(stored)).not.toMatch(
    /quantity|preview_hash|csrf_token|reduce_only|permission/,
  );
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.goto('/#overview?source=example');
  await page.reload();
  await expect(page.locator('body')).toHaveAttribute('data-density', 'compact');
  await expect(page.locator('body')).toHaveAttribute('data-ticket-width', 'wide');
  await (await layout(page)).getByRole('button', { name: /^Account review/ }).click();
  await expect(page).toHaveURL(/#execution\?source=example.*view=positions/);
  await expect(page.locator('#desk-order-ticket')).toHaveCount(0);
  const restored = await layout(page);
  await restored.getByRole('button', { name: 'Restore default layout', exact: true }).click();
  await expect(restored.getByLabel('Information density', { exact: true })).toHaveValue(
    'comfortable',
  );
  await expect(restored.getByRole('button', { name: /^Account review/ })).toBeVisible();
  expect(state.mutations).toEqual([]);
});

test('display preferences remain isolated by data source and authenticated user', async ({
  page,
}) => {
  const state = await desk(page);
  const dialog = await layout(page);
  await dialog.getByLabel('Monitor layout', { exact: true }).selectOption('focus');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await expect(page.locator('body')).toHaveAttribute('data-monitor-layout', 'balanced');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await expect(page.locator('body')).toHaveAttribute('data-monitor-layout', 'focus');
  state.session = 'different-user';
  await page.reload();
  await expect(page.locator('body')).toHaveAttribute('data-monitor-layout', 'balanced');
  state.session = 'desk-usability';
  await page.reload();
  await expect(page.locator('body')).toHaveAttribute('data-monitor-layout', 'focus');
  expect(state.mutations).toEqual([]);
});

test('workspace commands navigate with the keyboard and ignore focused inputs and IME', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  const search = page.getByLabel('Search positions', { exact: true });
  await search.focus();
  await search.press('Control+Shift+k');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('heading', { name: 'Execution', exact: true, level: 1 }).click();
  await page.evaluate(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, shiftKey: true, isComposing: true }),
    );
  });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.locator('body').press('Control+Shift+k');
  const commands = page.getByRole('dialog', { name: 'Workspace commands', exact: true });
  await expect(commands).toBeVisible();
  await expect(page.locator('.app-shell')).toHaveAttribute('inert', '');
  const input = commands.getByRole('combobox', { name: 'Find a workspace command', exact: true });
  await expect(input).toBeFocused();
  await input.fill('working orders');
  await input.press('Enter');
  await expect(page).toHaveURL(/#execution\?.*view=orders/);
  await expect(page.locator('.app-shell')).not.toHaveAttribute('inert', '');
  await page.locator('body').press('Control+Shift+k');
  await input.fill('paper order');
  await input.press('Enter');
  await expect(page.locator('#desk-order-ticket')).toBeVisible();
  await expect(page.locator('.ticket-account-context')).toContainText('5,969.97');
  expect(state.mutations).toEqual([]);
});

test('market search and favorites retain the paper account context without placing orders', async ({
  page,
}, info) => {
  const state = await desk(page, 'execution');
  if (info.project.name === 'desktop') await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const ticket = page.locator('#desk-order-ticket');
  await expect(ticket).toBeVisible();
  await expect(ticket.locator('.ticket-account-context')).toContainText('5,969.97');
  await expect(ticket.locator('.ticket-account-context')).toContainText('New risk enabled');
  if (info.project.name === 'desktop') {
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    const book = (await page.locator('.account-book-panel').boundingBox())!;
    const order = (await ticket.boundingBox())!;
    expect(Math.abs(book.y - order.y)).toBeLessThanOrEqual(3);
  }
  await ticket.getByRole('combobox', { name: 'Search ticket markets', exact: true }).fill('ETH');
  await ticket
    .getByRole('listbox', { name: 'Matching ticket markets', exact: true })
    .getByRole('option', { name: 'ETH-USDT', exact: true })
    .click();
  await expect(ticket.getByRole('combobox', { name: 'Market', exact: true })).toHaveValue(
    'ETH-USDT',
  );
  await ticket.getByRole('button', { name: 'Favorite ETH-USDT', exact: true }).click();
  await page.reload();
  await expect(
    ticket.getByRole('button', { name: 'Unfavorite ETH-USDT', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    ticket
      .getByRole('listbox', { name: 'Matching ticket markets', exact: true })
      .getByRole('option', { name: 'ETH-USDT', exact: true }),
  ).toBeVisible();
  await expect(ticket.locator('.ticket-account-context')).toContainText('5,969.97');
  expect(state.mutations).toEqual([]);
});

test('damaged preferences and unavailable browser storage have visible safe recovery', async ({
  page,
}) => {
  const state = await desk(page, 'overview', false);
  await page.evaluate((key) => localStorage.setItem(key, '{broken'), displayKey);
  await page.reload();
  let dialog = await layout(page);
  await expect(dialog.getByRole('status')).toContainText('could not be restored');
  await expect(dialog.getByLabel('Information density', { exact: true })).toHaveValue(
    'comfortable',
  );
  await dialog.getByRole('button', { name: 'Restore default layout', exact: true }).click();
  await expect(dialog.getByRole('status')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.addInitScript(() => {
    Object.defineProperty(Storage.prototype, 'getItem', {
      configurable: true,
      value() {
        throw new DOMException('Blocked', 'SecurityError');
      },
    });
    Object.defineProperty(Storage.prototype, 'setItem', {
      configurable: true,
      value() {
        throw new DOMException('Blocked', 'SecurityError');
      },
    });
  });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Trading overview', exact: true })).toBeVisible();
  dialog = await layout(page);
  await dialog.getByLabel('Information density', { exact: true }).selectOption('compact');
  await expect(dialog.getByRole('status')).toContainText('cannot be saved');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.locator('body')).toHaveAttribute('data-density', 'compact');
  await page
    .getByRole('combobox', { name: 'Interface language', exact: true })
    .selectOption('zh-CN');
  await expect(page.getByRole('heading', { name: '交易总览', exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]);
});

test('empty account gives useful next work and research records have identifiable names', async ({
  page,
}) => {
  const state = await desk(page, 'overview', false);
  const empty = page.locator('.overview-book .empty-state');
  await expect(empty).toContainText('No open positions');
  const box = (await empty.boundingBox())!;
  expect(box.height).toBeLessThanOrEqual(100);
  await page.unroute('**/api/v1/**');
  const busyState = await desk(page);
  await page.reload();
  await expect(
    page
      .locator('.overview-research')
      .getByRole('button', { name: /Moving average crossover/ })
      .first(),
  ).toBeVisible();
  await page
    .locator('.overview-research')
    .getByRole('button', { name: /Moving average crossover/ })
    .first()
    .click();
  await expect(page).toHaveURL(/#research\?.*run=study-0/);
  expect(state.mutations).toEqual([]);
  expect(busyState.mutations).toEqual([]);
});

test('saved read-only ledger and selected-order views keep their actual destinations', async ({
  page,
}) => {
  const state = await desk(page, 'execution');
  for (const view of ['ledger', 'orders:order-9']) {
    await page.goto(`/#execution?source=example&view=${encodeURIComponent(view)}`);
    const dialog = await layout(page);
    await dialog.getByLabel('Workspace name', { exact: true }).fill(`Review ${view}`);
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  }
  await page.goto('/#overview?source=example');
  await (await layout(page)).getByRole('button', { name: /^Review ledger/ }).click();
  await expect(page).toHaveURL(/view=ledger/);
  await (await layout(page)).getByRole('button', { name: /^Review orders:order-9/ }).click();
  await expect(page).toHaveURL(/view=orders%3Aorder-9/);
  await expect(page.locator('#desk-order-ticket')).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test('an open ticket retains incomplete funding conditions beside available cash', async ({
  page,
}) => {
  const state = await desk(page, 'execution', false);
  await page.route('**/api/v1/pro/execution/account?*', (route) =>
    route.fulfill({
      json: {
        source: 'example',
        equity: '6000',
        cash: '6000',
        available_cash: '6000',
        used_margin: '0',
        positions: [],
        as_of: Date.now(),
        economic_status: 'funding_schedule_unavailable',
        valuation_status: 'example',
        execution_mode: 'local-paper',
      },
    }),
  );
  await page.reload();
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const context = page.locator('.ticket-account-context');
  await expect(context).toContainText('6,000.00');
  await expect(context).toContainText('Funding schedule needs attention');
  await expect(context).toContainText('New risk enabled');
  expect(state.mutations).toEqual([]);
});
