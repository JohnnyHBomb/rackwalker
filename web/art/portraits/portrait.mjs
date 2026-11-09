// Portrait pipeline: hand-painted SVG source (768 x 768) -> rasterised by headless chromium -> area-filter downscale
// to 64 x 64 and 96 x 96 (exact box averages, 12x and 8x) -> mapped to the overworld palette (no dither, and ordered
// dither for comparison) -> the hand clean-up pass (pixel edits stored in cleanup/<who>-<size>.txt) -> indexed rows
// in web/src/game/world/assets/portraits.json, the same format as the overworld sprites.
// Also writes the side-by-side sheet docs/screenshots/g1/g1a/portrait-pipeline.png (source, raw downscale, ordered
// dither, cleaned).
//
//   cd web && node art/portraits/portrait.mjs        (needs the Playwright chromium of `make e2e`)
// Deterministic: same SVG, same chromium, same bytes.
import { chromium } from '@playwright/test';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(here, '..', '..');
const pal = JSON.parse(readFileSync(resolve(web, 'src/game/world/assets/palette.json'), 'utf8'));
const RGB = pal.colours.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
const IDX = new Map(pal.names.map((n, i) => [n, i]));
const SRC = 768;
const SIZES = [64, 96];
const WHO = ['ilatea', 'byte', 'nocti'];
// Each portrait maps to a sub-palette of the ramps it is painted with (as a 16-bit sprite uses its own palette rows),
// so a skin tone never lands on path earth or brick.
const RAMPS_OF = {
  ilatea: ['k', 's', 'sk', 'pu', 'l', 'c', 'ye', 'b'],
  byte: ['k', 's', 'b', 'ye', 'l', 'wd', 'c'],
  nocti: ['k', 's', 'wd', 'ye', 'b', 'l', 'tu'],
};
const BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];

/** Weighted RGB distance ("redmean"): close to perceived difference, cheap and exact. */
function dist(a, b) {
  const r = (a[0] + b[0]) / 2;
  const d0 = a[0] - b[0], d1 = a[1] - b[1], d2 = a[2] - b[2];
  return (2 + r / 256) * d0 * d0 + 4 * d1 * d1 + (2 + (255 - r) / 256) * d2 * d2;
}
let allowed = [];
function nearest2(c) {
  let best = [Infinity, 0], second = [Infinity, 0];
  for (const i of allowed) {
    const d = dist(c, RGB[i]);
    if (d < best[0]) (second = best), (best = [d, i]);
    else if (d < second[0]) second = [d, i];
  }
  return [best, second];
}
function downscale(px, n) {
  const k = SRC / n, out = [];
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      const s = [0, 0, 0];
      for (let j = 0; j < k; j++)
        for (let i = 0; i < k; i++) {
          const o = ((y * k + j) * SRC + x * k + i) * 4;
          s[0] += px[o], s[1] += px[o + 1], s[2] += px[o + 2];
        }
      out.push(s.map((v) => Math.round(v / (k * k))));
    }
  return out;
}
function quantise(rgb, n, dither) {
  return rgb.map((c, i) => {
    const [[d1, a], [d2, b]] = nearest2(c);
    if (!dither) return a;
    // ordered: pick the second colour when the pixel lies far enough towards it
    const t = Math.sqrt(d1) / (Math.sqrt(d1) + Math.sqrt(d2) + 1e-9);
    return t * 2 > (BAYER[Math.floor(i / n) & 3][i % n & 3] + 0.5) / 16 * 1.0 + 0.5 ? b : a;
  });
}
/**
 * The hand clean-up pass: cleanup/<who>-<n>.txt, applied in order. Format (one edit per line):
 *   ## why            starts a step and says what it fixes
 *   x,y colour        sets one pixel to a palette colour name
 *   x0,y0 x1,y1 colour  sets every pixel of the rectangle (corners included)
 *   # ...             a comment
 */
