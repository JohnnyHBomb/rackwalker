import { describe, expect, it } from 'vitest';
import { SLOT_SPECS, createPixelBuffer, fillDiamond, setPixel, type PixelBuffer } from '../src/contracts/theme';
import {
  bayer,
  checker,
  clearPixel,
  dither,
  fillDither,
  fillGradient,
  fillPolygon,
  getPixel,
  hash2,
  innerOutline,
  isOpaque,
  isoBox,
  isoPoint,
  isoPrism,
  lattice,
  outline,
  pick,
  ramp,
  rampAt,
  rampDither,
  rng,
  seedOf,
  speckle,
  valueNoise,
} from '../src/themes/kit';
import { debugTheme } from '../src/themes/debug';

const A = '#102030', B = '#a0b0c0', C = '#ff8800';

function colours(buf: PixelBuffer): Map<string, number> {
  const m = new Map<string, number>();
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++) {
      const c = getPixel(buf, x, y);
      if (c) m.set(c, (m.get(c) ?? 0) + 1);
    }
  return m;
}
const opaqueCount = (buf: PixelBuffer) => [...colours(buf).values()].reduce((a, b) => a + b, 0);
const same = (a: PixelBuffer, b: PixelBuffer) => a.width === b.width && a.height === b.height && a.data.every((v, i) => v === b.data[i]);

describe('pixel access', () => {
  it('getPixel / isOpaque / clearPixel round-trip and ignore out-of-range coordinates', () => {
    const b = createPixelBuffer(4, 4);
    setPixel(b, 1, 2, '#0a0b0c');
    expect(getPixel(b, 1, 2)).toBe('#0a0b0c');
    expect(isOpaque(b, 1, 2)).toBe(true);
    expect(getPixel(b, 0, 0)).toBeNull();
    expect(getPixel(b, -1, 0)).toBeNull();
    expect(isOpaque(b, 4, 0)).toBe(false);
    clearPixel(b, 1, 2);
    clearPixel(b, 9, 9);
    expect(isOpaque(b, 1, 2)).toBe(false);
  });
});

describe('palette ramps', () => {
  const r = ramp(debugTheme.palette, ['outline', 'metal_shade', 'metal', 'highlight']);
  it('ramp takes colours from palette keys, in order', () => {
    expect(r).toEqual([debugTheme.palette.outline, debugTheme.palette.metal_shade, debugTheme.palette.metal, debugTheme.palette.highlight]);
    expect(() => ramp(debugTheme.palette, [])).toThrow();
  });
  it('rampAt picks the nearest step and clamps', () => {
    expect(rampAt(r, 0)).toBe(r[0]);
    expect(rampAt(r, 1)).toBe(r[3]);
    expect(rampAt(r, 0.34)).toBe(r[1]);
    expect(rampAt(r, -5)).toBe(r[0]);
    expect(rampAt(r, 9)).toBe(r[3]);
    expect(rampAt(r, Number.NaN)).toBe(r[0]);
  });
  it('rampDither only uses the two neighbouring steps, in proportion', () => {
    const counts = new Map<string, number>();
    for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) {
      const c = rampDither(r, 1 / 3 + (1 / 3) * 0.25, x, y);
      counts.set(c, (counts.get(c) ?? 0) + 1);
    }
    expect(counts.get(r[1]!)).toBe(12);
    expect(counts.get(r[2]!)).toBe(4);
    expect(rampDither(r, 1, 3, 3)).toBe(r[3]);
    expect(rampDither([A], 0.5, 0, 0)).toBe(A);
  });
  it('fillGradient runs from the first step (top) to the last (bottom)', () => {
    const b = createPixelBuffer(4, 9);
    fillGradient(b, 0, 0, 4, 9, r);
    for (let x = 0; x < 4; x++) {
      expect(getPixel(b, x, 0)).toBe(r[0]);
      expect(getPixel(b, x, 8)).toBe(r[3]);
    }
    expect([...colours(b).keys()].every((c) => r.includes(c))).toBe(true);
  });
});

describe('ordered dithering', () => {
  it('the 4x4 thresholds are 16 distinct values in (0, 1), tiling every 4 px (also for negative coordinates)', () => {
    const t = new Set<number>();
    for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) t.add(bayer(x, y));
    expect(t.size).toBe(16);
    for (const v of t) expect(v > 0 && v < 1).toBe(true);
    expect(bayer(5, 6)).toBe(bayer(1, 2));
    expect(bayer(-3, -2)).toBe(bayer(1, 2));
  });
  it('t = 0 is all a, t = 1 all b, t = k/16 exactly k pixels of b per 4x4 block', () => {
    for (let k = 0; k <= 16; k++) {
      const b = createPixelBuffer(8, 8);
      fillDither(b, 0, 0, 8, 8, A, B, k / 16);
      expect(colours(b).get(B) ?? 0).toBe(4 * k);
    }
  });
  it('checker alternates', () => {
    expect(checker(0, 0, A, B)).toBe(A);
    expect(checker(1, 0, A, B)).toBe(B);
    expect(checker(1, 1, A, B)).toBe(A);
    expect(dither(0, 0, 0.5, A, B)).toBe(dither(4, 4, 0.5, A, B));
  });
});

