// RackWalkerView: the engine. One PIXI Container per level, built on demand from the SceneSource; explicit transitions
// between them; camera pan/zoom; picking; HUD. The theme can differ per level (a site's own theme at site and
// below) and change at runtime: a level is built per (target, theme), with one sprite/texture cache per theme.
import { Application, Container, Sprite } from 'pixi.js';
import type { RenderScale, ZoomLevel } from '../contracts/iso';
import type { Site } from '../contracts/scene.gen';
import type { PixelBuffer, Theme } from '../contracts/theme';
import {
  type CameraState,
  clampTo,
  containerOffset,
  fitScale,
  focusInside,
  levelToScreen,
  panBy,
  pinchStep,
  screenToLevel,
  stepScale,
  zoomAt,
} from './camera';
import { type Entity, entityKey, entityName } from './entity';
import { Hud } from './hud';
import { centre, homeBox, itemsOf, loadLevel, unionBox } from './levels';
import { CachedSource, type Crumb, type Target, WORLD, crumbsFor, enterTarget, formatView, sameTarget } from './nav';
import { pickAt, tileAt, type Pick } from './pick';
import { type Item, type LevelScene } from './scene';
import type { SpriteDesc } from './sprites';
import { type SceneSource } from './source';
import { SpriteCache } from './sprites';
import { TRANSITION_MS, planTransition, transitionFrame } from './transition';
import { TextureCache, buildLevelContainer, drawMarks, type LevelView } from './render';

/** What a theme choice depends on: the level, and the site it belongs to (null at world level). */
export interface ThemeQuery {
  readonly level: ZoomLevel;
  readonly site: Site | null;
}
/** A fixed theme, or one chosen per level. */
export type ThemeSelector = Theme | ((q: ThemeQuery) => Theme);

interface ThemeCaches {
  readonly sprites: SpriteCache;
  readonly tex: TextureCache;
}

interface Bundle {
  readonly target: Target;
  readonly theme: Theme;
  readonly site: Site | null;
  readonly caches: ThemeCaches;
  readonly scene: LevelScene;
  readonly view: LevelView;
  readonly crumbs: Crumb[];
  cam: CameraState;
}

export interface RackWalkerViewOptions {
  readonly animate?: boolean;
  /** Wraps the (cached) source for every level build, e.g. to show staged edits on top of NetBox data. */
  readonly wrap?: (src: SceneSource) => SceneSource;
}

/** A point in level px (scale 1) plus the client (DOM) point it came from. */
export interface DragPoint {
  readonly lx: number;
  readonly ly: number;
  readonly clientX: number;
  readonly clientY: number;
}

/** An object drag in progress (owned by the editor); the view only forwards pointer moves to it. */
export interface DragSession {
  move(p: DragPoint): void;
  drop(p: DragPoint): void;
  cancel(): void;
}

/** Editing hooks; every hook is optional and the view behaves as a pure viewer without them. */
export interface Interaction {
  /** A press on `pick` moved past the drag threshold: return a session to drag the object, or null to pan. */
  dragStart?(pick: Pick, p: DragPoint): DragSession | null;
  /** Pointer moves with no button down (tools that follow the pointer). */
  hover?(p: DragPoint): void;
  /** A click (press + release without a drag); return true when consumed (the selection then stays as it is). */
  click?(p: DragPoint): boolean;
  /** Keyboard first goes here; return true when handled. */
  key?(e: KeyboardEvent): boolean;
  selected?(e: Entity | null): void;
  levelChanged?(): void;
}

const DRAG_PX = 4;

export class RackWalkerView {
  readonly app = new Application();
  private readonly src: CachedSource;
  private readonly themeOf: (q: ThemeQuery) => Theme;
  private readonly caches = new Map<string, ThemeCaches>();
  private shownTheme: Theme;
  private hud!: Hud;
  /** Called after every HUD refresh (level, selection or theme changed); the theme picker listens here. */
  onUpdate: (() => void) | null = null;
  private readonly bundles = new Map<string, Bundle>();
  private cur: Bundle | null = null;
  private selected: Pick | null = null;
  private hoverTile: { tx: number; ty: number } | null = null;
  private error: string | null = null;
  private busy: Promise<void> = Promise.resolve();
  private busyCount = 0;
  private readonly levelLayer = new Container({ label: 'levels' });
  private interaction: Interaction = {};
  private drag: DragSession | null = null;
  private highlights: ReadonlySet<string> = new Set();
  private readonly reader: SceneSource;

