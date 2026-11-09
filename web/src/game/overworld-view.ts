// Shows the overworld through the tile engine (world/engine.ts, world/render.ts) on a 256 x 224 native canvas,
// scaled by the largest whole number that fits the window, and walks the player around it: keys move one tile per
// step (the sprite moves in whole pixels on the way), a tap or click walks there along the shortest path, a door
// enters its site, a tap on Ilatea walks up to her and talks, and the airfield pad (a placeholder until flights)
// takes the player to the next region's pad.
import { follow, integerScale } from './world/camera';
import { NATIVE_H, NATIVE_W, sprite } from './world/engine';
import { WorldRenderer } from './world/render';
import { dirTo, OverworldMap, STEP, TILE, type Dir, type Door, type Giver, type Pos } from './overworld';

export const STEP_MS = 110;
const SPRITE_OF: Readonly<Record<Dir, string>> = { N: 'player_up', S: 'player_down', W: 'player_left', E: 'player_right' };

export interface OverworldEvents {
  door(d: Door): void;
  moved(p: Pos, facing: Dir): void;
  /** The player reached Ilatea after tapping her. */
  talk(): void;
  /** True while something covers the green (a talk, the menu): taps then do not walk the player. */
  blocked?(): boolean;
  /** The player stepped on an airfield pad and was taken to another region. */
  travelled?(to: Pos): void;
  /** The player reached a site's quest giver after tapping them. */
  giver?(siteId: number): void;
}

/** What a quest giver's bubble shows: a quest on offer, one waiting for the player, or all in order (shapes, not colours). */
export type GiverMark = 'offer' | 'wait' | 'clear';
const MARK_SPRITE: Readonly<Record<GiverMark, string>> = { offer: 'quest_mark', wait: 'quest_wait', clear: 'quest_clear' };

export class OverworldView {
  /** The full-window backdrop that holds the native canvas (letterboxed, centred). */
  readonly host = document.createElement('div');
  readonly canvas = document.createElement('canvas');
  private readonly g: CanvasRenderingContext2D;
  private readonly image: ImageData;
  private readonly out: Uint32Array;
  readonly renderer: WorldRenderer;
  /** A tap on Ilatea: talk once the walk to her ends. */
  private talkOnArrival = false;
  /** A tap on a quest giver: talk to them once the walk ends. */
  private giverOnArrival: Giver | null = null;
  /** The bubble over each site's quest giver (none while the quest log has not loaded). */
  readonly marks = new Map<number, GiverMark>();
  /** Sites whose "Confusion" is ready and not yet acknowledged: its marker hangs by the building. */
  readonly bosses = new Set<number>();
  /** The tile of the next small step; an arrow at the edge of the view points to it while it is off screen. */
  pointer: Pos | null = null;
  /** Where the pointer arrow was drawn in the last frame (view pixels and direction), or null (tests). */
  pointerShown: { x: number; y: number; dir: 'n' | 'e' | 's' | 'w' } | null = null;
  pos: Pos;
  facing: Dir = 'N';
  private stepFrom: Pos | null = null;
  private stepT0 = 0;
  private queue: Pos[] = [];
  private held: Dir | null = null;
  private raf = 0;
  private active = false;
  private steps = 0;
  /** Set while Ilatea talks: her talk frames play. */
  talking = false;
  /** Reduce motion: animated tiles and sprites hold still. */
  reduceMotion = false;
  /** Frame-to-frame times (ms) of the last 600 frames, and the time spent composing each (tests, perf). */
  readonly frameTimes: number[] = [];
  readonly drawTimes: number[] = [];
  private lastFrameAt = 0;
  private atDoor = false;
  private shownScale = 0;

