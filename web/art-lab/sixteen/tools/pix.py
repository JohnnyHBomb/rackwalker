"""Indexed-colour pixel canvas and the direction's palette.

Every colour of the art lab lives in PALETTE (63 colours plus transparent). Materials are ramps of 4-6
steps that shift hue: shadows lean blue/purple, highlights lean yellow, so light reads as warm and
coming from the top-left.
"""

import math

# name -> hex. Order is the palette index order (index 0 is transparent).
PALETTE = [
    ("clear", "000000"),
    # outline darks (coloured, never pure black)
    ("k0", "140c1c"), ("k1", "2a1d33"),
    # stone / neutral ramp, cool shadow -> warm light
    ("s0", "3b3552"), ("s1", "5c5670"), ("s2", "85808f"), ("s3", "b3aeb0"), ("s4", "e2dfd2"),
    ("w", "fbf7e8"),
    # grass and foliage
    ("t0", "0f2328"), ("g0", "173b37"), ("g1", "1f5a3c"), ("g2", "2f7d3a"), ("g3", "55a03c"),
    ("g4", "8cc24a"), ("g5", "c8e070"),
    # earth: path, field soil, sand
    ("d0", "3d2228"), ("d1", "6b3a2e"), ("d2", "96603c"), ("d3", "bd8b52"), ("d4", "dfb978"),
    ("d5", "f2dca0"),
    # water
    ("b0", "142046"), ("b1", "1c3a78"), ("b2", "2560a8"), ("b3", "3b8cd0"), ("b4", "73c0e8"),
    ("b5", "c8f0ff"),
    # brick
    ("r0", "3f1621"), ("r1", "6e2428"), ("r2", "9c3a32"), ("r3", "c45e44"), ("r4", "e48c64"),
    # slate
    ("l0", "1e2440"), ("l1", "2f3a5e"), ("l2", "46597e"), ("l3", "6c84a0"),
    # roof tile orange
    ("o0", "5e2218"), ("o1", "a03a22"), ("o2", "d0602c"), ("o3", "f09048"),
    # wood
    ("wd0", "2e1a1a"), ("wd1", "553222"), ("wd2", "80502e"), ("wd3", "ab7a44"), ("wd4", "d4a868"),
    # tulips: red, yellow, pink
    ("tu0", "8a1030"), ("tu1", "d8283c"), ("tu2", "ff6a6a"),
    ("ye0", "b07818"), ("ye1", "f0c030"), ("ye2", "fff27a"),
    ("pk0", "a02870"), ("pk1", "e05aa0"), ("pk2", "ffa8d0"),
    # skin
    ("sk0", "6b3830"), ("sk1", "b8684a"), ("sk2", "e8a678"), ("sk3", "ffd8b0"),
    # guide glow (cyan)
    ("c0", "1aa8d8"), ("c1", "5ef0ff"), ("c2", "d8ffff"),
    # violet accents
    ("pu0", "4a2a6a"), ("pu1", "8a58b8"),
]
NAMES = [n for n, _ in PALETTE]
IDX = {n: i for i, n in enumerate(NAMES)}
assert len(PALETTE) - 1 <= 64, len(PALETTE)

RAMPS = {
    "k": ["k0", "k1"],
    "s": ["s0", "s1", "s2", "s3", "s4", "w"],
    "g": ["t0", "g0", "g1", "g2", "g3", "g4", "g5"],
    "d": ["d0", "d1", "d2", "d3", "d4", "d5"],
    "b": ["b0", "b1", "b2", "b3", "b4", "b5"],
    "r": ["r0", "r1", "r2", "r3", "r4"],
    "l": ["l0", "l1", "l2", "l3"],
    "o": ["o0", "o1", "o2", "o3"],
    "wd": ["wd0", "wd1", "wd2", "wd3", "wd4"],
    "tu": ["tu0", "tu1", "tu2"],
    "ye": ["ye0", "ye1", "ye2"],
    "pk": ["pk0", "pk1", "pk2"],
    "sk": ["sk0", "sk1", "sk2", "sk3"],
    "c": ["c0", "c1", "c2"],
    "pu": ["pu0", "pu1"],
}


