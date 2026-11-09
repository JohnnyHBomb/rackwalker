// The overworld map, derived deterministically from NetBox data: the same sites always give the same map, whatever
// order they arrive in. Pure: no DOM, no randomness (every "random" choice is a hash of tile coordinates and site ids).
//
// 1. Regions. Sites with GPS are clustered by great-circle distance (single linkage, REGION_KM): sites closer than
//    that, directly or through a chain of sites, share a region. Sites without GPS join the region of the
//    lowest-id site. Regions are ordered by their lowest site id.
// 2. Walking scale. Inside a region each site's offset from the region's centre (km, east and north) is compressed
//    logarithmically (TILES_PER_DOUBLING tiles per doubling of distance beyond KM0), so 10 km and 200 km both stay
//    walkable, then nudged apart until buildings keep their spacing.
// 3. Filling the space. A forest border, water (a canal with bridges, or a pond), paths joining every site of the
//    region (a minimum spanning tree, routed around buildings and over bridges), fields, the country's landmarks,
//    villagers, scattered trees and flowers.
// 4. Regions sit side by side on one map, separated by their forest borders; the camera never leaves the player's
//    region, and no path joins two regions. Each region of a multi-region world has an airfield pad (a placeholder:
//    stepping on it takes the player to the next region's pad until flights exist).
import { countrySet, type CountrySet, TREE_SHADOW } from './countries';

export const TILE = 16;
/** Terrain classes: grass, path, water, bridge (path over water), sand, tulip field, crop field. */
export type Terrain = 'G' | 'P' | 'W' | 'B' | 'S' | 'F' | 'C';
export type Dir = 'N' | 'E' | 'S' | 'W';
export interface Pos {
  readonly x: number;
  readonly y: number;
}
export const STEP: Readonly<Record<Dir, Pos>> = { N: { x: 0, y: -1 }, S: { x: 0, y: 1 }, W: { x: -1, y: 0 }, E: { x: 1, y: 0 } };

export interface WorldSite {
  readonly id: number;
  readonly name: string;
  readonly latitude: number | null;
  readonly longitude: number | null;
  readonly theme: string | null;
  readonly country: string | null;
}

export interface Door {
  readonly siteId: number;
  readonly name: string;
  readonly x: number;
  readonly y: number;
  /** Building footprint (tiles); the door is the middle of its bottom row. */
  readonly bx: number;
  readonly by: number;
  readonly bw: number;
  readonly bh: number;
  readonly region: number;
  readonly theme: string;
}

export interface Region {
  readonly index: number;
  readonly x0: number;
  readonly y0: number;
  readonly w: number;
  readonly h: number;
  readonly siteIds: readonly number[];
  readonly country: string;
  readonly set: CountrySet;
  /** The airfield pad (a walkable tile), or null in a one-region world. */
  readonly airfield: Pos | null;
  /** Where the player arrives in this region (in front of its first door, or its centre when it has none). */
  readonly arrive: Pos;
}

export interface WorldObject {
  readonly sprite: string;
  /** Anchor position, map pixels. */
  readonly x: number;
  readonly y: number;
  readonly anim?: { readonly fps: number; readonly phase?: number };
  readonly shadow?: readonly [number, number, number, number];
  /** Drawn on the ground before every shadow and sprite (mats, pads). */
  readonly flat?: boolean;
  /** Drawn above everything (sails, weather). */
  readonly top?: boolean;
  readonly glow?: readonly [number, number, number, number];
  readonly kind?: 'building' | 'landmark' | 'tree' | 'villager' | 'guide' | 'decor' | 'fence' | 'airfield' | 'label' | 'marker';
  /** Idle bob (villagers, the guide): one pixel up on alternate beats. */
  readonly bob?: boolean;
  /** Site id for buildings and their labels. */
  readonly site?: number;
}

export const BW = 5;
export const BH = 5;
export const REGION_KM = 250;
export const KM0 = 10;
export const TILES_PER_DOUBLING = 6;
const BORDER = 3;
const MARGIN = 12;
const MIN_W = 48;
const MIN_H = 38;
const SEP = BW + 7;

export function hash(...v: number[]): number {
  let h = 0x811c9dc5 | 0;
  for (const n of v) {
    h ^= n | 0;
    h = Math.imul(h, 0x01000193);
    h ^= h >>> 13;
  }
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Smooth value noise in [0, 1): hashed lattice values, bilinear with a smoothstep. */
export function valueNoise(x: number, y: number, seed: number): number {
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const fx = x - x0, fy = y - y0;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const v = (i: number, j: number) => hash(seed, x0 + i, y0 + j, 99);
  return (v(0, 0) * (1 - sx) + v(1, 0) * sx) * (1 - sy) + (v(0, 1) * (1 - sx) + v(1, 1) * sx) * sy;
}

/** Great-circle distance in km. */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(a)));
}

