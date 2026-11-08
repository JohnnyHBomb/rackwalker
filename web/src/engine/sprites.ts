// Every pixel the engine shows comes from a theme slot (contracts/theme.md). This module turns sprite
// descriptions into PixelBuffers: plain slots, the engine-side transforms the manifest asks for (mirrored W wall,
// zero-U PSU turned vertical), stacked faceplates (3U = 2U + 1U, 5U+ stack 4U) and the room-level rack sprite with
// its devices shown as small faceplates on the rack's front face. Pure and WebGL-free, so picking and tests use
// exactly the pixels the renderer uploads.
import { U_PX } from '../contracts/iso';
import type { Device, Rack } from '../contracts/scene.gen';
import { createPixelBuffer, renderSlot, type PixelBuffer, type SlotId, type SlotVariant, type Theme } from '../contracts/theme';
import { rackUnitTop } from './projection';

export type SpriteDesc =
  | { readonly kind: 'slot'; readonly slot: SlotId; readonly variant: SlotVariant; readonly mirror?: boolean; readonly turn?: boolean }
  | { readonly kind: 'faceplate'; readonly device: Device }
  | { readonly kind: 'rack'; readonly rack: Rack; readonly devices: readonly Device[]; readonly rotation: 0 | 1 | 2 | 3 }
  | { readonly kind: 'loose'; readonly device: Device };

/** NetBox colours are 6 hex digits without '#'; theme variants take '#rrggbb'. */
export const hexColor = (c: string | null | undefined): string | undefined => (c ? `#${c.toLowerCase()}` : undefined);

/** Faceplate pieces of a device, top to bottom (theme.md "Sprite choice for a device at rack zoom", rules 2-3). */
export function faceplatePieces(device: Device): { slot: SlotId; variant: SlotVariant; y: number }[] {
  const variant: SlotVariant = { role_color: hexColor(device.role.color), status: device.status };
  const u = Math.ceil(device.device_type.u_height);
  if (u === 1) {
    const r = device.role.slug;
    const slot: SlotId =
      r === 'switch' ? 'switch' : r === 'router' || r === 'firewall' ? 'router' : r === 'patch-panel' ? 'patch_panel' : r === 'pdu' ? 'psu' : 'device_1u';
    return [{ slot, variant, y: 0 }];
  }
  const pieces: { slot: SlotId; variant: SlotVariant; y: number }[] = [];
  let left = u;
  let y = 0;
  for (const [n, slot] of [[4, 'device_4u'], [2, 'device_2u'], [1, 'device_1u']] as const) {
    // 3U = 2U + 1U (never 4U); 5U and up take 4U blocks first.
    while (left >= n && !(n === 4 && u < 4)) {
      pieces.push({ slot, variant, y });
      y += n * U_PX;
      left -= n;
    }
  }
  return pieces;
}

export function isZeroU(device: Device): boolean {
  return device.device_type.u_height === 0;
}

/** Copies `src` into `dst` at (dx, dy), skipping transparent pixels. */
export function blit(dst: PixelBuffer, src: PixelBuffer, dx: number, dy: number): void {
  for (let y = 0; y < src.height; y++) {
    const ty = dy + y;
    if (ty < 0 || ty >= dst.height) continue;
    for (let x = 0; x < src.width; x++) {
      const tx = dx + x;
      if (tx < 0 || tx >= dst.width) continue;
      const s = (y * src.width + x) * 4;
      if (src.data[s + 3] === 0) continue;
      const d = (ty * dst.width + tx) * 4;
      for (let k = 0; k < 4; k++) dst.data[d + k] = src.data[s + k]!;
    }
  }
}

export function mirrorX(src: PixelBuffer): PixelBuffer {
  const out = createPixelBuffer(src.width, src.height);
  for (let y = 0; y < src.height; y++)
    for (let x = 0; x < src.width; x++) {
      const s = (y * src.width + x) * 4;
      const d = (y * src.width + (src.width - 1 - x)) * 4;
      for (let k = 0; k < 4; k++) out.data[d + k] = src.data[s + k]!;
    }
  return out;
}

/** Quarter turn clockwise: a W x H buffer becomes H x W. */
export function turnClockwise(src: PixelBuffer): PixelBuffer {
  const out = createPixelBuffer(src.height, src.width);
  for (let y = 0; y < src.height; y++)
    for (let x = 0; x < src.width; x++) {
      const s = (y * src.width + x) * 4;
      const d = (x * out.width + (src.height - 1 - y)) * 4;
      for (let k = 0; k < 4; k++) out.data[d + k] = src.data[s + k]!;
    }
  return out;
}

