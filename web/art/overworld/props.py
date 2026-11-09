"""Country landmarks, villagers and overworld markers, drawn in the art direction's palette and light.

United Kingdom: a red telephone kiosk, a pillar box, a tea stall, a small rain cloud; villagers: a badger in a
bowler hat and a red fox. Netherlands: a cheese cart (the windmill, cottage, tulips, bicycle and
hedgehog come from the art direction). Anywhere else: a well and haystacks; villager: a rabbit holding a carrot.
"""

from pix import Img, dith, from_rows, hsh


def phone_box():
    w, h = 14, 30
    img = Img(w, h)
    for y in range(3, h):
        for x in range(1, 13):
            col = "tu1" if x < 3 else "tu0" if x > 10 else "r3"
            img.set(x, y, col)
    # domed roof seen from the front: a lit crown, the dark eave below
    for x in range(2, 12):
        img.set(x, 1, "r3" if x < 6 else "r2")
    for x in range(1, 13):
        img.set(x, 2, "tu2" if x < 5 else "tu1")
    img.hline(0, 13, 3, "r0")
    # the sign band, light inside
    img.hline(3, 10, 5, "w")
    img.hline(3, 10, 6, "s3")
    # glazing: a grid of small panes on the front, a glint top-left
    for y in range(8, 24):
        for x in range(3, 11):
            col = "l1" if (x - 3) % 3 and (y - 8) % 3 else "r2"
            img.set(x, y, col)
    img.set(4, 9, "b5"), img.set(5, 9, "b4")
    img.set(9, 15, "tu1")  # the handle
    img.hline(1, 12, h - 2, "r1")
    img.hline(1, 12, h - 1, "r0")
    img.vline(0, 4, h - 1, "r0"), img.vline(13, 4, h - 1, "r0")
    return img


def pillar_box():
    rows = [
        "..aaaa..",
        ".abbbba.",
        "abccccba",
        "kddddddk",
        "kceeeedk",
        "kcffffdk",
        "kcccccdk",
        "kcwwwcdk",
        "kccccddk",
        "kccccddk",
        "kccccddk",
        "kccccddk",
        "kccccddk",
        "kssssssk",
        ".kkkkkk.",
    ]
    return from_rows(rows, {"a": "r0", "b": "tu2", "c": "tu1", "d": "tu0", "e": "k0", "f": "r1", "k": "r0",
                            "w": "s4", "s": "s1"})


