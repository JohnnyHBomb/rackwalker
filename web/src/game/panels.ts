// Game panels (DOM): sign-in, the menu, the settings page and the owls' field-guide pages. One look for all of them:
// dark ink on parchment, thick pixel borders, focus shown by a dashed outline and the selected state by a marker and
// underline as well as colour. Nothing here flashes or animates.
import type { Catalogue, CatalogueItem, GameSettings, HintLevel, PackList } from '../contracts/scene.gen';
import { HINT_LEVELS } from './dialogue';
import { ACTION_LABELS, DEFAULT_KEYS, KEY_ACTIONS, keyName, rebind, type KeyAction } from './keys';
import { drawPortrait } from './portraits';

const CSS = `
.game-panel{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:30;width:min(520px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;
  box-sizing:border-box;background:#f6efd9;color:#1d1a14;border:4px solid #1d1a14;box-shadow:0 0 0 2px #f6efd9,0 0 0 4px #1d1a14;padding:14px 16px;font:15px/1.45 ui-monospace,monospace}
.game-panel h2{font-size:17px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px}
.game-panel p{margin:6px 0}
.game-panel button,.game-panel select,.game-panel input{font:inherit;color:#1d1a14;background:#fffaf0;border:2px solid #1d1a14;padding:5px 9px;min-height:34px}
.game-panel button{cursor:pointer}
.game-panel input[type=checkbox]{width:20px;height:20px;min-height:0;padding:0;margin:0 6px 0 0;vertical-align:middle;accent-color:#1d1a14}
.game-panel input[type=range]{padding:0;min-height:0}
.game-panel label.check{display:block;margin:6px 0}
.game-panel :focus-visible{outline:3px dashed #1d1a14;outline-offset:2px}
.game-panel .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.game-panel .menu-list{display:flex;flex-direction:column;gap:6px}
.game-panel .menu-list button{text-align:left}
.game-panel .error{color:#8a1c12;font-weight:bold}
.game-panel .error::before{content:"⚠ "}
.game-panel table{border-collapse:collapse;width:100%}
.game-panel td{padding:3px 4px;border-bottom:1px solid #c8bc9c}
.game-panel .guide{display:flex;gap:12px;align-items:flex-start}
.game-panel kbd{border:1px solid #1d1a14;padding:0 4px;background:#fffaf0}
.game-panel .cat h3{font-size:14px;margin:10px 0 2px;border-bottom:2px solid #1d1a14}
.game-panel .cat .mark{display:inline-block;width:1.4em;font-weight:bold;text-align:center}
.game-panel .cat .note{font-size:13px;color:#4a4335}
.game-panel .cat tr[data-status=verified] td:first-child{font-weight:bold}
.game-panel details{margin:8px 0}
.game-panel details summary{cursor:pointer;font-weight:bold}
#game-button{position:fixed;right:8px;bottom:8px;z-index:15;font:14px ui-monospace,monospace;color:#1d1a14;background:#f6efd9;border:3px solid #1d1a14;padding:6px 10px;min-height:40px;cursor:pointer}
#game-notice{position:fixed;left:50%;top:8px;transform:translateX(-50%);z-index:35;width:min(620px,calc(100vw - 24px));box-sizing:border-box;
  background:#f6efd9;color:#1d1a14;border:4px solid #8a1c12;box-shadow:0 0 0 2px #f6efd9;padding:8px 12px;font:14px/1.4 ui-monospace,monospace;
  display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#game-notice p{margin:0;flex:1 1 260px;font-weight:bold}
#game-notice p::before{content:"⚠ "}
#game-notice button{font:inherit;color:#1d1a14;background:#fffaf0;border:2px solid #1d1a14;padding:4px 9px;min-height:32px;cursor:pointer}
#game-notice button:focus-visible{outline:3px dashed #1d1a14;outline-offset:2px}
#scene-card{position:fixed;inset:0;z-index:25;background:#0e1410;color:#f6efd9;display:flex;align-items:center;justify-content:center;font:18px ui-monospace,monospace;letter-spacing:1px}
`;

