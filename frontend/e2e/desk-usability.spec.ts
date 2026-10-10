import { test, expect, type Page } from '@playwright/test';
import { sizeNotional } from '../src/lib/orderSizing';
import { price } from '../src/lib/format';
import { readWorkspaceLocation } from '../src/lib/workspaceLocation';

const tick = '0.00000001';
test('price preserves nonzero micro prices and a single tick spread', () => {
  expect(price('0.00000341', tick)).toBe('0.00000341');
  expect(price('0.00000342', tick)).toBe('0.00000342');
  expect(price('0.00000008')).not.toMatch(/^0[.,]0+$/);
  expect(price(null)).toBe('—');
});
test('notional sizing respects exact lots, native contract values and minimum size', () => {
  const spot = { inst_id: 'BTC-USDT', lot_size: '0.0001', min_size: '0.0001' };
  expect(sizeNotional('1000', '33333.33', spot, 'SPOT')?.quantity).toBe('0.03');
  const swap = {
    ...spot,
    inst_id: 'BTC-USDT-SWAP',
    base: 'BTC',
    quote: 'USDT',
    settle_ccy: 'USDT',
    ct_val_ccy: 'BTC',
    ct_val: '.01',
    ct_mult: '1',
    lot_size: '1',
    min_size: '1',
  };
  expect(sizeNotional('1000', '25000', { ...swap, ct_val: '0.01' }, 'SWAP')?.quantity).toBe('4');
  expect(sizeNotional('1000', '25000', { ...swap, ct_val: '0.1' }, 'SWAP')).toBeUndefined();
  expect(
    sizeNotional('1000', '25000', { ...swap, ct_val: '0.01', ct_val_ccy: 'USDT' }, 'SWAP'),
  ).toBeUndefined();
  expect(sizeNotional('1000', '0', spot, 'SPOT')).toBeUndefined();
  expect(sizeNotional('1', '1.00000001', { lot_size: '1', min_size: '1' }, 'SPOT')).toBeUndefined();
  expect(sizeNotional('1', '0.00000341', { lot_size: '1', min_size: '1' }, 'SPOT')?.quantity).toBe(
    '293255',
  );
});

