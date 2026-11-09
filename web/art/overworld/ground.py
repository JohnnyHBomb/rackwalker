"""Ground autotiles for the overworld.

The game's map is a grid of terrain classes (G grass, P path, W water, B bridge = water under a path, S sand,
F tulip field, C crop field). A tile's pixels depend only on its own class, the classes of its four neighbours
and (for grass and path) one of four grass variants, so the tile set can be authored here once and looked up
at run time by a key: centre class, the four neighbour classes reduced to what that centre cares about (SIDES),
and the variant.

Each tile is drawn by rendering a 3 x 3 probe of tiles through the same per-pixel rules as the art direction's
test screen (recessed path rims with overhanging blades, a stone quay on a north bank and foam on the others,
sand with a wet line, field soil in rows) and keeping the centre tile. Water and fields have four frames.
"""

import math

from pix import IDX, Img, dith, hsh

# For each centre class: neighbour class -> the reduced class the centre's pixels depend on.
SIDES = {
    "G": {"G": "G", "P": "P", "W": "W", "B": "W", "S": "S", "F": "L", "C": "L"},
    "P": {"G": "G", "P": "P", "W": "W", "B": "P", "S": "G", "F": "G", "C": "G"},
    "W": {"G": "L", "P": "L", "W": "W", "B": "W", "S": "S", "F": "L", "C": "L"},
    "S": {"G": "G", "P": "G", "W": "W", "B": "W", "S": "S", "F": "G", "C": "G"},
    "F": {"G": "O", "P": "O", "W": "O", "B": "O", "S": "O", "F": "F", "C": "O"},
    "C": {"G": "O", "P": "O", "W": "O", "B": "O", "S": "O", "F": "O", "C": "C"},
}
# a reduced class back to a concrete class for the probe
CONCRETE = {"G": "G", "P": "P", "L": "S", "W": "W", "S": "S", "O": "G", "F": "F", "C": "C"}
CENTRE = {"G": "G", "P": "P", "W": "W", "S": "S", "F": "F", "C": "C"}   # B is drawn as W
VARIANTS = {"G": 4, "P": 4, "W": 4, "S": 2, "F": 1, "C": 1}
FRAMES = {"G": 1, "P": 1, "W": 4, "S": 1, "F": 4, "C": 4}
WATERISH = ("W", "B")


def keys():
    """Every tile key: centre + N E S W reduced classes + variant."""
    out = []
    for c, sides in SIDES.items():
        vals = sorted(set(sides.values()))
        for n in vals:
            for e in vals:
                for s in vals:
                    for w in vals:
                        for v in range(VARIANTS[c]):
                            out.append((c, n, e, s, w, v))
    return out


def probe(key):
    """The 3 x 3 grid of classes around a key's centre; diagonals follow their two neighbours when they agree."""
    c, n, e, s, w, v = key
    N, E, S_, Wc = (CONCRETE[x] for x in (n, e, s, w))
    if c == "W" and "L" in (n, e, s, w):
        # a water tile's land neighbours: grass for the quay / foam rules, sand is kept as sand
        N, E, S_, Wc = (("G" if x == "L" else CONCRETE[x]) for x in (n, e, s, w))
    if c == "G":
        # a grass tile beside a field only needs to know the ground there is lower
        N, E, S_, Wc = (("F" if x == "L" else CONCRETE[x]) for x in (n, e, s, w))

    def diag(a, b):
        return a if a == b else "G"
    return [[diag(N, Wc), N, diag(N, E)], [Wc, c, E], [diag(S_, Wc), S_, diag(S_, E)]], v


