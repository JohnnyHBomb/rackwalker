// Per-theme ground for the overworld: each region is drawn in the ground palette of its sites' most common theme
// (gen.ts Region.theme). A ground palette is a remap inside the art direction's 63 colours, authored with the other
// overworld art (web/art/overworld/author_world.py, palette.json "grounds"): one table for grass and foliage, one for
// path and sand earth. Trees and flowers take the grass table; field soil, water, buildings and characters keep their
// colours. The theme also picks the flowers and how wooded the region is.
import paletteJson from './assets/palette.json';

interface GroundJson {
  grass: Record<string, string>;
  earth: Record<string, string>;
}

const NAMES = paletteJson.names as string[];
const IDX = new Map(NAMES.map((n, i) => [n, i]));
const GROUNDS = ((paletteJson as unknown as { grounds?: Record<string, GroundJson> }).grounds ?? {}) as Record<string, GroundJson>;

function table(maps: Record<string, string>[]): Uint8Array | null {
  const lut = Uint8Array.from(NAMES.map((_, i) => i));
  let changed = false;
  for (const m of maps)
    for (const [from, to] of Object.entries(m)) {
      lut[IDX.get(from)!] = IDX.get(to)!;
      changed = true;
    }
  return changed ? lut : null;
}

const LUTS = new Map<string, Uint8Array | null>();
/** The palette remap of a theme's ground (with its path earth when `earth`), or null when it is the plain palette. */
export function groundLut(theme: string, earth: boolean): Uint8Array | null {
  const k = `${theme}|${earth}`;
  if (!LUTS.has(k)) {
    const g = GROUNDS[theme];
    LUTS.set(k, g ? table(earth ? [g.grass, g.earth] : [g.grass]) : null);
  }
  return LUTS.get(k)!;
}

/** Sprites redrawn in the region's grass table: trees, flowers and waterside plants. */
export const THEMED_SPRITES: ReadonlySet<string> = new Set(['tree_a', 'tree_b', 'poplar', 'bush', 'daisy', 'violet', 'buttercup', 'tuft', 'reeds']);

export interface GroundTheme {
  /** Flowers and small things scattered on open grass, in proportion. */
  readonly flora: readonly string[];
  /** Share of open grass tiles that get a flower (0..1). */
  readonly floraRate: number;
  /** Value-noise level above which a patch is a grove: lower is more wooded. */
  readonly grove: number;
}

export const GROUND_THEMES: Readonly<Record<string, GroundTheme>> = {
  debug: { flora: ['daisy', 'violet', 'buttercup', 'tuft', 'daisy', 'tuft', 'stone'], floraRate: 0.14, grove: 0.64 },
  'industrial-robotic': { flora: ['tuft', 'stone', 'tuft', 'stone', 'daisy'], floraRate: 0.12, grove: 0.7 },
  farm: { flora: ['buttercup', 'buttercup', 'daisy', 'tuft', 'buttercup', 'tuft'], floraRate: 0.18, grove: 0.66 },
  business: { flora: ['daisy', 'tuft', 'daisy'], floraRate: 0.07, grove: 0.72 },
  'pagodas-nature': { flora: ['violet', 'violet', 'tuft', 'stone', 'daisy', 'violet'], floraRate: 0.16, grove: 0.58 },
};
export const groundTheme = (theme: string): GroundTheme => GROUND_THEMES[theme] ?? GROUND_THEMES.debug!;
