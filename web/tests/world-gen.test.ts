// GPS-scaled regions: clustering by distance, compressed walking scale, paths between the sites of a region, water
// crossed by bridges, collisions, the airfield placeholder between regions, and the same data giving the same map.
import { describe, expect, it } from 'vitest';
import { CLAMP_REACH, clusterSites, compress, haversineKm, REGION_KM, WorldMap, type Pos, type WorldSite } from '../src/game/world/gen';

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
    // ... also when the lowest-id site itself has no GPS: they join the lowest-id site that has
    expect(clusterSites([S(1, null, null), S(2, 52, 0), S(3, 40, 20)])).toEqual([[1, 2], [3]]);
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

// The seed sites again, with the region of sites 1 and 3 in the Netherlands (a canal) instead of a pond.
const seedNL = seed.map((x) => ({ ...x, country: 'NL' }));
const tileOf = (o: { x: number; y: number }) => ({ x: Math.floor(o.x / 16), y: Math.floor(o.y / 16) });

describe.each([['GB (a pond)', seed], ['NL (a canal)', seedNL]])('collisions, %s', (_, sites) => {
  const m = new WorldMap(sites);
  it('buildings block except their door; water and fields block; paths and bridges are walkable', () => {
    for (const d of m.doors)
      for (let y = d.by; y < d.by + d.bh; y++) for (let x = d.bx; x < d.bx + d.bw; x++) expect(m.walkable(x, y)).toBe(x === d.x && y === d.y);
    let water = 0;
    for (let y = 0; y < m.h; y++)
      for (let x = 0; x < m.w; x++) {
        const t = m.at(x, y)!;
        if (t === 'W') water++;
        if (t === 'W' || t === 'F' || t === 'C') expect(m.walkable(x, y), `${t} at ${x},${y}`).toBe(false);
        if (t === 'B' || t === 'P') expect(m.walkable(x, y), `${t} at ${x},${y}`).toBe(true);
      }
    expect(water).toBeGreaterThan(10);
  });
  it('trees, villagers, landmarks, fences, the airfield sign and mooring posts block their tile', () => {
    const solids = m.objects.filter((o) => (o.kind === 'villager' || (o.kind === 'tree' && o.sprite !== 'bush') || (o.kind === 'landmark' && !o.top)
      || o.sprite === 'fence_h' || o.sprite === 'airfield_sign' || o.sprite === 'mooring_post'));
    for (const k of ['landmark', 'villager', 'tree']) expect(solids.some((o) => o.kind === k)).toBe(true);
    for (const n of ['fence_h', 'airfield_sign']) expect(solids.some((o) => o.sprite === n)).toBe(true);
    for (const o of solids) {
      const t = tileOf(o);
      expect(m.walkable(t.x, t.y), `${o.sprite} at ${t.x},${t.y}`).toBe(false);
    }
    // the bottom fence of every field closes the field's south side
    for (const o of m.objects.filter((o) => o.sprite === 'fence_h')) expect(m.walkable(tileOf(o).x, tileOf(o).y)).toBe(false);
  });
  it('bridges run straight north-south across water', () => {
    let bridges = 0;
    for (let y = 0; y < m.h; y++)
      for (let x = 0; x < m.w; x++)
        if (m.at(x, y) === 'B') {
          bridges++;
          expect(['P', 'B']).toContain(m.at(x, y - 1));
          expect(['P', 'B']).toContain(m.at(x, y + 1));
        }
    if (sites === seedNL) expect(bridges).toBeGreaterThan(0);
  });
  it('a walk never crosses an airfield pad it does not aim at', () => {
    for (const r of m.regions) {
      const a = r.airfield!;
      const p = m.path({ x: a.x - 1, y: a.y }, { x: a.x + 1, y: a.y });
      expect(p.length).toBeGreaterThan(2);
      for (const q of p) expect(m.isWarp(q.x, q.y)).toBe(false);
    }
  });
});

