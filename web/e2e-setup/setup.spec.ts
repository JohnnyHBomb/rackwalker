// The setup companion in the browser, on a NetBox with nothing in it (playwright.setup.config.ts): Ilatea offers the
// first site; the site, its rack, its first box from the device-type library and its carrier from the provider pack are
// each one previewed change; a serial that does not look like the vendor's is asked about; the catalogue lists what
// NetBox then holds. A run leaves the checkout as it was: screenshots go to test-results/setup-shots/ (not tracked);
// RACKWALKER_SETUP_SHOTS=1 writes them to docs/screenshots/setup/ instead, for the pages that show them.
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { boot, game, typed } from '../e2e/game-helpers';

const expect = baseExpect.configure({ timeout: 20_000 });
const SHOTS = process.env.RACKWALKER_SETUP_SHOTS === '1' ? '../docs/screenshots/setup' : 'test-results/setup-shots';

/** Goes through the speaker's lines until choices show, then picks the one that starts with `label` (paging on). */
async function choose(page: Page, label: string, shot?: string): Promise<void> {
  for (let i = 0; i < 14; i++) {
    await expect.poll(async () => (await game(page)).dialogue !== null).toBe(true);
    await typed(page);
    const d = (await game(page)).dialogue!;
    if (!d.choices.length) {
      await page.keyboard.press('Enter');
      continue;
    }
    const n = d.choices.findIndex((c) => c.startsWith(label));
    if (n >= 0) {
      if (shot) await page.screenshot({ path: `${SHOTS}/${shot}.png` });
      return void (await page.click(`#dialogue-choices li:nth-child(${n + 1}) button`));
    }
    await page.click('#dialogue-choices button:has-text("More choices")');
  }
  throw new Error(`no choice "${label}"`);
}
async function type(page: Page, text: string): Promise<void> {
  await expect(page.locator('#quest-step #quest-text')).toBeVisible();
  await page.fill('#quest-text', text);
  await page.click('#quest-step-send');
}
async function fill(page: Page, values: Record<string, string>): Promise<void> {
  await expect(page.locator('#quest-step')).toBeVisible();
  for (const [field, value] of Object.entries(values)) await page.fill(`#quest-step [data-field="${field}"]`, value);
  await page.click('#quest-step-send');
}
async function apply(page: Page, shot?: string): Promise<string> {
  await expect(page.locator('#quest-preview')).toBeVisible();
  await expect(page.locator('#quest-apply')).toBeEnabled();
  if (shot) await page.screenshot({ path: `${SHOTS}/${shot}.png` });
  await page.click('#quest-apply');
  await expect(page.locator('#quest-reward')).toBeVisible();
  const text = (await page.locator('#quest-reward').textContent()) ?? '';
  await page.keyboard.press('Escape');
  await expect(page.locator('#quest-reward')).toHaveCount(0);
  return text;
}
/** Starts a line from the quest log's data, as its "Start" button does. */
const start = (page: Page, kind: string) =>
  page.evaluate(async (kind) => {
    const s = window.__game!.shell;
    await s.quests!.refresh(true);
    const q = s.quests!.boards.netbox!.quests.find((x) => x.kind === kind && x.status !== 'done' && x.status !== 'dismissed');
    if (!q) throw new Error(`no open ${kind} line: ${s.quests!.boards.netbox!.quests.map((x) => `${x.kind}:${x.status}`).join(', ')}`);
    void s.quests!.run(q);
  }, kind);
const idle = (page: Page) => expect.poll(() => page.evaluate(() => !window.__game!.shell.quests!.panelOpen && !window.__game!.shell.dialogue.open)).toBe(true);

