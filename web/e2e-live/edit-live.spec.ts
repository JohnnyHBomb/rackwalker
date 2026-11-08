import { expect, test } from '@playwright/test';
import { drag, editor, editorIdle, open, pointOf, settled, state, uPoint } from '../e2e/helpers';

// Live write round trip, against a WRITER's own dev NetBox stack, never the shared one:
//   proxy on that stack's rw token with WRITE_ENABLED=1, vite in front of it (RACKWALKER_LIVE_URL), and for the
//   NetBox-side checks NB_URL + NB_ADMIN_TOKEN (read only here: the device and its changelog).
// Drags nd-rtr1 (device 3, ND-R1 U36 on a fresh seed) to U20, applies, and checks NetBox, its changelog and the
// reloaded scene. Run: npx playwright test -c playwright.live.config.ts e2e-live/edit-live.spec.ts
// Screenshots: docs/screenshots/editor/live-*.png. Reset the stack afterwards (make netbox-reset).
const SHOTS = '../docs/screenshots/editor';
const NB = process.env.NB_URL;
const TOKEN = process.env.NB_ADMIN_TOKEN;
const DEVICE = 3;
const TO = 20;

// A write-enabled proxy writes only for a signed-in player: sign in as the admin user before each test (the editor
// then sends the session's CSRF header), so the changelog names that player.
test.beforeEach(async ({ page }) => {
  if (!TOKEN) return;
  const r = await page.request.post('/api/game/session', { data: { token: TOKEN }, headers: { 'X-RackWalker-CSRF': 'sign-in' } });
  expect(r.status(), await r.text()).toBe(200);
});

async function nb<T>(path: string): Promise<T> {
  const r = await fetch(`${NB}${path}`, { headers: { Authorization: `Bearer ${TOKEN}`, Accept: 'application/json' } });
  expect(r.status, path).toBe(200);
  return (await r.json()) as T;
}

test('live: drag a device to a new U, apply, see it in NetBox, its changelog and the reloaded scene', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const before = await nb<{ position: number; rack: { id: number; name: string }; last_updated: string }>(`/api/dcim/devices/${DEVICE}/`);
  expect(before.position, 'fresh seed: nd-rtr1 at U36 (make netbox-reset)').toBe(36);
  const rackId = before.rack.id;
  const rack = await nb<{ u_height: number; starting_unit: number }>(`/api/dcim/racks/${rackId}/`);

  const api: { path: string; status: number; source?: string }[] = [];
  page.on('response', async (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/')) api.push({ path: `${r.request().method()} ${u.pathname}`, status: r.status(), source: (await r.json().catch(() => null))?.meta?.source });
  });
  await open(page, `source=http&anim=0&view=rack:${rackId}`);
  await settled(page);
  await expect(page.locator('#basket-mode')).toHaveText('WRITE');

  await drag(page, await pointOf(page, `device:${DEVICE}`), await uPoint(page, rack, TO), false);
  await expect.poll(async () => (await editor(page)).ghost?.verdict, { timeout: 15_000 }).toBe('ok'); // the real proxy's preview
  await page.screenshot({ path: `${SHOTS}/live-1-drag.png` });
  await page.mouse.up();
  await editorIdle(page);
  expect((await editor(page)).ops).toEqual([{ label: `nd-rtr1 → ${before.rack.name} U${TO}`, status: 'ok' }]);
  await expect(page.locator('#basket-ops .op-diff')).toHaveText([`position: 36 → ${TO}`]);
  await page.screenshot({ path: `${SHOTS}/live-2-staged.png` });

  const t0 = new Date();
  await page.fill('#basket-reason', 'live round trip: nd-rtr1 to U20');
  await page.click('#basket-apply');
  await expect(page.locator('#basket-msg')).toHaveText('Applied 1 change to NetBox (see its changelog).', { timeout: 30_000 });
  await editorIdle(page);
  expect((await editor(page)).ops).toEqual([]);
  expect(api.some((a) => a.path === 'POST /api/changes/apply' && a.status === 200)).toBe(true);
  // the scene was read again from NetBox (not the proxy cache, which every apply clears)
  const reload = api.filter((a) => a.path === `GET /api/scene/rack/${rackId}`).at(-1);
  expect(reload?.source).toBe('netbox');
  const placed = await page.evaluate((id) => window.__iso!.view.scene!.items.find((i) => i.entity?.kind === 'device' && i.entity.data.id === id)!, DEVICE);
  expect((placed.entity!.data as { position: number }).position).toBe(TO);
  expect(placed.y).toBe((rack.starting_unit + rack.u_height - (TO + 1)) * 8); // drawn at U20
  await page.screenshot({ path: `${SHOTS}/live-3-applied.png` });

  // NetBox: the device and the changelog entry, written by the proxy's own user with the app's message
  const after = await nb<{ position: number }>(`/api/dcim/devices/${DEVICE}/`);
  expect(after.position).toBe(TO);
  // position is a decimal field: the changelog keeps it as a string ("20.0")
  const log = await nb<{ results: { action: { value: string }; user_name: string; message: string; time: string; prechange_data: { position: string }; postchange_data: { position: string } }[] }>(
    `/api/core/object-changes/?changed_object_type=dcim.device&changed_object_id=${DEVICE}&ordering=-time&limit=1`,
  );
  const e = log.results[0]!;
  expect(e).toMatchObject({ action: { value: 'update' }, user_name: 'rackwalker-proxy', message: 'rackwalker (admin): live round trip: nd-rtr1 to U20' });
  expect([Number(e.prechange_data.position), Number(e.postchange_data.position)]).toEqual([36, TO]);
  expect(new Date(e.time).getTime()).toBeGreaterThan(t0.getTime() - 60_000);
  console.log(`changelog: ${e.time} ${e.user_name} ${e.action.value} dcim.device ${DEVICE} position ${e.prechange_data.position} -> ${e.postchange_data.position} "${e.message}"`);
  expect((await state(page)).error).toBeNull();
});

