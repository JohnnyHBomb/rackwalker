"""Overworld buildings in the 3/4 front-facing convention.

Every building shows its front wall (door and windows) to the camera, and its roof from the front and a little
above: the eaves edge (or a gable end) faces the viewer, the roof plane is foreshortened above the wall, and the
ridge runs across the top. No roof is drawn as a flat plan seen from straight above.

One building per theme (debug, industrial-robotic, farm, business, pagodas-nature), each 84 x 84 with its anchor
at the bottom centre (the footprint is 5 x 5 tiles; the door is the middle of the bottom row), plus the
Netherlands landmarks that are buildings (the stepped-gable cottage and the windmill).

Light comes from the top-left: west faces and the upper roof courses are lit, east faces are in shade, and the
eaves cast a two-pixel shadow on the wall (a palette remap, one ramp step darker).
"""

from pix import DARKER, IDX, NAMES, Img, dith, hsh

W = H = 84
OX, OY = 42, 83
DOOR_X0, DOOR_X1 = 35, 48          # the door opening (the door tile is footprint column 2: px 34..49)


def remap_dark(img, x0, y0, x1, y1, steps=1, dither=False):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            v = img.get(x, y)
            if not v or (dither and not dith(x, y, 0.5)):
                continue
            for _ in range(steps):
                v = DARKER[v]
            img.set(x, y, v)


# ---------------------------------------------------------------- roofs
def front_roof(img, eave, height, xl, xr, inset_l, inset_r, mat, moss=True):
    """A roof plane facing the viewer: eaves along the bottom row `eave`, ridge `height` rows above. With an inset
    the ends are hips (the plane narrows towards the ridge; a lit west hip and a shaded east hip show as strips);
    without one they are gable ends with bargeboards. Courses run across, foreshortened."""
    top = eave - height + 1
    for y in range(top, eave + 1):
        f = (y - top) / max(1, height - 1)          # 0 at the ridge, 1 at the eaves
        l = round(xl + inset_l * (1 - f))
        r = round(xr - inset_r * (1 - f))
        for x in range(l, r + 1):
            col = roof_px(mat, x, y - top, f, x - l, r - x)
            # hip strips: the hips seen almost edge-on, lit on the west, shaded on the east
            if inset_l and x - l < 3:
                col = {"slate": ("s3", "l3", "l2"), "pantile": ("o3", "o3", "o2"), "metal": ("s4", "s3", "s3"),
                       "pagoda": ("l3", "l3", "l2")}[mat][x - l]
            if inset_r and r - x < 3:
                col = {"slate": ("l0", "l1", "l1"), "pantile": ("o0", "o1", "o1"), "metal": ("s1", "s2", "s2"),
                       "pagoda": ("l0", "l1", "l1")}[mat][r - x]
            if moss and y >= eave - 2 and hsh(x, y, 67) % 11 == 0 and mat != "metal":
                col = "g1" if hsh(x, y, 68) % 2 else "g2"
            if hsh(x, y, 66) % 61 == 0 and mat in ("slate", "pagoda"):
                col = "l0"   # a worn slate
            img.set(x, y, col)
        img.set(l - 1, y, {"slate": "l0", "pantile": "o0", "metal": "s0", "pagoda": "l0"}[mat])
        img.set(r + 1, y, {"slate": "l0", "pantile": "o0", "metal": "s0", "pagoda": "l0"}[mat])
        if not inset_l:   # bargeboard on a gable end: lit
            img.set(l, y, "w" if mat != "metal" else "s4")
        if not inset_r:
            img.set(r, y, "s2")
    # ridge: a lit cap with small bumps on top
    l = xl + inset_l
    r = xr - inset_r
    cap = {"slate": ("w", "s3", "s1"), "pantile": ("o3", "o2", "o0"), "metal": ("s4", "s3", "s1"), "pagoda": ("l3", "l2", "l0")}[mat]
    img.hline(l, r, top - 1, cap[1])
    img.hline(l, r, top, cap[0] if mat != "metal" else cap[1])
    for x in range(l, r + 1, 5):
        img.set(x + 1, top - 2, cap[0])
        img.set(x + 2, top - 2, cap[1])
        img.set(x + 3, top - 2, cap[2])
    img.set(l - 1, top - 1, cap[2])
    img.set(r + 1, top - 1, cap[2])
    # eaves: fascia board and gutter
    img.hline(xl - 1, xr + 1, eave + 1, {"slate": "s4", "pantile": "wd3", "metal": "s3", "pagoda": "r3"}[mat])
    img.hline(xl - 1, xr + 1, eave + 2, {"slate": "s1", "pantile": "wd1", "metal": "s1", "pagoda": "r1"}[mat])
    return top


