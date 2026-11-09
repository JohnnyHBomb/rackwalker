// The editor: turns the viewer into a NetBox editor through the proxy's change API. Thin by design:
//  * validity while dragging is a local free-tile / free-U check on the scene data, confirmed by POST /preview of
//    the basket plus the candidate op; the proxy's answer is what counts (a drop it refuses is not staged);
//  * staged ops are shown by re-rendering the level through StagedSource (no second scene model);
//  * apply sends the basket; dry-run (403 write_disabled) keeps it; success clears it and reloads from the proxy.
import { U_PX } from '../contracts/iso';
import type { ChangeOp, ChangeResult, Device, DeviceInfo, IdOrRef, Rack, RackScene, SiteScene, TraceResponse } from '../contracts/scene.gen';
import { SLOT_SPECS, type Theme } from '../contracts/theme';
import { entityKey, type Entity } from '../engine/entity';
import { layoutSite, type SiteLayout } from '../engine/layout';
import { formatView, type Target } from '../engine/nav';
import { footprintCenter, screenToTile } from '../engine/projection';
import { rackItem } from '../engine/scene';
import type { SpriteDesc } from '../engine/sprites';
import { SceneSourceError, type SceneSource } from '../engine/source';
import type { DragPoint, DragSession, RackWalkerView } from '../engine/view';
import type { ChangeApi } from './api';
import { Basket, type Staged } from './basket';
import { fieldLabel, panelModel, parseInput, show, showField, type Names, type Row } from './fields';
import { keyCommand } from './keys';
import { endName, failedApplyText, staleIndexes } from './text';
import { EMPTY_STAGE, NEXT_FACING, StagedSource, freePosition, lockOf, positionAt, slotProblem, stageOf, tileProblem, type Stage } from './stage';
import { badgeText, bufferToDataUrl, button, edgeColour, el, frameCss, mutedText, setEnabled, toneColour, type Tone } from './ui';

type Verdict = 'pending' | 'ok' | 'error' | 'unconfirmed';

/** A drop candidate: the op it would stage, why it is locally invalid (or null), a short description, its ghost. */
/** An object drag, plus what the add-rack tool asks before it stages on a click. */
interface Session extends DragSession {
  /** The proxy's refusal of `op` when it is the candidate on screen and the preview refused it, else null. */
  refusal(op: ChangeOp): string | null;
  /** Computes the candidate again at the last pointer position (data the candidate needed has arrived). */
  recheck(): void;
}

interface Candidate {
  readonly op: ChangeOp | null;
  readonly problem: string | null;
  readonly text: string;
  readonly staged?: Omit<Staged, 'op' | 'label'>;
  readonly ghost?: { sprite: SpriteDesc; x: number; y: number };
}

const PREVIEW_DEBOUNCE_MS = 120;
/** A drag asks the proxy only about a candidate the pointer rests on this long (previously: one POST per U crossed
 *  queued up at the proxy, 7 previews for one short drag, and the verdict came 5 s after the pointer stopped). */
export const DRAG_PREVIEW_DEBOUNCE_MS = 200;

export class Editor {
  readonly basket = new Basket();
  private stage: Stage = EMPTY_STAGE;
  private view!: RackWalkerView;
  private reader!: StagedSource;
  private netboxBase: string | null = null;
  private writeEnabled: boolean | null = null;
  // Context of the current level (staged data).
  private site: SiteScene | null = null;
  private layout: SiteLayout | null = null;
  private rack: RackScene | null = null;
  private roomId: number | null = null;
  // Selection, trace, tool, drag.
  private selected: Entity | null = null;
  private info: DeviceInfo | null = null;
  private trace: TraceResponse | null = null;
  /** Asked before every trace; false refuses it (the game gates tracing behind a tool). Null: tracing is always allowed. */
  traceGate: (() => boolean) | null = null;
  private tool: 'add-rack' | null = null;
  private dragging: Session | null = null;
  private ghostText: { text: string; verdict: Verdict; problem: string | null } | null = null;
  // Basket preview.
  private result: ChangeResult | null = null;
  private resultFor = -1;
  private seq = 0;
  private message = '';
  /** Colour of the basket message: ok for a completed apply, err for failures, warn for the rest. */
  private tone: Tone = 'warn';
  private busy = false;
  // DOM.
  private readonly root: HTMLElement;
  private readonly infoEl: HTMLElement;
  private readonly basketEl: HTMLElement;
  private readonly trayEl: HTMLElement;
  private readonly statusEl: HTMLElement;
  private readonly reasonEl: HTMLInputElement;
  /** The theme the panels are drawn with: the theme of the level on screen (set on attach, then on every swap). */
  private theme!: Theme;

  /**
   * `readOnly`: why editing is off, or null. The canned fixture and synthetic sources set it: their ids are not
   * NetBox's, and an edit sent to the proxy would write a live NetBox object by a fixture id.
   */
  constructor(
    host: HTMLElement,
    private readonly api: ChangeApi,
    readonly readOnly: string | null = null,
  ) {
    this.root = el('div', { id: 'editor', style: 'position:fixed;right:8px;top:56px;bottom:8px;width:310px;display:flex;flex-direction:column;gap:6px;pointer-events:none' });
    this.infoEl = el('section', { id: 'info-panel' });
    this.basketEl = el('section', { id: 'basket' });
    this.root.append(this.infoEl, this.basketEl);
    this.trayEl = el('section', { id: 'tray' });
    this.statusEl = el('div', { id: 'drag-status' });
    // The changelog reason survives basket re-renders (one element, re-appended each time).
    this.reasonEl = el('input', { id: 'basket-reason', type: 'text', maxlength: '180', placeholder: 'reason (NetBox changelog)', title: 'Sent with Apply; NetBox stores it on each change as "rackwalker: <reason>"' });
    host.append(this.root, this.trayEl, this.statusEl);
    this.basket.onChange(() => this.basketChanged());
  }