// Regression: a duplicate asset_tag used to pass the preview and fail on write after op 1 was written.
// Since contract 1.1.3 the preview reads the tag's holders: op 2 is refused before anything is written.
test('live: two devices given one new asset tag: the preview refuses op 2, Apply stays off, NetBox untouched', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const tag = `DUP-${Date.now()}`;
  await open(page, 'source=http&anim=0&view=device:2');
  await settled(page);
  await expect(page.locator('#basket-mode')).toHaveText('WRITE');
  const setTag = async (id: number) => {
    await page.evaluate((d) => window.__iso!.view.goto({ level: 'device', deviceId: d }), id);
    await settled(page);
    const p = await pointOf(page, `device:${id}`);
    await page.mouse.click(p.x, p.y);
    const input = page.locator('#info-panel tr[data-field=asset_tag] input');
    await expect(input).toBeVisible({ timeout: 15_000 });
    await input.fill(tag);
    await input.press('Enter');
    await editorIdle(page);
  };
  await setTag(2);
  await setTag(3);
  expect((await editor(page)).ops.map((o) => o.status)).toEqual(['ok', 'error']);
  await expect(page.locator('#basket-ops li .op-error')).toHaveText([`Device nd-core1 already has asset tag '${tag}' (NetBox: an asset tag is unique across all devices).`]);
  await expect(page.locator('#basket-apply')).toBeDisabled();
  await page.screenshot({ path: `${SHOTS}/live-4-asset-tag-refused.png` });
  const d2 = await nb<{ asset_tag: string | null }>('/api/dcim/devices/2/');
  const d3 = await nb<{ asset_tag: string | null }>('/api/dcim/devices/3/');
  expect([d2.asset_tag, d3.asset_tag]).not.toContain(tag);
});

test('live: the device panel shows the staged serial, then the serial NetBox has after Apply', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const serial = `SYN-LIVE-${Date.now() % 100000}`;
  await open(page, 'source=http&anim=0&view=rack:1');
  await settled(page);
  const d = await pointOf(page, 'device:2');
  await page.mouse.click(d.x, d.y);
  const input = page.locator('#info-panel tr[data-field=serial] input');
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill(serial);
  await input.press('Enter');
  await editorIdle(page);
  await expect(input).toHaveValue(serial);
  await page.click('#basket-apply');
  await expect(page.locator('#basket-msg')).toHaveText('Applied 1 change to NetBox (see its changelog).', { timeout: 30_000 });
  await editorIdle(page);
  expect((await nb<{ serial: string }>('/api/dcim/devices/2/')).serial).toBe(serial);
  await expect(input).toHaveValue(serial);
});