describe('outline pass', () => {
  const dot = () => {
    const b = createPixelBuffer(5, 5);
    setPixel(b, 2, 2, A);
    return b;
  };
  it('4-neighbour outline surrounds a pixel with 4 outline pixels and keeps it', () => {
    const b = dot();
    outline(b, B);
    expect(getPixel(b, 2, 2)).toBe(A);
    expect(colours(b).get(B)).toBe(4);
    expect(getPixel(b, 1, 1)).toBeNull();
  });
  it('diagonal outline uses all 8 neighbours', () => {
    const b = dot();
    outline(b, B, { diagonal: true });
    expect(colours(b).get(B)).toBe(8);
  });
  it('does not depend on scan order (one pass grows by exactly one pixel)', () => {
    const b = dot();
    outline(b, B);
    outline(b, C);
    expect(colours(b).get(C)).toBe(8); // ring of the 5-pixel plus: 8 pixels at Manhattan distance 2
  });
  it('skip leaves pixels that only touch the skip colour alone', () => {
    const b = dot();
    outline(b, B);
    outline(b, C, { skip: B });
    expect(colours(b).get(C)).toBeUndefined();
  });
  it('innerOutline recolours the edge pixels only', () => {
    const b = createPixelBuffer(5, 5);
    for (let y = 0; y < 5; y++) for (let x = 0; x < 5; x++) setPixel(b, x, y, A);
    innerOutline(b, B);
    expect(colours(b).get(B)).toBe(16);
    expect(getPixel(b, 2, 2)).toBe(A);
  });
});

describe('polygons and iso shapes', () => {
  it('fillPolygon fills a rectangle exactly (pixel-centre rule)', () => {
    const b = createPixelBuffer(10, 10);
    fillPolygon(b, [[2, 3], [7, 3], [7, 8], [2, 8]], A);
    expect(opaqueCount(b)).toBe(25);
    expect(getPixel(b, 2, 3)).toBe(A);
    expect(getPixel(b, 6, 7)).toBe(A);
    expect(getPixel(b, 7, 7)).toBeNull();
    fillPolygon(b, [[0, 0], [1, 1]], B); // degenerate: nothing
    expect(colours(b).get(B)).toBeUndefined();
  });
  it('fillPolygon accepts a per-pixel paint', () => {
    const b = createPixelBuffer(4, 4);
    fillPolygon(b, [[0, 0], [4, 0], [4, 4], [0, 4]], (x, y) => checker(x, y, A, B));
    expect(colours(b).get(A)).toBe(8);
  });
  it('isoPoint maps tiles to the 32 x 16 diamond', () => {
    expect(isoPoint(16, 0, 1, 0)).toEqual([32, 8]);
    expect(isoPoint(16, 0, 0, 1)).toEqual([0, 8]);
    expect(isoPoint(16, 0, 1, 1, 4)).toEqual([16, 12]);
  });
  it('isoBox: the top face is the fillDiamond tile raised by height, the sides fill the columns below it', () => {
    const { width, height } = SLOT_SPECS.rack_empty;
    const b = createPixelBuffer(width, height);
    isoBox(b, 16, 48, 1, 1, 20, { top: A, left: B, right: C });
    const top = createPixelBuffer(width, height);
    fillDiamond(top, 16, 28, 1, 1, A);
    for (let y = 0; y < height; y++)
      for (let x = 0; x < width; x++) if (isOpaque(top, x, y)) expect(getPixel(b, x, y)).toBe(A);
    const c = colours(b);
    expect(c.get(A)).toBe(288); // 16 rows of 4..32..4
    expect(c.get(B)).toBe(16 * 20); // 16 left columns, each 20 px
    expect(c.get(C)).toBe(16 * 20);
    // Bottom corner of the box sits on the tile's bottom corner (ground point + 16 rows - 1).
    expect(isOpaque(b, 15, 48 + 15)).toBe(true);
    expect(isOpaque(b, 15, 48 + 16)).toBe(false);
  });
  it('isoBox of height 0 is exactly the floor tile; per-pixel paints work on every face', () => {
    const a = createPixelBuffer(32, 16), d = createPixelBuffer(32, 16);
    isoBox(a, 16, 0, 1, 1, 0, { top: A, left: B, right: C });
    fillDiamond(d, 16, 0, 1, 1, A);
    expect(same(a, d)).toBe(true);
    const p = createPixelBuffer(64, 80);
    isoBox(p, 32, 48, 2, 2, 30, { top: (x, y) => checker(x, y, A, B), left: (x, y) => dither(x, y, 0.25, B, C), right: C });
    expect(new Set(colours(p).keys())).toEqual(new Set([A, B, C]));
  });
  it('isoPrism uses only its four paints, stays inside its footprint column and above the ground, both ridges', () => {
    for (const ridge of ['x', 'y'] as const) {
      const b = createPixelBuffer(64, 80);
      isoPrism(b, 32, 48, 2, 2, 24, 16, ridge, { left: A, right: B, roofNear: C, roofFar: '#445566' });
      const c = colours(b);
      expect([...c.keys()].every((k) => [A, B, C, '#445566'].includes(k)), ridge).toBe(true);
      expect(c.get(A)! > 100 && c.get(B)! > 100 && c.get(C)! > 100, ridge).toBe(true);
      for (let y = 0; y < 80; y++) for (let x = 0; x < 64; x++) if (isOpaque(b, x, y)) expect(y).toBeLessThanOrEqual(48 + 32);
      expect(isOpaque(b, 31, 48 + 31)).toBe(true); // bottom corner on the ground
      expect(isOpaque(b, 31, 48 - 24 - 16 - 2)).toBe(false); // nothing above the ridge
    }
  });
});

