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
  it('every portrait, at both sizes, has a hand clean-up pass with a reason for each step', () => {
    for (const who of ['ilatea', 'byte', 'nocti'])
      for (const n of [64, 96]) {
        const text = readFileSync(resolve(dir, `${who}-${n}.txt`), 'utf8');
        const steps = text.split('\n').filter((l) => l.startsWith('## '));
        expect(steps.length, `${who}-${n}`).toBeGreaterThanOrEqual(3);
        for (const s of steps) expect(s.length, s).toBeGreaterThan(20);
      }
  });
  it('every clean-up edit is in the shipped pixels', () => {
    for (const f of readdirSync(dir)) {
      const [who, n] = f.replace('.txt', '').split('-') as [string, string];
      const rows = (portraits as P)[who]![n]!.frames[0]!;
      const last = new Map<string, string>();
      for (const line of readFileSync(resolve(dir, f), 'utf8').split('\n')) {
        const m = /^(\d+),(\d+)(?: (\d+),(\d+))? ([a-z]+\d?)$/.exec(line.trim());
        if (!m) {
          expect(line.trim() === '' || line.startsWith('#'), `${f}: ${line}`).toBe(true);
          continue;
        }
        const [x0, y0] = [+m[1]!, +m[2]!], [x1, y1] = m[3] ? [+m[3], +m[4]!] : [x0, y0];
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) last.set(`${x},${y}`, m[5]!);
      }
      expect(last.size, f).toBeGreaterThan(50);
      for (const [k, c] of last) {
        const [x, y] = k.split(',').map(Number) as [number, number];
        expect(palette.names[palette.alphabet.indexOf(rows[y]![x]!)], `${f} ${k}`).toBe(c);
      }
    }
  });
});
