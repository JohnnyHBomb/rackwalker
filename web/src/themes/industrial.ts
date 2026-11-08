// Theme 'industrial-robotic': a cheerful little factory. Riveted steel floor plates, brick-and-girder walls with a
// hazard-stripe kick plate, racks as stout cabinet robots (antennae, round status-LED eyes, hazard plinth), cables as
// conveyor belts, smiling smokestack factories for sites, helper bots, gear crates, oil cans and barrels as props.
// Original art, drawn procedurally in code (no images); every colour is a palette key or the variant's NetBox
// role/cable colour, and every choice depends only on the variant (seed, status, rotation), never on randomness.
// Light comes from the top left: top faces lightest, left faces mid, right faces darkest; 1 px dark outlines.
// Check: npx vitest run tests/theme-conformance.test.ts tests/theme-industrial.test.ts
// Look:  make theme-shots THEME=industrial-robotic
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
import { getPixel, innerOutline, isOpaque, isoBox, isoPoint, seedOf, type Pt } from './kit';

// Original 16-colour palette (8 Nov 2026): a grey steel ramp, a warm brick/orange ramp, one factory teal, distinct
// status LEDs (green / yellow / red) and two creams.
const palette: Palette = {
  background: '#232733',
  outline: '#17141d',
  floor: '#717985',
  floor_alt: '#666d79',
  wall: '#b9603f',
  wall_shade: '#7c3a2d',
  metal: '#b6c0c8',
  metal_shade: '#4a515d',
  accent: '#f5a034',
  accent_alt: '#2db3a2',
  led_ok: '#86e05a',
  led_warn: '#ffd43c',
  led_err: '#f0484a',
  wire: '#d6692c',
  text: '#f2e9d4',
  highlight: '#fff9e4',
};

// ------------------------------------------------------------------------------------------------ helpers

type Ctx = DrawContext;
const P = (ctx: Ctx) => ctx.palette;

/**
 * Device status, each with a cue that is not only hue (status rule proposed for contract 1.1.2):
 * ok = active (green round eyes); ghost = planned/staged (hollow unlit eyes, the plate drawn as a checker ghost);
 * off = offline/inventory (eyes shut: a dash); fail = failed (red X eyes in a square socket); warn = anything else,
 * e.g. decommissioning (yellow eyes, half-closed by a lid).
 */
export type Mood = 'ok' | 'ghost' | 'off' | 'fail' | 'warn';
export function moodOf(ctx: Ctx): Mood {
  switch (ctx.variant.status) {
    case undefined:
    case 'active':
    case 'connected':
      return 'ok';
    case 'failed':
      return 'fail';
    case 'offline':
    case 'inventory':
      return 'off';
    case 'planned':
    case 'staged':
      return 'ghost';
    default:
      return 'warn';
  }
}

/** Status LED colour: green ok, red failed, yellow warn, unlit (dark steel) when off or a planned ghost. */
export function ledOf(ctx: Ctx): string {
  const p = P(ctx);
  return { ok: p.led_ok, fail: p.led_err, warn: p.led_warn, off: p.metal_shade, ghost: p.metal_shade }[moodOf(ctx)];
}
const asleep = (ctx: Ctx) => moodOf(ctx) === 'off' || moodOf(ctx) === 'ghost';

/**
 * Planned/staged ghost: every other pixel (a checker) that is not outline becomes the background colour; `sparse`
 * (the big rack body) takes one pixel in four instead, so a planned rack stays legible.
 */
function ghostify(buf: PixelBuffer, ctx: Ctx, sparse = false): void {
  if (moodOf(ctx) !== 'ghost') return;
  const p = P(ctx);
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++)
      if ((sparse ? x % 2 === 0 && y % 2 === 0 : (x + y) % 2 === 0) && isOpaque(buf, x, y) && getPixel(buf, x, y) !== p.outline) setPixel(buf, x, y, p.background);
}
const roleOf = (ctx: Ctx, fallback: string) => ctx.variant.role_color ?? fallback;

/**
 * Pixel map: one string per row, one character per pixel, '.' = leave. Keys: o outline, m metal, s metal_shade,
 * f floor, F floor_alt, a accent, t accent_alt, w wire, b wall, B wall_shade, h highlight, x text, k background,
 * L status LED, R role colour (accent_alt when none), g/y/r the three LEDs.
 */
function keyOf(ctx: Ctx): Record<string, string> {
  const p = P(ctx);
  return {
    o: p.outline, m: p.metal, s: p.metal_shade, f: p.floor, F: p.floor_alt, a: p.accent, t: p.accent_alt, w: p.wire,
    b: p.wall, B: p.wall_shade, h: p.highlight, x: p.text, k: p.background, L: ledOf(ctx), R: roleOf(ctx, p.accent_alt),
    g: p.led_ok, y: p.led_warn, r: p.led_err,
  };
}
function stamp(buf: PixelBuffer, ctx: Ctx, x0: number, y0: number, rows: readonly string[], extra: Record<string, string> = {}): void {
  const key = { ...keyOf(ctx), ...extra };
  rows.forEach((row, j) => {
    for (let i = 0; i < row.length; i++) {
      const c = key[row[i]!];
      if (c) setPixel(buf, x0 + i, y0 + j, c);
    }
  });
}

