# -*- coding: utf-8 -*-
"""Build a maximally compatible multi-size .ico using classic BMP (DIB) entries.

Pillow writes RGBA icons as PNG-compressed ICO entries, which some shell icon
resolution paths (shortcut icon loading) fail to read.  Here we emit the
traditional 32bpp BGRA BMP entries with AND masks, which every Windows icon
consumer supports, while still keeping full alpha (rounded corners / shadow).
"""
import os
import struct

from PIL import Image

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
SRC = os.path.join(BASE, "icon.png")
DST = os.path.join(BASE, "icon.ico")

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def bmp_entry(img, s):
    im = img.convert("RGBA").resize((s, s), Image.LANCZOS)
    px = im.load()

    xor = bytearray()
    for y in range(s - 1, -1, -1):  # bottom-up
        for x in range(s):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    # AND mask: all-zero (opaque) so the alpha channel governs transparency
    and_row = ((s + 31) // 32) * 4
    and_mask = b"\x00" * (and_row * s)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        s,                  # biWidth
        s * 2,              # biHeight (XOR + AND)
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression (BI_RGB)
        len(xor) + len(and_mask),
        0, 0, 0, 0,         # resolution / colors
    )
    return header + bytes(xor) + and_mask


def main():
    img = Image.open(SRC)
    entries = [bmp_entry(img, s) for s in SIZES]

    # ICONDIR
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for s, data in zip(SIZES, entries):
        w = 0 if s == 256 else s
        h = 0 if s == 256 else s
        out += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in entries:
        out += data

    with open(DST, "wb") as f:
        f.write(out)
    print("saved", DST, "entries:", len(entries), "bytes:", len(out))


if __name__ == "__main__":
    main()
