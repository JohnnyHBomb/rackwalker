// The overworld: screenshots of every theme's region and every country set from the overworld sheet (world.html,
// fixture sites), the door transition and a walk in the real game, the walk cycle as a GIF, and frame times at 3x
// while walking (no layout shift). Writes docs/screenshots/overworld/.
import { expect, test, type Page } from '@playwright/test';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { game } from './game-helpers';

const here = dirname(fileURLToPath(import.meta.url));
const SHOTS = resolve(here, '../../docs/screenshots/overworld');
mkdirSync(SHOTS, { recursive: true });
const players = JSON.parse(readFileSync(resolve(here, '../../fixtures/players.json'), 'utf8')).players as { token: string }[];
const THEMES = ['debug', 'industrial-robotic', 'farm', 'business', 'pagodas-nature'];

async function sheet(page: Page): Promise<void> {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/world.html?still=1&scale=1');
  await page.waitForFunction(() => window.__world?.ready);
  expect(errors).toEqual([]);
}
const save = (name: string, dataUrl: string) => writeFileSync(resolve(SHOTS, name), Buffer.from(dataUrl.split(',')[1]!, 'base64'));

test('every theme: the region around its building at 1x and 3x, every autotile present', async ({ page }) => {
  await sheet(page);
  for (const theme of THEMES) {
    const info = (await page.evaluate((t) => window.__world!.info({ theme: t }), theme)) as { missing: string[]; doors: { x: number; y: number; theme: string }[] };
    expect(info.missing).toEqual([]);
    expect(info.doors.every((d) => d.theme === theme)).toBe(true);
    for (const s of [1, 3]) save(`region-${theme}-${s}x.png`, await page.evaluate(([t, s]) => window.__world!.capture({ theme: t as string, scale: s as number, t: 300 }), [theme, s]));
  }
});

test('every country set: its landmarks and villagers, at 3x', async ({ page }) => {
  await sheet(page);
  type P = { x: number; y: number };
  for (const country of ['NL', 'GB', 'ZZ']) {
    const info = (await page.evaluate((c) => window.__world!.info({ country: c }), country)) as { regions: { set: string }[]; landmark: P; stand: P; villager: P };
    expect(info.regions[0]!.set).toBe(country === 'ZZ' ? '' : country);
    const name = country === 'ZZ' ? 'neutral' : country;
    // the view on the region's first landmark, the player standing in front of it (never on it)
    const player = { x: info.stand.x * 16 + 8, y: info.stand.y * 16 + 14, dir: 'up', frame: 0 };
    save(`country-${name}-3x.png`, await page.evaluate(([c, at, p]) => window.__world!.capture({ country: c as string, scale: 3, t: 500, at: at as P, player: p as never }), [country, info.landmark, player] as const));
    // and on a villager
    save(`country-${name}-villager-3x.png`, await page.evaluate(([c, at]) => window.__world!.capture({ country: c as string, scale: 3, t: 500, at: at as P, player: { x: -99, y: -99, dir: 'down', frame: 0 } }), [country, info.villager] as const));
  }
});

test('the tile engine draws only palette colours, in every theme, country set and region', async ({ page }) => {
  await sheet(page);
  const pal = JSON.parse(readFileSync(resolve(here, '../src/game/world/assets/palette.json'), 'utf8')).colours as string[];
  const views: { theme?: string; country?: string; region: number }[] = [];
  for (const theme of THEMES) views.push({ theme, region: 0 });
  for (const country of ['NL', 'GB', 'ZZ']) {
    const n = ((await page.evaluate((c) => window.__world!.info({ country: c }), country)) as { regions: unknown[] }).regions.length;
    for (let region = 0; region < n; region++) views.push({ country, region });
  }
  const off = await page.evaluate(async ([views, pal]) => {
    const ok = new Set(pal.map((h) => parseInt(h.slice(1), 16)));
    const bad: string[] = [];
    for (const v of views) {
      const img = new Image();
      img.src = window.__world!.capture({ ...v, scale: 1, t: 700 });
      await img.decode();
      const c = document.createElement('canvas');
      c.width = img.width;
      c.height = img.height;
      const g = c.getContext('2d')!;
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, c.width, c.height).data;
      for (let i = 0; i < d.length; i += 4) {
        const rgb = (d[i]! << 16) | (d[i + 1]! << 8) | d[i + 2]!;
        if (!ok.has(rgb)) {
          bad.push(`${JSON.stringify(v)} #${rgb.toString(16).padStart(6, '0')} at ${(i / 4) % c.width},${Math.floor(i / 4 / c.width)}`);
          break;
        }
      }
    }
    return bad;
  }, [views, pal] as const);
  expect(views.length).toBeGreaterThan(8);
  expect(off).toEqual([]);
});

