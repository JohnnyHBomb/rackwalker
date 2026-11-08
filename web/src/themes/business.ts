// Theme 'business' (General business): a tidy modern office. Carpet and raised-floor tiles, plasterboard walls with a
// glass band, glass-and-plaster office towers and business parks, clean black cabinets with blue LEDs, cables in neat
// trays, and office props (plants, water cooler, coffee machine, chair, printer, desk, copier).
// Original, code-generated art (8 Nov 2026): every pixel is a palette key, or the variant's NetBox role/cable colour.
// Light comes from the top-left: top faces lightest, left (+y) faces mid, right (+x) faces darkest.
import {
  fillDiamond,
  fillRect,
  setPixel,
  type DrawContext,
  type Palette,
  type PaletteKey,
  type PixelBuffer,
  type SlotDrawFn,
  type SlotId,
  type Theme,
} from '../contracts/theme';
import { clearPixel, getPixel, hash2, isOpaque, outline, seedOf } from './kit';

// Original 16-colour office palette: navy, slate, plaster white, one corporate accent (signal orange), cabinet black.
// The glass is a muted steel blue, deliberately quieter than the orange accent.
const palette: Palette = {
  background: '#172038', // deep navy (sky / void)
  outline: '#0b1120', // ink navy
  floor: '#5b6782', // slate carpet
  floor_alt: '#4d5872', // darker carpet / seams
  wall: '#e3e7ee', // plasterboard
  wall_shade: '#a7b1c3', // plaster in shade
  metal: '#2c3346', // cabinet black
  metal_shade: '#1c2233', // cabinet black in shade
  accent: '#f08a3c', // the one corporate accent
  accent_alt: '#7290b4', // steel-blue glass, activity LEDs
  led_ok: '#45c07c', // LED ok, foliage
  led_warn: '#f2c443', // LED warn
  led_err: '#e2475a', // LED error
  wire: '#7ccfdc', // default cable
  text: '#f2f4f8',
  highlight: '#fdfefe',
};

type P = Palette;

// ------------------------------------------------------------------------------------------------ helpers

const reach = (r: number, n8: number) => (r < n8 ? 2 * (r + 1) : 2 * n8 - 2 * (r - n8));

type FacePaint = (u: number, z: number, x: number, y: number) => string | null;
type TopPaint = (tx: number, ty: number, x: number, y: number) => string | null;

/**
 * An iso box like kit.isoBox, but the faces are painted in face coordinates: u = px along the face from its far
 * (left) end, z = px above the ground. The top gets tile coordinates (tx, ty in tiles from its top corner). A paint
 * returning null leaves the pixel as it is.
 */
function box(buf: PixelBuffer, ax: number, ay: number, w: number, h: number, height: number, top: TopPaint, left: FacePaint, right: FacePaint): void {
  const w8 = w * 8, h8 = h * 8;
  const low = new Map<number, number>();
  const high = new Map<number, number>();
  for (let r = 0; r < w8 + h8; r++)
    for (let x = ax - reach(r, h8); x < ax + reach(r, w8); x++) {
      low.set(x, ay + r);
      if (!high.has(x)) high.set(x, ay + r);
    }
  const split = ax + (w - h) * 16;
  const leftEnd = ax - h * 16;
  for (const [x, lo] of low) {
    for (let z = 0; z < height; z++) {
      const y = lo - z;
      const c = x < split ? left(x - leftEnd, z, x, y) : right(x - split, z, x, y);
      if (c) setPixel(buf, x, y, c);
    }
    for (let y = high.get(x)! - height; y <= lo - height; y++) {
      const dx = (x + 0.5 - ax) / 16, dy = (y + 0.5 - (ay - height)) / 8;
      const c = top((dx + dy) / 2, (dy - dx) / 2, x, y);
      if (c) setPixel(buf, x, y, c);
    }
  }
}

/** Ground point that centres a w x h footprint on (cx, cy). */
const centred = (cx: number, cy: number, w: number, h: number): [number, number] => [Math.round(cx - (w - h) * 8), Math.round(cy - (w + h) * 4)];

const flat = (c: string): FacePaint => () => c;
const flatTop = (c: string): TopPaint => () => c;

/** Draws an ASCII pixel map; '.' is transparent. */
function drawMap(buf: PixelBuffer, rows: readonly string[], ox: number, oy: number, col: (ch: string) => string | null): void {
  rows.forEach((row, j) => {
    for (let i = 0; i < row.length; i++) {
      const ch = row[i]!;
      if (ch === '.' || ch === ' ') continue;
      const c = col(ch);
      if (c) setPixel(buf, ox + i, oy + j, c);
    }
  });
}

const KEY: Record<string, PaletteKey> = {
  o: 'outline', w: 'wall', s: 'wall_shade', h: 'highlight', m: 'metal', M: 'metal_shade', a: 'accent', b: 'accent_alt',
  g: 'led_ok', y: 'led_warn', r: 'led_err', f: 'floor', F: 'floor_alt', k: 'background', W: 'wire', t: 'text',
};
const mapColour = (p: P, role?: string) => (ch: string) => (ch === 'R' ? role ?? p.accent : KEY[ch] ? p[KEY[ch]!] : null);

