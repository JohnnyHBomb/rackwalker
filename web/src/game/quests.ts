// Quests in the browser: the conversation with a site's quest giver, the quest log, the marker to the next small step,
// the preview-and-apply panel, and the reward moment. Everything a quest says, asks and proposes comes from the proxy
// (contract 2.5.0: QuestBoard, Quest, QuestStep, QuestVerdict, Chain), which builds it from the player's own NetBox; this
// module only shows it and sends the player's answers back. A line is never completed here: the proxy reads NetBox
// again after the write and says what it found.
//
// Choice questions run in the shared dialogue bubble (scripts made by quest-script.ts); a typed value or a form (a
// rack audit) opens a panel. "I don't know yet" is always there: the line is parked as a field task with a short
// checklist, costs nothing, and asks the same question again later.
import type { Chain, ChangeResult, Quest, QuestBoard, QuestScope, QuestStep, QuestTarget, QuestVerdict, Rewards, ToolId } from '../contracts/scene.gen';
import { SceneSourceError } from '../engine/source';
import type { GameApi } from './api';
import type { Effects, Script } from './dialogue';
import { drawPortrait } from './portraits';
import { PICK, lineScript, offerScript, stepScript } from './quest-script';
import { spriteCanvas } from './world/card';

/** The practice island on the overworld: a made-up site far from anywhere, in a region of its own. Never NetBox data. */
export const PRACTICE_SITE = { id: 9_000_001, name: 'Practice Island', latitude: 36, longitude: -38, theme: null, country: null } as const;
export const scopeOf = (siteId: number): QuestScope => (siteId === PRACTICE_SITE.id ? 'practice' : 'netbox');

export const TOOLS: Readonly<Record<ToolId, { name: string; icon: string; unlocks: string }>> = {
  label_maker: { name: 'Label maker', icon: 'icon_label_maker', unlocks: 'In an audit it now suggests the next free asset tag of the site\'s own pattern. You still decide.' },
  cable_tracer: { name: 'Cable tracer', icon: 'icon_cable_tracer', unlocks: 'Inside a building you can now trace a cable from any port to its far end.' },
};

const CSS = `
#quest-next{position:fixed;left:8px;bottom:8px;z-index:15;width:min(440px,calc(100vw - 132px));height:48px;box-sizing:border-box;font:13px/1.35 ui-monospace,monospace;
  color:#1d1a14;background:#f6efd9;border:3px solid #1d1a14;padding:3px 9px;cursor:pointer;text-align:left;display:block;overflow:hidden}
#quest-next b,#quest-next span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;height:18px}
#quest-giver{position:fixed;left:8px;bottom:64px;z-index:15;font:13px ui-monospace,monospace;color:#1d1a14;background:#f6efd9;border:3px solid #1d1a14;padding:4px 8px 4px 4px;
  cursor:pointer;display:flex;gap:8px;align-items:center;min-height:44px}
#quest-giver canvas{width:40px;height:40px;border:2px solid #1d1a14;image-rendering:pixelated}
/* inside a building the bottom-left corner is the editor's tray: the quest chips sit left of the menu button */
body[data-scene=iso] #quest-next{left:auto;right:112px;width:min(400px,calc(100vw - 124px))}
body[data-scene=iso] #quest-giver{left:auto;right:112px}
#quest-pin{position:fixed;z-index:14;pointer-events:none;font:bold 20px ui-monospace,monospace;color:#1d1a14;text-shadow:0 0 0 #000;transform:translate(-50%,-100%)}
#quest-pin i{display:block;width:0;height:0;border-left:11px solid transparent;border-right:11px solid transparent;border-top:16px solid #1d1a14;position:relative}
#quest-pin i::after{content:"";position:absolute;left:-7px;top:-14px;border-left:7px solid transparent;border-right:7px solid transparent;border-top:10px solid #fff27a}
#quest-pin.bob{animation:quest-bob .8s steps(2) infinite}
@keyframes quest-bob{50%{margin-top:-4px}}
.quest-panel{width:min(640px,calc(100vw - 24px))}
.quest-panel h3{font-size:15px;margin:12px 0 4px;text-transform:uppercase;letter-spacing:1px;border-bottom:2px solid #1d1a14}
.quest-panel ul{margin:4px 0;padding-left:20px}
.quest-panel .q{border:2px solid #1d1a14;background:#fffaf0;padding:6px 8px;margin:6px 0}
.quest-panel .q .t{font-weight:bold}
.quest-panel .q .s{font-size:13px}
.quest-panel .tag{display:inline-block;border:2px solid #1d1a14;padding:0 5px;margin-right:6px;font-size:12px;text-transform:uppercase;background:#f6efd9}
.quest-panel .tag.rank-connectivity::before{content:"◆ "}
.quest-panel .tag.rank-identity::before{content:"● "}
.quest-panel .tag.rank-layout::before{content:"■ "}
.quest-panel .bar{height:10px;border:2px solid #1d1a14;background:#fffaf0;flex:1;min-width:80px}
.quest-panel .bar i{display:block;height:100%;background:#1d1a14}
.quest-panel .rewards{display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.quest-panel .rewards canvas,.quest-reward canvas{vertical-align:middle;margin-right:4px}
.quest-panel label.f{display:block;margin:8px 0 2px;font-weight:bold}
.quest-panel .hint{font-size:13px;color:#4a4436}
.quest-panel input+.hint,.quest-panel select+.hint{margin-top:6px}
.quest-panel input[type=text],.quest-panel input[type=number],.quest-panel select{width:100%;box-sizing:border-box}
.quest-panel table.diff td{font-size:13px;vertical-align:top}
.quest-panel .said{font-size:13px}
.quest-panel .mode{border:2px dashed #1d1a14;padding:6px 8px;margin:8px 0;background:#fffaf0}
.quest-panel .chain{border:2px solid #1d1a14;background:#fffaf0;padding:6px 8px;margin:6px 0}
.quest-panel .chain .t{font-weight:bold}
.quest-panel .chain .s{font-size:13px}
.quest-panel .chain ol{list-style:none;display:flex;flex-wrap:wrap;gap:6px;margin:6px 0;padding:0}
.quest-panel .chain li{border:2px solid #1d1a14;padding:1px 6px;font-size:12px;background:#f6efd9}
.quest-panel .chain li[data-status=open]{background:#fff27a;font-weight:bold}
.quest-panel .chain li[data-status=locked]{border-style:dashed;color:#4a4436}
.quest-reward .chain{font-size:13px;border-top:2px solid #1d1a14;margin-top:8px;padding-top:6px}
.quest-reward{position:fixed;left:50%;top:14%;transform:translateX(-50%);z-index:40;width:min(560px,calc(100vw - 24px));box-sizing:border-box;background:#f6efd9;color:#1d1a14;
  border:4px solid #1d1a14;box-shadow:0 0 0 2px #f6efd9,0 0 0 4px #1d1a14;padding:14px 16px;font:15px/1.45 ui-monospace,monospace;text-align:center}
.quest-reward h2{margin:0 0 6px;font-size:18px;text-transform:uppercase;letter-spacing:1px}
.quest-reward .xp{font-size:22px;font-weight:bold;margin:8px 0}
.quest-reward .now{font-weight:bold}
.quest-reward button{font:inherit;color:#1d1a14;background:#fffaf0;border:2px solid #1d1a14;padding:5px 12px;min-height:36px;cursor:pointer;margin-top:8px}
.quest-reward .sparkle{position:absolute;inset:-18px;pointer-events:none;overflow:visible}
.quest-reward .sparkle i{position:absolute;width:6px;height:6px;background:#fff27a;box-shadow:0 0 0 2px #1d1a14;transform:rotate(45deg);opacity:0;animation:quest-spark 1.6s ease-out forwards}
@keyframes quest-spark{0%{opacity:0;transform:rotate(45deg) scale(.2)}25%{opacity:1}100%{opacity:0;transform:translateY(-22px) rotate(45deg) scale(1.2)}}
body.quest-shake>#overworld,body.quest-shake>canvas{animation:quest-shake .28s steps(4)}
@keyframes quest-shake{25%{transform:translate(2px,0)}50%{transform:translate(-2px,1px)}75%{transform:translate(1px,-1px)}}
`;

