"""Generate the toolbar icons in FusionGit/resources/icons (pure Python, no dependencies).

Icons follow Fusion's toolbar style: no background tile, light grey shapes with
blue accents, green for "create" and orange for warnings, plus a thin dark outline
so they also read on the light theme. Each icon is a list of layers
(signed distance function, fill colour); layers are composited bottom to top with
4x4 supersampling and written as 16/32 px PNGs (plus @2x).
"""

import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(__file__), "..", "FusionGit", "resources", "icons")

GREY = (214, 219, 226)
GREY_DARK = (150, 158, 170)
BLUE = (47, 145, 230)
GREEN = (76, 175, 80)
ORANGE = (240, 160, 48)
WHITE = (250, 250, 250)
OUTLINE = (40, 46, 56)
OUTLINE_WIDTH = 0.035


# --- signed distance functions (unit square, y down; negative = inside) --------

def circle(cx, cy, r):
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def segment(ax, ay, bx, by, r):
    def d(x, y):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - (ax + t * dx), y - (ay + t * dy)) - r
    return d


def box(x0, y0, x1, y1, radius=0.0):
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius

    def d(x, y):
        qx, qy = abs(x - cx) - hw, abs(y - cy) - hh
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
    return d


def polygon(points):
    def d(x, y):
        dist = min(math.hypot(*_closest(x, y, points[i], points[i - 1])) for i in range(len(points)))
        return -dist if _inside(x, y, points) else dist
    return d


