// Theme 'farm': a countryside world. Grass and tilled-soil floor tiles, timber plank walls,
// sites as red barns and silos, racks as wooden cabinets (planned racks: a see-through ghost cabinet), faceplates as painted
// boards with lantern LEDs, cables as irrigation hoses, props of sheep, chickens, pumpkins, hay, a windmill, a silo
// and an apple tree. Original art, drawn in code: every pixel is a palette key or the variant's NetBox role/cable
// colour, and every choice comes from variant.seed (no Math.random, no clock), as tests/theme-conformance.test.ts
// requires. Light from the top-left: top faces lightest, left faces mid, right faces darkest.
// Check: npx vitest run tests/theme-conformance.test.ts tests/theme-farm.test.ts; look: make theme-shots THEME=farm.
import {
  createPixelBuffer,
  fillDiamond,
  fillRect,
  setPixel,
  type Palette,
  type PixelBuffer,
  type SlotDrawFn,
  type SlotId,
  type Theme,
} from '../contracts/theme';
import { checker, clearPixel, fillPolygon, hash2, innerOutline, isOpaque, outline, seedOf, type Paint, type Pt } from './kit';

// Original 16-colour palette (8 Nov 2026): meadow greens, timber browns, straw, barn red, pumpkin, hose blue, cream.
const palette: Palette = {
  background: '#233021', // dusk field
  outline: '#2b1d16', // dark umber line
  floor: '#7bab47', // meadow grass
  floor_alt: '#5f8d3b', // grass in shade
  wall: '#c58c52', // fresh timber
  wall_shade: '#7a402b', // weathered timber, tilled soil
  metal: '#e7c96b', // straw
  metal_shade: '#b28b3d', // straw in shade
  accent: '#b83a2c', // barn red
  accent_alt: '#ea8c30', // pumpkin
  led_ok: '#a8f060', // lantern: ok
  led_warn: '#ffd447', // lantern: warning
  led_err: '#ff4b3e', // lantern: error
  wire: '#b4e6f6', // irrigation hose, sun-faded blue (2.0x the grass's luminance)
  text: '#f3e7c9', // cream paint
  highlight: '#fffaef', // sunlit white
};

// ------------------------------------------------------------------------------------------------ small helpers

const paintAt = (p: Paint, x: number, y: number) => (typeof p === 'string' ? p : p(x, y));

/** Bresenham line. */
function line(buf: PixelBuffer, x0: number, y0: number, x1: number, y1: number, c: Paint): void {
  x0 = Math.round(x0), y0 = Math.round(y0), x1 = Math.round(x1), y1 = Math.round(y1);
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    setPixel(buf, x0, y0, paintAt(c, x0, y0));
    if (x0 === x1 && y0 === y1) return;
    const e2 = 2 * err;
    if (e2 >= dy) (err += dy), (x0 += sx);
    if (e2 <= dx) (err += dx), (y0 += sy);
  }
}

/**
 * A tiny pixel map: one string per row, one character per pixel. '.' is transparent, every other character is
 * looked up in `keys` (a palette colour). Drawn with its top-left at (ox, oy), mirrored when `flip`.
 */
function sprite(buf: PixelBuffer, rows: readonly string[], ox: number, oy: number, keys: Readonly<Record<string, string>>, flip = false): void {
  const w = Math.max(...rows.map((r) => r.length));
  rows.forEach((row, j) => {
    for (let i = 0; i < row.length; i++) {
      const ch = row[i]!;
      if (ch === '.') continue;
      const c = keys[ch];
      if (c) setPixel(buf, ox + (flip ? w - 1 - i : i), oy + j, c);
    }
  });
}

function keysOf(p: Palette, tag: string): Record<string, string> {
  return {
    o: p.outline, w: p.text, h: p.highlight, s: p.metal, S: p.metal_shade, r: p.accent, p: p.accent_alt,
    g: p.floor, G: p.floor_alt, b: p.wall, B: p.wall_shade, k: p.background, l: p.wire, T: tag,
  };
}

interface FaceColors {
  readonly top: Paint;
  readonly left: Paint;
  readonly right: Paint;
}

/** An iso block in pixels: top corner of its footprint at (cx, cy), `u` px of 2:1 run along +x (down-right), `v`
 *  along +y (down-left), raised `z` px. */
function isoBlock(buf: PixelBuffer, cx: number, cy: number, u: number, v: number, z: number, c: FaceColors): void {
  const T: Pt = [cx, cy - z], R: Pt = [cx + 2 * u, cy + u - z], L: Pt = [cx - 2 * v, cy + v - z], B: Pt = [cx + 2 * u - 2 * v, cy + u + v - z];
  const down = (q: Pt): Pt => [q[0], q[1] + z];
  fillPolygon(buf, [L, B, down(B), down(L)], c.left);
  fillPolygon(buf, [B, R, down(R), down(B)], c.right);
  fillPolygon(buf, [T, R, B, L], c.top);
}

/** Status rule (the cross-theme rule): active lit ok; planned and staged are a ghost (see
 *  `isGhost`) with an unlit lantern; offline and inventory unlit; failed red plus a crack (a non-hue cue);
 *  decommissioning amber. */
function lanternColor(p: Palette, status: string | undefined): string {
  if (status === undefined || status === 'active' || status === 'connected') return p.led_ok;
  if (status === 'failed') return p.led_err;
  if (status === 'decommissioning') return p.led_warn;
  return p.outline; // unlit: offline, inventory, planned, staged
}