const STATUS_WORDS: Readonly<Record<Quest['status'], string>> = {
  offered: 'on offer', active: 'in progress', ready: 'change ready to preview', parked: 'waiting for you (field task)', dismissed: 'not needed',
  practised: 'practised (not written)', done: 'done',
};

/** A stage's state in words and a shape, never by colour alone. */
const STAGE_WORDS: Readonly<Record<Chain['stages'][number]['status'], string>> = {
  done: '✓ in NetBox', answered: '✓ answered', not_known: '? not known yet', open: '▶ in play', locked: '· comes later',
};

/** One chain as a card: where the player is, each stage, what the record will hold, and the record so far. */
export function chainCard(c: Chain): HTMLElement {
  const card = el('div', undefined, 'chain');
  card.dataset.chain = c.id;
  card.dataset.status = c.status;
  const t = el('div', undefined, 't');
  t.append(el('span', 'chain', 'tag'), `${c.title} · ${c.label}`);
  const stages = el('ol');
  c.stages.forEach((s, i) => {
    const li = el('li', `${i + 1} ${s.stage}: ${STAGE_WORDS[s.status]}`);
    li.dataset.stage = s.stage;
    li.dataset.status = s.status;
    li.title = s.missing.length ? `${s.title}. Still missing: ${s.missing.join('; ')}` : s.title;
    stages.append(li);
  });
  card.append(t, stages);
  if (c.summary) card.append(el('div', c.summary, 's summary'));
  if (c.open_questions.length) card.append(el('div', `Open questions: ${c.open_questions.join(', ')}.`, 's open-questions'));
  if (c.status !== 'complete') {
    // the bonus is the most the whole record pays: by the share of its stages this player confirmed
    const bonus = c.bonus_xp > 0 ? ` The whole record, once NetBox confirms it, is worth up to ${c.bonus_xp} XP on top of its stages.` : '';
    card.append(el('div', `When it is complete, NetBox holds ${c.holds}.${bonus}`, 's hint'));
  }
  return card;
}