export function ensureCss(): void {
  if (document.getElementById('game-css')) return;
  const s = document.createElement('style');
  s.id = 'game-css';
  s.textContent = CSS;
  document.head.appendChild(s);
}

function panel(id: string, title: string): HTMLElement {
  ensureCss();
  document.getElementById(id)?.remove();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'game-panel game-ui';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', title);
  const h = document.createElement('h2');
  h.textContent = title;
  el.append(h);
  return el;
}
function button(label: string, onclick: () => void, id?: string): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  if (id) b.id = id;
  b.onclick = onclick;
  return b;
}
function para(text: string): HTMLParagraphElement {
  const p = document.createElement('p');
  p.textContent = text;
  return p;
}

/** Sign-in with a NetBox API token. Resolves when `submit` accepts the token; the field is cleared either way. */
export function signInPanel(host: HTMLElement, submit: (token: string) => Promise<string | null>, why: string | null = null): Promise<void> {
  const el = panel('signin', 'Sign in');
  if (why) {
    const w = para(why);
    w.id = 'signin-why';
    w.className = 'error';
    w.setAttribute('role', 'alert');
    el.append(w);
  }
  el.append(
    para('RackWalker plays as your NetBox user. Paste one of your NetBox API tokens: a read-only one is enough (NetBox: your user menu, API Tokens).'),
    para('The game asks NetBox once who owns it, then forgets it. It is never stored.'),
  );
  const form = document.createElement('form');
  form.className = 'row';
  const label = document.createElement('label');
  label.textContent = 'NetBox API token';
  label.htmlFor = 'signin-token';
  const input = document.createElement('input');
  input.id = 'signin-token';
  input.type = 'password';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.style.flex = '1';
  const go = document.createElement('button');
  go.type = 'submit';
  go.id = 'signin-submit';
  go.textContent = 'Sign in';
  const err = document.createElement('p');
  err.id = 'signin-error';
  err.setAttribute('role', 'alert');
  form.append(label, input, go);
  el.append(form, err);
  host.append(el);
  input.focus();
  return new Promise((resolve) => {
    form.onsubmit = async (e) => {
      e.preventDefault();
      const token = input.value.trim();
      input.value = '';
      go.disabled = true;
      const problem = token ? await submit(token) : 'Paste a token first.';
      go.disabled = false;
      if (problem === null) {
        el.remove();
        resolve();
      } else {
        err.className = 'error';
        err.textContent = problem;
        input.focus();
      }
    };
  });
}

/**
 * A message the player must see (a save that failed, a building that would not open) at the top of the screen, with
 * optional buttons. One at a time: a new notice replaces the old one. Never blocks play.
 */
export function showNotice(host: HTMLElement, text: string, actions: readonly { label: string; id: string; run: () => void }[] = []): HTMLElement {
  ensureCss();
  document.getElementById('game-notice')?.remove();
  const el = document.createElement('div');
  el.id = 'game-notice';
  el.className = 'game-ui';
  el.setAttribute('role', 'alert');
  el.append(para(text));
  for (const a of actions) el.append(button(a.label, a.run, a.id));
  host.append(el);
  return el;
}
export function clearNotice(): void {
  document.getElementById('game-notice')?.remove();
}

/** The game could not load (proxy or NetBox down): says why and waits for "Try again". */
export function loadFailedPanel(host: HTMLElement, problem: string): Promise<void> {
  const el = panel('load-failed', 'The game could not load');
  const msg = para(problem);
  msg.className = 'error';
  msg.setAttribute('role', 'alert');
  el.append(msg, para('Nothing is lost: your progress is kept by the proxy. Check that the proxy and NetBox are running, then try again.'));
  return new Promise((resolve) => {
    const again = button('Try again', () => (el.remove(), resolve()), 'load-retry');
    el.append(again);
    host.append(el);
    again.focus();
  });
}

