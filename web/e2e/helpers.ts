import { expect, type Page } from '@playwright/test';

/** RACKWALKER_E2E_THEME=<id> runs the specs under that theme (?theme= on every open), as `make e2e-themes` does for the
 *  interaction and edit specs. Unset: the app default, which the golden screenshots expect. */
const E2E_THEME = process.env.RACKWALKER_E2E_THEME;

export async function open(page: Page, query: string): Promise<void> {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  // The app reads the live proxy by default; specs without a stubbed proxy (proxy-stub.ts) read the fixtures.
  if (!/(^|&)(source|perf)=/.test(query)) query = `source=fixture&${query}`;
  await page.goto(`/?${query}${E2E_THEME && !/(^|&)theme=/.test(query) ? `&theme=${E2E_THEME}` : ''}`);
  await page.waitForFunction(() => !!window.__iso);
  await page.evaluate(() => window.__iso!.ready);
  expect(errors).toEqual([]);
}

export const state = (page: Page) => page.evaluate(() => window.__iso!.state());
export const settled = (page: Page) => page.evaluate(() => window.__iso!.view.settled());

/** Canvas coordinates of an entity's visible pixel nearest its sprite centre (the canvas fills the viewport). */
export async function pointOf(page: Page, key: string): Promise<{ x: number; y: number }> {
  const p = await page.evaluate((k) => window.__iso!.screenOf(k), key);
  expect(p, `entity ${key} on screen`).not.toBeNull();
  return p!; // already a pixel centre
}

/**
 * Pixel-crispness check on the live WebGL canvas: with integer scale s and integer container offset, every
 * screen pixel must equal the top-left pixel of its s x s block. Returns the number of mismatching pixels.
 */
export async function blockMismatches(page: Page): Promise<{ scale: number; checked: number; bad: number }> {
  return page.evaluate(() => {
    const v = window.__iso!.view;
    const cam = v.camera!;
    v.app.render();
    const src = v.app.canvas;
    const c = document.createElement('canvas');
    c.width = src.width;
    c.height = src.height;
    const g = c.getContext('2d')!;
    g.drawImage(src, 0, 0);
    const img = g.getImageData(0, 0, c.width, c.height).data;
    const s = cam.scale;
    const ox = Math.round(c.width / 2 - cam.cx * s);
    const oy = Math.round(c.height / 2 - cam.cy * s);
    const mod = (a: number) => ((a % s) + s) % s;
    let bad = 0, checked = 0;
    for (let y = 0; y < c.height; y++)
      for (let x = 0; x < c.width; x++) {
        const bx = x - mod(x - ox), by = y - mod(y - oy);
        if (bx < 0 || by < 0) continue;
        checked++;
        const i = (y * c.width + x) * 4, j = (by * c.width + bx) * 4;
        if (img[i] !== img[j] || img[i + 1] !== img[j + 1] || img[i + 2] !== img[j + 2]) bad++;
      }
    return { scale: s, checked, bad };
  });
}

/** Canvas point of the centre of floor tile (tx, ty) at an iso level. */
export async function tilePoint(page: Page, tx: number, ty: number): Promise<{ x: number; y: number }> {
  const p = await page.evaluate(([x, y]) => window.__iso!.view.screenOfLevel((x! - y!) * 16, (x! + y! + 1) * 8), [tx, ty]);
  return p!;
}

/** Canvas point of a U slot's middle row at rack level (frame x 8..72; U_PX = 8). */
export async function uPoint(page: Page, rack: { u_height: number; starting_unit: number }, u: number): Promise<{ x: number; y: number }> {
  const y = (rack.starting_unit + rack.u_height - 1 - u) * 8 + 4;
  const p = await page.evaluate(([ly]) => window.__iso!.view.screenOfLevel(40, ly!), [y]);
  return p!;
}

/** Press on `from`, move in steps to `to`, release (an object drag). */
export async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }, release = true): Promise<void> {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 6, from.y + 6, { steps: 2 });
  await page.mouse.move(to.x, to.y, { steps: 6 });
  if (release) await page.mouse.up();
}

export const editor = (page: Page) => page.evaluate(() => window.__iso!.editor.state());
export const editorIdle = (page: Page) => page.evaluate(() => window.__iso!.editor.idle());

/**
 * Drag rack `id` by its sprite so that it lands on tile (tx, ty). The rack follows the grab point, so the release
 * point is the target tile's centre plus the offset from the rack's own tile centre to the press.
 */
export async function rackDrag(page: Page, id: number, tx: number, ty: number, release = true): Promise<{ x: number; y: number }> {
  const from = await pointOf(page, `rack:${id}`);
  const at = await page.evaluate((i) => {
    const it = window.__iso!.view.scene!.items.find((x) => x.entity?.kind === 'rack' && x.entity.data.id === i && x.sprite.kind === 'rack');
    const r = it!.entity!.data as { iso_x: number; iso_y: number };
    return [r.iso_x, r.iso_y] as const;
  }, id);
  const own = await tilePoint(page, at[0], at[1]);
  const to = await tilePoint(page, tx, ty);
  await drag(page, from, { x: to.x + from.x - own.x, y: to.y + from.y - own.y }, release);
  return from;
}
