// Shared pixel-art helpers for themes: palette ramps, ordered dithering, an outline pass, iso boxes and gabled
// prisms, lattices and seeded noise. Everything here is pure and deterministic (no Math.random, no clock), and every
// colour it draws is one the caller passes in, so a theme that only passes palette colours stays inside its palette
// (the conformance test in tests/theme-conformance.test.ts checks exactly that).
import { fillDiamond, setPixel, type DrawContext, type Palette, type PaletteKey, type PixelBuffer } from '../contracts/theme';

// ------------------------------------------------------------------------------------------------ pixel access

export function inBounds(buf: PixelBuffer, x: number, y: number): boolean {
  return Number.isInteger(x) && Number.isInteger(y) && x >= 0 && y >= 0 && x < buf.width && y < buf.height;
}

export function isOpaque(buf: PixelBuffer, x: number, y: number): boolean {
  return inBounds(buf, x, y) && buf.data[(y * buf.width + x) * 4 + 3]! > 0;
}

/** The pixel's colour as '#rrggbb', or null when it is transparent or outside the buffer. */
export function getPixel(buf: PixelBuffer, x: number, y: number): string | null {
  if (!isOpaque(buf, x, y)) return null;
  const i = (y * buf.width + x) * 4;
  const n = (buf.data[i]! << 16) | (buf.data[i + 1]! << 8) | buf.data[i + 2]!;
  return `#${n.toString(16).padStart(6, '0')}`;
}

/** Makes a pixel transparent again (cut-outs: windows, notches). */
export function clearPixel(buf: PixelBuffer, x: number, y: number): void {
  if (inBounds(buf, x, y)) buf.data.fill(0, (y * buf.width + x) * 4, (y * buf.width + x) * 4 + 4);
}

// ------------------------------------------------------------------------------------------------ palette ramps

/** An ordered list of colours, dark to light, taken from palette keys (so shading never leaves the palette). */
export type Ramp = readonly string[];

export function ramp(palette: Palette, keys: readonly PaletteKey[]): Ramp {
  if (keys.length === 0) throw new Error('empty ramp');
  return keys.map((k) => palette[k]);
}

/** The ramp step nearest to t in [0, 1] (clamped). */
export function rampAt(r: Ramp, t: number): string {
  const i = Math.round(clamp01(t) * (r.length - 1));
  return r[i]!;
}

/** Ramp colour at t in [0, 1], with the in-between tones made by ordered dithering of the two nearest steps. */
export function rampDither(r: Ramp, t: number, x: number, y: number): string {
  const p = clamp01(t) * (r.length - 1);
  const i = Math.min(r.length - 2, Math.floor(p));
  if (r.length === 1) return r[0]!;
  return dither(x, y, p - i, r[i]!, r[i + 1]!);
}

const clamp01 = (t: number) => (Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0);

// ------------------------------------------------------------------------------------------------ dithering

// 4x4 ordered-dither threshold matrix (the standard recursive Bayer construction).
const BAYER4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5] as const;

/** Threshold in (0, 1) for pixel (x, y): (rank + 0.5) / 16. */
export function bayer(x: number, y: number): number {
  return (BAYER4[(((y % 4) + 4) % 4) * 4 + (((x % 4) + 4) % 4)]! + 0.5) / 16;
}

/** `b` where the mix t in [0, 1] beats the pixel's threshold, else `a`. t = 0 is all a, t = 1 all b. */
export function dither(x: number, y: number, t: number, a: string, b: string): string {
  return clamp01(t) > bayer(x, y) ? b : a;
}

/** 2-colour checkerboard (t = 0.5 dither), the commonest pixel-art half tone. */
export function checker(x: number, y: number, a: string, b: string): string {
  return (x + y) % 2 === 0 ? a : b;
}

export function fillDither(buf: PixelBuffer, x: number, y: number, w: number, h: number, a: string, b: string, t: number): void {
  for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) setPixel(buf, i, j, dither(i, j, t, a, b));
}

