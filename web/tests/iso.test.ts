import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { FACING_VECTOR, WORLD_TILES, ZOOM_LEVELS, depthKey, geoToWorld, isoToScreen, screenToIso } from '../src/contracts/iso';

describe('iso conventions', () => {
  it('maps grid to screen with a 32x16 diamond', () => {
    expect(isoToScreen(0, 0)).toEqual({ sx: 0, sy: 0 });
    expect(isoToScreen(1, 0)).toEqual({ sx: 16, sy: 8 });
    expect(isoToScreen(0, 1)).toEqual({ sx: -16, sy: 8 });
    expect(isoToScreen(1, 1, 10)).toEqual({ sx: 0, sy: 6 });
  });

  it('screenToIso inverts isoToScreen', () => {
    for (const [x, y] of [[0, 0], [3, 7], [-2, 5], [10.5, 1.25]] as const) {
      const p = isoToScreen(x, y);
      const g = screenToIso(p.sx, p.sy);
      expect(g.x).toBeCloseTo(x);
      expect(g.y).toBeCloseTo(y);
    }
  });

  it('facing vectors and depth order', () => {
    expect(FACING_VECTOR.E).toEqual([1, 0]);
    expect(FACING_VECTOR.N).toEqual([0, -1]);
    expect(depthKey(2, 3)).toBeGreaterThan(depthKey(1, 3));
    expect(depthKey(0, 0, 2, 2)).toBe(2);
    expect([...ZOOM_LEVELS]).toEqual(['world', 'site', 'room', 'rack', 'device']);
  });

  it('projects the fixture sites inside the overworld with north up and east right', () => {
    const world = JSON.parse(readFileSync(resolve(__dirname, '../../fixtures/world.json'), 'utf8'));
    const sites = world.sites.map((w: { site: { name: string; latitude: number; longitude: number } }) => w.site);
    const lats = sites.map((s: { latitude: number }) => s.latitude);
    const lons = sites.map((s: { longitude: number }) => s.longitude);
    const bbox = { minLat: Math.min(...lats), maxLat: Math.max(...lats), minLon: Math.min(...lons), maxLon: Math.max(...lons) };
    const at = Object.fromEntries(sites.map((s: { name: string; latitude: number; longitude: number }) => {
      const g = geoToWorld(s.latitude, s.longitude, bbox);
      expect(g.x).toBeGreaterThanOrEqual(4);
      expect(g.y).toBeGreaterThanOrEqual(4);
      expect(g.x).toBeLessThanOrEqual(WORLD_TILES - 4);
      expect(g.y).toBeLessThanOrEqual(WORLD_TILES - 4);
      return [s.name, isoToScreen(g.x, g.y)];
    }));
    // North Depot is north of West Works -> higher on screen; East Exchange is east of it -> further right.
    expect(at['North Depot'].sy).toBeLessThan(at['West Works'].sy);
    expect(at['East Exchange'].sx).toBeGreaterThan(at['West Works'].sx);
  });
});
