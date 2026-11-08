// Dialogue scripts: every file in src/game/scripts/ passes the JSON Schema and the graph checks; the run follows
// branches, conditions and flags; the guide's lines stay one line long and her intro never repeats.
import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { DialogueRun, MAX_TEXT, loadScript, mayAutoStart, validateScript, type Script } from '../src/game/dialogue';

const dir = resolve(__dirname, '../src/game/scripts');
const schema = JSON.parse(readFileSync(resolve(__dirname, '../src/game/dialogue.schema.json'), 'utf8'));
const ajv = new Ajv2020({ strict: true, allowUnionTypes: true, allErrors: true });
const validate = ajv.compile(schema);
const files = readdirSync(dir).filter((f) => f.endsWith('.json'));
const read = (f: string) => JSON.parse(readFileSync(resolve(dir, f), 'utf8'));

describe('dialogue script files', () => {
  it('there are scripts', () => expect(files.length).toBeGreaterThanOrEqual(4));
  it.each(files)('%s passes the schema and the graph checks', (f) => {
    const s = read(f);
    expect(validate(s), JSON.stringify(validate.errors)).toBe(true);
    expect(validateScript(s)).toEqual([]);
    expect(`${s.id}.json`).toBe(f);
  });
  it('every Ilatea line points at one small next step (one line, under the limit)', () => {
    for (const f of files.filter((n) => n.startsWith('ilatea-')))
      for (const n of Object.values(read(f).nodes) as { text: string }[]) {
        expect(n.text.length).toBeLessThanOrEqual(MAX_TEXT);
        expect(n.text).not.toContain('\n');
      }
  });
  it('the intro says the motto, can be dismissed at once and offers fewer hints', () => {
    const s = loadScript(read('ilatea-intro.json'));
    const first = s.nodes[s.start]!;
    expect(first.text).toMatch(/Small things matter/);
    expect(first.choices!.some((c) => c.next === null && !c.do)).toBe(true);
    expect(first.choices!.some((c) => c.do === 'hints_low')).toBe(true);
    expect(s.once).toBe('ilatea.intro.seen');
  });
});

const script = (over: Partial<Script> = {}): Script => ({
  id: 'test',
  start: 'a',
  nodes: {
    a: { speaker: 'ilatea', text: 'Hello.', choices: [{ label: 'Go', next: 'b', set: { 'went.b': true } }, { label: 'Secret', next: 'c', if: 'knows' }, { label: 'Bye', next: null }] },
    b: { speaker: 'byte', text: 'B.', next: [{ to: 'c', if: 'knows' }, { to: null }] },
    c: { speaker: 'nocti', text: 'C.', set: { 'saw.c': 1 } },
  },
  ...over,
});

describe('validateScript', () => {
  it.each([
    [{ start: 'zz' }, 'start: no node zz'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x', next: 'gone' } } }, 'next names missing node gone'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x' }, lost: { speaker: 'ilatea', text: 'y' } } }, 'unreachable'],
    [{ nodes: { a: { speaker: 'zeus', text: 'x' } } }, 'unknown speaker'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x'.repeat(MAX_TEXT + 1) } } }, 'longer than'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x', choices: [{ label: 'only if', if: 'f' }] } } }, 'every choice is conditional'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x', next: [{ to: null, if: 'f' }] } } }, 'last branch must be unconditional'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'x', choices: [1, 2, 3, 4, 5].map((i) => ({ label: `c${i}` })) } } }, '1 to 4'],
    [{ once: 'Bad Flag' }, 'once: not a flag name'],
  ] as [Partial<Script>, string][])('refuses %j', (over, msg) => {
    expect(validateScript(script(over)).join('; ')).toContain(msg);
    expect(() => loadScript(script(over))).toThrow();
  });
  it('accepts the sample', () => expect(validateScript(script())).toEqual([]));
});

describe('DialogueRun', () => {
  it('hides conditional choices, follows a choice, sets flags, branches on flags', () => {
    const r = new DialogueRun(script(), {});
    expect(r.choices.map((c) => c.label)).toEqual(['Go', 'Bye']);
    r.choose(0);
    expect(r.nodeName).toBe('b');
    r.advance();
    expect(r.done).toBe(true);
    expect(r.effects.set).toEqual({ 'went.b': true });
  });
  it('a flag opens the hidden choice and the branch', () => {
    const r = new DialogueRun(script(), { knows: true });
    expect(r.choices.map((c) => c.label)).toEqual(['Go', 'Secret', 'Bye']);
    r.choose(0);
    r.advance();
    expect(r.nodeName).toBe('c');
    r.advance();
    expect(r.done).toBe(true);
    expect(r.effects.set).toEqual({ 'went.b': true, 'saw.c': 1 });
  });
  it('a hidden choice cannot be picked; advance is ignored while choices show', () => {
    const r = new DialogueRun(script(), {});
    r.choose(1);
    r.advance();
    expect(r.nodeName).toBe('a');
  });
  it('the once flag is set at the first line, even if the player closes at once', () => {
    const r = new DialogueRun(script({ once: 'seen.it' }), {});
    r.close();
    expect(r.done).toBe(true);
    expect(r.effects.set).toEqual({ 'seen.it': true });
  });
  it('choices report their action', () => {
    const r = new DialogueRun(script({ nodes: { a: { speaker: 'ilatea', text: 'x', choices: [{ label: 'quiet', do: 'hints_off' }] } } }), {});
    r.choose(0);
    expect(r.effects.actions).toEqual(['hints_off']);
  });
});

describe('mayAutoStart (hint level, never repeats)', () => {
  const s = script({ once: 'seen.it', min_hint: 'normal' });
  it.each([
    ['off', {}, false],
    ['low', {}, false],
    ['normal', {}, true],
    ['high', {}, true],
    ['high', { 'seen.it': true }, false],
  ] as const)('hint %s flags %j -> %s', (hint, flags, ok) => expect(mayAutoStart(s, flags, hint)).toBe(ok));
  it('hint off silences even a low script', () => expect(mayAutoStart(script({ min_hint: 'low' }), {}, 'off')).toBe(false));
});
