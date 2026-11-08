// Remappable keys and the game API client (CSRF header, cookie credentials, error mapping).
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { CSRF_HEADER, HttpGameApi } from '../src/game/api';
import { DEFAULT_KEYS, DEFAULT_SETTINGS, actionOf, keyName, rebind } from '../src/game/keys';
import { SceneSourceError } from '../src/engine/source';

const fixture = (f: string) => JSON.parse(readFileSync(resolve(__dirname, '../../fixtures', f), 'utf8'));

describe('keys', () => {
  it('web defaults equal the store defaults (fixtures/game-settings.json)', () => expect(DEFAULT_SETTINGS).toEqual(fixture('game-settings.json')));
  it('maps codes to actions', () => {
    expect(actionOf(DEFAULT_KEYS, 'KeyW')).toBe('up');
    expect(actionOf(DEFAULT_KEYS, 'KeyZ')).toBeNull();
  });
  it('rebinding moves the key away from its old action', () => {
    const r = rebind(DEFAULT_KEYS, 'menu', 'KeyE');
    expect('keys' in r && r.keys.menu).toEqual(['KeyE', 'KeyM']);
    expect('keys' in r && r.keys.interact).toEqual(['Enter', 'Space']);
  });
  it('never leaves an action without a key', () => {
    const r = rebind(DEFAULT_KEYS, 'interact', 'Escape');
    expect('error' in r && r.error).toMatch(/only key/);
  });
  it('keeps at most three keys per action', () => {
    let k = DEFAULT_KEYS;
    for (const c of ['KeyI', 'KeyO', 'KeyP']) k = (rebind(k, 'up', c) as { keys: typeof k }).keys;
    expect(k.up).toEqual(['KeyP', 'KeyO', 'KeyI']);
  });
  it('refuses browser keys', () => expect('error' in rebind(DEFAULT_KEYS, 'menu', 'Tab')).toBe(true));
  it('names keys', () => expect([keyName('KeyW'), keyName('ArrowUp'), keyName('Digit3'), keyName('ShiftLeft')]).toEqual(['W', '↑', '3', 'Shift Left']));
});

describe('HttpGameApi', () => {
  const recorder = (answers: [number, unknown][]) => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchFn = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      const [status, body] = answers.shift()!;
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    }) as unknown as typeof fetch;
    return { calls, fetchFn };
  };
  it('sends the session CSRF token on writes and none on reads', async () => {
    const { calls, fetchFn } = recorder([[200, fixture('game-session.json')], [200, fixture('game-save.json')], [200, fixture('game-state.json')]]);
    const api = new HttpGameApi('', fetchFn);
    await api.signIn('nbt_x.y');
    await api.save('auto', fixture('game-save-request.json'));
    await api.state();
    const h = (i: number) => calls[i]!.init.headers as Record<string, string>;
    expect(h(0)[CSRF_HEADER]).toBe('sign-in');
    expect(h(1)[CSRF_HEADER]).toBe('fixture-csrf-token');
    expect(h(2)[CSRF_HEADER]).toBeUndefined();
    expect(calls.map((c) => `${c.init.method} ${c.url}`)).toEqual(['POST /api/game/session', 'PUT /api/game/saves/auto', 'GET /api/game/state']);
    expect(calls.every((c) => c.init.credentials === 'same-origin')).toBe(true);
  });
  it('turns an ErrorResponse into SceneSourceError', async () => {
    const { fetchFn } = recorder([[401, fixture('error-unauthorized.json')]]);
    await expect(new HttpGameApi('', fetchFn).state()).rejects.toMatchObject({ status: 401, code: 'unauthorized' });
  });
  it('an unreachable proxy is netbox_unreachable', async () => {
    const api = new HttpGameApi('', (async () => {
      throw new TypeError('fetch failed');
    }) as unknown as typeof fetch);
    await expect(api.session()).rejects.toBeInstanceOf(SceneSourceError);
  });
});
