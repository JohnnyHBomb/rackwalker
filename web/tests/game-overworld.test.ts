// The overworld model on the fixture sites: one building and one door per site, deterministic, every door of a region
// reachable from its arrival tile, walks never pass through a door they do not aim at, leaving a building puts the
// player in front of its door, Ilatea stands beside the first site's door.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { OverworldMap, STEP, dirTo, type WorldSite } from '../src/game/overworld';

const world = JSON.parse(readFileSync(resolve(__dirname, '../../fixtures/world.json'), 'utf8'));
const sites: WorldSite[] = world.sites.map((w: { site: { id: number; name: string; latitude: number; longitude: number; iso_theme: string; country: string } }) => ({
  id: w.site.id, name: w.site.name, latitude: w.site.latitude, longitude: w.site.longitude, theme: w.site.iso_theme, country: w.site.country,
}));
const plain = (id: number): WorldSite => ({ id, name: `S${id}`, latitude: null, longitude: null, theme: null, country: null });

describe('OverworldMap', () => {
  const m = new OverworldMap(sites);
  it('has one door per seeded site, in id order, each in its theme', () => {
    expect(m.doors.map((d) => d.siteId)).toEqual([1, 2, 3]);
    expect(m.doors.map((d) => d.theme)).toEqual(['industrial-robotic', 'farm', 'business']);
    for (const d of m.doors) expect(m.walkable(d.x, d.y)).toBe(true);
  });
  it('is deterministic and independent of the site order', () => {
    const again = new OverworldMap([...sites].reverse());
    expect(again.doors).toEqual(m.doors);
    expect(again.terrain).toEqual(m.terrain);
    expect(again.objects).toEqual(m.objects);
    for (let y = 0; y < m.h; y++) for (let x = 0; x < m.w; x++) expect(again.walkable(x, y)).toBe(m.walkable(x, y));
  });
  it('each region is fenced by forest and the start tile is free', () => {
    for (const r of m.regions)
      for (let x = r.x0; x < r.x0 + r.w; x++) expect([m.walkable(x, r.y0), m.walkable(x, r.y0 + r.h - 1)]).toEqual([false, false]);
    expect(m.walkable(m.start.x, m.start.y)).toBe(true);
  });
  it('Ilatea stands beside the first site\'s door: not walkable, in reach from the start', () => {
    const d = m.doorOf(1)!;
    expect(m.guide).toEqual({ x: d.x - 1, y: d.y + 1 });
    expect(m.walkable(m.guide.x, m.guide.y)).toBe(false);
    expect(m.path(m.start, m.guide)).toEqual([]);
    const beside = m.besideGuide(m.start)!;
    expect(m.nearGuide(beside)).toBe(true);
    expect(m.besideGuide(beside)).toEqual(beside);
    expect(m.nearGuide({ x: m.guide.x + 1, y: m.guide.y + 1 })).toBe(false);
  });
  it('every door is reachable on foot from its region\'s arrival tile, one tile per step, no other door on the way', () => {
    for (const d of m.doors) {
      const from = m.regions[d.region]!.arrive;
      const p = from.x === d.x && from.y === d.y + 2 ? m.path(from, d) : m.path(from, d);
      expect(p.length).toBeGreaterThan(0);
      expect(p[p.length - 1]).toEqual({ x: d.x, y: d.y });
      for (const q of p.slice(0, -1)) expect(m.doorAt(q.x, q.y)).toBeNull();
      let at = from;
      for (const q of p) {
        expect(Math.abs(q.x - at.x) + Math.abs(q.y - at.y)).toBe(1);
        at = q;
      }
    }
  });
  it('a tap on a building walks to its door', () => {
    const d = m.doorOf(3)!;
    const p = m.path(m.start, { x: d.bx, y: d.by });
    expect(p[p.length - 1]).toEqual({ x: d.x, y: d.y });
  });
  it('a tap on the forest goes nowhere', () => expect(m.path(m.start, { x: 0, y: 0 })).toEqual([]));
  it('leaving puts the player on the free tile in front of the door', () => {
    for (const d of m.doors) {
      const o = m.outside(d);
      expect(m.walkable(o.x, o.y)).toBe(true);
      expect(m.doorAt(o.x, o.y)).toBeNull();
      expect(o).toEqual({ x: d.x + STEP.S.x, y: d.y + STEP.S.y });
    }
  });
  it('clampPos refuses doors, walls, airfield pads and tiles off the map', () => {
    const d = m.doors[0]!;
    expect(m.clampPos({ x: d.x, y: d.y })).toEqual(m.start);
    expect(m.clampPos({ x: 999, y: 999 })).toEqual(m.start);
    expect(m.clampPos({ x: d.bx, y: d.by })).toEqual(m.start);
    expect(m.clampPos(m.outside(d))).toEqual(m.outside(d));
    const a = m.regions[0]!.airfield!;
    expect(m.clampPos(a)).toEqual(m.start);
  });
  it('works for no site, one site and many without GPS', () => {
    expect(new OverworldMap([]).doors).toHaveLength(0);
    expect(new OverworldMap([plain(9)]).doors).toHaveLength(1);
    const many = new OverworldMap(Array.from({ length: 9 }, (_, i) => plain(i + 1)));
    expect(many.regions).toHaveLength(1);
    for (const d of many.doors) expect(many.path(many.start, d).length).toBeGreaterThan(0);
  });
  it('dirTo names the step', () => {
    expect([dirTo({ x: 1, y: 1 }, { x: 2, y: 1 }), dirTo({ x: 1, y: 1 }, { x: 0, y: 1 }), dirTo({ x: 1, y: 1 }, { x: 1, y: 0 }), dirTo({ x: 1, y: 1 }, { x: 1, y: 2 })]).toEqual(['E', 'W', 'N', 'S']);
  });
});