test('the walk cycle in four directions, as a GIF', async ({ page }) => {
  await sheet(page);
  const dir = resolve(SHOTS, '.walk');
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir);
  const info = (await page.evaluate(() => window.__world!.info({}))) as { start: { x: number; y: number } };
  // a square walk around the start, two frames per step, eight steps per side
  const frames: { x: number; y: number; dir: string; frame: number }[] = [];
  let x = info.start.x * 16 + 8, y = info.start.y * 16 + 14;
  for (const [d, dx, dy] of [['right', 1, 0], ['down', 0, 1], ['left', -1, 0], ['up', 0, -1]] as const)
    for (let i = 0; i < 32; i++) {
      x += dx * 1;
      y += dy * 1;
      frames.push({ x, y, dir: d, frame: Math.floor(i / 4) % 4 });
    }
  for (let i = 0; i < frames.length; i++) {
    const url = await page.evaluate(([p, at, t]) => window.__world!.capture({ scale: 2, player: p as never, at: at as never, t: t as number }), [frames[i]!, info.start, i * 33]);
    save(`.walk/f${String(i).padStart(3, '0')}.png`, url);
  }
  // The GIF is a docs artefact: without ffmpeg (as on CI runners) the frames are still captured and checked, and no GIF is written.
  const hasFfmpeg = !spawnSync('ffmpeg', ['-version']).error;
  if (hasFfmpeg) execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', '30', '-i', resolve(dir, 'f%03d.png'), '-vf', 'split[a][b];[a]palettegen=max_colors=64:reserve_transparent=0[p];[b][p]paletteuse=dither=none', resolve(SHOTS, 'walk.gif')]);
  rmSync(dir, { recursive: true, force: true });
});

