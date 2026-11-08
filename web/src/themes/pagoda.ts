// Theme 'pagodas-nature': a temple garden. Raked-gravel floors, timber-and-paper screen walls,
// sites as tiered pagodas among cherry and pine trees, racks as lacquered cabinets, cables as bamboo conduits, and
// garden props (bonsai, stone lanterns, koi ponds, moss rocks, bamboo). Original, code-generated art: every pixel is
// a palette key (or the variant's NetBox role/cable colour), light comes from the top left (top faces lightest, left
// faces mid, right faces darkest), and every choice among alternatives comes from variant.seed (no randomness).
// Check: npx vitest run tests/theme-conformance.test.ts tests/theme-pagoda.test.ts; look: make theme-shots THEME=pagodas-nature.
import {
  fillDiamond,
  fillRect,
  setPixel,
  type DrawContext,
  type Palette,
  type PixelBuffer,
  type SlotDrawFn,
  type SlotId,
  type Theme,
} from '../contracts/theme';
import { clearPixel, fillPolygon, getPixel, hash2, isOpaque, isoBox, outline, seedOf, type Paint, type Pt } from './kit';

// Original 16-colour palette (8 Nov 2026): pond ink, sumi outline, raked gravel, shoji paper, deep lacquer timber,
// garden stone, lacquer red, leaf green, deep bamboo green, blossom pink. The three LED keys are a bright leaf green,
// an amber and a hot coral red: distinct from each other and from the lacquer red and greens of the art. Revised:
// wire is the deep bamboo green (contrast 4.39 on gravel, was 1.12) and doubles as the
// foliage mid tone; accent_alt is the lit leaf; floor_alt is darker (1.85 against floor) for the tile edging.
const palette: Palette = {
  background: '#1d2a2e',
  outline: '#1a1216',
  floor: '#c8bfa6',
  floor_alt: '#958b70',
  wall: '#f1e7cf',
  wall_shade: '#7c3326',
  metal: '#9a9a8f',
  metal_shade: '#5d605a',
  accent: '#c23b2c',
  accent_alt: '#82a845',
  led_ok: '#7de06a',
  led_warn: '#f4b844',
  led_err: '#ff4f5e',
  wire: '#2c5a2e',
  text: '#fbf6ea',
  highlight: '#f2a7bd',
};

// ------------------------------------------------------------------------------------------------ small helpers

/** Integer line (Bresenham), endpoints included. */
function line(buf: PixelBuffer, x0: number, y0: number, x1: number, y1: number, hex: string): void {
  x0 = Math.round(x0), y0 = Math.round(y0), x1 = Math.round(x1), y1 = Math.round(y1);
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    setPixel(buf, x0, y0, hex);
    if (x0 === x1 && y0 === y1) return;
    const e2 = 2 * err;
    if (e2 >= dy) (err += dy), (x0 += sx);
    if (e2 <= dx) (err += dx), (y0 += sy);
  }
}

/** Shaded round blob (foliage, blossom, stones): light on the top-left, dark on the bottom-right. */
function blob(buf: PixelBuffer, cx: number, cy: number, rx: number, ry: number, light: string, mid: string, dark: string): void {
  for (let y = Math.floor(cy - ry); y <= Math.ceil(cy + ry); y++)
    for (let x = Math.floor(cx - rx); x <= Math.ceil(cx + rx); x++) {
      const u = (x + 0.5 - cx) / rx, v = (y + 0.5 - cy) / ry;
      if (u * u + v * v > 1) continue;
      const s = u + v;
      setPixel(buf, x, y, s < -0.55 ? light : s > 0.45 ? dark : mid);
    }
}

/** Screen point of (u, v, z) on a square centred at (cx, cy): u along +x (down-right), v along +y (down-left), in tiles. */
const C = (cx: number, cy: number, u: number, v: number, z: number): Pt => [cx + (u - v) * 16, cy + (u + v) * 8 - z];

/** Iso box of side `s` tiles centred on (cx, cy), standing at height z0, `h` px tall. */
function centredBox(buf: PixelBuffer, cx: number, cy: number, s: number, z0: number, h: number, c: { top: Paint; left: Paint; right: Paint }): void {
  const s8 = Math.round(s * 8); // isoBox needs whole-pixel footprints
  isoBox(buf, cx, Math.round(cy - s8 - z0), s8 / 8, s8 / 8, h, c);
}

/**
 * Hipped roof with up-turned eaves: eave square `s` tiles at height z0, rising `rise` px to a ridge square `t` tiles,
 * centred on (cx, cy). Far slopes and ridge take `top` (lit), the near-left slope `left`, the near-right `right`.
 */
