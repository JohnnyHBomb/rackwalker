# Theme and sprite manifest (contract 2.0.0)

Code: `web/src/contracts/theme.ts`. Data mirror: `theme-slots.json` (a test checks they agree).
Placeholder theme: `web/src/themes/debug.ts` (flat coloured boxes) so the engine can render before real themes.

## Interface

```ts
interface Theme { id: ThemeId; name: string; palette: Palette; slots: Record<SlotId, SlotDrawFn> }
type SlotDrawFn = (buf: PixelBuffer, ctx: { palette; spec: SlotSpec; variant: SlotVariant }) => void
interface PixelBuffer { width; height; data: Uint8ClampedArray /* RGBA8, row-major, top-left origin */ }
interface SlotVariant { rotation?: 0|1|2|3; role_color?: '#rrggbb'; wire_color?: '#rrggbb'; status?: string; seed?: number }
```

* A slot function draws into a buffer that the engine allocates at the slot's declared size. It must be pure and
  deterministic (same inputs, same pixels), so themes are golden-image testable without WebGL.
* The engine converts buffers to PIXI textures (nearest-neighbour) and caches them per (theme, slot, variant).
* Hand-drawn packs can replace generated art later through the same slots (a draw function that blits a PNG).
* `renderSlot(theme, slot, variant)` runs a slot at its declared size: what the engine and tests call.

Theme ids: `debug`, `industrial-robotic` (cute), `farm`, `business`, `pagodas-nature`. A site picks its theme via
the `iso_theme` custom field; `null` = app default. All art is original; do not imitate any existing game's art,
characters or names.

## Palette: 16 fixed keys

`background, outline, floor, floor_alt, wall, wall_shade, metal, metal_shade, accent, accent_alt, led_ok,
led_warn, led_err, wire, text, highlight` (all `#rrggbb`). Every theme defines all 16.

## Slots

Anchor = the sprite pixel placed on the object's ground point. World slots: the ground point is the top corner of
the object's footprint (`isoToScreen(x, y)`). Rack slots: the top-left of the U slot on the rack elevation.
Screen slots: the UI origin.

| Slot | Space | Size | Anchor | Footprint | Use |
|---|---|---|---|---|---|
| `site_building` | world | 64x80 | 32,48 | 2x2 | a site on the overworld, 48 px tall |
| `room_floor` | world | 32x16 | 16,0 | 1x1 | one floor tile (`variant.seed` may alternate) |
| `room_wall` | world | 16x40 | 0,32 | N edge | wall on a tile's N edge; mirrored (anchor 16,32) for the W edge |
| `rack_empty` | world | 32x64 | 16,48 | 1x1 | a rack on the room grid, 48 px tall, front facing S |
| `device_1u` | rack | 64x8 | 0,0 | - | generic 1U faceplate tinted by `role_color` |
| `device_2u` | rack | 64x16 | 0,0 | - | generic 2U (3U = 2U + 1U; 5U and up stack 4U) |
| `device_4u` | rack | 64x32 | 0,0 | - | generic 4U |
| `switch` | rack | 64x8 | 0,0 | - | 1U faceplate for role `switch` |
| `router` | rack | 64x8 | 0,0 | - | 1U faceplate for roles `router` and `firewall` |
| `patch_panel` | rack | 64x8 | 0,0 | - | 1U faceplate for role `patch-panel` |
| `psu` | rack | 64x8 | 0,0 | - | role `pdu` when 1U; every zero-U device, rotation 1 (8x64) on the side rail |
| `rack_unit` | rack | 80x8 | 8,0 | - | one U of rack frame (8 px rails + empty slot), stacked `u_height` times under the faceplates |
| `port` | rack | 8x8 | 0,0 | - | one port at device zoom; `variant.status` connected/free/disabled/mgmt, `wire_color` when connected |
| `wire_straight` | world | 32x16 | 16,0 | 1x1 | floor run along +x (rotation 1: along +y), tinted by `wire_color` |
| `wire_corner` | world | 32x16 | 16,0 | 1x1 | corner joining the tile's -x and +y edges at rotation 0; 1 = -x/-y, 2 = +x/-y, 3 = +x/+y (each turns 90 degrees clockwise on screen), as in `theme-slots.json` |
| `wire_cross` | world | 32x16 | 16,0 | 1x1 | x and y runs crossing |
| `decoration_small` | world | 16x16 | 8,7 | 1x1 | small prop at the tile centre |
| `decoration_large` | world | 32x48 | 16,32 | 1x1 | large prop, 32 px tall |
| `ui_frame` | screen | 24x24 | 0,0 | - | 9-slice panel frame (8 px corners) |
| `cursor` | world | 32x16 | 16,0 | 1x1 | tile selector outline |
| `selection_highlight` | world | 34x18 | 17,1 | 1x1 | ring 1 px larger than a tile |

Sprite choice for a device at rack zoom, first match wins:

1. `device_type.u_height` 0 (zero-U, any role, e.g. every seeded PDU) -> `psu` with `variant.rotation` 1, drawn
   vertically on the rack's side rail (left rail for `face: front`, right for `rear`), not in a U slot.
2. 1U: role `switch` -> `switch`, `router`/`firewall` -> `router`, `patch-panel` -> `patch_panel`, `pdu` -> `psu`;
   any other role -> `device_1u`.
3. Otherwise `device_<n>u` by `device_type.u_height` (3U = 2U + 1U; 5U and up stack 4U), tinted by the role colour.
4. A fractional `u_height` (NetBox allows half units: 0.5U, 1.5U) is drawn at its real height, `u_height * 8` px,
   with its bottom edge on `position`: the pieces of the next whole U (rules 2-3 for `ceil(u_height)`) cropped at
   the bottom. Half-U neighbours never overlap.

The rack frame itself is `rack_unit` stacked `u_height` times; the faceplate's top-left sits on its anchor.
