import { expandResearchPolicy } from './desk-helpers';
import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';

const test = base.extend({
  request: async ({ context }, use) => {
    await use(context.request);
  },
});
const HOUR = 3_600_000,
  START = 1767225600000;
const symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT'];
type Fact = Record<string, unknown> & {
  source: Record<string, unknown>;
  instrument?: Record<string, unknown>;
};
async function navigate(page: Page, name: string) {
  const opener = page.getByRole('button', { name: /^(Open navigation|展开导航)$/ });
  if (await opener.isVisible()) await opener.click();
  await page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
}
async function login(request: APIRequestContext) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  const response = await request.post(
    status.setup_required ? '/api/v1/auth/setup' : '/api/v1/auth/login',
    {
      data: status.setup_required
        ? {
            username: 'browserqa',
            password: 'isolated-browser-password-123',
            display_name: 'Lifecycle acceptance',
          }
        : { username: 'browserqa', password: 'isolated-browser-password-123' },
    },
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()).csrf_token as string;
}
async function readyPackage(
  request: APIRequestContext,
  headers: Record<string, string>,
  inst_id: string,
  start: number,
  end: number,
) {
  const response = await request.post('/api/v1/pro/catalog/packages', {
    headers,
    data: { source: 'example', inst_id, bar: '1H', start, end },
  });
  expect(response.ok()).toBeTruthy();
  const id = (await response.json()).id;
  let result: any;
  await expect
    .poll(
      async () => {
        result = await (await request.get(`/api/v1/pro/catalog/packages/${id}`)).json();
        return result.ready;
      },
      { timeout: 30000 },
    )
    .toBeTruthy();
  const manifestResponse = await request.get(`/api/v1/pro/catalog/packages/${id}/manifest`);
  expect(manifestResponse.ok()).toBeTruthy();
  return { ...result, manifest: await manifestResponse.json() };
}
function sourceFact(
  fact: Fact,
  start: number,
  end: number,
  instrument: Record<string, unknown>,
): Fact {
  const offset = Number(fact.effective_ts) - START;
  const raw = JSON.stringify({
    fixture: 'Synthetic Playwright lifecycle acceptance; not real venue chronology.',
    inst_id: fact.inst_id,
    kind: fact.kind,
    effective_ts: start + offset,
  });
  return {
    ...fact,
    effective_ts: start + offset,
    known_at: start,
    ...(fact.instrument ? { instrument, valid_until: end } : {}),
    source: {
      ...fact.source,
      published_at: start,
      captured_at: end,
      raw_content: raw,
      content_hash: createHash('sha256').update(raw).digest('hex'),
    },
  };
}
const upload = (data: unknown, name: string) => ({
  name,
  mimeType: 'application/json',
  buffer: Buffer.from(JSON.stringify(data)),
});

