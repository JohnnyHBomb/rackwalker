import { describe, expect, it, vi } from 'vitest';
import type { SiteScene } from '../src/contracts/scene.gen';
import { CachedSource, crumbsFor, enterTarget, formatView, parseView } from '../src/engine/nav';
import { loadLevel } from '../src/engine/levels';
import { HttpSceneSource, SceneSourceError } from '../src/engine/source';
import { fixture, nodeFixtureSource } from './helpers';

describe('FixtureSceneSource', () => {
  const src = nodeFixtureSource();
  it('serves each level from the catalogued fixture', async () => {
    expect((await src.world()).sites.length).toBe(3);
    expect((await src.site(2)).site.name).toBe('West Works');
    expect((await src.rack(12)).rack.id).toBe(12);
    expect((await src.device(2)).device.name).toBe('nd-core1');
    expect((await src.trace('interface', 173)).trace.hops.length).toBeGreaterThan(0);
  });
  it('an uncatalogued path is a 404 not_found SceneSourceError; a catalogued error keeps its body', async () => {
    await expect(src.device(999)).rejects.toMatchObject({ status: 404, code: 'not_found' });
    await expect(src.site(999)).rejects.toMatchObject({ status: 404, code: 'not_found', message: fixture<{ error: { message: string } }>('error-not-found.json').error.message });
  });
});

describe('HttpSceneSource (proxy API stub)', () => {
  const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  it('GETs the openapi.yaml routes', async () => {
    const f = vi.fn(async (_u: RequestInfo | URL) => ok({}));
    const s = new HttpSceneSource('http://proxy:8710', f as unknown as typeof fetch);
    await s.world();
    await s.site(1);
    await s.rack(3);
    await s.device(4);
    await s.trace('front_port', 5);
    expect(f.mock.calls.map((c) => String(c[0]))).toEqual([
      'http://proxy:8710/api/scene/world',
      'http://proxy:8710/api/scene/site/1',
      'http://proxy:8710/api/scene/rack/3',
      'http://proxy:8710/api/device/4',
      'http://proxy:8710/api/trace/front_port/5',
    ]);
  });
  it('maps an ErrorResponse to SceneSourceError, a body-less failure to internal, a network failure to netbox_unreachable', async () => {
    const err = fixture('error-not-found.json');
    const s1 = new HttpSceneSource('', (async () => new Response(JSON.stringify(err), { status: 404 })) as typeof fetch);
    await expect(s1.site(999)).rejects.toMatchObject({ status: 404, code: 'not_found', details: { kind: 'site', id: 999 } });
    const s2 = new HttpSceneSource('', (async () => new Response('oops', { status: 500 })) as typeof fetch);
    await expect(s2.world()).rejects.toMatchObject({ status: 500, code: 'internal' });
    const s3 = new HttpSceneSource('', (async () => { throw new TypeError('connection refused'); }) as typeof fetch);
    await expect(s3.world()).rejects.toBeInstanceOf(SceneSourceError);
    await expect(s3.world()).rejects.toMatchObject({ code: 'netbox_unreachable' });
  });
});

describe('navigation', () => {
  it('deep links round-trip and reject junk', () => {
    for (const v of ['world', 'site:1', 'room:1:2', 'rack:3', 'device:2']) expect(formatView(parseView(v)!)).toBe(v);
    for (const v of ['', 'site', 'site:x', 'room:1', 'moon:1', null]) expect(parseView(v)).toBeNull();
  });
  it('double-click targets follow world -> site -> room -> rack -> device', () => {
    const s = fixture<SiteScene>('site-1.json');
    expect(enterTarget('world', { kind: 'site', data: s.site })).toEqual({ level: 'site', siteId: 1 });
    expect(enterTarget('site', { kind: 'location', data: s.locations[0]! })).toEqual({ level: 'room', siteId: 1, locationId: 1 });
    expect(enterTarget('room', { kind: 'rack', data: s.racks[0]! })).toEqual({ level: 'rack', rackId: 1 });
    expect(enterTarget('rack', { kind: 'device', data: s.devices[1]! })).toEqual({ level: 'device', deviceId: 2 });
    expect(enterTarget('device', { kind: 'device', data: s.devices[1]! })).toBeNull();
    expect(enterTarget('room', { kind: 'cable', data: s.cables[0]! })).toBeNull();
  });
  it('breadcrumbs from the world down, through rack and room', async () => {
    const src = new CachedSource(nodeFixtureSource());
    const names = async (v: string) => (await crumbsFor(parseView(v)!, src)).map((c) => c.name);
    expect(await names('world')).toEqual(['World']);
    expect(await names('room:1:2')).toEqual(['World', 'North Depot', 'Comms Cupboard']);
    expect(await names('rack:1')).toEqual(['World', 'North Depot', 'Server Room', 'ND-R1']);
    expect(await names('device:2')).toEqual(['World', 'North Depot', 'Server Room', 'ND-R1', 'nd-core1']);
  });
  it('CachedSource reads each scene once; site and room levels share it', async () => {
    const inner = nodeFixtureSource();
    const spy = vi.spyOn(inner, 'site');
    const src = new CachedSource(inner);
    await loadLevel({ level: 'site', siteId: 1 }, src);
    await loadLevel({ level: 'room', siteId: 1, locationId: 1 }, src);
    await loadLevel({ level: 'room', siteId: 1, locationId: 2 }, src);
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it('CachedSource does not cache failures', async () => {
    const inner = nodeFixtureSource();
    const spy = vi.spyOn(inner, 'device');
    const src = new CachedSource(inner);
    await expect(src.device(77)).rejects.toBeInstanceOf(SceneSourceError);
    await expect(src.device(77)).rejects.toBeInstanceOf(SceneSourceError);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
