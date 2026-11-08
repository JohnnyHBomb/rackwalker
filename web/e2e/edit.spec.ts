// Editor interactions against the fixture source, with the proxy's change routes stubbed (proxy-stub.ts). Every
// editor interaction has a test here. Screenshots go to docs/screenshots/editor/ only
// with RACKWALKER_DOCS_SHOTS=1 (they are font-dependent, not goldens; a plain e2e run left them rewritten in the tree).
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { drag, editor, editorIdle, open, pointOf, rackDrag, settled, state, tilePoint, uPoint } from './helpers';
import { stubProxy, type Stub } from './proxy-stub';

const SHOTS = '../docs/screenshots/editor';
const docShot = (page: Page, name: string) =>
  process.env.RACKWALKER_DOCS_SHOTS === '1' ? page.screenshot({ path: `${SHOTS}/${name}` }) : Promise.resolve(Buffer.alloc(0));
const RACK = { u_height: 42, starting_unit: 1 }; // ND-R1 (fixtures/rack-1.json)
const ops = async (page: Page) => (await editor(page)).ops;
const sentOps = (stub: Stub) => stub.sent.map((s) => s.body.ops);
/** The tint of the drag ghost sprite (0xrrggbb), or null when no ghost is drawn. */
const ghostTint = (page: Page) =>
  page.evaluate(() => {
    const g = window.__iso!.view.app.stage.children[0]!.children.at(-1)!.children.find((c) => c.label === 'ghost')!;
    return g.children.length ? (g.children[0] as unknown as { tint: number }).tint : null;
  });
/** Tints of the children of a level layer ('marks', 'outlines'). */
const layerTints = (page: Page, label: string) =>
  page.evaluate((l) => window.__iso!.view.app.stage.children[0]!.children.at(-1)!.children.find((c) => c.label === l)!.children.map((c) => (c as unknown as { tint: number }).tint), label);
/** A palette colour of the theme on screen as 0xrrggbb. */
const paletteHex = (page: Page, key: string) => page.evaluate((k) => parseInt((window.__iso!.view.theme.palette as unknown as Record<string, string>)[k]!.slice(1), 16), key);
/** RGBA pixels of the canvas in a (2w x 2h) box around a point, after a render. */
const canvasPixels = (page: Page, at: { x: number; y: number }, w: number, h: number) =>
  page.evaluate(
    ([x, y, w, h]) => {
      const v = window.__iso!.view;
      v.app.render();
      const c = document.createElement('canvas');
      c.width = v.app.canvas.width;
      c.height = v.app.canvas.height;
      const g = c.getContext('2d')!;
      g.drawImage(v.app.canvas, 0, 0);
      return [...g.getImageData(x! - w!, y! - h!, 2 * w!, 2 * h!).data];
    },
    [at.x, at.y, w, h],
  );
const markCount = (page: Page) =>
  page.evaluate(() => window.__iso!.view.app.stage.children[0]!.children.at(-1)!.children.find((c) => c.label === 'marks')!.children.length);

test.describe('room level', () => {
  test('drag a rack to a free tile: ghost confirmed by /preview, op staged with its diff, rack drawn there', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const from = await rackDrag(page, 2, 3, 4, false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
    await expect(page.locator('#drag-status')).toHaveText('ND-R2 → tile 3,4 (proxy: ok)');
    // the candidate preview carried the op with its optimistic lock
    expect(sentOps(stub).at(-1)).toEqual([
      { op: 'move_rack', target: { kind: 'rack', id: 2 }, fields: { iso_x: 3, iso_y: 4 }, if_unmodified_since: expect.any(String) },
    ]);
    await docShot(page, '01-rack-drag-ghost.png');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([{ label: 'ND-R2 → tile 3,4', status: 'ok' }]);
    await expect(page.locator('#basket-ops li .op-diff')).toHaveText(['iso y: 2 → 4']);
    await expect(page.locator('#drag-status')).toBeHidden();
    // the staged rack is drawn on its new tile
    expect((await pointOf(page, 'rack:2')).y).toBeGreaterThan(from.y + 20);
    await docShot(page, '02-rack-staged.png');
  });

  test('a taken tile is refused locally (red ghost, no preview call, nothing staged)', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 5, 2, false);
    expect((await editor(page)).ghost).toEqual({ text: 'ND-R2 → tile 5,2', verdict: 'error', problem: 'tile taken by ND-R3' });
    expect(await ghostTint(page)).toBe(await paletteHex(page, 'led_err')); // red ghost (previously: only the text was checked)
    await docShot(page, '03-rack-drag-taken.png');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await expect(page.locator('#basket-msg')).toHaveText('Not staged: tile taken by ND-R3');
    expect(stub.sent.filter((s) => s.body.ops.some((o) => o.op === 'move_rack' && o.fields.iso_x === 5 && o.fields.iso_y === 2))).toEqual([]);
  });

  test('a drop the proxy refuses is not staged, and the ghost says why', async ({ page }) => {
    await stubProxy(page, { refuse: (op) => (op.op === 'move_rack' ? 'Rack ND-R2 was changed in NetBox after you loaded it.' : null) });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4, false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('error');
    await expect(page.locator('#drag-status')).toHaveText('ND-R2 → tile 3,4: Rack ND-R2 was changed in NetBox after you loaded it.');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await expect(page.locator('#basket-msg')).toContainText('the proxy says Rack ND-R2 was changed');
  });

  test('Esc cancels a drag in progress', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4, false);
    await page.keyboard.press('Escape');
    expect((await editor(page)).ghost).toBeNull();
    await page.mouse.move(200, 500, { steps: 3 });
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    expect((await state(page)).level).toBe('room'); // Esc did not also zoom out
  });

  test('rotate a rack (R key and the panel button), add a rack on a free tile with the tool', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const r1 = await pointOf(page, 'rack:1');
    await page.mouse.click(r1.x, r1.y);
    await page.keyboard.press('r');
    await editorIdle(page);
    await page.click('#act-rotate');
    await editorIdle(page);
    expect(await ops(page)).toEqual([
      { label: 'ND-R1 faces W', status: 'ok' },
      { label: 'ND-R1 faces N', status: 'ok' },
    ]);
    await expect(page.locator('#basket-ops li').nth(1).locator('.op-diff')).toHaveText(['iso facing: S → N']);
    await page.keyboard.press('Escape'); // closes the panel
    await expect(page.locator('#info-panel')).toBeHidden();
    await page.click('#tool-add-rack');
    const t = await tilePoint(page, 8, 4);
    await page.mouse.move(t.x, t.y, { steps: 3 });
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
    await docShot(page, '04-add-rack-ghost.png');
    await page.mouse.click(t.x, t.y);
    await editorIdle(page);
    const st = await editor(page);
    expect(st.tool).toBeNull();
    expect(st.ops.at(-1)).toEqual({ label: 'new rack NEW-1 at tile 8,4', status: 'ok' });
    // drawn as a rack with a temporary id, on that tile
    expect(await page.evaluate(() => window.__iso!.view.scene!.items.some((i) => i.entity?.kind === 'rack' && i.entity.data.id === -1 && i.tile?.x === 8 && i.tile?.y === 4))).toBe(true);
    await docShot(page, '05-rotated-and-new-rack.png');
  });

  test('cables are drawn on the floor between racks; clicking one selects it and opens its panel', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const wires = await page.evaluate(() => [...new Set(window.__iso!.view.scene!.items.filter((i) => i.entity?.kind === 'cable').map((i) => i.entity!.data.id))]);
    expect(wires.length).toBeGreaterThanOrEqual(3); // ND-R1..R4 cross-connects in the Server Room
    const p = await pointOf(page, `cable:${wires[0]}`);
    await page.mouse.click(p.x, p.y);
    expect((await state(page)).selected).toBe(`cable:${wires[0]}`);
    await expect(page.locator('#info-panel')).toContainText('cable');
    await expect(page.locator('#info-panel tr[data-field=a_end]')).toContainText('nd-');
    await docShot(page, '06-cable-selected.png');
  });
});

