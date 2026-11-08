// DOM helpers for the editor panels. Every panel is framed with the theme's ui_frame slot (9-slice, 8 px corners)
// as a CSS border-image; colours come from the theme palette. No art of its own.
import { renderSlot, type PixelBuffer, type Theme } from '../contracts/theme';
import { bestOn, contrast, readableOn } from '../engine/colour';

export { contrast } from '../engine/colour';

/** A theme pixel buffer as a PNG data URL (panel frames, tray thumbnails). */
export function bufferToDataUrl(b: PixelBuffer): string {
  const c = document.createElement('canvas');
  c.width = b.width;
  c.height = b.height;
  c.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(b.data), b.width, b.height), 0, 0);
  return c.toDataURL();
}

/** The panel fill: the centre pixel of the theme's ui_frame slot, as #rrggbb. */
export function panelFill(theme: Theme): string {
  const b = renderSlot(theme, 'ui_frame');
  const i = ((b.height >> 1) * b.width + (b.width >> 1)) * 4;
  return '#' + [0, 1, 2].map((k) => (b.data[i + k] ?? 0).toString(16).padStart(2, '0')).join('');
}

/** Secondary panel text (field names, diffs, hints): the first of metal, wall_shade that reads on the panel fill
 *  (contrast at least 4.5), else the text colour. The contract does not promise metal is light (business: dark cabinets). */
export function mutedText(theme: Theme): string {
  const p = theme.palette;
  const fill = panelFill(theme);
  return [p.metal, p.wall_shade].find((c) => contrast(c, fill) >= 4.5) ?? p.text;
}

/** Edges of inputs and drop chips: the first of metal_shade, metal, wall_shade, wall that stands out from the panel
 *  fill (contrast at least 3, WCAG non-text), else the text colour (previously: business inputs had a 1.02 edge). */
export function edgeColour(theme: Theme): string {
  const p = theme.palette;
  const fill = panelFill(theme);
  return [p.metal_shade, p.metal, p.wall_shade, p.wall].find((c) => contrast(c, fill) >= 3) ?? p.text;
}

export type Tone = 'ok' | 'warn' | 'err';

/** Status text (op status, errors, basket messages) in the theme's LED colour, nudged towards the text colour until
 *  it reads on the panel fill (previously: led_err on the panel was 3.8 to 4.2). */
export function toneColour(theme: Theme, tone: Tone): string {
  const p = theme.palette;
  return readableOn({ ok: p.led_ok, warn: p.led_warn, err: p.led_err }[tone], panelFill(theme), p.text);
}

/** Text on a badge filled with `bg` (level and kind badges on the accent colour). */
export function badgeText(theme: Theme, bg: string): string {
  const p = theme.palette;
  return bestOn(bg, [p.outline, p.text, p.background, p.highlight]);
}

export function frameCss(theme: Theme): string {
  const p = theme.palette;
  return `border:8px solid ${p.highlight};border-image:url(${bufferToDataUrl(renderSlot(theme, 'ui_frame'))}) 8 fill / 8px / 0 stretch;image-rendering:pixelated;
    color:${p.text};font:12px/1.4 ui-monospace,monospace;padding:2px 4px;box-sizing:border-box`;
}

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, ...kids: (Node | string | null)[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'style') e.style.cssText = v;
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null) e.append(k);
  return e;
}

export function button(theme: Theme, label: string, title: string, onClick: () => void, attrs: Record<string, string> = {}): HTMLButtonElement {
  const p = theme.palette;
  const b = el('button', {
    title,
    style: `font:inherit;color:${p.text};background:${p.background};border:2px solid ${p.highlight};padding:0 6px;cursor:pointer;margin:2px 4px 2px 0`,
    ...attrs,
  });
  b.textContent = label;
  b.onclick = (e) => {
    e.stopPropagation();
    onClick();
  };
  return b;
}

/**
 * Turns a button on or off; off, it looks off (dashed muted border, muted text, not-allowed cursor) and its tooltip
 * and aria-description say why (previously: a disabled Apply looked like Discard all and explained nothing).
 */
export function setEnabled(theme: Theme, b: HTMLButtonElement, why: string | null): void {
  b.disabled = why !== null;
  b.dataset.why = why ?? '';
  if (why === null) return;
  const muted = mutedText(theme);
  b.title = why;
  b.setAttribute('aria-description', why);
  b.style.borderStyle = 'dashed';
  b.style.borderColor = muted;
  b.style.color = muted;
  b.style.cursor = 'not-allowed';
}
