"""Country landmarks, villagers and overworld markers, drawn in the art direction's palette and light.

United Kingdom: a red telephone kiosk, a pillar box, a tea stall, a small rain cloud; villager: a badger in a
bowler hat. Netherlands: a cheese cart (the windmill, cottage, tulips, bicycle and hedgehog come from the art
direction). Anywhere else: a well and haystacks; villager: a rabbit holding a carrot.
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
    """The down-pointing marker over each door (a shape, so it reads without colour), bobbing over two frames."""
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


def make_props(add_sprite):
    add_sprite("phone_box", phone_box())
    add_sprite("pillar_box", pillar_box())
    add_sprite("teapot_stall", teapot_stall())
    add_sprite("rain_cloud", rain_cloud(), 15, 29)
    add_sprite("cheese_cart", cheese_cart())
    add_sprite("well", well())
    add_sprite("haystack", haystack())
    add_sprite("badger", badger(), 7, 15)
    add_sprite("rabbit", rabbit(), 7, 14)
    add_sprite("airfield_pad", airfield_pad(), 22, 29)
    add_sprite("airfield_sign", airfield_sign(), 15, 29)
    add_sprite("marker", marker(), 4, 6)


__all__ = ["make_props", "dith"]