/**
 * Status rule for device slots (proposed rule): active led_ok; planned/staged a ghost (LED unlit);
 * offline/inventory unlit and dimmed; failed led_err plus a dashed red border (a shape cue, not only a hue);
 * decommissioning led_warn plus a dashed amber bottom edge. Every non-active state changes at least 4 native px.
 */
const ghosted = (status: string | undefined) => status === 'planned' || status === 'staged';
const dark = (status: string | undefined) => status === 'offline' || status === 'inventory' || status === 'deprecated' || ghosted(status);

/** Status LED colour for a device or rack status. */
function led(p: P, status: string | undefined): string {
  if (status === 'failed') return p.led_err;
  if (status === 'decommissioning') return p.led_warn;
  if (dark(status)) return p.metal_shade;
  return p.led_ok;
}

/** Planned objects are drawn as a ghost: every other body pixel removed (outline kept). */
function ghost(buf: PixelBuffer, p: P): void {
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++) if ((x + y) % 2 === 0 && isOpaque(buf, x, y) && getPixel(buf, x, y) !== p.outline) clearPixel(buf, x, y);
}

// ------------------------------------------------------------------------------------------------ site building

/** Curtain-wall glass: mullions every 4 px, spandrel every 6 px, a diagonal glint on the lit face. */
function curtain(p: P, lit: boolean, seed: number, crown: number): FacePaint {
  return (u, z, x, y) => {
    if (z >= crown) return z === crown ? (lit ? p.wall_shade : p.metal) : lit ? p.wall : p.wall_shade;
    if (z % 6 === 0) return lit ? p.wall : p.wall_shade;
    if (u % 4 === 0) return lit ? p.wall_shade : p.floor_alt;
    if (lit) {
      const g = (u + z) % 14;
      if (g === 0 || g === 1) return p.highlight;
      return p.accent_alt;
    }
    // shaded glass: a dither of blue and navy, with a few lit offices
    if (hash2(seed, Math.floor(u / 4), Math.floor(z / 6)) < 0.12) return p.led_warn;
    return (x + y) % 2 === 0 ? p.accent_alt : p.background;
  };
}

/** Ribbon windows on plaster: a glass band every `floorH` px. */
function ribbon(p: P, lit: boolean, floorH: number, from = 3): FacePaint {
  return (u, z, x, y) => {
    const k = (z - from + floorH * 4) % floorH;
    if (z >= from && k >= 1 && k <= floorH - 3) {
      if (u % 6 === 0) return lit ? p.wall : p.wall_shade;
      return lit ? ((u + z) % 11 === 0 ? p.highlight : p.accent_alt) : (x + y) % 2 === 0 ? p.accent_alt : p.background;
    }
    return lit ? p.wall : p.wall_shade;
  };
}

/** A 2px paved plaza under the whole 2x2 site. */
function plaza(buf: PixelBuffer, p: P, grass: boolean): void {
  box(
    buf, 32, 46, 2, 2, 2,
    (tx, ty, x, y) => (grass && tx > 1.05 && ty > 1.05 ? (hash2(3, x, y) < 0.05 ? p.highlight : p.led_ok) : (Math.floor(tx * 4) + Math.floor(ty * 4)) % 2 ? p.wall_shade : p.wall),
    flat(p.floor), flat(p.floor_alt),
  );
}

function door(p: P, at: number, width: number, lit: boolean): (f: FacePaint) => FacePaint {
  return (f) => (u, z, x, y) => {
    if (u >= at && u < at + width && z < 7) return z === 6 ? p.accent : u === at || u === at + width - 1 ? (lit ? p.wall_shade : p.floor_alt) : lit ? p.background : p.outline;
    return f(u, z, x, y);
  };
}

function roof(p: P): TopPaint {
  return (tx, ty) => (tx < 0.08 || ty < 0.08 ? p.highlight : p.wall_shade);
}