test.describe('rack level', () => {
  test('drag a device to a free U; an occupied U is refused', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=rack:1');
    const from = await pointOf(page, 'device:3'); // nd-rtr1, U36
    await drag(page, from, await uPoint(page, RACK, 20), false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
    await expect(page.locator('#drag-status')).toHaveText('nd-rtr1 → ND-R1 U20 (proxy: ok)');
    await docShot(page, '07-device-drag-ghost.png');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([{ label: 'nd-rtr1 → ND-R1 U20', status: 'ok' }]);
    await expect(page.locator('#basket-ops li .op-diff')).toHaveText(['position: 36 → 20']);
    expect((await pointOf(page, 'device:3')).y).toBeGreaterThan(from.y + 100);
    // onto nd-core1 (U38-39): red, not staged
    await drag(page, await pointOf(page, 'device:4'), await uPoint(page, RACK, 38), false);
    expect((await editor(page)).ghost).toMatchObject({ verdict: 'error', problem: 'U38 overlaps nd-core1' });
    await docShot(page, '08-device-drag-occupied.png');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toHaveLength(1);
  });

  test('drag a device to the unracked tray (unplace), to another rack (place_device), and from the tray into a slot', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=rack:1');
    await expect(page.locator('#tray .tray-device')).toHaveText(['nd-srv90 2U', 'nd-srv91 2U']);
    // rack -> tray
    const tray = await page.locator('#tray-unracked').boundingBox();
    await drag(page, await pointOf(page, 'device:4'), { x: tray!.x + 20, y: tray!.y + 10 }, false);
    await expect(page.locator('#drag-status')).toContainText('nd-fw1 → unracked tray');
    await page.mouse.up();
    await editorIdle(page);
    // rack -> another rack (first free U, its own U36 when free there)
    await expect(page.locator('#tray .tray-rack')).toHaveText(['ND-R2', 'ND-R3', 'ND-R4']);
    const r2 = await page.locator('#tray .tray-rack', { hasText: 'ND-R2' }).boundingBox();
    await drag(page, await pointOf(page, 'device:3'), { x: r2!.x + 10, y: r2!.y + 6 });
    await editorIdle(page);
    // tray -> slot (pointer on U21's row, 2U device: bottom U20)
    const chip = await page.locator('#tray .tray-device', { hasText: 'nd-srv90' }).boundingBox();
    await drag(page, { x: chip!.x + 10, y: chip!.y + 6 }, await uPoint(page, RACK, 21), false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
    await docShot(page, '09-tray-to-slot-ghost.png');
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([
      { label: 'nd-fw1 → unracked tray', status: 'ok' },
      { label: 'nd-rtr1 → ND-R2 U36', status: 'ok' },
      { label: 'nd-srv90 → ND-R1 U20', status: 'ok' },
    ]);
    expect(sentOps(stub).at(-1)!.slice(1).map((o) => o.fields)).toEqual([
      { rack_id: 2, position: 36, face: 'front' },
      { rack_id: 1, position: 20, face: 'front' },
    ]);
    // staged view: nd-fw1 is in the tray, nd-srv90 is not; nd-srv90 is drawn in ND-R1, nd-rtr1 and nd-fw1 are not
    await expect(page.locator('#tray .tray-device')).toHaveText(['nd-fw1 1U', 'nd-srv91 2U']);
    const ids = await page.evaluate(() => window.__iso!.view.scene!.items.filter((i) => i.entity?.kind === 'device').map((i) => i.entity!.data.id));
    expect(ids).toContain(27);
    expect(ids).not.toContain(3);
    expect(ids).not.toContain(4);
    // the diff names the racks (previously: "rack_id: 1 → 2")
    await expect(page.locator('#basket-ops li').nth(1).locator('.op-diff')).toHaveText(['rack: ND-R1 → ND-R2']);
    await docShot(page, '10-tray-and-between-racks.png');
  });

  test('Delete stages unplace for the selected device; Ctrl+Z undoes the last op; undo per op; Discard all', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=rack:1');
    for (const id of [3, 4]) {
      const p = await pointOf(page, `device:${id}`);
      await page.mouse.click(p.x, p.y);
      await page.keyboard.press('Delete');
      await editorIdle(page);
    }
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-rtr1 → unracked tray', 'nd-fw1 → unracked tray']);
    await page.keyboard.press('Control+z');
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-rtr1 → unracked tray']);
    const p = await pointOf(page, 'device:2');
    await page.mouse.click(p.x, p.y);
    await page.keyboard.press('Delete');
    await editorIdle(page);
    await page.locator('#basket-ops li').nth(0).locator('.op-undo').click();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-core1 → unracked tray']);
    await page.click('#basket-discard');
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
  });
});

