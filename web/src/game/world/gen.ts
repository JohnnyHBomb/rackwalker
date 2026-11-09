// The overworld map, derived deterministically from NetBox data: the same sites always give the same map, whatever
// order they arrive in. Pure: no DOM, no randomness (every "random" choice is a hash of tile coordinates and site ids).
//
// 1. Regions. Sites with GPS are clustered by great-circle distance (single linkage, REGION_KM): sites closer than
//    that, directly or through a chain of sites, share a region. Sites without GPS join the region of the
//    lowest-id site. Regions are ordered by their lowest site id.
// 2. Walking scale. Inside a region each site's offset from the region's centre (km, east and north) is compressed
//    logarithmically (TILES_PER_DOUBLING tiles per doubling of distance beyond KM0), so 10 km and 200 km both stay
//    walkable. A site whose building would overlap another moves to the nearest free place.
// 3. Filling the space. A forest border, water (a canal with bridges, or a pond), paths joining every site of the
//    region (a minimum spanning tree, routed around buildings and over bridges), fields, the country's landmarks,
//    villagers, scattered trees and flowers.
// 4. Regions sit side by side on one map, separated by their forest borders; the camera never leaves the player's
//    region, and no path joins two regions. Each region of a multi-region world has an airfield pad (a placeholder:
//    stepping on it takes the player to the next region's pad until flights exist).
import { countrySet, NEUTRAL_SET, type CountrySet, TREE_SHADOW } from './countries';
import { groundTheme } from './themes';

export const TILE = 16;
/** Terrain classes: grass, path, water, bridge (path over water), sand, tulip field, crop field. */
export type Terrain = 'G' | 'P' | 'W' | 'B' | 'S' | 'F' | 'C';
export type Dir = 'N' | 'E' | 'S' | 'W';
export interface Pos {
  readonly x: number;
  readonly y: number;
}
export const STEP: Readonly<Record<Dir, Pos>> = { N: { x: 0, y: -1 }, S: { x: 0, y: 1 }, W: { x: -1, y: 0 }, E: { x: 1, y: 0 } };
const STEPS = Object.values(STEP);

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

/** A site's quest giver: an animal of the region's country, standing beside the door of the site's building. */
export interface Giver {
  readonly siteId: number;
  readonly x: number;
  readonly y: number;
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
  /** The ground, trees and flowers follow this theme: the most common theme of the region's sites. */
  readonly theme: string;
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
  readonly kind?: 'building' | 'landmark' | 'tree' | 'villager' | 'guide' | 'decor' | 'prop' | 'fence' | 'airfield' | 'label' | 'marker';
  /** Idle bob (villagers, the guide): one pixel up on alternate beats. */
  readonly bob?: boolean;
  /** Site id for buildings and their labels. */
  readonly site?: number;
  /** A label's own text (the airfield's destination), when it is not a site's name. */
  readonly text?: string;
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
/** The pond's outline, W = water. */
const POND = ['..WWWW.', '.WWWWWW', 'WWWWWWW', '.WWWWW.'];
/** How far (steps) clampPos looks for a free tile around a saved place before it falls back to the start. */
export const CLAMP_REACH = 8;

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

/** Wraps a longitude difference into [-180, 180), so two sites either side of the 180th meridian stay close. */
export const wrapLon = (d: number): number => ((((d + 180) % 360) + 360) % 360) - 180;

/** Building origins (tiles, relative) for the sites of one region. */
export function layoutRegion(sites: readonly WorldSite[]): Map<number, Pos> {
  const gps = sites.filter(hasGps);
  const noGps = sites.filter((s) => !hasGps(s));
  const lat0 = gps.length ? gps.reduce((a, s) => a + s.latitude!, 0) / gps.length : 0;
  // The mean longitude, taken relative to the first site so a region across the 180th meridian is not averaged to 0.
  const lonRef = gps[0]?.longitude ?? 0;
  const lon0 = gps.length ? lonRef + gps.reduce((a, s) => a + wrapLon(s.longitude! - lonRef), 0) / gps.length : 0;
  const k = Math.cos((lat0 * Math.PI) / 180) * 111.32;
  const gpsPts = gps.map((s) => {
    const c = compress(wrapLon(s.longitude! - lon0) * k, (s.latitude! - lat0) * 110.57);
    return { id: s.id, x: c.x, y: c.y };
  });
  // Sites without GPS stand in a square block below the others (not one long row).
  const cols = Math.max(1, Math.ceil(Math.sqrt(noGps.length)));
  const below = gpsPts.reduce((a, p) => Math.max(a, p.y), -SEP) + 2 * SEP;
  const pts = [
    ...gpsPts,
    ...noGps.map((s, i) => ({ id: s.id, x: ((i % cols) - (cols - 1) / 2) * SEP, y: below + Math.floor(i / cols) * SEP })),
  ];
  // Each site, nearest the centre first (ties by id), takes the whole-tile origin nearest its own that keeps SEP from
  // every site already placed, so buildings never overlap however many sites share one point (a grid of SEP-sized
  // cells keeps the check cheap). The centre's sites keep their places; crowded ones move out.
  pts.sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y) || a.id - b.id);
  const out = new Map<number, Pos>();
  const grid = new Map<string, Pos[]>();
  const cell = (x: number, y: number) => `${Math.floor(x / SEP)},${Math.floor(y / SEP)}`;
  const clash = (x: number, y: number) => {
    const cx = Math.floor(x / SEP), cy = Math.floor(y / SEP);
    for (let j = cy - 1; j <= cy + 1; j++)
      for (let i = cx - 1; i <= cx + 1; i++)
        for (const q of grid.get(`${i},${j}`) ?? []) if (Math.abs(q.x - x) < SEP && Math.abs(q.y - y) < SEP) return true;
    return false;
  };
  for (const p of pts) {
    const x0 = Math.round(p.x - BW / 2), y0 = Math.round(p.y - BH / 2);
    let at: Pos | null = null;
    for (let r = 0; !at; r++) {
      for (const o of ring(r)) if (!clash(x0 + o.x, y0 + o.y)) {
        at = { x: x0 + o.x, y: y0 + o.y };
        break;
      }
    }
    out.set(p.id, at);
    const c = cell(at.x, at.y);
    if (!grid.has(c)) grid.set(c, []);
    grid.get(c)!.push(at);
  }
  return out;
}

