// Projection helpers on top of the frozen contract (contracts/iso.ts): tiles <-> screen, the inverse used for
// picking, and the rack elevation (1U = 8 px). All at render scale 1, relative to the level's origin.
import { U_PX, isoToScreen, screenToIso, type ScreenPoint } from '../contracts/iso';
import type { Rack } from '../contracts/scene.gen';

export { isoToScreen, screenToIso };

/** Screen position of the TOP corner of tile (tx, ty) (the ground point of world slots). */
export function tileToScreen(tx: number, ty: number): ScreenPoint {
  return isoToScreen(tx, ty);
}

/** Screen position of the centre of a w x h footprint whose top tile is (tx, ty). */
export function footprintCenter(tx: number, ty: number, w = 1, h = 1): ScreenPoint {
  return isoToScreen(tx + w / 2, ty + h / 2);
}

/** The tile under a screen point (z = 0): floor() of the inverse projection. */
export function screenToTile(sx: number, sy: number): { tx: number; ty: number } {
  const g = screenToIso(sx, sy);
  // Tile edges are exact in screen px, so snap tiny float errors before floor().
  return { tx: Math.floor(g.x + 1e-9), ty: Math.floor(g.y + 1e-9) };
}

/** Rack elevation: y (px, from the top of the frame) of the top edge of a device at `position` spanning `uHeight` U. */
export function rackUnitTop(rack: Pick<Rack, 'u_height' | 'starting_unit' | 'desc_units'>, position: number, uHeight: number): number {
  const units = rack.desc_units
    ? position - rack.starting_unit // U1 at the top
    : rack.starting_unit + rack.u_height - (position + uHeight); // U1 at the bottom
  return units * U_PX;
}

/** Inverse of rackUnitTop for a 1U slot: the U number whose slot contains elevation y, or null outside the frame. */
export function rackUnitAt(rack: Pick<Rack, 'u_height' | 'starting_unit' | 'desc_units'>, y: number): number | null {
  const row = Math.floor(y / U_PX);
  if (row < 0 || row >= rack.u_height) return null;
  return rack.desc_units ? rack.starting_unit + row : rack.starting_unit + rack.u_height - 1 - row;
}