test.describe('change basket: apply', () => {
  test('dry-run: apply is refused with write_disabled, the UI says so and keeps the basket', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: false });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await expect(page.locator('#basket-mode')).toHaveText('DRY-RUN');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    await page.click('#basket-apply');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toHaveText(
      'Dry-run: the proxy has writing turned off (WRITE_ENABLED is not 1). Nothing was written; your 1 change stays in the basket.',
    );
    expect(await ops(page)).toHaveLength(1);
    expect(stub.sent.filter((s) => s.route === 'apply')).toHaveLength(1);
    await docShot(page, '11-apply-dry-run.png');
  });

  test('apply with writes on: the basket empties and the scene is read again from the source', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await expect(page.locator('#basket-mode')).toHaveText('WRITE');
    const before = await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    expect((await pointOf(page, 'rack:2')).y).not.toBe(before.y);
    await page.fill('#basket-reason', 'move ND-R2 for the new PDU');
    await page.click('#basket-apply');
    await editorIdle(page);
    expect(stub.sent.at(-1)).toMatchObject({ route: 'apply', body: { reason: 'move ND-R2 for the new PDU' } });
    await expect(page.locator('#basket-reason')).toHaveCount(0); // the basket is empty: no reason box
    await expect(page.locator('#basket-msg')).toHaveText('Applied 1 change to NetBox (see its changelog).');
    await expect(page.locator('#basket-msg')).toHaveAttribute('data-tone', 'ok'); // not the warning colour
    expect(await ops(page)).toEqual([]);
    // the fixture source did not change, so the re-read scene shows ND-R2 where the fixture has it
    expect(await pointOf(page, 'rack:2')).toEqual(before);
  });

  test('a failed write: ops already written leave the basket, the scene reloads, the message says what happened', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    stub.failApplyAt = 1;
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    await rackDrag(page, 3, 5, 4);
    await editorIdle(page);
    await page.click('#basket-apply');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toHaveText('NetBox refused op 2 (move_rack): netbox_error. Op 1 was written and left the basket.');
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R3 → tile 5,4']);
    stub.failApplyAt = 0;
    stub.outcomeUnknown = true;
    await page.click('#basket-apply');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toContainText('may or may not be written');
    expect(await ops(page)).toHaveLength(1);
  });
});

test.describe('info panels', () => {
  test('rack panel: contract fields, whitelisted fields edit into the basket, open in NetBox', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const p = await pointOf(page, 'rack:1');
    await page.mouse.click(p.x, p.y);
    const panel = page.locator('#info-panel');
    await expect(panel.locator('#info-title')).toHaveText('ND-R1');
    await expect(panel.locator('tr th')).toHaveText(['name', 'status', 'height (U)', 'first U', 'location', 'iso x', 'iso y', 'iso facing', 'latitude', 'longitude']);
    await expect(panel.locator('tr[data-field=location_id] td')).toHaveText('Server Room'); // a name, not "1"
    await expect(panel.locator('tr[data-field=u_height] input')).toHaveCount(0); // not on the whitelist
    await expect(panel.locator('#act-netbox')).toHaveAttribute('href', 'http://netbox.example.invalid/dcim/racks/1/');
    await panel.locator('input[name=name]').fill('ND-R1A');
    await panel.locator('input[name=name]').press('Enter');
    await editorIdle(page);
    await panel.locator('select[name=iso_facing]').selectOption('E');
    await editorIdle(page);
    expect(await ops(page)).toEqual([
      { label: 'ND-R1: name = ND-R1A', status: 'ok' },
      { label: 'ND-R1A: iso_facing = E', status: 'ok' },
    ]);
    await expect(panel.locator('#info-title')).toHaveText('ND-R1A'); // the panel shows the staged value
    await docShot(page, '12-rack-panel-edit.png');
  });

  test('device panel adds DeviceInfo fields; port panel links to NetBox through the proxy-reported URL', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=device:2');
    const d = await pointOf(page, 'device:2');
    await page.mouse.click(d.x, d.y);
    await expect(page.locator('#info-panel tr[data-field=serial] input')).toHaveValue(/.+/);
    await expect(page.locator('#info-panel tr[data-field=role]')).toContainText('Switch');
    const port = await pointOf(page, 'port:interface:21');
    await page.mouse.click(port.x, port.y);
    await expect(page.locator('#info-panel #info-title')).toHaveText('Te1/0/20');
    await expect(page.locator('#act-netbox')).toHaveAttribute('href', 'http://netbox.example.invalid/dcim/interfaces/21/');
    await page.locator('#info-panel input[name=enabled]').uncheck();
    await editorIdle(page);
    expect(await ops(page)).toEqual([{ label: 'Te1/0/20: enabled = false', status: 'ok' }]);
  });
});

test.describe('trace', () => {
  test('trace from a port through two patch panels: hops listed, path highlighted at device, rack and room level', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=device:2');
    const port = await pointOf(page, 'port:interface:21');
    await page.mouse.click(port.x, port.y);
    await page.click('#act-trace');
    await expect(page.locator('#trace-flags')).toHaveText('3 hops, complete');
    await expect(page.locator('#trace-hops li')).toHaveText([
      'nd-core1 Te1/0/20 —cable ND-032→ nd-pp1 front 1',
      'nd-pp1 rear 1 —cable ND-031→ nd-pp-nd-c1 rear 1',
      'nd-pp-nd-c1 front 1 —cable ND-030→ nd-acc-nd-c1 Te1/1/1',
    ]);
    expect((await editor(page)).trace).toEqual(
      expect.arrayContaining(['cable:30', 'cable:31', 'cable:32', 'device:1', 'device:25', 'device:26', 'port:interface:173', 'port:front_port:1', 'port:interface:21']),
    );
    await docShot(page, '13-trace-device.png');
    await page.keyboard.press('Escape'); // closes the port panel (the trace stays)
    await page.keyboard.press('Backspace'); // zooms out to the rack (Esc would clear the trace first)
    await settled(page);
    expect((await state(page)).level).toBe('rack');
    expect(await markCount(page)).toBe(2); // nd-core1 and nd-pp1 framed, nothing else (previously: lower bound only)
    await page.keyboard.press('Backspace');
    await settled(page);
    expect((await state(page)).level).toBe('room');
    // ND-R1 holds nd-core1 and nd-pp1; the rest of the path is in the Comms Cupboard
    expect(await markCount(page)).toBe(1);
    // trace marks carry the trace colour, not the selection colour (previously: untested)
    const traceTint = await paletteHex(page, 'accent_alt');
    expect(await layerTints(page, 'marks')).toEqual([traceTint]);
    expect(await layerTints(page, 'outlines')).toEqual([traceTint]); // ND-R1 outlined above its own sprite
    await docShot(page, '14-trace-room.png');
    await page.keyboard.press('Escape'); // no panel open: Esc clears the trace, the level stays
    expect(await markCount(page)).toBe(0);
    expect((await editor(page)).trace).toEqual([]);
    expect((await state(page)).level).toBe('room');
  });

  test('trace across a circuit: the circuit hop, both sites and the world link light up', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=device:3');
    const port = await pointOf(page, 'port:interface:30');
    await page.mouse.click(port.x, port.y);
    await page.keyboard.press('t');
    await expect(page.locator('#trace-hops li')).toHaveText([
      'nd-rtr1 xe-0/1/0 —cable ND-042→ circuit EXC-1001 term A',
      'circuit EXC-1001 term A —circuit→ circuit EXC-1001 term Z',
      'circuit EXC-1001 term Z —cable WW-015→ ww-rtr1 xe-0/1/0',
    ]);
    await page.locator('#hud-crumbs a', { hasText: 'World' }).click();
    await settled(page);
    expect((await editor(page)).trace).toEqual(expect.arrayContaining(['site:1', 'site:2', 'link:1-2']));
    expect(await markCount(page)).toBeGreaterThan(8); // 2 x 2 site footprints + the link's wire tiles
    await docShot(page, '15-trace-circuit-world.png');
  });
});