export interface MenuActions {
  talk(): void;
  guide(who: 'byte' | 'nocti'): void;
  settings(): void;
  signOut(): void;
  close(): void;
  /** The quest log. */
  quests(): void;
  /** Go to the practice island (made-up data; nothing reaches NetBox). */
  practice(): void;
  /** The device catalogue: the models of the player's own NetBox. */
  catalogue(): void;
}

/** `saveProblem` is null while saving works; otherwise why progress is not being saved right now. */
export function menuPanel(host: HTMLElement, player: string, a: MenuActions, saveProblem: string | null = null): HTMLElement {
  const el = panel('game-menu', 'Menu');
  el.append(para(`Playing as ${player}.`));
  const save = para(saveProblem ? `Progress is not being saved right now: ${saveProblem}` : 'Progress saves by itself.');
  save.id = 'menu-save';
  if (saveProblem) save.className = 'error';
  el.append(save);
  const list = document.createElement('div');
  list.className = 'menu-list';
  list.append(
    button('▶ Back to the game', a.close, 'menu-resume'),
    button('Quest log', a.quests, 'menu-quests'),
    button('Device catalogue', a.catalogue, 'menu-catalogue'),
    button('Practice island (made up: never touches NetBox)', a.practice, 'menu-practice'),
    button('Talk to Ilatea', a.talk, 'menu-ilatea'),
    button('Byte: commands and hardening', () => a.guide('byte'), 'menu-byte'),
    button('Nocti: hardware and best practice', () => a.guide('nocti'), 'menu-nocti'),
    button('Settings', a.settings, 'menu-settings'),
    button('Sign out', a.signOut, 'menu-signout'),
  );
  el.append(list);
  host.append(el);
  (list.firstChild as HTMLButtonElement).focus();
  return el;
}

const GUIDES = {
  byte: {
    title: 'Byte',
    sub: 'Field guide: commands, command patterns and hardening',
    body: 'Byte keeps the command pages: what to type on which platform, and the small hardening steps that matter. The first pages arrive in a later version; reading them will never block play.',
  },
  nocti: {
    title: 'Nocti',
    sub: 'Field guide: hands-on hardware setup and best practice',
    body: 'Nocti keeps the hands-on pages: cables, labels, rack mounting, airflow. Safety notes come first on every page. The first pages arrive in a later version; reading them will never block play.',
  },
} as const;

export function guidePanel(host: HTMLElement, who: 'byte' | 'nocti', back: () => void): HTMLElement {
  const g = GUIDES[who];
  const el = panel(`guide-${who}`, g.title);
  const row = document.createElement('div');
  row.className = 'guide';
  const text = document.createElement('div');
  text.append(para(g.sub), para(g.body));
  row.append(drawPortrait(who, 3), text);
  el.append(row, button('◀ Back to the menu', back, 'guide-back'));
  host.append(el);
  (el.querySelector('#guide-back') as HTMLButtonElement).focus();
  return el;
}

// Each state has its own mark and word, so it does not rest on colour.
const CAT_MARK: Readonly<Record<CatalogueItem['status'], [string, string]>> = {
  verified: ['✔', 'confirmed by the device-type library'],
  unverified: ['?', 'not confirmed'],
  mismatch: ['≠', 'part number differs from the library'],
};

/**
 * The device catalogue: the manufacturers and models of the player's own NetBox, as a collection. What the device-type
 * library confirms carries a tick; the rest is kept as recorded. It counts only what the player's NetBox holds: there
 * is no target to chase and nothing is lost by leaving it. The packs the game ships with are listed below it, with
 * what each is complete for and where it came from.
 */