  constructor(
    host: HTMLElement,
    readonly map: OverworldMap,
    private readonly events: OverworldEvents,
    start: Pos,
    facing: Dir,
  ) {
    this.pos = map.clampPos(start);
    this.facing = facing;
    this.renderer = new WorldRenderer(map, NATIVE_W, NATIVE_H);
    this.host.id = 'overworld';
    this.host.style.cssText = 'position:fixed;inset:0;display:none;background:#140c1c;touch-action:none;overflow:hidden';
    this.canvas.width = NATIVE_W;
    this.canvas.height = NATIVE_H;
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', `Overworld: ${map.doors.map((d) => d.name).join(', ')}. Walk with your movement keys or tap; walk into a door to go inside.`);
    this.canvas.style.cssText = 'position:absolute;image-rendering:pixelated;image-rendering:crisp-edges';
    this.host.appendChild(this.canvas);
    host.appendChild(this.host);
    this.g = this.canvas.getContext('2d', { alpha: false })!;
    this.image = this.g.createImageData(NATIVE_W, NATIVE_H);
    this.out = new Uint32Array(this.image.data.buffer);
    this.host.addEventListener('pointerdown', (e) => {
      if (!this.active || this.atDoor || this.events.blocked?.()) return;
      const t = this.tileAtClient(e.clientX, e.clientY);
      const g = this.map.giverAt(t.x, t.y);
      if (t.x === this.map.guide.x && t.y === this.map.guide.y) this.walkToGuide();
      else if (g) this.walkToGiver(g);
      else this.walkTo(t);
    });
    window.addEventListener('resize', () => this.active && this.layout());
  }

  /** Integer scale of the native view in the window. */
  get scale(): number {
    return integerScale(window.innerWidth, window.innerHeight, NATIVE_W, NATIVE_H);
  }
  /** The view's top-left in map pixels: on the player, inside the player's region. */
  get view(): { x: number; y: number } {
    const p = this.pixelPos();
    const r = this.map.regionAt(this.pos);
    return follow(p.x + TILE / 2, p.y + TILE / 2 - 4, NATIVE_W, NATIVE_H, { x: r.x0 * TILE, y: r.y0 * TILE, w: r.w * TILE, h: r.h * TILE });
  }
  /** Camera centre in map pixels and the scale (tests). */
  get camera(): { cx: number; cy: number; scale: number } {
    const v = this.view;
    return { cx: v.x + NATIVE_W / 2, cy: v.y + NATIVE_H / 2, scale: this.scale };
  }
  /** Where the native canvas sits in the window (CSS pixels). */
  private get box(): { left: number; top: number; s: number } {
    const s = this.scale;
    return { left: Math.floor((window.innerWidth - NATIVE_W * s) / 2), top: Math.floor((window.innerHeight - NATIVE_H * s) / 2), s };
  }
  private layout(): void {
    const b = this.box;
    this.canvas.style.left = `${b.left}px`;
    this.canvas.style.top = `${b.top}px`;
    this.canvas.style.width = `${NATIVE_W * b.s}px`;
    this.canvas.style.height = `${NATIVE_H * b.s}px`;
    this.shownScale = b.s;
  }

