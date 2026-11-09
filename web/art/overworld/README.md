# The overworld: engine, map and art

The overworld is drawn in the 16-bit style of the art lab (`web/art-lab/sixteen/`): 256 x 224 native, 16 x 16
tiles, characters 16 x 24, its 63-colour palette in ramps, light from the top-left, hand dithering, whole-pixel
movement, shown at the largest whole-number scale that fits the window.

![A farm-theme site at 3x](../../../docs/screenshots/overworld/region-farm-3x.png)

Screenshots: `docs/screenshots/overworld/` (`region-<theme>-1x.png` / `-3x.png` for every theme, `country-<code>-3x.png`
(the first landmark, the player in front of it) and `country-<code>-villager-3x.png` for every country set,
`door-transition.png`, `door-inside.png`, `door-back-out.png`, `portrait-pipeline.png`, `walk.gif`, `perf.json`). They are written by
`web/e2e/overworld.spec.ts` (`make e2e`) from the overworld sheet `world.html` (`make dev-fixtures`, then
`http://127.0.0.1:5173/world.html?theme=farm&country=NL&scale=3`).

## How it is built

| Piece | What |
|---|---|
| `author_world.py` | writes `web/src/game/world/assets/` (palette, tiles, sprites). Reuses the art lab's palette, canvas and hand-drawn sprites (`web/art-lab/sixteen/tools/`) by import, so the game and the lab share one pipeline. Deterministic |
| `ground.py` | the ground autotiles (below) |
| `buildings.py` | one building per theme and the Netherlands buildings, all front-facing (below) |
| `props.py` | country landmarks, villagers, the airfield placeholder, the door marker |
| `../portraits/` | the portrait pipeline (below) |
| `web/src/game/world/engine.ts` | the indexed framebuffer: tiles, sprites, palette-remap shadows and glow, the palette applied last |
| `web/src/game/world/render.ts` | a map through the engine: autotile ids per cell once (in the region's ground palette), objects filed in 128 px buckets, so a frame looks only at what is near the camera, sorted by base line |
| `web/src/game/world/themes.ts` | per-theme ground: palette remaps, flowers, how wooded |
| `web/src/game/world/gen.ts` | the map from NetBox data (below) |
| `web/src/game/world/camera.ts` | the camera: follows the player in whole pixels, never leaves the player's region |
| `web/src/game/world/countries.ts` | country sets keyed by ISO 3166-1 alpha-2 code |

```sh
cd web
python3 art/overworld/author_world.py     # rebuild src/game/world/assets/{palette,tiles,sprites}.json
node art/portraits/portrait.mjs           # rebuild src/game/world/assets/portraits.json and portrait-pipeline.png
```

Nothing in the world is drawn with shape or fill calls at run time: every pixel comes from tile and sprite data.

### Ground autotiles

The map is a grid of terrain classes: grass, path, water, bridge (path over water), sand, tulip field, crop field. A
tile's pixels depend only on its class, its four neighbours (reduced to what that class cares about: grass asks
"path, water, sand, field or grass") and a variant, so `ground.py` renders every combination once, through the art
lab's per-pixel rules on a 3 x 3 probe of tiles, and keeps the centre. The engine looks each cell's tile up by that
key. Water and fields have four frames (ripples drift, tulips and the crop sway). Tests check that every key a
generated map uses exists.

Shapes: water and sand are drawn from a field over the tile centres of the probe (the art lab's sand rule), so a
pond, a canal's end or a sand bank has rounded corners inside and out; sand gets a lumpy wobble and spills a pixel
or two onto the grass, water edges stay level for the quay. Paths wobble as much as the art lab's (the grass leans
over in clumps) and fill the inside corner of every turn and junction in a curve. The pond is an irregular blob,
the sand bank a lump, and the canal runs on under the forest border out of the picture.

### Themes on the ground

Each region is drawn in the ground palette of its sites' most common theme (ties: the alphabetically first; a site
without a known theme does not vote; none gives the plain ground). A ground palette is a remap inside the same 63
colours (`author_world.py` `GROUNDS`, applied by `themes.ts`): one table for grass and foliage (trees and flowers
too), one for path and sand earth; water, field soil, buildings and characters keep their colours.

| Theme | Ground |
|---|---|
| `debug` | the art direction's own meadow and earth track |
| `industrial-robotic` | dusty, darker grass; grey gravel paths; tufts and stones; fewer groves |
| `farm` | a warm late-summer meadow; a dry dirt track; buttercups |
| `business` | a smooth mown lawn; pale paving; few flowers; the fewest groves |
| `pagodas-nature` | deep moss green; blue-grey stone flags; violets; the most woodland |

### The map from NetBox data

Same sites, same map, in any order (`web/tests/world-gen.test.ts`).

1. **Regions.** Sites with GPS are joined when they are within 250 km of each other, directly or through a chain
   of sites. Sites without GPS join the lowest-id site's region. Regions are ordered by their lowest site id.