  constructor(
    private readonly host: HTMLElement,
    source: SceneSource,
    theme: ThemeSelector,
    private readonly opts: RackWalkerViewOptions = {},
  ) {
    this.src = new CachedSource(source);
    this.reader = opts.wrap ? opts.wrap(this.src) : this.src;
    this.themeOf = typeof theme === 'function' ? theme : () => theme;
    this.shownTheme = this.themeOf({ level: 'world', site: null });
  }

  /** The scene data the view reads (memoised, through the `wrap` option's wrapper): search reads it too. */
  get data(): SceneSource {
    return this.reader;
  }

  /** The theme of the level on screen (before the first level: the world theme). */
  get theme(): Theme {
    return this.shownTheme;
  }
  /** Container in the HUD for extra controls (the theme picker); exists after init(). */
  get hudSlot(): HTMLElement {
    return this.hud.slot;
  }
  /** The level on screen and its site, as the theme choice sees them; null before the first level. */
  get themeQuery(): ThemeQuery | null {
    return this.cur ? { level: this.cur.scene.level, site: this.cur.site } : null;
  }

  private cachesFor(theme: Theme): ThemeCaches {
    let c = this.caches.get(theme.id);
    if (!c || c.sprites.theme !== theme) {
      const sprites = new SpriteCache(theme);
      c = { sprites, tex: new TextureCache(sprites) };
      this.caches.set(theme.id, c);
    }
    return c;
  }

  private showTheme(theme: Theme): void {
    this.shownTheme = theme;
    this.app.renderer.background.color = theme.palette.background;
    this.host.style.background = theme.palette.background;
    this.hud?.setPalette(theme.palette);
  }

  async init(): Promise<void> {
    await this.app.init({ resizeTo: window, background: this.shownTheme.palette.background, antialias: false, roundPixels: true, resolution: 1, preference: 'webgl' });
    this.app.canvas.style.imageRendering = 'pixelated';
    this.app.canvas.style.display = 'block';
    this.host.appendChild(this.app.canvas);
    this.app.stage.addChild(this.levelLayer);
    this.host.style.background = this.shownTheme.palette.background;
    this.hud = new Hud(this.host, this.shownTheme.palette, {
      // Failures are shown in the HUD (this.error); the promises are only for callers that wait.
      back: () => void this.back().catch(() => undefined),
      goto: (c) => void this.goto(c.target, 'out').catch(() => undefined),
      zoom: (d) => this.zoomBy(d),
      enterSelected: () => this.selected && void this.enter(this.selected.entity, this.selected.item).catch(() => undefined),
    });
    this.bindInput();
    this.app.renderer.on('resize', () => this.applyCamera());
  }

  // ------------------------------------------------------------------------------------------------- state

  get level(): ZoomLevel | null {
    return this.cur?.scene.level ?? null;
  }
  get camera(): CameraState | null {
    return this.cur?.cam ?? null;
  }
  get scene(): LevelScene | null {
    return this.cur?.scene ?? null;
  }
  get crumbs(): readonly Crumb[] {
    return this.cur?.crumbs ?? [];
  }
  get selection(): Entity | null {
    return this.selected?.entity ?? null;
  }
  get lastError(): string | null {
    return this.error;
  }
  /** Resolves when every queued navigation (and its transition) has finished. */
  settled(): Promise<void> {
    return this.busy;
  }
  private get viewSize() {
    return { width: this.app.renderer.width, height: this.app.renderer.height };
  }

  // ------------------------------------------------------------------------------------------------- navigation

  /** Queue a navigation; transitions never overlap. */
  goto(t: Target, direction: 'in' | 'out' | 'none' = 'none', from?: Item): Promise<void> {
    return this.enqueue(() => this.doGoto(t, direction, from));
  }

  /** Re-resolves the theme of the level on screen (after a theme choice) and rebuilds it if the theme changed. */
  refreshTheme(): Promise<void> {
    return this.enqueue(() => this.doRefreshTheme());
  }

  private enqueue(job: () => Promise<void>): Promise<void> {
    this.busyCount++;
    this.refreshHud();
    const run = this.busy.then(job).finally(() => {
      this.busyCount--;
      this.refreshHud();
    });
    this.busy = run.catch(() => undefined);
    return run;
  }

