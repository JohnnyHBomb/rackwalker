// Hit testing: the topmost item whose sprite has an opaque pixel under the point. Uses the same buffers the
// renderer uploads, so what you click is exactly what you see.
import type { Entity } from './entity';
import { screenToTile } from './projection';
import type { Item, LevelScene } from './scene';
import type { SpriteCache } from './sprites';

export interface Pick {
  readonly entity: Entity;
  readonly item: Item;
}

/** Topmost item with an entity at level point (lx, ly) (level px at scale 1), or null. */
export function pickAt(scene: LevelScene, sprites: SpriteCache, lx: number, ly: number): Pick | null {
  const px = Math.floor(lx);
  const py = Math.floor(ly);
  for (let i = scene.items.length - 1; i >= 0; i--) {
    const item = scene.items[i]!;
    if (!item.entity) continue;
    const ix = px - item.x;
    const iy = py - item.y;
    if (ix < 0 || iy < 0) continue;
    const buf = sprites.get(item.sprite);
    if (ix >= buf.width || iy >= buf.height) continue;
    if (buf.data[(iy * buf.width + ix) * 4 + 3]! > 0) return { entity: item.entity, item };
  }
  return null;
}

/** The ground tile under a level point (iso levels only). */
export function tileAt(scene: LevelScene, lx: number, ly: number): { tx: number; ty: number } | null {
  return scene.space === 'iso' ? screenToTile(lx, ly) : null;
}