type Fixture = {
  stale: boolean;
  future: number;
  priceTick: string;
  micro: boolean;
  mutations: string[];
};
async function desk(
  page: Page,
  entry = 'overview',
  busy = true,
  source = 'example',
): Promise<Fixture> {
  const state: Fixture = { stale: false, future: 0, priceTick: '0.1', micro: false, mutations: [] };
  const now = Date.now();
  const positions = busy
    ? Array.from({ length: 12 }, (_, i) => ({
        inst_id: `${i ? 'ASSET' + i : 'BTC'}-USDT`,
        inst_type: 'SPOT',
        side: 'long',
        quantity: '1',
        mark: String(100 + i),
        entry_price: '104',
        margin: '0',
        maintenance_margin: '0',
        liquidation_price: null,
        market_value: String(100 + i),
        unrealized_pnl: String(i - 4),
        as_of: now,
      }))
    : [];
  const account = {
    source,
    equity: busy ? '7266' : '6000',
    available_cash: busy ? '5969.97' : '6000',
    cash: '6000',
    gross_notional: busy ? '1266' : '0',
    used_margin: '0',
    maintenance_margin: '0',
    unrealized_pnl: busy ? '18' : '0',
    positions,
    as_of: now,
    economic_status: 'complete',
    valuation_status: 'example',
    execution_mode: 'local-paper',
  };
  await page.addInitScript(() => {
    localStorage.setItem('tidebench:language', 'en');
    localStorage.setItem('tidebench:source', 'example');
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    if (route.request().method() !== 'GET') {
      state.mutations.push(path);
      return route.fulfill({
        status: 405,
        json: { error: { message: 'Read-only desk fixture.' } },
      });
    }
    let json: unknown = { items: [] };
    if (path === '/auth/status')
      json = {
        authenticated: true,
        auth_required: true,
        setup_required: false,
        user: { id: 'desk-usability', username: 'desk', role: 'admin' },
      };
    else if (path === '/system') json = { version: '0.13.0', execution: 'local-paper' };
    else if (path === '/pro/execution/account') json = account;
    else if (path === '/pro/execution/risk') json = { source, halted: false, max_leverage: 10 };
    else if (path === '/pro/execution/orders')
      json = {
        items: busy
          ? Array.from({ length: 300 }, (_, i) => ({
              id: `order-${i}`,
              source: 'example',
              inst_id: `${i % 2 ? 'ETH' : 'BTC'}-USDT`,
              side: 'buy',
              quantity: '0.01',
              status: 'pending',
              order_type: 'limit',
              limit_price: '10',
              reduce_only: false,
              created_at: now - i,
            }))
          : [],
      };
    else if (path === '/pro/execution/analytics')
      json = {
        source,
        status: 'available',
        summary: { gross_notional: busy ? '1266' : '0', net_notional: busy ? '1266' : '0' },
        assets: [],
        input_snapshot: { account },
      };
    else if (path === '/pro/ops') json = { health: { status: 'ok' }, incidents: [], jobs: [] };
    else if (path === '/pro/research/runs')
      json = {
        items: busy
          ? Array.from({ length: 8 }, (_, i) => ({
              id: `study-${i}`,
              source: 'example',
              status: 'completed',
              mode: 'train_test',
              created_at: now - i,
              config: { strategy: { kind: 'sma_cross' }, fee_bps: '10', slippage_bps: '5' },
            }))
          : [],
      };
    else if (path === '/pro/catalog/datasets')
      json = {
        items: [
          {
            id: 'a'.repeat(32),
            source,
            inst_id: 'BTC-USDT',
            kind: 'trade',
            bar: '1H',
            start: Date.UTC(2025, 0, 1),
            end: Date.UTC(2025, 1, 1),
            content_hash: 'b'.repeat(64),
          },
        ],
      };
    else if (path === '/pro/catalog/instruments')
      json = {
        items: ['BTC', 'ETH'].map((asset) => ({
          inst_id: `${asset}-USDT${url.searchParams.get('inst_type') === 'SWAP' ? '-SWAP' : ''}`,
          state: 'live',
        })),
      };
    else if (path === '/pro/market') {
      const symbol = url.searchParams.get('inst_id') ?? 'BTC-USDT';
      const swap = symbol.endsWith('-SWAP');
      json = {
        ts: state.stale ? now - 60000 : Date.now() + state.future,
        last: state.micro ? '0.00000341' : '100',
        bid: state.micro ? '0.00000341' : '100',
        ask: state.micro ? '0.00000342' : '100.1',
        mark: state.micro ? '0.00000341' : '100',
        instrument: {
          inst_id: symbol,
          inst_type: swap ? 'SWAP' : 'SPOT',
          base: symbol.split('-')[0],
          quote: 'USDT',
          settle_ccy: 'USDT',
          ct_val_ccy: symbol.split('-')[0],
          ct_val: '0.01',
          ct_mult: '1',
          tick_size: state.priceTick,
          lot_size: swap ? '1' : '0.0001',
          min_size: swap ? '1' : '0.0001',
        },
      };
    }
    await route.fulfill({ json });
  });
  await page.goto(`/#${entry}?source=${source}&view=${entry === 'execution' ? 'positions' : ''}`);
  return state;
}

test('a busy desk keeps account and current book before research continuation', async ({
  page,
}, info) => {
  const state = await desk(page);
  await expect(page.locator('.trader-metrics')).toContainText('7,266.00');
  const book = page.locator('.overview-book');
  await expect(book).toContainText('BTC-USDT');
  const accountBox = await page.locator('.trader-metrics').boundingBox();
  const bookBox = await book.boundingBox();
  expect(accountBox!.y).toBeLessThan(info.project.name === 'mobile' ? 380 : 320);
  expect(bookBox!.y).toBeLessThan(info.project.name === 'mobile' ? 650 : 470);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).toBeTruthy();
  expect(state.mutations).toEqual([]);
  await page.screenshot({
    path: `../docs/assets/desk-overview-v0.13-${info.project.name}.png`,
    animations: 'disabled',
  });
  if (info.project.name === 'mobile') {
    await book.locator('.desk-compact-row[data-row-key="BTC-USDT"]').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: '../docs/assets/desk-book-v0.13-mobile.png',
      animations: 'disabled',
    });
  }
});

