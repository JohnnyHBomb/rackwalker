"""A minimal PNG writer (stdlib only) for previews of indexed images."""

import struct
import zlib


def write_png(path, w, h, rgb_rows, scale=1):
    """rgb_rows: h lists of w (r, g, b) tuples."""
    raw = bytearray()
    for row in rgb_rows:
        line = bytearray()
        for (r, g, b) in row:
            line += bytes((r, g, b)) * scale
        for _ in range(scale):
            raw += b"\x00" + line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def write_indexed(path, img, palette_hex, scale=1, bg=(40, 44, 52)):
    rgb = [tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) for h in palette_hex]
    rows = []
    for y in range(img.h):
        rows.append([rgb[v] if v else bg for v in img.px[y * img.w:(y + 1) * img.w]])
    write_png(path, img.w, img.h, rows, scale)
