// Finding things. Names on the canvas at every level, the name of whatever the pointer is
// over, a search box, and the level in the URL (reload stays, Back/Forward step through the levels).
import { expect, test, type Page } from '@playwright/test';
import { open, pointOf, settled, state } from './helpers';

const viewParam = (page: Page) => new URL(page.url()).searchParams.get('view');
const labelCount = (page: Page) =>
  page.evaluate(() => window.__iso!.view.app.stage.children[0]!.children.at(-1)!.children.find((c) => c.label === 'labels')!.children.length);

test('every level draws names on the canvas', async ({ page }) => {
  const want: [string, number][] = [['world', 3], ['site:1', 3], ['room:1:1', 4], ['rack:1', 42 + 4], ['device:2', 5]];
  for (const [view, n] of want) {
    await open(page, `anim=0&view=${view}`);
    expect(await labelCount(page), view).toBeGreaterThanOrEqual(n);
  }
});

test('the URL follows the level: reload stays, Back and Forward step through the levels', async ({ page }) => {
  await open(page, 'anim=0&view=room:1:1');
  const r3 = await pointOf(page, 'rack:3');
  await page.mouse.dblclick(r3.x, r3.y);
  await expect.poll(async () => (await state(page)).level).toBe('rack');
  await settled(page);
  expect(viewParam(page)).toBe('rack:3');
  expect(new URL(page.url()).searchParams.get('source')).toBe('fixture'); // other parameters stay
  await page.reload();
  await page.waitForFunction(() => !!window.__iso);
  await page.evaluate(() => window.__iso!.ready);
  expect((await state(page)).crumbs.at(-1)).toBe('ND-R3');
  await page.goBack();
  await expect.poll(async () => (await state(page)).level).toBe('room');
  await settled(page);
  expect(viewParam(page)).toBe('room:1:1');
  await page.goForward();
  await expect.poll(async () => (await state(page)).level).toBe('rack');
  expect(viewParam(page)).toBe('rack:3');
});

test('search finds a device by part of its name and opens it', async ({ page }) => {
  await open(page, 'anim=0&view=world');
  await page.keyboard.press('/');
  await expect(page.locator('#search')).toBeFocused();
  await page.keyboard.type('srv05');
  await expect(page.locator('#search-hits li')).toHaveText([/^ee-srv05 · device · /, /^nd-srv05 · device · North Depot \/ Server Room \/ ND-R3 U\d+$/]);
  await page.locator('#search').fill('CORE1'); // the fixtures carry device detail for nd-core1 (and nd-rtr1)
  await page.locator('#search-hits li', { hasText: 'nd-core1' }).click();
  await expect.poll(async () => (await state(page)).level).toBe('device');
  await settled(page);
  expect(await state(page)).toMatchObject({ level: 'device', crumbs: ['World', 'North Depot', 'Server Room', 'ND-R1', 'nd-core1'] });
  await expect(page.locator('#search-hits')).toBeHidden();
  // keyboard: arrows and Enter
  await page.locator('#search').fill('nd-r');
  await expect(page.locator('#search-hits li').first()).toHaveText(/^ND-R1 · rack/);
  await page.locator('#search').press('ArrowDown');
  await page.locator('#search').press('Enter');
  await expect.poll(async () => (await state(page)).crumbs.at(-1)).toBe('ND-R2');
});

test('the name of the object under the pointer shows next to it', async ({ page }) => {
  await open(page, 'anim=0&view=device:2');
  const p = await pointOf(page, 'port:interface:21');
  await page.mouse.move(p.x, p.y);
  await expect(page.locator('#hover-name')).toHaveText('Te1/0/20');
  await page.mouse.move(5, 590);
  await expect(page.locator('#hover-name')).toBeHidden();
});
