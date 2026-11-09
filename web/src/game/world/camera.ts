// The overworld camera: a 256 x 224 native view that follows the player in whole pixels and never leaves the
// player's region. The window shows the native view at the largest integer scale that fits (at least 1x).
export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

/** Top-left of the view (map pixels, integers): centred on the target, clamped to the bounds. */
export function follow(tx: number, ty: number, viewW: number, viewH: number, bounds: Rect): { x: number; y: number } {
  const clamp = (v: number, lo: number, hi: number) => (hi < lo ? Math.round((lo + hi) / 2) : Math.min(hi, Math.max(lo, v)));
  return {
    x: clamp(Math.round(tx - viewW / 2), bounds.x, bounds.x + bounds.w - viewW),
    y: clamp(Math.round(ty - viewH / 2), bounds.y, bounds.y + bounds.h - viewH),
  };
}

/** The largest integer scale at which the native view fits the window (never below 1). */
export function integerScale(winW: number, winH: number, nativeW: number, nativeH: number): number {
  return Math.max(1, Math.floor(Math.min(winW / nativeW, winH / nativeH)));
}
