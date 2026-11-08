// Deterministic auto-layout of objects without iso coordinates (contracts/iso.md "Grids and what iso_x / iso_y
// mean"). Web-only: nothing computed here is written back to NetBox.
import type { Facing } from '../contracts/iso';
import type { SiteScene } from '../contracts/scene.gen';

export interface RoomBox {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
  readonly facing: Facing;
  readonly auto: boolean;
}

export interface GridSpot {
  /** Location whose grid the tile is on (origin = the room's top corner); null = the site grid. */
  readonly locationId: number | null;
  readonly x: number;
  readonly y: number;
  readonly auto: boolean;
}

export interface RackSpot extends GridSpot {
  readonly facing: Facing;
}

export interface SiteLayout {
  readonly locations: ReadonlyMap<number, RoomBox>;
  readonly racks: ReadonlyMap<number, RackSpot>;
  /** Devices with rack_id null (not in the contract's table: packed after the racks with the same rules). */
  readonly loose: ReadonlyMap<number, GridSpot>;
}

/** Row width of the site-grid packing for racks (and loose devices) without a location. */
export const SITE_ROW_TILES = 8;
/** Size assumed for a location with null iso_w / iso_h. */
export const DEFAULT_ROOM_TILES = 4;

const placed = (o: { iso_x: number | null; iso_y: number | null }) => o.iso_x !== null && o.iso_y !== null;

/** (name, id) order with a locale-independent string compare, so the layout is the same in every browser. */
export function byNameId<T extends { name: string | null; id: number }>(a: T, b: T): number {
  const an = a.name ?? '';
  const bn = b.name ?? '';
  return an < bn ? -1 : an > bn ? 1 : a.id - b.id;
}

/** Fills tiles row by row (y, then x in [0, width)) from row `startY`, skipping `taken`; marks what it uses. */
function rowFiller(width: number, startY: number, taken: Set<string>) {
  let i = 0;
  return (): { x: number; y: number } => {
    for (;;) {
      const x = i % width;
      const y = startY + Math.floor(i / width);
      i++;
      const k = `${x},${y}`;
      if (!taken.has(k)) {
        taken.add(k);
        return { x, y };
      }
    }
  };
}

export function layoutSite(scene: Pick<SiteScene, 'locations' | 'racks' | 'devices'>): SiteLayout {
  const locations = new Map<number, RoomBox>();
  const racks = new Map<number, RackSpot>();
  const loose = new Map<number, GridSpot>();
  const locIds = new Set(scene.locations.map((l) => l.id));
  const gridOf = (locationId: number | null) => (locationId !== null && locIds.has(locationId) ? locationId : null);

  // Placed objects on the site grid fix "the lowest placed object".
  let bottom = 0;
  for (const l of scene.locations) {
    if (!placed(l)) continue;
    const box: RoomBox = {
      x: l.iso_x!,
      y: l.iso_y!,
      w: l.iso_w ?? DEFAULT_ROOM_TILES,
      h: l.iso_h ?? DEFAULT_ROOM_TILES,
      facing: l.iso_facing ?? 'S',
      auto: false,
    };
    locations.set(l.id, box);
    bottom = Math.max(bottom, box.y + box.h);
  }
  for (const r of scene.racks) if (placed(r) && gridOf(r.location_id) === null) bottom = Math.max(bottom, r.iso_y! + 1);

  // Unplaced locations: one row below the lowest placed object, 1 tile apart.
  let nextX = 0;
  for (const l of scene.locations.filter((l) => !placed(l)).sort(byNameId)) {
    const box: RoomBox = { x: nextX, y: bottom, w: l.iso_w ?? DEFAULT_ROOM_TILES, h: l.iso_h ?? DEFAULT_ROOM_TILES, facing: l.iso_facing ?? 'S', auto: true };
    locations.set(l.id, box);
    nextX += box.w + 1;
  }
  // Racks (and then loose devices) without a location start below everything on the site grid, including the
  // locations just auto-placed, so the two packings never collide.
  let siteBottom = 0;
  for (const b of locations.values()) siteBottom = Math.max(siteBottom, b.y + b.h);
  for (const r of scene.racks) if (placed(r) && gridOf(r.location_id) === null) siteBottom = Math.max(siteBottom, r.iso_y! + 1);

  // Tiles taken per grid (key: location id or 'site').
  const taken = new Map<number | 'site', Set<string>>();
  const takenOn = (g: number | null) => {
    const k = g ?? 'site';
    if (!taken.has(k)) taken.set(k, new Set());
    return taken.get(k)!;
  };
  for (const r of scene.racks) {
    if (!placed(r)) continue;
    const g = gridOf(r.location_id);
    racks.set(r.id, { locationId: g, x: r.iso_x!, y: r.iso_y!, facing: r.iso_facing ?? 'S', auto: false });
    takenOn(g).add(`${r.iso_x},${r.iso_y}`);
  }
  const fillers = new Map<number | 'site', () => { x: number; y: number }>();
  const fill = (g: number | null) => {
    const k = g ?? 'site';
    if (!fillers.has(k)) {
      fillers.set(k, g === null ? rowFiller(SITE_ROW_TILES, siteBottom, takenOn(null)) : rowFiller(locations.get(g)!.w, 0, takenOn(g)));
    }
    return fillers.get(k)!();
  };
  for (const r of scene.racks.filter((r) => !placed(r)).sort(byNameId)) {
    const g = gridOf(r.location_id);
    racks.set(r.id, { locationId: g, ...fill(g), facing: r.iso_facing ?? 'S', auto: true });
  }
  for (const d of scene.devices.filter((d) => d.rack_id === null).sort(byNameId)) {
    const g = gridOf(d.location_id);
    loose.set(d.id, { locationId: g, ...fill(g), auto: true });
  }
  return { locations, racks, loose };
}

/** Site-grid tile of a spot (adds the room origin for spots on a location grid). */
export function siteTile(layout: SiteLayout, spot: GridSpot): { x: number; y: number } {
  if (spot.locationId === null) return { x: spot.x, y: spot.y };
  const room = layout.locations.get(spot.locationId)!;
  return { x: room.x + spot.x, y: room.y + spot.y };
}

