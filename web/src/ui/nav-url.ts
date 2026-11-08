// The level on screen lives in the URL (?view=...): every navigation pushes a history entry, so a reload comes back
// to the same level and the browser's Back and Forward step through the levels (previously: the URL never changed,
// a reload went back to World and Back left the app). Other query parameters (source, theme, anim) are kept.
import { WORLD, formatView, parseView, type Target } from '../engine/nav';

export interface UrlView {
  readonly crumbs: readonly { readonly target: Target }[];
  goto(t: Target, direction?: 'in' | 'out' | 'none'): Promise<void>;
}

export class NavUrl {
  /** formatView of the level the URL names (what the last push or pop wrote). */
  private shown: string | null;
  /** A Back/Forward navigation still on its way. */
  private pending: object | null = null;

  constructor(
    private readonly view: UrlView,
    private readonly win: Pick<Window, 'location' | 'history' | 'addEventListener'> = window,
  ) {
    this.shown = new URL(win.location.href).searchParams.get('view');
    win.addEventListener('popstate', () => {
      const t = parseView(new URL(this.win.location.href).searchParams.get('view')) ?? WORLD;
      this.shown = formatView(t);
      if (this.current() === this.shown) return;
      // Until the view arrives, the level on screen is the one being left: writing it back would push a new entry
      // and cut off the Forward history.
      // (Set before goto: goto refreshes the HUD, and so calls sync(), before it returns.)
      const token = {};
      this.pending = token;
      void this.view
        .goto(t, 'none')
        .catch(() => undefined)
        .then(() => {
          if (this.pending === token) this.pending = null;
        });
    });
  }

  private current(): string | null {
    const c = this.view.crumbs;
    return c.length ? formatView(c[c.length - 1]!.target) : null;
  }

  /** After every level change: a new history entry when the level differs from the one the URL names. */
  sync(): void {
    const v = this.current();
    if (this.pending || v === null || v === this.shown) return;
    const u = new URL(this.win.location.href);
    const first = !u.searchParams.has('view') && this.shown === null; // the opening level: no extra entry
    u.searchParams.set('view', v);
    this.shown = v;
    try {
      if (first) this.win.history.replaceState(this.win.history.state, '', u);
      else this.win.history.pushState(null, '', u);
    } catch {
      /* no history API: navigation still works, only the URL does not follow */
    }
  }
}
