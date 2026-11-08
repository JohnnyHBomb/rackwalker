// Camera: integer render scale (RENDER_SCALES) plus a pan. The level container sits at an integer screen offset and
// an integer scale, so with nearest-neighbour textures every sprite pixel is an exact s x s block.
import { RENDER_SCALES, type RenderScale } from '../contracts/iso';

export interface CameraState {
  /** Level point (px at scale 1) shown at the centre of the view. */
  readonly cx: number;
  readonly cy: number;
  readonly scale: RenderScale;
}

export interface View {
  readonly width: number;
  readonly height: number;
}

/** Container offset for a camera: integers, always. */
export function containerOffset(cam: CameraState, view: View): { x: number; y: number } {
  return { x: Math.round(view.width / 2 - cam.cx * cam.scale), y: Math.round(view.height / 2 - cam.cy * cam.scale) };
}

/** Screen point -> level point (exact inverse of the rounded container offset). */
export function screenToLevel(cam: CameraState, view: View, sx: number, sy: number): { x: number; y: number } {
  const o = containerOffset(cam, view);
  return { x: (sx - o.x) / cam.scale, y: (sy - o.y) / cam.scale };
}

export function levelToScreen(cam: CameraState, view: View, lx: number, ly: number): { x: number; y: number } {
  const o = containerOffset(cam, view);
  return { x: o.x + lx * cam.scale, y: o.y + ly * cam.scale };
}

/** The next render scale up (dir 1) or down (dir -1), clamped to RENDER_SCALES. */
export function stepScale(scale: RenderScale, dir: 1 | -1): RenderScale {
  const i = RENDER_SCALES.indexOf(scale);
  return RENDER_SCALES[Math.max(0, Math.min(RENDER_SCALES.length - 1, i + dir))]!;
}

/** Nearest allowed render scale to a real number (used while tweening: never fractional). */
export function nearestScale(s: number): RenderScale {
  let best: RenderScale = RENDER_SCALES[0];
  for (const r of RENDER_SCALES) if (Math.abs(r - s) < Math.abs(best - s)) best = r;
  return best;
}

/** Zoom one step keeping the level point under screen point (sx, sy) fixed. */
export function zoomAt(cam: CameraState, view: View, sx: number, sy: number, dir: 1 | -1): CameraState {
  const scale = stepScale(cam.scale, dir);
  if (scale === cam.scale) return cam;
  const p = screenToLevel(cam, view, sx, sy);
  return { scale, cx: p.x - (sx - view.width / 2) / scale, cy: p.y - (sy - view.height / 2) / scale };
}

/** Pan by a screen-pixel drag delta. */
export function panBy(cam: CameraState, dx: number, dy: number): CameraState {
  return { ...cam, cx: cam.cx - dx / cam.scale, cy: cam.cy - dy / cam.scale };
}

/** Largest render scale at which a box (plus a margin) fits in the view; at least 1. */
export function fitScale(box: { w: number; h: number }, view: View, margin = 32): RenderScale {
  let best: RenderScale = RENDER_SCALES[0];
  for (const s of RENDER_SCALES) if (box.w * s + margin <= view.width && box.h * s + margin <= view.height) best = s;
  return best;
}

/** Keeps the camera centre inside the level bounds (so the scene cannot be dragged out of sight). */
export function clampTo(cam: CameraState, b: { x: number; y: number; w: number; h: number }): CameraState {
  return { ...cam, cx: Math.min(b.x + b.w, Math.max(b.x, cam.cx)), cy: Math.min(b.y + b.h, Math.max(b.y, cam.cy)) };
}

/** Two-finger pinch: one scale step each time the finger distance changes by this factor. */
export const PINCH_STEP = 1.25;
export function pinchStep(startDist: number, dist: number): 1 | -1 | 0 {
  if (dist >= startDist * PINCH_STEP) return 1;
  if (dist <= startDist / PINCH_STEP) return -1;
  return 0;
}

/**
 * The camera centre for a zoom out that wants `focus` (the object we came from) at the centre: moved only as far as
 * needed so that the level's home box stays on screen where it fits, or fills the screen where it does not (previously:
 * Back from a device centred the rack on that device, with its top U under the HUD).
 */
export function focusInside(focus: { x: number; y: number }, home: { x: number; y: number; w: number; h: number }, scale: number, view: View, margin = 32): { x: number; y: number } {
  const axis = (f: number, lo: number, len: number, size: number) => {
    const half = (size - margin) / 2 / scale;
    const [a, b] = len <= 2 * half ? [lo + len - half, lo + half] : [lo + half, lo + len - half];
    return Math.min(b, Math.max(a, f));
  };
  return { x: axis(focus.x, home.x, home.w, view.width), y: axis(focus.y, home.y, home.h, view.height) };
}