2. **Walking scale.** Each site's offset from its region's centre is compressed logarithmically (6 tiles per
   doubling of distance beyond 10 km): a site 10 km out and one 200 km out are both a short walk. North stays up.
   Longitudes wrap at the 180th meridian. Sites without GPS stand in a square block below the others. Then each
   site, nearest the centre first, takes the free place nearest its own, so buildings never overlap, however many
   sites share one point (200 sites at one point make a region about 200 tiles square).
3. **Filling the space.** A forest border; a canal with a sandy bank, a moored boat and reeds (or a pond); paths
   joining every site of the region (a minimum spanning tree over the doors, routed around buildings, crossing water
   straight as a bridge); fenced fields; the country's landmarks and villagers; groves and single trees; flowers.
   How much goes down scales with the region's area.
4. **Distant regions** sit side by side on one map, but no path joins them and the camera never leaves the player's
   region. Each region of a world with more than one has an **airfield pad, a placeholder**: a label over it names
   where it goes, and stepping on it takes the player to the next region's pad, with a card saying where, until
   flights replace it. A canal that no path crossed gets one crossing, so both banks can be reached.
5. **Collisions.** Buildings (except the door tile), water (except bridges), trees, fields with their fences,
   landmarks, villagers and the guide block the way. Walking onto a door enters that site's first room through the
   game shell's scene switcher; leaving puts the player on the tile in front of the same door.
6. **A saved place after an edit in NetBox.** Adding, moving or deleting a site, or changing its GPS or country,
   regenerates the map. A saved tile is kept only if the player can walk on from it to the region's doors (a reach
   map from every region's arrival tile); otherwise the player is put on the nearest such tile of the same region,
   or at the start.

### Country sets

Picked per region by its sites' most common country (`Site.country`: the site's `iso_country` custom field, else
its region's code or country name, else its address's last line; see `contracts/api.md`). Landmarks, flora, food and weather only;
villagers are native animals, sometimes with one silly prop (a hat, a tool, food or a vehicle).

| Code | Landmarks | Villager | Water, field |
|---|---|---|---|
| `NL` | windmill with turning sails (its brick base under a pantile skirt roof seen from the front, the door full height below it), stepped-gable cottage, cheese cart, bicycles, poplars | a hedgehog wearing its bicycle bell as a hat | canal with bridges, tulips |
| `GB` | red telephone kiosk, pillar box, tea stall with a steaming pot, a small rain cloud | a badger in a bowler hat; a red fox (second villager of a region with more than one site) | pond, yellow crop |
| any other | a well, haystacks | a rabbit holding a carrot | pond, yellow crop |

### Buildings and roofs

Every building shows its front wall (door and windows) to the camera and its roof from the front and a little
above: the eaves edge or a gable end faces the viewer, the roof plane is foreshortened above the wall, the ridge runs
across the top. No roof is a flat plan seen from straight above.

| Theme | Building |
|---|---|
| `debug` | the art lab's data hall walls under a hipped slate roof seen from the front: slate courses run across, the lit west hip and the shaded east hip show as narrow strips, a cooling unit and a mast stand behind the ridge |
| `industrial-robotic` | a brick works with a saw-tooth roof: three gable ends face the viewer, each with a glazed light; a chimney stack, a roller door framed in hazard stripes, a robot-eye lamp |
| `farm` | a red timber barn, gable end to the front under a gambrel roof, white trim, a hay loft with a bale, a silo |
| `business` | a glass office: a curtain wall that catches the sky, a parapet with the flat roof deck seen just above it (plant, a skylight), a canopy over glass doors |
| `pagodas-nature` | a two-tier pagoda hall: plaster and red lacquer posts, two roofs with rolled tiles running down the slope and eaves that sweep up at the corners |

The eaves cast a two-pixel shadow on the wall below them (one ramp step darker), like every cast shadow here.

### Portraits

Ilatea's, Byte's and Nocti's dialogue portraits start as hand-written SVG at 768 x 768 (`../portraits/*.svg`, from the
traits in `docs/art/guide-ilatea.md`), painted the way a 16-bit portrait is: flat tones (lit, base, shade) with a dark
outline, no soft gradients, framed close so the face fills the small sizes (Ilatea's scene is scaled 1.35x about her
face). `portrait.mjs` rasterises each in headless chromium, downscales it with an area filter (exact box averages, 12x
to 64 x 64 and 8x to 96 x 96), maps it to the palette with no dither (ordered dither was tried and is shown in the
sheet: it turned the skin and the background into noise at this size), using only the ramps the portrait is painted
with (a skin tone never lands on path earth), then applies the hand clean-up pass: pixel edits stored as data in
`../portraits/cleanup/<who>-<size>.txt`, one file for every portrait at both sizes, each step headed by its reason.
The format is one edit per line: `x,y colour` sets a pixel, `x0,y0 x1,y1 colour` a rectangle, `## why` starts a step.
The steps: Ilatea's brows as one level line, eyes as lash line / white corner / gold iris / black pupil, the smile with
upturned corners, one clean line per silver strand, holes and grey specks out of the hair, the circlet gem as a lit
diamond; the owls' eyes (iris ring, round pupil, glint), the beak as an outlined triangle with a sharp tip, clean disc
rims and chevrons, the toes, Byte's sheen kept, Nocti's goggles and sceptical lid. `portrait-pipeline.png` shows source,
raw downscale, both palette mappings and the cleaned result.

