import { expect, test } from '@playwright/test';
import { boot, game, idle, ready, typed } from '../e2e/game-helpers';

// The game on the live stack: sign in with a real NetBox user's token (NB_PLAYER_TOKEN, else NB_ADMIN_TOKEN), walk
// into each site's building and back out, and reload into the same place. Every scene comes from NetBox through the
// proxy; game state goes only to the proxy's game store. Skipped without a token.
const token = process.env.NB_PLAYER_TOKEN ?? process.env.NB_ADMIN_TOKEN;
const SHOTS = '../docs/screenshots/g0/g0b';

test('live: sign in as a NetBox user, every door in and out, reload keeps the place', async ({ page }) => {
  test.skip(!token, 'needs NB_PLAYER_TOKEN or NB_ADMIN_TOKEN');
  const writes: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/') && r.method() !== 'GET') writes.push(`${r.method()} ${u.pathname}`);
  });
  await boot(page);
  await expect(page.locator('#signin')).toBeVisible(); // a fresh browser context has no session cookie
  await page.fill('#signin-token', token!);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  const me = await page.evaluate(() => fetch('/api/game/session').then((r) => r.json()));
  expect(me.signed_in).toBe(true);
  expect(me.player.id).toBeGreaterThan(0);
  if ((await game(page)).dialogue) {
    await typed(page);
    await page.keyboard.press('Escape');
  }
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  const doors = await page.evaluate(() => window.__game!.shell.map!.doors.map((d) => ({ siteId: d.siteId, x: d.x, y: d.y })));
  expect(doors.map((d) => d.siteId)).toEqual([1, 2, 3]);
  for (const d of doors) {
    await page.evaluate((t) => window.__game!.shell.overworld!.walkTo(t), d);
    await expect.poll(async () => (await game(page)).scene, { timeout: 30_000 }).toBe('iso');
    const g = await game(page);
    expect(JSON.parse(g.view!).siteId).toBe(d.siteId);
    if (g.dialogue) await page.keyboard.press('Escape');
    await expect.poll(async () => page.evaluate(() => window.__iso!.state().error)).toBeNull();
    if (d.siteId === 1) await page.screenshot({ path: `${SHOTS}/live-room.png` });
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await expect.poll(async () => (await game(page)).scene, { timeout: 15_000 }).toBe('overworld');
    expect((await game(page)).pos).toEqual({ x: d.x, y: d.y + 1 });
  }
  await idle(page);
  const at = (await game(page)).pos;
  await page.reload();
  await ready(page);
  expect(await game(page)).toMatchObject({ scene: 'overworld', pos: at, error: null });
  await page.screenshot({ path: `${SHOTS}/live-overworld.png` });
  expect(writes.filter((w) => !w.startsWith('PUT /api/game/') && !w.startsWith('PATCH /api/game/') && !w.startsWith('POST /api/game/session'))).toEqual([]);
});
