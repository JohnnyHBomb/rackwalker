// CONTRACT 2.0.0: sprite/theme manifest. Mirrors contracts/theme-slots.json
// (a test checks they agree). Change only by contract change request; see contracts/README.md.
//
// A theme is pure data + pure functions: each slot function draws pixel art into a PixelBuffer that the
// engine allocates at the slot's declared size. Drawing is deterministic (same inputs -> same pixels), so
// themes can be golden-image tested without WebGL. The engine turns buffers into PIXI textures
// (nearest-neighbour) and caches them per (theme, slot, variant).

import type { ThemeId } from './scene.gen';
export type { ThemeId };

export const THEME_IDS: readonly ThemeId[] = ['debug', 'industrial-robotic', 'farm', 'business', 'pagodas-nature'];

export const SLOT_IDS = [
  'site_building',
  'room_floor',
  'room_wall',
  'rack_empty',
  'device_1u',
  'device_2u',
  'device_4u',
  'switch',
  'router',
  'patch_panel',
  'psu',
  'rack_unit',
  'port',
  'wire_straight',
  'wire_corner',
  'wire_cross',
  'decoration_small',
  'decoration_large',
  'ui_frame',
  'cursor',
  'selection_highlight',
] as const;
export type SlotId = (typeof SLOT_IDS)[number];

/** 16 fixed palette keys: every theme defines all of them. */
export const PALETTE_KEYS = [
  'background',
  'outline',
  'floor',
  'floor_alt',
  'wall',
  'wall_shade',
  'metal',
  'metal_shade',
  'accent',
  'accent_alt',
  'led_ok',
  'led_warn',
  'led_err',
  'wire',
  'text',
  'highlight',
] as const;
export type PaletteKey = (typeof PALETTE_KEYS)[number];
/** Colours as '#rrggbb' (lower case). */
export type Palette = Readonly<Record<PaletteKey, string>>;

export type SlotSpace = 'world' | 'rack' | 'screen';

export interface SlotSpec {
  readonly id: SlotId;
  readonly space: SlotSpace;
  readonly width: number;
  readonly height: number;
  /** Sprite pixel placed on the object's ground point (see contracts/iso.md and theme-slots.json). */
  readonly anchor: readonly [number, number];
  /** Tiles covered on the iso grid (world slots); null for rack/screen slots. */
  readonly footprint: readonly [number, number] | null;
}

export const SLOT_SPECS: Readonly<Record<SlotId, SlotSpec>> = {
  site_building: { id: 'site_building', space: 'world', width: 64, height: 80, anchor: [32, 48], footprint: [2, 2] },
  room_floor: { id: 'room_floor', space: 'world', width: 32, height: 16, anchor: [16, 0], footprint: [1, 1] },
  room_wall: { id: 'room_wall', space: 'world', width: 16, height: 40, anchor: [0, 32], footprint: [1, 0] },
  rack_empty: { id: 'rack_empty', space: 'world', width: 32, height: 64, anchor: [16, 48], footprint: [1, 1] },
  device_1u: { id: 'device_1u', space: 'rack', width: 64, height: 8, anchor: [0, 0], footprint: null },
  device_2u: { id: 'device_2u', space: 'rack', width: 64, height: 16, anchor: [0, 0], footprint: null },
  device_4u: { id: 'device_4u', space: 'rack', width: 64, height: 32, anchor: [0, 0], footprint: null },
  switch: { id: 'switch', space: 'rack', width: 64, height: 8, anchor: [0, 0], footprint: null },
  router: { id: 'router', space: 'rack', width: 64, height: 8, anchor: [0, 0], footprint: null },
  patch_panel: { id: 'patch_panel', space: 'rack', width: 64, height: 8, anchor: [0, 0], footprint: null },
  psu: { id: 'psu', space: 'rack', width: 64, height: 8, anchor: [0, 0], footprint: null },
  rack_unit: { id: 'rack_unit', space: 'rack', width: 80, height: 8, anchor: [8, 0], footprint: null },
  port: { id: 'port', space: 'rack', width: 8, height: 8, anchor: [0, 0], footprint: null },
  wire_straight: { id: 'wire_straight', space: 'world', width: 32, height: 16, anchor: [16, 0], footprint: [1, 1] },
  wire_corner: { id: 'wire_corner', space: 'world', width: 32, height: 16, anchor: [16, 0], footprint: [1, 1] },
  wire_cross: { id: 'wire_cross', space: 'world', width: 32, height: 16, anchor: [16, 0], footprint: [1, 1] },
  decoration_small: { id: 'decoration_small', space: 'world', width: 16, height: 16, anchor: [8, 7], footprint: [1, 1] },
  decoration_large: { id: 'decoration_large', space: 'world', width: 32, height: 48, anchor: [16, 32], footprint: [1, 1] },
  ui_frame: { id: 'ui_frame', space: 'screen', width: 24, height: 24, anchor: [0, 0], footprint: null },
  cursor: { id: 'cursor', space: 'world', width: 32, height: 16, anchor: [16, 0], footprint: [1, 1] },
  selection_highlight: { id: 'selection_highlight', space: 'world', width: 34, height: 18, anchor: [17, 1], footprint: [1, 1] },
};