const site_building: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const kind = seed % 3;
  if (kind === 0) {
    // Glass tower on a plaster podium, orange crown band.
    plaza(buf, p, false);
    const [px, py] = centred(32, 62, 1.75, 1.75);
    box(buf, px, py, 1.75, 1.75, 8, flatTop(p.wall_shade), door(p, 10, 6, true)(ribbon(p, true, 8, 9)), ribbon(p, false, 8, 9));
    const [tx, ty] = centred(32, 62 - 8, 1.25, 1.25);
    box(buf, tx, ty, 1.25, 1.25, 38, roof(p), curtain(p, true, seed, 33), curtain(p, false, seed, 33));
    // crown logo: an accent band on the lit face
    box(buf, tx, ty - 34, 1.25, 1.25, 2, () => null, flat(p.accent), flat(p.accent));
    // rooftop plant room
    const [rx, ry] = centred(32, 62 - 8 - 38, 0.5, 0.375);
    box(buf, rx, ry, 0.5, 0.375, 5, flatTop(p.highlight), (u, z) => (z === 2 && u % 2 === 1 ? p.metal : p.wall), (u, z) => (z === 2 && u % 2 === 0 ? p.metal_shade : p.wall_shade));
  } else if (kind === 1) {
    // Business park: an L of two low blocks round a lawn with a tree.
    plaza(buf, p, true);
    box(buf, 32, 44, 2, 1, 16, roof(p), ribbon(p, true, 7, 2), ribbon(p, false, 7, 2));
    box(buf, 32 - 16, 44 + 8, 1, 1, 12, roof(p), door(p, 5, 6, true)(ribbon(p, true, 7, 2)), ribbon(p, false, 7, 2));
    // company sign on the long block's roof edge
    fillRect(buf, 40, 30, 8, 3, p.accent);
    setPixel(buf, 41, 31, p.highlight);
    // tree on the lawn
    tree(buf, p, 31, 69, seed);
  } else {
    // Mid-rise slab with a stepped top and an entrance canopy.
    plaza(buf, p, false);
    const [sx, sy] = centred(32, 62, 1.75, 1.25);
    box(buf, sx, sy, 1.75, 1.25, 30, roof(p), door(p, 8, 6, true)(ribbon(p, true, 6, 8)), ribbon(p, false, 6, 8));
    const [ux, uy] = centred(32, 62 - 30, 1.0, 0.75);
    box(buf, ux, uy, 1.0, 0.75, 10, roof(p), curtain(p, true, seed, 8), curtain(p, false, seed, 8));
    // flag of the accent on the roof
    fillRect(buf, 46, 12, 1, 12, p.wall_shade);
    fillRect(buf, 47, 12, 5, 3, p.accent);
  }
  outline(buf, p.outline);
};

function tree(buf: PixelBuffer, p: P, cx: number, base: number, seed: number): void {
  fillRect(buf, cx, base - 6, 2, 6, p.metal);
  for (let y = base - 18; y < base - 4; y++)
    for (let x = cx - 6; x < cx + 8; x++) {
      const dx = (x + 0.5 - (cx + 1)) / 7, dy = (y + 0.5 - (base - 11)) / 7;
      if (dx * dx + dy * dy > 1) continue;
      const shade = dx * 0.6 + dy * 0.8 + (hash2(seed, x, y) - 0.5) * 0.5;
      setPixel(buf, x, y, shade < -0.55 ? p.highlight : shade > 0.35 ? p.floor_alt : p.led_ok);
    }
}

// ------------------------------------------------------------------------------------------------ room

const room_floor: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const raised = (ctx.variant.seed ?? 0) % 2 === 1;
  // Quiet on purpose: the world level tiles this under sites and wires. Both variants share
  // the slate base; the raised panel only adds a 1 px seam on its lower edges and a small four-hole vent.
  fillDiamond(buf, 16, 0, 1, 1, p.floor);
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 32; x++) {
      if (!isOpaque(buf, x, y)) continue;
      const hw = y < 8 ? 2 * (y + 1) : 16 - 2 * (y - 8);
      if (raised) {
        if (y >= 8 && (x === 16 - hw || x === 17 - hw || x === 14 + hw || x === 15 + hw)) setPixel(buf, x, y, p.floor_alt);
        else if ((x === 14 || x === 18) && (y === 6 || y === 9)) setPixel(buf, x, y, p.floor_alt);
      } else if (hash2(11, x, y) < 0.025) setPixel(buf, x, y, p.floor_alt); // sparse, irregular carpet fleck
    }
};

const room_wall: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  for (let t = 0; t < 16; t++) {
    const base = 32 + Math.floor(t / 2);
    for (let z = 0; z < 33; z++) {
      const y = base - z;
      let c: string;
      if (z === 32) c = p.outline; // top edge
      else if (z >= 30) c = p.highlight; // wall cap
      else if (z <= 1) c = p.metal; // skirting
      else if (z <= 13) c = z === 13 ? p.wall_shade : p.wall; // plasterboard
      else if (t === 0 || z === 14 || z === 29) c = p.wall_shade; // glass frame
      else {
        const g = (t + z) % 16;
        c = g === 3 || g === 4 ? p.highlight : z === 21 ? p.wall_shade : p.accent_alt; // glass with a transom
      }
      setPixel(buf, t, y, c);
    }
  }
};

// ------------------------------------------------------------------------------------------------ rack (room grid)

const rack_empty: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const rot = ctx.variant.rotation ?? 0;
  const status = ctx.variant.status;
  const front: 'L' | 'R' | null = rot === 0 ? 'L' : rot === 3 ? 'R' : null;
  const rear: 'L' | 'R' | null = rot === 2 ? 'L' : rot === 1 ? 'R' : null;
  const blue = dark(status) ? p.metal_shade : status === 'failed' ? p.led_err : p.accent_alt;
  const face = (side: 'L' | 'R'): FacePaint => {
    const body = side === 'L' ? p.metal : p.metal_shade;
    const deep = side === 'L' ? p.metal_shade : p.outline;
    if (front === side)
      return (u, z) => {
        if (z <= 1) return p.outline; // plinth
        if (u <= 1 || u >= 14 || z >= 46) {
          if (u === 1 && z >= 38 && z <= 43) return blue; // blue LED strip on the door frame
          if (u === 14 && z >= 20 && z <= 25) return p.wall_shade; // handle
          return body;
        }
        if ((u + 47 - z) % 16 === 0) return side === 'L' ? p.metal : p.metal_shade; // door-glass glint
        return deep;
      };
    if (rear === side)
      return (u, z) => (z <= 1 ? p.outline : u <= 1 || u >= 14 || z >= 46 ? body : (u + z) % 2 === 0 ? deep : body);
    return (u, z) => (z <= 1 ? p.outline : u === 2 || u === 13 || z === 4 || z === 44 ? deep : body); // side panel
  };
  box(buf, 16, 48, 1, 1, 48, (tx, ty) => (tx < 0.07 || ty < 0.07 ? p.wall_shade : (tx > 0.3 && tx < 0.7 && Math.round(ty * 16) % 3 === 0) ? p.metal : p.floor), face('L'), face('R'));
  outline(buf, p.outline);
  if (status === 'planned') ghost(buf, p);
};

