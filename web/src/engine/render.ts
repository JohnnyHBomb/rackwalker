// PIXI side: textures from sprite buffers (nearest-neighbour) and one Container per level scene.
import { Container, Sprite, Texture } from 'pixi.js';
import { SLOT_SPECS, type PixelBuffer } from '../contracts/theme';
import { labelBuffer } from './font';
import { type Item, type LevelScene, itemSize, labelBox } from './scene';
import { SpriteCache, spriteKey, type SpriteDesc } from './sprites';

export function bufferToTexture(buf: PixelBuffer): Texture {
  const canvas = document.createElement('canvas');
  canvas.width = buf.width;
  canvas.height = buf.height;
  canvas.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(buf.data), buf.width, buf.height), 0, 0);
  const tex = Texture.from(canvas);
  tex.source.scaleMode = 'nearest';
  return tex;
}

/** Textures per sprite key for one theme, backed by the SpriteCache that picking also reads. */
export class TextureCache {
  private readonly textures = new Map<string, Texture>();
  constructor(readonly sprites: SpriteCache) {}
  get(d: SpriteDesc): Texture {
    const k = spriteKey(d);
    let t = this.textures.get(k);
    if (!t) {
      t = bufferToTexture(this.sprites.get(d));
      this.textures.set(k, t);
    }
    return t;
  }
  /** A name plate in this theme's palette (font.ts). */
  label(text: string): Texture {
    const k = `label|${text}`;
    let t = this.textures.get(k);
    if (!t) {
      t = bufferToTexture(labelBuffer(text, this.sprites.theme.palette));
      this.textures.set(k, t);
    }
    return t;
  }

  /**
   * A white 1 px ring `inset` px inside a w x h box (tinted when drawn): the selection and trace marks at rack and
   * device zoom. Inside the item's own box, so it never hides a neighbour (previously: the 8 px ui_frame border
   * outside the box hid the next ports, and a traced faceplate's frame hid its first row of ports).
   */
  ringTexture(w: number, h: number, inset: number): Texture {
    const k = `ring|${w}x${h}|${inset}`;
    let t = this.textures.get(k);
    if (!t) {
      const data = new Uint8ClampedArray(w * h * 4);
      const x0 = inset, y0 = inset, x1 = w - 1 - inset, y1 = h - 1 - inset;
      for (let y = y0; y <= y1; y++)
        for (let x = x0; x <= x1; x++)
          if (x === x0 || x === x1 || y === y0 || y === y1) data.set([255, 255, 255, 255], (y * w + x) * 4);
      t = bufferToTexture({ width: w, height: h, data });
      this.textures.set(k, t);
    }
    return t;
  }
  /**
   * A white 1 px outline around the opaque pixels of a sprite (tinted when drawn), 1 px larger on every side: the
   * selection and trace mark of an object at iso zoom, drawn above the objects (previously: the floor ring under a
   * rack was hidden by the rack itself).
   */
  outlineTexture(d: SpriteDesc): Texture {
    const k = `outline|${spriteKey(d)}`;
    let t = this.textures.get(k);
    if (!t) {
      t = bufferToTexture(outlineBuffer(this.sprites.get(d)));
      this.textures.set(k, t);
    }
    return t;
  }
  destroy(): void {
    for (const t of this.textures.values()) t.destroy(true);
    this.textures.clear();
  }
}

/** White pixels where `b` is transparent next to an opaque pixel (4-neighbours), in a buffer 1 px larger each side. */
export function outlineBuffer(b: PixelBuffer): PixelBuffer {
  const w = b.width + 2, h = b.height + 2;
  const data = new Uint8ClampedArray(w * h * 4);
  const solid = (x: number, y: number) => x >= 0 && y >= 0 && x < b.width && y < b.height && b.data[(y * b.width + x) * 4 + 3]! > 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const sx = x - 1, sy = y - 1;
      if (!solid(sx, sy) && (solid(sx - 1, sy) || solid(sx + 1, sy) || solid(sx, sy - 1) || solid(sx, sy + 1))) data.set([255, 255, 255, 255], (y * w + x) * 4);
    }
  return { width: w, height: h, data };
}

