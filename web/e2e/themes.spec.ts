import { expect, test, type Page } from '@playwright/test';
import { open, settled, state } from './helpers';

// Runtime theme switching: site theme at site level and below, global choice at world level, ?theme= override,
// per-viewer persistence in localStorage, and a page that still works when storage throws.
const theme = async (page: Page) => (await state(page)).theme;
const pick = async (page: Page, id: string) => {
  await page.locator('#theme-button').click();
  await page.locator(`#theme-menu [data-theme="${id}"]`).click();
  await settled(page);
};
const bg = (page: Page) => page.evaluate(() => window.__iso!.view.app.renderer.background.color.toHex());

test("defaults: debug at world level, the site's iso_theme at site level and below", async ({ page }) => {
  await open(page, 'anim=0&view=world');
  expect(await theme(page)).toBe('debug');
  await expect(page.locator('#theme-button')).toHaveAttribute('data-theme', 'debug');
  for (const v of ['site:1', 'room:1:1', 'rack:1', 'device:2']) {
    await open(page, `anim=0&view=${v}`);
    expect(await theme(page), v).toBe('industrial-robotic'); // North Depot
  }
  await open(page, 'anim=0&view=room:3:7');
  expect(await theme(page)).toBe('business'); // East Exchange
});

test('picking switches the level on screen at runtime and persists per viewer', async ({ page }) => {
  await open(page, 'anim=0&view=world');
  const before = await bg(page);
  await pick(page, 'farm');
  expect(await theme(page)).toBe('farm');
  expect(await bg(page)).not.toBe(before);
  expect(await state(page)).toMatchObject({ level: 'world', error: null });
  // The global choice does not override a site's own theme...
  await page.evaluate(() => window.__iso!.view.goto({ level: 'site', siteId: 1 }));
  expect(await theme(page)).toBe('industrial-robotic');
  // ...a pick at site level does, for that site only, and it survives a reload.
  await pick(page, 'pagodas-nature');
  expect(await theme(page)).toBe('pagodas-nature');
  await open(page, 'anim=0&view=rack:1');
  expect(await theme(page)).toBe('pagodas-nature');
  await open(page, 'anim=0&view=room:3:7');
  expect(await theme(page)).toBe('business');
  await open(page, 'anim=0&view=world');
  expect(await theme(page)).toBe('farm');
  // "Site default" clears the per-site pick.
  await open(page, 'anim=0&view=site:1');
  await pick(page, 'default');
  expect(await theme(page)).toBe('industrial-robotic');
});

test('?theme= overrides every level; a pick replaces it and drops it from the URL', async ({ page }) => {
  await open(page, 'anim=0&view=site:1&theme=farm');
  expect(await theme(page)).toBe('farm');
  await expect(page.locator('#theme-button')).toContainText('(URL)');
  await pick(page, 'business');
  expect(await theme(page)).toBe('business');
  expect(new URL(page.url()).searchParams.has('theme')).toBe(false);
  await open(page, 'anim=0&view=site:1&theme=not-a-theme');
  expect(await theme(page)).toBe('business');
});

test('the picker still works when localStorage throws (choice lasts for the page)', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new Error('SecurityError'); } });
  });
  await open(page, 'anim=0&view=world');
  await pick(page, 'farm');
  expect(await theme(page)).toBe('farm');
  expect(await state(page)).toMatchObject({ error: null });
});

test('Escape closes the theme menu without zooming out', async ({ page }) => {
  await open(page, 'anim=0&view=site:1');
  await page.locator('#theme-button').click();
  await expect(page.locator('#theme-menu')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#theme-menu')).toBeHidden();
  await settled(page);
  expect((await state(page)).level).toBe('site');
});
