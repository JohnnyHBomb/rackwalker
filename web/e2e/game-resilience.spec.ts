// The game shell when things go wrong or the player does something unexpected, on the fixture-mode proxy: a building
// that does not open, a proxy or NetBox that is down at load, a session that changes in another tab, a saved place
// that no longer exists, input while a panel is open, and the guide's timing. Nothing may fail silently, freeze the
// player or lose their place. Each test signs in as its own fixture player, so they never share state.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { game, idle, ready, stored, typed } from './game-helpers';

const players = JSON.parse(readFileSync(new URL('../../fixtures/players.json', import.meta.url), 'utf8')).players as { token: string; user: { id: number } }[];
const down = { status: 502, json: { error: { code: 'netbox_unreachable', message: 'NetBox did not answer.', details: null } } };

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return errors;
}

async function signIn(page: Page, i: number, opts: { closeIntro?: boolean } = {}): Promise<void> {
  await page.goto('/');
  await page.waitForFunction(() => !!window.__game);
  await expect(page.locator('#signin')).toBeVisible();
  await page.fill('#signin-token', players[i]!.token);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  if (opts.closeIntro !== false) await page.evaluate(() => window.__game!.shell.dialogue.close());
}

/** Walks the player into the door of `siteId` the way a player does (the step onto the door tile). */
const walkIntoDoor = (page: Page, siteId: number) =>
  page.evaluate((id) => {
    const g = window.__game!.shell;
    const d = g.map!.doorOf(id)!;
    g.overworld!.walkTo({ x: d.x, y: d.y });
  }, siteId);

const pos = async (page: Page) => (await game(page)).pos;

test('a building that does not open once: a notice, the player outside its door and free to walk; the next try goes in', async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page, 5);
  let refused = 0;
  await page.route('**/api/scene/site/1', (r) => (refused++ === 0 ? r.fulfill(down) : r.continue()));
  await walkIntoDoor(page, 1);
  await expect(page.locator('#game-notice')).toContainText('cannot be opened right now');
  await expect(page.locator('#game-notice')).toContainText('NetBox did not answer');
  let g = await game(page);
  expect(g.scene).toBe('overworld');
  const outside = await page.evaluate(() => window.__game!.shell.map!.outside(window.__game!.shell.map!.doorOf(1)!));
  expect(g.pos).toEqual(outside);
  // Keys walk again (one press is one step, and the step ends) and taps walk again.
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => page.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
  expect((await pos(page)).x).toBe(outside.x + 1);
  await page.keyboard.press('ArrowLeft');
  await expect.poll(() => page.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
  // The second try goes in, and the notice goes away.
  await walkIntoDoor(page, 1);
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await expect(page.locator('#game-notice')).toHaveCount(0);
  g = await game(page);
  expect(JSON.parse(g.view!)).toMatchObject({ siteId: 1 });
  expect(errors).toEqual([]);
});

test('the proxy or NetBox down at load: the page says why and "Try again" starts the game', async ({ page }) => {
  await signIn(page, 6);
  await page.route('**/api/scene/world', (r) => r.fulfill(down));
  await page.reload();
  await page.waitForFunction(() => !!window.__game);
  await expect(page.locator('#load-failed')).toBeVisible();
  await expect(page.locator('#load-failed')).toContainText('NetBox did not answer');
  await page.screenshot({ path: '../docs/screenshots/game/load-failed.png' });
  await page.unroute('**/api/scene/world');
  await page.click('#load-retry');
  await page.evaluate(() => window.__game!.ready);
  expect((await game(page)).scene).toBe('overworld');
});

