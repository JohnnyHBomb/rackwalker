// Theme conformance: every registered theme fills all 21 contract slots at the declared size and anchor, draws only
// its own palette colours (plus the NetBox role/cable colour the variant passes in), never draws a fully transparent
// sprite, and renders deterministically for a given variant (seed included). Artists: run
//   npx vitest run tests/theme-conformance.test.ts
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { PALETTE_KEYS, SLOT_IDS, SLOT_SPECS, THEME_IDS, renderSlot, type PixelBuffer, type SlotId, type SlotSpec, type SlotVariant, type Theme } from '../src/contracts/theme';
import { THEMES, THEME_LIST, sampleVariants } from '../src/themes/index';

// Palettes of existing consoles must not be borrowed: the PICO-8 16 and the classic 4-green
// handheld ramp are listed as known offenders; the debug theme used to copy the first.
const BORROWED = new Set([
  '#000000', '#1d2b53', '#7e2553', '#008751', '#ab5236', '#5f574f', '#c2c3c7', '#fff1e8',
  '#ff004d', '#ffa300', '#ffec27', '#00e436', '#29adff', '#83769c', '#ff77a8', '#ffccaa',
  '#0f380f', '#306230', '#8bac0f', '#9bbc0f',
]);

/** Each channel within this many steps of a borrowed colour counts as nearly that colour (previously: a palette one
 *  step off each PICO-8 colour passed the exact-match check). */
const NEAR = 8;
/** Near matches tolerated per palette: one or two creams or near-blacks next to a console's own are coincidence
 *  (industrial's highlight #fff9e4 and pagoda's text #fbf6ea sit near PICO-8's #fff1e8); three are a borrowed palette. */
const NEAR_ALLOWED = 2;
const rgb = (c: string) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
/** Palette colours that copy a known console palette: every exact match, and the near matches once there are more
 *  than NEAR_ALLOWED of them; each as "key #colour ~ #borrowed". */
function borrowedColours(palette: Record<string, string>): string[] {
  const exact: string[] = [];
  const near: string[] = [];
  for (const [k, raw] of Object.entries(palette)) {
    const c = raw.toLowerCase();
    const a = rgb(c);
    if (BORROWED.has(c)) exact.push(`${k} ${c} ~ ${c}`);
    else for (const b of BORROWED) if (rgb(b).every((v, i) => Math.abs(v - a[i]!) <= NEAR)) near.push(`${k} ${c} ~ ${b}`);
  }
  return near.length > NEAR_ALLOWED ? [...exact, ...near] : exact;
}

const hex = (d: Uint8ClampedArray, i: number) => `#${((d[i]! << 16) | (d[i + 1]! << 8) | d[i + 2]!).toString(16).padStart(6, '0')}`;

/** Highest and lowest ground row per column of the footprint diamond whose top corner is the anchor (as fillDiamond draws it). */
function groundEnvelope(spec: SlotSpec): { high: Map<number, number>; low: Map<number, number> } {
  const [ax, ay] = spec.anchor;
  const [fw, fh] = spec.footprint!;
  const w8 = fw * 8, h8 = fh * 8;
  const reach = (r: number, n8: number) => (r < n8 ? 2 * (r + 1) : 2 * n8 - 2 * (r - n8));
  const low = new Map<number, number>(), high = new Map<number, number>();
  for (let r = 0; r < w8 + h8; r++)
    for (let x = ax - reach(r, h8); x < ax + reach(r, w8); x++) {
      low.set(x, ay + r);
      if (!high.has(x)) high.set(x, ay + r);
    }
  return { high, low };
}

interface Shape {
  opaque: number;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}
function shapeOf(buf: PixelBuffer): Shape {
  const s = { opaque: 0, minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++)
      if (buf.data[(y * buf.width + x) * 4 + 3]! > 0) {
        s.opaque++;
        s.minX = Math.min(s.minX, x), s.maxX = Math.max(s.maxX, x), s.minY = Math.min(s.minY, y), s.maxY = Math.max(s.maxY, y);
      }
  return s;
}