const isGhost = (status: string | undefined) => status === 'planned' || status === 'staged';

/** Ghost a drawn sprite: every other inner pixel (or, `sparse`, every fourth, for props too small to survive half)
 *  becomes transparent; the silhouette edge stays, so it still reads as the object and still stands on its anchor. */
function ghostKnockout(buf: PixelBuffer, sparse = false): void {
  const solid = (x: number, y: number) => isOpaque(buf, x, y);
  const drop: [number, number][] = [];
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++)
      if ((sparse ? x % 2 === 1 && y % 2 === 1 : (x + y) % 2 === 1) && solid(x, y) && solid(x - 1, y) && solid(x + 1, y) && solid(x, y - 1) && solid(x, y + 1)) drop.push([x, y]);
  for (const [x, y] of drop) clearPixel(buf, x, y);
}

// ------------------------------------------------------------------------------------------------ floor

/** Grass with sparse 'v' tufts (and, on meadow tiles, flowers) at fixed positions per salt. Kept sparse and
 *  low-contrast: every tile of one seed is identical, so anything bold would show as a grid across the world. */
function grassTile(buf: PixelBuffer, base: string, tuft: string, salt: number, p: Palette, flowers = false): void {
  fillDiamond(buf, 16, 0, 1, 1, base);
  for (let y = 1; y < 15; y++)
    for (let x = 1; x < 31; x++) {
      if (!isOpaque(buf, x, y) || !isOpaque(buf, x - 1, y - 1) || !isOpaque(buf, x + 1, y - 1)) continue;
      const h = hash2(salt, x, y);
      if (h < 0.035) {
        setPixel(buf, x, y, tuft);
        setPixel(buf, x - 1, y - 1, tuft);
        setPixel(buf, x + 1, y - 1, tuft);
      } else if (flowers && h > 0.975) {
        setPixel(buf, x, y, h > 0.99 ? p.accent_alt : p.text);
        setPixel(buf, x, y + 1, tuft);
      }
    }
}

function soilTile(buf: PixelBuffer, p: Palette, seed: number): void {
  // Tilled rows along +x (2 px across per 1 px down): furrow, soil, ridge, soil; sprouts on the ridges.
  fillDiamond(buf, 16, 0, 1, 1, p.wall_shade);
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 32; x++) {
      if (!isOpaque(buf, x, y)) continue;
      const k = (((2 * y - x) % 8) + 8) % 8;
      if (k === 0 || k === 1) setPixel(buf, x, y, p.outline);
      else if (k === 4 || k === 5) setPixel(buf, x, y, (x + seed) % 7 === 0 ? p.floor : p.wall);
    }
}

function strawTile(buf: PixelBuffer, p: Palette, seed: number): void {
  fillDiamond(buf, 16, 0, 1, 1, p.metal_shade);
  for (let y = 1; y < 15; y++)
    for (let x = 1; x < 31; x++) {
      if (!isOpaque(buf, x, y) || !isOpaque(buf, x + 2, y)) continue;
      const h = hash2(seed + 31, x, y);
      if (h < 0.12) {
        setPixel(buf, x, y, p.metal);
        setPixel(buf, x + 1, y, p.metal);
      } else if (h > 0.97) setPixel(buf, x, y, p.wall_shade);
    }
}

const roomFloor: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  // The engine alternates seeds 0/1 (a mown-lawn checker); higher seeds pick grass, tilled soil or barn straw.
  if (seed < 2) return seed === 0 ? grassTile(buf, p.floor, p.floor_alt, 11, p) : grassTile(buf, p.floor_alt, p.floor, 23, p);
  const kind = seed % 3;
  if (kind === 0) soilTile(buf, p, seed);
  else if (kind === 1) strawTile(buf, p, seed);
  else grassTile(buf, p.floor, p.floor_alt, 11 + seed, p, true);
};

// ------------------------------------------------------------------------------------------------ wall

const roomWall: SlotDrawFn = (buf, ctx) => {
  // Timber planks along the N edge (2:1 slope): column t runs from row floor(t/2) to floor(t/2) + 32.
  const p = ctx.palette;
  for (let t = 0; t < 16; t++) {
    const y0 = t >> 1;
    for (let r = 0; r <= 32; r++) {
      let c: string;
      if (r === 0 || r === 32) c = p.outline;
      else if (r <= 2) c = r === 1 ? p.wall : p.wall_shade; // cap rail
      else {
        const k = (r - 3) % 6; // 5 px planks + a seam
        const plank = Math.floor((r - 3) / 6);
        if (k === 5) c = p.wall_shade;
        else if (k === 0) c = t % 3 === 1 ? p.text : p.wall; // sunlit upper edge
        else if (k === 2 && hash2(plank * 7 + 3, t >> 2, 0) < 0.45 && t % 4 !== 3) c = p.metal_shade; // grain
        else c = p.wall;
      }
      if (t === 0 && r > 0 && r < 32) c = p.wall_shade; // corner post
      if (t === 15 && r > 2 && r < 32 && (r - 3) % 6 === 2) c = p.outline; // nail
      setPixel(buf, t, y0 + r, c);
    }
  }
};

// ------------------------------------------------------------------------------------------------ rack