/** Every tile reachable from an arrival tile, walked with the player's own rule (doors and pads end a walk). */
function reached(m: WorldMap): Set<number> {
  const seen = new Set<number>();
  for (const r of m.regions) {
    const q: Pos[] = [r.arrive];
    while (q.length) {
      const p = q.pop()!;
      const k = p.y * m.w + p.x;
      if (seen.has(k) || !m.walkable(p.x, p.y)) continue;
      seen.add(k);
      if (m.doorAt(p.x, p.y) || m.isWarp(p.x, p.y)) continue;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) q.push({ x: p.x + dx!, y: p.y + dy! });
    }
  }
  return seen;
}

describe('a saved place after a NetBox edit', () => {
  const before = new WorldMap(seed);
  const edits: [string, WorldSite[]][] = [
    ['a site added', [...seed, S(4, 53.9, -1.1, 'GB')]],
    ['a site\'s country changed (the pond becomes a canal)', [seed[0]!, { ...seed[1]!, country: 'NL' }, { ...seed[2]!, country: 'NL' }]],
    ['a site\'s GPS cleared', [seed[0]!, seed[1]!, { ...seed[2]!, latitude: null, longitude: null }]],
    ['a site deleted', [seed[0]!, seed[1]!]],
  ];
  it.each(edits)('%s: every place the player could stand is clamped to a place from which every door can be reached', (_, after) => {
    const m = new WorldMap(after);
    const ok = reached(m);
    for (const k of reached(before)) {
      const p = m.clampPos({ x: k % before.w, y: Math.floor(k / before.w) });
      expect(ok.has(p.y * m.w + p.x), `${k % before.w},${Math.floor(k / before.w)} -> ${p.x},${p.y}`).toBe(true);
      expect(m.doorAt(p.x, p.y)).toBeNull();
      expect(m.isWarp(p.x, p.y)).toBe(false);
      for (const d of m.doors.filter((d) => d.region === m.regionAt(p).index)) expect(m.path(p, d).length).toBeGreaterThan(0);
    }
  });
  it('a walled-in tile moves to the nearest free tile of its region, not back to the start', () => {
    const m = new WorldMap(seedNL);
    const ok = reached(m);
    let pocket: Pos | null = null;
    for (let y = 0; y < m.h && !pocket; y++) for (let x = 0; x < m.w && !pocket; x++) if (m.walkable(x, y) && !ok.has(y * m.w + x)) pocket = { x, y };
    expect(pocket).not.toBeNull();
    expect(m.reachable(pocket!.x, pocket!.y)).toBe(false);
    const p = m.clampPos(pocket!);
    expect(m.standable(p.x, p.y)).toBe(true);
    expect(Math.abs(p.x - pocket!.x) + Math.abs(p.y - pocket!.y)).toBeLessThanOrEqual(CLAMP_REACH);
    expect(m.regionAt(p).index).toBe(m.regionAt(pocket!).index);
  });
});