/** Vertical gradient over a rectangle: top row = ramp start, bottom row = ramp end, dithered in between. */
export function fillGradient(buf: PixelBuffer, x: number, y: number, w: number, h: number, r: Ramp): void {
  for (let j = 0; j < h; j++) {
    const t = h === 1 ? 0 : j / (h - 1);
    for (let i = 0; i < w; i++) setPixel(buf, x + i, y + j, rampDither(r, t, x + i, y + j));
  }
}

// ------------------------------------------------------------------------------------------------ outline pass

export interface OutlineOptions {
  /** Also outline across diagonals (8-neighbour). Default false: the classic 4-neighbour pixel-art outline. */
  readonly diagonal?: boolean;
  /** Only outline pixels whose neighbour is NOT this colour (keeps an outline from outlining itself twice). */
  readonly skip?: string;
}

/**
 * Outer outline: every transparent pixel that touches an opaque one becomes `hex`. Reads a snapshot, so the result
 * does not depend on scan order. Grows the sprite by one pixel; leave a 1 px margin inside the slot for it.
 */
export function outline(buf: PixelBuffer, hex: string, opts: OutlineOptions = {}): void {
  const { width: w, height: h } = buf;
  const alpha = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) alpha[i] = buf.data[i * 4 + 3]! > 0 ? 1 : 0;
  const skip = opts.skip?.toLowerCase();
  const nb = opts.diagonal
    ? [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]]
    : [[0, -1], [-1, 0], [1, 0], [0, 1]];
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      if (alpha[y * w + x]) continue;
      const touch = nb.some(([dx, dy]) => {
        const nx = x + dx!, ny = y + dy!;
        if (nx < 0 || ny < 0 || nx >= w || ny >= h || !alpha[ny * w + nx]) return false;
        return skip === undefined || getPixel(buf, nx, ny) !== skip;
      });
      if (touch) setPixel(buf, x, y, hex);
    }
}

/** Inner outline: every opaque pixel on the sprite's edge (touching transparency or the buffer edge) becomes `hex`. */
export function innerOutline(buf: PixelBuffer, hex: string): void {
  const { width: w, height: h } = buf;
  const edge: number[] = [];
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++)
      if (isOpaque(buf, x, y) && (!isOpaque(buf, x - 1, y) || !isOpaque(buf, x + 1, y) || !isOpaque(buf, x, y - 1) || !isOpaque(buf, x, y + 1)))
        edge.push(x, y);
  for (let i = 0; i < edge.length; i += 2) setPixel(buf, edge[i]!, edge[i + 1]!, hex);
}

// ------------------------------------------------------------------------------------------------ polygons

export type Pt = readonly [number, number];

/** Fills a polygon (screen pixels; a pixel is in when its centre is, even-odd rule). */
export function fillPolygon(buf: PixelBuffer, pts: readonly Pt[], hex: string | ((x: number, y: number) => string)): void {
  if (pts.length < 3) return;
  const ys = pts.map((p) => p[1]);
  const y0 = Math.max(0, Math.floor(Math.min(...ys)));
  const y1 = Math.min(buf.height - 1, Math.ceil(Math.max(...ys)));
  for (let y = y0; y <= y1; y++) {
    const cy = y + 0.5;
    const xs: number[] = [];
    for (let i = 0; i < pts.length; i++) {
      const [ax, ay] = pts[i]!;
      const [bx, by] = pts[(i + 1) % pts.length]!;
      if (ay === by || cy < Math.min(ay, by) || cy >= Math.max(ay, by)) continue;
      xs.push(ax + ((cy - ay) * (bx - ax)) / (by - ay));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      // pixel x is in when x + 0.5 lies in [xs[k], xs[k + 1])
      for (let x = Math.ceil(xs[k]! - 0.5); x + 0.5 < xs[k + 1]!; x++) setPixel(buf, x, y, typeof hex === 'string' ? hex : hex(x, y));
    }
  }
}

// ------------------------------------------------------------------------------------------------ iso shapes

/** Screen point of iso coordinates (tiles x, y; height z in px) for a sprite whose ground point is (ax, ay). */
export function isoPoint(ax: number, ay: number, x: number, y: number, z = 0): Pt {
  return [ax + (x - y) * 16, ay + (x + y) * 8 - z];
}

