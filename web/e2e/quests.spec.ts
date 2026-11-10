// Quests end to end on a fixture-mode proxy (no NetBox, a fresh game store): the quest giver by a door and inside,
// a question with real names as choices, "I don't know yet" as a field task that resumes, the quest log, the marker
// on the overworld and on the object inside, preview and a practice run, the practice island played to a verified
// outcome with its reward moment, the island's Confusion once everything is in order, and the cable-tracer gate.
// Real writes and real XP are in e2e-live/quests-live.spec.ts. Screenshots go to docs/screenshots/quests/.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { boot, game, typed } from './game-helpers';

const SHOTS = '../docs/screenshots/quests';
const players = JSON.parse(readFileSync(new URL('../../fixtures/players.json', import.meta.url), 'utf8')).players as { token: string }[];
const ISLAND = 9_000_001;

async function signIn(page: Page, i: number): Promise<void> {
  await boot(page);
  await page.fill('#signin-token', players[i]!.token);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await page.keyboard.press('Escape');
  await page.evaluate(() => ((window.__game!.shell.transitionMs = 0), window.__game!.shell.quests!.refresh()));
}
const q = <T>(page: Page, f: (s: NonNullable<Window['__game']>['shell']) => T) => page.evaluate(f as never, undefined) as Promise<T>;
const shell = <T>(page: Page, body: string) => page.evaluate(`(() => { const s = window.__game.shell; ${body} })()`) as Promise<T>;

