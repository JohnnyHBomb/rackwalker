import { describe, expect, it } from 'vitest';
import { depthKey } from '../src/contracts/iso';
import { compareDepth, sortByDepth, type DepthItem, type DepthKind } from '../src/engine/depth';
import { buildRoom, buildSite, buildWorld } from '../src/engine/scene';
import { fixture } from './helpers';
import type { SiteScene, WorldResponse } from '../src/contracts/scene.gen';

const item = (kind: DepthKind, x: number, y: number, w = 1, h = 1, id = 0): { depth: DepthItem; name: string } => ({
  depth: { x, y, w, h, z: 0, id, kind },
  name: `${kind}@${x},${y}`,
});
const order = (xs: { depth: DepthItem; name: string }[]) => sortByDepth(xs).map((i) => i.name);

/** Grid ranges overlap (open intervals; a zero-thickness wall counts if it lies strictly inside). */
const overlap = (a0: number, a1: number, b0: number, b1: number) => a0 < b1 && b0 < a1;
/** A must be drawn before B: A lies wholly behind B along x or y, and they share a range on the other axis. */
function behind(a: DepthItem, b: DepthItem): boolean {
  return (a.x + a.w <= b.x && overlap(a.y, a.y + a.h, b.y, b.y + b.h) && !(a.h === 0 && b.h === 0)) ||
    (a.y + a.h <= b.y && overlap(a.x, a.x + a.w, b.x, b.x + b.w) && !(a.w === 0 && b.w === 0));
}
/** Standing sprites overlap on screen when their screen-x spans overlap ((x - y) * 16 ranges). */
function screenOverlap(a: DepthItem, b: DepthItem): boolean {
  const span = (d: DepthItem) => [(d.x - d.y - d.h) * 16, (d.x + d.w - d.y) * 16] as const;
  const [a0, a1] = span(a);
  const [b0, b1] = span(b);
  return a0 < b1 && b0 < a1;
}
function violations(items: DepthItem[]): string[] {
  const sorted = [...items].sort(compareDepth);
  const out: string[] = [];
  for (let i = 0; i < sorted.length; i++)
    for (let j = 0; j < i; j++) {
      const later = sorted[j]!; // drawn earlier
      const earlier = sorted[i]!; // drawn later
      if (behind(earlier, later) && screenOverlap(earlier, later)) out.push(`${JSON.stringify(earlier)} should precede ${JSON.stringify(later)}`);
    }
  return out;
}
function rng(seed: number) {
  return () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
}

