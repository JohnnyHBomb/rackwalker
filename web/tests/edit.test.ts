import { describe, expect, it } from 'vitest';
import type { ChangeOp, Device, RackScene, SiteScene } from '../src/contracts/scene.gen';
import { HttpChangeApi } from '../src/edit/api';
import { Basket, refsOf, type Staged } from '../src/edit/basket';
import { WHITELIST, fieldLabel, panelModel, parseInput, showField } from '../src/edit/fields';
import { createdRacks } from '../src/edit/editor';
import { StagedSource, freePosition, lockOf, positionAt, slotProblem, stageOf, tileProblem } from '../src/edit/stage';
import { layoutSite } from '../src/engine/layout';
import { roomRuns } from '../src/engine/scene';
import { SceneSourceError } from '../src/engine/source';
import { fixture, nodeFixtureSource } from './helpers';

const site1 = () => fixture<SiteScene>('site-1.json');
const rack1 = () => fixture<RackScene>('rack-1.json');
const dev = (s: SiteScene | RackScene, id: number) => s.devices.find((d) => d.id === id)!;
const move = (id: number | string, x: number, y: number): Staged => ({ op: { op: 'move_rack', target: { kind: 'rack', id }, fields: { iso_x: x, iso_y: y } }, label: `m${id}` });
const createRack = (ref: string, x = 8, y = 4): Staged => ({
  op: { op: 'create_rack', target: { kind: 'rack', id: null }, client_ref: ref, fields: { site_id: 1, location_id: 1, name: 'NEW-1', iso_x: x, iso_y: y } },
  label: `c${ref}`,
});

describe('basket', () => {
  it('keeps ops in order, one per action, and builds the ChangeSet (+ a candidate at the end)', () => {
    const b = new Basket();
    expect(b.changeSet()).toBeNull();
    b.add(move(2, 3, 4));
    b.add(move(2, 5, 4)); // a second move of the same rack is a second op: the proxy plans in order
    const cand: ChangeOp = { op: 'unplace_device', target: { kind: 'device', id: 3 }, fields: {} };
    expect(b.changeSet(cand, 'why')).toEqual({ ops: [b.entries[0]!.op, b.entries[1]!.op, cand], reason: 'why' });
    expect(b.size).toBe(2);
  });

  it('undo removes an op and, transitively, the ops that name an object it creates', () => {
    const b = new Basket();
    const ref = b.nextRef();
    expect(ref).toBe('newrack1');
    b.add(move(1, 0, 0));
    b.add(createRack(ref));
    b.add({ op: { op: 'place_device', target: { kind: 'device', id: 3 }, fields: { rack_id: `@${ref}`, position: 1, face: 'front' } }, label: 'p' });
    b.add({ op: { op: 'update_fields', target: { kind: 'rack', id: `@${ref}` }, fields: { name: 'X' } }, label: 'u' });
    b.add(move(2, 1, 1));
    let n = 0;
    b.onChange(() => n++);
    b.undo(1);
    expect(b.entries.map((s) => s.label)).toEqual(['m1', 'm2']);
    b.undoLast();
    expect(b.entries.map((s) => s.label)).toEqual(['m1']);
    b.undo(5); // out of range: no-op, no event
    expect(n).toBe(2);
    b.clear();
    expect(b.size).toBe(0);
  });

  it('refsOf reads target id, rack_id and location_id refs', () => {
    expect(refsOf({ op: 'move_rack', target: { kind: 'rack', id: '@a' }, fields: { location_id: '@b' } })).toEqual(['a', 'b']);
    expect(refsOf({ op: 'place_device', target: { kind: 'device', id: 1 }, fields: { rack_id: '@c', position: 1, face: 'front' } })).toEqual(['c']);
    expect(refsOf(move(1, 1, 1).op)).toEqual([]);
  });

  it('undo of an index outside the basket (Ctrl+Z on an empty basket) changes nothing and emits nothing', () => {
    const b = new Basket();
    let emits = 0;
    b.onChange(() => emits++);
    b.undoLast();
    b.undo(-1);
    b.undo(0);
    expect(emits).toBe(0);
    b.add(move(2, 3, 4));
    b.undo(-1);
    b.undo(1);
    expect([emits, b.size]).toEqual([1, 1]);
  });

  it('settleWritten drops the written ops and points the ops left at the racks they created', () => {
    // [create_rack @newrack1, device 4 description, device 4 asset_tag, place device 27 into @newrack1]: ops 0, 1
    // written (NetBox gave the new rack id 77), op 2 refused, op 3 skipped.
    const b = new Basket();
    b.add(createRack('newrack1'));
    b.add({ op: { op: 'update_fields', target: { kind: 'device', id: 4 }, fields: { description: 'x' } }, label: 'd' });
    b.add({ op: { op: 'update_fields', target: { kind: 'device', id: 4 }, fields: { asset_tag: 'dup' } }, label: 'a' });
    b.add({ op: { op: 'place_device', target: { kind: 'device', id: 27 }, fields: { rack_id: '@newrack1', position: 10, face: 'front' } }, label: 'p' });
    b.add({ op: { op: 'move_rack', target: { kind: 'rack', id: '@newrack1' }, fields: { iso_x: 2 } }, label: 'm' });
    b.settleWritten([0, 1], new Map([['newrack1', 77]]));
    expect(b.entries.map((e) => e.label)).toEqual(['a', 'p', 'm']);
    expect(b.entries[1]!.op.fields).toEqual({ rack_id: 77, position: 10, face: 'front' });
    expect(b.entries[2]!.op.target.id).toBe(77);
    expect(b.entries.flatMap((e) => refsOf(e.op))).toEqual([]);
    // the staged view now places device 27 in rack 77, not unracked
    expect(stageOf(b.entries).patches.get('device:27')).toMatchObject({ rack_id: 77 });
  });

  it('createdRacks reads the NetBox ids of written create_rack ops from the apply answer', () => {
    const r = {
      mode: 'apply', applied: false, write_enabled: true,
      ops: [
        { index: 0, op: 'create_rack', client_ref: 'newrack1', target: { kind: 'rack', id: 77 }, status: 'ok', before: null, after: null, diff: [], error: null },
        { index: 1, op: 'create_rack', client_ref: 'newrack2', target: { kind: 'rack', id: '@newrack2' }, status: 'error', before: null, after: null, diff: [], error: null },
      ],
    } as never;
    expect([...createdRacks(r, [0])]).toEqual([['newrack1', 77]]);
    expect([...createdRacks(r, [])]).toEqual([]);
  });

  it('removeIndexes drops ops a failed apply already wrote', () => {
    const b = new Basket();
    [1, 2, 3].forEach((i) => b.add(move(i, i, i)));
    b.removeIndexes([0, 2]);
    expect(b.entries.map((s) => s.label)).toEqual(['m2']);
  });
});

