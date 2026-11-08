// Slot sheet (sheet.html?theme=<id>): every slot of one theme in every sample variant (rotations, seeds, statuses,
// NetBox colours) on one canvas at 4x, nearest-neighbour, over a neutral checkerboard so transparency shows. Artists
// open it while drawing; `make theme-shots THEME=<id>` saves it as docs/screenshots/themes/<id>/sheet.png.
import { SLOT_IDS, SLOT_SPECS, renderSlot, type SlotVariant } from '../contracts/theme';
import { DEFAULT_THEME, THEMES, sampleVariants, urlTheme } from '../themes/index';

declare global {
  interface Window {
    __sheet?: { theme: string; width: number; height: number; png(): string };
  }
}

const SCALE = 4;
const GAP = 16;
const LABEL_W = 176;
const CAPTION_H = 18;
const MIN_CELL = 72; // room for the variant caption above narrow sprites
// Sheet chrome only (not theme art): two neutral greys for the transparency checkerboard.
const CHECK_A = '#2b2d30';
const CHECK_B = '#35373b';

function variantLabel(v: SlotVariant): string {
  if (v.role_color) return 'role';
  if (v.wire_color) return 'wire';
  if (v.rotation !== undefined) return `rot ${v.rotation}`;
  if (v.seed !== undefined) return `seed ${v.seed}`;
  return v.status ?? 'plain';
}

function main(): void {
  const theme = THEMES[urlTheme(location.search) ?? DEFAULT_THEME];
  document.title = `${theme.name}: slot sheet`;
  const rows = SLOT_IDS.map((slot) => ({ slot, spec: SLOT_SPECS[slot], variants: sampleVariants(slot) }));
  const pitch = (w: number) => Math.max(w * SCALE, MIN_CELL) + GAP;
  const width = LABEL_W + Math.max(...rows.map((r) => r.variants.length * pitch(r.spec.width))) + GAP;
  const height = 40 + rows.reduce((h, r) => h + CAPTION_H + r.spec.height * SCALE + GAP, 0);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.id = 'sheet';
  const g = canvas.getContext('2d')!;
  g.imageSmoothingEnabled = false;
  g.fillStyle = theme.palette.background;
  g.fillRect(0, 0, width, height);
  g.font = '12px monospace';
  g.textBaseline = 'top';
  g.fillStyle = theme.palette.text;
  g.font = 'bold 16px monospace';
  g.fillText(`${theme.id}: ${theme.name} (21 slots x sample variants, ${SCALE}x)`, GAP, 12);
  g.font = '12px monospace';
  const one = document.createElement('canvas');
  let y = 40;
  for (const r of rows) {
    g.fillStyle = theme.palette.text;
    g.fillText(r.slot, GAP, y + CAPTION_H);
    g.fillText(`${r.spec.width}x${r.spec.height} @${r.spec.anchor.join(',')}`, GAP, y + CAPTION_H + 16);
    let x = LABEL_W;
    for (const v of r.variants) {
      const w = r.spec.width * SCALE, h = r.spec.height * SCALE;
      g.fillStyle = theme.palette.text;
      g.fillText(variantLabel(v), x, y + 2);
      for (let cy = 0; cy < h; cy += 2 * SCALE)
        for (let cx = 0; cx < w; cx += 2 * SCALE) {
          g.fillStyle = ((cx + cy) / (2 * SCALE)) % 2 ? CHECK_B : CHECK_A;
          g.fillRect(x + cx, y + CAPTION_H + cy, Math.min(2 * SCALE, w - cx), Math.min(2 * SCALE, h - cy));
        }
      const buf = renderSlot(theme, r.slot, v);
      one.width = buf.width;
      one.height = buf.height;
      one.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(buf.data), buf.width, buf.height), 0, 0);
      g.drawImage(one, x, y + CAPTION_H, w, h);
      x += pitch(r.spec.width);
    }
    y += CAPTION_H + r.spec.height * SCALE + GAP;
  }
  document.getElementById('status')?.remove();
  document.body.appendChild(canvas);
  window.__sheet = { theme: theme.id, width, height, png: () => canvas.toDataURL('image/png') };
}

try {
  main();
} catch (e) {
  const s = document.getElementById('status');
  if (s) s.textContent = `error: ${String(e)}`;
  console.error(e);
}