  /** Zoom into an entity (double-click / enter); no-op for entities without a level. */
  enter(e: Entity, item?: Item): Promise<void> {
    if (!this.cur) return Promise.resolve();
    const t = enterTarget(this.cur.scene.level, e);
    return t ? this.goto(t, 'in', item) : Promise.resolve();
  }

  /** Zoom out one level (the parent crumb). */
  back(): Promise<void> {
    const c = this.cur?.crumbs;
    if (!c || c.length < 2) return Promise.resolve();
    return this.goto(c[c.length - 2]!.target, 'out');
  }

  private async bundleFor(t: Target): Promise<Bundle> {
    // The crumbs name the site (the theme depends on it); the source memoises, so loadLevel reuses those reads.
    const crumbs = await crumbsFor(t, this.reader);
    const siteCrumb = crumbs.find((c) => c.target.level === 'site')?.target;
    const site = siteCrumb?.level === 'site' ? (await this.reader.site(siteCrumb.siteId)).site : null;
    const theme = this.themeOf({ level: t.level, site });
    const key = `${formatView(t)}|${theme.id}`;
    const hit = this.bundles.get(key);
    if (hit && hit.theme === theme) return hit;
    const scene = await loadLevel(t, this.reader);
    const caches = this.cachesFor(theme);
    const view = buildLevelContainer(scene, caches.tex);
    const home = homeBox(scene);
    const c = centre(home);
    const b: Bundle = { target: t, theme, site, caches, scene, view, crumbs, cam: { cx: c.x, cy: c.y, scale: fitScale(home, this.viewSize) } };
    this.bundles.set(key, b);
    return b;
  }

  private async doRefreshTheme(): Promise<void> {
    const prev = this.cur;
    if (!prev) return;
    const next = await this.bundleFor(prev.target);
    if (next === prev) return;
    next.cam = { ...prev.cam };
    this.levelLayer.removeChild(prev.view.root);
    this.levelLayer.addChild(next.view.root);
    next.view.root.alpha = 1;
    this.endDragFor(next);
    this.cur = next;
    this.selected = null;
    this.hoverTile = null;
    this.redrawMarks();
    this.showTheme(next.theme);
    this.applyCamera();
    this.refreshHud();
    this.interaction.selected?.(null);
  }

  /**
   * A level swap (navigation, theme change) ends any object drag: its session was computed against the old level
   * (another scene, another camera), so a drop there would stage a move nobody aimed. The ghost goes with it.
   */
  private endDragFor(next: Bundle): void {
    if (this.cur === next) return;
    if (this.drag) this.cancelDrag();
    this.setGhost(null);
  }

  private async doGoto(t: Target, direction: 'in' | 'out' | 'none', from?: Item): Promise<void> {
    if (this.cur && sameTarget(this.cur.target, t)) return;
    let next: Bundle;
    try {
      next = await this.bundleFor(t);
      this.error = null;
    } catch (e) {
      this.error = `Cannot open ${formatView(t)}: ${e instanceof Error ? e.message : String(e)}`;
      this.refreshHud();
      throw e;
    }
    const prev = this.cur;
    // Centre on the object entered; when zooming out, on the object we came from.
    const home = homeBox(next.scene);
    let focus = centre(home);
    // A rack level is one rack that fits the screen: it opens framed whole, from wherever we came.
    if (direction === 'out' && prev && next.scene.space !== 'rack') {
      const child = prev.crumbs[prev.crumbs.length - 1];
      const came = child && this.entityForCrumb(next.scene, prev);
      if (came && child) {
        const box = unionBox(itemsOf(next.scene, came));
        if (box) focus = centre(box);
      }
    }
    const scale = fitScale(home, this.viewSize);
    if (direction === 'out') focus = focusInside(focus, home, scale, this.viewSize); // the level stays in view
    next.cam = { cx: focus.x, cy: focus.y, scale };
    this.endDragFor(next);
    this.cur = next;
    this.selected = null;
    this.hoverTile = null;
    this.redrawMarks();
    this.levelLayer.addChild(next.view.root);
    if (prev && prev !== next && this.opts.animate !== false && direction !== 'none') {
      const oldFocus = direction === 'in' && from ? centre(unionBox([from])!) : { x: prev.cam.cx, y: prev.cam.cy };
      await this.animate(prev, next, planTransition(direction, prev.cam, oldFocus, next.cam));
    }
    if (prev && prev !== next) this.levelLayer.removeChild(prev.view.root);
    next.view.root.alpha = 1;
    this.showTheme(next.theme);
    this.applyCamera();
    this.refreshHud();
    this.showHoverName(null); // it named an object of the old level
    this.interaction.selected?.(null);
    this.interaction.levelChanged?.();
  }

