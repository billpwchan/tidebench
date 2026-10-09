import { expect, test as base } from '@playwright/test';

const test = base.extend({ request: async ({ context }, use) => use(context.request) });

test('real local-paper account freezes a whole observation window and verifies it after append', async ({
  page,
  request,
}, info) => {
  test.setTimeout(60000);
  const status = await (await request.get('/api/v1/auth/status')).json();
  const session = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: {
        username: 'browserqa',
        password: 'isolated-browser-password-123',
        ...(status.setup_required ? { display_name: 'Evidence acceptance' } : {}),
      },
    },
  );
  expect(session.ok()).toBeTruthy();
  const headers = { 'X-CSRF-Token': (await session.json()).csrf_token };
  await request.post('/api/v1/pro/execution/halt', {
    headers,
    data: { source: 'example', active: false, reason: 'Isolated financial evidence acceptance' },
  });
  const order = {
    source: 'example',
    inst_id: 'OKB-USDT',
    quantity: '1',
    side: 'buy',
    leverage: '1',
    margin_mode: 'isolated',
    order_type: 'market',
    reduce_only: false,
  };
  const opened = await request.post('/api/v1/pro/execution/orders', {
    headers: { ...headers, 'Idempotency-Key': `evidence-open-${info.project.name}` },
    data: order,
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  let frozen;
  try {
    await page.goto('/');
    await page.getByLabel('Market source', { exact: true }).selectOption('example');
    const nav = page.getByRole('button', { name: /^(Open navigation|展开导航)$/ });
    if (await nav.isVisible()) await nav.click();
    await page
      .getByRole('navigation')
      .getByRole('button', { name: 'Execution', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Forward performance', exact: true }).click();
    await expect(page.getByText('Window observations', { exact: true })).toBeVisible();
    const freezeResponse = page.waitForResponse(
      (r) => r.url().endsWith('/performance/snapshots') && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Freeze window evidence', exact: true }).click();
    frozen = await (await freezeResponse).json();
    expect(frozen.body.summary.scope).toBe('entire_frozen_observation_window');
    expect(frozen.body.window.count).toBeGreaterThan(0);
    expect(frozen.body.acceptance.passed).toBe(false);
  } finally {
    const closed = await request.post('/api/v1/pro/execution/orders', {
      headers: { ...headers, 'Idempotency-Key': `evidence-close-${info.project.name}` },
      data: { ...order, side: 'sell', reduce_only: true },
    });
    expect(closed.ok(), await closed.text()).toBeTruthy();
  }
  const verifiedResponse = page.waitForResponse((r) =>
    r.url().includes(`/performance/snapshots/${frozen.id}/verify`),
  );
  await page.getByRole('button', { name: 'Verify frozen window', exact: true }).click();
  expect((await (await verifiedResponse).json()).verified).toBe(true);
  await expect(
    page.getByText('Recorded observation window and hashes match the frozen evidence.'),
  ).toBeVisible();
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByText('窗口观察数', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '验证冻结窗口', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
