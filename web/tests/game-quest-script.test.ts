// Quest questions become dialogue scripts: whatever the proxy sends (any number of choices, long prompts, long
// names), the script passes the same validator as the hand-written ones, and every choice can be reached and picked.
import { describe, expect, it } from 'vitest';
import type { Quest, QuestBoard, QuestStep } from '../src/contracts/scene.gen';
import { DialogueRun, MAX_CHOICES, MAX_TEXT, validateScript } from '../src/game/dialogue';
import { PICK, lineScript, lines, offerScript, plain, stepScript } from '../src/game/quest-script';
import { tracerLocked } from '../src/game/quests';
import board from '../../fixtures/quest-board.json';
import quest from '../../fixtures/quest.json';

const step = (n: number, unknown = true, prompt = 'What does sw-north-1 uplink to?'): QuestStep => ({
  id: 'peer', prompt, input: 'choice', fields: [], hint: null, error: null,
  choices: [
    ...Array.from({ length: n }, (_, i) => ({ id: `device:${i + 1}`, label: `sw-${i + 1} (switch)`, kind: 'candidate' as const })),
    ...(unknown ? [{ id: 'unknown', label: "I don't know yet", kind: 'unknown' as const }] : []),
  ],
});

/** Every choice id a player can reach by paging through the script, and whether every page offers "I don't know yet". */
function reachable(s: ReturnType<typeof stepScript>): { ids: string[]; unknownEverywhere: boolean } {
  const ids = new Set<string>();
  let unknownEverywhere = true;
  const seen = new Set<string>();
  const todo = [s.start];
  while (todo.length) {
    const name = todo.pop()!;
    if (seen.has(name)) continue;
    seen.add(name);
    const n = s.nodes[name]!;
    if (typeof n.next === 'string') todo.push(n.next);
    if (n.choices && !n.choices.some((c) => c.set?.[PICK] === 'unknown')) unknownEverywhere = false;
    for (const c of n.choices ?? []) {
      if (c.set?.[PICK] !== undefined) ids.add(String(c.set[PICK]));
      if (typeof c.next === 'string') todo.push(c.next);
    }
  }
  return { ids: [...ids].sort(), unknownEverywhere };
}