def _closest(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
    return x - (a[0] + t * dx), y - (a[1] + t * dy)


def _inside(x, y, points):
    inside = False
    for i in range(len(points)):
        (x1, y1), (x2, y2) = points[i], points[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def union(*fns):
    return lambda x, y: min(f(x, y) for f in fns)


def subtract(a, b):
    return lambda x, y: max(a(x, y), -b(x, y))


def ring(cx, cy, r, width, gap=None):
    base = circle(cx, cy, r)

    def d(x, y):
        value = abs(base(x, y)) - width
        if gap:
            angle = math.atan2(cy - y, x - cx)
            if gap[0] <= angle <= gap[1]:
                return max(value, 0.02)
        return value
    return d


def gear(cx, cy, r, teeth=8, depth=0.09, width=0.13):
    shapes = [circle(cx, cy, r)]
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        ux, uy, vx, vy = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
        corners = [(r - 0.04, -width / 2), (r + depth, -width / 2), (r + depth, width / 2), (r - 0.04, width / 2)]
        shapes.append(polygon([(cx + ux * t + vx * w, cy + uy * t + vy * w) for t, w in corners]))
    return union(*shapes)


# --- glyph building blocks ----------------------------------------------------

def tray():
    return union(segment(0.18, 0.62, 0.18, 0.84, 0.06), segment(0.18, 0.84, 0.82, 0.84, 0.06),
                 segment(0.82, 0.84, 0.82, 0.62, 0.06))


def arrow(up):
    head_y, tail_y, base_y = (0.1, 0.62, 0.4) if up else (0.66, 0.1, 0.38)
    shaft = box(0.42, min(head_y, tail_y) + (0.15 if up else 0), 0.58, max(tail_y, base_y) - (0 if up else 0.02))
    head = polygon([(0.22, base_y), (0.78, base_y), (0.5, head_y)])
    return union(shaft, head)


def folder():
    return union(box(0.1, 0.28, 0.46, 0.4, 0.04), box(0.1, 0.34, 0.9, 0.82, 0.05))


def badge_plus():
    return [(circle(0.74, 0.74, 0.22), GREEN),
            (union(box(0.65, 0.715, 0.83, 0.765), box(0.715, 0.65, 0.765, 0.83)), WHITE)]


def badge_warning():
    return [(polygon([(0.74, 0.5), (0.97, 0.94), (0.51, 0.94)]), ORANGE),
            (union(box(0.725, 0.63, 0.755, 0.82), box(0.725, 0.85, 0.755, 0.89)), OUTLINE)]


def branch_graph(accent):
    return [(union(segment(0.32, 0.18, 0.32, 0.82, 0.05), segment(0.32, 0.6, 0.68, 0.36, 0.05)), GREY),
            (circle(0.32, 0.2, 0.11), GREY), (circle(0.32, 0.8, 0.11), GREY),
            (circle(0.68, 0.34, 0.12), accent)]


GLYPHS = {
    "init": [(union(segment(0.3, 0.14, 0.3, 0.66, 0.05), segment(0.3, 0.5, 0.62, 0.3, 0.05)), GREY),
             (circle(0.3, 0.16, 0.1), GREY), (circle(0.62, 0.28, 0.1), BLUE)] + badge_plus(),
    "commit": [(segment(0.08, 0.5, 0.92, 0.5, 0.06), GREY), (circle(0.5, 0.5, 0.22), GREY),
               (circle(0.5, 0.5, 0.13), BLUE)],
    "pull": [(tray(), GREY), (arrow(up=False), BLUE)],
    "push": [(tray(), GREY), (arrow(up=True), BLUE)],
    "reimport": [(subtract(box(0.14, 0.08, 0.66, 0.84, 0.04), polygon([(0.5, 0.0), (0.7, 0.0), (0.7, 0.22)])), GREY),
                 (box(0.24, 0.3, 0.54, 0.35), GREY_DARK), (box(0.24, 0.44, 0.5, 0.49), GREY_DARK),
                 (ring(0.68, 0.7, 0.18, 0.055, gap=(0.3, 1.3)), BLUE),
                 (polygon([(0.76, 0.6), (0.95, 0.6), (0.855, 0.76)]), BLUE)],
    "locate": [(folder(), GREY)] + badge_warning(),
    "status": branch_graph(BLUE),
    "settings": [(subtract(gear(0.5, 0.5, 0.27), circle(0.5, 0.5, 0.11)), GREY), (circle(0.5, 0.5, 0.07), BLUE)],
    "open": [(folder(), GREY),
             (union(segment(0.42, 0.66, 0.74, 0.34, 0.055),
                    polygon([(0.56, 0.24), (0.86, 0.22), (0.84, 0.52)])), BLUE)],
}


# --- rendering ------------------------------------------------------------------

def _over(dst, color, alpha):
    r, g, b, a = dst
    out_a = alpha + a * (1 - alpha)
    if out_a == 0:
        return (0.0, 0.0, 0.0, 0.0)
    mix = [(c * alpha + d * a * (1 - alpha)) / out_a for c, d in zip(color, (r, g, b))]
    return (*mix, out_a)


def render(layers, size, samples=4):
    outline_width = OUTLINE_WIDTH * max(1.0, 24 / size)
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(samples):
                for sx in range(samples):
                    x, y = (px + (sx + 0.5) / samples) / size, (py + (sy + 0.5) / samples) / size
                    pixel = (0.0, 0.0, 0.0, 0.0)
                    for sdf, color in layers:
                        d = sdf(x, y)
                        if d <= 0:
                            pixel = _over(pixel, color, 1.0)
                        elif d <= outline_width:
                            pixel = _over(pixel, OUTLINE, 0.85)
                    acc = [s + p * (pixel[3] if i < 3 else 1) for i, (s, p) in enumerate(zip(acc, pixel))]
            n = samples * samples
            alpha = acc[3] / n
            rgb = [round(c / acc[3]) if acc[3] else 0 for c in acc[:3]]
            row += bytes(rgb + [round(alpha * 255)])
        rows.append(bytes(row))
    return b"".join(rows)


def png(size, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main():
    for name, layers in GLYPHS.items():
        folder_path = os.path.join(OUT, name)
        os.makedirs(folder_path, exist_ok=True)
        for file_name, size in (("16x16.png", 16), ("16x16@2x.png", 32), ("32x32.png", 32), ("32x32@2x.png", 64)):
            with open(os.path.join(folder_path, file_name), "wb") as f:
                f.write(png(size, render(layers, size)))
    print(f"wrote {len(GLYPHS)} icons to {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
