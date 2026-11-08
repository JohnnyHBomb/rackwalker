import { describe, expect, it } from 'vitest';
import { WORLD_TILES } from '../src/contracts/iso';
import type { Device, DeviceDetail, RackScene, SiteScene, WorldResponse } from '../src/contracts/scene.gen';
import { SLOT_IDS, hexToRgba, renderSlot, type Theme } from '../src/contracts/theme';
import { layoutSite } from '../src/engine/layout';
import { buildDevice, buildRack, buildRoom, buildSite, buildWorld, itemSize, labelBox, portRowText, roomRuns, routeWires, siteTops, untangleRuns, wirePath } from '../src/engine/scene';
import { labelBuffer, labelText } from '../src/engine/font';
import {
  SpriteCache, deviceFaceRows, faceplateBuffer, faceplatePieces, looseBuffer, mirrorX, rackBuffer, rackFacePixel, rackFrontFace, turnClockwise, RACK_FACE_WIDTH,
} from '../src/engine/sprites';
import { THEME_LIST } from '../src/themes/index';
import { debugTheme } from '../src/themes/debug';
import { fixture } from './helpers';

const site1 = fixture<SiteScene>('site-1.json');
const rack1 = fixture<RackScene>('rack-1.json');
const dev2 = fixture<DeviceDetail>('device-2.json');
const withU = (d: Device, u: number, slug = d.role.slug): Device => ({ ...d, role: { ...d.role, slug }, device_type: { ...d.device_type, u_height: u } });

describe('sprite choice (theme.md rules)', () => {
  const d = site1.devices[0]!;
  it('1U by role, zero-U and multi-U stacks', () => {
    expect(faceplatePieces(withU(d, 1, 'switch')).map((p) => p.slot)).toEqual(['switch']);
    expect(faceplatePieces(withU(d, 1, 'router')).map((p) => p.slot)).toEqual(['router']);
    expect(faceplatePieces(withU(d, 1, 'firewall')).map((p) => p.slot)).toEqual(['router']);
    expect(faceplatePieces(withU(d, 1, 'patch-panel')).map((p) => p.slot)).toEqual(['patch_panel']);
    expect(faceplatePieces(withU(d, 1, 'pdu')).map((p) => p.slot)).toEqual(['psu']);
    expect(faceplatePieces(withU(d, 1, 'server')).map((p) => p.slot)).toEqual(['device_1u']);
    expect(faceplatePieces(withU(d, 2, 'switch')).map((p) => p.slot)).toEqual(['device_2u']);
    expect(faceplatePieces(withU(d, 3)).map((p) => [p.slot, p.y])).toEqual([['device_2u', 0], ['device_1u', 16]]);
    expect(faceplatePieces(withU(d, 4)).map((p) => p.slot)).toEqual(['device_4u']);
    expect(faceplatePieces(withU(d, 5)).map((p) => p.slot)).toEqual(['device_4u', 'device_1u']);
    expect(faceplatePieces(withU(d, 7)).map((p) => [p.slot, p.y])).toEqual([['device_4u', 0], ['device_2u', 32], ['device_1u', 48]]);
    expect(faceplatePieces(withU(d, 8)).map((p) => p.slot)).toEqual(['device_4u', 'device_4u']);
    expect(faceplatePieces(withU(d, 2)).every((p) => p.variant.role_color === '#9e9e9e')).toBe(true);
  });
});

describe('engine-side transforms', () => {
  it('mirror and quarter turn are exact pixel permutations', () => {
    const b = renderSlot(debugTheme, 'room_wall');
    const m = mirrorX(b);
    expect(mirrorX(m).data).toEqual(b.data);
    const t = turnClockwise(renderSlot(debugTheme, 'psu', { rotation: 1 }));
    expect([t.width, t.height]).toEqual([8, 64]);
    const back = turnClockwise(turnClockwise(turnClockwise(t)));
    expect(back.data).toEqual(renderSlot(debugTheme, 'psu', { rotation: 1 }).data);
  });
});

