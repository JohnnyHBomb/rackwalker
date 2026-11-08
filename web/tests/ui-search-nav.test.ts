// Regression: no way to find a device, and the URL never followed the level (reload -> World, Back left
// the app). Search index + ranking, and the URL/history sync, without a browser.
import { describe, expect, it } from 'vitest';
import { WORLD, formatView, type Target } from '../src/engine/nav';
import { NavUrl, type UrlView } from '../src/ui/nav-url';
import { buildIndex, search } from '../src/ui/search';
import { nodeFixtureSource } from './helpers';

describe('search', () => {
  it('indexes every site, room, rack and named device, with where it is', async () => {
    const index = await buildIndex(nodeFixtureSource());
    const kinds = new Set(index.map((h) => h.kind));
    expect([...kinds].sort()).toEqual(['device', 'rack', 'room', 'site']);
    const srv = index.find((h) => h.name === 'nd-srv05')!;
    expect(srv).toMatchObject({ kind: 'device', target: { level: 'device', deviceId: srv.target.level === 'device' ? srv.target.deviceId : -1 } });
    expect(srv.where).toMatch(/^North Depot \/ Server Room \/ ND-R3 U\d+$/);
    expect(index.find((h) => h.name === 'nd-srv90')!.where).toBe('North Depot / Store / unracked');
    expect(index.find((h) => h.kind === 'rack' && h.name === 'ND-R3')!.target).toEqual({ level: 'rack', rackId: 3 });
    expect(index.find((h) => h.kind === 'site' && h.name === 'West Works')!.target).toEqual({ level: 'site', siteId: 2 });
  });

  it('matches any part of the name, case-insensitive; exact, then prefix, then the rest', async () => {
    const index = await buildIndex(nodeFixtureSource());
    expect(search(index, 'ND-SRV05').map((h) => h.name)).toEqual(['nd-srv05']);
    expect(search(index, 'SRV05').map((h) => h.name)).toEqual(['ee-srv05', 'nd-srv05']);
    const r = search(index, 'nd-r', 50).map((h) => h.name);
    expect(r.slice(0, 3)).toEqual(['ND-R1', 'ND-R2', 'ND-R3']); // prefix hits first
    expect(search(index, '   ')).toEqual([]);
    expect(search(index, 'no such thing')).toEqual([]);
    expect(search(index, 'nd', 5)).toHaveLength(5);
  });
});

/** A window with a history stack, enough for NavUrl. */
function fakeWindow(href: string) {
  const entries = [href];
  let at = 0;
  const listeners: (() => void)[] = [];
  const win = {
    location: {
      get href() {
        return entries[at]!;
      },
    },
    history: {
      state: null,
      pushState: (_s: unknown, _t: string, u: URL) => {
        entries.splice(at + 1, Infinity, String(u));
        at++;
      },
      replaceState: (_s: unknown, _t: string, u: URL) => {
        entries[at] = String(u);
      },
    },
    addEventListener: (_: string, f: () => void) => listeners.push(f),
    back: () => {
      at--;
      for (const f of listeners) f();
    },
    entries,
  };
  return win;
}

function fakeView(start: Target) {
  const v: UrlView & { at: Target; gotos: string[]; onGoto?: () => void } = {
    at: start,
    gotos: [],
    get crumbs() {
      return [{ target: v.at }];
    },
    goto: async (t) => {
      v.gotos.push(formatView(t));
      v.onGoto?.(); // like RackWalkerView: the HUD refresh (and so sync) runs before goto returns, on the old level
      await Promise.resolve();
      v.at = t;
    },
  };
  return v;
}

describe('the level in the URL', () => {
  it('each level change is a history entry; the opening level is written in place; other parameters stay', () => {
    const win = fakeWindow('http://x/?source=http&theme=farm');
    const view = fakeView(WORLD);
    const nav = new NavUrl(view, win as never);
    nav.sync();
    expect(win.entries).toEqual(['http://x/?source=http&theme=farm&view=world']);
    view.at = { level: 'rack', rackId: 3 };
    nav.sync();
    nav.sync(); // no change, no entry
    expect(win.entries).toEqual(['http://x/?source=http&theme=farm&view=world', 'http://x/?source=http&theme=farm&view=rack%3A3']);
  });

  it('Back returns to the previous level (the view is sent there), not out of the app, and keeps Forward', async () => {
    const win = fakeWindow('http://x/?view=site:1');
    const view = fakeView({ level: 'site', siteId: 1 });
    const nav = new NavUrl(view, win as never);
    nav.sync();
    expect(win.entries).toHaveLength(1); // opened at the level the URL names
    view.at = { level: 'room', siteId: 1, locationId: 1 };
    nav.sync();
    view.onGoto = () => nav.sync();
    win.back();
    expect(view.gotos).toEqual(['site:1']);
    await new Promise((r) => setTimeout(r, 0));
    nav.sync(); // the view arrived where the URL is: nothing new pushed
    expect(win.entries).toEqual(['http://x/?view=site:1', 'http://x/?view=room%3A1%3A1']); // Forward is still there
    expect(win.location.href).toBe('http://x/?view=site:1'); // ...and the page is on the entry Back went to
  });
});
