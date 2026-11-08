// Placeholder theme 'debug': flat coloured boxes so the engine can render before real themes exist.
// Original, code-generated art. Every slot fills its footprint/face with one flat colour plus an outline, and every
// colour is a palette key (or the variant's role/cable colour), as the theme conformance test requires.
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

// Original palette (8 Nov 2026): cool slate greys, a warm amber accent and a teal second accent. Chosen by hand for
// legibility of the engine checks; not taken from any console or fantasy-console palette.
const palette: Palette = {
  background: '#172028',
  outline: '#0c1014',
  floor: '#56636e',
  floor_alt: '#4a5660',
  wall: '#9ba6ad',
  wall_shade: '#6f7a83',
  metal: '#b9c0c4',
  metal_shade: '#6b6f86',
  accent: '#e3922f',
  accent_alt: '#2f9fb5',
  led_ok: '#46c36f',
  led_warn: '#e6c84a',
  led_err: '#d8434e',
  wire: '#cf74a6',
  text: '#ecefe6',
  highlight: '#f8faf4',
};
/** An iso box: footprint diamond of w x h tiles, raised by `height` px, top at row 0 of the sprite. */
function box(buf: PixelBuffer, ctx: DrawContext, w: number, h: number, height: number, top: string, side: string): void {
  const [ax, ay] = ctx.spec.anchor;
  for (let z = height; z >= 1; z--) fillDiamond(buf, ax, ay - z, w, h, side);
  fillDiamond(buf, ax, ay - height, w, h, top);
}

function faceplate(color: (ctx: DrawContext) => string): SlotDrawFn {
  return (buf, ctx) => {
    fillRect(buf, 0, 0, buf.width, buf.height, ctx.palette.outline);
    fillRect(buf, 1, 1, buf.width - 2, buf.height - 2, color(ctx));
    setPixel(buf, buf.width - 4, 3, ctx.variant.status === 'failed' ? ctx.palette.led_err : ctx.palette.led_ok);
  };
}

const role = (ctx: DrawContext) => ctx.variant.role_color ?? ctx.palette.metal;

function wire(kind: 'straight' | 'corner' | 'cross'): SlotDrawFn {
  return (buf, ctx) => {
    const c = ctx.variant.wire_color ?? ctx.palette.wire;
    const rot = ctx.variant.rotation ?? 0;
    // Straight run along +x (screen down-right) through the tile centre (16, 8); rotation 1 runs along +y.
    const alongX = (t: number) => setPixel(buf, 8 + t, 4 + Math.floor(t / 2), c);
    const alongY = (t: number) => setPixel(buf, 23 - t, 4 + Math.floor(t / 2), c);
    if (kind === 'straight') for (let t = 0; t < 16; t++) (rot % 2 === 0 ? alongX : alongY)(t);
    if (kind === 'cross') for (let t = 0; t < 16; t++) { alongX(t); alongY(t); }
    // Corner (theme-slots.json): rotation 0 joins the -x and +y edges (tile's left corner); each rotation turns
    // 90 degrees clockwise on screen: 1 = -x/-y (top), 2 = +x/-y (right), 3 = +x/+y (bottom).
    if (kind === 'corner') for (let t = 0; t < 8; t++) { alongX(t + (rot >= 2 ? 8 : 0)); alongY(t + (rot === 0 || rot === 3 ? 8 : 0)); }
  };
}

const slots: Record<SlotId, SlotDrawFn> = {
  site_building: (buf, ctx) => box(buf, ctx, 2, 2, 48, ctx.palette.accent, ctx.palette.wall_shade),
  room_floor: (buf, ctx) => fillDiamond(buf, 16, 0, 1, 1, (ctx.variant.seed ?? 0) % 2 ? ctx.palette.floor_alt : ctx.palette.floor),
  room_wall: (buf, ctx) => {
    for (let t = 0; t < 16; t++) {
      const y0 = Math.floor(t / 2);
      fillRect(buf, t, y0, 1, 33, ctx.palette.wall);
    }
  },
  rack_empty: (buf, ctx) => box(buf, ctx, 1, 1, 48, ctx.palette.metal, ctx.palette.metal_shade),
  device_1u: faceplate(role),
  device_2u: faceplate(role),
  device_4u: faceplate(role),
  switch: faceplate((ctx) => ctx.palette.accent_alt),
  router: faceplate((ctx) => ctx.palette.accent),
  patch_panel: faceplate((ctx) => ctx.palette.metal),
  psu: faceplate((ctx) => ctx.palette.led_err),
  rack_unit: (buf, ctx) => {
    fillRect(buf, 0, 0, 8, 8, ctx.palette.metal_shade);
    fillRect(buf, 72, 0, 8, 8, ctx.palette.metal_shade);
    fillRect(buf, 8, 0, 64, 8, ctx.palette.background);
    setPixel(buf, 3, 3, ctx.palette.outline);
    setPixel(buf, 76, 3, ctx.palette.outline);
  },
  port: (buf, ctx) => {
    const fill = ctx.variant.status === 'connected' ? (ctx.variant.wire_color ?? ctx.palette.wire)
      : ctx.variant.status === 'disabled' ? ctx.palette.led_err : ctx.variant.status === 'mgmt' ? ctx.palette.accent : ctx.palette.floor;
    fillRect(buf, 0, 0, 8, 8, ctx.palette.outline);
    fillRect(buf, 1, 1, 6, 6, fill);
  },
  wire_straight: wire('straight'),
  wire_corner: wire('corner'),
  wire_cross: wire('cross'),
  decoration_small: (buf, ctx) => fillRect(buf, 4, 4, 8, 12, ctx.palette.led_ok),
  decoration_large: (buf, ctx) => box(buf, ctx, 1, 1, 32, ctx.palette.led_ok, ctx.palette.wall_shade),
  ui_frame: (buf, ctx) => {
    fillRect(buf, 0, 0, 24, 24, ctx.palette.outline);
    fillRect(buf, 1, 1, 22, 22, ctx.palette.text);
    fillRect(buf, 2, 2, 20, 20, ctx.palette.background);
  },
  cursor: (buf, ctx) => {
    // Diamond outline: the two outermost pixels of every row of a 1x1 tile.
    for (let r = 0; r < 16; r++) {
      const reach = r < 8 ? 2 * (r + 1) : 16 - 2 * (r - 8);
      for (const x of [16 - reach, 17 - reach, 14 + reach, 15 + reach]) setPixel(buf, x, r, ctx.palette.highlight);
    }
  },
  selection_highlight: (buf, ctx) => fillDiamond(buf, 17, 1, 1, 1, ctx.palette.accent_alt),
};

export const debugTheme: Theme = { id: 'debug', name: 'Debug boxes', palette, slots };