/** RGBA8 pixel buffer, row-major, origin top-left. Allocated by the engine at the slot's size. */
export interface PixelBuffer {
  readonly width: number;
  readonly height: number;
  readonly data: Uint8ClampedArray; // length = width * height * 4
}

/** Per-draw inputs. All optional; slots ignore what they do not use. */
export interface SlotVariant {
  /** Quarter turns clockwise on screen (racks, wires). */
  readonly rotation?: 0 | 1 | 2 | 3;
  /** NetBox role colour '#rrggbb' (device faceplates). */
  readonly role_color?: string;
  /** NetBox cable colour '#rrggbb' (wires); falls back to palette.wire. */
  readonly wire_color?: string;
  /** NetBox status value; themes may dim planned/offline objects. */
  readonly status?: string;
  /** Deterministic choice among a theme's alternatives (props, floor patterns). */
  readonly seed?: number;
}

export interface DrawContext {
  readonly palette: Palette;
  readonly spec: SlotSpec;
  readonly variant: SlotVariant;
}

/** Draws one sprite. Must be pure and deterministic, and write only inside the buffer. */
export type SlotDrawFn = (buf: PixelBuffer, ctx: DrawContext) => void;

export interface Theme {
  readonly id: ThemeId;
  readonly name: string;
  readonly palette: Palette;
  readonly slots: Readonly<Record<SlotId, SlotDrawFn>>;
}

// ---------------------------------------------------------------------------------------------------------
// Helpers every theme may use (part of the contract so themes do not each reinvent them).

export function createPixelBuffer(width: number, height: number): PixelBuffer {
  return { width, height, data: new Uint8ClampedArray(width * height * 4) };
}

export function hexToRgba(hex: string): [number, number, number, number] {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) throw new Error(`bad colour ${hex}`);
  const n = parseInt(m[1]!, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255];
}

/** Sets one pixel; silently ignores coordinates outside the buffer. */
export function setPixel(buf: PixelBuffer, x: number, y: number, hex: string): void {
  if (x < 0 || y < 0 || x >= buf.width || y >= buf.height || !Number.isInteger(x) || !Number.isInteger(y)) return;
  const [r, g, b, a] = hexToRgba(hex);
  const i = (y * buf.width + x) * 4;
  buf.data[i] = r;
  buf.data[i + 1] = g;
  buf.data[i + 2] = b;
  buf.data[i + 3] = a;
}

export function fillRect(buf: PixelBuffer, x: number, y: number, w: number, h: number, hex: string): void {
  for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) setPixel(buf, i, j, hex);
}

/**
 * Fills the 2:1 iso diamond whose top corner is at (cx, top) and which spans `tilesW` x `tilesH` tiles
 * (tile = 32x16). Rows use the pixel-exact 2-across-1-down stepping of iso/TILE_W.
 */
export function fillDiamond(buf: PixelBuffer, cx: number, top: number, tilesW: number, tilesH: number, hex: string): void {
  // Classic pixel-art diamond: a 1x1 tile has 16 rows of widths 4, 8, ..., 32, 32, ..., 8, 4.
  const w8 = tilesW * 8;
  const h8 = tilesH * 8;
  const reach = (r: number, n8: number) => (r < n8 ? 2 * (r + 1) : 2 * n8 - 2 * (r - n8));
  for (let r = 0; r < w8 + h8; r++) {
    const left = cx - reach(r, h8); // the left edge follows the +y side
    const right = cx + reach(r, w8); // the right edge follows the +x side
    for (let x = left; x < right; x++) setPixel(buf, x, top + r, hex);
  }
}

/** Runs a theme's slot at its declared size (what the engine and the golden tests do). */
export function renderSlot(theme: Theme, slot: SlotId, variant: SlotVariant = {}): PixelBuffer {
  const spec = SLOT_SPECS[slot];
  const buf = createPixelBuffer(spec.width, spec.height);
  theme.slots[slot](buf, { palette: theme.palette, spec, variant });
  return buf;
}
