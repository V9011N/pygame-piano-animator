"""
common.py - Pieces shared by the falling-notes player, the fingering editor
and the main menu: colours, the on-screen keyboard, sound output, playback
transport, file dialogs and a few small widgets.
"""
from __future__ import annotations

import bisect
import os

import pygame

from midi_loader import HIGHEST_PIANO_KEY, LEFT, LOWEST_PIANO_KEY, RIGHT, is_black_key

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
WINDOW_SIZE = (1280, 800)
FPS = 60
MAX_FRAME_DT = 1 / 15        # a slow frame (loading, a big redraw) never skips the song ahead
LEAD_IN = 2.0                # seconds of empty time before the first note
TOP_BAR_H = 34
FELT_H = 6
HAND_AREA_RATIO = 0.24       # share of the window height kept free below the keys
HAND_AREA_MIN_H = 150
SPEED_MIN, SPEED_MAX = 0.1, 2.0

# Colours
BG = (24, 24, 30)
LANE_LINE = (36, 36, 45)
BAR_LINE = (58, 58, 72)
FELT = (150, 22, 34)
WHITE_KEY = (242, 242, 238)
WHITE_KEY_EDGE = (150, 150, 150)
BLACK_KEY = (22, 22, 26)
BLACK_KEY_BEVEL = (58, 58, 64)
HAND_AREA_BG = (30, 30, 38)
TEXT = (220, 220, 228)
TEXT_DIM = (120, 120, 136)
BAR_BG = (44, 44, 54)
BAR_FILL = (116, 196, 64)
ACCENT = (255, 206, 84)      # selection / highlights
PANEL = (40, 40, 50)
PANEL_EDGE = (78, 78, 96)
BUTTON = (54, 54, 68)
BUTTON_HOVER = (74, 74, 94)

# Note colours per hand: (white-key note, black-key note)
HAND_COLORS = {
    RIGHT: ((116, 196, 64), (74, 146, 38)),
    LEFT: ((84, 152, 232), (48, 104, 190)),
}
HAND_NAMES = {RIGHT: "Right hand", LEFT: "Left hand"}
FINGER_NAMES = {1: "Thumb", 2: "Index", 3: "Middle", 4: "Ring", 5: "Little"}


def mix(c1, c2, amount):
    """Blend colour c1 toward c2 by amount (0..1)."""
    return tuple(int(a + (b - a) * amount) for a, b in zip(c1, c2))


def fmt_time(seconds, frac=False):
    neg = seconds < 0
    seconds = abs(seconds)
    m, s = divmod(seconds, 60)
    text = f"{int(m)}:{s:05.2f}" if frac else f"{int(m)}:{int(s):02d}"
    return ("-" if neg else "") + text


def load_fonts():
    face = "segoeui,arial,helvetica"
    return {
        "small": pygame.font.SysFont(face, 15),
        "normal": pygame.font.SysFont(face, 17),
        "big": pygame.font.SysFont(face, 30, bold=True),
        "title": pygame.font.SysFont(face, 54, bold=True),
        "finger": pygame.font.SysFont(face, 13, bold=True),
        "label": pygame.font.SysFont(face, 11, bold=True),
        "button": pygame.font.SysFont(face, 22, bold=True),
    }


KEY_LEN_WW = 5.6            # white key length, in white-key widths (the keys' true proportions)
HAND_LEN_WW = 7.0           # room below the keys for the hands and forearms, in white-key widths
BOTTOM_MAX_SHARE = 0.5      # the keys + hand area never take more than this share of the height


def bottom_layout(size):
    """
    (keyboard rect, hand-area rect) for a window size: the keys and the hand
    area always sit at the bottom, in every mode.

    Everything down here is drawn to one scale (pixels per white key), so it
    keeps its proportions at any window shape: the key length and the hand
    area follow the key width, never the window height. A window too wide for
    its height (wider than about 16:9) gets a keyboard that no longer spans
    the full width, centred, rather than squashed keys and cramped hands; a
    tall window just leaves more room for the notes above.
    """
    w, h = size
    ww = w / 52.0
    budget = BOTTOM_MAX_SHARE * h
    if ww * (KEY_LEN_WW + HAND_LEN_WW) > budget:
        ww = budget / (KEY_LEN_WW + HAND_LEN_WW)
    kb_w = int(round(ww * 52))
    kb_h = int(round(ww * KEY_LEN_WW))
    hand_h = max(int(round(ww * HAND_LEN_WW)), min(HAND_AREA_MIN_H, int(h * 0.2)))
    kb_x = (w - kb_w) // 2
    kb_y = h - hand_h - kb_h
    return pygame.Rect(kb_x, kb_y, kb_w, kb_h), pygame.Rect(0, kb_y + kb_h, w, hand_h)


