// The game shell end to end, on a real proxy in fixture mode (playwright.config.ts starts it with a fresh game store):
// sign in as a (fixture) NetBox user, Ilatea's intro, walk through a door into the site's room and back out, the
// dialogue by keyboard, mouse and touch, remapped keys, the owls' pages, and reload = you are where you were.
// Screenshots for the docs go to docs/screenshots/game/.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { boot, game, idle, ready, stored, typed } from './game-helpers';

const SHOTS = '../docs/screenshots/game';
const players = JSON.parse(readFileSync(new URL('../../fixtures/players.json', import.meta.url), 'utf8')).players as { token: string; user: { id: number; username: string } }[];

async function signIn(page: Page, i: number): Promise<void> {
  await boot(page);
  await expect(page.locator('#signin')).toBeVisible();
  await page.fill('#signin-token', players[i]!.token);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
}

test('first run: sign in, Ilatea once, door in and out, reload puts you back', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/') && r.method() !== 'GET') writes.push(`${r.method()} ${u.pathname}`);
  });
  await boot(page);
  // A wrong token is refused and never kept in the field.
  await page.fill('#signin-token', 'nbt_wrong.wrongwrongwrong');
  await page.click('#signin-submit');
  await expect(page.locator('#signin-error')).toContainText('did not accept');
  await expect(page.locator('#signin-token')).toHaveValue('');
  await page.fill('#signin-token', players[0]!.token);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);

  // Ilatea speaks one line with choices; a key finishes the typewriter at once.
  let g = await game(page);
  expect(g.scene).toBe('overworld');
  expect(g.dialogue).toMatchObject({ script: 'ilatea-intro', node: 'hello' });
  await expect(page.locator('#dialogue .portrait-ilatea')).toBeVisible();
  await page.keyboard.press('Enter');
  await typed(page);
  g = await game(page);
  expect(g.dialogue!.text).toContain('Small things matter');
  expect(g.dialogue!.choices).toEqual(['On my way', 'How do I walk?', 'Fewer hints, please']);
  // The selected choice is marked by a shape and an attribute, not by colour alone.
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#dialogue-choices button[aria-selected=true]')).toContainText('▶ 2. How do I walk?');
  await page.screenshot({ path: `${SHOTS}/dialogue-choices.png` });
  await page.keyboard.press('Enter');
  await typed(page);
  expect((await game(page)).dialogue).toMatchObject({ node: 'keys', choices: ['Got it', 'Change keys'] });
  await page.keyboard.press('Enter');
  expect((await game(page)).dialogue).toBeNull();
  expect((await game(page)).flags['ilatea.intro.seen']).toBe(true);
  await page.screenshot({ path: `${SHOTS}/overworld.png` });

  // Walk into the door of site 2 (on screen; a click walks there); the scene card shows on the way in.
  const door = await page.evaluate(() => {
    const d = window.__game!.shell.map!.doorOf(2)!;
    return { ...window.__game!.shell.overworld!.clientOf(d), x0: d.x, y0: d.y };
  });
  await page.evaluate(() => (window.__game!.shell.transitionMs = 1500));
  await page.mouse.click(door.x, door.y);
  await expect(page.locator('#scene-card')).toContainText('Entering', { timeout: 15_000 });
  await page.screenshot({ path: `${SHOTS}/door-transition.png` });
  await expect.poll(async () => (await game(page)).scene, { timeout: 15_000 }).toBe('iso');
  await expect(page.locator('#scene-card')).toHaveCount(0, { timeout: 5_000 });
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  g = await game(page);
  expect(JSON.parse(g.view!)).toMatchObject({ level: 'room', siteId: 2 });
  // Her one first-building line; the cancel key closes it and does not also leave the room.
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-first-building');
  await page.keyboard.press('Escape');
  g = await game(page);
  expect(g.dialogue).toBeNull();
  expect(JSON.parse(g.view!).level).toBe('room');
  await page.screenshot({ path: `${SHOTS}/room-after-door.png` });

  // Change the camera, let the autosave land, reload: same room, same camera, no intro again.
  await page.keyboard.press('+');
  await idle(page);
  const before = await game(page);
  await page.reload();
  await ready(page);
  g = await game(page);
  expect(g.scene).toBe('iso');
  expect(g.view).toBe(before.view);
  expect(g.camera).toEqual(before.camera);
  expect(g.dialogue).toBeNull();

  // Out again: Esc to the site level, Esc once more leaves the building and you stand in front of its door.
  await page.keyboard.press('Escape');
  await expect.poll(async () => JSON.parse((await game(page)).view ?? '{}').level).toBe('site');
  await page.keyboard.press('Escape');
  await expect.poll(async () => (await game(page)).scene).toBe('overworld');
  g = await game(page);
  expect(g.pos).toEqual({ x: door.x0, y: door.y0 + 1 });
  expect(g.facing).toBe('S');
  await idle(page);
  await page.reload();
  await ready(page);
  g = await game(page);
  expect(g).toMatchObject({ scene: 'overworld', pos: { x: door.x0, y: door.y0 + 1 }, facing: 'S', dialogue: null, error: null });
  const st = await stored(page);
  expect(st.saves[0]).toMatchObject({ slot: 'auto', data: { scene: 'overworld', iso: null } });
  // Game state goes to the game store only: nothing was written through the NetBox change routes.
  expect(writes.filter((w) => !w.includes('/api/game/'))).toEqual([]);
  expect(writes).toContain('PUT /api/game/saves/auto');
});

