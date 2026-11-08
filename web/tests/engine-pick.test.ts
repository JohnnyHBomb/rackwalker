import { describe, expect, it } from 'vitest';
import type { DeviceDetail, RackScene, SiteScene, WorldResponse } from '../src/contracts/scene.gen';
import { entityKey } from '../src/engine/entity';
import { pickAt } from '../src/engine/pick';
import { isoToScreen } from '../src/engine/projection';
import { buildDevice, buildRack, buildRoom, buildWorld, siteTops, type LevelScene } from '../src/engine/scene';
import { SpriteCache } from '../src/engine/sprites';
import { containerOffset, screenToLevel, type CameraState } from '../src/engine/camera';
import { debugTheme } from '../src/themes/debug';
import { createPixelBuffer } from '../src/contracts/theme';
import { fixture } from './helpers';

const cache = new SpriteCache(debugTheme);
const site1 = fixture<SiteScene>('site-1.json');
const key = (p: ReturnType<typeof pickAt>) => (p ? entityKey(p.entity) : null);

describe('picking returns the contract entity drawn on top', () => {
  // Two racks on one diagonal: (2,2) stands in front of (1,1) and hides its lower half.
  const diag: SiteScene = {
    ...site1,
    racks: site1.racks.map((r) => (r.id === 1 ? { ...r, iso_x: 1, iso_y: 1 } : r.id === 2 ? { ...r, iso_x: 2, iso_y: 2 } : r)),
  };
  const room = buildRoom(diag, 1);

  it('in the overlap, the front rack wins; above it, the rack behind', () => {
    expect(key(pickAt(room, cache, 0.5, 0.5))).toBe('rack:2');
    expect(key(pickAt(room, cache, 0.5, -24.5))).toBe('rack:1');
  });
  it('the room floor picks as the location; outside everything picks nothing', () => {
    const c = isoToScreen(8.5, 4.5); // a free floor tile
    expect(key(pickAt(room, cache, c.sx, c.sy))).toBe('location:1');
    expect(pickAt(room, cache, -5000, -5000)).toBeNull();
  });
  it('a rack in front of the N wall picks as the rack, the wall above it as the room', () => {
    const r = buildRoom({ ...site1, racks: site1.racks.map((x) => (x.id === 1 ? { ...x, iso_x: 4, iso_y: 0 } : x)) }, 1);
    const top = isoToScreen(4, 0); // rack's ground point = wall foot
    expect(key(pickAt(r, cache, top.sx + 0.5, top.sy - 10))).toBe('rack:1');
    expect(key(pickAt(r, cache, top.sx + 8.5, top.sy - 20))).toBe('rack:1');
    // lower down the same column, ND-R4 at (7,2) stands in front of it and wins
    expect(key(pickAt(r, cache, top.sx + 8.5, top.sy - 2))).toBe('rack:4');
    const w = isoToScreen(6, 0);
    expect(key(pickAt(r, cache, w.sx + 4.5, w.sy - 20))).toBe('location:1'); // bare wall
  });
  it('every rack pixel drawn over a wall or the floor picks as that rack (rack drawn later wins)', () => {
    const s = buildRoom(site1, 1);
    const opaque = (i: (typeof s.items)[number], px: number, py: number) => {
      const b = cache.get(i.sprite);
      const x = px - i.x, y = py - i.y;
      return x >= 0 && y >= 0 && x < b.width && y < b.height && b.data[(y * b.width + x) * 4 + 3]! > 0;
    };
    const ground = s.items.filter((i) => i.entity?.kind === 'location');
    let overWall = 0, overFloor = 0;
    for (const r of s.items.filter((i) => i.entity?.kind === 'rack')) {
      const b = cache.get(r.sprite);
      for (let y = 0; y < b.height; y++)
        for (let x = 0; x < b.width; x++) {
          const px = r.x + x, py = r.y + y;
          if (!opaque(r, px, py)) continue;
          const under = ground.filter((g) => opaque(g, px, py));
          if (!under.length) continue;
          if (under.some((g) => g.sprite.kind === 'slot' && g.sprite.slot === 'room_wall')) overWall++;
          if (under.some((g) => g.sprite.kind === 'slot' && g.sprite.slot === 'room_floor')) overFloor++;
          expect(key(pickAt(s, cache, px + 0.5, py + 0.5))).toBe(entityKey(r.entity!));
        }
    }
    expect(overWall).toBeGreaterThan(0);
    expect(overFloor).toBeGreaterThan(0);
  });
  it('wires on the floor pick as their cable', () => {
    const s = buildRoom(site1, 1);
    const wire = s.items.find((i) => i.entity?.kind === 'cable' && i.tile && !s.items.some((o) => o.entity?.kind === 'rack' && o.tile!.x === i.tile!.x && o.tile!.y === i.tile!.y))!;
    const c = isoToScreen(wire.tile!.x + 0.5, wire.tile!.y + 0.5);
    // the debug wire is a 1 px line: scan the tile centre's neighbourhood for it
    const hits = new Set<string>();
    for (let dy = -4; dy <= 4; dy++) for (let dx = -8; dx <= 8; dx++) hits.add(String(key(pickAt(s, cache, c.sx + dx + 0.5, c.sy + dy + 0.5))));
    expect(hits.has(`cable:${(wire.entity!.data as { id: number }).id}`)).toBe(true);
  });
  it('world: a site building picks as its Site', () => {
    const w = fixture<WorldResponse>('world.json');
    const s = buildWorld(w);
    const t = siteTops(w).get(2)!;
    const p = isoToScreen(t.x + 1, t.y + 1, 24);
    expect(pickAt(s, cache, p.sx, p.sy)?.entity).toMatchObject({ kind: 'site', data: { id: 2, name: 'West Works' } });
  });
  // A site ~1 km from another rounded to the same top tile and had 0 pickable pixels.
  it('world: sites whose GPS points round to the same tiles get separate footprints and all stay pickable', () => {
    const w = fixture<WorldResponse>('world.json');
    const s1 = w.sites[0]!;
    const near = (id: number, lat: number, lon: number) => ({ ...s1, site: { ...s1.site, id, name: `Annex ${id}`, latitude: lat, longitude: lon } });
    const world: WorldResponse = { ...w, sites: [...w.sites, near(99, 54.1171, -1.3405), near(98, s1.site.latitude!, s1.site.longitude!), near(97, 54.1176, -1.341)] };
    const tops = siteTops(world);
    const all = [...tops.values()];
    for (let i = 0; i < all.length; i++)
      for (let j = i + 1; j < all.length; j++)
        expect(Math.abs(all[i]!.x - all[j]!.x) >= 2 || Math.abs(all[i]!.y - all[j]!.y) >= 2).toBe(true);
    // the fixture's own sites keep their GPS spots (lowest id claims first)
    const plain = siteTops(w);
    for (const ws of w.sites) if (ws.site.id < 97) expect(tops.get(ws.site.id)).toEqual(plain.get(ws.site.id));
    const scene = buildWorld(world);
    for (const ws of world.sites) {
      const k = `site:${ws.site.id}`;
      let n = 0;
      for (const it of scene.items.filter((i) => i.entity && entityKey(i.entity) === k)) {
        const b = cache.get(it.sprite);
        for (let y = 0; y < b.height; y++)
          for (let x = 0; x < b.width; x++)
            if (b.data[(y * b.width + x) * 4 + 3]! > 0 && key(pickAt(scene, cache, it.x + x + 0.5, it.y + y + 0.5)) === k) n++;
      }
      expect(n, k).toBeGreaterThan(0);
    }
  });
  it('rack zoom: faceplates, zero-U rail and empty frame', () => {
    const s = buildRack(fixture<RackScene>('rack-1.json'));
    expect(key(pickAt(s, cache, 40, 4))).toBe('device:1'); // U42 patch panel
    expect(key(pickAt(s, cache, 40, 30))).toBe('device:2'); // U38-39 core switch
    expect(key(pickAt(s, cache, 3, 10))).toBe('device:5'); // zero-U PDU on the left rail
    expect(key(pickAt(s, cache, 40, 300))).toBe('rack:1'); // empty U
  });
  it('device zoom: ports by (kind, id)', () => {
    const s = buildDevice(fixture<DeviceDetail>('device-2.json'));
    expect(key(pickAt(s, cache, 4, 16 + 4 + 4))).toBe('port:interface:2');
    expect(key(pickAt(s, cache, 30, 4))).toBe('device:2');
  });
  it('device zoom: every one of device-2\'s 27 ports picks as itself', () => {
    const s = buildDevice(fixture<DeviceDetail>('device-2.json'));
    const ports = s.items.filter((i) => i.entity?.kind === 'port');
    expect(ports.length).toBe(27);
    for (const p of ports) {
      const b = cache.get(p.sprite);
      let hit: string | null = null;
      for (let i = 0; i < b.width * b.height && hit === null; i++)
        if (b.data[i * 4 + 3]! > 0) hit = key(pickAt(s, cache, p.x + (i % b.width) + 0.5, p.y + Math.floor(i / b.width) + 0.5));
      expect(hit).toBe(entityKey(p.entity!));
    }
  });
  it('through the camera: screen -> level -> entity, at every render scale', () => {
    const view = { width: 801, height: 599 };
    for (const scale of [1, 2, 3, 4, 5, 6, 8] as const) {
      const cam: CameraState = { cx: 0.3, cy: -7.7, scale };
      const o = containerOffset(cam, view);
      // screen pixel of level point (0.5, 0.5) -> back
      const l = screenToLevel(cam, view, o.x + 0.5 * scale, o.y + 0.5 * scale);
      expect(key(pickAt(room, cache, l.x, l.y))).toBe('rack:2');
    }
  });
});