# --------------------------------------------------------------------------- #
# Keyboard geometry and drawing
# --------------------------------------------------------------------------- #
# Small sideways nudges that make black keys sit like they do on a real piano.
_BLACK_OFFSETS = {1: -0.12, 3: 0.12, 6: -0.14, 8: 0.0, 10: 0.14}

# Key styles: "realistic" (a real piano's proportions) or "equal" (PASHKULI's
# equal lanes: every key's back, black or white, is the same width, so every
# falling note is too; white key fronts are C-E = 5 lanes / 3, F-B = 7 / 4).
KEY_STYLES = ("realistic", "equal")
_key_style = "realistic"
LANE_WHITE = (33, 33, 42)        # equal keys: the lanes above white keys, a shade lighter
KEY_GAP = (44, 44, 50)           # equal keys: the gaps between keys
_EQUAL_SPAN = 87 + 0.5 + 5 / 3   # the keyboard's width in lanes (A0's front half a lane left of its lane .. C8's front)


def key_style():
    return _key_style


def set_key_style(style):
    global _key_style
    _key_style = style if style in KEY_STYLES else "realistic"


def _equal_head(p):
    """(left, right) of white key p's front, in lanes from its octave's C."""
    i = p % 12
    if i <= 4:
        k = i // 2
        return k * 5 / 3, (k + 1) * 5 / 3
    k = (i - 5) // 2
    return 5 + k * 7 / 4, 5 + (k + 1) * 7 / 4


