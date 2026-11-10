// Quest conversations as dialogue scripts: every question a quest line asks (contract `QuestStep`, made by the proxy
// from the player's own NetBox) becomes a script in the shared dialogue format, checked by `validateScript` like the
// hand-written ones. Pure: no DOM.
//
// A bubble holds one line of at most 160 characters and at most four choices, so:
//   * a longer prompt is split at sentence ends into several lines (the last one carries the choices);
//   * more than four choices are paged, two or three to a page with "More choices", and "I don't know yet" on every
//     page, so it is always one key away. The likeliest candidates come first; the answers that end the search ("none,
//     on purpose", "another one that is in NetBox", "not in NetBox yet") come on the next page, before the rest of the
//     candidates, so "that is how it is" is never four pages deep;
//   * text from NetBox (a device or site name) is never read as a `{key:...}` token of the script format;
//   * a choice label longer than 48 characters is cut with an ellipsis (the proxy already keeps them short).
// Picking a choice sets the flag `quest.pick` to the choice's id and ends the script; the shell reads it from the run's
// effects and never stores it.
import type { Quest, QuestBoard, QuestChoice, QuestStep } from '../contracts/scene.gen';
import { MAX_CHOICES, MAX_TEXT, loadScript, type Choice, type DialogueNode, type Script } from './dialogue';

export const PICK = 'quest.pick';
const clip = (s: string, n: number) => (s.length <= n ? s : `${s.slice(0, n - 1).trimEnd()}…`);

/**
 * Text that came from the proxy (and so from NetBox names) as plain words: a `{key:` in it is not a key token. 
 * word joiner (U+2060, no width) after the brace keeps it looking the same and stops the script format from reading it.
 */