/** Goes through a quest giver's lines until choices show, then picks the one that starts with `label` (paging on). */
async function choose(page: Page, label: string): Promise<void> {
  for (let i = 0; i < 12; i++) {
    await expect.poll(async () => (await game(page)).dialogue !== null).toBe(true);
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
const talkTo = (page: Page, siteId: number) =>
  page.evaluate((id) => {
    const s = window.__game!.shell;
    s.overworld!.walkToGiver(s.map!.giverOf(id)!);
  }, siteId);
async function api(page: Page, path: string, body?: unknown): Promise<any> {
  return page.evaluate(async ([path, body]) => {
    const csrf = (await (await fetch('/api/game/session')).json()).csrf_token;
    const r = await fetch(path as string, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-RackWalker-CSRF': csrf }, body: JSON.stringify(body) });
    return r.json();
  }, [path, body] as const);
}

test('a quest giver by every door: a real question, "I don\'t know yet" as a field task, and it resumes', async ({ page }) => {
  await signIn(page, 26);
  // one giver per site (and one on the practice island), each beside its door, each with a bubble
  const givers = await shell<{ siteId: number; x: number; y: number }[]>(page, 'return s.map.givers');
  expect(givers.map((g) => g.siteId).sort((a, b) => a - b)).toEqual([1, 2, 3, ISLAND]);
  for (const g of givers) {
    const d = await page.evaluate((id) => window.__game!.shell.map!.doorOf(id)!, g.siteId);
    expect([g.x, g.y]).toEqual([d.x + 2, d.y + 1]);
  }
  expect(await shell(page, 'return [...s.overworld.marks]')).toEqual([[1, 'offer'], [2, 'offer'], [3, 'offer'], [ISLAND, 'offer']]);
  await expect(page.locator('#quest-next')).toContainText('Next: The other end of nd-rtr1 ge-0/0/0');
  await page.screenshot({ path: `${SHOTS}/giver-overworld.png` });

  // Tap the giver: the player walks up and they say why it matters, in plain words, with the lines on offer.
  const at = await page.evaluate(() => {
    const s = window.__game!.shell;
    return s.overworld!.clientOf(s.map!.giverOf(1)!);
  });
  await page.mouse.click(at.x, at.y);
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 10_000 }).toBe('quest-offer');
  await typed(page);
  let d = (await game(page)).dialogue!;
  // the first thing they say is what is missing and where, with the names NetBox has
  expect(d.text).toBe("nd-rtr1 ge-0/0/0 has an address but no cable. A port in use with no far end on record cannot be traced. Let's find its far end.");
  expect(d.choices).toEqual(["Let's find out", 'Or: What powers rack ND-R2', 'Show me the quest log', 'Not now']);
  await expect(page.locator('#dialogue .name')).toHaveText('Tansy');
  await expect(page.locator('#dialogue .portrait-giver')).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/giver-dialogue.png` });

  // The question names real objects; its choices are plausible far ends from NetBox, and "I don't know yet".
  await choose(page, "Let's find out");
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('quest-step');
  await typed(page);
  d = (await game(page)).dialogue!;
  expect(d.text).toBe('What is on the other end of nd-rtr1 ge-0/0/0?');
  expect(d.choices).toEqual(['nd-fw1 (firewall)', 'nd-core1 (switch)', 'More choices', "I don't know yet"]);
  await page.screenshot({ path: `${SHOTS}/giver-question.png` });
  await choose(page, "I don't know yet");

  // A field task: what to look for on site. Nothing is lost, nothing is charged.
  await expect(page.locator('#quest-field-task')).toBeVisible();
  await expect(page.locator('#quest-checklist li').first()).toHaveText('Find nd-rtr1: rack ND-R1 U36 in Server Room.');
  await expect(page.locator('#quest-field-go')).toHaveText('Show me nd-rtr1 (rack ND-R1 U36 in Server Room)');
  await page.screenshot({ path: `${SHOTS}/field-task.png` });
  await page.click('#quest-field-ok');
  await expect(page.locator('#quest-field-task')).toHaveCount(0);

  // The quest log: the parked line waits beside three fresh ones.
  await page.click('#quest-next');
  await expect(page.locator('#quest-log')).toBeVisible();
  await expect(page.locator('#quest-waiting .q')).toHaveCount(1);
  await expect(page.locator('#quest-waiting .q')).toContainText('waiting for you (field task)');
  await expect(page.locator('#quest-open .q[data-status=offered]').first()).toBeVisible();
  await expect(page.locator('#quest-rewards')).toContainText('Level 1 · 0 / 250 XP');
  await expect(page.locator('#quest-sites [data-site="1"]')).toContainText('North Depot: 87%');
  // said up front, not after the last answer: on this proxy nothing would be written
  await expect(page.locator('#quest-write-note')).toHaveText('This proxy serves canned fixture data: nothing is written. Lines you finish count as practice: no XP.');
  await page.screenshot({ path: `${SHOTS}/quest-log.png` });

  // "I know more now": the same question again, then the far port, the cable, and a preview of the change.
  await page.locator('#quest-waiting .q button', { hasText: 'I know more now' }).click();
  await choose(page, 'nd-fw1');
  // the likeliest ports first: the highest-numbered free ones
  await expect.poll(async () => (await game(page)).dialogue?.text).toBe('Which port on nd-fw1 does it land on?');
  await typed(page);
  expect((await game(page)).dialogue!.choices.slice(0, 2)).toEqual(['port8', 'port7']);
  await choose(page, 'port8');
  await choose(page, 'Multimode fibre');
  await expect(page.locator('#quest-preview')).toBeVisible();
  await expect(page.locator('#quest-changes li')).toHaveText(['New cable: nd-rtr1 ge-0/0/0 to nd-fw1 port8 (multimode fibre).']);
  // one answer can be changed without starting again: the last question comes back, the earlier answers stay
  await page.click('#quest-back');
  await expect.poll(async () => (await game(page)).dialogue?.text).toBe('What kind of cable is it?');
  await choose(page, 'Copper patch lead');
  await expect(page.locator('#quest-preview')).toBeVisible();
  await expect(page.locator('#quest-preview .said li').first()).toContainText('What is on the other end of nd-rtr1 ge-0/0/0? → nd-fw1 (firewall)');
  await expect(page.locator('#quest-preview .said li')).toHaveCount(3);
  await expect(page.locator('#quest-changes li')).toHaveText(['New cable: nd-rtr1 ge-0/0/0 to nd-fw1 port8 (copper patch lead (Cat 6)).']);
  await expect(page.locator('#quest-diff')).toContainText('connect cable');
  await expect(page.locator('#quest-diff')).toContainText('type: (empty) → cat6');
  await expect(page.locator('#quest-mode')).toContainText('nothing is written');
  await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'dry_run');
  await page.screenshot({ path: `${SHOTS}/preview.png` });

  // On a proxy that does not write, the completion is a practice run, clearly marked, with no XP.
  await page.click('#quest-apply');
  await expect(page.locator('#quest-reward')).toBeVisible();
  await expect(page.locator('#quest-reward h2')).toHaveText('Practice run');
  await expect(page.locator('#quest-reward')).toContainText('Practice run, nothing was written');
  await expect(page.locator('#quest-xp')).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/practice-run.png` });
  await page.click('#quest-reward-ok');
  await expect.poll(() => shell(page, 'return s.quests.rewards()')).toMatchObject({ xp: 0, practised: 1, completed: 0 });
  expect(await shell(page, 'return s.lastJingle')).toBeNull();
  // a practice run changes nothing: the line still stands with every answer and its change ready
  await page.click('#quest-next');
  const kept = page.locator('#quest-waiting .q', { hasText: 'The other end of nd-rtr1 ge-0/0/0' });
  await expect(kept).toContainText('practised (not written)');
  await kept.locator('button', { hasText: 'Show the change' }).click();
  await expect(page.locator('#quest-preview .said li')).toHaveCount(3);
  await expect(page.locator('#quest-changes li')).toHaveText(['New cable: nd-rtr1 ge-0/0/0 to nd-fw1 port8 (copper patch lead (Cat 6)).']);
});