class Keyboard:
    def __init__(self, rect: pygame.Rect, low=LOWEST_PIANO_KEY, high=HIGHEST_PIANO_KEY, style=None):
        self.low, self.high = low, high
        self.style = style or key_style()
        self.layout(rect)

    def layout(self, rect: pygame.Rect):
        self.rect = pygame.Rect(rect)
        self._base = None
        self.tails = {}              # equal keys: pitch -> Rect of a white key's back (between the blacks)
        self.lane_shade = []         # equal keys: (x, width) of the lanes above white keys
        if self.style == "equal" and self.low == LOWEST_PIANO_KEY and self.high == HIGHEST_PIANO_KEY:
            return self._layout_equal(rect)
        self.style = "realistic"
        whites = [p for p in range(self.low, self.high + 1) if not is_black_key(p)]
        self.white_w = rect.w / len(whites)
        self.black_w = self.white_w * 0.6
        self.black_h = int(rect.h * 0.63)

        self.key_rects = {}          # pitch -> pygame.Rect of the key itself
        self.lanes = {}              # pitch -> (x, width) for falling notes
        self.lane_lines = []         # x positions of faint guides at each C and F
        white_index = 0
        for p in range(self.low, self.high + 1):
            if is_black_key(p):
                boundary = rect.x + white_index * self.white_w
                x = boundary - self.black_w / 2 + _BLACK_OFFSETS[p % 12] * self.black_w
                r = pygame.Rect(round(x), rect.y, round(self.black_w), self.black_h)
                self.key_rects[p] = r
                self.lanes[p] = (r.x, r.w)
            else:
                x0 = round(rect.x + white_index * self.white_w)
                x1 = round(rect.x + (white_index + 1) * self.white_w)
                self.key_rects[p] = pygame.Rect(x0, rect.y, x1 - x0, rect.h)
                pad = max(1, round(self.white_w * 0.08))
                self.lanes[p] = (x0 + pad, x1 - x0 - 2 * pad)
                if p % 12 in (0, 5):
                    self.lane_lines.append(x0)
                white_index += 1

    def _layout_equal(self, rect):
        """
        PASHKULI's equal keys: 88 lanes of one width L, separated by a gap
        (1 px, or more on big windows); every black key and every white
        key's back fills one lane, and the white fronts share their group's
        lanes evenly. The average white key keeps the realistic width, so
        the hands keep their scale.
        """
        whites = [p for p in range(self.low, self.high + 1) if not is_black_key(p)]
        self.white_w = rect.w / len(whites)
        L = rect.w / _EQUAL_SPAN
        self.lane_w = L
        g = self.gap = max(1, round(L / 15))
        self.black_w = L - g
        self.black_h = int(rect.h * 0.63)
        self.key_rects, self.lanes, self.lane_lines = {}, {}, []
        lane_x = lambda p: rect.x + (p - self.low + 0.5) * L      # left edge of p's lane

        def span(a, b, top, h):
            x0, x1 = round(a) + g // 2, round(b) - (g - g // 2)
            return pygame.Rect(x0, top, max(1, x1 - x0), h)

        for p in range(self.low, self.high + 1):
            a, b = lane_x(p), lane_x(p) + L
            if is_black_key(p):
                self.key_rects[p] = span(a, b, rect.y, self.black_h)
                self.lanes[p] = (self.key_rects[p].x, self.key_rects[p].w)
                continue
            c = lane_x(p - p % 12)
            h0, h1 = c + _equal_head(p)[0] * L, c + _equal_head(p)[1] * L
            if p == self.low:                     # the ends: the back reaches the keyboard's edge
                a = h0
            if p == self.high:
                b = h1
            self.key_rects[p] = span(h0, h1, rect.y, rect.h)          # the front (where it is played)
            self.tails[p] = span(a, b, rect.y, self.black_h + g)
            lane = span(lane_x(p), lane_x(p) + L, rect.y, 1)
            self.lanes[p] = (lane.x, lane.w)
            self.lane_shade.append((lane.x, lane.w))

    # ----- drawing -------------------------------------------------------------
    # The keyboard at rest is drawn once and cached (_base); each frame it is
    # blitted and only the pressed keys (and the keys they touch) are drawn
    # again over it, so a frame costs a blit instead of 88 keys.

    def _white(self, surf, p, pressed):
        color = WHITE_KEY
        if p in pressed:
            color = mix(HAND_COLORS[pressed[p]][0], (255, 255, 255), 0.15)
        r = self.key_rects[p]
        if self.style == "equal":
            head = pygame.Rect(r.x, self.rect.y + self.black_h + self.gap, r.w, r.h - self.black_h - self.gap)
            pygame.draw.rect(surf, color, head, border_bottom_left_radius=3, border_bottom_right_radius=3)
            pygame.draw.rect(surf, color, self.tails[p])
        else:
            pygame.draw.rect(surf, color, r, border_bottom_left_radius=3, border_bottom_right_radius=3)
            pygame.draw.line(surf, WHITE_KEY_EDGE, r.topleft, (r.left, r.bottom - 1))

    def _black(self, surf, p, pressed):
        r = self.key_rects[p]
        if p in pressed:
            pygame.draw.rect(surf, HAND_COLORS[pressed[p]][1], r, border_bottom_left_radius=2,
                             border_bottom_right_radius=2)
            return
        pygame.draw.rect(surf, BLACK_KEY, r, border_bottom_left_radius=2, border_bottom_right_radius=2)
        if self.style == "equal" and r.w <= 6:
            return
        bevel = pygame.Rect(r.x + 2, r.y, r.w - 4, r.h - max(4, r.h // 9))
        pygame.draw.rect(surf, BLACK_KEY_BEVEL, bevel, border_bottom_left_radius=2,
                         border_bottom_right_radius=2)
        inner = bevel.inflate(-2, 0)
        inner.h -= 2
        pygame.draw.rect(surf, BLACK_KEY, inner)

    def _white_area(self, p):
        """The rects a white key covers."""
        r = self.key_rects[p]
        if self.style == "equal":
            return [pygame.Rect(r.x, self.rect.y + self.black_h + self.gap, r.w, r.h - self.black_h - self.gap),
                    self.tails[p]]
        return [r]

    def _shade(self):
        shade = getattr(self, "_shade_surf", None)
        if shade is None or shade.get_width() != self.rect.w:
            shade = self._shade_surf = pygame.Surface((self.rect.w, 6), pygame.SRCALPHA)
            for y in range(6):
                pygame.draw.line(shade, (0, 0, 0, 110 - y * 18), (0, y), (self.rect.w, y))
        return shade

    def _draw_all(self, surf, pressed):
        if self.style == "equal":
            pygame.draw.rect(surf, KEY_GAP, self.rect)
        for p in self.key_rects:
            if not is_black_key(p):
                self._white(surf, p, pressed)
        for p in self.key_rects:
            if is_black_key(p):
                self._black(surf, p, pressed)
        # Shadow cast by the felt strip onto the top of the keys.
        surf.blit(self._shade(), self.rect.topleft)

    def draw(self, surf, pressed):
        """pressed: dict pitch -> hand for keys currently held."""
        r = self.rect
        # what shows through the rounded corners: the background under the keyboard
        under = tuple(surf.get_at((r.x, r.bottom - 1))) if r.w and r.h else None
        base = getattr(self, "_base", None)
        if base is None or self._base_key != (tuple(r), self.style, under) or surf.get_clip() != surf.get_rect():
            if surf.get_clip() != surf.get_rect():
                return self._draw_all(surf, pressed)
            self._draw_all(surf, {})
            self._base = surf.subsurface(r).copy()
            self._base_key = (tuple(r), self.style, under)
        else:
            surf.blit(base, r)
        if not pressed:
            return
        # the pressed keys, and every key they touch, drawn again in the original order
        whites = {p for p in pressed if p in self.key_rects and not is_black_key(p)}
        blacks = {p for p in pressed if p in self.key_rects and is_black_key(p)}
        if self.style != "equal":
            # a realistic white key runs under its black neighbours: redraw those
            # (and the white keys under them) so the overlaps come out the same
            changed = True
            while changed:
                changed = False
                for p, kr in self.key_rects.items():
                    if is_black_key(p) and p not in blacks and any(kr.colliderect(self.key_rects[w]) for w in whites):
                        blacks.add(p)
                        changed = True
                for p, kr in self.key_rects.items():
                    if not is_black_key(p) and p not in whites and any(kr.colliderect(self.key_rects[b]) for b in blacks):
                        whites.add(p)
                        changed = True
        back = KEY_GAP if self.style == "equal" else under
        dirty = []
        for p in sorted(whites):
            for a in self._white_area(p):
                pygame.draw.rect(surf, back, a)
                dirty.append(a)
            self._white(surf, p, pressed)
        for p in sorted(blacks):
            kr = self.key_rects[p]
            if self.style == "equal":
                pygame.draw.rect(surf, back, kr)
            dirty.append(kr)
            self._black(surf, p, pressed)
        # the felt's shadow again over what was redrawn under it - once per column
        spans = sorted((a.left, a.right) for a in dirty if a.top < r.top + 6)
        merged = []
        for x0, x1 in spans:
            if merged and x0 <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        shade = self._shade()
        for x0, x1 in merged:
            surf.set_clip(pygame.Rect(x0, r.top, x1 - x0, 6))
            surf.blit(shade, r.topleft)
        surf.set_clip(None)


def draw_felt(surf, rect):
    pygame.draw.rect(surf, FELT, rect)
    pygame.draw.line(surf, mix(FELT, (255, 255, 255), 0.3), rect.topleft, rect.topright)


def draw_hand_area(surf, rect):
    """Background for the space below the keys; the hands are drawn on top of it."""
    pygame.draw.rect(surf, HAND_AREA_BG, rect)
    pygame.draw.line(surf, (50, 50, 62), rect.topleft, rect.topright)


# --------------------------------------------------------------------------- #
# Optional sound through the system MIDI synth (e.g. Windows GS Wavetable Synth)
# --------------------------------------------------------------------------- #
class MidiOut:
    def __init__(self, enabled=True):
        self.port = None
        self.muted = False
        if not enabled:
            return
        try:
            import pygame.midi
            pygame.midi.init()
            device = pygame.midi.get_default_output_id()
            if device >= 0:
                self.port = pygame.midi.Output(device)
                for ch in (0, 1):
                    self.port.set_instrument(0, ch)   # acoustic grand piano
        except Exception as exc:  # no synth available: run silently
            print(f"Sound disabled ({exc})")
            self.port = None

    @property
    def available(self):
        return self.port is not None

    def status(self):
        return "no synth" if not self.available else ("muted" if self.muted else "sound on")

    def note_on(self, note):
        if self.port and not self.muted:
            self.port.note_on(note.pitch, note.velocity, 1 if note.hand == LEFT else 0)

    def note_off(self, note):
        if self.port:
            self.port.note_off(note.pitch, 0, 1 if note.hand == LEFT else 0)

    def all_off(self):
        if self.port:
            for ch in (0, 1):
                self.port.write_short(0xB0 | ch, 123, 0)   # "all notes off"

    def control_change(self, number, value):
        """Pedals (sustain 64, sostenuto 66, soft 67) go to both hands' channels."""
        if self.port:
            for ch in (0, 1):
                self.port.write_short(0xB0 | ch, number, value)

    def pedals_up(self):
        for c in (64, 66, 67):
            self.control_change(c, 0)

    def close(self):
        if self.port:
            self.all_off()
            self.port.close()
            self.port = None
            import pygame.midi
            pygame.midi.quit()


# --------------------------------------------------------------------------- #
# Playback transport (shared by the player and the editor)
# --------------------------------------------------------------------------- #
class Performance:
    """
    What the hands actually play - [(press, release, note)] - which can differ
    from the MIDI notes: chords too wide for the pianist's hand are rolled,
    fingers let go early to jump, and notes a hand can't take (a sixth note in
    one hand) aren't played. Queried like MidiSong.
    """

    def __init__(self, items):
        self.items = sorted(items, key=lambda it: it[0])
        self._starts = [it[0] for it in self.items]
        self._max = max((it[1] - it[0] for it in self.items), default=0.0)

    @classmethod
    def from_animators(cls, animators):
        items = []
        for a in animators:
            items += a.performance
        return cls(items)

    @classmethod
    def from_notes(cls, notes):
        return cls([(n.start, n.end, n) for n in notes])

    def active(self, t):
        """[(press, release, note)] sounding (key down) at t."""
        lo = bisect.bisect_left(self._starts, t - self._max)
        hi = bisect.bisect_right(self._starts, t)
        return [it for it in self.items[lo:hi] if it[1] > t]

    def starting(self, t0, t1):
        """[(press, release, note)] with t0 < press <= t1."""
        lo = bisect.bisect_right(self._starts, t0)
        hi = bisect.bisect_right(self._starts, t1)
        return self.items[lo:hi]


class Transport:
    """
    Song clock with play/pause, seek and speed, sending to the synth what
    the hands play (self.perf, a Performance) and the pedals in the file.
    """

    def _init_transport(self, midi, speed=1.0):
        self.midi = midi
        self.speed = speed
        self.song = None
        self.perf = None
        self.t = 0.0
        self.paused = True
        self.sounding = {}             # id(note) -> (release, note) currently sent to the synth

    def _performance(self):
        if self.perf is None and self.song is not None:
            self.perf = Performance.from_notes(self.song.notes)
        return self.perf

    def keys_down(self):
        """{pitch: hand} for the keys the hands are holding down right now."""
        perf = self._performance()
        return {n.pitch: n.hand for _, _, n in perf.active(self.t)} if perf else {}

    def sustain_down(self):
        return bool(self.song) and self.song.sustain_at(self.t)

    def _send_pedals(self):
        if self.song:
            for c, v in self.song.control_state(self.t).items():
                self.midi.control_change(c, v)

    def seek(self, t):
        if not self.song:
            return
        self.t = max(-LEAD_IN, min(t, self.song.duration))
        self.midi.all_off()
        self._send_pedals()
        # Treat notes already in progress as "already played" so they don't retrigger.
        perf = self._performance()
        self.sounding = {id(n): (r, n) for _, r, n in perf.active(self.t)}

    def toggle_pause(self):
        if not self.song:
            return
        if self.paused and self.t >= self.song.duration:
            self.seek(-LEAD_IN)
        self.paused = not self.paused
        if self.paused:
            self.midi.pedals_up()
            self.midi.all_off()
        else:
            self._send_pedals()

    def change_speed(self, delta):
        self.speed = max(SPEED_MIN, min(SPEED_MAX, round(self.speed + delta, 2)))

    def update(self, dt):
        if not self.song or self.paused:
            return
        t0, t1 = self.t, self.t + dt * self.speed
        self.t = t1
        if t1 >= self.song.duration + 0.5:
            self.t = self.song.duration
            self.paused = True
            self.midi.pedals_up()
            self.midi.all_off()
            self.sounding = {}
            return
        perf = self._performance()
        # everything that happens in (t0, t1], in time order: releases,
        # pedal changes, then new notes
        events = [(r, 0, n) for r, n in self.sounding.values() if r <= t1]
        for p, r, n in perf.starting(t0, t1):
            events.append((p, 2, (r, n)))
            if r <= t1:
                events.append((r, 0, n))
        events += [(tc, 1, (c, v)) for tc, c, v in self.song.controls_between(t0, t1)]
        events.sort(key=lambda e: (e[0], e[1]))
        for _, kind, x in events:
            if kind == 0:
                if id(x) in self.sounding or not any(m.pitch == x.pitch for _, m in self.sounding.values()):
                    self.sounding.pop(id(x), None)
                    self.midi.note_off(x)
            elif kind == 1:
                self.midi.control_change(*x)
            else:
                r, n = x
                # a key struck again while still down: the old note ends here
                for k, (_, m) in list(self.sounding.items()):
                    if m.pitch == n.pitch:
                        del self.sounding[k]
                        self.midi.note_off(m)
                if r > t1:
                    self.sounding[id(n)] = (r, n)
                self.midi.note_on(n)


# --------------------------------------------------------------------------- #
# File dialogs (native, via tkinter)
# --------------------------------------------------------------------------- #
_MIDI_TYPES = [("MIDI or PIG files", "*.mid *.midi *.txt"), ("MIDI files", "*.mid *.midi"),
               ("PIG fingering files", "*.txt"), ("All files", "*.*")]
_SAVE_MIDI = [("MIDI file", "*.mid"), ("PIG fingering file", "*.txt")]
_SAVE_PIG = [("PIG fingering file", "*.txt")]


def _tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)   # open in front of the pygame window
    except Exception:
        pass
    return root


def pick_file(title="Open MIDI file", initialdir=None):
    """Native open dialog; returns '' if cancelled or unavailable."""
    try:
        from tkinter import filedialog
        root = _tk_root()
        path = filedialog.askopenfilename(parent=root, title=title, filetypes=_MIDI_TYPES,
                                          initialdir=initialdir or None)
        root.destroy()
        return path or ""
    except Exception as exc:
        print(f"File dialog unavailable ({exc})")
        return ""
    finally:
        _after_dialog()


def save_file_dialog(title="Export MIDI file", initialdir=None, initialfile=None, pig_only=False):
    """Native save dialog (MIDI or PIG; PIG only for PIG sources); '' if cancelled or unavailable."""
    try:
        from tkinter import filedialog
        root = _tk_root()
        path = filedialog.asksaveasfilename(parent=root, title=title,
                                            filetypes=_SAVE_PIG if pig_only else _SAVE_MIDI,
                                            defaultextension=".txt" if pig_only else ".mid",
                                            initialdir=initialdir or None,
                                            initialfile=initialfile or None)
        root.destroy()
        return path or ""
    except Exception as exc:
        print(f"File dialog unavailable ({exc})")
        return ""
    finally:
        _after_dialog()


def _after_dialog():
    # Clicks and key presses made in the dialog must not reach the app.
    try:
        pygame.event.clear()
    except Exception:
        pass


def default_export_name(path):
    base, ext = os.path.splitext(os.path.basename(path or "song"))
    return f"{base} (fingered){'.txt' if ext.lower() == '.txt' else '.mid'}"


# --------------------------------------------------------------------------- #
# Small widgets
# --------------------------------------------------------------------------- #
class Button:
    def __init__(self, label, action, font="normal", sub=None, key_hint=None):
        self.label, self.action, self.font, self.sub, self.key_hint = label, action, font, sub, key_hint
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.enabled = True
        self.active = False            # drawn highlighted (a toggle that's on)

    def hit(self, pos):
        return self.enabled and self.rect.collidepoint(pos)

    def draw(self, surf, fonts, mouse):
        hover = self.hit(mouse)
        base = BUTTON_HOVER if hover else BUTTON
        if self.active:
            base = mix(base, ACCENT, 0.35)
        if not self.enabled:
            base = mix(BUTTON, BG, 0.5)
        pygame.draw.rect(surf, base, self.rect, border_radius=8)
        pygame.draw.rect(surf, mix(base, (255, 255, 255), 0.18), self.rect, 1, border_radius=8)
        color = TEXT if self.enabled else TEXT_DIM
        img = fonts[self.font].render(self.label, True, color)
        if self.sub:
            sub = fonts["small"].render(self.sub, True, TEXT_DIM)
            gap = 6
            total = img.get_height() + gap + sub.get_height()
            y = self.rect.centery - total // 2
            surf.blit(img, img.get_rect(midtop=(self.rect.centerx, y)))
            surf.blit(sub, sub.get_rect(midtop=(self.rect.centerx, y + img.get_height() + gap)))
        else:
            surf.blit(img, img.get_rect(center=self.rect.center))
        if self.key_hint:
            k = fonts["small"].render(self.key_hint, True, TEXT_DIM)
            surf.blit(k, k.get_rect(topright=(self.rect.right - 10, self.rect.top + 8)))


def blit_shadowed(surf, img_font, text, color, pos, shadow=(0, 0, 0)):
    """Draw text with a dark drop shadow (readable over any background); returns its width."""
    for d in ((2, 2), (1, 1)):
        surf.blit(img_font.render(text, True, shadow), (pos[0] + d[0], pos[1] + d[1]))
    img = img_font.render(text, True, color)
    surf.blit(img, pos)
    return img.get_width()


def center_text(surf, fonts, rect, text, font="big"):
    img = fonts[font].render(text, True, TEXT)
    box = img.get_rect(center=rect.center).inflate(40, 20)
    panel = pygame.Surface(box.size, pygame.SRCALPHA)
    panel.fill((0, 0, 0, 150))
    surf.blit(panel, box)
    surf.blit(img, img.get_rect(center=box.center))


class Dialog:
    """A small modal box with a message and buttons; `choice` is set when one is clicked."""

    def __init__(self, title, message, options):
        self.title, self.message = title, message
        self.buttons = [Button(label, value) for label, value in options]
        self.choice = None

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons:
                if b.hit(event.pos):
                    self.choice = b.action
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.choice = "cancel"

    def draw(self, surf, fonts):
        w, h = surf.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 140))
        surf.blit(shade, (0, 0))
        box = pygame.Rect(0, 0, 520, 190)
        box.center = (w // 2, h // 2)
        pygame.draw.rect(surf, PANEL, box, border_radius=12)
        pygame.draw.rect(surf, PANEL_EDGE, box, 1, border_radius=12)
        surf.blit(fonts["button"].render(self.title, True, TEXT), (box.x + 24, box.y + 20))
        surf.blit(fonts["normal"].render(self.message, True, TEXT_DIM), (box.x + 24, box.y + 62))
        bw, gap = 140, 12
        x = box.right - 24 - len(self.buttons) * bw - (len(self.buttons) - 1) * gap
        mouse = pygame.mouse.get_pos()
        for b in self.buttons:
            b.rect = pygame.Rect(x, box.bottom - 24 - 42, bw, 42)
            b.draw(surf, fonts, mouse)
            x += bw + gap


def draw_pianist_badge(surf, fonts, area, pianist, pedal=None):
    """
    The active pianist, small, in the bottom-right corner of `area` (and the
    sustain pedal's state in the bottom-left, when the song uses it).
    """
    name = pianist.name
    img = fonts["small"].render(name, True, TEXT_DIM)
    pad = 8
    w = img.get_width() + 30
    box = pygame.Rect(area.right - w - pad, area.bottom - img.get_height() - 10 - pad, w,
                      img.get_height() + 8)
    panel = pygame.Surface(box.size, pygame.SRCALPHA)
    panel.fill((0, 0, 0, 90))
    surf.blit(panel, box)
    pygame.draw.circle(surf, tuple(pianist.badge_color), (box.x + 12, box.centery), 6)
    pygame.draw.circle(surf, (150, 150, 160), (box.x + 12, box.centery), 6, 1)
    surf.blit(img, (box.x + 24, box.y + 4))
    if pedal is not None:
        txt = fonts["small"].render("Ped.", True, ACCENT if pedal else (70, 70, 84))
        r = txt.get_rect(bottomleft=(area.x + pad + 4, area.bottom - pad - 4))
        surf.blit(txt, r)


class Slider:
    """A horizontal slider with a label and a value readout. on_change(value) as it moves."""

    def __init__(self, label, lo, hi, value, on_change, fmt=None, color=None, lo_label=None,
                 hi_label=None, step=None):
        self.label, self.lo, self.hi, self.value = label, lo, hi, value
        self.on_change, self.fmt, self.color = on_change, fmt or (lambda v: f"{v:.2f}"), color
        self.lo_label, self.hi_label, self.step = lo_label, hi_label, step
        self.rect = pygame.Rect(0, 0, 0, 0)       # whole widget
        self.track = pygame.Rect(0, 0, 0, 0)
        self.dragging = False
        self.enabled = True

    def layout(self, rect):
        self.rect = pygame.Rect(rect)
        top = 22
        self.track = pygame.Rect(self.rect.x + 8, self.rect.y + top + 8, self.rect.w - 16, 6)

    def _frac(self):
        return 0.0 if self.hi == self.lo else (self.value - self.lo) / (self.hi - self.lo)

    def _set_from_x(self, x):
        f = min(1.0, max(0.0, (x - self.track.x) / max(1, self.track.w)))
        v = self.lo + f * (self.hi - self.lo)
        if self.step:
            v = round(v / self.step) * self.step
        v = min(self.hi, max(self.lo, v))
        if v != self.value:
            self.value = v
            self.on_change(v)

    def handle_event(self, event):
        """True if the event was the slider's."""
        if not self.enabled or self.track.w <= 0:
            return False                  # not laid out (drawn) yet
        hit = self.track.inflate(16, 22)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and hit.collidepoint(event.pos):
            self.dragging = True
            self._set_from_x(event.pos[0])
            return True
        if event.type == pygame.MOUSEMOTION and self.dragging:
            self._set_from_x(event.pos[0])
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging:
            self.dragging = False
            return True
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            step = self.step or (self.hi - self.lo) / 100.0
            v = min(self.hi, max(self.lo, self.value + step * event.y))
            if v != self.value:
                self.value = v
                self.on_change(v)
            return True
        return False

    def draw(self, surf, fonts):
        f = fonts
        col = TEXT if self.enabled else TEXT_DIM
        surf.blit(f["small"].render(self.label, True, col), (self.rect.x + 8, self.rect.y))
        val = f["small"].render(self.fmt(self.value), True, ACCENT if self.enabled else TEXT_DIM)
        surf.blit(val, val.get_rect(topright=(self.rect.right - 8, self.rect.y)))
        tr = self.track
        pygame.draw.rect(surf, (58, 58, 72), tr, border_radius=3)
        fill = pygame.Rect(tr.x, tr.y, int(tr.w * self._frac()), tr.h)
        pygame.draw.rect(surf, self.color or mix(ACCENT, BG, 0.3), fill, border_radius=3)
        kx = tr.x + int(tr.w * self._frac())
        pygame.draw.circle(surf, (235, 235, 240) if self.enabled else TEXT_DIM, (kx, tr.centery), 8)
        pygame.draw.circle(surf, (30, 30, 36), (kx, tr.centery), 8, 1)
        if self.lo_label or self.hi_label:
            if self.lo_label:
                surf.blit(f["label"].render(self.lo_label, True, TEXT_DIM), (tr.x, tr.bottom + 8))
            if self.hi_label:
                img = f["label"].render(self.hi_label, True, TEXT_DIM)
                surf.blit(img, img.get_rect(topright=(tr.right, tr.bottom + 8)))


class TextInput:
    """A one-line text box. on_submit(text) on Enter; typing needs the box to have focus."""

    def __init__(self, text="", placeholder="", max_len=40, on_change=None, on_submit=None):
        self.text, self.placeholder, self.max_len = text, placeholder, max_len
        self.on_change, self.on_submit = on_change, on_submit
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.focus = False

    def set_focus(self, on):
        if on == self.focus:
            return
        self.focus = on
        try:
            (pygame.key.start_text_input if on else pygame.key.stop_text_input)()
        except Exception:
            pass

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.set_focus(self.rect.collidepoint(event.pos))
            return self.focus
        if not self.focus:
            return False
        if event.type == pygame.TEXTINPUT:
            add = "".join(ch for ch in event.text if ch.isprintable())
            if add and len(self.text) < self.max_len:
                self.text = (self.text + add)[:self.max_len]
                if self.on_change:
                    self.on_change(self.text)
            return True
        if event.type == pygame.KEYDOWN:
            mod = getattr(event, "mod", 0)
            if event.key == pygame.K_BACKSPACE:
                if mod & (pygame.KMOD_CTRL | pygame.KMOD_META):
                    self.text = self.text.rstrip()
                    self.text = self.text[:self.text.rfind(" ") + 1] if " " in self.text else ""
                else:
                    self.text = self.text[:-1]
                if self.on_change:
                    self.on_change(self.text)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.on_submit:
                    self.on_submit(self.text)
            elif event.key == pygame.K_ESCAPE:
                self.set_focus(False)
                return True
            return True        # typing never reaches the app's shortcuts
        return False

    def draw(self, surf, fonts, font="normal"):
        r = self.rect
        pygame.draw.rect(surf, (30, 30, 38), r, border_radius=6)
        pygame.draw.rect(surf, ACCENT if self.focus else PANEL_EDGE, r, 1, border_radius=6)
        f = fonts[font]
        if self.text:
            img = f.render(self.text, True, TEXT)
        else:
            img = f.render(self.placeholder, True, TEXT_DIM)
        clip = surf.get_clip()
        surf.set_clip(r.inflate(-12, 0))
        x = r.x + 10
        if self.text and img.get_width() > r.w - 24:
            x = r.right - 14 - img.get_width()
        surf.blit(img, (x, r.centery - img.get_height() // 2))
        surf.set_clip(clip)
        if self.focus and (pygame.time.get_ticks() // 530) % 2 == 0:
            cx = min(r.right - 10, x + (img.get_width() if self.text else 0) + 2)
            pygame.draw.line(surf, TEXT, (cx, r.y + 7), (cx, r.bottom - 8), 2)


def show_loading(surf, fonts, text):
    """Draw a 'Loading...' notice right away (before a slow load), over whatever is on screen."""
    try:
        w, h = surf.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        img = fonts["big"].render(text, True, TEXT)
        box = img.get_rect(center=(w // 2, h // 2)).inflate(48, 28)
        pygame.draw.rect(surf, PANEL, box, border_radius=12)
        pygame.draw.rect(surf, PANEL_EDGE, box, 1, border_radius=12)
        surf.blit(img, img.get_rect(center=box.center))
        pygame.display.flip()
        pygame.event.pump()
    except Exception:
        pass
