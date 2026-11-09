// The game shell: sign-in, the placeholder overworld <-> the isometric levels, the shared dialogue bubble, the menu,
// and autosave. The player's place is saved by the proxy's game store (never NetBox) on every change of place, so a
// reload puts them back where they were. Started by main.ts when the page opens without ?view= on the live proxy.
// Nothing fails silently: a write the proxy refuses or cannot take (autosave, flags, settings) shows a notice at the
// top and is kept and sent again; a stale CSRF token (another tab signed in) is refreshed; a signed-out session asks
// the player to sign in again without a reload, so the unsaved place is not lost.
import type { RenderScale } from '../contracts/iso';
import type { Site } from '../contracts/scene.gen';
import type { FlagValue, Flags, GameSettings, GameState, IsoCamera, IsoFacing, SaveData } from '../contracts/scene.gen';
import { formatView, parseView, type Target } from '../engine/nav';
import { SceneSourceError } from '../engine/source';
import type { RackWalkerView } from '../engine/view';
import type { GameApi } from './api';
import { loadScript, mayAutoStart, type Action, type Effects, type Script } from './dialogue';
import { DialogueBox } from './dialogue-ui';
import { actionOf, DEFAULT_SETTINGS, ISO_FIXED, ISO_FIXED_KEYS, ISO_KEYS, keysLabel } from './keys';
import { OverworldMap, type Dir, type Door, type Pos } from './overworld';
import { OverworldView } from './overworld-view';
import { buildingCard } from './world/card';
import { capturingKey, clearNotice, ensureCss, guidePanel, loadFailedPanel, menuPanel, settingsPanel, showNotice, signInPanel } from './panels';
import emptyJson from './scripts/ilatea-empty.json';
import introJson from './scripts/ilatea-intro.json';
import firstBuildingJson from './scripts/ilatea-first-building.json';
import menuJson from './scripts/ilatea-menu.json';
import stuckJson from './scripts/ilatea-stuck.json';

export const SCRIPTS = {
  intro: loadScript(introJson),
  empty: loadScript(emptyJson),
  firstBuilding: loadScript(firstBuildingJson),
  stuck: loadScript(stuckJson),
  menu: loadScript(menuJson),
} as const;

/** What the overworld needs to know about a site. */
const worldSite = (s: Site) => ({ id: s.id, name: s.name, latitude: s.latitude, longitude: s.longitude, theme: s.iso_theme, country: s.country });

/** Key events the shell sends on itself (a remapped key turned into the view's fixed key). */
const SYNTHETIC = new WeakSet<Event>();