  /**
   * Rebuilds the current level in place (same camera, same selection when the entity still exists): after a
   * staged edit (`refetch` false: the source wrapper changed) or after an apply (`refetch` true: read NetBox again).
   */
  refresh(refetch: boolean): Promise<void> {
    const run = this.busy.then(async () => {
      const cur = this.cur;
      if (refetch) this.src.clear();
      for (const [k, b] of this.bundles) if (b !== cur) this.bundles.delete(k);
      if (!cur) return;
      this.bundles.delete(`${formatView(cur.target)}|${cur.theme.id}`);
      let next: Bundle;
      try {
        next = await this.bundleFor(cur.target);
        this.error = null;
      } catch (e) {
        this.error = `Cannot reload ${formatView(cur.target)}: ${e instanceof Error ? e.message : String(e)}`;
        this.bundles.set(`${formatView(cur.target)}|${cur.theme.id}`, cur);
        this.refreshHud();
        return;
      }
      next.cam = cur.cam;
      const selKey = this.selected && entityKey(this.selected.entity);
      this.levelLayer.addChild(next.view.root);
      this.levelLayer.removeChild(cur.view.root);
      cur.view.root.destroy({ children: true });
      this.cur = next;
      const item = selKey ? next.scene.items.find((i) => i.entity && entityKey(i.entity) === selKey) : undefined;
      this.selected = item ? { entity: item.entity!, item } : null;
      // A rebuild can change the theme (a staged site iso_theme undone or applied): the page follows the sprites
      // (previously: background, HUD, picker and panels kept the old theme).
      if (next.theme !== cur.theme) this.showTheme(next.theme);
      this.applyCamera();
      this.redrawMarks();
      this.refreshHud();
      this.interaction.selected?.(this.selected?.entity ?? null);
    });
    this.busy = run.catch(() => undefined);
    return run;
  }

  setInteraction(i: Interaction): void {
    this.interaction = i;
  }

  /** Entity keys drawn with the trace mark at every level (racks also light up when they hold a traced device). */
  setHighlights(keys: ReadonlySet<string>): void {
    this.highlights = keys;
    this.redrawMarks();
  }

  /** A translucent copy of a sprite at level px (x, y), tinted (drag preview); null removes it. */
  setGhost(g: { sprite: SpriteDesc; x: number; y: number; tint: string } | null): void {
    // Clear the layer the ghost was drawn in, which may belong to a level no longer on screen (cached bundle).
    if (this.ghostLayer && !this.ghostLayer.destroyed) for (const c of this.ghostLayer.removeChildren()) c.destroy();
    this.ghostLayer = null;
    const layer = this.cur?.view.ghost;
    if (!layer || !g) return;
    this.ghostLayer = layer;
    const s = new Sprite(this.cur!.caches.tex.get(g.sprite));
    s.position.set(g.x, g.y);
    s.tint = g.tint;
    s.alpha = 0.75;
    layer.addChild(s);
  }

  /** Ends an object drag without dropping (Esc). The pointer, if still down, then does nothing until released. */
  cancelDrag(): void {
    const d = this.drag;
    this.drag = null;
    this.dragCancelled = true;
    d?.cancel();
    this.setGhost(null);
  }
  private dragCancelled = false;
  private ghostLayer: Container | null = null;

  /** True while a navigation or theme change (and its transition) is queued or running. */
  get transitioning(): boolean {
    return this.busyCount > 0;
  }

  get dragging(): boolean {
    return this.drag !== null;
  }

  /** Screen (canvas) point of a level point (scale 1). */
  screenOfLevel(lx: number, ly: number): { x: number; y: number } | null {
    return this.cur ? levelToScreen(this.cur.cam, this.viewSize, lx, ly) : null;
  }