function hipRoof(buf: PixelBuffer, cx: number, cy: number, s: number, t: number, z0: number, rise: number, top: string, left: Paint, right: Paint, rim: string): void {
  const a = s / 2, b = t / 2, z1 = z0 + rise;
  const P = (u: number, v: number, z: number) => C(cx, cy, u, v, z);
  fillPolygon(buf, [P(-a, -a, z0), P(a, -a, z0), P(b, -b, z1), P(-b, -b, z1)], top);
  fillPolygon(buf, [P(-a, -a, z0), P(-a, a, z0), P(-b, b, z1), P(-b, -b, z1)], top);
  fillPolygon(buf, [P(-b, -b, z1), P(b, -b, z1), P(b, b, z1), P(-b, b, z1)], top);
  fillPolygon(buf, [P(-a, a, z0), P(a, a, z0), P(b, b, z1), P(-b, b, z1)], left);
  fillPolygon(buf, [P(a, -a, z0), P(a, a, z0), P(b, b, z1), P(b, -b, z1)], right);
  // Eave line: the lit edge of the near slopes, then the up-turned corner tips.
  const [lx, ly] = P(-a, a, z0), [bx, by] = P(a, a, z0), [rx, ry] = P(a, -a, z0);
  line(buf, lx, ly - 1, bx, by - 1, rim);
  line(buf, bx, by - 1, rx, ry - 1, typeof left === 'string' ? left : left(bx, by));
  for (const [x, y, d] of [[lx, ly, -1], [rx, ry, 1]] as const) {
    setPixel(buf, Math.round(x) + d, Math.round(y) - 2, rim);
    setPixel(buf, Math.round(x) + 2 * d, Math.round(y) - 3, rim);
  }
}

// Device status (the rule proposed for contract 1.1.2): active lit led_ok; planned/staged a
// paper ghost; offline/inventory unlit; failed led_err plus an X seal (a shape cue, not only hue); decommissioning
// led_warn. Faceplates draw their activity LEDs in led_ok and a 3x2 status lamp; deviceStatus() then recolours them.
type Look = 'lit' | 'warn' | 'unlit' | 'failed' | 'ghost';
const lookOf = (st: string | undefined): Look =>
  st === 'failed' ? 'failed'
    : st === 'decommissioning' ? 'warn'
    : st === 'offline' || st === 'inventory' ? 'unlit'
    : st === 'planned' || st === 'staged' ? 'ghost'
    : 'lit';

/** 3x2 status lamp in an ink bezel, top-left of the glass at (x, y); drawn lit, deviceStatus() sets its state. */
function lamp(buf: PixelBuffer, p: Palette, x: number, y: number): void {
  fillRect(buf, x - 1, y - 1, 5, 4, p.outline);
  fillRect(buf, x, y, 3, 2, p.led_ok);
}

/**
 * Applies the device status to a finished faceplate. The lamp sits at (lx, ly); the failed seal (a 6x6 red X on ink)
 * goes at (sx, sy). Every non-active look changes at least 6 px against active (the lamp alone is 6 px).
 */
function deviceStatus(buf: PixelBuffer, p: Palette, st: string | undefined, lx: number, ly: number, sx: number, sy: number): void {
  const look = lookOf(st);
  if (look === 'lit') return;
  const lit = look === 'warn' ? p.led_warn : null; // null: activity LEDs go dark
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++) if (getPixel(buf, x, y) === p.led_ok) setPixel(buf, x, y, lit ?? p.outline);
  if (look === 'unlit') setPixel(buf, lx, ly, p.metal_shade); // dark glass with one dull glint
  if (look === 'failed') {
    fillRect(buf, lx, ly, 3, 2, p.led_err);
    fillRect(buf, sx, sy, 6, 6, p.outline);
    for (let t = 0; t < 6; t++) setPixel(buf, sx + t, sy + t, p.led_err), setPixel(buf, sx + 5 - t, sy + t, p.led_err);
  }
  if (look === 'ghost') {
    // Planned: paper ghost. Ink stays as a dotted outline, every other pixel turns to shoji paper.
    for (let y = 0; y < buf.height; y++)
      for (let x = 0; x < buf.width; x++) {
        const c = getPixel(buf, x, y);
        if (c === null) continue;
        if (c === p.outline) setPixel(buf, x, y, (x + y) & 1 ? p.floor_alt : p.outline);
        else setPixel(buf, x, y, (x + y) & 1 ? p.wall : p.floor);
      }
  }
}

// ------------------------------------------------------------------------------------------------ trees and props

function cherryTree(buf: PixelBuffer, p: Palette, x: number, y: number, size: number, seed: number): void {
  // Trunk: two-tone timber leaning slightly.
  const h = Math.round(size * 0.9);
  for (let j = 0; j < h; j++) {
    const lean = Math.floor((j * (seed % 2 ? 1 : -1)) / 6);
    setPixel(buf, x + lean, y - j, p.wall_shade);
    setPixel(buf, x + lean + 1, y - j, p.outline);
  }
  const top = y - h;
  const r = size * 0.55;
  const blobs: [number, number, number][] = [
    [0, -r * 0.2, r],
    [-r * 0.85, r * 0.25, r * 0.7],
    [r * 0.85, r * 0.2, r * 0.75],
    [r * 0.1, -r * 0.85, r * 0.65],
  ];
  for (const [dx, dy, rr] of blobs) blob(buf, x + dx, top + dy, rr, rr * 0.82, p.text, p.highlight, p.accent);
  // Scattered petals and a few darker blossom clusters, seeded (only on the blossom, never on what is behind it).
  for (let j = Math.floor(top - r * 1.6); j <= top + r; j++)
    for (let i = Math.floor(x - r * 1.7); i <= x + r * 1.7; i++) {
      if (getPixel(buf, i, j) !== p.highlight) continue;
      const k = hash2(seed, i, j);
      if (k < 0.07) setPixel(buf, i, j, p.text);
      else if (k > 0.95) setPixel(buf, i, j, p.accent);
    }
}