  /** Re-skins the panels when the theme on screen changed (level swap into a site with its own theme, picker). */
  syncTheme(): void {
    const t = this.view.theme;
    if (t === this.theme) return;
    this.theme = t;
    const frame = frameCss(t);
    const p = t.palette;
    // Every panel starts hidden; its render below shows it when it has content.
    this.infoEl.style.cssText = `${frame};pointer-events:auto;overflow:auto;max-height:55%;display:none`;
    this.basketEl.style.cssText = `${frame};pointer-events:auto;overflow:auto;flex:0 1 auto;min-height:0`;
    this.trayEl.style.cssText = `${frame};position:fixed;left:8px;bottom:8px;max-width:calc(100% - 340px);display:none`;
    this.statusEl.style.cssText = `${frame};position:fixed;left:8px;top:48px;max-width:calc(100% - 340px);display:none;pointer-events:none`;
    this.reasonEl.style.cssText = `font:inherit;width:100%;box-sizing:border-box;margin:2px 0;color:${p.text};background:${p.background};border:1px solid ${edgeColour(t)}`;
    this.renderBasket();
    this.renderInfo();
    this.renderTray();
    this.renderStatus();
  }

  /** The view's source wrapper: the proxy's scenes with the basket on top. */
  wrap = (src: SceneSource): SceneSource => (this.reader = new StagedSource(src, () => this.stage));

  attach(view: RackWalkerView): void {
    this.view = view;
    this.syncTheme();
    view.setInteraction({
      dragStart: (pick, p) => this.dragStart(pick.entity, pick.item.y, p),
      hover: (p) => this.toolHover(p),
      click: (p) => this.toolClick(p),
      key: (e) => this.key(e),
      selected: (e) => this.select(e),
      levelChanged: () => {
        // A drag from the tray belongs to the level it started on (the view already ended its own drags).
        if (this.dragging && this.dragging !== this.toolSession) this.dragging.cancel();
        if (this.trace) this.view.setHighlights(this.traceKeys()); // world links light up only at world level
        this.syncTheme();
        void this.loadContext();
      },
    });
    if (!this.readOnly) this.api.health().then(
      (h) => {
        this.netboxBase = h.netbox.url;
        this.writeEnabled = h.write_enabled;
        this.renderBasket();
        this.renderInfo();
      },
      () => undefined,
    );
    this.renderBasket();
  }

  // ------------------------------------------------------------------------------------------- state for tests

  state() {
    return {
      ops: this.basket.entries.map((s, i) => ({ label: s.label, status: this.resultFor === this.seq ? (this.result?.ops[i]?.status ?? null) : null })),
      message: this.message,
      ghost: this.ghostText,
      panel: this.selected ? entityKey(this.selected) : null,
      trace: this.trace ? [...this.traceKeys()] : [],
      tool: this.tool,
      writeEnabled: this.writeEnabled,
    };
  }

  /** Resolves when the basket preview matches the current basket (tests). */
  async idle(): Promise<void> {
    for (let i = 0; i < 200 && (this.busy || (this.basket.size > 0 && this.resultFor !== this.seq)); i++) await new Promise((r) => setTimeout(r, 25));
    await this.view.settled();
  }

  // ------------------------------------------------------------------------------------------- level context

  private get target(): Target | null {
    const c = this.view.crumbs;
    return c.length ? c[c.length - 1]!.target : null;
  }

  /** formatView of the level the context (site, layout, rack) was loaded for; drags need it to be the one shown. */
  private contextFor: string | null = null;

  private async loadContext(): Promise<void> {
    const t = this.target;
    const key = t ? formatView(t) : null;
    this.contextFor = null;
    this.site = this.layout = this.rack = null;
    this.roomId = null;
    try {
      if (t?.level === 'room' || t?.level === 'site') {
        this.site = await this.reader.site(t.siteId);
        this.roomId = t.level === 'room' ? t.locationId : null;
      } else if (t?.level === 'rack') {
        this.rack = await this.reader.rack(t.rackId);
        this.site = await this.reader.site(this.rack.rack.site_id);
        this.roomId = this.rack.rack.location_id;
      } else if (t?.level === 'device') {
        // Only for names in the panel (rack and location of the device); nothing is dragged at device level.
        const d = await this.reader.device(t.deviceId);
        this.site = await this.reader.site(d.device.site_id);
      }
    } catch {
      /* the view already shows the read error in the HUD */
    }
    const now = this.target;
    if ((now ? formatView(now) : null) !== key) return; // the level changed meanwhile: its own load sets the context
    this.layout = this.site ? layoutSite(this.site) : null;
    this.contextFor = key;
    if (t?.level !== 'room' && this.tool) {
      this.toolSession?.cancel();
      this.toolSession = null;
      this.tool = null;
    }
    this.renderTray();
    this.renderInfo(); // id fields name what they point at once the site is known
    this.renderBasket();
  }

  /** A real id, or "@ref" for a rack created in the basket (temporary negative id). */
  private ref(id: number): IdOrRef {
    if (id > 0) return id;
    for (const [r, t] of this.stage.refs) if (t === id) return `@${r}`;
    return id;
  }

  // ------------------------------------------------------------------------------------------- staging

  private stageOp(op: ChangeOp, label: string, extra: Omit<Staged, 'op' | 'label'> = {}): void {
    if (this.readOnly) return this.flash(`Not staged: ${this.readOnly}`);
    if (this.frozen()) return;
    this.tone = 'warn';
    this.message = '';
    this.basket.add({ op, label, ...extra });
  }

  /**
   * True (and says so) while an Apply is in flight: the basket must not change until the answer is in, because the
   * answer names ops by their index in the set that was sent, and success empties what was sent (previously: an
   * op staged or undone during the flight was lost, or the wrong op left the basket).
   */
  private frozen(): boolean {
    if (!this.busy) return false;
    this.flash('Apply is still writing to NetBox: wait for its answer, then make further changes.');
    return true;
  }

  private undo(index: number): void {
    if (!this.frozen()) this.basket.undo(index);
  }

  /** Set by apply before it changes the basket: the rebuild then reads NetBox again instead of the memo. */
  private refetchNext = false;

  private basketChanged(): void {
    this.stage = stageOf(this.basket.entries);
    this.seq++;
    this.renderBasket();
    const refetch = this.refetchNext;
    this.refetchNext = false;
    void this.view.refresh(refetch).then(() => this.loadContext());
    const seq = this.seq;
    setTimeout(() => void this.runPreview(seq), PREVIEW_DEBOUNCE_MS);
  }

