// Draws the placeholder overworld on its own canvas (2D, integer scale, no smoothing) and walks the player around
// it: keys move one tile per step, a tap or click walks there along the shortest path, a door enters its site, and a
// tap on Ilatea walks up to her and talks.
// All art is drawn here by code from the small palette below; buildings take their site's theme colours.
import type { Palette } from '../contracts/theme';
import { labelBuffer } from '../engine/font';
import { dirTo, OverworldMap, STEP, TILE, type Dir, type Door, type Pos, type Tile } from './overworld';

const P = {
  grass: '#5d9a48', grass2: '#6aa853', blade: '#4c8a3b', flowerA: '#f1e4a2', flowerB: '#e98fa4',
  canopy: '#2e6a33', canopy2: '#3e8443', trunk: '#6b4a2b', path: '#c8ae78', path2: '#b79a62',
  door: '#22170f', doorFrame: '#e9dcc0', skin: '#e7b98f', hair: '#3a2a20', tunic: '#2f5f9e', tunic2: '#24497a', boots: '#3b2b1d',
  marker: '#fff6d8', markerEdge: '#22170f',
  // Ilatea (docs/art/guide-ilatea.md): dark hair with a silver strand, owl-gold eyes, dark chiton with blue circuit
  // lines, spear-stylus with a glowing tip.
  gHair: '#2a2030', gSilver: '#c9d3e0', gSkin: '#d9a77e', gEye: '#f2c14e', gRobe: '#1e2a44', gLine: '#5fb8ff', gTip: '#9fe3ff',
} as const;
export const STEP_MS = 110;

function tileCanvas(draw: (g: CanvasRenderingContext2D) => void, w = TILE, h = TILE): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d')!);
  return c;
}
const px = (g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, c: string) => ((g.fillStyle = c), g.fillRect(x, y, w, h));

function drawTile(t: Tile, v: number): HTMLCanvasElement {
  return tileCanvas((g) => {
    px(g, 0, 0, TILE, TILE, v % 2 ? P.grass : P.grass2);
    for (let i = 0; i < 4; i++) px(g, (v * 5 + i * 7) % 15, (v * 3 + i * 5) % 14, 1, 2, P.blade);
    if (t === 'flowers') for (let i = 0; i < 3; i++) px(g, 2 + ((v + i * 5) % 11), 3 + ((v * 2 + i * 4) % 10), 2, 2, i % 2 ? P.flowerA : P.flowerB);
    if (t === 'path') {
      px(g, 0, 0, TILE, TILE, P.path);
      for (let i = 0; i < 3; i++) px(g, (v * 7 + i * 5) % 14, (v * 5 + i * 6) % 14, 2, 1, P.path2);
    }
    if (t === 'tree') {
      px(g, 7, 11, 2, 5, P.trunk);
      px(g, 2, 2, 12, 10, P.canopy);
      px(g, 4, 0, 8, 2, P.canopy);
      px(g, 4, 3, 4, 3, P.canopy2);
    }
  });
}

/** A building (site) in its theme's colours: wall, roof, windows, and a dark arched door with a light frame. */
function drawBuilding(d: Door, pal: Palette): HTMLCanvasElement {
  const w = d.bw * TILE, h = d.bh * TILE;
  return tileCanvas((g) => {
    px(g, 0, 10, w, h - 10, pal.wall);
    px(g, 0, h - 4, w, 4, pal.wall_shade);
    px(g, -1, 0, w + 2, 12, pal.accent);
    px(g, 0, 10, w, 2, pal.outline);
    for (let i = 0; i < d.bw; i++) if (Math.abs(i - (d.bw >> 1)) > 0) px(g, i * TILE + 4, 20, 8, 8, pal.metal_shade), px(g, i * TILE + 5, 21, 3, 3, pal.highlight);
    const dx = (d.x - d.bx) * TILE;
    px(g, dx + 1, h - 15, 14, 15, P.doorFrame);
    px(g, dx + 3, h - 13, 10, 13, P.door);
    px(g, dx + 4, h - 14, 8, 1, P.door);
  }, w, h);
}

