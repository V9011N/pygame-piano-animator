"""
editor.py - The piano roll: Hand-thesia's fingering editor grown into a MIDI editor.

The keyboard runs down the left, time runs left to right (notes come in from
the right as it plays), the velocity, pedal and tempo lanes sit underneath,
with a scroll bar on each axis. Every scroll and zoom eases to its target at
the frame rate (view.Axis).

    Click a note                 select it (Ctrl/Shift+click adds or removes it)
    Drag a note / its edge       move it (Ctrl: a copy) / change its length (selected notes move together)
    Drag on empty space          select every note in the box (Ctrl/Shift adds)
    Double-click empty space     add a note (or switch on Draw: P, and click)
    Delete / Backspace           remove the selected notes
    Right-click                  hand & finger, channel, delete... (several notes: re-finger as one hand)
    Wheel / Shift+wheel          scroll through time / up and down the keyboard
    Ctrl+wheel / Alt+wheel       zoom time / zoom rows (over the keyboard: Ctrl+wheel zooms rows)
    Middle-drag                  pan in any direction
    Click or drag the ruler      move the cursor; Space plays from it
    [ / ]                        select everything left / right of the cursor
    Arrows                       move the selection (Up/Down: semitones, Shift: octaves); no selection: the cursor
    Ctrl+C / X / V / D           copy / cut / paste at the cursor / duplicate
    Z / Shift+Z                  zoom to fit everything / the selection
    F                            show fingering (Hand-thesia's planner suggests what the file doesn't say)
    1-5, R, L, G                 finger the selected note / move notes to a hand / mark a glissando
    H                            hide / show the keyboard and hands under the roll (or click the arrow)
    Esc                          let go of the drag, the selection, Draw - with none: back to the main menu
"""
from __future__ import annotations

import math
import time

import numpy as np
import pygame

from . import theme as th
from .compare import CompareMixin
from .document import Document, Note, note_with
from .fingering_ui import FingeringMixin
from .lanes import LanesMixin
from .render import RenderMixin
from .theme import px
from .view import Axis
from .widgets import SEPARATOR, ContextMenu, MenuItem, ScrollBar

DRAG_START = 4                 # pixels before a press becomes a drag
DOUBLE_CLICK_T = 0.38
PLAYHEAD_FRAC = 0.3            # where the cursor sits across the roll while it plays (Hand-thesia's playhead)
FIT_MARGIN = 0.04
WHEEL_SCROLL = 0.08            # share of the view one wheel step scrolls through time
WHEEL_ZOOM = 0.8               # time zoom per wheel step
ROW_WHEEL = 3                  # rows one Shift+wheel step scrolls
ROW_ZOOM = 0.87                # row zoom per Alt+wheel step
MIN_ROWS, MAX_ROWS = 6, 128

