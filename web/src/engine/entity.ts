// What picking returns: the contract object under the cursor, tagged with its kind.
import type { Cable, Device, Interface, Location, Rack, Site, WorldLink } from '../contracts/scene.gen';

export type Entity =
  | { readonly kind: 'site'; readonly data: Site }
  | { readonly kind: 'location'; readonly data: Location }
  | { readonly kind: 'rack'; readonly data: Rack }
  | { readonly kind: 'device'; readonly data: Device }
  | { readonly kind: 'cable'; readonly data: Cable }
  | { readonly kind: 'port'; readonly data: Interface }
  | { readonly kind: 'link'; readonly data: WorldLink };

export type EntityKind = Entity['kind'];

/** Stable identity of an entity within a scene ("rack:3", "port:interface:12", "link:1-2"). */
export function entityKey(e: Entity): string {
  switch (e.kind) {
    case 'port':
      return `port:${e.data.kind}:${e.data.id}`;
    case 'link':
      return `link:${e.data.a_site_id}-${e.data.b_site_id}`;
    default:
      return `${e.kind}:${e.data.id}`;
  }
}

/** A display name for the HUD. */
export function entityName(e: Entity): string {
  switch (e.kind) {
    case 'device':
      return e.data.name ?? `device ${e.data.id}`;
    case 'cable':
      return e.data.label ?? `cable ${e.data.id}`;
    case 'link':
      return e.data.circuits.map((c) => c.cid).join(', ') || `link ${e.data.a_site_id}-${e.data.b_site_id}`;
    default:
      return e.data.name;
  }
}