/** Problems of one rendered sprite (empty = conforms). */
function problems(theme: Theme, slot: SlotId, v: SlotVariant, buf: PixelBuffer): string[] {
  const spec = SLOT_SPECS[slot];
  const out: string[] = [];
  const where = `${theme.id}/${slot} ${JSON.stringify(v)}`;
  if (buf.width !== spec.width || buf.height !== spec.height || buf.data.length !== spec.width * spec.height * 4) out.push(`${where}: size changed`);
  const allowed = new Set<string>(Object.values(theme.palette));
  if (v.role_color) allowed.add(v.role_color.toLowerCase());
  if (v.wire_color) allowed.add(v.wire_color.toLowerCase());
  const bad = new Set<string>();
  for (let i = 0; i < buf.data.length; i += 4) {
    const a = buf.data[i + 3]!;
    if (a === 0) continue;
    if (a !== 255) bad.add(`alpha ${a}`);
    const c = hex(buf.data, i);
    if (!allowed.has(c)) bad.add(c);
  }
  if (bad.size) out.push(`${where}: colours not in its palette or variant: ${[...bad].slice(0, 5).join(', ')}`);
  const s = shapeOf(buf);
  if (s.opaque === 0) return [...out, `${where}: fully transparent`];

  const [ax, ay] = spec.anchor;
  if (spec.space === 'world') {
    // Stands on its anchor: something is drawn on the footprint's ground diamond, nothing hangs below it or outside
    // its columns (2 px slack for outlines and the 1 px larger selection ring).
    const { high, low } = groundEnvelope(spec);
    const SLACK = 2;
    let onGround = false;
    for (let y = 0; y < buf.height; y++)
      for (let x = 0; x < buf.width; x++) {
        if (buf.data[(y * buf.width + x) * 4 + 3] === 0) continue;
        const g = low.get(x) ?? low.get(x - SLACK) ?? low.get(x + SLACK);
        if (g === undefined) {
          out.push(`${where}: pixel (${x},${y}) outside the footprint's columns`);
          return out;
        }
        if (y > Math.max(g, low.get(x - 1) ?? g, low.get(x + 1) ?? g) + SLACK) {
          out.push(`${where}: pixel (${x},${y}) below the ground of anchor (${ax},${ay})`);
          return out;
        }
        if (low.has(x) && y >= high.get(x)! && y <= low.get(x)!) onGround = true;
      }
    if (!onGround) out.push(`${where}: nothing drawn on the footprint at anchor (${ax},${ay})`);
  } else {
    // Rack and screen slots fill their slot from the anchor: the drawing reaches within 1 px of every edge.
    const x0 = slot === 'rack_unit' ? 0 : ax;
    if (s.minX > x0 + 1 || s.minY > ay + 1 || s.maxX < buf.width - 2 || s.maxY < buf.height - 2)
      out.push(`${where}: does not fill its ${buf.width}x${buf.height} slot from anchor (${ax},${ay})`);
    if (slot === 'rack_unit') {
      // 8 px rails each side of the slot that starts at the anchor (x = 8).
      const col = (x: number) => Array.from({ length: buf.height }, (_, y) => buf.data[(y * buf.width + x) * 4 + 3]!).some((a) => a > 0);
      if (![0, 1, 2, 3, 4, 5, 6, 7].some(col) || ![72, 73, 74, 75, 76, 77, 78, 79].some(col)) out.push(`${where}: rails missing`);
    }
    if (slot === 'ui_frame') {
      // 9-slice: each 8 x 8 corner draws something.
      for (const [cx, cy] of [[0, 0], [16, 0], [0, 16], [16, 16]] as const) {
        let any = false;
        for (let y = cy; y < cy + 8; y++) for (let x = cx; x < cx + 8; x++) any ||= buf.data[(y * 24 + x) * 4 + 3]! > 0;
        if (!any) out.push(`${where}: empty 9-slice corner at ${cx},${cy}`);
      }
    }
  }
  return out;
}

const digest = (b: PixelBuffer) => {
  let h = 2166136261;
  for (let i = 0; i < b.data.length; i++) h = Math.imul(h ^ b.data[i]!, 16777619);
  return `${b.width}x${b.height}:${(h >>> 0).toString(16)}`;
};

/** Randomness and clocks throw while slots draw (installed per theme; vi.restoreAllMocks undoes it). */
function forbidHiddenInputs(): void {
  vi.spyOn(Math, 'random').mockImplementation(() => {
    throw new Error('Math.random in a theme: use kit.ts hash2/rng with variant.seed');
  });
  vi.spyOn(Date, 'now').mockImplementation(() => {
    throw new Error('Date.now in a theme: drawing must not depend on time');
  });
}

/** Every sample variant drawn forward, then again in reverse order: the slots whose pixels changed (hidden state). */
function nondeterministicSlots(theme: Theme): string[] {
  const jobs = SLOT_IDS.flatMap((slot) => sampleVariants(slot).map((v) => ({ slot, v })));
  const forward = jobs.map((j) => digest(renderSlot(theme, j.slot, j.v)));
  const backward = [...jobs].reverse().map((j) => digest(renderSlot(theme, j.slot, j.v))).reverse();
  return [...new Set(jobs.filter((_, i) => backward[i] !== forward[i]).map((j) => j.slot))];
}