describe('depth order (contract tuple + tie-breaks)', () => {
  it('follows (depthKey, x, z, id) where those decide', () => {
    const a = item('rack', 2, 0).depth;
    const b = item('rack', 0, 3).depth;
    expect(depthKey(2, 0)).toBeLessThan(depthKey(0, 3));
    expect(compareDepth(a, b)).toBeLessThan(0);
    const c = item('rack', 1, 1).depth; // same key as (2,0), smaller x
    expect(compareDepth(c, a)).toBeLessThan(0);
    expect(compareDepth({ ...a, z: 0 }, { ...a, z: 8 })).toBeLessThan(0);
    expect(compareDepth({ ...a, id: 1 }, { ...a, id: 2 })).toBeLessThan(0);
  });

  it('racks: a rack directly in front (+x or +y) draws after the one behind it', () => {
    expect(order([item('rack', 1, 1), item('rack', 0, 1)])).toEqual(['rack@0,1', 'rack@1,1']);
    expect(order([item('rack', 1, 1), item('rack', 1, 0)])).toEqual(['rack@1,0', 'rack@1,1']);
  });

  it('walls: the N and W walls of a room draw before the racks standing against them', () => {
    // N wall on tile (3,0) is footprint 1x0; W wall on tile (0,2) is 0x1; racks stand on the edge tiles.
    expect(order([item('rack', 3, 0), item('wall', 3, 0, 1, 0)])).toEqual(['wall@3,0', 'rack@3,0']);
    expect(order([item('rack', 0, 2), item('wall', 0, 2, 0, 1)])).toEqual(['wall@0,2', 'rack@0,2']);
    // the corner tile: both walls, then the rack
    expect(order([item('rack', 0, 0), item('wall', 0, 0, 0, 1), item('wall', 0, 0, 1, 0)]).at(-1)).toBe('rack@0,0');
  });

  it('a rack outside a room right behind its N wall draws before that wall', () => {
    // rack on tile (4, -1) is behind the N wall on the edge between (4,-1) and (4,0)
    expect(order([item('wall', 4, 0, 1, 0), item('rack', 4, -1)])).toEqual(['rack@4,-1', 'wall@4,0']);
  });

  it('tie on (depthKey, x, z): a 2x2 building and a 1x1 object in front of its +y side', () => {
    // building (0,0) 2x2 and rack (0,2): both depthKey 2, both x 0 -> the near-corner tie-break decides
    expect(depthKey(0, 0, 2, 2)).toBe(depthKey(0, 2));
    expect(order([item('rack', 0, 2, 1, 1, 1), item('building', 0, 0, 2, 2, 9)])).toEqual(['building@0,0', 'rack@0,2']);
  });

  it('random layouts of racks and room walls never draw a front object before an overlapping back one', () => {
    const r = rng(7);
    for (let trial = 0; trial < 150; trial++) {
      const used = new Set<string>();
      const items: DepthItem[] = [];
      const W = 3 + Math.floor(r() * 8), H = 3 + Math.floor(r() * 8);
      for (let i = 0; i < W; i++) items.push({ x: i, y: 0, w: 1, h: 0, z: 0, id: 1, kind: 'wall' });
      for (let j = 0; j < H; j++) items.push({ x: 0, y: j, w: 0, h: 1, z: 0, id: 1, kind: 'wall' });
      for (let k = 0; k < 30; k++) {
        const x = Math.floor(r() * (W + 3)) - 2, y = Math.floor(r() * (H + 3)) - 2;
        if (used.has(`${x},${y}`)) continue;
        used.add(`${x},${y}`);
        items.push({ x, y, w: 1, h: 1, z: 0, id: k + 1, kind: r() < 0.8 ? 'rack' : 'loose' });
      }
      expect(violations(items)).toEqual([]);
    }
  });

  it('random layouts of 2x2 buildings and 1x1 objects are ordered correctly', () => {
    const r = rng(11);
    for (let trial = 0; trial < 150; trial++) {
      const used = new Set<string>();
      const items: DepthItem[] = [];
      for (let k = 0; k < 25; k++) {
        const big = r() < 0.5;
        const x = Math.floor(r() * 12), y = Math.floor(r() * 12), s = big ? 2 : 1;
        const tiles = [];
        for (let i = 0; i < s; i++) for (let j = 0; j < s; j++) tiles.push(`${x + i},${y + j}`);
        if (tiles.some((t) => used.has(t))) continue;
        tiles.forEach((t) => used.add(t));
        items.push({ x, y, w: s, h: s, z: 0, id: k, kind: big ? 'building' : 'rack' });
      }
      expect(violations(items)).toEqual([]);
    }
  });

  it('the fixture scenes are drawn in a valid order (world, every site, every room)', () => {
    const scenes = [buildWorld(fixture<WorldResponse>('world.json'))];
    for (const id of [1, 2, 3]) {
      const s = fixture<SiteScene>(`site-${id}.json`);
      scenes.push(buildSite(s));
      for (const l of s.locations) scenes.push(buildRoom(s, l.id));
    }
    for (const sc of scenes) {
      const objs = sc.items.filter((i) => i.layer === 'object').map((i) => i.depth);
      expect(violations(objs), sc.level).toEqual([]);
      // ground (floors, wires) always precedes every standing object
      const firstObject = sc.items.findIndex((i) => i.layer === 'object');
      if (firstObject >= 0) expect(sc.items.slice(firstObject).every((i) => i.layer === 'object')).toBe(true);
    }
  });
});

describe('object outline (selection and trace mark at iso levels)', () => {
  it('outlineBuffer: white pixels around the opaque ones, 1 px larger each side, nothing on the sprite itself', async () => {
    const { outlineBuffer } = await import('../src/engine/render');
    // a 2 x 1 opaque bar
    const b = { width: 2, height: 1, data: new Uint8ClampedArray([9, 9, 9, 255, 9, 9, 9, 255]) };
    const o = outlineBuffer(b);
    expect([o.width, o.height]).toEqual([4, 3]);
    const on = (x: number, y: number) => o.data[(y * 4 + x) * 4 + 3] === 255;
    const rows = [0, 1, 2].map((y) => [0, 1, 2, 3].map((x) => (on(x, y) ? '#' : '.')).join(''));
    expect(rows).toEqual(['.##.', '#..#', '.##.']);
  });
});
