// Theme 'industrial-robotic': checks beyond the shared conformance test. The palette is 16 distinct colours with
// distinct status LEDs, props vary by seed (at least 4 small, 3 large), status and NetBox colours show, faceplate
// kinds differ, and light comes from the top left.
import { describe, expect, it } from 'vitest';
import { PALETTE_KEYS, renderSlot, type PixelBuffer, type SlotId, type SlotVariant } from '../src/contracts/theme';
import { getPixel } from '../src/themes/kit';
import { INDUSTRIAL_PROPS, industrialTheme as T } from '../src/themes/industrial';

const digest = (b: PixelBuffer) => {
  let h = 2166136261;
  for (let i = 0; i < b.data.length; i++) h = Math.imul(h ^ b.data[i]!, 16777619);
  return (h >>> 0).toString(16);
};
const draw = (slot: SlotId, v: SlotVariant = {}) => renderSlot(T, slot, v);
const count = (b: PixelBuffer, hex: string) => {
  let n = 0;
  for (let y = 0; y < b.height; y++) for (let x = 0; x < b.width; x++) if (getPixel(b, x, y) === hex) n++;
  return n;
};
const luma = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
};

describe('industrial-robotic palette', () => {
  it('has 16 distinct colours and three distinct status LEDs', () => {
    const colours = PALETTE_KEYS.map((k) => T.palette[k].toLowerCase());
    expect(new Set(colours).size).toBe(16);
    const { led_ok, led_warn, led_err } = T.palette;
    expect(new Set([led_ok, led_warn, led_err]).size).toBe(3);
  });
  it('the steel ramp is lit top > left > right (rack_empty faces)', () => {
    // Rotation 2: back grille on the S face, robot face on the E face; sample plain panel pixels between the details.
    const b = draw('rack_empty', { rotation: 2 });
    const top = getPixel(b, 12, 8)!, left = getPixel(b, 7, 17)!, right = getPixel(b, 29, 35)!;
    expect([top, left, right]).toEqual([T.palette.metal, T.palette.floor, T.palette.metal_shade]);
    expect(luma(top)).toBeGreaterThan(luma(left));
    expect(luma(left)).toBeGreaterThan(luma(right));
  });
});

describe('industrial-robotic variants', () => {
  it('at least 4 small and 3 large props, each seed in a cycle drawing a different prop', () => {
    expect(INDUSTRIAL_PROPS.small).toBeGreaterThanOrEqual(4);
    expect(INDUSTRIAL_PROPS.large).toBeGreaterThanOrEqual(3);
    for (const [slot, n] of [['decoration_small', INDUSTRIAL_PROPS.small], ['decoration_large', INDUSTRIAL_PROPS.large]] as const) {
      const seen = new Set(Array.from({ length: n }, (_, seed) => digest(draw(slot, { seed }))));
      expect(seen.size, slot).toBe(n);
      expect(digest(draw(slot, { seed: n + 1 }))).toBe(digest(draw(slot, { seed: 1 })));
    }
  });
  it('device status shows in the eyes: running, failed and offline differ', () => {
    for (const slot of ['device_1u', 'device_2u', 'device_4u', 'rack_empty'] as const) {
      const ok = draw(slot, { status: 'active' }), bad = draw(slot, { status: 'failed' }), off = draw(slot, { status: 'offline' });
      expect(count(ok, T.palette.led_ok), slot).toBeGreaterThan(0);
      expect(count(bad, T.palette.led_err), slot).toBeGreaterThan(0);
      expect(count(off, T.palette.led_ok) + count(off, T.palette.led_err), slot).toBe(0);
    }
  });
  it('status rule: every non-active state differs from active by >= 4 native px that are not just an LED hue swap', () => {
    // Proposed contract 1.1.2 status rule: planned/staged ghost, offline/inventory unlit,
    // failed led_err plus a non-hue cue, decommissioning led_warn. A pixel counts only when it differs and the pair is
    // not two LED colours, so colour-blind viewers get a shape cue for every state.
    const leds = new Set([T.palette.led_ok, T.palette.led_warn, T.palette.led_err]);
    const shapeDiff = (a: PixelBuffer, b: PixelBuffer) => {
      let n = 0;
      for (let y = 0; y < a.height; y++)
        for (let x = 0; x < a.width; x++) {
          const pa = getPixel(a, x, y), pb = getPixel(b, x, y);
          if (pa !== pb && !(pa && pb && leds.has(pa) && leds.has(pb))) n++;
        }
      return n;
    };
    const slots = ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu', 'rack_empty'] as const;
    const states = ['planned', 'staged', 'offline', 'inventory', 'failed', 'decommissioning'];
    for (const slot of slots) {
      const ok = draw(slot, { status: 'active' });
      for (const status of states) expect(shapeDiff(ok, draw(slot, { status })), `${slot} ${status}`).toBeGreaterThanOrEqual(4);
      expect(count(draw(slot, { status: 'failed' }), T.palette.led_err), `${slot} failed`).toBeGreaterThan(0);
      expect(count(draw(slot, { status: 'decommissioning' }), T.palette.led_warn), `${slot} decommissioning`).toBeGreaterThan(0);
      const seen = new Set(['active', 'planned', 'offline', 'failed', 'decommissioning'].map((status) => digest(draw(slot, { status }))));
      expect(seen.size, `${slot} five states distinct`).toBe(5);
    }
  });
  it('NetBox colours show: role colour on faceplates and the helper bot, cable colour on belts and ports', () => {
    const role = '#2e8b6e', cable = '#c4622d';
    for (const slot of ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu'] as const)
      expect(count(draw(slot, { role_color: role }), role), slot).toBeGreaterThan(40);
    expect(count(draw('decoration_small', { seed: 0, role_color: role }), role)).toBeGreaterThan(10);
    for (const slot of ['wire_straight', 'wire_corner', 'wire_cross'] as const)
      for (const rotation of [0, 1, 2, 3] as const) expect(count(draw(slot, { rotation, wire_color: cable }), cable), `${slot} ${rotation}`).toBeGreaterThan(10);
    expect(count(draw('port', { status: 'connected', wire_color: cable }), cable)).toBeGreaterThan(10);
  });
  it('1U faceplate kinds look different from each other', () => {
    const v = { role_color: '#2e8b6e', status: 'active' };
    const kinds = ['device_1u', 'switch', 'router', 'patch_panel', 'psu'] as const;
    expect(new Set(kinds.map((k) => digest(draw(k, v)))).size).toBe(kinds.length);
  });
  it('rack rotation moves the open bay: front S at 0, front E at 3, no bay at 1 and 2', () => {
    const bay = (b: PixelBuffer, x: number, y: number) => getPixel(b, x, y) === T.palette.outline;
    // A pixel in the middle of each face's device columns.
    const s = [7, 9 + 3 + 20] as const, e = [24, 24 - 12 + 20] as const;
    expect([bay(draw('rack_empty', { rotation: 0 }), ...s), bay(draw('rack_empty', { rotation: 0 }), ...e)]).toEqual([true, false]);
    expect([bay(draw('rack_empty', { rotation: 3 }), ...s), bay(draw('rack_empty', { rotation: 3 }), ...e)]).toEqual([false, true]);
    for (const rotation of [1, 2] as const) expect(bay(draw('rack_empty', { rotation }), ...s) && bay(draw('rack_empty', { rotation }), ...e)).toBe(false);
  });
  it('floor tiles alternate with the seed', () => {
    expect(digest(draw('room_floor', { seed: 0 }))).not.toBe(digest(draw('room_floor', { seed: 1 })));
  });
});
