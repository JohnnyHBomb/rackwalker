// The game shell: sign-in, the placeholder overworld <-> the isometric levels, the shared dialogue bubble, the menu,
// and autosave. The player's place is saved by the proxy's game store (never NetBox) on every change of place, so a
// reload puts them back where they were. Started by main.ts when the page opens without ?view= on the live proxy.
import type { RenderScale } from '../contracts/iso';
import type { FlagValue, Flags, GameSettings, GameState, IsoCamera, IsoFacing, SaveData } from '../contracts/scene.gen';
import { formatView, parseView, type Target } from '../engine/nav';
import { SceneSourceError } from '../engine/source';
import type { RackWalkerView } from '../engine/view';
import { THEMES, isThemeId } from '../themes/index';
import type { GameApi } from './api';
import { loadScript, mayAutoStart, type Action, type Effects, type Script } from './dialogue';
import { DialogueBox } from './dialogue-ui';
import { actionOf, DEFAULT_SETTINGS, keysLabel } from './keys';
import { OverworldMap, type Dir, type Door, type Pos } from './overworld';
import { OverworldView } from './overworld-view';
import { capturingKey, ensureCss, guidePanel, menuPanel, settingsPanel, signInPanel } from './panels';
import introJson from './scripts/ilatea-intro.json';
import firstBuildingJson from './scripts/ilatea-first-building.json';
import menuJson from './scripts/ilatea-menu.json';
import stuckJson from './scripts/ilatea-stuck.json';

export const SCRIPTS = {
  intro: loadScript(introJson),
  firstBuilding: loadScript(firstBuildingJson),
  stuck: loadScript(stuckJson),
  menu: loadScript(menuJson),
} as const;

export type Scene = 'loading' | 'signin' | 'overworld' | 'iso';
export const VISITED = 'game.visited-building';
const SAVE_DEBOUNCE_MS = 250;

const SCENE_CSS = `
body[data-scene=overworld]>:not(.game-ui):not(#overworld):not(#dialogue):not(#game-button):not(#scene-card){display:none!important}
body[data-scene=signin]>:not(.game-ui),body[data-scene=loading]>:not(.game-ui):not(#scene-card){display:none!important}
`;

export class GameShell {
  scene: Scene = 'loading';
  state: GameState | null = null;
  settings: GameSettings = DEFAULT_SETTINGS;
  flags: Flags = {};
  map: OverworldMap | null = null;
  overworld: OverworldView | null = null;
  readonly dialogue: DialogueBox;
  /** The site whose building the player is in (scene iso). */
  siteId: number | null = null;
  private owPos: Pos = { x: 0, y: 0 };
  private owFacing: Dir = 'N';
  private menu: HTMLElement | null = null;
  private lastSaved = '';
  private saving: Promise<void> = Promise.resolve();
  private saveTimer = 0;
  private pendingSave = false;
  /** Scene-change card time; reduce motion makes it a cut. Tests raise it to photograph the card. */
  transitionMs = 380;
  /** Time on the overworld without entering any building before Ilatea offers one nudge. */
  stuckMs = 40_000;
  private owSince = 0;
  error: string | null = null;

  constructor(
    private readonly host: HTMLElement,
    private readonly view: RackWalkerView,
    private readonly api: GameApi,
  ) {
    this.dialogue = new DialogueBox(host);
    this.dialogue.onAction = (a) => this.act(a);
  }

  // ------------------------------------------------------------------------------------------- boot

