import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { FixtureSceneSource, type FixtureIndex } from '../src/engine/source';

export const FIXTURES = resolve(__dirname, '../../fixtures');
export function fixture<T>(file: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, file), 'utf8')) as T;
}
export function nodeFixtureSource(): FixtureSceneSource {
  return new FixtureSceneSource(fixture<FixtureIndex>('index.json'), async (f) => fixture(f));
}
