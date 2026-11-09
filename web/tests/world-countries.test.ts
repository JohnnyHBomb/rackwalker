// Country sets: keyed by country code, the neutral set for anything unknown, every sprite present, villagers placed.
import { describe, expect, it } from 'vitest';
import { COUNTRY_SETS, countrySet, NEUTRAL_SET, setSprites } from '../src/game/world/countries';
import { SPRITES } from '../src/game/world/engine';
import { WorldMap } from '../src/game/world/gen';

describe('country sets', () => {
  it('has the Netherlands and the United Kingdom (every country of the dev seed), keyed by ISO code', () => {
    expect(Object.keys(COUNTRY_SETS).sort()).toEqual(['GB', 'NL']);
    for (const [k, s] of Object.entries(COUNTRY_SETS)) expect(s.code).toBe(k);
  });
  it('looks codes up case-insensitively; unknown or missing gets the neutral set', () => {
    expect(countrySet('nl').code).toBe('NL');
    expect(countrySet(' GB ').code).toBe('GB');
    for (const c of ['ZZ', '', null, undefined, 'NLD']) expect(countrySet(c)).toBe(NEUTRAL_SET);
  });
  it('every sprite of every set exists in the asset file', () => {
    for (const s of [...Object.values(COUNTRY_SETS), NEUTRAL_SET]) for (const n of setSprites(s)) expect(SPRITES.has(n), n).toBe(true);
  });
  it('a region takes its sites\' most common country and shows that set', () => {
    const at = (c: string | null) => new WorldMap([1, 2, 3].map((id) => ({ id, name: `S${id}`, latitude: 52 + id * 0.01, longitude: 5, theme: null, country: c })));
    const nl = at('NL'), gb = at('GB'), none = at(null);
    expect([nl.regions[0]!.set.code, gb.regions[0]!.set.code, none.regions[0]!.set.code]).toEqual(['NL', 'GB', '']);
    const has = (m: WorldMap, s: string) => m.objects.some((o) => o.sprite === s);
    expect(has(nl, 'windmill') && has(nl, 'sails') && has(nl, 'hog')).toBe(true);
    expect(nl.terrain.includes('F')).toBe(true); // tulip fields
    expect(nl.terrain.includes('B')).toBe(true); // a canal with a bridge
    expect(has(gb, 'phone_box') && has(gb, 'badger')).toBe(true);
    expect(has(none, 'well') && has(none, 'rabbit')).toBe(true);
    expect(has(none, 'windmill') || has(none, 'phone_box')).toBe(false);
  });
});