export interface QuestHooks {
  /** Plays a script in the shared bubble with the given quest giver as the speaker `giver`. */
  talk(script: Script, cast: { name: string; sprite: string }): Promise<Effects>;
  notice(text: string): void;
  reduceMotion(): boolean;
  /** Opens the level a target is on (inside its building). */
  goTo(target: QuestTarget): void;
  /** Boards, the tracked line or a panel changed: markers and key handling follow. */
  changed(): void;
  /** A line was completed (so Ilatea may say her one line about it). */
  completed(v: QuestVerdict): void;
  /** The audio hook for the confirm jingle (a stub until the game has music). */
  jingle(name: 'confirm' | 'practice'): void;
  giverSprite(siteId: number): string;
  openGuide(who: 'nocti'): void;
  player(): string;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function btn(label: string, id: string, run: () => void): HTMLButtonElement {
  const b = el('button', label);
  b.type = 'button';
  b.id = id;
  b.onclick = run;
  return b;
}

/**
 * Whether tracing a cable inside a building waits for the cable tracer. Only while the tool can really be earned: the
 * player's changes would be written (not a dry-run proxy, not a read-only token, no missing permission) and NetBox has
 * a connectivity finding to work on. Everywhere else tracing stays open: a gate never takes a view away for good.
 */
export function tracerLocked(board: QuestBoard | null): boolean {
  if (!board || board.rewards.tools.includes('cable_tracer')) return false;
  return board.can_write && !board.write_blocked && board.findings.some((f) => f.rank === 'connectivity');
}

export class Quests {
  readonly boards: Record<QuestScope, QuestBoard | null> = { netbox: null, practice: null };
  /** The line the marker follows: the one the player last worked on, else the most valuable open one. */
  tracked: Quest | null = null;
  private panel: HTMLElement | null = null;
  private closePanel: (() => void) | null = null;
  private readonly next = el('button');
  private readonly giverBtn = el('button');
  private readonly pin = el('div');
  /** The site whose building the player is in, or null on the overworld. */
  private inside: number | null = null;
  lastVerdict: QuestVerdict | null = null;
  private logSite: number | null = null;
  lastError: string | null = null;