export interface BoxColors {
  /** Top face. */
  readonly top: Paint;
  /** Face on the +y side (screen left). */
  readonly left: Paint;
  /** Face on the +x side (screen right). */
  readonly right: Paint;
}

/**
 * An iso box standing on a `w` x `h`-tile footprint whose top corner (the ground point) is at (ax, ay), `height` px
 * tall. The top face is exactly `fillDiamond`'s diamond raised by `height`, so boxes line up with floor tiles.
 * Colours may be functions of the pixel for textured faces (dither, noise).
 */
export function isoBox(
  buf: PixelBuffer,
  ax: number,
  ay: number,
  w: number,
  h: number,
  height: number,
  c: BoxColors,
): void {
  const top = ay - height;
  const w8 = w * 8, h8 = h * 8;
  const reach = (r: number, n8: number) => (r < n8 ? 2 * (r + 1) : 2 * n8 - 2 * (r - n8));
  // Lowest row of the top diamond in every column, as fillDiamond draws it.
  const bottom = new Map<number, number>();
  for (let r = 0; r < w8 + h8; r++) for (let x = ax - reach(r, h8); x < ax + reach(r, w8); x++) bottom.set(x, top + r);
  const split = ax + (w - h) * 16; // the bottom corner's column: left face before it, right face from it
  for (const [x, yb] of bottom)
    for (let y = yb + 1; y <= yb + height; y++) setPixel(buf, x, y, paint(x < split ? c.left : c.right, x, y));
  if (typeof c.top === 'string') fillDiamond(buf, ax, top, w, h, c.top);
  else for (const [x, yb] of bottom) for (let y = top; y <= yb; y++) if (inTopDiamond(x, y)) setPixel(buf, x, y, c.top(x, y));
  function inTopDiamond(x: number, y: number): boolean {
    const r = y - top;
    return r >= 0 && r < w8 + h8 && x >= ax - reach(r, h8) && x < ax + reach(r, w8);
  }
}

/** A colour, or a colour chosen per pixel (dither, noise, lattice). */
export type Paint = string | ((x: number, y: number) => string);
const paint = (p: Paint, x: number, y: number) => (typeof p === 'string' ? p : p(x, y));

export interface PrismColors {
  /** Visible walls: +y side (screen left) and +x side (screen right). */
  readonly left: Paint;
  readonly right: Paint;
  /** Roof slope facing the viewer and the one facing away. */
  readonly roofNear: Paint;
  readonly roofFar: Paint;
}

/**
 * A gabled iso prism (a house): walls `height` px tall on a `w` x `h` footprint, roof ridge `rise` px above the
 * eaves, running along `ridge` ('x' = screen down-right, 'y' = screen down-left). Ground point at (ax, ay).
 */
export function isoPrism(
  buf: PixelBuffer,
  ax: number,
  ay: number,
  w: number,
  h: number,
  height: number,
  rise: number,
  ridge: 'x' | 'y',
  c: PrismColors,
): void {
  const P = (x: number, y: number, z: number) => isoPoint(ax, ay, x, y, z);
  const fill = (pts: Pt[], p: Paint) => fillPolygon(buf, pts, typeof p === 'string' ? p : (x, y) => p(x, y));
  const H = height, R = height + rise;
  if (ridge === 'x') {
    fill([P(0, 0, H), P(w, 0, H), P(w, h / 2, R), P(0, h / 2, R)], c.roofFar);
    fill([P(0, h, 0), P(w, h, 0), P(w, h, H), P(0, h, H)], c.left);
    fill([P(w, 0, 0), P(w, h, 0), P(w, h, H), P(w, h / 2, R), P(w, 0, H)], c.right);
    fill([P(0, h / 2, R), P(w, h / 2, R), P(w, h, H), P(0, h, H)], c.roofNear);
  } else {
    fill([P(0, 0, H), P(0, h, H), P(w / 2, h, R), P(w / 2, 0, R)], c.roofFar);
    fill([P(w, 0, 0), P(w, h, 0), P(w, h, H), P(w, 0, H)], c.right);
    fill([P(0, h, 0), P(w, h, 0), P(w, h, H), P(w / 2, h, R), P(0, h, H)], c.left);
    fill([P(w / 2, 0, R), P(w / 2, h, R), P(w, h, H), P(w, 0, H)], c.roofNear);
  }
}