const EYES: Record<Exclude<Mood, 'off'>, Record<number, string[]>> = {
  ok: {
    4: ['.oo.', 'ohLo', 'oLLo', '.oo.'],
    5: ['.ooo.', 'ohLLo', 'oLLLo', 'oLLLo', '.ooo.'],
    6: ['.oooo.', 'ohhLLo', 'ohLLLo', 'oLLLLo', 'oLLLLo', '.oooo.'],
  },
  ghost: {
    4: ['.oo.', 'okko', 'okko', '.oo.'],
    5: ['.ooo.', 'okkko', 'okkko', 'okkko', '.ooo.'],
    6: ['.oooo.', 'okkkko', 'okkkko', 'okkkko', 'okkkko', '.oooo.'],
  },
  fail: {
    4: ['roor', 'orro', 'orro', 'roor'],
    5: ['rooor', 'ororo', 'ooroo', 'ororo', 'rooor'],
    6: ['rroorr', 'orrrro', 'oorroo', 'oorroo', 'orrrro', 'rroorr'],
  },
  warn: {
    4: ['oooo', 'oooo', 'oLLo', '.oo.'],
    5: ['.ooo.', 'ooooo', 'oLLLo', 'oLLLo', '.ooo.'],
    6: ['.oooo.', 'oooooo', 'oooooo', 'oLLLLo', 'oLLLLo', '.oooo.'],
  },
};

/** Eye-like status LED, 4, 5 or 6 px, drawn per mood (see moodOf); an offline device sleeps (a closed-eye dash). */
function eye(buf: PixelBuffer, ctx: Ctx, x: number, y: number, size: 4 | 5 | 6): void {
  const m = moodOf(ctx);
  if (m === 'off') {
    fillRect(buf, x, y + Math.floor(size / 2), size, 1, P(ctx).outline);
    return;
  }
  stamp(buf, ctx, x, y, EYES[m][size]!);
}

/** Iso tile coordinates (u along +x, v along +y, 0..1 per tile) of a pixel centre, tile top corner at (ax, ay). */
function uv(ax: number, ay: number, x: number, y: number): [number, number] {
  const dx = (x + 0.5 - ax) / 16, dy = (y + 0.5 - ay) / 8;
  return [(dx + dy) / 2, (dy - dx) / 2];
}

/** Every pixel of one 1x1 floor diamond at (16, 0), as fillDiamond draws it. */
function forTile(fn: (x: number, y: number, u: number, v: number) => void): void {
  for (let r = 0; r < 16; r++) {
    const reach = r < 8 ? 2 * (r + 1) : 32 - 2 * r;
    for (let x = 16 - reach; x < 16 + reach; x++) {
      const [u, v] = uv(16, 0, x, r);
      fn(x, r, u, v);
    }
  }
}

/** Hazard stripes: diagonal bands 2 px wide, alternating `a` and `b`. */
const hazard = (x: number, y: number, a: string, b: string) => (((x + y) >> 1) & 1 ? b : a);

/** Upright cylinder (front view) over columns x0..x0+w-1, lid centre row yTop, base row yBot; body colours left to right. */
function cylinder(buf: PixelBuffer, ctx: Ctx, x0: number, w: number, yTop: number, yBot: number, body: readonly string[], lid: string): void {
  const p = P(ctx);
  const rx = w / 2, cx = x0 + rx, ry = Math.max(2, Math.round(w / 4));
  const k = (x: number) => Math.sqrt(Math.max(0, 1 - ((x + 0.5 - cx) / rx) ** 2));
  for (let x = x0; x < x0 + w; x++) {
    const col = body[Math.min(body.length - 1, Math.floor(((x + 0.5 - x0) / w) * body.length))]!;
    for (let y = yTop; y <= yBot + Math.round(k(x) * (ry - 1)); y++) setPixel(buf, x, y, col);
  }
  for (let x = x0; x < x0 + w; x++) {
    const h = Math.round(k(x) * ry);
    for (let y = yTop - h; y < yTop + h; y++) setPixel(buf, x, y, lid);
    setPixel(buf, x, yTop + h, p.outline);
  }
}

/** Round cog (16 teeth positions, every other one raised), shaded from the top left, dark hub. */
function cog(buf: PixelBuffer, ctx: Ctx, cx: number, cy: number, r: number, light: string, mid: string, dark: string): void {
  const p = P(ctx);
  for (let y = Math.floor(cy - r - 2); y <= cy + r + 2; y++)
    for (let x = Math.floor(cx - r - 2); x <= cx + r + 2; x++) {
      const dx = x + 0.5 - cx, dy = y + 0.5 - cy;
      const d = Math.hypot(dx, dy);
      const tooth = Math.floor(((Math.atan2(dy, dx) + Math.PI) / (2 * Math.PI)) * 16 + 0.5) % 2 === 0;
      if (d < r || (tooth && d < r + 1.6)) setPixel(buf, x, y, d < r * 0.35 ? p.outline : dx + dy < -r * 0.5 ? light : dx + dy > r * 0.6 ? dark : mid);
    }
}

// ------------------------------------------------------------------------------------------------ floor and walls

const roomFloor: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  const alt = seedOf(ctx) % 2 === 1;
  const base = alt ? p.floor_alt : p.floor;
  const tread = alt ? p.floor : p.floor_alt;
  fillDiamond(buf, 16, 0, 1, 1, base);
  // Diamond-plate tread: short bumps on a staggered lattice in the plate's own iso directions.
  forTile((x, y, u, v) => {
    const a = Math.floor(u * 8), b = Math.floor(v * 8);
    if ((a + b) % 2 !== 0 || u < 0.12 || u > 0.88 || v < 0.12 || v > 0.88) return;
    const fu = u * 8 - a, fv = v * 8 - b;
    if (fu > 0.35 && fu < 0.75 && fv > 0.4 && fv < 0.62) setPixel(buf, x, y, tread);
  });
  // Bevel: top edges catch the light, bottom edges are the seam to the next plate.
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 32; x++) {
      if (!isOpaque(buf, x, y)) continue;
      // Lower edges only: a dark seam on the shaded (lower-right) side, a soft one on the lower-left, so a plaza of
      // plates does not turn into a bright grid at world zoom (the rivets carry the light).
      if (!isOpaque(buf, x, y + 1)) setPixel(buf, x, y, x >= 16 ? p.metal_shade : tread);
    }
  // Four rivets in the plate corners.
  for (const [u, v] of [[0.16, 0.16], [0.84, 0.16], [0.16, 0.84], [0.84, 0.84]] as const) {
    const [x, y] = isoPoint(16, 0, u, v);
    setPixel(buf, Math.floor(x), Math.floor(y), p.metal);
    setPixel(buf, Math.floor(x) + 1, Math.floor(y), p.metal_shade);
  }
};