  constructor(
    private readonly host: HTMLElement,
    private readonly api: GameApi,
    private readonly hooks: QuestHooks,
    private readonly screenOf: (entity: string) => { x: number; y: number } | null,
  ) {
    if (!document.getElementById('quest-css')) {
      const s = el('style', CSS);
      s.id = 'quest-css';
      document.head.append(s);
    }
    this.next.id = 'quest-next';
    this.next.type = 'button';
    this.next.className = 'game-ui';
    this.next.onclick = () => this.openLog();
    this.giverBtn.id = 'quest-giver';
    this.giverBtn.type = 'button';
    this.giverBtn.className = 'game-ui';
    this.giverBtn.style.display = 'none';
    this.giverBtn.onclick = () => this.inside !== null && void this.talkToGiver(this.inside);
    this.pin.id = 'quest-pin';
    this.pin.className = 'game-ui';
    this.pin.append(el('i'));
    this.pin.style.display = 'none';
    host.append(this.next, this.giverBtn, this.pin);
    this.paintNext();
    const tick = () => {
      this.placePin();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  get panelOpen(): boolean {
    return this.panel !== null;
  }
  get panelId(): string | null {
    return this.panel?.id ?? null;
  }
  board(siteId: number): QuestBoard | null {
    return this.boards[scopeOf(siteId)];
  }
  rewards(): Rewards | null {
    return this.boards.netbox?.rewards ?? null;
  }
  hasTool(t: ToolId): boolean {
    return !!this.rewards()?.tools.includes(t);
  }
  giver(siteId: number): { name: string; sprite: string } {
    const name = this.board(siteId)?.sites.find((s) => s.site_id === siteId)?.giver.name ?? 'Quest giver';
    return { name, sprite: this.hooks.giverSprite(siteId) };
  }
  /** The open line about the estate itself (site 0: a NetBox with no site yet), if there is one. */
  estateLine(): Quest | null {
    return (this.boards.netbox?.quests ?? []).find((q) => q.site_id === 0 && q.status !== 'done' && q.status !== 'dismissed') ?? null;
  }
  /** A line about the estate has no quest giver: Ilatea asks it, in her own voice and with her own portrait. */
  private byIlatea = false;
  private say(script: Script, cast: { name: string; sprite: string }): Promise<Effects> {
    if (!this.byIlatea) return this.hooks.talk(script, cast);
    const nodes = Object.fromEntries(Object.entries(script.nodes).map(([k, n]) => [k, { ...n, speaker: 'ilatea' as const }]));
    return this.hooks.talk({ ...script, nodes }, cast);
  }
  /** Open lines of a site in the order the proxy offers them. */
  openAt(siteId: number): Quest[] {
    return (this.board(siteId)?.quests ?? []).filter((q) => q.site_id === siteId && q.status !== 'done' && q.status !== 'dismissed');
  }

  /**
   * Reads both logs again (the estate's and the practice island's). `fresh` has the proxy read NetBox itself again
   * (the log's "Read NetBox again": for something changed in NetBox within the last minute). Failures are said.
   */
  async refresh(fresh = false): Promise<void> {
    try {
      const [netbox, practice] = await Promise.all([this.api.questBoard('netbox', null, fresh), this.api.questBoard('practice')]);
      this.boards.netbox = netbox;
      this.boards.practice = practice;
      this.lastError = null;
    } catch (e) {
      this.lastError = `The quest log could not be read: ${errText(e)}`;
      if (!(e instanceof SceneSourceError && e.code === 'unauthorized')) this.hooks.notice(this.lastError);
    }
    const all = [...(this.boards.netbox?.quests ?? []), ...(this.boards.practice?.quests ?? [])];
    const still = this.tracked && all.find((q) => q.id === this.tracked!.id && q.scope === this.tracked!.scope && q.status !== 'done' && q.status !== 'dismissed');
    const nextId = this.boards.netbox?.next_quest_id;
    this.tracked = still || all.find((q) => q.scope === 'netbox' && q.id === nextId) || null;
    this.paintNext();
    this.hooks.changed();
    // an open quest log shows what was just read
    if (this.panel?.id === 'quest-log') this.openLog(this.logSite);
  }

  /** The player went into a site's building, or back out (null). */
  setInside(siteId: number | null): void {
    this.inside = siteId;
    this.giverBtn.style.display = siteId === null ? 'none' : 'flex';
    if (siteId !== null) {
      const g = this.giver(siteId);
      const c = drawPortrait('giver', 2, g);
      // every open line of the site, not only the few the giver offers at one time
      const n = Math.max(this.openAt(siteId).filter((q) => q.kind !== 'spot_check').length, this.board(siteId)?.sites.find((s) => s.site_id === siteId)?.open_quests ?? 0);
      this.giverBtn.replaceChildren(c, el('span', n ? `${g.name}: ${n} thing${n === 1 ? '' : 's'} to find out` : `${g.name}: all in order`));
      this.giverBtn.title = `Talk to ${g.name}, this site's quest giver`;
    }
    this.paintNext();
  }

  private paintNext(): void {
    const q = this.tracked;
    const r = this.rewards();
    // two fixed lines whose text changes in place: nothing on screen moves when the log loads or a line completes
    if (!this.next.firstChild) this.next.append(el('b'), el('span'));
    const where = q?.target ? ` → ${q.target.label}` : '';
    this.next.children[0]!.textContent = r ? `Quests · Level ${r.level} · ${r.xp} XP` : 'Quests';
    this.next.children[1]!.textContent = q ? `Next: ${q.title}${q.chain && q.chain.total > 1 ? ` (${q.chain.label})` : ''}${where}` : this.lastError ? 'The quest log is not available right now.' : this.boards.netbox ? 'Nothing open: all in order.' : 'Reading the quest log…';
    this.next.title = 'Open the quest log';
    this.next.dataset.quest = q?.id ?? '';
  }

  /** The marker over the object of the tracked line, when that object is on screen inside its building. */
  private placePin(): void {
    const t = this.tracked?.target;
    const p = t && this.inside === t.site_id && !this.panel ? this.screenOf(t.entity) : null;
    if (!p) {
      this.pin.style.display = 'none';
      return;
    }
    this.pin.style.display = 'block';
    this.pin.style.left = `${p.x}px`;
    this.pin.style.top = `${p.y - 6}px`;
    this.pin.classList.toggle('bob', !this.hooks.reduceMotion());
    this.pin.dataset.entity = t!.entity;
  }
  get pinShown(): string | null {
    return this.pin.style.display === 'none' ? null : (this.pin.dataset.entity ?? null);
  }

  // ------------------------------------------------------------------------------------------- the conversation

  /** Talking to a site's quest giver: how the site stands, and the lines on offer. */
  async talkToGiver(siteId: number): Promise<void> {
    if (this.panel) return;
    const scope = scopeOf(siteId);
    let board: QuestBoard;
    try {
      board = await this.api.questBoard(scope, siteId);
    } catch (e) {
      this.hooks.notice(`The quest giver cannot reach the quest log: ${errText(e)}`);
      return;
    }
    const cast = this.giver(siteId);
    const name = board.sites.find((s) => s.site_id === siteId)?.name ?? 'this site';
    let offer: Script;
    try {
      offer = offerScript(board, name);
    } catch (e) {
      this.hooks.notice(`The quest giver cannot say what is open here: ${errText(e)}`);
      return;
    }
    const eff = await this.say(offer, cast);
    const picked = eff.set[PICK];
    if (picked === 'log') return this.openLog(siteId);
    const quest = board.quests.find((q) => q.id === picked);
    if (quest) await this.run(quest);
    else if (board.all_in_order) await this.refresh();
  }

  /** Plays a line from where it stands until it is done, parked, closed by the player, or waits for a preview. */
  async run(start: Quest): Promise<void> {
    let q = start;
    this.byIlatea = start.site_id === 0;
    this.tracked = q;
    this.paintNext();
    const cast = this.giver(q.site_id);
    try {
      for (;;) {
        if (q.status === 'done' || q.status === 'dismissed') break;
        if (q.status === 'ready' && !q.proposal) {
          // NetBox already shows what this line was about (recorded by other means): read it again and settle the line.
          const v = await this.api.questVerify(q.id);
          await this.reward(v);
          break;
        }
        if (q.status === 'ready' || q.status === 'practised') {
          const v = await this.preview(q);
          if (!v) break;
          if (v === 'restart' || v === 'back') {
            q = await this.api.questAnswer({ scope: q.scope, quest_id: q.id, step: v });
            continue;
          }
          await this.reward(v);
          q = v.quest;
          // what NetBox (or the island) confirmed is done; the rest of an errand goes on at once. A dry run and a
          // check that confirmed nothing changed nothing: the line waits where it stood.
          if (v.complete || v.mode === 'dry_run') break;
          if (q.status === 'active' || (q.status === 'ready' && v.verified)) continue;
          break;
        }
        const step = q.step;
        if (!step) {
          await this.fieldTask(q);
          break;
        }
        const ans = step.input === 'choice' ? await this.ask(step, cast) : await this.form(q, step);
        if (!ans) break;
        q = await this.api.questAnswer({ scope: q.scope, quest_id: q.id, step: step.id, ...ans });
        this.tracked = q;
        this.paintNext();
        if (q.status === 'parked') {
          await this.fieldTask(q);
          break;
        }
        if (q.status === 'dismissed') {
          await this.say(lineScript('Good to know: that is how it is. I will not ask about it again.'), cast);
          break;
        }
        if (q.status === 'done') {
          // a spot check answered "as recorded": nothing to write, nothing to pay, one device surely right
          await this.say(lineScript('Checked: the record and the rack agree. Thank you.'), cast);
          break;
        }
      }
    } catch (e) {
      this.hooks.notice(`That quest could not go on: ${errText(e)}`);
    }
    await this.refresh();
  }

  private async ask(step: QuestStep, cast: { name: string; sprite: string }): Promise<{ choice: string } | null> {
    const eff = await this.say(stepScript(step), cast);
    const picked = eff.set[PICK];
    return typeof picked === 'string' ? { choice: picked } : null;
  }

  // ------------------------------------------------------------------------------------------- panels

  private open(id: string, title: string): { el: HTMLElement; done: Promise<void>; close: () => void } {
    this.close();
    const p = el('div');
    p.id = id;
    p.className = 'game-panel quest-panel game-ui';
    p.setAttribute('role', 'dialog');
    p.setAttribute('aria-label', title);
    p.append(el('h2', title));
    let finish!: () => void;
    const done = new Promise<void>((r) => (finish = r));
    const close = () => {
      if (this.panel !== p) return;
      p.remove();
      this.panel = null;
      this.closePanel = null;
      finish();
      this.hooks.changed();
    };
    this.panel = p;
    this.closePanel = close;
    this.host.append(p);
    this.hooks.changed();
    return { el: p, done, close };
  }
  /** Closes whatever quest panel is open (the cancel key, the menu taking over). */
  close(): void {
    this.closePanel?.();
  }

  /** A typed value or a short form (a rack audit, a site record). Null when the player closes it. */
  private async form(q: Quest, step: QuestStep): Promise<{ text?: string; choice?: string; values?: Record<string, string | number | null> } | null> {
    const { el: p, done, close } = this.open('quest-step', q.title);
    let out: { text?: string; choice?: string; values?: Record<string, string | number | null> } | null = null;
    p.append(el('p', step.prompt));
    if (step.error) {
      const e = el('p', step.error, 'error');
      e.id = 'quest-step-error';
      e.setAttribute('role', 'alert');
      p.append(e);
    }
    const inputs = new Map<string, HTMLInputElement | HTMLSelectElement>();
    if (step.input === 'text') {
      const i = el('input');
      i.type = 'text';
      i.id = 'quest-text';
      i.maxLength = 200;
      i.setAttribute('aria-label', step.prompt);
      if (step.hint) p.append(el('p', step.hint, 'hint'));
      p.append(i);
      inputs.set('', i);
    } else {
      if (step.hint) p.append(el('p', step.hint, 'hint'));
      step.fields.forEach((f, n) => {
        const label = el('label', f.label, 'f');
        const id = `quest-field-${n}`;
        label.htmlFor = id;
        let input: HTMLInputElement | HTMLSelectElement;
        if (f.type === 'select') {
          input = el('select');
          input.append(new Option('(not known yet)', ''), ...f.options.map((o) => new Option(o.label, o.id)));
        } else {
          input = el('input');
          input.type = f.type === 'number' ? 'number' : 'text';
          if (f.type === 'number') input.step = 'any';
        }
        input.id = id;
        input.dataset.field = f.id;
        p.append(label, input);
        if (f.hint) p.append(el('div', f.hint, 'hint'));
        if (f.suggestion) {
          // The label maker proposes; the player decides. Nothing is filled in until they press the button.
          const use = btn(`Use ${f.suggestion}`, `${id}-suggest`, () => ((input as HTMLInputElement).value = f.suggestion!));
          use.title = 'Label maker: the next free tag of this site\'s pattern';
          p.append(use);
        }
        inputs.set(f.id, input);
      });
    }
    const row = el('div', undefined, 'row');
    const send = () => {
      if (step.input === 'text') {
        const v = (inputs.get('') as HTMLInputElement).value.trim();
        out = v ? { text: v } : { choice: 'unknown' };
      } else {
        const values: Record<string, string | number | null> = {};
        for (const [id, i] of inputs) if (i.value.trim()) values[id] = i.value.trim();
        out = { values };
      }
      close();
    };
    row.append(
      btn(step.input === 'text' ? 'Answer' : 'Preview what I filled in', 'quest-step-send', send),
      btn("I don't know yet", 'quest-step-unknown', () => ((out = step.input === 'text' ? { choice: 'unknown' } : { values: {} }), close())),
    );
    // "that is how it is, on purpose": closes the line with nothing written (a rack whose kit carries no labels)
    step.choices.filter((c) => c.kind === 'not_applicable').forEach((c, n) => row.append(btn(c.label, `quest-step-none${n || ''}`, () => ((out = { choice: c.id }), close()))));
    row.append(btn('Close', 'quest-step-close', close));
    p.append(row);
    p.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') (e.preventDefault(), send());
    });
    (p.querySelector('input,select') as HTMLElement | null)?.focus();
    await done;
    return out;
  }

