import { describe, expect, it } from 'vitest';
import { contrast, mix, readableOn } from '../src/engine/colour';
import { badgeText, edgeColour, mutedText, panelFill, toneColour } from '../src/edit/ui';
import { THEME_LIST } from '../src/themes/index';

// Editor panels draw on each theme's ui_frame fill; every text colour they use must read there (WCAG 4.5), and the
// edges of inputs and drop chips must stand out from it (WCAG non-text 3).
describe('editor panel text reads under every theme', () => {
  for (const t of THEME_LIST) {
    it(`${t.id}: text, muted text, links and status tones reach contrast 4.5 on the panel fill`, () => {
      const fill = panelFill(t);
      expect(contrast(t.palette.text, fill)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(mutedText(t), fill)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.palette.accent_alt, fill)).toBeGreaterThanOrEqual(4.5);
      for (const tone of ['ok', 'warn', 'err'] as const) expect(contrast(toneColour(t, tone), fill), tone).toBeGreaterThanOrEqual(4.5);
    });
    it(`${t.id}: input and chip edges reach contrast 3 on the panel fill`, () => {
      expect(contrast(edgeColour(t), panelFill(t))).toBeGreaterThanOrEqual(3);
    });
    it(`${t.id}: badge text on the accent fill (HUD level, panel kind) reaches 4.5`, () => {
      expect(contrast(badgeText(t, t.palette.accent), t.palette.accent)).toBeGreaterThanOrEqual(4.5);
    });
    it(`${t.id}: HUD error text reads on the page background`, () => {
      const p = t.palette;
      expect(contrast(readableOn(p.led_err, p.background, p.text), p.background)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it('business keeps its dark cabinet metal off the panels (regression: 1.28 contrast before)', () => {
    const b = THEME_LIST.find((t) => t.id === 'business')!;
    expect(mutedText(b)).not.toBe(b.palette.metal);
  });

  it('the measured numbers: the raw palette colours were below the bar, the UI colours are not', () => {
    const by = (id: string) => THEME_LIST.find((t) => t.id === id)!;
    // led_err straight on the panel fill (before): under 4.5 in debug, industrial, farm, business.
    for (const id of ['debug', 'industrial-robotic', 'farm', 'business']) expect(contrast(by(id).palette.led_err, panelFill(by(id))), id).toBeLessThan(4.5);
    // outline on accent (before): farm 2.85.
    expect(contrast(by('farm').palette.outline, by('farm').palette.accent)).toBeLessThan(3);
    // metal_shade edge on the business panel (before): about 1.0.
    expect(contrast(by('business').palette.metal_shade, panelFill(by('business')))).toBeLessThan(1.1);
  });

  it('readableOn keeps the colour when it already reads, else steps towards the fallback', () => {
    expect(readableOn('#ffffff', '#000000', '#888888')).toBe('#ffffff');
    const c = readableOn('#802020', '#000000', '#ffffff');
    expect(contrast(c, '#000000')).toBeGreaterThanOrEqual(4.5);
    expect(c).not.toBe('#ffffff');
    expect(mix('#000000', '#ffffff', 0.5)).toBe('#808080');
  });
});
