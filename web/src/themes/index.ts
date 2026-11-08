// Theme registry and theme choice. Every theme the app can show is registered here; each lives in its own file so
// its artist touches only that file. Which theme a level shows (first match wins):
//
//   1. ?theme=<id> in the URL (a valid id), on every level;
//   2. world level: the viewer's global choice;
//      site, room, rack and device levels: the viewer's choice for that site, else the site's `iso_theme`
//      (NetBox custom field), else the viewer's global choice;
//   3. DEFAULT_THEME.
//
// The viewer's choices persist per browser in localStorage (one JSON record); every storage access is guarded,
// because storage can be missing or throw (private windows, blocked site data), and then choices last for the page.
import type { ZoomLevel } from '../contracts/iso';
import type { Site } from '../contracts/scene.gen';
import { THEME_IDS, type SlotId, type SlotVariant, type Theme, type ThemeId } from '../contracts/theme';
import { businessTheme } from './business';
import { debugTheme } from './debug';
import { farmTheme } from './farm';
import { industrialTheme } from './industrial';
import { pagodaTheme } from './pagoda';

export const THEMES: Readonly<Record<ThemeId, Theme>> = {
  debug: debugTheme,
  'industrial-robotic': industrialTheme,
  farm: farmTheme,
  business: businessTheme,
  'pagodas-nature': pagodaTheme,
};

/** Registry order = the contract's ThemeId order (the picker lists them so). */
export const THEME_LIST: readonly Theme[] = THEME_IDS.map((id) => THEMES[id]);

export const DEFAULT_THEME: ThemeId = 'debug';

export function isThemeId(v: unknown): v is ThemeId {
  return typeof v === 'string' && (THEME_IDS as readonly string[]).includes(v);
}

export function getTheme(id: ThemeId): Theme {
  return THEMES[id];
}

// ------------------------------------------------------------------------------------------------ resolution

/** The viewer's own choices: a global theme (world level, and sites without one) and per-site overrides. */
export interface ThemePrefs {
  readonly global: ThemeId | null;
  readonly sites: Readonly<Record<string, ThemeId>>;
}

export const EMPTY_PREFS: ThemePrefs = { global: null, sites: {} };

export type ThemeSource = 'url' | 'viewer-site' | 'site' | 'viewer' | 'default';

export interface ThemeQuery {
  readonly level: ZoomLevel;
  /** The site the level belongs to; null at world level. */
  readonly site: Pick<Site, 'id' | 'iso_theme'> | null;
}

export function resolveTheme(q: ThemeQuery, prefs: ThemePrefs, url: ThemeId | null): { id: ThemeId; source: ThemeSource } {
  if (url) return { id: url, source: 'url' };
  if (q.level !== 'world' && q.site) {
    const mine = prefs.sites[String(q.site.id)];
    if (mine) return { id: mine, source: 'viewer-site' };
    if (q.site.iso_theme && isThemeId(q.site.iso_theme)) return { id: q.site.iso_theme, source: 'site' };
  }
  if (prefs.global) return { id: prefs.global, source: 'viewer' };
  return { id: DEFAULT_THEME, source: 'default' };
}

/** ?theme=<id> from a query string; unknown ids are ignored. */
export function urlTheme(search: string): ThemeId | null {
  const v = new URLSearchParams(search).get('theme');
  return isThemeId(v) ? v : null;
}

// ------------------------------------------------------------------------------------------------ persistence

export const PREFS_KEY = 'rackwalker.theme.v1';

export type PrefsStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** window.localStorage, or null when it is missing or its getter throws. */
export function browserStorage(): PrefsStorage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Reads the viewer's choices; anything missing, unreadable or invalid reads as no choice. */
export function loadPrefs(storage: PrefsStorage | null): ThemePrefs {
  let raw: string | null = null;
  try {
    raw = storage?.getItem(PREFS_KEY) ?? null;
  } catch {
    return EMPTY_PREFS;
  }
  if (!raw) return EMPTY_PREFS;
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== 'object') return EMPTY_PREFS;
    const o = v as { global?: unknown; sites?: unknown };
    const sites: Record<string, ThemeId> = {};
    if (o.sites && typeof o.sites === 'object')
      for (const [k, id] of Object.entries(o.sites as Record<string, unknown>)) if (/^\d+$/.test(k) && isThemeId(id)) sites[k] = id;
    return { global: isThemeId(o.global) ? o.global : null, sites };
  } catch {
    return EMPTY_PREFS;
  }
}

/** Stores the viewer's choices; returns false when storage is unavailable (the choice then lasts for the page). */
export function savePrefs(storage: PrefsStorage | null, prefs: ThemePrefs): boolean {
  try {
    if (!storage) return false;
    storage.setItem(PREFS_KEY, JSON.stringify(prefs));
    return true;
  } catch {
    return false;
  }
}

/**
 * The live theme choice of one page: URL override + the viewer's stored choices. `choose` records a pick made in the
 * picker for the level being shown (world = global choice, site and below = that site); null clears it. A pick
 * replaces a ?theme= override for the rest of the page (the picker would otherwise seem to do nothing).
 */
export class ThemeChoice {
  private prefsNow: ThemePrefs;
  constructor(
    private url: ThemeId | null,
    private readonly storage: PrefsStorage | null,
  ) {
    this.prefsNow = loadPrefs(storage);
  }
  get prefs(): ThemePrefs {
    return this.prefsNow;
  }
  get urlOverride(): ThemeId | null {
    return this.url;
  }
  resolve(q: ThemeQuery): { id: ThemeId; source: ThemeSource } {
    return resolveTheme(q, this.prefsNow, this.url);
  }
  theme(q: ThemeQuery): Theme {
    return THEMES[this.resolve(q).id];
  }
  choose(q: ThemeQuery, id: ThemeId | null): void {
    this.url = null;
    const p = this.prefsNow;
    if (q.level === 'world' || !q.site) this.prefsNow = { global: id, sites: p.sites };
    else {
      const sites = { ...p.sites };
      if (id) sites[String(q.site.id)] = id;
      else delete sites[String(q.site.id)];
      this.prefsNow = { global: p.global, sites };
    }
    savePrefs(this.storage, this.prefsNow);
  }
}

// ------------------------------------------------------------------------------------------------ sample variants

/** Sample NetBox colours for the variant matrix (role and cable colours come from NetBox, not the theme). */
export const SAMPLE_ROLE_COLOR = '#2e8b6e';
export const SAMPLE_WIRE_COLOR = '#c4622d';

/**
 * The variants every slot is checked and shown with (conformance test, slot sheet): no variant, each rotation, a few
 * seeds, every status the engine passes, and the NetBox colour inputs.
 */
export function sampleVariants(slot: SlotId): SlotVariant[] {
  const out: SlotVariant[] = [{}];
  for (const rotation of [0, 1, 2, 3] as const) out.push({ rotation });
  for (const seed of [1, 2, 3, 7, 12345]) out.push({ seed });
  const statuses = slot === 'port' ? ['connected', 'free', 'disabled', 'mgmt'] : ['active', 'planned', 'offline', 'failed'];
  for (const status of statuses) out.push({ status });
  out.push({ role_color: SAMPLE_ROLE_COLOR, status: 'active' });
  out.push({ wire_color: SAMPLE_WIRE_COLOR, status: slot === 'port' ? 'connected' : 'active' });
  return out;
}
