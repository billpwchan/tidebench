import { expect, test } from '@playwright/test';
import { desk } from './desk-review-fixture';

for (const language of ['en', 'zh-CN']) {
  test(`shared context remains legible and reachable in ${language}`, async ({ page }, info) => {
    const mobile = info.project.name === 'mobile';
    const width = mobile ? 390 : 1280;
    await page.setViewportSize({ width, height: mobile ? 844 : 720 });
    const state = await desk(page);
    await page.getByRole('combobox', { name: 'Interface language' }).selectOption(language);
    const market = page.getByLabel('Market source', { exact: true });
    await expect(market).toHaveValue('example');
    const context = page.locator('.workspace-market-context');
    await expect(context).toContainText(language === 'en' ? 'Local paper' : '本地模拟');
    for (const control of [market, page.locator('.search-trigger')]) {
      const box = (await control.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      if (mobile) expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const colors = await page.locator('.sidebar .nav-item.active').evaluate((element) => {
      const style = getComputedStyle(element);
      return { foreground: style.color, background: style.backgroundColor };
    });
    const luminance = (color: string) => {
      const channels = color
        .match(/\d+/g)!
        .slice(0, 3)
        .map(Number)
        .map((n) => n / 255);
      const linear = channels.map((n) => (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4));
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    };
    const values = [luminance(colors.foreground), luminance(colors.background)].sort(
      (a, b) => a - b,
    );
    expect((values[1] + 0.05) / (values[0] + 0.05)).toBeGreaterThanOrEqual(4.5);
    await market.selectOption('okx');
    await expect(market).toHaveValue('okx');
    await expect(page.locator('.example-notice')).toHaveCount(0);
    await expect(context).toContainText(language === 'en' ? 'Local paper' : '本地模拟');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    );
    expect(state.mutations).toEqual([]);
  });
}