// rack_empty geometry (engine/sprites.ts): 1 x 1 box, 48 px tall, ground point (16, 48). S face = columns 0-15,
// starting at row 9 + floor(c / 2); E face = columns 16-31, starting at row 24 - floor(c / 2). Devices are pasted on
// face columns 2-13 (S) or 18-29 (E) when that face is the rack's front.
const faceTop = (c: number) => (c < 16 ? 9 + (c >> 1) : 24 - (c >> 1));

const rackEmpty: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const rot = ctx.variant.rotation ?? 0;
  const front = rot === 0 ? 'S' : rot === 3 ? 'E' : null;
  // Lid: boards along +x with seams, the far (top-left) board catching the most light.
  const topPaint = (x: number, y: number) => {
    const k = (((2 * y - x) % 10) + 10) % 10; // 2 px per row = an unbroken 2:1 line
    return k < 2 ? p.wall_shade : k < 4 ? p.metal : p.wall;
  };
  const facePaint = (x: number, y: number) => {
    const S = x < 16;
    const i = S ? x : x - 16; // 0..15 across the face
    const r = y - faceTop(x); // 0..47 down the face
    const base = S ? p.wall : p.wall_shade;
    const seam = S ? p.wall_shade : p.outline;
    if (front !== null && (front === 'S') === S) {
      // Cabinet front: a timber frame round a dark interior with shelves.
      if (i < 2 || i > 13 || r < 2 || r > 45) return i === 1 || i === 14 ? seam : base;
      return r % 8 === 7 ? p.wall_shade : p.outline;
    }
    // Side or back: vertical boards with two cross battens.
    if (r === 3 || r === 4 || r === 42 || r === 43) return r === 3 || r === 42 ? (S ? p.metal_shade : p.wall) : seam;
    return i % 4 === 3 ? seam : base;
  };
  for (let x = 0; x < 32; x++) for (let y = faceTop(x); y < faceTop(x) + 48; y++) setPixel(buf, x, y, facePaint(x, y));
  fillDiamond(buf, 16, 0, 1, 1, p.wall);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 32; x++) if (isOpaque(buf, x, y) && y < faceTop(x)) setPixel(buf, x, y, topPaint(x, y));
  // Sunlit rim along the top's two front edges, then the dark silhouette.
  for (let x = 0; x < 32; x++) setPixel(buf, x, faceTop(x) - 1, x < 16 ? p.text : p.metal);
  innerOutline(buf, p.outline);
  // The front corner edge between the faces.
  for (let y = faceTop(15); y < 63; y++) setPixel(buf, 15, y, front === 'S' ? p.wall_shade : p.outline);
  if (front === 'S') setPixel(buf, 3, faceTop(3) + 1, p.accent_alt); // lantern hook
  if (front === 'E') setPixel(buf, 28, faceTop(28) + 1, p.accent_alt);
  // A planned rack is the cabinet's ghost: half its pixels see-through, the outline kept.
  if (isGhost(ctx.variant.status)) ghostKnockout(buf);
};

// ------------------------------------------------------------------------------------------------ faceplates

type PlateKind = 'server' | 'switch' | 'router' | 'patch' | 'pdu';

function plate(kind: PlateKind, u: 1 | 2 | 4): SlotDrawFn {
  return (buf, ctx) => {
    const p = ctx.palette;
    const status = ctx.variant.status;
    const fallback = kind === 'switch' ? p.metal_shade : kind === 'router' ? p.accent : kind === 'patch' ? p.text : kind === 'pdu' ? p.wall_shade : p.wall;
    const body = ctx.variant.role_color ?? fallback;
    const H = 8 * u;
    const bodyPaint = (x: number, y: number) => (isGhost(status) ? checker(x, y, body, p.background) : body);
    // Board with a dark border, painted in the role colour; timber ears with nails each side.
    fillRect(buf, 0, 0, 64, H, p.outline);
    for (let y = 1; y < H - 1; y++) for (let x = 4; x < 60; x++) setPixel(buf, x, y, bodyPaint(x, y));
    for (const ex of [1, 60]) {
      fillRect(buf, ex, 1, 3, H - 2, p.wall);
      fillRect(buf, ex === 1 ? 3 : 60, 1, 1, H - 2, p.wall_shade);
      for (let y = 3; y < H - 2; y += 8) setPixel(buf, ex === 1 ? 2 : 61, y, p.outline);
    }
    lantern(buf, 55, 1, lanternColor(p, status), p);
    if (kind === 'switch') {
      // 2 groups of 8 ports, a link light over each live one.
      for (let k = 0; k < 16; k++) {
        const x = 6 + k * 3 + (k >= 8 ? 2 : 0);
        fillRect(buf, x, 4, 2, 2, p.outline);
        if (status === undefined || status === 'active') setPixel(buf, x + (k % 2), 2, (k * 5) % 7 < 4 ? p.led_ok : p.outline);
      }
    } else if (kind === 'router') {
      // 4 big ports and a two-headed weathervane arrow.
      for (let k = 0; k < 4; k++) {
        fillRect(buf, 7 + k * 5, 2, 4, 4, p.outline);
        setPixel(buf, 8 + k * 5, 3, p.wire);
      }
      line(buf, 30, 4, 46, 4, p.text);
      sprite(buf, ['..h', '.hh', 'hhh', '.hh', '..h'], 28, 2, { h: p.text });
      sprite(buf, ['h..', 'hh.', 'hhh', 'hh.', 'h..'], 46, 2, { h: p.text });
    } else if (kind === 'patch') {
      // A cream label strip and 24 small jacks; no lights but the lantern.
      fillRect(buf, 6, 2, 46, 1, p.text);
      for (let x = 7; x < 51; x += 4) setPixel(buf, x, 2, p.metal_shade);
      for (let k = 0; k < 24; k++) {
        const x = 6 + k * 2 + (k >= 12 ? 1 : 0);
        setPixel(buf, x, 4, p.outline);
        setPixel(buf, x, 5, p.outline);
      }
    } else if (kind === 'pdu') {
      // 7 cream sockets with two pins each, and a red rocker switch.
      for (let k = 0; k < 7; k++) {
        const x = 7 + k * 6;
        fillRect(buf, x, 2, 4, 4, p.text);
        setPixel(buf, x + 1, 3, p.outline), setPixel(buf, x + 2, 3, p.outline);
        setPixel(buf, x + 1, 4, p.metal_shade), setPixel(buf, x + 2, 4, p.metal_shade);
      }
      fillRect(buf, 49, 2, 3, 4, p.outline);
      fillRect(buf, 50, 2, 1, 2, p.accent);
    } else serverDetail(buf, u, status, p);
    if (status === 'failed') crack(buf, H, p);
  };
}