test('an empty NetBox is set up by playing: first site, rack, boxes from the library, the carrier, the catalogue', async ({ page }) => {
  await boot(page);
  await page.fill('#signin-token', 'nbt_synthetic.not-a-secret-0123456789');
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  // ---- no site: Ilatea offers the first one, and asks it herself (there is no quest giver yet)
  await expect.poll(async () => (await game(page)).dialogue?.script).toBe('ilatea-empty');
  await typed(page);
  expect((await game(page)).dialogue!.text).toContain('this NetBox has no site yet');
  await page.screenshot({ path: `${SHOTS}/onboarding.png` });
  await choose(page, 'Yes: my first site');
  await expect.poll(async () => (await game(page)).dialogue?.text ?? '').toContain('Which country is your first site in?');
  await expect(page.locator('#dialogue')).toHaveAttribute('data-speaker', 'ilatea');
  await choose(page, 'Netherlands');
  await type(page, 'Mill Lane');
  await expect(page.locator('#quest-preview')).toContainText('New site Mill Lane in Netherlands.');
  const reload = page.waitForEvent('load');
  expect(await apply(page)).toContain('NetBox confirms it.');
  // NetBox has a site now: the world is made again around it
  await reload;
  await page.waitForFunction(() => !!window.__game);
  await page.evaluate(() => window.__game!.ready);
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  await expect.poll(async () => (await game(page)).scene).toBe('overworld');
  if ((await game(page)).dialogue) await page.keyboard.press('Escape');
  await idle(page);
  // ---- the rack
  await start(page, 'site_no_rack');
  await choose(page, 'A wall cabinet (12U)');
  await type(page, 'ML-1');
  expect(await apply(page)).toContain('Setting up Mill Lane');
  await idle(page);
  // ---- the first box: found by what is printed on it; its serial does not look like a Cisco serial
  await start(page, 'site_no_devices');
  await type(page, 'c1111-4p');
  await choose(page, 'Cisco ISR C1111-4P', 'device-search');
  await choose(page, 'Router');
  await fill(page, { name: 'ml-rtr1', position: '10', serial: '12345' });
  await expect.poll(async () => (await game(page)).dialogue?.text ?? '').toContain('Are you sure?');
  await typed(page);
  await page.keyboard.press('Enter');
  await expect.poll(async () => (await game(page)).dialogue?.text ?? '').toContain('does not look like a Cisco serial');
  await typed(page);
  await page.screenshot({ path: `${SHOTS}/serial-warning.png` });
  await choose(page, 'Yes: record it, marked unverified');
  await choose(page, 'No, that is all for now');
  await expect(page.locator('#quest-preview')).toContainText('Import the device type Cisco ISR C1111-4P from the device-type library');
  await expect(page.locator('#quest-preview')).toContainText('marked unverified: it does not look like the vendor');
  // the change was checked against this player's NetBox permissions: the button says what it will do
  await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'real');
  await expect(page.locator('#quest-apply')).toHaveText('Apply to NetBox');
  const kit = await apply(page, 'import-preview');
  // the model is the library's, but the box carries the mark: no bonus for it
  expect(kit).toContain('No bonus for a box marked unverified.');
  expect(kit).not.toContain('+20 XP');
  await idle(page);
  // ---- the site is still being set up: the next box is offered, and its model comes from the library too
  await start(page, 'site_more_devices');
  await choose(page, 'Yes: add it');
  await type(page, 'EX2300-24P switch');
  await choose(page, 'Juniper EX2300-24P');
  await choose(page, 'Switch');
  await fill(page, { name: 'ml-sw1', position: '9' });
  await choose(page, 'No, that is all for now');
  await expect(page.locator('#quest-preview')).toContainText('Import the device type Juniper EX2300-24P from the device-type library');
  expect(await apply(page)).toContain('1 box of a model the device-type library confirms: +20 XP.');
  await idle(page);
  // ---- whose line: the provider pack of the Netherlands, by name
  await start(page, 'site_no_provider');
  await choose(page, 'KPN', 'provider-choice');
  await expect(page.locator('#quest-preview')).toContainText('New provider KPN (AS1136), from common providers: Netherlands.');
  expect(await apply(page)).toContain('NetBox confirms it.');
  await idle(page);
  // ---- the catalogue: derived from NetBox, a tick where the library confirms the model
  await page.keyboard.press('m');
  await page.click('#menu-catalogue');
  await expect(page.locator('#catalogue-message')).toContainText('All 2 models in your NetBox are confirmed by the device-type library.');
  const row = page.locator('#game-catalogue tr[data-model="ISR C1111-4P"]');
  await expect(row).toHaveAttribute('data-status', 'verified');
  await expect(row.locator('.mark')).toHaveText('✔');
  await expect(row).toContainText('confirmed by the device-type library · 1 device');
  await expect(page.locator('#game-catalogue tr[data-status="unverified"], #game-catalogue tr[data-status="mismatch"]')).toHaveCount(0);
  await page.click('#catalogue-packs summary');
  await expect(page.locator('#catalogue-packs [data-pack="providers-nl"]')).toContainText('Common providers: Netherlands');
  await page.click('#catalogue-packs summary');
  await page.screenshot({ path: `${SHOTS}/catalogue.png` });
  await page.click('#catalogue-back');
  await expect(page.locator('#game-menu')).toBeVisible();
  // the next thing the game asks is what it has always asked: how the site reaches the outside
  await page.keyboard.press('Escape');
  const kinds = await page.evaluate(async () => {
    const s = window.__game!.shell;
    await s.quests!.refresh(true);
    return s.quests!.boards.netbox!.quests.filter((q) => q.status === 'offered').map((q) => q.kind);
  });
  expect(kinds[0]).toBe('site_no_way_out');
});