function pineTree(buf: PixelBuffer, p: Palette, x: number, y: number, size: number, seed: number): void {
  // Cloud-pruned garden pine: a crooked trunk with flat foliage pads.
  const h = Math.round(size * 1.1);
  const bend = seed % 2 ? 1 : -1;
  for (let j = 0; j < h; j++) {
    const off = Math.round(Math.sin(j / 3) * 1.4) * bend;
    setPixel(buf, x + off, y - j, p.wall_shade);
    setPixel(buf, x + off + 1, y - j, p.outline);
  }
  const pads = Math.max(2, Math.round(size / 5));
  for (let k = 0; k < pads; k++) {
    const t = (k + 1) / pads;
    const py = y - Math.round(h * (0.35 + 0.65 * t)) + 1;
    const side = k % 2 === 0 ? -bend : bend;
    const w = size * (0.85 - 0.35 * t);
    const px = x + side * Math.round(size * 0.35 * (1 - t));
    blob(buf, px, py, w * 0.6, Math.max(1.6, w * 0.3), p.accent_alt, p.wire, p.background);
  }
}

function stoneLantern(buf: PixelBuffer, p: Palette, x: number, y: number, s: number, glow: string): void {
  // s = 1 (small, ~13 px) or 2 (large, ~28 px). Base, post, fire box, wide cap, finial.
  const W = (n: number) => Math.round(n * s);
  fillRect(buf, x - W(3), y - W(2), W(6) + 1, W(2), p.metal_shade);
  fillRect(buf, x - W(3), y - W(2), W(3), W(2), p.metal);
  const postTop = y - W(2) - W(4);
  fillRect(buf, x - W(1), postTop, W(2) + 1, W(4), p.metal);
  fillRect(buf, x + 1, postTop, W(1), W(4), p.metal_shade);
  // Fire box with a lit window.
  const boxTop = postTop - W(4);
  fillRect(buf, x - W(2), boxTop, W(4) + 1, W(4), p.metal);
  fillRect(buf, x + 1, boxTop, W(2), W(4), p.metal_shade);
  fillRect(buf, x - W(1), boxTop + 1, W(2) + 1, W(4) - 2, glow);
  // Cap: a wide flat roof with turned-up tips.
  const capY = boxTop - W(2);
  fillRect(buf, x - W(4), capY, W(8) + 1, W(2), p.metal_shade);
  fillRect(buf, x - W(4), capY, W(4) + 1, 1, p.text);
  fillRect(buf, x - W(3), capY - 1, W(6) + 1, 1, p.metal);
  setPixel(buf, x - W(4) - 1, capY - 1, p.metal);
  setPixel(buf, x + W(4) + 1, capY - 1, p.metal_shade);
  fillRect(buf, x, capY - 1 - W(2), 1, W(2), p.metal);
  setPixel(buf, x, capY - 2 - W(2), p.text);
}

function mossRock(buf: PixelBuffer, p: Palette, x: number, y: number, r: number, seed: number): void {
  blob(buf, x, y - r * 0.55, r, r * 0.62, p.metal, p.floor_alt, p.metal_shade);
  blob(buf, x + r * 0.7, y - r * 0.25, r * 0.5, r * 0.35, p.metal, p.floor_alt, p.metal_shade);
  // Moss on the lit top.
  for (let j = Math.floor(y - r * 1.3); j < y; j++)
    for (let i = Math.floor(x - r * 1.2); i <= x + r * 1.3; i++) {
      if (!isOpaque(buf, i, j) || isOpaque(buf, i, j - 1) || i > x + r * 0.6) continue;
      const k = hash2(seed, i, j);
      if (k < 0.75) setPixel(buf, i, j, k < 0.35 ? p.accent_alt : p.wire);
      if (k < 0.25) setPixel(buf, i, j + 1, p.accent_alt);
    }
}

function bamboo(buf: PixelBuffer, p: Palette, x: number, y: number, h: number, stems: number, seed: number): void {
  for (let k = 0; k < stems; k++) {
    const sx = x + Math.round((k - (stems - 1) / 2) * 3) + (hash2(seed, k, 1) < 0.5 ? 0 : 1);
    const sh = Math.round(h * (0.7 + 0.3 * hash2(seed, k, 2)));
    const base = y - (k % 2);
    for (let j = 0; j < sh; j++) {
      const node = (j + k * 2) % 6 === 5;
      setPixel(buf, sx, base - j, node ? p.wire : p.accent_alt);
      setPixel(buf, sx + 1, base - j, node ? p.background : p.accent_alt);
    }
    // Leaves: small slanted strokes near the top.
    for (let l = 0; l < 3; l++) {
      const ly = base - sh + 2 + l * 3;
      const d = (l + k) % 2 ? 1 : -1;
      setPixel(buf, sx + d * 2, ly, p.accent_alt);
      setPixel(buf, sx + d * 3, ly + 1, p.accent_alt);
      setPixel(buf, sx + d * 1, ly, p.accent_alt);
    }
  }
}

