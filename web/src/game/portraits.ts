// Placeholder portraits for the dialogue bubble and the field-guide pages, drawn by code on a 32 x 32 grid and shown
// at 2x or 3x (64 / 96 px). Traits follow docs/art/guide-ilatea.md; the final art replaces these later.
import type { Speaker } from './dialogue';

export const PORTRAIT = 32;
type Px = (x: number, y: number, w: number, h: number, c: string) => void;

function disc(px: Px, cx: number, cy: number, rx: number, ry: number, c: string): void {
  for (let y = -ry; y <= ry; y++) {
    const half = Math.floor(rx * Math.sqrt(Math.max(0, 1 - (y * y) / (ry * ry))));
    px(cx - half, cy + y, half * 2 + 1, 1, c);
  }
}

/** Ilatea: dark hair with silver strands, owl-gold eyes, dark chiton with blue circuit lines, hexagon aegis behind. */
function ilatea(px: Px): void {
  px(0, 0, 32, 32, '#1b2236');
  for (let y = 2; y < 30; y += 6) for (let x = (y / 6) % 2 ? 0 : 3; x < 32; x += 7) px(x, y, 4, 1, '#2f4a6e'), px(x - 1, y + 1, 1, 2, '#2f4a6e'), px(x + 4, y + 1, 1, 2, '#2f4a6e');
  disc(px, 16, 13, 9, 11, '#2a2030');
  px(7, 14, 3, 16, '#2a2030'), px(22, 14, 3, 16, '#2a2030');
  px(9, 6, 1, 18, '#c9d3e0'), px(23, 9, 1, 14, '#c9d3e0');
  disc(px, 16, 15, 6, 7, '#d9a77e');
  px(10, 8, 12, 3, '#2a2030');
  px(13, 14, 2, 2, '#f2c14e'), px(18, 14, 2, 2, '#f2c14e');
  px(13, 14, 1, 1, '#fff3c4'), px(18, 14, 1, 1, '#fff3c4');
  px(14, 19, 4, 1, '#9a5a4a'), px(13, 18, 1, 1, '#9a5a4a'), px(18, 18, 1, 1, '#9a5a4a');
  px(8, 24, 16, 8, '#1e2a44');
  px(10, 26, 12, 1, '#5fb8ff'), px(15, 26, 1, 6, '#5fb8ff'), px(11, 29, 4, 1, '#5fb8ff');
  px(27, 4, 1, 26, '#c9d3e0'), px(26, 2, 3, 3, '#9fe3ff'), px(27, 1, 1, 1, '#ffffff');
}

/** An owl: body colours, eye ring, beak. Byte is blue and white, Nocti brown and black and a little rumpled. */
function owl(px: Px, body: string, belly: string, ring: string, bg: string, rumpled: boolean): void {
  px(0, 0, 32, 32, bg);
  disc(px, 16, 18, 10, 12, body);
  disc(px, 16, 22, 6, 7, belly);
  px(7, 5, 3, 4, body), px(22, 5, 3, 4, body);
  disc(px, 12, 13, 4, 4, ring), disc(px, 20, 13, 4, 4, ring);
  disc(px, 12, 13, 2, 2, '#f2c14e'), disc(px, 20, 13, 2, 2, '#f2c14e');
  px(12, 13, 1, 1, '#111111'), px(20, 13, 1, 1, '#111111');
  px(15, 16, 2, 3, '#e0a030');
  if (rumpled) px(13, 4, 1, 3, body), px(18, 5, 2, 2, body), px(25, 20, 2, 1, belly);
  for (let i = 0; i < 3; i++) px(13 + i * 2, 21 + (i % 2), 1, 1, body);
}

export function drawPortrait(who: Speaker, scale: number): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = c.height = PORTRAIT * scale;
  c.className = `portrait portrait-${who}`;
  c.style.imageRendering = 'pixelated';
  const g = c.getContext('2d')!;
  const px: Px = (x, y, w, h, col) => {
    g.fillStyle = col;
    g.fillRect(x * scale, y * scale, w * scale, h * scale);
  };
  if (who === 'ilatea') ilatea(px);
  else if (who === 'byte') owl(px, '#3d6fd1', '#eef4ff', '#ffffff', '#16233d', false);
  else owl(px, '#6b4426', '#2a1b12', '#b88a5a', '#1f1a14', true);
  c.setAttribute('role', 'img');
  c.setAttribute('aria-label', `${who[0]!.toUpperCase()}${who.slice(1)}`);
  return c;
}

export const SPEAKER_NAMES: Readonly<Record<Speaker, string>> = { ilatea: 'Ilatea', byte: 'Byte', nocti: 'Nocti' };