  private async runPreview(seq: number): Promise<void> {
    if (seq !== this.seq) return;
    const cs = this.basket.changeSet();
    if (!cs) {
      this.result = null;
      this.resultFor = seq;
      return this.renderBasket();
    }
    try {
      const r = await this.api.preview(cs);
      if (seq !== this.seq) return;
      this.result = r;
      this.resultFor = seq;
      this.noteStale(r);
    } catch (e) {
      if (seq !== this.seq) return;
      this.result = null;
      this.resultFor = seq;
      this.tone = 'err';
      this.message = `Preview failed: ${errText(e)}`;
    }
    this.renderBasket();
  }

  async apply(): Promise<void> {
    const cs = this.basket.changeSet(undefined, this.reasonEl.value.trim() || undefined);
    if (!cs || this.applyBlocked()) return;
    this.busy = true;
    this.tone = 'warn';
    this.message = 'Applying…';
    this.renderBasket();
    let reload: Promise<void> | null = null;
    try {
      const r = await this.api.apply(cs);
      this.writeEnabled = r.write_enabled;
      if (r.applied) {
        this.tone = 'ok';
        this.message = `Applied ${r.ops.length} change${r.ops.length === 1 ? '' : 's'} to NetBox (see its changelog).`;
        this.refetchNext = true;
        this.reasonEl.value = '';
        this.basket.clear();
      } else {
        const failed = r.ops.find((o) => o.status === 'error');
        const d = (failed?.error?.details ?? {}) as { written_ops?: number[]; outcome_unknown?: boolean };
        this.result = r;
        this.resultFor = this.seq;
        this.noteStale(r);
        this.tone = 'err';
        this.message = failedApplyText(r);
        // Ops NetBox confirmed leave the basket in both cases (previously: with outcome_unknown they stayed, the
        // message said they had left, and the next Apply sent them again). The op whose outcome is unknown stays.
        if (d.written_ops?.length) {
          this.refetchNext = true;
          this.basket.settleWritten(d.written_ops, createdRacks(r, d.written_ops));
        } else if (d.outcome_unknown) reload = this.view.refresh(true).then(() => this.loadContext());
      }
    } catch (e) {
      if (e instanceof SceneSourceError && e.code === 'write_disabled') {
        this.writeEnabled = false;
        this.tone = 'warn';
        this.message = `Dry-run: the proxy has writing turned off (WRITE_ENABLED is not 1). Nothing was written; your ${this.basket.size} change${this.basket.size === 1 ? ' stays' : 's stay'} in the basket.`;
      } else {
        this.tone = 'err';
        this.message = `Apply failed: ${errText(e)}`;
      }
    } finally {
      this.busy = false;
    }
    await reload;
    await this.view.settled();
    this.renderBasket();
  }

  /**
   * Why Apply is off, or null when it may run. Apply needs the proxy's preview of exactly this basket, with no op
   * refused (previously: Apply ran while the preview was pending or had failed, so a change reached NetBox before
   * the user saw its diff).
   */
  private applyBlocked(): string | null {
    if (this.busy) return 'Apply is still writing to NetBox.';
    if (this.resultFor !== this.seq) return 'Waiting for the proxy to preview these changes.';
    if (!this.result) return 'No preview from the proxy: Apply needs one first (see the message above).';
    const bad = this.result.ops.find((o) => o.status === 'error');
    if (bad) return `The proxy refuses op ${bad.index + 1}: undo it or change it first.`;
    return null;
  }

  /**
   * A lock conflict means the scenes this tab holds are older than NetBox: the next rebuild reads NetBox again, so
   * a change staged after an undo carries the new last_updated (previously: it reused the stale one).
   */
  private noteStale(r: ChangeResult): void {
    if (staleIndexes(r).length) this.refetchNext = true;
  }

  /** "Reload from NetBox": the ops staged on old data leave the basket and every scene is read again. */
  reloadStale(): void {
    if (this.frozen()) return;
    const stale = staleIndexes(this.resultFor === this.seq ? this.result : null);
    this.tone = 'warn';
    this.message = stale.length
      ? `Read NetBox again. ${stale.length === 1 ? 'The change' : `${stale.length} changes`} made on old data left the basket: stage ${stale.length === 1 ? 'it' : 'them'} again on what NetBox has now.`
      : 'Read NetBox again.';
    if (stale.length) {
      this.refetchNext = true; // the rebuild that removeIndexes triggers reads NetBox
      this.basket.removeIndexes(stale);
    } else {
      this.refetchNext = false;
      void this.view.refresh(true).then(() => this.loadContext());
      this.renderBasket();
    }
  }

  discard(): void {
    if (this.frozen()) return;
    this.tone = 'warn';
    this.message = '';
    this.basket.clear();
  }

  // ------------------------------------------------------------------------------------------- drag and drop

  /** Starts an object drag from the canvas (room: racks; rack: devices). */
  private dragStart(e: Entity, itemY: number, p: DragPoint): DragSession | null {
    const t = this.target;
    // Only on the level the context was loaded for (not in the gap between a level swap and its context load).
    if (this.readOnly || !t || this.contextFor !== formatView(t) || this.view.transitioning) return null;
    if (t.level === 'room' && e.kind === 'rack' && this.site && this.layout) {
      // The rack follows the grab point: the offset from the press to its own tile's centre stays fixed.
      const spot = this.layout.racks.get(e.data.id);
      const c = spot ? footprintCenter(spot.x, spot.y) : { sx: p.lx, sy: p.ly };
      const grab = { dx: p.lx - c.sx, dy: p.ly - c.sy };
      return this.session((q) => this.rackCandidate(e.data, { ...q, lx: q.lx - grab.dx, ly: q.ly - grab.dy }));
    }
    if (t.level === 'rack' && e.kind === 'device' && this.rack && e.data.device_type.u_height > 0 && e.data.position !== null)
      return this.session((q) => this.deviceCandidate(e.data, q, p.ly - itemY));
    return null;
  }

