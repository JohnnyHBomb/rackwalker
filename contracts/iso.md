# Isometric conventions (contract 2.0.0)

Code: `web/src/contracts/iso.ts` (constants and pure functions, unit-tested in `web/tests/iso.test.ts`).

## Tile: 32 x 16 px, 2:1 diamond

* 2:1 gives pixel-exact edges: every edge steps 2 px across per 1 px down, so lines never jitter. This is the
  classic pixel-art isometric (dimetric) projection.
* 32 px wide is small enough for an 8-bit look and large enough for a recognisable rack or building at scale 1.
  Powers of two keep sprite sheets and maths simple.
* The art is drawn at scale 1 and shown at an **integer** render scale (1, 2, 3, 4, 5, 6, 8) with nearest-neighbour
  filtering. A 1080p screen typically uses 3x, 4K uses 6x. Camera zoom between scales is a pan/scale of the
  container by integer steps only, so pixels never blur.
* A 1x1 tile fills 16 rows of widths 4, 8, ..., 32, 32, ..., 8, 4 (see `fillDiamond`).

## Axes and the screen mapping

```
        W (-x)            N (-y)
        screen up-left    screen up-right
                 \        /
                  \      /
                   (0, 0)            <- grid origin = TOP corner of tile (0, 0)
                  /      \
                 /        \
        S (+y)            E (+x)
        screen down-left  screen down-right
```

`isoToScreen(x, y, z) = { sx: (x - y) * 16, sy: (x + y) * 8 - z }` at scale 1, relative to the screen position of
the grid origin. `(x, y)` is the top corner of tile `(x, y)`; `z` is height in screen px. `screenToIso` inverts
it for `z = 0`; `floor()` of the result is the tile under the pointer.

Facing (`iso_facing`) is a **grid** direction: N = -y (screen up-right), E = +x (down-right), S = +y (down-left),
W = -x (up-left). Sprites are drawn facing S (front towards the viewer's lower left); `FACING_ROTATION` gives the
quarter turns. Walls are drawn only on the N and W edges of a room, so they never hide its contents.

## Grids and what iso_x / iso_y mean

| Object | Grid it sits on | iso_x / iso_y | Footprint |
|---|---|---|---|
| Site | overworld (`WORLD_TILES` = 64 square) | from GPS via `geoToWorld` (not stored) | 2 x 2 (`site_building`) |
| Location (room) | site grid | top corner tile of the room | `iso_w` x `iso_h` tiles |
| Rack in a location | that location's grid (origin = the room's top corner) | tile of the rack | 1 x 1 |
| Rack without location | site grid | tile of the rack | 1 x 1 |
| Device | inside its rack (rack front elevation) | not used: NetBox `position` / `face` | `u_height` U |

`null` coordinates mean "not placed": the engine auto-lays them out and does not write them back unless the user
drags. An object counts as not placed when `iso_x` or `iso_y` is `null`. The packing is web-only (the proxy passes
`null` through), so the web engine owns it within these rules, which keep it deterministic:

* Unplaced objects are taken in order of `(name, id)`.
* Racks in a location fill that room's tiles row by row (`y` then `x`, from `(0, 0)`), skipping tiles held by
  placed racks; a room with no free tile overflows into the rows below its `iso_h`. A rack without a location does
  the same on the site grid, in rows 8 tiles wide starting one row below the lowest placed object.
* Locations without `iso_x`/`iso_y` go on the site grid in a row starting one row below the lowest placed object,
  1 tile apart; a `null` `iso_w`/`iso_h` counts as 4 x 4. `iso_facing` `null` counts as `S`.

The fixtures have no unplaced object (every seeded rack and location has coordinates); the web engine covers this path with
its own unit test.

## Depth order

Draw world objects in ascending `(depthKey, x, z, id)` where `depthKey(x, y, w, h) = (x + w - 1) + (y + h - 1)`,
the grid sum of the object's far-most (front-most) tile. An object is drawn after everything wholly behind it.

## Zoom levels (one continuous camera)

| Level | Shows | Data | Space |
|---|---|---|---|
| `world` | sites placed by GPS, inter-site links | `GET /api/scene/world` | world (overworld grid) |
| `site` | rooms (locations) as floor areas, unplaced racks/devices | `GET /api/scene/site/{id}` | world (site grid) |
| `room` | one room's racks on its floor grid, floor cable runs | same site scene, filtered by `location_id` | world (room grid) |
| `rack` | one rack's front (or rear) elevation, 1U = 8 px, 64 px wide faceplates | `GET /api/scene/rack/{id}` | rack |
| `device` | one device's ports and cables, trace on demand | `GET /api/device/{id}` (+ `GET /api/trace/{kind}/{id}`) | rack/screen |

Moving between levels is engine-internal: the contract fixes only what each level shows and fetches, that
the render scale stays an integer, and that the camera centres on the object entered (site building, room, rack,
device). Transition animation and timing are free.

## GPS on the overworld

`geoToWorld(lat, lon, bbox)`: local equirectangular projection around the centre of the sites' bounding box
(east scaled by cos(latitude)), rotated 45 degrees onto the grid so geographic **north is screen-up and east is
screen-right**, and scaled so every site lands inside `[4, 60]`. A single site lands at the centre. This
geographic orientation is separate from the grid facing names above.