function bonsai(buf: PixelBuffer, p: Palette, x: number, y: number, pot: string): void {
  // Pot (trapezoid), twisted trunk, two foliage pads.
  fillRect(buf, x - 4, y - 3, 9, 1, pot);
  fillRect(buf, x - 3, y - 2, 7, 2, pot);
  fillRect(buf, x + 1, y - 2, 3, 2, p.wall_shade);
  setPixel(buf, x - 4, y - 3, p.text);
  line(buf, x, y - 4, x - 1, y - 6, p.wall_shade);
  line(buf, x - 1, y - 6, x + 2, y - 9, p.wall_shade);
  blob(buf, x - 3, y - 8, 3.2, 1.8, p.accent_alt, p.wire, p.background);
  blob(buf, x + 2.5, y - 11, 3.4, 2, p.accent_alt, p.wire, p.background);
}

function koiPond(buf: PixelBuffer, p: Palette, cx: number, cy: number, rx: number, ry: number, fish: string, seed: number): void {
  // Stone rim, dark water, ripples, two koi.
  blob(buf, cx, cy, rx + 1, ry + 1, p.metal, p.floor_alt, p.metal_shade);
  for (let y = Math.floor(cy - ry); y <= cy + ry; y++)
    for (let x = Math.floor(cx - rx); x <= cx + rx; x++) {
      const u = (x + 0.5 - cx) / rx, v = (y + 0.5 - cy) / ry;
      if (u * u + v * v <= 0.8) setPixel(buf, x, y, p.background);
    }
  const f = hash2(seed, 3, 3) < 0.5 ? 1 : -1;
  const k1x = Math.round(cx - rx * 0.35), k1y = Math.round(cy - ry * 0.15);
  fillRect(buf, k1x, k1y, 3, 1, fish);
  setPixel(buf, k1x + (f > 0 ? 3 : -1), k1y, p.text);
  const k2x = Math.round(cx + rx * 0.2), k2y = Math.round(cy + ry * 0.3);
  fillRect(buf, k2x, k2y, 3, 1, p.text);
  setPixel(buf, k2x + 1, k2y, p.accent);
  setPixel(buf, Math.round(cx + rx * 0.35), Math.round(cy - ry * 0.4), p.metal_shade);
  setPixel(buf, Math.round(cx - rx * 0.1), Math.round(cy + ry * 0.45), p.metal_shade);
}

// ------------------------------------------------------------------------------------------------ site: pagoda

const sitePagoda: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const st = ctx.variant.status;
  const planned = st === 'planned';
  const cx = 32, cy = 64; // centre of the 2 x 2 footprint (anchor 32,48 + 16)

  // Raked-gravel ground pad.
  fillDiamond(buf, 32, 50, 1.75, 1.75, p.floor);
  for (let y = 50; y < 80; y++)
    for (let x = 4; x < 61; x++) if (isOpaque(buf, x, y) && ((y - (x >> 1)) & 3) === 0) setPixel(buf, x, y, p.floor_alt);

  // Stone plinth.
  centredBox(buf, cx, cy, 1.4, 0, 4, { top: p.metal, left: p.floor_alt, right: p.metal_shade });

  // Tiers: 3, 4 or 5 storeys from the seed; each a lacquered body with paper screens under a shallow hipped roof.
  const tiers = 3 + (Math.floor(seed / 3) % 3);
  const per = Math.floor(42 / tiers);
  const roofH = tiers === 3 ? 5 : tiers === 4 ? 4 : 3;
  const bodyH = per - roofH + 1;
  const red = planned ? p.floor : p.accent, redDark = planned ? p.floor_alt : p.wall_shade;
  let z = 4;
  for (let k = 0; k < tiers; k++) {
    const s = Math.round((0.95 - (0.45 * k) / Math.max(1, tiers - 1)) * 8) / 8;
    const half = s * 16;
    const left: Paint = (x, y) => {
      const col = x - (cx - half);
      const row = y - (cy - z) - Math.floor(col / 2); // 0 = the face's bottom edge, negative going up
      if (col < 2 || col > half - 2 || Math.abs(col - half / 2) < 1) return red;
      if (row <= -bodyH + 2 || (bodyH >= 7 && row === -Math.floor(bodyH / 2))) return redDark;
      return p.wall;
    };
    const right: Paint = (x, y) => {
      const col = cx + half - x;
      const row = y - (cy - z) - Math.floor(col / 2);
      if (col < 2 || col > half - 2 || Math.abs(col - half / 2) < 1) return redDark;
      if (row <= -bodyH + 2 || (bodyH >= 7 && row === -Math.floor(bodyH / 2))) return p.outline;
      return p.floor;
    };
    centredBox(buf, cx, cy, s, z, bodyH, { top: red, left, right });
    z += bodyH;
    // Tile ribs run down each slope: moss-green tiles with lit ribs on the left slope, shaded tiles on the right.
    const ribs = (base: string, rib: string): Paint => (x) => (((x - cx) % 4 + 4) % 4 === 2 ? rib : base);
    const roofL = planned ? p.floor_alt : ribs(p.accent_alt, p.wire);
    const roofR = planned ? p.metal : ribs(p.wire, p.background);
    hipRoof(buf, cx, cy, s + (tiers === 5 ? 0.4 : 0.55), s * 0.55, z - 1, roofH, planned ? p.floor : p.accent_alt, roofL, roofR, p.text);
    z += roofH - 1;
  }
  // Spire with rings, from the top ridge up toward the sprite's top.
  const topY = cy - z;
  for (let y = Math.max(1, topY - 8); y <= topY; y++) setPixel(buf, cx, y, (y & 1) ? p.metal : p.metal_shade);
  setPixel(buf, cx - 1, topY - 4, p.metal);
  setPixel(buf, cx + 1, topY - 4, p.metal_shade);
  setPixel(buf, cx, Math.max(0, topY - 9), p.highlight);

  // Trees on the side corners of the footprint, chosen by seed, then a stone lantern by the front steps lit by status.
  const cherryLeft = seed % 2 === 0;
  (cherryLeft ? cherryTree : pineTree)(buf, p, 9, 67, 15, seed);
  (cherryLeft ? pineTree : cherryTree)(buf, p, 54, 67, 15, seed + 1);
  const glow = st === 'failed' ? p.led_err : st === 'offline' || planned ? p.metal_shade : p.led_warn;
  stoneLantern(buf, p, 25, 76, 1, glow);

  outline(buf, p.outline);
};