test.describe('in the game', () => {
  test.use({ viewport: { width: 768, height: 672 } });
  test('walk at 3x: frame times, no layout shift; the door transition', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => !!window.__game);
    await page.fill('#signin-token', players[19]!.token);
    await page.click('#signin-submit');
    await page.evaluate(() => window.__game!.ready);
    await page.evaluate(() => window.__game!.shell.dialogue.close());
    expect(await page.evaluate(() => window.__game!.shell.overworld!.scale)).toBe(3);
    await page.evaluate(() => {
      (window as unknown as { __shift: number }).__shift = 0;
      new PerformanceObserver((l) => {
        for (const e of l.getEntries()) (window as unknown as { __shift: number }).__shift += (e as unknown as { value: number }).value;
      }).observe({ type: 'layout-shift', buffered: true });
    });
    await page.screenshot({ path: resolve(SHOTS, 'game-overworld-3x.png') });
    // a mid laptop: chromium's CPU throttled 4x (the dev machine is a fast desktop)
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    const trace = resolve(here, '../../data/g1-walk-trace.json');
    mkdirSync(resolve(trace, '..'), { recursive: true });
    await page.context().browser()!.startTracing(page, { path: trace, categories: ['devtools.timeline'] });
    await page.evaluate(() => {
      const o = window.__game!.shell.overworld!;
      o.frameTimes.length = 0;
      o.drawTimes.length = 0;
    });
    // walk to the east and back for about five seconds
    for (let i = 0; i < 4; i++) {
      await page.keyboard.down(i % 2 ? 'ArrowLeft' : 'ArrowRight');
      await page.waitForTimeout(1200);
      await page.keyboard.up(i % 2 ? 'ArrowLeft' : 'ArrowRight');
    }
    await page.context().browser()!.stopTracing();
    const m = await page.evaluate(() => {
      const o = window.__game!.shell.overworld!;
      const q = (a: number[], p: number) => [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * p))]!;
      return { frames: o.frameTimes.length, frame_p50: q(o.frameTimes, 0.5), frame_p95: q(o.frameTimes, 0.95), frame_max: Math.max(...o.frameTimes),
        draw_p50: q(o.drawTimes, 0.5), draw_p95: q(o.drawTimes, 0.95), draw_max: Math.max(...o.drawTimes), layout_shift: (window as unknown as { __shift: number }).__shift };
    });
    // frames whose rAF interval exceeded 1.5 vsyncs, from the trace's own frame events as a cross-check
    const ev = JSON.parse(readFileSync(trace, 'utf8')).traceEvents as { name: string; ts: number }[];
    const ticks = ev.filter((e) => e.name === 'FireAnimationFrame').map((e) => e.ts / 1000).sort((a, b) => a - b);
    const gaps = ticks.slice(1).map((t, i) => t - ticks[i]!);
    const traced = { trace_frames: ticks.length, trace_gap_p95: [...gaps].sort((a, b) => a - b)[Math.floor(gaps.length * 0.95)] ?? null, trace_long: gaps.filter((g) => g > 25).length };
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    const r = (v: number | null) => (v === null ? null : Math.round(v * 100) / 100);
    const out = Object.fromEntries(Object.entries({ ...m, ...traced }).map(([k, v]) => [k, typeof v === 'number' ? r(v) : v]));
    writeFileSync(resolve(SHOTS, 'perf.json'), JSON.stringify({ viewport: '768x672 (3x)', cpu_throttle: 4, ...out }, null, 1) + '\n');
    rmSync(trace, { force: true });
    expect(m.layout_shift).toBe(0);
    expect(m.frames).toBeGreaterThan(200);
    expect(m.frame_p95).toBeLessThan(20); // 60 fps: 16.7 ms a frame
    expect(m.draw_p95).toBeLessThan(8);

    // the door transition into the first building (beside the start)
    await page.evaluate(() => (window.__game!.shell.transitionMs = 1500));
    await page.evaluate(() => {
      const g = window.__game!.shell;
      const d = g.map!.doorOf(1)!;
      g.overworld!.walkTo({ x: d.x, y: d.y });
    });
    await expect(page.locator('#scene-card')).toContainText('Entering', { timeout: 15_000 });
    await page.screenshot({ path: resolve(SHOTS, 'door-transition.png') });
    await expect.poll(async () => (await game(page)).scene, { timeout: 15_000 }).toBe('iso');
    // inside: the card has gone and the isometric room is on screen
    await expect(page.locator('#scene-card')).toHaveCount(0, { timeout: 15_000 });
    await page.evaluate(() => window.__game!.shell.dialogue.close());
    await page.evaluate(() => window.__iso!.view.settled());
    await page.screenshot({ path: resolve(SHOTS, 'door-inside.png') });
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await expect.poll(async () => (await game(page)).scene, { timeout: 15_000 }).toBe('overworld');
    const g = await game(page);
    const d = await page.evaluate(() => window.__game!.shell.map!.doorOf(1)!);
    expect(g.pos).toEqual({ x: d.x, y: d.y + 1 });
    await expect(page.locator('#scene-card')).toHaveCount(0, { timeout: 15_000 });
    await page.screenshot({ path: resolve(SHOTS, 'door-back-out.png') });
    const shots = ['door-transition.png', 'door-inside.png', 'door-back-out.png'].map((n) => readFileSync(resolve(SHOTS, n)).toString('base64'));
    expect(new Set(shots).size).toBe(3);
  });
});
