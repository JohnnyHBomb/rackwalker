"""Authoring tool for the game's overworld art (the same pipeline as the 16-bit art lab in web/art-lab/sixteen/).

Writes the indexed pixel data the game's tile engine draws, into web/src/game/world/assets/:
  palette.json   the art direction's 63 colours, ramps and the shadow / glow look-up tables
  tiles.json     the ground autotile set: 16 x 16 tiles and the key -> tile table (water and fields animate)
  sprites.json   every sprite and animation frame as rows of palette indices

Reused from the art lab (imported, not copied): the palette and canvas (pix.py), the hand-drawn characters,
trees, sails, props and waterside sprites (author.py). New here: the ground autotiles (ground.py), the
front-facing buildings for every theme (buildings.py), the country landmarks and villagers (props.py).
Deterministic: same input, same bytes.

Run: python3 web/art/overworld/author_world.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.join(HERE, "..", "..", "art-lab", "sixteen", "tools")
sys.path.insert(0, LAB)
sys.path.insert(0, HERE)

import author  # noqa: E402
import buildings  # noqa: E402
import ground  # noqa: E402
import props  # noqa: E402
from pix import ALPH, DARKER, GLOW, LIGHTER, NAMES, PALETTE, RAMPS  # noqa: E402

OUT = os.path.join(HERE, "..", "..", "src", "game", "world", "assets")

# One ground palette per building theme: remaps inside the 63 colours (web/src/game/world/themes.ts applies them to a
# region's ground tiles, trees and flowers). "grass" covers grass and foliage, "earth" path and sand. The plain
# palette (debug) is the art direction's own meadow and earth.
GROUNDS = {
    "debug": {"grass": {}, "earth": {}},
    # a warm late-summer meadow and a dry dirt track
    "farm": {"grass": {"g1": "g2", "g2": "g3", "g4": "g5"},
             "earth": {"d1": "d2", "d2": "d3", "d3": "d4", "d4": "d5"}},
    # dusty, darker grass and grey gravel
    "industrial-robotic": {"grass": {"g4": "g3", "g5": "g4", "g3": "g2", "g2": "g1"},
                           "earth": {"d0": "k1", "d1": "s0", "d2": "s1", "d3": "s2", "d4": "s3", "d5": "s4"}},
    # a smooth mown lawn (no light blade tips, the darkest lifted) and pale paving
    "business": {"grass": {"g1": "g2", "g0": "g1", "g4": "g3"},
                 "earth": {"d1": "s1", "d2": "s2", "d3": "s3", "d4": "s4", "d5": "w"}},
    # deep moss green and blue-grey stone flags
    "pagodas-nature": {"grass": {"g4": "g3", "g5": "g4", "g3": "g2"},
                       "earth": {"d0": "l0", "d1": "l1", "d2": "l2", "d3": "l3", "d4": "s3", "d5": "s4"}},
}

# Art-lab sprites the overworld uses as they are.
FROM_LAB = [
    "player_down", "player_up", "player_left", "player_right", "guide", "guide_talk", "hog",
    "byte_perch", "byte_fly", "nocti_perch", "nocti_fly", "tree_a", "tree_b", "poplar", "bush", "sails",
    "bridge_deck", "bridge_rail", "fence_h", "fence_v", "barrel", "crate", "doormat", "bicycle", "signpost",
    "daisy", "violet", "buttercup", "stone", "stone_big", "tuft", "reeds", "lilypad", "lilypad_flower", "boat",
    "mooring_post", "cable_drum", "condenser",
]


def collect():
    author.make_characters()
    author.make_trees()
    author.make_sails()
    author.make_props()
    author.make_waterside()
    sprites = {n: author.SPRITES[n] for n in FROM_LAB}
    tulips = [author.SPRITES[n]["frames"] for n in ("tulip_red", "tulip_yellow", "tulip_pink")]
    grass = ground_grass = author.grass_variants()

    def add(name, frames, ox=None, oy=None):
        if not isinstance(frames, list):
            frames = [frames]
        f0 = frames[0]
        sprites[name] = {"frames": frames, "ox": f0.w // 2 if ox is None else ox, "oy": f0.h - 1 if oy is None else oy}

    buildings.make_buildings(add, author)
    props.make_props(add)
    del ground_grass
    return sprites, grass, tulips


def main():
    sprites, grass, tulips = collect()
    tiles, table = ground.build_tileset(grass, tulips)
    os.makedirs(OUT, exist_ok=True)
    pal = {"colours": ["#" + hx for _, hx in PALETTE], "names": NAMES, "count": len(PALETTE) - 1,
           "ramps": RAMPS, "darker": DARKER, "lighter": LIGHTER, "glow": GLOW, "alphabet": ALPH, "grounds": GROUNDS}
    with open(os.path.join(OUT, "palette.json"), "w") as f:
        json.dump(pal, f, indent=1)
        f.write("\n")
    with open(os.path.join(OUT, "tiles.json"), "w") as f:
        json.dump({"tile": 16, "frames": 4, "frameMs": 220, "sides": ground.SIDES, "variants": ground.VARIANTS,
                   "tiles": ["".join(ALPH[v] for v in t) for t in tiles], "keys": table}, f, separators=(",", ":"))
        f.write("\n")
    sp = {}
    for name in sorted(sprites):
        s = sprites[name]
        sp[name] = {"w": s["frames"][0].w, "h": s["frames"][0].h, "ox": s["ox"], "oy": s["oy"],
                    "frames": [fr.rows() for fr in s["frames"]]}
    with open(os.path.join(OUT, "sprites.json"), "w") as f:
        json.dump(sp, f, separators=(",", ":"))
        f.write("\n")
    print("tiles", len(tiles), "keys", len(table), "sprites", len(sp))


if __name__ == "__main__":
    main()
