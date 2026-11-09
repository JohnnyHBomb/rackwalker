// The building on the scene-change card: the overworld's own building sprite in its palette, drawn at twice its size
// in whole pixels (no smoothing), so a door transition shows the same pixel art as the green.
import { PALETTE, SPRITES, sprite } from './engine';
import { buildingSprite } from './gen';

export function buildingCard(theme: string | null, scale = 2): HTMLCanvasElement {
  const name = theme && SPRITES.has(buildingSprite(theme)) ? buildingSprite(theme) : buildingSprite('debug');
  const s = sprite(name);
  const c = document.createElement('canvas');
  c.width = s.w;
  c.height = s.h;
  c.style.cssText = `width:${s.w * scale}px;height:${s.h * scale}px;image-rendering:pixelated;image-rendering:crisp-edges;flex:none`;
  const g = c.getContext('2d');
  if (!g) return c;
  const img = g.createImageData(s.w, s.h);
  const out = new Uint32Array(img.data.buffer);
  const f = s.frames[0]!;
  for (let i = 0; i < f.length; i++) out[i] = f[i] ? PALETTE.rgba[f[i]!]! : 0; // index 0 stays transparent
  g.putImageData(img, 0, 0);
  return c;
}

/** Any sprite as a small canvas at a whole-number scale (reward icons, the boss marker in the quest log). */
export function spriteCanvas(name: string, scale = 2, frame = 0): HTMLCanvasElement {
  const c = document.createElement('canvas');
  const s = SPRITES.get(name);
  if (!s) return c;
  c.width = s.w;
  c.height = s.h;
  c.className = `sprite sprite-${name}`;
  c.setAttribute('aria-hidden', 'true');
  c.style.cssText = `width:${s.w * scale}px;height:${s.h * scale}px;image-rendering:pixelated;image-rendering:crisp-edges;flex:none`;
  const g = c.getContext('2d');
  if (!g) return c;
  const img = g.createImageData(s.w, s.h);
  const out = new Uint32Array(img.data.buffer);
  const f = s.frames[frame % s.frames.length]!;
  for (let i = 0; i < f.length; i++) out[i] = f[i] ? PALETTE.rgba[f[i]!]! : 0;
  g.putImageData(img, 0, 0);
  return c;
}