/** A lantern, 5 x 6 at (x, y): cap, a 3 x 3 glowing pane in a dark frame, base. 9 px of glow, so every status
 *  differs from active by at least 9 native px even at 1x rack zoom. */
function lantern(buf: PixelBuffer, x: number, y: number, glow: string, p: Palette): void {
  fillRect(buf, x + 1, y, 3, 1, p.wall_shade);
  fillRect(buf, x, y + 1, 5, 3, p.outline);
  fillRect(buf, x + 1, y + 1, 3, 3, glow);
  fillRect(buf, x + 1, y + 4, 3, 1, p.wall_shade);
  if (glow !== p.outline) setPixel(buf, x + 1, y + 1, p.highlight); // glint on a lit pane
}

/** Failed: a split runs across the board (a shape cue that does not depend on telling red from green). */
function crack(buf: PixelBuffer, H: number, p: Palette): void {
  // A jagged 2 px dark split across the board, irregular so it reads as damage rather than a painted pattern.
  const jag = [0, 0, -1, -1, -1, 0, 1, 1, 1, 0, 0, -1, 0, 1, 1, 0, -1, -1, 0, 0, 1, 0, -1];
  const mid = (H >> 1) - 1;
  for (let x = 6; x < 53; x++) {
    const y = mid + jag[(x - 6) % jag.length]!;
    setPixel(buf, x, y, p.outline);
    setPixel(buf, x, y + 1, p.outline);
  }
}

/** Generic servers: drive bays drawn as little hay bales; 4U adds a barn door with an X brace. */
function serverDetail(buf: PixelBuffer, u: 1 | 2 | 4, status: string | undefined, p: Palette): void {
  const bale = (x: number, y: number, w: number, h: number) => {
    fillRect(buf, x, y, w, h, p.outline);
    fillRect(buf, x + 1, y + 1, w - 2, h - 2, p.metal);
    for (let i = x + 1; i < x + w - 1; i++) setPixel(buf, i, y + h - 2, p.metal_shade);
    setPixel(buf, x + 1, y + 1, p.highlight);
    if (h >= 5) for (let j = y + 1; j < y + h - 1; j++) setPixel(buf, x + w - 3, j, p.wall_shade); // twine
  };
  const second = lanternColor(p, status);
  if (u === 1) {
    for (let k = 0; k < 6; k++) bale(6 + k * 7, 2, 6, 4);
    fillRect(buf, 49, 3, 3, 2, p.outline);
    return;
  }
  if (u === 2) {
    for (let r = 0; r < 2; r++) for (let k = 0; k < 6; k++) bale(6 + k * 7, 2 + r * 6, 6, 6);
    lantern(buf, 55, 9, second, p);
    return;
  }
  const dx = 6, dy = 3, dw = 22, dh = 26;
  fillRect(buf, dx, dy, dw, dh, p.outline);
  for (let y = dy + 1; y < dy + dh - 1; y++) for (let x = dx + 1; x < dx + dw - 1; x++) setPixel(buf, x, y, (x - dx) % 3 === 0 ? p.outline : p.wall_shade);
  const fr = p.text;
  fillRect(buf, dx + 1, dy + 1, dw - 2, 1, fr), fillRect(buf, dx + 1, dy + dh - 2, dw - 2, 1, fr);
  fillRect(buf, dx + 1, dy + 1, 1, dh - 2, fr), fillRect(buf, dx + dw - 2, dy + 1, 1, dh - 2, fr);
  line(buf, dx + 1, dy + 1, dx + dw - 2, dy + dh - 2, fr);
  line(buf, dx + dw - 2, dy + 1, dx + 1, dy + dh - 2, fr);
  for (let r = 0; r < 4; r++) for (let k = 0; k < 3; k++) bale(31 + k * 7, 3 + r * 6, 6, 6);
  lantern(buf, 55, 24, second, p);
}

