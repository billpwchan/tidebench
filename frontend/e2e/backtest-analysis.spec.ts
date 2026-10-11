import { expect, test as base, type APIRequestContext } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';

const test = base.extend({ request: async ({ context }, use) => use(context.request) });
const credentials = {
  username: 'browserqa',
  password: 'isolated-browser-password-123',
  display_name: 'Example workspace',
};

async function prepare(
  request: APIRequestContext,
  mode = 'single',
  options = {},
  days = 120,
  bar = '4H',
) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  const login = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: status.setup_required
        ? credentials
        : { username: credentials.username, password: credentials.password },
    },
  );
  expect(login.ok()).toBeTruthy();
  const headers = { 'X-CSRF-Token': (await login.json()).csrf_token };
  const end = 1767225600000;
  const queued = await request.post('/api/v1/pro/catalog/jobs', {
    headers,
    data: {
      source: 'example',
      inst_id: 'BTC-USDT',
      kind: 'trade',
      bar,
      start: end - days * 86400000,
      end,
    },
  });
  expect(queued.status()).toBe(202);
  const jobId = (await queued.json()).id;
  let datasetId: string | undefined;
  await expect
    .poll(
      async () => {
        const job = (await (await request.get('/api/v1/pro/catalog/jobs')).json()).items.find(
          (item: any) => item.id === jobId,
        );
        datasetId = job.dataset_id;
        return job.status;
      },
      { timeout: 30000 },
    )
    .toBe('completed');
  const created = await request.post('/api/v1/pro/research/runs', {
    headers,
    data: {
      dataset_id: datasetId,
      strategy: { kind: 'sma_cross', fast: 8, slow: 26 },
      mode,
      options,
    },
  });
  expect(created.status()).toBe(202);
  const id = (await created.json()).id;
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/pro/research/runs/${id}`)).json()).status,
      { timeout: 30000 },
    )
    .toBe('completed');
  return id as string;
}

test('selected report shows decision context and a meaningful performance plot on its initial fold', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90000);
  const mobile = info.project.name === 'mobile';
  const viewport = mobile ? { width: 390, height: 844 } : { width: 1280, height: 720 };
  await page.setViewportSize(viewport);
  const id = await prepare(request);
  await page.goto(`/#research?source=example&view=advanced&run=${id}`);
  const chart = page.getByRole('img', { name: 'Research performance chart', exact: true });
  await expect(chart).toBeVisible();
  await expect(page.getByText('Development evidence.', { exact: false }).first()).toBeVisible();
  await expect(
    page.getByText('Synthetic example data. Separate paper account.', { exact: true }),
  ).toBeVisible();
  const bounds = (await chart.boundingBox())!;
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  expect(viewport.height - bounds.y).toBeGreaterThanOrEqual(mobile ? 120 : 200);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    viewport.width,
  );
  const tabs = page.locator('.backtest-report [role="tablist"]');
  await tabs.getByRole('tab', { name: 'Performance', exact: true }).focus();
  await page.keyboard.press('End');
  await expect(tabs.getByRole('tab', { name: 'Economics', exact: true })).toBeFocused();
  const selected = (await tabs.getByRole('tab', { name: 'Economics', exact: true }).boundingBox())!;
  const strip = (await tabs.boundingBox())!;
  expect(selected.x).toBeGreaterThanOrEqual(strip.x - 1);
  expect(selected.x + selected.width).toBeLessThanOrEqual(strip.x + strip.width + 1);
  await page.keyboard.press('Home');
  await expect(tabs.getByRole('tab', { name: 'Performance', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  if (mobile) {
    for (const name of [
      'Performance',
      'Return calendar',
      'Risk & recovery',
      'Trade analysis',
      'Robustness',
      'Economics',
    ]) {
      await tabs.getByRole('tab', { name, exact: true }).click();
      const basis = page.locator('.backtest-mobile-basis');
      const summary = basis.locator('summary');
      await expect(summary).toBeVisible();
      if (!(await basis.evaluate((node) => node.hasAttribute('open')))) await summary.click();
      await expect(basis.getByText('Final equity', { exact: true })).toBeVisible();
      await expect(basis.getByText('Return difference', { exact: true })).toBeVisible();
      await expect(basis.getByText('Observed at bar close', { exact: true })).toBeVisible();
      await expect(
        basis.getByText('Daily · √365 · zero risk-free rate', { exact: true }),
      ).toBeVisible();
      await expect(basis.getByText('120 complete UTC days', { exact: true })).toBeVisible();
    }
    await tabs.getByRole('tab', { name: 'Performance', exact: true }).click();
  }
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1') {
    await tabs
      .getByRole('tab', { name: 'Performance', exact: true })
      .evaluate((node) => (node as HTMLElement).blur());
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `../docs/assets/trader-ui-v016-research-${info.project.name}.png`,
      fullPage: false,
    });
  }
});