export function cataloguePanel(host: HTMLElement, load: (fresh: boolean) => Promise<Catalogue>, packs: () => Promise<PackList>, back: () => void): HTMLElement {
  const el = panel('game-catalogue', 'Device catalogue');
  const body = document.createElement('div');
  body.className = 'cat';
  body.id = 'catalogue-body';
  body.append(para('Reading your NetBox...'));
  const show = (c: Catalogue) => {
    body.replaceChildren();
    const head = para(c.message);
    head.id = 'catalogue-message';
    body.append(head);
    for (const m of c.manufacturers) {
      const h = document.createElement('h3');
      h.textContent = `${m.name || '(no manufacturer)'}: ${m.verified} of ${m.total} confirmed`;
      const table = document.createElement('table');
      for (const it of c.items.filter((i) => i.manufacturer === m.name)) {
        const tr = document.createElement('tr');
        tr.dataset.status = it.status;
        tr.dataset.model = it.model;
        const [mark, word] = CAT_MARK[it.status];
        const name = document.createElement('td');
        const sign = document.createElement('span');
        sign.className = 'mark';
        sign.textContent = mark;
        sign.setAttribute('aria-hidden', 'true');
        name.append(sign, it.model + (it.part_number && it.part_number !== it.model ? ` (${it.part_number})` : ''));
        const state = document.createElement('td');
        state.textContent = `${word} · ${it.devices} device${it.devices === 1 ? '' : 's'}`;
        if (it.note) {
          const note = document.createElement('div');
          note.className = 'note';
          note.textContent = it.note;
          state.append(note);
        }
        tr.append(name, state);
        table.append(tr);
      }
      body.append(h, table);
    }
  };
  const fail = (e: unknown) => {
    const p = para(`The catalogue could not be read: ${e instanceof Error ? e.message : String(e)}`);
    p.className = 'error';
    body.replaceChildren(p);
  };
  const about = document.createElement('details');
  about.id = 'catalogue-packs';
  const sum = document.createElement('summary');
  sum.textContent = 'The packs this game ships with';
  about.append(sum);
  void packs().then((list) => {
    for (const e of list.errors) {
      const p = para(e);
      p.className = 'error';
      about.append(p);
    }
    for (const p of list.packs) {
      const row = para(`${p.title} (${p.entries} entries, read ${p.read}${p.partial ? ', partial' : ''}). ${p.coverage}`);
      row.dataset.pack = p.id;
      about.append(row);
      if (p.missing.length) {
        const miss = para(`Missing: ${p.missing.join('; ')}.`);
        miss.className = 'note';
        about.append(miss);
      }
    }
    if (list.library) about.append(para(list.library.import_from === 'none'
      ? 'A model cannot be imported right now: this game has no copy of the device-type library and may not fetch it. Searching still works.'
      : `A model is imported from ${list.library.import_from === 'local' ? 'a local copy of' : 'the pinned commit of'} the device-type library (${list.library.commit.slice(0, 10)}), checked by SHA-256.`));
  }).catch(() => about.append(para('The list of packs could not be read.')));
  const row = document.createElement('div');
  row.className = 'row';
  row.append(button('◀ Back to the menu', back, 'catalogue-back'), button('Read NetBox again', () => void load(true).then(show, fail), 'catalogue-fresh'));
  el.append(body, about, row);
  host.append(el);
  void load(false).then(show, fail);
  (el.querySelector('#catalogue-back') as HTMLButtonElement).focus();
  return el;
}

const HINT_TEXT: Readonly<Record<HintLevel, string>> = {
  off: 'off: she speaks only when you call her',
  low: 'low: first-time essentials only',
  normal: 'normal: also when you seem stuck',
  high: 'high: also extra tips',
};