describe('crowded and unusual site data', () => {
  const overlaps = (m: WorldMap) => {
    let n = 0;
    for (let i = 0; i < m.doors.length; i++)
      for (let j = i + 1; j < m.doors.length; j++) {
        const a = m.doors[i]!, b = m.doors[j]!;
        if (Math.abs(a.bx - b.bx) < a.bw + 2 && Math.abs(a.by - b.by) < a.bh + 2) n++;
      }
    return n;
  };
  it('200 sites at one point: no building overlaps another, every door and the start can be reached, the region stays square', () => {
    const m = new WorldMap(Array.from({ length: 200 }, (_, i) => S(i + 1, 52, 4)));
    expect(overlaps(m)).toBe(0);
    const ok = reached(m);
    expect(m.standable(m.start.x, m.start.y)).toBe(true);
    for (const d of m.doors) expect(ok.has(d.y * m.w + d.x), `door of ${d.siteId}`).toBe(true);
    expect(m.w).toBeLessThan(260);
    expect(m.h).toBeLessThan(260);
  });
  it('200 sites without GPS stand in a block, not one long row', () => {
    const m = new WorldMap(Array.from({ length: 200 }, (_, i) => S(i + 1, null, null)));
    expect(overlaps(m)).toBe(0);
    expect(m.w).toBeLessThan(260);
    expect(m.h).toBeLessThan(260);
    const mixed = new WorldMap(Array.from({ length: 40 }, (_, i) => (i % 2 ? S(i + 1, null, null) : S(i + 1, 52 + i * 0.001, 4))));
    expect(overlaps(mixed)).toBe(0);
    expect(mixed.w).toBeLessThan(140);
  });
  it('sites either side of the 180th meridian are near each other, in the right east-west order', () => {
    const m = new WorldMap([S(1, -17.7, 179.95, 'FJ'), S(2, -17.7, -179.95, 'FJ')]);
    const ctl = new WorldMap([S(1, -17.7, 178.95, 'FJ'), S(2, -17.7, 179.05, 'FJ')]);
    expect(m.regions).toHaveLength(1);
    const [a, b] = [m.doorOf(1)!, m.doorOf(2)!];
    expect(b.x).toBeGreaterThan(a.x); // -179.95 is east of 179.95
    expect(m.regions[0]!.w).toBe(ctl.regions[0]!.w);
    expect(b.x - a.x).toBe(ctl.doorOf(2)!.x - ctl.doorOf(1)!.x);
  });
  it('a one-site canal region has a bridge, so the far bank can be reached', () => {
    const m = new WorldMap([S(1, 52, 4)]);
    expect(m.terrain).toContain('W');
    expect(m.terrain).toContain('B');
    const ok = reached(m);
    let walk = 0;
    for (let y = 0; y < m.h; y++) for (let x = 0; x < m.w; x++) if (m.walkable(x, y)) walk++;
    expect(ok.size / walk).toBeGreaterThan(0.95);
  });
  it('no path spur: a door its path reaches from the side has no lone paved tile two below it', () => {
    const paved = (m: WorldMap, x: number, y: number) => ['P', 'B'].includes(m.at(x, y) ?? '');
    let side = 0;
    for (const m of [new WorldMap(seed), new WorldMap(seedNL)])
      for (const d of m.doors) {
        if (!paved(m, d.x - 1, d.y + 1) && !paved(m, d.x + 1, d.y + 1)) continue;
        side++;
        const y = d.y + 2;
        if (paved(m, d.x, y)) expect(paved(m, d.x - 1, y) || paved(m, d.x + 1, y) || paved(m, d.x, y + 1), `door of ${d.siteId}`).toBe(true);
      }
    expect(side).toBeGreaterThan(0);
  });
});

describe('a region\'s country and theme', () => {
  const region = (sites: WorldSite[]) => new WorldMap(sites).regions[0]!;
  const near = (id: number, country: string | null, theme: string | null = null) => S(id, 52 + id * 0.01, 4, country, theme);
  it('the most common country wins', () => expect(region([near(1, 'GB'), near(2, 'NL'), near(3, 'NL')]).country).toBe('NL'));
  it('a tie goes to the alphabetically first', () => expect(region([near(1, 'NL'), near(2, 'GB')]).country).toBe('GB'));
  it('missing or malformed countries do not vote', () => {
    expect(region([near(1, 'GB'), near(2, null), near(3, 'XYZ'), near(4, '')]).country).toBe('GB');
    expect(region([near(1, null)]).country).toBe('');
  });
  it('the most common theme wins; unknown themes do not vote; none gives the plain ground', () => {
    expect(region([near(1, 'GB', 'farm'), near(2, 'GB', 'business'), near(3, 'GB', 'business')]).theme).toBe('business');
    expect(region([near(1, 'GB', 'farm'), near(2, 'GB', 'business')]).theme).toBe('business');
    expect(region([near(1, 'GB', 'western'), near(2, 'GB', 'farm'), near(3, 'GB', 'western')]).theme).toBe('farm');
    expect(region([near(1, 'GB')]).theme).toBe('debug');
  });
});

