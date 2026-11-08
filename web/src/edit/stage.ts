// What the staged ops look like before they are applied: a SceneSource wrapper that patches the proxy's scenes
// with the basket (so a dropped rack stays where it was dropped), and the two local validity checks a drag needs
// before the proxy's preview confirms it (free floor tile, free U range). Pure data, no DOM.
import { U_PX } from '../contracts/iso';
import type { ChangeOp, Device, DeviceDetail, IdOrRef, Meta, Rack, RackScene, SiteScene, WorldResponse } from '../contracts/scene.gen';
import type { SiteLayout } from '../engine/layout';
import { SceneSourceError, type SceneSource } from '../engine/source';
import type { Staged } from './basket';

type Fields = Record<string, unknown>;

export interface Stage {
  /** Field patches by "<kind>:<id>" (kinds as in update_fields; racks created in the basket have ids -1, -2, ...). */
  readonly patches: ReadonlyMap<string, Fields>;
  /** Devices the basket places, as the drag saw them. */
  readonly devices: ReadonlyMap<number, Device>;
  readonly newRacks: readonly Rack[];
  /** client_ref -> temporary (negative) rack id. */
  readonly refs: ReadonlyMap<string, number>;
}

export const EMPTY_STAGE: Stage = { patches: new Map(), devices: new Map(), newRacks: [], refs: new Map() };

/** Folds the basket into patches, in op order (later ops win, as in the proxy's plan). */
export function stageOf(entries: readonly Staged[]): Stage {
  const patches = new Map<string, Fields>();
  const devices = new Map<number, Device>();
  const newRacks: Rack[] = [];
  const refs = new Map<string, number>();
  const id = (v: IdOrRef | null | undefined): number | null => (typeof v === 'string' ? (refs.get(v.slice(1)) ?? null) : (v ?? null));
  const patch = (key: string, f: Fields) => patches.set(key, { ...patches.get(key), ...f });
  for (const { op, device, rackLocationId } of entries) {
    switch (op.op) {
      case 'move_rack': {
        const f: Fields = { ...op.fields };
        if ('location_id' in f) f.location_id = id(op.fields.location_id);
        patch(`rack:${id(op.target.id)}`, f);
        break;
      }
      case 'place_device':
        if (device) devices.set(op.target.id, device);
        patch(`device:${op.target.id}`, {
          rack_id: id(op.fields.rack_id),
          position: op.fields.position,
          face: op.fields.face,
          ...(rackLocationId !== undefined ? { location_id: rackLocationId } : {}),
        });
        break;
      case 'unplace_device':
        patch(`device:${op.target.id}`, { rack_id: null, position: null, face: null });
        break;
      case 'create_rack': {
        // The temporary id follows the op's client_ref ("newrack<n>" -> -n), not its position: an undo of an earlier
        // create_rack must not renumber the later ones (previously: an open staged rack then showed another one).
        const n = /^newrack(\d+)$/.exec(op.client_ref ?? '');
        const tid = n ? -Number(n[1]) : -(1_000_000 + newRacks.length);
        if (op.client_ref) refs.set(op.client_ref, tid);
        const f = op.fields;
        newRacks.push({
          id: tid,
          site_id: f.site_id,
          location_id: id(f.location_id),
          name: f.name,
          status: f.status ?? 'planned',
          u_height: f.u_height ?? 42,
          starting_unit: 1,
          desc_units: false,
          iso_x: f.iso_x ?? null,
          iso_y: f.iso_y ?? null,
          iso_facing: f.iso_facing ?? null,
          latitude: null,
          longitude: null,
          last_updated: null,
          netbox_url: null,
        });
        break;
      }
      case 'update_fields':
        patch(`${op.target.kind}:${id(op.target.id)}`, { ...op.fields });
        break;
    }
  }
  return { patches, devices, newRacks, refs };
}

/** `o` with the staged fields of "<kind>:<o.id>" (only fields the object has). */
export function patched<T extends { id: number }>(stage: Stage, kind: string, o: T): T {
  const p = stage.patches.get(`${kind}:${o.id}`);
  if (!p) return o;
  const out: Record<string, unknown> = { ...o };
  for (const [k, v] of Object.entries(p)) if (k in out) out[k] = v;
  return out as T;
}

const META = (): Meta => ({ contract_version: '2.1.0', generated_at: new Date().toISOString(), netbox_version: null, source: 'cache' });

/** The proxy's scenes with the basket applied on top. */
export class StagedSource implements SceneSource {
  constructor(
    private readonly inner: SceneSource,
    private readonly stage: () => Stage,
  ) {}

  async world(): Promise<WorldResponse> {
    const w = await this.inner.world();
    const st = this.stage();
    return { ...w, sites: w.sites.map((s) => ({ ...s, site: patched(st, 'site', s.site) })) };
  }

  async site(siteId: number): Promise<SiteScene> {
    const s = await this.inner.site(siteId);
    const st = this.stage();
    const have = new Set(s.devices.map((d) => d.id));
    const moved = [...st.devices.values()].filter((d) => !have.has(d.id) && d.site_id === siteId);
    return {
      ...s,
      site: patched(st, 'site', s.site),
      locations: s.locations.map((l) => patched(st, 'location', l)),
      racks: [...s.racks, ...st.newRacks.filter((r) => r.site_id === siteId)].map((r) => patched(st, 'rack', r)),
      devices: [...s.devices, ...moved].map((d) => patched(st, 'device', d)),
      cables: s.cables.map((c) => patched(st, 'cable', c)),
    };
  }