test('a broken answer at load is a load failure the page names', async ({ page }) => {
  await page.route('**/api/game/session', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{"signed_in": tru' }));
  await page.goto('/');
  await page.waitForFunction(() => !!window.__game);
  // A broken session answer is a load failure like any other: the panel says why.
  await expect(page.locator('#load-failed')).toBeVisible();
  expect((await page.evaluate(() => document.body.innerText)).toLowerCase()).toContain('could not load');
});

test('two tabs: a sign-in in the other tab does not stop this tab from saving', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 600 } });
  const a = await ctx.newPage();
  const b = await ctx.newPage();
  await a.goto('/');
  await b.goto('/');
  for (const p of [a, b]) await p.waitForFunction(() => !!window.__game);
  for (const p of [a, b]) {
    await p.fill('#signin-token', players[7]!.token);
    await p.click('#signin-submit');
    await p.evaluate(() => window.__game!.ready);
    await p.evaluate(() => window.__game!.shell.dialogue.close());
  }
  const start = await pos(a);
  for (let i = 0; i < 2; i++) {
    await a.keyboard.press('ArrowRight'); // Ilatea stands two tiles left of the start
    await expect.poll(() => a.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
  }
  expect(await pos(a)).toEqual({ x: start.x + 2, y: start.y });
  await idle(a);
  expect((await game(a)).error).toBeNull();
  await expect(a.locator('#game-notice')).toHaveCount(0);
  await a.reload();
  await ready(a);
  expect(await pos(a)).toEqual({ x: start.x + 2, y: start.y });
  await ctx.close();
});

test('signed out in another tab: a notice, sign in again here, and the place walked to meanwhile is saved', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 600 } });
  const a = await ctx.newPage();
  await a.goto('/');
  await a.waitForFunction(() => !!window.__game);
  await a.fill('#signin-token', players[8]!.token);
  await a.click('#signin-submit');
  await a.evaluate(() => window.__game!.ready);
  await a.evaluate(() => window.__game!.shell.dialogue.close());
  const b = await ctx.newPage();
  await b.goto('/');
  await b.evaluate(async () => {
    const s = await (await fetch('/api/game/session')).json();
    await fetch('/api/game/session', { method: 'DELETE', headers: { 'X-RackWalker-CSRF': s.csrf_token } });
  });
  await b.close();
  const start = await pos(a);
  await a.keyboard.press('ArrowLeft');
  await expect(a.locator('#game-notice')).toContainText('signed out');
  expect((await game(a)).error).toContain('session has ended');
  // The menu no longer claims that progress saves by itself.
  await a.keyboard.press('KeyM');
  await expect(a.locator('#menu-save')).toContainText('not being saved');
  await a.keyboard.press('Escape');
  await a.click('#notice-signin');
  await a.fill('#signin-token', players[8]!.token);
  await a.click('#signin-submit');
  await expect(a.locator('#signin')).toHaveCount(0);
  await expect.poll(async () => (await game(a)).error).toBeNull();
  await expect(a.locator('#game-notice')).toHaveCount(0);
  await idle(a);
  expect((await stored(a)).saves[0].data.overworld).toMatchObject({ x: start.x - 1, y: start.y });
  await ctx.close();
});

test('a NetBox user deactivated in NetBox: play stops at the sign-in panel, which says why; no building opens; an active sign-in plays on', async ({ page }) => {
  await signIn(page, 15);
  const gone = { status: 401, json: { error: { code: 'unauthorized', message: 'Your NetBox user was deactivated in NetBox.', details: { reason: 'inactive' } } } };
  await page.route('**/api/game/saves/auto', (r) => r.fulfill(gone));
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#signin')).toBeVisible();
  await expect(page.locator('#signin-why')).toContainText('deactivated in NetBox');
  expect((await game(page)).error).toContain('deactivated in NetBox');
  // keys and taps belong to the form; a door does not open while the session is ended
  const at = await pos(page);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    void g.enter(g.map!.doorOf(1)!);
  });
  await page.waitForTimeout(300);
  expect((await game(page)).scene).toBe('overworld');
  await page.unroute('**/api/game/saves/auto');
  await page.fill('#signin-token', players[15]!.token);
  await page.click('#signin-submit');
  await expect(page.locator('#signin')).toHaveCount(0);
  await expect.poll(async () => (await game(page)).error).toBeNull();
  await page.keyboard.press('ArrowRight');
  await expect.poll(async () => (await pos(page)).x).not.toBe(at.x - 5);
});

test('signed out elsewhere: walking into a building keeps the notice, and later failed autosaves still show it', async ({ page }) => {
  await signIn(page, 22);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off' });
  });
  await idle(page);
  const out = { status: 401, json: { error: { code: 'unauthorized', message: 'Sign in first (POST /api/game/session).', details: null } } };
  await page.route('**/api/game/saves/auto', (r) => r.fulfill(out));
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#notice-signin')).toBeVisible();
  await walkIntoDoor(page, 1);
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await expect(page.locator('#notice-signin')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await expect.poll(async () => (await game(page)).scene).toBe('overworld');
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(400);
  await expect(page.locator('#notice-signin')).toBeVisible();
  await page.keyboard.press('KeyM');
  await expect(page.locator('#menu-save')).toContainText('not being saved');
});

