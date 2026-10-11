import { expect, test } from '@playwright/test';
import { desk } from './desk-review-fixture';

for (const width of [1180, 1280]) {
  for (const product of ['SPOT', 'SWAP']) {
    test(`${product} book and ticket remain side by side at ${width}px with exact financial values`, async ({
      page,
    }, info) => {
      if (info.project.name === 'mobile') test.skip();
      await page.setViewportSize({ width, height: 720 });
      const state = await desk(page, 'execution', product === 'SPOT');
      const symbol = product === 'SWAP' ? 'BTC-USDT-SWAP' : 'BTC-USDT';
      if (product === 'SWAP') {
        await page.route('**/api/v1/pro/execution/account?**', (route) =>
          route.fulfill({
            json: {
              source: 'example',
              equity: '9996',
              cash: '10000',
              available_cash: '9962',
              used_margin: '34',
              maintenance_margin: '0.5',
              unrealized_pnl: '-4',
              as_of: Date.now(),
              valuation_status: 'example',
              economic_status: 'complete',
              execution_mode: 'local-paper',
              positions: [
                {
                  inst_id: symbol,
                  inst_type: product,
                  side: 'long',
                  quantity: '100',
                  entry_price: '104',
                  mark: '100',
                  margin: '34',
                  maintenance_margin: '0.5',
                  liquidation_price: '70.5',
                  liquidation_price_kind: 'isolated_estimate',
                  leverage: 3,
                  unrealized_pnl: '-4',
                  as_of: Date.now(),
                },
              ],
            },
          }),
        );
        await page.reload();
      }
      await page.getByRole('button', { name: 'New order', exact: true }).click();
      const ticket = page.locator('.desk-order-ticket');
      const book = page.locator('.account-book-panel');
      const scroll = book.locator('.desk-table-scroll');
      const row = scroll.locator(`tr[data-row-key="${symbol}"]`);
      await expect(ticket).toBeVisible();
      await expect(row).toContainText('-4.00');
      await page.evaluate(() => window.scrollTo(0, 0));

      const ticketBox = (await ticket.boundingBox())!;
      const bookBox = (await book.boundingBox())!;
      expect(ticketBox.width).toBeCloseTo(310, 0);
      expect(ticketBox.x - (bookBox.x + bookBox.width)).toBeCloseTo(16, 0);
      expect(Math.abs(ticketBox.y - bookBox.y)).toBeLessThanOrEqual(2);
      expect(ticketBox.x + ticketBox.width).toBeLessThanOrEqual(width);

      const tableBox = (await scroll.boundingBox())!;
      const actionBox = (await row.locator('.desk-sticky-action').boundingBox())!;
      for (const cell of await row.locator('.desk-numeric').all()) {
        const cellBox = (await cell.boundingBox())!;
        expect(cellBox.x).toBeGreaterThanOrEqual(tableBox.x);
        expect(cellBox.x + cellBox.width).toBeLessThanOrEqual(actionBox.x + 1);
      }
      if (product === 'SWAP') {
        await expect(row.locator('.desk-position-liquidation')).toContainText('70.5');
        await expect(scroll.getByRole('columnheader', { name: 'Mark', exact: true })).toHaveCount(
          0,
        );
      }
      await row.getByRole('button', { name: `More details ${symbol}`, exact: true }).click();
      const details = scroll.locator(`[data-details-for="${symbol}"]`);
      await expect(details).toContainText('Entry price');
      await expect(details).toContainText('Maintenance margin');
      if (product === 'SWAP') {
        const mark = details
          .locator('dl > div')
          .filter({ has: page.getByText('Mark', { exact: true }) });
        await expect(mark).toContainText('100');
        await page.setViewportSize({ width: 1179, height: 720 });
        await expect(scroll.getByRole('columnheader', { name: 'Mark', exact: true })).toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        width,
      );
      expect(state.mutations).toEqual([]);
    });
  }
}

test('a 1179px desk stacks the ticket below the account book', async ({ page }, info) => {
  if (info.project.name === 'mobile') test.skip();
  await page.setViewportSize({ width: 1179, height: 720 });
  const state = await desk(page, 'execution');
  await page.getByRole('button', { name: 'New order', exact: true }).click();
  const book = page.locator('.account-book-panel');
  const ticket = page.locator('.desk-order-ticket');
  await expect(ticket).toBeVisible();
  const bookBox = (await book.boundingBox())!;
  const ticketBox = (await ticket.boundingBox())!;
  expect(ticketBox.y).toBeGreaterThanOrEqual(bookBox.y + bookBox.height);
  expect(ticketBox.x).toBeCloseTo(bookBox.x, 0);
  await expect(book.getByRole('columnheader', { name: 'Mark', exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]);
});