SNAPS = [("Bar", "bar"), ("1/2", 2.0), ("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125),
         ("1/8 triplet", 1 / 3), ("1/16 triplet", 1 / 6), ("Off", 0)]
DEFAULT_SNAP = 4


def _ctrl(mod):
    return bool(mod & (pygame.KMOD_CTRL | pygame.KMOD_META))


class Editor(RenderMixin, LanesMixin, FingeringMixin, CompareMixin):
    def __init__(self, app):
        self.app = app
        self.fonts = app.fonts
        self.doc = Document.new_empty()
        self.vx = Axis(0, 1920 * 4, min_span=60, bound_lo=0, bound_hi=1920 * 16)
        self.vy = Axis(40, 84, min_span=MIN_ROWS, max_span=MAX_ROWS, bound_lo=0, bound_hi=128)
        self.cursor = 0
        self.selection = set()
        self.hidden_channels = set()
        self.channel = 0                 # channel new notes go on
        self.draw_mode = False
        self.snap_i = DEFAULT_SNAP
        self.note_len = None             # length of the last note drawn (new notes take it)
        self.new_velocity = 90
        self.press = None                # what a held mouse button is doing
        self.hover = None                # note id under the mouse
        self.menu = None
        self.lanes_h = None              # (None: about a third of the editor, at most 220 px; dragging the divider sets it)
        self.follow = True               # the view follows the cursor while playing
        self.clipboard = []
        self.time_ruler = False          # ruler labels: bars (False) or minutes:seconds
        self._last_click = (0.0, (0, 0))
        self._cursor_shape = None
        self._bounds_ver = -1
        self.area = pygame.Rect(0, 0, 100, 100)
        self.vscroll = ScrollBar(vertical=True)
        self.src_vscroll = ScrollBar(vertical=True)
        self.hscroll = ScrollBar()
        self._init_fingering()
        self._init_lanes()
        self._init_compare()

    # ----- the document -------------------------------------------------------
    def set_document(self, doc, view=None):
        self.app.player.stop()
        self.doc = doc
        self.selection.clear()
        self.hover = None
        self.press = None
        self.menu = None
        self.close_impose()
        self._reset_fingering()
        self._reset_lanes()
        counts = doc.channel_counts()
        self.channel = counts.most_common(1)[0][0] if counts else 0
        self.hidden_channels = set()
        self._bounds_ver = -1
        self._update_bounds()
        self.cursor = 0
        if view:
            self.vx.jump(*view["x"])
            self.vy.jump(*view["y"])
        else:
            self.fit_view(initial=True)
        self.layout(self.area)

    @property
    def ppq(self):
        return self.doc.ppq

    def snap_step(self, tick=None):
        """Grid step in ticks (0: off)."""
        v = SNAPS[self.snap_i][1]
        if v == "bar":
            return self.doc.meter_map().bar_ticks(tick if tick is not None else self.cursor)
        return v * self.ppq

    def snap(self, tick, mode="round", free=False):
        step = self.snap_step(tick)
        if free or step <= 0:
            return int(round(tick))
        return self.doc.meter_map().snap(tick, step, mode)

    def default_len(self):
        if self.note_len:
            return self.note_len
        step = self.snap_step()
        return int(step) if step >= self.ppq / 16 else self.ppq // 2

    # ----- geometry -------------------------------------------------------------
    def layout(self, area):
        self.area = pygame.Rect(area)
        a = self.area
        sb, gw, rh, split = px(13), px(66), px(26), px(7)
        self.SB = sb
        want = self.lanes_h if self.lanes_h is not None else min(px(220), a.h * 0.3)
        lanes = int(min(max(want, px(90)), max(px(90), a.h * 0.55)))
        self.corner = pygame.Rect(a.x, a.y, gw, rh)
        self.ruler = pygame.Rect(a.x + gw, a.y, a.w - gw - sb, rh)
        roll_top = a.y + rh
        lanes_top = a.bottom - sb - lanes
        roll_bottom = lanes_top - split
        self.splitter = pygame.Rect(a.x, roll_bottom, a.w, split)
        x, w = a.x + gw, a.w - gw - sb
        if self.impose is not None:
            ih = px(40)
            each = max(px(40), (roll_bottom - roll_top - ih) // 2)
            self.src_roll = pygame.Rect(x, roll_top, w, each)
            self.impose_bar = pygame.Rect(a.x, roll_top + each, a.w, roll_bottom - roll_top - 2 * each)
            self.roll = pygame.Rect(x, self.impose_bar.bottom, w, each)
            self.src_gutter = pygame.Rect(a.x, self.src_roll.y, gw, each)
            self.src_vscroll.rect = pygame.Rect(self.src_roll.right, self.src_roll.y, sb, each)
        else:
            self.src_roll = None
            self.impose_bar = None
            self.roll = pygame.Rect(x, roll_top, w, max(px(40), roll_bottom - roll_top))
        self.gutter = pygame.Rect(a.x, self.roll.y, gw, self.roll.h)
        self.vscroll.rect = pygame.Rect(self.roll.right, self.roll.y, sb, self.roll.h)
        self.hscroll.rect = pygame.Rect(x, a.bottom - sb, w, sb)
        self.hscroll_corner = pygame.Rect(a.x, a.bottom - sb, gw, sb)
        self.vx.pixels = max(1, self.roll.w)
        self.vy.pixels = max(1, self.roll.h)
        self._layout_lanes(pygame.Rect(a.x, lanes_top, a.w, lanes), gw, sb)
        if self.impose is not None:
            self._layout_impose_bar()

    # world <-> screen (the x axis is shared by the ruler, the rolls and the lanes)
    def x_of(self, tick):
        return self.roll.x + (tick - self.vx.lo) * self.roll.w / self.vx.span

    def t_of(self, x):
        return self.vx.lo + (x - self.roll.x) * self.vx.span / self.roll.w

    def y_of(self, u, rect=None):
        r = rect or self.roll
        return r.y + (self.vy.hi - u) * r.h / self.vy.span

    def u_of(self, y, rect=None):
        r = rect or self.roll
        return self.vy.hi - (y - r.y) * self.vy.span / r.h

    def row_h(self, rect=None):
        r = rect or self.roll
        return r.h / self.vy.span

    def ticks_per_px(self):
        return self.vx.span / max(1, self.roll.w)

    def roll_at(self, pos):
        """The roll under pos: self.roll, self.src_roll or None."""
        if self.roll.collidepoint(pos):
            return self.roll
        if self.src_roll is not None and self.src_roll.collidepoint(pos):
            return self.src_roll
        return None

    # ----- view -----------------------------------------------------------------
    def _update_bounds(self):
        if self._bounds_ver == self.doc.version:
            return
        self._bounds_ver = self.doc.version
        bar = self.doc.meter_map().bar_ticks(self.doc.end_tick())
        end = max(self.doc.end_tick(), 4 * bar)
        self.vx.set_bounds(0, end + 8 * bar)
        self.vx.min_span = max(8, self.ppq / 2)

    def fit_view(self, initial=False, selection=False):
        """Zoom to show everything (or the selection); a new file opens on its first ~12 seconds."""
        doc = self.doc
        ix = doc.index()
        notes = [doc.notes[i] for i in self.selection if i in doc.notes] if selection else None
        if selection and not notes:
            return
        if notes:
            t0, t1 = min(n.start for n in notes), max(n.end for n in notes)
            p0, p1 = min(n.pitch for n in notes), max(n.pitch for n in notes)
        elif len(ix.start):
            t0, t1 = int(ix.start.min()), int(ix.end.max())
            p0, p1 = doc.pitch_range()
        else:
            t0, t1 = 0, doc.meter_map().bar_ticks(0) * 8
            p0, p1 = 48, 72
        if initial and len(ix.start):
            tm = doc.tempo_map()
            t1 = min(t1, max(tm.tick(tm.sec(t0) + 12.0), t0 + self.ppq * 4))
            t0 = max(0, t0 - self.ppq // 2)
            first = ix.window(t0, t1)                  # (the rows the opening needs)
            if len(first):
                p0, p1 = int(ix.pitch[first].min()), int(ix.pitch[first].max())
        span = max(t1 - t0, self.ppq)
        lo, hi = t0 - span * FIT_MARGIN, t1 + span * FIT_MARGIN
        rows = max(p1 - p0 + 5, 24 if not selection else 12)
        if self.roll.h > 0:
            rows = min(rows, max(MIN_ROWS, self.roll.h / max(1, px(11 if initial else 5))))
        mid = (p0 + p1 + 1) / 2
        if initial:
            self.vx.jump(max(0, lo), hi)
            self.vy.jump(mid - rows / 2, mid + rows / 2)
        else:
            self.vx.target(max(0, lo), hi)
            self.vy.target(mid - rows / 2, mid + rows / 2)

    def reveal(self, tick, pitch=None, center=False):
        """Bring a time (and pitch) into view, easing there."""
        span = self.vx.target_span
        if center or not (self.vx.tlo + span * 0.05 <= tick <= self.vx.thi - span * 0.05):
            self.vx.target(tick - span * 0.4, tick + span * 0.6)
        if pitch is not None:
            rows = self.vy.target_span
            if center or not (self.vy.tlo + 1 <= pitch <= self.vy.thi - 2):
                self.vy.target(pitch + 0.5 - rows / 2, pitch + 0.5 + rows / 2)

    def zoom_time(self, factor, x=None):
        anchor = self.t_of(x) if x is not None else (
            self.cursor if self.vx.lo <= self.cursor <= self.vx.hi else (self.vx.lo + self.vx.hi) / 2)
        self.vx.zoom_at(factor, anchor)

    def zoom_rows(self, factor, y=None, rect=None):
        anchor = self.u_of(y, rect) if y is not None else (self.vy.lo + self.vy.hi) / 2
        self.vy.zoom_at(factor, anchor)

    def scroll_time(self, steps):
        self.vx.scroll_by(steps * self.vx.target_span * WHEEL_SCROLL)
        self._user_scrolled()

    def scroll_rows(self, rows):
        self.vy.scroll_by(rows)

    def _user_scrolled(self):
        if self.app.player.playing:
            self.follow = False

    # ----- per frame ------------------------------------------------------------
    def update(self, dt):
        """Animate; True while anything moves (the app then draws every frame)."""
        self._update_bounds()
        player = self.app.player
        moving = False
        if player.playing:
            self.cursor = player.tick_position(self.doc)
            if self.follow and not (self.press and self.press["kind"] in ("pan", "hscroll")):
                span = self.vx.target_span
                self.vx.target(self.cursor - span * PLAYHEAD_FRAC, self.cursor + span * (1 - PLAYHEAD_FRAC))
            moving = True
        moving |= self.vx.update(dt)
        moving |= self.vy.update(dt)
        p = self.press
        if p and p["kind"] in ("band", "move", "draw", "resize_r", "resize_l") and p.get("moved"):
            moving |= self._autoscroll(dt)
        moving |= self._update_fingering(dt)
        moving |= self._update_compare(dt)
        return moving

    def _autoscroll(self, dt):
        """Dragging past the roll's edge scrolls it."""
        x, y = pygame.mouse.get_pos()
        r = self.press.get("roll") or self.roll
        moved = False
        edge = px(14)
        if x > r.right - edge:
            self.vx.jump(self.vx.lo + self.vx.span * 1.2 * dt, self.vx.hi + self.vx.span * 1.2 * dt)
            moved = True
        elif x < r.x + edge and self.vx.lo > self.vx.bound_lo:
            self.vx.jump(self.vx.lo - self.vx.span * 1.2 * dt, self.vx.hi - self.vx.span * 1.2 * dt)
            moved = True
        if y < r.y + edge // 2:
            self.vy.jump(self.vy.lo + 18 * dt, self.vy.hi + 18 * dt)
            moved = True
        elif y > r.bottom - edge // 2:
            self.vy.jump(self.vy.lo - 18 * dt, self.vy.hi - 18 * dt)
            moved = True
        if moved:
            self._drag_motion((x, y), pygame.key.get_mods())
        return moved

    # ----- notes under the mouse ----------------------------------------------
    def visible(self, n):
        return n.channel not in self.hidden_channels

    def note_at(self, pos):
        """The note id under pos in the main roll (the latest-starting one on top), or None."""
        if not self.roll.collidepoint(pos):
            return None
        t = self.t_of(pos[0])
        p = int(math.floor(self.u_of(pos[1])))
        tpp = self.ticks_per_px()
        ix = self.doc.index()
        best = None
        for i in ix.window(t - 4 * tpp, t + tpp):
            if ix.pitch[i] != p:
                continue
            n = ix.notes[i]
            if not self.visible(n):
                continue
            if n.start - 0.5 * tpp <= t <= max(n.end, n.start + 4 * tpp):
                best = n.id
        return best

    def edge_at(self, pos, nid):
        """'r' / 'l' when pos is on a note's right / left edge (resize), else None."""
        n = self.doc.notes.get(nid)
        if n is None:
            return None
        x0, x1 = self.x_of(n.start), self.x_of(n.end)
        w = x1 - x0
        grip = min(px(6), max(px(3), w / 4))
        if pos[0] >= x1 - grip and w > px(6):
            return "r"
        if pos[0] <= x0 + grip and w > px(16):
            return "l"
        return None

    def notes_in_box(self, t0, t1, u0, u1):
        t0, t1 = min(t0, t1), max(t0, t1)
        u0, u1 = min(u0, u1), max(u0, u1)
        ix = self.doc.index()
        out = set()
        for i in ix.window(t0, t1):
            n = ix.notes[i]
            if n.pitch + 1 > u0 and n.pitch < u1 and n.start < t1 and self.visible(n):
                out.add(n.id)
        return out

    # ----- editing --------------------------------------------------------------
    def say(self, text):
        self.app.say(text)

    def add_note(self, tick, pitch, length=None, velocity=None):
        doc = self.doc
        ch = self.channel
        st, ov = doc.release_style()
        n = Note(doc.new_id(), doc.track_for_channel(ch), ch, int(pitch), int(tick), int(tick + (length or self.default_len())),
                 int(velocity or self.new_velocity), off_velocity=ov, off_status=st)
        doc.put_note(n)
        return n

    def delete_selection(self):
        ids = [i for i in self.selection if i in self.doc.notes]
        if not ids:
            return
        fing = sum(1 for i in ids if self.doc.notes[i].has_fingering)
        self.doc.begin(f"Delete {len(ids)} note{'s' if len(ids) != 1 else ''}")
        for i in ids:
            self.doc.remove_note(i)
        self.doc.commit()
        self.selection.clear()
        self.say(f"Deleted {len(ids)} note{'s' if len(ids) != 1 else ''}"
                 + (f" ({fing} with fingering)" if fing else "") + " - Ctrl+Z brings them back")

    def select_side(self, right, add=False):
        """Every note left of the cursor (starting before it), or right of it (starting at or after it)."""
        c = self.cursor
        ix = self.doc.index()
        pick = {n.id for n in ix.notes if self.visible(n) and ((n.start >= c) if right else (n.start < c))}
        self.selection = (self.selection | pick) if add else pick
        side = "right" if right else "left"
        self.say(f"{len(pick)} note{'s' if len(pick) != 1 else ''} {side} of the cursor selected"
                 + ("" if pick else " (none there)"))

    def select_all(self):
        self.selection = {n.id for n in self.doc.notes.values() if self.visible(n)}
        self.say(f"All {len(self.selection)} notes selected")

    def nudge(self, dt=0, dp=0):
        ids = [i for i in self.selection if i in self.doc.notes]
        if not ids:
            return
        notes = [self.doc.notes[i] for i in ids]
        dt = max(dt, -min(n.start for n in notes))
        dp = max(min(dp, 127 - max(n.pitch for n in notes)), -min(n.pitch for n in notes))
        if dt == 0 and dp == 0:
            return
        label = "Transpose" if dp else "Move notes"
        self.doc.begin(label)
        for n in notes:
            self.doc.put_note(note_with(n, start=n.start + dt, end=n.end + dt, pitch=n.pitch + dp))
        self.doc.commit()
        if dp and len(notes) <= 12:
            for n in notes[:6]:
                self.app.player.preview(n.channel, n.pitch + dp, n.velocity, 0.2)

    def copy_selection(self, cut=False):
        notes = sorted((self.doc.notes[i] for i in self.selection if i in self.doc.notes), key=lambda n: n.start)
        if not notes:
            return
        t0 = notes[0].start
        self.clipboard = [(n.start - t0, n) for n in notes]
        if cut:
            self.delete_selection()
            self.say(f"Cut {len(notes)} notes")
        else:
            self.say(f"Copied {len(notes)} notes")

    def paste(self, at=None):
        if not self.clipboard:
            return
        at = self.snap(self.cursor if at is None else at)
        doc = self.doc
        doc.begin(f"Paste {len(self.clipboard)} notes")
        new = set()
        for off, n in self.clipboard:
            m = note_with(n, start=at + off, end=at + off + n.length)
            m = Note(doc.new_id(), m.track if m.track < len(doc.tracks) else doc.track_for_channel(m.channel),
                     m.channel, m.pitch, m.start, m.end, m.velocity, m.off_velocity, m.off_status,
                     m.hand, m.finger, m.gliss)
            doc.put_note(m)
            new.add(m.id)
        doc.commit()
        self.selection = new
        self.say(f"Pasted {len(new)} notes at the cursor")

    def duplicate(self):
        notes = [self.doc.notes[i] for i in self.selection if i in self.doc.notes]
        if not notes:
            return
        t0, t1 = min(n.start for n in notes), max(n.end for n in notes)
        step = self.snap_step(t1) or self.ppq
        at = int(math.ceil((t1 - t0) / step) * step) + t0 if step else t1
        saved = self.clipboard
        self.clipboard = [(n.start - t0, n) for n in sorted(notes, key=lambda n: n.start)]
        self.paste(at)
        self.clipboard = saved

    def set_channel(self, ids, ch):
        ids = [i for i in ids if i in self.doc.notes]
        if not ids:
            return
        self.doc.begin(f"Channel {ch + 1}")
        for i in ids:
            n = self.doc.notes[i]
            self.doc.put_note(note_with(n, channel=ch))
        self.doc.commit()
        self.channel = ch
        self.say(f"{len(ids)} note{'s' if len(ids) != 1 else ''} moved to channel {ch + 1}")

    def set_velocity(self, ids, v):
        self.doc.begin("Velocity")
        for i in ids:
            n = self.doc.notes.get(i)
            if n is not None:
                self.doc.put_note(note_with(n, velocity=max(1, min(127, int(v)))))
        self.doc.commit()

    def undo(self, redo=False):
        if self.press is not None:
            return
        e = self.doc.redo() if redo else self.doc.undo()
        if e is None:
            self.say("Nothing to redo" if redo else "Nothing to undo")
            return
        self.selection = {i for i in self.selection if i in self.doc.notes}
        if e.retime is not None:                          # everything moved in time: the view goes with it
            self._retimed(e.retime[0] if redo else e.retime[1])
        else:
            changed = [i for i, (a, b) in e.notes.items() if (b if redo else a) is not None]
            if changed and len(changed) <= 2000:
                self.selection = set(changed)
        self.say(("Redid: " if redo else "Undid: ") + e.label)

    def _retimed(self, move):
        """Every note just moved in time by `move` (old tick -> new): the cursor and the view follow them."""
        self.cursor = max(0, int(round(move(self.cursor))))
        self._update_bounds()
        tlo, thi = move(self.vx.tlo), move(self.vx.thi)
        self.vx.jump(move(self.vx.lo), move(self.vx.hi))
        self.vx.target(tlo, thi)

    # ----- input ----------------------------------------------------------------
    def handle_event(self, e):
        """Events for the area below the bars. True if handled."""
        if self.menu is not None:
            return self._menu_event(e)
        if self.seq is not None and e.type == pygame.KEYDOWN and self._seq_key(e):
            return True
        if e.type == pygame.MOUSEBUTTONDOWN:
            return self._mouse_down(e)
        if e.type == pygame.MOUSEMOTION:
            self._mouse_move(e)
            return True
        if e.type == pygame.MOUSEBUTTONUP:
            self._mouse_up(e)
            return True
        if e.type == pygame.MOUSEWHEEL:
            self._wheel(e)
            return True
        if e.type == pygame.KEYDOWN:
            return self._key(e)
        return False

    def _menu_event(self, e):
        m = self.menu
        if e.type == pygame.MOUSEMOTION:
            m.motion(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
            inside = m.contains(e.pos)
            if m.click(e.pos):
                self.menu = None
                if not inside and e.button == 3:
                    self._mouse_down(e)
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE or m.key(e.key):
                self.menu = None
        elif e.type == pygame.MOUSEWHEEL:
            self.menu = None
        return True

    def _wheel(self, e):
        mod = pygame.key.get_mods()
        mouse = pygame.mouse.get_pos()
        dy = getattr(e, "precise_y", e.y)
        dx = getattr(e, "precise_x", e.x)
        if getattr(e, "flipped", False):
            dy, dx = -dy, -dx
        over_keys = self.gutter.collidepoint(mouse) or (self.src_roll is not None and self.src_gutter.collidepoint(mouse))
        over_vbar = self.vscroll.rect.collidepoint(mouse) or (self.src_roll is not None and self.src_vscroll.rect.collidepoint(mouse))
        roll = self.roll_at(mouse) or (self.src_roll if self.src_roll is not None and self.src_gutter.collidepoint(mouse) else self.roll)
        if over_keys or over_vbar:
            if _ctrl(mod):
                self.zoom_rows(ROW_ZOOM ** dy, mouse[1], roll)
            else:
                self.scroll_rows(dy * ROW_WHEEL)
            return
        if self._lane_wheel(e, mouse, mod, dy):
            return
        if _ctrl(mod):
            x = mouse[0] if self.roll.x <= mouse[0] <= self.roll.right else None
            self.zoom_time(WHEEL_ZOOM ** dy, x)
            self._user_scrolled()
        elif mod & pygame.KMOD_ALT:
            self.zoom_rows(ROW_ZOOM ** dy, mouse[1] if self.roll_at(mouse) else None, self.roll_at(mouse))
        elif mod & pygame.KMOD_SHIFT:
            self.scroll_rows(dy * ROW_WHEEL)
        else:
            # wheel up = back in time (like scrolling a document up); sideways swipes scroll time too
            self.scroll_time(-dy + dx)

    def _mouse_down(self, e):
        pos, b = e.pos, e.button
        mod = pygame.key.get_mods()
        if b in (4, 5):
            return True
        self._update_bounds()
        self.sync_scrollbars()
        # scroll bars, splitter, ruler
        if b == 1:
            for bar, kind in ((self.vscroll, "vscroll"), (self.src_vscroll if self.src_roll is not None else None, "vscroll"),
                              (self.hscroll, "hscroll")):
                if bar is None:
                    continue
                got = bar.press(pos)
                if got == "drag":
                    self.press = {"kind": kind, "bar": bar, "moved": True}
                    return True
                if got == "page":
                    self._page(bar, pos)
                    return True
            if self.splitter.collidepoint(pos):
                self.press = {"kind": "split", "y": pos[1], "h": self.lanes_rect.h, "moved": True}
                return True
            if self.ruler.collidepoint(pos):
                playing = self.app.player.playing
                self.app.player.stop()                 # (scrubbing while it plays: it carries on from where you let go)
                self.press = {"kind": "ruler", "moved": True, "resume": playing}
                self.cursor = max(0, int(round(self.t_of(pos[0]))))
                return True
            if self.corner.collidepoint(pos):
                self.time_ruler = not self.time_ruler
                self.say("Ruler: minutes and seconds" if self.time_ruler else "Ruler: bars")
                return True
        if b == 2 and (self.roll_at(pos) or self.ruler.collidepoint(pos) or self.lanes_rect.collidepoint(pos)
                       or self.gutter.collidepoint(pos)):
            self.press = {"kind": "pan", "pos": pos, "x": (self.vx.lo, self.vx.hi), "y": (self.vy.lo, self.vy.hi),
                          "rows": self.roll_at(pos) is not None or self.gutter.collidepoint(pos), "moved": False}
            self._user_scrolled()
            return True
        if self.impose_bar is not None and self.impose_bar.collidepoint(pos):
            if b == 1:
                self._impose_bar_click(pos)
            return True
        if self.lanes_rect.collidepoint(pos):
            return self._lane_down(e, mod)
        if self.gutter.collidepoint(pos) or (self.src_roll is not None and self.src_gutter.collidepoint(pos)):
            if b == 1:
                rect = self.roll if self.gutter.collidepoint(pos) else self.src_roll
                p = int(math.floor(self.u_of(pos[1], rect)))
                if 0 <= p <= 127:
                    self.app.player.preview(self.channel, p, 90)
                    self.press = {"kind": "key", "pitch": p, "moved": False}
            return True
        if self.src_roll is not None and self.src_roll.collidepoint(pos):
            return self._src_roll_down(e, mod)
        if self.roll.collidepoint(pos):
            if b == 1:
                return self._roll_left_down(pos, mod)
            if b == 3:
                self._context_menu(pos)
                return True
        return False

    def _page(self, bar, pos):
        v = bar.value_at(pos)
        if bar is self.hscroll:
            span = self.vx.target_span
            self.vx.scroll_by(span * 0.9 * (1 if v > self.vx.thi else -1))
            self._user_scrolled()
        else:
            span = self.vy.target_span
            self.vy.scroll_by(span * 0.9 * (1 if v > self.vy.thi else -1))

    def _set_cursor(self, tick):
        tick = max(0, int(round(tick)))
        self.cursor = tick
        if self.app.player.playing:
            self.app.player.stop()
            self.app.player.start(self.doc, tick)
            self.follow = True

    def _roll_left_down(self, pos, mod):
        add = bool(mod & pygame.KMOD_SHIFT) or _ctrl(mod)
        now = time.perf_counter()
        last_t, last_pos = self._last_click
        double = now - last_t < DOUBLE_CLICK_T and abs(pos[0] - last_pos[0]) + abs(pos[1] - last_pos[1]) < px(6)
        self._last_click = (now, pos)
        if self._ghost_click(pos):                     # (impose: a note it would add)
            return True
        if mod & pygame.KMOD_ALT and self._removal_toggle_at(pos):
            return True
        nid = self.note_at(pos)
        if self.seq is not None and nid is not None and self._seq_has(nid):
            self._seq_goto(nid)
            return True
        if nid is not None:
            edge = self.edge_at(pos, nid)
            was_selected = nid in self.selection
            if add and edge is None and not _ctrl(mod):
                # Shift+click: in or out of the selection
                self.press = {"kind": "toggle", "note": nid, "pos": pos, "moved": False}
                return True
            if not was_selected:
                self.selection = (self.selection | {nid}) if add else {nid}
            n = self.doc.notes[nid]
            self.channel = n.channel
            group = [self.doc.notes[i] for i in self.selection if i in self.doc.notes]
            kind = {"r": "resize_r", "l": "resize_l"}.get(edge, "move")
            # Ctrl: dragging copies the selection; a click without moving adds the note or takes it out
            self.press = {"kind": kind, "pos": pos, "note": nid, "moved": False, "orig": {m.id: m for m in group},
                          "anchor": n, "t": self.t_of(pos[0]), "p": int(math.floor(self.u_of(pos[1]))),
                          "copy": _ctrl(mod) and kind == "move", "roll": self.roll, "last_dp": 0,
                          "ctrl_toggle": _ctrl(mod) and was_selected}
            if kind == "move":
                self.app.player.preview(n.channel, n.pitch, n.velocity, 0.25)
            return True
        p = int(math.floor(self.u_of(pos[1])))
        if (self.draw_mode or double) and 0 <= p <= 127:
            tick = self.snap(self.t_of(pos[0]), "floor", free=bool(mod & pygame.KMOD_ALT))
            self.doc.begin("Add note")
            n = self.add_note(max(0, tick), p)
            self.selection = {n.id}
            self.app.player.preview(n.channel, n.pitch, n.velocity, 0.3)
            self.press = {"kind": "draw", "pos": pos, "note": n.id, "moved": False, "start": n.start,
                          "len": n.length, "roll": self.roll}
            return True
        self.press = {"kind": "band", "pos": pos, "moved": False, "add": add, "before": set(self.selection),
                      "start": (self.t_of(pos[0]), self.u_of(pos[1])), "roll": self.roll}
        return True

    def _mouse_move(self, e):
        pos = e.pos
        p = self.press
        if p is None:
            self.hover = self.note_at(pos) if self.roll.collidepoint(pos) else None
            self._hover_compare(pos)
            self._update_cursor_shape(pos)
            return
        if not p["moved"] and "pos" in p:
            if abs(pos[0] - p["pos"][0]) + abs(pos[1] - p["pos"][1]) >= DRAG_START:
                p["moved"] = True
                if p["kind"] in ("move", "resize_r", "resize_l"):
                    self.doc.begin({"move": "Copy notes" if p.get("copy") else "Move notes",
                                    "resize_r": "Note length", "resize_l": "Note start"}[p["kind"]])
                    if p.get("copy"):
                        self._make_copies()
        if p["moved"]:
            self._drag_motion(pos, pygame.key.get_mods())

    def _make_copies(self):
        p = self.press
        doc = self.doc
        copies = {}
        for m in p["orig"].values():
            c = Note(doc.new_id(), m.track, m.channel, m.pitch, m.start, m.end, m.velocity, m.off_velocity,
                     m.off_status, m.hand, m.finger, m.gliss)
            doc.put_note(c)
            copies[c.id] = c
            if m.id == p["anchor"].id:
                p["anchor"] = c
        p["orig"] = copies
        self.selection = set(copies)

    def _drag_motion(self, pos, mod):
        p = self.press
        k = p["kind"]
        free = bool(mod & pygame.KMOD_ALT)
        if k == "pan":
            dx, dy = pos[0] - p["pos"][0], pos[1] - p["pos"][1]
            span = p["x"][1] - p["x"][0]
            d = -dx * span / max(1, self.roll.w)
            self.vx.jump(p["x"][0] + d, p["x"][1] + d)
            if p["rows"]:
                rspan = p["y"][1] - p["y"][0]
                du = dy * rspan / max(1, self.roll.h)
                self.vy.jump(p["y"][0] + du, p["y"][1] + du)
        elif k == "vscroll":
            lo = p["bar"].drag_to(pos)
            self.vy.jump(lo, lo + self.vy.span)
        elif k == "hscroll":
            lo = p["bar"].drag_to(pos)
            self.vx.jump(lo, lo + self.vx.span)
            self._user_scrolled()
        elif k == "split":
            self.lanes_h = p["h"] - (pos[1] - p["y"])
            self.layout(self.area)
        elif k == "ruler":
            self._set_cursor_scrub(self.t_of(min(max(pos[0], self.roll.x), self.roll.right)))
        elif k == "band":
            ta, ua = p["start"]
            x = max(self.roll.x, min(self.roll.right - 1, pos[0]))
            y = max(self.roll.y, min(self.roll.bottom - 1, pos[1]))
            inside = self.notes_in_box(ta, self.t_of(x), ua, self.u_of(y))
            self.selection = (p["before"] | inside) if p["add"] else inside
        elif k == "move":
            self._drag_move(pos, free)
        elif k in ("resize_r", "resize_l"):
            self._drag_resize(pos, free, k == "resize_r")
        elif k == "draw":
            n = self.doc.notes.get(p["note"])
            if n is not None:
                end = self.snap(self.t_of(pos[0]), free=free)
                end = max(n.start + max(1, self.ppq // 32), end if end > n.start else n.start + p["len"])
                if end != n.end:
                    self.doc.put_note(note_with(n, end=end))
        elif k in ("vel", "pedal", "tempo_seg", "tempo_key"):
            self._lane_drag(pos, mod)
        elif k == "toggle":
            pass

    def _set_cursor_scrub(self, tick):
        self.cursor = max(0, int(round(tick)))

    def _drag_move(self, pos, free):
        p = self.press
        a = p["anchor"]
        orig = p["orig"]
        raw = a.start + (self.t_of(pos[0]) - p["t"])
        dt = self.snap(raw, free=free) - a.start
        dp = int(math.floor(self.u_of(pos[1]))) - p["p"]
        lo_start = min(n.start for n in orig.values())
        dt = max(dt, -lo_start)
        dp = max(min(dp, 127 - max(n.pitch for n in orig.values())), -min(n.pitch for n in orig.values()))
        if (dt, dp) == p.get("applied", (0, 0)):
            return
        p["applied"] = (dt, dp)
        for n in orig.values():
            self.doc.put_note(note_with(n, start=n.start + dt, end=n.end + dt, pitch=n.pitch + dp))
        if dp != p["last_dp"]:
            p["last_dp"] = dp
            self.app.player.preview(a.channel, a.pitch + dp, a.velocity, 0.2)

    def _drag_resize(self, pos, free, right):
        p = self.press
        a = p["anchor"]
        minlen = max(1, self.ppq // 32)
        t = self.snap(self.t_of(pos[0]), free=free)
        if right:
            d = t - a.end
            for n in p["orig"].values():
                self.doc.put_note(note_with(n, end=max(n.start + minlen, n.end + d)))
        else:
            d = t - a.start
            for n in p["orig"].values():
                self.doc.put_note(note_with(n, start=max(0, min(n.end - minlen, n.start + d))))

    def _mouse_up(self, e):
        p = self.press
        if p is None or e.button not in (1, 2, 3):
            return
        if e.button == 2 and p["kind"] != "pan":
            return
        self.press = None
        k = p["kind"]
        if k in ("vscroll", "hscroll"):
            p["bar"].drag = None
        elif k == "ruler":
            if p.get("resume") and self.app.player.start(self.doc, self.cursor):
                self.follow = True
        elif k == "band" and not p["moved"]:
            if not p["add"]:
                self.selection.clear()
        elif k == "band":
            n = len(self.selection)
            if n:
                self.say(f"{n} note{'s' if n != 1 else ''} selected" +
                         (" - right-click one for hands, fingers and more" if n > 1 else ""))
        elif k == "toggle" and not p["moved"]:
            self.selection ^= {p["note"]}
        elif k in ("move", "resize_r", "resize_l"):
            if p["moved"]:
                edit = self.doc.commit()
                if edit and k == "move" and p.get("copy"):
                    self.say(f"Copied {len(p['orig'])} notes")
            elif k == "move" and p.get("ctrl_toggle"):
                self.selection.discard(p["note"])
            elif k == "move" and not p.get("copy"):
                self.selection = {p["note"]}
        elif k == "draw":
            self.doc.commit()
            n = self.doc.notes.get(p["note"])
            if n is not None:
                self.note_len = n.length
        elif k in ("vel", "pedal", "tempo_seg", "tempo_key"):
            self._lane_up(p)
        self._update_cursor_shape(e.pos)

    def _update_cursor_shape(self, pos):
        shape = pygame.SYSTEM_CURSOR_ARROW
        if self.press is None:
            if self.splitter.collidepoint(pos):
                shape = pygame.SYSTEM_CURSOR_SIZENS
            elif self.roll.collidepoint(pos):
                nid = self.note_at(pos)
                if nid is not None:
                    edge = self.edge_at(pos, nid)
                    shape = pygame.SYSTEM_CURSOR_SIZEWE if edge else pygame.SYSTEM_CURSOR_SIZEALL
                elif self.draw_mode:
                    shape = pygame.SYSTEM_CURSOR_CROSSHAIR
            elif self.ruler.collidepoint(pos):
                shape = pygame.SYSTEM_CURSOR_HAND
            else:
                shape = self._lane_cursor_shape(pos) or shape
        else:
            shape = {"pan": pygame.SYSTEM_CURSOR_SIZEALL, "split": pygame.SYSTEM_CURSOR_SIZENS,
                     "resize_r": pygame.SYSTEM_CURSOR_SIZEWE, "resize_l": pygame.SYSTEM_CURSOR_SIZEWE,
                     "move": pygame.SYSTEM_CURSOR_SIZEALL}.get(self.press["kind"], shape)
        if shape != self._cursor_shape:
            self._cursor_shape = shape
            try:
                pygame.mouse.set_cursor(shape)
            except pygame.error:
                pass

    def _key(self, e):
        k, mod = e.key, e.mod
        ctrl, shift = _ctrl(mod), bool(mod & pygame.KMOD_SHIFT)
        if ctrl:
            if k == pygame.K_a:
                self.select_all()
            elif k == pygame.K_z:
                self.undo(redo=shift)
            elif k == pygame.K_y:
                self.undo(redo=True)
            elif k == pygame.K_c:
                self.copy_selection()
            elif k == pygame.K_x:
                self.copy_selection(cut=True)
            elif k == pygame.K_v:
                self.paste()
            elif k == pygame.K_d:
                self.duplicate()
            else:
                return False
            return True
        if k == pygame.K_ESCAPE:
            if self.press is not None:
                if self.doc.in_transaction:
                    self.doc.cancel()
                self.press = None
            elif self.selection:
                self.selection.clear()
            elif self.draw_mode:
                self.draw_mode = False
            else:
                self.app.to_menu()                     # (nothing to let go of: back to the main menu)
            return True
        if k in (pygame.K_DELETE, pygame.K_BACKSPACE):
            self.delete_selection()
        elif k == pygame.K_LEFT or k == pygame.K_RIGHT:
            sign = 1 if k == pygame.K_RIGHT else -1
            if self.selection:
                step = self.snap_step() or self.ppq / 4
                self.nudge(dt=int(sign * (self.doc.meter_map().bar_ticks(self.cursor) if shift else step)))
            else:
                mm = self.doc.meter_map()
                step = mm.bar_ticks(self.cursor) if shift else mm.beat_ticks(self.cursor)
                self._set_cursor(mm.snap(self.cursor + sign * step, step))
                self.reveal(self.cursor)
        elif k == pygame.K_UP or k == pygame.K_DOWN:
            sign = 1 if k == pygame.K_UP else -1
            if self.selection:
                self.nudge(dp=sign * (12 if shift else 1))
            else:
                self.scroll_rows(sign * (12 if shift else 1))
        elif k == pygame.K_HOME:
            self._set_cursor(0)
            self.reveal(0)
        elif k == pygame.K_END:
            self._set_cursor(self.doc.end_tick())
            self.reveal(self.cursor)
        elif k == pygame.K_LEFTBRACKET:
            self.select_side(False, add=shift)
        elif k == pygame.K_RIGHTBRACKET:
            self.select_side(True, add=shift)
        elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.zoom_time(1 / 1.25)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.zoom_time(1.25)
        elif k == pygame.K_z:
            self.fit_view(selection=shift)
        elif k == pygame.K_p:
            self.toggle_draw()
        elif k == pygame.K_f:
            self.toggle_fingering()
        elif k == pygame.K_n and self.impose is not None:
            self.goto_edit(-1 if shift else 1)
        elif k == pygame.K_k and self.impose is not None:
            self.toggle_keep_selected()
        elif self._fingering_key(e):
            pass
        else:
            return False
        return True

    def toggle_draw(self):
        self.draw_mode = not self.draw_mode
        self.say("Draw: click to add a note, drag to set its length (P or Esc to stop)" if self.draw_mode
                 else "Select: click to select, drag to move; double-click adds a note")

    # ----- context menu ---------------------------------------------------------
    def _context_menu(self, pos):
        nid = self.note_at(pos)
        bounds = pygame.Rect(0, 0, *self.app.screen.get_size())
        if nid is None:
            tick = self.snap(self.t_of(pos[0]), "floor")
            p = int(math.floor(self.u_of(pos[1])))
            items = [MenuItem("Add note here", "add", hint="dbl-click"),
                     MenuItem("Paste", "paste", hint="Ctrl+V", enabled=bool(self.clipboard)),
                     MenuItem("Move the cursor here", "cursor"),
                     SEPARATOR,
                     MenuItem("Select all", "all", hint="Ctrl+A"),
                     MenuItem("Select left of the cursor", "left", hint="["),
                     MenuItem("Select right of the cursor", "right", hint="]")]
            if self.selection:
                items.append(MenuItem(f"Deselect ({len(self.selection)})", "none", hint="Esc"))

            def pick(v, _):
                if v == "add":
                    self.doc.begin("Add note")
                    n = self.add_note(max(0, tick), p)
                    self.doc.commit()
                    self.selection = {n.id}
                    self.app.player.preview(n.channel, n.pitch, n.velocity, 0.3)
                elif v == "paste":
                    self.paste(tick)
                elif v == "cursor":
                    self._set_cursor(self.t_of(pos[0]))
                elif v == "all":
                    self.select_all()
                elif v == "left":
                    self.select_side(False)
                elif v == "right":
                    self.select_side(True)
                elif v == "none":
                    self.selection.clear()
            self.menu = ContextMenu(pos, None, items, pick, self.fonts, bounds)
            return
        if nid not in self.selection:
            self.selection = {nid}
        sel = sorted(self.selection, key=lambda i: (self.doc.notes[i].start, self.doc.notes[i].pitch))
        items, handlers = self._fingering_menu_items(sel)
        compare_items, compare_handler = self._compare_menu_items(sel)
        if items:
            items.append(SEPARATOR)
        items += compare_items
        chans = [MenuItem(f"Channel {c + 1}", c, checked=all(self.doc.notes[i].channel == c for i in sel),
                          color=th.CHANNEL_COLORS[c]) for c in range(16)]
        items.append(MenuItem("Channel", "channel", children=chans))
        items.append(MenuItem("Copy" if len(sel) == 1 else f"Copy {len(sel)} notes", "copy", hint="Ctrl+C"))
        items.append(MenuItem("Delete note" if len(sel) == 1 else f"Delete {len(sel)} notes", "delete",
                              hint="Del", color=th.REMOVE_COLOR))
        n = self.doc.notes[nid]
        from .render import note_name
        title = (f"{note_name(n.pitch)} · ch {n.channel + 1} · vel {n.velocity}" if len(sel) == 1
                 else f"{len(sel)} notes selected")

        def pick(v, sub):
            if handlers(v, sub):
                return
            if compare_handler(v, sub):
                return
            if v == "channel" and sub is not None:
                self.set_channel(sel, sub)
            elif v == "copy":
                self.copy_selection()
            elif v == "delete":
                self.delete_selection()
        self.menu = ContextMenu(pos, title, items, pick, self.fonts, bounds, sub_title=None)