// ------------------------------------------------------------------------------------------------ room: floor and wall

/**
 * Raked gravel: parallel grooves every 4 px along the x axis, continuous across tiles (a tile's screen offset moves the
 * groove phase by a multiple of 4), with ridge glints between them. Seed parity only moves the few pebbles, so the
 * garden reads as one raked bed rather than a checkerboard.
 */
const gravelFloor: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  fillDiamond(buf, 16, 0, 1, 1, p.floor);
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 32; x++) {
      if (!isOpaque(buf, x, y)) continue;
      const m = (y - (x >> 1) + 32) % 4;
      if (m === 0 && (x & 1) === 0) setPixel(buf, x, y, p.floor_alt);
      else if (m === 2 && (x & 3) === 1) setPixel(buf, x, y, p.wall);
    }
  // Stone edging on the tile's two top edges (NW and NE): neighbouring tiles complete the grid, so cells read.
  for (let t = 0; t < 16; t++) {
    const y = Math.floor(t / 2);
    setPixel(buf, 15 - t, y, p.floor_alt);
    setPixel(buf, 16 + t, y, p.floor_alt);
  }
  for (const [x, y] of seedOf(ctx) % 2 === 1 ? [[9, 8], [22, 6]] : [[13, 5], [20, 10]]) setPixel(buf, x!, y!, p.metal);
};

/** Shoji screen on the N edge: timber frame, paper panels with a lattice, stone footing. */
const shojiWall: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  for (let t = 0; t < 16; t++) {
    const y0 = Math.floor(t / 2); // top of column t; the wall runs 33 px down to the tile edge
    for (let r = 0; r <= 32; r++) {
      let c: string;
      if (r === 0) c = p.outline;
      else if (r <= 2) c = p.wall_shade; // top beam
      else if (r >= 28) c = r === 28 ? p.outline : r <= 30 ? p.metal : p.metal_shade; // stone footing
      else if (r >= 25) c = p.wall_shade; // sill
      else if (t === 0 || t === 15) c = p.wall_shade; // posts at the tile ends
      else if (t === 1) c = p.outline;
      else if (t === 8 || (r - 3) % 6 === 5) c = p.wall_shade; // lattice
      else c = r < 6 && t > 9 ? p.text : p.wall;
      setPixel(buf, t, y0 + r, c);
    }
  }
};

// ------------------------------------------------------------------------------------------------ rack cabinet

const lacquerCabinet: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const rot = ctx.variant.rotation ?? 0;
  const planned = ctx.variant.status === 'planned';
  const red = planned ? p.floor : p.accent, dark = planned ? p.floor_alt : p.wall_shade;
  const front = rot === 0 ? 'S' : rot === 3 ? 'E' : null;
  // Face-local rows (see sprites.ts): S face column c starts at row 9 + floor(c / 2), E face at 24 - floor(c / 2).
  const sRow = (x: number, y: number) => y - (9 + (x >> 1));
  const eRow = (x: number, y: number) => y - (24 - (x >> 1));
  const opening = (col: number, row: number, frame: string) => {
    if (col < 2 || col > 13 || row < 0 || row > 47) return frame;
    if (col === 2 || col === 13) return (row & 3) === 1 ? p.metal : p.outline; // rails with mounting holes
    return (row & 7) === 0 ? p.outline : p.background;
  };
  const panel = (col: number, row: number, base: string, inset: string) => {
    if (col === 2 || col === 13 || row === 3 || row === 44) return inset;
    if (row > 20 && row < 27 && col > 5 && col < 10) return (col + row) % 2 ? inset : base; // carved crest
    return base;
  };
  const left: Paint = (x, y) => (front === 'S' ? opening(x, sRow(x, y), red) : panel(x, sRow(x, y), red, dark));
  const right: Paint = (x, y) => (front === 'E' ? opening(x - 16, eRow(x, y), dark) : panel(x - 16, eRow(x, y), dark, p.outline));
  isoBox(buf, 16, 48, 1, 1, 48, { top: red, left, right });
  // Lid: a lit rim on the top diamond's back edges.
  for (let t = 0; t < 16; t++) {
    setPixel(buf, 15 - t, Math.floor(t / 2), p.highlight);
    setPixel(buf, 16 + t, Math.floor(t / 2), planned ? p.text : p.highlight);
  }
  // Lid panel: an inset rim on the top, and a paper-white crest in its middle.
  for (let r = 0; r < 10; r++) {
    const reach = r < 5 ? 2 * (r + 1) : 10 - 2 * (r - 5);
    for (const x of [16 - reach, 17 - reach, 14 + reach, 15 + reach]) setPixel(buf, x, 3 + r, dark);
  }
  setPixel(buf, 15, 8, planned ? p.metal : p.highlight);
  setPixel(buf, 16, 8, planned ? p.metal : p.highlight);
  // Vertical edge between the faces.
  for (let y = 16; y < 64; y++) if (isOpaque(buf, 16, y)) setPixel(buf, 16, y, p.outline);
  outline(buf, p.outline);
};