describe('staged view (StagedSource)', () => {
  it('moves racks, creates racks with temporary ids, and resolves @refs', async () => {
    const b = new Basket();
    b.add(move(2, 3, 4));
    b.add(createRack('newrack1'));
    b.add({ op: { op: 'update_fields', target: { kind: 'rack', id: '@newrack1' }, fields: { name: 'R-NEW' } }, label: 'u' });
    const st = stageOf(b.entries);
    expect(st.refs.get('newrack1')).toBe(-1);
    const src = new StagedSource(nodeFixtureSource(), () => st);
    const s = await src.site(1);
    expect(s.racks.find((r) => r.id === 2)).toMatchObject({ iso_x: 3, iso_y: 4 });
    expect(s.racks.find((r) => r.id === -1)).toMatchObject({ name: 'R-NEW', location_id: 1, iso_x: 8, iso_y: 4, u_height: 42, status: 'planned' });
    expect((await src.rack(-1)).rack.name).toBe('R-NEW');
    await expect(src.rack(-2)).rejects.toBeInstanceOf(SceneSourceError);
    // the fixture itself is untouched
    expect(site1().racks.find((r) => r.id === 2)).toMatchObject({ iso_x: 3, iso_y: 2 });
  });

  it('staged racks keep their temporary id when an earlier create_rack is undone', async () => {
    const b = new Basket();
    b.add(createRack(b.nextRef(), 1, 4));
    b.add(createRack(b.nextRef(), 2, 4));
    b.add(createRack(b.nextRef(), 3, 4));
    expect([...stageOf(b.entries).refs]).toEqual([['newrack1', -1], ['newrack2', -2], ['newrack3', -3]]);
    b.undo(0);
    const st = stageOf(b.entries);
    expect([...st.refs]).toEqual([['newrack2', -2], ['newrack3', -3]]);
    const src = new StagedSource(nodeFixtureSource(), () => st);
    expect((await src.rack(-2)).rack.iso_x).toBe(2);
    await expect(src.rack(-1)).rejects.toThrow(/no longer in the basket/);
  });

  it('a device placed into another rack leaves its old rack and appears in the new one (with the rack location)', async () => {
    const d3 = dev(rack1(), 3);
    const st = stageOf([{ op: { op: 'place_device', target: { kind: 'device', id: 3 }, fields: { rack_id: 5, position: 10, face: 'front' } }, label: 'p', device: d3, rackLocationId: 2 }]);
    const src = new StagedSource(nodeFixtureSource(), () => st);
    expect((await src.rack(1)).devices.map((d) => d.id)).not.toContain(3);
    const r5 = await src.rack(5);
    expect(r5.devices.find((d) => d.id === 3)).toMatchObject({ rack_id: 5, position: 10, face: 'front', location_id: 2 });
    expect((await src.site(1)).devices.find((d) => d.id === 3)).toMatchObject({ rack_id: 5, location_id: 2 });
  });

  it('unplace empties rack/position/face; update_fields patches DeviceInfo and interfaces too', async () => {
    const st = stageOf([
      { op: { op: 'unplace_device', target: { kind: 'device', id: 2 }, fields: {} }, label: 'x' },
      { op: { op: 'update_fields', target: { kind: 'device', id: 2 }, fields: { serial: 'S-1', name: 'core-x' } }, label: 'y' },
      { op: { op: 'update_fields', target: { kind: 'interface', id: 21 }, fields: { enabled: false } }, label: 'z' },
    ]);
    const d = await new StagedSource(nodeFixtureSource(), () => st).device(2);
    expect(d.device).toMatchObject({ rack_id: null, position: null, face: null, name: 'core-x' });
    expect(d.info.serial).toBe('S-1');
    expect(d.interfaces.find((p) => p.kind === 'interface' && p.id === 21)!.enabled).toBe(false);
  });
});