/** 16 x 16 hero, four facings (the face shows which way: no colour-only cue). */
function drawPlayer(f: Dir, frame: number): HTMLCanvasElement {
  return tileCanvas((g) => {
    const bob = frame % 2;
    px(g, 4, 1 + bob, 8, 6, P.hair);
    px(g, 5, 3 + bob, 6, 5, P.skin);
    if (f === 'S') px(g, 6, 5 + bob, 1, 1, P.hair), px(g, 9, 5 + bob, 1, 1, P.hair);
    if (f === 'E') px(g, 9, 5 + bob, 1, 1, P.hair), px(g, 5, 3 + bob, 2, 4, P.hair);
    if (f === 'W') px(g, 6, 5 + bob, 1, 1, P.hair), px(g, 9, 3 + bob, 2, 4, P.hair);
    if (f === 'N') px(g, 5, 3 + bob, 6, 4, P.hair);
    px(g, 4, 8, 8, 5, P.tunic);
    px(g, 4, 12, 8, 1, P.tunic2);
    px(g, 3, 9, 1, 3, P.skin), px(g, 12, 9, 1, 3, P.skin);
    px(g, 5, 13, 2, 3 - (frame % 2), P.boots), px(g, 9, 13, 2, 2 + (frame % 2), P.boots);
  });
}

/** 16 x 16 Ilatea, facing the viewer, spear-stylus in her right hand. */
function drawGuide(): HTMLCanvasElement {
  return tileCanvas((g) => {
    px(g, 3, 0, 8, 9, P.gHair);
    px(g, 4, 1, 1, 7, P.gSilver);
    px(g, 4, 2, 6, 5, P.gSkin);
    px(g, 5, 4, 1, 1, P.gEye), px(g, 8, 4, 1, 1, P.gEye);
    px(g, 3, 8, 8, 7, P.gRobe);
    px(g, 4, 10, 6, 1, P.gLine), px(g, 7, 11, 1, 4, P.gLine);
    px(g, 2, 9, 1, 3, P.gSkin), px(g, 11, 9, 1, 3, P.gSkin);
    px(g, 4, 15, 2, 1, P.boots), px(g, 8, 15, 2, 1, P.boots);
    px(g, 13, 2, 1, 14, P.gSilver), px(g, 12, 0, 3, 2, P.gTip);
  });
}

/** A down-pointing marker over each door: a shape, so it reads without colour. */
function drawMarker(): HTMLCanvasElement {
  return tileCanvas((g) => {
    px(g, 3, 0, 10, 2, P.markerEdge);
    for (let i = 0; i < 5; i++) px(g, 4 + i, 2 + i, 8 - i * 2, 1, P.marker), px(g, 3 + i, 2 + i, 1, 1, P.markerEdge), px(g, 12 - i, 2 + i, 1, 1, P.markerEdge);
  }, 16, 8);
}

function bufferCanvas(b: { width: number; height: number; data: Uint8ClampedArray | Uint8Array }): HTMLCanvasElement {
  return tileCanvas((g) => g.putImageData(new ImageData(new Uint8ClampedArray(b.data), b.width, b.height), 0, 0), b.width, b.height);
}

export interface OverworldEvents {
  door(d: Door): void;
  moved(p: Pos, facing: Dir): void;
  /** The player reached Ilatea after tapping her. */
  talk(): void;
}

export class OverworldView {
  readonly canvas = document.createElement('canvas');
  private readonly g: CanvasRenderingContext2D;
  private readonly tiles = new Map<string, HTMLCanvasElement>();
  private readonly buildings = new Map<number, HTMLCanvasElement>();
  private readonly labels = new Map<number, HTMLCanvasElement>();
  private readonly marker = drawMarker();
  private readonly guide = drawGuide();
  /** A tap on Ilatea: talk once the walk to her ends. */
  private talkOnArrival = false;
  private readonly players = new Map<string, HTMLCanvasElement>();
  pos: Pos;
  facing: Dir = 'N';
  /** Pixel offset of the player during a step (the step animates from the old tile). */
  private stepFrom: Pos | null = null;
  private stepT0 = 0;
  private queue: Pos[] = [];
  private held: Dir | null = null;
  private raf = 0;
  private active = false;
  private frame = 0;
  private lastStepAt = 0;