  /** A parked line: what to look for on site. It stays open, costs nothing, and resumes at the same question. */
  private async fieldTask(q: Quest): Promise<void> {
    const { el: p, done, close } = this.open('quest-field-task', `Field task: ${q.title}`);
    p.append(el('p', q.note ?? 'No rush. This stays in your quest log, nothing is lost, and it picks up at the same question when you know more.'));
    if (q.checklist.length) {
      p.append(el('h3', 'What to look for on site'));
      const ul = el('ul');
      ul.id = 'quest-checklist';
      for (const c of q.checklist) ul.append(el('li', c));
      p.append(ul);
    }
    const row = el('div', undefined, 'row');
    row.append(btn('OK, I will look', 'quest-field-ok', close));
    // the next small step has a place: the marker follows this line, and the button opens the level it is on
    if (q.target) row.append(btn(`Show me ${q.target.label}`, 'quest-field-go', () => (close(), this.hooks.goTo(q.target!))));
    row.append(btn("Nocti's hardware notes", 'quest-field-nocti', () => (close(), this.hooks.openGuide('nocti'))));
    p.append(row);
    (p.querySelector('#quest-field-ok') as HTMLElement).focus();
    await done;
  }

  /** The change a ready line proposes, as the proxy previews it; Apply writes it (or runs it as practice). */
  private async preview(q: Quest): Promise<QuestVerdict | 'restart' | 'back' | null> {
    const board = this.boards[q.scope];
    let res: ChangeResult;
    try {
      res = await this.api.questPreview(q.scope, q.id);
    } catch (e) {
      this.hooks.notice(`The preview could not be made: ${errText(e)}`);
      return null;
    }
    const { el: p, done, close } = this.open('quest-preview', `Preview: ${q.title}`);
    let out: QuestVerdict | 'restart' | 'back' | null = null;
    if (q.said.length) {
      p.append(el('h3', 'You said'));
      const ul = el('ul', undefined, 'said');
      for (const s of q.said) ul.append(el('li', `${s.question} → ${s.answer}`));
      p.append(ul);
    }
    p.append(el('h3', 'What would change in NetBox'));
    if (q.changes.length) {
      const ul = el('ul');
      ul.id = 'quest-changes';
      for (const c of q.changes) ul.append(el('li', c));
      p.append(ul);
    }
    const table = el('table', undefined, 'diff');
    table.id = 'quest-diff';
    const bad = res.ops.some((o) => o.status === 'error');
    for (const o of res.ops) {
      const tr = el('tr');
      const what = el('td', `${o.op.replace(/_/g, ' ')}${o.target.id !== null && typeof o.target.id === 'number' ? ` (${o.target.kind} ${o.target.id})` : ''}`);
      const body = el('td');
      if (o.error) body.append(el('div', o.error.message, 'error'));
      else if (o.status === 'skipped') body.textContent = 'not run';
      else if (o.status === 'no_change') body.textContent = 'already so in NetBox';
      else {
        // the list above says it in names; NetBox's own numbers for the same thing (provider_id, a_id) are left out
        const shown = o.diff.filter((d) => !(q.changes.length && INTERNAL.test(d.field)));
        for (const d of shown) body.append(el('div', `${d.field.replace(/_/g, ' ')}: ${show(d.before)} → ${show(d.after)}`));
        if (!shown.length) body.textContent = o.status === 'ok' ? 'as listed above' : o.status;
      }
      tr.append(what, body);
      table.append(tr);
    }
    p.append(table);
    // the proxy has already checked this change against the player's own NetBox permissions: said before Apply, not after
    // The line's own verdict decides, never the log's summary: a button that says "Practice run" must not write.
    // (a line with nothing proposed has no verdict of its own; there the log's summary stands)
    const real = q.scope === 'netbox' && q.write_blocked === null && (q.proposal !== null || !!board?.can_write);
    const mode = el('p', real
      ? `Apply writes this to NetBox; its changelog will name you (${this.hooks.player()}). NetBox is then read again to check the outcome.`
      : `${q.write_blocked ?? board?.write_blocked ?? 'Nothing is written.'} This is a practice run: no XP.`, 'mode');
    mode.id = 'quest-mode';
    mode.dataset.mode = real ? 'real' : q.scope === 'practice' ? 'practice' : 'dry_run';
    p.append(mode);
    const row = el('div', undefined, 'row');
    const apply = btn(real ? 'Apply to NetBox' : 'Practice run', 'quest-apply', () => {
      apply.disabled = true;
      apply.textContent = 'Working…';
      void this.api.questApply(q.scope, q.id).then(
        (v) => ((out = v), close()),
        (e) => {
          apply.disabled = false;
          apply.textContent = real ? 'Apply to NetBox' : 'Practice run';
          const err = el('p', `Nothing was confirmed: ${errText(e)}`, 'error');
          err.id = 'quest-apply-error';
          err.setAttribute('role', 'alert');
          p.querySelector('#quest-apply-error')?.remove();
          p.append(err);
        },
      );
    });
    if (bad) apply.disabled = true;
    row.append(apply);
    if (q.said.length) row.append(btn('Change my last answer', 'quest-back', () => ((out = 'back'), close())));
    row.append(btn('Answer again from the start', 'quest-restart', () => ((out = 'restart'), close())), btn('Close', 'quest-preview-close', close));
    p.append(row);
    (bad ? (p.querySelector('#quest-restart') as HTMLElement) : apply).focus();
    await done;
    return out;
  }