export interface LevelView {
  readonly root: Container;
  /** Selection rings / hover cursor (between ground and objects at iso levels, on top at rack levels). */
  readonly marks: Container;
  /** Outlines of selected and traced objects at iso levels (above the objects, below the labels). */
  readonly outlines: Container;
  /** Drag preview, above everything. */
  readonly ghost: Container;
}

export function buildLevelContainer(scene: LevelScene, tex: TextureCache): LevelView {
  const root = new Container({ label: `level:${scene.level}` });
  const ground = new Container({ label: 'ground' });
  const objects = new Container({ label: 'objects' });
  const marks = new Container({ label: 'marks' });
  const ghost = new Container({ label: 'ghost' });
  const outlines = new Container({ label: 'outlines' });
  const labels = new Container({ label: 'labels' });
  for (const item of scene.items) {
    const s = new Sprite(tex.get(item.sprite));
    s.position.set(item.x, item.y);
    (item.layer === 'ground' ? ground : objects).addChild(s);
  }
  for (const l of scene.labels) {
    const s = new Sprite(tex.label(l.text));
    const b = labelBox(l);
    s.position.set(b.x, b.y);
    s.scale.set(l.scale);
    labels.addChild(s);
  }
  if (scene.space === 'iso') root.addChild(ground, marks, objects, outlines, labels, ghost);
  else root.addChild(ground, objects, labels, marks, ghost);
  return { root, marks, outlines, ghost };
}

/**
 * Draws the selection (and hover cursor) marks for a level; `traced` items get the same mark tinted `tint`. At rack
 * and device zoom the marks are rings inside the item (selection `selTint` on its edge, trace one pixel further in),
 * so both show at once and neither hides a neighbour.
 */
export function drawMarks(
  view: LevelView,
  scene: LevelScene,
  tex: TextureCache,
  selected: readonly Item[],
  cursorTile: { tx: number; ty: number } | null,
  traced: readonly Item[] = [],
  tint?: string,
  selTint = '#ffffff',
): void {
  for (const c of view.marks.removeChildren()) c.destroy();
  for (const c of view.outlines.removeChildren()) c.destroy();
  if (scene.space === 'rack') {
    const ring = (it: Item, inset: number, colour: string) => {
      const size = itemSize(it);
      const s = new Sprite(tex.ringTexture(size.w, size.h, Math.min(inset, Math.floor((Math.min(size.w, size.h) - 1) / 2))));
      s.position.set(it.x, it.y);
      s.tint = colour;
      view.marks.addChild(s);
    };
    for (const it of traced) ring(it, 1, tint ?? selTint);
    for (const it of selected) ring(it, 0, selTint);
    return;
  }
  const outline = (items: readonly Item[], colour: string) => {
    for (const it of items) {
      if (it.layer !== 'object') continue;
      const s = new Sprite(tex.outlineTexture(it.sprite));
      s.position.set(it.x - 1, it.y - 1);
      s.tint = colour;
      view.outlines.addChild(s);
    }
  };
  if (traced.length) {
    drawMarks(view, scene, tex, traced, null);
    // The traced items came back as "selected" of that call: floor rings in marks, outlines in outlines.
    if (tint) for (const c of [...view.marks.children, ...view.outlines.children]) (c as Sprite).tint = tint;
  }
  outline(selected, selTint);
  {
    const ring = SLOT_SPECS.selection_highlight;
    const ringTex = tex.get({ kind: 'slot', slot: 'selection_highlight', variant: {} });
    const seen = new Set<string>();
    for (const it of selected) {
      if (!it.tile) continue;
      for (let j = 0; j < it.tile.h; j++)
        for (let i = 0; i < it.tile.w; i++) {
          const tx = it.tile.x + i, ty = it.tile.y + j;
          if (seen.has(`${tx},${ty}`)) continue;
          seen.add(`${tx},${ty}`);
          const s = new Sprite(ringTex);
          s.position.set((tx - ty) * 16 - ring.anchor[0], (tx + ty) * 8 - ring.anchor[1]);
          view.marks.addChild(s);
        }
    }
    if (cursorTile) {
      const spec = SLOT_SPECS.cursor;
      const s = new Sprite(tex.get({ kind: 'slot', slot: 'cursor', variant: {} }));
      s.position.set((cursorTile.tx - cursorTile.ty) * 16 - spec.anchor[0], (cursorTile.tx + cursorTile.ty) * 8 - spec.anchor[1]);
      view.marks.addChild(s);
    }
  }
}
