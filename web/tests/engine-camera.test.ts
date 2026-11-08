import { describe, expect, it } from 'vitest';
import { RENDER_SCALES } from '../src/contracts/iso';
import { clampTo, containerOffset, fitScale, focusInside, levelToScreen, nearestScale, panBy, pinchStep, screenToLevel, stepScale, zoomAt, type CameraState } from '../src/engine/camera';
import { planTransition, transitionFrame } from '../src/engine/transition';

const view = { width: 800, height: 600 };

describe('camera', () => {
  it('focusInside: a zoom out keeps the focus at the centre as far as the level then stays on screen', () => {
    // A 42U rack (80 x 336 level px) at scale 1 in 800 x 600 (16 px margins): the centre may range over y 52..284.
    const rack = { x: 0, y: 0, w: 80, h: 336 };
    expect(focusInside({ x: 40, y: 30 }, rack, 1, view)).toEqual({ x: 40, y: 52 });
    expect(focusInside({ x: 40, y: 168 }, rack, 1, view)).toEqual({ x: 40, y: 168 });
    expect(focusInside({ x: 40, y: 330 }, rack, 1, view)).toEqual({ x: 40, y: 284 });
    // Too tall at scale 2 (672 px): the view stays inside the box instead.
    expect(focusInside({ x: 40, y: 10 }, rack, 2, view).y).toBe((600 - 32) / 4);
    expect(focusInside({ x: 40, y: 170 }, rack, 2, view).y).toBe(170);
    expect(focusInside({ x: 40, y: 330 }, rack, 2, view).y).toBe(336 - (600 - 32) / 4);
  });

  it('container offsets are integers for any pan and every scale (pixels stay crisp)', () => {
    for (const scale of RENDER_SCALES)
      for (const cx of [0, 0.1, 13.37, -99.5]) {
        const o = containerOffset({ cx, cy: cx / 3, scale }, { width: 801, height: 599 });
        expect(Number.isInteger(o.x) && Number.isInteger(o.y)).toBe(true);
      }
  });
  it('screenToLevel and levelToScreen are inverse', () => {
    const cam: CameraState = { cx: 10.25, cy: -3, scale: 3 };
    const l = screenToLevel(cam, view, 123, 456);
    expect(levelToScreen(cam, view, l.x, l.y)).toEqual({ x: 123, y: 456 });
  });
  it('steps through RENDER_SCALES and clamps at the ends', () => {
    expect(stepScale(1, 1)).toBe(2);
    expect(stepScale(6, 1)).toBe(8);
    expect(stepScale(8, 1)).toBe(8);
    expect(stepScale(1, -1)).toBe(1);
    expect(nearestScale(6.9)).toBe(6);
    expect(nearestScale(7.1)).toBe(8);
  });
  it('wheel zoom keeps the level point under the pointer fixed (to within the integer offset rounding)', () => {
    let cam: CameraState = { cx: 40, cy: 20, scale: 2 };
    const mouse = { x: 611, y: 97 };
    for (const dir of [1, 1, 1, -1, 1, -1, -1] as const) {
      const before = screenToLevel(cam, view, mouse.x, mouse.y);
      cam = zoomAt(cam, view, mouse.x, mouse.y, dir);
      const after = screenToLevel(cam, view, mouse.x, mouse.y);
      expect(Math.abs(after.x - before.x)).toBeLessThanOrEqual(0.5 / cam.scale + 1e-9);
      expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(0.5 / cam.scale + 1e-9);
      expect(RENDER_SCALES).toContain(cam.scale);
    }
    expect(zoomAt({ cx: 0, cy: 0, scale: 8 }, view, 1, 1, 1)).toEqual({ cx: 0, cy: 0, scale: 8 });
  });
  it('drag pans by screen pixels / scale', () => {
    expect(panBy({ cx: 10, cy: 10, scale: 4 }, 8, -4)).toEqual({ cx: 8, cy: 11, scale: 4 });
  });
  it('fitScale picks the largest integer scale that fits, at least 1', () => {
    expect(fitScale({ w: 64, h: 40 }, view)).toBe(8);
    expect(fitScale({ w: 256, h: 160 }, view)).toBe(3); // 256 * 3 + 32 = 800 fits exactly
    expect(fitScale({ w: 300, h: 160 }, view)).toBe(2);
    expect(fitScale({ w: 5000, h: 10 }, view)).toBe(1);
  });
  it('clampTo keeps the centre inside the level bounds', () => {
    expect(clampTo({ cx: -50, cy: 500, scale: 1 }, { x: 0, y: 0, w: 100, h: 100 })).toEqual({ cx: 0, cy: 100, scale: 1 });
  });
  it('pinch: one step per 1.25x change of finger distance', () => {
    expect(pinchStep(100, 124)).toBe(0);
    expect(pinchStep(100, 126)).toBe(1);
    expect(pinchStep(100, 79)).toBe(-1);
  });
});

describe('level transitions', () => {
  const oldCam: CameraState = { cx: 0, cy: 0, scale: 1 };
  const newCam: CameraState = { cx: 100, cy: 50, scale: 3 };
  it('zoom in: old level flies to the object and scales up, new level settles to its camera; integer scales throughout', () => {
    const plan = planTransition('in', oldCam, { x: 40, y: 20 }, newCam);
    expect(plan.oldTo).toEqual({ cx: 40, cy: 20, scale: 3 });
    expect(plan.newFrom.scale).toBe(2);
    let lastOld = 0;
    for (let i = 0; i <= 100; i++) {
      const f = transitionFrame(plan, i / 100);
      expect(RENDER_SCALES).toContain(f.oldCam.scale);
      expect(RENDER_SCALES).toContain(f.newCam.scale);
      expect(f.oldAlpha + f.newAlpha).toBeCloseTo(1);
      expect(f.oldCam.scale).toBeGreaterThanOrEqual(lastOld);
      lastOld = f.oldCam.scale;
    }
    const start = transitionFrame(plan, 0);
    const end = transitionFrame(plan, 1);
    expect([start.oldCam, start.oldAlpha, start.newAlpha]).toEqual([oldCam, 1, 0]);
    expect([end.newCam, end.newAlpha, end.oldAlpha]).toEqual([newCam, 1, 0]);
  });
  it('zoom out: old level shrinks, new level arrives from one step larger', () => {
    const plan = planTransition('out', { cx: 0, cy: 0, scale: 4 }, { x: 0, y: 0 }, newCam);
    expect(plan.oldTo.scale).toBe(2);
    expect(plan.newFrom.scale).toBe(4);
    expect(transitionFrame(plan, 1).newCam).toEqual(newCam);
  });
});