/** The offsets at Chebyshev distance r, nearest first (then north before south, west before east). */
const rings: Pos[][] = [];
function ring(r: number): Pos[] {
  if (!rings[r]) {
    const out: Pos[] = [];
    for (let y = -r; y <= r; y++) for (let x = -r; x <= r; x++) if (Math.max(Math.abs(x), Math.abs(y)) === r) out.push({ x, y });
    rings[r] = out.sort((a, b) => a.x * a.x + a.y * a.y - (b.x * b.x + b.y * b.y) || a.y - b.y || a.x - b.x);
  }
  return rings[r]!;
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
  /** One quest giver per site, two tiles right of the door's front tile (the view draws them: they animate). */
  readonly givers: Giver[] = [];
  readonly start: Pos;
  readonly guide: Pos;
  private readonly flags: Uint8Array;
  /** 1 where the player can get to on foot from a region's arrival tile (doors and pads included, as ends). */
  private readonly reach: Uint8Array;
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
    // Each airfield pad is labelled with where it goes: the first site of the next region that has a pad.
    for (const r of this.regions) {
      if (!r.airfield) continue;
      const to = this.nextAirfield(r.airfield);
      const dest = to && this.doors.find((d) => d.region === this.regionAt(to).index);
      if (dest) this.objects.push({ sprite: 'label:airfield', text: `To ${dest.name}`, x: r.airfield.x * TILE + 8, y: r.airfield.y * TILE - 32, top: true, kind: 'label' });
    }
    // Region 0 holds the lowest-id site: Ilatea stands beside its door, the player starts just below it.
    const r0 = this.regions[0]!;
    this.start = r0.arrive;
    this.guide = this.guideAt ?? { x: this.start.x - 2, y: this.start.y };
    this.flag(this.guide.x, this.guide.y, SOLID);
    this.reach = new Uint8Array(this.w * this.h);
    for (const r of this.regions) this.flood(r.arrive);
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
    // Theme: the same vote over the sites' themes (unknown or none do not vote); none = the plain debug ground.
    const tcounts = new Map<string, number>();
    for (const id of p.ids) {
      const t = byId.get(id)!.theme;
      if (t && THEME_IDS.includes(t)) tcounts.set(t, (tcounts.get(t) ?? 0) + 1);
    }
    const theme = [...tcounts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0]?.[0] ?? 'debug';
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
      // The marker bobs inside the upper half of the doorway (every door is 18 px tall from 6 px above the door tile),
      // below the building's sign, so it never covers the sign or the name.
      this.objects.push({ sprite: 'marker', x: d.x * TILE + 8, y: d.y * TILE + 2, top: true, kind: 'marker', anim: { fps: 3 }, site: id });
    }
    // The guide and the start in front of the first door of the world are kept free.
    const front = (d: Door) => ({ x: d.x, y: d.y + 1 });
    const keep: Pos[] = [];
    for (const d of doors) keep.push(front(d), { x: d.x, y: d.y + 2 }, { x: d.x - 1, y: d.y + 1 }, { x: d.x + 1, y: d.y + 1 }, { x: d.x + 2, y: d.y + 1 });
    for (const k of keep) this.unflag(k.x, k.y, SOLID), this.flag(k.x, k.y, RESERVED);
    if (index === 0 && doors[0]) {
      this.guideAt = { x: doors[0].x - 1, y: doors[0].y + 1 };
      this.flag(this.guideAt.x, this.guideAt.y, SOLID);
    }
    // Each site's quest giver stands two tiles right of its door's front tile (kept free above): never in the
    // doorway, and the tiles either side of the front tile stay free to step onto.
    for (const d of doors) {
      this.givers.push({ siteId: d.siteId, x: d.x + 2, y: d.y + 1 });
      this.flag(d.x + 2, d.y + 1, SOLID);
    }
    const cx = rx + Math.floor(w / 2), cy = Math.floor(h / 2);
    const arrive = doors[0] ? { x: doors[0].x, y: doors[0].y + 2 } : { x: cx, y: cy };
    // Water.
    let canalY = -1;
    if (set.water === 'canal') {
      const rows: number[] = [];
      for (let y = BORDER + 4; y < h - BORDER - 5; y++) rows.push(y);
      // near the first building, so the village has its water (a few rows below the door), else across the middle
      const want = doors[0] ? doors[0].y + 5 : h / 2;
      rows.sort((a, b) => Math.abs(a - want) - Math.abs(b - want) || a - b);
      for (const y of rows) {
        let ok = true;
        for (let x = rx + BORDER; x < rx + w - BORDER && ok; x++) for (let j = y - 1; j <= y + 2; j++) if (this.has(x, j, RESERVED)) ok = false;
        if (ok) {
          canalY = y;
          break;
        }
      }
      // The canal runs on under the forest border to the region's edge, so it flows out of the picture.
      if (canalY >= 0) for (let x = rx; x < rx + w; x++) for (let j = canalY; j < canalY + 2; j++) (this.setT(x, j, 'W'), this.flag(x, j, SOLID));
    }
    if (canalY < 0) {
      // A pond: an irregular 7 x 4 blob (its rounded outline comes from the ground tiles), as near the centre as fits.
      const spots: Pos[] = [];
      for (let y = BORDER + 2; y < h - BORDER - 6; y++) for (let x = BORDER + 2; x < w - BORDER - 9; x++) spots.push({ x: rx + x, y });
      // near the first building (beside and a little below its door), so the village has its water, else the centre
      const px = doors[0] ? doors[0].x + 3 : cx, py = doors[0] ? doors[0].y + 3 : cy;
      spots.sort((a, b) => Math.hypot(a.x - px, a.y - py) - Math.hypot(b.x - px, b.y - py) || a.y - b.y || a.x - b.x);
      const s = spots.find((q) => this.freeRect(q.x, q.y, 7, 4, 2) && !doors.some((d) => Math.abs(q.y - d.y) < 4 && q.x <= d.x + 1 && q.x + 7 >= d.x - 1));
      if (s) {
        POND.forEach((row, j) => [...row].forEach((c, i) => c === 'W' && (this.setT(s.x + i, s.y + j, 'W'), this.flag(s.x + i, s.y + j, SOLID))));
        this.stream(s, rx, w, R, arrive);
        this.objects.push({ sprite: 'reeds', x: (s.x + 1) * TILE + 6, y: (s.y + 3) * TILE + 4, anim: { fps: 2 }, kind: 'decor' });
        this.objects.push({ sprite: 'reeds', x: (s.x + 6) * TILE + 6, y: (s.y + 1) * TILE + 6, anim: { fps: 2, phase: 1 }, kind: 'decor' });
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
    // A canal splits the region: when no path crossed it, one path crosses from the first door to the far bank, so
    // every bank can be reached.
    if (canalY >= 0 && doors[0] && !this.anyIn(rx, w, h, 'B')) {
      const d = doors[0], north = d.y > canalY;
      const ys = north ? [canalY - 3, canalY - 2, canalY - 4] : [canalY + 4, canalY + 3, canalY + 5];
      const goal = ys.flatMap((y) => [0, 1, -1, 2, -2, 3, -3].map((o) => ({ x: d.x + o, y }))).find((q) => this.free(q.x, q.y));
      if (goal) this.route(front(d), goal, rx, w, h);
    }
    // A door whose path does not come from the side gets a straight two-tile approach.
    for (const d of doors) {
      const f = front(d);
      if (!['P', 'B'].includes(this.at(f.x - 1, f.y) ?? '') && !['P', 'B'].includes(this.at(f.x + 1, f.y) ?? '')) this.setT(d.x, d.y + 2, 'P');
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
          // a sand bank: six tiles along the water, four below them, so its outline is a rounded lump
          for (let i = 0; i < 6; i++) this.setT(x0 + i, y, 'S');
          for (let i = 1; i < 5; i++) this.setT(x0 + i, y + 1, 'S');
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
    // Fields, fenced, four to six tiles wide and two or three deep, near the buildings first (the village), each with
    // a gate in its bottom fence.
    // How far a tile is from the nearest door (a straight-line guess, enough to put things near the village first).
    const doorDist = (x: number, y: number) => doors.reduce((a, d) => Math.min(a, Math.max(Math.abs(d.x - x), Math.abs(d.y - y))), 99);
    const byVillage = (k: number, pts: Pos[]) =>
      pts.map((q) => ({ q, key: R(q.x, q.y, k) + doorDist(q.x, q.y) / 14 })).sort((a, b) => a.key - b.key).map((e) => e.q);
    const fieldSpots: Pos[] = [];
    for (let y = BORDER + 1; y < h - BORDER - 4; y++) for (let x = BORDER + 1; x < w - BORDER - 7; x++) fieldSpots.push({ x: rx + x, y });
    // How much to put down scales with the region's area, so a big region is as full as a small one.
    const fill = Math.max(1, Math.round(((w - 2 * BORDER) * (h - 2 * BORDER)) / 1400));
    let fields = 0;
    /** Tiles a side path leads to: each field's gate and each landmark's front. */
    const fronts: Pos[] = [];
    for (const s of byVillage(5, fieldSpots)) {
      if (fields >= 2 + 2 * fill) break;
      const fw = 4 + Math.floor(R(s.x, s.y, 42) * 3), fh = 2 + Math.floor(R(s.x, s.y, 43) * 2);
      if (!this.freeRect(s.x, s.y, fw, fh + 1, 1) || doorDist(s.x + (fw >> 1), s.y) < 4) continue;
      fields++;
      for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) (this.setT(s.x + i, s.y + j, set.field), this.flag(s.x + i, s.y + j, SOLID));
      // a gate in the bottom fence, where a side path comes in
      const gx = 1 + Math.floor(R(s.x, s.y, 17) * (fw - 2));
      for (let i = 0; i < fw; i++) {
        this.objects.push({ sprite: 'fence_h', x: (s.x + i) * TILE + 2, y: s.y * TILE + 1, kind: 'fence' });
        this.objects.push({ sprite: i === gx ? 'gate' : 'fence_h', x: (s.x + i) * TILE + 2, y: (s.y + fh) * TILE + 1, kind: 'fence' });
      }
      fronts.push({ x: s.x + gx, y: s.y + fh + 1 });
      // side fences the full height of the plot, stacked sections each side, as the art direction stacks them
      for (let k = 0; k < fh; k++)
        for (const fx of [s.x * TILE, (s.x + fw) * TILE]) this.objects.push({ sprite: 'fence_v', x: fx, y: (s.y + fh) * TILE - 2 - 18 * k, kind: 'fence' });
      for (let i = 0; i < fw; i++) this.flag(s.x + i, s.y + fh, SOLID);
    }
    // Landmarks of the country set.
    const spots: Pos[] = [];
    for (let y = BORDER + 1; y < h - BORDER - 1; y++) for (let x = BORDER + 1; x < w - BORDER - 1; x++) spots.push({ x: rx + x, y });
    const lmSpots = byVillage(6, spots);
    for (const l of set.landmarks)
      for (let n = 0; n < l.count * fill; n++) {
        const s = lmSpots.find((q) => (l.sky ? this.freeRect(q.x, q.y, l.fw, l.fh, 0) : this.freeRect(q.x, q.y, l.fw, l.fh, 1)) && doorDist(q.x, q.y) >= (l.sky ? 6 : 3)); // weather keeps clear of the doorways
        if (!s) break;
        const ax = s.x * TILE + l.fw * 8, ay = (s.y + l.fh) * TILE - 1;
        if (l.sky) {
          this.objects.push({ sprite: l.sprite, x: ax, y: ay - 28, top: true, anim: { fps: l.fps ?? 4 }, kind: 'landmark' });
          for (let j = 0; j < l.fh; j++) for (let i = 0; i < l.fw; i++) this.flag(s.x + i, s.y + j, RESERVED);
          continue;
        }
        for (let j = -1; j <= l.fh; j++) for (let i = -1; i <= l.fw; i++) this.flag(s.x + i, s.y + j, RESERVED);
        for (let j = 0; j < l.fh; j++) for (let i = 0; i < l.fw; i++) this.flag(s.x + i, s.y + j, SOLID);
        fronts.push({ x: s.x + (l.fw >> 1), y: s.y + l.fh });
        const o: WorldObject = { sprite: l.sprite, x: ax, y: ay, kind: 'landmark', ...(l.shadow ? { shadow: l.shadow } : {}), ...(l.fps ? { anim: { fps: l.fps } } : {}) };
        this.objects.push(o);
        if (l.top) this.objects.push({ sprite: l.top.sprite, x: ax + l.top.dx, y: ay + l.top.dy, top: true, anim: { fps: l.top.fps }, kind: 'landmark' });
      }
    this.dress({ rx, w, h, R, doors, theme, fronts, fill, seed });
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
    const nV = Math.min(vSpots.length, 1 + (p.ids.length > 1 ? 1 : 0) + Math.floor(fill / 2));
    for (let i = 0; i < nV; i++) {
      const s = vSpots[i * Math.max(1, Math.floor(vSpots.length / nV))]!;
      const v = set.villagers[i % set.villagers.length]!;
      this.flag(s.x, s.y, SOLID | RESERVED);
      this.objects.push({ sprite: v.sprite, x: s.x * TILE + 8, y: s.y * TILE + 14, anim: { fps: 2, phase: i }, bob: true, kind: 'villager', shadow: [1, 0, 7, 2] });
    }
    // The forest border and scattered trees.
    const trees = set.trees;
    const gt = groundTheme(theme);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const X = rx + x;
        const edge = x < BORDER || y < BORDER || x >= w - BORDER || y >= h - BORDER;
        if (edge) {
          // two trees per border tile row, staggered, so the edge reads as forest; none stands in the canal
          if (this.at(X, y) !== 'W' && ((x + y) % 2 === 0 || y >= h - BORDER)) {
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
        const grove = valueNoise(X / 5, y / 5, seed) > gt.grove;
        // Groves stand close; elsewhere single trees and pairs, with the gaps between them uneven.
        const lone = valueNoise(X / 3, y / 3, seed + 1) > 0.62 ? 0.09 : 0.03;
        if (!nearBusy && (grove ? v < 0.5 : v < lone)) {
          const s = trees[Math.floor(R(X, y, 11) * trees.length)]!;
          this.flag(X, y, SOLID);
          // a few pixels off the tile centre, so trees do not stand in rows
          const jx = Math.floor(R(X, y, 15) * 7) - 3, jy = Math.floor(R(X, y, 16) * 5) - 3;
          this.objects.push({ sprite: s, x: X * TILE + 8 + jx, y: y * TILE + 14 + jy, kind: 'tree', shadow: s === 'poplar' ? [4, -1, 6, 3] : s === 'bush' ? [3, 0, 8, 3] : TREE_SHADOW });
          // a bush at the foot of a lone tree, now and then
          if (!grove && s !== 'bush' && R(X, y, 18) < 0.45 && this.free(X + 1, y) && this.at(X + 1, y + 1) === 'G' && !this.has(X + 1, y, DOOR)) {
            this.flag(X + 1, y, SOLID | RESERVED);
            this.objects.push({ sprite: 'bush', x: (X + 1) * TILE + 6, y: y * TILE + 14, kind: 'tree', shadow: [3, 0, 8, 3] });
          }
        } else if (v > 1 - gt.floraRate && !grove) {
          const kinds = gt.flora;
          const s = kinds[Math.floor(R(X, y, 12) * kinds.length)]!;
          this.objects.push({ sprite: s, x: X * TILE + 3 + Math.floor(R(X, y, 13) * 10), y: y * TILE + 4 + Math.floor(R(X, y, 14) * 10), kind: 'decor', ...(s === 'stone' ? {} : { anim: { fps: 2, phase: (X + y) % 2 } }) });
        }
      }
    return { index, x0: rx, y0: 0, w, h, siteIds: p.ids, country, set, theme, airfield, arrive };
  }

  /**
   * A stream from the pond out under the forest border, on whichever side is shorter and clear: two tiles deep, running
   * east-west (so a path crosses it north-south on a bridge), stepping a tile up or down now and then. None when a
   * building, the airfield or the arrival tile is in the way on both sides.
   */
  private stream(s: Pos, rx: number, w: number, R: (...v: number[]) => number, arrive: Pos): void {
    const west = { from: s.x - 1, to: rx - 1, step: -1, y: s.y + 2 };
    const east = { from: s.x + 7, to: rx + w, step: 1, y: s.y + 1 };
    const sides = s.x - rx <= rx + w - (s.x + 7) ? [west, east] : [east, west];
    for (const side of sides) {
      const cells: Pos[] = [];
      let y = side.y, ok = true;
      for (let x = side.from; x !== side.to && ok; x += side.step) {
        const ys = [y, y + 1];
        // step a row up or down, now and then, never more than one row from where it left the pond
        const r = R(x, 19);
        if (Math.abs(x - side.from) > 2 && r < 0.18) {
          const ny = y + (r < 0.09 ? -1 : 1);
          if (Math.abs(ny - side.y) <= 1) (y = ny), ys.push(y, y + 1);
        }
        for (const yy of ys) {
          if (!this.inside(x, yy) || this.at(x, yy) !== 'G' || this.has(x, yy, RESERVED | DOOR | WARP) || (x === arrive.x && yy === arrive.y)) ok = false;
          cells.push({ x, y: yy });
        }
      }
      if (!ok) continue;
      // the pond's lower west corner joins a stream leaving west, so the two read as one water
      if (side === west && this.at(s.x, s.y + 3) === 'G' && !this.has(s.x, s.y + 3, RESERVED)) cells.push({ x: s.x, y: s.y + 3 });
      for (const c of cells) this.setT(c.x, c.y, 'W'), this.flag(c.x, c.y, SOLID);
      // reeds and lily pads along it
      for (const c of cells) {
        const r = R(c.x, c.y, 54);
        if (r < 0.12 && this.at(c.x, c.y + 1) === 'G' && !this.has(c.x, c.y + 1, SOLID | RESERVED))
          this.objects.push({ sprite: 'reeds', x: c.x * TILE + 8, y: (c.y + 1) * TILE + 4, anim: { fps: 2, phase: c.x % 2 }, kind: 'decor' });
        else if (r > 0.9 && this.at(c.x, c.y - 1) === 'W' && this.at(c.x, c.y + 1) === 'W')
          this.objects.push({ sprite: r > 0.95 ? 'lilypad_flower' : 'lilypad', x: c.x * TILE + 8, y: c.y * TILE + 8, flat: true, kind: 'decor' });
      }
      return;
    }
  }

  /**
   * Dresses a region as densely as the art direction's scene: side paths from the network to every landmark and field
   * gate (so paths branch and meet), signposts at junctions, benches and short fences along paths, a yard in front of
   * each building in its theme, flower beds, rock and shrub clusters. A solid prop never stands on a path, a door or
   * the tiles kept free in front of a door, so every door stays reachable along the paths.
   */
  private dress(c: { rx: number; w: number; h: number; R: (...v: number[]) => number; doors: readonly Door[]; theme: string; fronts: readonly Pos[]; fill: number; seed: number }): void {
    const { rx, w, h, R, doors, theme, fronts, fill, seed } = c;
    const x0 = rx + BORDER + 1, x1 = rx + w - BORDER - 1, y0 = BORDER + 1, y1 = h - BORDER - 1;
    const isPath = (x: number, y: number) => this.at(x, y) === 'P' || this.at(x, y) === 'B';
    // a tile right above a field holds the field's top fence, one right above water its bank
    const room = (x: number, y: number) => this.free(x, y) && !['F', 'C', 'W'].includes(this.at(x, y + 1) ?? 'G');
    const awayFromDoors = (x: number, y: number) => !doors.some((d) => Math.abs(x - d.x) <= 2 && y >= d.y - 1 && y <= d.y + 3);
    const placed: Pos[] = [];
    const spaced = (x: number, y: number, d: number) => !placed.some((p) => Math.abs(p.x - x) <= d && Math.abs(p.y - y) <= d);
    const prop = (sprite: string, x: number, y: number, shadow?: readonly [number, number, number, number], dx = 8, dy = 14) => {
      this.flag(x, y, SOLID | RESERVED);
      placed.push({ x, y });
      // barrels stand in pairs, the second a step behind
      if (sprite === 'barrel') this.objects.push({ sprite, x: x * TILE + dx + 5, y: y * TILE + dy - 3, kind: 'prop' });
      this.objects.push({ sprite, x: x * TILE + dx, y: y * TILE + dy, kind: sprite === 'bush' ? 'tree' : 'prop', ...(shadow ? { shadow } : {}) });
    };
    const decor = (sprite: string, x: number, y: number, k: number) => {
      const o: WorldObject = { sprite, x: x * TILE + 3 + Math.floor(R(x, y, k) * 10), y: y * TILE + 4 + Math.floor(R(x, y, k + 1) * 10), kind: 'decor' };
      this.objects.push(sprite === 'stone' || sprite === 'stone_big' ? o : { ...o, anim: { fps: 2, phase: (x + y) % 2 } });
    };
    // Steps from the nearest door (a breadth-first sweep over the region, through anything): props go near the
    // buildings first, so the village round each door is the busiest part of the region, and thin out towards the woods.
    const near = new Uint16Array(w * h).fill(0xffff);
    const q: number[] = [];
    for (const d of doors) if (d.x - rx >= 0 && d.x - rx < w) (near[d.y * w + d.x - rx] = 0), q.push(d.y * w + d.x - rx);
    for (let i = 0; i < q.length; i++) {
      const k = q[i]!, x = k % w, y = (k - x) / w;
      for (const st of STEPS) {
        const nx = x + st.x, ny = y + st.y, nk = ny * w + nx;
        if (nx >= 0 && ny >= 0 && nx < w && ny < h && near[nk] === 0xffff) (near[nk] = near[k]! + 1), q.push(nk);
      }
    }
    const nearDoor = (x: number, y: number) => (doors.length ? near[y * w + x - rx]! : 0);
    /** The region's inside tiles that pass pred, nearest a door first, shuffled by a hash within a few steps. */
    const shuffled = (k: number, pred: (x: number, y: number) => boolean) => {
      const out: { x: number; y: number; key: number }[] = [];
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) if (pred(x, y)) out.push({ x, y, key: nearDoor(x, y) / 6 + R(x, y, k) });
      return out.sort((a, b) => a.key - b.key);
    };
    // Side paths: from each landmark's front and each field's gate to the nearest path tile, when one is near.
    for (const f of fronts) {
      if (!this.walkable(f.x, f.y) || isPath(f.x, f.y) || this.has(f.x, f.y, DOOR) || f.y >= y1 || f.y < y0) continue;
      let best: Pos | null = null, bd = 16;
      for (let y = y0; y < y1; y++)
        for (let x = x0; x < x1; x++)
          if (this.at(x, y) === 'P') {
            const d = Math.abs(x - f.x) + Math.abs(y - f.y);
            if (d < bd) (bd = d), (best = { x, y });
          }
      if (best) this.route(f, best, rx, w, h);
    }
    // Two lanes leave the path network for the woods, one west or east and one south, so the paths branch and run on
    // out of the picture.
    const pathTiles = shuffled(34, (x, y) => this.at(x, y) === 'P');
    if (pathTiles.length) {
      const lanes: Pos[] = [];
      const ys = Array.from({ length: y1 - y0 - 4 }, (_, i) => y0 + 2 + i).sort((a, b) => R(a, 35) - R(b, 35));
      const side = R(rx, 36) < 0.5 ? x0 - 1 : x1;
      const yl = ys.find((y) => this.free(side, y) && this.free(side, y - 1) && this.free(side, y + 1));
      if (yl !== undefined) lanes.push({ x: side, y: yl });
      const xs = Array.from({ length: x1 - x0 - 4 }, (_, i) => x0 + 2 + i).sort((a, b) => R(a, 37) - R(b, 37));
      const xl = xs.find((x) => this.free(x, y1) && this.free(x - 1, y1) && this.free(x + 1, y1));
      if (xl !== undefined) lanes.push({ x: xl, y: y1 });
      for (const l of lanes) {
        let best = pathTiles[0]!, bd = Infinity;
        for (const p of pathTiles) {
          const d = Math.abs(p.x - l.x) + Math.abs(p.y - l.y);
          if (d < bd) (bd = d), (best = p);
        }
        if (bd < 40) this.route(best, l, rx, w, h);
      }
    }
    // A yard in front of each building, in its theme: a prop on each front corner and one against its east wall.
    const YARD: Record<string, readonly [string, string, string]> = {
      debug: ['crate', 'barrel', 'condenser'],
      'industrial-robotic': ['barrel', 'cable_drum', 'crate'],
      farm: ['barrel', 'crate', 'bench'],
      business: ['bench', 'crate', 'condenser'],
      'pagodas-nature': ['rock', 'bench', 'rock'],
    };
    for (const d of doors) {
      const [l, r, flank] = YARD[d.theme] ?? YARD.debug!;
      const spots: [string, number, number][] = [[l, d.bx, d.by + d.bh], [r, d.bx + d.bw - 1, d.by + d.bh], [flank, d.bx + d.bw, d.by + d.bh - 1]];
      for (const [sp, x, y] of spots)
        if (this.at(x, y) === 'G' && !this.has(x, y, SOLID | DOOR) && !(Math.abs(x - d.x) <= 1 && y === d.y + 1) && !(x === d.x && y === d.y + 2))
          prop(sp, x, y, [2, 0, 6, 2]);
    }
    // Signposts at junctions: a path tile with three or four path neighbours gets one on a free corner beside it.
    const junctions = shuffled(20, (x, y) => this.at(x, y) === 'P' && STEPS.filter((s) => isPath(x + s.x, y + s.y)).length >= 3 && awayFromDoors(x, y));
    let signs = 0;
    const signAt: Pos[] = [];
    for (const j of junctions) {
      if (signs >= 1 + fill) break;
      const at = [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([dx, dy]) => ({ x: j.x + dx!, y: j.y + dy! })).find((q) => room(q.x, q.y) && spaced(q.x, q.y, 3) && !signAt.some((p) => Math.abs(p.x - q.x) + Math.abs(p.y - q.y) < 10));
      if (at) signAt.push(at), prop('signpost', at.x, at.y, [2, 0, 5, 2]), signs++;
    }
    // Benches on the grass beside a straight east-west path, facing it.
    let benches = 0;
    for (const b of shuffled(21, (x, y) => room(x, y) && isPath(x - 1, y + 1) && isPath(x, y + 1) && isPath(x + 1, y + 1) && awayFromDoors(x, y))) {
      if (benches >= 2 + 2 * fill) break;
      if (!spaced(b.x, b.y, 4)) continue;
      prop('bench', b.x, b.y, [2, 0, 9, 2], 8, 13);
      benches++;
    }
    // Short fences along paths: three to five sections on the grass beside a path, east-west.
    let fences = 0;
    for (const f of shuffled(22, (x, y) => this.free(x, y) && (isPath(x, y + 1) || isPath(x, y - 1)) && awayFromDoors(x, y))) {
      if (fences >= 3 + 3 * fill) break;
      const n = 3 + Math.floor(R(f.x, f.y, 23) * 3);
      const side = isPath(f.x, f.y + 1) ? 1 : -1;
      let ok = spaced(f.x, f.y, 1) && spaced(f.x + n - 1, f.y, 1);
      for (let i = 0; i < n && ok; i++) ok = this.free(f.x + i, f.y) && isPath(f.x + i, f.y + side) && awayFromDoors(f.x + i, f.y);
      if (!ok) continue;
      for (let i = 0; i < n; i++) {
        this.flag(f.x + i, f.y, SOLID | RESERVED);
        placed.push({ x: f.x + i, y: f.y });
        this.objects.push({ sprite: 'fence_h', x: (f.x + i) * TILE + 2, y: f.y * TILE + 12, kind: 'fence' });
      }
      fences++;
    }
    // Flower beds, two tiles wide, beside paths (cool colours in the business and pagoda themes).
    const bed = theme === 'business' || theme === 'pagodas-nature' ? 'flowerbed_cool' : 'flowerbed';
    let beds = 0;
    for (const f of shuffled(24, (x, y) => room(x, y) && room(x + 1, y) && (isPath(x, y + 1) || isPath(x + 1, y + 1) || isPath(x - 1, y) || isPath(x + 2, y)) && awayFromDoors(x, y) && awayFromDoors(x + 1, y))) {
      if (beds >= 3 + 4 * fill) break;
      if (!spaced(f.x, f.y, 3) || !this.free(f.x + 1, f.y)) continue;
      prop(bed, f.x, f.y, undefined, 16, 14);
      this.flag(f.x + 1, f.y, SOLID | RESERVED);
      placed.push({ x: f.x + 1, y: f.y });
      beds++;
    }
    // Rock clusters and shrub clusters on open grass, each with small stones, tufts or flowers round it.
    const open = (x: number, y: number) => room(x, y) && !STEPS.some((s) => isPath(x + s.x, y + s.y)) && awayFromDoors(x, y);
    let rocks = 0, shrubs = 0;
    const maxRocks = 2 + 3 * fill, maxShrubs = 2 + 4 * fill;
    for (const r of shuffled(25, open)) {
      if (rocks >= maxRocks && shrubs >= maxShrubs) break;
      if (!spaced(r.x, r.y, 3) || !this.free(r.x, r.y)) continue;
      const shrub = R(r.x, r.y, 26) < 0.55 ? shrubs < maxShrubs : rocks >= maxRocks;
      if (shrub) {
        // two or three bushes, side by side, with flowers at their feet
        prop('bush', r.x, r.y, [3, 0, 8, 3]);
        if (this.free(r.x + 1, r.y)) prop('bush', r.x + 1, r.y, [3, 0, 8, 3], 5, 13);
        if (R(r.x, r.y, 27) < 0.6 && this.free(r.x, r.y + 1) && open(r.x, r.y + 1)) prop('bush', r.x, r.y + 1, [3, 0, 8, 3], 11, 12);
        for (const [dx, dy] of [[-1, 0], [2, 0], [0, 1], [1, 1]]) if (this.free(r.x + dx!, r.y + dy!) && R(r.x + dx!, r.y + dy!, 28) < 0.7) decor(R(r.x, r.y, 29) < 0.5 ? 'daisy' : 'buttercup', r.x + dx!, r.y + dy!, 30);
        shrubs++;
      } else {
        prop('rock', r.x, r.y, [2, 0, 7, 2], 8, 13);
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [1, 1]]) if (this.free(r.x + dx!, r.y + dy!) && R(r.x + dx!, r.y + dy!, 31) < 0.6) decor(R(r.x + dx!, r.y, 32) < 0.5 ? 'stone_big' : 'tuft', r.x + dx!, r.y + dy!, 33);
        rocks++;
      }
    }
    // Further out, away from the village, the same clusters at a steady rate on open grass, so no stretch of the region
    // is bare: a rock with stones, a clump of bushes, or a patch of flowers, with a few tiles between any two.
    for (let y = y0; y < y1; y++)
      for (let x = x0; x < x1; x++) {
        const v = R(x, y, 44);
        if (v >= 0.05 || !open(x, y) || !spaced(x, y, 2) || valueNoise(x / 5, y / 5, seed) > groundTheme(theme).grove) continue; // groves fill themselves later
        if (v < 0.01) {
          prop('rock', x, y, [2, 0, 7, 2], 8, 13);
          for (const [dx, dy] of [[1, 0], [0, 1]]) if (this.free(x + dx!, y + dy!)) decor(R(x, y, 46) < 0.5 ? 'stone_big' : 'tuft', x + dx!, y + dy!, 47);
        } else if (v < 0.025) {
          prop('bush', x, y, [3, 0, 8, 3]);
          if (this.free(x + 1, y) && R(x, y, 48) < 0.6) prop('bush', x + 1, y, [3, 0, 8, 3], 5, 13);
        } else {
          const kinds = groundTheme(theme).flora;
          const kind = kinds[Math.floor(R(x, y, 49) * kinds.length)]!;
          for (let n = 0; n < 3; n++) decor(kind, x, y, 50 + 2 * n);
          placed.push({ x, y });
        }
      }
    // Path verges: tufts and flowers in the grass along the paths, as the art direction lets the grass lean over them.
    const verge = groundTheme(theme).flora;
    for (let y = y0; y < y1; y++)
      for (let x = x0; x < x1; x++)
        if (this.free(x, y) && STEPS.some((st) => isPath(x + st.x, y + st.y)) && R(x, y, 55) < 0.3)
          decor(R(x, y, 56) < 0.5 ? 'tuft' : verge[Math.floor(R(x, y, 57) * verge.length)]!, x, y, 58);
    // Wildflower patches: a few tiles of one flower, thick, as the art direction scatters them by the paths.
    const flora = groundTheme(theme).flora.filter((f) => f !== 'stone' && f !== 'tuft');
    let patches = 0;
    for (const f of shuffled(38, (x, y) => this.free(x, y) && this.free(x + 1, y) && awayFromDoors(x, y))) {
      if (patches >= 4 + 6 * fill || !flora.length) break;
      if (!spaced(f.x, f.y, 2)) continue;
      const kind = flora[Math.floor(R(f.x, f.y, 39) * flora.length)]!;
      for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1], [-1, 0]])
        if (this.free(f.x + dx!, f.y + dy!) && (dx === 0 && dy === 0 || R(f.x + dx!, f.y + dy!, 40) < 0.6))
          for (let n = 0; n < 2; n++) decor(kind, f.x + dx!, f.y + dy!, 41 + 2 * n);
      placed.push(f);
      patches++;
    }
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

  private anyIn(rx: number, w: number, h: number, t: Terrain): boolean {
    for (let y = 0; y < h; y++) for (let x = rx; x < rx + w; x++) if (this.at(x, y) === t) return true;
    return false;
  }

  /** Marks everything reachable on foot from p; doors and pads are reached but not walked through. */
  private flood(p: Pos): void {
    if (!this.walkable(p.x, p.y)) return;
    const q = [p.y * this.w + p.x];
    this.reach[q[0]!] = 1;
    while (q.length) {
      const k = q.pop()!;
      const x = k % this.w, y = (k - x) / this.w;
      if (this.has(x, y, DOOR | WARP)) continue;
      for (const s of Object.values(STEP)) {
        const nx = x + s.x, ny = y + s.y, nk = ny * this.w + nx;
        if (this.walkable(nx, ny) && !this.reach[nk]) (this.reach[nk] = 1), q.push(nk);
      }
    }
  }

  // ---------------------------------------------------------------- queries
  /** Can the player get here on foot from a region's arrival tile? */
  reachable(x: number, y: number): boolean {
    return this.inside(x, y) && this.reach[y * this.w + x] === 1;
  }
  /** A tile a game may put the player on: reachable, not a door, not an airfield pad. */
  standable(x: number, y: number): boolean {
    return this.reachable(x, y) && !this.has(x, y, DOOR | WARP);
  }
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
  giverOf(siteId: number): Giver | null {
    return this.givers.find((g) => g.siteId === siteId) ?? null;
  }
  giverAt(x: number, y: number): Giver | null {
    return this.givers.find((g) => g.x === x && g.y === y) ?? null;
  }
  /** The quest giver the player stands next to, if any. */
  nearGiver(p: Pos): Giver | null {
    return this.givers.find((g) => Math.abs(p.x - g.x) + Math.abs(p.y - g.y) === 1) ?? null;
  }
  /** The villager sprite of a site's quest giver: one of the animals of its region's country, fixed per site. */
  giverSprite(siteId: number): string {
    const r = this.regions.find((q) => q.siteIds.includes(siteId));
    const cast = (r?.set ?? NEUTRAL_SET).villagers;
    return cast[siteId % cast.length]!.sprite;
  }
  giverWhat(siteId: number): string {
    const r = this.regions.find((q) => q.siteIds.includes(siteId));
    const cast = (r?.set ?? NEUTRAL_SET).villagers;
    return cast[siteId % cast.length]!.what;
  }
  /** The free tile next to a quest giver that is the shortest walk from `from` (`from` itself when already next to them). */
  besideGiver(from: Pos, g: Giver): Pos | null {
    if (Math.abs(from.x - g.x) + Math.abs(from.y - g.y) === 1) return from;
    let best: Pos[] | null = null;
    for (const s of Object.values(STEP)) {
      const n = { x: g.x + s.x, y: g.y + s.y };
      if (!this.walkable(n.x, n.y) || this.doorAt(n.x, n.y)) continue;
      const p = this.path(from, n);
      if (p.length && (!best || p.length < best.length)) best = p;
    }
    return best ? best[best.length - 1]! : null;
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
  /**
   * A saved place made safe for a changed map: the place itself when the player can stand there and walk on from it,
   * else the nearest such tile of the same region within CLAMP_REACH steps (an edit in NetBox can wall a saved tile
   * in, cover it or move a door onto it), else the start.
   */
  clampPos(p: Pos): Pos {
    if (!this.inside(p.x, p.y)) return this.start;
    if (this.standable(p.x, p.y)) return p;
    const r = this.regionAt(p);
    for (let d = 1; d <= CLAMP_REACH; d++)
      for (let dy = -d; dy <= d; dy++)
        for (const dx of dy === -d || dy === d ? [0] : [-(d - Math.abs(dy)), d - Math.abs(dy)]) {
          const x = p.x + dx, y = p.y + dy;
          if (x >= r.x0 && x < r.x0 + r.w && this.standable(x, y)) return { x, y };
        }
    return this.start;
  }
  /** The building whose footprint covers a tile. */
  buildingAt(x: number, y: number): Door | null {
    return this.doors.find((q) => x >= q.bx && x < q.bx + q.bw && y >= q.by && y < q.by + q.bh) ?? null;
  }
  /** Shortest walk (4-neighbour BFS); a building tile as target means its door. [] = no way. */
  path(from: Pos, to: Pos): Pos[] {
    const b = this.buildingAt(to.x, to.y);
    const goal = b ?? to;
    if (!this.walkable(goal.x, goal.y) || !this.inside(from.x, from.y)) return [];
    // typed arrays and a flat queue: a tap on a big region searches thousands of tiles within one frame
    const w = this.w, start = from.y * w + from.x, end = goal.y * w + goal.x;
    if (bfs.prev.length < w * this.h) (bfs.prev = new Int32Array(w * this.h)), (bfs.seen = new Uint32Array(w * this.h)), (bfs.queue = new Int32Array(w * this.h));
    if (++bfs.stamp === 0xffffffff) (bfs.seen.fill(0), (bfs.stamp = 1));
    const { prev, seen, queue: q, stamp } = bfs;
    seen[start] = stamp;
    prev[start] = -1;
    let head = 0, tail = 0;
    q[tail++] = start;
    while (head < tail) {
      const k = q[head++]!;
      if (k === end) break;
      const x = k % w, y = (k - x) / w;
      for (const s of STEPS) {
        const nx = x + s.x, ny = y + s.y, n = ny * w + nx;
        if (!this.walkable(nx, ny) || seen[n] === stamp) continue;
        // Doors and the airfield pad are ends, never stepping stones.
        if (n !== end && this.has(nx, ny, DOOR | WARP)) continue;
        seen[n] = stamp;
        prev[n] = k;
        q[tail++] = n;
      }
    }
    if (seen[end] !== stamp || end === start) return [];
    const out: Pos[] = [];
    for (let k = end; k !== start; k = prev[k]!) out.push({ x: k % w, y: Math.floor(k / w) });
    return out.reverse();
  }
}

/** Scratch space of path(), shared by every map and reused between calls (one walk is searched at a time). */
const bfs = { prev: new Int32Array(0), seen: new Uint32Array(0), queue: new Int32Array(0), stamp: 0 };

export function dirTo(a: Pos, b: Pos): Dir {
  return b.x > a.x ? 'E' : b.x < a.x ? 'W' : b.y < a.y ? 'N' : 'S';
}