// Wall on a tile's N edge: column t (0-15) runs from row t/2 (top) to row t/2 + 32 (ground). Rows down the wall (v):
// 0 outline, 1-3 girder cap with rivets, 4 outline, 5-25 brick courses, 26-29 hazard kick plate, 30-31 steel, 32 outline.
const roomWall: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  for (let t = 0; t < 16; t++) {
    const y0 = Math.floor(t / 2);
    for (let v = 0; v <= 32; v++) {
      let c: string;
      if (v === 0 || v === 32 || v === 4 || v === 26) c = p.outline;
      else if (v <= 3) c = v === 3 ? p.metal_shade : v === 2 && t % 8 === 5 ? p.highlight : p.metal;
      else if (v <= 25) {
        const course = Math.floor((v - 5) / 4), row = (v - 5) % 4, col = (t + (course % 2) * 4) % 8;
        c = row === 3 || col === 7 ? p.wall_shade : row === 0 && col < 3 ? p.accent : p.wall;
      } else if (v <= 29) c = hazard(t, v, p.accent, p.outline);
      else c = p.metal_shade;
      setPixel(buf, t, y0 + v, c);
    }
  }
  // Steel post at the tile's corner (the mirrored W wall puts it on the other corner).
  for (let v = 1; v <= 31; v++) {
    setPixel(buf, 0, v, p.outline);
    if (v < 26 || v > 29) setPixel(buf, 1, v, v === 4 ? p.outline : p.metal);
  }
  for (const v of [9, 17, 23]) setPixel(buf, 1, v, p.highlight);
};

// ------------------------------------------------------------------------------------------------ site building

// 2x2 smiling factory: brick hall with a girder band, sawtooth skylights (or roof vents), a round smokestack with
// smoke, a roller door with a lamp, and a face on the S wall (teal window eyes, cheeks, smile).
const siteBuilding: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  const seed = seedOf(ctx);
  const H = 30;
  const ax = 32, ay = 48;
  // Screen row of height z on column x of the S (left) and E (right) faces.
  const leftY = (x: number, z: number) => 64 + Math.floor(x / 2) - z;
  const rightY = (x: number, z: number) => 64 + Math.floor((63 - x) / 2) - z;
  const brick = (mortar: string, face: string, yOf: (x: number) => number) => (x: number, y: number) => {
    const z = yOf(x) - y;
    return z % 4 === 0 || (x + (Math.floor(z / 4) % 2) * 4) % 8 === 0 ? mortar : face;
  };
  isoBox(buf, ax, ay, 2, 2, H, {
    top: p.metal,
    left: brick(p.wall_shade, p.wall, (x) => leftY(x, 0)),
    right: brick(p.outline, p.wall_shade, (x) => rightY(x, 0)),
  });
  // Girder band under the roof edge, with rivets.
  for (let x = 0; x < 64; x++) {
    const L = x < 32;
    const yy = L ? leftY(x, H) : rightY(x, H);
    for (let k = 1; k <= 3; k++) setPixel(buf, x, yy + k, L ? (k === 3 ? p.metal_shade : p.metal) : k === 3 ? p.outline : p.metal_shade);
    if (x % 6 === 3) setPixel(buf, x, yy + 2, L ? p.highlight : p.floor);
  }
  // Roof: sawtooth skylight strips (teal glass, lit edge) on even seeds, three vents on odd ones.
  const vents = seed % 2 === 1;
  for (let y = 0; y < 80; y++)
    for (let x = 0; x < 64; x++) {
      const [u, v] = uv(ax, ay - H, x, y);
      if (u < 0.06 || v < 0.06 || u > 1.94 || v > 1.94) continue;
      if (!vents) {
        if (u < 0.2 || u > 1.8 || v < 0.25 || v > 1.75) continue;
        const f = (u * 2.5) % 1;
        if (f < 0.18) setPixel(buf, x, y, p.highlight);
        else if (f < 0.42) setPixel(buf, x, y, p.accent_alt);
        else if (f < 0.5) setPixel(buf, x, y, p.metal_shade);
      } else {
        for (const [cu, cv] of [[0.55, 0.6], [1.35, 0.6], [0.55, 1.4]] as const)
          if (Math.abs(u - cu) < 0.2 && Math.abs(v - cv) < 0.2) setPixel(buf, x, y, Math.abs(u - cu) < 0.08 ? p.outline : p.metal_shade);
      }
    }
  // Smokestack standing on the roof's back half, with two bands.
  const band = seed % 3 === 2 ? p.accent_alt : p.accent;
  const [sx, baseY] = isoPoint(ax, ay, 1.15, 0.55, H);
  const x0 = Math.round(sx) - 5, top = 9;
  cylinder(buf, ctx, x0, 10, top, Math.round(baseY), [p.metal, p.floor, p.floor, p.metal_shade, p.metal_shade], p.outline);
  for (const by of [top + 4, top + 10])
    for (let x = x0; x < x0 + 10; x++) {
      const dy = Math.round(Math.sqrt(Math.max(0, 1 - ((x + 0.5 - x0 - 5) / 5) ** 2)) * 2);
      setPixel(buf, x, by + dy, x < x0 + 6 ? band : p.wire);
      setPixel(buf, x, by + dy + 1, x < x0 + 6 ? band : p.wire);
    }
  // Smoke puffs drifting right.
  stamp(buf, ctx, x0 + 3, 0, ['.......xxx..', '..xx..xhhxx.', '.xhhxxxhxxx.', '.xxxxxxxx...', '..xxx.......']);
  // The face on the S wall, big enough to read at world zoom (1x): two 8 px round windows for eyes (cream glass, a
  // dark pupil), orange cheeks and a 2 px smile (an open grin with teeth when seed % 3 = 0). Drawn along the wall's
  // 2:1 slope: column x of a feature starts at leftY(x, z).
  const onWall = (x0: number, z: number, rows: readonly string[]) =>
    rows.forEach((row, j) => {
      for (let i = 0; i < row.length; i++) {
        const c = keyOf(ctx)[row[i]!];
        if (c) setPixel(buf, x0 + i, leftY(x0 + i, z) + j, c);
      }
    });
  const EYE = ['..oooo..', '.ohhhxo.', 'ohhxxxto', 'ohxooxto', 'oxxooxto', 'oxxxxtto', '.otttto.', '..oooo..'];
  for (const ex of [4, 18]) onWall(ex, 26, EYE);
  for (const cx of [3, 25]) onWall(cx, 16, ['aaaa', 'awwa']);
  const grin = seed % 3 === 0;
  for (let x = 7; x <= 25; x++) {
    const d = Math.min(5, Math.round(((x - 16) ** 2) / 16));
    const y = leftY(x, 10 + d);
    setPixel(buf, x, y, p.outline);
    setPixel(buf, x, y + 1, p.outline);
    if (grin && d < 3) setPixel(buf, x, y - 1, p.highlight);
    if (grin && d < 3) setPixel(buf, x, y - 2, p.outline);
  }
  // Roller door on the E wall, with a lamp above it.
  for (let x = 40; x <= 55; x++) {
    const yb = rightY(x, 0);
    for (let z = 1; z <= 15; z++) setPixel(buf, x, yb - z, x === 40 || x === 55 || z === 15 ? p.outline : z % 3 === 0 ? p.wire : p.accent);
  }
  for (const x of [47, 48]) {
    setPixel(buf, x, rightY(x, 19), p.led_warn);
    setPixel(buf, x, rightY(x, 18), p.outline);
  }
  innerOutline(buf, p.outline);
};

