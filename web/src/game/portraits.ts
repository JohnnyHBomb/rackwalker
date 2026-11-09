// Dialogue and field-guide portraits: indexed pixel art at 64 x 64 and 96 x 96 in the overworld palette, made from
// hand-painted vector sources by web/art/portraits/portrait.mjs (rasterise, area downscale, palette map, hand
// clean-up stored as pixel edits). Shown at 1x at their own size; nothing here draws shapes.
import type { Speaker } from './dialogue';
import portraitsJson from './world/assets/portraits.json';
import { PALETTE } from './world/engine';
import paletteJson from './world/assets/palette.json';

interface PortraitJson {
  w: number;
  h: number;
  frames: string[][];
}
const DATA = portraitsJson as Record<Speaker, Record<'64' | '96', PortraitJson>>;
const ALPH: string = paletteJson.alphabet;

/** The portrait as RGBA rows (tests). */
export function portraitPixels(who: Speaker, size: 64 | 96): Uint32Array {
  const p = DATA[who][String(size) as '64' | '96'];
  const out = new Uint32Array(p.w * p.h);
  p.frames[0]!.forEach((row, y) => {
    for (let x = 0; x < p.w; x++) out[y * p.w + x] = PALETTE.rgba[ALPH.indexOf(row[x]!)]!;
  });
  return out;
}

/** A portrait canvas: 64 x 64 for `scale` 2 and below (small screens), else 96 x 96. */
export function drawPortrait(who: Speaker, scale: number): HTMLCanvasElement {
  const size = scale >= 3 ? 96 : 64;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  c.className = `portrait portrait-${who}`;
  c.style.imageRendering = 'pixelated';
  const g = c.getContext('2d')!;
  const img = g.createImageData(size, size);
  new Uint32Array(img.data.buffer).set(portraitPixels(who, size));
  g.putImageData(img, 0, 0);
  c.setAttribute('role', 'img');
  c.setAttribute('aria-label', `${who[0]!.toUpperCase()}${who.slice(1)}`);
  return c;
}

export const SPEAKER_NAMES: Readonly<Record<Speaker, string>> = { ilatea: 'Ilatea', byte: 'Byte', nocti: 'Nocti' };