// ------------------------------------------------------------------------------------------------ faceplates (rack zoom)

const role = (ctx: DrawContext) => ctx.variant.role_color ?? ctx.palette.floor; // slate when NetBox gives no role colour

/** Black chassis with a 1 px ink border and a slate bevel on the top and left. */
function chassis(buf: PixelBuffer, p: P): void {
  const { width: w, height: h } = buf;
  fillRect(buf, 0, 0, w, h, p.outline);
  fillRect(buf, 1, 1, w - 2, h - 2, p.metal);
  fillRect(buf, 1, 1, w - 2, 1, p.floor_alt);
  fillRect(buf, 1, h - 2, w - 2, 1, p.metal_shade);
}

/** Role-colour pull tab at the left, with a highlight notch. */
function roleTab(buf: PixelBuffer, ctx: DrawContext, x0: number, w: number): void {
  const h = buf.height;
  fillRect(buf, x0, 1, w, h - 2, role(ctx));
  setPixel(buf, x0, 1, ctx.palette.highlight);
  fillRect(buf, x0 + w, 1, 1, h - 2, ctx.palette.outline);
}

function statusLeds(buf: PixelBuffer, ctx: DrawContext, x: number, y: number): void {
  const p = ctx.palette;
  setPixel(buf, x, y, led(p, ctx.variant.status));
  setPixel(buf, x + 2, y, dark(ctx.variant.status) ? p.metal_shade : p.accent_alt);
}

/** Drive bays: `n` bays of width bw from x0, rows y0..y0+bh-1, each with an activity LED and a handle. */
function bays(buf: PixelBuffer, ctx: DrawContext, x0: number, y0: number, n: number, bw: number, bh: number): void {
  const p = ctx.palette;
  const off = dark(ctx.variant.status);
  for (let i = 0; i < n; i++) {
    const x = x0 + i * (bw + 1);
    fillRect(buf, x, y0, bw, bh, p.metal_shade);
    setPixel(buf, x, y0, off ? p.metal : (i * 7 + y0) % 5 === 0 ? p.led_ok : p.accent_alt);
    fillRect(buf, x + 1, y0 + bh - 1, Math.max(1, bw - 2), 1, p.wall_shade);
  }
}

function vents(buf: PixelBuffer, p: P, x0: number, y0: number, w: number, h: number): void {
  for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) setPixel(buf, x, y, (x + y) % 2 === 0 ? p.outline : p.metal);
}

/** Dashed border: 2 px on, 2 px off, along the top and bottom rows (and the side columns when `sides`). */
function dashes(buf: PixelBuffer, c: string, top: boolean, sides: boolean): void {
  const { width: w, height: h } = buf;
  for (let x = 0; x < w; x++) {
    if (x % 4 >= 2) continue;
    if (top) setPixel(buf, x, 0, c);
    setPixel(buf, x, h - 1, c);
  }
  if (sides) for (let y = 0; y < h; y++) if (y % 4 < 2) (setPixel(buf, 0, y, c), setPixel(buf, w - 1, y, c));
}

/** Applies the status rule (see led()) to a finished faceplate. */
function plateStatus(buf: PixelBuffer, ctx: DrawContext): void {
  const p = ctx.palette;
  const st = ctx.variant.status;
  if (st === 'failed') dashes(buf, p.led_err, true, true);
  else if (st === 'decommissioning') dashes(buf, p.led_warn, false, false);
  else if (ghosted(st)) ghost(buf, p);
  else if (dark(st)) {
    // powered down: every light out, white labels and ports dimmed to grey
    const dim: Record<string, string> = {
      [p.led_ok]: p.metal_shade, [p.led_warn]: p.metal_shade, [p.led_err]: p.metal_shade, [p.accent_alt]: p.metal_shade,
      [p.highlight]: p.wall_shade, [p.wall]: p.wall_shade, [p.wall_shade]: p.floor_alt,
    };
    for (let y = 0; y < buf.height; y++)
      for (let x = 0; x < buf.width; x++) {
        const c = isOpaque(buf, x, y) ? dim[getPixel(buf, x, y) ?? ''] : undefined;
        if (c) setPixel(buf, x, y, c);
      }
  }
}

/** Wraps a faceplate so the status rule is applied the same way on every device slot. */
const plate = (draw: SlotDrawFn): SlotDrawFn => (buf, ctx) => (draw(buf, ctx), plateStatus(buf, ctx));