test('signing in again as another NetBox user: that player\'s own game loads; nothing of the first player is saved for them', async ({ page }) => {
  // the second player has played once and left a place of their own
  await signIn(page, 24);
  const s0 = await pos(page);
  await page.evaluate((t) => window.__game!.shell.overworld!.walkTo(t), { x: s0.x, y: s0.y + 2 });
  await expect.poll(async () => (await pos(page)).y).toBe(s0.y + 2);
  await expect.poll(async () => (await stored(page)).saves.length).toBe(1);
  const theirs = (await stored(page)).saves.find((x: { slot: string }) => x.slot === 'auto').data.overworld;
  await page.evaluate(async () => {
    const s = await (await fetch('/api/game/session')).json();
    await fetch('/api/game/session', { method: 'DELETE', headers: { 'X-RackWalker-CSRF': s.csrf_token } });
  });
  await signIn(page, 23);
  await page.evaluate(async () => {
    const s = await (await fetch('/api/game/session')).json();
    await fetch('/api/game/session', { method: 'DELETE', headers: { 'X-RackWalker-CSRF': s.csrf_token } });
  });
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#notice-signin')).toBeVisible();
  const first = await pos(page);
  await page.click('#notice-signin');
  await page.fill('#signin-token', players[24]!.token);
  await Promise.all([page.waitForEvent('load'), page.click('#signin-submit')]);
  await page.waitForFunction(() => !!window.__game);
  await page.evaluate(() => window.__game!.ready);
  expect(await page.evaluate(() => window.__game!.shell.state!.player.id)).toBe(players[24]!.user.id);
  await page.evaluate(() => window.__game!.shell.dialogue.close());
  expect(await pos(page)).toEqual({ x: theirs.x, y: theirs.y });
  await idle(page);
  const auto = (await stored(page)).saves.find((x: { slot: string }) => x.slot === 'auto').data.overworld;
  expect(auto).toMatchObject({ x: theirs.x, y: theirs.y });
  expect(auto).not.toMatchObject({ x: first.x, y: first.y });
  await page.keyboard.press('KeyM');
  await expect(page.locator('#game-menu')).toContainText('Playing as finder');
});

test('keys never step into a building, water, a tree or the forest; the menu stops a tap-walk on its way', async ({ page }) => {
  await signIn(page, 20);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off' });
  });
  // a standing tile next to each kind of obstacle, with the arrow that points at it
  const cases = await page.evaluate(() => {
    const g = window.__game!.shell;
    const m = g.map!;
    const r = m.regions[0]!;
    const dirs = [['ArrowUp', 0, -1], ['ArrowDown', 0, 1], ['ArrowLeft', -1, 0], ['ArrowRight', 1, 0]] as const;
    const trees = new Set(m.objects.filter((o) => o.kind === 'tree').map((o) => `${Math.floor(o.x / 16)},${Math.floor(o.y / 16)}`));
    const kindOf = (x: number, y: number) => (m.buildingAt(x, y) ? 'building' : m.at(x, y) === 'W' ? 'water'
      : x < r.x0 + 3 || y < 3 || x >= r.x0 + r.w - 3 || y >= r.h - 3 ? 'forest' : trees.has(`${x},${y}`) ? 'tree' : null);
    const out: { kind: string; at: { x: number; y: number }; key: string }[] = [];
    for (const want of ['building', 'water', 'tree', 'forest'])
      search: for (let y = 0; y < r.h; y++)
        for (let x = r.x0; x < r.x0 + r.w; x++) {
          if (!m.standable(x, y) || m.path(m.start, { x, y }).length === 0 && !(x === m.start.x && y === m.start.y)) continue;
          for (const [key, dx, dy] of dirs)
            if (!m.walkable(x + dx, y + dy) && kindOf(x + dx, y + dy) === want && !m.doorAt(x + dx, y + dy)) {
              out.push({ kind: want, at: { x, y }, key });
              break search;
            }
        }
    return out;
  });
  expect(cases.map((c) => c.kind)).toEqual(['building', 'water', 'tree', 'forest']);
  for (const c of cases) {
    await page.evaluate((t) => window.__game!.shell.overworld!.walkTo(t), c.at);
    await expect.poll(async () => (await pos(page)), { timeout: 20_000 }).toEqual(c.at);
    await expect.poll(() => page.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
    await page.keyboard.down(c.key);
    await page.waitForTimeout(500);
    await page.keyboard.up(c.key);
    expect(await pos(page), `${c.key} into the ${c.kind}`).toEqual(c.at);
  }
  // a long tap-walk, then the menu: the player stops where they are and goes into no building
  await page.evaluate(() => {
    const g = window.__game!.shell;
    const d = g.map!.doorOf(3)!;
    g.overworld!.walkTo({ x: d.x, y: d.y });
  });
  await page.waitForTimeout(300);
  await page.keyboard.press('KeyM');
  const stopped = await pos(page);
  await page.waitForTimeout(1500);
  const g = await game(page);
  expect(g.scene).toBe('overworld');
  expect(g.pos).toEqual(stopped);
  expect(g.menu).toBe('game-menu');
});

