import { expect, test, type Page } from '@playwright/test';
import { boot, game, typed } from '../e2e/game-helpers';

// A quest on the live stack, written for real: against a WRITER's own dev NetBox stack (fresh seed), with the proxy on
// that stack's rw token and WRITE_ENABLED=1, vite in front of it (RACKWALKER_LIVE_URL), and NB_URL + NB_ADMIN_TOKEN
// for the NetBox-side checks and to make the player. The player is a NetBox user made for this run, who may add
// cables and nothing else: they ask North Depot's quest giver, answer what the far end of nd-core1's management port
// is, preview the cable, apply it, and NetBox confirms: XP, the cable tracer, the changelog naming the player. The
// cable and the user are removed afterwards, so the stack is as seeded again and the test can run again.
// Screenshots: docs/screenshots/quests/live-*.png and reward.png.
const SHOTS = '../docs/screenshots/quests';
const NB = process.env.NB_URL;
const TOKEN = process.env.NB_ADMIN_TOKEN;

async function nb<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${NB}${path}`, { ...init, headers: { Authorization: `Bearer ${TOKEN}`, Accept: 'application/json', 'Content-Type': 'application/json' } });
  expect(r.status < 300, `${path}: ${r.status}`).toBe(true);
  return (r.status === 204 ? null : await r.json()) as T;
}

async function choose(page: Page, label: string): Promise<void> {
  for (let i = 0; i < 12; i++) {
    await expect.poll(async () => (await game(page)).dialogue !== null, { timeout: 20_000 }).toBe(true);
    await typed(page);
    const d = (await game(page)).dialogue!;
    if (!d.choices.length) {
      await page.keyboard.press('Enter');
      continue;
    }
    const n = d.choices.findIndex((c) => c.startsWith(label));
    if (n >= 0) return void (await page.click(`#dialogue-choices li:nth-child(${n + 1}) button`));
    await page.click('#dialogue-choices button:has-text("More choices")');
  }
  throw new Error(`no choice "${label}"`);
}

