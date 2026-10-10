import {
  expect,
  test as base,
  type APIRequestContext,
  type Page,
  type Route,
} from '@playwright/test';

const test = base.extend({ request: async ({ context }, use) => use(context.request) });
const credentials = { username: 'browserqa', password: 'isolated-browser-password-123' };
// This suite owns its disposable synthetic clock; leave stepping workflows paused.
test.afterEach(async ({ request }) => {
  const auth = await (await request.get('/api/v1/auth/status')).json();
  const clock = await (await request.get('/api/v1/pro/execution/clock')).json();
  const paused = await request.post('/api/v1/pro/execution/clock', {
    headers: { 'X-CSRF-Token': auth.csrf_token },
    data: { speed: 0, expected_revision: clock.revision },
  });
  expect(paused.ok(), await paused.text()).toBeTruthy();
});

async function enter(page: Page, request: APIRequestContext) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  const login = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: {
        ...credentials,
        ...(status.setup_required ? { display_name: 'Isolated execution journey' } : {}),
      },
    },
  );
  expect(login.ok(), await login.text()).toBeTruthy();
  const headers = { 'X-CSRF-Token': (await login.json()).csrf_token };
  await request.post('/api/v1/pro/execution/halt', {
    headers,
    data: { source: 'example', active: false, reason: 'Isolated execution journey verification' },
  });
  for (const deployment of (
    await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
  ).items)
    if (deployment.status === 'running')
      await request.post(`/api/v1/pro/execution/deployments/${deployment.id}/stop`, { headers });
  for (const order of (
    await (await request.get('/api/v1/pro/execution/orders?source=example')).json()
  ).items)
    if (order.status === 'pending')
      await request.post(`/api/v1/pro/execution/orders/${order.id}/cancel`, { headers });
  for (const position of (
    await (await request.get('/api/v1/pro/execution/account?source=example')).json()
  ).positions) {
    const closed = await request.post('/api/v1/pro/execution/orders', {
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
    expect(closed.ok(), await closed.text()).toBeTruthy();
  }
  const clock = await (await request.get('/api/v1/pro/execution/clock')).json();
  await request.post('/api/v1/pro/execution/clock', {
    headers,
    data: { speed: 1, expected_revision: clock.revision },
  });
  await page.route('**/api/v1/**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('source') === 'okx')
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: 'isolated_public_outage',
            message: 'Public requests are disabled in this isolated journey.',
          },
        },
      });
    else await route.continue();
  });
  await page.goto('/#execution?source=example&view=positions');
  await page.getByLabel('Interface language', { exact: true }).selectOption('en');
  return headers;
}

async function approvedBasket(
  request: APIRequestContext,
  headers: Record<string, string>,
  name: string,
) {
  const end = 1767225600000;
  const symbols = ['BTC-USDT', 'ETH-USDT'];
  const packages = [];
  for (const inst_id of symbols) {
    const prepared = await request.post('/api/v1/pro/catalog/packages', {
      headers,
      data: { source: 'example', inst_id, bar: '1H', start: end - 120 * 3600000, end },
    });
    expect(prepared.ok(), await prepared.text()).toBeTruthy();
    const item = await prepared.json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/v1/pro/catalog/packages/${item.id}`)).json()).ready,
      )
      .toBeTruthy();
    packages.push(item.id);
  }
  const hypothesis =
    'A small shared cash basket must retain inventory when every controller is stopped.';
  const strategy = { kind: 'buy_hold', allocation: '1' };
  const definition = {
    bar: '1H',
    mode: 'fixed_weights',
    capital_pct: '20',
    max_residual_pct: '2',
    execution_contract: 'reduce_group_v2_allowance',
    legs: symbols.map((inst_id) => ({
      inst_id,
      weight: '.5',
      leverage: 1,
      direction: 'long_only',
      strategy,
    })),
  };
  const saved = await request.post('/api/v1/pro/portfolio-strategies', {
    headers,
    data: { name, hypothesis, definition },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const project = await saved.json();
  const queued = await request.post('/api/v1/pro/research/portfolios', {
    headers,
    data: {
      name,
      hypothesis,
      mode: definition.mode,
      capital_pct: definition.capital_pct,
      max_residual_pct: definition.max_residual_pct,
      execution_contract: definition.execution_contract,
      portfolio_version_id: project.version.id,
      legs: packages.map((package_id) => ({
        package_id,
        weight: '.5',
        leverage: 1,
        direction: 'long_only',
        strategy,
      })),
    },
  });
  expect(queued.ok(), await queued.text()).toBeTruthy();
  const run = await queued.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/v1/pro/research/portfolios/${run.id}`)).json()).status,
      { timeout: 30000 },
    )
    .toBe('completed');
  const previewResponse = await request.post('/api/v1/pro/execution/portfolio-releases/preview', {
    headers,
    data: { run_id: run.id },
  });
  expect(previewResponse.ok(), await previewResponse.text()).toBeTruthy();
  const preview = await previewResponse.json();
  expect(preview.blockers).toEqual([]);
  const approved = await request.post('/api/v1/pro/execution/portfolio-releases', {
    headers,
    data: {
      run_id: run.id,
      preview_hash: preview.preview_hash,
      review: 'Reviewed whole-group stop scope, shared cash and retained filled inventory.',
      acknowledgements: preview.required_acknowledgements,
    },
  });
  expect(approved.ok(), await approved.text()).toBeTruthy();
  return approved.json();
}