/** The settings page. Each change is applied at once through `save`; key rows wait for the next key press. */
export function settingsPanel(host: HTMLElement, current: GameSettings, save: (s: GameSettings) => void, back: () => void): HTMLElement {
  let s: GameSettings = structuredClone(current);
  const el = panel('game-settings', 'Settings');
  const commit = () => save(structuredClone(s));

  const hint = document.createElement('select');
  hint.id = 'set-hint';
  for (const h of HINT_LEVELS) hint.append(new Option(HINT_TEXT[h], h, false, h === s.hint_level));
  hint.onchange = () => ((s = { ...s, hint_level: hint.value as HintLevel }), commit());
  const hintRow = document.createElement('label');
  hintRow.className = 'row';
  hintRow.append('Ilatea’s hints: ', hint);

  const mute = document.createElement('input');
  mute.type = 'checkbox';
  mute.id = 'set-mute';
  mute.checked = s.audio.muted;
  mute.onchange = () => ((s = { ...s, audio: { ...s.audio, muted: mute.checked } }), commit());
  const vol = document.createElement('input');
  vol.type = 'range';
  vol.id = 'set-volume';
  vol.min = '0';
  vol.max = '100';
  vol.value = String(Math.round(s.audio.volume * 100));
  vol.setAttribute('aria-label', 'Volume');
  vol.onchange = () => ((s = { ...s, audio: { ...s.audio, volume: Number(vol.value) / 100 } }), commit());
  const audioRow = document.createElement('div');
  audioRow.className = 'row';
  const muteLabel = document.createElement('label');
  muteLabel.append(mute, ' Mute');
  audioRow.append('Sound: ', muteLabel, vol);

  const motion = document.createElement('input');
  motion.type = 'checkbox';
  motion.id = 'set-motion';
  motion.checked = s.reduce_motion;
  motion.onchange = () => ((s = { ...s, reduce_motion: motion.checked }), commit());
  const motionRow = document.createElement('label');
  motionRow.className = 'check';
  motionRow.append(motion, ' Reduce motion (text appears at once, scene changes cut). Nothing in the game flashes.');

  const table = document.createElement('table');
  table.id = 'set-keys';
  const msg = document.createElement('p');
  msg.id = 'set-keys-msg';
  msg.setAttribute('role', 'status');
  let waiting: KeyAction | null = null;
  const drawKeys = () => {
    table.replaceChildren();
    for (const a of KEY_ACTIONS) {
      const tr = table.insertRow();
      tr.dataset.action = a;
      tr.insertCell().textContent = ACTION_LABELS[a];
      const keys = tr.insertCell();
      for (const k of s.keys[a] ?? []) {
        const kbd = document.createElement('kbd');
        kbd.textContent = keyName(k);
        keys.append(kbd, ' ');
      }
      const b = button(waiting === a ? 'press a key…' : 'set key', () => {
        waiting = a;
        msg.className = '';
        msg.textContent = `Press the new key for "${ACTION_LABELS[a]}" (Esc keeps the old one).`;
        drawKeys();
      });
      b.dataset.action = a;
      tr.insertCell().append(b);
    }
  };
  // Capture phase on window: the key that is being bound must not also act in the game.
  const onKey = (e: KeyboardEvent) => {
    if (!waiting || !el.isConnected) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const a = waiting;
    waiting = null;
    if (e.code === 'Escape' && a !== 'cancel') {
      msg.textContent = 'Kept the old key.';
    } else {
      const r = rebind(s.keys, a, e.code);
      if ('error' in r) {
        msg.className = 'error';
        msg.textContent = r.error;
      } else {
        s = { ...s, keys: r.keys };
        msg.className = '';
        msg.textContent = `"${ACTION_LABELS[a]}" is now ${keyName(e.code)}.`;
        commit();
      }
    }
    drawKeys();
  };
  window.addEventListener('keydown', onKey, true);
  drawKeys();
  const reset = button('Reset keys', () => {
    s = { ...s, keys: structuredClone(DEFAULT_KEYS) };
    msg.textContent = 'Keys reset.';
    drawKeys();
    commit();
  }, 'set-keys-reset');
  const done = button('◀ Back to the menu', () => {
    window.removeEventListener('keydown', onKey, true);
    back();
  }, 'settings-back');
  el.append(hintRow, audioRow, motionRow, para('Keys:'), table, msg, reset, ' ', done);
  host.append(el);
  hint.focus();
  return el;
}

/** Whether a settings page is waiting for a key (the shell then leaves keys alone). */
export function capturingKey(): boolean {
  return !!document.querySelector('#set-keys button[data-action]') && [...document.querySelectorAll('#set-keys button')].some((b) => b.textContent === 'press a key…');
}