/** Faceplate height in px: the device's real U height (0.5U = 4 px, 1.5U = 12 px; NetBox allows half units), so
 *  half-U neighbours never overlap. A fractional height crops the pieces of the next whole U (faceplatePieces).
 *  A zero-U device (device zoom only; at rack zoom it is on a rail) gets one unit. */
export function faceplateHeight(device: Device): number {
  const u = device.device_type.u_height;
  return u > 0 ? Math.round(u * U_PX) : U_PX;
}

export function faceplateBuffer(theme: Theme, device: Device): PixelBuffer {
  const pieces = faceplatePieces(device);
  const height = faceplateHeight(device);
  const out = createPixelBuffer(64, height);
  for (const p of pieces) blit(out, renderSlot(theme, p.slot, p.variant), 0, p.y);
  return out;
}

// rack_empty is 32 x 64 with its ground point at (16, 48): a 1 x 1 box 48 px tall. Its two visible side faces are
// fixed by that geometry, whatever the theme draws: the S face (left, columns 0-15) and the E face (right,
// columns 16-31). Column c of the S face starts at row 9 + floor(c / 2), of the E face at row 24 - floor(c / 2).
export const RACK_FACE_ROWS = 48;
const S_FACE_COLS = [2, 13] as const; // a 2 px margin keeps the theme's rack edges visible
const E_FACE_COLS = [18, 29] as const;
const faceTop = (face: 'S' | 'E', c: number) => (face === 'S' ? 9 + (c >> 1) : 24 - (c >> 1));

/** Which visible face shows the rack's front, given its rotation (0 = facing S); null = front faces away. */
export function rackFrontFace(rotation: 0 | 1 | 2 | 3): 'S' | 'E' | null {
  return rotation === 0 ? 'S' : rotation === 3 ? 'E' : null;
}

/** Rows (0-47 down the face) covered by a device on the room-level rack sprite: [top, bottom). */
export function deviceFaceRows(rack: Rack, device: Device): [number, number] {
  const total = rack.u_height * U_PX;
  const u = device.device_type.u_height;
  const top = rackUnitTop(rack, device.position!, u);
  const r0 = Math.floor((top * RACK_FACE_ROWS) / total);
  const r1 = Math.max(r0 + 1, Math.floor(((top + u * U_PX) * RACK_FACE_ROWS) / total));
  return [Math.max(0, r0), Math.min(RACK_FACE_ROWS, r1)];
}

/** Devices drawn on a rack's front: racked (position set), not zero-U, front face or full depth. */
export function frontDevices(devices: readonly Device[]): Device[] {
  return devices.filter((d) => d.position !== null && !isZeroU(d) && (d.face !== 'rear' || d.device_type.is_full_depth));
}

/** The pixel (sprite coordinates) of the rack sprite that shows face row `row` at face column index `i`. */
export function rackFacePixel(face: 'S' | 'E', i: number, row: number): { x: number; y: number } {
  const c = (face === 'S' ? S_FACE_COLS[0] : E_FACE_COLS[0]) + i;
  return { x: c, y: faceTop(face, c) + row };
}
export const RACK_FACE_WIDTH = S_FACE_COLS[1] - S_FACE_COLS[0] + 1;

export function rackBuffer(theme: Theme, rack: Rack, devices: readonly Device[], rotation: 0 | 1 | 2 | 3): PixelBuffer {
  const out = renderSlot(theme, 'rack_empty', { rotation, status: rack.status });
  const face = rackFrontFace(rotation);
  if (!face) return out;
  for (const d of frontDevices(devices)) {
    const plate = faceplateBuffer(theme, d);
    const [r0, r1] = deviceFaceRows(rack, d);
    const rows = r1 - r0;
    // Nearest-neighbour downscale of the faceplate onto the face, sheared along the face's 2:1 edge.
    for (let i = 0; i < RACK_FACE_WIDTH; i++)
      for (let r = 0; r < rows; r++) {
        const sx = Math.floor(((i + 0.5) * plate.width) / RACK_FACE_WIDTH);
        const sy = Math.floor(((r + 0.5) * plate.height) / rows);
        const s = (sy * plate.width + sx) * 4;
        if (plate.data[s + 3] === 0) continue;
        const p = rackFacePixel(face, i, r0 + r);
        const o = (p.y * out.width + p.x) * 4;
        for (let k = 0; k < 4; k++) out.data[o + k] = plate.data[s + k]!;
      }
  }
  return out;
}