test('captured evidence drives all six analysis views, normalized chart and trade exports', async ({
  page,
  request,
}, info) => {
  test.setTimeout(90000);
  const id = await prepare(request);
  const run = await (await request.get(`/api/v1/pro/research/runs/${id}/export`)).json();
  const diagnostic = await (
    await request.get(`/api/v1/pro/research/runs/${id}/diagnostics?variant=single`)
  ).json();
  await page.goto(`/#research?source=example&view=advanced&run=${id}`);
  const report = page.getByRole('region', { name: 'Backtest analysis', exact: true });
  await expect(report).toBeVisible();
  await expect(report.getByText('Completed trades', { exact: true })).toBeVisible();
  await expect(report.getByRole('button', { name: '1M', exact: true })).toBeVisible();
  await report.getByRole('button', { name: '1M', exact: true }).click();
  await report.getByRole('button', { name: 'Equity · USDT', exact: true }).click();
  await expect(report.getByRole('button', { name: '1M', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await report.getByRole('button', { name: 'Return · %', exact: true }).click();
  const inspector = report.getByRole('slider');
  await inspector.focus();
  await inspector.press('Home');
  await expect(inspector).toHaveValue('0');
  await expect(report.locator('.research-chart-point')).toContainText('0.00%');
  const chart = report.getByRole('img', { name: 'Research performance chart', exact: true });
  await chart.hover({ position: { x: 100, y: 100 } });
  await expect(inspector).toBeFocused();
  await chart.dispatchEvent('pointerleave', { pointerType: 'mouse' });
  await chart.locator('canvas').evaluateAll((canvases) => {
    for (const canvas of canvases) canvas.dispatchEvent(new MouseEvent('mouseleave'));
  });
  await expect(inspector).toHaveValue('0');
  await expect(report.locator('.research-chart-point')).toContainText('Initial capital');
  await expect(report.locator('.research-chart-point-strategy strong')).toHaveText('0.00%');
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ ...viewport, width: viewport.width - 24 });
  await chart.hover({ position: { x: 100, y: 100 } });
  await expect(inspector).toBeFocused();
  await expect(inspector).toHaveValue('0');
  await expect(report.locator('.research-chart-point-strategy strong')).toHaveText('0.00%');
  await page.setViewportSize(viewport);
  await expect(inspector).toHaveValue('0');
  await inspector.evaluate((node) => node.blur());
  await page.mouse.move(0, 0);
  await chart.hover({ position: { x: 100, y: 100 } });
  const lastIndex = Number(await inspector.getAttribute('max'));
  await expect.poll(async () => Number(await inspector.inputValue())).toBeGreaterThan(0);
  await expect.poll(async () => Number(await inspector.inputValue())).toBeLessThan(lastIndex);
  await inspector.focus();
  await inspector.press('End');
  const endingReturn = Number(run.result.result.metrics.total_return_pct);
  const endingLabel = `${endingReturn > 0 ? '+' : ''}${new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(endingReturn)}%`;
  await expect(report.locator('.research-chart-point')).toContainText(endingLabel);
  await report.getByRole('tab', { name: 'Return calendar', exact: true }).click();
  await expect(report.getByRole('table').first()).toContainText('2025');
  await expect(report.getByText('Median daily return', { exact: true })).toBeVisible();
  await report.getByRole('tab', { name: 'Risk & recovery', exact: true }).click();
  const terminal = run.result.result.equity.at(-1).drawdown_pct;
  const formatted =
    new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
      -Math.abs(terminal),
    ) + '%';
  await expect(
    report
      .locator('.backtest-stat')
      .filter({ has: page.getByText('Current drawdown', { exact: true }) }),
  ).toContainText(formatted);
  await expect(report.getByText('Rolling 30-day risk', { exact: true })).toBeVisible();
  await report.getByRole('tab', { name: 'Trade analysis', exact: true }).click();
  await expect(report.getByText('Top 5 winners / gross profit', { exact: true })).toBeVisible();
  await report.getByLabel('Completed trade filter', { exact: true }).selectOption('losses');
  const losses = run.result.result.round_trips.filter((item: any) => Number(item.net_pnl) < 0);
  if (losses.length) {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      report.getByRole('button', { name: 'Export CSV', exact: true }).click(),
    ]);
    expect(download.suggestedFilename()).toContain('completed-trades.csv');
  }
  await report.getByRole('tab', { name: 'Economics', exact: true }).click();
  const expectedPnl = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(diagnostic.diagnostics.contribution.total_equity_pnl));
  await expect(report.locator('.backtest-economics')).toContainText(expectedPnl);
  await report.getByRole('tab', { name: 'Robustness', exact: true }).click();
  await expect(report.getByText(/This run has no test folds/)).toBeVisible();
  await report.getByRole('tab', { name: 'Performance', exact: true }).click();
  if (process.env.TIDEBENCH_CAPTURE_ASSETS === '1') {
    await mkdir('../docs/assets', { recursive: true });
    await page.screenshot({
      path: `../docs/assets/backtest-analysis-${info.project.name}.png`,
      fullPage: false,
    });
  }
  const bounds = await report.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(info.project.use.viewport!.width);
});

