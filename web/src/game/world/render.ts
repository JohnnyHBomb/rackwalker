// Draws a WorldMap through the tile engine: the per-cell autotile ids are worked out once per map, the static objects
// are sorted once, and each frame only the cells and objects under the camera are composed.
import { labelBuffer } from '../../engine/font';
import type { Palette as ThemePalette } from '../../contracts/theme';
import { addSprite, Frame, PALETTE, SPRITES, sprite, TILE, TILESET, tileKey, type Drawable } from './engine';
import { hash, type WorldMap, type WorldObject } from './gen';

const ANIM_FRAMES = 4;

/** A site's name as an indexed plate (light text on the darkest outline colour). */
export function nameplate(text: string): string {
  const name = `label:${text}`;
  if (SPRITES.has(name)) return name;
  const ink = PALETTE.colours[PALETTE.index('w')]!, bg = PALETTE.colours[PALETTE.index('k1')]!;
  const buf = labelBuffer(text, { background: bg, text: ink } as unknown as ThemePalette);
  const w = buf.width + 2, h = buf.height + 2;
  const px = new Uint8Array(w * h).fill(PALETTE.index('k0'));
  const inkR = parseInt(ink.slice(1, 3), 16);
  for (let y = 0; y < buf.height; y++)
    for (let x = 0; x < buf.width; x++) px[(y + 1) * w + x + 1] = buf.data[(y * buf.width + x) * 4] === inkR ? PALETTE.index('w') : PALETTE.index('k1');
  addSprite(name, { w, h, ox: w >> 1, oy: h - 1, frames: [px] });
  return name;
}

export class WorldRenderer {
  /** Tile id per cell and animation frame: cells[(y * w + x) * 4 + f]. */
  private readonly cells: Uint16Array;
  readonly frame: Frame;
  private readonly statics: WorldObject[];
  private readonly flats: WorldObject[];
  private readonly tops: WorldObject[];
  private readonly labelOf = new Map<number, string>();

  constructor(readonly map: WorldMap, w: number, h: number) {
    this.frame = new Frame(w, h);
    this.cells = new Uint16Array(map.w * map.h * ANIM_FRAMES);
    const T = (x: number, y: number) => map.at(Math.max(0, Math.min(map.w - 1, x)), Math.max(0, Math.min(map.h - 1, y)))!;
    const fallback = TILESET.keys.get('GGGGG0')!;
    for (let y = 0; y < map.h; y++)
      for (let x = 0; x < map.w; x++) {
        const c = T(x, y);
        // grass: the art direction's mix of its four variants; other terrains: an even mix
        const variant = c === 'G' ? [0, 0, 1, 1, 2, 3, 0][Math.floor(hash(x, y, 5) * 7)]! : Math.floor(hash(x, y, 6) * 4);
        const key = tileKey(c, T(x, y - 1), T(x + 1, y), T(x, y + 1), T(x - 1, y), variant);
        const ids = TILESET.keys.get(key) ?? fallback;
        for (let f = 0; f < ANIM_FRAMES; f++) this.cells[(y * map.w + x) * ANIM_FRAMES + f] = ids[f % ids.length]!;
      }
    for (const d of map.doors) this.labelOf.set(d.siteId, nameplate(d.name));
    const objs = map.objects.map((o) => (o.kind === 'label' ? { ...o, sprite: this.labelOf.get(o.site!)! } : o));
    this.flats = objs.filter((o) => o.flat);
    this.tops = objs.filter((o) => o.top && !o.flat);
    this.statics = objs.filter((o) => !o.flat && !o.top);
  }

  /** Every autotile key the map uses that the tile set lacks (tests: always empty). */
  missingKeys(): string[] {
    const out = new Set<string>();
    const m = this.map;
    const T = (x: number, y: number) => m.at(Math.max(0, Math.min(m.w - 1, x)), Math.max(0, Math.min(m.h - 1, y)))!;
    for (let y = 0; y < m.h; y++)
      for (let x = 0; x < m.w; x++) {
        const key = tileKey(T(x, y), T(x, y - 1), T(x + 1, y), T(x, y + 1), T(x - 1, y), 0);
        if (!TILESET.keys.has(key)) out.add(key);
      }
    return [...out];
  }