// ------------------------------------------------------------------------------------------------ rack (room zoom)

// Face geometry fixed by the engine (sprites.ts): S face columns 0-15 start at row 9 + floor(c / 2), E face columns
// 16-31 at row 24 - floor(c / 2); devices are drawn over columns 2-13 (S) or 18-29 (E) of the front face.
const faceTop = (face: 'S' | 'E', c: number) => (face === 'S' ? 9 + (c >> 1) : 24 - (c >> 1));

const rackEmpty: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  const rot = ctx.variant.rotation ?? 0;
  isoBox(buf, 16, 48, 1, 1, 48, { top: p.metal, left: p.floor, right: p.metal_shade });
  innerOutline(buf, p.outline);
  const roles: Record<'S' | 'E', 'front' | 'side' | 'back'> =
    rot === 0 ? { S: 'front', E: 'side' } : rot === 3 ? { S: 'side', E: 'front' } : rot === 1 ? { S: 'side', E: 'back' } : { S: 'back', E: 'side' };
  for (const face of ['S', 'E'] as const) {
    const c0 = face === 'S' ? 2 : 18;
    const dark = face === 'E';
    const role = roles[face];
    if (role === 'front') {
      // Open bay: dark slot with rail holes (devices are drawn over it).
      for (let i = 0; i < 12; i++)
        for (let r = 0; r < 41; r++) setPixel(buf, c0 + i, faceTop(face, c0 + i) + r, (i === 0 || i === 11) && r % 4 !== 1 ? p.metal_shade : p.outline);
    } else if (role === 'side') {
      // Side panel: two rivets and vent louvres.
      for (const i of [2, 9]) setPixel(buf, c0 + i, faceTop(face, c0 + i) + 4, dark ? p.floor : p.highlight);
      for (let r = 9; r < 38; r += 3) for (let i = 2; i < 10; i++) setPixel(buf, c0 + i, faceTop(face, c0 + i) + r, dark ? p.outline : p.metal_shade);
    } else {
      // Back: a grille.
      for (let r = 4; r < 38; r += 2) for (let i = 1; i < 11; i++) setPixel(buf, c0 + i, faceTop(face, c0 + i) + r, dark ? p.outline : p.metal_shade);
    }
  }
  // Hazard-striped plinth round the base (rows 41-45 of each face column).
  for (let c = 1; c < 31; c++) {
    const face = c < 16 ? 'S' : 'E';
    const t0 = faceTop(face, c);
    for (let r = 41; r <= 45; r++) if (isOpaque(buf, c, t0 + r)) setPixel(buf, c, t0 + r, r === 41 ? p.outline : hazard(c, t0 + r, p.accent, p.outline));
  }
  // Antennae with status-light tips (red X sparks when failed), and rivets on the lid.
  for (const x of [9, 21]) {
    for (let k = 0; k < 4; k++) setPixel(buf, x + 1, 4 + k, k < 2 ? p.outline : p.metal_shade);
    stamp(buf, ctx, x, 1, moodOf(ctx) === 'fail' ? ['ror', 'oro', 'ror'] : ['.o.', 'oLo', '.o.']);
  }
  for (const [x, y] of [[16, 3], [6, 8], [26, 8], [16, 13]] as const) {
    setPixel(buf, x, y, p.highlight);
    setPixel(buf, x, y + 1, p.metal_shade);
  }
  // The robot's eyes sit on the lid along the front edge, looking out over the open bay (none when it faces away).
  const eyes = rot === 0 ? [[6, 7], [12, 10]] : rot === 3 ? [[22, 7], [16, 10]] : [];
  for (const [x, y] of eyes) eye(buf, ctx, x!, y!, 4);
  ghostify(buf, ctx, true);
};