def _shift_tables():
    """darker[i] / lighter[i]: one ramp step down / up, used for cast shadows and the guide's glow."""
    darker = list(range(len(NAMES)))
    lighter = list(range(len(NAMES)))
    for ramp in RAMPS.values():
        for j, n in enumerate(ramp):
            darker[IDX[n]] = IDX[ramp[max(0, j - 1)]] if j > 0 else IDX["k0"]
            lighter[IDX[n]] = IDX[ramp[min(len(ramp) - 1, j + 1)]]
    darker[0] = 0
    lighter[0] = 0
    # shadow on darkest water stays deep blue, on outline stays outline
    darker[IDX["b0"]] = IDX["b0"]
    darker[IDX["k0"]] = IDX["k0"]
    # cast shadows on grass skip a little further toward blue-green
    return darker, lighter


DARKER, LIGHTER = _shift_tables()

# glow table: a colour seen through the guide's cyan glow
GLOW = list(range(len(NAMES)))
for _n in NAMES[1:]:
    _i = IDX[_n]
    GLOW[_i] = LIGHTER[_i]
for _n in ("g2", "g3"):
    GLOW[IDX[_n]] = IDX["g4"]
for _n in ("g4", "g5"):
    GLOW[IDX[_n]] = IDX["g5"]
for _n in ("d2", "d3"):
    GLOW[IDX[_n]] = IDX["d4"]


def ramp(name, i):
    r = RAMPS[name]
    return r[max(0, min(len(r) - 1, i))]


def hsh(*a):
    """Deterministic integer hash (no randomness: every render is identical)."""
    h = 2166136261
    for v in a:
        h ^= (int(v) & 0xFFFFFFFF)
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 13
    return h


def rnd(*a):
    return (hsh(*a) % 10000) / 10000.0


BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def dith(x, y, f):
    """Ordered dither: True for a fraction f of pixels."""
    return (BAYER4[y & 3][x & 3] + 0.5) / 16.0 < f


class Img:
    def __init__(self, w, h, fill=0):
        self.w, self.h = w, h
        self.px = [fill] * (w * h)

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y * self.w + x]
        return 0

    def name(self, x, y):
        return NAMES[self.get(x, y)]

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = IDX[c] if isinstance(c, str) else c

    def setif(self, x, y, c):
        """Set only over already-opaque pixels."""
        if self.get(x, y):
            self.set(x, y, c)

    def rect(self, x0, y0, w, h, c):
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                self.set(x, y, c)

    def hline(self, x0, x1, y, c):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            self.set(x, y, c)

    def vline(self, x, y0, y1, c):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            self.set(x, y, c)

    def blit(self, src, ox, oy):
        for y in range(src.h):
            for x in range(src.w):
                v = src.px[y * src.w + x]
                if v:
                    self.set(ox + x, oy + y, v)

    def copy(self):
        o = Img(self.w, self.h)
        o.px = list(self.px)
        return o

    def mirror(self):
        o = Img(self.w, self.h)
        for y in range(self.h):
            for x in range(self.w):
                o.px[y * self.w + x] = self.px[y * self.w + (self.w - 1 - x)]
        return o

    def opaque(self, x, y):
        return self.get(x, y) != 0

    def outline(self, inner_map, default="k1", sides="all"):
        """Selective outline: each transparent pixel next to the sprite takes the darkest shade of the
        material it touches (inner_map: palette name -> outline name)."""
        out = self.copy()
        for y in range(self.h):
            for x in range(self.w):
                if self.get(x, y):
                    continue
                for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                    v = self.get(x + dx, y + dy)
                    if v:
                        out.set(x, y, inner_map.get(NAMES[v], default))
                        break
        return out

    def rows(self):
        return [enc_row(self.px[y * self.w:(y + 1) * self.w]) for y in range(self.h)]


ALPH = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
assert len(ALPH) >= len(PALETTE)


def enc_row(vals):
    return "".join(ALPH[v] for v in vals)


def from_rows(rows, legend):
    h = len(rows)
    w = max(len(r) for r in rows)
    img = Img(w, h)
    for y, r in enumerate(rows):
        assert len(r) == w, (r, len(r), w)
        for x, ch in enumerate(r):
            if ch in (".", " "):
                continue
            img.set(x, y, legend[ch])
    return img


def shade_sphere(nx, ny, nz, light=(-0.55, -0.7, 0.45)):
    lx, ly, lz = light
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    return max(0.0, (nx * lx + ny * ly + nz * lz) / ln)