  /** A drag driven by `candidate` (called on every move): ghost, local tint, preview confirmation, drop. */
  private session(candidate: (p: DragPoint) => Candidate): Session {
    let cur: Candidate | null = null;
    let last: DragPoint | null = null;
    let key = '';
    let verdict: Verdict = 'pending';
    let proxyError: string | null = null;
    let ended = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const show = () => {
      if (!cur || ended) return;
      const v: Verdict = cur.problem || !cur.op ? 'error' : verdict;
      this.ghostText = { text: cur.text, verdict: v, problem: cur.problem ?? proxyError };
      this.renderStatus();
    };
    const tint = () => {
      const pal = this.theme.palette;
      return cur?.problem || !cur?.op || verdict === 'error' ? pal.led_err : verdict === 'ok' ? pal.led_ok : pal.led_warn;
    };
    const end = () => {
      ended = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (this.dragging === s) this.dragging = null;
      this.ghostText = null;
      this.view.setGhost(null);
      this.renderStatus();
      this.highlightDrop(null);
    };
    const s: Session = {
      move: (p) => {
        if (ended) return;
        last = p;
        const c = candidate(p);
        cur = c;
        const k = JSON.stringify(c.op);
        if (k !== key) {
          key = k;
          verdict = 'pending';
          proxyError = null;
          if (timer !== null) clearTimeout(timer);
          timer = null;
          const op = c.op;
          if (op && !c.problem)
            timer = setTimeout(() => {
              timer = null;
              void this.confirm(op).then((r) => {
                if (key !== k || ended) return; // a late answer must not bring back the ghost of a finished drag
                verdict = r.verdict;
                proxyError = r.error;
                show();
                if (c.ghost) this.view.setGhost({ ...c.ghost, tint: tint() });
              });
            }, DRAG_PREVIEW_DEBOUNCE_MS);
        }
        this.view.setGhost(c.ghost ? { ...c.ghost, tint: tint() } : null);
        show();
      },
      drop: (p) => {
        if (ended) return;
        const c = candidate(p);
        const same = JSON.stringify(c.op) === key;
        end();
        if (!c.op || c.problem) {
          if (c.problem) this.flash(`Not staged: ${c.problem}`);
          return;
        }
        if (same && verdict === 'error') return this.flash(`Not staged: the proxy says ${proxyError}`);
        this.stageOp(c.op, c.text, c.staged);
      },
      cancel: () => end(),
      refusal: (op) => (!ended && JSON.stringify(op) === key && verdict === 'error' ? (proxyError ?? 'refused') : null),
      recheck: () => {
        if (!ended && last) s.move(last);
      },
    };
    this.dragging = s;
    return s;
  }

  /** Preview of basket + candidate: the candidate's own verdict. */
  private async confirm(op: ChangeOp): Promise<{ verdict: Verdict; error: string | null }> {
    try {
      const r = await this.api.preview(this.basket.changeSet(op)!);
      const last = r.ops[r.ops.length - 1];
      if (!last) return { verdict: 'unconfirmed', error: null };
      return last.status === 'error' ? { verdict: 'error', error: last.error?.message ?? 'refused' } : { verdict: 'ok', error: null };
    } catch {
      return { verdict: 'unconfirmed', error: null };
    }
  }

  private dropTargetAt(p: DragPoint): HTMLElement | null {
    for (const n of document.elementsFromPoint(p.clientX, p.clientY)) {
      const t = (n as HTMLElement).closest?.('[data-drop]') as HTMLElement | null;
      if (t) return t;
    }
    return null;
  }

  private highlightDrop(t: HTMLElement | null): void {
    for (const n of this.trayEl.querySelectorAll<HTMLElement>('[data-drop]')) n.style.outline = n === t ? `2px solid ${this.theme.palette.highlight}` : '';
  }

  private rackCandidate(rack: Rack, p: DragPoint): Candidate {
    const { tx, ty } = screenToTile(p.lx, p.ly);
    const spot = this.layout!.racks.get(rack.id);
    const problem = spot && spot.x === tx && spot.y === ty ? 'that is where it stands' : tileProblem(this.site!, this.layout!, this.roomId!, tx, ty, rack.id);
    const devices = this.site!.devices.filter((d) => d.rack_id === rack.id);
    const g = rackItem(rack, devices, tx, ty, rack.iso_facing ?? spot?.facing ?? 'S');
    return {
      op: { op: 'move_rack', target: { kind: 'rack', id: this.ref(rack.id) }, fields: { iso_x: tx, iso_y: ty }, ...lockOf(rack) } as ChangeOp,
      problem,
      text: `${rack.name} → tile ${tx},${ty}`,
      ghost: { sprite: g.sprite, x: g.x, y: g.y },
    };
  }

  /** Device drop: a U on the shown rack, the unracked tray, or another rack of the room (its first free U). */
  private deviceCandidate(device: Device, p: DragPoint, grabY: number): Candidate {
    const name = device.name ?? `device ${device.id}`;
    const face = device.face ?? 'front';
    const target = this.dropTargetAt(p);
    this.highlightDrop(target);
    if (target?.dataset.drop === 'tray') {
      if (device.rack_id === null) return { op: null, problem: 'already unracked', text: `${name}: already in the tray` };
      return { op: { op: 'unplace_device', target: { kind: 'device', id: device.id }, fields: {}, ...lockOf(device) }, problem: null, text: `${name} → unracked tray` };
    }
    if (target?.dataset.drop === 'rack') {
      const rackId = Number(target.dataset.rackId);
      const other = this.otherRacks.get(rackId);
      if (!other) return { op: null, problem: 'rack not loaded yet', text: `${name} → rack ${rackId}` };
      const pos = freePosition(other.rack, other.devices, device, face, device.position);
      if (pos === null) return { op: null, problem: `${other.rack.name} has no free ${device.device_type.u_height}U slot`, text: `${name} → ${other.rack.name}` };
      return this.placeOp(device, other.rack, pos, face, null);
    }
    const rs = this.target?.level === 'rack' ? this.rack : null;
    if (!rs || target) return { op: null, problem: null, text: `${name}: drop on a U slot, a rack or the tray` };
    const h = device.device_type.u_height;
    const pos = positionAt(rs.rack, p.ly - grabY, h);
    if (device.rack_id === rs.rack.id && device.position === pos && device.face === face) return { op: null, problem: 'that is where it is', text: `${name} → U${pos}` };
    const problem = slotProblem(rs.rack, rs.devices, device, pos, face);
    const y = (rs.rack.desc_units ? pos - rs.rack.starting_unit : rs.rack.starting_unit + rs.rack.u_height - (pos + h)) * U_PX;
    return { ...this.placeOp(device, rs.rack, pos, face, problem), ghost: { sprite: { kind: 'faceplate', device }, x: SLOT_SPECS.rack_unit.anchor[0], y } };
  }

