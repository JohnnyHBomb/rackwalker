// The dialogue system every NPC shares: branching scripts as JSON data files (scripts/*.json, schema
// contracts/dialogue.schema.json), checked by `validateScript` when they load and in the unit tests. This module is
// the pure part (no DOM): which line is on screen, which choices show, and what picking one does. The bubble is dialogue-ui.ts.
//
// A script: { id, once?, min_hint?, start, nodes: { <name>: Node } }.
//   Node:   { speaker, text, choices?: Choice[], next?: Next, set?: {flag: value} }
//   Choice: { label, next?: Next, set?: {flag: value}, do?: Action, if?: flag, unless?: flag }
//   Next:   null (end) | "<node>" | [{ to: "<node>" | null, if?: flag, unless?: flag }, ...] (first match wins)
// `once` names a flag the run sets as soon as the first line shows; a script whose `once` flag is set never starts on
// its own again (the guide never repeats herself). `min_hint` is the lowest hint level at which the game may start
// the script on its own; the player can still open it from the menu.
// `{key:<action>}` in a line names the player's own key for that action (keys.ts KEY_ACTIONS), so a line about keys
// stays true after the player remaps them.
import type { FlagValue, Flags, HintLevel } from '../contracts/scene.gen';
import { KEY_ACTIONS } from './keys';

/** `giver` is the quest giver of the site in play: the bubble takes their name and animal from the game (dialogue-ui.ts `cast`). */
export const SPEAKERS = ['ilatea', 'byte', 'nocti', 'giver'] as const;
export type Speaker = (typeof SPEAKERS)[number];
export const ACTIONS = ['hints_low', 'hints_off', 'open_settings', 'open_menu', 'open_practice', 'open_first_site'] as const;
export type Action = (typeof ACTIONS)[number];
export const HINT_LEVELS: readonly HintLevel[] = ['off', 'low', 'normal', 'high'];
/** One line, then the player acts: text longer than this is a speech, not a hint. */
export const MAX_TEXT = 160;
export const MAX_CHOICES = 4;
const FLAG = /^[a-z0-9][a-z0-9_.:-]{0,63}$/;
/** The longest string flag value the game store keeps (contracts: FlagValue). */
export const MAX_FLAG_TEXT = 200;
const isNode = (nodes: object, name: unknown): name is string => typeof name === 'string' && Object.hasOwn(nodes, name);
/** A value the game store accepts for a flag: boolean, integer, or a string of at most MAX_FLAG_TEXT characters. */
const flagValue = (v: unknown) => typeof v === 'boolean' || Number.isSafeInteger(v) || (typeof v === 'string' && v.length <= MAX_FLAG_TEXT);
const NAME = /^[a-z][a-z0-9_-]{0,31}$/;
/** `{key:<action>}` in a line: replaced by the key(s) the player has bound to that action. */
export const KEY_TOKEN = /\{key:([a-z]+)\}/g;

export interface Branch {
  readonly to: string | null;
  readonly if?: string;
  readonly unless?: string;
}
export type Next = string | null | readonly Branch[];
export interface Choice {
  readonly label: string;
  readonly next?: Next;
  readonly set?: Readonly<Record<string, FlagValue>>;
  readonly do?: Action;
  readonly if?: string;
  readonly unless?: string;
}
export interface DialogueNode {
  readonly speaker: Speaker;
  readonly text: string;
  readonly choices?: readonly Choice[];
  readonly next?: Next;
  readonly set?: Readonly<Record<string, FlagValue>>;
}
export interface Script {
  readonly id: string;
  readonly once?: string;
  readonly min_hint?: HintLevel;
  readonly start: string;
  readonly nodes: Readonly<Record<string, DialogueNode>>;
}

