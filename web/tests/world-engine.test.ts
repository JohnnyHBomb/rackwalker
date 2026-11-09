// The tile engine: indexed framebuffer composition (tiles, sprites, shadows by palette remap), the autotile set covers
// every key a generated map uses, and the camera follows in whole pixels inside the region.
import { describe, expect, it } from 'vitest';
import { Frame, PALETTE, SPRITES, sprite, TILESET, tileKey, NATIVE_W, NATIVE_H } from '../src/game/world/engine';
import { follow, integerScale } from '../src/game/world/camera';
import { WorldMap } from '../src/game/world/gen';
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
    expect(tileKey('G', 'W', 'S', 'F', 'C', 1)).toBe('GLLLL1');
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