// An object drag must never start on, or outlive, a level swap.
test.describe('drags and level swaps', () => {
  for (const [from, key] of [
    ['site:1', 'rack:1'],
    ['room:1:1', 'rack:2'],
  ] as const) {
    test(`double-click ${key} at ${from} (animated) then drag at once: nothing staged, no ghost left`, async ({ page }) => {
      const stub = await stubProxy(page);
      for (let run = 0; run < 5; run++) {
        await open(page, `source=http&view=${from}`); // animated transitions
        const p = await pointOf(page, key);
        await page.mouse.dblclick(p.x, p.y);
        // press at once, while the zoom-in runs, and drag across the screen
        await page.mouse.move(p.x, p.y);
        await page.mouse.down();
        await page.mouse.move(p.x + 6, p.y + 6, { steps: 2 });
        await page.mouse.move(p.x + 90, p.y + 40, { steps: 6 });
        await settled(page);
        await page.mouse.move(p.x + 120, p.y + 60, { steps: 4 }); // and on after the transition ended
        await page.mouse.up();
        await editorIdle(page);
        expect(await ops(page), `run ${run}`).toEqual([]);
        expect((await editor(page)).ghost, `run ${run}`).toBeNull();
        expect(await page.evaluate(() => window.__iso!.view.dragging)).toBe(false);
      }
      expect(stub.sent.filter((s) => s.body.ops.some((o) => o.op === 'move_rack'))).toEqual([]);
    });
  }

  test('a navigation during a drag ends it: no drop on the new level, no ghost', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4, false);
    expect((await editor(page)).ghost).not.toBeNull();
    await page.evaluate(() => window.__iso!.view.goto({ level: 'site', siteId: 1 }, 'out'));
    await settled(page);
    expect((await editor(page)).ghost).toBeNull();
    await page.mouse.move(300, 300, { steps: 3 });
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    expect((await state(page)).level).toBe('site');
    // back in the room (cached level): no ghost sprite left in its ghost layer
    await page.evaluate(() => window.__iso!.view.goto({ level: 'room', siteId: 1, locationId: 1 }, 'in'));
    await settled(page);
    expect((await editor(page)).ghost).toBeNull();
    expect(await page.evaluate(() => window.__iso!.view.app.stage.children[0]!.children.at(-1)!.children.find((c) => c.label === 'ghost')?.children.length ?? 0)).toBe(0);
  });

  test('a slow preview answer after the drop does not bring the ghost back', async ({ page }) => {
    const stub = await stubProxy(page);
    stub.previewDelayMs = 400;
    await open(page, 'source=http&anim=0&view=rack:1');
    await drag(page, await pointOf(page, 'device:3'), await uPoint(page, RACK, 20), false);
    // rest past the candidate debounce (the preview is sent), then release before its answer arrives
    await expect.poll(() => stub.sent.length - stub.answered).toBeGreaterThan(0);
    await page.mouse.up();
    expect((await editor(page)).ghost).toBeNull();
    const inFlight = stub.sent.length - stub.answered;
    expect(inFlight).toBeGreaterThan(0); // the candidate previews are still unanswered at the drop
    await expect.poll(() => stub.sent.length - stub.answered, { timeout: 20_000 }).toBe(0);
    await page.waitForTimeout(100); // let the page handle the last answer
    expect((await editor(page)).ghost).toBeNull();
    await expect(page.locator('#drag-status')).toBeHidden();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-rtr1 → ND-R1 U20']);
  });

  test('during a drag only Esc acts: Delete, Ctrl+Z and Backspace do nothing', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 3, 5, 4);
    await editorIdle(page);
    await rackDrag(page, 2, 3, 4, false);
    await page.keyboard.press('Control+z');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('r');
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R3 → tile 5,4']);
    expect((await state(page)).level).toBe('room');
    expect((await editor(page)).ghost).not.toBeNull(); // still dragging
    await page.mouse.up();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R3 → tile 5,4', 'ND-R2 → tile 3,4']);
  });
});

