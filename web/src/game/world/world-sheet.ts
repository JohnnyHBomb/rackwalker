// The overworld sheet (world.html, development only): the overworld of the fixture sites without signing in, for
// looking at the art and for the screenshots. Query: theme=<id> (every site), country=<code> (every site),
// region=<n>, at=<x>,<y> (tile the view centres on), scale=<n>, t=<ms> (animation time), still=1.
// window.__world.capture(opts) returns a PNG data URL of the native view at an integer scale.
import worldJson from '../../../../fixtures/world.json';
import { NATIVE_H, NATIVE_W, PALETTE } from './engine';
import { follow } from './camera';
import { WorldMap, TILE, type Pos } from './gen';
import { WorldRenderer } from './render';

interface Opts {
  theme?: string | null;
  country?: string | null;
  region?: number;
  at?: Pos | null;
  scale?: number;
  t?: number;
  player?: { x: number; y: number; dir: 'down' | 'up' | 'left' | 'right'; frame: number } | null;
}

export function buildMap(theme: string | null, country: string | null): WorldMap {
  return new WorldMap(
    worldJson.sites.map(({ site: s }) => ({
      id: s.id, name: s.name, latitude: s.latitude, longitude: s.longitude,
      theme: theme ?? s.iso_theme, country: country ?? (s as { country?: string | null }).country ?? null,
    })),
  );
}

/** The nearest tile at or below (x, y) the player can stand on (screenshots put the player there, not on a landmark). */
function nearStand(map: WorldMap, x: number, y: number): Pos | null {
  for (let d = 0; d < 6; d++)
    for (const [dx, dy] of [[0, d], [-d, d], [d, d], [-d, 0], [d, 0]] as const) if (map.standable(x + dx, y + dy)) return { x: x + dx, y: y + dy };
  return null;
}

const maps = new Map<string, { map: WorldMap; r: WorldRenderer }>();
function get(theme: string | null, country: string | null) {
  const k = `${theme}|${country}`;
  let m = maps.get(k);
  if (!m) {
    const map = buildMap(theme, country);
    m = { map, r: new WorldRenderer(map, NATIVE_W, NATIVE_H) };
    maps.set(k, m);
  }
  return m;
}

export function frameCanvas(o: Opts): HTMLCanvasElement {
  const { map, r } = get(o.theme ?? null, o.country ?? null);
  const reg = map.regions[o.region ?? 0] ?? map.regions[0]!;
  const at = o.at ?? (o.region ? reg.arrive : map.start);
  const v = follow(at.x * TILE + 8, at.y * TILE + 4, NATIVE_W, NATIVE_H, { x: reg.x0 * TILE, y: reg.y0 * TILE, w: reg.w * TILE, h: reg.h * TILE });
  const g = map.guide;
  const actors = [];
  if (map.regionAt(g).index === reg.index)
    actors.push({ sprite: 'guide', x: g.x * TILE + 8, y: g.y * TILE + 14, frame: Math.floor((o.t ?? 0) / 250), shadow: [1, 0, 6, 2], glow: [0, 0, 13, 5] });
  const p = o.player ?? { x: at.x * TILE + 8, y: at.y * TILE + 14, dir: 'down', frame: 0 };
  actors.push({ sprite: `player_${p.dir}`, x: p.x, y: p.y, frame: p.frame, shadow: [0, 0, 6, 2] });
  const fr = r.render(v.x, v.y, o.t ?? 0, actors);
  const img = new ImageData(NATIVE_W, NATIVE_H);
  fr.present(new Uint32Array(img.data.buffer));
  const src = document.createElement('canvas');
  src.width = NATIVE_W;
  src.height = NATIVE_H;
  src.getContext('2d')!.putImageData(img, 0, 0);
  const s = o.scale ?? 1;
  const c = document.createElement('canvas');
  c.width = NATIVE_W * s;
  c.height = NATIVE_H * s;
  const g2 = c.getContext('2d')!;
  g2.imageSmoothingEnabled = false;
  g2.drawImage(src, 0, 0, c.width, c.height);
  return c;
}

declare global {
  interface Window {
    __world?: { ready: true; capture(o: Opts): string; info(o: Opts): unknown; colours: number };
  }
}

const q = new URLSearchParams(location.search);
const at = q.get('at')?.split(',').map(Number);
const opts: Opts = {
  theme: q.get('theme'), country: q.get('country'), region: Number(q.get('region') ?? 0),
  at: at && at.length === 2 ? { x: at[0]!, y: at[1]! } : null, scale: Number(q.get('scale') ?? 3), t: Number(q.get('t') ?? 0),
};
const canvas = frameCanvas(opts);
document.body.append(canvas);
document.getElementById('status')!.remove();
window.__world = {
  ready: true,
  colours: PALETTE.colours.length - 1,
  capture: (o) => frameCanvas(o).toDataURL('image/png'),
  info: (o) => {
    const { map, r } = get(o.theme ?? null, o.country ?? null);
    const lm = map.objects.find((o) => o.kind === 'landmark' && !o.top && o.x < map.regions[0]!.w * TILE);
    const v = map.objects.find((o) => o.kind === 'villager' && o.x < map.regions[0]!.w * TILE);
    return {
      w: map.w, h: map.h, regions: map.regions.map((x) => ({ ...x, set: x.set.code })), doors: map.doors, guide: map.guide, start: map.start,
      missing: r.missingKeys(), landmark: lm ? { x: Math.floor(lm.x / TILE), y: Math.floor(lm.y / TILE) } : null,
      // a tile the player can stand on just in front of that landmark, and the region's first villager
      stand: lm ? nearStand(map, Math.floor(lm.x / TILE), Math.floor(lm.y / TILE) + 1) : null,
      villager: v ? { x: Math.floor(v.x / TILE), y: Math.floor(v.y / TILE) } : null,
    };
  },
};
if (!q.has('still')) {
  const t0 = performance.now();
  const tick = () => {
    const c = frameCanvas({ ...opts, t: performance.now() - t0 });
    canvas.getContext('2d')!.drawImage(c, 0, 0);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