  /** A sprite's pixels in the theme on screen (tray thumbnails); null before the first level. */
  spriteBuffer(d: SpriteDesc): PixelBuffer | null {
    return this.cur ? this.cur.caches.sprites.get(d) : null;
  }

  /** Level point (scale 1) under a client (DOM) point. */
  levelAtClient(clientX: number, clientY: number): DragPoint | null {
    if (!this.cur) return null;
    const r = this.app.canvas.getBoundingClientRect();
    const sx = ((clientX - r.left) * this.viewSize.width) / r.width;
    const sy = ((clientY - r.top) * this.viewSize.height) / r.height;
    const l = screenToLevel(this.cur.cam, this.viewSize, sx, sy);
    return { lx: l.x, ly: l.y, clientX, clientY };
  }

  /** The entity in `scene` that the previous level was about (rack -> its rack item in the room, ...). */
  private entityForCrumb(scene: LevelScene, prev: Bundle): Entity | null {
    const t = prev.target;
    const want =
      t.level === 'site' ? `site:${t.siteId}` : t.level === 'room' ? `location:${t.locationId}` : t.level === 'rack' ? `rack:${t.rackId}` : t.level === 'device' ? `device:${t.deviceId}` : null;
    if (!want) return null;
    return scene.items.find((i) => i.entity && entityKey(i.entity) === want)?.entity ?? null;
  }

  private animate(prev: Bundle, next: Bundle, plan: ReturnType<typeof planTransition>): Promise<void> {
    return new Promise((resolve) => {
      const t0 = performance.now();
      const step = () => {
        const t = Math.min(1, (performance.now() - t0) / TRANSITION_MS);
        const f = transitionFrame(plan, t);
        this.place(prev.view.root, f.oldCam);
        prev.view.root.alpha = f.oldAlpha;
        this.place(next.view.root, f.newCam);
        next.view.root.alpha = f.newAlpha;
        if (t >= 1) {
          this.app.ticker.remove(step);
          prev.view.root.alpha = 1;
          resolve();
        }
      };
      this.app.ticker.add(step);
      step();
    });
  }

  // ------------------------------------------------------------------------------------------------- camera

  private place(root: Container, cam: CameraState): void {
    const o = containerOffset(cam, this.viewSize);
    root.position.set(o.x, o.y);
    root.scale.set(cam.scale);
  }

  private applyCamera(): void {
    if (this.cur) this.place(this.cur.view.root, this.cur.cam);
  }

  setCamera(cam: CameraState): void {
    if (!this.cur) return;
    this.cur.cam = clampTo(cam, this.cur.scene.bounds);
    this.applyCamera();
    this.refreshHud();
  }

  zoomBy(dir: 1 | -1, at?: { x: number; y: number }): void {
    if (!this.cur) return;
    const v = this.viewSize;
    const p = at ?? { x: v.width / 2, y: v.height / 2 };
    this.setCamera(zoomAt(this.cur.cam, v, p.x, p.y, dir));
  }

  setScale(scale: RenderScale): void {
    if (this.cur) this.setCamera({ ...this.cur.cam, scale });
  }

  // ------------------------------------------------------------------------------------------------- picking

  /** The entity drawn at a screen (canvas) point, or null. */
  pickScreen(sx: number, sy: number): Pick | null {
    if (!this.cur) return null;
    const p = screenToLevel(this.cur.cam, this.viewSize, sx, sy);
    return pickAt(this.cur.scene, this.cur.caches.sprites, p.x, p.y);
  }

  /**
   * Screen point of the visible pixel of an entity nearest the centre of everything that draws it (tests, HUD).
   * Visible = picking at that point returns this entity. Null if it is fully hidden.
   */
  screenOf(key: string): { x: number; y: number } | null {
    if (!this.cur) return null;
    const scene = this.cur.scene;
    const sprites = this.cur.caches.sprites;
    const items = scene.items.filter((i) => i.entity && entityKey(i.entity) === key);
    const box = unionBox(items);
    if (!box) return null;
    const c = centre(box);
    let best: { x: number; y: number } | null = null;
    let bestD = Infinity;
    for (const item of items) {
      const buf = sprites.get(item.sprite);
      for (let y = 0; y < buf.height; y++)
        for (let x = 0; x < buf.width; x++) {
          if (buf.data[(y * buf.width + x) * 4 + 3] === 0) continue;
          const lx = item.x + x + 0.5, ly = item.y + y + 0.5;
          const d = (lx - c.x) ** 2 + (ly - c.y) ** 2;
          if (d >= bestD) continue;
          const hit = pickAt(scene, sprites, lx, ly);
          if (hit && entityKey(hit.entity) === key) (bestD = d), (best = { x: lx, y: ly });
        }
    }
    return best && levelToScreen(this.cur.cam, this.viewSize, best.x, best.y);
  }

