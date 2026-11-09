// Quest questions become dialogue scripts: whatever the proxy sends (any number of choices, long prompts, long
// names), the script passes the same validator as the hand-written ones, and every choice can be reached and picked.
import { describe, expect, it } from 'vitest';
import type { Quest, QuestBoard, QuestStep } from '../src/contracts/scene.gen';
import { DialogueRun, MAX_CHOICES, MAX_TEXT, validateScript } from '../src/game/dialogue';
import { PICK, lineScript, lines, offerScript, stepScript } from '../src/game/quest-script';
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
    expect(first.text).toBe("Nobody can tell how Practice Island reaches the internet. Let's find out.");
    expect(first.choices!.map((c) => c.label)).toEqual(["Let's find out", 'Or: The link between two rooms', 'Show me the quest log', 'Not now']);
    expect(first.choices![0]!.set![PICK]).toBe(b.quests[0]!.id);
    for (const q of b.quests) if (q.step?.input === 'choice') expect(validateScript(stepScript(q.step))).toEqual([]);
    const q = quest as unknown as Quest;
    expect(q.step!.input).toBe('text');
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
});