export type Scene = 'loading' | 'signin' | 'overworld' | 'iso';
export const VISITED = 'game.visited-building';
const SAVE_DEBOUNCE_MS = 250;
/** A failed game write (proxy or disk trouble) is tried again this often until it lands. */
export const RETRY_MS = 10_000;
type Write = 'save' | 'flags' | 'settings';
const WRITE_NAMES: Readonly<Record<Write, string>> = { save: 'Autosave', flags: 'Saving your progress flags', settings: 'Saving your settings' };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

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
  /** The last write problem the player was told about (null when every write has landed). */
  error: string | null = null;
  /** Writes that failed and wait to be sent again, with why. */
  private readonly failing = new Map<Write, string>();
  private retryTimer = 0;
  private signedOut = false;
  /** Why the session ended, as NetBox told the proxy: the user was deleted or deactivated (play stops until a sign-in). */
  private endedBy: 'gone' | 'inactive' | null = null;
  /** A write failure that does not mend by trying again (the proxy's own NetBox permissions). */
  private stuckWrites = false;
  /** Flags set during play that the proxy has not confirmed yet. */
  private unsentFlags: Record<string, FlagValue> = {};
  /** Settings the player changed that the proxy has not confirmed yet (top-level keys only). */
  private unsentSettings: Partial<GameSettings> = {};
  private settingsSending: Promise<void> = Promise.resolve();

  constructor(
    private readonly host: HTMLElement,
    private readonly view: RackWalkerView,
    private readonly api: GameApi,
  ) {
    this.dialogue = new DialogueBox(host);
    this.dialogue.onAction = (a) => this.act(a);
    this.dialogue.avoid = () => {
      const ow = this.overworld;
      if (this.scene !== 'overworld' || !ow || !this.map) return [];
      return [ow.clientOf(ow.pos), ow.clientOf(this.map.guide)];
    };
  }

  // ------------------------------------------------------------------------------------------- boot

  async start(): Promise<void> {
    ensureCss();
    const css = document.createElement('style');
    css.textContent = SCENE_CSS;
    document.head.append(css);
    // Until the session, the game state and the world have all loaded, a failure says why and offers "Try again".
    let world: Awaited<ReturnType<RackWalkerView['data']['world']>>;
    for (;;) {
      this.setScene('loading');
      try {
        const session = await this.api.session();
        if (!session.signed_in) {
          this.setScene('signin');
          await this.signIn();
          this.setScene('loading');
        }
        this.state = await this.api.state();
        world = await this.view.data.world();
        break;
      } catch (e) {
        await loadFailedPanel(this.host, errText(e));
      }
    }
    this.settings = this.state.settings;
    this.flags = { ...this.state.flags };
    this.applyMotion();
    const sites = world.sites.map((w) => w.site);
    this.map = new OverworldMap(sites.map(worldSite));
    const save = this.state.saves.find((s) => s.slot === 'auto')?.data ?? null;
    this.owPos = this.map.clampPos(save ? { x: save.overworld.x, y: save.overworld.y } : this.map.start);
    this.owFacing = save?.overworld.facing ?? 'N';
    this.overworld = new OverworldView(this.host, this.map, {
      door: (d) => void this.enter(d),
      moved: (p, f) => this.moved(p, f),
      talk: () => this.talkToGuide(),
      blocked: () => this.dialogue.open || !!this.menu || !!document.getElementById('signin'),
      travelled: (to) => this.travelled(to),
    }, this.owPos, this.owFacing);
    this.view.navGuard = (t) => this.guard(t);
    const prevUpdate = this.view.onUpdate;
    this.view.onUpdate = () => {
      prevUpdate?.();
      if (this.scene !== 'iso') return;
      // The building is the site on screen (a search pick can take the player to another site's rack).
      const site = this.view.crumbs.find((c) => c.target.level === 'site')?.target;
      if (site?.level === 'site') this.siteId = site.siteId;
      this.scheduleSave();
    };
    this.menuButton();
    this.applyKeyLabels();
    window.addEventListener('keydown', (e) => this.keydown(e), true);
    window.addEventListener('keyup', (e) => this.keyup(e), true);
    // A key held while the page loses focus never sends its keyup: stop walking.
    window.addEventListener('blur', () => this.overworld?.hold(null));
    document.addEventListener('visibilitychange', () => this.overworld?.hold(null));
    let restored = false;
    if (save?.scene === 'iso' && save.iso && parseView(save.iso.view)) {
      try {
        await this.showIso(save.iso.site_id, parseView(save.iso.view)!, save.iso.camera);
        restored = true;
      } catch (e) {
        this.notice(`Your saved place inside a building cannot be opened (${errText(e)}). You are back on the green.`);
      }
    }
    if (!restored) this.showOverworld();
    this.lastSaved = JSON.stringify(this.saveData());
    if (this.scene === 'overworld') void this.autoTalk(this.map.doors.length ? SCRIPTS.intro : SCRIPTS.empty);
    window.setInterval(() => this.stuckCheck(), 1000);
  }

  /** The sign-in panel until NetBox accepts a token (also when a session ends mid-game). */
  private async signIn(why: string | null = null): Promise<void> {
    await signInPanel(this.host, async (token) => {
      try {
        await this.api.signIn(token);
        return null;
      } catch (e) {
        return e instanceof SceneSourceError && e.code === 'unauthorized'
          ? 'NetBox did not accept that token. Check it in NetBox (your user menu, API Tokens) and try again.'
          : `Sign-in failed: ${errText(e)}`;
      }
    }, why);
  }

  private applyMotion(): void {
    this.dialogue.reduceMotion = this.settings.reduce_motion;
    this.view.reduceMotion = this.settings.reduce_motion;
    if (this.overworld) this.overworld.reduceMotion = this.settings.reduce_motion;
  }

  private notice(text: string, actions: Parameters<typeof showNotice>[2] = []): void {
    showNotice(this.host, text, actions);
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
    this.overworld!.show(true);
    this.overworld!.place(this.owPos, this.owFacing);
    this.owSince = performance.now();
  }

  /** Opens a level inside a building; the overworld stays as it was until the level has loaded. */
  private async showIso(siteId: number, t: Target, camera: IsoCamera | null): Promise<void> {
    await this.view.goto(t, 'none');
    this.overworld?.show(false);
    this.siteId = siteId;
    this.setScene('iso');
    if (camera) this.view.setCamera({ cx: camera.cx, cy: camera.cy, scale: camera.scale as RenderScale });
  }

  /** The scene-change card: the building (pixel art from the overworld's sprites) and a line of text. */
  private async card(text: string, theme: string | null = null): Promise<void> {
    const ms = this.settings.reduce_motion ? 0 : this.transitionMs;
    if (!ms) return;
    const c = document.createElement('div');
    c.id = 'scene-card';
    c.className = 'game-ui';
    const art = buildingCard(theme);
    art.style.marginRight = '16px';
    art.setAttribute('aria-hidden', 'true');
    c.append(art, text);
    this.host.append(c);
    await new Promise((r) => setTimeout(r, ms));
    c.remove();
  }

  /**
   * Walking into a door: that site's first room (its site level if it has none). When the building cannot be opened
   * (NetBox or the proxy did not answer), the player stays outside its door with a notice and can simply try again.
   */
  async enter(d: Door): Promise<void> {
    if (this.scene !== 'overworld') return;
    this.dialogue.close();
    this.owPos = this.map!.outside(d);
    this.owFacing = 'S';
    if (this.endedBy) {
      // the player's NetBox user is gone or deactivated: no building opens until someone signs in
      this.showOverworld();
      this.showSignedOut();
      return;
    }
    const card = this.card(`Entering ${d.name}`, d.theme);
    try {
      const ss = await this.view.data.site(d.siteId);
      const room = [...ss.locations].sort((a, b) => a.id - b.id)[0];
      const t: Target = room ? { level: 'room', siteId: d.siteId, locationId: room.id } : { level: 'site', siteId: d.siteId };
      await this.showIso(d.siteId, t, null);
    } catch (e) {
      await card;
      this.showOverworld();
      this.notice(`${d.name} cannot be opened right now: ${errText(e)} Walk into the door to try again.`);
      this.scheduleSave();
      return;
    }
    this.restoreNotice();
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
    const card = this.card(door ? `Leaving ${door.name}` : 'Outside', door?.theme ?? null);
    this.showOverworld();
    await card;
    this.saveNow();
  }

  private guard(t: Target): boolean {
    if (this.scene !== 'iso' || t.level !== 'world') return false;
    void this.leave();
    return true;
  }

  /** The airfield pad took the player to another region: say where, the way a door says which building. */
  private travelled(to: Pos): void {
    const r = this.map!.regionAt(to);
    const first = this.map!.doors.find((d) => d.region === r.index);
    void this.card(first ? `Off to ${first.name}` : 'Off to the next region', first?.theme ?? null);
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
      if (await this.write('save', () => this.api.save('auto', data))) this.lastSaved = json;
    });
  }

  /**
   * Sends one game write. A refused CSRF token (another tab signed in since this page loaded) is refreshed from the
   * session and the write sent once more; a signed-out session asks the player to sign in again; any other failure
   * shows a notice and is retried every RETRY_MS. True when the proxy took the write.
   */
  private async write(what: Write, send: () => Promise<unknown>): Promise<boolean> {
    for (let attempt = 0; ; attempt++) {
      try {
        await send();
        this.landed(what);
        return true;
      } catch (e) {
        const code = e instanceof SceneSourceError ? e.code : null;
        if (attempt === 0 && code === 'forbidden') {
          const s = await this.api.session().catch(() => null);
          if (s?.signed_in) continue;
        }
        if (code === 'unauthorized' || code === 'forbidden') this.lostSession(what, e as SceneSourceError);
        else this.failed(what, `${WRITE_NAMES[what]} failed: ${errText(e)}`, e instanceof SceneSourceError && e.details?.reason === 'proxy_cannot_view_users');
        return false;
      }
    }
  }

  private landed(what: Write): void {
    if (!this.failing.delete(what)) return;
    if (this.failing.size) return this.showFailing();
    this.error = null;
    this.signedOut = false;
    this.stuckWrites = false;
    clearTimeout(this.retryTimer);
    clearNotice();
  }

  /** `stuck`: trying again will not help until someone changes the proxy's NetBox permissions. */
  private failed(what: Write, why: string, stuck = false): void {
    this.failing.set(what, why);
    this.error = why;
    this.stuckWrites = stuck;
    if (this.signedOut) return;
    this.showFailing();
    clearTimeout(this.retryTimer);
    this.retryTimer = window.setTimeout(() => this.retryWrites(), RETRY_MS);
  }

  private showFailing(): void {
    const why = [...this.failing.values()][0]!;
    this.notice(this.stuckWrites ? `${why} Until then nothing you do is saved; it is kept in this page and sent once it can be.`
      : `${why} Your progress is kept in this page and is sent again on its own.`, [
      { label: 'Try again now', id: 'notice-retry', run: () => this.retryWrites() },
    ]);
  }

  /** Puts back the notice that matters after another one (a door that would not open) is done with. */
  private restoreNotice(): void {
    clearNotice();
    if (this.signedOut) this.showSignedOut();
    else if (this.failing.size) this.showFailing();
  }

  /**
   * The session ended. Signed out elsewhere (another tab, expired): the notice asks to sign in again here, and
   * everything waiting is sent after. NetBox deleted or deactivated the user: play stops, and the sign-in panel
   * stays up until an active NetBox user signs in.
   */
  private lostSession(what: Write, e: SceneSourceError): void {
    // The proxy names the reason when NetBox deleted or deactivated the user (on every request after, too).
    const reason = e.details?.reason;
    if (reason === 'gone' || reason === 'inactive') this.endedBy = reason;
    this.failing.set(what, 'you are signed out');
    this.error = `${WRITE_NAMES[what]} failed: the game session has ended (${this.endedWhy()}).`;
    const was = this.signedOut;
    this.signedOut = true;
    clearTimeout(this.retryTimer);
    if (this.endedBy) {
      if (!document.getElementById('signin')) void this.signInAgain();
    } else if (!was || !document.getElementById('game-notice')) this.showSignedOut();
  }

  private endedWhy(): string {
    return this.endedBy === 'gone' ? 'your NetBox user was deleted in NetBox'
      : this.endedBy === 'inactive' ? 'your NetBox user was deactivated in NetBox'
      : 'signed out, perhaps in another tab';
  }

  private showSignedOut(): void {
    this.notice(`You are signed out (${this.endedWhy()}), so your progress is not being saved. Sign in again to keep it.`, [
      { label: 'Sign in again', id: 'notice-signin', run: () => void this.signInAgain() },
    ]);
  }

  /**
   * Signs in again mid-game. The same player: everything waiting is sent. Another NetBox user: what waits belongs to
   * the previous player and is dropped, and the page reloads into the new player's own game.
   */
  private async signInAgain(): Promise<void> {
    clearNotice();
    this.overworld?.stop();
    this.dialogue.close();
    this.closeMenu();
    await this.signIn(this.endedBy ? `Your game session has ended: ${this.endedWhy()}. Play goes on when an active NetBox user signs in.` : null);
    const s = await this.api.session().catch(() => null);
    const before = this.state?.player.id;
    if (s?.signed_in && s.player && before !== undefined && s.player.id !== before) {
      this.failing.clear();
      clearTimeout(this.retryTimer);
      this.signedOut = false;
      this.endedBy = null;
      location.reload();
      return;
    }
    this.signedOut = false;
    this.endedBy = null;
    this.retryWrites();
  }

  /** Sends every write that failed (and anything waiting behind it) again. */
  retryWrites(): void {
    clearTimeout(this.retryTimer);
    const waiting = [...this.failing.keys()];
    if (waiting.includes('save')) {
      this.lastSaved = '';
      this.saveNow();
    }
    if (waiting.includes('flags')) this.sendFlags();
    if (waiting.includes('settings')) this.sendSettings();
  }

  /** Resolves when no save is waiting or in flight (settings writes included). */
  async idle(): Promise<void> {
    while (this.pendingSave) {
      this.saveNow();
      await this.saving;
    }
    await this.saving;
    await this.settingsSending;
  }

  // ------------------------------------------------------------------------------------------- dialogue

  private persistFlags(set: Record<string, FlagValue>): void {
    Object.assign(this.flags, set);
    Object.assign(this.unsentFlags, set);
    this.sendFlags();
  }

  private sendFlags(): void {
    const batch = { ...this.unsentFlags };
    if (!Object.keys(batch).length) return;
    void this.write('flags', () => this.api.flags(batch)).then((ok) => {
      if (!ok) return;
      for (const [k, v] of Object.entries(batch)) if (this.unsentFlags[k] === v) delete this.unsentFlags[k];
    });
  }

  /** Starts a script if the hint level and its `once` flag allow it. */
  async autoTalk(s: Script): Promise<void> {
    if (this.dialogue.open || this.menu || !mayAutoStart(s, this.flags, this.settings.hint_level)) return;
    await this.talk(s);
  }

  async talk(s: Script): Promise<void> {
    if (this.dialogue.open) this.dialogue.close();
    this.overworld?.stop();
    this.dialogue.reduceMotion = this.settings.reduce_motion;
    const playing = this.dialogue.play(s, this.flags, (a) => keysLabel(this.settings.keys, a));
    if (s.once) this.persistFlags({ [s.once]: true }); // seen as soon as it shows: never repeats, even after a reload
    const eff: Effects = await playing;
    this.owSince = performance.now(); // the stuck clock starts when a talk ends, never while one was open
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
    if (!this.map?.doors.length) return; // no building to point at: the empty-world line already said what to do
    if (performance.now() - this.owSince >= this.stuckMs) void this.autoTalk(SCRIPTS.stuck);
  }

  // ------------------------------------------------------------------------------------------- menu

  /**
   * Applies new settings here and sends only what changed: the proxy's current settings are read first and the
   * changed parts laid over them, so a change made meanwhile in another tab is kept, not reverted.
   */
  saveSettings(s: GameSettings): void {
    const prev = this.settings;
    for (const k of Object.keys(s) as (keyof GameSettings)[])
      if (JSON.stringify(s[k]) !== JSON.stringify(prev[k])) (this.unsentSettings as Record<string, unknown>)[k] = s[k];
    this.settings = s;
    this.applyMotion();
    this.applyKeyLabels();
    this.sendSettings();
  }

  private sendSettings(): void {
    this.settingsSending = this.settingsSending.then(async () => {
      const changes = { ...this.unsentSettings };
      if (!Object.keys(changes).length) return;
      let merged: GameSettings | null = null;
      const ok = await this.write('settings', async () => {
        const current = (await this.api.state()).settings;
        merged = { ...current, ...changes };
        await this.api.settings(merged);
      });
      if (!ok || !merged) return;
      for (const k of Object.keys(changes)) if ((this.unsentSettings as Record<string, unknown>)[k] === (changes as Record<string, unknown>)[k]) delete (this.unsentSettings as Record<string, unknown>)[k];
      // Take what other tabs changed, keeping anything changed here since this write began.
      this.settings = { ...(merged as GameSettings), ...this.unsentSettings };
      this.applyMotion();
    });
  }

  /** Names the player's own keys wherever the page names a key (menu button, the building HUD, the search box). */
  private applyKeyLabels(): void {
    const k = (a: string) => keysLabel(this.settings.keys, a);
    const title = (id: string, t: string) => {
      const el = document.getElementById(id);
      if (el) el.title = t;
    };
    title('game-button', `Menu (${k('menu')})`);
    title('hud-back', `Zoom out one level (${k('cancel')})`);
    title('hud-enter', `Zoom into the selected object (${k('interact')} or double-click)`);
    title('hud-zoom-in', `Zoom in (wheel, pinch, ${k('zoom_in')})`);
    title('hud-zoom-out', `Zoom out (wheel, pinch, ${k('zoom_out')})`);
    const search = document.getElementById('search') as HTMLInputElement | null;
    if (search) search.placeholder = `find site, room, rack, device (${k('search')})`;
  }

  toggleMenu(): void {
    if (this.menu) this.closeMenu();
    else this.openMenu();
  }

  openMenu(page: 'menu' | 'settings' | 'byte' | 'nocti' = 'menu'): void {
    if (this.scene === 'signin' || this.scene === 'loading') return;
    this.overworld?.stop(); // a tap-walk under the menu would walk on, and could go into a building
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
      }, this.failing.size ? (this.error ?? 'a write failed') : null);
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
    if (document.getElementById('signin')) return; // signing in again mid-game: keys belong to the form
    if (SYNTHETIC.has(e)) return; // a key the shell itself sent on to the view (below)
    const a = actionOf(this.settings.keys, e.code);
    // Undo inside a building: the player's undo key with Ctrl or Cmd held.
    if (this.scene === 'iso' && !this.dialogue.open && !this.menu && (e.ctrlKey || e.metaKey) && !e.altKey && (a === 'undo' || e.code === 'KeyZ')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (a === 'undo') this.sendIsoKey('undo');
      return;
    }
    // Ctrl, Alt and Meta combinations are the browser's (Ctrl+D, Alt+←): never game moves, never swallowed.
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const eat = () => (e.preventDefault(), e.stopImmediatePropagation());
    if (this.dialogue.open) {
      if (a === 'menu') {
        this.dialogue.close(); // the menu takes over: its keys must never reach a hidden talk
        return this.openMenu(), eat();
      }
      const digit = /^(Digit|Numpad)([1-4])$/.exec(e.code);
      if (digit) this.dialogue.key('digit', Number(digit[2]));
      else if (a === 'up' || a === 'down' || a === 'interact' || a === 'cancel') this.dialogue.key(a);
      return eat();
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
    if (this.scene === 'iso') {
      // Every key inside a building follows the remap: the player's key for an action is sent on to the view and the
      // editor as the fixed key they listen for (Esc for back, so an open panel or a drag ends first, as before);
      // a fixed key the player has moved elsewhere does nothing here.
      if (a && ISO_KEYS[a]) {
        eat();
        if (a === 'undo') return; // undo needs Ctrl or Cmd (above)
        this.sendIsoKey(a);
        return;
      }
      // Fixed keys by position (code) and by character (key: '+' sits elsewhere on many layouts).
      if (ISO_FIXED.has(e.code) || ISO_FIXED_KEYS.has(e.key)) eat();
    }
  }

  /** Sends the fixed key of an in-building action to the view and the editor. */
  private sendIsoKey(a: keyof typeof ISO_KEYS): void {
    const k = ISO_KEYS[a]!;
    const ev = new KeyboardEvent('keydown', { key: k.key, code: k.code, ctrlKey: !!k.ctrl, bubbles: true, cancelable: true });
    SYNTHETIC.add(ev);
    (document.activeElement && document.activeElement !== document.body ? document.activeElement : window).dispatchEvent(ev);
  }

  private keyup(e: KeyboardEvent): void {
    if (this.scene !== 'overworld') return;
    const a = actionOf(this.settings.keys, e.code);
    if (a === 'up' || a === 'down' || a === 'left' || a === 'right') this.overworld!.hold(null);
  }
}
