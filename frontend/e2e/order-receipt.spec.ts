import { expect, test, type Page } from '@playwright/test';
import type { OrderRequest, RecordData } from '../src/proApi';

// Every API call is intercepted. These tests neither access an account database
// nor submit a venue order; only synthetic local-paper response contracts run.
type ReceiptFixture = {
  orders: RecordData[];
  events: RecordData[];
  ledger: RecordData[];
  requests: { path: string; body: RecordData; key?: string }[];
  nextStatus: string;
  nextError?: number;
  delay: boolean;
  release?: () => void;
  session: string;
};
const submittedId = 'receipt-order-exact-1';
const instrument = {
  inst_id: 'BTC-USDT',
  inst_type: 'SPOT',
  base: 'BTC',
  quote: 'USDT',
  settle_ccy: 'USDT',
  quantity_unit: 'base',
  tick_size: '0.01',
  lot_size: '0.0001',
  min_size: '0.0001',
};
function serverOrder(id: string, status = 'pending'): RecordData {
  return {
    id,
    source: 'example',
    inst_id: 'BTC-USDT',
    side: 'buy',
    order_type: 'limit',
    quantity: '0.01',
    requested_quantity: '0.01',
    filled_quantity: status === 'filled' ? '0.01' : '0',
    canceled_quantity: '0',
    limit_price: '90',
    price: '123.45',
    fee: '0.001234',
    status,
    execution_mode: 'local-paper',
    instrument,
    created_at: Date.now(),
    updated_at: Date.now(),
  };
}
async function fixture(page: Page, view = 'positions'): Promise<ReceiptFixture> {
  const state: ReceiptFixture = {
    orders: [serverOrder('unrelated-same-market', 'filled')],
    events: [],
    ledger: [],
    requests: [],
    nextStatus: 'filled',
    delay: false,
    session: 'receipt-user',
  };
  await page.addInitScript(() => {
    localStorage.setItem('tidebench:language', 'en');
    localStorage.setItem('tidebench:source', 'example');
  });
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    const method = route.request().method();
    const source = url.searchParams.get('source') ?? 'example';
    if (method === 'POST') {
      const body = (route.request().postDataJSON() ?? {}) as RecordData;
      state.requests.push({ path, body, key: route.request().headers()['idempotency-key'] });
      if (path === '/pro/execution/orders/preview') {
        expect(body.source).toBe('example');
        return route.fulfill({
          json: {
            order: body,
            estimated_price: '999.99',
            notional: '10',
            fee: '0.09',
            required_margin: '10',
            estimated_cash_after: '9990',
            warnings: [],
            market_snapshot: { ts: Date.now() },
          },
        });
      }
      if (path === '/pro/execution/orders') {
        expect(body.source).toBe('example');
        if (state.delay) await new Promise<void>((resolve) => (state.release = resolve));
        if (state.nextError) {
          return route.fulfill({
            status: state.nextError,
            json: {
              error: {
                code: 'fixture_rejection',
                message: 'Local paper risk rejected this request.',
              },
            },
          });
        }
        const response = {
          ...serverOrder(submittedId, state.nextStatus),
          ...body,
          filled_quantity: state.nextStatus === 'filled' ? body.quantity : '0',
        };
        state.orders.unshift(response);
        state.events.push(
          {
            id: 1,
            source: 'example',
            ts: Date.now(),
            kind: `pro.order_${state.nextStatus}`,
            summary: 'Exact order event',
            details: { order_id: submittedId },
          },
          {
            id: 2,
            source: 'okx',
            ts: Date.now(),
            kind: 'wrong.source',
            summary: 'Other source event',
            details: { order_id: submittedId },
          },
          {
            id: 3,
            source: 'example',
            ts: Date.now(),
            kind: 'wrong.order',
            summary: 'Other order event',
            details: { order_id: `${submittedId}-suffix` },
          },
        );
        state.ledger.push(
          {
            id: 1,
            source: 'example',
            ts: Date.now(),
            asset: 'USDT',
            account: 'cash',
            debit: '0',
            credit: '1.2345',
            memo: 'Exact ledger entry',
            reference: submittedId,
          },
          {
            id: 2,
            source: 'okx',
            ts: Date.now(),
            asset: 'USDT',
            account: 'cash',
            debit: '0',
            credit: '1.2345',
            memo: 'Other source ledger',
            reference: submittedId,
          },
        );
        return route.fulfill({ status: 201, json: response });
      }
      if (path.endsWith('/cancel')) {
        const id = path.split('/').at(-2);
        const selected = state.orders.find((order) => order.id === id);
        expect(selected?.source).toBe('example');
        Object.assign(selected!, {
          status: 'canceled',
          filled_quantity: '0',
          canceled_quantity: selected!.requested_quantity,
          cancellation_reason: 'operator_canceled',
          updated_at: Date.now(),
        });
        state.events.push({
          id: 4,
          source: 'example',
          ts: Date.now(),
          kind: 'pro.order_canceled',
          summary: 'Exact cancellation event',
          details: { order_id: id },
        });
        return route.fulfill({ json: selected });
      }
      throw new Error(`Unexpected mutation in isolated fixture: ${path}`);
    }
    let json: unknown = { items: [] };
    if (path === '/auth/status') {
      json = {
        authenticated: true,
        auth_required: true,
        setup_required: false,
        csrf_token: `fixture-${state.session}`,
        user: { id: state.session, username: state.session, role: 'admin' },
      };
    } else if (path === '/system') {
      json = { version: '0.14.0', execution: 'local-paper' };
    } else if (path === '/pro/execution/account') {
      json = {
        source,
        execution_mode: 'local-paper',
        equity: '10000',
        available_cash: '10000',
        used_margin: '0',
        maintenance_margin: '0',
        unrealized_pnl: '0',
        funding_paid: '0',
        positions: [],
        economic_status: 'complete',
        valuation_status: 'example',
        as_of: Date.now(),
      };
    } else if (path === '/pro/execution/risk') {
      json = { source, halted: false, max_leverage: 10 };
    } else if (path === '/pro/execution/orders') {
      json = { items: state.orders.filter((order) => order.source === source) };
    } else if (path === '/pro/execution/ledger') {
      json = { items: state.ledger.filter((entry) => entry.source === source) };
    } else if (path === '/pro/ops/audit') {
      json = { items: state.events };
    } else if (path === '/pro/ops') {
      json = { health: { status: 'ok' }, incidents: [], jobs: [] };
    } else if (path === '/pro/catalog/instruments') {
      const swap = url.searchParams.get('inst_type') === 'SWAP';
      json = { items: [{ inst_id: `BTC-USDT${swap ? '-SWAP' : ''}`, state: 'live' }] };
    } else if (path === '/pro/market') {
      const symbol = url.searchParams.get('inst_id') ?? 'BTC-USDT';
      json = {
        ts: Date.now(),
        last: '123.45',
        bid: '123.44',
        ask: '123.45',
        instrument: {
          ...instrument,
          inst_id: symbol,
          ...(symbol.endsWith('-SWAP')
            ? {
                inst_type: 'SWAP',
                quantity_unit: 'contracts',
                ct_val: '0.01',
                ct_val_ccy: 'BTC',
                ct_mult: '1',
                lot_size: '1',
                min_size: '1',
              }
            : {}),
        },
      };
    }
    await route.fulfill({ json });
  });
  await page.goto(`/#execution?source=example&view=${view}`);
  return state;
}
async function submit(page: Page, request: Partial<OrderRequest> = {}) {
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const ticket = page.locator('#desk-order-ticket');
  if (request.order_type)
    await ticket.getByLabel('Order type', { exact: true }).selectOption(request.order_type);
  await ticket.getByLabel('Quantity', { exact: false }).fill(request.quantity ?? '0.01');
  if (request.order_type === 'limit')
    await ticket.getByLabel('Limit price', { exact: true }).fill('90');
  await ticket.getByRole('button', { name: 'Preview order', exact: true }).click();
  await ticket.getByRole('button', { name: 'Submit order', exact: true }).click();
  return ticket;
}
const receiptValue = (page: Page, label: string) =>
  page
    .locator('#desk-order-ticket .order-receipt-values > div')
    .filter({ has: page.locator('dt', { hasText: new RegExp(`^${label}$`) }) })
    .locator('dd');