// ------------------------------------------------------------------------------------------------ faceplates

/** Common faceplate: outline, role-coloured body, steel rack ears with screws. */
function plate(buf: PixelBuffer, ctx: Ctx, body: string): void {
  const p = P(ctx);
  const W = buf.width, H = buf.height;
  fillRect(buf, 0, 0, W, H, p.outline);
  fillRect(buf, 1, 1, W - 2, H - 2, body);
  for (const x0 of [1, W - 5]) {
    fillRect(buf, x0, 1, 4, H - 2, p.metal);
    fillRect(buf, x0 + 3, 1, 1, H - 2, p.metal_shade);
    for (const y of H >= 16 ? [3, H - 5] : [Math.floor(H / 2) - 1]) {
      setPixel(buf, x0 + 1, y, p.outline);
      setPixel(buf, x0 + 2, y, p.metal_shade);
      setPixel(buf, x0 + 1, y + 1, p.metal_shade);
    }
  }
  fillRect(buf, 5, 1, 1, H - 2, p.outline);
  fillRect(buf, W - 6, 1, 1, H - 2, p.outline);
}

const device1u: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  plate(buf, ctx, roleOf(ctx, p.metal));
  for (let x = 8; x < 40; x += 3) fillRect(buf, x, 2, 2, 4, p.outline); // vent slots
  eye(buf, ctx, 46, 2, 4);
  eye(buf, ctx, 52, 2, 4);
};

function bigPlate(units: 2 | 4): SlotDrawFn {
  return (buf, ctx) => {
    const p = P(ctx);
    const H = buf.height;
    plate(buf, ctx, roleOf(ctx, p.metal));
    // Drive caddies: steel trays with a small activity light.
    const rows = units === 2 ? 2 : 4;
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < 4; c++) {
        const x = 8 + c * 8, y = 2 + r * 6 + (units === 4 ? 2 : 0);
        fillRect(buf, x, y, 7, 6, p.outline);
        fillRect(buf, x + 1, y + 1, 5, 4, p.metal);
        fillRect(buf, x + 1, y + 4, 5, 1, p.metal_shade);
        setPixel(buf, x + 4, y + 2, (r + c) % 3 === 0 && !asleep(ctx) ? ledOf(ctx) : p.metal_shade);
      }
    // A friendly face: two round LED eyes and a smile (with a speaker grille below on 4U).
    if (units === 2) {
      eye(buf, ctx, 42, 2, 5);
      eye(buf, ctx, 50, 2, 5);
      stamp(buf, ctx, 45, 9, moodOf(ctx) === 'fail' ? ['.ooooo.', 'o.....o'] : ['o.....o', '.ooooo.']); // a frown when failed
    } else {
      eye(buf, ctx, 41, 4, 6);
      eye(buf, ctx, 50, 4, 6);
      const smile = ['o.......o', '.o.....o.', '..ooooo..'];
      stamp(buf, ctx, 43, 12, moodOf(ctx) === 'fail' ? [...smile].reverse() : smile);
      for (let y = 18; y < H - 3; y += 2) fillRect(buf, 41, y, 16, 1, p.outline);
      for (const x of [41, 55]) setPixel(buf, x, 11, p.accent);
    }
  };
}

const switchPlate: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  plate(buf, ctx, roleOf(ctx, p.accent_alt));
  // A long row of ports with link lights above: the switch's signature.
  for (let i = 0; i < 11; i++) {
    const x = 8 + i * 4;
    fillRect(buf, x, 3, 3, 4, p.outline);
    setPixel(buf, x + 1, 4, p.metal_shade);
    setPixel(buf, x + 1, 2, asleep(ctx) || moodOf(ctx) === 'fail' ? p.metal_shade : i % 4 === 3 ? p.led_warn : i % 3 === 2 ? p.metal_shade : p.led_ok);
  }
  eye(buf, ctx, 53, 2, 4);
};

const routerPlate: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  plate(buf, ctx, roleOf(ctx, p.accent));
  // Four-way arrows badge (routing), four chunky ports, two eyes.
  // Two-way arrows badge (routing).
  stamp(buf, ctx, 7, 1, ['..h....', '.hhhhhh', '..h....', '....h..', 'hhhhhh.', '....h..']);
  for (let i = 0; i < 4; i++) {
    const x = 16 + i * 6;
    fillRect(buf, x, 2, 5, 4, p.outline);
    fillRect(buf, x + 1, 3, 3, 2, p.metal_shade);
    setPixel(buf, x + 2, 3, p.metal);
  }
  eye(buf, ctx, 46, 2, 4);
  eye(buf, ctx, 52, 2, 4);
};

const patchPanel: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  plate(buf, ctx, roleOf(ctx, p.metal_shade));
  // Light steel strip with 20 tiny keystone holes and group ticks, then one status eye like every device.
  fillRect(buf, 7, 2, 41, 4, p.metal);
  fillRect(buf, 7, 5, 41, 1, p.metal_shade);
  for (let i = 0; i < 20; i++) fillRect(buf, 8 + i * 2, 3, 1, 2, p.outline);
  for (let i = 1; i < 4; i++) setPixel(buf, 7 + i * 10, 2, p.metal_shade);
  eye(buf, ctx, 51, 2, 4);
};

