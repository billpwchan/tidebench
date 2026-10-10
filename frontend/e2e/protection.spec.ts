import { visibleBookRecord } from './desk-helpers';
import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test';
const test = base.extend({ request: async ({ context }, use) => use(context.request) });
async function enter(page: Page, request: APIRequestContext) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  const session = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: {
        username: 'browserqa',
        password: 'isolated-browser-password-123',
        ...(status.setup_required ? { display_name: 'Isolated protection desk' } : {}),
      },
    },
  );
  expect(session.ok()).toBeTruthy();
  const headers = { 'X-CSRF-Token': (await session.json()).csrf_token };
  expect(
    (
      await request.post('/api/v1/pro/execution/halt', {
        headers,
        data: {
          source: 'example',
          active: false,
          reason: 'Isolated browser protection acceptance',
        },
      })
    ).ok(),
  ).toBeTruthy();
  for (const deployment of (
    await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
  ).items)
    if (deployment.status === 'running')
      await request.post('/api/v1/pro/execution/deployments/' + deployment.id + '/stop', {
        headers,
      });
  for (const order of (
    await (await request.get('/api/v1/pro/execution/orders?source=example')).json()
  ).items)
    if (order.status === 'pending')
      await request.post('/api/v1/pro/execution/orders/' + order.id + '/cancel', { headers });
  for (const position of (
    await (await request.get('/api/v1/pro/execution/account?source=example')).json()
  ).positions) {
    const response = await request.post('/api/v1/pro/execution/orders', {
      headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() },
      data: {
        source: 'example',
        inst_id: position.inst_id,
        side: String(position.quantity).startsWith('-') ? 'buy' : 'sell',
        quantity: String(position.quantity).replace(/^-/, ''),
        leverage: Number(position.leverage),
        reduce_only: true,
        margin_mode: 'isolated',
        order_type: 'market',
      },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await page.goto('/');
  await page.getByLabel('Interface language', { exact: true }).selectOption('en');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  const nav = page.getByRole('navigation').getByRole('button', { name: 'Execution', exact: true });
  await nav.waitFor({ state: 'attached' });
  const opener = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await opener.isVisible()) await opener.click();
  await nav.click();
  return headers;
}

test('protection stops the controller before reducing and prevents the next automatic entry', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90000);
  const headers = await enter(page, request);
  const deployment = await request.post('/api/v1/pro/execution/deployments', {
    headers,
    data: {
      source: 'example',
      inst_id: 'DOGE-USDT',
      bar: '1H',
      direction: 'long_only',
      leverage: 1,
      strategy: { kind: 'buy_hold', allocation: '0.02' },
    },
  });
  expect(deployment.ok(), await deployment.text()).toBeTruthy();
  const controller = await deployment.json();
  await expect
    .poll(
      async () =>
        (
          await (await request.get('/api/v1/pro/execution/account?source=example')).json()
        ).positions.some((p: { inst_id: string }) => p.inst_id === 'DOGE-USDT'),
      { timeout: 30000 },
    )
    .toBe(true);
  await visibleBookRecord(page, 'DOGE-USDT')
    .getByRole('button', { name: 'Reduce account position', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Reduce account position', exact: true });
  await expect(dialog).toContainText('Sell to reduce a long');
  await expect(
    dialog.getByRole('button', { name: 'Preview protective exit', exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole('button', { name: 'Stop controllers and cancel entries', exact: true })
    .click();
  await expect(dialog).toContainText('Controllers stopped · no working entries');
  expect(
    (
      await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
    ).items.find((d: { id: string }) => d.id === controller.id).status,
  ).toBe('stopped');
  await dialog.getByRole('button', { name: 'Preview protective exit', exact: true }).click();
  await expect(
    dialog.getByRole('button', { name: 'Submit reduce-only exit', exact: true }),
  ).toBeEnabled();
  await dialog.screenshot({ path: '/tmp/tidebench-v11-protection-' + info.project.name + '.png' });
  const filled = page.waitForResponse(
    (r) => r.url().endsWith('/pro/execution/orders') && r.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Submit reduce-only exit', exact: true }).click();
  const receipt = await (await filled).json();
  expect(receipt.reduce_only).toBe(true);
  expect(receipt.side).toBe('sell');
  expect(receipt.status).toBe('filled');
  await expect(dialog).toContainText('Protective exit filled');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  const clock = await (await request.get('/api/v1/pro/execution/clock')).json();
  await request.post('/api/v1/pro/execution/clock', {
    headers,
    data: { step_ms: 3600000, expected_revision: clock.revision },
  });
  await expect
    .poll(
      async () =>
        (
          await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
        ).items.find((d: { id: string }) => d.id === controller.id).status,
    )
    .toBe('stopped');
  expect(
    (
      await (await request.get('/api/v1/pro/execution/account?source=example')).json()
    ).positions.some((p: { inst_id: string }) => p.inst_id === 'DOGE-USDT'),
  ).toBe(false);
});

test('short protection locks buy-to-reduce and rejects a stale reviewed quantity', async ({
  page,
  request,
}, info) => {
  const headers = await enter(page, request);
  const body = {
    source: 'example',
    inst_id: 'BTC-USDT-SWAP',
    side: 'sell',
    quantity: '2',
    leverage: 2,
    reduce_only: false,
    margin_mode: 'isolated',
    order_type: 'market',
  };
  const open = await request.post('/api/v1/pro/execution/orders', {
    headers: { ...headers, 'Idempotency-Key': 'short-protection-open-' + info.project.name },
    data: body,
  });
  expect(open.ok(), await open.text()).toBeTruthy();
  await visibleBookRecord(page, 'BTC-USDT-SWAP')
    .getByRole('button', { name: 'Reduce account position', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Reduce account position', exact: true });
  await expect(dialog).toContainText('Buy to reduce a short');
  await expect(dialog).toContainText('Contracts');
  await expect(dialog.getByLabel('Exit quantity', { exact: true })).toHaveValue('2');
  await dialog.getByRole('button', { name: 'Preview protective exit', exact: true }).click();
  const partial = await request.post('/api/v1/pro/execution/orders', {
    headers: { ...headers, 'Idempotency-Key': 'short-protection-partial-' + info.project.name },
    data: { ...body, side: 'buy', quantity: '1', reduce_only: true },
  });
  expect(partial.ok(), await partial.text()).toBeTruthy();
  await expect(dialog).toContainText('Inventory changed. Refresh the position and preview again.');
  await expect(
    dialog.getByRole('button', { name: 'Preview protective exit', exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole('button', { name: 'Refresh to full current quantity', exact: true })
    .click();
  await expect(dialog.getByLabel('Exit quantity', { exact: true })).toHaveValue('1');
  await dialog.getByRole('button', { name: 'Preview protective exit', exact: true }).click();
  const filled = page.waitForResponse(
    (r) => r.url().endsWith('/pro/execution/orders') && r.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Submit reduce-only exit', exact: true }).click();
  const receipt = await (await filled).json();
  expect(receipt.side).toBe('buy');
  expect(receipt.reduce_only).toBe(true);
  expect(receipt.quantity).toBe('1');
  await expect(dialog).toContainText('Protective exit filled');
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByRole('dialog', { name: '减少账户持仓', exact: true })).toContainText(
    '保护性退出已成交',
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