  constructor(
    host: HTMLElement,
    readonly map: OverworldMap,
    palettes: (siteId: number) => Palette,
    private readonly events: OverworldEvents,
    start: Pos,
    facing: Dir,
  ) {
    this.pos = map.clampPos(start);
    this.facing = facing;
    this.canvas.id = 'overworld';
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', `Overworld: ${map.doors.map((d) => d.name).join(', ')}. Walk with your movement keys or tap; walk into a door to go inside.`);
    this.canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;display:none;image-rendering:pixelated;touch-action:none';
    host.appendChild(this.canvas);
    this.g = this.canvas.getContext('2d')!;
    for (const d of map.doors) {
      const pal = palettes(d.siteId);
      this.buildings.set(d.siteId, drawBuilding(d, pal));
      this.labels.set(d.siteId, bufferCanvas(labelBuffer(d.name, pal)));
    }
    this.canvas.addEventListener('pointerdown', (e) => {
      if (!this.active) return;
      const t = this.tileAtClient(e.clientX, e.clientY);
      if (t.x === this.map.guide.x && t.y === this.map.guide.y) this.walkToGuide();
      else this.walkTo(t);
    });
    window.addEventListener('resize', () => this.active && this.draw());
  }

  get scale(): number {
    // As large as fits: at least 16 tiles across and the whole map height when it can; never below 2x or above 4x.
    return Math.max(2, Math.min(4, Math.floor(Math.min(window.innerWidth / (TILE * 16), window.innerHeight / (TILE * this.map.h)))));
  }
  /** Camera centre in map pixels: on the player, kept inside the map. */
  get camera(): { cx: number; cy: number; scale: number } {
    const s = this.scale;
    const half = { w: window.innerWidth / s / 2, h: window.innerHeight / s / 2 };
    const p = this.pixelPos();
    const mw = this.map.w * TILE, mh = this.map.h * TILE;
    const clamp = (v: number, lo: number, hi: number) => (lo > hi ? (lo + hi) / 2 : Math.min(hi, Math.max(lo, v)));
    return { cx: Math.round(clamp(p.x + TILE / 2, half.w, mw - half.w)), cy: Math.round(clamp(p.y + TILE / 2, half.h, mh - half.h)), scale: s };
  }

  show(on: boolean): void {
    this.active = on;
    this.canvas.style.display = on ? 'block' : 'none';
    this.held = null;
    this.queue = [];
    this.talkOnArrival = false;
    if (on) this.loop();
    else cancelAnimationFrame(this.raf);
  }
  get shown(): boolean {
    return this.active;
  }

  /** Puts the player on a tile (after leaving a building, after a load). */
  place(p: Pos, facing: Dir): void {
    this.pos = this.map.clampPos(p);
    this.facing = facing;
    this.stepFrom = null;
    this.queue = [];
    this.talkOnArrival = false;
    this.draw();
  }

  /** Held direction (key down / up); a fresh press steps at once, holding repeats every STEP_MS. */
  hold(d: Dir | null): void {
    if (d) this.talkOnArrival = false;
    if (d && d !== this.held) {
      this.queue = [];
      this.held = d;
      if (!this.stepFrom) this.tryStep(d);
    } else if (!d) this.held = null;
  }

  walkTo(t: Pos): void {
    this.queue = this.map.path(this.pos, t);
    this.held = null;
    this.talkOnArrival = false;
  }
  /** Walks to the nearest free tile next to Ilatea, then talks to her. */
  walkToGuide(): void {
    const to = this.map.besideGuide(this.pos);
    if (!to) return;
    this.walkTo(to);
    this.talkOnArrival = true;
  }
  /** Turns to Ilatea; true when she is in reach (one step away). */
  faceGuide(): boolean {
    if (!this.map.nearGuide(this.pos)) return false;
    this.facing = dirTo(this.pos, this.map.guide);
    this.draw();
    return true;
  }
  /** Tile under a client (CSS pixel) point. */
  tileAtClient(x: number, y: number): Pos {
    const c = this.camera;
    const r = this.canvas.getBoundingClientRect();
    const mx = (x - r.left - window.innerWidth / 2) / c.scale + c.cx, my = (y - r.top - window.innerHeight / 2) / c.scale + c.cy;
    return { x: Math.floor(mx / TILE), y: Math.floor(my / TILE) };
  }
  /** Client point of the centre of a tile (tests, door labels). */
  clientOf(t: Pos): { x: number; y: number } {
    const c = this.camera;
    return { x: Math.round((t.x * TILE + TILE / 2 - c.cx) * c.scale + window.innerWidth / 2), y: Math.round((t.y * TILE + TILE / 2 - c.cy) * c.scale + window.innerHeight / 2) };
  }
  get walking(): boolean {
    return !!this.stepFrom || this.queue.length > 0;
  }