describe('drag validity (local, before the proxy confirms)', () => {
  const s = site1();
  const layout = layoutSite(s);
  it('tileProblem: inside the room, not on another rack (the dragged rack itself does not count)', () => {
    expect(tileProblem(s, layout, 1, 3, 4, 2)).toBeNull();
    expect(tileProblem(s, layout, 1, 5, 2, 2)).toBe('tile taken by ND-R3');
    expect(tileProblem(s, layout, 1, 3, 2, 2)).toBeNull();
    expect(tileProblem(s, layout, 1, 10, 0, 2)).toBe('outside the room');
    expect(tileProblem(s, layout, 1, -1, 0, 2)).toBe('outside the room');
    expect(tileProblem(s, layout, 99, 0, 0, 2)).toBe('not a room');
  });

  it('tileProblem: the room is 10 x 6 tiles, so row 5 is inside and row 6 outside (previously: y bound untested)', () => {
    expect(layout.locations.get(1)).toMatchObject({ w: 10, h: 6 });
    expect(tileProblem(s, layout, 1, 0, 5, 2)).toBeNull();
    expect(tileProblem(s, layout, 1, 0, 6, 2)).toBe('outside the room');
    expect(tileProblem(s, layout, 1, 0, -1, 2)).toBe('outside the room');
    expect(tileProblem(s, layout, 1, 9, 0, 2)).toBeNull();
  });

  it('tileProblem: racks and floor devices of other rooms do not take a tile here', () => {
    // ND-C1 stands at 1,1 of the Comms Cupboard (room 2); tile 1,1 of the Server Room is free.
    expect(layout.racks.get(5)).toMatchObject({ locationId: 2, x: 1, y: 1 });
    expect(tileProblem(s, layout, 1, 1, 1, 2)).toBeNull();
    // The Store's floor devices: the same tiles of the Server Room are free (none of them holds a Server Room rack).
    const spots = [...layout.loose].filter(([, g]) => g.locationId === 3);
    expect(spots.length).toBe(2);
    for (const [, g] of spots) {
      expect([...layout.racks.values()].some((r) => r.locationId === 1 && r.x === g.x && r.y === g.y)).toBe(false);
      expect(tileProblem(s, layout, 1, g.x, g.y, null)).toBeNull();
    }
  });

  it('tileProblem: an unracked device standing on the floor takes its tile (room 3, the Store)', () => {
    const spots = [...layout.loose].filter(([, g]) => g.locationId === 3);
    expect(spots.map(([id]) => id).sort()).toEqual([27, 28]);
    for (const [id, g] of spots) expect(tileProblem(s, layout, 3, g.x, g.y, null)).toBe(`tile taken by ${dev(s, id).name}`);
    expect(tileProblem(s, layout, 3, 5, 3, null)).toBeNull();
  });

  it('slotProblem: bounds, overlap, faces, full depth, zero-U', () => {
    const r = rack1();
    const rtr = dev(r, 3); // 1U, front, U36
    expect(slotProblem(r.rack, r.devices, rtr, 20, 'front')).toBeNull();
    expect(slotProblem(r.rack, r.devices, rtr, 36, 'front')).toBeNull(); // its own slot
    expect(slotProblem(r.rack, r.devices, rtr, 38, 'front')).toBe('U38 overlaps nd-core1');
    expect(slotProblem(r.rack, r.devices, rtr, 39, 'front')).toBe('U39 overlaps nd-core1'); // top U of the 2U core
    expect(slotProblem(r.rack, r.devices, rtr, 43, 'front')).toBe('U43 is outside ND-R1');
    expect(slotProblem(r.rack, r.devices, rtr, 0, 'front')).toBe('U0 is outside ND-R1');
    const half: Device = { ...rtr, id: 999, face: 'rear', device_type: { ...rtr.device_type, is_full_depth: false } };
    const others = r.devices.map((d) => (d.id === 3 ? { ...d, device_type: { ...d.device_type, is_full_depth: false } } : d));
    expect(slotProblem(r.rack, others, half, 36, 'rear')).toBeNull(); // half-depth front + half-depth rear share a U
    expect(slotProblem(r.rack, others, { ...half, device_type: { ...half.device_type, is_full_depth: true } }, 36, 'rear')).toBe('U36 overlaps nd-rtr1');
    const pdu = dev(r, 5);
    expect(slotProblem(r.rack, r.devices, pdu, 10, 'front')).toMatch(/zero-U/);
  });

  it('slotProblem: a full-depth occupant refuses a half-depth device on the other face', () => {
    const r = rack1();
    const rtr = dev(r, 3); // 1U, front, U36
    const halfRear: Device = { ...rtr, id: 999, face: 'rear', device_type: { ...rtr.device_type, is_full_depth: false } };
    const fullOccupant = r.devices.map((d) => (d.id === 3 ? { ...d, device_type: { ...d.device_type, is_full_depth: true } } : d));
    expect(slotProblem(r.rack, fullOccupant, halfRear, 36, 'rear')).toBe('U36 overlaps nd-rtr1');
    const halfOccupant = r.devices.map((d) => (d.id === 3 ? { ...d, device_type: { ...d.device_type, is_full_depth: false } } : d));
    expect(slotProblem(r.rack, halfOccupant, halfRear, 36, 'rear')).toBeNull();
  });

  it('slotProblem: the top U of the rack is inside, one above is outside', () => {
    const r = rack1(); // 42U from U1; U42 holds pp1
    const rtr = dev(r, 3);
    const empty = r.devices.filter((d) => d.position === null || d.position + d.device_type.u_height <= 42);
    expect(slotProblem(r.rack, empty, rtr, 42, 'front')).toBeNull();
    expect(slotProblem(r.rack, empty, rtr, 43, 'front')).toBe('U43 is outside ND-R1');
    expect(slotProblem(r.rack, empty, rtr, 1, 'front')).toBeNull();
    const twoU = { ...rtr, device_type: { ...rtr.device_type, u_height: 2 } };
    expect(slotProblem(r.rack, empty, twoU, 41, 'front')).toBeNull();
    expect(slotProblem(r.rack, empty, twoU, 42, 'front')).toBe('U42 is outside ND-R1');
  });

  it('positionAt inverts the elevation (ascending and descending units); whole-U devices snap to whole U', () => {
    const asc = { u_height: 42, starting_unit: 1, desc_units: false };
    expect(positionAt(asc, (42 - 20) * 8, 1)).toBe(20); // top edge of U20's row
    expect(positionAt(asc, (42 - 21) * 8 + 3, 2)).toBe(20); // 2U whose top row is U21
    expect(positionAt(asc, (42 - 20) * 8 + 3, 1)).toBe(20); // nearest whole U
    expect(positionAt(asc, 8 * 22 + 4, 0.5)).toBe(20); // a half-U device snaps to half units
    expect(positionAt(asc, 8 * 22 + 4, 1.5)).toBe(19); // top edge at U22.5 - 1.5U
    const desc = { u_height: 42, starting_unit: 1, desc_units: true };
    expect(positionAt(desc, 0, 1)).toBe(1);
    expect(positionAt(desc, 8 * 9, 2)).toBe(10);
  });

  it('freePosition prefers the given U, else the highest free one; null when full', () => {
    const r = rack1();
    const rtr = dev(r, 3);
    expect(freePosition(r.rack, r.devices, rtr, 'front', 20)).toBe(20);
    expect(freePosition(r.rack, r.devices, rtr, 'front', 38)).toBe(41); // U42 pp1, U41 free
    const tiny = { ...r.rack, u_height: 1 };
    expect(freePosition(tiny, [dev(r, 1)].map((d) => ({ ...d, position: 1 })), rtr, 'front', null)).toBeNull();
  });

  it('lockOf sends last_updated for NetBox objects only', () => {
    expect(lockOf({ id: 3, last_updated: '2026-11-08T00:00:00+00:00' })).toEqual({ if_unmodified_since: '2026-11-08T00:00:00+00:00' });
    expect(lockOf({ id: -1, last_updated: null })).toEqual({});
  });
});