  tileAt(x: number, y: number, f: number): number {
    return this.cells[(y * this.map.w + x) * ANIM_FRAMES + (f % ANIM_FRAMES)]!;
  }

  /**
   * Composes one frame with the view's top-left at (cx, cy) map pixels, at time t (ms). `actors` are drawn with the
   * static sprites, sorted by base line; each may carry a shadow and glow.
   */
  render(cx: number, cy: number, t: number, actors: readonly (Drawable & { shadow?: readonly number[]; glow?: readonly number[] })[], reduceMotion = false): Frame {
    const fr = this.frame;
    const m = this.map;
    const tf = reduceMotion ? 0 : Math.floor(t / TILESET.frameMs);
    const tx0 = Math.floor(cx / TILE), ty0 = Math.floor(cy / TILE);
    const tx1 = Math.ceil((cx + fr.w) / TILE), ty1 = Math.ceil((cy + fr.h) / TILE);
    for (let ty = ty0; ty < ty1; ty++)
      for (let tx = tx0; tx < tx1; tx++) {
        const id = m.inside(tx, ty) ? this.tileAt(tx, ty, tf) : this.tileAt(0, 0, 0);
        fr.tile(TILESET.tiles[id]!, tx * TILE - cx, ty * TILE - cy);
      }
    const visible = (o: { sprite: string; x: number; y: number }) => {
      const s = SPRITES.get(o.sprite);
      if (!s) return false;
      const x0 = o.x - s.ox - cx, y0 = o.y - s.oy - cy;
      return x0 < fr.w && y0 < fr.h && x0 + s.w > 0 && y0 + s.h > 0;
    };
    const frameOf = (o: WorldObject) => (o.anim && !reduceMotion ? Math.floor((t * o.anim.fps) / 1000) + (o.anim.phase ?? 0) : 0);
    for (const o of this.flats) if (visible(o)) fr.sprite(sprite(o.sprite), frameOf(o), o.x - cx, o.y - cy);
    const vis = this.statics.filter(visible);
    const act = actors.filter(visible);
    for (const o of vis) if (o.shadow) fr.ellipse(o.x - cx + o.shadow[0], o.y - cy + o.shadow[1], o.shadow[2], o.shadow[3], PALETTE.darker);
    for (const a of act) if (a.shadow) fr.ellipse(a.x - cx + a.shadow[0]!, a.y - cy + a.shadow[1]!, a.shadow[2]!, a.shadow[3]!, PALETTE.darker);
    for (const a of act) if (a.glow) fr.ellipse(a.x - cx + a.glow[0]!, a.y - cy + a.glow[1]!, a.glow[2]!, a.glow[3]!, PALETTE.glow);
    // Merge the (already sorted) statics with the actors by base line.
    const sortedActs = [...act].sort((a, b) => a.y - b.y || a.x - b.x);
    let i = 0;
    for (const o of vis) {
      while (i < sortedActs.length && sortedActs[i]!.y < o.y) this.drawActor(sortedActs[i++]!, cx, cy);
      const bob = o.bob && !reduceMotion && Math.floor(t / 400 + (o.anim?.phase ?? 0)) % 2 ? 1 : 0;
      fr.sprite(sprite(o.sprite), frameOf(o), o.x - cx, o.y - cy - bob);
    }
    while (i < sortedActs.length) this.drawActor(sortedActs[i++]!, cx, cy);
    for (const o of this.tops) if (visible(o)) fr.sprite(sprite(o.sprite), frameOf(o), o.x - cx, o.y - cy);
    return fr;
  }

  private drawActor(a: Drawable, cx: number, cy: number): void {
    this.frame.sprite(sprite(a.sprite), a.frame ?? 0, a.x - cx, a.y - cy, a.mirror);
  }
}