def roof_px(mat, x, r, f, dl, dr):
    """Colour of one roof pixel; r = rows below the ridge, f = 0..1 from ridge to eaves."""
    if mat == "slate":
        c, sub = divmod(r, 3)
        off = 3 if c % 2 else 0
        base = ("l3", "l2", "l1") if f < 0.4 else ("l2", "l2", "l1") if f < 0.8 else ("l2", "l1", "l0")
        col = base[0] if sub == 0 else base[1]
        if sub == 2 or (sub == 1 and (x + off) % 7 == 0):
            col = base[2]
        if sub == 0 and (x + off) % 7 == 1 and f < 0.5:
            col = "s3"
        return col
    if mat == "pantile":
        c, sub = divmod(r, 4)
        u = (x + (1 if c % 3 == 2 else 0)) % 5
        col = ("o1", "o3", "o2", "o2", "o1")[u]
        if f > 0.6 and u in (2, 3):
            col = "o2" if u == 2 else "o1"
        if sub == 3:
            col = "o1" if u in (1, 2) else "o0"    # the curled lip of each course
        if sub == 0 and u == 1:
            col = "o3"
        return col
    if mat == "metal":
        u = x % 4
        col = ("s3", "s4", "s3", "s2")[u]
        if f > 0.5 and u == 1:
            col = "s3"
        if r % 9 == 8:
            col = "s2" if u != 3 else "s1"
        return col
    if mat == "pagoda":
        # rolled tiles run down the slope: seen from the front, vertical ribs with lit west sides
        u = x % 4
        col = ("l1", "l3", "l2", "l2")[u]
        if f > 0.55:
            col = ("l0", "l2", "l2", "l1")[u]
        if r % 5 == 4 and u != 1:
            col = "l1" if f < 0.55 else "l0"
        return col
    raise ValueError(mat)


def pagoda_roof(img, eave, height, xl, xr, inset):
    """A pagoda roof: a hipped front plane whose eaves sweep up at both ends, with round tile ends along the edge."""
    top = front_roof(img, eave, height, xl + 3, xr - 3, inset, inset, "pagoda", moss=False)
    # upturned eave ends: two rows that rise towards the corners
    for i in range(7):
        for side in (0, 1):
            x = xl + 3 - i if side == 0 else xr - 3 + i
            y = eave - (i * i) // 8
            img.set(x, y, "l2" if side == 0 else "l1")
            img.set(x, y + 1, "r3" if side == 0 else "r2")
            img.set(x, y + 2, "r1")
            img.set(x, y - 1, "l0")
    # round tile ends along the eave, lit top-left
    for x in range(xl + 4, xr - 3, 4):
        img.set(x, eave, "l3")
        img.set(x + 1, eave, "l2")
        img.set(x, eave + 1, "l2")
        img.set(x + 1, eave + 1, "l0")
    return top


# ---------------------------------------------------------------- walls
def brick(img, x0, y0, x1, y1, lit=True):
    for y in range(y0, y1 + 1):
        row = (y - y0) // 3
        off = 3 if row % 2 else 0
        for x in range(x0, x1 + 1):
            col = "r3" if lit and x - x0 < 3 else "r2"
            if (y - y0) % 3 == 2:
                col = "r1"
            elif (x + off) % 6 == 0:
                col = "r1"
            elif (x + off) % 6 == 1 and (y - y0) % 3 == 0:
                col = "r3"
            if hsh(x, y, 71) % 37 == 0:
                col = "r4" if col != "r1" else "r0"
            if x == x1:
                col = "r0"
            img.set(x, y, col)


