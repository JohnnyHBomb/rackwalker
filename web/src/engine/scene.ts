// Level scenes: what each zoom level draws, as renderer-agnostic items in level space (scale 1). World, site and
// room levels are iso space (origin = grid origin); rack and device levels are rack space (1U = 8 px).
import { FACING_ROTATION, FACING_VECTOR, U_PX, WORLD_TILES, geoToWorld, type Facing, type ZoomLevel } from '../contracts/iso';
import type { Cable, Device, DeviceDetail, Interface, Location, PortKind, Rack, RackScene, SiteScene, WorldResponse } from '../contracts/scene.gen';
import { SLOT_SPECS, type SlotId, type SlotVariant } from '../contracts/theme';
import { sortByDepth, type DepthItem } from './depth';
import type { Entity } from './entity';
import { layoutSite, siteTile, type RackSpot, type SiteLayout } from './layout';
import { labelSize } from './font';
import { isoToScreen, rackUnitTop } from './projection';
import { RACK_FACE_ROWS, faceplateHeight, frontDevices, hexColor, isZeroU, looseBoxRows, type SpriteDesc } from './sprites';

export type Layer = 'ground' | 'object';

export interface Item {
  readonly sprite: SpriteDesc;
  /** Top-left of the sprite in level px. */
  readonly x: number;
  readonly y: number;
  readonly layer: Layer;
  /** Iso levels: painter's order key. Rack levels: unused (NO_DEPTH; items are already in draw order). */
  readonly depth: DepthItem;
  readonly entity: Entity | null;
  /** Ground tile this item stands on (iso levels), for selection rings. */
  readonly tile?: { x: number; y: number; w: number; h: number };
}

/**
 * A name drawn on the canvas (font.ts): never picked, drawn above the objects. (x, y) is the anchor in level px:
 * the plate's top edge at y, its left edge, centre or right edge at x (align); `scale` integer font px per level px.
 */
export interface Label {
  readonly text: string;
  readonly x: number;
  readonly y: number;
  readonly align: 'left' | 'center' | 'right';
  readonly scale: 1 | 2;
}

/** A label's plate box in level px. */
export function labelBox(l: Label): { x: number; y: number; w: number; h: number } {
  const s = labelSize(l.text);
  const w = s.w * l.scale;
  return { x: l.align === 'center' ? l.x - Math.floor(w / 2) : l.align === 'right' ? l.x - w : l.x, y: l.y, w, h: s.h * l.scale };
}

export interface LevelScene {
  readonly level: ZoomLevel;
  readonly space: 'iso' | 'rack';
  /** Draw order: ground items first, then objects, each layer depth-sorted. */
  readonly items: readonly Item[];
  /** Names on the canvas (previously: no names anywhere; every object had to be clicked to be found). */
  readonly labels: readonly Label[];
  /** Bounding box of everything, level px. */
  readonly bounds: { x: number; y: number; w: number; h: number };
}

const NO_DEPTH: DepthItem = { x: 0, y: 0, w: 0, h: 0, z: 0, id: 0, kind: 'floor' };

/** Places a world slot so its anchor lands on the ground point of tile (tx, ty). */
function at(slot: SlotId, tx: number, ty: number, mirror = false): { x: number; y: number } {
  const spec = SLOT_SPECS[slot];
  const p = isoToScreen(tx, ty);
  const ax = mirror ? spec.width - spec.anchor[0] : spec.anchor[0];
  return { x: p.sx - ax, y: p.sy - spec.anchor[1] };
}

function slotItem(
  slot: SlotId,
  variant: SlotVariant,
  tx: number,
  ty: number,
  depth: DepthItem,
  entity: Entity | null,
  layer: Layer,
  mirror = false,
): Item {
  const fp = SLOT_SPECS[slot].footprint ?? [1, 1];
  return { sprite: { kind: 'slot', slot, variant, mirror }, ...at(slot, tx, ty, mirror), layer, depth, entity, tile: { x: tx, y: ty, w: Math.max(1, fp[0]), h: Math.max(1, fp[1]) } };
}