  show(on: boolean): void {
    const was = this.active;
    this.active = on;
    this.host.style.display = on ? 'block' : 'none';
    this.held = null;
    this.queue = [];
    this.talkOnArrival = false;
    this.giverOnArrival = null;
    this.atDoor = false;
    cancelAnimationFrame(this.raf);
    if (on) {
      this.layout();
      this.lastFrameAt = 0;
      this.loop();
    } else if (was) this.stepFrom = null;
  }
  resume(p: Pos, facing: Dir): void {
    this.atDoor = false;
    this.place(p, facing);
  }
  get entering(): boolean {
    return this.atDoor;
  }
  get shown(): boolean {
    return this.active;
  }
  place(p: Pos, facing: Dir): void {
    this.pos = this.map.clampPos(p);
    this.facing = facing;
    this.stepFrom = null;
    this.queue = [];
    this.talkOnArrival = false;
    this.giverOnArrival = null;
    this.draw();
  }
  hold(d: Dir | null): void {
    if (d && this.atDoor) return;
    if (d) (this.talkOnArrival = false), (this.giverOnArrival = null);
    if (d && d !== this.held) {
      this.queue = [];
      this.held = d;
      if (!this.stepFrom) this.tryStep(d);
    } else if (!d) this.held = null;
  }
  /** Stops every walk: a held key, a tap-walk on its way, a walk up to Ilatea. */
  stop(): void {
    this.held = null;
    this.queue = [];
    this.talkOnArrival = false;
    this.giverOnArrival = null;
  }
  walkTo(t: Pos): void {
    if (this.atDoor) return;
    this.queue = this.map.path(this.pos, t);
    this.held = null;
    this.talkOnArrival = false;
    this.giverOnArrival = null;
  }
  walkToGiver(g: Giver): void {
    const to = this.map.besideGiver(this.pos, g);
    if (!to) return;
    this.walkTo(to);
    this.giverOnArrival = g;
  }
  /** Turns the player to the quest giver next to them; null when none is. */
  faceGiver(): Giver | null {
    const g = this.map.nearGiver(this.pos);
    if (!g) return null;
    this.facing = dirTo(this.pos, g);
    this.draw();
    return g;
  }
  walkToGuide(): void {
    const to = this.map.besideGuide(this.pos);
    if (!to) return;
    this.walkTo(to);
    this.talkOnArrival = true;
  }
  faceGuide(): boolean {
    if (!this.map.nearGuide(this.pos)) return false;
    this.facing = dirTo(this.pos, this.map.guide);
    this.draw();
    return true;
  }
  /** Tile under a client (CSS pixel) point. */
  tileAtClient(x: number, y: number): Pos {
    const b = this.box, v = this.view;
    const mx = Math.floor((x - b.left) / b.s) + v.x, my = Math.floor((y - b.top) / b.s) + v.y;
    return { x: Math.floor(mx / TILE), y: Math.floor(my / TILE) };
  }
  /** Client point of the centre of a tile (tests, door labels). */
  clientOf(t: Pos): { x: number; y: number } {
    const b = this.box, v = this.view;
    return { x: Math.round(b.left + (t.x * TILE + TILE / 2 - v.x) * b.s), y: Math.round(b.top + (t.y * TILE + TILE / 2 - v.y) * b.s) };
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
    this.steps++;
    return true;
  }
  private stepT(): number {
    return this.stepFrom ? Math.min(1, (performance.now() - this.stepT0) / STEP_MS) : 1;
  }
  /** Top-left of the player's tile in whole map pixels, between tiles during a step. */
  private pixelPos(): Pos {
    if (!this.stepFrom) return { x: this.pos.x * TILE, y: this.pos.y * TILE };
    const t = this.stepT();
    return { x: Math.round((this.stepFrom.x + (this.pos.x - this.stepFrom.x) * t) * TILE), y: Math.round((this.stepFrom.y + (this.pos.y - this.stepFrom.y) * t) * TILE) };
  }

  private loop = (): void => {
    if (!this.active) return;
    const now = performance.now();
    if (this.lastFrameAt) {
      this.frameTimes.push(now - this.lastFrameAt);
      if (this.frameTimes.length > 600) this.frameTimes.shift();
    }
    this.lastFrameAt = now;
    if (this.stepFrom && now - this.stepT0 >= STEP_MS) {
      this.stepFrom = null;
      const door = this.map.doorAt(this.pos.x, this.pos.y);
      if (door) {
        this.queue = [];
        this.held = null;
        this.atDoor = true;
        this.events.door(door);
      } else if (this.map.isWarp(this.pos.x, this.pos.y)) {
        const to = this.map.nextAirfield(this.pos);
        this.queue = [];
        this.held = null;
        if (to) {
          this.pos = { x: to.x, y: to.y + 1 };
          this.facing = 'S';
          this.events.travelled?.(this.pos);
        }
        this.events.moved(this.pos, this.facing);
      } else this.events.moved(this.pos, this.facing);
    }
    if (!this.active) return;
    if (!this.atDoor && !this.stepFrom) {
      const next = this.queue.shift();
      if (next) this.tryStep(dirTo(this.pos, next));
      else if (this.talkOnArrival) {
        this.talkOnArrival = false;
        if (this.faceGuide()) this.events.talk();
      } else if (this.giverOnArrival) {
        const g = this.giverOnArrival;
        this.giverOnArrival = null;
        if (this.faceGiver()?.siteId === g.siteId) this.events.giver?.(g.siteId);
      } else if (this.held) this.tryStep(this.held);
    }
    this.draw();
    this.raf = requestAnimationFrame(this.loop);
  };