// Regressions
describe('picking: half-U faceplates and sprite transparency', () => {
  const rack1 = fixture<RackScene>('rack-1.json');
  const base = rack1.devices.find((d) => d.position !== null && d.device_type.u_height === 1)!;
  const half = (id: number, position: number) => ({ ...base, id, name: `half-${id}`, position, face: 'front' as const,
    device_type: { ...base.device_type, u_height: 0.5 } });
  it('0.5U neighbours are drawn half a unit tall at their own position, and each picks as itself', () => {
    const scene: RackScene = { ...rack1, devices: [...rack1.devices.filter((d) => d.position === null), half(901, 20), half(902, 20.5)] };
    const s = buildRack(scene);
    const item = (id: number) => s.items.find((i) => i.entity?.kind === 'device' && (i.entity.data as { id: number }).id === id)!;
    // 42U rack, U1 at the bottom: U20 spans y 176..184; U20.0-20.5 is the lower half (180..184), U20.5-21.0 the upper
    expect([item(901).y, cache.get(item(901).sprite).height]).toEqual([180, 4]);
    expect([item(902).y, cache.get(item(902).sprite).height]).toEqual([176, 4]);
    expect(key(pickAt(s, cache, 40, 182.5))).toBe('device:901');
    expect(key(pickAt(s, cache, 40, 177.5))).toBe('device:902');
  });
  it('a transparent pixel of the top item falls through to the item below (not its bounding box)', () => {
    const rack1Dev = rack1.devices[0]!;
    const buf = (holeAt: number | null) => {
      const b = createPixelBuffer(4, 4);
      for (let i = 0; i < 16; i++) b.data[i * 4 + 3] = i === holeAt ? 0 : 255;
      return b;
    };
    const under = buf(null);
    const over = buf(5); // (1, 1) is see-through
    const item = (id: number) => ({ sprite: { kind: 'faceplate' as const, device: { ...rack1Dev, id } }, x: 10, y: 20,
      layer: 'object' as const, depth: 0, entity: { kind: 'device' as const, data: { ...rack1Dev, id } } });
    const items = [item(1), item(2)];
    const scene = { ...buildRack(rack1), items } as unknown as LevelScene;
    const sprites = { get: (sp: { device: { id: number } }) => (sp.device.id === 2 ? over : under) } as unknown as SpriteCache;
    expect(key(pickAt(scene, sprites, 10.5, 20.5))).toBe('device:2'); // opaque pixel of the top item
    expect(key(pickAt(scene, sprites, 11.5, 21.5))).toBe('device:1'); // its transparent pixel: the item below
    expect(pickAt(scene, sprites, 14.5, 20.5)).toBeNull(); // outside both
  });
});
