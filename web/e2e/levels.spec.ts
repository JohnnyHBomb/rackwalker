import { expect, test } from '@playwright/test';
import { blockMismatches, open, state } from './helpers';

// Golden screenshots, one per zoom level, from fixtures with the debug theme (pinned with ?theme=debug: the fixture
// sites carry their own iso_theme, and theme art must not move the engine goldens). The HUD (DOM text,
// font-dependent) is hidden for the image and checked as text instead.
const LEVELS = [
  { view: 'world', level: 'world', crumbs: ['World'] },
  { view: 'site:1', level: 'site', crumbs: ['World', 'North Depot'] },
  { view: 'room:1:1', level: 'room', crumbs: ['World', 'North Depot', 'Server Room'] },
  { view: 'rack:1', level: 'rack', crumbs: ['World', 'North Depot', 'Server Room', 'ND-R1'] },
  { view: 'device:2', level: 'device', crumbs: ['World', 'North Depot', 'Server Room', 'ND-R1', 'nd-core1'] },
] as const;

for (const l of LEVELS) {
  test(`golden: ${l.level}`, async ({ page }) => {
    await open(page, `anim=0&theme=debug&view=${l.view}`);
    expect(await state(page)).toMatchObject({ level: l.level, crumbs: [...l.crumbs], error: null });
    await expect(page.locator('#hud-level')).toHaveText(l.level);
    await expect(page.locator('#hud-crumbs')).toHaveText(l.crumbs.join(' › '));
    await page.addStyleTag({ content: '#hud,#editor,#tray,#drag-status{display:none !important}' });
    await expect(page.locator('#hud')).toBeHidden();
    await expect(page.locator('canvas')).toHaveScreenshot(`${l.level}.png`);
  });
}

// A second site whose only rack is turned (iso_facing E): checks the rotated rack sprite and its placement.
test('golden: room level of a second site with a rotated rack (East Exchange, Hall B)', async ({ page }) => {
  await open(page, 'anim=0&theme=debug&view=room:3:7');
  expect(await state(page)).toMatchObject({ level: 'room', crumbs: ['World', 'East Exchange', 'Hall B'], error: null });
  await page.addStyleTag({ content: '#hud,#editor,#tray,#drag-status{display:none !important}' });
  await expect(page.locator('canvas')).toHaveScreenshot('room-hall-b.png');
  const r = await blockMismatches(page);
  expect(r.bad, `hall B at ${r.scale}x`).toBe(0);
});

test('every level renders crisp integer-scaled pixels (each s x s block is one colour)', async ({ page }) => {
  for (const l of LEVELS) {
    await open(page, `anim=0&view=${l.view}`);
    for (let k = 0; k < 2; k++) {
      const r = await blockMismatches(page);
      expect(r.bad, `${l.level} at ${r.scale}x`).toBe(0);
      expect(r.checked).toBeGreaterThan(400_000);
      await page.evaluate(() => window.__iso!.view.stepScale(1));
    }
  }
});