  async start(): Promise<void> {
    ensureCss();
    const css = document.createElement('style');
    css.textContent = SCENE_CSS;
    document.head.append(css);
    this.setScene('loading');
    let session = await this.api.session();
    if (!session.signed_in) {
      this.setScene('signin');
      await signInPanel(this.host, async (token) => {
        try {
          session = await this.api.signIn(token);
          return null;
        } catch (e) {
          return e instanceof SceneSourceError && e.code === 'unauthorized'
            ? 'NetBox did not accept that token. Check it in NetBox (your user menu, API Tokens) and try again.'
            : `Sign-in failed: ${e instanceof Error ? e.message : String(e)}`;
        }
      });
    }
    this.state = await this.api.state();
    this.settings = this.state.settings;
    this.flags = { ...this.state.flags };
    this.dialogue.reduceMotion = this.settings.reduce_motion;
    const world = await this.view.data.world();
    const sites = world.sites.map((w) => w.site);
    this.map = new OverworldMap(sites.map((s) => ({ id: s.id, name: s.name })));
    const palette = (id: number) => {
      const t = sites.find((s) => s.id === id)?.iso_theme;
      return (isThemeId(t) ? THEMES[t] : THEMES.debug).palette;
    };
    const save = this.state.saves.find((s) => s.slot === 'auto')?.data ?? null;
    this.owPos = this.map.clampPos(save ? { x: save.overworld.x, y: save.overworld.y } : this.map.start);
    this.owFacing = save?.overworld.facing ?? 'N';
    this.overworld = new OverworldView(this.host, this.map, palette, { door: (d) => void this.enter(d), moved: (p, f) => this.moved(p, f), talk: () => this.talkToGuide() }, this.owPos, this.owFacing);
    this.view.navGuard = (t) => this.guard(t);
    const prevUpdate = this.view.onUpdate;
    this.view.onUpdate = () => {
      prevUpdate?.();
      if (this.scene === 'iso') this.scheduleSave();
    };
    this.menuButton();
    window.addEventListener('keydown', (e) => this.keydown(e), true);
    window.addEventListener('keyup', (e) => this.keyup(e), true);
    if (save?.scene === 'iso' && save.iso && parseView(save.iso.view)) {
      await this.showIso(save.iso.site_id, parseView(save.iso.view)!, save.iso.camera);
    } else this.showOverworld();
    this.lastSaved = JSON.stringify(this.saveData());
    if (this.scene === 'overworld') void this.autoTalk(SCRIPTS.intro);
    window.setInterval(() => this.stuckCheck(), 1000);
  }

  private setScene(s: Scene): void {
    this.scene = s;
    document.body.dataset.scene = s;
  }

  private menuButton(): void {
    const b = document.createElement('button');
    b.id = 'game-button';
    b.className = 'game-ui';
    b.type = 'button';
    b.textContent = '☰ Menu';
    b.title = `Menu (${keysLabel(this.settings.keys, 'menu')})`;
    b.onclick = () => this.toggleMenu();
    this.host.append(b);
  }

  // ------------------------------------------------------------------------------------------- scenes

  private showOverworld(): void {
    this.siteId = null;
    this.setScene('overworld');
    this.overworld!.place(this.owPos, this.owFacing);
    this.overworld!.show(true);
    this.owSince = performance.now();
  }

  private async showIso(siteId: number, t: Target, camera: IsoCamera | null): Promise<void> {
    this.overworld?.show(false);
    this.siteId = siteId;
    this.setScene('iso');
    await this.view.goto(t, 'none');
    if (camera) this.view.setCamera({ cx: camera.cx, cy: camera.cy, scale: camera.scale as RenderScale });
  }

  private async card(text: string): Promise<void> {
    const ms = this.settings.reduce_motion ? 0 : this.transitionMs;
    if (!ms) return;
    const c = document.createElement('div');
    c.id = 'scene-card';
    c.className = 'game-ui';
    const arch = document.createElement('div');
    arch.style.cssText = 'width:40px;height:56px;margin-right:16px;background:#22170f;border:6px solid #e9dcc0;border-bottom:none;border-radius:20px 20px 0 0';
    arch.setAttribute('aria-hidden', 'true');
    c.append(arch, text);
    this.host.append(c);
    await new Promise((r) => setTimeout(r, ms));
    c.remove();
  }

  /** Walking into a door: that site's first room (its site level if it has none). */
  async enter(d: Door): Promise<void> {
    if (this.scene !== 'overworld') return;
    this.dialogue.close();
    this.owPos = this.map!.outside(d);
    this.owFacing = 'S';
    const ss = await this.view.data.site(d.siteId);
    const room = [...ss.locations].sort((a, b) => a.id - b.id)[0];
    const t: Target = room ? { level: 'room', siteId: d.siteId, locationId: room.id } : { level: 'site', siteId: d.siteId };
    const card = this.card(`Entering ${d.name}`);
    this.overworld!.show(false);
    await this.showIso(d.siteId, t, null);
    await card;
    const first = !this.flags[VISITED];
    if (first) this.persistFlags({ [VISITED]: true });
    this.saveNow();
    if (first) void this.autoTalk(SCRIPTS.firstBuilding);
  }

