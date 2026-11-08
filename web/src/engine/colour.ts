// Colour arithmetic for DOM text on theme colours (HUD, editor panels): WCAG contrast and readable variants of a
// palette colour. The art itself never uses these; only text and UI edges do.

function luminance(hex: string): number {
  const ch = (i: number) => {
    const x = parseInt(hex.slice(i, i + 2), 16) / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5);
}

/** WCAG contrast ratio of two #rrggbb colours (1..21). */
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** `a` moved a fraction `t` (0..1) towards `b`, as #rrggbb. */
export function mix(a: string, b: string, t: number): string {
  const c = (i: number) => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - t) + parseInt(b.slice(i, i + 2), 16) * t);
  return '#' + [1, 3, 5].map((i) => c(i).toString(16).padStart(2, '0')).join('');
}

/**
 * `colour`, or the nearest step of it towards `toward` (a colour that reads on `bg`), that reaches contrast `min`
 * on `bg`: an error red stays red-ish but readable. Ends at `toward` itself in the worst case.
 */
export function readableOn(colour: string, bg: string, toward: string, min = 4.5): string {
  for (let i = 0; i <= 20; i++) {
    const c = mix(colour, toward, i / 20);
    if (contrast(c, bg) >= min) return c;
  }
  return toward;
}

/** The candidate with the highest contrast on `bg` (badge text on an accent fill). */
export function bestOn(bg: string, candidates: readonly string[]): string {
  return candidates.reduce((best, c) => (contrast(c, bg) > contrast(best, bg) ? c : best));
}