function finish(level: ZoomLevel, space: 'iso' | 'rack', items: Item[], sizeOf: (i: Item) => { w: number; h: number }, labels: Label[] = []): LevelScene {
  const ordered = space === 'iso' ? [...sortByDepth(items.filter((i) => i.layer === 'ground')), ...sortByDepth(items.filter((i) => i.layer === 'object'))] : items;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const grow = (b: { x: number; y: number; w: number; h: number }) => {
    x0 = Math.min(x0, b.x);
    y0 = Math.min(y0, b.y);
    x1 = Math.max(x1, b.x + b.w);
    y1 = Math.max(y1, b.y + b.h);
  };
  for (const i of ordered) grow({ x: i.x, y: i.y, ...sizeOf(i) });
  for (const l of labels) grow(labelBox(l));
  const bounds = ordered.length ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : { x: 0, y: 0, w: 0, h: 0 };
  return { level, space, items: ordered, labels, bounds };
}

/** A label centred over (above) an iso item: rack and floor-device names at room level. */
function nameAbove(i: Item, text: string): Label {
  return { text, x: i.x + Math.floor(itemSize(i).w / 2), y: i.y - labelSize(text).h - 1, align: 'center', scale: 1 };
}

/** Size of an item's sprite without rendering it (from the manifest). */
export function itemSize(i: Item): { w: number; h: number } {
  const s = i.sprite;
  if (s.kind === 'faceplate') return { w: 64, h: faceplateHeight(s.device) };
  if (s.kind === 'rack') return { w: SLOT_SPECS.rack_empty.width, h: SLOT_SPECS.rack_empty.height };
  if (s.kind === 'loose') return { w: SLOT_SPECS.rack_empty.width, h: SLOT_SPECS.rack_empty.height - (RACK_FACE_ROWS - looseBoxRows(s.device)) };
  const spec = SLOT_SPECS[s.slot];
  return s.turn ? { w: spec.height, h: spec.width } : { w: spec.width, h: spec.height };
}

// ---------------------------------------------------------------------------------------------------------
// Floor wires: L-shaped runs (along x, then along y) between tiles, merged per tile into straight/corner/cross.

const XM = 1, XP = 2, YM = 4, YP = 8;

export interface WireTile {
  readonly x: number;
  readonly y: number;
  readonly slot: 'wire_straight' | 'wire_corner' | 'wire_cross';
  readonly rotation: 0 | 1 | 2 | 3;
  /** Lowest id of the runs crossing this tile (its colour and its pick result). */
  readonly runId: number;
}

/** Grid direction from a tile towards a neighbour (for lead stubs). */
export type WireDir = '-x' | '+x' | '-y' | '+y';
const DIR_BIT: Record<WireDir, number> = { '-x': XM, '+x': XP, '-y': YM, '+y': YP };

export interface WireRun {
  readonly id: number;
  readonly a: { x: number; y: number };
  readonly b: { x: number; y: number };
  /** Extra edge on tile a / tile b (a stub leading under the rack the run starts or ends at). */
  readonly leadA?: WireDir;
  readonly leadB?: WireDir;
  /** Route along y first, then x (default: x first). */
  readonly yFirst?: boolean;
}

/** The tiles of a run's L route, from a to b. */
export function wirePath(r: Pick<WireRun, 'a' | 'b' | 'yFirst'>): { x: number; y: number }[] {
  const out = [{ ...r.a }];
  let { x, y } = r.a;
  const stepX = () => {
    while (x !== r.b.x) out.push({ x: (x += Math.sign(r.b.x - x)), y });
  };
  const stepY = () => {
    while (y !== r.b.y) out.push({ x, y: (y += Math.sign(r.b.y - y)) });
  };
  if (r.yFirst) (stepY(), stepX());
  else (stepX(), stepY());
  return out;
}

/**
 * Picks each run's L (x first or y first) so it shares as few tiles as possible with the runs before it (id order;
 * x first on a tie). Tiles where `shared` is true (e.g. under a building both runs start from) do not count. A run
 * laid on top of another reads as a tee into it (previously: the North-West circuit seemed to join the
 * North-East link half way).
 */