test('performance controls wait for verified diagnostics and preserve their initial-capital basis', async ({
  page,
  request,
}) => {
  const id = await prepare(request);
  let releaseDiagnostics!: () => void;
  const diagnosticsGate = new Promise<void>((resolve) => {
    releaseDiagnostics = resolve;
  });
  let replyFetched = false;
  let startTs: number | undefined;
  await page.route(
    `**/api/v1/pro/research/runs/${id}/diagnostics?variant=single`,
    async (route) => {
      const response = await route.fetch();
      const diagnostic = await response.json();
      startTs = Number(diagnostic.diagnostics.sample.start_ts);
      replyFetched = true;
      await diagnosticsGate;
      await route.fulfill({ response });
    },
  );
  try {
    await page.goto(`/#research?source=example&view=advanced&run=${id}`);
    const report = page.getByRole('region', { name: 'Backtest analysis', exact: true });
    await expect(report.getByText('Net return', { exact: true })).toBeVisible();
    await expect(report.getByText('Completed trades', { exact: true })).toBeVisible();
    await expect.poll(() => replyFetched).toBe(true);
    const loading = report
      .getByRole('status')
      .filter({ hasText: 'Calculating diagnostics from the captured result…' });
    await expect(loading).toBeVisible();
    await expect(report.getByRole('button', { name: '1M', exact: true })).toHaveCount(0);
    await expect(report.getByRole('slider')).toHaveCount(0);
    await expect(
      report.getByRole('img', { name: 'Research performance chart', exact: true }),
    ).toHaveCount(0);
    releaseDiagnostics();
    await expect(loading).toHaveCount(0);
    const month = report.getByRole('button', { name: '1M', exact: true });
    await expect(month).toBeVisible();
    await month.click();
    await expect(month).toHaveAttribute('aria-pressed', 'true');
    for (const name of ['Equity · USDT', 'Return · %']) {
      await report.getByRole('button', { name, exact: true }).click();
      await expect(month).toHaveAttribute('aria-pressed', 'true');
    }
    const inspector = report.getByRole('slider');
    await inspector.focus();
    await inspector.press('Home');
    await expect(inspector).toHaveValue('0');
    const point = report.locator('.research-chart-point');
    await expect(point.getByText('Initial capital', { exact: true })).toBeVisible();
    await expect(point.locator('.research-chart-point-strategy strong')).toHaveText('0.00%');
    expect(Number.isFinite(startTs)).toBe(true);
    await expect(point.locator('time')).toHaveAttribute(
      'datetime',
      new Date(startTs!).toISOString(),
    );
  } finally {
    releaseDiagnostics();
  }
});