const rackUnit: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // Empty U: the dark inside of the cabinet with a back-board seam; timber posts with a peg hole each side.
  fillRect(buf, 8, 0, 64, 8, p.outline);
  for (let x = 8; x < 72; x++) setPixel(buf, x, 7, x % 9 === 0 ? p.outline : p.background);
  for (const x0 of [0, 72]) {
    fillRect(buf, x0, 0, 8, 8, p.wall);
    fillRect(buf, x0, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 7, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 6, 0, 1, 8, p.wall_shade);
    fillRect(buf, x0 + 1, 0, 1, 8, p.text);
    fillRect(buf, x0 + 3, 3, 2, 2, p.outline);
    setPixel(buf, x0 + 3, 5, p.metal_shade);
    setPixel(buf, x0 + 4, 5, p.metal_shade);
    setPixel(buf, x0 + 2, 7, p.wall_shade);
  }
};

const port: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const s = ctx.variant.status;
  const frame = s === 'mgmt' ? p.accent_alt : s === 'disabled' ? p.wall_shade : p.wall;
  fillRect(buf, 0, 0, 8, 8, p.outline);
  fillRect(buf, 1, 1, 6, 6, frame);
  setPixel(buf, 1, 1, p.text);
  fillRect(buf, 2, 2, 4, 4, p.outline);
  if (s === 'connected') {
    const c = ctx.variant.wire_color ?? p.wire;
    fillRect(buf, 2, 2, 4, 3, c);
    setPixel(buf, 2, 2, p.highlight);
    fillRect(buf, 3, 5, 2, 2, c);
  } else if (s === 'disabled') {
    for (let k = 0; k < 4; k++) setPixel(buf, 2 + k, 2 + k, p.led_err), setPixel(buf, 5 - k, 2 + k, p.led_err);
  } else if (s === 'mgmt') {
    setPixel(buf, 3, 3, p.text), setPixel(buf, 4, 3, p.text);
  }
};

// ------------------------------------------------------------------------------------------------ wires (hoses)

function hose(kind: 'straight' | 'corner' | 'cross'): SlotDrawFn {
  return (buf, ctx) => {
    const p = ctx.palette;
    const c = ctx.variant.wire_color ?? p.wire;
    const rot = ctx.variant.rotation ?? 0;
    const pts: [number, number][] = [];
    const alongX = (t: number) => pts.push([8 + t, 4 + Math.floor(t / 2)]);
    const alongY = (t: number) => pts.push([23 - t, 4 + Math.floor(t / 2)]);
    if (kind === 'straight') for (let t = 0; t < 16; t++) (rot % 2 === 0 ? alongX : alongY)(t);
    if (kind === 'cross') {
      for (let t = 0; t < 16; t++) alongY(t);
      drawHose(buf, pts, c, p);
      pts.length = 0;
      for (let t = 0; t < 16; t++) alongX(t);
    }
    // Corner (theme-slots.json): rotation 0 joins -x and +y; each rotation turns 90 degrees clockwise on screen.
    if (kind === 'corner')
      for (let t = 0; t < 8; t++) {
        alongX(t + (rot >= 2 ? 8 : 0));
        alongY(t + (rot === 0 || rot === 3 ? 8 : 0));
      }
    drawHose(buf, pts, c, p);
  };
}

/** A 2 px hose in a dark 1 px casing above and below (so any cable colour reads on grass), with a brass coupling
 *  at the tile centre. The body is unbroken: glints would read as a dashed line at world zoom. */
function drawHose(buf: PixelBuffer, pts: readonly [number, number][], c: string, p: Palette): void {
  for (const [x, y] of pts) setPixel(buf, x, y - 2, p.outline), setPixel(buf, x, y + 1, p.outline);
  for (const [x, y] of pts) setPixel(buf, x, y, c), setPixel(buf, x, y - 1, c);
  for (const [x, y] of pts) if (x === 15 || x === 16) setPixel(buf, x, y - 1, p.metal), setPixel(buf, x, y, p.metal_shade);
}

// ------------------------------------------------------------------------------------------------ props

const SHEEP = [
  '...hh.hh.....',
  '..hwwhwwhwB..',
  '.hwwwwwwwwBB.',
  '.hwwwwwwwTBhB',
  '.wwwwwwwwTBBB',
  '.wwwwwwwsTBB.',
  '..sswwsss....',
  '..B.B..B.B...',
];
const CHICKEN = [
  '......rr..',
  '.....rhh..',
  '.....hoh..',
  'h....hhhpp',
  'hh..hhwh..',
  'hwhhwwww..',
  '.wwTTTww..',
  '.swwTTws..',
  '..sswws...',
  '...p..p...',
  '..pp.pp...',
];
const PUMPKIN = [
  '.....B..G...',
  '.....BGGg...',
  '...pTTTpp...',
  '.pphppprppp.',
  'phhppprpppr.',
  'phppprpppprr',
  'pppppprpppr.',
  '.rpppprpprr.',
  '..rrrrrrr...',
];

