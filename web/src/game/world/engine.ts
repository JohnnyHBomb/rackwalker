// The overworld tile engine: composes each frame into an indexed framebuffer (one byte per pixel, a palette index),
// the way a 16-bit console composes its tile layer and sprite layer, and applies the palette only at the end.
//   1. tile layer: the ground autotiles under the camera (water and fields switch frames);
//   2. flat sprites (mats, pads, bridge decks) on the ground;
//   3. cast shadows: ellipses remapped one ramp step darker through the palette (no alpha);
//   4. glow (the guide): remapped one step lighter;
//   5. sprites sorted by their base line (y), so what stands lower on screen is drawn in front;
//   6. top sprites (sails, weather, labels, door markers).
// Integer positions only. Nothing here draws a shape or a fill: every pixel of the world comes from tile and sprite
// data (assets/*.json, written by web/art/overworld/author_world.py).
import paletteJson from './assets/palette.json';
import spritesJson from './assets/sprites.json';
import tilesJson from './assets/tiles.json';

export const NATIVE_W = 256;
export const NATIVE_H = 224;
export const TILE = 16;

interface SpriteJson {
  w: number;
  h: number;
  ox: number;
  oy: number;
  frames: string[][];
}
export interface Sprite {
  readonly w: number;
  readonly h: number;
  readonly ox: number;
  readonly oy: number;
  readonly frames: Uint8Array[];
}
export interface Palette {
  readonly colours: readonly string[];
  readonly names: readonly string[];
  readonly darker: Uint8Array;
  readonly lighter: Uint8Array;
  readonly glow: Uint8Array;
  /** RGBA per index, as one little-endian 32-bit word (ImageData's byte order). */
  readonly rgba: Uint32Array;
  index(name: string): number;
}

const ALPH: string = paletteJson.alphabet;
const DEC = new Map<string, number>([...ALPH].map((c, i) => [c, i]));
function decode(s: string, out: Uint8Array, at: number): void {
  for (let i = 0; i < s.length; i++) out[at + i] = DEC.get(s[i]!) ?? 0;
}

function makePalette(): Palette {
  const colours = paletteJson.colours as string[];
  const rgba = new Uint32Array(colours.length);
  colours.forEach((h, i) => {
    const n = parseInt(h.slice(1), 16);
    rgba[i] = (255 << 24) | ((n & 255) << 16) | (((n >> 8) & 255) << 8) | ((n >> 16) & 255);
  });
  const names = paletteJson.names as string[];
  const idx = new Map(names.map((n, i) => [n, i]));
  return {
    colours,
    names,
    darker: Uint8Array.from(paletteJson.darker as number[]),
    lighter: Uint8Array.from(paletteJson.lighter as number[]),
    glow: Uint8Array.from(paletteJson.glow as number[]),
    rgba,
    index: (n) => {
      const i = idx.get(n);
      if (i === undefined) throw new Error(`no colour ${n}`);
      return i;
    },
  };
}

export const PALETTE = makePalette();

function decodeSprite(s: SpriteJson): Sprite {
  return {
    w: s.w,
    h: s.h,
    ox: s.ox,
    oy: s.oy,
    frames: s.frames.map((rows) => {
      const px = new Uint8Array(s.w * s.h);
      rows.forEach((r, y) => decode(r, px, y * s.w));
      return px;
    }),
  };
}

export const SPRITES = new Map<string, Sprite>(Object.entries(spritesJson as Record<string, SpriteJson>).map(([k, v]) => [k, decodeSprite(v)]));
export function sprite(name: string): Sprite {
  const s = SPRITES.get(name);
  if (!s) throw new Error(`no sprite ${name}`);
  return s;
}
/** Adds a sprite made at run time (site name plates). */
export function addSprite(name: string, s: Sprite): void {
  SPRITES.set(name, s);
}

export interface TileSet {
  readonly tiles: Uint8Array[];
  readonly keys: ReadonlyMap<string, readonly number[]>;
  readonly sides: Readonly<Record<string, Readonly<Record<string, string>>>>;
  readonly variants: Readonly<Record<string, number>>;
  readonly frameMs: number;
}
export const TILESET: TileSet = {
  tiles: (tilesJson.tiles as string[]).map((s) => {
    const px = new Uint8Array(256);
    decode(s, px, 0);
    return px;
  }),
  keys: new Map(Object.entries(tilesJson.keys as Record<string, number[]>)),
  sides: tilesJson.sides as Record<string, Record<string, string>>,
  variants: tilesJson.variants as Record<string, number>,
  frameMs: tilesJson.frameMs,
};

/** The autotile key of a cell: its class, its four neighbours reduced to what it cares about, a grass variant. */
export function tileKey(c: string, n: string, e: string, s: string, w: string, variant: number): string {
  const centre = c === 'B' ? 'W' : c;
  const side = TILESET.sides[centre]!;
  const nv = TILESET.variants[centre] ?? 1;
  return `${centre}${side[n] ?? 'G'}${side[e] ?? 'G'}${side[s] ?? 'G'}${side[w] ?? 'G'}${variant % nv}`;
}

export interface Drawable {
  readonly sprite: string;
  readonly x: number;
  readonly y: number;
  readonly frame?: number;
  readonly mirror?: boolean;
}

export class Frame {
  readonly px: Uint8Array;
  constructor(readonly w: number, readonly h: number) {
    this.px = new Uint8Array(w * h);
  }
  /** Copies one tile with its top-left at (x, y), clipped to the frame. */
  tile(t: Uint8Array, x: number, y: number): void {
    const x0 = Math.max(0, -x), x1 = Math.min(TILE, this.w - x);
    if (x1 <= x0) return;
    for (let j = Math.max(0, -y); j < TILE && y + j < this.h; j++) this.px.set(t.subarray(j * TILE + x0, j * TILE + x1), (y + j) * this.w + x + x0);
  }
  /** Draws a sprite frame with its anchor at (x, y); index 0 is transparent. */
  sprite(s: Sprite, frame: number, x: number, y: number, mirror = false): void {
    const f = s.frames[((frame % s.frames.length) + s.frames.length) % s.frames.length]!;
    const ox = x - s.ox, oy = y - s.oy;
    const j0 = Math.max(0, -oy), j1 = Math.min(s.h, this.h - oy);
    const i0 = Math.max(0, -ox), i1 = Math.min(s.w, this.w - ox);
    for (let j = j0; j < j1; j++) {
      const row = (oy + j) * this.w + ox, src = j * s.w;
      for (let i = i0; i < i1; i++) {
        const v = f[src + (mirror ? s.w - 1 - i : i)]!;
        if (v) this.px[row + i] = v;
      }
    }
  }
  /** Remaps the pixels inside an ellipse through a palette table; the outer ring is dithered 50 %. */
  ellipse(cx: number, cy: number, rx: number, ry: number, table: Uint8Array): void {
    for (let y = Math.max(0, Math.floor(cy - ry)); y <= Math.min(this.h - 1, Math.ceil(cy + ry)); y++)
      for (let x = Math.max(0, Math.floor(cx - rx)); x <= Math.min(this.w - 1, Math.ceil(cx + rx)); x++) {
        const d = ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2;
        if (d > 1 || (d > 0.62 && (x + y) & 1)) continue;
        const i = y * this.w + x;
        this.px[i] = table[this.px[i]!]!;
      }
  }
  /** Writes the frame as RGBA into `out` (a Uint32Array view of an ImageData). */
  present(out: Uint32Array): void {
    const lut = PALETTE.rgba;
    for (let i = 0; i < this.px.length; i++) out[i] = lut[this.px[i]!]!;
  }
}