  private tryStep(d: Dir): boolean {
    this.facing = d;
    const n = { x: this.pos.x + STEP[d].x, y: this.pos.y + STEP[d].y };
    if (!this.map.walkable(n.x, n.y)) return this.draw(), false;
    this.stepFrom = this.pos;
    this.stepT0 = performance.now();
    this.pos = n;
    this.frame++;
    return true;
  }

  private pixelPos(): Pos {
    if (!this.stepFrom) return { x: this.pos.x * TILE, y: this.pos.y * TILE };
    const t = Math.min(1, (performance.now() - this.stepT0) / STEP_MS);
    return { x: Math.round((this.stepFrom.x + (this.pos.x - this.stepFrom.x) * t) * TILE), y: Math.round((this.stepFrom.y + (this.pos.y - this.stepFrom.y) * t) * TILE) };
  }

  private loop = (): void => {
    if (!this.active) return;
    const now = performance.now();
    if (this.stepFrom && now - this.stepT0 >= STEP_MS) {
      this.stepFrom = null;
      this.lastStepAt = now;
      const door = this.map.doorAt(this.pos.x, this.pos.y);
      if (door) {
        this.queue = [];
        this.held = null;
        this.draw();
        this.events.door(door);
        return;
      }
      this.events.moved(this.pos, this.facing);
    }
    if (!this.stepFrom) {
      const next = this.queue.shift();
      if (next) this.tryStep(dirTo(this.pos, next));
      else if (this.talkOnArrival) {
        this.talkOnArrival = false;
        if (this.faceGuide()) this.events.talk();
      }
      else if (this.held && now - this.lastStepAt >= 0) this.tryStep(this.held);
    }
    this.draw();
    this.raf = requestAnimationFrame(this.loop);
  };

  draw(): void {
    const W = window.innerWidth, H = window.innerHeight;
    if (this.canvas.width !== W || this.canvas.height !== H) (this.canvas.width = W), (this.canvas.height = H);
    const g = this.g;
    const c = this.camera;
    const s = c.scale;
    g.imageSmoothingEnabled = false;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.fillStyle = '#1d3b22';
    g.fillRect(0, 0, W, H);
    g.setTransform(s, 0, 0, s, Math.round(W / 2 - c.cx * s), Math.round(H / 2 - c.cy * s));
    const x0 = Math.max(0, Math.floor((c.cx - W / s / 2) / TILE) - 1), x1 = Math.min(this.map.w, Math.ceil((c.cx + W / s / 2) / TILE) + 1);
    const y0 = Math.max(0, Math.floor((c.cy - H / s / 2) / TILE) - 1), y1 = Math.min(this.map.h, Math.ceil((c.cy + H / s / 2) / TILE) + 1);
    for (let y = y0; y < y1; y++)
      for (let x = x0; x < x1; x++) {
        const t = this.map.at(x, y)!;
        const v = (x * 7 + y * 13) % 8;
        const kind = t === 'building' || t === 'door' || t === 'guide' ? 'grass' : t;
        const k = `${kind}:${v}`;
        let img = this.tiles.get(k);
        if (!img) this.tiles.set(k, (img = drawTile(kind, v)));
        g.drawImage(img, x * TILE, y * TILE);
      }
    for (const d of this.map.doors) {
      g.drawImage(this.buildings.get(d.siteId)!, d.bx * TILE, d.by * TILE);
      const lab = this.labels.get(d.siteId)!;
      g.drawImage(lab, d.bx * TILE + Math.round((d.bw * TILE - lab.width) / 2), d.by * TILE - lab.height - 2);
      g.drawImage(this.marker, d.x * TILE, (d.y - 1) * TILE + 6);
    }
    g.drawImage(this.guide, this.map.guide.x * TILE, this.map.guide.y * TILE);
    const p = this.pixelPos();
    const pk = `${this.facing}:${this.stepFrom ? this.frame % 2 : 0}`;
    let pl = this.players.get(pk);
    if (!pl) this.players.set(pk, (pl = drawPlayer(this.facing, this.stepFrom ? this.frame % 2 : 0)));
    g.drawImage(pl, p.x, p.y);
  }
}
