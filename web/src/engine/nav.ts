// Navigation between the five zoom levels: targets, what entering an object leads to, breadcrumbs, deep links.
import type { ZoomLevel } from '../contracts/iso';
import type { DeviceDetail, PortKind, RackScene, SiteScene, TraceResponse, WorldResponse } from '../contracts/scene.gen';
import type { Entity } from './entity';
import type { SceneSource } from './source';

export type Target =
  | { readonly level: 'world' }
  | { readonly level: 'site'; readonly siteId: number }
  | { readonly level: 'room'; readonly siteId: number; readonly locationId: number }
  | { readonly level: 'rack'; readonly rackId: number }
  | { readonly level: 'device'; readonly deviceId: number };

export interface Crumb {
  readonly target: Target;
  readonly name: string;
}

export const WORLD: Target = { level: 'world' };

/** Deep link form: world | site:<id> | room:<site>:<location> | rack:<id> | device:<id>. */
export function parseView(s: string | null): Target | null {
  if (!s) return null;
  const [level, a, b] = s.split(':');
  const n = (v: string | undefined) => (v !== undefined && /^\d+$/.test(v) ? Number(v) : null);
  if (level === 'world') return WORLD;
  if (level === 'site' && n(a) !== null) return { level, siteId: n(a)! };
  if (level === 'room' && n(a) !== null && n(b) !== null) return { level, siteId: n(a)!, locationId: n(b)! };
  if (level === 'rack' && n(a) !== null) return { level, rackId: n(a)! };
  if (level === 'device' && n(a) !== null) return { level, deviceId: n(a)! };
  return null;
}

export function formatView(t: Target): string {
  switch (t.level) {
    case 'world':
      return 'world';
    case 'site':
      return `site:${t.siteId}`;
    case 'room':
      return `room:${t.siteId}:${t.locationId}`;
    case 'rack':
      return `rack:${t.rackId}`;
    case 'device':
      return `device:${t.deviceId}`;
  }
}

export function sameTarget(a: Target, b: Target): boolean {
  return formatView(a) === formatView(b);
}

/** Where double-clicking an entity goes from a level; null = it has no level of its own (wires, ports). */
export function enterTarget(from: ZoomLevel, e: Entity): Target | null {
  switch (e.kind) {
    case 'site':
      return from === 'world' ? { level: 'site', siteId: e.data.id } : null;
    case 'location':
      return from === 'site' ? { level: 'room', siteId: e.data.site_id, locationId: e.data.id } : null;
    case 'rack':
      return from === 'site' || from === 'room' ? { level: 'rack', rackId: e.data.id } : null;
    case 'device':
      return from === 'device' ? null : { level: 'device', deviceId: e.data.id };
    default:
      return null;
  }
}

/** A SceneSource with a per-path memo (levels share reads: site and room use one SiteScene). */
export class CachedSource implements SceneSource {
  private readonly memo = new Map<string, Promise<unknown>>();
  constructor(readonly inner: SceneSource) {}
  /** Forget every memoised read (after an apply: the next read goes to the proxy again). */
  clear(): void {
    this.memo.clear();
  }
  private once<T>(key: string, f: () => Promise<T>): Promise<T> {
    let p = this.memo.get(key) as Promise<T> | undefined;
    if (!p) {
      p = f();
      p.catch(() => this.memo.delete(key));
      this.memo.set(key, p);
    }
    return p;
  }
  world(): Promise<WorldResponse> {
    return this.once('w', () => this.inner.world());
  }
  site(id: number): Promise<SiteScene> {
    return this.once(`s${id}`, () => this.inner.site(id));
  }
  rack(id: number): Promise<RackScene> {
    return this.once(`r${id}`, () => this.inner.rack(id));
  }
  device(id: number): Promise<DeviceDetail> {
    return this.once(`d${id}`, () => this.inner.device(id));
  }
  trace(kind: PortKind, id: number): Promise<TraceResponse> {
    return this.inner.trace(kind, id);
  }
}

/** The breadcrumb from the world down to a target (loads the scenes it needs through the source). */
export async function crumbsFor(t: Target, src: SceneSource): Promise<Crumb[]> {
  const world: Crumb = { target: WORLD, name: 'World' };
  const siteChain = async (siteId: number, locationId: number | null): Promise<Crumb[]> => {
    const s = await src.site(siteId);
    const out: Crumb[] = [world, { target: { level: 'site', siteId }, name: s.site.name }];
    const loc = locationId === null ? undefined : s.locations.find((l) => l.id === locationId);
    if (loc) out.push({ target: { level: 'room', siteId, locationId: loc.id }, name: loc.name });
    return out;
  };
  const rackChain = async (rackId: number): Promise<Crumb[]> => {
    const r = await src.rack(rackId);
    return [...(await siteChain(r.rack.site_id, r.rack.location_id)), { target: { level: 'rack', rackId }, name: r.rack.name }];
  };
  switch (t.level) {
    case 'world':
      return [world];
    case 'site':
      return siteChain(t.siteId, null);
    case 'room':
      return siteChain(t.siteId, t.locationId);
    case 'rack':
      return rackChain(t.rackId);
    case 'device': {
      const d = (await src.device(t.deviceId)).device;
      const up = d.rack_id !== null ? await rackChain(d.rack_id) : await siteChain(d.site_id, d.location_id);
      return [...up, { target: t, name: d.name ?? `device ${d.id}` }];
    }
  }
}
