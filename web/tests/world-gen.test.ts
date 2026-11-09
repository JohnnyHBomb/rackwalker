// GPS-scaled regions: clustering by distance, compressed walking scale, paths between the sites of a region, water
// crossed by bridges, collisions, the airfield placeholder between regions, and the same data giving the same map.
import { describe, expect, it } from 'vitest';
import { clusterSites, compress, haversineKm, REGION_KM, WorldMap, type WorldSite } from '../src/game/world/gen';

const S = (id: number, lat: number | null, lon: number | null, country: string | null = 'NL', theme: string | null = null): WorldSite =>
  ({ id, name: `Site ${id}`, latitude: lat, longitude: lon, theme, country });
// Three seed-like sites in Great Britain: 1-3 are 225 km apart, 2 is more than 250 km from both.
const seed = [S(1, 54.118, -1.342, 'GB', 'industrial-robotic'), S(2, 51.621, -2.893, 'GB', 'farm'), S(3, 52.784, 1.412, 'GB', 'business')];

describe('regions', () => {
  it('haversine is right to a kilometre on a known pair', () => expect(Math.round(haversineKm(51.5, 0, 52.5, 0))).toBe(111));
  it('clusters by single linkage under the threshold, ordered by lowest id', () => {
    expect(haversineKm(54.118, -1.342, 52.784, 1.412)).toBeLessThan(REGION_KM);
    expect(clusterSites(seed)).toEqual([[1, 3], [2]]);
    // a chain joins: 1-4 and 4-3 are close although 1-3 are not
    expect(clusterSites([S(1, 52, 0), S(3, 52, 6), S(4, 52, 3)])).toEqual([[1, 3, 4]]);
    // sites without GPS join the lowest-id site's region
    expect(clusterSites([S(5, null, null), S(2, 52, 0), S(7, 40, 20)])).toEqual([[2, 5], [7]]);
  });
  it('compresses distance logarithmically: 10x the distance is far less than 10x the walk', () => {
    const a = Math.hypot(...Object.values(compress(20, 0)));
    const b = Math.hypot(...Object.values(compress(200, 0)));
    expect(b).toBeGreaterThan(a);
    expect(b / a).toBeLessThan(3);
    expect(compress(0, 50).y).toBeLessThan(0); // north is up
    expect(compress(50, 0).x).toBeGreaterThan(0); // east is right
  });
  it('keeps compass order inside a region', () => {
    const m = new WorldMap([S(1, 52, 4), S(2, 52.3, 4.9)]);
    const [a, b] = [m.doorOf(1)!, m.doorOf(2)!];
    expect(b.x).toBeGreaterThan(a.x); // 2 is east of 1
    expect(b.y).toBeLessThan(a.y); // and north
  });
});