test('execution opens its book first and an explicit ticket sizes by budget without submitting', async ({
  page,
}, info) => {
  const state = await desk(page, 'execution');
  await expect(page.locator('.execution-ticket')).toHaveCount(0);
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await expect(page.locator('.execution-ticket')).toBeVisible();
  await page.getByText('Size by USDT notional', { exact: true }).click();
  await page.getByLabel('Notional budget (USDT)', { exact: true }).fill('1');
  await expect(
    page.getByRole('button', { name: 'Use calculated quantity', exact: true }),
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Use calculated quantity', exact: true }).click();
  await expect(page.getByLabel('Quantity', { exact: false })).not.toHaveValue('');
  await expect(page.locator('.ticket-quotes')).toContainText('100.0 / 100.1');
  expect(state.mutations).toEqual([]);
  await page.locator('.execution-ticket').scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `../docs/assets/desk-execution-v0.13-${info.project.name}.png`,
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'Close order ticket', exact: true }).click();
  await expect(page.locator('.execution-ticket')).toHaveCount(0);
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await expect(page.getByLabel('Quantity', { exact: false })).not.toHaveValue('');
});

test('market search filters and keyboard inspection preserve market identity', async ({ page }) => {
  const state = await desk(page, 'overview', false);
  await page.getByRole('button', { name: 'Search markets, Command K', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Find a market', exact: true });
  await dialog.getByRole('button', { name: 'USDT perpetuals', exact: true }).click();
  await expect(dialog.locator('[data-market="BTC-USDT"]')).toHaveCount(0);
  await dialog.getByLabel('Search market symbol', { exact: true }).fill('ETH');
  await dialog.getByLabel('Search market symbol', { exact: true }).press('ArrowDown');
  await expect(
    dialog.getByRole('button', { name: 'Inspect ETH-USDT-SWAP', exact: true }),
  ).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/symbol=ETH-USDT-SWAP/);
  expect(state.mutations).toEqual([]);
});

test('data preparation reload keeps the research product, interval and UTC window', async ({
  page,
}) => {
  const state = await desk(page, 'overview', false);
  const start = Date.UTC(2025, 0, 1),
    end = start + 40 * 3600000;
  await page.goto(
    `/#data?source=example&view=packages&return=advanced&dataProduct=SWAP&dataBar=4H&dataMarket=ETH-USDT-SWAP&dataStart=${start}&dataEnd=${end}`,
  );
  await expect(page.getByLabel('Product', { exact: true })).toHaveValue('SWAP');
  await expect(page.getByLabel('Interval', { exact: true })).toHaveValue('4H');
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ETH-USDT-SWAP');
  await page.reload();
  await expect(page.getByLabel('Product', { exact: true })).toHaveValue('SWAP');
  await expect(page.getByLabel('Start (UTC)', { exact: true })).toHaveValue('2025-01-01T00:00');
  expect(state.mutations).toEqual([]);
});

test('invalid data timestamps are discarded independently without losing a valid window edge', () => {
  const valid = Date.UTC(2025, 0, 1);
  const location = readWorkspaceLocation(
    `#data?source=okx&dataProduct=SWAP&dataBar=4H&dataStart=${valid}&dataEnd=9999999999999999`,
    'example',
  );
  expect(location?.dataIntent?.startTs).toBe(valid);
  expect(location?.dataIntent?.endTs).toBeUndefined();
});

test('an empty account has no search controls for an absent book', async ({ page }) => {
  await desk(page, 'overview', false);
  await expect(page.getByText('No open positions', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Search positions', { exact: true })).toHaveCount(0);
  await expect(page.locator('.overview-book')).toContainText('No open positions');
});

test('research uses the width of the desk for market, strategy and evaluation', async ({
  page,
}, info) => {
  const state = await desk(page, 'research', false);
  await page.getByLabel('Dataset', { exact: true }).selectOption('a'.repeat(32));
  const market = await page.locator('[aria-labelledby="research-market-section"]').boundingBox();
  const strategy = await page
    .locator('[aria-labelledby="research-strategy-section"]')
    .boundingBox();
  const evaluation = await page
    .locator('[aria-labelledby="research-evaluation-section"]')
    .boundingBox();
  if (info.project.name === 'desktop') {
    expect(Math.abs(market!.y - strategy!.y)).toBeLessThan(2);
    expect(Math.abs(strategy!.y - evaluation!.y)).toBeLessThan(2);
    expect(strategy!.x).toBeGreaterThan(market!.x);
    expect(evaluation!.x).toBeGreaterThan(strategy!.x);
  } else {
    expect(strategy!.y).toBeGreaterThan(market!.y);
    expect(evaluation!.y).toBeGreaterThan(strategy!.y);
  }
  await expect(page.getByRole('button', { name: 'Run research', exact: true })).toHaveCount(1);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: `../docs/assets/desk-research-v0.13-${info.project.name}.png`,
    animations: 'disabled',
  });
  if (info.project.name === 'desktop') {
    await page.setViewportSize({ width: 1111, height: 900 });
    const mediumMarket = await page
      .locator('[aria-labelledby="research-market-section"]')
      .boundingBox();
    const mediumStrategy = await page
      .locator('[aria-labelledby="research-strategy-section"]')
      .boundingBox();
    const mediumEvaluation = await page
      .locator('[aria-labelledby="research-evaluation-section"]')
      .boundingBox();
    expect(Math.abs(mediumMarket!.y - mediumStrategy!.y)).toBeLessThan(2);
    expect(Math.abs(mediumStrategy!.y - mediumEvaluation!.y)).toBeLessThan(2);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    ).toBeTruthy();
  }
  expect(state.mutations).toEqual([]);
});

