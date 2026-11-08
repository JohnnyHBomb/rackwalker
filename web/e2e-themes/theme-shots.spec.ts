import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { THEME_IDS } from '../src/contracts/theme';
import { open, state } from '../e2e/helpers';

// RACKWALKER_THEME=<id>|all (make theme-shots THEME=...). ?theme= overrides the theme on every level, so a site whose
// iso_theme is another theme still shows this one.
const want = process.env.RACKWALKER_THEME ?? '';
const ids = want === 'all' ? [...THEME_IDS] : THEME_IDS.filter((t) => t === want);
if (ids.length === 0) throw new Error(`RACKWALKER_THEME must be one of ${THEME_IDS.join(', ')} or all (got "${want}")`);

const OUT = resolve(fileURLToPath(new URL('.', import.meta.url)), '../../docs/screenshots/themes');
const LEVELS = [
  { view: 'world', file: 'world', level: 'world' },
  { view: 'site:1', file: 'site', level: 'site' },
  { view: 'room:1:1', file: 'room', level: 'room' },
  { view: 'room:3:7', file: 'room-rotated-rack', level: 'room' },
  { view: 'rack:1', file: 'rack', level: 'rack' },
  { view: 'device:2', file: 'device', level: 'device' },
] as const;

for (const id of ids) {
  test(`theme ${id}: every zoom level and the slot sheet`, async ({ page }) => {
    const dir = resolve(OUT, id);
    mkdirSync(dir, { recursive: true });
    for (const l of LEVELS) {
      await open(page, `anim=0&view=${l.view}&theme=${id}`);
      expect(await state(page)).toMatchObject({ level: l.level, error: null, theme: id });
      await page.addStyleTag({ content: '#hud{display:none !important}' });
      await page.locator('canvas').screenshot({ path: resolve(dir, `${l.file}.png`) });
    }
    // The world level once more with the HUD and the open theme picker (how a viewer meets the theme).
    await open(page, `anim=0&view=world&theme=${id}`);
    await page.locator('#theme-button').click();
    await expect(page.locator('#theme-menu [role=option]')).toHaveCount(THEME_IDS.length + 1);
    await page.screenshot({ path: resolve(dir, 'picker.png') });

    await page.goto(`/sheet.html?theme=${id}`);
    await page.waitForFunction(() => !!window.__sheet);
    const sheet = await page.evaluate(() => ({ theme: window.__sheet!.theme, png: window.__sheet!.png() }));
    expect(sheet.theme).toBe(id);
    writeFileSync(resolve(dir, 'sheet.png'), Buffer.from(sheet.png.replace(/^data:image\/png;base64,/, ''), 'base64'));
  });
}