### Frame times

`web/e2e/overworld.spec.ts` walks the player in the real game at 768 x 672 (3x) with chromium's CPU throttled 4x and
writes `perf.json`: frame interval p95 17.3 ms (60 fps), composing a frame 2.3 ms median, 3.1 ms p95, no layout shift.
A frame looks only at the objects near the camera (`web/tests/world-engine.test.ts` checks that a world of 200 sites
looks at no more than a world of 3): in node, unthrottled, composing a frame of a 200-site world (122,000 objects)
takes 0.6 ms median, as for 3 sites.

## Art notes

Each round: render, look at the 3x screens and close crops, list the five worst weaknesses against the style and the
front-facing roof rule, fix them, render again.

1. **First render** (the industrial site at the start).
   Weaknesses: path edges scalloped in a pattern that repeated every tile, so a long path read as a chain; the door
   marker sat on the door instead of above it; groves of trees stood in exact rows; water ripples formed a regular
   grid; the portraits' ordered dither turned skin and background into noise.
   Fixes: a calmer path wobble; the marker raised above the door frame; four variants of every path, water and sand
   tile, chosen per cell by a hash, with the wobble, pebbles and ripples keyed to the variant; grove trees nudged a
   few pixels off the tile centre and thinned; portraits mapped without dither.
2. **Portraits.**
   Weaknesses: the skin mapped onto path-earth and brick colours (blotchy yellow and orange patches); at 64 px the eyes
   were smudges, the brows and lash lines merged, the nose had no shadow, the mouth did not read; the silver strands
   lost their glint.
   Fixes: each portrait maps only to the ramps it is painted with; the hand clean-up pass on brows, eyes, nose, mouth
   and strands (stored as pixel edits with their reasons).
3. **Country sets and themes** (the Netherlands set, the pagoda).
   Weaknesses: the windmill's new skirt roof was a saturated orange band, brighter than the sails; the rain cloud
   floats with nothing under it; the tulip field's lower fence hides the last row; fields show no soil margin; no path
   leads to the windmill.
   Fixes: the skirt roof darkened one ramp step with a dark lip. The other four are left for later (below).
4. **Door marker, portraits again** (every theme at 3x, the six portraits at 10x with a pixel grid).
   Weaknesses: the door marker, raised above the door frame in round 1, now covered the sign over the door on every
   theme that has one ("DA▼--" on the data hall); Ilatea's face was long and her hair one dark mass; the owls, painted
   with soft gradients, came out mushy, Nocti's dark side was the same colour as its background, and Byte at 96 and
   Nocti at both sizes had no hand pass.
   Fixes: the marker bobs inside the upper half of the doorway, below the sign (a test keeps it there); the three
   sources repainted in flat tones with a dark outline, Ilatea's face shortened, her hair broken into lit clumps with
   one silver strand per side, the scene framed closer; Nocti against a slate-blue workshop wall, with a sceptical
   lid and a screwdriver; a hand clean-up file for every portrait at both sizes.

5. **Roofs, ground, portraits** (every theme at 1x and 3x, NL and GB at 4x, the portraits at 8x).
   Weaknesses: the windmill's skirt roof was painted over the top of its door (a 5 px stub, the knob gone); every
   theme had the same ground; the pond was a square pool, the sand bank a rectangle, paths ruler-straight with
   square inside corners, the canal ended square at the forest; the fields' side fences covered a third of the
   plot; Ilatea's eyes read as half-closed slits, and a straight light/shade line split her forehead.
   Fixes: the skirt roof on rows of its own (everything above the base moved up 4 px); per-theme ground palettes and
   flowers; rounded water and sand shapes, path wobble and curved corners, the canal under the forest; stacked side
   fences; her eyes open (two rows of gold iris, a glint on each pupil), the forehead light on a slant.

## Where it still falls short

- The rain cloud casts no shadow; landmarks are not joined to the paths; the tulip field's lower fence hides its last
  row.
- The pond's outline follows whole tiles, so its curves step at tile corners; the quay stones follow those steps.
- Nothing points at buildings off screen yet (a map and a marker to the next small fix come with the quest log).
- Phones get 1x (256 px wide) because only whole-number scales are used.
- The airfield pad is a placeholder: it moves the player between regions at once, with no flight.
- The portraits have no idle or talk frames yet.
