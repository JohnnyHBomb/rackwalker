// A 3 x 5 pixel font for names on the canvas (site, room, rack, device, U numbers, port rows), drawn for this
// project. Upper case only: lower-case letters are shown as capitals. Labels are plates in the theme's own palette
// (`background` behind `text`), so they read on any floor and stay within the theme's colours.
import { createPixelBuffer, hexToRgba, type Palette, type PixelBuffer } from '../contracts/theme';

export const GLYPH_W = 3;
export const GLYPH_H = 5;
/** Plate padding around the text, px. */
export const LABEL_PAD = 1;
/** Longest label; longer names end in '.'. */
export const LABEL_MAX = 24;

// Each glyph: five rows of three bits, top row first ('1' = ink).
const G: Record<string, string> = {
  A: '010101111101101', B: '110101110101110', C: '011100100100011', D: '110101101101110', E: '111100110100111',
  F: '111100110100100', G: '011100101101011', H: '101101111101101', I: '111010010010111', J: '001001001101010',
  K: '101101110101101', L: '100100100100111', M: '101111111101101', N: '110101101101101', O: '010101101101010',
  P: '110101110100100', Q: '010101101110011', R: '110101110101101', S: '011100010001110', T: '111010010010010',
  U: '101101101101111', V: '101101101101010', W: '101101111111101', X: '101101010101101', Y: '101101010010010',
  Z: '111001010100111',
  '0': '111101101101111', '1': '010110010010111', '2': '110001010100111', '3': '110001010001110', '4': '101101111001001',
  '5': '111100110001110', '6': '011100110101010', '7': '111001010010010', '8': '010101010101010', '9': '010101011001110',
  ' ': '000000000000000', '-': '000000111000000', '/': '001001010100100', '.': '000000000000010', _: '000000000000111',
  ':': '000010000010000', '#': '101111101111101', '(': '001010010010001', ')': '100010010010100', '+': '000010111010000',
  ',': '000000000010100', "'": '010010000000000', '?': '110001010000010', '&': '010101010101011', '>': '100010001010100',
  '<': '001010100010001', '=': '000111000111000', '*': '000101010101000', '@': '010101111100011', '|': '010010010010010',
};

/** The text as the font draws it: capitals, unknown characters as '?', at most LABEL_MAX characters. */
export function labelText(s: string): string {
  const up = [...s.toUpperCase()].map((c) => (G[c] ? c : '?')).join('');
  return up.length > LABEL_MAX ? `${up.slice(0, LABEL_MAX - 1)}.` : up;
}

/** Plate size of a label at scale 1 (text width + padding). */
export function labelSize(text: string): { w: number; h: number } {
  const n = labelText(text).length;
  return { w: Math.max(0, n * (GLYPH_W + 1) - 1) + 2 * LABEL_PAD, h: GLYPH_H + 2 * LABEL_PAD };
}

/** The label's pixels: `text` in palette.text on a palette.background plate. */
export function labelBuffer(text: string, palette: Palette): PixelBuffer {
  const t = labelText(text);
  const { w, h } = labelSize(text);
  const buf = createPixelBuffer(w, h);
  const bg = hexToRgba(palette.background);
  const ink = hexToRgba(palette.text);
  for (let i = 0; i < w * h; i++) buf.data.set(bg, i * 4);
  [...t].forEach((ch, k) => {
    const bits = G[ch]!;
    for (let r = 0; r < GLYPH_H; r++)
      for (let c = 0; c < GLYPH_W; c++)
        if (bits[r * GLYPH_W + c] === '1') buf.data.set(ink, ((LABEL_PAD + r) * w + LABEL_PAD + k * (GLYPH_W + 1) + c) * 4);
  });
  return buf;
}
