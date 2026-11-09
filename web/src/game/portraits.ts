// Dialogue and field-guide portraits: indexed pixel art at 64 x 64 and 96 x 96 in the overworld palette, made from
// hand-painted vector sources by web/art/portraits/portrait.mjs (rasterise, area downscale, palette map, hand
// clean-up stored as pixel edits). Shown at 1x at their own size; nothing here draws shapes.
import type { Speaker } from './dialogue';
import portraitsJson from './world/assets/portraits.json';
import { PALETTE, SPRITES } from './world/engine';
import paletteJson from './world/assets/palette.json';

interface PortraitJson {
  w: number;
  h: number;
  frames: string[][];
}
type Painted = Exclude<Speaker, 'giver'>;
const DATA = portraitsJson as Record<Painted, Record<'64' | '96', PortraitJson>>;

/**
 * A quest giver's portrait: their overworld sprite at a whole-number scale on a plain panel of the palette. A stand-in
 * until the animal cast has painted portraits of its own; every pixel is still sprite data in the game's palette.
 */
export function giverPixels(spriteName: string, size: 64 | 96): Uint32Array {
  const out = new Uint32Array(size * size).fill(PALETTE.rgba[PALETTE.index('l1')]!);
  const s = SPRITES.get(spriteName);
  if (!s) return out;
  const k = Math.max(1, Math.floor((size - 8) / Math.max(s.w, s.h)));
  const x0 = (size - s.w * k) >> 1, y0 = size - s.h * k - 4;
  const f = s.frames[0]!;
  // a floor line under the feet, one ramp step lighter than the panel
  for (let x = 4; x < size - 4; x++) for (let y = size - 6; y < size - 4; y++) out[y * size + x] = PALETTE.rgba[PALETTE.index('l2')]!;
  for (let y = 0; y < s.h * k; y++)
    for (let x = 0; x < s.w * k; x++) {
      const v = f[Math.floor(y / k) * s.w + Math.floor(x / k)]!;
      if (v && y0 + y >= 0) out[(y0 + y) * size + x0 + x] = PALETTE.rgba[v]!;
    }
  return out;
}
const ALPH: string = paletteJson.alphabet;

/** The portrait as RGBA rows (tests). */
export function portraitPixels(who: Speaker, size: 64 | 96, giverSprite = 'rabbit'): Uint32Array {
  if (who === 'giver') return giverPixels(giverSprite, size);
  const p = DATA[who][String(size) as '64' | '96'];
  const out = new Uint32Array(p.w * p.h);
  p.frames[0]!.forEach((row, y) => {
    for (let x = 0; x < p.w; x++) out[y * p.w + x] = PALETTE.rgba[ALPH.indexOf(row[x]!)]!;
  });
  return out;
}

/** A portrait canvas: 64 x 64 for `scale` 2 and below (small screens), else 96 x 96. */
export function drawPortrait(who: Speaker, scale: number, giver: { name: string; sprite: string } | null = null): HTMLCanvasElement {
  const size = scale >= 3 ? 96 : 64;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  c.className = `portrait portrait-${who}`;
  c.style.imageRendering = 'pixelated';
  const g = c.getContext('2d')!;
  const img = g.createImageData(size, size);
  new Uint32Array(img.data.buffer).set(portraitPixels(who, size, giver?.sprite));
  g.putImageData(img, 0, 0);
  c.setAttribute('role', 'img');
  c.setAttribute('aria-label', who === 'giver' && giver ? giver.name : `${who[0]!.toUpperCase()}${who.slice(1)}`);
  return c;
}

export const SPEAKER_NAMES: Readonly<Record<Speaker, string>> = { ilatea: 'Ilatea', byte: 'Byte', nocti: 'Nocti', giver: 'Quest giver' };