const decorationSmall: SlotDrawFn = (buf, ctx) => {
  // Loose devices are drawn with this slot, so the tag colour (collar, wing, ribbon, twine) is the role colour.
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const tag = ctx.variant.role_color ?? p.accent;
  const keys = keysOf(p, tag);
  const flip = ((seed >>> 2) & 1) === 1;
  switch (seed % 4) {
    case 0:
      sprite(buf, SHEEP, 1, 6, keys, flip);
      break;
    case 1:
      sprite(buf, CHICKEN, 3, 3, keys, flip);
      break;
    case 2:
      sprite(buf, PUMPKIN, 2, 5, keys, flip);
      break;
    default:
      isoBlock(buf, 8, 7, 3, 4, 6, {
        top: (x, y) => (hash2(41, x, y) < 0.3 ? p.highlight : p.metal),
        left: (x, y) => ((x + y) % 3 === 0 ? p.metal_shade : p.metal),
        right: p.metal_shade,
      });
      for (let y = 6; y < 16; y++) for (const x of [4, 11]) if (isOpaque(buf, x, y)) setPixel(buf, x, y, tag);
  }
  // A loose device that needs attention flies a small lantern-coloured pennant over its prop.
  const st = ctx.variant.status;
  if (st === 'failed' || st === 'decommissioning') {
    const c = lanternColor(p, st);
    setPixel(buf, 13, 1, c), setPixel(buf, 14, 1, c), setPixel(buf, 13, 2, c);
    for (let y = 1; y < 6; y++) setPixel(buf, 12, y, p.wall_shade);
  }
  outline(buf, p.outline);
  if (isGhost(st)) ghostKnockout(buf, true);
};

const decorationLarge: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  switch (seedOf(ctx) % 4) {
    case 0:
      windmill(buf, p);
      break;
    case 1:
      silo(buf, p, 16, 41, 7, 30);
      break;
    case 2:
      bigHay(buf, p);
      break;
    default:
      appleTree(buf, p);
  }
  outline(buf, p.outline);
};

function windmill(buf: PixelBuffer, p: Palette): void {
  // Tapered timber tower, gabled red cap, four lattice sails on a hub.
  const top = 14, bottom = 42;
  for (let y = top; y <= bottom; y++) {
    const half = 4 + Math.floor(((y - top) * 3) / (bottom - top));
    for (let x = 16 - half; x < 16 + half; x++) {
      let c = x < 16 ? p.wall : p.wall_shade;
      if ((y - top) % 5 === 4) c = x < 16 ? p.metal_shade : p.outline;
      setPixel(buf, x, y, c);
    }
  }
  fillRect(buf, 14, 36, 4, 7, p.outline); // door
  fillRect(buf, 15, 35, 2, 1, p.outline);
  fillRect(buf, 15, 24, 2, 3, p.outline); // window
  setPixel(buf, 15, 24, p.accent_alt);
  fillPolygon(buf, [[9, 15], [16, 8], [23, 15]], p.accent); // cap
  line(buf, 9, 15, 16, 8, p.text);
  line(buf, 17, 9, 23, 15, p.wall_shade);
  const hx = 16, hy = 13;
  const arms: [number, number, number, number][] = [[-1, -1, 1, 0], [1, -1, 0, 1], [1, 1, -1, 0], [-1, 1, 0, -1]];
  for (const [dx, dy, sx, sy] of arms)
    for (let k = 2; k <= 12; k++) {
      const x = hx + dx * k, y = hy + dy * k;
      setPixel(buf, x, y, p.wall_shade);
      if (k >= 4) for (let j = 1; j <= 3; j++) setPixel(buf, x + sx * j, y + sy * j, (k + j) % 3 === 0 ? p.metal_shade : p.text);
    }
  fillRect(buf, hx - 1, hy - 1, 3, 3, p.outline);
  setPixel(buf, hx, hy, p.metal);
}

function silo(buf: PixelBuffer, p: Palette, cx: number, groundY: number, r: number, height: number): void {
  // Cream cylinder (2:1 elliptical ends) shaded left to right, hoop bands that follow the curve, red dome.
  const ramp = [p.highlight, p.text, p.text, p.metal, p.metal_shade];
  const e = (dx: number) => Math.round(Math.sqrt(Math.max(0, 1 - (dx / (r + 0.5)) ** 2)) * (r / 2));
  const topY = groundY - height;
  for (let x = cx - r; x <= cx + r; x++) {
    const dx = x - cx;
    const t = (x - (cx - r)) / (2 * r + 1);
    const shade = ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))]!;
    const off = e(dx);
    for (let y = topY + off; y <= groundY + off; y++) {
      const band = (y - off - topY) % 7 === 6;
      setPixel(buf, x, y, band ? (t < 0.6 ? p.metal_shade : p.wall_shade) : shade);
    }
    const dh = Math.round(Math.sqrt(Math.max(0, 1 - (dx / (r + 0.5)) ** 2)) * (r * 0.9));
    for (let y = topY - dh; y < topY + off; y++) setPixel(buf, x, y, t < 0.3 ? (y < topY - dh + 2 ? p.text : p.accent) : t < 0.75 ? p.accent : p.wall_shade);
    setPixel(buf, x, topY + off, p.outline);
  }
  for (let y = topY + 4; y < groundY; y += 2) setPixel(buf, cx - r + 2, y + e(-r + 2), p.wall_shade); // ladder
}

function bigHay(buf: PixelBuffer, p: Palette): void {
  const faces = (k: number): FaceColors => ({
    top: (x, y) => (hash2(60 + k, x, y) < 0.3 ? p.highlight : (x + 2 * y) % 6 === 0 ? p.metal_shade : p.metal),
    left: (x, y) => ((x + y) % 4 === 0 ? p.metal_shade : hash2(70 + k, x, y) < 0.15 ? p.text : p.metal),
    right: (x, y) => ((y - x) % 3 === 0 ? p.wall_shade : p.metal_shade),
  });
  // Two bales on the ground, one on top, a pitchfork leaning on the right.
  isoBlock(buf, 12, 30, 5, 5, 10, faces(0));
  isoBlock(buf, 21, 33, 5, 5, 10, faces(1));
  isoBlock(buf, 16, 22, 5, 5, 10, faces(2));
  line(buf, 28, 41, 26, 20, p.wall_shade);
  sprite(buf, ['w.w.w', 'wwwww', '..w..'], 23, 17, { w: p.text });
}

