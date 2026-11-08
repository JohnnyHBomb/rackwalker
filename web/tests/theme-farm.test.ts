// Farm theme art rules that the generic conformance test does not check: distinct lantern colours, variety by
// seed, light from the top-left, faceplates that differ by role and show status, role colour carried on plates and
// loose-device props.
import { describe, expect, it } from 'vitest';
import { renderSlot, type PixelBuffer, type SlotId, type SlotVariant } from '../src/contracts/theme';
import { getPixel } from '../src/themes/kit';
import { farmTheme } from '../src/themes/farm';

const P = farmTheme.palette;
const digest = (b: PixelBuffer) => Array.from(b.data).join(',');
const colours = (b: PixelBuffer) => {
  const out = new Map<string, number>();
  for (let y = 0; y < b.height; y++)
    for (let x = 0; x < b.width; x++) {
      const c = getPixel(b, x, y);
      if (c) out.set(c, (out.get(c) ?? 0) + 1);
    }
  return out;
};
const luma = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
};
/** Mean luminance of the opaque pixels in a rectangle. */
function meanLuma(b: PixelBuffer, x0: number, y0: number, x1: number, y1: number): number {
  let s = 0, n = 0;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const c = getPixel(b, x, y);
      if (c) (s += luma(c)), n++;
    }
  return s / n;
}
const distinctBySeed = (slot: SlotId, seeds: number[]) => new Set(seeds.map((seed) => digest(renderSlot(farmTheme, slot, { seed })))).size;

