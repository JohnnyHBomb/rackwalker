// The placeholder overworld: one building and one door per site, deterministic, every door reachable from the start,
// walks never pass through a door they do not aim at, leaving a building puts the player in front of its door.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { OverworldMap, STEP, dirTo } from '../src/game/overworld';

const world = JSON.parse(readFileSync(resolve(__dirname, '../../fixtures/world.json'), 'utf8'));
const sites = world.sites.map((w: { site: { id: number; name: string } }) => ({ id: w.site.id, name: w.site.name }));

describe('OverworldMap', () => {
  const m = new OverworldMap(sites);
  it('has one door per seeded site, in id order', () => {
    expect(m.doors.map((d) => d.siteId)).toEqual([1, 2, 3]);
    for (const d of m.doors) expect(m.at(d.x, d.y)).toBe('door');
  });
  it('is deterministic and independent of the site order', () => {
    const again = new OverworldMap([...sites].reverse());
    expect(again.doors).toEqual(m.doors);
    for (let y = 0; y < m.h; y++) for (let x = 0; x < m.w; x++) expect(again.at(x, y)).toBe(m.at(x, y));
  });
  it('is fenced by trees and the start tile is free', () => {
    for (let x = 0; x < m.w; x++) expect([m.walkable(x, 0), m.walkable(x, m.h - 1)]).toEqual([false, false]);
    expect(m.walkable(m.start.x, m.start.y)).toBe(true);
  });
  it('Ilatea stands on the grass near the start: not walkable, in reach from the tile beside her', () => {
    expect(m.at(m.guide.x, m.guide.y)).toBe('guide');
    expect(m.walkable(m.guide.x, m.guide.y)).toBe(false);
    expect(m.path(m.start, m.guide)).toEqual([]); // walks never go through her
    expect(m.nearGuide(m.start)).toBe(false);
    const beside = m.besideGuide(m.start)!;
    expect(m.nearGuide(beside)).toBe(true);
    expect(m.path(m.start, beside).length).toBe(1); // the free tile between the start and her
    expect(m.besideGuide(beside)).toEqual(beside);
    expect(m.nearGuide({ x: m.guide.x + 1, y: m.guide.y + 1 })).toBe(false); // diagonal is not in reach
  });
  it('every door is reachable from the start, and the walk ends on the door', () => {
    for (const d of m.doors) {
      const p = m.path(m.start, d);
      expect(p.length).toBeGreaterThan(0);
      expect(p[p.length - 1]).toEqual({ x: d.x, y: d.y });
      // no other door on the way
      for (const q of p.slice(0, -1)) expect(m.doorAt(q.x, q.y)).toBeNull();
      // each step is one tile
      let at = m.start;
      for (const q of p) {
        expect(Math.abs(q.x - at.x) + Math.abs(q.y - at.y)).toBe(1);
        at = q;
      }
    }
  });
  it('a tap on a building walks to its door', () => {
    const d = m.doors[1]!;
    const p = m.path(m.start, { x: d.bx, y: d.by });
    expect(p[p.length - 1]).toEqual({ x: d.x, y: d.y });
  });
  it('a tap on a tree goes nowhere', () => expect(m.path(m.start, { x: 0, y: 0 })).toEqual([]));
  it('leaving puts the player on the free tile in front of the door', () => {
    for (const d of m.doors) {
      const o = m.outside(d);
      expect(m.walkable(o.x, o.y)).toBe(true);
      expect(m.doorAt(o.x, o.y)).toBeNull();
      expect(o).toEqual({ x: d.x + STEP.S.x, y: d.y + STEP.S.y });
    }
  });
  it('clampPos refuses doors, walls and tiles off the map', () => {
    const d = m.doors[0]!;
    expect(m.clampPos({ x: d.x, y: d.y })).toEqual(m.start);
    expect(m.clampPos({ x: 999, y: 999 })).toEqual(m.start);
    expect(m.clampPos({ x: d.bx, y: d.by })).toEqual(m.start);
    expect(m.clampPos(m.outside(d))).toEqual(m.outside(d));
  });
  it('works for one site and for many', () => {
    expect(new OverworldMap([{ id: 9, name: 'Solo' }]).doors).toHaveLength(1);
    const many = new OverworldMap(Array.from({ length: 9 }, (_, i) => ({ id: i + 1, name: `S${i}` })));
    for (const d of many.doors) expect(many.path(many.start, d).length).toBeGreaterThan(0);
  });
  it('dirTo names the step', () => {
    expect([dirTo({ x: 1, y: 1 }, { x: 2, y: 1 }), dirTo({ x: 1, y: 1 }, { x: 0, y: 1 }), dirTo({ x: 1, y: 1 }, { x: 1, y: 0 }), dirTo({ x: 1, y: 1 }, { x: 1, y: 2 })]).toEqual(['E', 'W', 'N', 'S']);
  });
});