// ------------------------------------------------------------------------------------------------ faceplates

/** Shared faceplate: ink border, timber ears with stone screws, body fill. */
function plate(buf: PixelBuffer, p: Palette, body: string, ear: string): void {
  const w = buf.width, h = buf.height;
  fillRect(buf, 0, 0, w, h, p.outline);
  fillRect(buf, 1, 1, w - 2, h - 2, body);
  for (const x0 of [1, w - 4]) {
    fillRect(buf, x0, 1, 3, h - 2, ear);
    for (let y = 2; y < h - 2; y += 4) setPixel(buf, x0 + 1, y + 1, p.metal);
  }
}

function genericPlate(units: 1 | 2 | 4): SlotDrawFn {
  return (buf, ctx) => {
    const p = ctx.palette;
    const body = ctx.variant.role_color ?? p.metal;
    plate(buf, p, body, p.wall_shade);
    const h = buf.height;
    if (units === 1) {
      // Vent slots and a paper label.
      for (let x = 8; x < 30; x += 2) setPixel(buf, x, 3 + ((x >> 1) & 1), p.outline);
      fillRect(buf, 34, 3, 12, 2, p.outline);
      fillRect(buf, 35, 3, 3, 1, p.wall);
    } else {
      // Drive bays: paper labels on ink trays.
      const rows = units === 2 ? 1 : 3;
      for (let r = 0; r < rows; r++)
        for (let k = 0; k < 6; k++) {
          const x = 7 + k * 7, y = units === 2 ? 6 : 4 + r * 9;
          fillRect(buf, x, y, 6, 4, p.outline);
          fillRect(buf, x + 1, y + 1, 3, 1, p.wall);
          setPixel(buf, x + 4, y + 2, k % 3 === 0 ? p.led_ok : p.metal_shade);
        }
      if (units === 4) {
        // Kumiko vent grille on the right: an iso lattice of ink lines.
        for (let y = 3; y < h - 3; y++)
          for (let x = 50; x < 58; x++) if (((x + 2 * y) & 3) === 0 || ((x - 2 * y) & 3) === 0) setPixel(buf, x, y, p.outline);
      }
    }
    const ly = units === 4 ? h - 5 : 3;
    lamp(buf, p, 55, ly);
    deviceStatus(buf, p, ctx.variant.status, 55, ly, 47, ly - 2);
  };
}

/** Left ear in the NetBox role colour (so the role reads on typed plates too), else the default ear. */
const earOf = (ctx: DrawContext) => ctx.variant.role_color ?? ctx.palette.wall_shade;

const switchPlate: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  plate(buf, p, p.wire, p.wall_shade);
  fillRect(buf, 1, 1, 3, 6, earOf(ctx));
  // 12 ports in groups of four, link LEDs above each.
  for (let k = 0; k < 12; k++) {
    const x = 5 + k * 4 + (k >> 2);
    fillRect(buf, x, 3, 3, 3, p.outline);
    setPixel(buf, x + 1, 5, p.metal_shade);
    setPixel(buf, x + 1, 2, k % 3 === 2 ? p.metal_shade : p.led_ok);
  }
  lamp(buf, p, 56, 3);
  deviceStatus(buf, p, ctx.variant.status, 56, 3, 49, 1);
};

const routerPlate: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  plate(buf, p, p.accent, p.wall_shade);
  fillRect(buf, 1, 1, 3, 6, earOf(ctx));
  // Four uplinks, then the compass, then two activity LEDs and the status lamp.
  for (let k = 0; k < 4; k++) {
    fillRect(buf, 7 + k * 5, 2, 4, 4, p.outline);
    setPixel(buf, 8 + k * 5, 2, p.led_ok);
  }
  // A paper-white compass diamond with an ink hub (not a cross: an X is the failed seal).
  fillRect(buf, 31, 1, 13, 6, p.wall_shade);
  for (let t = 0; t < 3; t++) {
    for (const [x, y] of [[37 - 2 * t, 1 + t], [38 - 2 * t, 1 + t], [37 + 2 * t, 1 + t], [36 + 2 * t, 1 + t]] as const) setPixel(buf, x, y, p.text);
    for (const [x, y] of [[37 - 2 * t, 6 - t], [38 - 2 * t, 6 - t], [37 + 2 * t, 6 - t], [36 + 2 * t, 6 - t]] as const) setPixel(buf, x, y, p.text);
  }
  setPixel(buf, 37, 3, p.outline);
  setPixel(buf, 37, 4, p.outline);
  setPixel(buf, 48, 3, p.led_ok);
  setPixel(buf, 50, 3, p.led_ok);
  lamp(buf, p, 55, 3);
  deviceStatus(buf, p, ctx.variant.status, 55, 3, 46, 1);
};

