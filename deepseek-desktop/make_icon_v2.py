# -*- coding: utf-8 -*-
"""Generate a polished DeepSeek Assistant app icon (png + multi-size ico).

Design: DeepSeek-blue rounded-square badge with a clean white whale mark.
The whale outline is a closed Catmull-Rom spline so the silhouette stays smooth
at every size; the result is downsampled with LANCZOS.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(BASE, exist_ok=True)

S = 1024
BLUE_TOP = (0x5B, 0x7B, 0xFF)
BLUE_BOT = (0x22, 0x33, 0xCC)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def catmull_closed(pts, per_seg=40):
    """Smooth closed loop through pts (normalised coords) -> pixel points."""
    n = len(pts)
    out = []
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        for j in range(per_seg):
            t = j / per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x * S, y * S))
    return out


def background():
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), lerp(BLUE_TOP, BLUE_BOT, (y / (S - 1)) ** 0.9))
    grad = grad.resize((S, S), Image.BILINEAR).convert("RGBA")

    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([-S * 0.5, -S * 0.6, S * 0.8, S * 0.55], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.14))
    grad = Image.composite(Image.new("RGBA", (S, S), (255, 255, 255, 255)), grad, glow)

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=int(S * 0.225), fill=255
    )
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


# Whale facing left: nose, back, peduncle, upper fluke, notch, lower fluke, belly.
WHALE = [
    (0.148, 0.548),   # nose
    (0.168, 0.452),
    (0.258, 0.398),   # top of head
    (0.430, 0.404),
    (0.600, 0.438),
    (0.706, 0.498),   # peduncle top
    (0.806, 0.432),   # upper fluke inner edge
    (0.906, 0.330),   # upper fluke tip
    (0.806, 0.512),   # notch between flukes
    (0.906, 0.792),   # lower fluke tip
    (0.706, 0.604),   # peduncle bottom
    (0.560, 0.656),
    (0.400, 0.688),
    (0.250, 0.652),
    (0.176, 0.606),
]


def whale_mask():
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    d.polygon(catmull_closed(WHALE), fill=255)

    # eye hole
    d.ellipse(
        [0.235 * S, 0.470 * S, 0.305 * S, 0.540 * S], fill=0
    )

    return m.rotate(4, resample=Image.BICUBIC, center=(0.46 * S, 0.55 * S))


def main():
    canvas = background()
    mask = whale_mask()

    shadow = mask.filter(ImageFilter.GaussianBlur(S * 0.02))
    shadow = shadow.point(lambda v: int(v * 0.38))
    canvas.alpha_composite(
        Image.composite(
            Image.new("RGBA", (S, S), (12, 20, 92, 255)),
            Image.new("RGBA", (S, S), (0, 0, 0, 0)),
            shadow,
        ),
        dest=(int(S * 0.008), int(S * 0.012)),
    )

    white = Image.new("RGBA", (S, S), (255, 255, 255, 255))
    empty = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.alpha_composite(Image.composite(white, empty, mask))

    icon = canvas.resize((S, S), Image.LANCZOS)
    png = os.path.join(BASE, "icon.png")
    icon.save(png)
    print("saved", png)

    ico = os.path.join(BASE, "icon.ico")
    icon.save(
        ico,
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("saved", ico)


if __name__ == "__main__":
    main()
