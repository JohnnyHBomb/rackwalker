import { expect, test } from '@playwright/test';
import { open, state } from './helpers';

// 1,000 devices at room level must render at >= 50 fps in headless chromium while the camera moves every frame.
// PERF_MIN_FPS overrides the gate; CI sets 0 (report-only: shared GitHub-hosted runners render with software GL).
const MIN_FPS = Number(process.env.PERF_MIN_FPS ?? 50);

test('perf: 1,000 devices at room level >= PERF_MIN_FPS (default 50) fps', async ({ page }, info) => {
  await open(page, 'anim=0&perf=1000&view=room:1:1');
  expect((await state(page)).level).toBe('room');
  const sprites = await page.evaluate(() => window.__iso!.view.scene!.items.length);
  const r = await page.evaluate(() => window.__iso!.fps(3000));
  const line = `perf 1000 devices: ${r.fps.toFixed(1)} fps (${r.frames} frames in ${r.ms.toFixed(0)} ms, worst frame ${r.worstMs.toFixed(1)} ms, ${sprites} sprites)`;
  console.log(line);
  info.annotations.push({ type: 'perf', description: line });
  expect(r.fps).toBeGreaterThanOrEqual(MIN_FPS);
});

test('perf (informational): 10,000 devices at room level', async ({ page }, info) => {
  await open(page, 'anim=0&perf=10000&view=room:1:1');
  const sprites = await page.evaluate(() => window.__iso!.view.scene!.items.length);
  const r = await page.evaluate(() => window.__iso!.fps(3000));
  const line = `perf 10000 devices: ${r.fps.toFixed(1)} fps (${r.frames} frames in ${r.ms.toFixed(0)} ms, worst frame ${r.worstMs.toFixed(1)} ms, ${sprites} sprites)`;
  console.log(line);
  info.annotations.push({ type: 'perf', description: line });
});
