"""Authoring tool for the 16-bit art direction.

Writes the indexed pixel data the art lab page draws:
  assets/palette.json  colours, ramps and the shadow / glow look-up tables
  assets/tiles.json    16x16 tile set + tile map (4 water frames)
  assets/sprites.json  every sprite and animation frame as indexed pixel rows
  assets/scene.json    the test screen: objects, positions, animations, shadows

Hand-drawn sprites come from sprites.txt. Terrain, trees, buildings, the windmill and props are drawn
here pixel by pixel with material ramps. Nothing is random: every run gives the same bytes.

Run: python3 tools/author.py   (from web/art-lab/sixteen)
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pix import (ALPH, DARKER, GLOW, IDX, LIGHTER, NAMES, PALETTE, RAMPS, Img, dith,  # noqa: E402
                 from_rows, hsh, rnd)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")
W, H, T = 256, 224, 16
TW, TH = W // T, H // T

SPRITES = {}   # name -> {"frames": [Img], "ox": anchor x, "oy": anchor y}


def add_sprite(name, frames, ox=None, oy=None):
    if isinstance(frames, Img):
        frames = [frames]
    f0 = frames[0]
    SPRITES[name] = {"frames": frames, "ox": f0.w // 2 if ox is None else ox,
                     "oy": f0.h - 1 if oy is None else oy}


# ---------------------------------------------------------------- hand-drawn sprites
def parse_sprite_text(path):
    legends, sprites = {}, {}
    cur_leg = cur_spr = None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.startswith("#"):
            continue
        if line.startswith("@legend"):
            cur_leg = line.split()[1]
            legends[cur_leg] = {}
            cur_spr = None
            continue
        if line.startswith("=="):
            _, name, leg = line.split()
            cur_spr = (name, leg)
            sprites[name] = (leg, [])
            cur_leg = None
            continue
        if not line.strip():
            cur_spr = None if cur_spr else cur_spr
            continue
        if cur_spr:
            sprites[cur_spr[0]][1].append(line)
        elif cur_leg:
            ch, pal = line.split()
            legends[cur_leg][ch] = pal
    out = {}
    for name, (leg, rows) in sprites.items():
        w = max(len(r) for r in rows)
        rows = [r.ljust(w, ".") for r in rows]
        out[name] = from_rows(rows, legends[leg])
    return out


HAND = parse_sprite_text(os.path.join(HERE, "sprites.txt"))


def compose_player():
    """4 directions x 4 frames. Frames 1 and 3 are strides: legs apart, upper body 1 px lower."""
    legs = {"down": ["legs_down_0", "legs_down_1", "legs_down_0", "legs_down_3"],
            "up": ["legs_down_0", "legs_down_3", "legs_down_0", "legs_down_1"],
            "right": ["legs_right_0", "legs_right_1", "legs_right_0", "legs_right_3"]}
    tops = {"down": "player_down_top", "up": "player_up_top", "right": "player_right_top"}
    out = {}
    for d in ("down", "up", "right"):
        frames = []
        for f in range(4):
            img = Img(16, 24)
            bob = 1 if f in (1, 3) else 0
            img.blit(HAND[legs[d][f]], 0, 20)
            top = HAND[tops[d]]
            img.blit(top, 0, bob)
            # arm swing: on strides the hands move 1 px against the leading leg
            frames.append(img)
        out[d] = frames
    out["left"] = [f.mirror() for f in out["right"]]
    for d, frames in out.items():
        add_sprite("player_" + d, frames, 8, 23)


# ---------------------------------------------------------------- terrain
def grass_variants():
    """Four seamless 16x16 grass tiles. Blades are small dark 'v' strokes with a lit tip above-left."""
    vs = []
    for v in range(4):
        t = Img(16, 16, IDX["g3"])
        # broad soft value patches (dithered sparingly) so a field of tiles does not grid
        for y in range(16):
            for x in range(16):
                p = 0.5 + 0.5 * math.sin((x + v * 5) * 0.39 + 0.8) * math.cos((y + v * 3) * 0.39)
                pass
        n = [5, 6, 4, 7][v]
        for i in range(n):
            bx = hsh(v, i, 1) % 16
            by = hsh(v, i, 2) % 16
            kind = hsh(v, i, 3) % 3
            if kind == 0:   # 'v' tuft
                pts = [(0, 0, "g2"), (2, 0, "g2"), (1, 1, "g2"), (0, -1, "g4"), (2, -1, "g4")]
            elif kind == 1:  # single blade pair
                pts = [(0, 0, "g2"), (1, 0, "g1"), (0, -1, "g4")]
            else:           # three blades
                pts = [(0, 0, "g2"), (2, 0, "g2"), (4, 0, "g2"), (1, 1, "g2"), (3, 1, "g2"),
                       (0, -1, "g4"), (2, -1, "g4"), (4, -1, "g4")]
            for dx, dy, c in pts:
                t.set((bx + dx) % 16, (by + dy) % 16, c)
        if v == 1:   # lit clusters: short two-pixel glints
            for i in range(4):
                gx, gy = hsh(11, i) % 16, hsh(12, i) % 16
                t.set(gx, gy, "g4"); t.set((gx + 1) % 16, gy, "g4"); t.set(gx, (gy + 1) % 16, "g3")
        if v == 2:   # clover: three-leaf dark clusters with a lit leaf
            for i in range(3):
                gx, gy = hsh(13, i) % 16, hsh(14, i) % 16
                for dx, dy, c in ((0, 0, "g2"), (1, 0, "g2"), (0, 1, "g2"), (1, 1, "g1"), (-1, 1, "g2"), (0, -1, "g4")):
                    t.set((gx + dx) % 16, (gy + dy) % 16, c)
        if v == 3:
            for i in range(3):
                t.set(hsh(9, i) % 16, hsh(8, i) % 16, "g5")
        vs.append(t)
    return vs


def tile_grid(rows):
    return [[ch for ch in r] for r in rows]


MAP = [
    # 0123456789012345
    "........HHHHHHHH",  # 0
    "........HHHHHHHH",  # 1
    ".........HHHHHHH",  # 2
    ".........HHHHHHH",  # 3
    ".........HHHHHHH",  # 4
    "...........P....",  # 5
    "...........P.FFF",  # 6
    "...P....P..P.FFF",  # 7
    "..PPPPPPPPPP.FFF",  # 8
    "......P.........",  # 9
    "WWWWWWWWWWWWWWWW",  # 10
    "WWWWWWWWWWWWWWWW",  # 11
    "FFFF..P.SSSSFFFF",  # 12
    "FFFF..P.....FFFF",  # 13
]
# path on the plateau up to the windmill door
HIGH_PATH = {(11, 4), (12, 4), (13, 4)}
FACE = 16          # cliff face height in pixels


def field_from(pred, thresh, wob=0.08):
    """Centre-node bilinear field: tiles are nodes at their centres, so tile-aligned shapes come out
    with rounded corners. A wobble that repeats every 16 px keeps identical tile situations identical,
    so the edge tiles form a reusable set."""
    nodes = [[1.0 if pred(tx, ty) else 0.0 for tx in range(TW)] for ty in range(TH)]

    def node(tx, ty):
        tx = max(0, min(TW - 1, tx))
        ty = max(0, min(TH - 1, ty))
        return nodes[ty][tx]

    out = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            fx = (x - 7.5) / 16.0
            fy = (y - 7.5) / 16.0
            x0, y0 = math.floor(fx), math.floor(fy)
            ax, ay = fx - x0, fy - y0
            v = (node(x0, y0) * (1 - ax) * (1 - ay) + node(x0 + 1, y0) * ax * (1 - ay)
                 + node(x0, y0 + 1) * (1 - ax) * ay + node(x0 + 1, y0 + 1) * ax * ay)
            lx, ly = x % 16, y % 16
            n = wob * (math.sin(lx * 0.785 + 1.1) * 0.6 + math.sin(ly * 0.785 + 2.3) * 0.6
                       + ((hsh(lx, ly, 77) % 100) / 100.0 - 0.5) * 0.5)
            out[y][x] = v + n > thresh
    return out


def path_network(is_path, half=6.0):
    """Paths are drawn as a network: every pair of neighbouring path tiles is joined centre to centre,
    and a pixel is path if it lies within `half` px of a joint (round caps and joins, constant width)."""
    nodes = {(tx, ty) for ty in range(TH) for tx in range(TW) if is_path(tx, ty)}
    segs = []
    for (tx, ty) in nodes:
        segs.append(((tx, ty), (tx, ty)))
        for dx, dy in ((1, 0), (0, 1)):
            if (tx + dx, ty + dy) in nodes:
                segs.append(((tx, ty), (tx + dx, ty + dy)))
    out = [[False] * W for _ in range(H)]
    for (a, b) in segs:
        ax, ay = a[0] * 16 + 7.5, a[1] * 16 + 7.5
        bx, by = b[0] * 16 + 7.5, b[1] * 16 + 7.5
        for y in range(int(min(ay, by) - half - 3), int(max(ay, by) + half + 4)):
            for x in range(int(min(ax, bx) - half - 3), int(max(ax, bx) + half + 4)):
                if not (0 <= x < W and 0 <= y < H):
                    continue
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                t = 0 if L2 == 0 else max(0, min(1, ((x - ax) * vx + (y - ay) * vy) / L2))
                px, py = ax + t * vx - x, ay + t * vy - y
                d = math.sqrt(px * px + py * py)
                lx, ly = x % 16, y % 16
                wob = 0.9 * math.sin(lx * 0.785 + ly * 0.39 + 0.7) + 0.6 * ((hsh(lx, ly, 3) % 100) / 100 - 0.5)
                if d <= half + wob:
                    out[y][x] = True
    return out


def build_ground():
    g = tile_grid(MAP)
    high = field_from(lambda x, y: g[y][x] == "H", 0.5, 0.06)
    path = path_network(lambda x, y: g[y][x] == "P" or (x, y) in HIGH_PATH or (x, y) in ((6, 10), (6, 11)))
    water = field_from(lambda x, y: g[y][x] == "W", 0.5, 0.05)
    sand = field_from(lambda x, y: g[y][x] in "S", 0.42, 0.1)
    field = field_from(lambda x, y: g[y][x] == "F", 0.55, 0.0)
    gv = grass_variants()
    for i, t in enumerate(gv):
        add_sprite("grass_variant_%d" % i, t, 0, 0)

    cls = [[None] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if high[y][x]:
                c = "hpath" if path[y][x] else "hgrass"
            else:
                k = next((k for k in range(1, FACE + 1) if y - k >= 0 and high[y - k][x]), 0)
                if k:
                    c = "steps" if path[y][x] else "face"
                elif water[y][x]:
                    c = "water"
                elif path[y][x] and not (96 <= x < 112 and 150 <= y < 200):
                    c = "path"
                elif sand[y][x]:
                    c = "sand"
                elif field[y][x]:
                    c = "field"
                else:
                    c = "grass"
            cls[y][x] = c

    def C(x, y):
        if 0 <= x < W and 0 <= y < H:
            return cls[y][x]
        return cls[min(H - 1, max(0, y))][min(W - 1, max(0, x))]

    def face_k(x, y):
        return next((k for k in range(1, FACE + 1) if y - k >= 0 and high[y - k][x]), 0)

    def j_ok(x, y):
        """Foot-of-cliff shadow: two solid rows, the third dithered."""
        near = C(x, y - 1) in ("face", "steps") or C(x, y - 2) in ("face", "steps")
        return near or dith(x, y, 0.5)

    frames = []
    for fr in range(4):
        img = Img(W, H)
        for y in range(H):
            for x in range(W):
                c = cls[y][x]
                tx, ty = x // 16, y // 16
                lx, ly = x % 16, y % 16
                if c in ("grass", "hgrass"):
                    v = hsh(tx, ty, 5) % 7
                    v = [0, 0, 1, 1, 2, 3, 0][v]
                    col = gv[v].name(lx, ly)
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    lower = ("path", "sand", "field", "water")
                    if c == "hgrass":
                        if lf not in ("hgrass", "hpath") or rt not in ("hgrass", "hpath"):
                            col = "g1"
                        elif up not in ("hgrass", "hpath"):
                            col = "g4"
                    if dn in lower or rt in lower or lf in lower or up in lower:
                        col = "g2"
                    if dn == "path" and C(x, y + 2) == "path" and hsh(x, y, 3) % 3 == 0:
                        col = "g4"   # a lit blade tip leaning over the path edge
                    if c == "grass" and any(C(x, y - j) in ("face", "steps") for j in (1, 2, 3)) and (j_ok(x, y)):
                        col = NAMES[DARKER[IDX[col]]]  # foot-of-cliff shadow
                    if c == "grass" and C(x + 1, y) in ("hgrass",) or C(x + 2, y) == "hgrass" and c == "grass":
                        col = NAMES[DARKER[IDX[col]]]
                elif c in ("path", "hpath"):
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    soft = ("grass", "hgrass", "field", "sand")
                    hv = hsh(x // 3, y // 2, 11) % 100
                    col = "d3"
                    if hv < 10:
                        col = "d2"
                    elif hv < 16:
                        col = "d4"
                    # pebbles: a lit pixel with its shadow below-right
                    if hsh(x, y, 21) % 61 == 0:
                        col = "d5"
                    if hsh(x - 1, y - 1, 21) % 61 == 0:
                        col = "d2"
                    if up in soft:
                        col = "d1"
                    elif C(x, y - 2) in soft:
                        col = "d2"
                    elif lf in soft:
                        col = "d2"
                    elif dn in soft or rt in soft:
                        col = "d4"
                    if up in soft and hsh(x, y, 4) % 4 == 0:
                        col = "g2"   # blades overhanging the top edge
                elif c == "water":
                    # distance up to land (the north bank shows its stone quay face)
                    du = next((k for k in range(1, 10) if C(x, y - k) != "water"), 99)
                    dd = next((k for k in range(1, 4) if C(x, y + k) != "water"), 99)
                    dl = next((k for k in range(1, 3) if C(x - k, y) != "water"), 99)
                    dr = next((k for k in range(1, 3) if C(x + k, y) != "water"), 99)
                    col = "b2"
                    ph = (fr * 2)
                    # long slow swell bands and short lit ripples, both drifting with the frame
                    # ripples: short lit dashes on staggered rows, a dark trough under each; they drift
                    # one pixel a frame and the brightest pixel moves along the dash (the shimmer)
                    row = y % 5
                    lane = y // 5
                    drift = fr if lane % 2 else -fr
                    xx = x + drift + hsh(lane, 1) % 32
                    seg = xx // 20
                    u = xx % 20
                    dash = 3 + hsh(seg, lane, 2) % 4
                    on = hsh(seg, lane, 3) % 3 != 0
                    if row == 0 and on and u < dash:
                        col = "b4" if u == fr % dash else "b3"
                    elif row == 1 and on and 1 <= u < dash - 1:
                        col = "b1"
                    if row == 0 and on and u == fr % dash and hsh(seg, lane, fr) % 6 == 0:
                        col = "b5"
                    if du <= 6 and C(x, y - du) not in ("sand",):
                        # quay: coping stone, then four courses of stone blocks, then the dark waterline
                        if du == 1:
                            col = "s4" if (x % 8) else "s2"
                        elif du == 2:
                            col = "s2" if (x % 8) else "s1"
                        else:
                            course = (du - 3) // 2
                            off = 4 if course % 2 else 0
                            if (du - 3) % 2 == 1:
                                col = "s0"
                            elif (x + off) % 8 == 0:
                                col = "s0"
                            else:
                                col = "s1" if (x + off) % 8 > 4 else "s2"
                            if du == 6:
                                col = "b0"
                    elif du in (7, 8):
                        col = "b1" if (du == 7 or dith(x, y, 0.5)) else col
                    elif dd == 1 or dl == 1 or dr == 1:
                        col = "b5" if (x + y + fr) % 4 < 2 else "b4"
                    elif dd == 2:
                        col = "b4" if (x + fr) % 5 == 0 else "b3"
                    if 117 <= x <= 120 and du > 6:
                        col = NAMES[DARKER[IDX[col]]]   # the bridge's shadow on the water
                elif c == "sand":
                    col = "d4"
                    if hsh(x, y, 31) % 13 == 0:
                        col = "d3"
                    elif hsh(x, y, 32) % 11 == 0:
                        col = "d5"
                    if C(x, y + 1) == "water" or C(x - 1, y) == "water" or C(x + 1, y) == "water":
                        col = "d3"
                    if C(x, y + 2) == "water" and dith(x, y, 0.5):
                        col = "d3"
                    if C(x, y - 1) in ("grass",):
                        col = "g2" if dith(x, y, 0.5) else "d3"
                elif c == "field":
                    r = y % 6
                    col = ["d1", "d2", "d3", "d3", "d2", "d2"][r]
                    if r == 2 and hsh(x, y, 41) % 5 == 0:
                        col = "d4"
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    if up != "field" or lf != "field":
                        col = "d1"
                    elif dn != "field" or rt != "field":
                        col = "d2"
                elif c == "face":
                    k = face_k(x, y)
                    lip = 2 + hsh(x // 2, 17) % 3          # ragged grass overhang, 2-4 px
                    if k <= lip:
                        col = "g3" if k == 1 else ("g2" if k < lip else "g1")
                    else:
                        # earth face, lit from above: lighter near the top, darker toward the foot
                        e = (k - lip) / (FACE - lip)
                        col = "d2" if e < 0.35 else ("d1" if e < 0.8 else "d0")
                        if hsh(x, y, 23) % 5 == 0 and e < 0.8:
                            col = "d2" if col == "d1" else "d3"
                        # stones bedded in the earth: lit top-left pixel, dark lower edge
                        sx, sy = x // 7, (k + (x // 7) % 2 * 3) // 6
                        if hsh(sx, sy, 29) % 3 == 0:
                            ux, uy = x % 7, (k + (x // 7) % 2 * 3) % 6
                            if 1 <= ux <= 5 and 1 <= uy <= 4:
                                col = "s3" if (ux + uy) <= 3 else ("s2" if uy < 4 else "s0")
                        if k == FACE:
                            col = "d0"
                    if C(x - 1, y) not in ("face", "steps") and k > lip:
                        col = "d2"
                    if C(x + 1, y) not in ("face", "steps") and k > lip:
                        col = "d0"
                elif c == "steps":
                    k = face_k(x, y)
                    s = (k - 1) % 4
                    col = ["s3", "s2", "s1", "s0"][s]
                    if C(x - 1, y) != "steps" or C(x - 2, y) != "steps":
                        col = "d2"   # earth cheeks either side of the flight
                    if C(x + 1, y) != "steps" or C(x + 2, y) != "steps":
                        col = "d0"
                    if k == 1 and hsh(x, 3) % 2:
                        col = "g2"
                img.set(x, y, col)
        frames.append(img)
    return frames, cls


def slice_tiles(frames):
    tiles, index = [], {}
    maps = []
    for img in frames:
        m = []
        for ty in range(TH):
            for tx in range(TW):
                key = tuple(img.px[(ty * 16 + y) * W + tx * 16 + x] for y in range(16) for x in range(16))
                if key not in index:
                    index[key] = len(tiles)
                    tiles.append(key)
                m.append(index[key])
        maps.append(m)
    return tiles, maps


# ---------------------------------------------------------------- foliage
def canopy(w, h, clumps, trunk=None, seed=0, ramp_names=("t0", "g0", "g1", "g2", "g3", "g4", "g5")):
    """Layered canopy: each clump is a lit dome; the clump nearest the viewer owns the pixel. Edges
    between clumps get a dark seam so the layers read, the outline is dark only on the shadow side."""
    img = Img(w, h)
    if trunk:
        tx, ty, tw, th = trunk
        for y in range(ty, ty + th):
            for x in range(tx, tx + tw):
                u = (x - tx) / max(1, tw - 1)
                col = "wd3" if u < 0.25 else ("wd2" if u < 0.6 else "wd1")
                if hsh(x, y, seed) % 5 == 0:
                    col = "wd1"
                if x == tx + tw - 1:
                    col = "wd0"
                img.set(x, y, col)
        # root flare
        img.set(tx - 1, ty + th - 1, "wd1")
        img.set(tx + tw, ty + th - 1, "wd0")
        img.set(tx - 1, ty + th - 2, "wd2")
    owner = [[-1] * w for _ in range(h)]
    zbuf = [[-9.0] * w for _ in range(h)]
    lit = [[0.0] * w for _ in range(h)]
    L = (-0.55, -0.75, 0.42)
    ln = math.sqrt(sum(v * v for v in L))
    for i, (cx, cy, r, zb) in enumerate(clumps):
        for y in range(int(cy - r - 1), int(cy + r + 2)):
            for x in range(int(cx - r - 1), int(cx + r + 2)):
                if not (0 <= x < w and 0 <= y < h):
                    continue
                dx, dy = (x + 0.5 - cx) / r, (y + 0.5 - cy) / r
                # leafy scalloped rim
                ang = math.atan2(dy, dx)
                rim = 1.0 + 0.09 * math.sin(ang * 7 + i * 1.7)
                d2 = dx * dx + dy * dy
                if d2 > rim * rim:
                    continue
                nz = math.sqrt(max(0.0, 1 - min(1.0, d2)))
                z = zb + nz * r * 0.5 - dy * 2.0
                if z > zbuf[y][x]:
                    zbuf[y][x] = z
                    owner[y][x] = i
                    li = (dx * L[0] + dy * L[1] + nz * L[2]) / ln
                    # leaf clusters: small bumps lit on their own top-left
                    bump = 0.16 * math.sin(x * 1.9 + y * 0.7 + i) * math.cos(y * 1.6 - x * 0.4)
                    lit[y][x] = li + bump
    for y in range(h):
        for x in range(w):
            i = owner[y][x]
            if i < 0:
                continue
            v = lit[y][x]
            lvl = 2 + v * 4.2
            # dither only between the two lightest steps
            if 4.4 < lvl < 5.0:
                lvl = 5 if dith(x, y, (lvl - 4.4) / 0.6) else 4
            lvl = int(max(1, min(5, lvl)))
            col = ramp_names[lvl]
            # seam between clumps: darker where this clump sits under its neighbour above
            up = owner[y - 1][x] if y > 0 else -1
            lf = owner[y][x - 1] if x > 0 else -1
            if up >= 0 and up != i and zbuf[y - 1][x] > zbuf[y][x]:
                col = ramp_names[1]
            elif lf >= 0 and lf != i and zbuf[y][x - 1] > zbuf[y][x] and lvl < 4:
                col = ramp_names[2]
            img.set(x, y, col)
    # selective outline
    out = img.copy()
    for y in range(h):
        for x in range(w):
            if owner[y][x] >= 0 or img.get(x, y):
                continue
            n = [(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)]
            if any(0 <= a < w and 0 <= b < h and owner[b][a] >= 0 for a, b in n):
                below_or_right = (y > 0 and owner[y - 1][x] >= 0) or (x > 0 and owner[y][x - 1] >= 0)
                out.set(x, y, ramp_names[0] if below_or_right else ramp_names[1])
    return out


def make_trees():
    t1 = canopy(32, 42, [(16, 24, 11, 0), (8, 18, 7.5, 1), (24, 18, 7.5, 1), (11, 11, 7, 2),
                         (21, 11, 7, 2), (16, 7, 6, 3), (16, 18, 8, 3)],
                trunk=(14, 30, 5, 11), seed=1)
    t2 = canopy(32, 42, [(15, 25, 10, 0), (22, 20, 8, 1), (9, 19, 7, 1), (17, 12, 8, 2),
                         (10, 10, 5.5, 3), (23, 11, 5.5, 2), (16, 19, 7, 3)],
                trunk=(13, 31, 5, 10), seed=2)
    # a slim poplar, typical along Dutch dykes
    t3 = canopy(18, 44, [(9, 30, 6.5, 0), (9, 22, 6.5, 1), (9, 14, 6, 2), (9, 7, 4.5, 3)],
                trunk=(8, 34, 3, 9), seed=3)
    bush = canopy(18, 13, [(6, 8, 5, 0), (12, 8, 5, 0), (9, 5, 5, 1)], seed=4)
    add_sprite("tree_a", t1, 16, 40)
    add_sprite("tree_b", t2, 15, 40)
    add_sprite("poplar", t3, 9, 42)
    add_sprite("bush", bush, 9, 12)


# ---------------------------------------------------------------- tiny pixel font for signs
FONT = {
    "D": ["110", "101", "101", "101", "110"], "A": ["010", "101", "111", "101", "101"],
    "T": ["111", "010", "010", "010", "010"], "C": ["011", "100", "100", "100", "011"],
    "1": ["010", "110", "010", "010", "111"], "R": ["110", "101", "110", "101", "101"],
}


def text(img, s, x, y, col, shadow=None):
    for ch in s:
        g = FONT.get(ch)
        if g:
            for j, row in enumerate(g):
                for i, b in enumerate(row):
                    if b == "1":
                        if shadow:
                            img.set(x + i + 1, y + j + 1, shadow)
                        img.set(x + i, y + j, col)
        x += 4


# ---------------------------------------------------------------- buildings
def make_datacentre():
    """A small brick-and-render data hall: hipped slate roof seen from above, plinth, pilasters, sign,
    double door with a step, windows that show a lit rack inside, rooftop cooling unit and a mast."""
    w, h = 84, 86
    img = Img(w, h)
    wall_top, wall_bot = 50, 85
    x0, x1 = 3, 80
    # walls
    for y in range(wall_top, wall_bot + 1):
        for x in range(x0, x1 + 1):
            col = "s4"
            if (x * 3 + y * 7) % 23 == 0 or hsh(x, y, 2) % 41 == 0:
                col = "s3"  # render texture
            if y < wall_top + 3:
                col = "s2" if y < wall_top + 2 else "s3"  # shadow under the eaves
            # brick plinth
            if y >= wall_bot - 6:
                row = (wall_bot - y) // 2
                off = 2 if row % 2 else 0
                col = "r2"
                if (wall_bot - y) % 2 == 1:
                    col = "r1" if (x + off) % 6 else "r0"
                elif (x + off) % 6 == 0:
                    col = "r1"
                elif (x + off) % 6 == 1:
                    col = "r3"
                if y == wall_bot - 6:
                    col = "s2"  # plinth capping
            # brick pilasters at the corners
            if x - x0 < 5 or x1 - x < 5:
                row = (y - wall_top) // 3
                off = 2 if row % 2 else 0
                col = "r3" if (x - x0 < 5) else "r2"
                if (y - wall_top) % 3 == 2:
                    col = "r1"
                elif (x + off) % 5 == 0:
                    col = "r1"
                if x == x0 or x == x1:
                    col = "r0" if x == x1 else "r2"
            img.set(x, y, col)
        img.set(x0 - 1, y, "r0")
        img.set(x1 + 1, y, "r0")
    img.hline(x0 - 1, x1 + 1, wall_bot + 0, "r0")

    def window(wx, wy, ww=11, wh=12, rack=True):
        # stone frame, glass with a lit rack behind it, sill with a shadow below
        for y in range(wy - 1, wy + wh + 1):
            for x in range(wx - 1, wx + ww + 1):
                img.set(x, y, "s1" if (x == wx + ww or y == wy + wh) else "s3")
        img.hline(wx - 1, wx + ww, wy - 1, "w")
        for y in range(wy, wy + wh):
            for x in range(wx, wx + ww):
                col = "l1"
                if rack and wx + 2 <= x <= wx + ww - 3 and y >= wy + 2:
                    col = "k1" if (x - wx) % 6 in (2, 7) else "l0"
                    if (y - wy) % 2 == 0 and (x - wx) in (4, 5, 8) and hsh(x, y) % 3:
                        col = ["g4", "c1", "ye1"][hsh(x, y, 9) % 3]
                if x == wx or y == wy:
                    col = "l0"
                img.set(x, y, col)
        # mullion and transom
        img.vline(wx + ww // 2, wy, wy + wh - 1, "s4")
        img.hline(wx, wx + ww - 1, wy + 4, "s4")
        # glass glint (top-left light)
        img.set(wx + 1, wy + 1, "b5")
        img.set(wx + 2, wy + 1, "b4")
        img.set(wx + 1, wy + 2, "b4")
        img.set(wx + ww // 2 + 1, wy + 5, "b4")
        # sill
        img.hline(wx - 2, wx + ww + 1, wy + wh + 1, "w")
        img.hline(wx - 1, wx + ww + 1, wy + wh + 2, "s2")
        # rain stain under the sill
        for k in range(3):
            img.set(wx + 2 + k * 3, wy + wh + 3, "s3")

    window(10, 61, 11, 12)
    window(63, 61, 11, 12)
    # door: stone surround, two green leaves, brass handles, step
    dx0, dx1, dy0 = 33, 50, 64
    for y in range(dy0 - 2, wall_bot - 1):
        for x in range(dx0 - 2, dx1 + 3):
            img.set(x, y, "s2" if x >= dx1 + 1 else "s3")
    img.hline(dx0 - 2, dx1 + 2, dy0 - 3, "w")
    for y in range(dy0, wall_bot - 1):
        for x in range(dx0, dx1 + 1):
            u = x - dx0
            col = "g2"
            if u in (0, 8, 9):
                col = "g0" if u != 0 else "g1"
            elif u in (1, 10):
                col = "g3"
            if (y - dy0) in (3, 12) and u not in (0, 8, 9):
                col = "g1"
            if (y - dy0) in (4, 13) and u not in (0, 8, 9):
                col = "g3"
            if u == 17:
                col = "g0"
            img.set(x, y, col)
    img.set(dx0 + 7, dy0 + 10, "ye2")
    img.set(dx0 + 10, dy0 + 10, "ye2")
    img.set(dx0 + 7, dy0 + 11, "ye0")
    img.set(dx0 + 10, dy0 + 11, "ye0")
    # top glass of the door
    for x in range(dx0 + 2, dx1 - 1):
        if x - dx0 not in (8, 9):
            img.set(x, dy0 + 1, "b3")
            img.set(x, dy0 + 2, "l1")
    # step
    img.hline(dx0 - 3, dx1 + 3, wall_bot - 1, "s4")
    img.hline(dx0 - 3, dx1 + 3, wall_bot, "s1")
    # sign board above the door
    sx, sy = 27, 52
    img.rect(sx, sy, 30, 9, "l1")
    img.hline(sx, sx + 29, sy, "l2")
    img.hline(sx, sx + 29, sy + 8, "l0")
    img.vline(sx + 29, sy, sy + 8, "l0")
    img.vline(sx, sy, sy + 8, "l2")
    text(img, "DATA", sx + 3, sy + 2, "w", "l0")
    img.set(sx + 21, sy + 4, "c1")
    img.set(sx + 22, sy + 4, "c2")
    img.set(sx + 24, sy + 4, "g4")
    img.set(sx + 25, sy + 4, "g5")
    # wall lamp beside the door
    img.set(dx1 + 6, 66, "k1"); img.set(dx1 + 6, 67, "ye2"); img.set(dx1 + 6, 68, "ye1"); img.set(dx1 + 7, 68, "k1")

    # hipped roof: back slope (lit, faces up), front slope (mid), west hip (lit), east hip (shade)
    rt, rb = 2, 47   # roof top / eave rows
    ridge_y = 18
    rl, rr = 0, 83
    rx0, rx1 = 26, 57  # ridge ends
    for y in range(rt, rb + 1):
        for x in range(rl, rr + 1):
            # which plane? distances to the plane boundaries
            if y < ridge_y:
                fy = (ridge_y - y) / (ridge_y - rt)        # 0 at ridge, 1 at back eave
                lxe = rx0 - fy * (rx0 - rl)
                rxe = rx1 + fy * (rr - rx1)
                plane = "back" if lxe <= x <= rxe else ("west" if x < lxe else "east")
            else:
                fy = (y - ridge_y) / (rb - ridge_y)
                lxe = rx0 - fy * (rx0 - rl)
                rxe = rx1 + fy * (rr - rx1)
                plane = "front" if lxe <= x <= rxe else ("west" if x < lxe else "east")
            # slate courses run parallel to each eave
            if plane in ("back", "front"):
                c = (y - rt) // 3
                sub = (y - rt) % 3
                off = 3 if c % 2 else 0
                joint = (x + off) % 6 == 0
                base = {"back": ("l3", "l2", "l1"), "front": ("l2", "l1", "l0")}[plane]
                col = base[0] if sub == 0 else base[1]
                if sub == 2:
                    col = base[2]
                elif joint:
                    col = base[2]
            else:
                c = x // 3 if plane == "west" else (rr - x) // 3
                sub = (x % 3) if plane == "west" else ((rr - x) % 3)
                off = 3 if c % 2 else 0
                joint = (y + off) % 6 == 0
                base = {"west": ("s3", "l3", "l2"), "east": ("l1", "l1", "l0")}[plane]
                col = base[0] if sub == 0 else base[1]
                if sub == 2 or joint:
                    col = base[2]
            # worn slates and moss near the eaves
            if hsh(x, y, 66) % 53 == 0:
                col = "l0"
            if y > rb - 4 and hsh(x, y, 67) % 9 == 0:
                col = "g1"
            img.set(x, y, col)
    # hip and ridge lines with lead flashing highlights
    for y in range(rt, rb + 1):
        if y < ridge_y:
            fy = (ridge_y - y) / (ridge_y - rt)
        else:
            fy = (y - ridge_y) / (rb - ridge_y)
        lxe = round(rx0 - fy * (rx0 - rl))
        rxe = round(rx1 + fy * (rr - rx1))
        img.set(lxe, y, "w")
        img.set(lxe + 1, y, "s3")
        img.set(rxe, y, "s2")
        img.set(rxe + 1, y, "l0")
    img.hline(rx0, rx1, ridge_y - 1, "w")
    img.hline(rx0, rx1, ridge_y, "s3")
    img.hline(rx0, rx1, ridge_y + 1, "l0")
    # outline of the roof (dark slate, not black) and gutter
    img.hline(rl, rr, rt - 1, "l0")
    for y in range(rt, rb + 1):
        img.set(rl - 0, y, "l0" if y > ridge_y else "l1")
        img.set(rr, y, "l0")
    img.hline(rl, rr, rb + 1, "s4")
    img.hline(rl, rr, rb + 2, "s1")
    img.hline(rl + 1, rr - 1, rb + 3, "s0")
    # rooftop cooling unit on the back slope
    ux, uy = 30, 6
    for y in range(uy, uy + 9):
        for x in range(ux, ux + 16):
            col = "s3" if y < uy + 3 else "s2"
            if y == uy:
                col = "s4"
            if x == ux + 15 or y == uy + 8:
                col = "s0"
            if y >= uy + 3 and x == ux:
                col = "s3"
            img.set(x, y, col)
    for x in range(ux + 2, ux + 14, 2):
        img.set(x, uy + 1, "s1")
        img.set(x, uy + 2, "s2")
    # fan grilles
    for cx in (ux + 4, ux + 11):
        for y in range(uy + 4, uy + 8):
            for x in range(cx - 2, cx + 2):
                img.set(x, y, "s0" if (x + y) % 2 else "s1")
    img.hline(ux + 1, ux + 16, uy + 9, "l0")   # its shadow on the slates
    # mast with a small dish and a status light
    mx = 66
    img.vline(mx, 0, 12, "s1")
    img.vline(mx + 1, 1, 12, "s0")
    img.set(mx, 0, "tu1")
    img.set(mx - 1, 0, "tu2")
    for y in range(4, 9):
        for x in range(mx + 2, mx + 6):
            if (x - (mx + 3.5)) ** 2 / 4 + (y - 6.5) ** 2 / 6 <= 1:
                img.set(x, y, "s4" if x < mx + 4 else "s3")
    img.set(mx + 5, 6, "s1")
    img.hline(mx - 1, mx + 6, 13, "l0")
    return img


def make_cottage():
    """A brick cottage with a stepped gable to the front and an orange pantile roof running back from
    it (west slope lit, east slope in shade), white window frames with green shutters, a red window
    box, a split door and a chimney."""
    w, h = 54, 76
    img = Img(w, h)
    x0, x1 = 4, 49
    wall_top, wall_bot = 44, 75
    gtop = 20                      # top of the gable
    rt = 2                         # far end of the roof
    ridge = (x0 + x1) // 2
    # roof: pantile columns (an S-wave across each 4 px column), courses every 5 px
    for y in range(rt, gtop + 14):
        for x in range(x0 - 3, x1 + 4):
            west = x <= ridge
            u = (x - (x0 - 3)) % 4 if west else ((x1 + 3) - x) % 4
            c = (y - rt) % 5
            if west:
                col = ("o3", "o2", "o2", "o1")[u]
            else:
                col = ("o2", "o1", "o1", "o0")[u]
            if c == 4 and u >= 2:
                col = "o1" if west else "o0"
            if c == 0 and u == 0:
                col = "o3" if west else "o2"
            if hsh(x, y, 5) % 41 == 0:
                col = "o0"
            if y > gtop + 9 and hsh(x, y, 6) % 7 == 0:
                col = "g2"   # moss where the roof meets the gable
            img.set(x, y, col)
    img.vline(ridge, rt, gtop + 13, "o3")
    img.vline(ridge + 1, rt, gtop + 13, "s3")
    img.vline(ridge + 2, rt, gtop + 13, "o0")
    img.hline(x0 - 3, x1 + 3, rt - 1, "o0")
    img.vline(x0 - 4, rt, gtop + 13, "o0")
    img.vline(x1 + 4, rt, gtop + 13, "o0")
    for y in range(rt, gtop + 14):  # bargeboards: lit west edge, dark east edge
        img.set(x0 - 3, y, "w")
        img.set(x1 + 3, y, "s2")
    # chimney on the east slope
    cx, cy = 34, 5
    for y in range(cy, cy + 11):
        for x in range(cx, cx + 7):
            col = "r3" if x < cx + 2 else "r2"
            if (y - cy) % 3 == 2:
                col = "r1"
            if x == cx + 6:
                col = "r0"
            img.set(x, y, col)
    img.hline(cx - 1, cx + 7, cy, "s3")
    img.hline(cx - 1, cx + 7, cy - 1, "s4")
    img.hline(cx + 1, cx + 5, cy + 1, "k1")
    for y in range(cy + 2, cy + 12):       # chimney shadow on the slope
        img.set(cx + 7, y, "o0"); img.set(cx + 8, y, "o0")
    img.hline(cx, cx + 8, cy + 11, "o0")
    # stepped gable + front wall
    steps = 4
    step_h = 6
    def inset_at(y):
        if y >= gtop + steps * step_h:
            return 0
        k = (y - gtop) // step_h
        return (steps - k) * 4 + 2
    for y in range(gtop, wall_bot + 1):
        ins = inset_at(y)
        for x in range(x0 + ins, x1 - ins + 1):
            row = (y - gtop) // 2
            off = 3 if row % 2 else 0
            col = "r3"
            if (y - gtop) % 2 == 1:
                col = "r1" if (x + off) % 6 else "r0"
            elif (x + off) % 6 == 0:
                col = "r1"
            elif x > ridge + 8:
                col = "r2"
            elif (x + off) % 6 == 1 and hsh(x, y) % 3 == 0:
                col = "r4"
            img.set(x, y, col)
        img.set(x0 + ins - 1, y, "r0")
        img.set(x1 - ins + 1, y, "r0")
        img.set(x0 + ins, y, "r4" if y % 2 == 0 else "r3")
        if y < gtop + steps * step_h:
            # the gable casts a shadow on the east slope right beside each step
            for k in range(1, 4):
                img.set(x1 - ins + 1 + k, y, "o0")
    # white coping on each step: lit top, grey front edge
    for k in range(steps + 1):
        y = gtop + k * step_h
        ins = inset_at(y) if k < steps else 2
        img.hline(x0 + ins - 1, x1 - ins + 1, y - 1, "w")
        img.hline(x0 + ins - 1, x1 - ins + 1, y, "s3")
        img.set(x1 - ins + 1, y, "s1")
    # anchor finial on top
    img.vline(ridge + 1, gtop - 5, gtop - 1, "k1")
    img.set(ridge, gtop - 4, "k1"); img.set(ridge + 2, gtop - 4, "k1")
    # attic hatch window in the gable
    for y in range(gtop + 9, gtop + 17):
        for x in range(ridge - 3, ridge + 5):
            img.set(x, y, "w" if (x in (ridge - 3, ridge + 4) or y in (gtop + 9, gtop + 16)) else "l1")
    img.vline(ridge + 1, gtop + 10, gtop + 15, "w")
    img.set(ridge - 2, gtop + 10, "b4")
    # band course under the gable
    img.hline(x0, x1, wall_top - 1, "s3")
    img.hline(x0, x1, wall_top, "r1")
    def window(wx, wy):
        for y in range(wy, wy + 11):
            for x in range(wx, wx + 9):
                col = "l1"
                if x in (wx, wx + 8) or y in (wy, wy + 10) or x == wx + 4 or y == wy + 5:
                    col = "w"
                img.set(x, y, col)
        img.set(wx + 1, wy + 1, "b5"); img.set(wx + 2, wy + 1, "b4"); img.set(wx + 1, wy + 2, "b4")
        img.set(wx + 5, wy + 6, "b4")
        for sx in (wx - 4, wx + 9):
            for y in range(wy, wy + 11):
                for x in range(sx, sx + 4):
                    col = "g2" if (y - wy) % 3 else "g1"
                    if x == sx:
                        col = "g3"
                    if x == sx + 3:
                        col = "g0"
                    img.set(x, y, col)
        img.hline(wx - 1, wx + 9, wy + 12, "wd2")
        img.hline(wx - 1, wx + 9, wy + 13, "wd1")
        for x in range(wx - 1, wx + 10):
            img.set(x, wy + 11, "g2" if x % 2 else "g3")
            if x % 3 == 0:
                img.set(x, wy + 10, "tu1")
                img.set(x, wy + 11, "tu2" if x % 2 else "tu1")
    window(9, 50)
    # split door
    dx0, dy0 = 31, 53
    for y in range(dy0, wall_bot):
        for x in range(dx0, dx0 + 11):
            col = "wd2"
            if x == dx0 or y == dy0:
                col = "wd3"
            if x == dx0 + 10:
                col = "wd0"
            if y == dy0 + 10:
                col = "wd1"
            if (x - dx0) in (3, 7) and y > dy0 + 1:
                col = "wd1"
            img.set(x, y, col)
    img.hline(dx0 - 1, dx0 + 11, dy0 - 1, "w")
    img.vline(dx0 - 1, dy0 - 1, wall_bot - 1, "w")
    img.vline(dx0 + 11, dy0 - 1, wall_bot - 1, "s3")
    img.set(dx0 + 8, dy0 + 13, "ye2")
    img.hline(dx0 - 2, dx0 + 12, wall_bot, "s3")
    img.hline(x0 - 1, x1 + 1, wall_bot, "r0")
    img.vline(x0 - 1, gtop + steps * step_h, wall_bot, "r0")
    img.vline(x1 + 1, gtop + steps * step_h, wall_bot, "r0")
    return img


def make_windmill():
    """Octagonal smock mill: brick base with a wooden stage round it, a reed-thatched body drawn as flat
    facets (lit left, shaded right) with binding lines, white-framed door and window, and a boat-shaped
    shingled cap with a white windboard behind the hub. The sails are separate frames."""
    w, h = 44, 56
    img = Img(w, h)
    cx = 22
    top, base_y = 10, 55
    brick_top = base_y - 11
    facet_cols = ["d4", "d3", "d2", "d1", "d0"]
    for y in range(top, base_y + 1):
        t = (y - top) / (base_y - top)
        half = 7 + t * 8 if y < brick_top else 15.5
        for x in range(int(cx - half), int(cx + half) + 1):
            u = (x + 0.5 - cx) / half
            # five visible facets of the octagon
            f = 0 if u < -0.78 else 1 if u < -0.3 else 2 if u < 0.3 else 3 if u < 0.78 else 4
            if y >= brick_top:
                fb = ["r4", "r3", "r2", "r1", "r0"][f]
                row = (base_y - y) // 2
                col = fb
                if (base_y - y) % 2 == 1 or (x + (2 if row % 2 else 0)) % 5 == 0:
                    col = NAMES[DARKER[IDX[fb]]]
            else:
                col = facet_cols[f]
                if (x + (y // 2)) % 3 == 0:          # reed strokes
                    col = NAMES[DARKER[IDX[col]]] if f > 0 else "d3"
                if (y - top) % 7 == 6:                # binding lines
                    col = NAMES[DARKER[IDX[facet_cols[min(4, f + 1)]]]]
                if (y - top) % 7 == 0 and f < 2:
                    col = "d5" if f == 0 else "d4"
            img.set(x, y, col)
        # facet edges
        for ue in (-0.78, -0.3, 0.3, 0.78):
            xe = int(cx + ue * half)
            if y < brick_top:
                img.set(xe, y, "d0" if ue > 0 else "d2")
    # stage (gallery) at the top of the brick base, with its railing and the shadow it casts
    sy = brick_top - 1
    for x in range(cx - 21, cx + 22):
        img.set(x, sy, "wd4" if x < cx - 4 else ("wd3" if x < cx + 10 else "wd2"))
        img.set(x, sy + 1, "wd2" if x < cx + 10 else "wd1")
        img.set(x, sy + 2, "wd0")
        if x >= cx - 15 and x <= cx + 15:
            img.set(x, sy + 3, NAMES[DARKER[img.get(x, sy + 3)]] if img.get(x, sy + 3) else 0)
    for x in range(cx - 21, cx + 22, 4):
        img.vline(x, sy - 5, sy - 1, "wd2" if x < cx + 8 else "wd1")
        img.vline(x, sy + 3, sy + 7, "wd1")       # struts under the stage
    img.hline(cx - 21, cx + 21, sy - 5, "wd3")
    img.hline(cx - 21, cx + 21, sy - 6, "wd4")
    # door and window, white frames
    for y in range(base_y - 9, base_y + 1):
        for x in range(cx - 3, cx + 3):
            img.set(x, y, "wd2" if x < cx else "wd1")
    img.hline(cx - 4, cx + 3, base_y - 10, "w")
    img.vline(cx - 4, base_y - 10, base_y, "w")
    img.vline(cx + 3, base_y - 10, base_y, "s3")
    img.set(cx + 1, base_y - 4, "ye2")
    wy = 22
    for y in range(wy, wy + 5):
        for x in range(cx - 2, cx + 2):
            img.set(x, y, "l1")
    img.hline(cx - 3, cx + 2, wy - 1, "w"); img.vline(cx - 3, wy - 1, wy + 5, "w"); img.hline(cx - 3, cx + 2, wy + 5, "s3")
    img.vline(cx + 2, wy - 1, wy + 5, "s3")
    img.set(cx - 2, wy, "b4")
    # cap: boat shape, shingles, white windboard (beard) behind the hub
    for y in range(0, 12):
        half = 4 + y * 1.2 if y < 6 else 11 - (y - 6) * 0.6
        for x in range(int(cx - half), int(cx + half) + 1):
            u = (x + 0.5 - cx) / max(1, half)
            col = "wd3" if u < -0.35 else ("wd2" if u < 0.35 else "wd1")
            if (y % 3 == 2) and abs(u) < 0.95:
                col = NAMES[DARKER[IDX[col]]]
            if y == 0 or (u < -0.6 and y < 6):
                col = "wd4"
            img.set(x, y, col)
    img.hline(cx - 10, cx + 10, 12, "wd0")
    for y in range(4, 13):
        img.set(cx - 1, y, "w"); img.set(cx, y, "s4"); img.set(cx + 1, y, "s3")
    return img.outline({}, "wd0")


def make_sails(n=8):
    """Four sails on two stocks: lattice frames with cloth spread on the outer part. 8 frames cover a
    quarter turn, which loops because the sails are four-fold symmetric."""
    R = 27
    size = 2 * R + 5
    c = size // 2
    frames = []
    for f in range(n):
        th = f * (math.pi / 2) / n
        img = Img(size, size)
        for y in range(size):
            for x in range(size):
                px, py = x + 0.5 - c, y + 0.5 - c
                best = None
                for s in range(4):
                    a = th + s * math.pi / 2
                    ux, uy = math.cos(a), math.sin(a)
                    u = px * ux + py * uy
                    v = -px * uy + py * ux
                    if u < 0:
                        continue
                    lit = math.cos(a + 2.4) > -0.1
                    if abs(v) <= 1.0 and u <= R + 1:
                        best = "wd2" if v < 0 else "wd1"
                    elif 1.0 < v <= 7.5 and 4 <= u <= R:
                        edge = v > 6.6
                        bar = (u - 4) % 4 < 1.0
                        if edge or bar:
                            best = "wd3" if lit else "wd2"
                        elif u >= 9:
                            best = ("w" if v < 4.5 else "s4") if lit else ("s4" if v < 4.5 else "s3")
                        else:
                            best = None
                if best:
                    img.set(x, y, best)
        out = img.outline({}, "wd0")
        for y in range(c - 3, c + 4):
            for x in range(c - 3, c + 4):
                d = (x + 0.5 - c - 0.5) ** 2 + (y + 0.5 - c - 0.5) ** 2
                if d <= 8:
                    out.set(x, y, "s3" if (x <= c and y <= c) else "s1")
                elif d <= 11:
                    out.set(x, y, "s0")
        out.set(c - 1, c - 1, "w")
        out.set(c, c, "k1")
        frames.append(out)
    add_sprite("sails", frames, c, c)


# ---------------------------------------------------------------- props
def make_props():
    # wooden bridge: planks across two side beams, resting on the quay and the south bank
    bw, bh = 26, 54
    br = Img(bw, bh)
    for y in range(bh):
        for x in range(bw):
            p = y % 4
            col = "wd3" if p == 0 else ("wd2" if p < 3 else "wd0")
            if p == 1 and hsh(x // 6, y // 4) % 3 == 0:
                col = "wd4" if x < 13 else "wd3"
            if p in (1, 2) and x > 18:
                col = "wd2" if p == 1 else "wd1"
            if hsh(x, y // 4, 7) % 23 == 0 and p in (1, 2):
                col = "wd1"
            if x in (0, 1):
                col = "wd2" if x == 1 else "wd1"
            if x in (bw - 2, bw - 1):
                col = "wd1" if x == bw - 2 else "wd0"
            br.set(x, y, col)
    for y in range(1, bh, 4):
        br.set(3, y, "s3"); br.set(bw - 4, y, "s2")
    add_sprite("bridge_deck", br, 0, 0)
    # railing: a handrail along the side with posts standing up from the deck
    rail = Img(4, bh + 2)
    for y in range(bh + 2):
        rail.set(0, y, "wd4" if y % 13 else "wd3"); rail.set(1, y, "wd3"); rail.set(2, y, "wd1")
    for py in range(0, bh, 13):
        for y in range(py, min(bh + 2, py + 6)):
            rail.set(0, y, "wd3"); rail.set(1, y, "wd2"); rail.set(2, y, "wd1"); rail.set(3, y, "wd0")
        rail.set(0, py, "wd4"); rail.set(1, py, "wd4")
    add_sprite("bridge_rail", rail.outline({}, "wd0"), 1, 0)

    # fences: post and two rails, horizontal run 16 px
    fh = Img(16, 13)
    for x in range(16):
        fh.set(x, 4, "wd4"); fh.set(x, 5, "wd2")
        fh.set(x, 8, "wd3"); fh.set(x, 9, "wd1")
    for y in range(0, 13):
        fh.set(1, y, "wd3"); fh.set(2, y, "wd2"); fh.set(3, y, "wd1")
    fh.set(1, 0, "wd4"); fh.set(2, 0, "wd4"); fh.set(3, 0, "wd2")
    add_sprite("fence_h", fh.outline({}, "wd0"), 2, 12)
    post = Img(3, 13)
    for y in range(13):
        post.set(0, y, "wd3"); post.set(1, y, "wd2"); post.set(2, y, "wd1")
    post.set(0, 0, "wd4"); post.set(1, 0, "wd4")
    # vertical run seen from the side: a rail drawn as a lit line going back
    fv = Img(5, 22)
    for y in range(0, 22):
        fv.set(1, y, "wd4" if y % 2 == 0 else "wd3"); fv.set(2, y, "wd2")
    for y in range(0, 13):
        fv.set(1, y + 9, "wd3"); fv.set(2, y + 9, "wd2"); fv.set(3, y + 9, "wd1")
    fv.set(1, 9, "wd4"); fv.set(2, 9, "wd4")
    add_sprite("fence_v", fv.outline({}, "wd0"), 2, 21)

    # barrel
    ba = Img(12, 14)
    for y in range(14):
        for x in range(12):
            u = (x + 0.5 - 6) / 6
            if abs(u) > 0.98:
                continue
            bulge = 1 - 0.1 * ((y - 7) / 7) ** 2
            if abs(u) > bulge:
                continue
            col = "wd3" if u < -0.3 else ("wd2" if u < 0.35 else "wd1")
            if y in (3, 10):
                col = "s3" if u < 0 else "s1"
            if y < 3:
                col = "wd4" if u < 0 else "wd3"
                if y == 1 and abs(u) < 0.5:
                    col = "wd2"
            if x % 3 == 0 and y >= 3 and col.startswith("wd"):
                col = "wd1" if u < 0.3 else "wd0"
            ba.set(x, y, col)
    add_sprite("barrel", ba.outline({}, "wd0"), 6, 13)

    # crate
    cr = Img(14, 14)
    for y in range(14):
        for x in range(14):
            top = y < 4
            col = "wd4" if top else ("wd3" if x < 7 else "wd2")
            if not top and (y - 4) % 3 == 2:
                col = "wd1"
            if not top and (x in (0, 13) or (x == y - 4 + 2)):
                col = "wd2" if x < 7 else "wd1"
            if top and (x + y) % 4 == 0:
                col = "wd3"
            cr.set(x, y, col)
    add_sprite("crate", cr.outline({}, "wd0"), 7, 13)

    # cable drum: wooden spool with blue cable
    dr = Img(16, 16)
    for y in range(16):
        for x in range(16):
            dx, dy = x + 0.5 - 8, y + 0.5 - 8
            r = math.sqrt(dx * dx + dy * dy)
            if r > 7.6:
                continue
            col = "wd3" if dx + dy < 0 else "wd2"
            if r > 6.6:
                col = "wd1" if dx + dy > 0 else "wd2"
            if r < 5.4:
                col = "b3" if ((x + y) % 3) else "b2"
                if dx + dy > 3:
                    col = "b1"
            if r < 2.2:
                col = "wd1"
            if r < 1:
                col = "k1"
            dr.set(x, y, col)
    dr.set(5, 4, "b5"); dr.set(6, 4, "b4")
    add_sprite("cable_drum", dr.outline({}, "wd0"), 8, 15)

    # outdoor cooling condenser
    ac = Img(20, 15)
    for y in range(15):
        for x in range(20):
            col = "s4" if y < 3 else "s3"
            if y == 0:
                col = "w"
            if x == 19 or y == 14:
                col = "s1"
            if y >= 3 and x == 0:
                col = "s4"
            ac.set(x, y, col)
    for y in range(5, 12):
        for x in range(4, 13):
            if (x + 0.5 - 8.5) ** 2 + (y + 0.5 - 8.5) ** 2 < 14:
                ac.set(x, y, "s1" if (x + y) % 2 else "s0")
    ac.set(8, 8, "s3")
    for y in range(5, 12, 2):
        ac.hline(14, 17, y, "s2")
    ac.set(16, 3, "g4")
    add_sprite("condenser", ac.outline({}, "s0"), 10, 14)

    # door mat
    dm = Img(16, 6)
    for y in range(6):
        for x in range(16):
            col = "wd2" if (x + y) % 2 else "wd3"
            if y in (0, 5) or x in (0, 15):
                col = "wd1"
            if y in (2, 3) and 3 <= x <= 12:
                col = "l2" if (x // 2) % 2 else "l1"
            dm.set(x, y, col)
    add_sprite("doormat", dm, 8, 3)

    # bicycle (side view, parked)
    bi = Img(22, 14)
    def wheel(cx, cy):
        for y in range(cy - 5, cy + 6):
            for x in range(cx - 5, cx + 6):
                r = math.sqrt((x + 0.5 - cx - 0.5) ** 2 + (y + 0.5 - cy - 0.5) ** 2)
                if 4.3 <= r <= 5.3:
                    bi.set(x, y, "k1" if y > cy - 2 else "s1")
                elif r < 1.2:
                    bi.set(x, y, "s3")
                elif r < 4.3 and (x == cx or y == cy):
                    bi.set(x, y, "s2")
    wheel(5, 8); wheel(16, 8)
    for i in range(10):
        bi.set(5 + i, 8 - i // 3, "tu1")
        bi.set(7 + i, 4, "tu1")
    bi.set(16, 7, "tu0"); bi.set(16, 6, "tu1"); bi.set(16, 5, "tu1"); bi.set(16, 4, "tu1")
    bi.hline(15, 18, 2, "k1"); bi.set(18, 3, "s3")
    bi.hline(7, 10, 2, "wd1"); bi.set(8, 3, "s2")
    bi.hline(1, 4, 4, "tu1"); bi.hline(17, 21, 5, "tu1")  # mudguards
    add_sprite("bicycle", bi, 11, 13)

    # signpost
    sp = Img(16, 22)
    sp.vline(7, 4, 21, "wd2"); sp.vline(8, 4, 21, "wd1")
    for y in range(3, 9):
        for x in range(1, 15):
            if x - 1 + abs(y - 5.5) * 0 <= 13 and not (x == 14 and y in (3, 8)):
                sp.set(x, y, "wd4" if y == 3 else "wd3")
    sp.hline(2, 13, 8, "wd1")
    sp.hline(3, 10, 5, "wd1")
    sp.set(11, 5, "wd1"); sp.set(12, 5, "wd1"); sp.set(11, 4, "wd1"); sp.set(11, 6, "wd1")
    add_sprite("signpost", sp.outline({}, "wd0"), 8, 21)

    # flowers and ground detail (2 sway frames each where they move)
    def flower(petal, centre):
        fr = []
        for f in range(2):
            img = Img(5, 7)
            sx = 2 + (1 if f else 0)
            img.set(2, 6, "g2"); img.set(2, 5, "g2"); img.set(sx if f else 2, 4, "g3")
            hx = sx
            img.set(hx, 1, petal); img.set(hx - 1, 2, petal); img.set(hx + 1, 2, petal); img.set(hx, 3, petal)
            img.set(hx, 2, centre)
            img.set(hx - 1, 1, LIGHTER_N(petal))
            img.set(1, 5, "g3")
            fr.append(img)
        return fr
    add_sprite("daisy", flower("w", "ye1"), 2, 6)
    add_sprite("violet", flower("pu1", "ye2"), 2, 6)
    add_sprite("buttercup", flower("ye1", "ye0"), 2, 6)
    st = from_rows([".sS.", "sSSs", "kss.", ".kk."], {"s": "s2", "S": "s3", "k": "s0"})
    add_sprite("stone", st, 2, 3)
    st2 = from_rows(["..sS..", ".sSSs.", "sSSsss", "ksssk.", ".kkk.."], {"s": "s2", "S": "s4", "k": "s0"})
    add_sprite("stone_big", st2, 3, 4)
    tuft = []
    for f in range(2):
        rows = ["..4...", ".42.4.", ".2242.", "42222.", ".1212."] if f == 0 else ["...4..", "..24.4", ".2242.", "42222.", ".1212."]
        tuft.append(from_rows(rows, {"1": "g1", "2": "g2", "4": "g4"}))
    add_sprite("tuft", tuft, 3, 4)

    # tulips: three sway frames; heads in three colours
    for name, rp in (("tulip_red", "tu"), ("tulip_yellow", "ye"), ("tulip_pink", "pk")):
        fr = []
        for f, lean in enumerate((0, 1, 0, -1)):
            img = Img(5, 8)
            img.set(2, 7, "g1"); img.set(2, 6, "g2"); img.set(2 + (lean if lean > 0 else 0), 5, "g2")
            img.set(1, 6, "g3")  # leaf
            img.set(3, 7, "g2")
            hx = 2 + lean
            r0, r1, r2 = RAMPS[rp]
            img.set(hx - 1, 2, r1); img.set(hx, 2, r1); img.set(hx + 1, 2, r0)
            img.set(hx - 1, 3, r1); img.set(hx, 3, r1); img.set(hx + 1, 3, r0)
            img.set(hx - 1, 1, r2); img.set(hx + 1, 1, r1); img.set(hx, 1, r1)
            img.set(hx, 4, r0)
            img.set(hx - 1, 4, r0)
            fr.append(img)
        add_sprite(name, fr, 2, 7)


def make_waterside():
    # reeds with cattails, two sway frames
    fr = []
    for f in range(2):
        img = Img(11, 16)
        for i, (bx, hgt, lean) in enumerate(((2, 12, -1), (5, 15, 0), (8, 11, 1))):
            for k in range(hgt):
                xx = bx + (lean * k // 8 if f == 0 else (lean + (1 if i == 1 else 0)) * k // 7)
                img.set(xx, 15 - k, "g2" if k < hgt - 4 else "g3")
                if k == hgt - 4 and i != 2:
                    img.set(xx, 15 - k, "wd1"); img.set(xx, 14 - k, "wd2"); img.set(xx, 13 - k, "wd2")
                    img.set(xx, 12 - k, "wd3")
        for (x, y) in ((1, 15), (0, 14), (9, 15), (10, 14), (4, 15), (6, 15), (3, 13)):
            img.set(x, y, "g3")
        img.set(0, 13, "g4"); img.set(10, 13, "g4")
        fr.append(img.outline({}, "g0"))
    add_sprite("reeds", fr, 5, 15)
    lp = from_rows(["..4433..", ".43332..", "4332.21.", ".3222211", "..1111.."],
                   {"4": "g4", "3": "g3", "2": "g2", "1": "g1"})
    add_sprite("lilypad", lp, 4, 3)
    lf = lp.copy()
    lf.set(3, 0, "pk1"); lf.set(4, 0, "pk2"); lf.set(3, 1, "pk1"); lf.set(4, 1, "w"); lf.set(5, 1, "pk1")
    add_sprite("lilypad_flower", lf, 4, 3)
    # a small rowing boat, green with a white rim, moored; bobs one pixel
    frames = []
    for f in range(2):
        img = Img(32, 15)
        oy = f
        for y in range(13):
            for x in range(32):
                u = (x + 0.5 - 16) / 15.5
                v = (y + 0.5 - 6) / 6.0
                if u * u + v * v * (1.0 if u < 0 else 0.8) > 1:
                    continue
                col = "g2"
                inner = (u / 0.84) ** 2 + ((y + 0.5 - 5.5) / 4.0) ** 2 < 1
                if inner:
                    col = "wd2"
                    if x % 9 == 4 and abs(u) < 0.7:
                        col = "wd4"   # thwarts
                    elif x % 9 == 5 and abs(u) < 0.7:
                        col = "wd1"
                    elif y < 4:
                        col = "wd1"   # inner shadow under the near gunwale
                else:
                    if y < 6:
                        col = "w" if y <= 2 or (y < 5 and abs(u) > 0.8) else "s4"
                    else:
                        col = "g2" if u < 0.2 else "g1"
                        if y >= 10:
                            col = "g0"
                img.set(x, y + oy, col)
        img = img.outline({}, "g0")
        for x in range(4, 28):
            img.set(x, 13 + oy, "b1")
            if x % 4 == 0:
                img.set(x + f, 14, "b4")
        img.set(1, 6 + oy, "wd1"); img.set(0, 6 + oy, "wd1")   # mooring rope
        frames.append(img)
    add_sprite("boat", frames, 16, 13)
    post = Img(4, 12)
    for y in range(12):
        post.set(0, y, "wd3"); post.set(1, y, "wd2"); post.set(2, y, "wd1")
    post.hline(0, 2, 0, "wd4")
    post.hline(0, 2, 4, "s3")
    add_sprite("mooring_post", post.outline({}, "wd0"), 1, 11)


def LIGHTER_N(n):
    return NAMES[LIGHTER[IDX[n]]]


# ---------------------------------------------------------------- characters (from the hand data)
def make_characters():
    compose_player()
    # Ilatea: the body is hand-drawn; the spear-stylus and Byte on her shoulder are composed on top.
    # Four glow-pulse frames: circuit lines c1 -> c2 -> c1 -> c0, the stylus tip flickers.
    body = HAND["guide_0"]
    frames = []
    for f, cmap in enumerate(("c1", "c2", "c1", "c0")):
        img = Img(body.w, body.h)
        sx = 17
        img.vline(sx, 5, 27, "s3")
        img.vline(sx + 1, 6, 27, "s1")
        img.set(sx, 4, "s1")
        tip = [(0, 0, "c2"), (0, 1, "w"), (-1, 1, "c1"), (1, 1, "c1"), (0, 2, "c2"), (0, 3, "c1")]
        for dx, dy, c in tip:
            img.set(sx + dx, dy, c)
        if f % 2 == 0:
            img.set(sx - 2, 2, "c0"); img.set(sx + 2, 0, "c0")
        else:
            img.set(sx + 2, 3, "c0"); img.set(sx - 2, 0, "c1")
        img.blit(body, 0, 0)
        img.blit(HAND["owl_small"], 1, 8)
        for i, v in enumerate(img.px):
            if v == IDX["c1"] and i // img.w > 5:
                img.px[i] = IDX[cmap]
        frames.append(img)
    # a faint dithered aura round her silhouette (not round the spear), shimmering frame to frame
    for f, img in enumerate(frames):
        src = img.copy()
        for y in range(4, src.h - 2):
            for x in range(0, 17):
                if src.get(x, y):
                    continue
                if any(src.get(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))) and (x + y + f) % 2 == 0:
                    img.set(x, y, "c0" if y > 12 else "c1")
    add_sprite("guide", frames, 11, frames[0].h - 1)
    add_sprite("hog", [HAND["hog_0"], HAND["hog_1"]], 8, 14)
    for o in ("byte", "nocti"):
        add_sprite(o + "_perch", HAND[o + "_perch"], 8, 15)
        add_sprite(o + "_fly", [HAND[o + "_fly_0"], HAND[o + "_fly_1"]], 8, 12)


# ---------------------------------------------------------------- portrait
def make_portrait():
    S = 64
    img = Img(S, S)
    # background: night-blue with a soft stream of light behind her, a cracked marble column at left,
    # a rack with blinking lights at right
    for y in range(S):
        for x in range(S):
            d = (x - 40) * 0.6 + (y - 10) * -0.4
            col = "l0"
            st = math.sin((x * 0.12 - y * 0.18)) * 6 + (x - 2 * y + 70)
            if abs(st) < 9:
                col = "l1"
            if abs(st) < 4:
                col = "l2" if dith(x, y, 0.5) else "l1"
            if abs(st) < 1.5:
                col = "c0" if dith(x, y, 0.5) else "l2"
            img.set(x, y, col)
    for y in range(S):
        for x in range(1, 9):
            u = (x - 1) / 7
            col = "s4" if u < 0.3 else ("s3" if u < 0.7 else "s2")
            if x in (3, 6):
                col = "s2" if u < 0.7 else "s1"
            if x == 8:
                col = "s0"
            img.set(x, y, col)
    for y in range(18, 40):
        img.set(4 + (y // 5) % 2, y, "s1")
    for y in range(4, 60):
        for x in range(54, 63):
            col = "k1" if x in (54, 62) else "l0"
            if (y - 4) % 6 == 0:
                col = "s0"
            img.set(x, y, col)
        if (y - 4) % 6 == 3:
            img.set(57, y, ["g4", "c1", "ye1", "g4", "tu2"][(y // 6) % 5])
            img.set(59, y, ["c1", "g4", "g4", "ye1", "c1"][(y // 6) % 5])

    cx, cy = 32, 30
    # hair, back layer: long dark flowing hair framing the face down past the shoulders
    for y in range(6, 62):
        for x in range(10, 54):
            dx = (x + 0.5 - cx)
            dy = (y + 0.5 - cy)
            wide = 17 + max(0, (y - 26)) * 0.18
            if y < 14:
                wide = 6 + (y - 6) * 1.6
            if abs(dx) < wide and y < 56 - abs(dx) * 0.2:
                strand = math.sin(dx * 0.9 + y * 0.15) * 0.5 + 0.5
                col = "l1" if strand > 0.55 else "l0"
                if dx < -4 and strand > 0.85:
                    col = "l2"
                if abs((dx * 3.0 + y * 0.6) % 11 - 5) < 0.5:
                    col = "s3"   # silver strand like a fibre
                img.set(x, y, col)
    # neck and shoulders, chiton
    for y in range(40, 64):
        for x in range(8, 57):
            dx = x + 0.5 - cx
            sh = 10 + (y - 40) * 1.6
            if abs(dx) < sh:
                if abs(dx) < 4.5 and y < 47:
                    col = "sk2" if dx < 1 else "sk1"
                    if dx < -2.5:
                        col = "sk3"
                    img.set(x, y, col)
                elif y >= 46 or abs(dx) > 6:
                    # bare shoulders at the sides, chiton over the chest from a shoulder clasp
                    chit = abs(dx) < 11 + (y - 46) * 1.0 and y >= 46
                    if chit:
                        col = "l1" if dx < 3 else "l0"
                        if dx < -6:
                            col = "l2"
                        # folds
                        if (int(dx + 40) + (y // 4)) % 7 == 0:
                            col = "l0"
                    else:
                        col = "sk2" if dx < 0 else "sk1"
                        if dx < -14:
                            col = "sk3"
                        if abs(dx) > sh - 1.5:
                            col = "sk0"
                    img.set(x, y, col)
    # circuit lines on the chiton
    for (x0, y0, x1, y1) in ((24, 50, 30, 50), (30, 50, 30, 56), (30, 56, 38, 56), (36, 48, 36, 53),
                             (36, 53, 42, 53), (20, 58, 26, 58), (26, 58, 26, 63), (40, 59, 46, 59)):
        for t in range(0, 41):
            xx = round(x0 + (x1 - x0) * t / 40)
            yy = round(y0 + (y1 - y0) * t / 40)
            img.set(xx, yy, "c1")
    for (x, y) in ((30, 56), (36, 53), (26, 58)):
        img.set(x, y, "c2")
    # aegis clasp on the shoulder: a small hex
    for (x, y, c) in ((19, 47, "c1"), (20, 46, "c2"), (21, 47, "c1"), (19, 48, "c0"), (21, 48, "c0"), (20, 49, "c1"), (20, 47, "w")):
        img.set(x, y, c)
    # face: shaded oval
    for y in range(13, 43):
        for x in range(18, 47):
            dx = (x + 0.5 - cx) / 10.5
            dy = (y + 0.5 - 28) / 13.5
            jaw = 1.0 if dy < 0.3 else 1.0 - (dy - 0.3) * 0.75
            if dx * dx + dy * dy <= 1 and abs(dx) <= jaw:
                nz = math.sqrt(max(0, 1 - dx * dx - dy * dy))
                li = -dx * 0.55 - dy * 0.35 + nz * 0.55
                col = "sk3" if li > 0.62 else "sk2" if li > 0.25 else "sk1" if li > -0.1 else "sk0"
                img.set(x, y, col)
    # face outline on the shadow side only
    for y in range(13, 44):
        for x in range(18, 48):
            if img.name(x, y).startswith("sk") and not img.name(x + 1, y).startswith("sk") and x > cx:
                img.set(x, y, "sk0")
            if img.name(x, y).startswith("sk") and not img.name(x, y + 1).startswith("sk") and y > 36 and abs(x - cx) < 9:
                img.set(x, y + 1, "sk0") if img.name(x, y + 1)[0] in "l" else None
    # fringe: hair falling over the forehead in two sweeps, parted a little left of centre
    for y in range(10, 24):
        for x in range(14, 50):
            dx = x + 0.5 - 29
            lim = 22 - (y - 10) * 1.1 - abs(dx) * 0.55 + math.sin(x * 0.8) * 1.2
            if y < 13 + abs(dx) * 0.0 or (y - 10) < (8 - abs(dx) * 0.25 + math.sin(x * 0.9) * 1.5):
                if abs(x + 0.5 - cx) < 17:
                    col = "l1" if dx < -2 else "l0"
                    if (x + y) % 5 == 0 and dx < 0:
                        col = "l2"
                    if abs((x * 2 + y) % 13 - 6) < 0.6:
                        col = "s3"
                    img.set(x, y, col)
    # hair sides covering the face edges
    for y in range(18, 44):
        for x in range(16, 49):
            dx = x + 0.5 - cx
            edge = 10.5 - (0 if y < 30 else (y - 30) * 0.15)
            if abs(dx) > edge and abs(dx) < edge + 4 + (y - 18) * 0.12:
                col = "l1" if dx < 0 else "l0"
                if (y + x) % 7 == 0:
                    col = "s4" if dx < 0 else "s2"
                img.set(x, y, col)
    # glowing circlet (a thin line of light on the brow)
    for x in range(22, 43):
        y = 17 + round(((x - 32.5) / 10.0) ** 2 * 2)
        img.set(x, y, "c1" if x % 4 else "c2")
    img.set(32, 17, "w"); img.set(33, 17, "c2")
    # eyes: owl-gold and glowing, calm
    for ex, sgn in ((26, -1), (37, 1)):
        img.hline(ex - 2, ex + 2, 27, "l0")       # upper lid / lashes
        img.set(ex - 3, 27 + (0 if sgn < 0 else 0), "l0") if sgn < 0 else img.set(ex + 3, 27, "l0")
        img.hline(ex - 2, ex + 2, 28, "w")
        img.set(ex - 1, 28, "ye1"); img.set(ex, 28, "ye2"); img.set(ex + 1, 28, "ye1")
        img.set(ex - 1, 29, "ye0"); img.set(ex, 29, "ye1"); img.set(ex + 1, 29, "ye0")
        img.set(ex, 29, "k0"); img.set(ex - 1 + (1 if sgn < 0 else 0), 28, "w")
        img.hline(ex - 1, ex + 1, 30, "sk1")
        # brows
        img.hline(ex - 2, ex + 2, 24, "l1")
        img.set(ex + 3 * sgn, 25 if sgn < 0 else 25, "l1")
    # nose (light from top-left: shadow on the right)
    img.set(32, 30, "sk2"); img.set(33, 31, "sk1"); img.set(33, 32, "sk1"); img.set(32, 33, "sk1"); img.set(33, 33, "sk0")
    # warm knowing smile
    img.hline(29, 35, 36, "sk0")
    img.set(28, 35, "sk0"); img.set(36, 35, "sk0")
    img.hline(30, 34, 37, "pk1")
    img.set(31, 37, "pk2")
    # cheeks
    img.set(24, 32, "pk2"); img.set(25, 33, "pk1"); img.set(40, 32, "pk1"); img.set(39, 33, "pk1")
    # spear-stylus across the lower right, tip glowing with a traced glyph
    for t in range(0, 60):
        x = 63 - t * 0.48
        y = 63 - t * 0.98
        if y < 2:
            break
        xi, yi = round(x), round(y)
        if yi < 44 and xi < 52:
            pass
        img.set(xi, yi, "s3"); img.set(xi + 1, yi, "s1")
    tipx, tipy = 34, 4
    for (x, y, c) in ((44, 2, "c2"), (45, 3, "w"), (44, 4, "c1"), (43, 3, "c1"), (46, 2, "c0"), (42, 5, "c0"),
                      (45, 1, "c1")):
        img.set(x, y, c)
    # traced constellation glyph
    for (x, y) in ((48, 6), (51, 9), (49, 12), (52, 14)):
        img.set(x, y, "c2")
    for (x, y) in ((49, 7), (50, 8), (50, 10), (50, 11), (51, 13)):
        img.set(x, y, "c0")
    # Byte the owl on her shoulder (left of the picture)
    owl = HAND["byte_perch"]
    img.blit(owl, 3, 38)
    # frame
    for i in range(S):
        img.set(i, 0, "s1"); img.set(i, S - 1, "k0"); img.set(0, i, "s1"); img.set(S - 1, i, "k0")
    add_sprite("portrait", img, 0, 0)


# ---------------------------------------------------------------- scene objects
def scene_objects():
    objs = []

    def o(sprite, x, y, **kw):
        d = {"sprite": sprite, "x": x, "y": y}
        d.update(kw)
        objs.append(d)

    # forest edge: back rows first; trees overlap the row behind
    forest = [(8, 22, "tree_b"), (30, 18, "tree_a"), (54, 16, "tree_b"), (78, 20, "tree_a"),
              (104, 16, "tree_b"), (122, 22, "poplar"), (-6, 40, "tree_a"), (20, 36, "tree_b"),
              (44, 34, "poplar"), (132, 36, "tree_a"), (110, 38, "poplar"),
              (-4, 70, "tree_b"), (6, 98, "poplar"), (-6, 128, "tree_a"), (-2, 156, "poplar")]
    for x, y, s in forest:
        o(s, x, y, shadow=[5, -1, 12 if s != "poplar" else 6, 4])
    o("bush", 138, 60, shadow=[3, 0, 8, 3])
    o("bush", 188, 56, shadow=[3, 0, 8, 3])
    o("bush", 246, 150, shadow=[3, 0, 8, 3])
    # buildings
    o("datacentre", 56, 112, shadow=[6, -2, 44, 5], kind="building")
    o("cottage", 134, 112, shadow=[5, -2, 27, 4], kind="building")
    o("doormat", 56, 116, flat=True)
    o("condenser", 102, 111, shadow=[3, 0, 10, 3])
    o("cable_drum", 12, 117, shadow=[3, 0, 8, 3])
    o("crate", 92, 120, shadow=[3, 0, 7, 2])
    o("barrel", 162, 113, shadow=[2, 0, 6, 2])
    o("barrel", 170, 116, shadow=[2, 0, 6, 2])
    o("bicycle", 112, 118, shadow=[2, 0, 10, 2])
    o("signpost", 198, 128, shadow=[2, 0, 5, 2])
    # windmill on the plateau, sails turn over it
    o("windmill", 216, 77, shadow=[6, -1, 21, 4])
    o("sails", 216, 28, anim={"fps": 8}, layer="top")
    # tulip field to the east, fenced
    for x in range(206, 256, 16):
        o("fence_h", x + 2, 98)
        o("fence_h", x + 2, 144)
    o("fence_v", 205, 120)
    o("fence_v", 205, 142)
    rows = ["tulip_red", "tulip_yellow", "tulip_pink", "tulip_red", "tulip_yellow", "tulip_pink", "tulip_red", "tulip_yellow"]
    for r in range(8):
        yy = 105 + r * 5
        for x in range(212, 256, 4):
            o(rows[r], x + (r % 2) * 2, yy, anim={"fps": 5, "phase": (x // 4 + r) % 4})
    # tulip fields south of the canal
    for (fx0, fx1, fy0, n) in ((2, 64, 199, 5), (196, 256, 199, 5)):
        for r in range(n):
            yy = fy0 + r * 5
            for x in range(fx0, fx1, 4):
                o(["tulip_yellow", "tulip_red", "tulip_pink", "tulip_yellow", "tulip_red"][r], x + (r % 2) * 2, yy,
                  anim={"fps": 5, "phase": (x // 4 + r) % 4})
    # bridge
    o("bridge_deck", 91, 150, flat=True, ax=0, ay=0)
    o("bridge_rail", 92, 148, ay=0, flat=True)
    o("bridge_rail", 114, 148, ay=0, flat=True)
    # waterside: reeds on the banks, lily pads, a moored boat
    for x, y in ((8, 199), (30, 197), (70, 198), (126, 197), (188, 199), (240, 198), (150, 160), (224, 160)):
        o("reeds", x, y, anim={"fps": 2, "phase": x % 2})
    for x, y in ((40, 178), (52, 184), (200, 186), (244, 176), (64, 176)):
        o("lilypad_flower" if x in (52, 244) else "lilypad", x, y, flat=True)
    o("boat", 168, 182, anim={"fps": 2})
    o("mooring_post", 148, 166, shadow=[2, 0, 3, 1])
    # ground detail: flowers, stones, tufts
    det = [("daisy", 30, 140), ("daisy", 36, 146), ("violet", 22, 132), ("buttercup", 124, 140),
           ("daisy", 152, 148), ("tuft", 88, 146), ("tuft", 178, 150), ("stone", 74, 152), ("stone_big", 182, 100),
           ("buttercup", 130, 150), ("violet", 160, 136), ("tuft", 14, 152), ("daisy", 238, 146),
           ("stone", 144, 140), ("tuft", 120, 126), ("daisy", 176, 76), ("violet", 150, 72), ("tuft", 196, 74),
           ("buttercup", 100, 136), ("tuft", 66, 132), ("stone", 236, 36), ("daisy", 160, 48), ("tuft", 150, 30),
           ("violet", 248, 56), ("buttercup", 168, 24), ("tuft", 240, 20)]
    for s, x, y in det:
        o(s, x, y, anim={"fps": 2, "phase": (x + y) % 2} if s != "stone" and s != "stone_big" else None)
    # characters
    o("guide", 36, 124, anim={"fps": 4}, glow=[0, 0, 13, 5], shadow=[1, 0, 6, 2])
    o("hog", 186, 148, anim={"fps": 2}, shadow=[1, 0, 7, 2])
    o("player", 78, 140, walker=True, shadow=[0, 0, 6, 2])
    return [d for d in objs]


def main():
    make_characters()
    make_trees()
    add_sprite("datacentre", make_datacentre(), 42, 85)
    add_sprite("cottage", make_cottage(), 27, 75)
    add_sprite("windmill", make_windmill(), 22, 55)
    make_sails()
    make_props()
    make_waterside()
    make_portrait()
    frames, _ = build_ground()
    tiles, maps = slice_tiles(frames)
    # tile animation table: a cell whose tile differs between frames is animated
    os.makedirs(OUT, exist_ok=True)
    pal = {"colours": ["#" + hx for _, hx in PALETTE], "names": NAMES, "count": len(PALETTE) - 1,
           "ramps": RAMPS, "darker": DARKER, "lighter": LIGHTER, "glow": GLOW, "alphabet": ALPH}
    with open(os.path.join(OUT, "palette.json"), "w") as f:
        json.dump(pal, f, indent=1)
    with open(os.path.join(OUT, "tiles.json"), "w") as f:
        json.dump({"tile": 16, "cols": TW, "rows": TH, "frameMs": 220,
                   "tiles": ["".join(ALPH[v] for v in t) for t in tiles], "maps": maps}, f)
    sp = {}
    for name, s in SPRITES.items():
        sp[name] = {"w": s["frames"][0].w, "h": s["frames"][0].h, "ox": s["ox"], "oy": s["oy"],
                    "frames": [fr.rows() for fr in s["frames"]]}
    with open(os.path.join(OUT, "sprites.json"), "w") as f:
        json.dump(sp, f)
    with open(os.path.join(OUT, "scene.json"), "w") as f:
        json.dump({"width": W, "height": H, "objects": scene_objects(),
                   "walk": {"path": [[40, 140], [90, 140], [96, 146], [96, 150], [96, 146], [90, 140]],
                            "speed": 40}}, f, indent=0)
    print("tiles", len(tiles), "sprites", len(SPRITES), "colours", len(PALETTE) - 1)


if __name__ == "__main__":
    main()