const device_1u: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  chassis(buf, p);
  roleTab(buf, ctx, 1, 10);
  bays(buf, ctx, 13, 2, 8, 3, 4);
  vents(buf, p, 46, 2, 8, 4);
  statusLeds(buf, ctx, 57, 3);
  setPixel(buf, 61, 3, p.wall_shade);
};

const device_2u: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  chassis(buf, p);
  roleTab(buf, ctx, 1, 10);
  bays(buf, ctx, 13, 2, 8, 3, 5);
  bays(buf, ctx, 13, 8, 8, 3, 5);
  vents(buf, p, 46, 2, 9, 11);
  statusLeds(buf, ctx, 57, 3);
  fillRect(buf, 57, 6, 5, 1, p.metal_shade);
  setPixel(buf, 61, 11, p.wall_shade);
};

const device_4u: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  chassis(buf, p);
  roleTab(buf, ctx, 1, 10);
  for (let r = 0; r < 4; r++) bays(buf, ctx, 13, 2 + r * 7, 6, 4, 6);
  // two fans
  for (const cy of [9, 22]) {
    const cx = 52.5;
    for (let y = cy - 6; y <= cy + 6; y++)
      for (let x = 45; x <= 60; x++) {
        const d = Math.hypot(x - cx, y - cy);
        if (d > 6.2) continue;
        setPixel(buf, x, y, d > 5.2 ? p.metal_shade : d < 1.6 ? p.wall_shade : ((Math.atan2(y - cy, x - cx) * 6) / Math.PI + d) % 2 < 1 ? p.outline : p.metal);
      }
  }
  statusLeds(buf, ctx, 57, 28);
};

const switch_: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const off = dark(ctx.variant.status);
  chassis(buf, p);
  roleTab(buf, ctx, 1, 3);
  // 12 columns of stacked RJ45 pairs
  for (let k = 0; k < 12; k++) {
    const x = 6 + k * 3 + Math.floor(k / 6);
    for (const y of [2, 4]) {
      fillRect(buf, x, y, 2, 2, p.outline);
      setPixel(buf, x + 1, y + 1, p.metal_shade);
    }
    setPixel(buf, x, 6, off ? p.metal_shade : hash2(k, 3, 9) < 0.7 ? p.led_ok : p.metal_shade);
  }
  // 4 SFP cages
  for (let k = 0; k < 4; k++) {
    const x = 44 + k * 3;
    fillRect(buf, x, 2, 2, 4, p.wall_shade);
    fillRect(buf, x + 1, 3, 1, 2, p.outline);
  }
  statusLeds(buf, ctx, 57, 3);
  fillRect(buf, 57, 5, 4, 1, p.accent_alt); // console
};

const router: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  chassis(buf, p);
  roleTab(buf, ctx, 1, 3);
  // status display: navy glass with a traffic graph
  fillRect(buf, 6, 2, 16, 4, p.background);
  for (let x = 7; x < 21; x++) {
    const hgt = 1 + Math.floor(hash2(5, x, 0) * 3);
    setPixel(buf, x, 6 - hgt, dark(ctx.variant.status) ? p.floor_alt : x % 5 === 0 ? p.accent : p.accent_alt);
  }
  // 4 big ports, framed
  for (let k = 0; k < 4; k++) {
    const x = 25 + k * 5;
    fillRect(buf, x, 2, 4, 4, p.wall_shade);
    fillRect(buf, x + 1, 3, 2, 2, p.outline);
  }
  // 2 SFP and a shield-shaped badge in the accent
  fillRect(buf, 46, 2, 3, 4, p.metal_shade);
  fillRect(buf, 50, 2, 3, 4, p.metal_shade);
  fillRect(buf, 54, 2, 2, 3, p.accent);
  setPixel(buf, 54, 5, p.accent);
  statusLeds(buf, ctx, 58, 3);
  setPixel(buf, 58, 5, p.accent_alt);
};

const patch_panel: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  chassis(buf, p);
  roleTab(buf, ctx, 1, 2);
  // white label strip with port numbers as ticks
  fillRect(buf, 5, 1, 55, 2, p.wall);
  for (let x = 6; x < 59; x += 2) setPixel(buf, x, 2, p.wall_shade);
  // 24 keystones in 4 groups of 6
  for (let k = 0; k < 24; k++) {
    const x = 5 + k * 2 + Math.floor(k / 6) * 2;
    fillRect(buf, x, 3, 2, 4, p.wall_shade);
    setPixel(buf, x, 4, p.outline);
    setPixel(buf, x, 5, p.outline);
  }
  setPixel(buf, 62, 1, p.wall_shade);
  setPixel(buf, 62, 6, p.wall_shade);
};

