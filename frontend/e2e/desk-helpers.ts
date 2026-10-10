import { type Page } from '@playwright/test';
export async function executionView(page: Page, name: string) {
  const disclosure = page.locator('.desk-view-disclosure');
  if (!(await disclosure.getAttribute('open'))) {
    // HTML boolean attributes may be represented by an empty string.
    if (!(await disclosure.evaluate((node) => (node as HTMLDetailsElement).open)))
      await disclosure.locator(':scope > summary').click();
  }
  await disclosure.getByRole('button', { name, exact: true }).click();
}
export async function expandClock(page: Page) {
  const disclosure = page
    .locator('.desk-account-details')
    .filter({ has: page.locator('summary').filter({ hasText: 'Synthetic market clock' }) });
  await disclosure.waitFor({ state: 'attached' });
  if (!(await disclosure.evaluate((node) => (node as HTMLDetailsElement).open)))
    await disclosure.locator(':scope > summary').click();
}
export async function showConditions(page: Page) {
  const disclosure = page.locator('.desk-conditions');
  // AuthGate may still be resolving immediately after reload.
  await disclosure.waitFor({ state: 'attached' });
  if (!(await disclosure.evaluate((node) => (node as HTMLDetailsElement).open)))
    await disclosure.locator(':scope > summary').click();
}

export async function expandResearchPolicy(page: Page) {
  const summary = page.locator('summary').filter({ hasText: /^Execution policy & risk limits/ });
  if (await summary.count()) {
    const parent = summary.locator('..');
    if (!(await parent.evaluate((node) => (node as HTMLDetailsElement).open)))
      await summary.click();
  }
}

export async function expandSnapshotBasis(page: Page) {
  const disclosure = page.locator('.desk-snapshot-basis');
  // AuthGate may still be resolving immediately after reload.
  await disclosure.waitFor({ state: 'attached' });
  if (!(await disclosure.evaluate((node) => (node as HTMLDetailsElement).open)))
    await disclosure.locator(':scope > summary').click();
}

export function visibleBookRecord(page: Page, key: string) {
  // Both responsive presentations retain the same stable economic record identity.
  return page.locator(`[data-row-key=${JSON.stringify(key)}]:visible`);
}