test('inside a building, a key that types the old zoom character on another layout does not zoom once zoom is moved', async ({ page }) => {
  await signIn(page, 25);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off', keys: { ...g.settings.keys, zoom_in: ['KeyI'] } });
  });
  await walkIntoDoor(page, 1);
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await page.evaluate(() => window.__iso!.view.settled());
  const scale = async () => (await game(page)).camera!.scale;
  const before = await scale();
  // '+' on a German layout is BracketRight; '=' on a French layout is Digit0
  for (const [key, code] of [['+', 'BracketRight'], ['=', 'Digit0']])
    await page.evaluate(([k, c]) => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: k, code: c, bubbles: true, cancelable: true })), [key, code]);
  await page.waitForTimeout(300);
  expect(await scale()).toBe(before);
  // the moved key still zooms, and the HUD names it
  await page.keyboard.press('KeyI');
  await expect.poll(scale).toBeGreaterThan(before);
  expect(await page.getAttribute('#hud-zoom-in', 'title')).toContain('I');
});

test('a write the proxy cannot take shows a notice, is kept and lands on "Try again now"', async ({ page }) => {
  await signIn(page, 9);
  await page.route('**/api/game/saves/auto', (r) => r.fulfill({ status: 500, json: { error: { code: 'internal', message: 'The proxy hit an unexpected error.', details: null } } }));
  const start = await pos(page);
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#game-notice')).toContainText('Autosave failed');
  await page.unroute('**/api/game/saves/auto');
  await page.click('#notice-retry');
  await expect(page.locator('#game-notice')).toHaveCount(0);
  await idle(page);
  expect((await stored(page)).saves[0].data.overworld).toMatchObject({ x: start.x - 1, y: start.y });
});

test('a saved place in a site that is gone: back on the green with a notice, and the way out works', async ({ page }) => {
  await signIn(page, 10);
  await page.evaluate(async () => {
    const s = await (await fetch('/api/game/session')).json();
    await fetch('/api/game/saves/auto', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-RackWalker-CSRF': s.csrf_token },
      body: JSON.stringify({ scene: 'iso', overworld: { x: 9, y: 9, facing: 'S' }, iso: { site_id: 99, view: 'site:99', camera: null } }),
    });
  });
  await page.reload();
  await ready(page);
  const g = await game(page);
  expect(g.scene).toBe('overworld');
  await expect(page.locator('#game-notice')).toContainText('cannot be opened');
  await page.keyboard.press('ArrowUp');
  await expect.poll(async () => (await game(page)).pos).not.toEqual(g.pos);
});

test('Ilatea waits the full stuck time after a talk closes; she never nudges on the heels of a dismissal', async ({ page }) => {
  await signIn(page, 11, { closeIntro: false });
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await page.evaluate(() => (window.__game!.shell.stuckMs = 2_500));
  await page.waitForTimeout(3_200); // the intro stays open longer than the stuck time
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1_800);
  expect((await game(page)).dialogue).toBeNull();
  // Still idle after the full stuck time since the talk closed: now the one nudge.
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 5_000 }).toBe('ilatea-stuck');
});