describe('info panel field mapping', () => {
  it('rack: contract fields shown, whitelisted ones editable, netbox_url linked', () => {
    const r = site1().racks[0]!;
    const m = panelModel({ kind: 'rack', data: r });
    expect(m).toMatchObject({ title: 'ND-R1', editKind: 'rack', id: 1, netboxUrl: 'http://netbox.example.invalid/dcim/racks/1/' });
    const editable = m.rows.filter((x) => x.input).map((x) => x.field);
    expect(editable).toEqual(['name', 'status', 'iso_x', 'iso_y', 'iso_facing', 'latitude', 'longitude']);
    for (const f of editable) expect(WHITELIST.rack).toContain(f);
    expect(m.rows.find((x) => x.field === 'iso_facing')).toMatchObject({ input: 'choice', choices: [null, 'N', 'E', 'S', 'W'] });
    expect(m.rows.find((x) => x.field === 'status')).toMatchObject({ input: 'choice', choices: ['reserved', 'available', 'planned', 'active', 'deprecated'] });
    expect(m.rows.find((x) => x.field === 'u_height')!.input).toBeNull();
  });

  it('every editable row of every kind is on the api.md whitelist of its kind', () => {
    const s = site1();
    const d2 = fixture<{ info: Parameters<typeof panelModel>[1]; interfaces: { kind: string }[] }>('device-2.json');
    const ents = [
      { kind: 'site', data: s.site },
      { kind: 'location', data: s.locations[0]! },
      { kind: 'rack', data: s.racks[0]! },
      { kind: 'device', data: s.devices[1]! },
      { kind: 'cable', data: s.cables[0]! },
      ...fixture<{ interfaces: never[] }>('device-2.json').interfaces.map((p) => ({ kind: 'port', data: p })),
    ] as Parameters<typeof panelModel>[0][];
    for (const e of ents) {
      const m = panelModel(e, e.kind === 'device' ? d2.info : null, 'http://nb');
      for (const r of m.rows.filter((x) => x.input)) expect(WHITELIST[m.editKind!]).toContain(r.field);
    }
  });

  it('device panel adds DeviceInfo fields when loaded; ports link through the proxy-reported NetBox URL by kind', () => {
    const d2 = fixture<{ device: Device; info: Parameters<typeof panelModel>[1]; interfaces: Parameters<typeof panelModel>[0]['data'][] }>('device-2.json');
    const without = panelModel({ kind: 'device', data: d2.device });
    expect(without.rows.map((r) => r.field)).not.toContain('serial');
    const withInfo = panelModel({ kind: 'device', data: d2.device }, d2.info);
    expect(withInfo.rows.find((r) => r.field === 'serial')).toMatchObject({ input: 'text' });
    expect(withInfo.rows.find((r) => r.field === 'platform')).toMatchObject({ input: null });
    const ports = d2.interfaces as never as { kind: string; id: number }[];
    const iface = ports.find((p) => p.kind === 'interface')!;
    const m = panelModel({ kind: 'port', data: iface } as never, null, 'http://nb.example/');
    expect(m).toMatchObject({ editKind: 'interface', netboxUrl: `http://nb.example/dcim/interfaces/${iface.id}/` });
    const other = ports.find((p) => p.kind !== 'interface');
    if (other) expect(panelModel({ kind: 'port', data: other } as never, null, 'http://nb').editKind).toBeNull();
    expect(panelModel({ kind: 'port', data: iface } as never, null, null).netboxUrl).toBeNull(); // no health yet: no link
  });

  it('labels read as words and id fields show names (previously: "rack_id 3", "location_id 1")', () => {
    expect(['rack_id', 'location_id', 'asset_tag', 'u_height', 'iso_facing'].map(fieldLabel)).toEqual(['rack', 'location', 'asset tag', 'height (U)', 'iso facing']);
    const names = { rack: (id: number) => (id === 3 ? 'ND-R3' : null), location: (id: number) => (id === 1 ? 'Server Room' : null), site: () => null };
    expect(showField('rack_id', 3, names)).toBe('ND-R3');
    expect(showField('rack_id', 9, names)).toBe('rack 9');
    expect(showField('location_id', 1, names)).toBe('Server Room');
    expect(showField('rack_id', null, names)).toBe('—');
    expect(showField('position', 3, names)).toBe('3');
    const s = site1();
    const r = s.racks.find((x) => x.id === 3)!;
    const m = panelModel({ kind: 'rack', data: r }, null, null, names);
    const row = m.rows.find((x) => x.field === 'location_id')!;
    expect([row.label, row.value, row.display]).toEqual(['location', 1, 'Server Room']);
  });

  it('parseInput: numbers, nullable text, choices, bool; invalid -> undefined', () => {
    expect(parseInput('iso_x', 'number', ' 4 ')).toBe(4);
    expect(parseInput('iso_x', 'number', '4.5')).toBeUndefined();
    expect(parseInput('iso_x', 'number', '')).toBeNull();
    expect(parseInput('latitude', 'number', '51.5')).toBe(51.5);
    expect(parseInput('latitude', 'number', 'abc')).toBeUndefined();
    expect(parseInput('name', 'text', '')).toBeNull();
    expect(parseInput('description', 'text', '')).toBe('');
    expect(parseInput('iso_facing', 'choice', 'E')).toBe('E');
    expect(parseInput('iso_facing', 'choice', '')).toBeNull(); // the empty option of a nullable choice
    expect(parseInput('enabled', 'bool', false)).toBe(false);
  });
});

