// Theme picker in the HUD: a pixel-styled button showing the current theme, opening a list of every registered
// theme with a palette swatch. At world level a pick is the viewer's global theme; at site level and below it is the
// viewer's theme for that site, and "site default" goes back to the site's own iso_theme. Picks persist per viewer
// (ThemeChoice, localStorage) and replace a ?theme= override.
import type { Palette, Theme, ThemeId } from '../contracts/theme';
import { PALETTE_KEYS } from '../contracts/theme';
import type { ThemeQuery } from '../engine/view';
import { DEFAULT_THEME, THEMES, THEME_LIST, type ThemeChoice } from '../themes/index';

/** What the picker needs from the view. */
export interface ThemedView {
  readonly theme: Theme;
  readonly themeQuery: ThemeQuery | null;
  refreshTheme(): Promise<void>;
}

/** A 16 x 16 px swatch of the theme's 16 palette colours (4 x 4 px each). DOM blocks, not a canvas: the app's
 * canvas stays the only one on the page. */
function swatch(palette: Palette): HTMLElement {
  const el = document.createElement('span');
  el.className = 'theme-swatch';
  el.style.cssText = 'display:inline-grid;grid-template-columns:repeat(4,4px);grid-auto-rows:4px;vertical-align:middle;margin-right:6px';
  for (const k of PALETTE_KEYS) {
    const c = document.createElement('span');
    c.style.background = palette[k];
    el.appendChild(c);
  }
  return el;
}

export class ThemePicker {
  readonly el: HTMLElement;
  private readonly button: HTMLButtonElement;
  private readonly menu: HTMLElement;
  private open = false;
  private drawn = '';

  constructor(
    host: HTMLElement,
    private readonly choice: ThemeChoice,
    private readonly view: ThemedView,
  ) {
    this.el = document.createElement('span');
    this.el.id = 'theme-picker';
    this.el.style.cssText = 'position:relative;display:inline-block';
    this.button = document.createElement('button');
    this.button.id = 'theme-button';
    this.button.setAttribute('aria-haspopup', 'listbox');
    this.button.onclick = () => this.toggle(!this.open);
    this.menu = document.createElement('div');
    this.menu.id = 'theme-menu';
    this.menu.setAttribute('role', 'listbox');
    this.el.addEventListener('keydown', (e) => {
      // Esc closes the menu instead of zooming out; Enter picks the focused option, not the selected entity.
      if (e.key === 'Escape' && this.open) this.toggle(false), this.button.focus();
      if (e.key === 'Escape' || e.key === 'Enter') e.stopPropagation();
    });
    this.el.append(this.button, this.menu);
    host.appendChild(this.el);
    this.update();
  }

  private toggle(open: boolean): void {
    this.open = open;
    this.button.setAttribute('aria-expanded', String(open));
    this.render();
  }

  /** The options for the level on screen: "default" (null) first, then every registered theme. */
  private options(): { id: ThemeId | null; label: string }[] {
    const q = this.view.themeQuery;
    const atSite = !!q && q.level !== 'world' && !!q.site;
    let def: string;
    if (atSite) {
      const own = q!.site!.iso_theme;
      def = own ? `Site default (${THEMES[own].name})` : `Site default (none: ${THEMES[this.choice.prefs.global ?? DEFAULT_THEME].name})`;
    } else def = `App default (${THEMES[DEFAULT_THEME].name})`;
    return [{ id: null, label: def }, ...THEME_LIST.map((t) => ({ id: t.id, label: t.name }))];
  }

  /** The option that is chosen now (what the list marks). */
  private chosen(): ThemeId | null {
    const q = this.view.themeQuery;
    if (this.choice.urlOverride) return this.choice.urlOverride;
    if (q && q.level !== 'world' && q.site) return this.choice.prefs.sites[String(q.site.id)] ?? null;
    return this.choice.prefs.global;
  }

  private pick(id: ThemeId | null): void {
    const q = this.view.themeQuery ?? { level: 'world' as const, site: null };
    this.choice.choose(q, id);
    try {
      const u = new URL(location.href);
      if (u.searchParams.has('theme')) {
        u.searchParams.delete('theme');
        history.replaceState(history.state, '', u);
      }
    } catch {
      /* no history API (tests): the stored choice still applies */
    }
    this.toggle(false);
    void this.view.refreshTheme().catch(() => undefined);
  }

  /**
   * What a pick here changes (previously: the tooltip said a world pick applied "everywhere", but a site's own
   * iso_theme wins over it, and every seeded site has one).
   */
  private hint(): string {
    const q = this.view.themeQuery;
    if (q && q.level !== 'world' && q.site) return `Your theme for ${q.site.name} (this browser): a pick here wins over the site's own theme (iso_theme) and the world pick.`;
    return "Your theme (this browser) for the world map and for sites without their own theme (iso_theme): a site with one keeps it; pick inside that site to change it.";
  }

  /** Re-reads the view (level, theme) and redraws; called on every HUD refresh. */
  update(): void {
    this.render();
  }

  private render(): void {
    const theme = this.view.theme;
    const q = this.view.themeQuery;
    // update() runs on every HUD refresh (each pan frame too): redraw only when something shown changed.
    const key = [theme.id, this.open, this.chosen(), q?.level, q?.site?.id, q?.site?.iso_theme, this.choice.urlOverride, this.choice.prefs.global].join('|');
    if (key === this.drawn) return;
    this.drawn = key;
    const p = theme.palette;
    const pixelBox = `font:inherit;color:${p.text};background:${p.background};border:2px solid ${p.highlight};
      box-shadow:2px 2px 0 ${p.outline};image-rendering:pixelated;cursor:pointer`;
    this.button.style.cssText = `${pixelBox};padding:2px 8px;pointer-events:auto`;
    this.button.replaceChildren(swatch(p), `${theme.name}${this.choice.urlOverride ? ' (URL)' : ''} ${this.open ? '▴' : '▾'}`);
    this.button.dataset.theme = theme.id;
    this.button.title = this.hint();
    this.menu.style.cssText = `${pixelBox};cursor:default;position:absolute;right:0;top:calc(100% + 4px);z-index:10;
      min-width:220px;padding:4px;display:${this.open ? 'block' : 'none'}`;
    if (!this.open) return this.menu.replaceChildren();
    const chosen = this.chosen();
    const note = document.createElement('div');
    note.id = 'theme-note';
    note.style.cssText = 'padding:2px 6px 4px;max-width:260px;white-space:normal';
    note.textContent = this.hint();
    this.menu.replaceChildren(
      note,
      ...this.options().map((o) => {
        const b = document.createElement('button');
        const on = o.id === chosen;
        b.setAttribute('role', 'option');
        b.setAttribute('aria-selected', String(on));
        b.dataset.theme = o.id ?? 'default';
        b.style.cssText = `display:block;width:100%;text-align:left;font:inherit;padding:3px 6px;border:0;cursor:pointer;
          color:${on ? p.outline : p.text};background:${on ? p.accent : 'transparent'}`;
        b.append(o.id ? swatch(THEMES[o.id].palette) : '', `${on ? '► ' : ''}${o.label}`);
        b.onclick = () => this.pick(o.id);
        return b;
      }),
    );
  }
}
