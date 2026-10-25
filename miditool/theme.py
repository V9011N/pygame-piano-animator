"""
theme.py - Colours, fonts and the UI scale (Hand-thesia's look).

Sizes are written for a 96-dpi screen and multiplied by `S` (1 in
Hand-thesia, which draws at the screen's own pixels).
"""
from __future__ import annotations

S = 1.0


def set_scale(s):
    global S
    S = max(0.75, min(3.0, float(s)))


def px(v):
    """A size in pixels at the current UI scale."""
    return int(round(v * S))


def mix(c1, c2, amount):
    """Blend colour c1 toward c2 by amount (0..1)."""
    return tuple(int(a + (b - a) * amount) for a, b in zip(c1, c2))


# --- Hand-thesia's palette ----------------------------------------------------
BG = (24, 24, 30)
BAR_BG = (44, 44, 54)
TEXT = (220, 220, 228)
TEXT_DIM = (120, 120, 136)
ACCENT = (255, 206, 84)
PANEL = (40, 40, 50)
PANEL_EDGE = (78, 78, 96)
BUTTON = (54, 54, 68)
BUTTON_HOVER = (74, 74, 94)
INFO_BG = (30, 30, 38)
INFO_LINE = (50, 50, 62)

ROW_WHITE = (34, 34, 43)
ROW_BLACK = (27, 27, 34)
ROW_LINE = (40, 40, 50)
ROW_C_LINE = (62, 62, 78)
BEAT_LINE = (40, 40, 52)
SUB_LINE = (33, 33, 42)
BAR_LINE_ROLL = (74, 74, 92)
PLAYHEAD = (240, 240, 245)
RULER_BG = (36, 36, 46)
GUTTER_BG = (18, 18, 22)
LANE_BG = (28, 28, 35)
LANE_HEAD_BG = (22, 22, 28)
SCROLL_TRACK = (30, 30, 38)
SCROLL_THUMB = (78, 78, 96)
SCROLL_THUMB_HOT = (104, 104, 126)

GOOD = (110, 200, 120)
WARN = (235, 180, 70)
BAD = (230, 90, 80)
ADD_COLOR = (90, 215, 120)
REMOVE_COLOR = (235, 80, 75)
DISPLACED_COLOR = (230, 190, 70)

# fingering: the hands' colours (Hand-thesia's HAND_COLORS: white-key, black-key notes)
HAND_COLORS = {"R": ((116, 196, 64), (74, 146, 38)), "L": ((84, 152, 232), (48, 104, 190))}
HAND_NAMES = {"R": "Right hand", "L": "Left hand"}
FINGER_NAMES = {1: "Thumb", 2: "Index", 3: "Middle", 4: "Ring", 5: "Little"}

# one colour per MIDI channel; 1 and 2 are Hand-thesia's hands (its exports put the right hand on the piano's
# first channel and the left hand on the next)
CHANNEL_COLORS = [
    (116, 196, 64),    # 1  green
    (84, 152, 232),    # 2  blue
    (240, 150, 60),    # 3  orange
    (176, 122, 232),   # 4  violet
    (232, 92, 96),     # 5  red
    (64, 200, 184),    # 6  teal
    (228, 204, 76),    # 7  yellow
    (236, 120, 188),   # 8  pink
    (98, 212, 236),    # 9  cyan
    (168, 168, 178),   # 10 drums: grey
    (186, 222, 84),    # 11 lime
    (196, 146, 98),    # 12 tan
    (146, 150, 244),   # 13 periwinkle
    (244, 146, 124),   # 14 salmon
    (124, 222, 160),   # 15 mint
    (212, 172, 236),   # 16 lavender
]
SOURCE_NOTE = (150, 154, 170)       # the imposed perfect source's notes


def channel_color(ch, black=False):
    c = CHANNEL_COLORS[ch & 15]
    return mix(c, (0, 0, 0), 0.22) if black else c


# --- fonts ---------------------------------------------------------------------
def ui_font(size, bold=False):
    """Hand-thesia's typeface (common.ui_font: the bundled one, or the one chosen in the Settings)."""
    import common
    return common.ui_font(max(6, round(size * S)), bold)


def load_fonts():
    return {
        "small": ui_font(14),
        "normal": ui_font(16),
        "bold": ui_font(16, True),
        "big": ui_font(28, True),
        "button": ui_font(20, True),
        "finger": ui_font(12, True),
        "label": ui_font(11, True),
        "tiny": ui_font(10, True),
    }


def fit_text(font, text, width):
    """`text`, shortened with an ellipsis if it's wider than `width`."""
    if width <= 0:
        return ""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "…")[0] > width:
        text = text[:-1]
    return text.rstrip() + "…"