test.describe('editor details', () => {
  test('a rack follows its grab point: a small move lands on the next tile, not under the pointer', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const from = await pointOf(page, 'rack:2'); // the sprite centre is well above the rack's own tile
    const a = await tilePoint(page, 3, 2);
    const b = await tilePoint(page, 4, 2); // one tile along x
    await drag(page, from, { x: from.x + b.x - a.x, y: from.y + b.y - a.y }, false);
    expect((await editor(page)).ghost?.text).toBe('ND-R2 → tile 4,2');
    await page.mouse.up();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R2 → tile 4,2']);
  });

  test('a tile with an unracked device on the floor is refused (Store room)', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:3');
    const p = await pointOf(page, 'device:27');
    await page.click('#tool-add-rack');
    await page.mouse.move(p.x, p.y + 4, { steps: 2 });
    expect((await editor(page)).ghost?.problem).toBe('tile taken by nd-srv90');
  });

  test('status and enum fields are selects; the changelog reason goes with Apply', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    await open(page, 'source=http&anim=0&view=room:1:1');
    const r = await pointOf(page, 'rack:2');
    await page.mouse.click(r.x, r.y);
    const status = page.locator('#info-panel select[name="status"]');
    await expect(status.locator('option')).toHaveText(['reserved', 'available', 'planned', 'active', 'deprecated']);
    await status.selectOption('planned');
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R2: status = planned']);
    await expect(page.locator('#info-panel select[name="iso_facing"] option')).toHaveText(['—', 'N', 'E', 'S', 'W']);
    await page.fill('#basket-reason', 'plan the swap');
    await page.click('#basket-apply');
    await editorIdle(page);
    expect(stub.sent.at(-1)).toMatchObject({ route: 'apply', body: { reason: 'plan the swap', ops: [{ op: 'update_fields', fields: { status: 'planned' } }] } });
  });

  test('unracked tray chips carry a faceplate thumbnail in the theme on screen', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:3');
    const imgs = page.locator('#tray .tray-device img');
    await expect(imgs).toHaveCount(2);
    expect(await imgs.first().evaluate((i: HTMLImageElement) => i.naturalWidth > 0 && i.src.startsWith('data:image/png'))).toBe(true);
    await docShot(page, '16-tray-thumbnails.png');
  });

  test('editor panels take the palette of the theme on screen and follow a theme change', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const look = () =>
      page.evaluate(() => ({ want: window.__iso!.view.theme.palette.text, got: getComputedStyle(document.getElementById('basket')!).color, frame: document.getElementById('basket')!.style.borderImageSource }));
    const hex = (rgb: string) => '#' + rgb.match(/\d+/g)!.slice(0, 3).map((n) => Number(n).toString(16).padStart(2, '0')).join('');
    const a = await look();
    expect(hex(a.got)).toBe(a.want.toLowerCase());
    const target = (await state(page)).theme === 'farm' ? 'business' : 'farm'; // a real change under any RACKWALKER_E2E_THEME
    await page.locator('#theme-button').click();
    await page.locator(`#theme-menu [data-theme="${target}"]`).click();
    await settled(page);
    expect((await state(page)).theme).toBe(target);
    const b = await look();
    expect(hex(b.got)).toBe(b.want.toLowerCase());
    expect(b.frame).not.toBe(a.frame);
  });
});

// --------------------------------------------------------------------------------------------------- regressions
const fixture = (f: string) => JSON.parse(readFileSync(new URL(`../../fixtures/${f}`, import.meta.url), 'utf8'));