const psu: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const off = dark(ctx.variant.status);
  chassis(buf, p);
  // ammeter display
  fillRect(buf, 2, 2, 11, 4, p.background);
  for (const [x, y] of [[3, 3], [4, 3], [3, 4], [6, 3], [7, 3], [7, 4], [6, 4], [9, 3], [10, 4], [11, 3]] as const)
    setPixel(buf, x, y, off ? p.floor_alt : ctx.variant.status === 'failed' ? p.led_err : p.led_ok);
  // 7 outlets
  for (let k = 0; k < 7; k++) {
    const x = 15 + k * 6;
    fillRect(buf, x, 2, 5, 4, p.wall_shade);
    setPixel(buf, x + 1, 3, p.outline);
    setPixel(buf, x + 3, 3, p.outline);
    fillRect(buf, x + 1, 5, 3, 1, p.metal_shade);
  }
  fillRect(buf, 57, 2, 2, 4, p.accent); // breaker
  fillRect(buf, 60, 1, 3, 6, role(ctx)); // role tab on the cord end
  setPixel(buf, 56, 6, led(p, ctx.variant.status));
};

const rack_unit: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const u = seedOf(ctx);
  const rail = (x0: number, inner: number) => {
    fillRect(buf, x0, 0, 8, 8, p.metal);
    fillRect(buf, x0 + (inner > x0 ? 0 : 7), 0, 1, 8, p.floor_alt); // lit outer edge
    fillRect(buf, inner, 0, 1, 8, p.outline);
    for (const y of [1, 4, 6]) fillRect(buf, x0 + 3, y, 2, 1, p.outline); // cage-nut holes
    if (u % 5 === 0) fillRect(buf, x0 + 2, 0, 4, 1, p.wall_shade); // every fifth U marked
  };
  rail(0, 7);
  rail(72, 72);
  fillRect(buf, 8, 0, 64, 8, p.metal_shade);
  fillRect(buf, 8, 7, 64, 1, p.outline);
  for (let x = 10; x < 70; x += 4) setPixel(buf, x, 3, p.metal);
};

const port: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const st = ctx.variant.status ?? 'free';
  fillRect(buf, 0, 0, 8, 8, p.outline);
  fillRect(buf, 1, 1, 6, 6, st === 'mgmt' ? p.accent : p.wall_shade);
  fillRect(buf, 2, 2, 4, 4, p.outline); // jack
  fillRect(buf, 3, 6, 2, 1, p.outline); // clip slot
  if (st === 'connected') {
    const c = ctx.variant.wire_color ?? p.wire;
    fillRect(buf, 2, 2, 4, 5, c);
    setPixel(buf, 2, 2, p.highlight);
    fillRect(buf, 3, 4, 2, 1, p.outline);
  } else if (st === 'disabled') {
    for (const [x, y] of [[2, 2], [3, 3], [4, 4], [5, 5], [5, 2], [2, 5]] as const) setPixel(buf, x, y, p.led_err);
  }
  setPixel(buf, 1, 1, st === 'connected' ? p.led_ok : st === 'mgmt' ? p.accent_alt : st === 'disabled' ? p.led_err : p.metal);
};

// ------------------------------------------------------------------------------------------------ cable trays

function tray(kind: 'straight' | 'corner' | 'cross'): SlotDrawFn {
  return (buf, ctx) => {
    const p = ctx.palette;
    const c = ctx.variant.wire_color ?? p.wire;
    const rot = ctx.variant.rotation ?? 0;
    // A run is a list of (column, centre row) along the tile's x (down-right) or y (down-left) axis.
    const runs: [number, number, 'x' | 'y'][] = [];
    const alongX = (t: number) => runs.push([8 + t, 4 + Math.floor(t / 2), 'x']);
    const alongY = (t: number) => runs.push([23 - t, 4 + Math.floor(t / 2), 'y']);
    if (kind === 'straight') for (let t = 0; t < 16; t++) (rot % 2 === 0 ? alongX : alongY)(t);
    if (kind === 'cross') for (let t = 0; t < 16; t++) (alongX(t), alongY(t));
    // rotation 0 joins -x and +y; each rotation turns 90 degrees clockwise on screen (theme-slots.json)
    if (kind === 'corner') for (let t = 0; t < 8; t++) (alongX(t + (rot >= 2 ? 8 : 0)), alongY(t + (rot === 0 || rot === 3 ? 8 : 0)));
    // tray body first (both runs), then cables, so crossings stay neat
    for (const [x, y] of runs) {
      setPixel(buf, x, y - 2, p.wall_shade);
      setPixel(buf, x, y - 1, p.metal_shade);
      setPixel(buf, x, y, p.metal_shade);
      setPixel(buf, x, y + 1, p.metal);
      setPixel(buf, x, y + 2, p.outline);
    }
    for (const [x, y, ax] of runs) {
      setPixel(buf, x, y - 1, c);
      setPixel(buf, x, y, (x + (ax === 'x' ? 0 : 1)) % 4 === 0 ? p.metal_shade : c);
    }
  };
}

// ------------------------------------------------------------------------------------------------ props

