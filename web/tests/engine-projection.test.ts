import { describe, expect, it } from 'vitest';
import { createPixelBuffer, fillDiamond } from '../src/contracts/theme';
import { footprintCenter, isoToScreen, rackUnitAt, rackUnitTop, screenToTile, tileToScreen } from '../src/engine/projection';

describe('tile <-> screen', () => {
  it('tileToScreen is the top corner; footprintCenter the diamond centre', () => {
    expect(tileToScreen(2, 1)).toEqual({ sx: 16, sy: 24 });
    expect(footprintCenter(0, 0)).toEqual({ sx: 0, sy: 8 });
    expect(footprintCenter(0, 0, 2, 2)).toEqual({ sx: 0, sy: 16 });
  });

  it('drawn tile pixels pick back to their tile (the pixel-art diamond overhangs the exact one by its rim only)', () => {
    // fillDiamond rows are 4, 8, ..., 32, ..., 4 px wide: a 1-2 px rim lies outside the exact diamond and belongs to a
    // neighbour under floor(screenToIso). Interior pixels must map back exactly; rim pixels to an edge neighbour.
    for (const [tx, ty] of [[0, 0], [3, 1], [-2, 4], [7, 7], [12, -3]] as const) {
      const buf = createPixelBuffer(32, 16);
      fillDiamond(buf, 16, 0, 1, 1, '#ffffff');
      const top = tileToScreen(tx, ty);
      let inside = 0, rim = 0;
      for (let y = 0; y < 16; y++)
        for (let x = 0; x < 32; x++) {
          if (buf.data[(y * 32 + x) * 4 + 3] === 0) continue;
          const px = x + 0.5, py = y + 0.5;
          const t = screenToTile(top.sx - 16 + px, top.sy + py);
          const exact = Math.abs(px - 16) / 16 + Math.abs(py - 8) / 8 < 1;
          if (exact) {
            inside++;
            expect(t).toEqual({ tx, ty });
          } else {
            rim++;
            expect(Math.abs(t.tx - tx) + Math.abs(t.ty - ty)).toBe(1);
          }
        }
      expect(inside + rim).toBe(2 * (4 + 8 + 12 + 16 + 20 + 24 + 28 + 32));
      expect(inside / (inside + rim)).toBeGreaterThan(0.85);
    }
  });

  it('screenToTile inverts isoToScreen at tile centres across a grid', () => {
    for (let x = -5; x < 20; x++)
      for (let y = -5; y < 20; y++) {
        const c = isoToScreen(x + 0.5, y + 0.5);
        expect(screenToTile(c.sx, c.sy)).toEqual({ tx: x, ty: y });
      }
  });
});

describe('rack elevation (1U = 8 px)', () => {
  const rack = { u_height: 42, starting_unit: 1, desc_units: false };
  it('U42 is the top row, U1 the bottom row; a device top is its highest U', () => {
    expect(rackUnitTop(rack, 42, 1)).toBe(0);
    expect(rackUnitTop(rack, 1, 1)).toBe(41 * 8);
    expect(rackUnitTop(rack, 38, 2)).toBe(3 * 8); // U38-39
    expect(rackUnitTop(rack, 10.5, 1)).toBe(31.5 * 8);
    expect(rackUnitTop(rack, 20, 0.5)).toBe(22.5 * 8); // a 0.5U device's top is half a unit above its position
    expect(rackUnitTop(rack, 20, 1.5)).toBe(21.5 * 8);
  });
  it('descending units put U1 at the top; starting_unit shifts numbering', () => {
    expect(rackUnitTop({ ...rack, desc_units: true }, 1, 1)).toBe(0);
    expect(rackUnitTop({ ...rack, desc_units: true }, 3, 2)).toBe(16);
    expect(rackUnitTop({ u_height: 10, starting_unit: 5, desc_units: false }, 14, 1)).toBe(0);
    expect(rackUnitTop({ u_height: 10, starting_unit: 5, desc_units: false }, 5, 1)).toBe(72);
  });
  it('rackUnitAt inverts rackUnitTop for every U, both directions', () => {
    for (const r of [rack, { ...rack, desc_units: true }, { u_height: 24, starting_unit: 3, desc_units: false }]) {
      for (let u = r.starting_unit; u < r.starting_unit + r.u_height; u++) {
        const y = rackUnitTop(r, u, 1);
        expect(rackUnitAt(r, y)).toBe(u);
        expect(rackUnitAt(r, y + 7.9)).toBe(u);
      }
      expect(rackUnitAt(r, -1)).toBeNull();
      expect(rackUnitAt(r, r.u_height * 8)).toBeNull();
    }
  });
});