// A second NetBox user of the same made-up NetBox, who may add devices and device roles and nothing else
// (proxy/tests/empty_netbox.py). It plays on what the test above left: a site with a rack, two boxes and two models.
const BOX = `http://127.0.0.1:${process.env.RACKWALKER_SETUP_NETBOX_PORT ?? 8745}/api`;
const count = async (page: Page, path: string): Promise<number> => ((await (await page.request.get(`${BOX}/${path}/`)).json()) as { count: number }).count;

test('the button says what it will do: "Apply to NetBox" writes, "Practice run" writes nothing', async ({ page }) => {
  await boot(page);
  await page.fill('#signin-token', 'nbt_limited.not-a-secret-0123456789');
  await page.click('#signin-submit');
  await page.evaluate(() => window.__game!.ready);
  await page.evaluate(() => (window.__game!.shell.transitionMs = 0));
  await expect.poll(async () => (await game(page)).scene).toBe('overworld');
  if ((await game(page)).dialogue) await page.keyboard.press('Escape');
  await idle(page);
  expect([await count(page, 'dcim/devices'), await count(page, 'dcim/device-types')]).toEqual([2, 2]);
  // ---- a box of a model NetBox holds: this player may add devices, so this line is written, whatever else they may not do
  await start(page, 'site_more_devices');
  await choose(page, 'Yes: add it');
  await type(page, 'EX2300-24P');
  await choose(page, 'Juniper EX2300-24P (in NetBox');
  await choose(page, 'Switch');
  await fill(page, { name: 'ml-sw2', position: '8' });
  await choose(page, 'No, that is all for now');
  await expect(page.locator('#quest-preview')).toContainText('New device ml-sw2');
  await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'real');
  await expect(page.locator('#quest-mode')).toContainText('Apply writes this to NetBox');
  await expect(page.locator('#quest-apply')).toHaveText('Apply to NetBox');
  expect(await apply(page)).toContain('NetBox confirms it.');
  expect(await count(page, 'dcim/devices')).toBe(3);
  await idle(page);
  // ---- a box of a model NetBox lacks: importing it needs a permission this player does not hold. Said before the
  // button is pressed, the button is named for it, and pressing it writes nothing.
  await start(page, 'site_more_devices');
  await choose(page, 'Yes: add it');
  await type(page, 'MX204');
  await choose(page, 'Juniper MX204');
  await choose(page, 'Router');
  await fill(page, { name: 'ml-rtr2' });
  await choose(page, 'No, that is all for now');
  await expect(page.locator('#quest-preview')).toContainText('Import the device type Juniper MX204');
  await expect(page.locator('#quest-mode')).toHaveAttribute('data-mode', 'dry_run');
  await expect(page.locator('#quest-mode')).toContainText('Your NetBox user may not add dcim.devicetype');
  await expect(page.locator('#quest-mode')).toContainText('This is a practice run: no XP.');
  await expect(page.locator('#quest-apply')).toHaveText('Practice run');
  expect(await apply(page)).toContain('Practice run, nothing was written');
  expect([await count(page, 'dcim/devices'), await count(page, 'dcim/device-types')]).toEqual([3, 2]);
});
