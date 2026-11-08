// The speech bubble every NPC uses: portrait, name, typewriter text (skippable), choices by keyboard, mouse or touch.
// It never traps the player: the close button and the cancel key end the conversation at any time, and the
// typewriter finishes at the first key or tap. With reduce motion, text appears at once.
import { DialogueRun, type Action, type Effects, type Script } from './dialogue';
import { drawPortrait, SPEAKER_NAMES } from './portraits';
import type { Flags } from '../contracts/scene.gen';

export const TYPE_MS = 22;
export type DialogueKey = 'up' | 'down' | 'interact' | 'cancel' | 'digit';

const CSS = `
#dialogue{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);width:min(680px,calc(100vw - 24px));z-index:20;
  display:flex;gap:12px;align-items:flex-start;background:#f6efd9;color:#1d1a14;border:4px solid #1d1a14;box-shadow:0 0 0 2px #f6efd9,0 0 0 4px #1d1a14;
  padding:10px 12px;font:15px/1.45 ui-monospace,monospace;box-sizing:border-box}
#dialogue .portrait{flex:none;border:2px solid #1d1a14;background:#1d1a14}
#dialogue .body{flex:1;min-width:0}
#dialogue .name{font-weight:bold;text-transform:uppercase;letter-spacing:1px;margin-bottom:2px}
#dialogue-text{min-height:2.9em;cursor:pointer;white-space:pre-wrap}
#dialogue-more{float:right;margin-left:6px}
#dialogue-choices{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:4px}
#dialogue-choices button{width:100%;text-align:left;font:inherit;color:#1d1a14;background:#fffaf0;border:2px solid #1d1a14;padding:6px 8px;min-height:36px;cursor:pointer}
#dialogue-choices button[aria-selected=true]{background:#1d1a14;color:#fffaf0;text-decoration:underline}
#dialogue-choices button:focus-visible{outline:3px dashed #1d1a14;outline-offset:2px}
#dialogue-close{flex:none;font:inherit;font-weight:bold;background:#fffaf0;color:#1d1a14;border:2px solid #1d1a14;width:32px;height:32px;cursor:pointer}
`;

export class DialogueBox {
  private el: HTMLElement | null = null;
  private run: DialogueRun | null = null;
  private shownChars = 0;
  private timer = 0;
  private sel = 0;
  private resolve: ((e: Effects) => void) | null = null;
  reduceMotion = false;
  /** Called as soon as a choice with an action is picked (a hint-level change applies while the talk goes on). */
  onAction: ((a: Action) => void) | null = null;
  /**
   * Client points the bubble should not cover (the speaker and the player on the green). The bubble sits at the
   * bottom; when that hides one of them it moves to the top, unless the top hides one too.
   */
  avoid: (() => readonly { x: number; y: number }[]) | null = null;

  constructor(private readonly host: HTMLElement) {
    if (!document.getElementById('dialogue-css')) {
      const s = document.createElement('style');
      s.id = 'dialogue-css';
      s.textContent = CSS;
      document.head.appendChild(s);
    }
  }

  get open(): boolean {
    return this.run !== null;
  }
  /** Test/inspection view of what is on screen. */
  get state(): { script: string; node: string | null; text: string; typing: boolean; choices: string[]; selected: number } | null {
    const r = this.run;
    if (!r) return null;
    return { script: r.script.id, node: r.nodeName, text: r.text, typing: this.typing, choices: r.choices.map((c) => c.label), selected: this.sel };
  }
  private get typing(): boolean {
    return !!this.run?.current && this.shownChars < this.run.text.length;
  }

  /** Plays a script; resolves with the flags it set and the actions picked once it ends or is closed. */
  play(script: Script, flags: Flags, keyLabel?: (action: string) => string): Promise<Effects> {
    this.finish();
    this.run = new DialogueRun(script, flags, keyLabel);
    return new Promise((res) => {
      this.resolve = res;
      this.render();
    });
  }

  /** Ends the conversation now (close button, cancel key, scene change). */
  close(): void {
    if (!this.run) return;
    this.run.close();
    this.finish();
  }

  /** A key the shell mapped to an action. Returns true when the bubble used it. */
  key(k: DialogueKey, digit = 0): boolean {
    const r = this.run;
    if (!r) return false;
    if (k === 'cancel') return this.close(), true;
    if (this.typing) {
      if (k === 'interact' || k === 'digit') this.skip();
      return true;
    }
    const n = r.choices.length;
    if (k === 'up' && n) this.select((this.sel + n - 1) % n);
    else if (k === 'down' && n) this.select((this.sel + 1) % n);
    else if (k === 'digit' && digit >= 1 && digit <= n) this.pick(digit - 1);
    else if (k === 'interact') n ? this.pick(this.sel) : this.next();
    return true;
  }