const patchPanelPlate: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  plate(buf, p, p.metal, p.metal_shade);
  fillRect(buf, 1, 1, 3, 6, earOf(ctx));
  // Label strip, then 24 keystone holes in groups of six.
  for (let k = 0; k < 24; k++) {
    const x = 6 + k * 2 + ((k / 6) | 0);
    setPixel(buf, x, 2, k % 6 === 0 ? p.accent : p.text);
    fillRect(buf, x, 4, 1, 2, p.outline);
  }
  lamp(buf, p, 57, 3);
  deviceStatus(buf, p, ctx.variant.status, 57, 3, 49, 1);
};

const pduPlate: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  plate(buf, p, p.wall_shade, p.metal_shade);
  fillRect(buf, 1, 1, 3, 6, earOf(ctx));
  // Power switch with its lamp, then six paper-white outlets with two ink pins each.
  lamp(buf, p, 7, 3);
  for (let k = 0; k < 6; k++) {
    const x = 14 + k * 7;
    fillRect(buf, x, 2, 5, 4, p.wall);
    setPixel(buf, x + 1, 3, p.outline);
    setPixel(buf, x + 3, 3, p.outline);
    setPixel(buf, x + 2, 5, p.floor_alt);
  }
  deviceStatus(buf, p, ctx.variant.status, 7, 3, 54, 1);
};

const rackUnit: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // Empty slot: dark interior with a faint U divider; lacquered posts each side with mounting holes.
  fillRect(buf, 8, 0, 64, 8, p.outline);
  for (let x = 9; x < 71; x += 4) setPixel(buf, x, 0, p.background);
  for (const x0 of [0, 72]) {
    fillRect(buf, x0, 0, 8, 8, p.accent);
    fillRect(buf, x0 + 5, 0, 2, 8, p.wall_shade);
    fillRect(buf, x0, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 7, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 1, 0, 6, 1, p.wall_shade);
    setPixel(buf, x0 + 3, 2, p.outline);
    setPixel(buf, x0 + 3, 5, p.outline);
  }
  // Every fifth U gets a paper tally on the left post.
  if (seedOf(ctx) % 5 === 0) setPixel(buf, 2, 4, p.text);
};

const portSlot: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const st = ctx.variant.status;
  fillRect(buf, 0, 0, 8, 8, p.outline);
  fillRect(buf, 1, 1, 6, 6, st === 'mgmt' ? p.highlight : p.metal);
  fillRect(buf, 2, 2, 4, 4, p.outline);
  setPixel(buf, 3, 6, p.outline), setPixel(buf, 4, 6, p.outline); // latch notch
  if (st === 'connected') {
    const c = ctx.variant.wire_color ?? p.wire;
    fillRect(buf, 2, 2, 4, 4, c);
    setPixel(buf, 2, 2, p.text);
  } else if (st === 'disabled') {
    for (let t = 0; t < 4; t++) setPixel(buf, 2 + t, 2 + t, p.led_err), setPixel(buf, 5 - t, 2 + t, p.led_err);
  } else if (st === 'mgmt') {
    setPixel(buf, 3, 3, p.accent), setPixel(buf, 4, 4, p.accent);
  } else {
    setPixel(buf, 3, 4, p.metal_shade), setPixel(buf, 4, 4, p.metal_shade);
  }
};

// ------------------------------------------------------------------------------------------------ bamboo conduits

type Run = (t: number) => [number, number];
// Through the tile centre (16, 8): along +x from the -x edge midpoint (8, 4); along +y from the -y edge midpoint (23, 4).
const RUN_X: Run = (t) => [8 + t, 4 + Math.floor(t / 2)];
const RUN_Y: Run = (t) => [23 - t, 4 + Math.floor(t / 2)];

function conduit(kind: 'straight' | 'corner' | 'cross'): SlotDrawFn {
  return (buf, ctx) => {
    const p = ctx.palette;
    const c = ctx.variant.wire_color ?? p.wire;
    const rot = ctx.variant.rotation ?? 0;
    const pieces: [Run, number, number][] = [];
    if (kind === 'straight') pieces.push([rot % 2 === 0 ? RUN_X : RUN_Y, 0, 16]);
    if (kind === 'cross') pieces.push([RUN_X, 0, 16], [RUN_Y, 0, 16]);
    // Corner (theme.md): rotation 0 joins -x and +y edges; 1 = -x/-y, 2 = +x/-y, 3 = +x/+y.
    if (kind === 'corner') {
      const xHalf: [number, number] = rot >= 2 ? [8, 16] : [0, 9];
      const yHalf: [number, number] = rot === 0 || rot === 3 ? [8, 16] : [0, 9];
      pieces.push([RUN_X, ...xHalf], [RUN_Y, ...yHalf]);
    }
    // Ink casing first, then the colour core (two rows), then bamboo nodes.
    for (const [run, a, b] of pieces)
      for (let t = a; t < b; t++) {
        const [x, y] = run(t);
        setPixel(buf, x, y - 1, p.outline);
        setPixel(buf, x, y + 2, p.outline);
      }
    for (const [run, a, b] of pieces)
      for (let t = a; t < b; t++) {
        const [x, y] = run(t);
        setPixel(buf, x, y, c);
        setPixel(buf, x, y + 1, c);
      }
    for (const [run, a, b] of pieces)
      for (const t of [3, 12])
        if (t >= a && t < b) {
          const [x, y] = run(t);
          setPixel(buf, x, y, p.text);
          setPixel(buf, x, y + 1, p.outline);
        }
    // A leaf sprouting at the centre joint.
    setPixel(buf, 16, 6, p.accent_alt);
    setPixel(buf, 17, 5, p.accent_alt);
    setPixel(buf, 18, 5, p.accent_alt);
  };
}