  /** Leaving the building (zooming out past the site level, or the menu): back on the overworld at its door. */
  async leave(): Promise<void> {
    if (this.scene !== 'iso') return;
    this.dialogue.close();
    const door = this.siteId !== null ? this.map!.doorOf(this.siteId) : null;
    if (door) this.owPos = this.map!.outside(door);
    this.owFacing = 'S';
    const card = this.card(door ? `Leaving ${door.name}` : 'Outside');
    this.showOverworld();
    await card;
    this.saveNow();
  }

  private guard(t: Target): boolean {
    if (this.scene !== 'iso' || t.level !== 'world') return false;
    void this.leave();
    return true;
  }

  private moved(p: Pos, f: Dir): void {
    this.owPos = p;
    this.owFacing = f;
    this.owSince = performance.now();
    this.scheduleSave();
  }

  // ------------------------------------------------------------------------------------------- save

  saveData(): SaveData {
    const overworld = { x: this.owPos.x, y: this.owPos.y, facing: this.owFacing as IsoFacing };
    const t = this.view.crumbs[this.view.crumbs.length - 1]?.target;
    if (this.scene !== 'iso' || !t || t.level === 'world') return { scene: 'overworld', overworld, iso: null };
    const site = this.view.crumbs.find((c) => c.target.level === 'site')?.target;
    const siteId = site?.level === 'site' ? site.siteId : this.siteId!;
    const cam = this.view.camera;
    return {
      scene: 'iso',
      overworld,
      iso: { site_id: siteId, view: formatView(t), camera: cam ? { cx: cam.cx, cy: cam.cy, scale: cam.scale } : null },
    };
  }

  private scheduleSave(): void {
    clearTimeout(this.saveTimer);
    this.pendingSave = true;
    this.saveTimer = window.setTimeout(() => this.saveNow(), SAVE_DEBOUNCE_MS);
  }

  /** Writes the autosave slot now when the place changed since the last write (one write in flight at a time). */
  saveNow(): void {
    clearTimeout(this.saveTimer);
    this.pendingSave = true;
    this.saving = this.saving.then(async () => {
      this.pendingSave = false;
      const data = this.saveData();
      const json = JSON.stringify(data);
      if (json === this.lastSaved) return;
      try {
        await this.api.save('auto', data);
        this.lastSaved = json;
        this.error = null;
      } catch (e) {
        this.error = `Autosave failed: ${e instanceof Error ? e.message : String(e)}`;
      }
    });
  }

  /** Resolves when no save is waiting or in flight. */
  async idle(): Promise<void> {
    while (this.pendingSave) {
      this.saveNow();
      await this.saving;
    }
    await this.saving;
  }

  // ------------------------------------------------------------------------------------------- dialogue

  private persistFlags(set: Record<string, FlagValue>): void {
    Object.assign(this.flags, set);
    this.api.flags(set).catch((e) => (this.error = `Could not save flags: ${String(e)}`));
  }

  /** Starts a script if the hint level and its `once` flag allow it. */
  async autoTalk(s: Script): Promise<void> {
    if (this.dialogue.open || this.menu || !mayAutoStart(s, this.flags, this.settings.hint_level)) return;
    await this.talk(s);
  }

  async talk(s: Script): Promise<void> {
    if (this.dialogue.open) this.dialogue.close();
    this.overworld?.hold(null);
    this.dialogue.reduceMotion = this.settings.reduce_motion;
    const playing = this.dialogue.play(s, this.flags, (a) => keysLabel(this.settings.keys, a));
    if (s.once) this.persistFlags({ [s.once]: true }); // seen as soon as it shows: never repeats, even after a reload
    const eff: Effects = await playing;
    const rest = Object.fromEntries(Object.entries(eff.set).filter(([k]) => k !== s.once));
    if (Object.keys(rest).length) this.persistFlags(rest);
  }

  /** Ilatea on the overworld: the player walked up to her (or tapped her) and talks. */
  private talkToGuide(): void {
    if (this.scene !== 'overworld' || this.dialogue.open || this.menu) return;
    this.owSince = performance.now();
    void this.talk(SCRIPTS.menu);
  }