describe('lattice', () => {
  it('grid lines every step, iso lines at 2:1, diagonal at 1:1, optional opaque mask', () => {
    const g = createPixelBuffer(8, 8);
    lattice(g, 0, 0, 8, 8, 4, 'grid', A);
    expect(opaqueCount(g)).toBe(64 - 36);
    const iso = createPixelBuffer(16, 8);
    lattice(iso, 0, 0, 16, 8, 8, 'iso', A);
    expect(isOpaque(iso, 2, 3)).toBe(true); // 2 + 2*3 = 8
    expect(isOpaque(iso, 4, 2)).toBe(true); // 4 - 2*2 = 0
    const dg = createPixelBuffer(8, 8);
    lattice(dg, 0, 0, 8, 8, 4, 'diag', A);
    expect(isOpaque(dg, 1, 3)).toBe(true);
    const m = createPixelBuffer(8, 8);
    setPixel(m, 4, 4, B);
    lattice(m, 0, 0, 8, 8, 4, 'grid', A, { onOpaque: true });
    expect(opaqueCount(m)).toBe(1);
    expect(getPixel(m, 4, 4)).toBe(A);
  });
});

describe('seeded noise', () => {
  it('hash2 is deterministic, in [0, 1), and depends on seed and position', () => {
    expect(hash2(1, 2, 3)).toBe(hash2(1, 2, 3));
    expect(hash2(1, 2, 3)).not.toBe(hash2(2, 2, 3));
    expect(hash2(1, 2, 3)).not.toBe(hash2(1, 3, 2));
    let sum = 0;
    for (let i = 0; i < 4096; i++) {
      const v = hash2(42, i % 64, i >> 6);
      expect(v >= 0 && v < 1).toBe(true);
      sum += v;
    }
    expect(Math.abs(sum / 4096 - 0.5)).toBeLessThan(0.03);
  });
  it('rng repeats per seed and differs between seeds', () => {
    const a = rng(7), b = rng(7), c = rng(8);
    const sa = [a(), a(), a()];
    expect([b(), b(), b()]).toEqual(sa);
    expect([c(), c(), c()]).not.toEqual(sa);
  });
  it('valueNoise is smooth, deterministic and in [0, 1)', () => {
    for (let x = 0; x < 32; x++) {
      const v = valueNoise(3, x, 5, 8);
      expect(v >= 0 && v < 1).toBe(true);
      expect(Math.abs(valueNoise(3, x + 1, 5, 8) - v)).toBeLessThan(0.5);
    }
    expect(valueNoise(3, 8, 8, 8)).toBe(hash2(3, 1, 1)); // lattice points are the hashed values
  });
  it('pick and seedOf', () => {
    expect(pick(5, ['a', 'b', 'c'])).toBe(pick(5, ['a', 'b', 'c']));
    expect(new Set(Array.from({ length: 50 }, (_, s) => pick(s, ['a', 'b', 'c']))).size).toBe(3);
    expect(() => pick(1, [])).toThrow();
    const spec = SLOT_SPECS.room_floor;
    expect(seedOf({ palette: debugTheme.palette, spec, variant: {} })).toBe(0);
    expect(seedOf({ palette: debugTheme.palette, spec, variant: { seed: -1 } })).toBe(4294967295);
    expect(seedOf({ palette: debugTheme.palette, spec, variant: { seed: Number.NaN } })).toBe(0);
  });
  it('speckle only touches opaque pixels, about `density` of them', () => {
    const b = createPixelBuffer(32, 32);
    for (let y = 0; y < 32; y++) for (let x = 0; x < 16; x++) setPixel(b, x, y, A);
    speckle(b, 9, 0, 0, 32, 32, 0.25, B);
    const n = colours(b).get(B) ?? 0;
    expect(n).toBeGreaterThan(80);
    expect(n).toBeLessThan(180);
    for (let y = 0; y < 32; y++) for (let x = 16; x < 32; x++) expect(isOpaque(b, x, y)).toBe(false);
  });
});
