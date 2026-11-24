"""Derive assets/sprites/eyes_patch.png (alpha mask of both eye patches) from idle.png.

Used for eye recolouring and eyelid blinks so overlay shapes match the art exactly.
Pixels in each eye box that are dark or cyan-tinted, plus any holes they enclose.
Usage: python3 -m mascot.tools.eyemask assets/sprites
"""
import sys
from .pngio import read_png, write_png_rgba

BOXES = ((304, 318, 452, 452), (496, 318, 644, 452))  # x0,y0,x1,y1 in idle.png coordinates


def build(path):
    w, h, _, rows = read_png(path)
    out = [bytearray(w * 4) for _ in range(h)]
    for x0, y0, x1, y1 in BOXES:
        bw, bh = x1 - x0, y1 - y0
        cand = bytearray(bw * bh)
        for y in range(bh):
            r = rows[y0 + y]
            for x in range(bw):
                i = (x0 + x) * 4
                R, G, B = r[i], r[i + 1], r[i + 2]
                if (R + G + B) / 3 < 120 or B - R > 15:
                    cand[y * bw + x] = 1
        outside = bytearray(bw * bh)
        stack = [(x, y) for x in range(bw) for y in (0, bh - 1)] + [(x, y) for y in range(bh) for x in (0, bw - 1)]
        while stack:
            x, y = stack.pop()
            if x < 0 or y < 0 or x >= bw or y >= bh or outside[y * bw + x] or cand[y * bw + x]:
                continue
            outside[y * bw + x] = 1
            stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        inside = [(x, y) for y in range(bh) for x in range(bw) if not outside[y * bw + x]]
        for x, y in inside:  # 2px dilation to cover the anti-aliased rim
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    if 0 <= x + dx < bw and 0 <= y + dy < bh:
                        i = (x0 + x + dx) * 4
                        out[y0 + y + dy][i:i + 4] = b"\xff\xff\xff\xff"
    return w, h, out


if __name__ == "__main__":
    d = sys.argv[1]
    w, h, rows = build(f"{d}/idle.png")
    write_png_rgba(f"{d}/eyes_patch.png", w, h, rows)