describe('room cables', () => {
  it('a cable to an unracked device of the room is drawn from its floor tile', () => {
    const s = site1();
    const store = 3; // nd-srv90 / nd-srv91 lie unracked in the Store
    const base = s.cables[0]!;
    const mk = (id: number, a: number, b: number) => ({
      ...base,
      id,
      a_terminations: [{ ...base.a_terminations[0], device_id: a }] as typeof base.a_terminations,
      b_terminations: [{ ...base.b_terminations[0], device_id: b }] as typeof base.b_terminations,
    });
    const scene = { ...s, cables: [mk(900, 27, 28)] };
    const runs = roomRuns(scene, layoutSite(scene), store);
    const layout = layoutSite(scene);
    expect(runs).toHaveLength(1);
    expect(runs[0]!.a).toEqual({ x: layout.loose.get(27)!.x, y: layout.loose.get(27)!.y });
    expect(runs[0]!.b).toEqual({ x: layout.loose.get(28)!.x, y: layout.loose.get(28)!.y });
    expect(roomRuns(scene, layoutSite(scene), 1)).toEqual([]); // not in the Server Room
  });
});

describe('HttpChangeApi', () => {
  const reply = (status: number, body: unknown) => async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  it('posts the ChangeSet as JSON and returns the ChangeResult', async () => {
    const seen: RequestInit[] = [];
    const preview = fixture('changes-preview.json');
    const api = new HttpChangeApi('', async (url, init) => {
      seen.push({ ...init!, body: init!.body });
      expect(url).toBe('/api/changes/preview');
      return reply(200, preview)();
    });
    const cs = fixture<Parameters<HttpChangeApi['preview']>[0]>('changes-request.json');
    expect(await api.preview(cs)).toEqual(preview);
    expect(seen[0]).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/json' } });
    expect(JSON.parse(String(seen[0]!.body))).toEqual(cs);
  });
  it('a 403 write_disabled becomes a SceneSourceError with that code; a network failure netbox_unreachable', async () => {
    const api = new HttpChangeApi('', reply(403, fixture('error-write-disabled.json')));
    await expect(api.apply({ ops: [move(1, 1, 1).op] })).rejects.toMatchObject({ status: 403, code: 'write_disabled' });
    const down = new HttpChangeApi('', async () => {
      throw new TypeError('fetch failed');
    });
    await expect(down.health()).rejects.toMatchObject({ code: 'netbox_unreachable' });
  });
});
