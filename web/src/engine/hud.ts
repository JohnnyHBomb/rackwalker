// Minimal HUD (DOM): back control, breadcrumb, current level, selected entity, zoom buttons. Colours come from the
// theme palette (restyled when the theme changes); no art of its own. `slot` hosts extra controls (theme picker).
import type { ZoomLevel } from '../contracts/iso';
import type { Palette } from '../contracts/theme';
import { bestOn, readableOn } from './colour';
import type { Crumb } from './nav';

export interface HudState {
  readonly level: ZoomLevel;
  readonly crumbs: readonly Crumb[];
  readonly selected: string | null;
  readonly scale: number;
  readonly error: string | null;
  readonly busy: boolean;
}

export interface HudActions {
  back(): void;
  goto(c: Crumb): void;
  zoom(dir: 1 | -1): void;
  enterSelected(): void;
}

export class Hud {
  readonly el: HTMLElement;
  private readonly crumbsEl: HTMLElement;
  private readonly levelEl: HTMLElement;
  private readonly selEl: HTMLElement;
  private readonly errEl: HTMLElement;
  private readonly backBtn: HTMLButtonElement;
  private readonly enterBtn: HTMLButtonElement;
  private readonly scaleEl: HTMLElement;
  private readonly buttons: HTMLButtonElement[] = [];
  /** Container for extra HUD controls (the theme picker); styled by its owner. */
  readonly slot: HTMLElement;

  constructor(host: HTMLElement, palette: Palette, private readonly actions: HudActions) {
    const el = document.createElement('div');
    el.id = 'hud';
    const btn = (label: string, title: string, id: string) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.title = title;
      b.id = id;
      this.buttons.push(b);
      return b;
    };
    this.backBtn = btn('◀ back', 'Zoom out one level (Esc)', 'hud-back');
    this.backBtn.onclick = () => actions.back();
    this.levelEl = document.createElement('span');
    this.levelEl.id = 'hud-level';
    this.crumbsEl = document.createElement('nav');
    this.crumbsEl.id = 'hud-crumbs';
    this.crumbsEl.style.pointerEvents = 'auto';
    this.selEl = document.createElement('span');
    this.selEl.id = 'hud-selected';
    this.enterBtn = btn('enter ▶', 'Zoom into the selected object (Enter or double-click)', 'hud-enter');
    this.enterBtn.onclick = () => actions.enterSelected();
    const zoomIn = btn('+', 'Zoom in (wheel, pinch, +)', 'hud-zoom-in');
    zoomIn.onclick = () => actions.zoom(1);
    const zoomOut = btn('−', 'Zoom out (wheel, pinch, -)', 'hud-zoom-out');
    zoomOut.onclick = () => actions.zoom(-1);
    this.scaleEl = document.createElement('span');
    this.scaleEl.id = 'hud-scale';
    this.errEl = document.createElement('div');
    this.errEl.id = 'hud-error';
    this.slot = document.createElement('span');
    this.slot.id = 'hud-slot';
    this.slot.style.pointerEvents = 'auto';
    el.append(this.backBtn, this.levelEl, this.crumbsEl, this.selEl, this.enterBtn, zoomOut, this.scaleEl, zoomIn, this.slot, this.errEl);
    host.appendChild(el);
    this.el = el;
    this.setPalette(palette);
  }

  /** Recolours the HUD for a theme. */
  setPalette(palette: Palette): void {
    this.el.style.cssText = `position:fixed;left:8px;top:8px;right:8px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;
      font:13px/1.4 ui-monospace,monospace;color:${palette.text};pointer-events:none`;
    for (const b of this.buttons)
      b.style.cssText = `pointer-events:auto;font:inherit;color:${palette.text};background:${palette.background};
        border:2px solid ${palette.highlight};padding:2px 8px;cursor:pointer;image-rendering:pixelated;${b === this.enterBtn ? `display:${this.enterBtn.style.display}` : ''}`;
    // Badge text and error text in the colour that reads on their fill (previously: outline on accent was 2.85 in farm).
    const badge = bestOn(palette.accent, [palette.outline, palette.text, palette.background, palette.highlight]);
    this.levelEl.style.cssText = `background:${palette.accent};color:${badge};padding:2px 6px;text-transform:uppercase`;
    this.errEl.style.cssText = `flex-basis:100%;color:${readableOn(palette.led_err, palette.background, palette.text)}`;
  }

  update(s: HudState): void {
    this.levelEl.textContent = s.level;
    this.backBtn.disabled = s.crumbs.length <= 1;
    this.crumbsEl.replaceChildren();
    s.crumbs.forEach((c, i) => {
      if (i) this.crumbsEl.append(' › ');
      const last = i === s.crumbs.length - 1;
      const a = document.createElement(last ? 'strong' : 'a');
      a.textContent = c.name;
      if (!last) {
        (a as HTMLAnchorElement).href = '#';
        a.style.color = 'inherit';
        a.onclick = (ev) => {
          ev.preventDefault();
          this.actions.goto(c);
        };
      }
      this.crumbsEl.append(a);
    });
    this.selEl.textContent = s.selected ? `selected: ${s.selected}` : '';
    this.enterBtn.style.display = s.selected ? '' : 'none';
    this.scaleEl.textContent = `${s.scale}x`;
    this.errEl.textContent = s.error ?? '';
    this.el.dataset.busy = s.busy ? '1' : '0';
  }
}
