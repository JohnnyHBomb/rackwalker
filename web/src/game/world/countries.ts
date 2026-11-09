// Per-country landmark and villager sets for the overworld, keyed by ISO 3166-1 alpha-2 country code.
//
// Rules for every set: landmarks, flora, food and weather only (never caricatures of people); villagers are animals
// native to the country, sometimes with one affectionately silly prop (a hat, a tool, food or a vehicle; never a
// religious, sacred or ceremonial item, never an animal dressed as a human ethnic stereotype). A country without a set
// gets the neutral set. Sprite names refer to `assets/sprites.json` (written by web/art/overworld/author_world.py).

/** A landmark: a sprite standing on a solid footprint of fw x fh tiles; its anchor is the bottom centre. */
export interface Landmark {
  readonly sprite: string;
  readonly fw: number;
  readonly fh: number;
  /** How many of it a region tries to place. */
  readonly count: number;
  /** A second sprite drawn above everything at an offset from the anchor (the windmill's sails). */
  readonly top?: { readonly sprite: string; readonly dx: number; readonly dy: number; readonly fps: number };
  readonly fps?: number;
  /** Cast shadow ellipse [dx, dy, rx, ry] from the anchor. */
  readonly shadow?: readonly [number, number, number, number];
  /** Not solid and drawn above everything (weather). */
  readonly sky?: boolean;
}

export interface Villager {
  readonly sprite: string;
  /** What the villager is, for screen readers and the docs. */
  readonly what: string;
}

export interface CountrySet {
  /** ISO 3166-1 alpha-2, or '' for the neutral set. */
  readonly code: string;
  readonly name: string;
  /** A canal across the region (with bridges and a sandy bank) or a pond. */
  readonly water: 'canal' | 'pond';
  /** Field crop: 'F' tulips in rows, 'C' a yellow crop in rows. */
  readonly field: 'F' | 'C';
  readonly landmarks: readonly Landmark[];
  readonly villagers: readonly Villager[];
  /** Tree sprites scattered on the grass, in proportion. */
  readonly trees: readonly string[];
}

const TREE_SHADOW = [5, -1, 12, 4] as const;
export { TREE_SHADOW };

export const COUNTRY_SETS: Readonly<Record<string, CountrySet>> = {
  NL: {
    code: 'NL',
    name: 'Netherlands',
    water: 'canal',
    field: 'F',
    landmarks: [
      { sprite: 'windmill', fw: 3, fh: 2, count: 1, top: { sprite: 'sails', dx: 0, dy: -53, fps: 8 }, shadow: [6, -1, 16, 4] },
      { sprite: 'cottage_nl', fw: 4, fh: 3, count: 1, shadow: [6, -1, 26, 4] },
      { sprite: 'cheese_cart', fw: 2, fh: 1, count: 1, shadow: [2, 0, 12, 3] },
      { sprite: 'bicycle', fw: 2, fh: 1, count: 2, shadow: [2, 0, 10, 2] },
    ],
    villagers: [{ sprite: 'hog', what: 'a hedgehog wearing its bicycle bell as a hat' }],
    trees: ['tree_a', 'tree_b', 'poplar', 'poplar'],
  },
  GB: {
    code: 'GB',
    name: 'United Kingdom',
    water: 'pond',
    field: 'C',
    landmarks: [
      { sprite: 'phone_box', fw: 1, fh: 1, count: 1, shadow: [3, 0, 7, 2] },
      { sprite: 'pillar_box', fw: 1, fh: 1, count: 1, shadow: [2, 0, 5, 2] },
      { sprite: 'teapot_stall', fw: 2, fh: 1, count: 1, shadow: [2, 0, 12, 3] },
      { sprite: 'rain_cloud', fw: 2, fh: 1, count: 1, sky: true, fps: 6 },
    ],
    villagers: [
      { sprite: 'badger', what: 'a badger in a bowler hat' },
      { sprite: 'fox', what: 'a red fox with a white-tipped tail' },
    ],
    trees: ['tree_a', 'tree_b', 'tree_b', 'bush'],
  },
};

export const NEUTRAL_SET: CountrySet = {
  code: '',
  name: 'Anywhere',
  water: 'pond',
  field: 'C',
  landmarks: [
    { sprite: 'well', fw: 2, fh: 1, count: 1, shadow: [3, 0, 12, 3] },
    { sprite: 'haystack', fw: 2, fh: 1, count: 2, shadow: [3, 0, 12, 3] },
  ],
  villagers: [{ sprite: 'rabbit', what: 'a rabbit holding a carrot' }],
  trees: ['tree_a', 'tree_b', 'bush'],
};

/** The set for a country code (case-insensitive); anything unknown or missing gets the neutral set. */
export function countrySet(code: string | null | undefined): CountrySet {
  const c = (code ?? '').trim().toUpperCase();
  return COUNTRY_SETS[c] ?? NEUTRAL_SET;
}

/** Every sprite a set needs (tests check the asset file has them all). */
export function setSprites(s: CountrySet): string[] {
  return [
    ...s.landmarks.flatMap((l) => (l.top ? [l.sprite, l.top.sprite] : [l.sprite])),
    ...s.villagers.map((v) => v.sprite),
    ...s.trees,
  ];
}