describe('quest questions as dialogue scripts', () => {
  it('any number of choices gives a valid script in which every choice can be picked', () => {
    for (const unknown of [true, false])
      for (let n = 0; n <= 14; n++) {
        if (n === 0 && !unknown) continue;
        const st = stepScript(step(n, unknown));
        expect(validateScript(st), `${n} choices, unknown ${unknown}`).toEqual([]);
        const r = reachable(st);
        expect(r.ids).toEqual(step(n, unknown).choices.map((c) => c.id).sort());
        if (unknown) expect(r.unknownEverywhere, `${n} choices`).toBe(true);
        for (const node of Object.values(st.nodes)) expect((node.choices ?? []).length).toBeLessThanOrEqual(MAX_CHOICES);
      }
  });

  it('up to three candidates and "I don\'t know yet" fit one bubble; more are paged with a way back', () => {
    expect(Object.keys(stepScript(step(3)).nodes)).toEqual(['page0']);
    const paged = stepScript(step(5));
    expect(Object.keys(paged.nodes)).toEqual(['page0', 'page1', 'page2']);
    expect(paged.nodes.page0!.choices!.map((c) => c.label)).toEqual(['sw-1 (switch)', 'sw-2 (switch)', 'More choices', "I don't know yet"]);
    expect(paged.nodes.page2!.choices!.map((c) => c.label)).toEqual(['sw-5 (switch)', 'Back to the first choices', "I don't know yet"]);
  });

  it('picking a choice ends the script with its id as the answer', () => {
    const run = new DialogueRun(stepScript(step(5)), {});
    run.choose(2); // More choices
    expect(run.nodeName).toBe('page1');
    run.choose(1);
    expect(run.done).toBe(true);
    expect(run.effects.set[PICK]).toBe('device:4');
  });

  it('a long prompt is split at sentence ends into lines of at most 160 characters; the last carries the choices', () => {
    const long = `${'sw-north-1 has 24 ports and nothing recorded on any of them. '.repeat(3)}What does it uplink to?`;
    expect(long.length).toBeGreaterThan(MAX_TEXT);
    const s = stepScript(step(2, true, long));
    expect(validateScript(s)).toEqual([]);
    expect(s.start).toBe('q0');
    expect(Object.values(s.nodes).every((n) => n.text.length <= MAX_TEXT)).toBe(true);
    expect(s.nodes.page0!.text.endsWith('What does it uplink to?')).toBe(true);
    expect(lines('x'.repeat(400)).every((l) => l.length <= MAX_TEXT)).toBe(true);
    expect(lines('One. Two.')).toEqual(['One. Two.']);
  });

  it('why the last answer was not taken is said before the question', () => {
    const s = stepScript({ ...step(1), error: "sw-quay-1 has no free port called 'Gi9/9/9'." });
    expect(s.nodes[s.start]!.text.startsWith("sw-quay-1 has no free port called 'Gi9/9/9'.")).toBe(true);
  });

  it('a name longer than a choice label is cut with an ellipsis, never refused', () => {
    const st = step(1);
    const s = stepScript({ ...st, choices: [{ id: 'device:1', label: 'x'.repeat(48), kind: 'candidate' }, ...st.choices.slice(1)] });
    expect(validateScript(s)).toEqual([]);
  });

  it('the proxy\'s own example bodies make valid scripts: the offer of the practice board and its first question', () => {
    const b = board as unknown as QuestBoard;
    const offer = offerScript(b, 'Practice Island');
    expect(validateScript(offer)).toEqual([]);
    const first = Object.values(offer.nodes).find((n) => n.choices)!;
    expect(first.text).toBe("Nobody can tell from NetBox how Practice Island reaches the outside world. Let's find out.");
    expect(first.choices!.map((c) => c.label)).toEqual(["Let's find out", 'Or: The link between Loft and Quay Room', 'Show me the quest log', 'Not now']);
    expect(first.choices![0]!.set![PICK]).toBe(b.quests[0]!.id);
    for (const q of b.quests) if (q.step?.input === 'choice') expect(validateScript(stepScript(q.step))).toEqual([]);
    // the example line after its first answer: whose line it is (asked even with one provider on record)
    const q = quest as unknown as Quest;
    expect(q.step!.id).toBe('provider');
    expect(validateScript(stepScript(q.step!))).toEqual([]);
  });

  it('a name from NetBox that looks like a key token is shown as it is and breaks no script', () => {
    const st = step(1, true, 'What does sw-loft-{key:pwn} uplink to, and sw-{key:interact}-1?');
    const s = stepScript(st);
    expect(validateScript(s)).toEqual([]);
    const run = new DialogueRun(s, { interact: 'Enter' });
    // nothing is swapped for a key name, nothing is lost; the brace is followed by a character of no width
    expect(run.text.replace(/\u2060/g, '')).toBe('What does sw-loft-{key:pwn} uplink to, and sw-{key:interact}-1?');
    const b = { ...(board as unknown as QuestBoard) };
    b.quests = b.quests.map((x, i) => (i === 0 ? { ...x, purpose: 'Nobody can tell how Harbour {key:pwn} reaches the outside world.' } : x));
    expect(validateScript(offerScript(b, 'Harbour {key:pwn}'))).toEqual([]);
    expect(validateScript(lineScript('Good to know about {key:nothing}.'))).toEqual([]);
    expect(plain('{key:x} {keys} {key')).toBe('{\u2060key:x} {keys} {key');
  });

  it('the answers that end the search come right after the first candidates, not pages deep', () => {
    const many: QuestStep = { ...step(6), choices: [
      ...step(6, false).choices,
      { id: 'named', label: 'Another device that is in NetBox', kind: 'other' },
      { id: 'other', label: 'A device that is not in NetBox yet', kind: 'other' },
      { id: 'none', label: 'Nothing: it stands alone, on purpose', kind: 'not_applicable' },
      { id: 'unknown', label: "I don't know yet", kind: 'unknown' },
    ] };
    const s = stepScript(many);
    expect(validateScript(s)).toEqual([]);
    expect(s.nodes.page0!.choices!.map((c) => c.label)).toEqual(['sw-1 (switch)', 'sw-2 (switch)', 'More choices', "I don't know yet"]);
    expect(s.nodes.page1!.choices!.map((c) => c.label)).toEqual(['Nothing: it stands alone, on purpose', 'Another device that is in NetBox', 'More choices', "I don't know yet"]);
    expect(s.nodes.page2!.choices!.map((c) => c.label)).toEqual(['A device that is not in NetBox yet', 'sw-3 (switch)', 'More choices', "I don't know yet"]);
    expect(reachable(s).ids).toEqual(many.choices.map((c) => c.id).sort());
    // when everything fits one bubble, the order is the proxy's own
    const few: QuestStep = { ...step(1), choices: [step(1).choices[0]!, { id: 'none', label: 'None, on purpose', kind: 'not_applicable' }, { id: 'other', label: 'Not listed', kind: 'other' }, step(1).choices[1]!] };
    expect(stepScript(few).nodes.page0!.choices!.map((c) => c.label)).toEqual(['sw-1 (switch)', 'None, on purpose', 'Not listed', "I don't know yet"]);
  });

  it('tracing waits for the cable tracer only while the tool can be earned', () => {
    const b = board as unknown as QuestBoard;
    const connectivity = b.findings.some((f) => f.rank === 'connectivity');
    expect(connectivity).toBe(true);
    const earnable: QuestBoard = { ...b, can_write: true, write_blocked: null, rewards: { ...b.rewards, tools: [] } };   // (the island lends its tools)
    expect(tracerLocked(earnable)).toBe(true);
    expect(tracerLocked({ ...earnable, rewards: { ...b.rewards, tools: ['cable_tracer'] } })).toBe(false);
    // a dry-run proxy, a read-only token, a missing permission: the tool cannot be earned, so nothing is locked
    expect(tracerLocked({ ...earnable, can_write: false, write_blocked: 'The proxy runs in dry-run mode.' })).toBe(false);
    expect(tracerLocked({ ...earnable, write_blocked: 'Your NetBox user may not add cables.' })).toBe(false);
    // a tidy estate has no connectivity line to earn it with
    expect(tracerLocked({ ...earnable, findings: b.findings.filter((f) => f.rank !== 'connectivity') })).toBe(false);
    expect(tracerLocked(null)).toBe(false);
  });

  it('a site with nothing open gets a short honest line, and its Confusion is named when it is ready', () => {
    const b = board as unknown as QuestBoard;
    const site = { ...b.sites[0]!, boss_ready: true, boss_acknowledged: false };
    const clear: QuestBoard = { ...b, site_id: site.site_id, quests: [], all_in_order: true, sites: [site] };
    const s = offerScript(clear, 'Practice Island');
    expect(validateScript(s)).toEqual([]);
    expect(Object.values(s.nodes).map((n) => n.text).join(' ')).toBe('All in order at Practice Island: nothing I can see is missing. Its Confusion has shown itself outside.');
    expect(validateScript(lineScript('Good to know: that is how it is. I will not ask about it again.'))).toEqual([]);
    // with a spot check on offer the same line carries it as a choice: one device, never invented work
    const spot = { ...b.quests[0]!, id: 'q:spot_check:device:1:0', kind: 'spot_check', status: 'offered' as const };
    const withSpot = offerScript({ ...clear, quests: [spot] }, 'Practice Island');
    expect(validateScript(withSpot)).toEqual([]);
    const lastNode = Object.values(withSpot.nodes).find((n) => n.choices)!;
    expect(lastNode.choices!.map((c) => c.label)).toEqual(['Spot-check one device', 'Show me the quest log', 'Not now']);
  });

  it('a later stage of a chain says where it stands in the chain; a first stage opens as it always did', () => {
    const b = board as unknown as QuestBoard;
    const lead = b.quests[0]!;
    const text = (q: Quest) => {
      const s = offerScript({ ...b, quests: [q] }, 'Practice Island');
      const out: string[] = [];
      for (let n: string | null | undefined = s.start; n; n = typeof s.nodes[n]!.next === 'string' ? (s.nodes[n]!.next as string) : null) out.push(s.nodes[n]!.text);
      return out.join(' ');
    };
    expect(lead.chain).toMatchObject({ stage: 'physical', position: 1, total: 3, label: '1 of 3: physical' });
    expect(text(lead)).toBe(lead.purpose);
    const later: Quest = { ...lead, chain: { ...lead.chain!, stage: 'logical', position: 2, label: '2 of 3: logical' } };
    expect(text(later)).toBe(`Next in "The way out of Practice Island": 2 of 3: logical. ${lead.purpose}`);
    expect(validateScript(offerScript({ ...b, quests: [later] }, 'Practice Island'))).toEqual([]);
    // the fixture quest log lists the chains in play, each with its stages and what the record will hold
    expect(b.chains.map((c) => [c.kind, c.label])).toEqual([['way_out', '1 of 3: physical'], ['room_link', '1 of 1: physical'], ['device', '1 of 2: physical']]);
    expect(b.chains[0]!.stages.map((s) => s.status)).toEqual(['open', 'locked', 'locked']);
  });
});