test('the marker: an arrow towards a building off screen, then the object inside it', async ({ page }) => {
  await signIn(page, 27);
  // Track a line at East Exchange from the log: the arrow at the edge of the view points towards its quest giver.
  await page.click('#quest-next');
  await page.locator('#quest-open .q', { hasText: 'ee-rtr1 ge-0/0/0' }).locator('button', { hasText: 'Point me there' }).click();
  await expect(page.locator('#quest-next')).toContainText('ee-rtr1 (rack EE-A1 U36 in Hall A)');
  const far = await shell<boolean>(page, `const g = s.map.giverOf(3), v = s.overworld.view;
    const x = g.x * 16 + 8 - v.x, y = g.y * 16 + 8 - v.y; return x < 0 || y < 0 || x >= 256 || y >= 224;`);
  expect(far).toBe(true);
  await expect.poll(() => shell(page, 'return s.overworld.pointerShown')).not.toBeNull();
  const arrow = await shell<{ x: number; y: number; dir: string }>(page, 'return s.overworld.pointerShown');
  const want = await shell<{ dx: number; dy: number }>(page, `const g = s.map.giverOf(3), v = s.overworld.view;
    return { dx: g.x * 16 + 8 - v.x - 128, dy: g.y * 16 + 8 - v.y - 112 };`);
  if (arrow.dir === 'e' || arrow.dir === 'w') expect(arrow.dir).toBe(want.dx > 0 ? 'e' : 'w');
  else expect(arrow.dir).toBe(want.dy > 0 ? 's' : 'n');
  await page.screenshot({ path: `${SHOTS}/marker-overworld.png` });

  // Next to the giver the arrow is gone: the bubble over their head is the marker.
  await talkTo(page, 3);
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 30_000 }).toBe('quest-offer');
  expect(await shell(page, 'return s.overworld.pointerShown')).toBeNull();
  await page.keyboard.press('Escape');

  // Inside the building the quest giver is there too, and "Point me there" opens the rack and marks the device.
  await shell(page, 'return s.enter(s.map.doorOf(3))');
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-first-building');
  await page.keyboard.press('Escape');
  await expect(page.locator('#quest-giver')).toContainText('things to find out');
  await page.click('#quest-next');
  await page.locator('#quest-open .q', { hasText: 'ee-rtr1 ge-0/0/0' }).locator('button', { hasText: 'Point me there' }).click();
  await expect.poll(async () => (await game(page)).view).toContain('"rackId":9');
  await expect.poll(() => shell(page, 'return s.quests.pinShown')).toBe('device:44');
  const pin = await page.locator('#quest-pin').boundingBox();
  const dev = await page.evaluate(() => window.__iso!.screenOf('device:44'));
  expect(Math.abs(pin!.x + pin!.width / 2 - dev!.x)).toBeLessThan(3);
  await page.screenshot({ path: `${SHOTS}/marker-iso.png` });
  await page.click('#quest-giver');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('quest-offer');
  await page.screenshot({ path: `${SHOTS}/giver-inside.png` });
  await page.keyboard.press('Escape');

  // A gate that opens with a tool: without the cable tracer, a trace inside a building is refused, and Ilatea says why.
  expect(await page.evaluate(() => document.body.dataset.tracer)).toBe('locked');
  await page.evaluate(() => window.__iso!.editor.runTrace('interface', 21));
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-tracer');
  expect(await page.evaluate(() => window.__iso!.editor.state().trace)).toEqual([]);
});