const hasGps = (s: WorldSite) => s.latitude !== null && s.longitude !== null;

/** Groups of site ids, one per region, ordered by lowest site id; each group sorted by id. */
export function clusterSites(sites: readonly WorldSite[], km = REGION_KM): number[][] {
  const list = [...sites].sort((a, b) => a.id - b.id);
  const parent = new Map(list.map((s) => [s.id, s.id]));
  const find = (i: number): number => (parent.get(i) === i ? i : find(parent.get(i)!));
  const gps = list.filter(hasGps);
  for (let i = 0; i < gps.length; i++)
    for (let j = i + 1; j < gps.length; j++) {
      const a = gps[i]!, b = gps[j]!;
      if (haversineKm(a.latitude!, a.longitude!, b.latitude!, b.longitude!) < km) {
        const ra = find(a.id), rb = find(b.id);
        if (ra !== rb) parent.set(Math.max(ra, rb), Math.min(ra, rb));
      }
    }
  // Sites without GPS join the lowest-id site's region.
  const anchor = gps[0] ?? list[0];
  for (const s of list) if (!hasGps(s) && anchor) parent.set(s.id, find(anchor.id));
  const groups = new Map<number, number[]>();
  for (const s of list) {
    const r = find(s.id);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r)!.push(s.id);
  }
  return [...groups.values()].sort((a, b) => a[0]! - b[0]!);
}

/** Compressed walking offset (tiles) of a point km east / km north of the region's centre. */
export function compress(eastKm: number, northKm: number): { x: number; y: number } {
  const r = Math.hypot(eastKm, northKm);
  if (r < 1e-9) return { x: 0, y: 0 };
  const t = TILES_PER_DOUBLING * Math.log2(1 + r / KM0);
  return { x: (eastKm / r) * t, y: (-northKm / r) * t };
}

/** Building origins (tiles, relative) for the sites of one region. */
export function layoutRegion(sites: readonly WorldSite[]): Map<number, Pos> {
  const gps = sites.filter(hasGps);
  const lat0 = gps.length ? gps.reduce((a, s) => a + s.latitude!, 0) / gps.length : 0;
  const lon0 = gps.length ? gps.reduce((a, s) => a + s.longitude!, 0) / gps.length : 0;
  const k = Math.cos((lat0 * Math.PI) / 180) * 111.32;
  const pts = sites.map((s, i) => {
    if (!hasGps(s)) return { id: s.id, x: (i - gps.length) * SEP, y: 3 * SEP }; // a row below the others
    const c = compress((s.longitude! - lon0) * k, (s.latitude! - lat0) * 110.57);
    return { id: s.id, x: c.x, y: c.y };
  });
  // Push apart pairs closer than SEP on both axes, along the axis that needs less (deterministic order).
  for (let it = 0; it < 200; it++) {
    let moved = false;
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i]!, b = pts[j]!;
        const dx = b.x - a.x, dy = b.y - a.y;
        if (Math.abs(dx) >= SEP || Math.abs(dy) >= SEP) continue;
        moved = true;
        if (SEP - Math.abs(dx) <= SEP - Math.abs(dy)) {
          const s = (SEP - Math.abs(dx)) / 2 + 0.01, d = dx > 0 || (dx === 0 && a.id < b.id) ? 1 : -1;
          a.x -= s * d, b.x += s * d;
        } else {
          const s = (SEP - Math.abs(dy)) / 2 + 0.01, d = dy > 0 || (dy === 0 && a.id < b.id) ? 1 : -1;
          a.y -= s * d, b.y += s * d;
        }
      }
    if (!moved) break;
  }
  const out = new Map<number, Pos>();
  // Round outward from the centre so rounding never pulls two buildings closer together.
  for (const p of pts) out.set(p.id, { x: Math.round(p.x - BW / 2), y: Math.round(p.y - BH / 2) });
  return out;
}

const THEME_IDS = ['debug', 'industrial-robotic', 'farm', 'business', 'pagodas-nature'];
export const buildingSprite = (theme: string) => `bld_${theme}`;

/** A map cell's flags. */
const SOLID = 1, RESERVED = 2, DOOR = 4, WARP = 8;

export class WorldMap {
  readonly w: number;
  readonly h: number;
  readonly terrain: Terrain[];
  readonly regions: Region[] = [];
  readonly doors: Door[] = [];
  readonly objects: WorldObject[] = [];
  readonly start: Pos;
  readonly guide: Pos;
  private readonly flags: Uint8Array;
  private guideAt: Pos | null = null;