test.describe('regressions', () => {
  test('canned fixture data is read-only: said on screen, nothing staged, nothing sent to the proxy', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    await open(page, 'source=fixture&anim=0&view=room:1:1');
    await expect(page.locator('#basket-mode')).toHaveText('READ-ONLY');
    await expect(page.locator('#basket-readonly')).toContainText('Canned fixture data, not your NetBox: editing is off');
    const r1 = await pointOf(page, 'rack:1');
    await page.mouse.click(r1.x, r1.y);
    await expect(page.locator('#info-panel #info-title')).toHaveText('ND-R1');
    await expect(page.locator('#info-panel input, #info-panel select')).toHaveCount(0); // fields shown, not editable
    await page.keyboard.press('r');
    await expect(page.locator('#basket-msg')).toContainText('Not staged: Canned fixture data');
    await rackDrag(page, 2, 3, 4);
    await settled(page);
    expect(await ops(page)).toEqual([]);
    await expect(page.locator('#tool-add-rack')).toHaveCount(0);
    expect(stub.sent).toEqual([]); // no preview, no apply
  });

  test('the default page (no source=) reads the proxy, so what it shows is what an edit writes', async ({ page }) => {
    const stub = await stubProxy(page);
    await page.goto('/?anim=0&view=room:1:1');
    await page.waitForFunction(() => !!window.__iso);
    await page.evaluate(() => window.__iso!.ready);
    expect(stub.read).toContain('/api/scene/site/1');
    await expect(page.locator('#basket-mode')).toHaveText('DRY-RUN');
  });

  test('a change during an Apply in flight is refused, not lost; the answer empties exactly what was sent', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    stub.applyDelayMs = 1500;
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    const r1 = await pointOf(page, 'rack:1');
    await page.mouse.click(r1.x, r1.y);
    await page.click('#basket-apply');
    await expect(page.locator('#basket-msg')).toHaveText('Applying…');
    await page.keyboard.press('r'); // rotate ND-R1 during the flight
    await expect(page.locator('#basket-msg')).toHaveText('Apply is still writing to NetBox: wait for its answer, then make further changes.');
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R2 → tile 3,4']);
    await expect(page.locator('#basket-discard')).toBeDisabled();
    await expect(page.locator('.op-undo')).toBeDisabled();
    await editorIdle(page);
    expect(stub.sent.filter((s) => s.route === 'apply').map((s) => s.body.ops.map((o) => o.op))).toEqual([['move_rack']]);
    await expect(page.locator('#basket-msg')).toHaveText('Applied 1 change to NetBox (see its changelog).');
    expect(await ops(page)).toEqual([]);
    await page.keyboard.press('r'); // after the answer the rotation stages as usual
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R1 faces W']);
  });

  test('an undo during a failing Apply is refused: the op that was written leaves, the refused one stays', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    stub.applyDelayMs = 1500;
    stub.failApplyAt = 1;
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    await rackDrag(page, 3, 5, 4);
    await editorIdle(page);
    const r1 = await pointOf(page, 'rack:1');
    await page.mouse.click(r1.x, r1.y);
    await page.keyboard.press('r');
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R2 → tile 3,4', 'ND-R3 → tile 5,4', 'ND-R1 faces W']);
    await page.click('#basket-apply');
    await page.keyboard.press('Control+z'); // keyboard and button both refused
    await page.locator('.op-undo').first().click({ force: true });
    expect(await ops(page)).toHaveLength(3);
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toHaveText('NetBox refused op 2 (move_rack): netbox_error. Op 1 was written and left the basket. Op 3 was not sent and stays in the basket.');
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R3 → tile 5,4', 'ND-R1 faces W']);
  });

  test('the device panel follows a staged edit and an apply (DeviceInfo is read again)', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    await open(page, 'source=http&anim=0&view=rack:1');
    const d = await pointOf(page, 'device:2');
    await page.mouse.click(d.x, d.y);
    const serial = page.locator('#info-panel tr[data-field=serial] input');
    await expect(serial).toHaveValue('SYN100002');
    await serial.fill('SYN-NEW');
    await serial.press('Enter');
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-core1: serial = SYN-NEW']);
    await expect(serial).toHaveValue('SYN-NEW'); // the staged value, not the one read before
    await serial.fill('SYN100002'); // typing the old value back is a change again
    await serial.press('Enter');
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-core1: serial = SYN-NEW', 'nd-core1: serial = SYN100002']);
    await page.keyboard.press('Control+z');
    await editorIdle(page);
    // NetBox after the apply: the device detail now carries the written serial
    const after = fixture('device-2.json');
    after.info.serial = 'SYN-LIVE-1';
    stub.gets.set('/api/device/2', { body: after });
    await page.click('#basket-apply');
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await expect(serial).toHaveValue('SYN-LIVE-1');
  });

  test('a failed trace clears the previous trace (list and highlights)', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=device:2');
    const p21 = await pointOf(page, 'port:interface:21');
    await page.mouse.click(p21.x, p21.y);
    await page.keyboard.press('t');
    await expect(page.locator('#trace-flags')).toHaveText('3 hops, complete');
    stub.gets.set('/api/trace/interface/2', { status: 502, body: { error: { code: 'netbox_unreachable', message: 'NetBox did not answer.', details: null } } });
    const p2 = await pointOf(page, 'port:interface:2');
    await page.mouse.click(p2.x, p2.y);
    await page.keyboard.press('t');
    await expect(page.locator('#basket-msg')).toHaveText('Trace failed: NetBox did not answer.');
    expect((await editor(page)).trace).toEqual([]);
    await expect(page.locator('#trace')).toHaveCount(0);
    expect(await markCount(page)).toBe(1); // only the selection frame of Te1/0/1, no trace marks
  });

  test('a drag asks the proxy only about the slot the pointer rests on (debounced candidate previews)', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=rack:1');
    const candidates = () => stub.sent.filter((s) => s.route === 'preview' && s.body.ops.at(-1)?.op === 'place_device').length;
    // across about 16 U slots in one sweep, then rest on U20
    await drag(page, await pointOf(page, 'device:3'), await uPoint(page, RACK, 20), false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
    expect(candidates()).toBe(1);
    expect(stub.sent.at(-1)!.body.ops.at(-1)).toMatchObject({ op: 'place_device', fields: { position: 20 } });
    await page.mouse.up();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-rtr1 → ND-R1 U20']);
  });

  test('selection and trace marks stay inside what they mark: no neighbouring port or port row is covered', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=device:2');
    // a digest of the canvas pixels inside each port's box (level box -> screen box at the integer camera scale)
    const portPixels = () =>
      page.evaluate(() => {
        const v = window.__iso!.view;
        v.app.render();
        const src = v.app.canvas;
        const c = document.createElement('canvas');
        c.width = src.width;
        c.height = src.height;
        const g = c.getContext('2d')!;
        g.drawImage(src, 0, 0);
        const out: Record<string, string> = {};
        for (const it of v.scene!.items) {
          if (it.entity?.kind !== 'port' && it.entity?.kind !== 'device') continue;
          const w = it.entity.kind === 'port' ? 8 : 64;
          const h = it.entity.kind === 'port' ? 8 : 16;
          const a = v.screenOfLevel(it.x, it.y)!, b = v.screenOfLevel(it.x + w, it.y + h)!;
          const d = g.getImageData(Math.round(a.x - 0.5), Math.round(a.y - 0.5), Math.round(b.x - a.x), Math.round(b.y - a.y)).data;
          let hsh = 0;
          for (let i = 0; i < d.length; i++) hsh = (hsh * 31 + d[i]!) >>> 0;
          out[it.entity.kind === 'port' ? `port:${it.entity.data.kind}:${it.entity.data.id}` : 'device'] = hsh.toString(16);
        }
        return out;
      });
    const before = await portPixels();
    const p21 = await pointOf(page, 'port:interface:21');
    await page.mouse.click(p21.x, p21.y);
    await page.keyboard.press('t'); // the trace marks the faceplate (device 2) and port 21
    await expect(page.locator('#trace-flags')).toHaveText('3 hops, complete');
    await page.mouse.move(5, 590); // no hover name over the canvas
    const after = await portPixels();
    const changed = Object.keys(before).filter((k) => before[k] !== after[k]).sort();
    expect(changed).toEqual(['device', 'port:interface:21']);
    await docShot(page, '17-marks-inside.png');
  });

  test('a lock conflict: Reload from NetBox drops the stale op and reads NetBox; staged again it carries the new time', async ({ page }) => {
    const OLD = '2026-11-07T09:00:00Z';
    const NEW = '2026-11-08T07:00:00Z';
    const stub = await stubProxy(page, {
      writeEnabled: true,
      refuse: (op) =>
        op.op === 'move_rack' && op.if_unmodified_since === OLD
          ? { code: 'conflict', message: 'Rack ND-R2 was changed in NetBox after you loaded it: reload, then stage this change again.', details: { field: 'if_unmodified_since', last_updated: NEW, if_unmodified_since: OLD } }
          : null,
    });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4, false);
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('error');
    await page.mouse.up(); // refused by the proxy: not staged
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    // NetBox now has the newer rack; the tab still holds the old scene until it reads again
    const site = fixture('site-1.json');
    site.racks.find((r: { id: number }) => r.id === 2).last_updated = NEW;
    stub.gets.set('/api/scene/site/1', { body: site });
    // stage it on a slow path: rotate (no candidate preview), then the basket preview reports the conflict
    const r2 = await pointOf(page, 'rack:2');
    await page.mouse.click(r2.x, r2.y);
    await page.keyboard.press('r');
    await editorIdle(page);
    await expect(page.locator('#basket-ops li .op-error')).toHaveText(['Rack ND-R2 was changed in NetBox after you loaded it: reload, then stage this change again.']);
    await expect(page.locator('#basket-apply')).toBeDisabled();
    const reads = stub.read.filter((p) => p === '/api/scene/site/1').length;
    await page.click('#basket-reload');
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await expect(page.locator('#basket-msg')).toHaveText('Read NetBox again. The change made on old data left the basket: stage it again on what NetBox has now.');
    expect(stub.read.filter((p) => p === '/api/scene/site/1').length).toBeGreaterThan(reads);
    const again = await pointOf(page, 'rack:2');
    await page.mouse.click(again.x, again.y);
    await page.keyboard.press('r');
    await editorIdle(page);
    expect(await ops(page)).toEqual([{ label: 'ND-R2 faces W', status: 'ok' }]);
    expect(stub.sent.at(-1)!.body.ops.at(-1)).toMatchObject({ op: 'move_rack', if_unmodified_since: NEW });
  });

  test('a lock conflict on Apply is said once, and an undo then restage carries the new time (no Reload needed)', async ({ page }) => {
    const OLD = '2026-11-07T09:00:00Z';
    const NEW = '2026-11-08T07:00:00Z';
    let changed = false;
    const lock = { code: 'conflict' as const, message: 'Rack ND-R2 was changed in NetBox after you loaded it: reload, then stage this change again.', details: { field: 'if_unmodified_since' } };
    const stub = await stubProxy(page, { writeEnabled: true, refuse: (op) => (changed && op.op === 'move_rack' && op.if_unmodified_since === OLD ? lock : null) });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    changed = true; // someone edits ND-R2 in NetBox now
    const site = fixture('site-1.json');
    site.racks.find((r: { id: number }) => r.id === 2).last_updated = NEW;
    stub.gets.set('/api/scene/site/1', { body: site });
    await page.click('#basket-apply');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toHaveText('Nothing was written: op 1 was staged on data that changed in NetBox since you loaded it. Reload from NetBox, then stage it again.');
    await expect(page.locator('#basket-ops li .op-error')).toHaveText([lock.message]); // the reason once, on its op
    await page.keyboard.press('Control+z');
    await editorIdle(page);
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    expect(stub.sent.at(-1)!.body.ops.at(-1)).toMatchObject({ op: 'move_rack', if_unmodified_since: NEW });
    expect(await ops(page)).toEqual([{ label: 'ND-R2 → tile 3,4', status: 'ok' }]);
  });
});