const psuPlate: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  plate(buf, ctx, roleOf(ctx, p.wire));
  // Lightning badge, a row of rounded outlets, a red rocker switch, one status light.
  stamp(buf, ctx, 7, 1, ['..yy', '.yy.', 'yyyy', '.yy.', 'yy..', 'y...']);
  for (let i = 0; i < 6; i++) stamp(buf, ctx, 13 + i * 5, 2, ['.oo.', 'ooso', 'oxxo', '.oo.']);
  stamp(buf, ctx, 45, 1, ['oooo', 'ohro', 'orro', 'oBBo', 'oBBo', 'oooo']);
  eye(buf, ctx, 51, 2, 4);
};

// ------------------------------------------------------------------------------------------------ rack frame, port

const rackUnit: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  const u = seedOf(ctx);
  // Empty bay: dark, with a faint U seam and cage-nut holes by the rails.
  fillRect(buf, 8, 0, 64, 8, p.outline);
  fillRect(buf, 8, 7, 64, 1, p.background);
  for (const x of [9, 70]) {
    setPixel(buf, x, 2, p.metal_shade);
    setPixel(buf, x, 5, p.metal_shade);
  }
  // Rails: steel girders with a mounting hole and a rivet per U; every fifth U gets an orange tick.
  for (const [x0, lit] of [[0, true], [72, false]] as const) {
    fillRect(buf, x0, 0, 8, 8, p.metal_shade);
    fillRect(buf, x0 + 1, 0, 1, 8, lit ? p.metal : p.floor);
    fillRect(buf, x0 + 2, 0, 4, 8, lit ? p.floor : p.floor_alt);
    fillRect(buf, x0, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 7, 0, 1, 8, p.outline);
    fillRect(buf, x0 + 3, 3, 2, 2, p.outline);
    setPixel(buf, x0 + 3, 1, p.highlight);
    if (u % 5 === 0) fillRect(buf, x0 + 2, 6, 4, 1, p.accent);
  }
};

const PORTS: Record<string, string[]> = {
  connected: ['oooooooo', 'oCCCCCCo', 'oChCCCgo', 'oCCCCCCo', 'osmmmmso', 'osmmmmso', 'oossssoo', '.oooooo.'],
  disabled: ['oooooooo', 'osssssso', 'osrssrso', 'ossrrsso', 'ossrrsso', 'osrssrso', 'osssssso', 'oooooooo'],
  mgmt: ['oooooooo', 'ohttttto', 'otkkkkto', 'otkkkkto', 'otkkkkto', 'otkookto', 'otttttto', 'oooooooo'],
  free: ['oooooooo', 'ommmmmso', 'omkkkkso', 'omkkkkso', 'omkkkkso', 'omkookso', 'osssssso', 'oooooooo'],
};
const port: SlotDrawFn = (buf, ctx) => {
  const s = ctx.variant.status ?? 'free';
  stamp(buf, ctx, 0, 0, PORTS[s] ?? PORTS.free!, { C: ctx.variant.wire_color ?? P(ctx).wire });
};

// ------------------------------------------------------------------------------------------------ conveyor-belt wires

/** Belt segments in tile coordinates: 'x' runs along u at v = 0.5 over [a, b]; 'y' runs along v at u = 0.5. */
type Seg = readonly ['x' | 'y', number, number];

function belts(segs: readonly Seg[], hub: boolean): SlotDrawFn {
  return (buf, ctx) => {
    const p = P(ctx);
    const belt = ctx.variant.wire_color ?? p.wire;
    const HALF = 0.14, RAIL = 0.2;
    forTile((x, y, u, v) => {
      let col: string | null = null;
      let onBelt = false;
      for (const [axis, a, b] of segs) {
        const along = axis === 'x' ? u : v;
        const across = axis === 'x' ? v : u;
        const d = Math.abs(across - 0.5);
        // Inner ends (at the tile centre) reach past it by the belt half-width so the L of a corner closes.
        const lo = a > 0 ? a - HALF : a, hi = b < 1 ? b + HALF : b;
        if (along < lo || along > hi) continue;
        if (d < HALF) {
          // Slats: screen lines across the belt (x + 2y is constant across an x run, 2y - x across a y run).
          const q = axis === 'x' ? x + 2 * y : 2 * y - x + 64;
          onBelt = true;
          col = q % 8 < 2 ? p.outline : belt;
        } else if (d < RAIL && !onBelt) col = across < 0.5 ? p.metal : p.metal_shade; // lit and shaded side rails
      }
      if (col) setPixel(buf, x, y, col);
    });
    // Roller hub where runs meet.
    if (hub) stamp(buf, ctx, 13, 6, ['.oooo.', 'ohmmmo', 'omssso', '.oooo.']);
  };
}

const CORNERS: Record<number, Seg[]> = {
  0: [['x', 0, 0.5], ['y', 0.5, 1]],
  1: [['x', 0, 0.5], ['y', 0, 0.5]],
  2: [['x', 0.5, 1], ['y', 0, 0.5]],
  3: [['x', 0.5, 1], ['y', 0.5, 1]],
};
const wireStraight: SlotDrawFn = (buf, ctx) => belts([[(ctx.variant.rotation ?? 0) % 2 === 0 ? 'x' : 'y', 0, 1]], false)(buf, ctx);
const wireCorner: SlotDrawFn = (buf, ctx) => belts(CORNERS[ctx.variant.rotation ?? 0]!, true)(buf, ctx);
const wireCross: SlotDrawFn = belts([['x', 0, 1], ['y', 0, 1]], true);

// ------------------------------------------------------------------------------------------------ props

/** Cog stencil (cream on a crate face). */
const GEAR = ['.x.x.', 'xxxxx', '.xox.', 'xxxxx', '.x.x.'];