  private act(a: Action): void {
    if (a === 'hints_low' || a === 'hints_off') this.saveSettings({ ...this.settings, hint_level: a === 'hints_low' ? 'low' : 'off' });
    if (a === 'open_menu') this.openMenu();
    if (a === 'open_settings') this.openMenu('settings');
  }

  private stuckCheck(): void {
    if (this.scene !== 'overworld' || this.flags[VISITED] || this.dialogue.open || this.menu || this.overworld?.walking) return;
    if (performance.now() - this.owSince >= this.stuckMs) void this.autoTalk(SCRIPTS.stuck);
  }

  // ------------------------------------------------------------------------------------------- menu

  saveSettings(s: GameSettings): void {
    this.settings = s;
    this.dialogue.reduceMotion = s.reduce_motion;
    const b = document.getElementById('game-button');
    if (b) b.title = `Menu (${keysLabel(s.keys, 'menu')})`;
    this.api.settings(s).catch((e) => (this.error = `Could not save settings: ${String(e)}`));
  }

  toggleMenu(): void {
    if (this.menu) this.closeMenu();
    else this.openMenu();
  }

  openMenu(page: 'menu' | 'settings' | 'byte' | 'nocti' = 'menu'): void {
    if (this.scene === 'signin' || this.scene === 'loading') return;
    this.overworld?.hold(null);
    this.menu?.remove();
    const back = () => this.openMenu();
    if (page === 'settings') this.menu = settingsPanel(this.host, this.settings, (s) => this.saveSettings(s), back);
    else if (page === 'byte' || page === 'nocti') this.menu = guidePanel(this.host, page, back);
    else {
      const p = this.state?.player;
      this.menu = menuPanel(this.host, p?.display_name ?? p?.username ?? 'player', {
        close: () => this.closeMenu(),
        talk: () => (this.closeMenu(), void this.talk(SCRIPTS.menu)),
        guide: (who) => this.openMenu(who),
        settings: () => this.openMenu('settings'),
        signOut: () => void this.api.signOut().then(() => location.reload()),
      });
    }
  }

  closeMenu(): void {
    this.menu?.remove();
    this.menu = null;
    this.owSince = performance.now();
  }

  get menuPage(): string | null {
    return this.menu?.id ?? null;
  }

  // ------------------------------------------------------------------------------------------- keys

  private keydown(e: KeyboardEvent): void {
    if (this.scene === 'signin' || this.scene === 'loading' || capturingKey()) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    const a = actionOf(this.settings.keys, e.code);
    const eat = () => (e.preventDefault(), e.stopImmediatePropagation());
    if (this.dialogue.open) {
      const digit = /^(Digit|Numpad)([1-4])$/.exec(e.code);
      const used = digit ? this.dialogue.key('digit', Number(digit[2])) : a === 'up' || a === 'down' || a === 'interact' || a === 'cancel' ? this.dialogue.key(a) : false;
      if (used || a !== 'menu') return eat();
    }
    if (this.menu) {
      if (a === 'cancel' || a === 'menu') return this.closeMenu(), eat();
      // Tab, Enter and Space work the focused button as usual; nothing reaches the level behind the menu.
      return e.stopImmediatePropagation();
    }
    if (a === 'menu') return this.openMenu(), eat();
    if (this.scene === 'overworld') {
      const dir = ({ up: 'N', down: 'S', left: 'W', right: 'E' } as const)[a as 'up'] as Dir | undefined;
      if (dir) {
        this.overworld!.hold(dir);
        this.owSince = performance.now();
        return eat();
      }
      if (a === 'interact' && this.overworld!.faceGuide()) return this.talkToGuide(), eat();
      if (a === 'interact' || a === 'cancel') return eat();
      // The isometric view behind the overworld is hidden: none of its keys (zoom, back, search) act from here.
      return e.stopImmediatePropagation();
    }
    if (this.scene === 'iso' && a === 'cancel' && e.code !== 'Escape') {
      void this.view.back().catch(() => undefined); // a remapped "back" key does what Esc does
      return eat();
    }
  }

  private keyup(e: KeyboardEvent): void {
    if (this.scene !== 'overworld') return;
    const a = actionOf(this.settings.keys, e.code);
    if (a === 'up' || a === 'down' || a === 'left' || a === 'right') this.overworld!.hold(null);
  }
}
