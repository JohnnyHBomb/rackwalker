import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { PALETTE_KEYS, SLOT_IDS, SLOT_SPECS, THEME_IDS, renderSlot } from '../src/contracts/theme';
import { debugTheme } from '../src/themes/debug';

const manifest = JSON.parse(readFileSync(resolve(__dirname, '../../contracts/theme-slots.json'), 'utf8'));

describe('theme manifest', () => {
  it('TS slot specs equal contracts/theme-slots.json', () => {
    expect(manifest.slots.map((s: { id: string }) => s.id)).toEqual([...SLOT_IDS]);
    for (const s of manifest.slots) {
      const t = SLOT_SPECS[s.id as keyof typeof SLOT_SPECS];
      expect({ space: t.space, width: t.width, height: t.height, anchor: [...t.anchor], footprint: t.footprint ? [...t.footprint] : null })
        .toEqual({ space: s.space, width: s.width, height: s.height, anchor: s.anchor, footprint: s.footprint });
    }
    expect(manifest.palette_keys).toEqual([...PALETTE_KEYS]);
  });

  it('theme ids match the schema enum', () => {
    const schema = JSON.parse(readFileSync(resolve(__dirname, '../../contracts/scene.schema.json'), 'utf8'));
    expect(schema.$defs.ThemeId.enum).toEqual([...THEME_IDS]);
  });

  it('debug theme fills every slot, deterministically, with visible pixels', () => {
    expect(Object.keys(debugTheme.palette).sort()).toEqual([...PALETTE_KEYS].sort());
    for (const id of SLOT_IDS) {
      const a = renderSlot(debugTheme, id, { seed: 1, rotation: 1 });
      const b = renderSlot(debugTheme, id, { seed: 1, rotation: 1 });
      expect(a.data).toEqual(b.data);
      let opaque = 0;
      for (let i = 3; i < a.data.length; i += 4) if (a.data[i] === 255) opaque++;
      expect(opaque, id).toBeGreaterThan(0);
    }
  });

  it('room_floor fills exactly the 2:1 diamond (16 rows, 4..32..4 px)', () => {
    const buf = renderSlot(debugTheme, 'room_floor');
    const widths = Array.from({ length: 16 }, (_, r) => {
      let n = 0;
      for (let x = 0; x < 32; x++) if (buf.data[(r * 32 + x) * 4 + 3] === 255) n++;
      return n;
    });
    expect(widths).toEqual([4, 8, 12, 16, 20, 24, 28, 32, 32, 28, 24, 20, 16, 12, 8, 4]);
  });
});