function appleTree(buf: PixelBuffer, p: Palette): void {
  fillRect(buf, 14, 26, 4, 16, p.wall_shade); // trunk
  fillRect(buf, 14, 26, 1, 16, p.wall);
  setPixel(buf, 13, 41, p.wall_shade), setPixel(buf, 18, 41, p.wall_shade);
  // Canopy: three overlapping blobs lit from the top-left.
  const blobs: [number, number, number][] = [[11, 18, 8], [21, 17, 8], [16, 11, 9]];
  for (let y = 0; y < 34; y++)
    for (let x = 1; x < 31; x++) {
      let inside = false, light = 99;
      for (const [bx, by, br] of blobs) {
        if (Math.hypot(x - bx, y - by) <= br) {
          inside = true;
          light = Math.min(light, Math.hypot(x - (bx - br * 0.45), y - (by - br * 0.45)) / br);
        }
      }
      if (!inside) continue;
      const n = hash2(88, x, y) * 0.35;
      setPixel(buf, x, y, light + n < 0.75 ? p.floor : light + n < 1.35 ? p.floor_alt : p.background);
    }
  for (const [ax, ay] of [[9, 15], [20, 12], [24, 20], [14, 22], [17, 7]] as const) {
    setPixel(buf, ax, ay, p.accent);
    setPixel(buf, ax + 1, ay, p.accent);
    setPixel(buf, ax, ay - 1, p.highlight);
  }
}

// ------------------------------------------------------------------------------------------------ site buildings

type Iso = (x: number, y: number, z?: number) => Pt;
const SITE: Iso = (x, y, z = 0) => [Math.round(32 + (x - y) * 16), Math.round(48 + (x + y) * 8 - z)];

const siteBuilding: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const v = seedOf(ctx) % 3;
  if (v === 0) barn(buf, p, 0.15, 0.2, 1.7, 1.6, 26, 18);
  else if (v === 1) {
    barn(buf, p, 0.1, 0.35, 1.3, 1.5, 24, 16);
    const [sx, sy] = SITE(1.6, 0.45);
    silo(buf, p, sx, sy, 7, 44);
  } else {
    const [ax, ay] = SITE(0.55, 0.45);
    silo(buf, p, ax, ay, 7, 46);
    const [bx, by] = SITE(1.45, 0.5);
    silo(buf, p, bx, by, 6, 38);
    shed(buf, p);
  }
  outline(buf, p.outline);
};

function barn(buf: PixelBuffer, p: Palette, x0: number, y0: number, w: number, h: number, H: number, rise: number): void {
  const P: Iso = (x, y, z = 0) => SITE(x0 + x, y0 + y, z);
  const R = H + rise;
  const mid = w / 2;
  // Gable faces +y (screen left, mid tone); the ridge runs along y.
  const redL = (x: number) => (x % 3 === 0 ? p.wall_shade : p.accent);
  const redR = (x: number, y: number) => (x % 3 === 0 ? p.outline : checker(x, y, p.accent, p.wall_shade));
  fillPolygon(buf, [P(0, 0, H), P(0, h, H), P(mid, h, R), P(mid, 0, R)], p.wall); // far roof slope (lit)
  fillPolygon(buf, [P(w, 0, 0), P(w, h, 0), P(w, h, H), P(w, 0, H)], redR);
  fillPolygon(buf, [P(0, h, 0), P(w, h, 0), P(w, h, H), P(mid, h, R), P(0, h, H)], redL);
  fillPolygon(buf, [P(mid, 0, R), P(mid, h, R), P(w, h, H), P(w, 0, H)], (x, y) => ((y + Math.floor(x / 2)) % 4 === 0 ? p.outline : p.wall_shade));
  // Cream trim.
  line(buf, ...P(0, h, H), ...P(mid, h, R), p.text);
  line(buf, ...P(mid, h, R), ...P(w, h, H), p.text);
  line(buf, ...P(w, h, H), ...P(w, 0, H), p.metal_shade);
  line(buf, ...P(w, h, 0), ...P(w, h, H), p.text);
  line(buf, ...P(0, h, 0), ...P(0, h, H), p.text);
  line(buf, ...P(mid, 0, R), ...P(mid, h, R), p.metal);
  // Big door with an X brace on the gable, hayloft door above.
  const dw = Math.min(0.32, w * 0.2);
  const door = [P(mid - dw, h, 0), P(mid + dw, h, 0), P(mid + dw, h, 15), P(mid - dw, h, 15)] as const;
  fillPolygon(buf, [...door], p.wall_shade);
  for (const [a, b] of [[0, 3], [1, 2], [3, 2], [0, 2], [1, 3]] as const) line(buf, ...door[a], ...door[b], p.text);
  line(buf, ...P(mid, h, 1), ...P(mid, h, 14), p.outline);
  const lw = dw * 0.55;
  const loft = [P(mid - lw, h, H - 2), P(mid + lw, h, H - 2), P(mid + lw, h, H + 6), P(mid - lw, h, H + 6)] as const;
  fillPolygon(buf, [...loft], p.outline);
  for (const [a, b] of [[0, 1], [3, 2], [0, 3], [1, 2]] as const) line(buf, ...loft[a], ...loft[b], p.text);
  // Two windows on the long side.
  for (const f of [0.3, 0.7]) {
    const [wx, wy] = P(w, h * f, 14);
    fillRect(buf, wx - 1, wy - 1, 4, 5, p.text);
    fillRect(buf, wx, wy, 2, 3, p.outline);
    setPixel(buf, wx, wy, p.accent_alt);
  }
}

