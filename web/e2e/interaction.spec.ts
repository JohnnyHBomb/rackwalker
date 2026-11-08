import { expect, test } from '@playwright/test';
import { open, pointOf, settled, state } from './helpers';

test('double-click down world -> site -> room -> rack -> device, back up with the HUD, Esc and the breadcrumb', async ({ page }) => {
  await open(page, 'view=world'); // animated transitions on
  const dbl = async (key: string, level: string) => {
    const p = await pointOf(page, key);
    // Sample the level containers every frame through the transition: two levels cross-fade, every scale and
    // every container offset stays an integer.
    const sampling = page.evaluate(
      () =>
        new Promise<{ n: number; scale: number; x: number; y: number; alpha: number }[][]>((resolve) => {
          const out: { n: number; scale: number; x: number; y: number; alpha: number }[][] = [];
          const layer = window.__iso!.view.app.stage.children[0]!;
          const t0 = performance.now();
          const tick = () => {
            out.push(layer.children.map((c) => ({ n: layer.children.length, scale: c.scale.x, x: c.position.x, y: c.position.y, alpha: c.alpha })));
            if (performance.now() - t0 < 700) requestAnimationFrame(tick);
            else resolve(out);
          };
          requestAnimationFrame(tick);
        }),
    );
    await page.mouse.dblclick(p.x, p.y);
    const frames = await sampling;
    await settled(page);
    expect((await state(page)).level).toBe(level);
    const all = frames.flat();
    for (const c of all) {
      expect([1, 2, 3, 4, 5, 6, 8]).toContain(c.scale);
      expect(Number.isInteger(c.x) && Number.isInteger(c.y)).toBe(true);
    }
    expect(frames.some((f) => f.length === 2 && f.every((c) => c.alpha > 0 && c.alpha < 1)), 'a cross-fade frame').toBe(true);
    expect(new Set(all.map((c) => c.scale)).size, 'scale steps during the flight').toBeGreaterThan(1);
  };
  await dbl('site:1', 'site');
  await dbl('location:1', 'room');
  // single click selects and names the entity in the HUD
  const r1 = await pointOf(page, 'rack:1');
  await page.mouse.click(r1.x, r1.y);
  expect((await state(page)).selected).toBe('rack:1');
  await expect(page.locator('#hud-selected')).toHaveText('selected: ND-R1');
  await dbl('rack:1', 'rack');
  await dbl('device:2', 'device');
  await expect(page.locator('#hud-crumbs')).toHaveText('World › North Depot › Server Room › ND-R1 › nd-core1');
  const port = await pointOf(page, 'port:interface:2');
  await page.mouse.click(port.x, port.y);
  await expect(page.locator('#hud-selected')).toHaveText('selected: Te1/0/1');

  await page.click('#hud-back');
  await settled(page);
  expect((await state(page)).level).toBe('rack');
  await page.keyboard.press('Escape');
  await settled(page);
  expect((await state(page)).level).toBe('room');
  await page.locator('#hud-crumbs a', { hasText: 'World' }).click();
  await settled(page);
  expect(await state(page)).toMatchObject({ level: 'world', crumbs: ['World'], error: null });
  await expect(page.locator('#hud-back')).toBeDisabled();
});

test('the selected object can be entered with the HUD button; zooming out centres on the object we left', async ({ page }) => {
  await open(page, 'anim=0&view=site:1');
  const p = await pointOf(page, 'location:2');
  await page.mouse.click(p.x, p.y);
  await page.click('#hud-enter');
  await settled(page);
  expect(await state(page)).toMatchObject({ level: 'room', crumbs: ['World', 'North Depot', 'Comms Cupboard'] });
  await page.click('#hud-back');
  await settled(page);
  // the Comms Cupboard is now at the screen centre
  const c = await pointOf(page, 'location:2');
  const vp = page.viewportSize()!;
  expect(Math.abs(c.x - vp.width / 2)).toBeLessThan(80);
  expect(Math.abs(c.y - vp.height / 2)).toBeLessThan(60);
});