test('multi-dimensional candidates remain individually inspectable', async ({ page, request }) => {
  test.setTimeout(90000);
  const id = await prepare(request, 'grid', {
    grid: { fast: [8], slow: [26], allocation: ['.25', '.75'] },
  });
  await page.goto(`/#research?source=example&view=advanced&run=${id}`);
  const report = page.getByRole('region', { name: 'Backtest analysis', exact: true });
  await report.getByRole('tab', { name: 'Robustness', exact: true }).click();
  await expect(report.getByText('All sensitivity candidates', { exact: true })).toBeVisible();
  await expect(report.getByRole('button', { name: 'experiment-1', exact: true })).toBeVisible();
  await report.getByRole('button', { name: 'experiment-2', exact: true }).click();
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('experiment-2');
  await expect(report.getByRole('tab', { name: 'Performance', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('walk-forward presents separate OOS windows and exact fold handoff', async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const id = await prepare(request, 'walk_forward', {
    train_bars: 30 * 6,
    test_bars: 30 * 6,
    step_bars: 30 * 6,
    purge_bars: 6,
    grid: { fast: [8, 12], slow: [26] },
  });
  await page.goto(`/#research?source=example&view=advanced&run=${id}`);
  const report = page.getByRole('region', { name: 'Backtest analysis', exact: true });
  await expect(report).toContainText('Out-of-sample window');
  await report.getByRole('tab', { name: 'Robustness', exact: true }).click();
  await expect(report.getByText('Test folds', { exact: true })).toBeVisible();
  await expect(report).toContainText('Each fold starts with its own flat account');
  await report.getByRole('button', { name: 'fold-2', exact: true }).click();
  await expect(page.getByLabel('Inspect experiment', { exact: true })).toHaveValue('fold-2:test');
  await expect(page.getByRole('button', { name: 'Next trial draft', exact: true })).toBeVisible();
});

test('long histories disclose displayed calendar exports and sampled rolling risk', async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const id = await prepare(request, 'single', {}, 1100, '1Dutc');
  const diagnostic = await (
    await request.get(`/api/v1/pro/research/runs/${id}/diagnostics?variant=single`)
  ).json();
  expect(diagnostic.diagnostics.calendar.daily_count).toBe(1100);
  expect(diagnostic.diagnostics.calendar.daily_omitted_count).toBe(700);
  await page.goto(`/#research?source=example&view=advanced&run=${id}`);
  const report = page.getByRole('region', { name: 'Backtest analysis', exact: true });
  await report.getByRole('tab', { name: 'Return calendar', exact: true }).click();
  await expect(report).toContainText(
    'Displaying and exporting the latest 400 / 1,100 observed days.',
  );
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    report.getByRole('button', { name: 'Export displayed days', exact: true }).click(),
  ]);
  const path = await download.path();
  expect(path).toBeTruthy();
  expect((await readFile(path!, 'utf8')).trim().split(/\r?\n/)).toHaveLength(401);
  await report.getByRole('tab', { name: 'Risk & recovery', exact: true }).click();
  await expect(report).toContainText(
    'eligible windows displayed, evenly sampled across the full history',
  );
});
