// CONTRACT 2.0.0: isometric conventions. Prose and rationale: contracts/iso.md.
// Change only by contract change request.

/** Tile = 2:1 diamond, 32 x 16 px at render scale 1. */
export const TILE_W = 32;
export const TILE_H = 16;
/** Screen px per tile step along x or y: (+16, +8) for +x, (-16, +8) for +y. */
export const HALF_W = TILE_W / 2;
export const HALF_H = TILE_H / 2;
/** Rack front elevation (zoom 'rack'): 1U = 8 px, faceplate 64 px wide (19-inch). */
export const U_PX = 8;
export const RACK_FACE_W = 64;
/** Integer render scales only (nearest-neighbour; no fractional zoom ever touches the pixels). */
export const RENDER_SCALES = [1, 2, 3, 4, 5, 6, 8] as const;
export type RenderScale = (typeof RENDER_SCALES)[number];

export type Facing = 'N' | 'E' | 'S' | 'W';
/** Grid direction of each facing: N = -y, E = +x, S = +y, W = -x. */
export const FACING_VECTOR: Readonly<Record<Facing, readonly [number, number]>> = {
  N: [0, -1],
  E: [1, 0],
  S: [0, 1],
  W: [-1, 0],
};
/** Quarter turns clockwise on screen from S (the sprites' default facing): S=0, W=1, N=2, E=3. */
export const FACING_ROTATION: Readonly<Record<Facing, 0 | 1 | 2 | 3>> = { S: 0, W: 1, N: 2, E: 3 };

export interface ScreenPoint {
  readonly sx: number;
  readonly sy: number;
}

/**
 * Grid (x, y, z) -> screen px at scale 1, relative to the screen position of grid origin (0, 0, 0).
 * (x, y) is the TOP corner of tile (x, y); z is height in screen px above the floor.
 */
export function isoToScreen(x: number, y: number, z = 0): ScreenPoint {
  return { sx: (x - y) * HALF_W, sy: (x + y) * HALF_H - z };
}

/** Inverse of isoToScreen for z = 0 (fractional grid coordinates; floor() gives the tile under a point). */
export function screenToIso(sx: number, sy: number): { x: number; y: number } {
  return { x: (sy / HALF_H + sx / HALF_W) / 2, y: (sy / HALF_H - sx / HALF_W) / 2 };
}

/**
 * Painter's order key for a world object occupying tiles [x, x+w) x [y, y+h) at height z.
 * Draw in ascending order of (depth, x, z, id). depth = far-most tile sum, so an object is drawn after
 * everything whose footprint lies wholly behind it.
 */
export function depthKey(x: number, y: number, w = 1, h = 1): number {
  return x + w - 1 + (y + h - 1);
}

/** The five zoom levels of the one continuous camera and the API call that feeds each. */
export const ZOOM_LEVELS = ['world', 'site', 'room', 'rack', 'device'] as const;
export type ZoomLevel = (typeof ZOOM_LEVELS)[number];
export const ZOOM_FEED: Readonly<Record<ZoomLevel, string>> = {
  world: 'GET /api/scene/world',
  site: 'GET /api/scene/site/{site_id}',
  room: 'GET /api/scene/site/{site_id} (filtered client-side by location_id)',
  rack: 'GET /api/scene/rack/{rack_id}',
  device: 'GET /api/device/{device_id} (+ GET /api/trace/{kind}/{id} on demand)',
};

/** Overworld grid size in tiles (zoom 'world'); GPS is projected into it, see geoToWorld. */
export const WORLD_TILES = 64;

/**
 * Lat/long -> overworld grid (fractional tiles), so geographic north points screen-up and east screen-right.
 * Local equirectangular projection around the centre of the sites' bounding box, rotated 45 degrees onto
 * the grid and scaled so every site lands inside [4, WORLD_TILES - 4]; a single site lands at the centre.
 */
export function geoToWorld(
  lat: number,
  lon: number,
  bbox: { minLat: number; maxLat: number; minLon: number; maxLon: number },
): { x: number; y: number } {
  const lat0 = (bbox.minLat + bbox.maxLat) / 2;
  const lon0 = (bbox.minLon + bbox.maxLon) / 2;
  const k = Math.cos((lat0 * Math.PI) / 180);
  const spanE = (bbox.maxLon - bbox.minLon) * k;
  const spanN = bbox.maxLat - bbox.minLat;
  const span = Math.max(spanE, spanN);
  const scale = span > 0 ? (WORLD_TILES - 8) / (2 * span) : 0; // |e| + |n| <= (WORLD_TILES - 8) / 2
  const e = (lon - lon0) * k * scale;
  const n = (lat - lat0) * scale;
  // east = screen right = (+x, -y); north = screen up = (-x, -y)
  const c = WORLD_TILES / 2;
  return { x: c + e - n, y: c - e - n };
}