export function untangleRuns<R extends WireRun>(runs: readonly R[], shared: (x: number, y: number) => boolean = () => false): (R & { yFirst: boolean })[] {
  const used = new Set<string>();
  const out: (R & { yFirst: boolean })[] = [];
  for (const r of [...runs].sort((p, q) => p.id - q.id)) {
    const cost = (yFirst: boolean) => wirePath({ ...r, yFirst }).filter((t) => !shared(t.x, t.y) && used.has(`${t.x},${t.y}`)).length;
    const yFirst = cost(true) < cost(false);
    const chosen = { ...r, yFirst };
    for (const t of wirePath(chosen)) used.add(`${t.x},${t.y}`);
    out.push(chosen);
  }
  return out;
}

/** Routes runs from tile a to tile b (x first, then y; `yFirst` the other way) and returns one sprite choice per tile, sorted by (y, x). */
export function routeWires(runs: readonly WireRun[]): WireTile[] {
  const tiles = new Map<string, { x: number; y: number; mask: number; runId: number }>();
  const touch = (x: number, y: number, bit: number, id: number) => {
    const k = `${x},${y}`;
    const t = tiles.get(k) ?? { x, y, mask: 0, runId: id };
    t.mask |= bit;
    t.runId = Math.min(t.runId, id);
    tiles.set(k, t);
  };
  for (const r of runs) {
    if (r.leadA) touch(r.a.x, r.a.y, DIR_BIT[r.leadA], r.id);
    if (r.leadB) touch(r.b.x, r.b.y, DIR_BIT[r.leadB], r.id);
    const path = wirePath(r);
    for (let i = 1; i < path.length; i++) {
      const p = path[i - 1]!, q = path[i]!;
      const [from, to] = q.x > p.x ? [XP, XM] : q.x < p.x ? [XM, XP] : q.y > p.y ? [YP, YM] : [YM, YP];
      touch(p.x, p.y, from, r.id);
      touch(q.x, q.y, to, r.id);
    }
  }
  const corner: Record<number, 0 | 1 | 2 | 3> = { [XM | YP]: 0, [YM | XM]: 1, [XP | YM]: 2, [YP | XP]: 3 };
  const out: WireTile[] = [];
  for (const t of tiles.values()) {
    if (t.mask === XM || t.mask === XP) (t.mask = XM | XP); // a lone stub draws as a short straight run
    if (t.mask === YM || t.mask === YP) (t.mask = YM | YP);
    const xs = t.mask & (XM | XP);
    const ys = t.mask & (YM | YP);
    let slot: WireTile['slot'];
    let rotation: 0 | 1 | 2 | 3 = 0;
    if (!ys) slot = 'wire_straight';
    else if (!xs) (slot = 'wire_straight'), (rotation = 1);
    else if (corner[t.mask] !== undefined) (slot = 'wire_corner'), (rotation = corner[t.mask]!);
    else slot = 'wire_cross';
    out.push({ x: t.x, y: t.y, slot, rotation, runId: t.runId });
  }
  return out.sort((a, b) => a.y - b.y || a.x - b.x);
}

// ---------------------------------------------------------------------------------------------------------
// World level

export function buildWorld(world: WorldResponse): LevelScene {
  const items: Item[] = [];
  for (let y = 0; y < WORLD_TILES; y++)
    for (let x = 0; x < WORLD_TILES; x++)
      items.push(slotItem('room_floor', { seed: (x + y) % 2 }, x, y, { x, y, w: 1, h: 1, z: 0, id: 0, kind: 'floor' }, null, 'ground'));
  const tops = siteTops(world);
  const labels: Label[] = [];
  for (const w of world.sites) {
    const t = tops.get(w.site.id)!;
    const b = slotItem('site_building', { seed: w.site.id }, t.x, t.y, { x: t.x, y: t.y, w: 2, h: 2, z: 0, id: w.site.id, kind: 'building' }, { kind: 'site', data: w.site }, 'object');
    items.push(b);
    // under the building's front corner (the footprint's bottom vertex)
    const front = isoToScreen(t.x + 2, t.y + 2);
    labels.push({ text: w.site.name, x: front.sx, y: front.sy + 2, align: 'center', scale: 2 });
  }
  const runs = world.links
    .map((l, i) => ({ id: i, l, a: tops.get(l.a_site_id), b: tops.get(l.b_site_id) }))
    .filter((r) => r.a && r.b)
    .map((r) => ({ id: r.id, a: { x: r.a!.x + 1, y: r.a!.y + 1 }, b: { x: r.b!.x + 1, y: r.b!.y + 1 } }));
  const underBuilding = (x: number, y: number) => [...tops.values()].some((t) => x >= t.x && x <= t.x + 1 && y >= t.y && y <= t.y + 1);
  for (const t of routeWires(untangleRuns(runs, underBuilding))) {
    const link = world.links[t.runId]!;
    items.push(slotItem(t.slot, { rotation: t.rotation }, t.x, t.y, { x: t.x, y: t.y, w: 1, h: 1, z: 0, id: t.runId, kind: 'wire' }, { kind: 'link', data: link }, 'ground'));
  }
  return finish('world', 'iso', items, itemSize, labels);
}