const SMALL: readonly (readonly string[])[] = [
  // potted plant
  [
    '................',
    '.......g........',
    '.....g.gg.g.....',
    '....gghggggg....',
    '...ggggkgghgg...',
    '...gkgggggkgg...',
    '..gghggkgggkgg..',
    '...gkgggkggggg..',
    '....ggkggkggg...',
    '.....kgggkgg....',
    '....hhhhhhhs....',
    '.....RRRRRRo....',
    '.....RRRRRRo....',
    '......RRRRo.....',
    '......RRRRo.....',
    '................',
  ],
  // water cooler
  [
    '................',
    '......bbbb......',
    '.....bhbbbbk....',
    '.....bhbbbbk....',
    '.....bbbbbbk....',
    '......bbbbk.....',
    '.......ss.......',
    '....hhhhhhhs....',
    '....wwwwwwss....',
    '....MMMMMwss....',
    '....MbMRMwss....',
    '....MMMMMwss....',
    '....wwwwwwss....',
    '....wwwwwwss....',
    '.....wwwwss.....',
    '................',
  ],
  // coffee machine on a little cabinet
  [
    '................',
    '.....h..........',
    '.....fffffff....',
    '....mmmmmmmM....',
    '....mbbbbbMM....',
    '....mmmmmmMM....',
    '....mmMMmmMM....',
    '....mooooMMM....',
    '....moRRoMMM....',
    '....MMMMMMMM....',
    '...hhhhhhhhhs...',
    '...wwwwwwwwss...',
    '...wwswwswwss...',
    '...wwwwwwwwss...',
    '....wwwwwwss....',
    '................',
  ],
  // office chair (upholstery in the role colour, else the accent)
  [
    '................',
    '.....RRRRR......',
    '....RRRRRRm.....',
    '....RhRRRRm.....',
    '....RRRRRRm.....',
    '....RRRRRRm.....',
    '.....mmmmm......',
    '.......mm.......',
    '...RRRRRRRR.....',
    '..RhRRRRRRRm....',
    '...mmmmmmmmm....',
    '.......mm.......',
    '...m...mm..m....',
    '..mmmmmmmmmmm...',
    '..M....M....M...',
    '................',
  ],
  // printer
  [
    '................',
    '................',
    '................',
    '......hhhhh.....',
    '......wwwws.....',
    '....hhhhhhhhs...',
    '...hwwwwwwwwss..',
    '...wwwwwwwwsss..',
    '...wMMMMMMwsss..',
    '...wwwwwwwwsss..',
    '...wwwwbRwwsss..',
    '...wwwwwwwwsss..',
    '....wwwwwwwss...',
    '................',
    '................',
    '................',
  ],
];

const decoration_small: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  drawMap(buf, SMALL[(seedOf(ctx) % 7) % SMALL.length]!, 0, 0, mapColour(p, ctx.variant.role_color));
  outline(buf, p.outline);
  if (ctx.variant.status === 'planned') ghost(buf, p);
};

function desk(buf: PixelBuffer, p: P): void {
  const w = 0.875, h = 0.5;
  const [ax, ay] = centred(16, 40, w, h);
  // legs
  for (const [lx, ly] of [[0.05, h - 0.05], [w - 0.05, h - 0.05], [w - 0.05, 0.05]] as const) {
    const [x, y] = [ax + (lx - ly) * 16, ay + (lx + ly) * 8];
    fillRect(buf, Math.round(x) - 1, Math.round(y) - 12, 1, 12, p.wall_shade);
  }
  // top
  box(buf, ax, ay - 12, w, h, 2, flatTop(p.wall), flat(p.wall_shade), flat(p.floor));
  // monitor
  const [mx, my] = [ax + (0.35 - 0.12) * 16, ay - 14 + (0.35 + 0.12) * 8];
  box(buf, Math.round(mx), Math.round(my), 0.375, 0.125, 9, flatTop(p.metal), (u, z) => (z === 0 || u === 0 || z === 8 ? p.metal : (u + z) % 9 === 0 ? p.highlight : p.accent_alt), flat(p.metal_shade));
  // mug in the accent and a keyboard
  fillRect(buf, ax - 6, ay - 11, 2, 2, p.accent);
  fillRect(buf, ax - 1, ay - 7, 6, 1, p.metal);
  fillRect(buf, ax + 1, ay - 6, 6, 1, p.metal);
}

function bigPlant(buf: PixelBuffer, p: P, seed: number): void {
  const [ax, ay] = centred(16, 40, 0.5, 0.5);
  box(buf, ax, ay, 0.5, 0.5, 9, (tx, ty) => (tx < 0.1 || ty < 0.1 ? p.highlight : p.floor_alt), flat(p.wall), flat(p.wall_shade));
  fillRect(buf, 15, 18, 2, 14, p.metal);
  for (let y = 1; y < 28; y++)
    for (let x = 4; x < 29; x++) {
      const dx = (x + 0.5 - 16) / 11, dy = (y + 0.5 - 14) / 13;
      const n = hash2(seed, x >> 1, y >> 1);
      if (dx * dx + dy * dy > 0.75 + n * 0.35) continue;
      const shade = dx * 0.7 + dy * 0.7 + (hash2(seed, x, y) - 0.5) * 0.6;
      setPixel(buf, x, y, shade < -0.6 ? p.highlight : shade > 0.25 ? p.floor_alt : shade > 0 && (x + y) % 2 === 0 ? p.floor_alt : p.led_ok);
    }
}

