"""
Draw the app icon: assets/icon.png (the window's, 256 px) and assets/icon.ico
(the .exe's: 16-256 px, PNG-compressed entries). Run from the repository root:
    python tools/make_icon.py
"""
import io
import os
import struct
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(n=1024):
    """The icon at n x n: piano keys under a falling note, on a rounded dark tile."""
    s = pygame.Surface((n, n), pygame.SRCALPHA)
    u = n / 64.0
    pygame.draw.rect(s, (30, 31, 40), (0, 0, n, n), border_radius=int(12 * u))
    # the falling note (green, as the right hand's) and its glow
    pygame.draw.rect(s, (60, 110, 40), (int(25 * u), int(6 * u), int(14 * u), int(26 * u)), border_radius=int(4 * u))
    pygame.draw.rect(s, (130, 205, 75), (int(26.5 * u), int(7.5 * u), int(11 * u), int(23 * u)), border_radius=int(3 * u))
    # four white keys, the second lit where the note lands, and three black keys
    top, bottom = int(33 * u), int(58 * u)
    x0, w = 6 * u, 13 * u
    for i in range(4):
        r = pygame.Rect(int(x0 + i * w), top, int(w - 1.2 * u), bottom - top)
        pygame.draw.rect(s, (130, 205, 75) if i == 1 else (242, 242, 236), r,
                         border_bottom_left_radius=int(2.5 * u), border_bottom_right_radius=int(2.5 * u))
    for i in (1, 2, 3):
        cx = x0 + i * w - 0.6 * u
        pygame.draw.rect(s, (18, 18, 22), (int(cx - 4 * u), top, int(8 * u), int(14 * u)),
                         border_bottom_left_radius=int(1.5 * u), border_bottom_right_radius=int(1.5 * u))
    return s


def png_bytes(surf):
    buf = io.BytesIO()
    pygame.image.save(surf, buf, "icon.png")
    return buf.getvalue()


def write_ico(path, big):
    images = [png_bytes(pygame.transform.smoothscale(big, (n, n))) for n in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for n, data in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as fh:
        fh.write(out + b"".join(images))


def main():
    pygame.init()
    big = draw()
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    pygame.image.save(pygame.transform.smoothscale(big, (256, 256)), os.path.join(ROOT, "assets", "icon.png"))
    write_ico(os.path.join(ROOT, "assets", "icon.ico"), big)
    print("wrote assets/icon.png and assets/icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
