// The tile engine: indexed framebuffer composition (tiles, sprites, shadows by palette remap), the autotile set covers
// every key a generated map uses, and the camera follows in whole pixels inside the region.
import { describe, expect, it } from 'vitest';
import { Frame, PALETTE, SPRITES, sprite, TILESET, tileKey, NATIVE_W, NATIVE_H } from '../src/game/world/engine';
import { follow, integerScale } from '../src/game/world/camera';
import { WorldMap, type Terrain, type WorldObject } from '../src/game/world/gen';
import { groundLut } from '../src/game/world/themes';
import { WorldRenderer } from '../src/game/world/render';

describe('engine', () => {
  it('uses the 63-colour palette of the art direction, index 0 transparent', () => {
    expect(PALETTE.colours.length - 1).toBe(63);
    expect(PALETTE.darker[0]).toBe(0);
  });
  it('every tile is 16 x 16 palette indices, never transparent', () => {
    for (const t of TILESET.tiles) {
      expect(t.length).toBe(256);
      expect(t.every((v) => v > 0 && v < PALETTE.colours.length)).toBe(true);
    }
  });
  it('water and fields animate, grass does not', () => {
    const ids = (k: string) => TILESET.keys.get(k)!;
    expect(new Set(ids('WWWWW0')).size).toBeGreaterThan(1);
    expect(new Set(ids('FFFFF0')).size).toBeGreaterThan(1);
    expect(ids('GGGGG0')).toHaveLength(1);
  });
  it('tile keys reduce neighbours to what the centre cares about', () => {
    expect(tileKey('G', 'W', 'S', 'F', 'C', 1)).toBe('GWSLL1'); // grass rounds into water and sand, not into fields
    expect(tileKey('G', 'B', 'G', 'P', 'C', 2)).toBe('GWGPL2');
    expect(tileKey('B', 'P', 'W', 'P', 'W', 3)).toBe('WLWLW3');
    expect(tileKey('S', 'G', 'G', 'W', 'G', 3)).toBe('SGGWG1');
    expect(tileKey('P', 'B', 'G', 'P', 'S', 2)).toBe('PPGPG2');
  });
  it('draws sprites with transparency, clipping and mirroring', () => {
    const f = new Frame(8, 8);
    f.px.fill(5);
    const s = { w: 2, h: 1, ox: 0, oy: 0, frames: [Uint8Array.from([0, 7])] };
    f.sprite(s, 0, 0, 0);
    expect([f.px[0], f.px[1]]).toEqual([5, 7]);
    f.sprite(s, 0, 6, 1, true);
    expect([f.px[8 + 6], f.px[8 + 7]]).toEqual([7, 5]);
    f.sprite(s, 0, -1, 2);
    expect(f.px[16]).toBe(7);
    f.sprite(s, 0, 7, 9); // off the frame: nothing, no throw
    // a sprite past the right edge is cut there: nothing wraps into the next row
    const g = new Frame(4, 2);
    g.sprite({ w: 3, h: 1, ox: 0, oy: 0, frames: [Uint8Array.from([7, 7, 7])] }, 0, 2, 0);
    expect([...g.px]).toEqual([0, 0, 7, 7, 0, 0, 0, 0]);
  });
  it('a shadow\'s outer ring is dithered: every other pixel of the rim keeps its colour', () => {
    const f = new Frame(32, 16);
    const g3 = PALETTE.index('g3'), g2 = PALETTE.index('g2');
    f.px.fill(g3);
    f.ellipse(16, 8, 12, 6, PALETTE.darker);
    const row = [...f.px.subarray(8 * 32, 9 * 32)];
    expect(row[16]).toBe(g2);
    // near the ends of the middle row (outside 0.62 of the radius) the pixels alternate
    const rim = row.slice(5, 8);
    expect(rim.filter((v) => v === g3).length).toBeGreaterThan(0);
    expect(rim.filter((v) => v === g2).length).toBeGreaterThan(0);
  });
  it('shadows are a palette remap one ramp step darker, not alpha', () => {
    const f = new Frame(16, 16);
    const g3 = PALETTE.index('g3');
    f.px.fill(g3);
    f.ellipse(8, 8, 4, 2, PALETTE.darker);
    expect(f.px[8 * 16 + 8]).toBe(PALETTE.index('g2'));
    expect(f.px[0]).toBe(g3);
  });
  it('the autotile set covers every key the generator makes, for every country set and theme', () => {
    for (const c of ['NL', 'GB', null]) {
      const m = new WorldMap([1, 2, 3, 4].map((id) => ({ id, name: `S${id}`, latitude: 52 + id * 0.2, longitude: 4 + id * 0.3, theme: null, country: c })));
      expect(new WorldRenderer(m, NATIVE_W, NATIVE_H).missingKeys()).toEqual([]);
    }
  });
  it('renders a whole native frame from data, every pixel opaque', () => {
    const m = new WorldMap([{ id: 1, name: 'A', latitude: 52, longitude: 4, theme: 'farm', country: 'NL' }]);
    const r = new WorldRenderer(m, NATIVE_W, NATIVE_H);
    const fr = r.render(0, 0, 0, [{ sprite: 'player_down', x: 100, y: 100 }]);
    expect(fr.px.every((v) => v > 0)).toBe(true);
    const out = new Uint32Array(NATIVE_W * NATIVE_H);
    fr.present(out);
    expect(out.every((v) => v >>> 24 === 255)).toBe(true);
    // deterministic
    const again = new WorldRenderer(m, NATIVE_W, NATIVE_H).render(0, 0, 0, [{ sprite: 'player_down', x: 100, y: 100 }]);
    expect(again.px).toEqual(fr.px);
  });
  it('every building theme and every character the overworld draws is in the sprite data', () => {
    for (const t of ['debug', 'industrial-robotic', 'farm', 'business', 'pagodas-nature']) expect(SPRITES.has(`bld_${t}`)).toBe(true);
    for (const d of ['up', 'down', 'left', 'right']) expect(sprite(`player_${d}`).frames).toHaveLength(4);
    expect(sprite('guide').frames.length).toBeGreaterThan(1);
  });
});