function copier(buf: PixelBuffer, p: P): void {
  const w = 0.75, h = 0.625;
  const [ax, ay] = centred(16, 40, w, h);
  box(buf, ax, ay, w, h, 18, (tx, ty) => (tx < 0.06 || ty < 0.06 ? p.highlight : ty > 0.55 && tx < 0.35 ? p.accent_alt : p.wall),
    (u, z) => (z === 0 ? p.metal : z === 5 || z === 10 ? p.wall_shade : u === 6 && z < 15 ? p.wall_shade : p.wall),
    (u, z) => (z === 0 ? p.metal_shade : z === 5 || z === 10 ? p.floor : p.wall_shade));
  // output tray in the accent
  const [ox, oy] = [ax + (w - h) * 16 + 2, ay + (w + h) * 8 - 13];
  fillRect(buf, Math.round(ox), Math.round(oy), 6, 1, p.accent);
  fillRect(buf, Math.round(ox) + 1, Math.round(oy) - 1, 4, 1, p.highlight);
}

function cooler(buf: PixelBuffer, p: P): void {
  const [ax, ay] = centred(16, 40, 0.5, 0.5);
  box(buf, ax, ay, 0.5, 0.5, 18, flatTop(p.highlight), (u, z) => (z === 13 && (u === 2 || u === 5) ? (u === 2 ? p.accent_alt : p.led_err) : u >= 1 && u <= 6 && z >= 7 && z <= 12 ? (z <= 8 && u >= 3 && u <= 4 ? p.highlight : z === 7 ? p.wall_shade : p.metal) : p.wall), flat(p.wall_shade));
  const [bx, by] = centred(16, 40 - 19, 0.375, 0.375);
  box(buf, bx, by, 0.375, 0.375, 10, flatTop(p.wall), (u, z) => (u === 1 && z > 2 ? p.highlight : z === 0 ? p.wall_shade : p.accent_alt), (u, z) => (z === 0 ? p.wall_shade : (u + z) % 2 === 0 ? p.accent_alt : p.background));
  // a paper cup on the side
  fillRect(buf, 23, 31, 2, 3, p.wall);
}

const LARGE = ['desk', 'plant', 'copier', 'cooler'] as const;

const decoration_large: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  const seed = seedOf(ctx);
  const kind = LARGE[seed % LARGE.length]!;
  if (kind === 'desk') desk(buf, p);
  else if (kind === 'plant') bigPlant(buf, p, seed);
  else if (kind === 'copier') copier(buf, p);
  else cooler(buf, p);
  outline(buf, p.outline);
  if (ctx.variant.status === 'planned') ghost(buf, p);
};

// ------------------------------------------------------------------------------------------------ UI

const ui_frame: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  fillRect(buf, 1, 0, 22, 24, p.outline);
  fillRect(buf, 0, 1, 24, 22, p.outline);
  fillRect(buf, 1, 1, 22, 22, p.wall_shade);
  fillRect(buf, 1, 1, 22, 1, p.highlight);
  fillRect(buf, 1, 1, 1, 22, p.highlight);
  fillRect(buf, 2, 2, 20, 20, p.outline);
  fillRect(buf, 3, 3, 18, 18, p.background);
  // accent tab in the top-left corner (the corner slice only)
  fillRect(buf, 3, 3, 4, 1, p.accent);
  fillRect(buf, 3, 3, 1, 4, p.accent);
};

const cursor: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // corner brackets of the tile diamond, white, with blue ticks at the top and bottom corners
  for (let r = 0; r < 16; r++) {
    const hw = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
    for (const x of [16 - hw, 17 - hw, 14 + hw, 15 + hw]) if (r < 4 || r > 11 || x < 7 || x > 24) setPixel(buf, x, r, p.highlight);
  }
  setPixel(buf, 15, 2, p.accent_alt), setPixel(buf, 16, 2, p.accent_alt);
  setPixel(buf, 15, 13, p.accent_alt), setPixel(buf, 16, 13, p.accent_alt);
};

const selection_highlight: SlotDrawFn = (buf, ctx) => {
  const p = ctx.palette;
  // A diamond ring 1 px larger than the tile all round (rows 0-17), 2 rows thick: accent, white at the four tips.
  const hw = (y: number, a: number, b: number) => (y < a || y > b ? -1 : 2 * Math.min(y - a, b - y) + 2);
  for (let y = 0; y < 18; y++)
    for (let x = 0; x < 34; x++) {
      const d = Math.abs(x + 0.5 - 17);
      if (d > hw(y, 0, 17) || d <= hw(y, 2, 15) - 2) continue;
      setPixel(buf, x, y, y < 2 || y > 15 || x < 4 || x > 29 ? p.highlight : p.accent);
    }
};

const slots: Record<SlotId, SlotDrawFn> = {
  site_building,
  room_floor,
  room_wall,
  rack_empty,
  device_1u: plate(device_1u),
  device_2u: plate(device_2u),
  device_4u: plate(device_4u),
  switch: plate(switch_),
  router: plate(router),
  patch_panel: plate(patch_panel),
  psu: plate(psu),
  rack_unit,
  port,
  wire_straight: tray('straight'),
  wire_corner: tray('corner'),
  wire_cross: tray('cross'),
  decoration_small,
  decoration_large,
  ui_frame,
  cursor,
  selection_highlight,
};

export const businessTheme: Theme = { id: 'business', name: 'General business', palette, slots };