/**
 * Top tile of each site's 2 x 2 building: the building is centred on its GPS point (geoToWorld). Sites without
 * GPS (not covered by the contract) line up along the bottom edge of the overworld in (name, id) order.
 * Footprints never overlap: a site whose spot is taken (two sites ~1 km apart round to the same tiles) moves to
 * the nearest free 2 x 2 spot, so every building stays pickable. Sites claim spots in id order (stable).
 */
export function siteTops(world: WorldResponse): Map<number, { x: number; y: number }> {
  const wanted = wantedSiteTops(world);
  const out = new Map<number, { x: number; y: number }>();
  const taken: { x: number; y: number }[] = [];
  const free = (x: number, y: number) =>
    x >= 0 && y >= 0 && x <= WORLD_TILES - 2 && y <= WORLD_TILES - 2 && taken.every((t) => Math.abs(t.x - x) >= 2 || Math.abs(t.y - y) >= 2);
  for (const id of [...wanted.keys()].sort((a, b) => a - b)) {
    const w = wanted.get(id)!;
    let best = w;
    if (!free(w.x, w.y)) {
      let bestD = Infinity; // nearest free spot by squared distance; ties go to the first in (y, x) order
      for (let y = 0; y <= WORLD_TILES - 2; y++)
        for (let x = 0; x <= WORLD_TILES - 2; x++) {
          const d = (x - w.x) ** 2 + (y - w.y) ** 2;
          if (d < bestD && free(x, y)) (bestD = d), (best = { x, y });
        }
    }
    taken.push(best);
    out.set(id, best);
  }
  return out;
}

function wantedSiteTops(world: WorldResponse): Map<number, { x: number; y: number }> {
  const out = new Map<number, { x: number; y: number }>();
  const gps = world.sites.filter((w) => w.site.latitude !== null && w.site.longitude !== null).map((w) => w.site);
  if (gps.length) {
    const lats = gps.map((s) => s.latitude!);
    const lons = gps.map((s) => s.longitude!);
    const bbox = { minLat: Math.min(...lats), maxLat: Math.max(...lats), minLon: Math.min(...lons), maxLon: Math.max(...lons) };
    for (const s of gps) {
      const g = geoToWorld(s.latitude!, s.longitude!, bbox);
      out.set(s.id, { x: Math.round(g.x) - 1, y: Math.round(g.y) - 1 });
    }
  }
  const rest = world.sites.map((w) => w.site).filter((s) => !out.has(s.id)).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.id - b.id));
  rest.forEach((s, i) => out.set(s.id, { x: 2 + 3 * i, y: WORLD_TILES - 3 }));
  return out;
}

// ---------------------------------------------------------------------------------------------------------
// Site and room levels (both from SiteScene)

function roomFloorAndWalls(items: Item[], loc: Location, box: { x: number; y: number; w: number; h: number }): void {
  const ent: Entity = { kind: 'location', data: loc };
  for (let j = 0; j < box.h; j++)
    for (let i = 0; i < box.w; i++) {
      const x = box.x + i, y = box.y + j;
      items.push(slotItem('room_floor', { seed: (i + j) % 2 }, x, y, { x, y, w: 1, h: 1, z: 0, id: loc.id, kind: 'floor' }, ent, 'ground'));
    }
  // Walls only on the N and W edges (iso.md), so they never hide the room's contents.
  for (let i = 0; i < box.w; i++) {
    const x = box.x + i, y = box.y;
    items.push(slotItem('room_wall', {}, x, y, { x, y, w: 1, h: 0, z: 0, id: loc.id, kind: 'wall' }, ent, 'object'));
  }
  for (let j = 0; j < box.h; j++) {
    const x = box.x, y = box.y + j;
    items.push({ ...slotItem('room_wall', {}, x, y, { x, y, w: 0, h: 1, z: 0, id: loc.id, kind: 'wall' }, ent, 'object', true) });
  }
}