export const plain = (text: string): string => text.replace(/\{(?=key:)/g, '{\u2060');

/** A text as lines of at most MAX_TEXT characters, split at sentence ends, then at spaces. */
export function lines(text: string): string[] {
  const out: string[] = [];
  let cur = '';
  for (const s of plain(text).trim().split(/(?<=[.!?])\s+/)) {
    let part = s;
    while (part.length > MAX_TEXT) {
      const cut = part.lastIndexOf(' ', MAX_TEXT - 1);
      const at = cut > 40 ? cut : MAX_TEXT - 1;
      if (cur) out.push(cur), (cur = '');
      out.push(part.slice(0, at).trimEnd() + (cut > 40 ? '' : '…'));
      part = part.slice(at).trimStart();
    }
    if (!cur) cur = part;
    else if (cur.length + 1 + part.length <= MAX_TEXT) cur += ` ${part}`;
    else out.push(cur), (cur = part);
  }
  if (cur) out.push(cur);
  return out.length ? out : ['...'];
}

const pick = (c: Pick<QuestChoice, 'id' | 'label'>): Choice => ({ label: clip(c.label, 48), set: { [PICK]: c.id }, next: null });

/**
 * A chain of plain lines ending in one node with choices. `prefix` names the nodes (p0, p1, ...); returns the nodes
 * and the name of the first.
 */
function say(prefix: string, text: string, last: (text: string) => DialogueNode): { nodes: Record<string, DialogueNode>; start: string } {
  const parts = lines(text);
  const nodes: Record<string, DialogueNode> = {};
  parts.forEach((t, i) => {
    nodes[`${prefix}${i}`] = i === parts.length - 1 ? last(t) : { speaker: 'giver', text: t, next: `${prefix}${i + 1}` };
  });
  return { nodes, start: `${prefix}0` };
}

/** The question of one step as a script. Only for steps with `input: 'choice'`. */
export function stepScript(step: QuestStep): Script {
  const unknown = step.choices.find((c) => c.kind === 'unknown');
  const all = step.choices.filter((c) => c.kind !== 'unknown');
  // One page when everything fits; else every page also carries "More choices" (the last: "Back to the first choices").
  const room = MAX_CHOICES - (unknown ? 1 : 0);
  const per = all.length <= room ? room : room - 1;
  // When paged: the first page of candidates, then "that is how it is" and the other ways out, then the other candidates.
  const picks = all.filter((c) => c.kind === 'candidate' || c.kind === 'confirm' || c.kind === 'skip');
  const ways = [...all.filter((c) => c.kind === 'not_applicable'), ...all.filter((c) => c.kind === 'other')];
  const rest = all.length <= room ? all : [...picks.slice(0, per), ...ways, ...picks.slice(per)];
  const pages: QuestChoice[][] = [];
  for (let i = 0; i < rest.length; i += per) pages.push(rest.slice(i, i + per));
  if (!pages.length) pages.push([]);
  const nodes: Record<string, DialogueNode> = {};
  const pageChoices = (p: number): Choice[] => {
    const cs: Choice[] = pages[p]!.map(pick);
    if (pages.length > 1) cs.push({ label: p < pages.length - 1 ? 'More choices' : 'Back to the first choices', next: p < pages.length - 1 ? `page${p + 1}` : 'page0' });
    if (unknown) cs.push(pick(unknown));
    return cs;
  };
  // Why the last answer was not taken comes first, then the question; its last line carries the choices.
  const q = lines(step.error ? `${step.error} ${step.prompt}` : step.prompt);
  q.slice(0, -1).forEach((text, i) => {
    nodes[`q${i}`] = { speaker: 'giver', text, next: i === q.length - 2 ? 'page0' : `q${i + 1}` };
  });
  pages.forEach((_, p) => {
    nodes[`page${p}`] = { speaker: 'giver', text: q[q.length - 1]!, choices: pageChoices(p) };
  });
  return loadScript({ id: 'quest-step', start: q.length > 1 ? 'q0' : 'page0', nodes });
}

/** What a quest giver says first: how things stand at the site, and the lines on offer (at most two, then the log). */
export function offerScript(board: QuestBoard, siteName: string): Script {
  const all = board.quests.filter((q) => q.status !== 'done' && q.status !== 'dismissed');
  const open = all.filter((q) => q.kind !== 'spot_check');
  if (!open.length) {
    // Nothing is missing: a short honest line, and at most a spot check on offer. No invented work.
    const site = board.sites.find((s) => s.site_id === board.site_id);
    const text = board.all_in_order
      ? `All in order at ${siteName}: nothing I can see is missing.${site?.boss_ready && !site.boss_acknowledged ? ' Its Confusion has shown itself outside.' : ''}`
      : board.message;
    const spot = all.find((q) => q.kind === 'spot_check');
    const last = (t: string): DialogueNode =>
      spot
        ? { speaker: 'giver', text: t, choices: [{ label: spot.status === 'parked' ? 'It matches the record now' : 'Spot-check one device', set: { [PICK]: spot.id }, next: null }, { label: 'Show me the quest log', set: { [PICK]: 'log' }, next: null }, { label: 'Not now', next: null }] }
        : { speaker: 'giver', text: t, next: null };
    return loadScript({ id: 'quest-offer', start: 'a0', nodes: say('a', text, last).nodes });
  }
  const fresh = open.filter((q) => q.status === 'offered' || q.status === 'active' || q.status === 'ready');
  const lead = fresh[0] ?? open[0]!;
  const others = open.filter((q) => q !== lead).slice(0, 1);
  const choices: Choice[] = [
    { label: clip(verb(lead), 48), set: { [PICK]: lead.id }, next: null },
    ...others.map((q) => ({ label: clip(`Or: ${q.title}`, 48), set: { [PICK]: q.id }, next: null })),
    { label: 'Show me the quest log', set: { [PICK]: 'log' }, next: null },
    { label: 'Not now', next: null },
  ];
  const text = lead.status === 'parked' ? `Still looking into "${lead.title}"? Tell me when you know more.` : lead.purpose;
  return loadScript({ id: 'quest-offer', start: 'o0', nodes: say('o', text, (t) => ({ speaker: 'giver', text: t, choices })).nodes });
}

function verb(q: Quest): string {
  if (q.status === 'parked') return 'I know more now';
  if (q.status === 'ready' || q.status === 'practised') return 'Show me the change';
  if (q.status === 'active') return 'Carry on';
  return q.rank === 'connectivity' ? "Let's find out" : "Let's do it";
}

/** A short closing line from the giver (parked, dismissed, a note). */
export function lineScript(text: string): Script {
  return loadScript({ id: 'quest-line', start: 'l0', nodes: say('l', text, (t) => ({ speaker: 'giver', text: t, next: null })).nodes });
}
