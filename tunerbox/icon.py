"""Draws the TunerBox app icon (a little retro TV) and writes it as .ico/.png, no image libraries."""
import struct
import zlib
from pathlib import Path


def _rgba(size: int, bg=None) -> bytes:
    ss = 4                                     # supersampling for smooth edges
    n = size * ss
    px = bytearray(size * size * 4)

    def rrect(x, y, x0, y0, x1, y1, r):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def shade(u, v):
        # u, v in 0..1
        if (abs(u - 0.5) < 0.035 and 0.80 < v < 0.93) or (0.30 < u < 0.70 and 0.90 < v < 0.95):
            return (90, 96, 110, 255)                        # stand
        for ax, bx in ((0.30, 0.42), (0.70, 0.58)):          # rabbit ears
            t = (v - 0.04) / (0.20 - 0.04)
            if 0 <= t <= 1 and abs(u - (ax + (bx - ax) * t)) < 0.022:
                return (150, 156, 170, 255)
        if rrect(u, v, 0.06, 0.18, 0.94, 0.84, 0.10):
            if rrect(u, v, 0.13, 0.26, 0.74, 0.76, 0.06):    # screen
                g = int(30 + 60 * (1 - v))
                if 0.33 < v < 0.40 and 0.20 < u < 0.67:
                    return (255, 214, 64, 255)               # guide bars
                if 0.46 < v < 0.53 and 0.20 < u < 0.52:
                    return (120, 200, 255, 255)
                if 0.59 < v < 0.66 and 0.20 < u < 0.60:
                    return (120, 200, 255, 255)
                return (20, 40 + g, 110 + g, 255)
            if (u - 0.84) ** 2 + (v - 0.38) ** 2 < 0.045 ** 2:
                return (255, 214, 64, 255)                   # knob
            if (u - 0.84) ** 2 + (v - 0.56) ** 2 < 0.035 ** 2:
                return (200, 60, 60, 255)
            return (52, 56, 68, 255)                         # cabinet
        return None

    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((x * ss + sx + 0.5) / n, (y * ss + sy + 0.5) / n)
                    if c:
                        for k in range(4):
                            acc[k] += c[k]
            cnt = ss * ss
            if bg:      # opaque tile (phone/tablet home screens): blend onto bg
                a = acc[3] / cnt / 255
                avg = [acc[k] / acc[3] * 255 if acc[3] else 0 for k in range(3)]
                i = (y * size + x) * 4
                px[i:i + 4] = bytes([int(avg[k] * a + bg[k] * (1 - a)) for k in range(3)] + [255])
                continue
            a = acc[3] / cnt
            i = (y * size + x) * 4
            if a:
                px[i:i + 4] = bytes([int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)),
                                     int(acc[2] / (acc[3] / 255)), int(a)])
    return bytes(px)


def _png(size: int, bg=None, raw=None, height=None) -> bytes:
    raw = raw if raw is not None else _rgba(size, bg)
    w, h = size, height or size
    rows = b"".join(b"\x00" + raw[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def write_ico(path: Path, sizes=(256, 48, 32, 16)):
    if path.exists():
        return path
    imgs = [_png(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    for s, d in zip(sizes, imgs):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off)
        off += len(d)
    path.write_bytes(out + b"".join(imgs))
    return path


TILE_BG = (16, 40, 92)


def write_png(path: Path, size=192, bg=None, overwrite=False):
    if overwrite or not path.exists():
        path.write_bytes(_png(size, bg))
    return path


def write_banner(path: Path, w=320, h=180, bg=TILE_BG):
    """Android TV launcher banner: the icon centred on a wide tile."""
    ico = int(h * 0.8)
    tile = _rgba(ico, bg)
    px = bytearray(bytes(list(bg) + [255]) * (w * h))
    ox, oy = (w - ico) // 2, (h - ico) // 2
    for y in range(ico):
        i = ((oy + y) * w + ox) * 4
        px[i:i + ico * 4] = tile[y * ico * 4:(y + 1) * ico * 4]
    path.write_bytes(_png(w, raw=bytes(px), height=h))
    return path