  select(p: Pick | null): void {
    this.selected = p;
    this.redrawMarks();
    this.refreshHud();
    this.interaction.selected?.(p?.entity ?? null);
  }

  /** Selects the entity with this key in the current level (null if it is not drawn here). */
  selectKey(key: string | null): void {
    const item = key ? this.cur?.scene.items.find((i) => i.entity && entityKey(i.entity) === key) : undefined;
    this.select(item ? { entity: item.entity!, item } : null);
  }

  private redrawMarks(): void {
    if (!this.cur) return;
    const sel = this.selected ? (this.cur.scene.space === 'iso' && this.selected.entity.kind === 'location' ? itemsOf(this.cur.scene, this.selected.entity).filter((i) => i.layer === 'ground') : [this.selected.item]) : [];
    const hl = this.highlights;
    const traced = hl.size
      ? this.cur.scene.items.filter(
          (i) => i.entity && (hl.has(entityKey(i.entity)) || (i.sprite.kind === 'rack' && i.sprite.devices.some((d) => hl.has(`device:${d.id}`)))),
        )
      : [];
    const pal = this.cur.theme.palette;
    drawMarks(this.cur.view, this.cur.scene, this.cur.caches.tex, sel, this.hoverTile, traced, pal.accent_alt, pal.highlight);
  }

  // ------------------------------------------------------------------------------------------------- input

