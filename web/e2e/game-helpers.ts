// Helpers for the game e2e (fixture proxy: e2e/game.spec.ts; live NetBox: e2e-live/game-live.spec.ts).
import { expect, type Page } from '@playwright/test';

export type GameView = {
  scene: string;
  pos: { x: number; y: number };
  facing: string;
  view: string | null;
  camera: { cx: number; cy: number; scale: number } | null;
  dialogue: { script: string; node: string | null; text: string; typing: boolean; choices: string[]; selected: number } | null;
  menu: string | null;
  flags: Record<string, unknown>;
  hint: string;
  keysUp: string[];
  error: string | null;
};

export const game = (page: Page) =>
  page.evaluate((): GameView => {
    const g = window.__game!.shell;
    const ow = g.overworld;
    const t = window.__iso!.view.crumbs.at(-1)?.target;
    const cam = window.__iso!.view.camera;
    return {
      scene: g.scene,
      pos: ow ? { x: ow.pos.x, y: ow.pos.y } : { x: -1, y: -1 },
      facing: ow?.facing ?? '',
      view: g.scene === 'iso' && t ? JSON.stringify(t) : null,
      camera: g.scene === 'iso' && cam ? { cx: cam.cx, cy: cam.cy, scale: cam.scale } : null,
      dialogue: g.dialogue.state,
      menu: g.menuPage,
      flags: { ...g.flags },
      hint: g.settings.hint_level,
      keysUp: [...g.settings.keys.up],
      error: g.error,
    };
  });
export const idle = (page: Page) => page.evaluate(() => window.__game!.shell.idle());
export const stored = (page: Page) => page.evaluate(() => fetch('/api/game/state').then((r) => r.json()));

export async function boot(page: Page): Promise<void> {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await page.waitForFunction(() => !!window.__game);
  expect(errors).toEqual([]);
}
export async function ready(page: Page): Promise<void> {
  await boot(page);
  await page.evaluate(() => window.__game!.ready);
}
/** Waits until the typewriter has finished the line on screen. */
export const typed = (page: Page) => expect.poll(async () => (await game(page)).dialogue?.typing).toBe(false);