def teapot_stall():
    """A tea stall: a little table with a cloth, a big teapot (steam rising) and two cups."""
    out = []
    for f in range(4):
        img = Img(30, 26)
        # table and cloth
        for y in range(15, 19):
            for x in range(2, 28):
                col = "w" if (x // 3 + y) % 2 else "tu2"
                if y == 18:
                    col = "tu1" if (x // 3) % 2 else "s3"
                img.set(x, y, col)
        for y in range(19, 25):
            img.set(4, y, "wd2"), img.set(5, y, "wd1")
            img.set(24, y, "wd2"), img.set(25, y, "wd1")
        # teapot: body lit top-left
        for y in range(6, 15):
            for x in range(7, 19):
                d = ((x - 12.5) / 6.0) ** 2 + ((y - 10.5) / 4.5) ** 2
                if d <= 1:
                    col = "b4" if d < 0.3 and x < 12 else "b3" if x < 14 else "b2"
                    if d > 0.75:
                        col = "b1"
                    img.set(x, y, col)
        img.hline(10, 15, 5, "b2"), img.set(12, 4, "w"), img.set(13, 4, "s3")
        for i, (x, y) in enumerate(((19, 9), (20, 8), (21, 7), (22, 6))):
            img.set(x, y, "b2")
        img.set(6, 9, "b1"), img.set(5, 10, "b1"), img.set(5, 11, "b1"), img.set(6, 12, "b1")
        # cups
        for cx in (20, 24):
            img.hline(cx, cx + 2, 13, "w"), img.hline(cx, cx + 2, 14, "s3"), img.set(cx + 3, 13, "s3")
        # steam: three wisps that rise one pixel a frame
        for k, sx in enumerate((22, 23, 21)):
            y = 4 - ((f + k * 2) % 4)
            if y >= 0:
                img.set(sx, y, "s4" if k % 2 else "w")
        out.append(img)
    return out


def rain_cloud():
    """A small cloud with a drizzle that falls in four frames (the affectionate local weather)."""
    out = []
    for f in range(4):
        img = Img(30, 30)
        for (cx, cy, r) in ((9, 9, 6), (16, 7, 7), (22, 10, 5)):
            for y in range(cy - r, cy + r + 1):
                for x in range(cx - r, cx + r + 1):
                    d = ((x - cx) ** 2 + (y - cy) ** 2) / (r * r)
                    if d <= 1 and y <= 13:
                        col = "w" if d < 0.35 and (x < cx and y < cy) else "s4" if y < 10 else "s3"
                        if y >= 12:
                            col = "s2"
                        img.set(x, y, col)
        for x in range(4, 27):
            if img.get(x, 13):
                img.set(x, 14, "s1")
        for k in range(7):
            x = 6 + k * 3
            y0 = 16 + ((f * 3 + hsh(k, 5) % 4 * 2) % 12)
            img.set(x, y0, "b4"), img.set(x, y0 + 1, "b3")
        out.append(img)
    return out


def cheese_cart():
    img = Img(30, 20)
    # cart bed
    for y in range(11, 15):
        for x in range(2, 26):
            img.set(x, y, "wd3" if y == 11 else "wd2" if y < 14 else "wd1")
    img.hline(26, 29, 12, "wd2")   # the handle
    # wheels of cheese stacked, lit top-left
    for (cx, cy) in ((7, 8), (14, 8), (21, 8), (10, 4), (17, 4)):
        for y in range(cy - 3, cy + 3):
            for x in range(cx - 4, cx + 4):
                if ((x - cx + 0.5) / 4) ** 2 + ((y - cy + 0.5) / 3) ** 2 <= 1:
                    col = "ye2" if y < cy - 1 else "ye1"
                    if x >= cx + 2 or y == cy + 2:
                        col = "ye0"
                    img.set(x, y, col)
        img.set(cx - 2, cy - 2, "w")
    # a wedge cut out of one wheel
    img.set(21, 7, "d4"), img.set(22, 7, "d4"), img.set(22, 6, "d5")
    # wheels
    for wx in (7, 20):
        for y in range(14, 20):
            for x in range(wx - 3, wx + 3):
                d = ((x - wx + 0.5) / 3) ** 2 + ((y - 16.5) / 3) ** 2
                if d <= 1:
                    img.set(x, y, "wd0" if d > 0.5 else "wd2")
        img.set(wx, 16, "wd4")
    return img


def well():
    img = Img(26, 30)
    # stone ring, front face
    for y in range(17, 28):
        for x in range(2, 24):
            course = (y - 17) // 3
            off = 3 if course % 2 else 0
            col = "s3" if x < 8 else "s2" if x < 19 else "s1"
            if (y - 17) % 3 == 2 or (x + off) % 6 == 0:
                col = "s0"
            img.set(x, y, col)
    # the opening seen from slightly above: dark water inside the lit rim
    for x in range(3, 23):
        img.set(x, 15, "s4"), img.set(x, 16, "b0" if 5 < x < 21 else "s3")
    img.hline(2, 23, 14, "s3")
    # posts and a little pantile roof facing the viewer
    for px in (4, 20):
        img.vline(px, 5, 15, "wd2"), img.vline(px + 1, 5, 15, "wd1")
    for y in range(1, 6):
        for x in range(1 + (5 - y) // 2, 25 - (5 - y) // 2):
            u = x % 5
            col = ("o1", "o3", "o2", "o2", "o1")[u]
            if y == 5:
                col = "o0"
            img.set(x, y, col)
    img.hline(5, 20, 0, "o2")
    # winch and bucket
    img.hline(6, 19, 8, "wd3")
    img.vline(12, 9, 11, "s1")
    img.rect(11, 12, 3, 2, "wd2")
    return img


def haystack():
    img = Img(28, 20)
    for y in range(2, 19):
        for x in range(1, 27):
            d = ((x - 13.5) / 13) ** 2 + ((y - 19) / 17) ** 2
            if d <= 1:
                col = "ye2" if x < 10 and y < 10 else "ye1"
                if x > 18 or y > 15:
                    col = "ye0"
                if hsh(x, y, 3) % 5 == 0:
                    col = "d3"
                if (x + y * 2) % 7 == 0:
                    col = "d4"
                img.set(x, y, col)
    for x in range(1, 27):
        img.set(x, 19, "d2")
    return img


def badger():
    """A badger in a bowler hat, front view; two idle frames (an ear twitch and a blink)."""
    base = [
        "....bbbbbbb.....",
        "...bhhhhhhhb....",
        "...bhhhhhhhb....",
        ".bbbbbbbbbbbbb..",
        "..kwwkkkkwwk....",
        "..kwwkwwkwwk....",
        ".kkwkkwwkkwkk...",
        ".kkekkwwkkekk...",
        ".kkkkwwwwkkkk...",
        "..kgggwwgggk....",
        "..ggggnnggggk...",
        ".gggggggggggg...",
        ".gglggggggllg...",
        ".ggggggggggggk..",
        "..ggggggggggk...",
        "..kk.....kk.....",
    ]
    legend = {"b": "k1", "h": "s1", "k": "k1", "w": "s4", "e": "k0", "g": "s1", "n": "k0", "l": "s2"}
    a = from_rows(base, legend)
    # hat band and a lit brim edge
    for x in range(4, 11):
        a.set(x, 2, "pu0")
    a.set(4, 1, "s2")
    b = a.copy()
    b.set(3, 7, "s1"), b.set(10, 7, "s1")   # blink
    return [a, b]


def fox():
    """A red fox with a white chest and a white-tipped tail, front view; two idle frames (a blink)."""
    base = [
        "..xx......xx.....",
        "..xOx....xOx.....",
        "..xORx..xORx.....",
        "..xOORxxxORRx....",
        "..xOOOORRRRRx....",
        "..xOEeRRREeRx....",
        "..xOEERRREERx....",
        "..xWOORRRRRWx....",
        "...xWWRKRRWx.....",
        "....xWWWWWx......",
        "...xOxxxxxRx.....",
        "..xORRWWWRRRx....",
        "..xORWWWWWRRx..xx",
        "..xORWWWWWRRx.xOx",
        "..xORRRRRRRRxxOWx",
        "..xOORRRRRRRRRWx.",
        "...xRRxxxxRRxxx..",
        "...xKKx..xKKx....",
        "....xx....xx.....",
    ]
    legend = {"x": "r0", "R": "r2", "O": "r3", "W": "w", "E": "k0", "e": "w", "K": "k0"}
    a = from_rows(base, legend)
    b = a.copy()
    for x in (4, 5, 9, 10):
        b.set(x, 5, "r2")  # blink: the eyes close to a dark line
    return [a, b]


def rabbit():
    base = [
        "...ww...ww....",
        "...wp...wp....",
        "...wp...wp....",
        "...ww...ww....",
        "..wwwwwwwww...",
        "..wwewwwewws..",
        "..wwwwnwwwws..",
        "...wwwwwwws...",
        "..wwwwwwwwws..",
        ".wwwwwwwwwwso.",
        ".wwwwwwwwwwoO.",
        ".wwwwwwwwwsOg.",
        ".swwwwwwwwsgg.",
        "..sssssssss...",
        "..ss.....ss...",
    ]
    legend = {"w": "s4", "p": "pk2", "e": "k0", "n": "pk1", "s": "s3", "o": "o2", "O": "o3", "g": "g3"}
    a = from_rows(base, legend)
    b = a.copy()
    # ears dip on the second frame
    for y in range(0, 2):
        for x in range(14):
            b.set(x, y, 0)
    return [a, b]


def airfield_pad():
    img = Img(44, 30)
    for y in range(2, 28):
        for x in range(2, 42):
            col = "s2" if (x + y) % 11 else "s1"
            if x in (2, 41) or y in (2, 27):
                col = "s0"
            img.set(x, y, col)
    # a painted marking (a circle and a cross) so the pad reads without colour
    for y in range(2, 28):
        for x in range(2, 42):
            d = ((x - 21.5) / 15) ** 2 + ((y - 14.5) / 10) ** 2
            if 0.8 <= d <= 1.0:
                img.set(x, y, "w")
    for i in range(-5, 6):
        img.set(21 + i, 14 + i // 2, "ye1"), img.set(21 + i, 14 - i // 2, "ye1")
    return img


def airfield_sign():
    """A windsock on a mast and a sign with a plane glyph and a 'not open yet' barrier stripe."""
    out = []
    for f in range(2):
        img = Img(30, 30)
        img.vline(4, 2, 29, "s3"), img.vline(5, 2, 29, "s1")
        for i in range(5):
            x = 6 + i * 3
            y = 3 + (i * (1 + f)) // 3
            for j in range(3):
                for k in range(3):
                    img.set(x + k, y + j, ("tu1" if i % 2 == 0 else "w") if j < 2 else "tu0")
        # sign board
        for y in range(14, 24):
            for x in range(10, 29):
                img.set(x, y, "l1" if 10 < x < 28 and 14 < y < 23 else "s3")
        for (x, y) in ((18, 16), (19, 16), (17, 17), (18, 17), (19, 17), (20, 17), (21, 17), (14, 18), (15, 18), (16, 18),
                       (17, 18), (18, 18), (19, 18), (20, 18), (21, 18), (22, 18), (23, 18), (17, 19), (18, 19), (19, 19),
                       (18, 20), (22, 17), (22, 19)):
            img.set(x, y, "w")
        for x in range(11, 28):
            img.set(x, 22, "ye1" if (x // 2) % 2 else "k1")
        img.vline(12, 24, 29, "wd2"), img.vline(26, 24, 29, "wd2")
        out.append(img)
    return out


def marker():
    """The down-pointing marker in each doorway (a shape, so it reads without colour), bobbing over two frames."""
    rows = [
        "kkkkkkkkk",
        "kwwwwwwwk",
        ".kwwwwwk.",
        "..kwwwk..",
        "...kwk...",
        "....k....",
    ]
    a = from_rows(rows, {"k": "k1", "w": "ye2"})
    b = Img(9, 7)
    b.blit(a, 0, 1)
    a2 = Img(9, 7)
    a2.blit(a, 0, 0)
    return [a2, b]


def bench():
    """A park bench seen from the front and a little above: a slatted seat, a back rail, two dark legs."""
    w, h = 22, 14
    img = Img(w, h)
    # back rail: two lit slats
    for x in range(2, 20):
        img.set(x, 1, "wd4" if x < 8 else "wd3"), img.set(x, 2, "wd2")
        img.set(x, 4, "wd3" if x < 8 else "wd2"), img.set(x, 5, "wd1")
    for y in range(1, 9):
        img.set(3, y, "wd1"), img.set(18, y, "wd1")
    # seat seen from above: three slats, foreshortened
    for x in range(1, 21):
        img.set(x, 7, "wd4" if x < 9 else "wd3")
        img.set(x, 8, "wd3" if x < 9 else "wd2")
        img.set(x, 9, "wd2")
    img.hline(1, 20, 10, "wd1")
    # iron legs
    for x in (3, 18):
        img.vline(x, 11, 13, "k1"), img.set(x + 1, 13, "s0")
    return img.outline({}, "wd0")


def rock():
    """A boulder lit from the top left, with a moss cap and a dark foot."""
    rows = [
        "....aabb....",
        "..aaccbbbd..",
        ".accccbbbdd.",
        "acccbbbbbddd",
        "accbbbbbbddd",
        "abbbbbbbddde",
        ".bbbbbbdddde",
        "..eeeeeeeee.",
    ]
    img = from_rows(rows, {"a": "s3", "b": "s2", "c": "s4", "d": "s1", "e": "s0"})
    for (x, y) in ((4, 0), (5, 0), (6, 1), (3, 1)):
        img.set(x, y, "g3")
    img.set(5, 1, "g2")
    return img.outline({}, "k1")


def gate():
    """A field gate: two posts and a five-bar gate with a diagonal brace, in the fences' wood."""
    img = Img(16, 13)
    for y in range(13):
        img.set(1, y, "wd3"), img.set(2, y, "wd2"), img.set(14, y, "wd2"), img.set(13, y, "wd3")
    img.set(1, 0, "wd4"), img.set(2, 0, "wd4"), img.set(13, 0, "wd4")
    for yy in (3, 6, 9):
        for x in range(3, 13):
            img.set(x, yy, "wd4" if x < 8 else "wd3"), img.set(x, yy + 1, "wd1")
    for i in range(8):
        img.set(4 + i, 10 - i, "wd3")
    return img.outline({}, "wd0")


def flowerbed(petals):
    """A raised flower bed two tiles wide, seen from the front and a little above: a timber edge, dark soil, three
    staggered rows of flowers in two or three colours with leaves, the front row largest."""
    w, h = 30, 16
    img = Img(w, h)
    for y in range(3, 12):
        for x in range(1, 29):
            img.set(x, y, "d1" if (x * 3 + y) % 5 else "d0")
    # the timber edge: lit top plank, shaded face
    for x in range(1, 29):
        img.set(x, 12, "wd4" if x < 10 else "wd3")
        img.set(x, 13, "wd2"), img.set(x, 14, "wd1")
    for x in (1, 14, 28):
        img.vline(x, 12, 14, "wd0")
    rows = ((3, 2, 1), (6, 3, 0), (9, 2, 1))
    for r, (y, x0, stagger) in enumerate(rows):
        for i, x in enumerate(range(x0 + stagger, 28, 4)):
            c = petals[(i + r) % len(petals)]
            img.set(x, y + 2, "g2"), img.set(x - 1, y + 2, "g3"), img.set(x + 1, y + 2, "g1")
            img.set(x, y, c), img.set(x - 1, y + 1, c), img.set(x + 1, y + 1, c), img.set(x, y + 1, "ye2" if c != "ye1" else "o2")
            img.set(x - 1, y, LIGHTER_OF.get(c, c))
            if r == 2:
                img.set(x + 1, y, c)
    return img.outline({}, "k1")


LIGHTER_OF = {"tu1": "tu2", "ye1": "ye2", "pk1": "pk2", "pu1": "pk2", "w": "w", "o2": "o3"}


def quest_bubble(glyph):
    """A small speech bubble over a quest giver's head: light paper, dark outline, shaded on the right and bottom
    (light from the top left). The glyph inside is a shape, so it reads without colour: "!" a quest to offer,
    "..." a quest that waits for the player (parked), a tick for all in order."""
    rows = [
        ".kkkkkkk.",
        "kwwwwwwsk",
        "kwwwwwwsk",
        "kwwwwwwsk",
        "kwwwwwwsk",
        "kwwwwwwsk",
        "kwwwwwwsk",
        "ksssssssk",
        ".kkkskkk.",
        "...ksk...",
        "....k....",
    ]
    img = from_rows(rows, {"k": "k1", "w": "w", "s": "s4"})
    for y, line in enumerate(glyph):
        for x, ch in enumerate(line):
            if ch != ".":
                img.set(1 + x, 1 + y, {"k": "k0", "r": "tu1", "g": "g2"}[ch])
    return img


BANG = ["...r...", "...r...", "...r...", "...r...", ".......", "...r..."]
DOTS = [".......", ".......", ".......", ".k.k.k.", ".......", "......."]
TICK = [".......", ".....g.", "....g..", "g..g...", ".gg....", "......."]


def quest_arrow(direction):
    """The pointer towards the next small step when it is off screen: the door marker's yellow wedge, one per
    compass direction."""
    south = [
        "kkkkkkkkk",
        "kyyyyyyyk",
        ".kyyyyyk.",
        "..kyyyk..",
        "...kyk...",
        "....k....",
    ]
    legend = {"k": "k1", "y": "ye2"}
    if direction == "S":
        return from_rows(south, legend)
    if direction == "N":
        return from_rows(south[::-1], legend)
    east = ["".join(row[i] for row in south) for i in range(9)]   # the wedge turned a quarter: it points east
    return from_rows(east if direction == "E" else [r[::-1] for r in east], legend)


def boss_mark():
    """A site's "Confusion", ready to be faced: a small knot of dark violet cloud with two pale eyes and a question
    curl. Two frames: the eyes look left, then right."""
    rows = [
        "....kkkk.......",
        "...kppppkk.....",
        "..kpqqqqppk.kk.",
        ".kpqqqqqqqpkppk",
        "kpqqqqqqqqqpqpk",
        "kpqwwqqqwwqqqpk",
        "kpqwkqqqwkqqppk",
        "kppqqqqqqqqqpk.",
        ".kppqqkkqqqppk.",
        "..kppppppppkk..",
        "...kkpppkkk....",
        ".....kkk.......",
    ]
    legend = {"k": "k0", "p": "pu0", "q": "pu1", "w": "w"}
    a = from_rows(rows, legend)
    b = from_rows([r.replace("wk", "kw") for r in rows], legend)
    return [a, b]


def icon(rows, legend):
    return from_rows(rows, legend)


ICONS = {
    # a five-point star: experience
    "icon_xp": (["....k....", "...kyk...", "kkkkykkkk", "kyyyyyyyk", ".kyyyyyk.", "..kyyyk..", ".kyykyyk.", "kyyk.kyyk", "kkk...kkk"],
                {"k": "k1", "y": "ye1"}),
    # a round flask with a cork: a potion
    "icon_potion": (["...kkk...", "...kdk...", "...kwk...", "..kwwwk..", ".kwrrrrk.", "krrrrrrrk", "krrtrrrrk", ".krrrrrk.", "..kkkkk.."],
                    {"k": "k1", "d": "wd3", "w": "b5", "r": "tu1", "t": "tu2"}),
    # a paper tag with a hole and two printed lines: the label maker
    "icon_label_maker": (["..kkkkkkk", ".kwwwwwwk", "kwkwwwwwk", "kwwwkkkwk", "kwwwwwwwk", "kwwwkkwwk", ".kwwwwwwk", "..kkkkkkk", "........."],
                         {"k": "k1", "w": "w"}),
    # a probe with a lead running off it: the cable tracer
    "icon_cable_tracer": (["......kk.", ".....kck.", "....kck..", "...kbk...", "..kbk....", ".kbk.....", "kok......", "ko.kkkk..", ".kkk..kk."],
                          {"k": "k1", "c": "c1", "b": "b3", "o": "o2"}),
}


def make_quest_marks(add_sprite):
    add_sprite("quest_mark", quest_bubble(BANG), 4, 10)
    add_sprite("quest_wait", quest_bubble(DOTS), 4, 10)
    add_sprite("quest_clear", quest_bubble(TICK), 4, 10)
    for d in "NESW":
        a = quest_arrow(d)
        add_sprite(f"quest_arrow_{d.lower()}", a, a.w // 2, a.h // 2)
    add_sprite("boss_mark", boss_mark(), 7, 11)
    for name, (rows, legend) in ICONS.items():
        add_sprite(name, icon(rows, legend), 4, 8)


def make_props(add_sprite):
    make_quest_marks(add_sprite)
    add_sprite("phone_box", phone_box())
    add_sprite("pillar_box", pillar_box())
    add_sprite("teapot_stall", teapot_stall())
    add_sprite("rain_cloud", rain_cloud(), 15, 29)
    add_sprite("cheese_cart", cheese_cart())
    add_sprite("well", well())
    add_sprite("haystack", haystack())
    add_sprite("badger", badger(), 7, 15)
    add_sprite("rabbit", rabbit(), 7, 14)
    add_sprite("fox", fox(), 7, 18)
    add_sprite("airfield_pad", airfield_pad(), 22, 29)
    add_sprite("airfield_sign", airfield_sign(), 15, 29)
    add_sprite("marker", marker(), 4, 6)
    add_sprite("bench", bench(), 11, 13)
    add_sprite("rock", rock(), 6, 8)
    add_sprite("gate", gate(), 2, 12)
    add_sprite("flowerbed", flowerbed(["tu1", "ye1", "pk1"]), 15, 15)
    add_sprite("flowerbed_cool", flowerbed(["pu1", "w", "b4"]), 15, 15)


__all__ = ["make_props", "dith"]