  private skip(): void {
    if (!this.run?.current) return;
    this.shownChars = this.run.text.length;
    this.paintText();
  }
  private next(): void {
    this.run?.advance();
    this.after();
  }
  pick(i: number): void {
    const c = this.run?.choices[i];
    if (!c || this.typing) return;
    const run = this.run!;
    const had = run.effects.actions.length;
    run.choose(c.index);
    for (const a of run.effects.actions.slice(had)) this.onAction?.(a);
    this.after();
  }
  private after(): void {
    if (!this.run || this.run.done) this.finish();
    else this.render();
  }
  private select(i: number): void {
    this.sel = i;
    this.el?.querySelectorAll<HTMLButtonElement>('#dialogue-choices button').forEach((b, k) => {
      b.setAttribute('aria-selected', String(k === i));
      b.firstChild!.textContent = k === i ? '▶ ' : '  ';
    });
  }

  private finish(): void {
    clearInterval(this.timer);
    this.el?.remove();
    this.el = null;
    const r = this.run, res = this.resolve;
    this.run = null;
    this.resolve = null;
    if (r && res) res(r.effects);
  }

  private render(): void {
    const node = this.run!.current!;
    clearInterval(this.timer);
    this.el?.remove();
    const el = document.createElement('div');
    el.id = 'dialogue';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', `${SPEAKER_NAMES[node.speaker]} says`);
    el.dataset.speaker = node.speaker;
    el.append(drawPortrait(node.speaker, window.innerWidth >= 640 ? 3 : 2));
    const body = document.createElement('div');
    body.className = 'body';
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = SPEAKER_NAMES[node.speaker];
    const text = document.createElement('div');
    text.id = 'dialogue-text';
    text.setAttribute('aria-live', 'polite');
    text.onclick = () => (this.typing ? this.skip() : this.run?.choices.length ? undefined : this.next());
    body.append(name, text);
    const choices = this.run!.choices;
    if (choices.length) {
      const ul = document.createElement('ul');
      ul.id = 'dialogue-choices';
      choices.forEach((c, i) => {
        const li = document.createElement('li');
        const b = document.createElement('button');
        b.type = 'button';
        b.append(document.createTextNode('  '), `${i + 1}. ${c.label}`);
        b.onclick = () => (this.typing ? this.skip() : this.pick(i));
        li.append(b);
        ul.append(li);
      });
      body.append(ul);
    }
    const close = document.createElement('button');
    close.id = 'dialogue-close';
    close.type = 'button';
    close.textContent = '×';
    close.title = 'Close';
    close.setAttribute('aria-label', 'Close');
    close.onclick = () => this.close();
    el.append(body, close);
    this.host.append(el);
    this.el = el;
    this.place();
    this.sel = 0;
    this.shownChars = this.reduceMotion ? this.run!.text.length : 0;
    this.paintText();
    this.select(0);
    if (!this.reduceMotion)
      this.timer = window.setInterval(() => {
        this.shownChars++;
        this.paintText();
        if (!this.typing) clearInterval(this.timer);
      }, TYPE_MS);
  }

  /** Bottom unless that covers a point to avoid and the top does not. */
  private place(): void {
    const el = this.el;
    const pts = this.avoid?.() ?? [];
    if (!el || !pts.length) return;
    const covers = () => {
      const r = el.getBoundingClientRect();
      const m = 24; // a sprite is drawn around its centre point
      return pts.some((p) => p.x >= r.left - m && p.x <= r.right + m && p.y >= r.top - m && p.y <= r.bottom + m);
    };
    if (!covers()) return;
    el.style.top = '16px';
    el.style.bottom = 'auto';
    el.dataset.place = 'top';
    if (covers()) {
      el.style.top = '';
      el.style.bottom = '';
      delete el.dataset.place;
    }
  }

  private paintText(): void {
    const node = this.run?.current;
    const t = this.el?.querySelector('#dialogue-text');
    if (!node || !t) return;
    t.textContent = this.run!.text.slice(0, this.shownChars);
    if (!this.typing && !node.choices) {
      const more = document.createElement('span');
      more.id = 'dialogue-more';
      more.textContent = '▼ next';
      t.append(more);
    }
    this.el!.dataset.typing = this.typing ? '1' : '0';
  }
}