test('live: a lock conflict: Reload from NetBox, stage again on the new data, Apply', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const dev = (await nb<{ results: { id: number; name: string }[] }>('/api/dcim/devices/?name=nd-srv05')).results[0]!;
  await open(page, `source=http&anim=0&view=device:${dev.id}`);
  await settled(page);
  const stageDescription = async (text: string) => {
    const p = await pointOf(page, `device:${dev.id}`);
    await page.mouse.click(p.x, p.y);
    const input = page.locator('#info-panel tr[data-field=description] input');
    await expect(input).toBeVisible({ timeout: 15_000 });
    await input.fill(text);
    await input.press('Enter');
    await editorIdle(page);
  };
  const run = Date.now() % 100000;
  await stageDescription(`dup conflict ${run} a`);
  // someone else changes the device in NetBox now
  const r = await fetch(`${NB}/api/dcim/devices/${dev.id}/`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ serial: `OTHER-${Date.now() % 100000}` }),
  });
  expect(r.status).toBe(200);
  await page.click('#basket-apply');
  await expect(page.locator('#basket-msg')).toHaveText(
    'Nothing was written: op 1 was staged on data that changed in NetBox since you loaded it. Reload from NetBox, then stage it again.',
    { timeout: 30_000 },
  );
  await expect(page.locator('#basket-ops li .op-error')).toHaveText([`Device ${dev.name} was changed in NetBox after you loaded it: reload, then stage this change again.`]);
  await page.screenshot({ path: `${SHOTS}/live-5-conflict.png` });
  await page.click('#basket-reload');
  await editorIdle(page);
  expect((await editor(page)).ops).toEqual([]);
  await stageDescription(`dup conflict ${run} b`);
  expect((await editor(page)).ops.map((o) => o.status)).toEqual(['ok']);
  await page.click('#basket-apply');
  await expect(page.locator('#basket-msg')).toHaveText('Applied 1 change to NetBox (see its changelog).', { timeout: 30_000 });
  expect((await nb<{ description: string }>(`/api/dcim/devices/${dev.id}/`)).description).toBe(`dup conflict ${run} b`);
});

test('live: a drag asks the proxy about the slot it rests on; the verdict comes within one preview', async ({ page }) => {
  test.skip(!NB || !TOKEN, 'needs NB_URL and NB_ADMIN_TOKEN of the writer stack');
  const dev = (await nb<{ results: { id: number; position: number; rack: { id: number } }[] }>('/api/dcim/devices/?name=nd-srv05')).results[0]!;
  const rack = await nb<{ u_height: number; starting_unit: number }>(`/api/dcim/racks/${dev.rack.id}/`);
  const previews: { sent: number; done?: number }[] = [];
  page.on('request', (q) => {
    if (q.url().endsWith('/api/changes/preview')) previews.push({ sent: Date.now() });
  });
  page.on('requestfinished', (q) => {
    if (q.url().endsWith('/api/changes/preview')) previews.at(-1)!.done = Date.now();
  });
  await open(page, `source=http&anim=0&view=rack:${dev.rack.id}`);
  await settled(page);
  await page.waitForLoadState('networkidle'); // the tray reads the room's other racks first (about 2 s on a remote dev stack)
  await drag(page, await pointOf(page, `device:${dev.id}`), await uPoint(page, rack, 30), false); // across ~16 U
  const stopped = Date.now();
  await expect.poll(async () => (await editor(page)).ghost?.verdict, { timeout: 15_000, intervals: [50] }).toBe('ok');
  const ms = Date.now() - stopped;
  const one = previews[0]!.done! - previews[0]!.sent;
  console.log(`drag: ${previews.length} preview(s), verdict ${ms} ms after the pointer stopped (the preview itself ${one} ms)`);
  expect(previews.length).toBe(1); // was one per U crossed (7 for a short drag) and the verdict came 5 s late
  expect(ms).toBeLessThan(200 + one + 500); // the debounce, the one preview, a little slack: nothing queued before it
  await page.keyboard.press('Escape');
});

test('live: the page without source= reads the proxy (what it shows is what an edit writes)', async ({ page }) => {
  const paths: string[] = [];
  page.on('request', (q) => paths.push(new URL(q.url()).pathname));
  await page.goto('/?anim=0&view=room:1:1');
  await page.waitForFunction(() => !!window.__iso);
  await page.evaluate(() => window.__iso!.ready);
  expect(paths).toContain('/api/scene/site/1');
  await expect(page.locator('#basket-mode')).not.toHaveText('READ-ONLY');
});