test('input while a panel is open: the menu key takes over a talk; taps do not walk behind a panel; a lost focus stops walking; Ctrl/Alt are the browser\'s', async ({ page }) => {
  await signIn(page, 12, { closeIntro: false });
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await page.keyboard.press('KeyM');
  let g = await game(page);
  expect(g.dialogue).toBeNull();
  expect(g.menu).toBe('game-menu');
  // A tap on the green behind the menu does not walk.
  const start = g.pos;
  const grass = await page.evaluate(() => window.__game!.shell.overworld!.clientOf({ x: 3, y: 3 }));
  await page.mouse.click(Math.max(5, Math.min(795, grass.x)), Math.max(5, Math.min(595, grass.y)));
  await page.waitForTimeout(400);
  expect((await game(page)).pos).toEqual(start);
  await page.keyboard.press('Escape');
  expect((await game(page)).menu).toBeNull();
  // Held right arrow, then the window loses focus without a keyup: walking stops.
  await page.keyboard.down('ArrowRight');
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await expect.poll(() => page.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
  const stopped = await pos(page);
  await page.waitForTimeout(500);
  expect(await pos(page)).toEqual(stopped);
  await page.keyboard.up('ArrowRight');
  // Ctrl+D and Alt+ArrowUp are browser shortcuts, not moves.
  await page.keyboard.press('Control+KeyD');
  await page.keyboard.press('Alt+ArrowUp');
  await page.waitForTimeout(300);
  expect(await pos(page)).toEqual(stopped);
});

test('a remapped back key does what Esc does inside a building: it closes an open panel before it zooms out', async ({ page }) => {
  await signIn(page, 13);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off', keys: { ...g.settings.keys, cancel: ['KeyQ', 'Escape'] } });
  });
  await walkIntoDoor(page, 1);
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await page.evaluate(() => window.__iso!.view.settled());
  const r = await page.evaluate(() => window.__iso!.screenOf('rack:1'));
  await page.mouse.click(r!.x, r!.y);
  await expect.poll(() => page.evaluate(() => window.__iso!.state().selected)).toBe('rack:1');
  await page.keyboard.press('KeyQ');
  await page.evaluate(() => window.__iso!.view.settled());
  let st = await page.evaluate(() => window.__iso!.state());
  expect(st.level).toBe('room');
  expect(st.selected).toBeNull();
  await page.keyboard.press('KeyQ');
  await page.evaluate(() => window.__iso!.view.settled());
  st = await page.evaluate(() => window.__iso!.state());
  expect(st.level).toBe('site');
});

test('backing out after a search took the player to another site: the door of the building on screen', async ({ page }) => {
  await signIn(page, 14);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off' });
  });
  await walkIntoDoor(page, 1);
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await page.evaluate(() => window.__iso!.view.settled());
  const target = await page.evaluate(async () => {
    const w = await window.__iso!.view.data.world();
    for (const { site } of w.sites) {
      if (site.id === 1) continue;
      const s = await window.__iso!.view.data.site(site.id);
      if (s.racks.length) return { siteId: site.id, rack: s.racks[0]! };
    }
    return null;
  });
  expect(target).not.toBeNull();
  await page.keyboard.press('/');
  await page.keyboard.type(target!.rack.name);
  await expect(page.locator('#search-hits')).toContainText(target!.rack.name);
  await page.keyboard.press('Enter');
  await page.evaluate(() => window.__iso!.view.settled());
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  expect(await page.evaluate(() => window.__game!.shell.siteId)).toBe(target!.siteId);
  for (let i = 0; i < 5 && (await game(page)).scene === 'iso'; i++) {
    await page.keyboard.press('Backspace');
    await page.evaluate(() => window.__iso!.view.settled());
    await page.waitForTimeout(100);
  }
  expect((await game(page)).scene).toBe('overworld');
  const outside = await page.evaluate((id) => window.__game!.shell.map!.outside(window.__game!.shell.map!.doorOf(id)!), target!.siteId);
  expect(await pos(page)).toEqual(outside);
});

test('settings changed in two tabs: each change is kept, neither tab reverts the other', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 600 } });
  const a = await ctx.newPage();
  await a.goto('/');
  await a.waitForFunction(() => !!window.__game);
  await a.fill('#signin-token', players[14]!.token);
  await a.click('#signin-submit');
  await a.evaluate(() => window.__game!.ready);
  const b = await ctx.newPage();
  await ready(b); // loaded before tab A's change
  await a.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off' });
  });
  await idle(a);
  await b.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, reduce_motion: true });
  });
  await idle(b);
  await expect.poll(async () => (await stored(b)).settings.reduce_motion).toBe(true);
  const s = (await stored(b)).settings;
  expect(s).toMatchObject({ hint_level: 'off', reduce_motion: true });
  expect((await game(b)).hint).toBe('off'); // tab B took tab A's change too
  await ctx.close();
});

