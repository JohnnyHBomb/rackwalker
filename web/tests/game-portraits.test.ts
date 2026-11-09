// Portraits come from the vector-source pipeline: both sizes for every speaker, palette colours only, and every hand
// clean-up edit is in the shipped pixels.
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import portraits from '../src/game/world/assets/portraits.json';
import palette from '../src/game/world/assets/palette.json';

const dir = resolve(__dirname, '../art/portraits/cleanup');
type P = Record<string, Record<string, { w: number; h: number; frames: string[][] }>>;

describe('portraits', () => {
  it('has 64 and 96 px portraits of Ilatea, Byte and Nocti with a vector source each', () => {
    for (const who of ['ilatea', 'byte', 'nocti']) {
      expect(readFileSync(resolve(__dirname, `../art/portraits/${who}.svg`), 'utf8')).toMatch(/^<svg[^>]+width="768"/);
      for (const n of [64, 96]) {
        const p = (portraits as P)[who]![String(n)]!;
        expect([p.w, p.h, p.frames[0]!.length, p.frames[0]![0]!.length]).toEqual([n, n, n, n]);
        for (const r of p.frames[0]!) for (const ch of r) expect(palette.alphabet.indexOf(ch)).toBeGreaterThan(0);
      }
    }
  });
  it('every clean-up edit is in the shipped pixels', () => {
    for (const f of readdirSync(dir)) {
      const [who, n] = f.replace('.json', '').split('-') as [string, string];
      const spec = JSON.parse(readFileSync(resolve(dir, f), 'utf8')) as { steps: { why: string; px: [number, number, string][] }[] };
      const rows = (portraits as P)[who]![n]!.frames[0]!;
      const last = new Map<string, string>();
      for (const s of spec.steps) {
        expect(s.why.length).toBeGreaterThan(10);
        for (const [x, y, c] of s.px) last.set(`${x},${y}`, c);
      }
      for (const [k, c] of last) {
        const [x, y] = k.split(',').map(Number) as [number, number];
        expect(palette.names[palette.alphabet.indexOf(rows[y]![x]!)], `${f} ${k}`).toBe(c);
      }
    }
  });
});