  constructor(sitesIn: readonly WorldSite[]) {
    const sites = [...sitesIn].sort((a, b) => a.id - b.id);
    const byId = new Map(sites.map((s) => [s.id, s]));
    const groups = sites.length ? clusterSites(sites) : [[]];
    // Region rectangles: building layout + margin.
    const plans = groups.map((ids) => {
      const lay = layoutRegion(ids.map((i) => byId.get(i)!));
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const p of lay.values()) (x0 = Math.min(x0, p.x)), (y0 = Math.min(y0, p.y)), (x1 = Math.max(x1, p.x + BW)), (y1 = Math.max(y1, p.y + BH));
      if (!lay.size) (x0 = 0), (y0 = 0), (x1 = BW), (y1 = BH);
      let w = x1 - x0 + 2 * MARGIN, h = y1 - y0 + 2 * MARGIN;
      const padX = Math.max(0, MIN_W - w), padY = Math.max(0, MIN_H - h);
      w += padX;
      h += padY;
      return { ids, lay, ox: MARGIN + Math.floor(padX / 2) - x0, oy: MARGIN + Math.floor(padY / 2) - y0, w, h };
    });
    this.w = plans.reduce((a, p) => a + p.w, 0);
    this.h = Math.max(...plans.map((p) => p.h));
    this.terrain = Array.from({ length: this.w * this.h }, () => 'G' as Terrain);
    this.flags = new Uint8Array(this.w * this.h).fill(SOLID); // outside every region: solid
    let rx = 0;
    plans.forEach((p, index) => {
      this.regions.push(this.buildRegion(index, rx, p, byId, groups.length > 1));
      rx += p.w;
    });
    this.doors.sort((a, b) => a.siteId - b.siteId);
    // Region 0 holds the lowest-id site: Ilatea stands beside its door, the player starts just below it.
    const r0 = this.regions[0]!;
    this.start = r0.arrive;
    this.guide = this.guideAt ?? { x: this.start.x - 2, y: this.start.y };
    this.flag(this.guide.x, this.guide.y, SOLID);
    // Ilatea (drawn by the view: she animates), glow and shadow come with her.
    this.objects.sort((a, b) => a.y - b.y || a.x - b.x || (a.sprite < b.sprite ? -1 : a.sprite > b.sprite ? 1 : 0));
  }

  // ---------------------------------------------------------------- cells
  inside(x: number, y: number): boolean {
    return x >= 0 && y >= 0 && x < this.w && y < this.h;
  }
  at(x: number, y: number): Terrain | null {
    return this.inside(x, y) ? this.terrain[y * this.w + x]! : null;
  }
  private setT(x: number, y: number, t: Terrain): void {
    if (this.inside(x, y)) this.terrain[y * this.w + x] = t;
  }
  private has(x: number, y: number, f: number): boolean {
    return !this.inside(x, y) || (this.flags[y * this.w + x]! & f) !== 0;
  }
  private flag(x: number, y: number, f: number): void {
    if (this.inside(x, y)) this.flags[y * this.w + x]! |= f;
  }
  private unflag(x: number, y: number, f: number): void {
    if (this.inside(x, y)) this.flags[y * this.w + x]! &= ~f;
  }
  /** Free grass: not solid, not reserved, grass. */
  private free(x: number, y: number): boolean {
    return this.at(x, y) === 'G' && !this.has(x, y, SOLID | RESERVED);
  }
  private freeRect(x: number, y: number, w: number, h: number, margin: number): boolean {
    for (let j = y - margin; j < y + h + margin; j++) for (let i = x - margin; i < x + w + margin; i++) if (!this.free(i, j)) return false;
    return true;
  }

  // ---------------------------------------------------------------- generation
  private buildRegion(index: number, rx: number, p: { ids: number[]; lay: Map<number, Pos>; ox: number; oy: number; w: number; h: number }, byId: Map<number, WorldSite>, multi: boolean): Region {
    const { w, h } = p;
    const seed = p.ids.reduce((a, i) => a * 31 + i, 7 + index);
    const R = (...v: number[]) => hash(seed, ...v);
    // Country: the most common among the region's sites (ties: the alphabetically first); none = neutral.
    const counts = new Map<string, number>();
    for (const id of p.ids) {
      const c = (byId.get(id)!.country ?? '').trim().toUpperCase();
      if (/^[A-Z]{2}$/.test(c)) counts.set(c, (counts.get(c) ?? 0) + 1);
    }
    const country = [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0]?.[0] ?? '';
    const set = countrySet(country);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) this.flags[y * this.w + rx + x] = 0;
    // Forest border.
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++)
        if (x < BORDER || y < BORDER || x >= w - BORDER || y >= h - BORDER) this.flag(rx + x, y, SOLID);
    // Buildings.
    const doors: Door[] = [];
    for (const id of p.ids) {
      const s = byId.get(id)!;
      const o = p.lay.get(id)!;
      const bx = rx + o.x + p.ox, by = o.y + p.oy;
      const theme = s.theme && THEME_IDS.includes(s.theme) ? s.theme : 'debug';
      const d: Door = { siteId: id, name: s.name, x: bx + (BW >> 1), y: by + BH - 1, bx, by, bw: BW, bh: BH, region: index, theme };
      for (let j = by - 1; j < by + BH + 2; j++) for (let i = bx - 1; i < bx + BW + 1; i++) this.flag(i, j, RESERVED);
      for (let j = by; j < by + BH; j++) for (let i = bx; i < bx + BW; i++) this.flag(i, j, SOLID);
      this.unflag(d.x, d.y, SOLID);
      this.flag(d.x, d.y, DOOR);
      doors.push(d);
      this.doors.push(d);
      const ax = bx * TILE + (BW * TILE) / 2, ay = (by + BH) * TILE - 1;
      this.objects.push({ sprite: buildingSprite(theme), x: ax, y: ay, kind: 'building', site: id, shadow: [BW * 8 - 6, -4, 10, 6] });
      this.objects.push({ sprite: 'doormat', x: d.x * TILE + 8, y: (d.y + 1) * TILE + 2, flat: true, kind: 'decor' });
      this.objects.push({ sprite: `label:${id}`, x: ax, y: by * TILE - 3, top: true, kind: 'label', site: id });
      this.objects.push({ sprite: 'marker', x: d.x * TILE + 8, y: d.y * TILE - 9, top: true, kind: 'marker', anim: { fps: 3 }, site: id });
    }
    // The guide and the start in front of the first door of the world are kept free.
    const front = (d: Door) => ({ x: d.x, y: d.y + 1 });
    const keep: Pos[] = [];
    for (const d of doors) keep.push(front(d), { x: d.x, y: d.y + 2 }, { x: d.x - 1, y: d.y + 1 }, { x: d.x + 1, y: d.y + 1 });
    for (const k of keep) this.unflag(k.x, k.y, SOLID), this.flag(k.x, k.y, RESERVED);
    if (index === 0 && doors[0]) {
      this.guideAt = { x: doors[0].x - 1, y: doors[0].y + 1 };
      this.flag(this.guideAt.x, this.guideAt.y, SOLID);
    }
    const cx = rx + Math.floor(w / 2), cy = Math.floor(h / 2);
    const arrive = doors[0] ? { x: doors[0].x, y: doors[0].y + 2 } : { x: cx, y: cy };
    // Water.
    let canalY = -1;
    if (set.water === 'canal') {
      const rows: number[] = [];
      for (let y = BORDER + 4; y < h - BORDER - 5; y++) rows.push(y);
      rows.sort((a, b) => Math.abs(a - h / 2) - Math.abs(b - h / 2) || a - b);
      for (const y of rows) {
        let ok = true;
        for (let x = rx + BORDER; x < rx + w - BORDER && ok; x++) for (let j = y - 1; j <= y + 2; j++) if (this.has(x, j, RESERVED)) ok = false;
        if (ok) {
          canalY = y;
          break;
        }
      }
      if (canalY >= 0)
        for (let x = rx + BORDER; x < rx + w - BORDER; x++) for (let j = canalY; j < canalY + 2; j++) (this.setT(x, j, 'W'), this.flag(x, j, SOLID));
    }
    if (canalY < 0) {
      // A pond: 6 x 3, as near the region's centre as fits.
      const spots: Pos[] = [];
      for (let y = BORDER + 2; y < h - BORDER - 5; y++) for (let x = BORDER + 2; x < w - BORDER - 8; x++) spots.push({ x: rx + x, y });
      spots.sort((a, b) => Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy) || a.y - b.y || a.x - b.x);
      const s = spots.find((q) => this.freeRect(q.x, q.y, 6, 3, 2) && !doors.some((d) => Math.abs(q.y - d.y) < 3 && q.x <= d.x + 1 && q.x + 6 >= d.x - 1));
      if (s) {
        for (let j = 0; j < 3; j++) for (let i = 0; i < 6; i++) (this.setT(s.x + i, s.y + j, 'W'), this.flag(s.x + i, s.y + j, SOLID));
        this.objects.push({ sprite: 'reeds', x: s.x * TILE + 4, y: (s.y + 3) * TILE + 6, anim: { fps: 2 }, kind: 'decor' });
        this.objects.push({ sprite: 'reeds', x: (s.x + 6) * TILE + 2, y: (s.y + 1) * TILE + 2, anim: { fps: 2, phase: 1 }, kind: 'decor' });
        this.objects.push({ sprite: 'lilypad', x: (s.x + 2) * TILE + 4, y: (s.y + 1) * TILE + 8, flat: true, kind: 'decor' });
        this.objects.push({ sprite: 'lilypad_flower', x: (s.x + 4) * TILE + 2, y: (s.y + 2) * TILE + 4, flat: true, kind: 'decor' });
      }
    }
    // The airfield pad, on the east side, before the paths so a path reaches it.
    let airfield: Pos | null = null;
    if (multi) {
      const spots: Pos[] = [];
      for (let y = BORDER + 2; y < h - BORDER - 3; y++) for (let x = w - BORDER - 6; x > BORDER + 2; x--) spots.push({ x: rx + x, y });
      spots.sort((a, b) => b.x - a.x || Math.abs(a.y - cy) - Math.abs(b.y - cy) || a.y - b.y);
      const s = spots.find((q) => this.freeRect(q.x, q.y, 3, 2, 1));
      if (s) {
        airfield = { x: s.x + 1, y: s.y + 1 };
        for (let j = 0; j < 2; j++) for (let i = 0; i < 3; i++) this.flag(s.x + i, s.y + j, RESERVED);
        this.flag(s.x + 1, s.y, SOLID); // the windsock and sign behind the pad
        this.flag(s.x + 2, s.y, SOLID);
        this.flag(airfield.x, airfield.y, WARP);
        this.objects.push({ sprite: 'airfield_pad', x: airfield.x * TILE + 8, y: airfield.y * TILE + 15, flat: true, kind: 'airfield' });
        this.objects.push({ sprite: 'airfield_sign', x: (s.x + 2) * TILE + 8, y: (s.y + 1) * TILE - 1, kind: 'airfield', shadow: [3, 0, 8, 2] });
      }
    }
    // Paths: Prim's minimum spanning tree over door fronts (Manhattan length, ties by site id), each edge routed.
    const nodes: Pos[] = doors.map(front);
    if (airfield) nodes.push({ x: airfield.x, y: airfield.y + 1 });
    for (const n of nodes) this.setT(n.x, n.y, 'P');
    for (const d of doors) this.setT(d.x, d.y + 2, 'P');
    if (airfield) this.setT(airfield.x, airfield.y, 'P'), this.unflag(airfield.x, airfield.y + 1, SOLID);
    const inTree = [0];
    while (inTree.length < nodes.length) {
      let best: [number, number, number] | null = null;
      for (const a of inTree)
        for (let b = 0; b < nodes.length; b++) {
          if (inTree.includes(b)) continue;
          const dd = Math.abs(nodes[a]!.x - nodes[b]!.x) + Math.abs(nodes[a]!.y - nodes[b]!.y);
          if (!best || dd < best[0] || (dd === best[0] && (a < best[1] || (a === best[1] && b < best[2])))) best = [dd, a, b];
        }
      this.route(nodes[best![1]]!, nodes[best![2]]!, rx, w, h);
      inTree.push(best![2]);
    }
    // Bridges: a plank deck with rails over each crossing (drawn flat; the deck is walkable, the water beside it not).
    for (let y = 0; y < h; y++)
      for (let x = rx; x < rx + w; x++)
        if (this.at(x, y) === 'B' && this.at(x, y - 1) !== 'B') {
          const dx = x * TILE + 8 - 13, dy = y * TILE - 10;
          this.objects.push({ sprite: 'bridge_deck', x: dx, y: dy, flat: true, kind: 'decor' });
          this.objects.push({ sprite: 'bridge_rail', x: dx + 1, y: dy - 2, flat: true, kind: 'decor' });
          this.objects.push({ sprite: 'bridge_rail', x: dx + 23, y: dy - 2, flat: true, kind: 'decor' });
        }
    // Sand on the canal's south bank, under a stretch without a bridge.
    if (canalY >= 0) {
      const y = canalY + 2;
      for (let x0 = rx + BORDER + 2; x0 < rx + w - BORDER - 7; x0++) {
        let ok = true;
        for (let i = 0; i < 6 && ok; i++) for (let j = 0; j < 2; j++) if (!this.free(x0 + i, y + j)) ok = false;
        if (ok && R(x0, 1) < 0.5) {
          for (let i = 0; i < 6; i++) this.setT(x0 + i, y, 'S');
          this.objects.push({ sprite: 'boat', x: (x0 + 3) * TILE, y: canalY * TILE + 26, anim: { fps: 2 }, kind: 'decor' });
          this.objects.push({ sprite: 'mooring_post', x: (x0 + 1) * TILE + 4, y: (canalY + 2) * TILE + 6, kind: 'decor', shadow: [2, 0, 3, 1] });
          this.flag(x0 + 1, y, SOLID);
          break;
        }
      }
      for (let x = rx + BORDER + 1; x < rx + w - BORDER - 1; x++)
        if (R(x, 2) < 0.12 && this.at(x, canalY + 2) === 'G') this.objects.push({ sprite: 'reeds', x: x * TILE + 8, y: (canalY + 2) * TILE + 4, anim: { fps: 2, phase: x % 2 }, kind: 'decor' });
        else if (R(x, 3) < 0.08 && this.at(x, canalY) === 'W' && this.at(x, canalY - 1) !== 'B')
          this.objects.push({ sprite: R(x, 4) < 0.4 ? 'lilypad_flower' : 'lilypad', x: x * TILE + 8, y: canalY * TILE + 22, flat: true, kind: 'decor' });
    }
    // Fields, fenced: two 6 x 3 plots.
    const fieldSpots: Pos[] = [];
    for (let y = BORDER + 1; y < h - BORDER - 4; y++) for (let x = BORDER + 1; x < w - BORDER - 7; x++) fieldSpots.push({ x: rx + x, y });
    fieldSpots.sort((a, b) => R(a.x, a.y, 5) - R(b.x, b.y, 5));
    // How much to put down scales with the region's area, so a big region is as full as a small one.
    const fill = Math.max(1, Math.round(((w - 2 * BORDER) * (h - 2 * BORDER)) / 1400));
    let fields = 0;
    for (const s of fieldSpots) {
      if (fields >= 1 + fill) break;
      if (!this.freeRect(s.x, s.y, 6, 3, 1)) continue;
      fields++;
      for (let j = 0; j < 3; j++) for (let i = 0; i < 6; i++) (this.setT(s.x + i, s.y + j, set.field), this.flag(s.x + i, s.y + j, SOLID));
      for (let i = 0; i < 6; i++) {
        this.objects.push({ sprite: 'fence_h', x: (s.x + i) * TILE + 2, y: s.y * TILE + 1, kind: 'fence' });
        this.objects.push({ sprite: 'fence_h', x: (s.x + i) * TILE + 2, y: (s.y + 3) * TILE + 1, kind: 'fence' });
      }
      this.objects.push({ sprite: 'fence_v', x: s.x * TILE, y: (s.y + 3) * TILE - 2, kind: 'fence' });
      this.objects.push({ sprite: 'fence_v', x: (s.x + 6) * TILE, y: (s.y + 3) * TILE - 2, kind: 'fence' });
      for (let i = 0; i < 6; i++) this.flag(s.x + i, s.y + 3, SOLID);
    }
    // Landmarks of the country set.
    const spots: Pos[] = [];
    for (let y = BORDER + 1; y < h - BORDER - 1; y++) for (let x = BORDER + 1; x < w - BORDER - 1; x++) spots.push({ x: rx + x, y });
    spots.sort((a, b) => R(a.x, a.y, 6) - R(b.x, b.y, 6));
    for (const l of set.landmarks)
      for (let n = 0; n < l.count * fill; n++) {
        const s = spots.find((q) => (l.sky ? this.freeRect(q.x, q.y, l.fw, l.fh, 0) : this.freeRect(q.x, q.y, l.fw, l.fh, 1)));
        if (!s) break;
        const ax = s.x * TILE + l.fw * 8, ay = (s.y + l.fh) * TILE - 1;
        if (l.sky) {
          this.objects.push({ sprite: l.sprite, x: ax, y: ay - 28, top: true, anim: { fps: l.fps ?? 4 }, kind: 'landmark' });
          for (let j = 0; j < l.fh; j++) for (let i = 0; i < l.fw; i++) this.flag(s.x + i, s.y + j, RESERVED);
          continue;
        }
        for (let j = -1; j <= l.fh; j++) for (let i = -1; i <= l.fw; i++) this.flag(s.x + i, s.y + j, RESERVED);
        for (let j = 0; j < l.fh; j++) for (let i = 0; i < l.fw; i++) this.flag(s.x + i, s.y + j, SOLID);
        const o: WorldObject = { sprite: l.sprite, x: ax, y: ay, kind: 'landmark', ...(l.shadow ? { shadow: l.shadow } : {}), ...(l.fps ? { anim: { fps: l.fps } } : {}) };
        this.objects.push(o);
        if (l.top) this.objects.push({ sprite: l.top.sprite, x: ax + l.top.dx, y: ay + l.top.dy, top: true, anim: { fps: l.top.fps }, kind: 'landmark' });
      }
    // Villagers: beside a path, never next to a door, one or two per region.
    const vSpots: Pos[] = [];
    for (let y = BORDER + 1; y < h - BORDER - 1; y++)
      for (let x = BORDER + 1; x < w - BORDER - 1; x++) {
        const X = rx + x;
        if (!this.free(X, y)) continue;
        const nearPath = Object.values(STEP).some((s) => this.at(X + s.x, y + s.y) === 'P');
        const nearDoor = doors.some((d) => Math.abs(d.x - X) <= 2 && Math.abs(d.y + 1 - y) <= 2);
        if (nearPath && !nearDoor) vSpots.push({ x: X, y });
      }
    vSpots.sort((a, b) => R(a.x, a.y, 7) - R(b.x, b.y, 7));
    const nV = Math.min(vSpots.length, 1 + (p.ids.length > 1 ? 1 : 0));
    for (let i = 0; i < nV; i++) {
      const s = vSpots[i * Math.max(1, Math.floor(vSpots.length / nV))]!;
      const v = set.villagers[i % set.villagers.length]!;
      this.flag(s.x, s.y, SOLID | RESERVED);
      this.objects.push({ sprite: v.sprite, x: s.x * TILE + 8, y: s.y * TILE + 14, anim: { fps: 2, phase: i }, bob: true, kind: 'villager', shadow: [1, 0, 7, 2] });
    }
    // The forest border and scattered trees.
    const trees = set.trees;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const X = rx + x;
        const edge = x < BORDER || y < BORDER || x >= w - BORDER || y >= h - BORDER;
        if (edge) {
          // two trees per border tile row, staggered, so the edge reads as forest
          if ((x + y) % 2 === 0 || y >= h - BORDER) {
            const s = trees[Math.floor(R(X, y, 8) * trees.length)]! === 'bush' ? 'tree_a' : trees[Math.floor(R(X, y, 8) * trees.length)]!;
            this.objects.push({ sprite: s, x: X * TILE + 8 + Math.floor(R(X, y, 9) * 7) - 3, y: y * TILE + 14, kind: 'tree', shadow: s === 'poplar' ? [4, -1, 6, 3] : TREE_SHADOW });
          }
          continue;
        }
        if (!this.free(X, y)) continue;
        const nearBusy = [-1, 0, 1].some((dy) => [-1, 0, 1].some((dx) => {
          const t = this.at(X + dx, y + dy);
          return t !== 'G' || this.has(X + dx, y + dy, RESERVED | DOOR);
        }));
        const v = R(X, y, 10);
        // Groves: a coarse noise picks wooded patches, where most free tiles get a tree; elsewhere a few singles.
        const grove = valueNoise(X / 5, y / 5, seed) > 0.64;
        if (!nearBusy && (grove ? v < 0.5 : v < 0.025)) {
          const s = trees[Math.floor(R(X, y, 11) * trees.length)]!;
          this.flag(X, y, SOLID);
          // a few pixels off the tile centre, so a grove does not stand in rows
          const jx = grove ? Math.floor(R(X, y, 15) * 7) - 3 : 0, jy = grove ? Math.floor(R(X, y, 16) * 5) - 3 : 0;
          this.objects.push({ sprite: s, x: X * TILE + 8 + jx, y: y * TILE + 14 + jy, kind: 'tree', shadow: s === 'poplar' ? [4, -1, 6, 3] : s === 'bush' ? [3, 0, 8, 3] : TREE_SHADOW });
        } else if (v > 0.86 && !grove) {
          const kinds = ['daisy', 'violet', 'buttercup', 'tuft', 'daisy', 'tuft', 'stone'];
          const s = kinds[Math.floor(R(X, y, 12) * kinds.length)]!;
          this.objects.push({ sprite: s, x: X * TILE + 3 + Math.floor(R(X, y, 13) * 10), y: y * TILE + 4 + Math.floor(R(X, y, 14) * 10), kind: 'decor', ...(s === 'stone' ? {} : { anim: { fps: 2, phase: (X + y) % 2 } }) });
        }
      }
    return { index, x0: rx, y0: 0, w, h, siteIds: p.ids, country, set, airfield, arrive };
  }

  /** Routes a path between two tiles: cheapest walk over grass (existing paths are cheaper; water is crossed straight N-S as a bridge). */
  private route(a: Pos, b: Pos, rx: number, w: number, h: number): void {
    const key = (x: number, y: number) => y * this.w + x;
    const dist = new Map<number, number>([[key(a.x, a.y), 0]]);
    const prev = new Map<number, number>();
    // A small binary heap would be faster; regions are small (< 100 x 100), so a bucket queue is plenty.
    const buckets: number[][] = [[key(a.x, a.y)]];
    const goal = key(b.x, b.y);
    for (let c = 0; c < buckets.length; c++) {
      const q = buckets[c];
      if (!q) continue;
      for (let qi = 0; qi < q.length; qi++) {
        const k = q[qi]!;
        if (dist.get(k) !== c) continue;
        if (k === goal) break;
        const x = k % this.w, y = Math.floor(k / this.w);
        const here = this.at(x, y);
        for (const [dn, s] of Object.entries(STEP) as [Dir, Pos][]) {
          const nx = x + s.x, ny = y + s.y;
          if (nx < rx + BORDER || ny < BORDER || nx >= rx + w - BORDER || ny >= h - BORDER) continue;
          const t = this.at(nx, ny)!;
          const nk = key(nx, ny);
          let cost: number;
          if (t === 'W' || t === 'B') {
            if (dn === 'E' || dn === 'W') continue; // bridges run straight north-south
            cost = 6;
          } else if ((here === 'W' || here === 'B') && (dn === 'E' || dn === 'W')) continue;
          else if (nk === goal) cost = 1;
          else if (this.has(nx, ny, SOLID | DOOR)) continue;
          else if (t === 'P') cost = 1;
          else if (t === 'G') cost = this.has(nx, ny, RESERVED) ? 6 : 3;
          else continue;
          // a turn costs a little, so paths run straight
          const pk = prev.get(k);
          if (pk !== undefined) {
            const px = pk % this.w, py = Math.floor(pk / this.w);
            if ((px === x) !== (nx === x)) cost += 1;
          }
          const nd = c + cost;
          if (nd < (dist.get(nk) ?? Infinity)) {
            dist.set(nk, nd);
            prev.set(nk, k);
            (buckets[nd] ??= []).push(nk);
          }
        }
      }
      if (dist.has(goal) && c >= dist.get(goal)!) break;
    }
    if (!prev.has(goal)) return;
    for (let k: number | undefined = goal; k !== undefined; k = prev.get(k)) {
      const x = k % this.w, y = Math.floor(k / this.w);
      const t = this.at(x, y);
      if (t === 'W' || t === 'B') {
        this.setT(x, y, 'B');
        this.unflag(x, y, SOLID);
      } else this.setT(x, y, 'P');
      this.flag(x, y, RESERVED);
    }
  }

  // ---------------------------------------------------------------- queries
  /** Can the player stand here? Doors count: stepping onto one enters the building. */
  walkable(x: number, y: number): boolean {
    return this.inside(x, y) && !this.has(x, y, SOLID);
  }
  solid(x: number, y: number): boolean {
    return !this.walkable(x, y);
  }
  isWarp(x: number, y: number): boolean {
    return this.inside(x, y) && this.has(x, y, WARP);
  }
  regionAt(p: Pos): Region {
    return this.regions.find((r) => p.x >= r.x0 && p.x < r.x0 + r.w) ?? this.regions[0]!;
  }
  /** The next region's airfield (the placeholder trip), or null in a one-region world. */
  nextAirfield(p: Pos): Pos | null {
    const r = this.regionAt(p);
    const others = [...this.regions.slice(r.index + 1), ...this.regions.slice(0, r.index)].filter((q) => q.airfield);
    return others[0]?.airfield ?? null;
  }
  nearGuide(p: Pos): boolean {
    return Math.abs(p.x - this.guide.x) + Math.abs(p.y - this.guide.y) === 1;
  }
  besideGuide(from: Pos): Pos | null {
    if (this.nearGuide(from)) return from;
    let best: Pos[] | null = null;
    for (const s of Object.values(STEP)) {
      const n = { x: this.guide.x + s.x, y: this.guide.y + s.y };
      if (!this.walkable(n.x, n.y) || this.doorAt(n.x, n.y)) continue;
      const p = this.path(from, n);
      if (p.length && (!best || p.length < best.length)) best = p;
    }
    return best ? best[best.length - 1]! : null;
  }
  doorAt(x: number, y: number): Door | null {
    return this.doors.find((d) => d.x === x && d.y === y) ?? null;
  }
  doorOf(siteId: number): Door | null {
    return this.doors.find((d) => d.siteId === siteId) ?? null;
  }
  outside(d: Door): Pos {
    return { x: d.x, y: d.y + 1 };
  }
  clampPos(p: Pos): Pos {
    return this.walkable(p.x, p.y) && !this.doorAt(p.x, p.y) && !this.isWarp(p.x, p.y) ? p : this.start;
  }
  /** The building whose footprint covers a tile. */
  buildingAt(x: number, y: number): Door | null {
    return this.doors.find((q) => x >= q.bx && x < q.bx + q.bw && y >= q.by && y < q.by + q.bh) ?? null;
  }
  /** Shortest walk (4-neighbour BFS); a building tile as target means its door. [] = no way. */
  path(from: Pos, to: Pos): Pos[] {
    const b = this.buildingAt(to.x, to.y);
    const goal = b ?? to;
    if (!this.walkable(goal.x, goal.y)) return [];
    const key = (p: Pos) => p.y * this.w + p.x;
    const prev = new Map<number, number>([[key(from), -1]]);
    const q: Pos[] = [from];
    for (let qi = 0; qi < q.length; qi++) {
      const p = q[qi]!;
      if (p.x === goal.x && p.y === goal.y) break;
      for (const s of Object.values(STEP)) {
        const n = { x: p.x + s.x, y: p.y + s.y };
        const end = n.x === goal.x && n.y === goal.y;
        // Doors and the airfield pad are ends, never stepping stones.
        if (!this.walkable(n.x, n.y) || prev.has(key(n)) || ((this.doorAt(n.x, n.y) || this.isWarp(n.x, n.y)) && !end)) continue;
        prev.set(key(n), key(p));
        q.push(n);
      }
    }
    if (!prev.has(key(goal))) return [];
    const out: Pos[] = [];
    for (let k = key(goal); k !== key(from); k = prev.get(k)!) out.push({ x: k % this.w, y: Math.floor(k / this.w) });
    return out.reverse();
  }
}

export function dirTo(a: Pos, b: Pos): Dir {
  return b.x > a.x ? 'E' : b.x < a.x ? 'W' : b.y < a.y ? 'N' : 'S';
}
