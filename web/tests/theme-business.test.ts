// Theme 'business': checks beyond the shared conformance test (tests/theme-conformance.test.ts).
import { describe, expect, it } from 'vitest';
import { PALETTE_KEYS, renderSlot, type PixelBuffer } from '../src/contracts/theme';
import { businessTheme as t } from '../src/themes/business';

const digest = (b: PixelBuffer) => Array.from(b.data).join(',');
const opaque = (b: PixelBuffer, x: number, y: number) => b.data[(y * b.width + x) * 4 + 3]! > 0;
const at = (b: PixelBuffer, x: number, y: number) => {
  const i = (y * b.width + x) * 4;
  return `#${((b.data[i]! << 16) | (b.data[i + 1]! << 8) | b.data[i + 2]!).toString(16).padStart(6, '0')}`;
};

describe('business theme', () => {
  it('has 16 distinct palette colours, LED keys distinct from each other', () => {
    expect(new Set(PALETTE_KEYS.map((k) => t.palette[k])).size).toBe(16);
    expect(new Set([t.palette.led_ok, t.palette.led_warn, t.palette.led_err]).size).toBe(3);
  });

  it('chooses at least 4 small, 3 large props and 3 site buildings by variant.seed', () => {
    const kinds = (slot: 'decoration_small' | 'decoration_large' | 'site_building', n: number) =>
      new Set(Array.from({ length: n }, (_, seed) => digest(renderSlot(t, slot, { seed })))).size;
    expect(kinds('decoration_small', 7)).toBeGreaterThanOrEqual(4);
    expect(kinds('decoration_large', 4)).toBeGreaterThanOrEqual(3);
    expect(kinds('site_building', 3)).toBe(3);
  });

  it('a loose device prop carries its role colour', () => {
    for (const seed of [0, 1, 2, 3, 4]) {
      const b = renderSlot(t, 'decoration_small', { seed, role_color: '#123456' });
      expect(b.data.some((_, i) => i % 4 === 0 && at(b, (i / 4) % 16, Math.floor(i / 64)) === '#123456'), `seed ${seed}`).toBe(true);
    }
  });

  it('the rack shows its blue LED strip on the front face only (S face at rotation 0, E face at rotation 3)', () => {
    const blue = t.palette.accent_alt;
    const hasBlue = (b: PixelBuffer, x0: number, x1: number) => {
      for (let y = 0; y < b.height; y++) for (let x = x0; x <= x1; x++) if (opaque(b, x, y) && at(b, x, y) === blue) return true;
      return false;
    };
    const r0 = renderSlot(t, 'rack_empty', { rotation: 0 });
    expect(hasBlue(r0, 0, 15)).toBe(true);
    expect(hasBlue(r0, 16, 31)).toBe(false);
    const r3 = renderSlot(t, 'rack_empty', { rotation: 3 });
    expect(hasBlue(r3, 16, 31)).toBe(true);
    expect(hasBlue(r3, 0, 15)).toBe(false);
  });

  it('status LEDs follow the device status', () => {
    const led = (status: string) => at(renderSlot(t, 'device_1u', { status }), 57, 3);
    expect(led('active')).toBe(t.palette.led_ok);
    expect(led('decommissioning')).toBe(t.palette.led_warn);
    expect(led('failed')).toBe(t.palette.led_err);
    expect(led('offline')).toBe(t.palette.metal_shade);
  });

  // At the default 1x rack zoom a 1 px LED change is unreadable.
  const PLATES = ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu'] as const;
  const diff = (a: PixelBuffer, b: PixelBuffer) => {
    let n = 0;
    for (let i = 0; i < a.data.length; i += 4)
      if (a.data[i] !== b.data[i] || a.data[i + 1] !== b.data[i + 1] || a.data[i + 2] !== b.data[i + 2] || a.data[i + 3] !== b.data[i + 3]) n++;
    return n;
  };

  it('every non-active status differs from active by at least 4 native px on every faceplate, patch panel included', () => {
    for (const slot of PLATES) {
      const active = renderSlot(t, slot, { status: 'active' });
      for (const status of ['planned', 'staged', 'offline', 'inventory', 'failed', 'decommissioning'])
        expect(diff(active, renderSlot(t, slot, { status })), `${slot} ${status}`).toBeGreaterThanOrEqual(4);
    }
  });

  it('failed carries a shape cue as well as a hue: a dashed border on all four edges', () => {
    for (const slot of PLATES) {
      const b = renderSlot(t, slot, { status: 'failed' });
      const err = t.palette.led_err;
      expect([at(b, 0, 0), at(b, 1, 0), at(b, 2, 0), at(b, 3, 0)], slot).toEqual([err, err, t.palette.outline, t.palette.outline]);
      expect(at(b, b.width - 1, 0), slot).toBe(err);
      expect(at(b, 0, b.height - 1) === err || at(b, 0, b.height - 2) === err, slot).toBe(true);
    }
  });

  it('the glass blue is quieter than the orange accent (lower saturation and lightness)', () => {
    const hsl = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
      const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
      return { s: mx === mn ? 0 : (mx - mn) / (1 - Math.abs(2 * l - 1)), l };
    };
    const glass = hsl(t.palette.accent_alt), accent = hsl(t.palette.accent);
    expect(glass.s).toBeLessThan(accent.s * 0.5);
    expect(glass.l).toBeLessThan(accent.l);
  });

  it('faceplates of different roles differ, and the generic one shows the role colour', () => {
    const plates = (['device_1u', 'switch', 'router', 'patch_panel', 'psu'] as const).map((s) => digest(renderSlot(t, s, {})));
    expect(new Set(plates).size).toBe(5);
    expect(at(renderSlot(t, 'device_1u', { role_color: '#abcdef' }), 5, 4)).toBe('#abcdef');
  });
});
