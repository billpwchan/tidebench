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
  await expect(
    page.getByRole('region', { name: 'Observation acceptance', exact: true }),
  ).toContainText('Observation targets not met');
  await page.reload();
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  const reopenNav = page.getByRole('button', { name: /^(Open navigation|展开导航)$/ });
  await page
    .getByRole('navigation')
    .getByRole('button', { name: 'Execution', exact: true })
    .waitFor({ state: 'attached' });
  if (await reopenNav.isVisible()) await reopenNav.click();
  await page
    .getByRole('navigation')
    .getByRole('button', { name: 'Execution', exact: true })
    .click();
  await page.getByRole('tab', { name: 'Forward performance', exact: true }).click();
  const history = page.getByRole('region', { name: 'Frozen account windows', exact: true });
  await expect(history).toContainText('Hash valid · not recomputed');
  await history.getByRole('button', { name: 'Open frozen window', exact: true }).first().click();
  const reopenedVerify = page.waitForResponse((r) =>
    r.url().includes(`/performance/snapshots/${frozen.id}/verify`),
  );
  await page.getByRole('button', { name: 'Verify frozen window', exact: true }).click();
  expect((await (await reopenedVerify).json()).verified).toBe(true);
  await page.getByLabel('Observed from (UTC)', { exact: true }).fill('2020-01-01T00:00');
  await page
    .getByLabel('Observed until (UTC, exclusive)', { exact: true })
    .fill('2020-01-02T00:00');
  await page.getByRole('button', { name: 'Review fixed account window', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Freeze window evidence', exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole('heading', { name: 'No forward observations', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'All account observations', exact: true }).click();
  await history.getByRole('button', { name: 'Open frozen window', exact: true }).first().click();
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(
    page
      .getByRole('region', { name: '已选冻结账户窗口', exact: true })
      .getByText('窗口观察数', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '验证冻结窗口', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