test('budget sizing refuses a stale OKX quote and non-market fill semantics', async ({ page }) => {
  const state = await desk(page, 'execution', false, 'okx');
  state.stale = true;
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await page.getByLabel('Market', { exact: true }).selectOption('ETH-USDT');
  await page.getByText('Size by USDT notional', { exact: true }).click();
  await page.getByLabel('Notional budget (USDT)', { exact: true }).fill('100');
  await expect(
    page.getByRole('button', { name: 'Use calculated quantity', exact: true }),
  ).toBeDisabled();
  await page.getByLabel('Order type', { exact: true }).selectOption('limit');
  await expect(page.locator('.budget-sizing')).toContainText(
    'Limit and stop orders require explicit native quantity',
  );
  await expect(
    page.getByRole('button', { name: 'Use calculated quantity', exact: true }),
  ).toBeDisabled();
  expect(state.mutations).toEqual([]);
});

for (const source of ['example', 'okx']) {
  test(`${source} budget sizing observes its own clock when the quote is ahead of wall time`, async ({
    page,
  }) => {
    const state = await desk(page, 'execution', false, source);
    state.future = 3600000;
    await page.reload();
    await page.getByRole('button', { name: 'New order', exact: true }).click();
    await page.getByText('Size by USDT notional', { exact: true }).click();
    await page.getByLabel('Notional budget (USDT)', { exact: true }).fill('100');
    const apply = page.getByRole('button', { name: 'Use calculated quantity', exact: true });
    if (source === 'example') await expect(apply).toBeEnabled();
    else await expect(apply).toBeDisabled();
    expect(state.mutations).toEqual([]);
  });
}