def render_wall(img, x0, y0, x1, y1, base="s4", tex="s3"):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            col = base
            if (x * 3 + y * 7) % 23 == 0 or hsh(x, y, 2) % 41 == 0:
                col = tex
            if x == x1:
                col = tex
            img.set(x, y, col)


def planks(img, x0, y0, x1, y1, ramp=("r1", "r2", "r3")):
    """Vertical boards, 4 px wide, lit on their west edge; a darker gap between boards."""
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            u = (x - x0) % 4
            col = (ramp[0], ramp[2], ramp[1], ramp[1])[u]
            if hsh((x - x0) // 4, y // 9, 72) % 5 == 0 and u in (2, 3):
                col = ramp[0]   # a weathered board
            img.set(x, y, col)


def window(img, wx, wy, ww=11, wh=12, inside="rack", frame=("s3", "s1", "w"), shutters=None):
    """A framed window: lit sill and head, glass with a glint, the room behind (a lit rack, or curtains)."""
    for y in range(wy - 1, wy + wh + 1):
        for x in range(wx - 1, wx + ww + 1):
            img.set(x, y, frame[1] if (x == wx + ww or y == wy + wh) else frame[0])
    img.hline(wx - 1, wx + ww, wy - 1, frame[2])
    for y in range(wy, wy + wh):
        for x in range(wx, wx + ww):
            col = "l1"
            if inside == "rack" and wx + 2 <= x <= wx + ww - 3 and y >= wy + 2:
                col = "k1" if (x - wx) % 6 in (2, 7) else "l0"
                if (y - wy) % 2 == 0 and (x - wx) in (4, 5, 8) and hsh(x, y) % 3:
                    col = ["g4", "c1", "ye1"][hsh(x, y, 9) % 3]
            elif inside == "warm":
                col = "ye1" if y > wy + 2 else "d4"
                if x in (wx, wx + ww - 1) or y == wy:
                    col = "tu1"   # curtains
            if x == wx or y == wy:
                col = "l0" if inside != "warm" else col
            img.set(x, y, col)
    # glazing bars and a glint top-left
    img.vline(wx + ww // 2, wy, wy + wh - 1, frame[0])
    img.hline(wx, wx + ww - 1, wy + wh // 2, frame[0])
    img.set(wx + 1, wy + 1, "b5")
    img.set(wx + 2, wy + 1, "b4")
    img.set(wx + 1, wy + 2, "b4")
    # sill with its shadow
    img.hline(wx - 2, wx + ww + 1, wy + wh + 1, "w")
    img.hline(wx - 2, wx + ww + 1, wy + wh + 2, "s1")
    if shutters:
        for sx in (wx - 5, wx + ww + 1):
            for y in range(wy - 1, wy + wh + 1):
                for x in range(sx, sx + 4):
                    col = shutters[1] if (x - sx) % 2 else shutters[0]
                    if (y - wy) % 4 == 3:
                        col = shutters[2]
                    img.set(x, y, col)


def door(img, kind="double", colours=("g1", "g2", "g3"), y1=80, top=62):
    """The door, centred on the door tile, with a frame, a step and a mat-coloured threshold."""
    x0, x1 = DOOR_X0, DOOR_X1
    for y in range(top - 2, y1 + 1):
        for x in range(x0 - 2, x1 + 3):
            img.set(x, y, "s3" if x < x0 or y < top else "s1")
    img.hline(x0 - 2, x1 + 2, top - 2, "w")
    a, b, c = colours
    for y in range(top, y1 + 1):
        for x in range(x0, x1 + 1):
            col = b
            if kind == "double":
                if x in (x0, (x0 + x1) // 2 + 1):
                    col = c
                if x in ((x0 + x1) // 2, x1):
                    col = a
                if (y - top) in (3, 10) and x not in ((x0 + x1) // 2, (x0 + x1) // 2 + 1):
                    col = a
            elif kind == "plank":
                col = (a, c, b, b)[(x - x0) % 4]
            elif kind == "glass":
                col = "b2" if (x + y) % 9 else "b4"
                if x in (x0, x1, (x0 + x1) // 2) or y == top:
                    col = "s1"
            elif kind == "roller":
                col = "s3" if (y - top) % 3 else "s1"
                if x == x0:
                    col = "s4"
            if y == top:
                col = a
            img.set(x, y, col)
    if kind == "double":
        img.set((x0 + x1) // 2 - 1, top + 8, "ye1")
        img.set((x0 + x1) // 2 + 2, top + 8, "ye1")
    img.hline(x0 - 3, x1 + 3, y1 + 1, "s4")
    img.hline(x0 - 3, x1 + 3, y1 + 2, "s1")


def outline_sides(img, x0, x1, y0, y1, dark="r0"):
    img.vline(x0 - 1, y0, y1, dark)
    img.vline(x1 + 1, y0, y1, dark)
    img.hline(x0 - 1, x1 + 1, y1 + 1, dark)


# ---------------------------------------------------------------- the five themes
def bld_debug(base_hall):
    """The data hall of the art direction, its walls kept, with a hipped slate roof seen from the front."""
    img = Img(W, H)
    # the hall's walls (rows 48.. of the 84 x 86 original) sit at the bottom of the footprint
    for y in range(48, 86):
        for x in range(84):
            v = base_hall.get(x, y)
            if v:
                img.set(x, y - 2, v)
    top = front_roof(img, 43, 24, 0, 83, 14, 14, "slate")
    remap_dark(img, 3, 46, 80, 47)
    # cooling unit and mast behind the ridge, seen from the front
    ux, uy = 24, top - 10
    for y in range(uy, uy + 9):
        for x in range(ux, ux + 18):
            col = "s3" if x < ux + 3 else "s2"
            if y == uy:
                col = "s4"
            if x == ux + 17:
                col = "s0"
            img.set(x, y, col)
    for cx in (ux + 6, ux + 12):
        for y in range(uy + 3, uy + 8):
            for x in range(cx - 2, cx + 2):
                img.set(x, y, "s0" if (x + y) % 2 else "s1")
    mx = 62
    img.vline(mx, top - 16, top, "s1")
    img.vline(mx + 1, top - 15, top, "s0")
    img.set(mx, top - 17, "tu1")
    img.set(mx - 1, top - 17, "tu2")
    for y in range(top - 13, top - 8):
        for x in range(mx + 2, mx + 6):
            if (x - (mx + 3.5)) ** 2 / 4 + (y - (top - 10.5)) ** 2 / 6 <= 1:
                img.set(x, y, "s4" if x < mx + 4 else "s3")
    return img


def bld_industrial():
    """A brick works with a saw-tooth roof: three gable ends face the viewer, each with its glazed north light,
    a chimney stack, a roller door with hazard stripes and a robot-eye lamp."""
    img = Img(W, H)
    wt, wb = 44, 80
    brick(img, 3, wt, 80, wb)
    outline_sides(img, 3, 80, wt, wb)
    # stone band and plinth
    img.hline(3, 80, wt + 4, "s3")
    img.hline(3, 80, wt + 5, "s1")
    for y in range(wb - 4, wb + 1):
        for x in range(3, 81):
            img.set(x, y, "s2" if (x + (y // 2) * 3) % 8 else "s1")
    # saw-tooth roof: three teeth, steep glazed face to the right, metal slope to the left
    for k in range(3):
        x0 = 2 + k * 27
        for y in range(18, wt):
            for x in range(x0, x0 + 27):
                u = x - x0
                h = wt - 1 - y            # height above the eaves
                slope_h = u * 0.85         # the long slope rises to the right
                if h > slope_h + 0.5:
                    continue
                if u >= 22:                # the glazed steep face (a gable end of the tooth)
                    col = "b3" if (y + u) % 5 else "b4"
                    if u == 22 or (y % 6 == 0):
                        col = "s1"
                    if u == 26:
                        col = "s0"
                else:
                    col = roof_px("metal", x, wt - y, h / 22.0, 0, 0)
                    if h > slope_h - 1.5:
                        col = "s4"         # the lit edge of the slope
                img.set(x, y, col)
        img.hline(x0, x0 + 26, wt, "s1")
    img.hline(2, 82, wt + 1, "s0")
    remap_dark(img, 4, wt + 2, 79, wt + 3)
    # chimney stack
    cx = 66
    for y in range(4, 26):
        for x in range(cx, cx + 7):
            col = "r3" if x < cx + 2 else "r2"
            if (y % 3) == 2:
                col = "r1"
            if x == cx + 6:
                col = "r0"
            img.set(x, y, col)
    img.hline(cx - 1, cx + 7, 4, "s3")
    img.hline(cx - 1, cx + 7, 3, "s4")
    img.hline(cx + 1, cx + 5, 5, "k1")
    for i, (x, y) in enumerate(((cx + 2, 1), (cx + 4, 0), (cx + 1, 0))):
        img.set(x, y, "s3" if i % 2 else "s4")
    # windows with lit racks
    window(img, 8, 54, 12, 12, "rack")
    window(img, 63, 54, 12, 12, "rack")
    # roller door with hazard stripes on its frame
    door(img, "roller", ("s1", "s3", "s4"), 79, 62)
    for y in range(58, 81):
        for x in (DOOR_X0 - 3, DOOR_X0 - 2, DOOR_X1 + 2, DOOR_X1 + 3):
            img.set(x, y, "ye1" if ((y + x) // 2) % 2 else "k1")
    for x in range(DOOR_X0 - 3, DOOR_X1 + 4):
        img.set(x, 58, "ye1" if (x // 2) % 2 else "k1")
    # robot-eye lamp above the door
    img.rect(39, 50, 6, 5, "s1")
    img.hline(39, 44, 50, "s3")
    img.set(40, 52, "c1"), img.set(41, 52, "c2"), img.set(42, 52, "c1"), img.set(43, 52, "c1")
    return img


def bld_farm():
    """A red timber barn, gable end to the front under a gambrel roof, white trim, a hay loft door with a bale
    and a small silo on its east side."""
    img = Img(W, H)
    wt, wb = 40, 80
    x0, x1 = 8, 68
    planks(img, x0, wt, x1, wb, ("r1", "r2", "r3"))
    # gambrel gable: the wall rises in two pitches; roof edges overhang it
    mid = (x0 + x1) // 2
    for y in range(8, wt):
        h = wt - y
        # half-width of the gable at this height: steep lower pitch, shallow upper pitch
        half = (x1 - x0) / 2 - max(0, h) * 0.28 if h < 16 else (x1 - x0) / 2 - 16 * 0.28 - (h - 16) * 1.05
        if half < 1:
            continue
        for x in range(round(mid - half), round(mid + half) + 1):
            u = (x - x0) % 4
            img.set(x, y, ("r1", "r3", "r2", "r2")[u])
    # the roof seen just past the gable edge: shingle strips along both pitches, lit west, shaded east
    for y in range(6, wt + 2):
        h = wt - y
        half = (x1 - x0) / 2 - max(0, h) * 0.28 if h < 16 else (x1 - x0) / 2 - 16 * 0.28 - (h - 16) * 1.05
        if half < -1:
            continue
        for d in range(1, 5):
            xl, xr = round(mid - half) - d, round(mid + half) + d
            img.set(xl, y - d // 2, ("s4", "l3", "l2", "l1")[d - 1])
            img.set(xr, y - d // 2, ("s2", "l1", "l1", "l0")[d - 1])
    # white trim on the gable edges and corners
    for y in range(8, wt):
        h = wt - y
        half = (x1 - x0) / 2 - max(0, h) * 0.28 if h < 16 else (x1 - x0) / 2 - 16 * 0.28 - (h - 16) * 1.05
        if half >= 1:
            img.set(round(mid - half), y, "w")
            img.set(round(mid + half), y, "s3")
    img.vline(x0, wt, wb, "w")
    img.vline(x1, wt, wb, "s3")
    img.hline(x0, x1, wt, "w")
    remap_dark(img, x0 + 1, wt + 1, x1 - 1, wt + 2)
    outline_sides(img, x0, x1, wt - 1, wb, "r0")
    # hay loft door with a bale and a hoist beam
    lx, ly = mid - 6, 18
    img.rect(lx - 1, ly - 1, 14, 13, "w")
    img.rect(lx, ly, 12, 11, "wd0")
    for y in range(ly + 4, ly + 11):
        for x in range(lx + 1, lx + 11):
            img.set(x, y, "ye1" if (x + y) % 3 else "ye0")
    img.hline(lx + 1, lx + 10, ly + 4, "ye2")
    img.hline(mid - 1, mid + 1, ly - 4, "wd2")
    img.vline(mid, ly - 4, ly - 2, "wd1")
    # big doors: white X braces on red
    door(img, "plank", ("r1", "r3", "r2"), 79, 56)
    for i in range(DOOR_X1 - DOOR_X0 + 1):
        y = 56 + round(i * (79 - 56) / (DOOR_X1 - DOOR_X0))
        img.set(DOOR_X0 + i, y, "w")
        img.set(DOOR_X1 - i, y, "s4")
    img.rect(DOOR_X0 - 2, 54, DOOR_X1 - DOOR_X0 + 5, 2, "w")
    window(img, 14, 54, 9, 8, "warm", ("w", "s2", "w"))
    # silo on the east: a corrugated cylinder with a dome cap, lit on its west side
    sx0, sx1 = 70, 82
    for y in range(22, 81):
        for x in range(sx0, sx1 + 1):
            u = (x - sx0) / (sx1 - sx0)
            col = "s4" if u < 0.2 else "s3" if u < 0.5 else "s2" if u < 0.85 else "s1"
            if y % 6 == 5:
                col = NAMES[DARKER[IDX[col]]]
            img.set(x, y, col)
    for y in range(14, 22):
        hw = ((sx1 - sx0) / 2) * (1 - ((22 - y) / 8.5) ** 2) ** 0.5
        c = (sx0 + sx1) / 2
        for x in range(round(c - hw), round(c + hw) + 1):
            img.set(x, y, "s4" if x < c - 1 else "s3" if x < c + 3 else "s1")
    img.vline(sx1 + 1, 22, 80, "s0")
    img.hline(sx0, sx1, 81, "s0")
    return img


def bld_business():
    """A small glass office: a curtain wall with mullions that catches the sky, a parapet with the flat roof deck
    seen just above it (plant and a skylight on it), a canopy over glass doors."""
    img = Img(W, H)
    wt, wb = 30, 80
    x0, x1 = 4, 79
    # the roof deck, seen from slightly above: a narrow band behind the parapet
    for y in range(18, 24):
        for x in range(x0 + 2, x1 - 1):
            img.set(x, y, "s2" if (x + y) % 7 else "s1")
    img.hline(x0 + 2, x1 - 2, 18, "s3")
    # plant on the deck: an air handler and a skylight
    for y in range(9, 21):
        for x in range(54, 72):
            col = "s3" if x < 57 else "s2"
            if y == 9:
                col = "s4"
            if x == 71:
                col = "s0"
            if 12 <= y <= 18 and 59 <= x <= 69 and (x % 2 == 0):
                col = "s1"
            img.set(x, y, col)
    for y in range(14, 20):
        for x in range(14, 32):
            img.set(x, y, "b4" if (x - y) % 6 < 2 else "b3")
    img.hline(14, 31, 13, "s4")
    img.hline(14, 31, 20, "s1")
    # parapet: a lit coping and a stone band
    for y in range(24, wt):
        for x in range(x0, x1 + 1):
            img.set(x, y, "s3" if y > 25 else "s4")
    img.hline(x0, x1, wt - 1, "s1")
    # curtain wall: glass panels with a diagonal sky reflection, mullions
    for y in range(wt, wb - 3):
        for x in range(x0, x1 + 1):
            col = "b1" if y < 46 else "b2"
            if (x + y) % 23 < 4:
                col = "b3"
            if (x + y) % 23 == 1:
                col = "b4"
            if (x - x0) % 12 == 0:
                col = "s3"
            if (x - x0) % 12 == 11:
                col = "s1"
            if (y - wt) % 13 == 0:
                col = "s3"
            img.set(x, y, col)
    remap_dark(img, x0, wt, x1, wt + 1)
    # a lit desk lamp or two and the edge of a rack inside
    for (lx, ly) in ((12, 38), (66, 52), (22, 54)):
        img.set(lx, ly, "ye2")
        img.set(lx + 1, ly, "ye1")
    for y in range(wb - 3, wb + 1):
        img.hline(x0, x1, y, "s2" if y < wb else "s0")
    outline_sides(img, x0, x1, 24, wb, "s0")
    # canopy and glass doors
    for y in range(56, 59):
        img.hline(DOOR_X0 - 6, DOOR_X1 + 6, y, ("s4", "s3", "s1")[y - 56])
    remap_dark(img, DOOR_X0 - 5, 59, DOOR_X1 + 5, 60)
    door(img, "glass", ("s1", "b2", "b4"), 79, 62)
    # sign above the canopy: a plate with three dots, no words
    img.rect(32, 48, 20, 6, "s0")
    img.hline(32, 51, 48, "s1")
    for i, c in enumerate(("c1", "g4", "ye1")):
        img.set(37 + i * 5, 51, c)
    return img


def bld_pagoda():
    """A two-tier pagoda hall: white plaster and red lacquer posts, two roofs with upturned eaves seen from the
    front, a finial, lanterns either side of the door."""
    img = Img(W, H)
    # lower storey
    wt, wb = 50, 80
    for y in range(wt, wb + 1):
        for x in range(8, 76):
            col = "w" if (x * 5 + y * 3) % 29 else "s4"
            img.set(x, y, col)
    for px in (8, 24, 59, 75):
        for y in range(wt, wb + 1):
            img.set(px, y, "r3")
            img.set(px + 1, y, "r2")
            img.set(px + 2, y, "r1")
    img.hline(8, 77, wt, "r2")
    img.hline(8, 77, wt + 1, "r1")
    # lattice windows
    for wx in (12, 63):
        for y in range(58, 70):
            for x in range(wx, wx + 9):
                col = "d4" if (x + y) % 3 else "wd1"
                if x in (wx, wx + 8) or y in (58, 69):
                    col = "wd1"
                img.set(x, y, col)
    # stone base
    for y in range(wb - 2, wb + 1):
        img.hline(6, 78, y, ("s3", "s2", "s1")[y - (wb - 2)])
    # lower roof
    pagoda_roof(img, wt - 3, 10, 2, 82, 10)
    remap_dark(img, 10, wt + 2, 74, wt + 3)
    # upper storey: narrower
    ut, ub = 24, 37
    for y in range(ut, ub + 1):
        for x in range(22, 62):
            img.set(x, y, "w" if (x + y) % 31 else "s4")
    for px in (22, 40, 59):
        for y in range(ut, ub + 1):
            img.set(px, y, "r3"), img.set(px + 1, y, "r2"), img.set(px + 2, y, "r1")
    for x in range(28, 37, 3):
        img.vline(x, 28, 34, "wd1")
    for x in range(46, 56, 3):
        img.vline(x, 28, 34, "wd1")
    pagoda_roof(img, ut - 2, 9, 14, 70, 9)
    remap_dark(img, 22, ut + 0, 61, ut + 1)
    # finial
    for y in range(0, 7):
        img.set(42, y, "ye1" if y % 2 else "ye2")
        img.set(43, y, "ye0")
    door(img, "plank", ("r1", "r3", "r2"), 79, 62)
    # paper lanterns either side of the door
    for lx in (29, 52):
        img.vline(lx + 1, 56, 57, "k1")
        for y in range(58, 63):
            for x in range(lx, lx + 3):
                img.set(x, y, "tu2" if x == lx else "tu1")
        img.set(lx + 1, 63, "ye1")
    return img


# ---------------------------------------------------------------- Netherlands landmarks
def cottage_nl(base):
    """The stepped-gable cottage: the gable faces the viewer; behind it the pantile roof runs back, so from the
    front and a little above only short, foreshortened slopes show past the steps and above the apex."""
    img = Img(base.w, base.h - 10)
    x0, x1 = 4, 49
    ridge = (x0 + x1) // 2
    gtop = 10
    # the roof behind the gable: its apex 7 px above the gable's, slopes falling at the gable's own pitch
    for y in range(gtop - 7, gtop + 24):
        for x in range(x0 - 3, x1 + 4):
            dx = abs(x - ridge - 0.5)
            if y < gtop - 7 + dx * 0.95:
                continue
            west = x <= ridge
            c, sub = divmod(y - (gtop - 7), 3)
            u = (x + (2 if c % 2 else 0)) % 5
            col = ("o1", "o3", "o2", "o2", "o1")[u] if west else ("o0", "o2", "o1", "o1", "o0")[u]
            if sub == 2:
                col = "o1" if west else "o0"
            img.set(x, y, col)
    for y in range(gtop - 7, gtop + 24):
        dx = (y - (gtop - 7)) / 0.95
        img.set(round(ridge - dx) - 1, y, "o0")
        img.set(round(ridge + dx) + 2, y, "o0")
    img.vline(ridge, gtop - 7, gtop, "o3")
    img.vline(ridge + 1, gtop - 7, gtop, "o0")
    # chimney behind, on the east slope
    cx = 36
    for y in range(gtop - 9, gtop + 4):
        for x in range(cx, cx + 6):
            col = "r3" if x < cx + 2 else "r2"
            if y % 3 == 2:
                col = "r1"
            if x == cx + 5:
                col = "r0"
            img.set(x, y, col)
    img.hline(cx - 1, cx + 6, gtop - 9, "s4")
    img.hline(cx + 1, cx + 4, gtop - 8, "k1")
    # the gable and front wall of the original, without its roof
    roof = {"o0", "o1", "o2", "o3"}
    for y in range(20, base.h):
        for x in range(base.w):
            v = base.get(x, y)
            if not v:
                continue
            n = NAMES[v]
            if y < 34 and (n in roof or x <= x0 - 3 or x >= x1 + 3 or (n == "g2")):
                continue
            img.set(x, y - 10, v)
    return img


def windmill_base(base):
    """The windmill with a front-facing skirt roof over its brick base: the base reads as a little building with
    its eaves to the viewer rather than a block seen from above."""
    img = base.copy()
    # find the brick base's top: the first row from the bottom where brick ends
    rows = [y for y in range(base.h) if any(NAMES[base.get(x, y)].startswith("r") for x in range(base.w))]
    btop = min(rows)
    xl = min(x for x in range(base.w) if NAMES[base.get(x, btop + 2)].startswith("r"))
    xr = max(x for x in range(base.w) if NAMES[base.get(x, btop + 2)].startswith("r"))
    # a short skirt roof of pantiles between the stage and the base
    for y in range(btop - 1, btop + 4):
        for x in range(xl - 1, xr + 2):
            u = x % 5
            col = ("o1", "o3", "o2", "o2", "o1")[u]
            if y == btop + 3:
                col = "o1" if u in (1, 2) else "o0"
            img.set(x, y, col)
    img.hline(xl - 1, xr + 1, btop + 4, "wd1")
    remap_dark(img, xl, btop + 5, xr, btop + 6)
    return img


THEMES = ("debug", "industrial-robotic", "farm", "business", "pagodas-nature")


def make_buildings(add_sprite, author):
    hall = author.make_datacentre()
    add_sprite("bld_debug", bld_debug(hall), OX, OY)
    add_sprite("bld_industrial-robotic", bld_industrial(), OX, OY)
    add_sprite("bld_farm", bld_farm(), OX, OY)
    add_sprite("bld_business", bld_business(), OX, OY)
    add_sprite("bld_pagodas-nature", bld_pagoda(), OX, OY)
    c = cottage_nl(author.make_cottage())
    add_sprite("cottage_nl", c, c.w // 2, c.h - 1)
    wm = windmill_base(author.make_windmill())
    add_sprite("windmill", wm, 22, wm.h - 1)