  /** The reward moment: what NetBox confirmed, what the admin can now do, XP, tools. Feedback you can feel. */
  private async reward(v: QuestVerdict): Promise<void> {
    this.lastVerdict = v;
    const calm = this.hooks.reduceMotion();
    const box = el('div', undefined, 'quest-reward game-ui');
    box.id = 'quest-reward';
    box.setAttribute('role', 'alertdialog');
    box.dataset.mode = v.mode;
    box.dataset.verified = String(v.verified);
    const real = v.mode === 'real';
    // a dry run on real data wrote nothing, so it is never "complete"; the practice island's own check can be
    const notWritten = real && !!v.result && !v.result.applied;
    box.append(el('h2', real ? (v.quest.status === 'dismissed' ? 'Line closed' : notWritten ? 'Not written' : v.complete ? (v.verified ? 'NetBox confirms it' : 'Still counted')
      : v.verified ? 'Partly confirmed' : 'Not confirmed yet')
      : v.mode === 'practice' && v.complete ? 'Practice complete' : 'Practice run'));
    box.append(el('p', v.message));
    if (v.can_now) box.append(el('p', v.can_now, 'now'));
    if (v.trace) box.append(el('p', v.trace, 'hint'));
    if (v.xp_awarded > 0) {
      const xp = el('div', undefined, 'xp');
      xp.id = 'quest-xp';
      xp.append(spriteCanvas('icon_xp', 3), `+${v.xp_awarded} XP`);
      box.append(xp);
    }
    for (const t of v.unlocked) {
      const row = el('p');
      row.className = 'tool';
      row.append(spriteCanvas(TOOLS[t].icon, 3), el('b', `New tool: ${TOOLS[t].name}. `), TOOLS[t].unlocks);
      box.append(row);
    }
    if (v.potions_awarded > 0) {
      const row = el('p');
      row.append(spriteCanvas('icon_potion', 3), `Level ${v.rewards.level}! A potion for the road.`);
      box.append(row);
    }
    if (v.chain && v.chain.total > 1) {
      // where this leaves the chain the line belongs to; a whole verified record pays a bonus of its own
      const row = el('div', `${v.chain.title} · ${v.chain.label}${v.chain_xp > 0 ? ` · ${v.chain_xp} XP of this for the whole record` : ''}`, 'chain');
      row.id = 'quest-reward-chain';
      row.dataset.status = v.chain.status;
      box.append(row);
    }
    let finish!: () => void;
    const done = new Promise<void>((r) => (finish = r));
    const ok = btn('Onwards', 'quest-reward-ok', () => (box.remove(), finish()));
    box.append(el('div'), ok);
    const felt = v.complete && (real ? v.verified : v.mode === 'practice');
    if (felt && !calm) {
      const sp = el('div', undefined, 'sparkle');
      for (let i = 0; i < 10; i++) {
        const s = el('i');
        s.style.left = `${5 + ((i * 37) % 90)}%`;
        s.style.top = `${10 + ((i * 53) % 80)}%`;
        s.style.animationDelay = `${(i % 5) * 0.12}s`;
        sp.append(s);
      }
      box.append(sp);
      document.body.classList.add('quest-shake');
      window.setTimeout(() => document.body.classList.remove('quest-shake'), 320);
    }
    if (felt) this.hooks.jingle(real ? 'confirm' : 'practice');
    this.panel = box;
    this.closePanel = () => ok.click();
    this.host.append(box);
    this.hooks.changed();
    ok.focus();
    await done;
    this.panel = null;
    this.closePanel = null;
    this.hooks.changed();
    if (v.complete) this.hooks.completed(v);
  }