/** Structural and graph checks: every `next` names a node, every node is reachable, limits hold. [] = valid. */
export function validateScript(s: unknown): string[] {
  const e: string[] = [];
  const o = s as Partial<Script> | null;
  if (!o || typeof o !== 'object') return ['script is not an object'];
  if (typeof o.id !== 'string' || !/^[a-z][a-z0-9-]{0,47}$/.test(o.id)) e.push('id: lower-case words with dashes');
  if (o.once !== undefined && (typeof o.once !== 'string' || !FLAG.test(o.once))) e.push('once: not a flag name');
  if (o.min_hint !== undefined && !HINT_LEVELS.includes(o.min_hint)) e.push('min_hint: unknown hint level');
  const nodes = (o.nodes && typeof o.nodes === 'object' ? o.nodes : {}) as Record<string, DialogueNode>;
  const names = Object.keys(nodes);
  if (!names.length) e.push('nodes: none');
  if (!isNode(nodes, o.start)) e.push(`start: no node ${String(o.start)}`);
  const flags = (where: string, set: unknown) => {
    if (set === undefined) return;
    if (!set || typeof set !== 'object') return void e.push(`${where}.set: not an object`);
    for (const [k, v] of Object.entries(set)) {
      if (!FLAG.test(k)) e.push(`${where}.set: bad flag name ${k}`);
      if (!flagValue(v)) e.push(`${where}.set.${k}: not a flag value (boolean, integer, or text of at most ${MAX_FLAG_TEXT} characters)`);
    }
  };
  const targets = (where: string, n: Next | undefined): (string | null)[] => {
    if (n === undefined || n === null) return [null];
    if (typeof n === 'string') return [n];
    if (!Array.isArray(n) || !n.length) return void e.push(`${where}.next: empty branch list`), [];
    for (const b of n) for (const k of ['if', 'unless'] as const) if (b[k] !== undefined && !FLAG.test(b[k]!)) e.push(`${where}.next.${k}: bad flag`);
    if (n[n.length - 1]!.if !== undefined || n[n.length - 1]!.unless !== undefined) e.push(`${where}.next: last branch must be unconditional`);
    return n.map((b) => b.to);
  };
  const edges = new Map<string, (string | null)[]>();
  for (const name of names) {
    const n = nodes[name]!;
    const w = `nodes.${name}`;
    if (!NAME.test(name)) e.push(`${w}: bad node name`);
    if (!SPEAKERS.includes(n.speaker)) e.push(`${w}.speaker: unknown speaker ${String(n.speaker)}`);
    if (typeof n.text !== 'string' || !n.text.trim()) e.push(`${w}.text: empty`);
    else if (n.text.length > MAX_TEXT) e.push(`${w}.text: longer than ${MAX_TEXT} characters`);
    if (typeof n.text === 'string')
      for (const m of n.text.matchAll(KEY_TOKEN)) if (!(KEY_ACTIONS as readonly string[]).includes(m[1]!)) e.push(`${w}.text: {key:${m[1]}} names no key action`);
    flags(w, n.set);
    let out: (string | null)[] = [];
    if (n.choices !== undefined) {
      if (n.next !== undefined) e.push(`${w}: has both choices and next`);
      if (!Array.isArray(n.choices) || n.choices.length < 1 || n.choices.length > MAX_CHOICES) e.push(`${w}.choices: 1 to ${MAX_CHOICES}`);
      else {
        n.choices.forEach((c, i) => {
          const cw = `${w}.choices.${i}`;
          if (typeof c.label !== 'string' || !c.label.trim() || c.label.length > 48) e.push(`${cw}.label: 1 to 48 characters`);
          if (c.do !== undefined && !ACTIONS.includes(c.do)) e.push(`${cw}.do: unknown action`);
          for (const k of ['if', 'unless'] as const) if (c[k] !== undefined && !FLAG.test(c[k]!)) e.push(`${cw}.${k}: bad flag`);
          flags(cw, c.set);
          out = out.concat(targets(cw, c.next));
        });
        // A choice the player can always pick: a node never strands the player with every option hidden.
        if (n.choices.every((c) => c.if !== undefined || c.unless !== undefined)) e.push(`${w}.choices: every choice is conditional`);
      }
    } else out = targets(w, n.next);
    for (const t of out) if (t !== null && !isNode(nodes, t)) e.push(`${w}: next names missing node ${t}`);
    edges.set(name, out);
  }
  // Reachability from start.
  if (isNode(nodes, o.start)) {
    const seen = new Set<string>([o.start]);
    const todo = [o.start];
    while (todo.length) for (const t of edges.get(todo.pop()!) ?? []) if (t !== null && isNode(nodes, t) && !seen.has(t)) seen.add(t), todo.push(t);
    for (const n of names) if (!seen.has(n)) e.push(`nodes.${n}: unreachable from start`);
  }
  // Every node can reach an end (a null next), so no loop of lines can hold the player in a conversation.
  const ends = new Set<string>();
  for (let grew = true; grew; ) {
    grew = false;
    for (const n of names) if (!ends.has(n) && (edges.get(n) ?? []).some((t) => t === null || ends.has(t))) ends.add(n), (grew = true);
  }
  for (const n of names) if (!ends.has(n)) e.push(`nodes.${n}: can never reach an end`);
  return e;
}