// An unracked device on the floor: a short box cut from the theme's rack_empty (its top face and lower edge, the
// middle of the side faces removed) with the device's own faceplate on the S face. It reads as equipment, not as a
// decoration prop (previously: nd-srv90/91 were drawn with decoration_small, a water cooler and a potted plant).
/** Box height in px (rack_empty is 48): 16 for a device up to 2U, 24 above. */
export function looseBoxRows(device: Device): number {
  return device.device_type.u_height > 2 ? 24 : 16;
}

export function looseBuffer(theme: Theme, device: Device): PixelBuffer {
  const src = renderSlot(theme, 'rack_empty', { rotation: 0, status: device.status });
  const rows = looseBoxRows(device);
  const cut = RACK_FACE_ROWS - rows;
  const out = createPixelBuffer(src.width, src.height - cut);
  for (let x = 0; x < src.width; x++) {
    // Above the middle of the side face: copied as is; below it: shifted up by `cut` rows.
    const keep = (x < 16 ? faceTop('S', x) : faceTop('E', x)) + (rows >> 1);
    for (let y = 0; y < out.height; y++) {
      const sy = y < keep ? y : y + cut;
      const si = (sy * src.width + x) * 4, di = (y * out.width + x) * 4;
      for (let k = 0; k < 4; k++) out.data[di + k] = src.data[si + k]!;
    }
  }
  const plate = faceplateBuffer(theme, device);
  const faceRows = rows - 2; // a 1 px margin of the box above and below
  for (let i = 0; i < RACK_FACE_WIDTH; i++)
    for (let r = 0; r < faceRows; r++) {
      const sx = Math.floor(((i + 0.5) * plate.width) / RACK_FACE_WIDTH);
      const sy = Math.floor(((r + 0.5) * plate.height) / faceRows);
      const si = (sy * plate.width + sx) * 4;
      if (plate.data[si + 3] === 0) continue;
      const p = rackFacePixel('S', i, r + 1);
      const o = (p.y * out.width + p.x) * 4;
      for (let k = 0; k < 4; k++) out.data[o + k] = plate.data[si + k]!;
    }
  return out;
}

/** Cache key: equal keys mean identical pixels for one theme. */
export function spriteKey(d: SpriteDesc): string {
  switch (d.kind) {
    case 'slot':
      return `slot|${d.slot}|${JSON.stringify(d.variant)}|${d.mirror ? 'm' : ''}${d.turn ? 't' : ''}`;
    case 'faceplate':
      return `face|${d.device.device_type.u_height}|${d.device.role.slug}|${d.device.role.color}|${d.device.status}`;
    case 'loose':
      return `loose|${d.device.device_type.u_height}|${d.device.role.slug}|${d.device.role.color}|${d.device.status}`;
    case 'rack': {
      const parts = frontDevices(d.devices).map((x) => `${x.position}/${x.device_type.u_height}/${x.role.slug}/${x.role.color}/${x.status}`);
      return `rack|${d.rack.u_height}|${d.rack.starting_unit}|${d.rack.desc_units}|${d.rack.status}|${d.rotation}|${parts.sort().join(',')}`;
    }
  }
}

export function buildSprite(theme: Theme, d: SpriteDesc): PixelBuffer {
  switch (d.kind) {
    case 'slot': {
      let b = renderSlot(theme, d.slot, d.variant);
      if (d.mirror) b = mirrorX(b);
      if (d.turn) b = turnClockwise(b);
      return b;
    }
    case 'faceplate':
      return faceplateBuffer(theme, d.device);
    case 'rack':
      return rackBuffer(theme, d.rack, d.devices, d.rotation);
    case 'loose':
      return looseBuffer(theme, d.device);
  }
}

/** Per-theme cache of sprite buffers (the renderer keeps a matching texture cache keyed the same way). */
export class SpriteCache {
  private readonly buffers = new Map<string, PixelBuffer>();
  constructor(readonly theme: Theme) {}
  get(d: SpriteDesc): PixelBuffer {
    const k = spriteKey(d);
    let b = this.buffers.get(k);
    if (!b) {
      b = buildSprite(this.theme, d);
      this.buffers.set(k, b);
    }
    return b;
  }
  get size(): number {
    return this.buffers.size;
  }
}