function shed(buf: PixelBuffer, p: Palette): void {
  // A little timber lean-to in front of the silos.
  const Q: Iso = (x, y, z = 0) => SITE(0.9 + x, 1.05 + y, z);
  const w = 0.75, h = 0.7, H = 12;
  fillPolygon(buf, [Q(w, 0, 0), Q(w, h, 0), Q(w, h, H), Q(w, 0, H + 4)], (x) => (x % 3 === 0 ? p.outline : p.wall_shade));
  fillPolygon(buf, [Q(0, h, 0), Q(w, h, 0), Q(w, h, H), Q(0, h, H + 4)], (x) => (x % 3 === 0 ? p.wall_shade : p.wall));
  fillPolygon(buf, [Q(0, 0, H + 6), Q(w + 0.1, 0, H + 6), Q(w + 0.1, h + 0.1, H + 1), Q(0, h + 0.1, H + 1)], (x, y) => ((x + 2 * y) % 5 === 0 ? p.wall_shade : p.accent));
  const [dx, dy] = Q(w * 0.5, h, 0);
  fillRect(buf, dx - 2, dy - 9, 4, 8, p.outline);
}

// ------------------------------------------------------------------------------------------------ UI

const uiFrame: SlotDrawFn = (buf, ctx) => {
  // A timber frame: sunlit top/left boards, shaded bottom/right, nails in the corners. The middle third of every
  // edge is uniform along its length, so the 9-slice stretches cleanly.
  const p = ctx.palette;
  fillRect(buf, 0, 0, 24, 24, p.outline);
  fillRect(buf, 1, 1, 22, 22, p.wall);
  fillRect(buf, 1, 1, 22, 1, p.text);
  fillRect(buf, 1, 1, 1, 22, p.metal);
  fillRect(buf, 1, 22, 22, 1, p.wall_shade);
  fillRect(buf, 22, 1, 1, 22, p.wall_shade);
  fillRect(buf, 4, 4, 16, 16, p.outline);
  fillRect(buf, 5, 5, 14, 14, p.background);
  for (const [x, y] of [[2, 2], [20, 2], [2, 20], [20, 20]] as const) {
    fillRect(buf, x, y, 2, 2, p.outline);
    setPixel(buf, x, y, p.metal_shade);
  }
};

const cursor: SlotDrawFn = (buf, ctx) => {
  // Corner brackets of the tile outline in sunlit white, each backed by a dark line inside so they read on grass.
  const p = ctx.palette;
  for (let r = 0; r < 16; r++) {
    if (!(r < 4 || r > 11 || (r >= 6 && r <= 9))) continue;
    const reach = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
    for (const x of [16 - reach, 17 - reach, 14 + reach, 15 + reach]) setPixel(buf, x, r, p.highlight);
    if (reach > 2) for (const x of [18 - reach, 13 + reach]) setPixel(buf, x, r, p.outline);
  }
};

const selectionHighlight: SlotDrawFn = (buf, ctx) => {
  // A pumpkin ring 1 px outside the tile with a sunlit dashed inner edge; the tile itself stays clear.
  const p = ctx.palette;
  const tile = createPixelBuffer(34, 18);
  fillDiamond(tile, 17, 1, 1, 1, '#000000');
  const ring = createPixelBuffer(34, 18);
  fillDiamond(ring, 17, 1, 1, 1, '#000000');
  outline(ring, '#ffffff');
  const edge = createPixelBuffer(34, 18);
  fillDiamond(edge, 17, 1, 1, 1, '#000000');
  innerOutline(edge, '#ffffff');
  for (let y = 0; y < 18; y++)
    for (let x = 0; x < 34; x++) {
      if (isOpaque(ring, x, y) && !isOpaque(tile, x, y)) setPixel(buf, x, y, p.accent_alt);
      else if (edge.data[(y * 34 + x) * 4] === 255) setPixel(buf, x, y, (x + y) % 4 < 2 ? p.highlight : p.metal);
    }
};

const slots: Record<SlotId, SlotDrawFn> = {
  site_building: siteBuilding,
  room_floor: roomFloor,
  room_wall: roomWall,
  rack_empty: rackEmpty,
  device_1u: plate('server', 1),
  device_2u: plate('server', 2),
  device_4u: plate('server', 4),
  switch: plate('switch', 1),
  router: plate('router', 1),
  patch_panel: plate('patch', 1),
  psu: plate('pdu', 1),
  rack_unit: rackUnit,
  port,
  wire_straight: hose('straight'),
  wire_corner: hose('corner'),
  wire_cross: hose('cross'),
  decoration_small: decorationSmall,
  decoration_large: decorationLarge,
  ui_frame: uiFrame,
  cursor,
  selection_highlight: selectionHighlight,
};

export const farmTheme: Theme = { id: 'farm', name: 'Farm', palette, slots };