describe('the map', () => {
  const m = new WorldMap(seed);
  it('puts distant regions side by side with no walk between them, and an airfield pad in each', () => {
    expect(m.regions.map((r) => r.siteIds)).toEqual([[1, 3], [2]]);
    const [r0, r1] = m.regions as [typeof m.regions[0], typeof m.regions[0]];
    expect(m.path(r0.arrive, r1.arrive)).toEqual([]);
    expect(m.path(r0.arrive, m.doorOf(2)!)).toEqual([]);
    for (const r of m.regions) {
      expect(r.airfield).not.toBeNull();
      expect(m.isWarp(r.airfield!.x, r.airfield!.y)).toBe(true);
      expect(m.path(r.arrive, r.airfield!).length).toBeGreaterThan(0);
    }
    expect(m.nextAirfield(r0.arrive)).toEqual(r1.airfield);
    expect(m.nextAirfield(r1.arrive)).toEqual(r0.airfield);
  });
  it('a one-region world has no airfield', () => {
    const one = new WorldMap([S(1, 52, 4), S(2, 52.1, 4.2)]);
    expect(one.regions).toHaveLength(1);
    expect(one.regions[0]!.airfield).toBeNull();
  });
  it('joins the sites of a region by a path from door to door', () => {
    const [a, b] = [m.doorOf(1)!, m.doorOf(3)!];
    // BFS over path and bridge tiles only
    const seen = new Set<string>();
    const q = [{ x: a.x, y: a.y + 1 }];
    while (q.length) {
      const p = q.pop()!;
      const k = `${p.x},${p.y}`;
      if (seen.has(k) || !['P', 'B'].includes(m.at(p.x, p.y) ?? '')) continue;
      seen.add(k);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) q.push({ x: p.x + dx!, y: p.y + dy! });
    }
    expect(seen.has(`${b.x},${b.y + 1}`)).toBe(true);
  });
  it('collides with buildings, water, trees, fields, landmarks and villagers; doors, paths and bridges are walkable', () => {
    for (const d of m.doors) {
      for (let y = d.by; y < d.by + d.bh; y++) for (let x = d.bx; x < d.bx + d.bw; x++) expect(m.walkable(x, y)).toBe(x === d.x && y === d.y);
    }
    for (let y = 0; y < m.h; y++)
      for (let x = 0; x < m.w; x++) {
        const t = m.at(x, y)!;
        if (t === 'W' || t === 'F' || t === 'C') expect(m.walkable(x, y), `${t} at ${x},${y}`).toBe(false);
        if (t === 'B') expect(m.walkable(x, y)).toBe(true);
      }
    for (const o of m.objects.filter((o) => o.kind === 'villager' || (o.kind === 'tree' && o.sprite !== 'bush'))) {
      expect(m.walkable(Math.floor(o.x / 16), Math.floor(o.y / 16)), `${o.sprite} at ${o.x},${o.y}`).toBe(false);
    }
  });
  it('bridges run straight north-south across water', () => {
    for (let y = 0; y < m.h; y++)
      for (let x = 0; x < m.w; x++)
        if (m.at(x, y) === 'B') {
          expect(['P', 'B']).toContain(m.at(x, y - 1));
          expect(['P', 'B']).toContain(m.at(x, y + 1));
        }
  });
  it('fills a region with landmarks, trees and a villager (design rule: no empty world)', () => {
    for (const r of m.regions) {
      const inR = m.objects.filter((o) => o.x >= r.x0 * 16 && o.x < (r.x0 + r.w) * 16);
      expect(inR.filter((o) => o.kind === 'landmark').length).toBeGreaterThanOrEqual(3);
      expect(inR.filter((o) => o.kind === 'villager').length).toBeGreaterThanOrEqual(1);
      expect(inR.filter((o) => o.kind === 'tree').length).toBeGreaterThan(50);
    }
  });
  it('same data, same map; a changed coordinate changes it', () => {
    const again = new WorldMap(seed.map((s) => ({ ...s })));
    expect(JSON.stringify(again)).toBe(JSON.stringify(m));
    const moved = new WorldMap([seed[0]!, seed[1]!, { ...seed[2]!, latitude: 52.0 }]);
    expect(JSON.stringify(moved.doors)).not.toBe(JSON.stringify(m.doors));
  });
  it('the door marker sits inside the doorway, below the sign above the door', async () => {
    const { sprite } = await import('../src/game/world/engine');
    const mk = sprite('marker');
    expect(m.doors.length).toBe(3);
    for (const d of m.doors) {
      const o = m.objects.find((x) => x.kind === 'marker' && x.site === d.siteId)!;
      const top = o.y - mk.oy - 1, bottom = o.y - mk.oy + mk.h; // the bob lifts the marker by up to one pixel
      // every building's doorway spans 6 px above the door tile to 11 px into it (web/art/overworld/buildings.py door())
      expect(top).toBeGreaterThanOrEqual(d.y * 16 - 6);
      expect(bottom).toBeLessThanOrEqual(d.y * 16 + 11);
      expect(o.x).toBe(d.x * 16 + 8);
    }
  });
  it('unknown themes fall back to the plain building', () => {
    const w = new WorldMap([S(1, 52, 4, 'NL', 'western')]);
    expect(w.doors[0]!.theme).toBe('debug');
  });
  it('many sites in one city stay apart and all reachable', () => {
    const city = Array.from({ length: 12 }, (_, i) => S(i + 1, 52.37 + (i % 4) * 0.004, 4.89 + Math.floor(i / 4) * 0.004));
    const w = new WorldMap(city);
    expect(w.regions).toHaveLength(1);
    for (let i = 0; i < w.doors.length; i++)
      for (let j = i + 1; j < w.doors.length; j++) {
        const a = w.doors[i]!, b = w.doors[j]!;
        expect(Math.abs(a.bx - b.bx) >= a.bw + 2 || Math.abs(a.by - b.by) >= a.bh + 2).toBe(true);
      }
    for (const d of w.doors) expect(w.path(w.start, d).length).toBeGreaterThan(0);
  });
});
