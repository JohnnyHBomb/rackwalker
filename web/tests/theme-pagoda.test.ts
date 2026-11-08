// Theme 'pagodas-nature': the art rules the conformance test does not cover. LED keys stay distinct for status,
// seeds pick distinct props (at least 4 small, 3 large) and pagodas, NetBox role and cable colours show on the
// sprites that carry them, faceplates follow the status rule and stay readable at 1x, the default wire reads on
// gravel, floor tiles show their grid edge, and the raked gravel joins up across tiles.
import { describe, expect, it } from 'vitest';
import { renderSlot, type PixelBuffer, type SlotId, type SlotVariant } from '../src/contracts/theme';
import { pagodaTheme } from '../src/themes/pagoda';

const p = pagodaTheme.palette;
const key = (b: PixelBuffer) => Buffer.from(b.data).toString('base64');
const colours = (b: PixelBuffer) => {
  const out = new Set<string>();
  for (let i = 0; i < b.data.length; i += 4)
    if (b.data[i + 3]) out.add(`#${((b.data[i]! << 16) | (b.data[i + 1]! << 8) | b.data[i + 2]!).toString(16).padStart(6, '0')}`);
  return out;
};
const render = (slot: SlotId, v: SlotVariant = {}) => renderSlot(pagodaTheme, slot, v);
const distinctBySeed = (slot: SlotId, seeds: number) => new Set(Array.from({ length: seeds }, (_, s) => key(render(slot, { seed: s })))).size;

describe('pagodas-nature theme', () => {
  it('keeps the id and has 16 distinct palette colours with the three LEDs apart from the art colours', () => {
    expect(pagodaTheme.id).toBe('pagodas-nature');
    expect(new Set(Object.values(p)).size).toBe(16);
    for (const led of [p.led_ok, p.led_warn, p.led_err]) expect([p.accent, p.accent_alt, p.wire, p.highlight]).not.toContain(led);
  });

  it('seeds choose at least 4 small props, 3 large props and several pagodas', () => {
    expect(distinctBySeed('decoration_small', 40)).toBeGreaterThanOrEqual(4);
    expect(distinctBySeed('decoration_large', 40)).toBeGreaterThanOrEqual(3);
    expect(distinctBySeed('site_building', 12)).toBeGreaterThanOrEqual(3);
  });

  it('shows the NetBox role colour on every faceplate and the cable colour on every conduit', () => {
    const role = '#123456';
    for (const slot of ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu'] as const)
      expect(colours(render(slot, { role_color: role })).has(role), slot).toBe(true);
    const cable = '#654321';
    for (const slot of ['wire_straight', 'wire_corner', 'wire_cross'] as const)
      for (const rotation of [0, 1, 2, 3] as const) expect(colours(render(slot, { wire_color: cable, rotation })).has(cable), `${slot} ${rotation}`).toBe(true);
    expect(colours(render('port', { status: 'connected', wire_color: cable })).has(cable)).toBe(true);
  });

  it('faceplates follow the device status rule, readable at 1x (at least 4 native px from active)', () => {
    // Rule proposed for contract 1.1.2: active led_ok; planned/staged ghost; offline/inventory unlit;
    // failed led_err plus a shape cue (no led_ok left); decommissioning led_warn.
    const plates = ['device_1u', 'device_2u', 'device_4u', 'switch', 'router', 'patch_panel', 'psu'] as const;
    const diff = (a: PixelBuffer, b: PixelBuffer) => {
      let n = 0;
      for (let i = 0; i < a.data.length; i += 4)
        if (a.data[i] !== b.data[i] || a.data[i + 1] !== b.data[i + 1] || a.data[i + 2] !== b.data[i + 2] || a.data[i + 3] !== b.data[i + 3]) n++;
      return n;
    };
    for (const slot of plates) {
      const active = render(slot, { status: 'active' });
      expect(colours(active).has(p.led_ok), slot).toBe(true);
      for (const status of ['planned', 'staged', 'offline', 'inventory', 'failed', 'decommissioning'])
        expect(diff(active, render(slot, { status })), `${slot} ${status}`).toBeGreaterThanOrEqual(4);
      const failed = colours(render(slot, { status: 'failed' }));
      expect(failed.has(p.led_err) && !failed.has(p.led_ok), `${slot} failed`).toBe(true);
      expect(colours(render(slot, { status: 'decommissioning' })).has(p.led_warn), `${slot} decommissioning`).toBe(true);
      for (const status of ['offline', 'inventory', 'planned']) {
        const c = colours(render(slot, { status }));
        expect([p.led_ok, p.led_warn, p.led_err].some((l) => c.has(l)), `${slot} ${status} unlit`).toBe(false);
      }
    }
    expect(colours(render('port', { status: 'disabled' })).has(p.led_err)).toBe(true);
  });

  it('the default wire reads on raked gravel (luminance contrast at least 3)', () => {
    const lum = (hex: string) => {
      const ch = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
    };
    const [hi, lo] = [lum(p.floor), lum(p.wire)].sort((x, y) => y - x);
    expect((hi! + 0.05) / (lo! + 0.05)).toBeGreaterThanOrEqual(3);
  });

  it('floor tiles carry a visible edge so the room grid reads', () => {
    const f = render('room_floor', { seed: 0 });
    const at = (x: number, y: number) => `#${[0, 1, 2].map((k) => f.data[(y * 32 + x) * 4 + k]!.toString(16).padStart(2, '0')).join('')}`;
    for (let t = 0; t < 16; t++) {
      expect(at(15 - t, Math.floor(t / 2))).toBe(p.floor_alt);
      expect(at(16 + t, Math.floor(t / 2))).toBe(p.floor_alt);
    }
  });

  it('only the rack front facing the viewer shows the open cabinet', () => {
    const shown = (rotation: 0 | 1 | 2 | 3) => colours(render('rack_empty', { rotation })).has(p.background);
    expect([0, 1, 2, 3].map((r) => shown(r as 0 | 1 | 2 | 3))).toEqual([true, false, false, true]);
  });

  it('raked gravel grooves line up across neighbouring tiles', () => {
    // Tile (x, y) sits at screen (16(x - y), 8(x + y)); a groove pixel of one tile continues in its +x neighbour.
    const a = render('room_floor', { seed: 0 }), b = render('room_floor', { seed: 1 });
    const at = (buf: PixelBuffer, x: number, y: number) => (buf.data[(y * 32 + x) * 4 + 3] ? buf.data[(y * 32 + x) * 4] : -1);
    const groove = Number.parseInt(p.floor_alt.slice(1, 3), 16);
    // A groove pixel (x, y) runs on to (x + 2, y + 1), which on the +x neighbour is (x + 2 - 16, y + 1 - 8).
    let joins = 0;
    for (let y = 0; y < 16; y++)
      for (let x = 24; x < 32; x++)
        if (at(a, x, y) === groove) {
          const nx = x + 2 - 16, ny = y + 1 - 8;
          if (nx >= 0 && ny >= 0 && at(b, nx, ny) !== -1) {
            expect(at(b, nx, ny), `(${x},${y})`).toBe(groove);
            joins++;
          }
        }
    expect(joins).toBeGreaterThan(0);
  });
});