export function rackItem(rack: Rack, devices: readonly Device[], tx: number, ty: number, facing: keyof typeof FACING_ROTATION): Item {
  const rotation = FACING_ROTATION[facing];
  return {
    sprite: { kind: 'rack', rack, devices, rotation },
    ...at('rack_empty', tx, ty),
    layer: 'object',
    depth: { x: tx, y: ty, w: 1, h: 1, z: 0, id: rack.id, kind: 'rack' },
    entity: { kind: 'rack', data: rack },
    tile: { x: tx, y: ty, w: 1, h: 1 },
  };
}

/** An unracked device standing on its floor tile (sprites.ts looseBuffer: a short box showing its faceplate). */
function looseItem(d: Device, tx: number, ty: number): Item {
  const p = isoToScreen(tx, ty);
  const spec = SLOT_SPECS.rack_empty;
  const rows = looseBoxRows(d);
  return {
    sprite: { kind: 'loose', device: d },
    x: p.sx - spec.anchor[0],
    y: p.sy - (spec.anchor[1] - (RACK_FACE_ROWS - rows)),
    layer: 'object',
    depth: { x: tx, y: ty, w: 1, h: 1, z: 0, id: d.id, kind: 'loose' },
    entity: { kind: 'device', data: d },
    tile: { x: tx, y: ty, w: 1, h: 1 },
  };
}

function devicesByRack(scene: Pick<SiteScene, 'devices'>): Map<number, Device[]> {
  const m = new Map<number, Device[]>();
  for (const d of scene.devices) if (d.rack_id !== null) (m.get(d.rack_id) ?? m.set(d.rack_id, []).get(d.rack_id)!).push(d);
  return m;
}

export function buildSite(scene: SiteScene, layout: SiteLayout = layoutSite(scene)): LevelScene {
  const items: Item[] = [];
  const labels: Label[] = [];
  for (const loc of scene.locations) {
    const box = layout.locations.get(loc.id)!;
    roomFloorAndWalls(items, loc, box);
    const front = isoToScreen(box.x + box.w, box.y + box.h); // the room's front corner
    labels.push({ text: loc.name, x: front.sx, y: front.sy + 2, align: 'center', scale: 2 });
  }
  const byRack = devicesByRack(scene);
  for (const r of scene.racks) {
    const spot = layout.racks.get(r.id)!;
    const t = siteTile(layout, spot);
    items.push(rackItem(r, byRack.get(r.id) ?? [], t.x, t.y, spot.facing));
  }
  for (const d of scene.devices) {
    const spot = layout.loose.get(d.id);
    if (!spot) continue;
    const t = siteTile(layout, spot);
    items.push(looseItem(d, t.x, t.y));
  }
  return finish('site', 'iso', items, itemSize, labels);
}

/**
 * Cables between devices in two different racks of this room, as floor runs from the tile in front of one rack to
 * the tile in front of the other, with a stub at each end leading back under the rack (a run on the rack tiles
 * themselves would be hidden by the racks). A front tile outside the room falls back to the rack's own tile.
 * An unracked device of the room (drawn loose on the floor) is an end on its own tile.
 */