export function loadScript(s: unknown): Script {
  const errs = validateScript(s);
  if (errs.length) throw new Error(`dialogue script ${(s as { id?: string })?.id ?? '?'}: ${errs.join('; ')}`);
  return s as Script;
}

/** May the game start this script on its own (not from the menu)? */
export function mayAutoStart(s: Script, flags: Flags, hint: HintLevel): boolean {
  if (s.once && flags[s.once]) return false;
  if (hint === 'off') return false;
  return HINT_LEVELS.indexOf(hint) >= HINT_LEVELS.indexOf(s.min_hint ?? 'low');
}

export interface Effects {
  readonly set: Record<string, FlagValue>;
  readonly actions: Action[];
}

const truthy = (flags: Flags, f: string | undefined) => (f === undefined ? true : !!flags[f]);
const shown = (flags: Flags, c: { if?: string; unless?: string }) => truthy(flags, c.if) && !(c.unless !== undefined && flags[c.unless]);

/** One conversation in progress. Flags it sets are applied to its own copy at once and reported as effects. */
export class DialogueRun {
  private node: string | null;
  private readonly flags: Record<string, FlagValue>;
  readonly effects: Effects = { set: {}, actions: [] };

  constructor(
    readonly script: Script,
    flags: Flags,
    private readonly keyLabel: (action: string) => string = (a) => a,
  ) {
    this.flags = Object.fromEntries(Object.entries(flags).filter((e): e is [string, FlagValue] => e[1] !== undefined));
    this.node = script.start;
    if (script.once) this.setFlags({ [script.once]: true });
    this.enter();
  }

  get done(): boolean {
    return this.node === null;
  }
  get current(): DialogueNode | null {
    return this.node === null ? null : this.script.nodes[this.node]!;
  }
  /** The line on screen, with each `{key:<action>}` replaced by the player's key for it. */
  get text(): string {
    return this.current?.text.replace(KEY_TOKEN, (_, a: string) => this.keyLabel(a)) ?? '';
  }
  get nodeName(): string | null {
    return this.node;
  }
  /** The choices on screen (conditions applied), with their index in the script for `choose`. */
  get choices(): { label: string; index: number }[] {
    const n = this.current;
    return (n?.choices ?? []).map((c, index) => ({ c, index })).filter(({ c }) => shown(this.flags, c)).map(({ c, index }) => ({ label: c.label, index }));
  }

  /** A line without choices: go on. Ignored while choices are on screen. */
  advance(): void {
    const n = this.current;
    if (!n || n.choices) return;
    this.go(n.next);
  }

  choose(index: number): void {
    const c = this.current?.choices?.[index];
    if (!c || !shown(this.flags, c)) return;
    if (c.set) this.setFlags(c.set);
    if (c.do) this.effects.actions.push(c.do);
    this.go(c.next);
  }

  /** The player closed the bubble: the run ends here (the `once` flag stays set). */
  close(): void {
    this.node = null;
  }

  private go(next: Next | undefined): void {
    if (next === undefined || next === null) this.node = null;
    else if (typeof next === 'string') this.node = next;
    else this.node = next.find((b) => shown(this.flags, b))?.to ?? null;
    this.enter();
  }

  private enter(): void {
    const n = this.current;
    if (n?.set) this.setFlags(n.set);
  }

  private setFlags(set: Readonly<Record<string, FlagValue>>): void {
    Object.assign(this.flags, set);
    Object.assign(this.effects.set, set);
  }
}