// ------------------------------------------------------------------------------------------------ lattices

export type LatticeKind = 'grid' | 'iso' | 'diag';

export interface LatticeOptions {
  /** Only draw over pixels that are already opaque (texture a face). Default false. */
  readonly onOpaque?: boolean;
  /** Phase shift of the lines in px. */
  readonly offset?: number;
}

/**
 * Lines every `step` px inside a rectangle: 'grid' = horizontal + vertical (tiles, panels, rack holes), 'iso' = the two
 * 2:1 iso directions (floor seams, fences, trellis), 'diag' = 45-degree cross-hatch (bamboo screens, mesh).
 */
export function lattice(buf: PixelBuffer, x: number, y: number, w: number, h: number, step: number, kind: LatticeKind, hex: string, opts: LatticeOptions = {}): void {
  const s = Math.max(2, Math.floor(step));
  const o = opts.offset ?? 0;
  const m = (v: number) => (((v + o) % s) + s) % s === 0;
  for (let j = y; j < y + h; j++)
    for (let i = x; i < x + w; i++) {
      const on = kind === 'grid' ? m(i) || m(j) : kind === 'iso' ? m(i + 2 * j) || m(i - 2 * j) : m(i + j) || m(i - j);
      if (!on || (opts.onOpaque && !isOpaque(buf, i, j))) continue;
      setPixel(buf, i, j, hex);
    }
}

// ------------------------------------------------------------------------------------------------ seeded noise

/** The variant's seed (0 when unset) as an unsigned 32-bit integer. */
export function seedOf(ctx: DrawContext): number {
  const s = ctx.variant.seed ?? 0;
  return Number.isFinite(s) ? Math.trunc(s) >>> 0 : 0;
}

/** Integer hash of (seed, x, y) to [0, 1). Stateless, so pixels can be evaluated in any order. */
export function hash2(seed: number, x: number, y: number): number {
  let h = (seed ^ 0x9e3779b9) >>> 0;
  h = Math.imul(h ^ (x | 0), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13) ^ (y | 0), 0xc2b2ae35);
  h ^= h >>> 16;
  h = Math.imul(h, 0x27d4eb2f);
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

/** Small seeded generator (mulberry32) for sequential choices: `const r = rng(seed); r(); r();`. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** One of `items`, chosen by the seed. */
export function pick<T>(seed: number, items: readonly T[]): T {
  if (items.length === 0) throw new Error('pick from nothing');
  return items[Math.floor(hash2(seed, 7, 11) * items.length)]!;
}

/** Smooth value noise in [0, 1): lattice values every `cell` px, bilinear with smoothstep in between. */
export function valueNoise(seed: number, x: number, y: number, cell = 4): number {
  const c = Math.max(1, cell);
  const gx = Math.floor(x / c), gy = Math.floor(y / c);
  const fx = x / c - gx, fy = y / c - gy;
  const s = (t: number) => t * t * (3 - 2 * t);
  const a = hash2(seed, gx, gy), b = hash2(seed, gx + 1, gy), d = hash2(seed, gx, gy + 1), e = hash2(seed, gx + 1, gy + 1);
  const top = a + (b - a) * s(fx), bot = d + (e - d) * s(fx);
  return top + (bot - top) * s(fy);
}

/** Sprinkles `hex` over the opaque pixels of a rectangle with probability `density` (seeded; grass, rust, gravel). */
export function speckle(buf: PixelBuffer, seed: number, x: number, y: number, w: number, h: number, density: number, hex: string): void {
  for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (isOpaque(buf, i, j) && hash2(seed, i, j) < density) setPixel(buf, i, j, hex);
}
