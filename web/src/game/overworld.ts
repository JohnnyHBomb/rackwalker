// The placeholder overworld (the real, GPS-scaled one comes later): a code-drawn tile field with one building per
// site, each with one door, and Ilatea standing near the start (walk up to her and press interact, or tap her). Pure model, no DOM: the map, walking with collisions, tap-to-walk paths and doors.
// Deterministic: the same site list always gives the same map.

export const TILE = 16;
export type Tile = 'grass' | 'flowers' | 'tree' | 'path' | 'building' | 'door' | 'guide';
/** Facing, in the contract's IsoFacing letters: N = up, S = down, W = left, E = right on screen. */
export type Dir = 'N' | 'E' | 'S' | 'W';

export interface DoorSite {
  readonly id: number;
  readonly name: string;
}
export interface Door {
  readonly siteId: number;
  readonly name: string;
  readonly x: number;
  readonly y: number;
  /** Building footprint (tiles), the door sits in the middle of its bottom row. */
  readonly bx: number;
  readonly by: number;
  readonly bw: number;
  readonly bh: number;
}
export interface Pos {
  readonly x: number;
  readonly y: number;
}

export const STEP: Readonly<Record<Dir, Pos>> = { N: { x: 0, y: -1 }, S: { x: 0, y: 1 }, W: { x: -1, y: 0 }, E: { x: 1, y: 0 } };
const BW = 6, BH = 4, GAP = 4, MARGIN = 3, ROAD_GAP = 3;

/** Small deterministic hash (decor placement). */
function hash(x: number, y: number): number {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

export class OverworldMap {
  readonly w: number;
  readonly h: number;
  readonly doors: readonly Door[];
  readonly start: Pos;
  /** Where Ilatea stands: two tiles left of the start, on the open grass. */
  readonly guide: Pos;
  private readonly tiles: Tile[];

  constructor(sites: readonly DoorSite[]) {
    const list = [...sites].sort((a, b) => a.id - b.id);
    const n = Math.max(1, list.length);
    const perRow = Math.min(n, 4);
    const rows = Math.ceil(n / perRow);
    this.w = MARGIN * 2 + perRow * BW + (perRow - 1) * GAP + 2;
    const rowH = BH + ROAD_GAP + 1;
    this.h = MARGIN + rows * rowH + 3; // forest edge, a strip of meadow under the last road
    this.tiles = Array.from({ length: this.w * this.h }, () => 'grass' as Tile);
    const doors: Door[] = [];
    list.forEach((s, i) => {
      const r = Math.floor(i / perRow), c = i % perRow;
      // Buildings stagger by one tile so the row does not look ruled.
      const bx = MARGIN + 1 + c * (BW + GAP), by = MARGIN + r * rowH + (c % 2);
      for (let y = by; y < by + BH; y++) for (let x = bx; x < bx + BW; x++) this.set(x, y, 'building');
      const door = { siteId: s.id, name: s.name, x: bx + (BW >> 1), y: by + BH - 1, bx, by, bw: BW, bh: BH };
      this.set(door.x, door.y, 'door');
      doors.push(door);
    });
    this.doors = doors;
    // A road under each row of buildings, a path from each door down to it.
    for (let r = 0; r < rows; r++) {
      const ry = MARGIN + r * rowH + BH + ROAD_GAP - 1;
      for (let x = MARGIN; x < this.w - MARGIN; x++) this.set(x, ry, 'path');
    }
    for (const d of doors) {
      const ry = MARGIN + Math.floor(doors.indexOf(d) / perRow) * rowH + BH + ROAD_GAP - 1;
      for (let y = d.y + 1; y <= ry; y++) this.set(d.x, y, 'path');
    }
    // A forest edge, and a few trees and flowers on the open grass (never on a path or next to a door).
    for (let y = 0; y < this.h; y++)
      for (let x = 0; x < this.w; x++) {
        if (x === 0 || y === 0 || x === this.w - 1 || y === this.h - 1) this.set(x, y, 'tree');
        else if (this.at(x, y) === 'grass' && !this.nearDoorOrPath(x, y)) {
          const v = hash(x, y);
          if (v < 0.06) this.set(x, y, 'tree');
          else if (v < 0.14) this.set(x, y, 'flowers');
        }
      }
    this.start = { x: Math.floor(this.w / 2), y: this.h - 3 };
    this.set(this.start.x, this.start.y, 'grass');
    this.guide = { x: this.start.x - 2, y: this.start.y };
    this.set(this.start.x - 1, this.start.y, 'grass');
    this.set(this.guide.x, this.guide.y + 1, 'grass'); // the tile in front of her is always free
    this.set(this.guide.x, this.guide.y, 'guide');
  }

  private set(x: number, y: number, t: Tile): void {
    if (this.inside(x, y)) this.tiles[y * this.w + x] = t;
  }
  private nearDoorOrPath(x: number, y: number): boolean {
    for (let dy = -1; dy <= 1; dy++)
      for (let dx = -1; dx <= 1; dx++) {
        const t = this.at(x + dx, y + dy);
        if (t === 'path' || t === 'door' || t === 'building') return true;
      }
    return false;
  }

  inside(x: number, y: number): boolean {
    return x >= 0 && y >= 0 && x < this.w && y < this.h;
  }
  at(x: number, y: number): Tile | null {
    return this.inside(x, y) ? this.tiles[y * this.w + x]! : null;
  }
  /** Can the player stand here? Doors count: stepping onto one enters the building. */
  walkable(x: number, y: number): boolean {
    const t = this.at(x, y);
    return t === 'grass' || t === 'flowers' || t === 'path' || t === 'door';
  }
  /** Is the player next to Ilatea (one step away, not diagonal)? */
  nearGuide(p: Pos): boolean {
    return Math.abs(p.x - this.guide.x) + Math.abs(p.y - this.guide.y) === 1;
  }
  /** The free tile next to Ilatea that is the shortest walk from `from` (where a tap on her walks to). */
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
  /** Where the player stands after leaving a building: the tile in front of its door, facing away from it. */
  outside(d: Door): Pos {
    return { x: d.x, y: d.y + 1 };
  }
  /** A tile the player may stand on, or the start tile (a save from another map shape). */
  clampPos(p: Pos): Pos {
    return this.walkable(p.x, p.y) && !this.doorAt(p.x, p.y) ? p : this.start;
  }

  /** Shortest walk (4-neighbour BFS) from `from` to `to`; a building tile as target means its door. [] = no way. */
  path(from: Pos, to: Pos): Pos[] {
    let goal = to;
    if (this.at(to.x, to.y) === 'building') {
      const d = this.doors.find((q) => to.x >= q.bx && to.x < q.bx + q.bw && to.y >= q.by && to.y < q.by + q.bh);
      if (d) goal = d;
    }
    if (!this.walkable(goal.x, goal.y)) return [];
    const key = (p: Pos) => p.y * this.w + p.x;
    const prev = new Map<number, number>([[key(from), -1]]);
    const q: Pos[] = [from];
    while (q.length) {
      const p = q.shift()!;
      if (p.x === goal.x && p.y === goal.y) break;
      for (const s of Object.values(STEP)) {
        const n = { x: p.x + s.x, y: p.y + s.y };
        // Doors are ends, never stepping stones: a walk past a building does not enter it.
        if (!this.walkable(n.x, n.y) || prev.has(key(n)) || (this.doorAt(n.x, n.y) && (n.x !== goal.x || n.y !== goal.y))) continue;
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