test.describe('editor minors', () => {
  const THEMED = !!process.env.RACKWALKER_E2E_THEME;

  test('outcome unknown after a written op: the written op leaves the basket and is not sent again', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    stub.failApplyAt = 1;
    stub.outcomeUnknown = true;
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 4);
    await editorIdle(page);
    await rackDrag(page, 3, 5, 4);
    await editorIdle(page);
    await page.click('#basket-apply');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toContainText('Op 1 was written and left the basket');
    expect((await ops(page)).map((o) => o.label)).toEqual(['ND-R3 → tile 5,4']);
    stub.failApplyAt = null;
    stub.outcomeUnknown = false;
    await page.click('#basket-apply');
    await editorIdle(page);
    expect(stub.sent.filter((x) => x.route === 'apply').at(-1)!.body.ops.map((o) => o.target.id)).toEqual([3]);
  });

  test('an open staged rack stays itself when an earlier staged rack is undone', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    for (const x of [1, 2]) {
      await page.click('#tool-add-rack');
      const t = await tilePoint(page, x, 4);
      await page.mouse.move(t.x, t.y, { steps: 3 });
      await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('ok');
      await page.mouse.click(t.x, t.y);
      await editorIdle(page);
    }
    expect((await ops(page)).map((o) => o.label)).toEqual(['new rack NEW-1 at tile 1,4', 'new rack NEW-2 at tile 2,4']);
    await page.evaluate(() => window.__iso!.view.goto({ level: 'rack', rackId: -2 }));
    await settled(page);
    expect((await state(page)).crumbs.at(-1)).toBe('NEW-2');
    await page.locator('#basket-ops li').first().locator('.op-undo').click();
    await editorIdle(page);
    const st = await state(page);
    expect([st.crumbs.at(-1), st.error]).toEqual(['NEW-2', null]);
  });

  test('a trace over a direct cross-site cable lights both sites and the world link that carries the cable', async ({ page }) => {
    const stub = await stubProxy(page);
    // nd-core1 Te1/0/23 -> cable 82 (ND-044) -> ee-core1 Te1/0/23, site 1 -> site 3; no circuit on the path.
    const end = (id: number, device_id: number, device_name: string, site_id: number) => ({ kind: 'interface', id, name: 'Te1/0/23', device_id, device_name, circuit_id: null, circuit_cid: null, site_id });
    const a = end(24, 2, 'nd-core1', 1);
    const b = end(311, 43, 'ee-core1', 3);
    stub.gets.set('/api/trace/interface/24', {
      body: {
        meta: { contract_version: '1.1.3', generated_at: '2026-11-08T10:00:00Z', netbox_version: null, source: 'fixture' },
        trace: { origin: a, hops: [{ near_end: [a], cable: { id: 82, label: 'ND-044', type: 'smf-os2', color: 'ff5722', status: 'planned' }, far_end: [b] }], is_complete: true, is_split: false },
      },
    });
    await open(page, 'source=http&anim=0&view=world');
    await page.evaluate(() => window.__iso!.editor.runTrace('interface', 24));
    await expect(page.locator('#trace-flags')).toHaveText('1 hop, complete');
    expect((await editor(page)).trace).toEqual(expect.arrayContaining(['site:1', 'site:3', 'link:1-3', 'cable:82']));
    expect((await editor(page)).trace).not.toContain('link:1-2');
    expect(await markCount(page)).toBeGreaterThan(8);
  });

  test('undoing a staged site theme puts the page back in the old theme (background, picker, panels)', async ({ page }) => {
    test.skip(THEMED, '?theme= overrides every site theme');
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=site:1');
    const own = (await state(page)).theme;
    expect(own).not.toBe('farm');
    await page.evaluate(() => window.__iso!.view.goto({ level: 'world' }));
    await settled(page);
    await page.evaluate(() => window.__iso!.view.selectKey('site:1'));
    await page.locator('#info-panel tr[data-field=iso_theme] select').selectOption('farm');
    await editorIdle(page);
    await page.evaluate(() => window.__iso!.view.goto({ level: 'site', siteId: 1 }));
    await settled(page);
    expect((await state(page)).theme).toBe('farm');
    await page.keyboard.press('Control+z');
    await editorIdle(page);
    await settled(page);
    const st = await state(page);
    expect(st.theme).toBe(own);
    await expect(page.locator('#theme-button')).toHaveAttribute('data-theme', own);
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const want = await page.evaluate(() => {
      const h = window.__iso!.view.theme.palette.background;
      return `rgb(${parseInt(h.slice(1, 3), 16)}, ${parseInt(h.slice(3, 5), 16)}, ${parseInt(h.slice(5, 7), 16)})`;
    });
    expect(bg).toBe(want);
  });

  test('the add-rack tool does not stage a tile the proxy refused', async ({ page }) => {
    const stub = await stubProxy(page, { refuse: (op) => (op.op === 'create_rack' ? 'A rack named NEW-1 already exists in this location.' : null) });
    await open(page, 'source=http&anim=0&view=room:1:1');
    await page.click('#tool-add-rack');
    const t = await tilePoint(page, 1, 4);
    await page.mouse.move(t.x, t.y, { steps: 3 });
    await expect.poll(async () => (await editor(page)).ghost?.verdict).toBe('error');
    await page.mouse.click(t.x, t.y);
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await expect(page.locator('#basket-msg')).toHaveText('Not staged: the proxy says A rack named NEW-1 already exists in this location.');
    expect((await editor(page)).tool).toBe('add-rack'); // still on: pick another tile
    expect(stub.sent.filter((x) => x.route === 'apply')).toEqual([]);
  });

  test('Apply waits for the preview of exactly this basket, is off after a failed preview, and looks off', async ({ page }) => {
    const stub = await stubProxy(page, { writeEnabled: true });
    await open(page, 'source=http&anim=0&view=room:1:1');
    stub.previewDelayMs = 1500;
    const r1 = await pointOf(page, 'rack:1');
    await page.mouse.click(r1.x, r1.y);
    await page.keyboard.press('r');
    await expect(page.locator('#basket-ops li')).toHaveCount(1);
    const apply = page.locator('#basket-apply');
    await expect(apply).toBeDisabled();
    await expect(page.locator('#basket-why')).toHaveText('Waiting for the proxy to preview these changes.');
    await apply.click({ force: true });
    expect(stub.sent.filter((x) => x.route === 'apply')).toEqual([]);
    expect(await apply.evaluate((b) => b.style.borderStyle)).toBe('dashed');
    await editorIdle(page);
    await expect(apply).toBeEnabled();
    await expect(page.locator('#basket-why')).toHaveCount(0);
    // a preview that fails (proxy 502): Apply stays off and says why
    stub.previewDelayMs = 0;
    await page.unroute('**/api/changes/*');
    await page.route('**/api/changes/preview', (r) => r.fulfill({ status: 502, json: { error: { code: 'netbox_unreachable', message: 'NetBox could not be reached.', details: null } } }));
    await page.keyboard.press('r');
    await editorIdle(page);
    await expect(page.locator('#basket-msg')).toContainText('Preview failed');
    await expect(apply).toBeDisabled();
    await expect(page.locator('#basket-why')).toContainText('No preview from the proxy');
  });

  test('drops that change nothing are not staged (own tile, own U)', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    await rackDrag(page, 2, 3, 2, false); // ND-R2 stands on 3,2
    expect((await editor(page)).ghost).toEqual({ text: 'ND-R2 → tile 3,2', verdict: 'error', problem: 'that is where it stands' });
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
    await page.evaluate(() => window.__iso!.view.goto({ level: 'rack', rackId: 1 }));
    await settled(page);
    await editorIdle(page);
    const d = await pointOf(page, 'device:3'); // nd-rtr1 at U36 (2U)
    await drag(page, d, { x: d.x + 3, y: d.y + 1 }, false);
    expect((await editor(page)).ghost).toMatchObject({ verdict: 'error', problem: 'that is where it is' });
    await page.mouse.up();
    await editorIdle(page);
    expect(await ops(page)).toEqual([]);
  });

  test('Ctrl+Z on an empty basket does nothing (no rebuild, no preview)', async ({ page }) => {
    const stub = await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    const reads = stub.read.length;
    await page.evaluate(() => ((window as unknown as { __root: unknown }).__root = window.__iso!.view.app.stage.children[0]!.children.at(-1)));
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(300);
    await settled(page);
    expect(stub.sent).toEqual([]);
    expect(stub.read.length).toBe(reads);
    // the level on screen is the same object: no rebuild ran
    expect(await page.evaluate(() => (window as unknown as { __root: unknown }).__root === window.__iso!.view.app.stage.children[0]!.children.at(-1))).toBe(true);
  });

  test('a selected rack at room level is outlined above its sprite in the highlight colour', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=room:1:1');
    expect(await layerTints(page, 'outlines')).toEqual([]);
    const r3 = await pointOf(page, 'rack:3');
    await page.mouse.click(r3.x, r3.y);
    expect(await layerTints(page, 'outlines')).toEqual([await paletteHex(page, 'highlight')]);
    // the outline shows: many more pixels change around ND-R3 than the hidden floor ring did (measured: 32 to 192 px)
    await page.keyboard.press('Escape');
    const before = await canvasPixels(page, r3, 110, 130);
    await page.mouse.click(r3.x, r3.y);
    const after = await canvasPixels(page, r3, 110, 130);
    let changed = 0;
    for (let i = 0; i < before.length; i += 4) if (before[i] !== after[i] || before[i + 1] !== after[i + 1] || before[i + 2] !== after[i + 2]) changed++;
    expect(changed).toBeGreaterThan(400);
    await docShot(page, '18-rack-selected-outline.png');
  });

  test('Back from a device shows the whole rack again (top U not under the HUD)', async ({ page }) => {
    await stubProxy(page);
    await open(page, 'source=http&anim=0&view=rack:1');
    const first = (await page.evaluate(() => window.__iso!.view.camera))!;
    await page.evaluate(() => window.__iso!.view.goto({ level: 'device', deviceId: 2 }, 'in'));
    await settled(page);
    await page.evaluate(() => window.__iso!.view.back());
    await settled(page);
    const back = (await page.evaluate(() => window.__iso!.view.camera))!;
    expect(back).toEqual(first);
  });

  test('a drag resting on a rack chip says what that rack offers once it has loaded (no stale "not loaded yet")', async ({ page }) => {
    await stubProxy(page);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    await page.route('**/api/scene/rack/2', async (r) => {
      await gate;
      await r.fallback();
    });
    await open(page, 'source=http&anim=0&view=rack:1');
    const chip = await page.locator('#tray .tray-rack', { hasText: 'ND-R2' }).boundingBox();
    await drag(page, await pointOf(page, 'device:3'), { x: chip!.x + 10, y: chip!.y + 6 }, false);
    await expect(page.locator('#drag-status')).toContainText('rack not loaded yet');
    release();
    await expect(page.locator('#drag-status')).toHaveText(/nd-rtr1 → ND-R2 U36/); // pointer has not moved
    await page.mouse.up();
    await editorIdle(page);
    expect((await ops(page)).map((o) => o.label)).toEqual(['nd-rtr1 → ND-R2 U36']);
  });
});
