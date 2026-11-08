// Renders the art lab's screenshots with Playwright (headless chromium) and ffmpeg.
//   node art-lab/sixteen/render.mjs [--only scene,sheet] [--out DIR]     (run from web/)
// Writes PNGs at native size and integer scales, and animated GIFs (ffmpeg, nearest-neighbour, no dither).
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const webDir = resolve(here, '..', '..');
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const only = opt('--only', '') ? new Set(opt('--only', '').split(',')) : null;
const out = resolve(opt('--out', resolve(webDir, '..', 'docs', 'screenshots', 'art', 'sixteen')));
mkdirSync(out, { recursive: true });

const server = await createServer({ root: webDir, logLevel: 'error', server: { port: 5300 + Math.floor(Math.random() * 400), strictPort: false, host: '127.0.0.1' } });
await server.listen();
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.error('page error:', e.message));
  page.on('console', (m) => { if (m.type() === 'error') console.error('console:', m.text()); });
  await page.goto(new URL('art-lab/sixteen/?t=0', server.resolvedUrls.local[0]).href);
  await page.waitForFunction(() => window.lab && window.lab.ready, null, { timeout: 20000 });
  const grab = (view, t, scale) => page.evaluate(([v, tt, s]) => window.lab.capture(v, tt, s), [view, t, scale]);
  const save = async (file, view, t, scale) => {
    const url = await grab(view, t, scale);
    writeFileSync(join(out, file), Buffer.from(url.split(',')[1], 'base64'));
    console.log('wrote', file);
  };
  const want = (k) => !only || only.has(k);
  const gif = async (file, view, ms, step, scale) => {
    const dir = mkdtempSync(join(tmpdir(), 'artlab-'));
    let n = 0;
    for (let t = 0; t < ms; t += step) {
      const url = await grab(view, t, 1);
      writeFileSync(join(dir, `f${String(n++).padStart(4, '0')}.png`), Buffer.from(url.split(',')[1], 'base64'));
    }
    const fps = String(1000 / step);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', fps, '-i', join(dir, 'f%04d.png'), '-vf',
      `scale=iw*${scale}:ih*${scale}:flags=neighbor,split[a][b];[a]palettegen=max_colors=128:stats_mode=full[p];[b][p]paletteuse=dither=none`,
      '-loop', '0', join(out, file)]);
    rmSync(dir, { recursive: true, force: true });
    console.log('wrote', file, n, 'frames');
  };
  const T0 = Number(opt('--t', '440'));
  if (want('scene')) { await save('scene-1x.png', 'scene', T0, 1); await save('scene-3x.png', 'scene', T0, 3); }
  if (want('portrait')) await save('portrait.png', 'portrait', 0, 4);
  if (want('owls')) await save('owls.png', 'owls', 0, 4);
  if (want('walksheet')) await save('walk-sheet.png', 'walksheet', 0, 4);
  if (want('sheet')) await save('sheet.png', 'sheet', 0, 2);
  if (want('walk')) await gif('walk.gif', 'walk', 1200, 50, 4);
  if (want('scenegif')) await gif('scene.gif', 'scene', 2200, 50, 2);
} finally {
  await browser.close();
  await server.close();
}