export function roomRuns(scene: SiteScene, layout: SiteLayout, locationId: number): (WireRun & { cable: Cable })[] {
  const box = layout.locations.get(locationId);
  const end = (s: { x: number; y: number; facing: Facing }): { t: { x: number; y: number }; lead?: WireDir } => {
    const [dx, dy] = FACING_VECTOR[s.facing];
    const t = { x: s.x + dx, y: s.y + dy };
    if (!box || t.x < 0 || t.y < 0 || t.x >= box.w || t.y >= box.h) return { t: { x: s.x, y: s.y } };
    return { t, lead: dx > 0 ? '-x' : dx < 0 ? '+x' : dy > 0 ? '-y' : '+y' };
  };
  const rackOf = new Map(scene.devices.map((d) => [d.id, d.rack_id] as const));
  // Where a device's wire starts: its rack (key "r<id>") or, unracked, its own floor tile (key "d<id>").
  const endOf = (deviceId: number | null) => {
    if (deviceId === null || !rackOf.has(deviceId)) return null;
    const r = rackOf.get(deviceId)!;
    const spot = r !== null ? layout.racks.get(r) : layout.loose.get(deviceId);
    if (!spot || spot.locationId !== locationId) return null;
    return { key: r !== null ? `r${r}` : `d${deviceId}`, e: r !== null ? end(spot as RackSpot) : { t: { x: spot.x, y: spot.y } } };
  };
  const out = [];
  for (const c of [...scene.cables].sort((p, q) => p.id - q.id)) {
    const a = endOf(c.a_terminations[0].device_id);
    const b = endOf(c.b_terminations[0].device_id);
    if (!a || !b || a.key === b.key) continue;
    const ea: { t: { x: number; y: number }; lead?: WireDir } = a.e;
    const eb: { t: { x: number; y: number }; lead?: WireDir } = b.e;
    out.push({ id: c.id, cable: c, a: ea.t, b: eb.t, ...(ea.lead ? { leadA: ea.lead } : {}), ...(eb.lead ? { leadB: eb.lead } : {}) });
  }
  return out;
}

export function buildRoom(scene: SiteScene, locationId: number, layout: SiteLayout = layoutSite(scene)): LevelScene {
  const loc = scene.locations.find((l) => l.id === locationId);
  if (!loc) throw new Error(`location ${locationId} is not in site ${scene.site.id}`);
  const box = layout.locations.get(loc.id)!;
  const items: Item[] = [];
  roomFloorAndWalls(items, loc, { x: 0, y: 0, w: box.w, h: box.h });
  const byRack = devicesByRack(scene);
  const labels: Label[] = [];
  for (const r of scene.racks) {
    const spot = layout.racks.get(r.id)!;
    if (spot.locationId !== loc.id) continue;
    const it = rackItem(r, byRack.get(r.id) ?? [], spot.x, spot.y, spot.facing);
    items.push(it);
    labels.push(nameAbove(it, r.name));
  }
  for (const d of scene.devices) {
    const spot = layout.loose.get(d.id);
    if (!spot || spot.locationId !== loc.id) continue;
    const it = looseItem(d, spot.x, spot.y);
    items.push(it);
    labels.push(nameAbove(it, d.name ?? `device ${d.id}`));
  }
  const runs = roomRuns(scene, layout, loc.id);
  const cableById = new Map(runs.map((r) => [r.id, r.cable]));
  for (const t of routeWires(runs)) {
    const cable = cableById.get(t.runId)!;
    items.push(
      slotItem(t.slot, { rotation: t.rotation, wire_color: hexColor(cable.color) }, t.x, t.y, { x: t.x, y: t.y, w: 1, h: 1, z: 0, id: cable.id, kind: 'wire' }, { kind: 'cable', data: cable }, 'ground'),
    );
  }
  return finish('room', 'iso', items, itemSize, labels);
}

// ---------------------------------------------------------------------------------------------------------
// Rack level: front elevation. rack_unit frame (80 px wide; faceplates at x = 8), zero-U devices on the side rails.

export const RACK_FRAME_W = SLOT_SPECS.rack_unit.width;
const RAIL_X = { left: 0, right: RACK_FRAME_W - 8 } as const;
const ZERO_U_LEN = SLOT_SPECS.psu.width; // a turned psu is 8 x 64