function cleanup(idx, n, who) {
  const f = resolve(here, 'cleanup', `${who}-${n}.txt`);
  if (!existsSync(f)) return { idx, edits: 0, steps: 0 };
  const out = [...idx];
  let edits = 0, steps = 0;
  readFileSync(f, 'utf8').split('\n').forEach((line, i) => {
    const l = line.trim();
    if (l.startsWith('## ')) return void steps++;
    if (!l || l.startsWith('#')) return;
    const m = /^(\d+),(\d+)(?: (\d+),(\d+))? ([a-z]+\d?)$/.exec(l);
    const c = m && IDX.get(m[5]);
    if (c === undefined || c === null) throw new Error(`${f}:${i + 1}: cannot read "${l}"`);
    const [x0, y0] = [+m[1], +m[2]], [x1, y1] = m[3] ? [+m[3], +m[4]] : [x0, y0];
    if (x1 < x0 || y1 < y0 || x1 >= n || y1 >= n) throw new Error(`${f}:${i + 1}: outside the ${n} x ${n} portrait`);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) (out[y * n + x] = c), edits++;
  });
  return { idx: out, edits, steps };
}
const rows = (idx, n) => Array.from({ length: n }, (_, y) => idx.slice(y * n, y * n + n).map((v) => pal.alphabet[v]).join(''));

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setContent('<canvas id=c></canvas>');
const result = {}, sheet = [];
for (const who of WHO) {
  const svg = readFileSync(resolve(here, `${who}.svg`), 'utf8');
  const px = await page.evaluate(async ([svg, S]) => {
    const img = new Image();
    img.src = 'data:image/svg+xml;base64,' + btoa(svg);
    await img.decode();
    const c = document.getElementById('c');
    c.width = c.height = S;
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0, S, S);
    return Array.from(g.getImageData(0, 0, S, S).data);
  }, [svg, SRC]);
  result[who] = {};
  allowed = RAMPS_OF[who].flatMap((r) => pal.ramps[r]).concat(['w']).map((n) => IDX.get(n));
  for (const n of SIZES) {
    const rgb = downscale(px, n);
    const flat = quantise(rgb, n, false);
    const dith = quantise(rgb, n, true);
    const clean = cleanup(flat, n, who);
    result[who][n] = { w: n, h: n, ox: 0, oy: 0, frames: [rows(clean.idx, n)] };
    sheet.push({ who, n, svg, rgb, flat, dith, clean: clean.idx, edits: clean.edits });
    console.log(who, n, 'clean-up edits', clean.edits, 'colours', new Set(clean.idx).size);
  }
}
writeFileSync(resolve(web, 'src/game/world/assets/portraits.json'), JSON.stringify(result) + '\n');

// The sheet: per row, the source at 192 px, then raw downscale (true colour), palette no dither, ordered dither,
// cleaned, each at 192 px (nearest neighbour).
const shot = resolve(web, '..', 'docs/screenshots/g1/g1a');
mkdirSync(shot, { recursive: true });
const png = await page.evaluate(([sheet, RGB]) => {
  const cell = 192, pad = 8, cols = 5, label = 14;
  const c = document.createElement('canvas');
  c.width = cols * (cell + pad) + pad;
  c.height = sheet.length * (cell + pad + label) + pad + label;
  const g = c.getContext('2d');
  g.fillStyle = '#140c1c';
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = '#e2dfd2';
  g.font = '12px monospace';
  ['source (vector)', 'area downscale', 'palette, no dither', 'palette, ordered dither', 'after clean-up'].forEach((t, i) => g.fillText(t, pad + i * (cell + pad), 12));
  return new Promise(async (done) => {
    for (let r = 0; r < sheet.length; r++) {
      const s = sheet[r], y = label + pad + r * (cell + pad + label);
      g.fillStyle = '#e2dfd2';
      g.fillText(`${s.who} ${s.n} x ${s.n}${s.edits ? `, ${s.edits} hand edits` : ''}`, pad, y + cell + 12);
      const img = new Image();
      img.src = 'data:image/svg+xml;base64,' + btoa(s.svg);
      await img.decode();
      g.imageSmoothingEnabled = true;
      g.drawImage(img, pad, y, cell, cell);
      const put = (col, colourOf) => {
        const t = document.createElement('canvas');
        t.width = t.height = s.n;
        const tg = t.getContext('2d');
        const id = tg.createImageData(s.n, s.n);
        for (let i = 0; i < s.n * s.n; i++) {
          const [rr, gg, bb] = colourOf(i);
          id.data.set([rr, gg, bb, 255], i * 4);
        }
        tg.putImageData(id, 0, 0);
        g.imageSmoothingEnabled = false;
        g.drawImage(t, pad + col * (cell + pad), y, cell, cell);
      };
      put(1, (i) => s.rgb[i]);
      put(2, (i) => RGB[s.flat[i]]);
      put(3, (i) => RGB[s.dith[i]]);
      put(4, (i) => RGB[s.clean[i]]);
    }
    done(c.toDataURL('image/png'));
  });
}, [sheet, RGB]);
writeFileSync(resolve(shot, 'portrait-pipeline.png'), Buffer.from(png.split(',')[1], 'base64'));
await browser.close();