test('wheel zoom steps integer scales around the pointer; drag pans; buttons zoom', async ({ page }) => {
  await open(page, 'anim=0&view=room:1:1');
  const s0 = (await state(page)).scale!;
  const r = await pointOf(page, 'rack:2');
  await page.mouse.move(r.x, r.y);
  await page.mouse.wheel(0, -100);
  const s1 = (await state(page)).scale!;
  expect(s1).toBeGreaterThan(s0);
  expect([1, 2, 3, 4, 5, 6, 8]).toContain(s1);
  // the rack is still under the pointer
  expect(await page.evaluate(([x, y]) => window.__iso!.view.pickScreen(x!, y!)?.entity.kind, [r.x, r.y])).toBe('rack');
  await page.mouse.wheel(0, 100);
  expect((await state(page)).scale).toBe(s0);

  const cam0 = await page.evaluate(() => window.__iso!.view.camera!);
  // A drag that starts on a rack moves the rack (editor); a drag from anywhere else pans.
  expect(await page.evaluate(() => window.__iso!.view.pickScreen(60, 500)?.entity.kind ?? null)).not.toBe('rack');
  await page.mouse.move(60, 500);
  await page.mouse.down();
  await page.mouse.move(90, 520, { steps: 5 });
  await page.mouse.up();
  const cam1 = await page.evaluate(() => window.__iso!.view.camera!);
  expect(cam1.cx).toBeCloseTo(cam0.cx - 30 / cam0.scale, 5);
  expect(cam1.cy).toBeCloseTo(cam0.cy - 20 / cam0.scale, 5);
  expect((await state(page)).selected).toBeNull(); // a drag is not a click

  await page.click('#hud-zoom-in');
  expect((await state(page)).scale).toBeGreaterThan(s0);
  await page.click('#hud-zoom-out');
  expect((await state(page)).scale).toBe(s0);
});

test('pinch (two touch pointers) zooms by integer steps', async ({ page }) => {
  await open(page, 'anim=0&view=room:1:1');
  const s0 = (await state(page)).scale!;
  await page.evaluate(() => {
    const c = window.__iso!.view.app.canvas;
    const ev = (type: string, id: number, x: number, y: number) =>
      c.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', clientX: x, clientY: y, bubbles: true, isPrimary: id === 1 }));
    ev('pointerdown', 1, 380, 300);
    ev('pointerdown', 2, 420, 300);
    for (let d = 20; d <= 40; d += 2) {
      ev('pointermove', 1, 400 - d, 300);
      ev('pointermove', 2, 400 + d, 300);
    }
    ev('pointerup', 1, 360, 300);
    ev('pointerup', 2, 440, 300);
  });
  const s1 = (await state(page)).scale!;
  expect(s1).toBeGreaterThan(s0);
  expect([1, 2, 3, 4, 5, 6, 8]).toContain(s1);
});

test('a level without data shows the error in the HUD and keeps the current level', async ({ page }) => {
  await open(page, 'anim=0&view=rack:1');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const p = await pointOf(page, 'device:1'); // only device-2 has a fixture
  await page.mouse.dblclick(p.x, p.y);
  await settled(page);
  expect((await state(page)).level).toBe('rack');
  await expect(page.locator('#hud-error')).toContainText('device:1');
  // the same through the HUD's enter button: still no uncaught rejection
  await page.mouse.click(p.x, p.y);
  await page.click('#hud-enter');
  await settled(page);
  expect((await state(page)).level).toBe('rack');
  expect(errors).toEqual([]);
  // a working navigation clears the error
  const ok = await pointOf(page, 'device:2');
  await page.mouse.dblclick(ok.x, ok.y);
  await settled(page);
  expect(await state(page)).toMatchObject({ level: 'device', error: null });
});