test('an invalid optional budget cannot block a valid native limit order preview', async ({
  page,
}) => {
  const state = await desk(page, 'execution', false);
  state.priceTick = '0.000000000001';
  await page.reload();
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await page.getByText('Size by USDT notional', { exact: true }).click();
  await page.getByLabel('Notional budget (USDT)', { exact: true }).fill('-100');
  await expect(
    page.getByRole('button', { name: 'Use calculated quantity', exact: true }),
  ).toBeDisabled();
  await page.getByText('Size by USDT notional', { exact: true }).click();
  await page.getByLabel('Order type', { exact: true }).selectOption('limit');
  await expect(page.getByLabel('Limit price', { exact: true })).toHaveAttribute(
    'step',
    '0.000000000001',
  );
  await page.getByLabel('Limit price', { exact: true }).fill('0.000000000003');
  await page.getByLabel('Quantity', { exact: false }).fill('100');
  expect(
    await page
      .locator('.execution-ticket form')
      .evaluate((form: HTMLFormElement) => form.checkValidity()),
  ).toBeTruthy();
  await page.getByRole('button', { name: 'Preview order', exact: true }).click();
  await expect.poll(() => state.mutations).toEqual(['/pro/execution/orders/preview']);
});

test('the working order book filters all records and paginates without losing identity', async ({
  page,
}, info) => {
  const state = await desk(page);
  const book = page.locator('.overview-book');
  await book.getByRole('tab', { name: /^Pending orders/ }).click();
  await book.getByLabel('Search orders', { exact: true }).fill('ETH');
  await expect(book.locator('.table-pagination')).toContainText('1–100 / 150');
  const records =
    info.project.name === 'mobile'
      ? book.locator('.desk-compact-records')
      : book.locator('.dense-table tbody');
  await expect(records.locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'order-1',
  );
  if (info.project.name === 'mobile') {
    const box = await records.boundingBox();
    expect(box!.height).toBeLessThan(650);
    await records.evaluate((el) => {
      el.scrollTop = 200;
    });
  }
  await book.getByRole('button', { name: 'Next page', exact: true }).click();
  await expect(book.locator('.table-pagination')).toContainText('101–150 / 150');
  await expect(records.locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'order-201',
  );
  if (info.project.name === 'mobile') expect(await records.evaluate((el) => el.scrollTop)).toBe(0);
  await book.getByLabel('Search orders', { exact: true }).fill('BTC');
  await expect(book.locator('.table-pagination')).toContainText('1–100 / 150');
  await expect(records.locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'order-0',
  );
  expect(state.mutations).toEqual([]);
});

test('a micro-price ticket preserves the bid-ask tick and native budget quantity', async ({
  page,
}) => {
  const state = await desk(page, 'execution', false);
  state.micro = true;
  state.priceTick = '0.00000001';
  await page.reload();
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  await expect(page.locator('.ticket-quotes')).toContainText('0.00000341 / 0.00000342');
  await page.getByText('Size by USDT notional', { exact: true }).click();
  await page.getByLabel('Notional budget (USDT)', { exact: true }).fill('1');
  await page.getByRole('button', { name: 'Use calculated quantity', exact: true }).click();
  await expect(page.getByLabel('Quantity', { exact: false })).toHaveValue('292397.6608');
  expect(state.mutations).toEqual([]);
});

test('position sorting uses numeric P&L and filtering preserves the actual position action', async ({
  page,
}, info) => {
  const state = await desk(page);
  const book = page.locator('.overview-book');
  const records =
    info.project.name === 'mobile'
      ? book.locator('.desk-compact-records')
      : book.locator('.dense-table tbody');
  if (info.project.name === 'mobile')
    await book
      .getByLabel('Sort by', { exact: true })
      .selectOption(JSON.stringify(['unrealized_pnl', 'desc']));
  else {
    await book.getByRole('button', { name: 'Unrealized P&L', exact: true }).click();
    await book.getByRole('button', { name: 'Unrealized P&L', exact: true }).click();
  }
  await expect(records.locator('[data-row-key]').first()).toHaveAttribute(
    'data-row-key',
    'ASSET11-USDT',
  );
  await book.getByLabel('Search positions', { exact: true }).fill('BTC-USDT');
  await expect(records.locator('[data-row-key]')).toHaveCount(1);
  await records.getByRole('button', { name: 'Reduce account position', exact: true }).click();
  await expect(page).toHaveURL(/view=protect(?:%3A|:)BTC-USDT/);
  expect(state.mutations).toEqual([]);
});
