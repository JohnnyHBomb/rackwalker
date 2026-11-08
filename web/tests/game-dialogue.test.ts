// Dialogue scripts: every file in src/game/scripts/ passes the JSON Schema and the graph checks; the run follows
// branches, conditions and flags; the guide's lines stay one line long and her intro never repeats.
import { describe, expect, it } from 'vitest';
import Ajv2020 from 'ajv/dist/2020.js';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { DialogueRun, MAX_TEXT, loadScript, mayAutoStart, validateScript, type Script } from '../src/game/dialogue';
import { DEFAULT_KEYS, keysLabel, rebind } from '../src/game/keys';

const dir = resolve(__dirname, '../src/game/scripts');
const schema = JSON.parse(readFileSync(resolve(__dirname, '../../contracts/dialogue.schema.json'), 'utf8'));
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
    [{ nodes: { a: { speaker: 'ilatea', text: 'x', next: 'b' }, b: { speaker: 'ilatea', text: 'y', next: 'a' } } }, 'nodes.a: can never reach an end'],
    [{ nodes: { a: { speaker: 'ilatea', text: 'Press {key:jump}.' } } }, '{key:jump} names no key action'],
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

describe('lines name the player\'s own keys', () => {
  const intro = () => loadScript(read('ilatea-intro.json'));
  const keysLine = (keys = DEFAULT_KEYS) => {
    const r = new DialogueRun(intro(), {}, (a) => keysLabel(keys, a));
    r.choose(1); // How do I walk?
    return r.text;
  };
  it('the intro\'s keys line names the default keys', () => {
    expect(keysLine()).toBe('Walk with ↑/W ←/A ↓/S →/D, or tap where you want to go. M opens the menu.');
  });
  it('after a remap the same line names the new keys and never the old ones', () => {
    let keys = DEFAULT_KEYS;
    for (const [a, code] of [['up', 'KeyI'], ['menu', 'KeyP']] as const) {
      const r = rebind(keys, a, code);
      if (!('keys' in r)) throw new Error(r.error);
      keys = r.keys;
    }
    const line = keysLine(keys);
    expect(line).toContain('I/↑/W');
    expect(line).toContain('P/M opens the menu');
  });
  it('no script hard-codes a key name in its text', () => {
    for (const f of files)
      for (const n of Object.values(read(f).nodes) as { text: string }[])
        expect(n.text.replace(/\{key:[a-z]+\}/g, ''), `${f}: ${n.text}`).not.toMatch(/\b(Esc|Escape|Enter|Space|WASD)\b|[Aa]rrow keys?|\b[A-Z] (opens|closes|steps|talks)\b/);
  });
  it('a script without a key label function shows the action name', () => {
    const r = new DialogueRun(script({ nodes: { a: { speaker: 'ilatea', text: 'Press {key:interact}.' } } }), {});
    expect(r.text).toBe('Press interact.');
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

// Every refusal branch of the validator: for any script that is not shipped (and so not checked by the schema), it is
// the only gate before the script runs.
describe('validateScript refuses', () => {
  const one = (node: Record<string, unknown>, over: Record<string, unknown> = {}) => ({ id: 'x', start: 'a', nodes: { a: { speaker: 'ilatea', text: 'hi', ...node } }, ...over });
  it.each([
    ['a bad id', one({}, { id: 'Not An Id' }), 'id: lower-case'],
    ['an unknown min_hint', one({}, { min_hint: 'loud' }), 'min_hint: unknown hint level'],
    ['choices and next together', one({ choices: [{ label: 'a', next: null }], next: null }), 'has both choices and next'],
    ['an empty label', one({ choices: [{ label: ' ', next: null }] }), 'label: 1 to 48'],
    ['a label over 48 characters', one({ choices: [{ label: 'x'.repeat(49), next: null }] }), 'label: 1 to 48'],
    ['an unknown action', one({ choices: [{ label: 'a', do: 'teleport', next: null }] }), 'do: unknown action'],
    ['a bad flag name to set', one({ set: { 'Bad Name': true } }), 'bad flag name'],
    ['a set that is not an object', one({ set: 3 }), 'set: not an object'],
    ['a non-integer flag value', one({ set: { 'quest.count': 1.5 } }), 'not a flag value'],
    ['a flag text longer than the store keeps', one({ set: { note: 'z'.repeat(201) } }), 'not a flag value'],
    ['an object flag value', one({ choices: [{ label: 'a', next: null, set: { note: { a: 1 } } }] }), 'not a flag value'],
    ['a bad flag in a branch if', one({ next: [{ to: null, if: 'Bad Flag' }, { to: null }] }), 'next.if: bad flag'],
    ['a bad flag in a branch unless', one({ next: [{ to: null, unless: 'Bad Flag' }, { to: null }] }), 'next.unless: bad flag'],
    ['a bad flag in a choice if', one({ choices: [{ label: 'a', next: null, if: 'Bad Flag' }, { label: 'b', next: null }] }), 'choices.0.if: bad flag'],
    ['a bad flag in a choice unless', one({ choices: [{ label: 'a', next: null, unless: 'Bad Flag' }, { label: 'b', next: null }] }), 'choices.0.unless: bad flag'],
    ['a bad node name', { id: 'x', start: 'A b', nodes: { 'A b': { speaker: 'ilatea', text: 'hi' } } }, 'bad node name'],
    ['no nodes', { id: 'x', start: 'a', nodes: {} }, 'nodes: none'],
    ['empty text', one({ text: '   ' }), 'text: empty'],
    ['an empty branch list', one({ next: [] }), 'empty branch list'],
    ['a last branch with only unless', one({ next: [{ to: null, unless: 'f' }] }), 'last branch must be unconditional'],
    ['choices that are all conditional by unless', one({ choices: [{ label: 'a', next: null, unless: 'f' }] }), 'every choice is conditional'],
    ['an inherited name as the next node', one({ choices: [{ label: 'go', next: 'constructor' }, { label: 'stop', next: null }] }), 'missing node constructor'],
    ['an inherited name as the start', { id: 'x', start: 'toString', nodes: { a: { speaker: 'ilatea', text: 'hi' } } }, 'start: no node toString'],
    ['a script that is not an object', null, 'script is not an object'],
  ] as [string, unknown, string][])('%s', (_, s, msg) => {
    expect(validateScript(s).join('; ')).toContain(msg);
  });
  it('accepts every flag value the store keeps', () => {
    expect(validateScript(one({ set: { a: true, b: false, c: 0, d: -3, e: 'z'.repeat(200) } }))).toEqual([]);
  });
});

describe('conditions on choices and branches', () => {
  const s: Script = {
    id: 'cond',
    start: 'a',
    nodes: {
      a: {
        speaker: 'ilatea',
        text: 'Hello.',
        choices: [
          { label: 'New here', next: null, unless: 'met' },
          { label: 'Again', next: null, if: 'met' },
          { label: 'Bye', next: 'b' },
        ],
      },
      b: { speaker: 'ilatea', text: 'B.', next: [{ to: 'c', unless: 'done' }, { to: null }] },
      c: { speaker: 'ilatea', text: 'C.' },
    },
  };
  const labels = (flags: Record<string, boolean | number | string>) => new DialogueRun(s, flags).choices.map((c) => c.label);
  it('unless hides a choice while its flag is set', () => {
    expect(labels({})).toEqual(['New here', 'Bye']);
    expect(labels({ met: true })).toEqual(['Again', 'Bye']);
  });
  it('a flag set to false, 0 or empty text counts as not set', () => {
    for (const v of [false, 0, '']) expect(labels({ met: v })).toEqual(['New here', 'Bye']);
  });
  it('unless on a branch skips it while its flag is set', () => {
    const go = (flags: Record<string, boolean | number>) => {
      const r = new DialogueRun(s, flags);
      r.choose(2);
      r.advance();
      return r.nodeName;
    };
    expect(go({})).toBe('c');
    expect(go({ done: true })).toBeNull();
    expect(go({ done: 0 })).toBe('c');
  });
  it('a hidden choice cannot be picked by its index', () => {
    const r = new DialogueRun(s, {});
    r.choose(1);
    expect(r.nodeName).toBe('a');
  });
});

describe('mayAutoStart and the hint level', () => {
  const at = (min_hint?: Script['min_hint']): Script => ({ id: 'h', start: 'a', nodes: { a: { speaker: 'ilatea', text: 'x' } }, ...(min_hint ? { min_hint } : {}) });
  it('hint level off never starts a script on its own, even one that allows off', () => {
    expect(mayAutoStart(at('off'), {}, 'off')).toBe(false);
    expect(mayAutoStart(at('off'), {}, 'low')).toBe(true);
  });
  it('a script without min_hint starts from low up', () => {
    expect(mayAutoStart(at(), {}, 'low')).toBe(true);
    expect(mayAutoStart(at(), {}, 'normal')).toBe(true);
    expect(mayAutoStart(at('normal'), {}, 'low')).toBe(false);
    expect(mayAutoStart(at('high'), {}, 'normal')).toBe(false);
  });
});