describe('camera', () => {
  const b = { x: 160, y: 0, w: 800, h: 600 };
  it('centres on the target in whole pixels', () => expect(follow(500.4, 300.6, 256, 224, b)).toEqual({ x: 372, y: 189 }));
  it('stops at the region bounds', () => {
    expect(follow(0, 0, 256, 224, b)).toEqual({ x: 160, y: 0 });
    expect(follow(5000, 5000, 256, 224, b)).toEqual({ x: 160 + 800 - 256, y: 600 - 224 });
  });
  it('centres a region smaller than the view', () => expect(follow(0, 0, 256, 224, { x: 0, y: 0, w: 100, h: 100 })).toEqual({ x: -78, y: -62 }));
  it('scales by whole numbers only, at least 1x', () => {
    expect(integerScale(1920, 1080, 256, 224)).toBe(4);
    expect(integerScale(800, 700, 256, 224)).toBe(3);
    expect(integerScale(300, 200, 256, 224)).toBe(1);
  });
});

/** A small hand-made map for the renderer: grass everywhere, water in the bottom-left corner, the objects given. */
function fakeMap(objects: WorldObject[], theme = 'debug'): WorldMap {
  const w = 40, h = 30;
  const at = (x: number, y: number): Terrain | null => (x >= 0 && y >= 0 && x < w && y < h ? (x < 4 && y > 24 ? 'W' : 'G') : null);
  const region = { index: 0, x0: 0, y0: 0, w, h, theme };
  return { w, h, at, inside: (x: number, y: number) => at(x, y) !== null, doors: [], objects, regions: [region], regionAt: () => region } as unknown as WorldMap;
}
const at = (fr: Frame, x: number, y: number) => fr.px[y * fr.w + x]!;

