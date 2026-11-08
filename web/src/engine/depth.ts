// Painter's order for world objects (contracts/iso.md "Depth order").
import { depthKey } from '../contracts/iso';

/** Kinds in tie-break order: flat things before standing things on the same diagonal. */
export const KIND_RANK = { floor: 0, wire: 1, wall: 2, loose: 3, rack: 4, building: 5 } as const;
export type DepthKind = keyof typeof KIND_RANK;

export interface DepthItem {
  /** Footprint top tile and size in tiles; a wall is 1 x 0 (N edge) or 0 x 1 (W edge), as in theme-slots.json. */
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
  /** Height above the floor in screen px. */
  readonly z: number;
  readonly id: number;
  readonly kind: DepthKind;
}

/**
 * Contract order: ascending (depthKey, x, z, id). Ids are unique only per kind, so where the contract tuple leaves
 * a tie that id would settle arbitrarily we first compare the near-corner sum x + y (a larger footprint whose far
 * tile shares the diagonal is further back), then the kind rank, then id. On every input where (depthKey, x, z)
 * already differ this is exactly the contract order.
 */
export function compareDepth(a: DepthItem, b: DepthItem): number {
  return (
    depthKey(a.x, a.y, a.w, a.h) - depthKey(b.x, b.y, b.w, b.h) ||
    a.x - b.x ||
    a.z - b.z ||
    a.x + a.y - (b.x + b.y) ||
    KIND_RANK[a.kind] - KIND_RANK[b.kind] ||
    a.id - b.id
  );
}

/** Stable sort by compareDepth (returns a new array). */
export function sortByDepth<T extends { depth: DepthItem }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => compareDepth(a.depth, b.depth));
}
