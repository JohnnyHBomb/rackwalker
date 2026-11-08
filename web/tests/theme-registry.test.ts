import { describe, expect, it } from 'vitest';
import {
  DEFAULT_THEME,
  EMPTY_PREFS,
  PREFS_KEY,
  ThemeChoice,
  browserStorage,
  isThemeId,
  loadPrefs,
  resolveTheme,
  savePrefs,
  urlTheme,
  type PrefsStorage,
  type ThemePrefs,
} from '../src/themes/index';

function memStorage(init: Record<string, string> = {}): PrefsStorage & { data: Record<string, string> } {
  const data = { ...init };
  return { data, getItem: (k) => data[k] ?? null, setItem: (k, v) => void (data[k] = v) };
}
const throwing: PrefsStorage = {
  getItem: () => {
    throw new Error('SecurityError');
  },
  setItem: () => {
    throw new Error('QuotaExceededError');
  },
};

const farmSite = { id: 1, iso_theme: 'farm' as const };
const plainSite = { id: 2, iso_theme: null };

describe('theme resolution', () => {
  const prefs: ThemePrefs = { global: 'business', sites: { '1': 'pagodas-nature' } };
  it('world level: URL override, else the viewer global choice, else the default', () => {
    expect(resolveTheme({ level: 'world', site: null }, EMPTY_PREFS, null)).toEqual({ id: DEFAULT_THEME, source: 'default' });
    expect(resolveTheme({ level: 'world', site: null }, prefs, null)).toEqual({ id: 'business', source: 'viewer' });
    expect(resolveTheme({ level: 'world', site: null }, prefs, 'farm')).toEqual({ id: 'farm', source: 'url' });
  });
  it("site and below: URL, then the viewer's pick for that site, then the site's iso_theme, then the global choice", () => {
    for (const level of ['site', 'room', 'rack', 'device'] as const) {
      expect(resolveTheme({ level, site: farmSite }, EMPTY_PREFS, null)).toEqual({ id: 'farm', source: 'site' });
      expect(resolveTheme({ level, site: farmSite }, prefs, null)).toEqual({ id: 'pagodas-nature', source: 'viewer-site' });
      expect(resolveTheme({ level, site: farmSite }, prefs, 'debug')).toEqual({ id: 'debug', source: 'url' });
      expect(resolveTheme({ level, site: plainSite }, prefs, null)).toEqual({ id: 'business', source: 'viewer' });
      expect(resolveTheme({ level, site: plainSite }, EMPTY_PREFS, null)).toEqual({ id: 'debug', source: 'default' });
    }
  });
  it('a site theme outside the registry falls through', () => {
    expect(resolveTheme({ level: 'site', site: { id: 3, iso_theme: 'nope' as never } }, EMPTY_PREFS, null).id).toBe(DEFAULT_THEME);
  });
  it('?theme= accepts registered ids only', () => {
    expect(urlTheme('?theme=farm&view=world')).toBe('farm');
    expect(urlTheme('?theme=pagodas-nature')).toBe('pagodas-nature');
    expect(urlTheme('?theme=nes')).toBeNull();
    expect(urlTheme('')).toBeNull();
    expect(isThemeId('business')).toBe(true);
    expect(isThemeId(3)).toBe(false);
  });
});

describe('viewer preferences in storage', () => {
  it('round-trips and drops invalid entries', () => {
    const s = memStorage();
    expect(savePrefs(s, { global: 'farm', sites: { '4': 'business' } })).toBe(true);
    expect(loadPrefs(s)).toEqual({ global: 'farm', sites: { '4': 'business' } });
    s.data[PREFS_KEY] = JSON.stringify({ global: 'gameboy', sites: { '1': 'farm', x: 'farm', '2': 'nope' } });
    expect(loadPrefs(s)).toEqual({ global: null, sites: { '1': 'farm' } });
  });
  it('missing, unparsable, wrongly shaped or throwing storage reads as no choice and never throws', () => {
    expect(loadPrefs(null)).toEqual(EMPTY_PREFS);
    expect(loadPrefs(memStorage())).toEqual(EMPTY_PREFS);
    expect(loadPrefs(memStorage({ [PREFS_KEY]: '{oops' }))).toEqual(EMPTY_PREFS);
    expect(loadPrefs(memStorage({ [PREFS_KEY]: '[1,2]' }))).toEqual({ global: null, sites: {} });
    expect(loadPrefs(memStorage({ [PREFS_KEY]: 'null' }))).toEqual(EMPTY_PREFS);
    expect(loadPrefs(throwing)).toEqual(EMPTY_PREFS);
    expect(savePrefs(throwing, EMPTY_PREFS)).toBe(false);
    expect(savePrefs(null, EMPTY_PREFS)).toBe(false);
  });
  it('browserStorage returns null when localStorage is missing or its getter throws', () => {
    const g = globalThis as { localStorage?: unknown };
    const had = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    try {
      Object.defineProperty(globalThis, 'localStorage', { configurable: true, get: () => { throw new Error('denied'); } });
      expect(browserStorage()).toBeNull();
      Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: undefined });
      expect(browserStorage()).toBeNull();
      const m = memStorage();
      Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: m });
      expect(browserStorage()).toBe(m);
    } finally {
      if (had) Object.defineProperty(globalThis, 'localStorage', had);
      else delete g.localStorage;
    }
  });
});

describe('ThemeChoice', () => {
  it('a pick at world level sets the global choice and persists it', () => {
    const s = memStorage();
    const c = new ThemeChoice(null, s);
    c.choose({ level: 'world', site: null }, 'business');
    expect(c.theme({ level: 'world', site: null }).id).toBe('business');
    expect(c.theme({ level: 'room', site: plainSite }).id).toBe('business');
    expect(c.theme({ level: 'room', site: farmSite }).id).toBe('farm');
    expect(new ThemeChoice(null, s).prefs).toEqual({ global: 'business', sites: {} });
  });
  it('a pick at site level and below is for that site only; null goes back to the site default', () => {
    const s = memStorage();
    const c = new ThemeChoice(null, s);
    c.choose({ level: 'rack', site: farmSite }, 'industrial-robotic');
    expect(c.theme({ level: 'device', site: farmSite }).id).toBe('industrial-robotic');
    expect(c.theme({ level: 'world', site: null }).id).toBe('debug');
    expect(c.theme({ level: 'site', site: plainSite }).id).toBe('debug');
    c.choose({ level: 'site', site: farmSite }, null);
    expect(c.theme({ level: 'site', site: farmSite }).id).toBe('farm');
    expect(loadPrefs(s)).toEqual({ global: null, sites: {} });
  });
  it('the URL override wins until the viewer picks; a pick works without storage', () => {
    const c = new ThemeChoice('pagodas-nature', throwing);
    expect(c.theme({ level: 'site', site: farmSite }).id).toBe('pagodas-nature');
    expect(c.urlOverride).toBe('pagodas-nature');
    c.choose({ level: 'site', site: farmSite }, 'business');
    expect(c.urlOverride).toBeNull();
    expect(c.theme({ level: 'site', site: farmSite }).id).toBe('business');
  });
});