test('remapped keys walk the player; settings persist; a stuck nudge can turn hints off', async ({ page }) => {
  await signIn(page, 1);
  // The intro can be dismissed at once with the cancel key.
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await page.keyboard.press('Escape');
  expect((await game(page)).dialogue).toBeNull();

  await page.keyboard.press('KeyM');
  await expect(page.locator('#game-menu')).toBeVisible();
  await page.click('#menu-settings');
  await expect(page.locator('#game-settings')).toBeVisible();
  await page.click('#set-keys button[data-action=up]');
  await page.keyboard.press('KeyI');
  await expect(page.locator('#set-keys-msg')).toContainText('is now I');
  await page.check('#set-motion');
  await page.screenshot({ path: `${SHOTS}/settings.png` });
  // A key that is another action's only key is refused (M is the only "Menu" key).
  await page.click('#set-keys button[data-action=down]');
  await page.keyboard.press('KeyM');
  await expect(page.locator('#set-keys-msg')).toContainText('only key');
  await page.click('#settings-back');
  await page.keyboard.press('Escape');
  await expect(page.locator('#game-menu')).toHaveCount(0);

  const start = (await game(page)).pos;
  await page.keyboard.down('KeyI');
  await expect.poll(async () => (await game(page)).pos.y).toBeLessThan(start.y);
  await page.keyboard.up('KeyI');
  expect((await game(page)).facing).toBe('N');

  // Stuck on the green: one nudge (hint level normal); "Stop the hints" turns them off.
  await page.evaluate(() => (window.__game!.shell.stuckMs = 300));
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 5_000 }).toBe('ilatea-stuck');
  await typed(page); // reduce motion is on: the line is there at once
  await page.click('#dialogue-choices button:has-text("Stop the hints")');
  expect((await game(page)).hint).toBe('off');
  await idle(page);
  await page.reload();
  await ready(page);
  const g = await game(page);
  expect(g.keysUp[0]).toBe('KeyI');
  expect(g.hint).toBe('off');
  expect(g.dialogue).toBeNull();
  const st = await stored(page);
  expect(st.settings.reduce_motion).toBe(true);
  expect(st.flags).toMatchObject({ 'ilatea.intro.seen': true, 'ilatea.stuck.seen': true });
});