  private placeOp(device: Device, rack: Rack, position: number, face: 'front' | 'rear', problem: string | null): Candidate {
    return {
      op: { op: 'place_device', target: { kind: 'device', id: device.id }, fields: { rack_id: this.ref(rack.id), position, face }, ...lockOf(device) },
      problem,
      text: `${device.name ?? `device ${device.id}`} → ${rack.name} U${position}`,
      staged: { device, rackLocationId: rack.location_id },
    };
  }

  /** Racks of the current room other than the shown one, with their (staged) devices: drop targets in the tray. */
  private otherRacks = new Map<number, RackScene>();

  /** Drag from a tray chip (an unracked device) onto a U slot of the shown rack. */
  private trayDrag(device: Device, down: PointerEvent): void {
    const s = this.session((q) => this.deviceCandidate(device, q, U_PX / 2));
    const pt = (e: PointerEvent): DragPoint => this.view.levelAtClient(e.clientX, e.clientY) ?? { lx: 0, ly: 0, clientX: e.clientX, clientY: e.clientY };
    const move = (e: PointerEvent) => s.move(pt(e));
    const up = (e: PointerEvent) => {
      stop();
      if (this.dragging === s) s.drop(pt(e));
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    const cancel = s.cancel;
    s.cancel = () => {
      stop();
      cancel();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    s.move(pt(down));
  }

  // ------------------------------------------------------------------------------------------- add-rack tool

  private newRackName(): string {
    const names = new Set(this.site?.racks.map((r) => r.name));
    let n = 1;
    while (names.has(`NEW-${n}`)) n++;
    return `NEW-${n}`;
  }

  private toolCandidate(p: DragPoint): Candidate & { op: ChangeOp } {
    const { tx, ty } = screenToTile(p.lx, p.ly);
    const name = this.newRackName();
    const site = this.site!;
    const rack: Rack = {
      id: 0, site_id: site.site.id, location_id: this.roomId, name, status: 'planned', u_height: 42, starting_unit: 1, desc_units: false,
      iso_x: tx, iso_y: ty, iso_facing: 'S', latitude: null, longitude: null, last_updated: null, netbox_url: null,
    };
    const g = rackItem(rack, [], tx, ty, 'S');
    const op: ChangeOp = {
      op: 'create_rack',
      target: { kind: 'rack', id: null },
      client_ref: 'candidate',
      fields: { site_id: site.site.id, location_id: this.roomId, name, status: 'planned', u_height: 42, iso_x: tx, iso_y: ty, iso_facing: 'S' },
    };
    return { op, problem: tileProblem(site, this.layout!, this.roomId!, tx, ty, null), text: `new rack ${name} at tile ${tx},${ty}`, ghost: { sprite: g.sprite, x: g.x, y: g.y } };
  }

  private toolSession: Session | null = null;

  setTool(tool: 'add-rack' | null): void {
    this.toolSession?.cancel();
    this.toolSession = null;
    this.tool = !this.readOnly && this.target?.level === 'room' && this.site ? tool : null;
    if (this.tool) this.toolSession = this.session((p) => this.toolCandidate(p));
    this.renderTray();
  }

  private toolHover(p: DragPoint): void {
    if (this.tool && this.toolSession) this.toolSession.move(p);
  }

  private toolClick(p: DragPoint): boolean {
    if (!this.tool || !this.toolSession) return false;
    const c = this.toolCandidate(p);
    if (c.problem) {
      this.toolSession.move(p);
      return true;
    }
    // A tile the proxy's preview refused is not staged either (previously: the tool staged it with status error).
    const refused = this.toolSession.refusal(c.op);
    if (refused) {
      this.flash(`Not staged: the proxy says ${refused}`);
      return true;
    }
    const ref = this.basket.nextRef();
    this.toolSession.cancel();
    this.toolSession = null;
    this.tool = null;
    this.stageOp({ ...c.op, client_ref: ref } as ChangeOp, c.text);
    return true;
  }

  // ------------------------------------------------------------------------------------------- keyboard

  private key(e: KeyboardEvent): boolean {
    const sel = this.selected;
    const cmd = keyCommand(
      {
        dragging: (this.dragging !== null && this.dragging !== this.toolSession) || this.view.dragging,
        tool: this.tool !== null,
        panelOpen: sel !== null,
        traceOn: this.trace !== null,
        selected: !sel ? null : sel.kind === 'device' || sel.kind === 'rack' || sel.kind === 'port' ? sel.kind : 'other',
      },
      { key: e.key, ctrl: e.ctrlKey || e.metaKey },
    );
    switch (cmd) {
      case 'cancel_drag':
        this.view.cancelDrag();
        this.dragging?.cancel();
        return true;
      case 'exit_tool':
        return this.setTool(null), true;
      case 'close_panel':
        return this.view.select(null), true;
      case 'clear_trace':
        return this.clearTrace(), true;
      case 'undo':
        e.preventDefault();
        this.undo(this.basket.size - 1);
        return true;
      case 'unplace':
        if (sel?.kind === 'device') this.unplace(sel.data);
        return true;
      case 'rotate':
        if (sel?.kind === 'rack') this.rotate(sel.data);
        return true;
      case 'trace':
        if (sel?.kind === 'port') void this.runTrace(sel.data.kind, sel.data.id);
        return true;
      case 'swallow':
        e.preventDefault();
        return true;
      case 'pass':
        return false;
    }
  }

  unplace(d: Device): void {
    if (d.rack_id === null || d.position === null) return this.flash(`${d.name ?? 'device'} is not in a rack U slot.`);
    this.stageOp({ op: 'unplace_device', target: { kind: 'device', id: d.id }, fields: {}, ...lockOf(d) }, `${d.name ?? `device ${d.id}`} → unracked tray`);
  }

  rotate(r: Rack): void {
    const spot = this.layout?.racks.get(r.id);
    const next = NEXT_FACING[r.iso_facing ?? spot?.facing ?? 'S'];
    this.stageOp({ op: 'move_rack', target: { kind: 'rack', id: this.ref(r.id) }, fields: { iso_facing: next }, ...lockOf(r) }, `${r.name} faces ${next}`);
  }

  // ------------------------------------------------------------------------------------------- trace

  async runTrace(kind: Parameters<SceneSource['trace']>[0], id: number): Promise<void> {
    if (this.traceGate && !this.traceGate()) return;
    try {
      this.trace = await this.reader.trace(kind, id);
      this.tone = 'warn';
      this.message = '';
    } catch (e) {
      this.trace = null;
      this.flash(`Trace failed: ${errText(e)}`);
    }
    this.view.setHighlights(this.traceKeys());
    this.renderInfo();
  }

  clearTrace(): void {
    this.trace = null;
    this.view.setHighlights(new Set());
    this.renderInfo();
  }

  /**
   * Entity keys on the traced path: ports, their devices, cables, the sites of circuit ends, every site of a path that
   * leaves its site, and the world links that carry its circuits or cables (previously: a direct cross-site cable
   * lit nothing at world level).
   */
  private traceKeys(): Set<string> {
    const keys = new Set<string>();
    if (!this.trace) return keys;
    const t = this.trace.trace;
    const circuits = new Set<number>();
    const cables = new Set<number>();
    const sites = new Set<number>();
    for (const end of [t.origin, ...t.hops.flatMap((h) => [...h.near_end, ...h.far_end])]) {
      keys.add(`port:${end.kind}:${end.id}`);
      if (end.device_id !== null) keys.add(`device:${end.device_id}`);
      if (end.site_id !== null) sites.add(end.site_id);
      if (end.circuit_id !== null) {
        circuits.add(end.circuit_id);
        if (end.site_id !== null) keys.add(`site:${end.site_id}`);
      }
    }
    if (sites.size > 1) for (const s of sites) keys.add(`site:${s}`);
    for (const h of t.hops)
      if (h.cable) {
        keys.add(`cable:${h.cable.id}`);
        cables.add(h.cable.id);
      }
    for (const i of this.view.scene?.items ?? [])
      if (i.entity?.kind === 'link' && (i.entity.data.circuits.some((c) => circuits.has(c.id)) || i.entity.data.cable_ids.some((c) => cables.has(c))))
        keys.add(entityKey(i.entity));
    return keys;
  }

  // ------------------------------------------------------------------------------------------- selection + panel

  private infoSeq = 0;

  /**
   * The view calls this on every selection, and again with the same entity after each rebuild (a staged edit, an
   * apply). DeviceInfo is read again every time: it carries the staged patches and, after an apply, NetBox's new
   * values (previously: a staged serial snapped back to the old one in its own input). The old info stays on
   * screen until the new one arrives, so a re-read does not flicker.
   */
  private select(e: Entity | null): void {
    const same = e && this.selected && entityKey(e) === entityKey(this.selected);
    this.selected = e;
    if (!same) this.info = null;
    this.renderInfo();
    if (e?.kind !== 'device') return;
    const seq = ++this.infoSeq;
    this.reader.device(e.data.id).then(
      (d) => {
        if (seq === this.infoSeq && this.selected && entityKey(this.selected) === `device:${e.data.id}`) (this.info = d.info), this.renderInfo();
      },
      () => undefined,
    );
  }

  /** Names for id fields, from the (staged) site on screen and the sites the world level shows. */
  private names(): Names {
    const site = this.site;
    const sites = new Map<number, string>();
    for (const i of this.view.scene?.items ?? []) if (i.entity?.kind === 'site') sites.set(i.entity.data.id, i.entity.data.name);
    if (site) sites.set(site.site.id, site.site.name);
    return {
      rack: (id) => site?.racks.find((r) => r.id === id)?.name ?? null,
      location: (id) => site?.locations.find((l) => l.id === id)?.name ?? null,
      site: (id) => sites.get(id) ?? null,
    };
  }

  private edit(row: Row, raw: string | boolean): void {
    const e = this.selected;
    if (!e || !row.input) return;
    const m = panelModel(e, this.info, this.netboxBase);
    const v = parseInput(row.field, row.input, raw);
    if (v === undefined) return this.flash(`${row.field}: not a valid value`);
    if (v === row.value || (v === null && row.value === undefined)) return;
    const lock = 'last_updated' in e.data ? lockOf(e.data as { id: number; last_updated: string | null }) : {};
    this.stageOp(
      { op: 'update_fields', target: { kind: m.editKind!, id: this.ref(m.id) }, fields: { [row.field]: v }, ...lock } as ChangeOp,
      `${m.title}: ${row.field} = ${show(v)}`,
    );
  }

  private renderInfo(): void {
    const e = this.selected;
    const box = this.infoEl;
    box.replaceChildren();
    if (!e && !this.trace) return void (box.style.display = 'none');
    box.style.display = '';
    const pal = this.theme.palette;
    const muted = mutedText(this.theme);
    if (e) {
      const m = panelModel(e, this.info, this.netboxBase, this.names());
      const staged = this.stage.patches.get(`${m.editKind ?? m.kind}:${m.id}`) ?? {};
      box.append(
        el(
          'header',
          { style: 'display:flex;align-items:center;gap:6px;margin-bottom:4px' },
          el('span', { style: `background:${pal.accent};color:${badgeText(this.theme, pal.accent)};padding:0 4px;text-transform:uppercase`, text: m.kind }),
          el('strong', { id: 'info-title', style: 'flex:1;overflow:hidden;text-overflow:ellipsis', text: m.title }),
          button(this.theme, '×', 'Close (Esc)', () => this.view.select(null), { id: 'info-close' }),
        ),
      );
      const table = el('table', { style: 'width:100%;border-collapse:collapse' });
      for (const row of m.rows) {
        const cell = el('td', { style: 'padding:1px 0' });
        if (row.input && !this.readOnly) cell.append(this.input(row, row.field in staged));
        else cell.append(row.display ?? show(row.value));
        table.append(el('tr', { 'data-field': row.field }, el('th', { style: `text-align:left;font-weight:normal;color:${muted};padding-right:6px;white-space:nowrap`, text: row.label }), cell));
      }
      box.append(table);
      const actions = el('div', { style: 'margin-top:4px' });
      if (e.kind === 'rack') actions.append(button(this.theme, '⟳ rotate', 'Stage a quarter turn (R)', () => this.rotate(e.data), { id: 'act-rotate' }));
      if (e.kind === 'device' && e.data.rack_id !== null && e.data.position !== null)
        actions.append(button(this.theme, '⇩ unrack', 'Stage moving it to the unracked tray (Delete)', () => this.unplace(e.data), { id: 'act-unplace' }));
      if (e.kind === 'port') actions.append(button(this.theme, '⇢ trace', 'Trace the cable path end to end (T)', () => void this.runTrace(e.data.kind, e.data.id), { id: 'act-trace' }));
      if (m.netboxUrl) {
        const a = el('a', { id: 'act-netbox', href: m.netboxUrl, target: '_blank', rel: 'noopener noreferrer', style: `color:${pal.accent_alt}`, text: 'open in NetBox ↗' });
        actions.append(a);
      }
      box.append(actions);
    }
    if (this.trace) box.append(this.traceView());
  }

  private input(row: Row, staged: boolean): HTMLElement {
    const pal = this.theme.palette;
    const style = `font:inherit;width:100%;box-sizing:border-box;color:${pal.text};background:${pal.background};border:1px solid ${staged ? pal.accent : edgeColour(this.theme)}`;
    let i: HTMLInputElement | HTMLSelectElement;
    if (row.input === 'choice') {
      i = el('select', { style });
      for (const c of row.choices ?? []) i.append(el('option', { value: c ?? '', text: c ?? '—' }));
      i.value = (row.value as string | null) ?? '';
    } else if (row.input === 'bool') {
      i = el('input', { type: 'checkbox' });
      i.checked = Boolean(row.value);
    } else {
      i = el('input', { style, type: 'text' });
      i.value = row.value === null || row.value === undefined ? '' : String(row.value);
    }
    i.name = row.field;
    i.title = staged ? 'staged in the change basket' : 'edit: the change goes into the basket';
    i.addEventListener('change', () => this.edit(row, i instanceof HTMLInputElement && i.type === 'checkbox' ? i.checked : i.value));
    i.addEventListener('keydown', (ev) => {
      if ((ev as KeyboardEvent).key === 'Escape') (i as HTMLElement).blur();
    });
    return i;
  }

  private traceView(): HTMLElement {
    const t = this.trace!.trace;
    const pal = this.theme.palette;
    const name = endName;
    const list = el('ol', { id: 'trace-hops', style: 'margin:2px 0;padding-left:18px' });
    for (const h of t.hops) {
      const li = el('li', {}, `${h.near_end.map(name).join(', ')} `, el('span', { style: `color:${pal.accent_alt}`, text: h.cable ? `—cable ${h.cable.label ?? h.cable.id}→` : '—circuit→' }), ` ${h.far_end.map(name).join(', ')}`);
      const dev = h.far_end.find((f) => f.device_id !== null)?.device_id;
      if (dev) {
        li.style.cursor = 'pointer';
        li.title = 'open this device';
        li.onclick = () => void this.view.goto({ level: 'device', deviceId: dev }, 'none').catch(() => undefined);
      }
      list.append(li);
    }
    const flags = t.hops.length ? `${t.hops.length} hop${t.hops.length === 1 ? '' : 's'}${t.is_complete ? ', complete' : ', incomplete'}${t.is_split ? ', split' : ''}` : 'no cable on this port';
    return el(
      'div',
      { id: 'trace', style: `margin-top:6px;border-top:1px solid ${pal.metal_shade}` },
      el('div', { style: 'display:flex;align-items:center;gap:6px' }, el('strong', { text: `TRACE ${name(t.origin)}` }), button(this.theme, 'clear', 'Clear the trace', () => this.clearTrace(), { id: 'trace-clear' })),
      el('div', { id: 'trace-flags', text: flags }),
      list,
    );
  }

  // ------------------------------------------------------------------------------------------- basket panel

  private renderBasket(): void {
    const pal = this.theme.palette;
    const muted = mutedText(this.theme);
    const box = this.basketEl;
    box.replaceChildren();
    const mode = this.readOnly ? 'READ-ONLY' : this.writeEnabled === null ? '' : this.writeEnabled ? 'WRITE' : 'DRY-RUN';
    const modeFill = this.writeEnabled && !this.readOnly ? pal.led_ok : pal.led_warn;
    box.append(
      el(
        'header',
        { style: 'display:flex;gap:6px;align-items:center' },
        el('strong', { style: 'flex:1', text: `CHANGES (${this.basket.size})` }),
        mode ? el('span', { id: 'basket-mode', style: `background:${modeFill};color:${badgeText(this.theme, modeFill)};padding:0 4px`, text: mode }) : null,
      ),
    );
    if (this.readOnly) box.append(el('div', { id: 'basket-readonly', style: `color:${toneColour(this.theme, 'warn')};margin:4px 0`, text: this.readOnly }));
    const fresh = this.resultFor === this.seq ? this.result : null;
    const list = el('ol', { id: 'basket-ops', style: 'margin:4px 0;padding-left:18px' });
    this.basket.entries.forEach((s, i) => {
      const r = fresh?.ops[i];
      const colour = !r ? muted : toneColour(this.theme, r.status === 'error' ? 'err' : r.status === 'ok' ? 'ok' : 'warn');
      const li = el(
        'li',
        { 'data-status': r?.status ?? 'pending', style: 'margin-bottom:3px' },
        el('div', { style: 'display:flex;gap:4px;align-items:baseline' }, el('span', { style: 'flex:1', text: s.label }), el('span', { class: 'op-status', style: `color:${colour}`, text: r?.status ?? '…' }), button(this.theme, 'undo', 'Remove this change', () => this.undo(i), { class: 'op-undo', ...(this.busy ? { disabled: '' } : {}) })),
      );
      const names = this.names();
      for (const d of r?.diff ?? []) li.append(el('div', { class: 'op-diff', style: `color:${muted}`, text: `${fieldLabel(d.field)}: ${showField(d.field, d.before, names)} → ${showField(d.field, d.after, names)}` }));
      if (r?.error) li.append(el('div', { class: 'op-error', style: `color:${toneColour(this.theme, 'err')}`, text: r.error.message }));
      list.append(li);
    });
    if (this.basket.size) box.append(list);
    else if (!this.readOnly) box.append(el('div', { style: `color:${muted}`, text: 'Drag racks and devices, or edit fields: changes collect here until you apply them.' }));
    if (this.message) box.append(el('div', { id: 'basket-msg', 'data-tone': this.tone, style: `color:${toneColour(this.theme, this.tone)};margin:4px 0`, text: this.message }));
    if (this.basket.size) {
      const apply = button(this.theme, 'Apply', 'Write every change to NetBox (Ctrl+Z undoes the last staged change)', () => void this.apply(), { id: 'basket-apply' });
      setEnabled(this.theme, apply, this.applyBlocked());
      box.append(this.reasonEl);
      const discard = button(this.theme, 'Discard all', 'Empty the basket', () => this.discard(), { id: 'basket-discard' });
      discard.disabled = this.busy;
      const reload = staleIndexes(fresh).length
        ? button(this.theme, 'Reload from NetBox', 'Read NetBox again; the changes staged on old data leave the basket', () => this.reloadStale(), { id: 'basket-reload' })
        : null;
      if (reload) reload.disabled = this.busy;
      box.append(el('div', {}, reload, apply, discard));
      const why = this.busy ? null : this.applyBlocked();
      if (why) box.append(el('div', { id: 'basket-why', style: `color:${muted}`, text: why }));
    }
  }

  private flash(text: string): void {
    this.tone = 'warn';
    this.message = text;
    this.renderBasket();
  }

  private renderStatus(): void {
    const g = this.ghostText;
    const s = this.statusEl;
    if (!g) return void (s.style.display = 'none');
    const pal = this.theme.palette;
    s.style.display = '';
    const why = g.problem ? `: ${g.problem}` : g.verdict === 'ok' ? ' (proxy: ok)' : g.verdict === 'pending' ? ' (checking…)' : g.verdict === 'unconfirmed' ? ' (local check only: proxy unreachable)' : '';
    s.style.color = toneColour(this.theme, g.verdict === 'error' ? 'err' : g.verdict === 'ok' ? 'ok' : 'warn');
    s.dataset.verdict = g.verdict;
    s.textContent = `${g.text}${why}`;
  }

  // ------------------------------------------------------------------------------------------- tray

  private renderTray(): void {
    const t = this.target;
    const tray = this.trayEl;
    tray.replaceChildren();
    if (!this.site || (t?.level !== 'room' && t?.level !== 'rack')) return void (tray.style.display = 'none');
    tray.style.display = '';
    const pal = this.theme.palette;
    const chip = `display:inline-block;margin:2px 4px 2px 0;padding:0 4px;border:2px solid ${edgeColour(this.theme)};background:${pal.background}`;
    const unracked = this.site.devices.filter((d) => d.rack_id === null && d.device_type.u_height > 0);
    const zone = el('div', { 'data-drop': 'tray', id: 'tray-unracked', style: 'min-height:20px' }, el('strong', { text: `UNRACKED (${unracked.length}) ` }));
    for (const d of unracked) {
      const c = el('span', { class: 'tray-device', 'data-device-id': String(d.id), title: 'Drag onto a U slot of the open rack', style: `${chip};cursor:grab;touch-action:none` });
      const thumb = this.view.spriteBuffer({ kind: 'faceplate', device: d });
      if (thumb) c.append(el('img', { src: bufferToDataUrl(thumb), alt: '', draggable: 'false', style: `display:block;height:${thumb.height}px;image-rendering:pixelated;pointer-events:none` }));
      c.append(`${d.name ?? d.id} ${d.device_type.u_height}U`);
      c.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        if (this.readOnly) return this.flash(`Not staged: ${this.readOnly}`);
        if (t?.level === 'rack' && !this.view.transitioning && this.contextFor === formatView(t)) this.trayDrag(d, e);
        else if (t?.level === 'rack') return;
        else this.flash('Open a rack (double-click it) to drop an unracked device into a U slot.');
      });
      zone.append(c);
    }
    tray.append(zone);
    if (t.level === 'rack' && this.rack) {
      const others = this.site.racks.filter((r) => r.location_id === this.rack!.rack.location_id && r.id !== this.rack!.rack.id);
      const row = el('div', {}, el('strong', { text: 'RACKS ' }));
      this.otherRacks.clear();
      for (const r of others) {
        row.append(el('span', { 'data-drop': 'rack', 'data-rack-id': String(r.id), class: 'tray-rack', title: 'Drop a device here: first free U', style: chip, text: r.name }));
        void this.reader.rack(r.id).then(
          (rs) => {
            this.otherRacks.set(r.id, rs);
            this.dragging?.recheck(); // a drag resting on this chip said "rack not loaded yet": say what it is now
          },
          () => undefined,
        );
      }
      if (others.length) tray.append(row);
    }
    if (t.level === 'room' && !this.readOnly) {
      const b = button(this.theme, this.tool ? '✕ cancel' : '+ rack', this.tool ? 'Stop placing (Esc)' : 'Place a new rack on a free tile', () => this.setTool(this.tool ? null : 'add-rack'), { id: 'tool-add-rack' });
      tray.append(el('div', {}, b, this.tool ? el('span', { text: ' click a free tile' }) : null));
    }
  }
}

/** client_ref -> NetBox id of the create_rack ops among `written` (the proxy reports the new id as the target id). */
export function createdRacks(r: ChangeResult, written: readonly number[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const o of r.ops)
    if (written.includes(o.index) && o.op === 'create_rack' && o.client_ref && typeof o.target.id === 'number') out.set(o.client_ref, o.target.id);
  return out;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