test('market fill stays in the ticket and explicit inspection preserves the next draft and exact evidence', async ({
  page,
}) => {
  const state = await fixture(page);
  const ticket = await submit(page);
  const receipt = ticket.locator('.order-receipt');
  await expect(receipt).toContainText(submittedId);
  await expect(receipt).toContainText('BTC-USDT · buy · market');
  await expect(receipt.getByRole('status').filter({ hasText: 'Filled' })).toBeVisible();
  await expect(receiptValue(page, 'Requested')).toHaveText('0.01 BTC');
  await expect(receiptValue(page, 'Filled quantity')).toHaveText('0.01 BTC');
  await expect(receiptValue(page, 'Fees')).toHaveText('0.001234 USDT');
  await expect(receiptValue(page, 'Fill price')).toHaveText('123.45 USDT');
  await expect(page.getByRole('tab', { name: /^Positions/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await ticket.getByLabel('Quantity', { exact: false }).fill('0.02');
  await receipt.getByRole('button', { name: 'Inspect this order', exact: true }).click();
  const inspected = page.getByRole('region', { name: 'Selected order', exact: true });
  await expect(inspected).toContainText(submittedId);
  await expect(inspected).toContainText('Exact order event');
  await expect(inspected).not.toContainText('Other source event');
  await expect(inspected).not.toContainText('Other order event');
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.02');
  await inspected.getByText('Ledger entries (1)', { exact: true }).click();
  await expect(inspected).toContainText('Exact ledger entry');
  await expect(inspected).not.toContainText('Other source ledger');
  await page.getByRole('searchbox', { name: 'Search orders', exact: true }).fill(submittedId);
  await expect(page.locator(`tr[data-row-key="${submittedId}"]`)).toHaveCount(1);
  await expect(page.locator('tr[data-row-key="unrelated-same-market"]')).toHaveCount(0);
  expect(state.requests.filter((request) => request.path === '/pro/execution/orders')).toHaveLength(
    1,
  );
  await page.reload();
  await expect(page.getByRole('region', { name: 'Selected order', exact: true })).toContainText(
    submittedId,
  );
  await expect(page.getByRole('region', { name: 'Selected order', exact: true })).toContainText(
    'Exact order event',
  );
});

test('a pending inspected order remains pinned when polling fills it and Working excludes it', async ({
  page,
}) => {
  const state = await fixture(page);
  state.nextStatus = 'pending';
  const ticket = await submit(page, { order_type: 'limit' });
  await expect(ticket.locator('.order-receipt')).toContainText('Pending · working order');
  await expect(receiptValue(page, 'Fill price')).toHaveText('Not reported');
  await expect(receiptValue(page, 'Fees')).toHaveText('Not reported');
  await ticket.getByRole('button', { name: 'Inspect this order', exact: true }).click();
  await page.getByRole('button', { name: 'Working', exact: true }).click();
  const target = state.orders.find((order) => order.id === submittedId)!;
  Object.assign(target, { status: 'filled', filled_quantity: '0.01', updated_at: Date.now() + 1 });
  state.events.push({
    id: 5,
    source: 'example',
    ts: Date.now(),
    kind: 'pro.order_filled',
    summary: 'Polled fill event',
    details: { order_id: submittedId },
  });
  const inspected = page.getByRole('region', { name: 'Selected order', exact: true });
  await expect(inspected.getByRole('status').filter({ hasText: 'Filled' })).toBeVisible();
  await expect(inspected).toContainText('Polled fill event');
  await expect(page.locator(`tr[data-row-key="${submittedId}"]`)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Working', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(ticket.locator('.order-receipt')).toContainText('Filled');
});

test('server rejection retains the submitted request and reports no invented order ID or execution', async ({
  page,
}) => {
  const state = await fixture(page);
  state.nextError = 422;
  const ticket = await submit(page);
  await expect(ticket.locator('.order-receipt')).toContainText('Rejected');
  await expect(ticket.locator('.order-receipt')).toContainText(
    'Local paper risk rejected this request.',
  );
  await expect(receiptValue(page, 'Order ID')).toHaveText('No order ID returned');
  await expect(receiptValue(page, 'Filled quantity')).toHaveText('Not reported');
  await expect(receiptValue(page, 'Fill price')).toHaveText('Not reported');
  await expect(receiptValue(page, 'Fees')).toHaveText('Not reported');
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.01');
  await expect(ticket.getByRole('button', { name: 'Inspect this order', exact: true })).toHaveCount(
    0,
  );
});

test('late success preserves a new preview and a draft edited away and back to the same payload', async ({
  page,
}) => {
  const state = await fixture(page);
  state.delay = true;
  const ticket = await submit(page);
  await expect.poll(() => !!state.release).toBeTruthy();
  await ticket.getByLabel('Quantity', { exact: false }).fill('0.02');
  await ticket.getByLabel('Quantity', { exact: false }).fill('0.01');
  await ticket.getByRole('button', { name: 'Preview order', exact: true }).click();
  await expect(ticket.locator('.order-preview')).toBeVisible();
  state.release!();
  await expect(ticket.locator('.order-receipt')).toContainText(submittedId);
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.01');
  await expect(ticket.locator('.order-preview')).toBeVisible();
  await ticket.getByRole('button', { name: 'Inspect this order', exact: true }).click();
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.01');
});

test('late success from an earlier source cannot enter the new source ticket', async ({ page }) => {
  const state = await fixture(page);
  state.delay = true;
  await submit(page);
  await expect.poll(() => !!state.release).toBeTruthy();
  await page.getByLabel('Market source', { exact: true }).selectOption('okx');
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const ticket = page.locator('#desk-order-ticket');
  await ticket.getByLabel('Quantity', { exact: false }).fill('0.03');
  state.release!();
  await expect.poll(() => state.orders.some((order) => order.id === submittedId)).toBeTruthy();
  await expect(ticket.locator('.order-receipt')).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'Order submitted' })).toHaveCount(0);
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.03');
  expect(state.requests.every((request) => request.body.source === 'example')).toBeTruthy();
});

test('a new authenticated session cannot inherit a previous request receipt or clear its draft', async ({
  page,
}) => {
  const state = await fixture(page);
  state.delay = true;
  const ticket = await submit(page);
  await expect.poll(() => !!state.release).toBeTruthy();
  state.session = 'second-receipt-user';
  await page.evaluate(() => window.dispatchEvent(new Event('tidebench:auth-required')));
  await expect(ticket.locator('.order-receipt')).toHaveCount(0);
  await ticket.getByLabel('Quantity', { exact: false }).fill('0.03');
  state.release!();
  await expect.poll(() => state.orders.some((order) => order.id === submittedId)).toBeTruthy();
  await expect(ticket.locator('.order-receipt')).toHaveCount(0);
  await expect(ticket.getByLabel('Quantity', { exact: false })).toHaveValue('0.03');
});

test('cancellation keeps an exact server receipt after the order leaves Working', async ({
  page,
}) => {
  const state = await fixture(page, 'orders');
  const target = serverOrder('cancel-receipt-order');
  state.orders.push(target);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  const receipt = page.locator('.account-book-panel > .order-receipt');
  await expect(receipt).toContainText('Cancellation receipt');
  await expect(receipt).toContainText('cancel-receipt-order');
  await expect(receipt).toContainText('Canceled');
  await expect(
    receipt
      .locator('dt')
      .filter({ hasText: /^Canceled remainder$/ })
      .locator('..')
      .locator('dd'),
  ).toHaveText('0.01 BTC');
  await expect(
    receipt
      .locator('dt')
      .filter({ hasText: /^Filled quantity$/ })
      .locator('..')
      .locator('dd'),
  ).toHaveText('0 BTC');
  await expect(page.getByRole('button', { name: 'Working', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.locator('tr[data-row-key="cancel-receipt-order"]')).toHaveCount(0);
  await receipt.getByRole('button', { name: 'Inspect this order', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Selected order', exact: true })).toContainText(
    'Exact cancellation event',
  );
});
