import { describe, expect, it } from 'vitest';
import type { Device, Location, Rack, SiteScene } from '../src/contracts/scene.gen';
import { layoutSite, siteTile } from '../src/engine/layout';
import { fixture } from './helpers';

const loc = (id: number, name: string, p: Partial<Location> = {}): Location => ({
  id, site_id: 1, parent_id: null, name, slug: name.toLowerCase(), iso_x: null, iso_y: null, iso_w: null, iso_h: null,
  iso_facing: null, latitude: null, longitude: null, last_updated: null, netbox_url: null, ...p,
});
const rack = (id: number, name: string, location_id: number | null, p: Partial<Rack> = {}): Rack => ({
  id, site_id: 1, location_id, name, status: 'active', u_height: 42, starting_unit: 1, desc_units: false, iso_x: null, iso_y: null,
  iso_facing: null, latitude: null, longitude: null, last_updated: null, netbox_url: null, ...p,
});
const dev = (id: number, name: string, location_id: number | null): Device => ({
  id, name, role: { id: 1, slug: 'server', name: 'Server', color: '4caf50' },
  device_type: { id: 1, manufacturer: 'X', model: 'Y', slug: 'y', u_height: 1, is_full_depth: false },
  site_id: 1, location_id, rack_id: null, position: null, face: null, status: 'active', primary_ip: null, last_updated: null, netbox_url: null,
});

describe('auto-layout (iso.md rules)', () => {
  it('keeps every fixture coordinate as stored (no fixture object is unplaced)', () => {
    for (const id of [1, 2, 3]) {
      const s = fixture<SiteScene>(`site-${id}.json`);
      const l = layoutSite(s);
      for (const x of s.locations) expect(l.locations.get(x.id)).toMatchObject({ x: x.iso_x, y: x.iso_y, w: x.iso_w, h: x.iso_h, auto: false });
      for (const r of s.racks) expect(l.racks.get(r.id)).toMatchObject({ locationId: r.location_id, x: r.iso_x, y: r.iso_y, facing: r.iso_facing, auto: false });
    }
  });

  it('fills a room row by row (y then x) in (name, id) order, skipping placed racks, overflowing below iso_h', () => {
    const scene = {
      locations: [loc(1, 'Room', { iso_x: 2, iso_y: 3, iso_w: 3, iso_h: 1 })],
      racks: [
        rack(10, 'B', 1),
        rack(11, 'A', 1),
        rack(12, 'P', 1, { iso_x: 1, iso_y: 0 }), // placed in the middle of row 0
        rack(13, 'A', 1), // same name as 11: id breaks the tie
        rack(14, 'C', 1),
      ],
      devices: [],
    };
    const l = layoutSite(scene);
    const at = (id: number) => { const s = l.racks.get(id)!; return [s.x, s.y, s.auto]; };
    expect(at(11)).toEqual([0, 0, true]);
    expect(at(13)).toEqual([2, 0, true]); // skips (1,0)
    expect(at(10)).toEqual([0, 1, true]); // room full: overflow into row 1 (= iso_h)
    expect(at(14)).toEqual([1, 1, true]);
    expect(at(12)).toEqual([1, 0, false]);
    expect(siteTile(l, l.racks.get(14)!)).toEqual({ x: 3, y: 4 }); // room origin (2,3) + (1,1)
  });

  it('places locations without coordinates one row below the lowest placed object, 1 tile apart; null size = 4x4, facing S', () => {
    const scene = {
      locations: [loc(1, 'Placed', { iso_x: 0, iso_y: 0, iso_w: 5, iso_h: 2 }), loc(3, 'Zed', { iso_w: 2, iso_h: 3 }), loc(2, 'Alpha')],
      racks: [rack(9, 'Lone', null, { iso_x: 7, iso_y: 4 })], // a placed site-grid rack is lower: row 4 -> bottom 5
      devices: [],
    };
    const l = layoutSite(scene);
    expect(l.locations.get(2)).toEqual({ x: 0, y: 5, w: 4, h: 4, facing: 'S', auto: true });
    expect(l.locations.get(3)).toEqual({ x: 5, y: 5, w: 2, h: 3, facing: 'S', auto: true });
  });

  it('packs racks without a location on the site grid in rows of 8 below everything, skipping placed ones', () => {
    const racks = [rack(1, 'Placed', null, { iso_x: 0, iso_y: 3 })];
    for (let i = 0; i < 10; i++) racks.push(rack(100 + i, `R${i}`, null));
    const l = layoutSite({ locations: [loc(1, 'Room', { iso_x: 0, iso_y: 0, iso_w: 2, iso_h: 2 })], racks, devices: [] });
    // lowest object: the placed rack on row 3 -> start row 4
    expect([l.racks.get(100)!.x, l.racks.get(100)!.y]).toEqual([0, 4]);
    expect([l.racks.get(107)!.x, l.racks.get(107)!.y]).toEqual([7, 4]);
    expect([l.racks.get(108)!.x, l.racks.get(108)!.y]).toEqual([0, 5]);
    expect(l.racks.get(108)!.locationId).toBeNull();
  });

  it('a rack whose location is not in the scene goes on the site grid', () => {
    const l = layoutSite({ locations: [], racks: [rack(1, 'Orphan', 77)], devices: [] });
    expect(l.racks.get(1)).toMatchObject({ locationId: null, x: 0, y: 0 });
  });

  it('loose devices (rack_id null) pack after the racks in their room', () => {
    const l = layoutSite({
      locations: [loc(1, 'Room', { iso_x: 0, iso_y: 0, iso_w: 2, iso_h: 2 })],
      racks: [rack(1, 'R', 1)],
      devices: [dev(5, 'b', 1), dev(4, 'a', 1), dev(6, 'site-level', null)],
    });
    expect(l.loose.get(4)).toMatchObject({ locationId: 1, x: 1, y: 0 });
    expect(l.loose.get(5)).toMatchObject({ locationId: 1, x: 0, y: 1 });
    expect(l.loose.get(6)).toMatchObject({ locationId: null, x: 0, y: 2 });
  });

  it('is deterministic: input order does not change the result', () => {
    const base = {
      locations: [loc(1, 'R1', { iso_x: 0, iso_y: 0, iso_w: 3, iso_h: 3 }), loc(2, 'Q'), loc(3, 'Q')],
      racks: [rack(1, 'x', 1), rack(2, 'y', 1), rack(3, 'a', null), rack(4, 'b', 2), rack(5, 'a', 1)],
      devices: [dev(1, 'd', 1), dev(2, 'c', null)],
    };
    const a = layoutSite(base);
    const b = layoutSite({ locations: [...base.locations].reverse(), racks: [...base.racks].reverse(), devices: [...base.devices].reverse() });
    expect([...b.locations.entries()].sort()).toEqual([...a.locations.entries()].sort());
    expect([...b.racks.entries()].sort()).toEqual([...a.racks.entries()].sort());
    expect([...b.loose.entries()].sort()).toEqual([...a.loose.entries()].sort());
  });
});