describe('theme registry', () => {
  it('registers every contract theme id under its own id', () => {
    expect(Object.keys(THEMES).sort()).toEqual([...THEME_IDS].sort());
    for (const id of THEME_IDS) expect(THEMES[id].id).toBe(id);
    expect(THEME_LIST.map((t) => t.id)).toEqual([...THEME_IDS]);
  });
  it('every theme has its own palette, and no palette borrows a known console palette colour', () => {
    const seen = new Map<string, string>();
    for (const t of THEME_LIST) {
      const key = PALETTE_KEYS.map((k) => t.palette[k]).join();
      expect(seen.get(key), `${t.id} shares its palette with ${seen.get(key)}`).toBeUndefined();
      seen.set(key, t.id);
      expect(borrowedColours(t.palette), `${t.id} borrows`).toEqual([]);
    }
  });
});

describe.each(THEME_LIST.map((t) => [t.id, t] as const))('theme %s conforms', (_id, theme) => {
  // Hidden nondeterminism (randomness, clocks) fails loudly while slots draw.
  beforeAll(() => forbidHiddenInputs());
  afterAll(() => vi.restoreAllMocks());

  it('defines all 16 palette keys as lower-case #rrggbb', () => {
    expect(Object.keys(theme.palette).sort()).toEqual([...PALETTE_KEYS].sort());
    for (const k of PALETTE_KEYS) expect(theme.palette[k], k).toMatch(/^#[0-9a-f]{6}$/);
  });

  it('fills all 21 slots with a draw function', () => {
    expect(Object.keys(theme.slots).sort()).toEqual([...SLOT_IDS].sort());
    for (const id of SLOT_IDS) expect(typeof theme.slots[id], id).toBe('function');
  });

  it.each([...SLOT_IDS])('%s: declared size and anchor, own palette only, never empty, for every sample variant', (slot) => {
    const errs: string[] = [];
    for (const v of sampleVariants(slot)) errs.push(...problems(theme, slot, v, renderSlot(theme, slot, v)));
    expect(errs).toEqual([]);
  });

  it('renders deterministically: same variant, same pixels, whatever was drawn before', () => {
    expect(nondeterministicSlots(theme)).toEqual([]);
  });
});

describe('the conformance checks catch broken themes', () => {
  const base = THEMES.debug;
  const broken = (slot: SlotId, draw: Theme['slots'][SlotId]): Theme => ({ ...base, slots: { ...base.slots, [slot]: draw } });
  const check = (t: Theme, slot: SlotId, v: SlotVariant = {}) => problems(t, slot, v, renderSlot(t, slot, v)).join('\n');

  it('a colour outside the palette', () => {
    const t = broken('room_floor', (buf, ctx) => {
      base.slots.room_floor(buf, ctx);
      buf.data.set([1, 2, 3, 255], (8 * 32 + 16) * 4);
    });
    expect(check(t, 'room_floor')).toMatch(/not in its palette.*#010203/);
    // ...but the variant's own NetBox colour is allowed.
    expect(check(base, 'device_1u', { role_color: '#010203' })).toBe('');
  });
  it('a colour outside the palette is caught for variants that carry a NetBox role or cable colour too', () => {
    const t = broken('device_1u', (buf, ctx) => {
      base.slots.device_1u(buf, ctx);
      buf.data.set([1, 2, 3, 255], (2 * buf.width + 2) * 4);
    });
    expect(check(t, 'device_1u', { role_color: '#2e8b6e' })).toMatch(/not in its palette.*#010203/);
    expect(check(t, 'device_1u', { wire_color: '#c4622d' })).toMatch(/not in its palette.*#010203/);
  });
  it('a borrowed console palette, exact or a few steps off each colour', () => {
    const near = { ...base.palette, floor: '#5f5750', floor_alt: '#ab5237', wall: '#c2c3c8', accent: '#ffa301', led_ok: '#00e437' };
    expect(borrowedColours(near)).toHaveLength(5);
    expect(borrowedColours({ ...base.palette, background: '#1d2b53' })).toEqual(['background #1d2b53 ~ #1d2b53']);
    expect(borrowedColours({ ...base.palette, background: '#1d2b54', floor: '#5f5750' })).toEqual([]); // two near: coincidence
    expect(borrowedColours({ ...base.palette, background: '#1d2b54', floor: '#5f5750', wall: '#c2c3c8' })).toHaveLength(3);
    expect(borrowedColours(base.palette)).toEqual([]);
  });
  it('a half-transparent pixel', () => {
    const t = broken('port', (buf, ctx) => {
      base.slots.port(buf, ctx);
      buf.data[3] = 128;
    });
    expect(check(t, 'port')).toMatch(/alpha 128/);
  });
  it('a fully transparent sprite', () => {
    expect(check(broken('cursor', () => undefined), 'cursor')).toMatch(/fully transparent/);
  });
  it('a world sprite drawn off its anchor (below the ground, or not standing on its footprint)', () => {
    // room_wall stands on the N edge line from (0,32) to (15,39): a pixel at (0,39) hangs below it.
    const below = broken('room_wall', (buf, ctx) => {
      base.slots.room_wall(buf, ctx);
      buf.data.set([0x0c, 0x10, 0x14, 255], (39 * 16 + 0) * 4);
    });
    expect(base.palette.outline).toBe('#0c1014');
    expect(check(below, 'room_wall')).toMatch(/pixel \(0,39\) below the ground/);
    const floating = broken('decoration_large', (buf) => void buf.data.set([0x0c, 0x10, 0x14, 255], (2 * 32 + 16) * 4));
    expect(check(floating, 'decoration_large')).toMatch(/nothing drawn on the footprint/);
  });
  it('a faceplate that does not fill its U slot, a rack unit without rails, a 9-slice frame with an empty corner', () => {
    const half = broken('switch', (buf, ctx) => base.slots.switch({ width: 32, height: 8, data: buf.data.subarray(0) } as PixelBuffer, ctx));
    expect(check(half, 'switch')).toMatch(/does not fill/);
    // ...nor when it starts right or below of its slot (left columns or top rows empty).
    const clear = (cols: (x: number, y: number) => boolean) =>
      broken('switch', (buf, ctx) => {
        base.slots.switch(buf, ctx);
        for (let y = 0; y < buf.height; y++) for (let x = 0; x < buf.width; x++) if (cols(x, y)) buf.data[(y * buf.width + x) * 4 + 3] = 0;
      });
    expect(check(clear((x) => x < 3), 'switch')).toMatch(/does not fill/);
    expect(check(clear((_x, y) => y < 3), 'switch')).toMatch(/does not fill/);
    expect(check(clear((x) => x < 1), 'switch')).toBe(''); // 1 px slack
    const noRails = broken('rack_unit', (buf, ctx) => {
      base.slots.rack_unit(buf, ctx);
      for (let y = 0; y < 8; y++) for (const x of [0, 1, 2, 3, 4, 5, 6, 7, 72, 73, 74, 75, 76, 77, 78, 79]) buf.data[(y * 80 + x) * 4 + 3] = 0;
    });
    expect(check(noRails, 'rack_unit')).toMatch(/rails missing/);
    const corner = broken('ui_frame', (buf, ctx) => {
      base.slots.ui_frame(buf, ctx);
      for (let y = 16; y < 24; y++) for (let x = 0; x < 8; x++) buf.data[(y * 24 + x) * 4 + 3] = 0;
    });
    expect(check(corner, 'ui_frame')).toMatch(/empty 9-slice corner at 0,16/);
    for (const [cx, cy] of [[0, 0], [16, 0], [16, 16]] as const) {
      const t = broken('ui_frame', (buf, ctx) => {
        base.slots.ui_frame(buf, ctx);
        for (let y = cy; y < cy + 8; y++) for (let x = cx; x < cx + 8; x++) buf.data[(y * 24 + x) * 4 + 3] = 0;
      });
      expect(check(t, 'ui_frame')).toMatch(new RegExp(`empty 9-slice corner at ${cx},${cy}`));
    }
  });
  it('hidden state (a draw that depends on earlier draws) fails the determinism check', () => {
    // A slot that, on every other call, also sets one pixel: the same variant no longer gives the same pixels.
    let calls = 0;
    const t = broken('room_floor', (buf, ctx) => {
      base.slots.room_floor(buf, ctx);
      if (calls++ % 2) buf.data.set([0x0c, 0x10, 0x14, 255], (8 * 32 + 16) * 4);
    });
    expect(nondeterministicSlots(t)).toEqual(['room_floor']);
    expect(nondeterministicSlots(base)).toEqual([]);
  });
  it('Math.random or Date.now in a slot throws while the guards are on', () => {
    forbidHiddenInputs();
    try {
      const rnd = broken('port', (buf, ctx) => {
        base.slots.port(buf, ctx);
        buf.data[3] = Math.random() < 2 ? 255 : 0;
      });
      expect(() => renderSlot(rnd, 'port', {})).toThrow(/Math.random in a theme/);
      const clock = broken('port', (buf, ctx) => {
        base.slots.port(buf, ctx);
        buf.data[3] = Date.now() > 0 ? 255 : 0;
      });
      expect(() => renderSlot(clock, 'port', {})).toThrow(/Date.now in a theme/);
      expect(() => renderSlot(base, 'port', {})).not.toThrow();
    } finally {
      vi.restoreAllMocks();
    }
  });
});