export function buildRack(scene: RackScene): LevelScene {
  const { rack } = scene;
  const items: Item[] = [];
  for (let u = 0; u < rack.u_height; u++)
    items.push({ sprite: { kind: 'slot', slot: 'rack_unit', variant: { seed: u } }, x: 0, y: u * U_PX, layer: 'ground', depth: NO_DEPTH, entity: { kind: 'rack', data: rack } });
  const ax = SLOT_SPECS.rack_unit.anchor[0];
  const labels: Label[] = [];
  // U numbers left of the frame, device names right of it (vertically centred on the faceplate).
  for (let u = 0; u < rack.u_height; u++) {
    const pos = rack.starting_unit + u;
    labels.push({ text: String(pos), x: -2, y: rackUnitTop(rack, pos, 1) + 1, align: 'right', scale: 1 });
  }
  for (const d of frontDevices(scene.devices).sort((a, b) => a.position! - b.position! || a.id - b.id)) {
    const y = rackUnitTop(rack, d.position!, d.device_type.u_height);
    items.push({ sprite: { kind: 'faceplate', device: d }, x: ax, y, layer: 'object', depth: NO_DEPTH, entity: { kind: 'device', data: d } });
    const text = d.name ?? `device ${d.id}`;
    labels.push({ text, x: RACK_FRAME_W + 3, y: y + Math.max(0, Math.floor((faceplateHeight(d) - labelSize(text).h) / 2)), align: 'left', scale: 1 });
  }
  const rails = { left: 0, right: 0 };
  for (const d of scene.devices.filter(isZeroU).sort((a, b) => (a.name ?? '') < (b.name ?? '') ? -1 : (a.name ?? '') > (b.name ?? '') ? 1 : a.id - b.id)) {
    const side = d.face === 'rear' ? 'right' : 'left';
    items.push({
      sprite: { kind: 'slot', slot: 'psu', variant: { rotation: 1, role_color: hexColor(d.role.color), status: d.status }, turn: true },
      x: RAIL_X[side],
      y: rails[side],
      layer: 'object',
      depth: NO_DEPTH,
      entity: { kind: 'device', data: d },
    });
    rails[side] += ZERO_U_LEN;
  }
  return finish('rack', 'rack', items, itemSize, labels);
}

// ---------------------------------------------------------------------------------------------------------
// Device level: the faceplate, then its ports in rows of 8, one block per port kind (contract order).

export const PORTS_PER_ROW = 8;
export const PORT_GAP = 4;

export function portStatus(p: Interface): 'connected' | 'free' | 'disabled' | 'mgmt' {
  if (!p.enabled) return 'disabled';
  if (p.cable_id !== null) return 'connected';
  return p.mgmt_only ? 'mgmt' : 'free';
}

export function buildDevice(detail: DeviceDetail): LevelScene {
  const items: Item[] = [];
  const d = detail.device;
  items.push({ sprite: { kind: 'faceplate', device: d }, x: 0, y: 0, layer: 'object', depth: NO_DEPTH, entity: { kind: 'device', data: d } });
  const cables = new Map(detail.cables.map((c) => [c.id, c]));
  let y = faceplateHeight(d) + PORT_GAP;
  let kind: PortKind | null = null;
  let col = 0;
  const rows: { y: number; names: string[] }[] = [];
  for (const p of detail.interfaces) {
    if (kind !== null && p.kind !== kind) {
      y += U_PX + PORT_GAP;
      col = 0;
    } else if (col === PORTS_PER_ROW) {
      y += U_PX;
      col = 0;
    }
    if (col === 0) rows.push({ y, names: [] });
    rows.at(-1)!.names.push(p.name);
    kind = p.kind;
    const status = portStatus(p);
    const wire = status === 'connected' ? hexColor(cables.get(p.cable_id!)?.color) : undefined;
    const variant: SlotVariant = wire ? { status, wire_color: wire } : { status };
    items.push({ sprite: { kind: 'slot', slot: 'port', variant }, x: col * 8, y, layer: 'object', depth: NO_DEPTH, entity: { kind: 'port', data: p } });
    col++;
  }
  // The names of each row's ports, left to right, beside the row.
  const labels: Label[] = rows.map((r) => ({ text: portRowText(r.names), x: PORTS_PER_ROW * 8 + 3, y: r.y, align: 'left', scale: 1 }));
  return finish('device', 'rack', items, itemSize, labels);
}

/**
 * One row of port names as a label: every name when they fit ("BMC ETH0 ETH1"), else the first and the last with
 * their shared start dropped from the last ("TE1/0/1-8", "TE1/0/9-16").
 */
export function portRowText(names: readonly string[]): string {
  const all = names.join(' ');
  if (names.length <= 1 || all.length <= 14) return all;
  const a = names[0]!, b = names.at(-1)!;
  let k = 0;
  while (k < a.length && k < b.length && a[k] === b[k]) k++;
  while (k > 0 && /[0-9]/.test(b[k - 1]!)) k--; // never split a number
  return `${a}-${b.slice(k)}`;
}