const SMALL: readonly ((buf: PixelBuffer, ctx: Ctx) => void)[] = [
  // Helper bot: round head, visor with LED eyes, antenna, role-coloured chest, wheels.
  (buf, ctx) =>
    stamp(buf, ctx, 0, 0, [
      '.......oo.......',
      '......oLLo......',
      '.......oo.......',
      '.......os.......',
      '....oooooooo....',
      '...ohmmmmmmso...',
      '...omooooooso...',
      '...omoLooLoso...',
      '...omooooooso...',
      '...ommmmmmsso...',
      '....oooooooo....',
      '...ohRRRRRRRo...',
      '..omoRRxxRRoso..',
      '...ooRRRRRRoo...',
      '...osso..osso...',
      '....oo....oo....',
    ]),
  // Gear crate: a small orange crate with a cog stencil.
  (buf, ctx) => {
    const p = P(ctx);
    isoBox(buf, 8, 9, 0.375, 0.375, 7, { top: p.accent, left: p.wire, right: p.wall_shade });
    stamp(buf, ctx, 3, 9, ['.x.', 'xBx', '.x.']); // cog stencil
    fillRect(buf, 6, 4, 4, 1, p.wall); // lid slats
    fillRect(buf, 5, 6, 4, 1, p.wall);
    innerOutline(buf, p.outline);
  },
  // Oil can with a long spout and a teal label.
  (buf, ctx) =>
    stamp(buf, ctx, 0, 0, [
      '................',
      '.............o..',
      '............omo.',
      '...........oms..',
      '..........oms...',
      '.....oooooms....',
      '....ohmmmso.....',
      '...oaaaaaawo....',
      '...ohaaaaawo....',
      '...otttttttoo...',
      '...othxxxttowo..',
      '...otttttttowo..',
      '...oaaaaaawoo...',
      '...ohaaaaawo....',
      '....ooooooo.....',
      '................',
    ]),
  // Hazard barrel.
  (buf, ctx) => {
    const p = P(ctx);
    cylinder(buf, ctx, 3, 10, 4, 12, [p.floor, p.metal, p.floor, p.metal_shade, p.metal_shade], p.floor);
    for (let y = 7; y <= 10; y++) for (let x = 3; x < 13; x++) setPixel(buf, x, y, hazard(x, y, p.accent, p.outline));
    setPixel(buf, 6, 3, p.outline);
    setPixel(buf, 7, 3, p.outline);
    innerOutline(buf, p.outline);
  },
  // Spare cog leaning on a little block.
  (buf, ctx) => {
    const p = P(ctx);
    fillRect(buf, 9, 11, 6, 4, p.metal_shade);
    fillRect(buf, 9, 11, 6, 1, p.metal);
    cog(buf, ctx, 7, 8.5, 4.2, p.highlight, p.accent_alt, p.metal_shade);
    innerOutline(buf, p.outline);
  },
];

const decorationSmall: SlotDrawFn = (buf, ctx) => SMALL[seedOf(ctx) % SMALL.length]!(buf, ctx);

