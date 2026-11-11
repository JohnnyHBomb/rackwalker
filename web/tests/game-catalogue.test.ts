// The device catalogue as it is shown: every state has its own mark and its own word, an entry's note is shown, and
// nothing that the device-type library does not confirm carries the tick.
import { describe, expect, it } from 'vitest';
import type { CatalogueItem, PackList, Quest } from '../src/contracts/scene.gen';
import { catalogueRow, packSections } from '../src/game/panels';
import { wouldWrite } from '../src/game/quests';
import catalogue from '../../fixtures/game-catalogue.json';
import packs from '../../fixtures/packs.json';

const item = (over: Partial<CatalogueItem>): CatalogueItem => ({
  id: 1, manufacturer: 'Juniper', model: 'EX2300-24P', part_number: 'EX2300-24P', status: 'verified', note: null, devices: 2, first_seen: null, ...over,
} as CatalogueItem);

describe('device catalogue rows', () => {
  it('gives each state its own mark and its own word', () => {
    const rows = (['verified', 'unverified', 'mismatch'] as const).map((status) => catalogueRow(item({ status })));
    expect(rows.map((r) => r.status)).toEqual(['verified', 'unverified', 'mismatch']);
    expect(rows.map((r) => r.mark)).toEqual(['✔', '?', '≠']);
    expect(new Set(rows.map((r) => r.mark)).size).toBe(3);
    expect(rows[0]!.state).toBe('confirmed by the device-type library · 2 devices');
    expect(rows[1]!.state).toBe('not confirmed · 2 devices');
    expect(rows[2]!.state).toBe('part number of another model · 2 devices');
    // only a confirmed entry is called confirmed
    expect(rows.filter((r) => /confirmed by/.test(r.state)).map((r) => r.status)).toEqual(['verified']);
  });

  it('shows the note of an entry, the part number beside a model that is not its own part number, and one device as one', () => {
    const noted = catalogueRow(item({ status: 'unverified', model: 'HX-9', part_number: 'HX-9-A', devices: 1, note: 'Recorded by hand: the device-type library does not hold this model.' }));
    expect(noted).toEqual({ status: 'unverified', mark: '?', name: 'HX-9 (HX-9-A)', state: 'not confirmed · 1 device',
      note: 'Recorded by hand: the device-type library does not hold this model.' });
    expect(catalogueRow(item({})).name).toBe('EX2300-24P');
    expect(catalogueRow(item({ part_number: null })).name).toBe('EX2300-24P');
    expect(catalogueRow(item({})).note).toBeNull();
    expect(catalogueRow(item({ devices: 0 })).state).toBe('confirmed by the device-type library · 0 devices');
  });

  it('shows every entry of the fixture catalogue under its own state', () => {
    const items = (catalogue as unknown as { items: CatalogueItem[] }).items;
    expect(items.length).toBeGreaterThan(0);
    for (const it of items) expect(catalogueRow(it).status).toBe(it.status);
  });
});

describe('what the Apply button will do', () => {
  const ready = { scope: 'netbox', write_blocked: null, proposal: { ops: [], reason: 'quest' } } as unknown as Pick<Quest, 'scope' | 'write_blocked' | 'proposal'>;

  it('follows the line, not the summary of the quest log', () => {
    // the log says "some lines are practice" or even "all": this line's change was checked and will be written
    expect(wouldWrite(ready, { can_write: true })).toBe(true);
    expect(wouldWrite(ready, { can_write: false })).toBe(true);
    expect(wouldWrite(ready, null)).toBe(true);
    // the line itself is blocked (a dry-run proxy, a read-only token, a missing permission): a practice run, whatever the log says
    const blocked = { ...ready, write_blocked: 'Your NetBox user may not add dcim.rack.' };
    expect(wouldWrite(blocked, { can_write: true })).toBe(false);
    expect(wouldWrite({ ...ready, scope: 'practice' }, { can_write: true })).toBe(false);
    // nothing proposed: no verdict of its own, the log's summary stands
    expect(wouldWrite({ ...ready, proposal: null }, { can_write: false })).toBe(false);
    expect(wouldWrite({ ...ready, proposal: null }, { can_write: true })).toBe(true);
  });
});

describe('the packs the game ships with, as the catalogue lists them', () => {
  const list = packs as unknown as PackList;

  it('puts the provider packs under one heading that counts the countries and the partial ones', () => {
    const { providers, others, heading } = packSections(list.packs);
    expect(providers.length).toBe(50);
    expect(providers.every((p) => p.kind === 'providers')).toBe(true);
    expect(others.map((p) => p.kind)).toEqual(['devices', 'serials']);
    expect(providers.length + others.length).toBe(list.packs.length);
    const partial = providers.filter((p) => p.partial).length;
    expect(partial).toBeGreaterThan(0);
    expect(heading).toBe(`Provider packs: 49 countries and the international names (${partial} partial)`);
    // by the name of the country, not by its code (Spain is ES, and stands after Slovenia); the international pack last
    const titles = providers.map((p) => p.title);
    expect(titles.slice(0, 3)).toEqual(['Common providers: Australia', 'Common providers: Austria', 'Common providers: Belgium']);
    expect(titles.indexOf('Common providers: Spain')).toBeGreaterThan(titles.indexOf('Common providers: Slovenia'));
    expect(providers.at(-1)!.country).toBeNull();
    expect(providers.slice(0, -1).every((p) => p.country)).toBe(true);
    // every partial pack has something to show under "Missing"
    for (const p of providers.filter((x) => x.partial)) expect(p.missing.length, p.id).toBeGreaterThan(0);
  });

  it('counts one country as one, and says nothing of partial packs when there are none', () => {
    const one = list.packs.filter((p) => p.id === 'providers-nl');
    expect(one[0]!.partial).toBe(false);
    expect(packSections(one).heading).toBe('Provider packs: 1 country');
    expect(packSections(list.packs.filter((p) => p.id === 'providers-international')).heading).toBe('Provider packs: the international names (1 partial)');
    expect(packSections([])).toEqual({ providers: [], others: [], heading: 'Provider packs: ' });
  });
});