describe('room-level rack sprite', () => {
  const rack = rack1.rack;
  it('maps U ranges to face rows top-down (U42 at the top)', () => {
    const pp = rack1.devices.find((x) => x.name === 'nd-pp1')!; // U42
    const core = rack1.devices.find((x) => x.name === 'nd-core1')!; // U38-39
    expect(deviceFaceRows(rack, pp)).toEqual([0, 1]);
    const [c0, c1] = deviceFaceRows(rack, core);
    expect(c0).toBe(Math.floor((3 * 8 * 48) / (42 * 8)));
    expect(c1).toBeGreaterThan(c0);
  });
  it('paints devices on the S face only when facing S, on the E face when facing E, nowhere when facing away', () => {
    const plain = (rot: 0 | 1 | 2 | 3) => renderSlot(debugTheme, 'rack_empty', { rotation: rot, status: rack.status });
    const diff = (rot: 0 | 1 | 2 | 3) => {
      const a = plain(rot), b = rackBuffer(debugTheme, rack, rack1.devices, rot);
      const cols = new Set<number>();
      for (let i = 0; i < a.data.length; i += 4) if (a.data.slice(i, i + 4).join() !== b.data.slice(i, i + 4).join()) cols.add((i / 4) % a.width);
      return [...cols].sort((p, q) => p - q);
    };
    expect(rackFrontFace(0)).toBe('S');
    expect(diff(0).every((c) => c >= 2 && c <= 13)).toBe(true);
    expect(diff(0).length).toBe(RACK_FACE_WIDTH);
    expect(diff(3).every((c) => c >= 18 && c <= 29)).toBe(true);
    expect(diff(1)).toEqual([]);
    expect(diff(2)).toEqual([]);
  });
  it('face pixels stay inside the 32x64 sprite', () => {
    for (const face of ['S', 'E'] as const)
      for (let i = 0; i < RACK_FACE_WIDTH; i++)
        for (const r of [0, 47]) {
          const p = rackFacePixel(face, i, r);
          expect(p.x >= 0 && p.x < 32 && p.y >= 0 && p.y < 64).toBe(true);
        }
  });
});