test('the practice island: a whole quest from giver to verified outcome, one line from Ilatea, nothing for NetBox', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/changes/')) writes.push(u.pathname);
  });
  await signIn(page, 28);
  // From the menu, at any time; Ilatea says one line (once) and lets the player act.
  await page.keyboard.press('KeyM');
  await page.click('#menu-practice');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-practice');
  await typed(page);
  expect((await game(page)).dialogue!.text).toBe('This island is made up: nothing here reaches NetBox. Talk to the quest giver by the hut and answer what you can.');
  const hut = await page.evaluate((id) => window.__game!.shell.map!.doorOf(id)!, ISLAND);
  expect((await game(page)).pos).toEqual({ x: hut.x, y: hut.y + 2 });
  await page.screenshot({ path: `${SHOTS}/practice-island.png` });
  await page.keyboard.press('Escape');
  // Dismissed means dismissed: she never says it again.
  await shell(page, 's.goToPractice()');
  expect((await game(page)).dialogue).toBeNull();

  // The giver, with the interact key: the first line is the most valuable unknown of the island.
  await page.evaluate(([x, y]) => window.__game!.shell.overworld!.walkTo({ x: x!, y: y! }), [hut.x + 1, hut.y + 1]);
  await expect.poll(async () => (await game(page)).pos).toEqual({ x: hut.x + 1, y: hut.y + 1 });
  await page.keyboard.press('KeyE');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('quest-offer');
  await typed(page);
  expect((await game(page)).dialogue!.text).toBe("Nobody can tell from NetBox how Practice Island reaches the outside world. Let's find out.");
  await choose(page, "Let's find out");
  await choose(page, 'sw-quay-1');
  // A typed value: the circuit ID from the bill. An empty answer would park the line; a real one goes on.
  await expect(page.locator('#quest-step')).toBeVisible();
  await expect(page.locator('#quest-step')).toContainText('What is the circuit ID? Example Fibre prints it on the bill and on the hand-off box.');
  await page.fill('#quest-text', 'EXF-20417');
  await page.screenshot({ path: `${SHOTS}/typed-answer.png` });
  await page.keyboard.press('Enter');
  await choose(page, 'Internet');
  await choose(page, 'Te1/1/2');
  await choose(page, 'Single-mode fibre');
  await expect(page.locator('#quest-preview')).toBeVisible();
  await expect(page.locator('#quest-diff tr')).toHaveCount(3);
  await expect(page.locator('#quest-changes li')).toHaveText(['New circuit EXF-20417 from Example Fibre (Internet).', 'Circuit EXF-20417 gets its A end at Practice Island.',
    'New cable: sw-quay-1 Te1/1/2 to circuit EXF-20417 (side A) (single-mode fibre).']);
  await expect(page.locator('#quest-diff')).toContainText('cid: (empty) → EXF-20417');
  await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'practice');
  await page.screenshot({ path: `${SHOTS}/preview-practice.png` });
  await page.click('#quest-apply');

  // The island is read again; the reward moment says what is now possible. Practice pays no XP.
  await expect(page.locator('#quest-reward h2')).toHaveText('Practice complete');
  await expect(page.locator('#quest-reward .now')).toHaveText("You can now trace Practice Island to the carrier's hand-off.");
  await expect(page.locator('#quest-reward .sparkle i')).toHaveCount(10);
  expect(await shell(page, 'return s.lastJingle')).toBe('practice');
  expect(await shell(page, 'return s.quests.lastVerdict')).toMatchObject({ mode: 'practice', verified: true, complete: true, xp_awarded: 0 });
  await page.screenshot({ path: `${SHOTS}/reward-practice.png` });
  await page.click('#quest-reward-ok');
  // Ilatea's one line about that kind of quest, once.
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-practice-connectivity');
  await page.keyboard.press('Escape');
  expect(writes).toEqual([]); // nothing went near the change routes that write NetBox

  // The hut can be walked into like any building: the island's rooms and racks, with the new circuit's cable on the switch.
  await shell(page, 'return s.enter(s.map.doorOf(9000001))');
  await expect.poll(async () => (await game(page)).scene).toBe('iso');
  expect((await game(page)).view).toContain(`"siteId":${ISLAND}`);
  const dev = await api(page, '/api/device/9000001');
  expect(dev.meta.source).toBe('fixture');
  expect(dev.interfaces.find((i: { name: string }) => i.name === 'Te1/1/2').link_peers[0]).toMatchObject({ kind: 'circuit_termination', circuit_cid: 'EXF-20417' });
  await page.screenshot({ path: `${SHOTS}/practice-inside.png` });
});