describe('farm theme', () => {
  it('has 16 distinct palette colours, with ok/warn/err lanterns clearly apart', () => {
    expect(new Set(Object.values(P)).size).toBe(16);
    const leds = [P.led_ok, P.led_warn, P.led_err];
    for (const a of leds) for (const b of leds) if (a !== b) {
      const [x, y] = [parseInt(a.slice(1), 16), parseInt(b.slice(1), 16)];
      const d = Math.hypot(((x >> 16) & 255) - ((y >> 16) & 255), ((x >> 8) & 255) - ((y >> 8) & 255), (x & 255) - (y & 255));
      expect(d).toBeGreaterThan(90);
    }
  });

  it('chooses at least 4 small and 3 large props by seed, deterministically', () => {
    expect(distinctBySeed('decoration_small', [0, 1, 2, 3])).toBe(4);
    expect(distinctBySeed('decoration_large', [0, 1, 2, 3])).toBe(4);
    expect(distinctBySeed('site_building', [0, 1, 2])).toBe(3);
    expect(digest(renderSlot(farmTheme, 'decoration_small', { seed: 9 }))).toBe(digest(renderSlot(farmTheme, 'decoration_small', { seed: 9 })));
  });

  it('floor: the engine checker (seeds 0/1) is two grasses; higher seeds add tilled soil and straw', () => {
    const main = (seed: number) => [...colours(renderSlot(farmTheme, 'room_floor', { seed }))].sort((a, b) => b[1] - a[1])[0]![0];
    expect(main(0)).toBe(P.floor);
    expect(main(1)).toBe(P.floor_alt);
    expect(main(3)).toBe(P.wall_shade); // tilled soil
    expect(main(7)).toBe(P.metal_shade); // straw
  });

  it('light comes from the top-left: rack top lighter than its left face, left lighter than right', () => {
    const b = renderSlot(farmTheme, 'rack_empty', { rotation: 1 }); // back to the viewer: both faces are boards
    const top = meanLuma(b, 8, 2, 24, 12);
    const left = meanLuma(b, 2, 24, 14, 50);
    const right = meanLuma(b, 18, 24, 30, 50);
    expect(top).toBeGreaterThan(left);
    expect(left).toBeGreaterThan(right);
  });

  it('a rack faces its front (rotation 0: S face, 3: E face) with a dark cabinet opening', () => {
    const dark = (v: SlotVariant, x: number) => getPixel(renderSlot(farmTheme, 'rack_empty', v), x, 30);
    expect(dark({ rotation: 0 }, 8)).toBe(P.outline);
    expect(dark({ rotation: 3 }, 24)).toBe(P.outline);
    expect(dark({ rotation: 1 }, 8)).not.toBe(P.outline);
  });

  it('faceplates for each role differ, carry the role colour as most of the board, and light their lantern by status', () => {
    const role = '#3366cc';
    const kinds: SlotId[] = ['device_1u', 'switch', 'router', 'patch_panel', 'psu'];
    expect(new Set(kinds.map((s) => digest(renderSlot(farmTheme, s, { role_color: role })))).size).toBe(kinds.length);
    for (const s of [...kinds, 'device_2u', 'device_4u'] as SlotId[]) {
      const c = colours(renderSlot(farmTheme, s, { role_color: role, status: 'active' }));
      const top = [...c].sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k]) => k);
      expect(top, s).toContain(role);
      expect(c.has(P.led_ok), s).toBe(true);
      const failed = colours(renderSlot(farmTheme, s, { role_color: role, status: 'failed' }));
      expect(failed.has(P.led_err) && !failed.has(P.led_ok), s).toBe(true);
      const off = colours(renderSlot(farmTheme, s, { role_color: role, status: 'offline' }));
      expect(off.has(P.led_ok) || off.has(P.led_err), s).toBe(false);
    }
  });

  it('ports show their status: plug in the cable colour, red cross when disabled, pumpkin frame for mgmt', () => {
    expect(colours(renderSlot(farmTheme, 'port', { status: 'connected', wire_color: '#123456' })).has('#123456')).toBe(true);
    expect(colours(renderSlot(farmTheme, 'port', { status: 'disabled' })).has(P.led_err)).toBe(true);
    expect(colours(renderSlot(farmTheme, 'port', { status: 'mgmt' })).has(P.accent_alt)).toBe(true);
  });

  it('loose-device props wear the role colour and fly a pennant when they need attention', () => {
    for (const seed of [0, 1, 2, 3]) {
      expect(colours(renderSlot(farmTheme, 'decoration_small', { seed, role_color: '#123456' })).has('#123456'), `seed ${seed}`).toBe(true);
      expect(colours(renderSlot(farmTheme, 'decoration_small', { seed, status: 'failed' })).has(P.led_err), `seed ${seed}`).toBe(true);
      expect(colours(renderSlot(farmTheme, 'decoration_small', { seed, status: 'active' })).has(P.led_err), `seed ${seed}`).toBe(false);
    }
  });

  it('every non-active status differs from active by at least 4 native px on every device slot (1x rack zoom)', () => {
    const differ = (a: PixelBuffer, b: PixelBuffer) => {
      let n = 0;
      for (let i = 0; i < a.data.length; i += 4) if (a.data[i] !== b.data[i] || a.data[i + 1] !== b.data[i + 1] || a.data[i + 2] !== b.data[i + 2] || a.data[i + 3] !== b.data[i + 3]) n++;
      return n;
    };
    const slots: SlotId[] = ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu'];
    for (const s of slots) {
      const active = renderSlot(farmTheme, s, { status: 'active' });
      for (const st of ['planned', 'staged', 'offline', 'inventory', 'failed', 'decommissioning'])
        expect(differ(active, renderSlot(farmTheme, s, { status: st })), `${s} ${st}`).toBeGreaterThanOrEqual(4);
    }
  });

  it('status rule: decommissioning amber, offline/inventory/planned/staged unlit, failed red plus a crack', () => {
    const c = (st: string) => colours(renderSlot(farmTheme, 'patch_panel', { status: st }));
    expect(c('decommissioning').has(P.led_warn)).toBe(true);
    for (const st of ['offline', 'inventory', 'planned', 'staged']) expect([P.led_ok, P.led_err, P.led_warn].some((k) => c(st).has(k)), st).toBe(false);
    // The crack is a non-hue cue: failed differs from offline in more than the lantern's 9 px.
    const failed = renderSlot(farmTheme, 'patch_panel', { status: 'failed' }), off = renderSlot(farmTheme, 'patch_panel', { status: 'offline' });
    let n = 0;
    for (let i = 0; i < failed.data.length; i += 4) if (failed.data[i] !== off.data[i] || failed.data[i + 1] !== off.data[i + 1]) n++;
    expect(n).toBeGreaterThan(20);
  });

  it('a planned rack is a see-through ghost of the cabinet, not a hay stack', () => {
    const active = renderSlot(farmTheme, 'rack_empty', { rotation: 0 });
    const planned = renderSlot(farmTheme, 'rack_empty', { rotation: 0, status: 'planned' });
    expect(colours(planned).get(P.metal) ?? 0).toBeLessThan((colours(active).get(P.metal) ?? 0) + 1);
    let holes = 0;
    for (let y = 20; y < 50; y++) for (let x = 4; x < 12; x++) if (getPixel(active, x, y) && !getPixel(planned, x, y)) holes++;
    expect(holes).toBeGreaterThan(80);
    expect(getPixel(planned, 16, 63)).not.toBeNull();
  });

  it('hoses wear a dark casing so they read on grass', () => {
    const b = renderSlot(farmTheme, 'wire_straight', {});
    expect(colours(b).get(P.outline) ?? 0).toBeGreaterThanOrEqual(28);
    const lin = (hex: string) => {
      const ch = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
    };
    expect((lin(P.wire) + 0.05) / (lin(P.floor) + 0.05)).toBeGreaterThan(1.9); // WCAG luminance ratio, was 1.31
  });

  it('hoses take the cable colour', () => {
    for (const slot of ['wire_straight', 'wire_corner', 'wire_cross'] as SlotId[])
      expect(colours(renderSlot(farmTheme, slot, { wire_color: '#123456' })).get('#123456')).toBeGreaterThan(20);
  });
});
