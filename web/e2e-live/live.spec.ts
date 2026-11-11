import { expect, test } from '@playwright/test';
import { open, pointOf, settled, state } from '../e2e/helpers';

// World -> site -> room -> rack -> device by double-click, every scene read from the live proxy (source=http).
// Screenshots go to docs/screenshots/live-<level>.png.
const SHOTS = '../docs/screenshots';

test('live proxy: world shows 3 sites; double-click into a site, a room, a rack, a device', async ({ page }) => {
  const api: { path: string; status: number; source: unknown; version: unknown; contract: unknown }[] = [];
  const fixtureLoads: string[] = [];
  page.on('request', (r) => {
    if (/\/fixtures\//.test(r.url())) fixtureLoads.push(r.url());
  });
  page.on('response', async (r) => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith('/api/')) return;
    const body = await r.json().catch(() => null);
    // /api/health (the editor reads the write mode) is a HealthResponse, not a scene: same facts, other places
    if (u.pathname === '/api/health')
      api.push({ path: u.pathname, status: r.status(), source: body?.netbox?.reachable ? 'netbox' : null, version: body?.netbox?.version, contract: body?.contract_version });
    else api.push({ path: u.pathname, status: r.status(), source: body?.meta?.source, version: body?.meta?.netbox_version, contract: body?.meta?.contract_version });
  });

  await open(page, 'source=http&view=world');
  await settled(page);
  expect(await state(page)).toMatchObject({ level: 'world', error: null });
  const vp = page.viewportSize()!;
  for (const id of [1, 2, 3]) {
    const p = await pointOf(page, `site:${id}`);
    expect(p.x >= 0 && p.x < vp.width && p.y >= 0 && p.y < vp.height, `site:${id} inside the viewport`).toBe(true);
  }
  await page.screenshot({ path: `${SHOTS}/live-world.png` });

  const dbl = async (key: string, level: string, file: string) => {
    const p = await pointOf(page, key);
    await page.mouse.dblclick(p.x, p.y);
    await settled(page);
    await expect.poll(async () => (await state(page)).level).toBe(level);
    expect((await state(page)).error).toBeNull();
    await page.screenshot({ path: `${SHOTS}/live-${file}.png` });
  };
  await dbl('site:1', 'site', 'site');
  await dbl('location:1', 'room', 'room');
  await dbl('rack:1', 'rack', 'rack');
  await dbl('device:2', 'device', 'device');
  await expect(page.locator('#hud-crumbs')).toHaveText('World › North Depot › Server Room › ND-R1 › nd-core1');

  const paths = api.map((a) => a.path);
  for (const p of ['/api/scene/world', '/api/scene/site/1', '/api/scene/rack/1', '/api/device/2']) expect(paths).toContain(p);
  // 'cache' is the proxy's own cache of NetBox reads (another spec in the same run may have warmed it); never 'fixture'
  for (const a of api)
    expect(a, `${a.path} answered by the proxy from NetBox`).toMatchObject({ status: 200, source: expect.stringMatching(/^(netbox|cache)$/), version: '4.7.4', contract: '2.5.0' });
  expect(fixtureLoads).toEqual([]);
  console.log(JSON.stringify(api));
});