describe('composing a frame', () => {
  it('draws the cells under the whole view, the last column too when the view does not start on a tile edge', () => {
    const m = new WorldMap([{ id: 1, name: 'A', latitude: 52, longitude: 4, theme: null, country: 'NL' }]);
    const r = new WorldRenderer(m, NATIVE_W, NATIVE_H);
    const right = [...r.render(16 * 20 + 5, 160, 0, []).px];
    const ref = [...r.render(16 * 20 + 21, 160, 0, []).px];
    for (let y = 0; y < NATIVE_H; y++) for (let x = NATIVE_W - 16; x < NATIVE_W; x++) expect(right[y * NATIVE_W + x]).toBe(ref[y * NATIVE_W + x - 16]);
  });
  it('static shadows darken the ground one ramp step', () => {
    const plain = [...new WorldRenderer(fakeMap([]), 64, 64).render(0, 0, 0, []).px];
    const r = new WorldRenderer(fakeMap([{ sprite: 'stone', x: 20, y: 10, shadow: [0, 30, 6, 2], kind: 'decor' }]), 64, 64);
    const fr = r.render(0, 0, 0, []);
    expect(at(fr, 20, 40)).toBe(PALETTE.darker[plain[40 * 64 + 20]!]);
  });
  it('actors and statics are drawn by base line: the lower one is in front', () => {
    const st = sprite('stone_big'), pl = sprite('player_down');
    const r = new WorldRenderer(fakeMap([{ sprite: 'stone_big', x: 30, y: 30, kind: 'decor' }]), 64, 64);
    const px = (s: typeof st, ax: number, ay: number, x: number, y: number) => {
      const i = x - (ax - s.ox), j = y - (ay - s.oy);
      return i >= 0 && j >= 0 && i < s.w && j < s.h ? s.frames[0]![j * s.w + i]! : 0;
    };
    for (const [ay, front] of [[27, false], [33, true]] as const) {
      const fr = r.render(0, 0, 0, [{ sprite: 'player_down', x: 31, y: ay }]);
      let both = 0;
      for (let y = 0; y < 64; y++)
        for (let x = 0; x < 64; x++) {
          const a = px(pl, 31, ay, x, y), b = px(st, 30, 30, x, y);
          if (!a || !b || a === b) continue;
          both++;
          expect(at(fr, x, y), `${front ? 'player' : 'stone'} in front at ${x},${y}`).toBe(front ? a : b);
        }
      expect(both).toBeGreaterThan(3);
    }
  });
  it('the top layer (labels, markers, sails, weather) is drawn over everything', () => {
    const objs: WorldObject[] = [{ sprite: 'stone_big', x: 30, y: 30, kind: 'decor' }, { sprite: 'marker', x: 30, y: 28, top: true, kind: 'marker' }];
    const fr = new WorldRenderer(fakeMap(objs), 64, 64).render(0, 0, 0, [{ sprite: 'player_down', x: 30, y: 34 }]);
    const mk = sprite('marker');
    const f0 = mk.frames[0]!;
    for (let j = 0; j < mk.h; j++)
      for (let i = 0; i < mk.w; i++) if (f0[j * mk.w + i]) expect(at(fr, 30 - mk.ox + i, 28 - mk.oy + j)).toBe(f0[j * mk.w + i]);
  });
  it('water animates over time; reduce motion holds it still', () => {
    const r = new WorldRenderer(fakeMap([]), 64, 64);
    const water = (t: number, still = false) => [...r.render(0, 400, t, [], still).px].slice(0, 64 * 64);
    expect(water(0)).not.toEqual(water(TILESET.frameMs));
    expect(water(0, true)).toEqual(water(TILESET.frameMs, true));
  });
  it('an object is found from every part of the view its sprite reaches, not only from its anchor', () => {
    // a building anchored well right of the view, whose left wall reaches into it
    const b = sprite('bld_debug');
    const ax = 300, left = ax - b.ox;
    const r = new WorldRenderer(fakeMap([{ sprite: 'bld_debug', x: ax, y: 200, kind: 'building' }]), 128, 128);
    const cx = left + 8 - 128;
    const fr = [...r.render(cx, 120, 0, []).px];
    const bare = [...new WorldRenderer(fakeMap([]), 128, 128).render(cx, 120, 0, []).px];
    let drawn = 0;
    for (let y = 0; y < 128; y++) for (let x = 120; x < 128; x++) if (fr[y * 128 + x] !== bare[y * 128 + x]) drawn++;
    expect(drawn).toBeGreaterThan(20);
  });
  it('each theme draws its ground in its own palette; the plain theme keeps the art direction\'s colours', () => {
    expect(groundLut('debug', true)).toBeNull();
    const frames = ['debug', 'farm', 'industrial-robotic', 'business', 'pagodas-nature'].map((t) => [...new WorldRenderer(fakeMap([{ sprite: 'tree_a', x: 30, y: 40, kind: 'tree' }], t), 64, 64).render(0, 0, 0, []).px].join());
    expect(new Set(frames).size).toBe(5);
    for (const t of ['farm', 'industrial-robotic', 'business', 'pagodas-nature']) {
      const lut = groundLut(t, true)!;
      // a remap stays inside the palette and never touches water
      expect(Math.max(...lut)).toBeLessThan(PALETTE.colours.length);
      for (const b of ['b0', 'b1', 'b2', 'b3', 'b4', 'b5']) expect(lut[PALETTE.index(b)]).toBe(PALETTE.index(b));
    }
  });
});