test('lifecycle source imports run causally, revise restores inputs, and one-use freeze pins scenario evidence', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120000);
  const csrf = await login(request),
    headers = { 'X-CSRF-Token': csrf };
  const start = START + (testInfo.project.name === 'mobile' ? 96 : 0) * HOUR,
    end = start + 12 * HOUR;
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('source') === 'okx')
      await route.fulfill({
        status: 502,
        json: {
          error: {
            code: 'isolated_source',
            message: 'External venue disabled for synthetic lifecycle acceptance.',
          },
        },
      });
    else await route.continue();
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const packages = [],
    imported: Fact[][] = [];
  for (const symbol of symbols) {
    const p = await readyPackage(request, headers, symbol, start, end);
    packages.push(p);
    const template = JSON.parse(
      await readFile(`../examples/lifecycle/synthetic-${symbol.toLowerCase()}-events.json`, 'utf8'),
    ) as Fact[];
    imported.push(template.map((f) => sourceFact(f, start, end, p.manifest.instrument)));
  }
  await page.goto('/');
  await page.getByLabel('Market source', { exact: true }).selectOption('example');
  await navigate(page, 'Research');
  await page.getByRole('tab', { name: 'Portfolio research', exact: true }).click();
  await page.getByRole('button', { name: 'New portfolio study', exact: true }).click();
  await page
    .getByLabel('Study name', { exact: true })
    .fill(`Lifecycle browser ${testInfo.project.name}`);
  await page
    .getByLabel('Economic hypothesis', { exact: true })
    .fill(
      'Staggered causal source facts and suspensions retain actual custody without inventing market history.',
    );
  await page.getByLabel('Market history', { exact: true }).selectOption('historical_lifecycle');
  await page.getByLabel('Per-market listing / resume warmup', { exact: true }).fill('2');
  await page.getByRole('button', { name: 'Add market leg', exact: true }).click();
  for (let i = 0; i < symbols.length; i++) {
    await page.getByLabel(`Package ${i + 1}`, { exact: true }).selectOption(packages[i].id);
    await page
      .locator('.portfolio-study-leg')
      .nth(i)
      .getByLabel('Notional weight %', { exact: true })
      .fill('20');
    await page
      .getByLabel(`Import lifecycle JSON · ${symbols[i]}`, { exact: true })
      .setInputFiles(upload(imported[i], `synthetic-${symbols[i]}.json`));
    await expect(
      page.locator('.portfolio-study-leg').nth(i).locator('.lifecycle-import-heading'),
    ).toContainText(`${imported[i].length} captured facts`);
  }
  // Invalid source bytes cannot replace already imported evidence.
  const corrupt = JSON.parse(JSON.stringify(imported[1]));
  corrupt[0].source.raw_content = 'Tampered source bytes';
  await page
    .getByLabel('Import lifecycle JSON · ETH-USDT', { exact: true })
    .setInputFiles(upload(corrupt, 'invalid.json'));
  await expect(
    page.getByText(
      'Source bytes do not match their declared hash. The existing events were retained.',
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByLabel('Import lifecycle JSON · ETH-USDT', { exact: true })
    .setInputFiles(upload(imported[1], 'valid.json'));
  await page.getByLabel('Capital allocation %', { exact: true }).fill('60');
  await page.getByLabel('Rebalance every N bars', { exact: true }).fill('1');
  await page.getByLabel('Fee (bps)', { exact: true }).fill('0');
  await page.getByLabel('Slippage (bps)', { exact: true }).fill('0');
  await expandResearchPolicy(page);
  await page.getByLabel('Daily loss limit %', { exact: true }).fill('50');
  const queued = page.waitForResponse(
    (r) => r.url().endsWith('/pro/research/portfolios') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run portfolio research', exact: true }).click();
  const response = await queued;
  expect(response.ok()).toBeTruthy();
  const run = await response.json();
  expect(run.config.universe_mode).toBe('historical_lifecycle');
  expect(run.config.lifecycle_warmup_bars).toBe(2);
  expect(run.config.legs.map((l: any) => l.lifecycle_events.length)).toEqual([3, 1, 1]);
  let completed: any;
  await expect
    .poll(
      async () => {
        completed = await (await request.get(`/api/v1/pro/research/portfolios/${run.id}`)).json();
        return completed.status;
      },
      { timeout: 30000 },
    )
    .toBe('completed');
  expect(completed.result.lifecycle.status).toBe('complete_within_supplied_scope');
  const evidence = page.getByRole('region', { name: 'Causal market eligibility', exact: true });
  await expect(evidence).toBeVisible();
  await page
    .getByLabel('Eligibility boundary · UTC', { exact: true })
    .selectOption(String(start + 5 * HOUR));
  await expect(evidence.getByText('Suspended', { exact: true }).first()).toBeVisible();
  await expect(
    evidence.getByRole('heading', { name: 'Pinned source evidence', exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await mkdir('../docs/assets', { recursive: true });
  await evidence.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `../docs/assets/lifecycle-${testInfo.project.name}.png`,
    animations: 'disabled',
  });
  await page.getByLabel('Interface language', { exact: true }).selectOption('zh-CN');
  await expect(page.getByRole('region', { name: '因果市场资格', exact: true })).toBeVisible();
  await expect(page.getByText('已暂停', { exact: true }).first()).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await page.getByLabel('界面语言', { exact: true }).selectOption('en');
  await page.getByRole('button', { name: 'Revise & research', exact: true }).click();
  await expect(page.getByLabel('Market history', { exact: true })).toHaveValue(
    'historical_lifecycle',
  );
  await expect(page.getByLabel('Per-market listing / resume warmup', { exact: true })).toHaveValue(
    '2',
  );
  await expect(
    page.locator('.portfolio-study-leg').first().locator('.lifecycle-import-heading'),
  ).toContainText('3 captured facts');
  await page
    .locator('.portfolio-study-editor')
    .getByRole('button', { name: 'Close', exact: true })
    .click();

  const version = await (
    await request.get(`/api/v1/pro/portfolio-versions/${run.config.portfolio_version_id}`)
  ).json();
  const holdStart = start + 24 * HOUR,
    holdEnd = start + 72 * HOUR,
    holdPackages = [],
    held: Record<string, Fact[]> = {};
  for (const symbol of symbols) {
    const p = await readyPackage(request, headers, symbol, holdStart, holdEnd);
    holdPackages.push(p);
    const first = imported[symbols.indexOf(symbol)][0];
    held[symbol] = [
      sourceFact(
        { ...first, effective_ts: START, kind: 'listing' },
        holdStart,
        holdEnd,
        p.manifest.instrument,
      ),
    ];
  }
  await page.getByRole('tab', { name: 'Research governance', exact: true }).click();
  await page.getByRole('tab', { name: 'Portfolios', exact: true }).click();
  await page.getByLabel('Portfolio project', { exact: true }).selectOption(version.project_id);
  await page.getByRole('button', { name: 'Register portfolio holdout', exact: true }).click();
  await page
    .getByLabel('Holdout name', { exact: true })
    .fill(`Lifecycle final ${testInfo.project.name}`);
  await page
    .getByLabel('Portfolio version', { exact: true })
    .selectOption(run.config.portfolio_version_id);
  await page.getByLabel('Market history', { exact: true }).selectOption('historical_lifecycle');
  await page.getByLabel('Per-market listing / resume warmup', { exact: true }).fill('3');
  for (let i = 0; i < symbols.length; i++) {
    await page
      .getByLabel(`Package · ${symbols[i]}`, { exact: true })
      .selectOption(holdPackages[i].id);
    await page
      .getByLabel(`Import lifecycle JSON · ${symbols[i]}`, { exact: true })
      .setInputFiles(upload({ lifecycle_events: held }, 'all-markets.json'));
  }
  await page.getByLabel('Indicator warmup bars', { exact: true }).fill('4');
  await page
    .getByLabel('UTC final start', { exact: true })
    .fill(new Date(holdStart + 4 * HOUR).toISOString().slice(0, 16));
  await page
    .getByLabel('UTC final end', { exact: true })
    .fill(new Date(holdEnd).toISOString().slice(0, 16));
  await page.getByLabel('Fee (bps)', { exact: true }).fill('0');
  await page.getByLabel('Slippage (bps)', { exact: true }).fill('0');
  await expandResearchPolicy(page);
  await page.getByLabel('Daily loss limit %', { exact: true }).fill('50');
  await page.getByLabel('Minimum return versus cash %', { exact: true }).fill('-100');
  await page.getByLabel('Maximum accepted drawdown %', { exact: true }).fill('100');
  await page
    .getByLabel('Rejection plan', { exact: true })
    .fill(
      'Reject missing valuation or lifecycle execution evidence; keep original source bodies and no retuning after freezing.',
    );
  const previewed = page.waitForResponse(
    (r) => r.url().endsWith('/portfolio-holdouts/preview') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Capture evaluation preview', exact: true }).click();
  const previewResponse = await previewed;
  expect(previewResponse.ok()).toBeTruthy();
  const preview = await previewResponse.json();
  expect(preview.plan.test_config.universe_mode).toBe('historical_lifecycle');
  expect(preview.plan.test_config.lifecycle_warmup_bars).toBe(3);
  expect(
    preview.plan.test_config.legs.map((l: any) => l.lifecycle_events[0].source.content_hash),
  ).toEqual(symbols.map((s) => held[s][0].source.content_hash));
  await expect(
    page.getByRole('region', { name: 'Frozen lifecycle sources', exact: true }),
  ).toBeVisible();
  const sealed = page.waitForResponse(
    (r) => r.url().endsWith('/portfolio-holdouts') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Seal captured portfolio', exact: true }).click();
  expect((await sealed).ok()).toBeTruthy();
  const saved = await (
    await request.get(`/api/v1/pro/research/portfolio-holdouts/${preview.id}`)
  ).json();
  expect(saved.plan.test_config.universe_mode).toBe('historical_lifecycle');
  expect(saved.plan.test_config.legs.map((l: any) => l.lifecycle_events)).toEqual(
    preview.plan.test_config.legs.map((l: any) => l.lifecycle_events),
  );
  expect(errors).toEqual([]);
});