  async rack(rackId: number): Promise<RackScene> {
    const st = this.stage();
    let base: RackScene;
    if (rackId < 0) {
      const r = st.newRacks.find((x) => x.id === rackId);
      if (!r) throw new SceneSourceError(404, 'not_found', `Staged rack ${rackId} is no longer in the basket.`);
      base = { meta: META(), rack: r, devices: [], cables: [] };
    } else base = await this.inner.rack(rackId);
    const have = new Set(base.devices.map((d) => d.id));
    const devices = [...base.devices, ...[...st.devices.values()].filter((d) => !have.has(d.id))]
      .map((d) => patched(st, 'device', d))
      .filter((d) => d.rack_id === rackId);
    return { ...base, rack: patched(st, 'rack', base.rack), devices };
  }

  async device(deviceId: number): Promise<DeviceDetail> {
    const d = await this.inner.device(deviceId);
    const st = this.stage();
    const p = st.patches.get(`device:${deviceId}`) ?? {};
    const info = { ...d.info };
    for (const k of ['serial', 'asset_tag', 'description', 'comments'] as const) if (k in p) (info as Record<string, unknown>)[k] = p[k];
    return {
      ...d,
      device: patched(st, 'device', d.device),
      info,
      interfaces: d.interfaces.map((i) => (i.kind === 'interface' ? patched(st, 'interface', i) : i)),
      cables: d.cables.map((c) => patched(st, 'cable', c)),
    };
  }

  trace: SceneSource['trace'] = (kind, id) => this.inner.trace(kind, id);
}

// ------------------------------------------------------------------------------------------------ local checks

/** Why a rack cannot stand on tile (x, y) of a room, or null when the tile is free (other racks and unracked floor devices). */
export function tileProblem(scene: SiteScene, layout: SiteLayout, locationId: number, x: number, y: number, rackId: number | null): string | null {
  const box = layout.locations.get(locationId);
  if (!box) return 'not a room';
  if (x < 0 || y < 0 || x >= box.w || y >= box.h) return 'outside the room';
  for (const [id, spot] of layout.racks)
    if (id !== rackId && spot.locationId === locationId && spot.x === x && spot.y === y)
      return `tile taken by ${scene.racks.find((r) => r.id === id)?.name ?? `rack ${id}`}`;
  // Unracked devices standing on the floor take their tile too (what the room shows is what the check sees).
  for (const [id, spot] of layout.loose)
    if (spot.locationId === locationId && spot.x === x && spot.y === y)
      return `tile taken by ${scene.devices.find((d) => d.id === id)?.name ?? `device ${id}`}`;
  return null;
}

/** Why `device` cannot go to U `position` on `face` of `rack` (holding `devices`), or null when it fits. */
export function slotProblem(rack: Rack, devices: readonly Device[], device: Device, position: number, face: 'front' | 'rear'): string | null {
  const h = device.device_type.u_height;
  if (h === 0) return 'a zero-U device mounts on the rails, not in a U slot';
  if (position < rack.starting_unit || position + h > rack.starting_unit + rack.u_height) return `U${position} is outside ${rack.name}`;
  for (const o of devices) {
    if (o.id === device.id || o.position === null || o.device_type.u_height === 0) continue;
    const overlap = position < o.position + o.device_type.u_height && o.position < position + h;
    const sameSide = o.face === face || o.device_type.is_full_depth || device.device_type.is_full_depth;
    if (overlap && sameSide) return `U${position} overlaps ${o.name ?? `device ${o.id}`}`;
  }
  return null;
}

/** The U position whose faceplate top edge is nearest elevation y (level px): whole U for whole-U devices, else half U. */
export function positionAt(rack: Pick<Rack, 'u_height' | 'starting_unit' | 'desc_units'>, yTop: number, uHeight: number): number {
  const step = Number.isInteger(uHeight) ? 1 : 0.5;
  const units = Math.round(yTop / U_PX / step) * step;
  return rack.desc_units ? rack.starting_unit + units : rack.starting_unit + rack.u_height - units - uHeight;
}

/** First U (from the top) where the device fits, preferring `prefer`; null if the rack is full. */
export function freePosition(rack: Rack, devices: readonly Device[], device: Device, face: 'front' | 'rear', prefer: number | null): number | null {
  if (prefer !== null && !slotProblem(rack, devices, device, prefer, face)) return prefer;
  const h = device.device_type.u_height;
  for (let p = rack.starting_unit + rack.u_height - Math.max(1, Math.ceil(h)); p >= rack.starting_unit; p--)
    if (!slotProblem(rack, devices, device, p, face)) return p;
  return null;
}

export const NEXT_FACING = { N: 'E', E: 'S', S: 'W', W: 'N' } as const;

/** The optimistic lock for an op on an object read from NetBox (absent for objects created in the basket). */
export function lockOf(o: { id: number; last_updated: string | null }): Pick<ChangeOp, 'if_unmodified_since'> {
  return o.id > 0 && o.last_updated ? { if_unmodified_since: o.last_updated } : {};
}