test('Ilatea names your own keys and stands on the green: walk up and press interact, or click her', async ({ page }) => {
  await signIn(page, 4);
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await page.keyboard.press('Enter'); // finishes the typewriter
  await typed(page);
  await page.keyboard.press('Digit2'); // How do I walk?
  await typed(page);
  let g = await game(page);
  expect(g.dialogue).toMatchObject({ node: 'keys', choices: ['Got it', 'Change keys'] });
  expect(g.dialogue!.text).toBe('Walk with ↑/W ←/A ↓/S →/D, or tap where you want to go. M opens the menu.');
  await page.keyboard.press('Digit2'); // Change keys: the settings page opens
  await expect(page.locator('#game-settings')).toBeVisible();
  await page.click('#set-keys button[data-action=interact]');
  await page.keyboard.press('KeyF');
  await expect(page.locator('#set-keys-msg')).toContainText('is now F');
  await page.click('#settings-back');
  await page.keyboard.press('Escape');
  await expect(page.locator('#game-menu')).toHaveCount(0);
  expect(await page.locator('#game-button').getAttribute('title')).toBe('Menu (M)');

  // One step left from the start puts Ilatea in reach; the (remapped) interact key talks to her.
  const guide = await page.evaluate(() => ({ ...window.__game!.shell.map!.guide }));
  await page.keyboard.press('ArrowLeft');
  await expect.poll(async () => (await game(page)).pos).toEqual({ x: guide.x + 1, y: guide.y });
  await page.keyboard.press('KeyF');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-menu');
  expect((await game(page)).facing).toBe('W');
  await page.keyboard.press('Escape');
  expect((await game(page)).dialogue).toBeNull();

  // Walk away, then click her: the player walks back beside her and she talks.
  await page.evaluate(() => window.__game!.shell.overworld!.walkTo({ x: window.__game!.shell.map!.start.x + 2, y: window.__game!.shell.map!.start.y }));
  await expect.poll(async () => (await game(page)).pos.x).toBe(guide.x + 4);
  const at = await page.evaluate((t) => window.__game!.shell.overworld!.clientOf(t), guide);
  await page.mouse.click(at.x, at.y);
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 10_000 }).toBe('ilatea-menu');
  g = await game(page);
  expect(Math.abs(g.pos.x - guide.x) + Math.abs(g.pos.y - guide.y)).toBe(1);
});

test.describe('touch', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 780 } });
  test('choices by tap; Byte and Nocti pages from the menu button; the close button always ends a talk', async ({ page }) => {
    await signIn(page, 2);
    await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
    await page.tap('#dialogue-text'); // finishes the typewriter
    await typed(page);
    await page.tap('#dialogue-choices button:has-text("Fewer hints")');
    await expect.poll(async () => (await game(page)).hint).toBe('low');
    await typed(page);
    await page.tap('#dialogue-text'); // the last line: a tap goes on, and ends the talk
    expect((await game(page)).dialogue).toBeNull();

    await page.tap('#game-button');
    await page.tap('#menu-byte');
    await expect(page.locator('#guide-byte .portrait-byte')).toBeVisible();
    await expect(page.locator('#guide-byte')).toContainText('commands');
    await page.screenshot({ path: `${SHOTS}/byte-page.png` });
    await page.tap('#guide-back');
    await page.tap('#menu-nocti');
    await expect(page.locator('#guide-nocti .portrait-nocti')).toBeVisible();
    await page.tap('#guide-back');
    await page.tap('#menu-ilatea');
    await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-menu');
    await page.tap('#dialogue-close');
    expect((await game(page)).dialogue).toBeNull();
    // Tap a building: the player walks to its door and goes in.
    const p = await page.evaluate(() => {
      const d = window.__game!.shell.map!.doorOf(2)!; // the door above the start tile, on screen at phone width
      return window.__game!.shell.overworld!.clientOf({ x: d.bx + 1, y: d.by + 1 });
    });
    await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
    await page.touchscreen.tap(p.x, p.y);
    await expect.poll(async () => (await game(page)).scene, { timeout: 15_000 }).toBe('iso');
    expect(JSON.parse((await game(page)).view!).siteId).toBe(2);
  });
});

test('sign out ends the session; the plain viewer still opens with view=', async ({ page }) => {
  await signIn(page, 3);
  await page.keyboard.press('Escape');
  await page.keyboard.press('KeyM');
  await page.click('#menu-signout');
  await expect(page.locator('#signin')).toBeVisible();
  expect(await page.evaluate(() => fetch('/api/game/state').then((r) => r.status))).toBe(401);
  await page.goto('/?source=fixture&view=world');
  await page.waitForFunction(() => !!window.__iso);
  await page.evaluate(() => window.__iso!.ready);
  expect(await page.evaluate(() => !!window.__game)).toBe(false);
  await expect(page.locator('#signin')).toHaveCount(0);
});