describe('no art outside the theme', () => {
  /** RGBA of pixel i (byte offset) as one number: the pixel walks below compare numbers, not joined strings
   *  (the string version took 4.2 s of the 5 s test timeout and failed once at 5.2 s under load, 8 Nov). */
  const rgba = (d: Uint8ClampedArray, i: number) => ((d[i]! << 24) | (d[i + 1]! << 16) | (d[i + 2]! << 8) | d[i + 3]!) >>> 0;
  /** Every colour the theme can produce for the inputs used (palette, slot literals, role/cable colours). */
  function themeColours(theme: Theme, extra: string[]): Set<number> {
    const set = new Set<number>();
    const add = (b: { data: Uint8ClampedArray }) => {
      for (let i = 0; i < b.data.length; i += 4) if (b.data[i + 3]) set.add(rgba(b.data, i));
    };
    for (const slot of SLOT_IDS)
      for (const variant of [{}, { status: 'failed' }, { rotation: 1 as const }, { seed: 1 }, ...extra.map((c) => ({ role_color: c, wire_color: c, status: 'connected' }))])
        add(renderSlot(theme, slot, variant));
    return set;
  }
  it('every opaque pixel of every level sprite is a colour the theme itself draws', () => {
    const sites = [1, 2, 3].map((i) => fixture<SiteScene>(`site-${i}.json`));
    const colours = [...sites.flatMap((s) => [...s.devices.map((d) => d.role.color), ...s.cables.map((c) => c.color)]), ...dev2.cables.map((c) => c.color)]
      .filter((c): c is string => !!c).map((c) => `#${c}`);
    const allowed = themeColours(debugTheme, colours);
    const cache = new SpriteCache(debugTheme);
    const scenes = [buildWorld(fixture<WorldResponse>('world.json')), buildRack(rack1), buildDevice(dev2)];
    for (const s of sites) {
      scenes.push(buildSite(s));
      for (const l of s.locations) scenes.push(buildRoom(s, l.id));
    }
    let checked = 0;
    for (const sc of scenes)
      for (const it of sc.items) {
        const b = cache.get(it.sprite);
        expect([b.width, b.height]).toEqual([itemSize(it).w, itemSize(it).h]);
        for (let i = 0; i < b.data.length; i += 4) {
          if (!b.data[i + 3]) continue;
          checked++;
          if (!allowed.has(rgba(b.data, i))) throw new Error(`${sc.level}: colour ${Array.from(b.data.slice(i, i + 4)).join()} not from the theme`);
        }
      }
    expect(checked).toBeGreaterThan(100000);
  });
  it('a different palette changes the pixels (art flows through the theme)', () => {
    const recoloured: Theme = { ...debugTheme, palette: { ...debugTheme.palette, floor: '#123456', floor_alt: '#123456' } };
    const scene = buildRoom(site1, 1);
    const floor = scene.items.find((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'room_floor')!;
    const b = new SpriteCache(recoloured).get(floor.sprite);
    expect(Array.from(b.data.slice((8 * 32 + 16) * 4, (8 * 32 + 16) * 4 + 4))).toEqual(hexToRgba('#123456'));
  });
});

describe('floor wires', () => {
  it('routes along x then y and picks straight, corner and cross sprites', () => {
    const t = routeWires([{ id: 1, a: { x: 0, y: 0 }, b: { x: 2, y: 2 } }]);
    expect(t.map((w) => [w.x, w.y, w.slot, w.rotation])).toEqual([
      [0, 0, 'wire_straight', 0],
      [1, 0, 'wire_straight', 0],
      [2, 0, 'wire_corner', 0], // enters from -x, leaves towards +y
      [2, 1, 'wire_straight', 1],
      [2, 2, 'wire_straight', 1],
    ]);
  });
  it('corner rotations follow theme-slots.json (0 joins -x and +y; each step turns 90 degrees clockwise on screen)', () => {
    const corner = (a: [number, number], b: [number, number]) => routeWires([{ id: 1, a: { x: a[0], y: a[1] }, b: { x: b[0], y: b[1] } }]).find((w) => w.slot === 'wire_corner')!;
    expect(corner([0, 0], [1, 1])).toMatchObject({ x: 1, y: 0, rotation: 0 }); // -x & +y
    expect(corner([0, 1], [1, 0])).toMatchObject({ x: 1, y: 1, rotation: 1 }); // -x & -y
    expect(corner([1, 1], [0, 0])).toMatchObject({ x: 0, y: 1, rotation: 2 }); // +x & -y
    expect(corner([1, 0], [0, 1])).toMatchObject({ x: 0, y: 0, rotation: 3 }); // +x & +y
  });
  it('the debug theme draws exactly the two tile edges each engine corner rotation joins', () => {
    // Edge midpoints of the 32x16 tile (+-1 px): -x upper-left, -y upper-right, +x lower-right, +y lower-left.
    const edges = { '-x': [8, 4], '-y': [23, 4], '+x': [24, 12], '+y': [7, 12] } as const;
    const near = (b: { data: Uint8ClampedArray; width: number }, x: number, y: number) =>
      [-1, 0, 1].some((dx) => [-1, 0, 1].some((dy) => b.data[((y + dy) * b.width + x + dx) * 4 + 3]! > 0));
    // Each L run's corner tile, with the two edges the run actually uses there.
    const cases: [[number, number], [number, number], string[]][] = [
      [[0, 0], [1, 1], ['-x', '+y']],
      [[0, 1], [1, 0], ['-x', '-y']],
      [[1, 1], [0, 0], ['+x', '-y']],
      [[1, 0], [0, 1], ['+x', '+y']],
    ];
    const seen = new Set<number>();
    for (const [a, b, want] of cases) {
      const c = routeWires([{ id: 1, a: { x: a[0], y: a[1] }, b: { x: b[0], y: b[1] } }]).find((w) => w.slot === 'wire_corner')!;
      seen.add(c.rotation);
      const buf = renderSlot(debugTheme, 'wire_corner', { rotation: c.rotation });
      const hit = Object.entries(edges).filter(([, [x, y]]) => near(buf, x, y)).map(([k]) => k).sort();
      expect(hit, `${a} -> ${b} (rotation ${c.rotation})`).toEqual([...want].sort());
    }
    expect(seen.size).toBe(4);
  });
  it('world links never lie on each other outside the buildings (no false tee)', () => {
    const world = fixture<WorldResponse>('world.json');
    const tops = siteTops(world);
    const under = (x: number, y: number) => [...tops.values()].some((t) => x >= t.x && x <= t.x + 1 && y >= t.y && y <= t.y + 1);
    const scene = buildWorld(world);
    // tiles of each link's wire sprites, by link (the pick result of a wire tile is its link)
    const byLink = new Map<string, Set<string>>();
    for (const i of scene.items.filter((i) => i.entity?.kind === 'link')) {
      const id = (i.entity!.data as { circuits: { cid: string }[] }).circuits.map((c) => c.cid).join();
      (byLink.get(id) ?? byLink.set(id, new Set()).get(id)!).add(`${i.tile!.x},${i.tile!.y}`);
    }
    expect(byLink.size).toBe(2);
    // every wire tile outside a building belongs to exactly one link's route
    const runs = world.links.map((l, i) => ({ id: i, a: { x: tops.get(l.a_site_id)!.x + 1, y: tops.get(l.a_site_id)!.y + 1 }, b: { x: tops.get(l.b_site_id)!.x + 1, y: tops.get(l.b_site_id)!.y + 1 } }));
    const overlap = (rs: typeof runs) => {
      const seen = new Map<string, number>();
      for (const r of rs) for (const t of wirePath(r)) if (!under(t.x, t.y)) seen.set(`${t.x},${t.y}`, (seen.get(`${t.x},${t.y}`) ?? 0) + 1);
      return [...seen.values()].filter((n) => n > 1).length;
    };
    expect(overlap(runs)).toBeGreaterThan(0); // x-first for both, as before: EXC-1001 ran along the EXC-1002 line
    expect(overlap(untangleRuns(runs, under))).toBe(0);
  });
  it('untangleRuns keeps x first unless y first shares fewer tiles', () => {
    const a = { id: 1, a: { x: 0, y: 0 }, b: { x: 4, y: 0 } };
    const b = { id: 2, a: { x: 0, y: 0 }, b: { x: 3, y: 3 } };
    expect(untangleRuns([a, b]).map((r) => r.yFirst)).toEqual([false, true]);
    expect(untangleRuns([b]).map((r) => r.yFirst)).toEqual([false]);
    expect(wirePath({ a: { x: 0, y: 0 }, b: { x: 1, y: 1 }, yFirst: true })).toEqual([{ x: 0, y: 0 }, { x: 0, y: 1 }, { x: 1, y: 1 }]);
  });
  it('crossing runs make a cross tile carrying the lowest run id', () => {
    const t = routeWires([
      { id: 5, a: { x: 0, y: 1 }, b: { x: 2, y: 1 } },
      { id: 3, a: { x: 1, y: 0 }, b: { x: 1, y: 2 } },
    ]);
    expect(t.find((w) => w.x === 1 && w.y === 1)).toMatchObject({ slot: 'wire_cross', runId: 3 });
  });
  it('room cables run on the tiles in front of the racks, with a stub leading back under each rack', () => {
    const scene = buildRoom(site1, 1);
    const rackTiles = new Set(scene.items.filter((i) => i.entity?.kind === 'rack').map((i) => `${i.tile!.x},${i.tile!.y}`));
    const wires = scene.items.filter((i) => i.entity?.kind === 'cable');
    expect(wires.some((w) => rackTiles.has(`${w.tile!.x},${w.tile!.y}`))).toBe(false);
    // Racks 1..4 face S at (1,2) (3,2) (5,2) (7,2): the run is the row y = 3, turning up (-y) at both ends.
    const t = routeWires(roomRuns(site1, layoutSite(site1), 1));
    expect(t.map((w) => [w.x, w.y, w.slot, w.rotation])).toEqual([
      [1, 3, 'wire_corner', 2], // +x & -y
      [2, 3, 'wire_straight', 0],
      [3, 3, 'wire_cross', 0], // T into rack 2 (no T slot in the contract)
      [4, 3, 'wire_straight', 0],
      [5, 3, 'wire_cross', 0],
      [6, 3, 'wire_straight', 0],
      [7, 3, 'wire_corner', 1], // -x & -y
    ]);
  });
  it('a lone stub draws as a straight piece and a front tile outside the room falls back to the rack tile', () => {
    expect(routeWires([{ id: 1, a: { x: 0, y: 1 }, b: { x: 0, y: 1 }, leadA: '-y' }]).map((w) => [w.slot, w.rotation])).toEqual([['wire_straight', 1]]);
    const rack = site1.racks.find((r) => r.id === 1)!;
    const layout = layoutSite(site1);
    const spot = layout.racks.get(1)!;
    const north = { ...layout, racks: new Map([...layout.racks, [1, { ...spot, y: 0, facing: 'N' as const }]]) };
    const run = roomRuns(site1, north, rack.location_id!).find((r) => r.id === 6)!;
    expect(run.a).toEqual({ x: 3, y: 3 });
    expect(run.b).toEqual({ x: 1, y: 0 });
    expect(run.leadB).toBeUndefined();
  });
  it('the room scene draws cables between its racks as floor tiles with the cable colour', () => {
    const scene = buildRoom(site1, 1);
    const wires = scene.items.filter((i) => i.entity?.kind === 'cable');
    expect(wires.length).toBeGreaterThan(0);
    const c6 = wires.find((w) => (w.entity!.data as { id: number }).id === 6);
    expect(c6?.sprite).toMatchObject({ kind: 'slot', variant: { wire_color: '#00bcd4' } });
    expect(wires.every((w) => w.layer === 'ground')).toBe(true);
  });
});

describe('level scenes from fixtures', () => {
  it('world: one 2x2 building per site, centred on its GPS point, inside the overworld; links as wires', () => {
    const w = fixture<WorldResponse>('world.json');
    const s = buildWorld(w);
    const b = s.items.filter((i) => i.entity?.kind === 'site');
    expect(b.map((i) => (i.entity!.data as { id: number }).id).sort()).toEqual([1, 2, 3]);
    for (const t of siteTops(w).values()) {
      expect(t.x).toBeGreaterThanOrEqual(2);
      expect(t.y + 2).toBeLessThanOrEqual(WORLD_TILES - 2);
    }
    expect(s.items.some((i) => i.entity?.kind === 'link')).toBe(true);
    expect(s.items.filter((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'room_floor').length).toBe(WORLD_TILES * WORLD_TILES);
  });
  it('site: every location floor, N/W walls, every rack and every loose device', () => {
    const s = buildSite(site1);
    const floors = s.items.filter((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'room_floor');
    expect(floors.length).toBe(site1.locations.reduce((n, l) => n + l.iso_w! * l.iso_h!, 0));
    const walls = s.items.filter((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'room_wall');
    expect(walls.length).toBe(site1.locations.reduce((n, l) => n + l.iso_w! + l.iso_h!, 0));
    expect(s.items.filter((i) => i.entity?.kind === 'rack').length).toBe(site1.racks.length);
    expect(s.items.filter((i) => i.entity?.kind === 'device').map((i) => (i.entity!.data as { id: number }).id).sort()).toEqual(site1.devices.filter((d) => d.rack_id === null).map((d) => d.id).sort());
  });
  it('room: only that room, in room coordinates', () => {
    const s = buildRoom(site1, 2); // Comms Cupboard at (12,0), rack ND-C1 at (1,1)
    const racks = s.items.filter((i) => i.entity?.kind === 'rack');
    expect(racks.map((r) => (r.entity!.data as { id: number }).id)).toEqual([5]);
    expect(racks[0]!.tile).toEqual({ x: 1, y: 1, w: 1, h: 1 });
    expect(() => buildRoom(site1, 99)).toThrow();
  });
  it('rack: frame of u_height units, faceplates at their U, zero-U on the left rail', () => {
    const s = buildRack(rack1);
    expect(s.items.filter((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'rack_unit').length).toBe(42);
    const at = (name: string) => s.items.find((i) => i.entity?.kind === 'device' && (i.entity.data as { name: string }).name === name)!;
    expect([at('nd-pp1').x, at('nd-pp1').y]).toEqual([8, 0]);
    expect([at('nd-core1').y, itemSize(at('nd-core1')).h]).toEqual([24, 16]);
    expect([at('nd-pdu-nd-r1').x, at('nd-pdu-nd-r1').y, itemSize(at('nd-pdu-nd-r1'))]).toEqual([0, 0, { w: 8, h: 64 }]);
    // bounds take the labels in: U numbers left of the frame ("42" ends 2 px before it), device names right of it
    expect(s.bounds).toEqual({ x: -11, y: 0, w: 127, h: 336 });
  });
  it('device: faceplate then ports, one block per kind in contract order, status from the port', () => {
    const s = buildDevice(dev2);
    const ports = s.items.filter((i) => i.entity?.kind === 'port');
    expect(ports.length).toBe(dev2.interfaces.length);
    const p = (name: string) => ports.find((i) => (i.entity!.data as { name: string }).name === name)!;
    expect(p('Te1/0/1').sprite).toMatchObject({ variant: { status: 'connected', wire_color: '#00bcd4' } });
    expect(p('Te1/0/7').sprite).toMatchObject({ variant: { status: 'free' } });
    expect(p('mgmt0').sprite).toMatchObject({ variant: { status: 'mgmt' } });
    expect(p('PSU1').y).toBeGreaterThan(p('mgmt0').y); // power ports start a new block
    expect([p('Te1/0/1').x, p('Te1/0/9').x, p('Te1/0/9').y - p('Te1/0/1').y]).toEqual([0, 0, 8]);
  });
});

describe('unracked devices on the floor (previously: drawn as decoration props)', () => {
  it('stand as a short box with the device faceplate, never a decoration slot, in every theme', () => {
    const room = buildRoom(site1, 3); // the Store: nd-srv90 and nd-srv91 unracked
    const loose = room.items.filter((i) => i.entity?.kind === 'device');
    expect(loose.map((i) => (i.entity!.data as Device).name).sort()).toEqual(['nd-srv90', 'nd-srv91']);
    for (const i of loose) expect(i.sprite.kind).toBe('loose');
    expect(room.items.some((i) => i.sprite.kind === 'slot' && i.sprite.slot === 'decoration_small')).toBe(false);
    const d = loose[0]!.entity!.data as Device;
    for (const theme of THEME_LIST) {
      const box = looseBuffer(theme, d);
      expect(itemSize(loose[0]!)).toEqual({ w: box.width, h: box.height });
      // the faceplate's colours show on the S face; the box keeps the rack's ground point on the tile
      const plate = new Set<string>();
      const fp = faceplateBuffer(theme, d);
      for (let k = 0; k < fp.data.length; k += 4) if (fp.data[k + 3]) plate.add(`${fp.data[k]},${fp.data[k + 1]},${fp.data[k + 2]}`);
      let onFace = 0;
      for (let r = 1; r < 15; r++)
        for (let c = 0; c < RACK_FACE_WIDTH; c++) {
          const p = rackFacePixel('S', c, r);
          const o = (p.y * box.width + p.x) * 4;
          if (plate.has(`${box.data[o]},${box.data[o + 1]},${box.data[o + 2]}`)) onFace++;
        }
      expect(onFace, theme.id).toBeGreaterThan(RACK_FACE_WIDTH * 10);
      expect(box.height, theme.id).toBe(64 - (48 - 16));
      const rack = renderSlot(theme, 'rack_empty', { rotation: 0 });
      const lastRow = (b: { width: number; height: number; data: Uint8ClampedArray }) => {
        for (let y = b.height - 1; y >= 0; y--) for (let x = 0; x < b.width; x++) if (b.data[(y * b.width + x) * 4 + 3]) return y;
        return -1;
      };
      expect(lastRow(box), `${theme.id}: the box bottom is the rack bottom moved up 32 rows`).toBe(lastRow(rack) - 32);
    }
  });
});

describe('names on the canvas (previously: none at any level)', () => {
  const texts = (sc: { labels: readonly { text: string }[] }) => sc.labels.map((l) => l.text);
  it('world: every site name, under its building', () => {
    const w = fixture<WorldResponse>('world.json');
    const sc = buildWorld(w);
    expect(texts(sc).sort()).toEqual(w.sites.map((x) => x.site.name).sort());
    for (const l of sc.labels) {
      const b = sc.items.find((i) => i.entity?.kind === 'site' && (i.entity.data as { name: string }).name === l.text)!;
      expect(l.y).toBeGreaterThan(b.y);
      expect(Math.abs(l.x - (b.x + itemSize(b).w / 2))).toBeLessThanOrEqual(1);
    }
  });
  it('site: every room name; room: every rack and floor-device name above it', () => {
    expect(texts(buildSite(site1)).sort()).toEqual(site1.locations.map((l) => l.name).sort());
    const room = buildRoom(site1, 1);
    expect(texts(room).sort()).toEqual(site1.racks.filter((r) => r.location_id === 1).map((r) => r.name).sort());
    for (const l of room.labels) {
      const r = room.items.find((i) => i.sprite.kind === 'rack' && i.sprite.rack.name === l.text)!;
      expect(labelBox(l).y + labelBox(l).h).toBeLessThanOrEqual(r.y);
    }
    expect(texts(buildRoom(site1, 3))).toEqual(expect.arrayContaining(['nd-srv90', 'nd-srv91']));
  });
  it('rack: a U number per unit beside its row, each front device named beside its faceplate', () => {
    const s = buildRack(rack1);
    const us = s.labels.filter((l) => /^\d+$/.test(l.text));
    expect(us.map((l) => Number(l.text)).sort((a, b) => a - b)).toEqual(Array.from({ length: 42 }, (_, i) => i + 1));
    expect(us.find((l) => l.text === '42')!.y).toBe(1); // U42 is the top row (rows 0-7)
    const names = s.labels.filter((l) => !/^\d+$/.test(l.text));
    for (const d of s.items.filter((i) => i.sprite.kind === 'faceplate')) {
      const n = (d.entity!.data as { name: string }).name;
      const l = names.find((x) => x.text === n)!;
      expect(l, n).toBeDefined();
      expect(l.y).toBeGreaterThanOrEqual(d.y);
      expect(l.y + labelBox(l).h).toBeLessThanOrEqual(d.y + itemSize(d).h + 1);
    }
  });
  it('device: each port row is named beside it', () => {
    const s = buildDevice(dev2);
    const ports = s.items.filter((i) => i.entity?.kind === 'port');
    const rowYs = [...new Set(ports.map((p) => p.y))];
    expect(s.labels.map((l) => l.y)).toEqual(rowYs);
    expect(s.labels[0]!.text).toBe(portRowText(ports.filter((p) => p.y === rowYs[0]).map((p) => (p.entity!.data as { name: string }).name)));
    expect(s.bounds.x + s.bounds.w).toBeGreaterThan(64 + 3); // the labels are on screen
  });
  it('portRowText: all names when short, else first-last with the shared start dropped (numbers kept whole)', () => {
    expect(portRowText(['bmc', 'eth0', 'eth1'])).toBe('bmc eth0 eth1');
    expect(portRowText(['Te1/0/1', 'Te1/0/2', 'Te1/0/3', 'Te1/0/8'])).toBe('Te1/0/1-8');
    expect(portRowText(['Te1/0/9', 'a', 'b', 'Te1/0/16'])).toBe('Te1/0/9-16');
    expect(portRowText(['Te1/0/10', 'a', 'b', 'Te1/0/17'])).toBe('Te1/0/10-17');
    expect(portRowText(['mgmt0'])).toBe('mgmt0');
  });
  it('font: capitals, unknown characters as ?, long names cut; plates use the theme palette only', () => {
    expect(labelText('nd-srv05 é')).toBe('ND-SRV05 ?');
    expect(labelText('x'.repeat(40))).toHaveLength(24);
    for (const theme of THEME_LIST) {
      const b = labelBuffer('ND-R1 U42', theme.palette);
      const cols = new Set<string>();
      for (let k = 0; k < b.data.length; k += 4) cols.add('#' + [0, 1, 2].map((j) => b.data[k + j]!.toString(16).padStart(2, '0')).join(''));
      expect([...cols].sort(), theme.id).toEqual([theme.palette.background, theme.palette.text].sort());
    }
  });
});
