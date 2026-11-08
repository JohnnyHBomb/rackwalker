// World of Confusion: RackWalker web entry. Query parameters:
//   source=http (default) | fixture      where scenes come from (http = the proxy's /api; fixture = canned data)
//   view=world | site:1 | room:1:1 | rack:1 | device:2     the level to open (default world); every navigation
//                                        writes it back (history entries), so reload and Back/Forward follow
//   anim=0                               no transition animation (golden screenshots)
//   perf=<n>                             synthetic site with n devices (performance check)
//   theme=<id>                           theme override on every level (see themes/index.ts for the normal choice)
//   game=0                               the plain viewer even without view=
// The game (sign-in, overworld, dialogue, autosave: src/game/) runs when the page reads the live proxy and has no
// view= deep link: it keeps the player's place in the proxy's game store, so the URL stays as it is. A view= link,
// fixture data or perf= open the plain viewer, as before.
// Editing (drag and drop, change basket, field edits) talks to the proxy's /api/changes and /api/health, so it is on
// only with source=http: on canned fixture data (or perf=) the editor is read-only and says so, because a fixture id
// sent to a proxy names a live NetBox object (previously: a fixture-view cable edit wrote NetBox cable 6).
import type { ZoomLevel } from './contracts/iso';
import { HttpChangeApi } from './edit/api';
import { Editor } from './edit/editor';
import { entityKey } from './engine/entity';
import { fixtureSource } from './engine/fixtures';
import { WORLD, parseView } from './engine/nav';
import { HttpGameApi } from './game/api';
import { GameShell } from './game/shell';
import { HttpSceneSource, type SceneSource } from './engine/source';
import { SyntheticSceneSource } from './engine/synthetic';
import { RackWalkerView } from './engine/view';
import { ThemeChoice, browserStorage, urlTheme } from './themes/index';
import { NavUrl } from './ui/nav-url';
import { SearchBox } from './ui/search';
import { ThemePicker } from './ui/theme-picker';

declare global {
  interface Window {
    __game?: { shell: GameShell; ready: Promise<void> };
    __iso?: {
      view: RackWalkerView;
      editor: Editor;
      ready: Promise<void>;
      choice: ThemeChoice;
      state(): { level: ZoomLevel | null; crumbs: string[]; selected: string | null; scale: number | null; error: string | null; theme: string };
      screenOf(key: string): { x: number; y: number } | null;
      fps(ms: number): Promise<{ fps: number; frames: number; ms: number; worstMs: number }>;
    };
  }
}

async function main() {
  const q = new URLSearchParams(location.search);
  const perf = Number(q.get('perf') ?? 0);
  const kind = perf > 0 ? 'synthetic' : q.get('source') === 'fixture' ? 'fixture' : 'http';
  const source: SceneSource = kind === 'synthetic' ? new SyntheticSceneSource(perf) : kind === 'fixture' ? await fixtureSource() : new HttpSceneSource('');
  const choice = new ThemeChoice(urlTheme(location.search), browserStorage());
  const readOnly = {
    http: null,
    fixture: 'Canned fixture data, not your NetBox: editing is off (open the page without source=fixture for the live NetBox).',
    synthetic: 'Synthetic perf data, not your NetBox: editing is off.',
  }[kind];
  const editor = new Editor(document.body, new HttpChangeApi(''), readOnly);
  const view = new RackWalkerView(document.body, source, (tq) => choice.theme(tq), { animate: q.get('anim') !== '0', wrap: editor.wrap });
  await view.init();
  const picker = new ThemePicker(view.hudSlot, choice, view);
  const search = new SearchBox(view.hudSlot, view);
  const game = kind === 'http' && !q.has('view') && q.get('game') !== '0' ? new GameShell(document.body, view, new HttpGameApi('')) : null;
  const url = game ? null : new NavUrl(view);
  editor.attach(view);
  view.onUpdate = () => {
    picker.update();
    search.restyle();
    url?.sync(); // the level on screen is in the URL: reload and Back/Forward work (the game keeps it in its save)
    editor.syncTheme(); // the panels follow the theme on screen
  };
  document.getElementById('status')?.remove();
  const ready = game ? game.start() : view.goto(parseView(q.get('view')) ?? WORLD);
  if (game) window.__game = { shell: game, ready };
  window.__iso = {
    view,
    editor,
    ready,
    choice,
    state: () => ({
      level: view.level,
      crumbs: view.crumbs.map((c) => c.name),
      selected: view.selection ? entityKey(view.selection) : null,
      scale: view.camera?.scale ?? null,
      error: view.lastError,
      theme: view.theme.id,
    }),
    screenOf: (k) => view.screenOf(k),
    // Frames rendered in `ms` while the camera pans one pixel per frame (so every frame really re-renders).
    fps: (ms) =>
      new Promise((resolve) => {
        let frames = 0;
        let worstMs = 0;
        const t0 = performance.now();
        let last = t0;
        const tick = () => {
          const now = performance.now();
          worstMs = Math.max(worstMs, now - last);
          last = now;
          const cam = view.camera;
          if (cam) view.setCamera({ ...cam, cx: cam.cx + (frames % 2 ? 1 : -1) });
          frames++;
          const dt = now - t0;
          if (dt >= ms) {
            view.app.ticker.remove(tick);
            resolve({ fps: (frames * 1000) / dt, frames, ms: dt, worstMs });
          }
        };
        view.app.ticker.add(tick);
      }),
  };
  await ready;
}

// Whatever stops the page from starting is said on the page, never only in the console (#status is gone by then).
main().catch((e) => {
  console.error(e);
  const s = document.getElementById('status') ?? document.body.appendChild(document.createElement('p'));
  s.id = 'status';
  s.className = 'game-ui';
  s.setAttribute('role', 'alert');
  s.style.cssText = 'position:fixed;left:12px;right:12px;top:12px;z-index:50;margin:0;padding:10px 12px;background:#f6efd9;color:#8a1c12;border:4px solid #8a1c12;font:15px/1.4 ui-monospace,monospace';
  s.textContent = `RackWalker could not start: ${e instanceof Error ? e.message : String(e)} Reload the page to try again.`;
});