  draw(): void {
    if (this.shownScale !== this.scale) this.layout();
    const t0 = performance.now();
    const v = this.view;
    const p = this.pixelPos();
    // Walk cycle: two frames per step (legs apart on the stride), standing frame when still.
    const walkFrame = this.stepFrom ? ((this.steps % 2) * 2 + (this.stepT() < 0.5 ? 1 : 2)) % 4 : 0;
    const g = this.map.guide;
    const t = this.reduceMotion ? 0 : t0;
    const actors = [
      { sprite: this.talking ? 'guide_talk' : 'guide', x: g.x * TILE + 8, y: g.y * TILE + 14, frame: Math.floor(t / 250), shadow: [1, 0, 6, 2], glow: [0, 0, 13, 5] },
      { sprite: SPRITE_OF[this.facing], x: p.x + 8, y: p.y + 14, frame: walkFrame, shadow: [0, 0, 6, 2] },
    ];
    for (const q of this.map.givers)
      actors.push({ sprite: this.map.giverSprite(q.siteId), x: q.x * TILE + 8, y: q.y * TILE + 14, frame: Math.floor(t / 500) + q.siteId, shadow: [1, 0, 7, 2] });
    const fr = this.renderer.render(v.x, v.y, t0, actors, this.reduceMotion);
    // Quest marks are drawn over everything, like the door markers: a bubble over each quest giver, a site's
    // Confusion by its building, and the arrow towards the next small step when that is off screen.
    const bob = this.reduceMotion ? 0 : Math.floor(t0 / 400) % 2;
    for (const q of this.map.givers) {
      const mark = this.marks.get(q.siteId);
      if (mark) fr.sprite(sprite(MARK_SPRITE[mark]), 0, q.x * TILE + 8 - v.x, q.y * TILE - 4 - bob - v.y);
    }
    for (const d of this.map.doors)
      if (this.bosses.has(d.siteId)) fr.sprite(sprite('boss_mark'), this.reduceMotion ? 0 : Math.floor(t0 / 600), (d.bx + d.bw) * TILE - 9 - v.x, d.by * TILE + 12 - bob - v.y);
    this.pointerShown = null;
    if (this.pointer) {
      const tx = this.pointer.x * TILE + 8 - v.x, ty = this.pointer.y * TILE + 8 - v.y;
      if (tx < 0 || ty < 0 || tx >= NATIVE_W || ty >= NATIVE_H) {
        const cx = NATIVE_W / 2, cy = NATIVE_H / 2;
        const dx = tx - cx, dy = ty - cy;
        // the point where the line from the centre to the target leaves the view, kept a margin inside it
        const k = Math.min((cx - 10) / Math.max(1, Math.abs(dx)), (cy - 10) / Math.max(1, Math.abs(dy)));
        const x = Math.round(cx + dx * k), y = Math.round(cy + dy * k);
        const dir = Math.abs(dx) * (cy - 10) > Math.abs(dy) * (cx - 10) ? (dx > 0 ? 'e' : 'w') : dy > 0 ? 's' : 'n';
        fr.sprite(sprite(`quest_arrow_${dir}`), 0, x + (dir === 'e' ? -bob : dir === 'w' ? bob : 0), y + (dir === 's' ? -bob : dir === 'n' ? bob : 0));
        this.pointerShown = { x, y, dir };
      }
    }
    fr.present(this.out);
    this.g.putImageData(this.image, 0, 0);
    this.drawTimes.push(performance.now() - t0);
    if (this.drawTimes.length > 600) this.drawTimes.shift();
  }
}