test('live: a quest line from the giver to a cable NetBox confirms, with XP, a tool and the player in the changelog', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const health = await (await page.request.get('/api/health')).json();
  test.skip(!health.write_enabled, 'needs a write-enabled proxy (WRITE=1 FORCE=1 make netbox-env, then make dev)');
  expect(health.netbox.permission_check).toBe('ok');
  type Port = { id: number; cable: { id: number } | null };
  const core = (await nb<{ results: Port[] }>('/api/dcim/interfaces/?device=nd-core1&name=mgmt0')).results[0]!;
  expect(core.cable, 'fresh seed: nd-core1 mgmt0 has no cable (make netbox-reset)').toBeNull();
  let cableId: number | null = null;
  // The player: a NetBox user of their own, whose only write permission is to add cables.
  const name = `quest-walker-${Date.now() % 100000}`;
  const user = await nb<{ id: number }>('/api/users/users/', { method: 'POST', body: JSON.stringify({ username: name, password: `Rw-${crypto.randomUUID()}`, is_active: true }) });
  const perm = await nb<{ id: number }>('/api/users/permissions/', { method: 'POST', body: JSON.stringify({ name: `${name} cables`, enabled: true, users: [user.id], object_types: ['dcim.cable'], actions: ['add'] }) });
  const made = await nb<{ key: string; token: string }>('/api/users/tokens/', { method: 'POST', body: JSON.stringify({ user: user.id, version: 2, write_enabled: false, description: 'quest e2e' }) });
  const playerToken = made.token.startsWith('nbt_') ? made.token : `nbt_${made.key}.${made.token}`;
  try {
    await boot(page);
    await page.fill('#signin-token', playerToken);
    await page.click('#signin-submit');
    await page.evaluate(() => window.__game!.ready);
    if ((await game(page)).dialogue) await page.keyboard.press('Escape');
    // NetBox read now (a run just before this one may have removed its cable in NetBox itself)
    await page.evaluate(() => ((window.__game!.shell.transitionMs = 0), window.__game!.shell.quests!.refresh(true)));
    const before = await page.evaluate(() => window.__game!.shell.quests!.rewards());
    expect(await page.evaluate(() => window.__game!.shell.quests!.boards.netbox!.can_write)).toBe(true);

    // The giver of North Depot: stand beside them (wherever the last visit left the player), press the interact key,
    // hear why it matters, start the line.
    await page.evaluate(() => {
      const s = window.__game!.shell;
      if (s.scene === 'iso') return s.leave();
    });
    await expect.poll(async () => (await game(page)).scene).toBe('overworld');
    await page.evaluate(() => {
      const s = window.__game!.shell;
      const g = s.map!.giverOf(1)!;
      s.overworld!.place({ x: g.x - 1, y: g.y }, 'E');
    });
    await page.keyboard.press('KeyE');
    await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 30_000 }).toBe('quest-offer');
    await page.screenshot({ path: `${SHOTS}/live-giver.png` });
    await choose(page, "Let's find out");
    await typed(page);
    expect((await game(page)).dialogue!.text).toBe('nd-core1 port mgmt0 is carrying an address, but has no cable on record. What is on the other end?');
    await choose(page, 'nd-acc-nd-r2');
    await choose(page, 'Gi1/0/7');
    await choose(page, 'Copper patch lead');

    // The preview comes from NetBox's own state; Apply will write, and says whose name the changelog gets.
    await expect(page.locator('#quest-preview')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('#quest-changes li')).toHaveText(['New cable: nd-core1 mgmt0 to nd-acc-nd-r2 Gi1/0/7 (copper patch lead (Cat 6)).']);
    await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'real');
    await expect(page.locator('#quest-mode')).toContainText('its changelog will name you');
    await expect(page.locator('#quest-apply')).toHaveText('Apply to NetBox');
    await page.screenshot({ path: `${SHOTS}/live-preview-apply.png` });
    await page.click('#quest-apply');

    // NetBox is read again: confirmed, with what the admin can now do, XP for the rank, and (the first time) the tool.
    await expect(page.locator('#quest-reward h2')).toHaveText('NetBox confirms it', { timeout: 60_000 });
    await expect(page.locator('#quest-reward')).toHaveAttribute('data-verified', 'true');
    await expect(page.locator('#quest-reward .now')).toHaveText('You can now trace that port to its far end.');
    await expect(page.locator('#quest-reward')).toContainText("NetBox's cable trace from nd-core1 mgmt0 reaches nd-acc-nd-r2 Gi1/0/7 in 1 hop.");
    const v = await page.evaluate(() => window.__game!.shell.quests!.lastVerdict!);
    expect(v).toMatchObject({ mode: 'real', verified: true, complete: true });
    expect(v.xp_awarded).toBeGreaterThan(0);
    await expect(page.locator('#quest-xp')).toHaveText(`+${v.xp_awarded} XP`);
    expect(v.xp_awarded).toBe(120); // a connectivity line, confirmed whole, for the first time
    await expect(page.locator('#quest-reward .tool')).toContainText('New tool: Cable tracer.');
    expect(await page.evaluate(() => window.__game!.shell.lastJingle)).toBe('confirm');
    await page.screenshot({ path: `${SHOTS}/reward.png` });
    await page.click('#quest-reward-ok');
    await expect.poll(() => page.evaluate(() => window.__game!.shell.quests!.rewards()!.xp)).toBe(before!.xp + v.xp_awarded);
    expect(await page.evaluate(() => document.body.dataset.tracer)).toBe('open');

    // NetBox itself: the cable is there, typed as answered, and its changelog names the player and the quest.
    const now = (await nb<{ results: Port[] }>('/api/dcim/interfaces/?device=nd-core1&name=mgmt0')).results[0]!;
    cableId = now.cable!.id;
    const cable = await nb<{ type: string; b_terminations: { object: { name: string; device: { name: string } } }[] }>(`/api/dcim/cables/${cableId}/`);
    expect([cable.type, cable.b_terminations[0]!.object.device.name, cable.b_terminations[0]!.object.name]).toEqual(['cat6', 'nd-acc-nd-r2', 'Gi1/0/7']);
    const me = await (await page.request.get('/api/game/session')).json();
    const log = await nb<{ results: { message: string }[] }>(`/api/core/object-changes/?changed_object_type=dcim.cable&changed_object_id=${cableId}`);
    expect(me.player.username).toBe(name);
    expect(log.results[0]!.message).toBe(`rackwalker (${name}): quest: The other end of nd-core1 mgmt0`);

    // The log: one line confirmed by NetBox, the tool on show.
    await page.click('#quest-next');
    await expect(page.locator('#quest-counts')).toContainText('confirmed by NetBox');
    await expect(page.locator('#quest-rewards')).toContainText('Cable tracer');
    await page.screenshot({ path: `${SHOTS}/live-quest-log.png` });
  } finally {
    // put the stack back as seeded
    const now = (await nb<{ results: Port[] }>('/api/dcim/interfaces/?device=nd-core1&name=mgmt0')).results[0]!;
    if (now.cable) await nb(`/api/dcim/cables/${now.cable.id}/`, { method: 'DELETE' });
    await nb(`/api/users/permissions/${perm.id}/`, { method: 'DELETE' });
    await nb(`/api/users/users/${user.id}/`, { method: 'DELETE' });
  }
  expect(cableId).not.toBeNull();
});
