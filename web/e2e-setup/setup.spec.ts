// The setup companion in the browser, on a NetBox with nothing in it (playwright.setup.config.ts): Ilatea offers the
// first site; the site, its rack, its first box from the device-type library and its carrier from the provider pack are
// each one previewed change; a serial that does not look like the vendor's is asked about; the catalogue lists what
// NetBox then holds. Screenshots go to docs/screenshots/setup/.
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { boot, game, typed } from '../e2e/game-helpers';

const expect = baseExpect.configure({ timeout: 20_000 });
const SHOTS = '../docs/screenshots/setup';

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

test('an empty NetBox is set up by playing: first site, rack, a box from the library, the carrier, the catalogue', async ({ page }) => {
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
  const kit = await apply(page, 'import-preview');
  expect(kit).toContain('1 box of a model the device-type library confirms: +20 XP.');
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
  await expect(page.locator('#catalogue-message')).toContainText('All 1 model in your NetBox is confirmed by the device-type library.');
  await expect(page.locator('#game-catalogue tr[data-status="verified"]')).toContainText('ISR C1111-4P');
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
