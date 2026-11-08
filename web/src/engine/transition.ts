// Level transitions: the old level's container flies to the entered (or left) object while stepping through
// integer scales, then cross-fades into the new level's container, which settles from one scale step away.
// Pure function of t in [0, 1], so it is unit-tested; the scale is an allowed integer at every frame.
import { nearestScale, stepScale, type CameraState } from './camera';

export interface TransitionPlan {
  readonly direction: 'in' | 'out';
  /** Old level: where its camera starts and where it heads (centred on the object, 2 scale steps further). */
  readonly oldFrom: CameraState;
  readonly oldTo: CameraState;
  /** New level: where its camera starts (1 step away) and its resting camera. */
  readonly newFrom: CameraState;
  readonly newTo: CameraState;
}

export const TRANSITION_MS = 420;
/** Fraction of the transition at which the cross-fade starts / the old flight ends. */
export const FADE_START = 0.45;
export const FLIGHT_END = 0.7;

export function planTransition(direction: 'in' | 'out', oldCam: CameraState, oldFocus: { x: number; y: number }, newCam: CameraState): TransitionPlan {
  const d = direction === 'in' ? 1 : -1;
  const oldTo: CameraState = { cx: oldFocus.x, cy: oldFocus.y, scale: stepScale(stepScale(oldCam.scale, d), d) };
  const newFrom: CameraState = { ...newCam, scale: stepScale(newCam.scale, d === 1 ? -1 : 1) };
  return { direction, oldFrom: oldCam, oldTo, newFrom, newTo: newCam };
}

const smooth = (t: number) => t * t * (3 - 2 * t);
const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

function between(a: CameraState, b: CameraState, t: number): CameraState {
  const e = smooth(clamp01(t));
  return { cx: lerp(a.cx, b.cx, e), cy: lerp(a.cy, b.cy, e), scale: nearestScale(lerp(a.scale, b.scale, e)) };
}

export interface TransitionFrame {
  readonly oldCam: CameraState;
  readonly oldAlpha: number;
  readonly newCam: CameraState;
  readonly newAlpha: number;
}

export function transitionFrame(plan: TransitionPlan, t: number): TransitionFrame {
  const fade = clamp01((t - FADE_START) / (1 - FADE_START));
  return {
    oldCam: between(plan.oldFrom, plan.oldTo, t / FLIGHT_END),
    oldAlpha: 1 - smooth(fade),
    newCam: between(plan.newFrom, plan.newTo, (t - FADE_START) / (1 - FADE_START)),
    newAlpha: smooth(fade),
  };
}