test('a NetBox without sites: Ilatea names the one next step (add a site), and never sends the player to a door', async ({ page }) => {
  await page.route('**/api/scene/world', async (r) => {
    const w = await (await r.fetch()).json();
    await r.fulfill({ json: { ...w, sites: [], links: [] } });
  });
  await signIn(page, 16, { closeIntro: false });
  // no site of NetBox's; the practice island (made up, in a region of its own) is always there
  expect(await page.evaluate(() => window.__game!.shell.map!.doors.map((d) => d.siteId))).toEqual([9_000_001]);
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-empty');
  expect((await game(page)).dialogue!.text).toContain('add one site in NetBox');
  await page.keyboard.press('Escape');
  await page.evaluate(() => (window.__game!.shell.stuckMs = 200));
  await page.waitForTimeout(1_500);
  expect((await game(page)).dialogue).toBeNull(); // no "walk into a door" nudge in a world without doors
});

test('reduce motion: level changes inside a building cut instead of zooming', async ({ page }) => {
  await signIn(page, 17);
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, hint_level: 'off' });
    const v = window.__iso!.view as unknown as { animate: (...a: unknown[]) => Promise<void> };
    const real = v.animate.bind(v);
    (window as unknown as { animations: number }).animations = 0;
    v.animate = (...a) => ((window as unknown as { animations: number }).animations++, real(...a));
  });
  const zoomInAndOut = async () => {
    await walkIntoDoor(page, 1);
    await expect.poll(async () => (await game(page)).scene).toBe('iso');
    await page.evaluate(() => window.__iso!.view.settled());
    const r = await page.evaluate(() => window.__iso!.screenOf('rack:2'));
    await page.mouse.dblclick(r!.x, r!.y);
    await page.evaluate(() => window.__iso!.view.settled());
    expect((await page.evaluate(() => window.__iso!.state())).level).toBe('rack');
    for (let i = 0; i < 4 && (await game(page)).scene === 'iso'; i++) {
      await page.keyboard.press('Escape');
      await page.evaluate(() => window.__iso!.view.settled());
    }
    expect((await game(page)).scene).toBe('overworld');
    await page.keyboard.press('ArrowLeft'); // off the door tile's front, so the next walk steps onto the door again
    await expect.poll(() => page.evaluate(() => window.__game!.shell.overworld!.walking)).toBe(false);
    return page.evaluate(() => (window as unknown as { animations: number }).animations);
  };
  const moving = await zoomInAndOut();
  expect(moving).toBeGreaterThan(0); // the count sees the zoom transitions
  await page.evaluate(() => {
    const g = window.__game!.shell;
    g.saveSettings({ ...g.settings, reduce_motion: true });
    (window as unknown as { animations: number }).animations = 0;
  });
  expect(await zoomInAndOut()).toBe(0);
});

for (const [w, h] of [[800, 600], [1024, 768], [1280, 720], [1366, 768]] as const)
  test(`the speech bubble leaves Ilatea and the player in view at ${w}x${h}`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    await page.goto('/');
    await page.waitForFunction(() => !!window.__game);
    await page.fill('#signin-token', players[18]!.token);
    await page.click('#signin-submit');
    await page.evaluate(() => window.__game!.ready);
    // The intro once, then the menu talk (same bubble) for every later size.
    if (!(await game(page)).dialogue) await page.evaluate(() => (window.__game!.shell as unknown as { talkToGuide(): void }).talkToGuide());
    await expect.poll(async () => (await game(page)).dialogue).not.toBeNull();
    const covered = await page.evaluate(() => {
      const g = window.__game!.shell;
      const r = document.getElementById('dialogue')!.getBoundingClientRect();
      const pts = [g.overworld!.clientOf(g.overworld!.pos), g.overworld!.clientOf(g.map!.guide)];
      return pts.filter((p) => p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom).length;
    });
    expect(covered).toBe(0);
    if (w === 1280) {
      await typed(page);
      await page.screenshot({ path: '../docs/screenshots/game/dialogue-clear-of-sprites.png' });
    }
    await ctx.close();
  });
