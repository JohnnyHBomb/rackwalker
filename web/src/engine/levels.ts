// Target -> LevelScene (data through the SceneSource only), plus focus boxes for the camera. WebGL-free.
import type { Entity } from './entity';
import { entityKey } from './entity';
import { layoutSite, type SiteLayout } from './layout';
import type { Target } from './nav';
import { buildDevice, buildRack, buildRoom, buildSite, buildWorld, itemSize, type Item, type LevelScene } from './scene';
import type { SceneSource } from './source';

const layouts = new WeakMap<object, SiteLayout>();
function layoutOf(scene: Parameters<typeof layoutSite>[0]): SiteLayout {
  let l = layouts.get(scene);
  if (!l) layouts.set(scene, (l = layoutSite(scene)));
  return l;
}

export async function loadLevel(t: Target, src: SceneSource): Promise<LevelScene> {
  switch (t.level) {
    case 'world':
      return buildWorld(await src.world());
    case 'site': {
      const s = await src.site(t.siteId);
      return buildSite(s, layoutOf(s));
    }
    case 'room': {
      const s = await src.site(t.siteId);
      return buildRoom(s, t.locationId, layoutOf(s));
    }
    case 'rack':
      return buildRack(await src.rack(t.rackId));
    case 'device':
      return buildDevice(await src.device(t.deviceId));
  }
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function unionBox(items: readonly Item[]): Box | null {
  if (!items.length) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const i of items) {
    const s = itemSize(i);
    x0 = Math.min(x0, i.x);
    y0 = Math.min(y0, i.y);
    x1 = Math.max(x1, i.x + s.w);
    y1 = Math.max(y1, i.y + s.h);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Items that draw an entity (a room is its floor tiles and walls; a cable all its wire tiles). */
export function itemsOf(scene: LevelScene, e: Entity): Item[] {
  const k = entityKey(e);
  return scene.items.filter((i) => i.entity && entityKey(i.entity) === k);
}

/** The box the camera frames when a level is entered: the sites on the overworld, else the whole level. */
export function homeBox(scene: LevelScene): Box {
  if (scene.level === 'world') {
    const b = unionBox(scene.items.filter((i) => i.entity?.kind === 'site'));
    if (b) return b;
  }
  return scene.bounds;
}

export const centre = (b: Box) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