  /** The quest log: rewards, each site's progress, and every line the player has open, waiting or done. */
  openLog(siteId: number | null = null): void {
    this.logSite = siteId;
    const { el: p, close } = this.open('quest-log', 'Quest log');
    const r = this.rewards();
    if (r) {
      const row = el('div', undefined, 'rewards');
      row.id = 'quest-rewards';
      const xp = el('span');
      xp.append(spriteCanvas('icon_xp', 2), `Level ${r.level} · ${r.xp} / ${r.next_level_xp} XP`);
      const pot = el('span');
      pot.append(spriteCanvas('icon_potion', 2), `${r.potions} potion${r.potions === 1 ? '' : 's'}`);
      row.append(xp, pot);
      for (const t of r.tools) {
        const s = el('span');
        s.title = TOOLS[t].unlocks;
        s.append(spriteCanvas(TOOLS[t].icon, 2), TOOLS[t].name);
        row.append(s);
      }
      p.append(row);
      const counts = el('p', `${r.completed} confirmed by NetBox · ${r.practised} practised` + (r.lapsed ? ` · ${r.lapsed} since undone in NetBox (nothing is reopened; it is only counted)` : ''), 'hint');
      counts.id = 'quest-counts';
      p.append(counts);
    }
    if (this.lastError) p.append(el('p', this.lastError, 'error'));
    const blocked = this.boards.netbox?.write_blocked;
    if (blocked) {
      // said up front: whether answers would really be written for this player, and if not, why
      const note = el('p', this.boards.netbox?.can_write ? blocked : `${blocked} Lines you finish count as practice: no XP.`, 'mode');
      note.id = 'quest-write-note';
      p.append(note);
    }
    const setup = this.boards.netbox?.setup ?? [];
    if (setup.length) {
      // what this NetBox cannot hold yet is said once, up front; the game does not ask for it
      const note = el('p', setup.join(' '), 'mode');
      note.id = 'quest-setup';
      p.append(note);
    }
    const sites = [...(this.boards.netbox?.sites ?? []), ...(this.boards.practice?.sites ?? [])];
    const quests = [...(this.boards.netbox?.quests ?? []), ...(this.boards.practice?.quests ?? [])];
    const chains = [...(this.boards.netbox?.chains ?? []), ...(this.boards.practice?.chains ?? [])].filter((c) => siteId === null || c.site_id === siteId);
    if (chains.length) {
      // chains first: each is a short series (physical, logical, administrative) that ends in a whole record
      p.append(el('h3', 'Chains'));
      const box = el('div');
      box.id = 'quest-chains';
      // those waiting on a fact the player may now know, then those in play (the proxy lists at most a few per site),
      // then the last few complete ones
      const order = { open_questions: 0, open: 1, complete: 2 } as const;
      const sorted = [...chains].sort((a, b) => order[a.status] - order[b.status]);
      const shown = [...sorted.filter((c) => c.status !== 'complete'), ...sorted.filter((c) => c.status === 'complete').slice(0, 6)];
      for (const c of shown) box.append(chainCard(c));
      const more = chains.length - shown.length + (this.boards.netbox?.chains_queued ?? 0);
      if (more > 0) box.append(el('p', `${more} more chain${more === 1 ? '' : 's'} come into play as these are completed.`, 'hint'));
      p.append(box);
    }
    const section = (title: string, list: Quest[], id: string) => {
      if (!list.length) return;
      p.append(el('h3', title));
      const box = el('div');
      box.id = id;
      for (const q of list) {
        const card = el('div', undefined, 'q');
        card.dataset.quest = q.id;
        card.dataset.status = q.status;
        const site = sites.find((s) => s.site_id === q.site_id)?.name ?? '';
        const t = el('div', undefined, 't');
        t.append(el('span', q.rank, `tag rank-${q.rank}`), q.title);
        card.append(t, el('div', `${site}${q.scope === 'practice' ? ' (practice, never NetBox)' : ''} · ${STATUS_WORDS[q.status]}${q.lapsed ? ' · since undone in NetBox' : ''} · about ${q.minutes} min`, 's'));
        if (q.chain && q.chain.total > 1) {
          const c = el('div', `Chain: ${q.chain.title} · ${q.chain.label}`, 's in-chain');
          c.dataset.chain = q.chain.id;
          card.append(c);
        }
        if (q.status !== 'done' && q.status !== 'dismissed') {
          card.append(el('div', q.purpose, 's'));
          if (q.status === 'parked' && q.checklist.length) card.append(el('div', `Look for: ${q.checklist[0]}`, 's'));
          else if (q.step) card.append(el('div', `Next step: ${q.step.prompt}`, 's'));
          else if (q.note) card.append(el('div', q.note, 's'));
          const row = el('div', undefined, 'row');
          row.append(btn(q.status === 'ready' && !q.proposal ? 'Check NetBox' : q.status === 'offered' ? 'Start' : q.status === 'parked' ? 'I know more now' : q.status === 'ready' || q.status === 'practised' ? 'Show the change' : 'Carry on', `quest-go-${box.children.length}`,
            () => (close(), void this.run(q))));
          if (q.target) row.append(btn('Point me there', `quest-track-${box.children.length}`, () => {
            this.tracked = q;
            this.paintNext();
            close();
            this.hooks.goTo(q.target!);
          }));
          card.append(row);
        }
        box.append(card);
      }
      p.append(box);
    };
    const mine = quests.filter((q) => siteId === null || q.site_id === siteId);
    section('Open', mine.filter((q) => ['offered', 'active', 'ready'].includes(q.status)), 'quest-open');
    section('Waiting for you', mine.filter((q) => q.status === 'parked' || q.status === 'practised'), 'quest-waiting');
    section('Done', mine.filter((q) => q.status === 'done' || q.status === 'dismissed'), 'quest-done');
    if (!mine.length) p.append(el('p', siteId === null ? (this.boards.netbox?.message ?? 'No quests yet.') : 'Nothing open here: all in order.'));
    p.append(el('h3', 'Sites'));
    const st = el('div');
    st.id = 'quest-sites';
    for (const s of sites) {
      const row = el('div', undefined, 'row');
      row.dataset.site = String(s.site_id);
      const bar = el('div', undefined, 'bar');
      const fill = el('i');
      fill.style.width = `${s.completeness.percent}%`;
      bar.append(fill);
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', `${s.completeness.percent} percent complete`);
      row.append(el('span', `${s.name}: ${s.completeness.percent}%`), bar);
      if (s.boss_ready) {
        row.append(spriteCanvas('boss_mark', 2));
        if (s.boss_acknowledged) row.append(el('span', 'Its Confusion waits for you.'));
        else row.append(btn('Its Confusion is ready: acknowledge', `quest-boss-${s.site_id}`, () => void this.ackBoss(s.site_id).then(() => this.openLog(siteId))));
      } else row.append(el('span', `${s.completeness.open} open`, 'hint'));
      st.append(row);
    }
    p.append(st);
    const foot = el('div', undefined, 'row');
    foot.append(btn('◀ Close', 'quest-log-close', close), btn('Read NetBox again', 'quest-log-refresh', () => void this.refresh(true)));
    if (this.boards.practice?.quests.some((q) => q.status === 'done') || (this.boards.practice?.rewards.practised ?? 0) > 0)
      foot.append(btn('Start the practice island afresh', 'quest-practice-reset', () => void this.api.practiceReset().then(() => this.refresh()).then(() => this.openLog(siteId))));
    p.append(foot);
    (p.querySelector('#quest-go-0') as HTMLElement | null ?? (p.querySelector('#quest-log-close') as HTMLElement)).focus();
  }

  /** The site's Confusion has been seen. The fight itself comes with the combat slice: this only acknowledges it. */
  async ackBoss(siteId: number): Promise<void> {
    try {
      this.boards[scopeOf(siteId)] = await this.api.bossAck(scopeOf(siteId), siteId);
    } catch (e) {
      this.hooks.notice(`That could not be saved: ${errText(e)}`);
    }
    this.hooks.changed();
  }
}

/** Diff fields that are NetBox ids or reference plumbing, said in names by the proxy's list of changes. */
const INTERNAL = /(^|_)id$|_kind$|^term_side$/;

function show(v: unknown): string {
  return v === null || v === undefined || v === '' ? '(empty)' : String(v);
}
