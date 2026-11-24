"""Derive mascot/assets/sprites/idle.png from the flat-background source art.

The source PNG is RGB (no alpha) on a flat light-grey background. Method:
flood-fill from the border over pixels close to the background colour, then
de-matte a 2px edge band so light-grey halo pixels become semi-transparent.
Original file is only read. Usage: python3 -m mascot.tools.cutout SRC OUTDIR
"""
import sys
from .pngio import read_png, write_png_rgba

BG = (225, 225, 232)
TOL = 14
PAD = 12


def cutout(src):
    w, h, bpp, rows = read_png(src)
    def px(x, y):
        i = x * bpp
        r = rows[y]
        return r[i], r[i + 1], r[i + 2]
    def isbg(x, y):
        p = px(x, y)
        return abs(p[0] - BG[0]) <= TOL and abs(p[1] - BG[1]) <= TOL and abs(p[2] - BG[2]) <= TOL
    bg = bytearray(w * h)
    stack = [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h or bg[y * w + x] or not isbg(x, y):
            continue
        bg[y * w + x] = 1
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    # distance (chebyshev, up to 2) from background region
    band = {}
    for y in range(h):
        for x in range(w):
            if bg[y * w + x]:
                continue
            for d in (1, 2):
                if any(0 <= x + dx < w and 0 <= y + dy < h and bg[(y + dy) * w + x + dx]
                       for dx in range(-d, d + 1) for dy in range(-d, d + 1)):
                    band[(x, y)] = d
                    break
    out = [bytearray(w * 4) for _ in range(h)]
    minx, miny, maxx, maxy = w, h, 0, 0
    for y in range(h):
        o = out[y]
        for x in range(w):
            if bg[y * w + x]:
                continue
            r, g, b = px(x, y)
            if (x, y) in band:
                dist = max(abs(r - BG[0]), abs(g - BG[1]), abs(b - BG[2]))
                a = min(1.0, dist / 70.0) if band[(x, y)] == 1 else min(1.0, dist / 40.0 + 0.4)
                a = max(a, 0.15)
                r, g, b = (max(0, min(255, round((c - (1 - a) * k) / a))) for c, k in zip((r, g, b), BG))
                A = round(a * 255)
            else:
                A = 255
            i = x * 4
            o[i:i + 4] = bytes((r, g, b, A))
            minx, maxx, miny, maxy = min(minx, x), max(maxx, x), min(miny, y), max(maxy, y)
    x0, y0 = max(0, minx - PAD), max(0, miny - PAD)
    x1, y1 = min(w, maxx + PAD + 1), min(h, maxy + PAD + 1)
    crop = [bytearray(r[x0 * 4:x1 * 4]) for r in out[y0:y1]]
    return (x1 - x0, y1 - y0), crop, (x0, y0)


def halve(w, h, rows):
    w2, h2 = w // 2, h // 2
    res = []
    for y in range(h2):
        a, b = rows[2 * y], rows[2 * y + 1]
        o = bytearray(w2 * 4)
        for x in range(w2):
            i, j = 8 * x, 8 * x + 4
            al = a[i + 3] + a[j + 3] + b[i + 3] + b[j + 3]
            if al:
                for c in range(3):
                    s = (a[i + c] * a[i + 3] + a[j + c] * a[j + 3] + b[i + c] * b[i + 3] + b[j + c] * b[j + 3])
                    o[4 * x + c] = s // al
            o[4 * x + 3] = al // 4
        res.append(o)
    return w2, h2, res


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    (w, h), rows, off = cutout(src)
    write_png_rgba(f"{outdir}/idle.png", w, h, rows)
    w2, h2, r2 = halve(w, h, rows)
    write_png_rgba(f"{outdir}/idle_web.png", w2, h2, r2)
    print("size", w, h, "crop offset in source", off, "web", w2, h2)


if __name__ == "__main__":
    main()