  private bindInput(): void {
    const c = this.app.canvas;
    const pointers = new Map<number, { x: number; y: number }>();
    let downAt: { x: number; y: number } | null = null;
    let downEvent: PointerEvent | null = null;
    let dragged = false;
    let pressedBusy = false;
    let pinchStart = 0;
    const local = (e: PointerEvent | WheelEvent | MouseEvent) => {
      const r = c.getBoundingClientRect();
      return { x: ((e.clientX - r.left) * this.viewSize.width) / r.width, y: ((e.clientY - r.top) * this.viewSize.height) / r.height };
    };
    const dragPoint = (e: PointerEvent): DragPoint => this.levelAtClient(e.clientX, e.clientY) ?? { lx: 0, ly: 0, clientX: e.clientX, clientY: e.clientY };
    const dist = () => {
      const [a, b] = [...pointers.values()];
      return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
    };
    c.addEventListener('pointerdown', (e) => {
      try {
        c.setPointerCapture(e.pointerId); // keeps a drag alive outside the canvas
      } catch {
        /* synthetic or already-released pointer: capture is a convenience only */
      }
      pointers.set(e.pointerId, local(e));
      if (pointers.size === 1) (downAt = local(e)), (downEvent = e), (dragged = false), (this.dragCancelled = false), (pressedBusy = this.busyCount > 0);
      if (pointers.size === 2) pinchStart = dist();
    });
    c.addEventListener('pointermove', (e) => {
      const p = local(e);
      const last = pointers.get(e.pointerId);
      if (last) {
        pointers.set(e.pointerId, p);
        if (pointers.size === 2) {
          const s = pinchStep(pinchStart, dist());
          if (s) {
            const [a, b] = [...pointers.values()];
            this.zoomBy(s, { x: (a!.x + b!.x) / 2, y: (a!.y + b!.y) / 2 });
            pinchStart = dist();
          }
          dragged = true;
          return;
        }
        if (this.drag) return this.drag.move(dragPoint(e));
        if (this.dragCancelled) return;
        if (!dragged && downAt && Math.hypot(p.x - downAt.x, p.y - downAt.y) > DRAG_PX) {
          dragged = true;
          // An object under the press point may start an object drag (editor); otherwise the drag pans. Not while a
          // navigation runs or when the press came during one: the camera and the level are not the ones the user
          // aimed at (a double-click to zoom in followed at once by a drag staged a move on the old level).
          const still = !pressedBusy && this.busyCount === 0;
          const hit = this.interaction.dragStart && still ? this.pickScreen(downAt.x, downAt.y) : null;
          this.drag = hit ? this.interaction.dragStart!(hit, dragPoint(downEvent!)) : null;
          if (this.drag) return this.drag.move(dragPoint(e));
        }
        if (dragged && this.cur) this.setCamera(panBy(this.cur.cam, p.x - last.x, p.y - last.y));
        return;
      }
      this.hover(p);
      this.interaction.hover?.(dragPoint(e));
    });
    const up = (e: PointerEvent) => {
      const was = pointers.get(e.pointerId);
      pointers.delete(e.pointerId);
      const drag = this.drag;
      if (drag) {
        this.drag = null;
        this.setGhost(null);
        drag.drop(dragPoint(e));
      } else if (pointers.size === 0 && was && !dragged && !this.dragCancelled && !this.interaction.click?.(dragPoint(e))) this.select(this.pickScreen(was.x, was.y));
      if (pointers.size === 0) downAt = null;
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointerleave', () => this.showHoverName(null));
    c.addEventListener('pointercancel', (e) => {
      pointers.delete(e.pointerId);
      if (this.drag) this.cancelDrag();
    });
    c.addEventListener('dblclick', (e) => {
      const p = local(e);
      const hit = this.pickScreen(p.x, p.y);
      if (hit) void this.enter(hit.entity, hit.item).catch(() => undefined);
    });
    c.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        if (e.deltaY) this.zoomBy(e.deltaY < 0 ? 1 : -1, local(e));
      },
      { passive: false },
    );
    window.addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (this.interaction.key?.(e)) return;
      if (e.key === 'Escape' || e.key === 'Backspace') void this.back().catch(() => undefined);
      else if (e.key === '+' || e.key === '=') this.zoomBy(1);
      else if (e.key === '-') this.zoomBy(-1);
      else if (e.key === 'Enter' && this.selected) void this.enter(this.selected.entity, this.selected.item).catch(() => undefined);
    });
  }

  /** The name of the object under the pointer, next to it (every level; ports and cables have no canvas label). */
  private hoverName: HTMLElement | null = null;

  private showHoverName(p: { x: number; y: number } | null): void {
    const hit = p && this.cur ? this.pickScreen(p.x, p.y) : null;
    if (!this.hoverName) {
      this.hoverName = document.createElement('div');
      this.hoverName.id = 'hover-name';
      this.host.appendChild(this.hoverName);
    }
    const el = this.hoverName;
    if (!hit || !p) return void (el.style.display = 'none');
    const pal = this.shownTheme.palette;
    const r = this.app.canvas.getBoundingClientRect();
    const cx = r.left + (p.x * r.width) / this.viewSize.width, cy = r.top + (p.y * r.height) / this.viewSize.height;
    el.textContent = entityName(hit.entity);
    el.dataset.key = entityKey(hit.entity);
    el.style.cssText = `position:fixed;left:${Math.round(cx + 14)}px;top:${Math.round(cy + 14)}px;pointer-events:none;z-index:5;
      font:12px/1.3 ui-monospace,monospace;color:${pal.text};background:${pal.background};border:1px solid ${pal.highlight};padding:1px 4px`;
  }

  private hover(p: { x: number; y: number }): void {
    this.showHoverName(p);
    if (!this.cur || this.cur.scene.space !== 'iso') return;
    const l = screenToLevel(this.cur.cam, this.viewSize, p.x, p.y);
    const t = tileAt(this.cur.scene, l.x, l.y);
    if (t?.tx === this.hoverTile?.tx && t?.ty === this.hoverTile?.ty) return;
    this.hoverTile = t;
    this.redrawMarks();
  }

  private refreshHud(): void {
    if (!this.hud) return;
    this.hud.update({
      level: this.cur?.scene.level ?? 'world',
      crumbs: this.cur?.crumbs ?? [{ target: WORLD, name: 'World' }],
      selected: this.selected ? entityName(this.selected.entity) : null,
      scale: this.cur?.cam.scale ?? 1,
      error: this.error,
      busy: this.busyCount > 0,
    });
    this.onUpdate?.();
  }

  /** Steps the render scale (used by tests to reach a scale without wheel events). */
  stepScale(dir: 1 | -1): void {
    if (this.cur) this.setScale(stepScale(this.cur.cam.scale, dir));
  }
}