const LARGE: readonly ((buf: PixelBuffer, ctx: Ctx) => void)[] = [
  // Boiler tank with a pressure gauge, two riveted bands and a chimney pipe.
  (buf, ctx) => {
    const p = P(ctx);
    cylinder(buf, ctx, 6, 20, 12, 40, [p.metal, p.metal, p.floor, p.floor, p.floor_alt, p.metal_shade, p.metal_shade], p.metal);
    for (let x = 6; x < 26; x++) {
      const k = Math.round(Math.sqrt(Math.max(0, 1 - ((x + 0.5 - 16) / 10) ** 2)) * 4);
      for (const y0 of [18, 33]) {
        setPixel(buf, x, y0 + k, x < 15 ? p.accent : p.wire);
        setPixel(buf, x, y0 + k + 1, x < 15 ? p.accent : p.wire);
        if (x % 3 === 0) setPixel(buf, x, y0 + k, p.highlight);
      }
    }
    stamp(buf, ctx, 9, 24, ['.ooo.', 'oxxxo', 'oxoro', 'oxxxo', '.ooo.']);
    stamp(buf, ctx, 20, 3, ['oooo', 'ommo', 'oooo', '.mo.', '.mo.', '.mo.', '.mo.']);
    innerOutline(buf, p.outline);
  },
  // Robot arm: a turret base, two orange limbs with steel joints, a claw.
  (buf, ctx) => {
    const p = P(ctx);
    cylinder(buf, ctx, 7, 18, 36, 41, [p.metal, p.floor, p.floor, p.metal_shade], p.metal);
    const limb = (a: Pt, b: Pt, w: number) => {
      const n = Math.max(Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
      for (let i = 0; i <= n; i++) {
        const x = Math.round(a[0] + ((b[0] - a[0]) * i) / n), y = Math.round(a[1] + ((b[1] - a[1]) * i) / n);
        for (let k = 0; k < w; k++) setPixel(buf, x + k, y, k === 0 ? p.highlight : k < w - 1 ? p.accent : p.wire);
      }
    };
    limb([13, 35], [9, 18], 5);
    limb([10, 16], [21, 8], 5);
    stamp(buf, ctx, 11, 33, ['.ooo.', 'ommso', 'omsso', '.ooo.']);
    stamp(buf, ctx, 8, 14, ['.ooo.', 'ohmso', 'omsso', '.ooo.']);
    stamp(buf, ctx, 20, 5, ['.ooo.', 'ohLso', 'omsso', '.ooo.']);
    stamp(buf, ctx, 24, 6, ['mm..', '.m..', '.mm.', '....', '.mm.', '.m..', 'mm..']);
    innerOutline(buf, p.outline);
  },
  // Stacked gear crates.
  (buf, ctx) => {
    const p = P(ctx);
    isoBox(buf, 16, 33, 0.75, 0.75, 13, { top: p.accent, left: p.wire, right: p.wall_shade });
    // The top crate sits on the lower one's lid, centred (lid top corner at y = 33 - 13 = 20; its 0.5-tile footprint
    // starts a quarter tile in: 3 px down).
    isoBox(buf, 16, 23, 0.5, 0.5, 9, { top: p.accent_alt, left: p.floor, right: p.metal_shade });
    stamp(buf, ctx, 7, 37, GEAR);
    stamp(buf, ctx, 9, 25, ['..o..', '.ooo.', 'oo.oo', '.ooo.', '..o..']);
    fillRect(buf, 14, 17, 5, 1, p.outline); // carry handle on the toolbox lid
    setPixel(buf, 13, 18, p.outline);
    setPixel(buf, 19, 18, p.outline);
    innerOutline(buf, p.outline);
  },
  // Compressor: a steel box with a big fan, a gauge, a beacon and hazard stripes.
  (buf, ctx) => {
    const p = P(ctx);
    isoBox(buf, 16, 32, 1, 1, 18, { top: p.metal, left: p.floor, right: p.metal_shade });
    for (let x = 0; x < 16; x++) {
      const yb = 40 + Math.floor(x / 2);
      for (let z = 1; z <= 3; z++) setPixel(buf, x, yb - z, hazard(x, yb - z, p.accent, p.outline));
    }
    // Round fan grille: dark ring, three lit blades, a hub.
    for (let y = 22; y < 40; y++)
      for (let x = 1; x < 16; x++) {
        const dx = (x + 0.5 - 8) / 5.5, dy = (y + 0.5 - 30.5) / 5.5;
        const d = Math.hypot(dx, dy);
        if (d > 1) continue;
        const blade = Math.floor(((Math.atan2(dy, dx) + Math.PI) / (2 * Math.PI)) * 6 + d * 1.5) % 2 === 0;
        setPixel(buf, x, y, d > 0.8 ? p.outline : d < 0.25 ? p.metal : blade ? p.metal : p.metal_shade);
      }
    stamp(buf, ctx, 21, 25, ['.ooo.', 'oxxxo', 'oxogo', '.ooo.']);
    stamp(buf, ctx, 14, 9, ['.oo.', 'oLLo', '.oo.', '.o..', '.o..']);
    innerOutline(buf, p.outline);
  },
];

const decorationLarge: SlotDrawFn = (buf, ctx) => LARGE[seedOf(ctx) % LARGE.length]!(buf, ctx);

// ------------------------------------------------------------------------------------------------ UI

// 9-slice steel panel: bevelled frame, orange riveted corner caps, dark centre.
const uiFrame: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  fillRect(buf, 0, 0, 24, 24, p.outline);
  fillRect(buf, 1, 1, 22, 22, p.metal_shade);
  fillRect(buf, 1, 1, 22, 2, p.metal);
  fillRect(buf, 1, 1, 2, 22, p.metal);
  fillRect(buf, 1, 1, 22, 1, p.highlight);
  fillRect(buf, 1, 1, 1, 22, p.highlight);
  fillRect(buf, 4, 4, 16, 16, p.outline);
  fillRect(buf, 5, 5, 14, 14, p.background);
  for (const [x, y] of [[1, 1], [19, 1], [1, 19], [19, 19]] as const) {
    fillRect(buf, x, y, 4, 4, p.accent);
    setPixel(buf, x + 1, y + 1, p.highlight);
    setPixel(buf, x + 2, y + 2, p.wire);
  }
};

// Tile selector: a 2 px diamond outline in orange and cream hazard dashes.
const cursor: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  for (let r = 0; r < 16; r++) {
    const reach = r < 8 ? 2 * (r + 1) : 32 - 2 * r;
    const c = Math.floor(r / 2) % 2 === 0 ? p.accent : p.highlight;
    for (const x of [16 - reach, 17 - reach, 14 + reach, 15 + reach]) setPixel(buf, x, r, c);
  }
};

// Selection: a teal ring with a dark outer edge and cream corner studs.
const selectionHighlight: SlotDrawFn = (buf, ctx) => {
  const p = P(ctx);
  for (let y = 0; y < 18; y++)
    for (let x = 0; x < 34; x++) {
      const d = Math.abs(x + 0.5 - 17) / 17 + Math.abs(y + 0.5 - 9) / 9;
      if (d <= 1 && d > 0.8) setPixel(buf, x, y, d > 0.93 ? p.outline : p.accent_alt);
    }
  for (const [x, y] of [[16, 1], [17, 1], [2, 9], [31, 9], [16, 16], [17, 16]] as const) setPixel(buf, x, y, p.highlight);
};

/** Faceplates end with the planned/staged ghost pass. */
const ghosted = (fn: SlotDrawFn): SlotDrawFn => (buf, ctx) => {
  fn(buf, ctx);
  ghostify(buf, ctx);
};

const slots: Record<SlotId, SlotDrawFn> = {
  site_building: siteBuilding,
  room_floor: roomFloor,
  room_wall: roomWall,
  rack_empty: rackEmpty,
  device_1u: ghosted(device1u),
  device_2u: ghosted(bigPlate(2)),
  device_4u: ghosted(bigPlate(4)),
  switch: ghosted(switchPlate),
  router: ghosted(routerPlate),
  patch_panel: ghosted(patchPanel),
  psu: ghosted(psuPlate),
  rack_unit: rackUnit,
  port,
  wire_straight: wireStraight,
  wire_corner: wireCorner,
  wire_cross: wireCross,
  decoration_small: decorationSmall,
  decoration_large: decorationLarge,
  ui_frame: uiFrame,
  cursor,
  selection_highlight: selectionHighlight,
};

/** Prop counts, for tests: decorations are chosen by `variant.seed % n`. */
export const INDUSTRIAL_PROPS = { small: SMALL.length, large: LARGE.length } as const;

export const industrialTheme: Theme = { id: 'industrial-robotic', name: 'Industrial robots', palette, slots };
