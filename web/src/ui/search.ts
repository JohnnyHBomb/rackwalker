// Find: a box in the HUD that finds sites, rooms, racks and devices by name across every site and opens the one
// picked (previously: there was no way to find a device but clicking through the levels). The index is built on
// each focus from the view's own source (memoised reads, with the change basket on top; an apply clears the memo).
import type { Theme } from '../contracts/theme';
import type { Target } from '../engine/nav';
import type { SceneSource } from '../engine/source';

export interface SearchHit {
  readonly kind: 'site' | 'room' | 'rack' | 'device';
  readonly name: string;
  /** Where it is: "North Depot / Server Room / ND-R3 U20". */
  readonly where: string;
  readonly target: Target;
}

/** Every named object of every site, in (site, room, rack, device) order. */
export async function buildIndex(src: SceneSource): Promise<SearchHit[]> {
  const world = await src.world();
  const out: SearchHit[] = [];
  const sites = await Promise.all(world.sites.map((w) => src.site(w.site.id).catch(() => null)));
  for (const s of sites) {
    if (!s) continue;
    const site = s.site;
    out.push({ kind: 'site', name: site.name, where: 'site', target: { level: 'site', siteId: site.id } });
    const room = new Map(s.locations.map((l) => [l.id, l.name]));
    for (const l of s.locations) out.push({ kind: 'room', name: l.name, where: site.name, target: { level: 'room', siteId: site.id, locationId: l.id } });
    const rackName = new Map(s.racks.map((r) => [r.id, r.name]));
    for (const r of s.racks)
      out.push({ kind: 'rack', name: r.name, where: [site.name, r.location_id !== null ? room.get(r.location_id) : null].filter(Boolean).join(' / '), target: { level: 'rack', rackId: r.id } });
    for (const d of s.devices) {
      if (!d.name) continue;
      const place = d.rack_id !== null ? `${rackName.get(d.rack_id) ?? `rack ${d.rack_id}`}${d.position !== null ? ` U${d.position}` : ''}` : 'unracked';
      const where = [site.name, d.location_id !== null ? room.get(d.location_id) : null, place].filter(Boolean).join(' / ');
      out.push({ kind: 'device', name: d.name, where, target: { level: 'device', deviceId: d.id } });
    }
  }
  return out;
}

/** Hits for a query: case-insensitive substring of the name; exact and prefix matches first, then by name. */
export function search(index: readonly SearchHit[], q: string, limit = 8): SearchHit[] {
  const n = q.trim().toLowerCase();
  if (!n) return [];
  const rank = (h: SearchHit) => {
    const s = h.name.toLowerCase();
    return s === n ? 0 : s.startsWith(n) ? 1 : 2;
  };
  return index
    .filter((h) => h.name.toLowerCase().includes(n))
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name) || a.kind.localeCompare(b.kind))
    .slice(0, limit);
}

export interface SearchView {
  readonly data: SceneSource;
  readonly theme: Theme;
  goto(t: Target, direction?: 'in' | 'out' | 'none'): Promise<void>;
}

export class SearchBox {
  readonly input: HTMLInputElement;
  private readonly list: HTMLOListElement;
  private index: Promise<SearchHit[]> | null = null;
  private hits: SearchHit[] = [];
  private active = 0;

  constructor(
    host: HTMLElement,
    private readonly view: SearchView,
  ) {
    const wrap = document.createElement('span');
    wrap.style.cssText = 'position:relative;pointer-events:auto';
    this.input = document.createElement('input');
    this.input.id = 'search';
    this.input.type = 'search';
    this.input.placeholder = 'find site, room, rack, device (/)';
    this.input.autocomplete = 'off';
    this.input.setAttribute('aria-label', 'Find by name');
    this.list = document.createElement('ol');
    this.list.id = 'search-hits';
    this.list.setAttribute('role', 'listbox');
    this.list.style.display = 'none';
    wrap.append(this.input, this.list);
    host.append(wrap);
    this.input.addEventListener('focus', () => {
      this.index = null;
      void this.load().catch(() => undefined);
    });
    this.input.addEventListener('input', () => void this.update());
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (this.hits.length) this.active = (this.active + (e.key === 'ArrowDown' ? 1 : this.hits.length - 1)) % this.hits.length;
        this.render();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const h = this.hits[this.active];
        if (h) this.open(h);
      } else if (e.key === 'Escape') {
        this.close();
        this.input.blur();
      }
    });
    window.addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement | null;
      if (e.key === '/' && !(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT'))) {
        e.preventDefault();
        this.input.focus();
      }
    });
    this.restyle();
  }

  private load(): Promise<SearchHit[]> {
    this.index ??= buildIndex(this.view.data).catch((e) => {
      this.index = null;
      throw e;
    });
    return this.index;
  }

  private async update(): Promise<void> {
    const q = this.input.value;
    let index: SearchHit[];
    try {
      index = await this.load();
    } catch {
      this.hits = [];
      this.list.replaceChildren(this.row('cannot read the sites to search', null));
      return;
    }
    if (q !== this.input.value) return; // typed on meanwhile
    this.hits = search(index, q);
    this.active = 0;
    this.render();
  }

  private open(h: SearchHit): void {
    this.close();
    this.input.value = '';
    this.input.blur();
    void this.view.goto(h.target, 'none').catch(() => undefined);
  }

  private close(): void {
    this.hits = [];
    this.list.replaceChildren();
    this.list.style.display = 'none';
  }

  private row(text: string, h: SearchHit | null, i = -1): HTMLLIElement {
    const p = this.view.theme.palette;
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.style.cssText = `padding:1px 6px;cursor:${h ? 'pointer' : 'default'};${i === this.active ? `background:${p.highlight};color:${p.outline}` : ''}`;
    li.textContent = text;
    if (h) {
      li.dataset.kind = h.kind;
      li.addEventListener('pointerdown', (e) => {
        e.preventDefault(); // keep the focus until the click opens it
        this.open(h);
      });
    }
    return li;
  }

  private render(): void {
    this.restyle();
    this.list.replaceChildren(...this.hits.map((h, i) => this.row(`${h.name} · ${h.kind} · ${h.where}`, h, i)));
    if (!this.hits.length && this.input.value.trim()) this.list.append(this.row('no match', null));
    this.list.style.display = this.list.children.length ? '' : 'none';
  }

  /** Follows the theme on screen (called on every HUD refresh). */
  restyle(): void {
    const p = this.view.theme.palette;
    this.input.style.cssText = `font:inherit;width:16em;color:${p.text};background:${p.background};border:2px solid ${p.highlight};padding:2px 6px`;
    const shown = this.list.style.display;
    this.list.style.cssText = `position:absolute;left:0;top:100%;margin:2px 0 0;padding:2px 0;list-style:none;min-width:24em;max-width:60vw;z-index:10;
      color:${p.text};background:${p.background};border:2px solid ${p.highlight};white-space:nowrap;overflow:hidden;text-overflow:ellipsis`;
    this.list.style.display = shown;
  }
}
