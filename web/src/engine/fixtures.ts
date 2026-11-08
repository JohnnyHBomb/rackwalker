// Browser wiring of FixtureSceneSource: fixtures/*.json bundled by Vite (lazy, one chunk per file).
import { FixtureSceneSource, type FixtureIndex } from './source';

const files = import.meta.glob<unknown>('../../../fixtures/*.json', { import: 'default' });

export async function fixtureSource(): Promise<FixtureSceneSource> {
  const load = (file: string) => {
    const f = files[`../../../fixtures/${file}`];
    if (!f) throw new Error(`fixture file ${file} is not bundled`);
    return f();
  };
  return new FixtureSceneSource((await load('index.json')) as FixtureIndex, load);
}