// ------------------------------------------------------------------------------------------------ props

/** Small props at the tile centre (16 x 16, ground point = tile top corner at 8,7; tile centre at 8,15). */
const smallProp: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const tint = ctx.variant.role_color;
  const which = Math.floor(hash2(seed, 5, 9) * 5);
  if (which === 0) bonsai(buf, p, 8, 15, tint ?? p.accent);
  else if (which === 1) stoneLantern(buf, p, 7, 15, 1, tint ?? p.led_warn);
  else if (which === 2) koiPond(buf, p, 8, 12, 6.5, 3.2, tint ?? p.accent, seed);
  else if (which === 3) {
    mossRock(buf, p, 7, 15, 5.5, seed);
    if (tint) fillRect(buf, 3, 10, 8, 1, tint); // a rope round the rock in the role colour
  } else {
    bamboo(buf, p, 8, 15, 13, 3, seed);
    if (tint) fillRect(buf, 5, 11, 7, 1, tint);
  }
  outline(buf, p.outline);
};

/** Large props filling a tile, ~32 px tall (32 x 48, ground point 16,32; tile centre 16,40). */
const largeProp: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const which = seed % 4;
  // Moss ground patch under every large prop.
  fillDiamond(buf, 16, 34, 0.75, 0.75, p.accent_alt);
  for (let y = 34; y < 48; y++) for (let x = 0; x < 32; x++) if (isOpaque(buf, x, y) && hash2(seed, x, y) < 0.25) setPixel(buf, x, y, p.wire);
  if (which === 0) cherryTree(buf, p, 15, 41, 18, seed);
  else if (which === 1) pineTree(buf, p, 15, 41, 18, seed);
  else if (which === 2) bamboo(buf, p, 16, 42, 32, 5, seed);
  else stoneLantern(buf, p, 15, 42, 2, ctx.variant.status === 'failed' ? p.led_err : p.led_warn);
  outline(buf, p.outline);
};

// ------------------------------------------------------------------------------------------------ UI

const frame: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  fillRect(buf, 0, 0, 24, 24, p.outline);
  fillRect(buf, 1, 1, 22, 22, p.accent);
  fillRect(buf, 1, 1, 22, 1, p.highlight);
  fillRect(buf, 1, 1, 1, 22, p.highlight);
  fillRect(buf, 3, 3, 18, 18, p.outline);
  fillRect(buf, 4, 4, 16, 16, p.background);
  // Corner brackets: little stone caps with an ink rivet.
  for (const [x, y] of [[0, 0], [19, 0], [0, 19], [19, 19]] as const) {
    fillRect(buf, x, y, 5, 5, p.outline);
    fillRect(buf, x + 1, y + 1, 3, 3, p.metal);
    setPixel(buf, x + 1, y + 1, p.text);
    setPixel(buf, x + 2, y + 2, p.wall_shade);
  }
};

const cursorSlot: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // Blossom-pink diamond outline, ink-backed on the inside so it reads on pale gravel; corners paper-white.
  for (let r = 0; r < 16; r++) {
    const reach = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
    const corner = r < 3 || r > 12 || (r > 5 && r < 10);
    for (const x of [16 - reach, 17 - reach, 14 + reach, 15 + reach]) setPixel(buf, x, r, corner ? p.text : p.highlight);
    if (reach > 2) for (const x of [18 - reach, 13 + reach]) setPixel(buf, x, r, p.outline);
  }
};

const selectionRing: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // Lacquer-red ring two pixels wide on the tile edge, with a blossom-pink rim one pixel outside it.
  for (let r = 0; r < 16; r++) {
    const reach = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
    for (const x of [17 - reach, 18 - reach, 15 + reach, 16 + reach]) setPixel(buf, x, r + 1, p.accent);
  }
  outline(buf, p.highlight);
  // The outline pass also rimmed the inside of the ring: clear the tile's interior again.
  for (let r = 0; r < 16; r++) {
    const reach = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
    for (let x = 19 - reach; x < 15 + reach; x++) clearPixel(buf, x, r + 1);
  }
};

const slots: Record<SlotId, SlotDrawFn> = {
  site_building: sitePagoda,
  room_floor: gravelFloor,
  room_wall: shojiWall,
  rack_empty: lacquerCabinet,
  device_1u: genericPlate(1),
  device_2u: genericPlate(2),
  device_4u: genericPlate(4),
  switch: switchPlate,
  router: routerPlate,
  patch_panel: patchPanelPlate,
  psu: pduPlate,
  rack_unit: rackUnit,
  port: portSlot,
  wire_straight: conduit('straight'),
  wire_corner: conduit('corner'),
  wire_cross: conduit('cross'),
  decoration_small: smallProp,
  decoration_large: largeProp,
  ui_frame: frame,
  cursor: cursorSlot,
  selection_highlight: selectionRing,
};

export const pagodaTheme: Theme = { id: 'pagodas-nature', name: 'Pagodas and nature', palette, slots };