def render_key(key, grass, tulips, crop_frames):
    grid, variant = probe(key)
    T = 16
    PW = 48

    def cell(tx, ty):
        tx = max(0, min(2, tx))
        ty = max(0, min(2, ty))
        return grid[ty][tx]

    # Water and sand shapes: a bilinear field over the tile centres of the probe (1 inside, 0 outside), as the art
    # direction's sand is drawn, so every corner of a pond, a canal end or a sand bank comes out rounded, the
    # inside corners as well as the outside ones (the diagonal of a probe follows its two neighbours). Sand gets the
    # art direction's lumpy wobble and spills a pixel or two onto the grass; water edges stay level for the quay.
    def field(member, x, y, smooth=False):
        fx, fy = (x - 7.5) / 16.0, (y - 7.5) / 16.0
        x0, y0 = math.floor(fx), math.floor(fy)
        ax, ay = fx - x0, fy - y0
        if smooth:
            # smoothstep weights: corners come out as round curves, not 45-degree cuts
            ax, ay = ax * ax * (3 - 2 * ax), ay * ay * (3 - 2 * ay)

        def node(tx, ty):
            return 1.0 if member(cell(tx, ty)) else 0.0
        return (node(x0, y0) * (1 - ax) * (1 - ay) + node(x0 + 1, y0) * ax * (1 - ay)
                + node(x0, y0 + 1) * (1 - ax) * ay + node(x0 + 1, y0 + 1) * ax * ay)

    def sand_wobble(x, y):
        lx, ly = x % 16, y % 16
        return 0.1 * (math.sin(lx * 0.785 + 1.1 + variant) * 0.6 + math.sin(ly * 0.785 + 2.3) * 0.6
                      + ((hsh(lx, ly, 77, variant) % 100) / 100.0 - 0.5) * 0.5)

    # pixel class
    def nodes_linked(tx, ty, dx, dy):
        a, b = cell(tx, ty), cell(tx + dx, ty + dy)
        return a in ("P", "B") and b in ("P", "B") and 0 <= tx + dx <= 2 and 0 <= ty + dy <= 2

    cls = [[None] * PW for _ in range(PW)]
    for y in range(PW):
        for x in range(PW):
            tx, ty = x // T, y // T
            lx, ly = x % T, y % T
            c = cell(tx, ty)
            k = c
            if c == "P":
                # path network: segments from the tile centre to each linked neighbour, half-width 6 with a wobble
                d = math.hypot(lx - 7.5, ly - 7.5)
                links = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if nodes_linked(tx, ty, dx, dy)]
                for dx, dy in links:
                    # distance to the half segment from the centre to the edge in that direction
                    if dx and (lx - 7.5) * dx >= 0:
                        d = min(d, abs(ly - 7.5))
                    elif dy and (ly - 7.5) * dy >= 0:
                        d = min(d, abs(lx - 7.5))
                # a turn or a junction: the inside corner between two linked sides is filled in a curve (radius 4)
                for (dx, _a) in [l for l in links if l[0]]:
                    for (_b, dy) in [l for l in links if l[1]]:
                        u, v = (lx - 7.5) * dx, (ly - 7.5) * dy
                        a = 6.0 + 4.0
                        if 0 <= u <= a and 0 <= v <= a and math.hypot(a - u, a - v) >= 4.0:
                            d = min(d, 6.0)
                # the art direction's edge wobble: the grass leans over the path in uneven clumps
                wob = 0.9 * math.sin(lx * 0.785 + ly * 0.39 + 0.7 + variant * 1.7) + 0.6 * ((hsh(lx, ly, 3, variant) % 100) / 100 - 0.5)
                k = "P" if d <= 6.0 + wob else "G"
            elif c in ("W", "B", "S", "G"):
                if field(lambda q: q in WATERISH, x, y, True) > 0.5:
                    k = "W"
                elif field(lambda q: q == "S", x, y) + sand_wobble(x, y) > 0.42:
                    k = "S"
                else:
                    k = "G"
            elif c in ("F", "C"):
                same = (lambda q: q == c)
                r = 3
                k = c
                for qx, qy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    if not same(cell(tx + qx, ty)) and not same(cell(tx, ty + qy)) and 0 <= tx + qx <= 2 and 0 <= ty + qy <= 2:
                        cx = r - 0.5 if qx < 0 else T - r - 0.5
                        cy = r - 0.5 if qy < 0 else T - r - 0.5
                        inx = (lx < cx) if qx < 0 else (lx > cx)
                        iny = (ly < cy) if qy < 0 else (ly > cy)
                        if inx and iny and math.hypot(lx - cx, ly - cy) > r:
                            k = "G"
            cls[y][x] = k

    def C(x, y):
        return cls[max(0, min(PW - 1, y))][max(0, min(PW - 1, x))]

    frames = []
    nfr = FRAMES[key[0]]
    for fr in range(nfr):
        img = Img(T, T)
        for ly in range(T):
            for lx in range(T):
                x, y = 16 + lx, 16 + ly
                c = C(x, y)
                if c == "G":
                    col = grass[variant].name(lx, ly)
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    lower = ("P", "S", "F", "C", "W")
                    if dn in lower or rt in lower or lf in lower or up in lower:
                        col = "g2"
                    if dn == "P" and C(x, y + 2) == "P" and hsh(x, y, 3) % 3 == 0:
                        col = "g4"
                elif c == "P":
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    soft = ("G", "F", "C", "S")
                    hv = hsh(lx // 3, ly // 2, 11, variant) % 100
                    col = "d3"
                    if hv < 10:
                        col = "d2"
                    elif hv < 16:
                        col = "d4"
                    if hsh(lx, ly, 21, variant) % 61 == 0:
                        col = "d5"
                    if hsh(lx - 1, ly - 1, 21, variant) % 61 == 0:
                        col = "d2"
                    if up in soft:
                        col = "d1"
                    elif C(x, y - 2) in soft:
                        col = "d2"
                    elif lf in soft:
                        col = "d2"
                    elif dn in soft or rt in soft:
                        col = "d4"
                    if up in soft and hsh(lx, ly, 4, variant) % 4 == 0:
                        col = "g2"
                elif c == "W":
                    du = next((k for k in range(1, 10) if C(x, y - k) != "W"), 99)
                    dd = next((k for k in range(1, 4) if C(x, y + k) != "W"), 99)
                    dl = next((k for k in range(1, 3) if C(x - k, y) != "W"), 99)
                    dr = next((k for k in range(1, 3) if C(x + k, y) != "W"), 99)
                    col = "b2"
                    row = ly % 5
                    lane = ly // 5
                    drift = fr if lane % 2 else -fr
                    xx = (lx + drift + 32 + variant * 5) % 16
                    seg = xx // 8
                    u = xx % 8
                    dash = 3 + hsh(seg, lane, 2, variant) % 3
                    on = hsh(seg, lane, 3, variant) % 3 != 0
                    if row == 0 and on and u < dash:
                        col = "b4" if u == fr % dash else "b3"
                    elif row == 1 and on and 1 <= u < dash - 1:
                        col = "b1"
                    if du <= 6 and C(x, y - du) != "S":
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
                    elif du in (7, 8) and C(x, y - du) != "S":
                        col = "b1" if (du == 7 or dith(x, y, 0.5)) else col
                    elif dd == 1 or dl == 1 or dr == 1 or du == 1:
                        col = "b5" if (x + y + fr) % 4 < 2 else "b4"
                    elif dd == 2 or du == 2:
                        col = "b4" if (x + fr) % 5 == 0 else "b3"
                elif c == "S":
                    col = "d4"
                    if hsh(lx, ly, 31, variant) % 13 == 0:
                        col = "d3"
                    elif hsh(lx, ly, 32, variant) % 11 == 0:
                        col = "d5"
                    if C(x, y + 1) == "W" or C(x - 1, y) == "W" or C(x + 1, y) == "W" or C(x, y - 1) == "W":
                        col = "d3"
                    if C(x, y + 2) == "W" and dith(x, y, 0.5):
                        col = "d3"
                    if C(x, y - 1) == "G":
                        col = "g2" if dith(x, y, 0.5) else "d3"
                elif c in ("F", "C"):
                    r = ly % 5
                    col = ["d1", "d2", "d3", "d3", "d2"][r]
                    if r == 2 and hsh(lx, ly, 41) % 5 == 0:
                        col = "d4"
                    up, lf, dn, rt = C(x, y - 1), C(x - 1, y), C(x, y + 1), C(x + 1, y)
                    if up != c or lf != c:
                        col = "d1"
                    elif dn != c or rt != c:
                        col = "d2"
                img.set(lx, ly, col)
        # plants: from every field cell of the probe, so a plant from the tile below may lean into this one
        if key[0] in ("F", "C"):
            for ty in range(3):
                for tx in range(3):
                    if cell(tx, ty) != key[0]:
                        continue
                    for r, by in enumerate((5, 10, 15)):
                        for k, bx in enumerate((1, 5, 9, 13)):
                            ax = tx * 16 + bx + (r % 2) * 2 - 16
                            ay = ty * 16 + by - 16
                            if key[0] == "F":
                                spr = tulips[r % 3]
                                ph = (k + r) % 4
                                f = spr[(fr + ph) % len(spr)]
                                ox, oy = 2, 7
                            else:
                                f = crop_frames[(fr // 2 + k + r) % 2]
                                ox, oy = 2, 6
                            for j in range(f.h):
                                for i in range(f.w):
                                    vv = f.px[j * f.w + i]
                                    px_, py_ = ax + i - ox, ay + j - oy
                                    if vv and 0 <= px_ < 16 and 0 <= py_ < 16:
                                        img.px[py_ * 16 + px_] = vv
        frames.append(img)
    return frames


def crop_plant():
    """Two sway frames of a yellow crop plant: a stem, leaves, a flower head lit top-left."""
    out = []
    for f in range(2):
        img = Img(5, 7)
        s = f
        img.set(2, 6, "g1"), img.set(2, 5, "g2"), img.set(2 + (s if 0 else 0), 4, "g2")
        img.set(1, 5, "g3"), img.set(3, 4, "g2")
        for (x, y, c) in ((1, 1, "ye2"), (2, 1, "ye1"), (1, 2, "ye1"), (2, 2, "ye1"), (3, 2, "ye0"), (2, 0, "ye1"), (2, 3, "ye0")):
            img.set(x + s if x + s < 5 else x, y, c)
        out.append(img)
    return out


def build_tileset(grass, tulip_sprites):
    """Returns (tiles: list of 256-tuples, table: key string -> [tile id per frame])."""
    crop = crop_plant()
    tiles, index, table = [], {}, {}
    for key in keys():
        frames = render_key(key, grass, tulip_sprites, crop)
        ids = []
        for img in frames:
            t = tuple(img.px)
            if t not in index:
                index[t] = len(tiles)
                tiles.append(t)
            ids.append(index[t])
        table["".join(str(k) for k in key)] = ids
    return tiles, table


__all__ = ["SIDES", "CENTRE", "VARIANTS", "build_tileset", "IDX"]
