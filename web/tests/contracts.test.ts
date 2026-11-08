// Every fixture validates against contracts/scene.schema.json (ajv, draft 2020-12); mutations are rejected;
// the generated TS types are fresh.
import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { generate } from '../scripts/gen-types.mjs';

const repo = resolve(__dirname, '../..');
const schema = JSON.parse(readFileSync(resolve(repo, 'contracts/scene.schema.json'), 'utf8'));
const index = JSON.parse(readFileSync(resolve(repo, 'fixtures/index.json'), 'utf8')).fixtures as Array<Record<string, string | number>>;
const load = (f: string) => JSON.parse(readFileSync(resolve(repo, 'fixtures', f), 'utf8'));

const ajv = new Ajv2020({ strict: true, allowUnionTypes: true, allErrors: true });
addFormats.default(ajv);
ajv.addSchema(schema);
const validate = (def: string, body: unknown) => {
  const v = ajv.getSchema(`${schema.$id}#/$defs/${def}`);
  if (!v) throw new Error(`no $def ${def}`);
  const ok = v(body);
  return { ok, errors: v.errors };
};

const cases: Array<[string, string]> = [
  ...index.map((f) => [f.file as string, f.schema as string] as [string, string]),
  ...index.filter((f) => f.request_file).map((f) => [f.request_file as string, f.request_schema as string] as [string, string]),
];

describe('fixtures vs schema', () => {
  it.each(cases)('%s is a valid %s', (file, def) => {
    const r = validate(def, load(file));
    expect(r.errors ?? []).toEqual([]);
    expect(r.ok).toBe(true);
  });

  it('rejects an extra key, a bad enum and a missing required key', () => {
    const base = load('site-1.json');
    const extra = structuredClone(base);
    extra.devices[0].colour = 'red';
    const badEnum = structuredClone(base);
    badEnum.racks[0].iso_facing = 'NE';
    const missing = structuredClone(base);
    delete missing.cables[0].a_terminations;
    for (const bad of [extra, badEnum, missing]) expect(validate('SiteScene', bad).ok).toBe(false);
  });

  it('rejects an unknown change op', () => {
    const cs = load('changes-request.json');
    cs.ops[0].op = 'teleport_rack';
    expect(validate('ChangeSet', cs).ok).toBe(false);
  });
});

describe('generated types', () => {
  it('src/contracts/scene.gen.ts matches a fresh generation from the schema', async () => {
    const fresh = await generate();
    expect(readFileSync(resolve(__dirname, '../src/contracts/scene.gen.ts'), 'utf8')).toBe(fresh);
  });
});