test('a real approved basket activates precisely, cancel preserves controllers, and member stop retains the whole inventory', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90000);
  const headers = await enter(page, request);
  const name = `Execution journey basket ${info.project.name}`;
  const approval = await approvedBasket(request, headers, name);
  await page.goto(
    `/#execution?source=example&view=${encodeURIComponent('portfolio-release:' + approval.id)}`,
  );
  const selected = page.locator('.portfolio-release-review').filter({
    has: page.getByRole('heading', { name: 'Selected portfolio release', exact: true }),
  });
  await expect(selected).toContainText(name);
  await expect(selected).toContainText(approval.id);
  const activating = page.waitForResponse((r) =>
    r.url().endsWith(`/portfolio-releases/${approval.id}/activate`),
  );
  await selected.getByRole('button', { name: 'Activate', exact: true }).click();
  const released = await (await activating).json();
  expect(released.status).toBe('deployed');
  await expect(page).toHaveURL(new RegExp(encodeURIComponent('managed:' + released.group_id)));
  await expect
    .poll(
      async () => {
        const account = await (
          await request.get('/api/v1/pro/execution/account?source=example')
        ).json();
        return account.positions.filter((p: { inst_id: string }) =>
          ['BTC-USDT', 'ETH-USDT'].includes(p.inst_id),
        ).length;
      },
      { timeout: 30000 },
    )
    .toBe(2);
  const clock = await (await request.get('/api/v1/pro/execution/clock')).json();
  await request.post('/api/v1/pro/execution/clock', {
    headers,
    data: { speed: 0, expected_revision: clock.revision },
  });
  const before = await (await request.get('/api/v1/pro/execution/account?source=example')).json();
  const inventory = (account: typeof before) =>
    account.positions
      .map((p: { inst_id: string; quantity: string }) => [p.inst_id, p.quantity])
      .sort();
  const group = await (
    await request.get(`/api/v1/pro/execution/portfolios/${released.group_id}`)
  ).json();
  const member = group.manifest.legs[0].deployment_id;
  await page.goto(`/#execution?source=example&view=${encodeURIComponent('strategies:' + member)}`);
  const row = page
    .getByRole('row')
    .filter({ has: page.locator(`[data-deployment-id="${member}"]`) });
  await expect(row).toContainText(name);
  await expect(row).toContainText('Selected deployment');
  const exitRequests: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().endsWith('/pro/execution/orders'))
      exitRequests.push(r.url());
  });
  await row.getByRole('button', { name: 'Stop whole portfolio', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Stop whole portfolio', exact: true });
  await expect(dialog).toContainText(name);
  await expect(dialog).toContainText(released.group_id);
  await expect(dialog).toContainText('BTC-USDT');
  await expect(dialog).toContainText('ETH-USDT');
  await expect(dialog).toContainText('does not close positions');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(
    (await (await request.get(`/api/v1/pro/execution/portfolios/${released.group_id}`)).json())
      .status,
  ).toBe('running');
  await row.getByRole('button', { name: 'Stop whole portfolio', exact: true }).click();
  await dialog.getByRole('button', { name: 'Confirm stop whole portfolio', exact: true }).click();
  await expect(
    page.getByText(
      'Whole portfolio stopped. Filled inventory remains in the account; stopping does not close positions.',
      { exact: true },
    ),
  ).toBeVisible();
  const stopped = await (
    await request.get(`/api/v1/pro/execution/portfolios/${released.group_id}`)
  ).json();
  expect(stopped.status).toBe('stopped');
  const controllers = (
    await (await request.get('/api/v1/pro/execution/deployments?source=example')).json()
  ).items;
  expect(
    controllers
      .filter((d: { group_id: string }) => d.group_id === released.group_id)
      .map((d: { status: string }) => d.status),
  ).toEqual(['stopped', 'stopped']);
  const after = await (await request.get('/api/v1/pro/execution/account?source=example')).json();
  expect(inventory(after)).toEqual(inventory(before));
  expect(exitRequests).toEqual([]);
  await row.getByRole('button', { name: 'Inspect group & recovery', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(encodeURIComponent('managed:' + released.group_id)));
  await page.reload();
  await expect(
    page.locator('.managed-group-evidence').getByRole('heading', { name, exact: true }),
  ).toBeVisible();
  const releaseHistory = await (
    await request.get('/api/v1/pro/execution/portfolio-releases?source=example')
  ).json();
  expect(releaseHistory.items.find((r: { id: string }) => r.id === approval.id).status).toBe(
    'deployed',
  );
  await page.goto(
    `/#execution?source=example&view=${encodeURIComponent('portfolio-release:' + approval.id)}`,
  );
  await expect(selected.getByText('Activation recorded', { exact: true })).toBeVisible();
  await expect(
    selected.getByText('Current group state', { exact: true }).locator('..'),
  ).toContainText('stopped');
});

test('explicit missing entities remain visible and a perpetual ticket restores its market without submitting', async ({
  page,
  request,
}) => {
  await enter(page, request);
  const mutations: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/pro\/execution\/(orders|deployments)(\/|$)/.test(r.url()))
      mutations.push(r.url());
  });
  await page.goto(
    '/#execution?source=example&view=strategies%3Amissing-deployment&symbol=ETH-USDT-SWAP',
  );
  await expect(
    page.getByText('Selected deployment is unavailable in this account.', { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('Product', { exact: true })).toHaveValue('SWAP');
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ETH-USDT-SWAP');
  await page.getByRole('tab', { name: 'Orders', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Orders', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('ETH-USDT-SWAP');
  await page.getByLabel('Product', { exact: true }).selectOption('SPOT');
  await page.getByLabel('Market', { exact: true }).selectOption('SOL-USDT');
  await page.reload();
  await expect(page.getByLabel('Product', { exact: true })).toHaveValue('SPOT');
  await expect(page.getByLabel('Market', { exact: true })).toHaveValue('SOL-USDT');
  await page.goto('/#execution?source=example&view=managed%3Amissing-group');
  await expect(
    page.getByText('Selected portfolio is unavailable in this account.', { exact: true }),
  ).toBeVisible();
  await expect(page.locator('.managed-group-evidence')).toHaveCount(0);
  await page.goto('/#execution?source=example&view=portfolio-release%3Amissing-release');
  await expect(
    page.getByText('Selected portfolio release is unavailable in this account.', { exact: true }),
  ).toBeVisible();
  expect(mutations).toEqual([]);
});

const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const settleReceipt = async (page: Page) => {
  // Wait for fetch microtasks and the resulting React commit, rather than asserting
  // the URL before the delayed response has reached the mutation observer.
  await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
};
async function moveTo(page: Page, hash: string) {
  await page.evaluate((next) => {
    window.location.hash = next;
  }, hash);
  await expect(page).toHaveURL(new RegExp(hash.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'));
  await settleReceipt(page);
}

// The real activation/stop workflow above covers the backend contract. This
// fixture controls response order without sleeping or issuing public/live orders.
async function receiptFixture(page: Page, request: APIRequestContext) {
  await enter(page, request);
  const strategy = { kind: 'buy_hold', allocation: '.1' };
  const definition = {
    schema_version: 1,
    product: 'SPOT',
    bar: '1H',
    strategy,
    direction: 'long_only',
    leverage: '1',
  };
  const preview = (revision = 1) => ({
    run_id: 'receipt-run',
    selection: 'single',
    selection_scope: 'single',
    preview_hash: `preview-${revision}`,
    definition,
    execution_config: { inst_id: 'BTC-USDT' },
    risk_policy: {},
    research_governance: {},
    cost_differences: [],
    required_acknowledgements: [],
    blockers: [],
    model_difference: 'Captured simulation policy.',
  });
  const paperRelease = (id: string) => ({
    id,
    run_id: 'receipt-run',
    source: 'example',
    status: 'approved',
    config: { source: 'example', inst_id: 'BTC-USDT' },
    preview: preview(),
    approval_hash: `approval-${id}`,
    review: 'Original submitted review.',
    approved_by: 'browserqa',
    approved_at: Date.now(),
    strategy_version_id: 'receipt-version',
    deployment_id: undefined as string | undefined,
  });
  const portfolioPreview = (revision = 1) => ({
    run_id: 'receipt-portfolio-run',
    source: 'example',
    name: 'Receipt basket',
    hypothesis: 'Delayed receipts must stay with their original review.',
    version_id: 'receipt-version',
    preview_hash: `portfolio-preview-${revision}`,
    result_hash: 'bound-result',
    metrics: {},
    definition: {
      schema_version: 1,
      mode: 'fixed_weights',
      bar: '1H',
      capital_pct: '10',
      max_residual_pct: '2',
      execution_contract: 'reduce_group_v2_allowance',
      legs: [{ inst_id: 'BTC-USDT', weight: '1', leverage: 1, direction: 'long_only', strategy }],
    },
    execution_config: {},
    risk_policy: {},
    research_evidence: {},
    cost_differences: [],
    required_acknowledgements: [],
    blockers: [],
  });
  const portfolioRelease = (id: string) => ({
    id,
    source: 'example',
    status: 'approved',
    created_at: Date.now(),
    group_id: undefined as string | undefined,
    approval: {
      preview: portfolioPreview(),
      review: 'Original submitted review.',
      actor: 'browserqa',
      approved_at: Date.now(),
    },
  });
  type Controller = {
    id: string;
    source: string;
    inst_id: string;
    status: string;
    bar: string;
    direction: string;
    leverage: number;
  };
  const state = {
    releases: [paperRelease('receipt-release'), paperRelease('other-release')],
    portfolioReleases: [portfolioRelease('receipt-portfolio-release')],
    deployments: [] as Controller[],
    previews: 0,
    portfolioPreviews: 0,
  };
  let delayed:
    | {
        path: string;
        gate: ReturnType<typeof deferred>;
        response: (body: Record<string, unknown>) => unknown;
        status: number;
        requested: boolean;
      }
    | undefined;
  const delay = (
    path: string,
    response: (body: Record<string, unknown>) => unknown,
    status = 200,
  ) => {
    const item = {
      path,
      response,
      status,
      gate: deferred(),
      requested: false,
    };
    delayed = item;
    return item;
  };
  const groups = () =>
    [
      'old-group',
      'other-group',
      ...state.portfolioReleases.flatMap((r) => (r.group_id ? [r.group_id] : [])),
    ].map((id) => ({
      id,
      source: 'example',
      status: 'running',
      release_id: 'receipt-portfolio-release',
      version_id: 'receipt-version',
      created_at: Date.now(),
      manifest: null,
      integrity_error: {
        code: 'fixture_evidence',
        message: 'Response ownership fixture has no trading manifest.',
      },
    }));
  const result = {
    metrics: {},
    equity: [],
    decisions: [],
    orders: [],
    ledger: [],
    execution_rejections: [],
    assumptions: {},
  };
  const run = {
    id: 'receipt-run',
    source: 'example',
    status: 'completed',
    created_at: Date.now(),
    manifest: {},
    config: {
      dataset_id: 'receipt-dataset',
      mode: 'single',
      strategy,
      direction: 'long_only',
      leverage: 1,
      initial_cash: '10000',
      fee_bps: '10',
      slippage_bps: '5',
      liquidation_fee_bps: '50',
      options: {},
    },
    result,
  };
  const portfolioRun = {
    ...run,
    id: 'receipt-portfolio-run',
    config: {
      ...portfolioPreview().definition,
      name: 'Receipt basket',
      hypothesis: portfolioPreview().hypothesis,
      portfolio_version_id: 'receipt-version',
    },
    result: {
      ...result,
      execution_status: 'running',
      execution_contract: 'reduce_group_v2_allowance',
    },
  };
  await page.route('**/api/v1/pro/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace('/api/v1', '');
    const method = route.request().method();
    if (method === 'POST' && delayed?.path === path) {
      const item = delayed;
      delayed = undefined;
      const json = item.response(route.request().postDataJSON() ?? {});
      item.requested = true;
      await item.gate.promise;
      return route.fulfill({ status: item.status, json });
    }
    const source = url.searchParams.get('source') ?? 'example';
    const items = (rows: unknown[]) => ({ items: source === 'example' ? rows : [] });
    if (path === '/pro/execution/releases' && method === 'GET')
      return route.fulfill({ json: items(state.releases) });
    if (path === '/pro/execution/portfolio-releases' && method === 'GET')
      return route.fulfill({ json: items(state.portfolioReleases) });
    if (path === '/pro/execution/deployments' && method === 'GET')
      return route.fulfill({ json: items(state.deployments) });
    if (path === '/pro/execution/portfolios') return route.fulfill({ json: items(groups()) });
    if (path === '/pro/research/runs')
      return route.fulfill({ json: { ...items([run]), next_cursor: null } });
    if (path === '/pro/research/runs/receipt-run') return route.fulfill({ json: run });
    if (path === '/pro/research/portfolios') return route.fulfill({ json: items([portfolioRun]) });
    if (path === '/pro/research/portfolios/receipt-portfolio-run')
      return route.fulfill({ json: portfolioRun });
    if (path === '/pro/execution/releases/preview')
      return route.fulfill({ json: preview(++state.previews) });
    if (path === '/pro/execution/portfolio-releases/preview')
      return route.fulfill({ json: portfolioPreview(++state.portfolioPreviews) });
    await route.fallback();
  });
  return { state, delay, preview, portfolioPreview };
}

for (const kind of ['release', 'portfolio', 'deploy'] as const) {
  test(`${kind} receipts cannot take over a departed, switched or reopened execution task`, async ({
    page,
    request,
  }) => {
    test.setTimeout(90000);
    const fixture = await receiptFixture(page, request);
    const { state, delay } = fixture;
    const initial =
      kind === 'release'
        ? '#execution?source=example&view=releases%3Areceipt-release'
        : kind === 'portfolio'
          ? '#execution?source=example&view=portfolio-release%3Areceipt-portfolio-release'
          : '#execution?source=example&view=positions&symbol=BTC-USDT';
    const path =
      kind === 'release'
        ? '/pro/execution/releases/receipt-release/activate'
        : kind === 'portfolio'
          ? '/pro/execution/portfolio-releases/receipt-portfolio-release/activate'
          : '/pro/execution/deployments';
    const startButton = () =>
      kind === 'deploy'
        ? page
            .getByRole('dialog', { name: 'Deploy strategy', exact: true })
            .getByRole('button', { name: 'Start strategy', exact: true })
        : page
            .getByRole('row')
            .filter({
              has: page.getByText(
                kind === 'release' ? 'receipt-release' : 'receipt-portfolio-release',
                { exact: true },
              ),
            })
            .getByRole('button', {
              name: kind === 'release' ? 'Activate release' : 'Activate',
              exact: true,
            });
    for (const departure of [
      'page',
      'source',
      'tab',
      'entity',
      'return',
      ...(kind === 'deploy' ? ['new-draft'] : []),
    ]) {
      state.releases[0].status = 'approved';
      state.releases[0].deployment_id = undefined;
      state.portfolioReleases[0].status = 'approved';
      state.portfolioReleases[0].group_id = undefined;
      state.deployments = [];
      // Reset server facts before resetting this iteration's client QueryClient.
      // Returning by hash alone can briefly expose the previous deployed cache row.
      await moveTo(page, initial);
      await page.reload();
      if (kind === 'deploy')
        await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
      await expect(startButton()).toBeEnabled();
      const held = delay(path, () => {
        const controller = {
          id: `receipt-controller-${departure}`,
          source: 'example',
          inst_id: 'BTC-USDT',
          status: 'running',
          bar: '1H',
          direction: 'long_only',
          leverage: 1,
        };
        state.deployments.push(controller);
        if (kind === 'release') {
          Object.assign(state.releases[0], { status: 'deployed', deployment_id: controller.id });
          return state.releases[0];
        }
        if (kind === 'portfolio') {
          Object.assign(state.portfolioReleases[0], {
            status: 'deployed',
            group_id: 'activated-group',
          });
          return state.portfolioReleases[0];
        }
        return controller;
      });
      const response = page.waitForResponse(
        (r) => new URL(r.url()).pathname === '/api/v1' + path && r.request().method() === 'POST',
      );
      await startButton().click();
      await expect
        .poll(() => held.requested, {
          timeout: 5000,
          message: `${kind}/${departure}: expected POST ${path} to start`,
        })
        .toBe(true);
      if (departure === 'page' || departure === 'return') {
        await moveTo(page, '#overview?source=example');
        if (departure === 'return') await moveTo(page, initial);
      } else if (departure === 'source')
        await page.getByLabel('Market source', { exact: true }).selectOption('okx');
      else if (departure === 'tab') {
        if (kind === 'deploy')
          await page
            .getByRole('dialog')
            .getByRole('button', { name: 'Close', exact: true })
            .click();
        await page.getByRole('tab', { name: 'Orders', exact: true }).click();
      } else if (departure === 'new-draft') {
        await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
        await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
      } else if (kind === 'release')
        await moveTo(page, '#execution?source=example&view=releases%3Aother-release');
      else if (kind === 'portfolio')
        await page
          .locator('.managed-group-list')
          .getByRole('button')
          .filter({ hasText: 'other-gr' })
          .click();
      else
        await page
          .getByRole('dialog')
          .getByLabel('Market', { exact: true })
          .selectOption('ETH-USDT');
      const destination = page.url();
      held.gate.resolve();
      await (await response).finished();
      await settleReceipt(page);
      expect(page.url(), `${kind}/${departure} late response must preserve the current task`).toBe(
        destination,
      );
      if (departure === 'new-draft')
        await expect(
          page.getByRole('dialog', { name: 'Deploy strategy', exact: true }),
        ).toBeVisible();
      if (kind === 'deploy') {
        const dialog = page.getByRole('dialog', { name: 'Deploy strategy', exact: true });
        if (await dialog.isVisible())
          await dialog.getByRole('button', { name: 'Close', exact: true }).click();
        await moveTo(
          page,
          `#execution?source=example&view=strategies%3Areceipt-controller-${departure}`,
        );
        await expect(
          page.locator(`[data-deployment-id="receipt-controller-${departure}"]`).locator('..'),
        ).toContainText('Selected deployment');
      } else {
        await moveTo(page, '#execution?source=example&view=ledger');
        await moveTo(page, initial);
        await expect(page.getByText('Activation recorded', { exact: true }).first()).toBeVisible();
      }
    }
    if (kind === 'release') state.releases[0].status = 'approved';
    if (kind === 'portfolio') state.portfolioReleases[0].status = 'approved';
    await moveTo(page, initial);
    await page.reload();
    if (kind === 'deploy')
      await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
    await expect(startButton()).toBeEnabled();
    const failed = delay(
      path,
      () => ({
        error: {
          code: 'late_execution_failure',
          message: 'Failure from the departed execution task.',
        },
      }),
      409,
    );
    const failedResponse = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/v1' + path && r.request().method() === 'POST',
    );
    await startButton().click();
    await expect
      .poll(() => failed.requested, {
        timeout: 5000,
        message: `${kind}/late error: expected POST ${path} to start`,
      })
      .toBe(true);
    if (kind === 'release')
      await moveTo(page, '#execution?source=example&view=releases%3Aother-release');
    else if (kind === 'portfolio')
      await page
        .locator('.managed-group-list')
        .getByRole('button')
        .filter({ hasText: 'other-gr' })
        .click();
    else {
      await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
      await page.getByRole('button', { name: 'Deploy strategy', exact: true }).click();
    }
    failed.gate.resolve();
    await (await failedResponse).finished();
    await settleReceipt(page);
    await expect(
      page.getByText('Failure from the departed execution task.', { exact: true }),
    ).toHaveCount(0);
    if (kind === 'deploy')
      await expect(page.getByRole('button', { name: 'Start strategy', exact: true })).toBeEnabled();
  });
}

for (const kind of ['single', 'portfolio'] as const) {
  test(`${kind} review refresh cannot adopt an old approval or its late error`, async ({
    page,
    request,
  }) => {
    const fixture = await receiptFixture(page, request);
    const { state, delay } = fixture;
    const entry =
      kind === 'single'
        ? '#research?source=example&view=advanced&run=receipt-run'
        : '#research?source=example&view=portfolio&run=receipt-portfolio-run';
    const approvePath =
      kind === 'single' ? '/pro/execution/releases' : '/pro/execution/portfolio-releases';
    const previewPath = approvePath + '/preview';
    const reviewButton = () =>
      page.getByRole('button', {
        name: kind === 'single' ? 'Review paper release' : 'Review portfolio release',
        exact: true,
      });
    const form = () =>
      kind === 'single'
        ? page.getByRole('dialog', { name: 'Review paper release', exact: true })
        : page.locator('.portfolio-release-review');
    for (const outcome of ['accepted', 'rejected']) {
      await moveTo(page, '#execution?source=example&view=positions');
      await moveTo(page, entry);
      await reviewButton().click();
      const field = form().getByLabel(kind === 'single' ? 'Release review' : 'Review note', {
        exact: true,
      });
      await field.fill('Original review sent before requesting a fresh preview.');
      const heldApproval = delay(
        approvePath,
        (body) => {
          if (outcome === 'rejected')
            return { error: { code: 'late_old_approval', message: 'Late old approval rejected.' } };
          if (kind === 'single') {
            const saved = {
              ...state.releases[0],
              id: 'old-review-record',
              review: String(body.review),
              approval_hash: 'old-approved-review',
              preview: fixture.preview(state.previews),
            };
            state.releases.push(saved);
            return saved;
          }
          const saved = {
            ...state.portfolioReleases[0],
            id: 'old-portfolio-review-record',
            approval: {
              ...state.portfolioReleases[0].approval,
              review: String(body.review),
              preview: fixture.portfolioPreview(state.portfolioPreviews),
            },
          };
          state.portfolioReleases.push(saved);
          return saved;
        },
        outcome === 'accepted' ? 200 : 409,
      );
      const oldResponse = page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === '/api/v1' + approvePath && r.request().method() === 'POST',
      );
      await form()
        .getByRole('button', {
          name: kind === 'single' ? 'Approve paper release' : 'Approve portfolio release',
          exact: true,
        })
        .click();
      await expect
        .poll(() => heldApproval.requested, {
          timeout: 5000,
          message: `Expected POST ${approvePath} to start`,
        })
        .toBe(true);
      await expect(field).toBeDisabled();
      // Hold the replacement preview too: an old approval must not expose an
      // activation button during the new review's loading window.
      const heldPreview = delay(previewPath, () =>
        kind === 'single'
          ? fixture.preview(++state.previews)
          : fixture.portfolioPreview(++state.portfolioPreviews),
      );
      await form()
        .getByRole('button', {
          name: kind === 'single' ? 'Refresh preview' : 'Review portfolio release',
          exact: true,
        })
        .click();
      await expect
        .poll(() => heldPreview.requested, {
          timeout: 5000,
          message: `Expected replacement POST ${previewPath} to start`,
        })
        .toBe(true);
      heldApproval.gate.resolve();
      await (await oldResponse).finished();
      await settleReceipt(page);
      await expect(
        page.getByRole('button', {
          name: /^(Activate paper release|Activate managed paper portfolio)$/,
        }),
      ).toHaveCount(0);
      await expect(page.getByText('Late old approval rejected.', { exact: true })).toHaveCount(0);
      heldPreview.gate.resolve();
      await expect(field).toBeEnabled();
      await field.fill('A replacement review that remains editable after the previous receipt.');
      await expect(
        page.getByRole('button', {
          name: /^(Activate paper release|Activate managed paper portfolio)$/,
        }),
      ).toHaveCount(0);
      if (kind === 'single')
        await form().getByRole('button', { name: 'Close', exact: true }).click();
      if (outcome === 'accepted') {
        await moveTo(
          page,
          kind === 'single'
            ? '#execution?source=example&view=releases%3Aold-review-record'
            : '#execution?source=example&view=portfolio-release%3Aold-portfolio-review-record',
        );
        if (kind === 'single')
          await expect(
            page
              .getByText('Original review sent before requesting a fresh preview.', { exact: true })
              .first(),
          ).toBeVisible();
        else
          await expect(page.locator('.portfolio-release-review').first()).toContainText(
            'old-portfolio-review-record',
          );
      }
    }
  });
}

test('closing and reopening the same research release cannot revive an old approval, activation or preview', async ({
  page,
  request,
}) => {
  const fixture = await receiptFixture(page, request);
  const { state, delay } = fixture;
  await moveTo(page, '#research?source=example&view=advanced&run=receipt-run');
  const open = () =>
    page.getByRole('button', { name: 'Review paper release', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review paper release', exact: true });
  await open();
  await dialog
    .getByLabel('Release review', { exact: true })
    .fill('An approval belongs only to the review that submitted it.');
  const oldApproval = delay('/pro/execution/releases', () => ({
    ...state.releases[0],
    id: 'closed-review',
  }));
  const approvalResponse = page.waitForResponse(
    (r) => r.url().endsWith('/pro/execution/releases') && r.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Approve paper release', exact: true }).click();
  await expect
    .poll(() => oldApproval.requested, {
      timeout: 5000,
      message: 'Expected approval POST for the original review to start',
    })
    .toBe(true);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await open();
  await expect(dialog.getByLabel('Release review', { exact: true })).toBeEnabled();
  oldApproval.gate.resolve();
  await (await approvalResponse).finished();
  await settleReceipt(page);
  await expect(
    dialog.getByRole('button', { name: 'Activate paper release', exact: true }),
  ).toHaveCount(0);

  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  const oldPreview = delay('/pro/execution/releases/preview', () => fixture.preview(0));
  const previewResponse = page.waitForResponse((r) => r.url().endsWith('/releases/preview'));
  await open();
  await expect
    .poll(() => oldPreview.requested, {
      timeout: 5000,
      message: 'Expected the original preview POST to start',
    })
    .toBe(true);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await open();
  await dialog
    .getByLabel('Release review', { exact: true })
    .fill('This newer completed review must survive a previous preview response.');
  const newApproval = delay('/pro/execution/releases', () => ({
    ...state.releases[0],
    id: 'current-review',
  }));
  newApproval.gate.resolve();
  await dialog.getByRole('button', { name: 'Approve paper release', exact: true }).click();
  await expect(
    dialog.getByRole('button', { name: 'Activate paper release', exact: true }),
  ).toBeVisible();
  oldPreview.gate.resolve();
  await (await previewResponse).finished();
  await settleReceipt(page);
  await expect(
    dialog.getByRole('button', { name: 'Activate paper release', exact: true }),
  ).toBeVisible();

  const activationPath = '/pro/execution/releases/current-review/activate';
  const oldActivation = delay(activationPath, () => ({
    ...state.releases[0],
    id: 'current-review',
    status: 'deployed',
    deployment_id: 'old-activated-controller',
  }));
  const activationResponse = page.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1' + activationPath,
  );
  await dialog.getByRole('button', { name: 'Activate paper release', exact: true }).click();
  await expect
    .poll(() => oldActivation.requested, {
      timeout: 5000,
      message: `Expected POST ${activationPath} to start`,
    })
    .toBe(true);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await open();
  oldActivation.gate.resolve();
  await (await activationResponse).finished();
  await settleReceipt(page);
  await expect(dialog.getByLabel('Release review', { exact: true })).toBeEnabled();
  await expect(dialog.getByRole('button', { name: 'Inspect deployment', exact: true })).toHaveCount(
    0,
  );
});