test('an errand bundles labels; with reduce motion nothing moves; the island in order shows its Confusion', async ({ page }) => {
  await signIn(page, 29);
  await shell(page, 's.saveSettings({ ...s.settings, reduce_motion: true })');
  // Put the island's wiring in order through the routes, so the errands come up.
  const play = async (kinds: string[]) => {
    for (let n = 0; n < 12; n++) {
      const b = await api(page, '/api/game/quests?scope=practice');
      let line = b.quests.find((x: any) => kinds.includes(x.kind) && ['offered', 'active'].includes(x.status));
      if (!line) return;
      for (let i = 0; i < 10 && line.status !== 'ready'; i++) {
        const s = line.step;
        const a = s.input === 'choice' ? { choice: (s.choices.find((c: any) => ['candidate', 'confirm', 'skip'].includes(c.kind)) ?? s.choices.find((c: any) => c.id === 'new')).id }
          : s.input === 'text' ? { text: 'EXF-1' }
          : { values: Object.fromEntries(s.fields.map((f: any, k: number) => [f.id, f.type === 'select' ? f.options[0].id : f.type === 'number' ? 5 : `PI-${k}`])) };
        line = await api(page, '/api/game/quests/answer', { scope: 'practice', quest_id: line.id, step: s.id, ...a });
      }
      await api(page, '/api/game/quests/apply', { scope: 'practice', quest_id: line.id });
    }
  };
  await play(['site_no_way_out', 'locations_not_linked', 'site_island', 'device_unconnected']);
  await shell(page, 'return s.quests.refresh()');

  // The audit of rack PI-Q1: one errand for the labels of one rack, never one field at a time.
  await page.click('#quest-next');
  await page.locator('#quest-open .q', { hasText: 'Audit rack PI-Q1' }).locator('button', { hasText: 'Start' }).click();
  await expect(page.locator('#quest-step')).toContainText('Walk rack PI-Q1 top to bottom and read each label. Leave blank what you cannot see.');
  await expect(page.locator('#quest-step label.f')).toHaveText(['sw-quay-2 (U8): serial number', 'sw-quay-2 (U8): asset tag']);
  // The label maker (on loan on the island) proposes the next tag of the site's pattern; the field stays empty until the player takes it.
  await expect(page.locator('#quest-field-1')).toHaveValue('');
  await expect(page.locator('#quest-field-1-suggest')).toHaveText('Use PI-0004');
  await page.screenshot({ path: `${SHOTS}/audit-errand.png` });
  await page.fill('#quest-field-0', 'SYN-PI-0002');
  await page.click('#quest-step-send');
  await expect(page.locator('#quest-diff')).toContainText('serial: (empty) → SYN-PI-0002');
  await page.click('#quest-apply');
  // One of two labels: the island confirms what was read, and the errand goes on with the rest only.
  await expect(page.locator('#quest-reward h2')).toHaveText('Practice run');
  await expect(page.locator('#quest-reward .sparkle')).toHaveCount(0);
  expect(await page.evaluate(() => document.body.classList.contains('quest-shake'))).toBe(false);
  await page.click('#quest-reward-ok');
  await expect(page.locator('#quest-step label.f')).toHaveText(['sw-quay-2 (U8): asset tag']);
  await page.click('#quest-field-0-suggest');
  await expect(page.locator('#quest-field-0')).toHaveValue('PI-0004');
  await page.click('#quest-step-send');
  await page.click('#quest-apply');
  await expect(page.locator('#quest-reward h2')).toHaveText('Practice complete');
  await expect(page.locator('#quest-reward .sparkle')).toHaveCount(0); // reduce motion: no sparkle, no shake
  await page.click('#quest-reward-ok');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-practice-identity');
  await page.keyboard.press('Escape');

  // What a device runs is one question (one answer for every device of a model), not a field to read off a label.
  await page.click('#quest-next');
  await page.locator('#quest-open .q', { hasText: 'What sw-quay-2 runs' }).locator('button', { hasText: 'Start' }).click();
  await expect.poll(async () => (await game(page)).dialogue?.text).toBe('What does sw-quay-2 run?');
  await typed(page);
  expect((await game(page)).dialogue!.choices).toEqual(['Example OS', 'Something not listed', "I don't know yet"]);
  await choose(page, 'Example OS');
  await expect(page.locator('#quest-changes li')).toHaveText(['sw-quay-2: platform Example OS.']);
  await page.click('#quest-apply');
  await expect(page.locator('#quest-reward h2')).toHaveText('Practice complete');
  await expect(page.locator('#quest-reward')).toContainText('Practice Island is now');
  await page.click('#quest-reward-ok');

  // Everything else on the island, then: 100 %, all in order, and its Confusion shows by the hut. Optional, visible.
  await play(['site_record', 'rack_devices_unpositioned', 'room_layout']);
  await shell(page, 'return s.quests.refresh()');
  expect(await shell(page, 'return [...s.overworld.bosses]')).toEqual([ISLAND]);
  expect(await shell(page, 'return s.overworld.marks.get(9000001)')).toBe('clear');
  await shell(page, 's.goToPractice()');
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-practice');
  await page.keyboard.press('Escape');
  await page.screenshot({ path: `${SHOTS}/boss-ready.png` });
  await talkTo(page, ISLAND);
  await expect.poll(async () => (await game(page)).dialogue?.script, { timeout: 15_000 }).toBe('quest-offer');
  await typed(page);
  expect((await game(page)).dialogue!.text).toBe('All in order at Practice Island: nothing I can see is missing. Its Confusion has shown itself outside.');
  // no invented work: the only thing on offer is a spot check of one device
  expect((await game(page)).dialogue!.choices).toEqual(['Spot-check one device', 'Show me the quest log', 'Not now']);
  await choose(page, 'Spot-check one device');
  await typed(page);
  expect((await game(page)).dialogue!.text).toBe('Go and look: is sw-quay-1 still in rack PI-Q1 U10 in Quay Room, serial SYN-PI-0001?');
  await choose(page, 'Yes, exactly as recorded');
  await typed(page);
  expect((await game(page)).dialogue!.text).toBe('Checked: the record and the rack agree. Thank you.');
  await page.keyboard.press('Enter');
  await expect.poll(async () => (await game(page)).dialogue).toBeNull();
  await page.click('#quest-next');
  await expect(page.locator('#quest-sites [data-site="9000001"]')).toContainText('Practice Island: 100%');
  await page.screenshot({ path: `${SHOTS}/boss-ready-log.png` });
  await page.click('#quest-boss-9000001');
  await expect(page.locator('#quest-sites [data-site="9000001"]')).toContainText('Its Confusion waits for you.');
  expect(await shell(page, 'return [...s.overworld.bosses]')).toEqual([]);
});

test('the practice island is offered at the very start, in Ilatea\'s first line', async ({ page }) => {
  await boot(page);
  await page.fill('#signin-token', players[30]!.token);
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-intro');
  await typed(page);
  expect((await game(page)).dialogue!.choices).toContain('Let me practise first');
  await page.click('#dialogue-choices button:has-text("Let me practise first")');
  // the player stands in front of the island's hut, and Ilatea says what the island is
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-practice');
  const hut = await page.evaluate((id) => window.__game!.shell.map!.doorOf(id)!, ISLAND);
  expect((await game(page)).pos).toEqual({ x: hut.x, y: hut.y + 2 });
});
