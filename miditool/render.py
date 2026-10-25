"""
render.py - Drawing the editor: the rows and grid, the notes (coloured by
channel), finger tags, the sideways keyboard, the ruler, the cursor and the
scroll bars. Positions are worked out with numpy for every visible note at
once; what's drawn per note is a fill and an outline.
"""
from __future__ import annotations

import math

import numpy as np
import pygame

from . import theme as th
from .theme import (ACCENT, BEAT_LINE, BG, PLAYHEAD, ROW_BLACK, ROW_C_LINE, ROW_LINE, ROW_WHITE, SUB_LINE,
                    TEXT, TEXT_DIM, mix, px)

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLACK = {1, 3, 6, 8, 10}
_IS_BLACK = np.array([(p % 12) in BLACK for p in range(128)])
DETAIL_MAX = 2500              # above this many visible notes they're drawn flat (no outlines)


def note_name(p):
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def fmt_time(seconds, frac=True):
    neg = seconds < 0
    seconds = abs(seconds)
    m, s = divmod(seconds, 60)
    text = f"{int(m)}:{s:05.2f}" if frac else f"{int(m)}:{int(s):02d}"
    return ("-" if neg else "") + text


def _channel_tables():
    fills, borders, sel_fills = [], [], []
    for ch in range(16):
        w = th.channel_color(ch)
        b = th.channel_color(ch, black=True)
        fills.append((w, b))
        borders.append((mix(w, (0, 0, 0), 0.5), mix(b, (0, 0, 0), 0.5)))
        sel_fills.append((mix(w, (255, 255, 255), 0.4), mix(b, (255, 255, 255), 0.4)))
    return fills, borders, sel_fills


FILLS, BORDERS, SEL_FILLS = _channel_tables()


class RenderMixin:
    _label_cache = None

    def _label(self, text, font, color):
        if self._label_cache is None or len(self._label_cache) > 4000:
            self._label_cache = {}
        key = (text, font, color, th.S)
        img = self._label_cache.get(key)
        if img is None:
            img = self._label_cache[key] = self.fonts[font].render(text, True, color)
        return img

    # ----- the whole editor area ------------------------------------------------
    def sync_scrollbars(self):
        self.hscroll.set(self.vx.bound_lo, self.vx.bound_hi, self.vx.lo, self.vx.hi)
        self.vscroll.set(0, 128, self.vy.lo, self.vy.hi)
        self.src_vscroll.set(0, 128, self.vy.lo, self.vy.hi)

    def render(self, surf):
        self.sync_scrollbars()
        mouse = pygame.mouse.get_pos()
        rolls = [self.roll] + ([self.src_roll] if self.src_roll is not None else [])
        for r in rolls:
            self._draw_rows(surf, r)
            self._draw_grid(surf, r)
        if self.impose is not None:
            self._draw_links(surf)
            self._draw_source_notes(surf)
        self._draw_notes(surf)
        if self.impose is not None:
            self._draw_ghosts(surf)
        self._draw_band(surf)
        for r in rolls:
            self._draw_cursor_line(surf, r)
        self._draw_gutter(surf, self.roll, self.gutter)
        if self.src_roll is not None:
            self._draw_gutter(surf, self.src_roll, self.src_gutter)
            self._draw_impose_bar(surf, mouse)
        self._draw_ruler(surf)
        self._draw_splitter(surf, mouse)
        self._draw_lanes(surf, mouse)
        self.vscroll.draw(surf, mouse)
        if self.src_roll is not None:
            self.src_vscroll.draw(surf, mouse)
        self.hscroll.draw(surf, mouse)
        pygame.draw.rect(surf, th.LANE_HEAD_BG, self.hscroll_corner)
        pygame.draw.rect(surf, th.LANE_HEAD_BG, (self.hscroll.rect.right, self.hscroll.rect.y,
                                                 self.area.right - self.hscroll.rect.right, self.SB))
        pygame.draw.rect(surf, th.LANE_HEAD_BG, (self.vscroll.rect.x, self.area.y, self.SB, self.ruler.h))
        if not self.doc.notes and self.impose is None:
            self._draw_empty_hint(surf)

    def render_overlays(self, surf):
        if self.menu is not None:
            self.menu.draw(surf, pygame.mouse.get_pos())

    def _draw_empty_hint(self, surf):
        r = self.roll
        lines = ["Open MIDI…  (Ctrl+O) or drop a .mid file here",
                 "or double-click anywhere to start adding notes (P switches on Draw)"]
        y = r.centery - px(22)
        for i, t in enumerate(lines):
            img = self._label(t, "normal" if i == 0 else "small", TEXT_DIM)
            surf.blit(img, img.get_rect(center=(r.centerx, y + i * px(26))))

    # ----- rows and grid --------------------------------------------------------
    def _visible_pitches(self):
        return max(0, int(math.floor(self.vy.lo))), min(127, int(math.ceil(self.vy.hi)) - 1)

    def _draw_rows(self, surf, rect):
        """The rows (lighter for white keys) - straight fills: on a big window cheaper than copying an image."""
        rh = rect.h / self.vy.span
        lo, hi = self._visible_pitches()
        dr = pygame.draw.rect
        top, x, w = rect.y + self.vy.hi * rh, rect.x, rect.w
        surf.set_clip(rect)
        for p in range(lo, hi + 1):
            y0 = int(top - (p + 1) * rh)
            y1 = int(top - p * rh)
            dr(surf, ROW_BLACK if p % 12 in BLACK else ROW_WHITE, (x, y0, w, y1 - y0))
            if p % 12 == 0:
                dr(surf, ROW_C_LINE, (x, y1, w, 1))
            elif rh >= 10 and p % 12 == 5:
                dr(surf, ROW_LINE, (x, y1, w, 1))
        surf.set_clip(None)

    def _grid_ticks(self):
        """(sub lines, beat lines, bar lines) in view, thinned to stay readable; cached per view."""
        key = (self.vx.lo, self.vx.hi, self.roll.w, self.doc.timing_rev, self.snap_i)
        got = getattr(self, "_grid_cache", None)
        if got is not None and got[0] == key:
            return got[1]
        mm = self.doc.meter_map()
        t0, t1 = self.vx.lo, self.vx.hi
        ppt = self.roll.w / self.vx.span
        beat = mm.beat_ticks(t0)
        bars = mm.bars_between(t0, t1)
        bar_px = mm.bar_ticks(t0) * ppt
        k = 1
        while bar_px * k < px(22):
            k *= 2
        bars = [(t, b) for t, b in bars if (b - 1) % k == 0]
        beats = mm.lines_between(t0, t1, beat) if beat * ppt >= px(7) else []
        step = self.snap_step(t0)
        subs = mm.lines_between(t0, t1, step) if step and step < beat and step * ppt >= px(9) else []
        res = (subs, beats, bars)
        self._grid_cache = (key, res)
        return res

    def _draw_grid(self, surf, rect):
        subs, beats, bars = self._grid_ticks()
        x0, k = self.roll.x - self.vx.lo * self.roll.w / self.vx.span, self.roll.w / self.vx.span
        y, h = rect.y, rect.h
        lo, hi = rect.x, rect.right
        dr = pygame.draw.rect
        for color, ticks in ((SUB_LINE, subs), (BEAT_LINE, beats), (th.BAR_LINE_ROLL, [t for t, _ in bars])):
            for t in ticks:
                x = int(x0 + t * k)
                if lo <= x < hi:
                    dr(surf, color, (x, y, 1, h))

    # ----- notes ------------------------------------------------------------------
    def _visible_rows(self, rect, starts, ends, pitches, max_len):
        """Indices and screen boxes (x, w, y, h lists) of the notes in view: a NoteIndex-like set of arrays."""
        t0, t1 = self.vx.lo, self.vx.hi
        lo = int(np.searchsorted(starts, t0 - max_len, "left"))
        hi = int(np.searchsorted(starts, t1, "left"))
        if hi <= lo:
            return None
        s, e, p = starts[lo:hi], ends[lo:hi], pitches[lo:hi]
        p_lo, p_hi = self._visible_pitches()
        m = ((e > t0) | (s >= t0)) & (p >= p_lo) & (p <= p_hi)
        idx = np.flatnonzero(m)
        if not len(idx):
            return None
        ppt = rect.w / self.vx.span
        rh = rect.h / self.vy.span
        xs = (s[idx] - t0) * ppt + rect.x
        xe = (e[idx] - t0) * ppt + rect.x
        ys = (self.vy.hi - (p[idx].astype(np.float64) + 1)) * rh + rect.y
        x0 = np.floor(xs).astype(np.int64)
        w = np.maximum(np.floor(xe).astype(np.int64) - x0 - (1 if ppt * 4 > 1 else 0), 2)
        y0 = np.floor(ys).astype(np.int64) + (1 if rh >= 4 else 0)
        h = max(1, int(rh) - (1 if rh >= 4 else 0))
        return idx + lo, x0.tolist(), w.tolist(), y0.tolist(), h

    def _draw_notes(self, surf):
        r = self.roll
        ix = self.doc.index()
        got = self._visible_rows(r, ix.start, ix.end, ix.pitch, ix.max_len)
        self._drawn_count = 0
        if got is None:
            return
        idx, xs, ws, ys, h = got
        if self.hidden_channels:
            keep = ~np.isin(ix.channel[idx], list(self.hidden_channels))
            k = np.flatnonzero(keep)
            idx = idx[k]
            xs, ws, ys = [xs[i] for i in k], [ws[i] for i in k], [ys[i] for i in k]
        notes = ix.notes
        n = len(idx)
        self._drawn_count = n
        sel, hov = self.selection, self.hover
        detail = n <= DETAIL_MAX and h >= 4
        rad = min(px(3), h // 3) if detail and n < 600 else 0
        if not detail:
            self._draw_notes_flat(surf, r, ix, idx, xs, ws, ys, h)
            return
        surf.set_clip(r)
        drect = pygame.draw.rect
        status = self._note_status if self.impose is not None else None
        idx_l = idx.tolist()
        inner = h > 2
        for i, x, w, y in zip(idx_l, xs, ws, ys):
            nt = notes[i]
            bk = 1 if nt.pitch % 12 in BLACK else 0
            ch = nt.channel
            selected = nt.id in sel
            color = SEL_FILLS[ch][bk] if selected else FILLS[ch][bk]
            border = BORDERS[ch][bk]
            mark = None
            if status is not None:
                color, border, mark = status(nt, color, border, selected)
            rect = (x, y, w, h)
            if rad:
                drect(surf, color, rect, border_radius=rad)
                drect(surf, ACCENT if selected else border, rect, 2 if selected else 1, border_radius=rad)
            else:
                drect(surf, ACCENT if selected else border, rect)
                if inner and w > 2:
                    b = 2 if selected and w > 4 and h > 4 else 1
                    drect(surf, color, (x + b, y + b, w - 2 * b, h - 2 * b))
            if nt.id == hov and not selected:
                drect(surf, (255, 255, 255), rect, 1, border_radius=rad)
            if mark is not None:
                self._draw_mark(surf, mark, rect)
        if self.show_fingering and h >= px(9) and (n < 40 or np.median(ws) >= px(5)):
            self._draw_tags(surf, idx_l, xs, ws, ys, h)
        if self.seq is not None:
            self._draw_seq_marks(surf, idx_l, xs, ws, ys, h)
        surf.set_clip(None)

    def _draw_notes_flat(self, surf, r, ix, idx, xs, ws, ys, h):
        """Zoomed far out: plain boxes, and the ones of a colour that touch in a row drawn as one."""
        x0 = np.asarray(xs, np.int64)
        x1 = x0 + np.asarray(ws, np.int64)
        y = np.asarray(ys, np.int64)
        cid = ix.channel[idx].astype(np.int64) * 2 + _IS_BLACK[ix.pitch[idx]]
        if self.selection:
            sel = np.isin(ix.ids[idx], np.fromiter(self.selection, np.int64, len(self.selection)))
            cid = np.where(sel, 32, cid)
        order = np.lexsort((x0, cid, y))
        x0, x1, y, cid = x0[order], x1[order], y[order], cid[order]
        grp = y * 64 + cid
        big = int(x1.max()) + 2 if len(x1) else 1
        rank = np.concatenate(([0], np.cumsum(grp[1:] != grp[:-1])))
        reach = np.maximum.accumulate(x1 + rank * big) - rank * big       # furthest end so far in the group
        new = np.ones(len(x0), bool)
        new[1:] = (grp[1:] != grp[:-1]) | (x0[1:] > reach[:-1] + 1)
        starts = np.flatnonzero(new)
        ends = np.concatenate((starts[1:], [len(x0)])) - 1
        colors = [FILLS[c // 2][c % 2] for c in range(32)] + [ACCENT]
        surf.set_clip(r)
        dr = pygame.draw.rect
        for a, b, yy, c in zip(x0[starts].tolist(), reach[ends].tolist(), y[starts].tolist(), cid[starts].tolist()):
            dr(surf, colors[c], (a, yy, b - a, h))
        surf.set_clip(None)

    def _draw_mark(self, surf, mark, rect):
        x, y, w, h = rect
        if mark == "remove":                         # struck through
            pygame.draw.line(surf, (255, 255, 255), (x + 1, y + h // 2), (x + w - 2, y + h // 2), max(1, px(2)))
        elif mark == "kept":
            pygame.draw.rect(surf, th.REMOVE_COLOR, (x - 1, y - 1, w + 2, h + 2), 1)
        elif mark == "displaced":
            pygame.draw.rect(surf, th.DISPLACED_COLOR, (x - 1, y - 1, w + 2, h + 2), 1)
        elif mark == "focus":
            pygame.draw.rect(surf, (255, 255, 255), (x - 3, y - 3, w + 6, h + 6), 2, border_radius=px(4))

    # ----- finger tags --------------------------------------------------------------
    def _tag_for(self, n):
        """(text, hand, solid) for a note's finger tag, or None."""
        sg = self.sugg.get(n.id)
        hand = n.hand or (sg[0] if sg else None)
        if n.gliss:
            return "g", hand, True
        if n.finger:
            return str(n.finger), hand, True
        if sg:
            if sg[2]:
                return "g", hand, False
            if sg[1]:
                return str(sg[1]), hand, False
            return "?", hand, False
        if n.hand:
            return "?", n.hand, True
        return None

    def _tag_image(self, text, hand, solid, h):
        """A finger tag (rounded box in the hand's colour, the number on it), drawn once and reused."""
        cache = self.__dict__.setdefault("_tag_cache", {})
        key = (text, hand, solid, h, th.S)
        img = cache.get(key)
        if img is None:
            font = "finger" if h >= px(13) else "tiny"
            base = th.HAND_COLORS.get(hand, ((150, 150, 160), (120, 120, 130)))[0]
            if solid:
                t = self.fonts[font].render(text, True, (18, 18, 22))
                bg = base
            else:
                t = self.fonts[font].render(text, True, mix(base, (255, 255, 255), 0.25))
                bg = mix(base, (0, 0, 0), 0.62)
            box = pygame.Rect(0, 0, t.get_width() + px(6), min(h, t.get_height() + px(1)))
            img = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(img, bg, box, border_radius=px(3))
            if not solid:
                pygame.draw.rect(img, mix(base, (0, 0, 0), 0.2), box, 1, border_radius=px(3))
            img.blit(t, t.get_rect(center=box.center))
            img = cache[key] = img.convert_alpha()
        return img

    def _draw_tags(self, surf, idx_l, xs, ws, ys, h):
        notes = self.doc.index().notes
        tag_for, image = self._tag_for, self._tag_image
        blits = []
        free_from = {}                    # per row: where the last tag ended (zoomed out, tags would pile up)
        for i, x, y in zip(idx_l, xs, ys):
            if x < free_from.get(y, -1):
                continue
            tag = tag_for(notes[i])
            if tag is not None:
                img = image(tag[0], tag[1], tag[2], h)
                blits.append((img, (x, y + (h - img.get_height()) // 2)))
                free_from[y] = x + img.get_width() + 1
        surf.blits(blits, doreturn=False)

    # ----- overlays -------------------------------------------------------------------
    def _draw_band(self, surf):
        p = self.press
        if not (p and p["kind"] == "band" and p["moved"]):
            return
        ta, ua = p["start"]
        mx, my = pygame.mouse.get_pos()
        x0, y0 = self.x_of(ta), self.y_of(ua)
        band = pygame.Rect(int(min(x0, mx)), int(min(y0, my)), int(abs(mx - x0)), int(abs(my - y0))).clip(self.roll)
        if band.w > 0 and band.h > 0:
            fill = pygame.Surface(band.size, pygame.SRCALPHA)
            fill.fill((*ACCENT, 38))
            surf.blit(fill, band.topleft)
            pygame.draw.rect(surf, ACCENT, band, 1)

    def _draw_cursor_line(self, surf, rect):
        x = int(self.x_of(self.cursor))
        if rect.x <= x < rect.right:
            color = ACCENT if self.app.player.playing else PLAYHEAD
            surf.fill(color, (x, rect.y, 1, rect.h))

    # ----- keyboard -----------------------------------------------------------------
    def _draw_gutter(self, surf, rect, g):
        surf.fill(th.GUTTER_BG, g)
        surf.set_clip(g)
        sounding = {p for _, p in self.app.player.sounding}
        held = self.press.get("pitch") if self.press and self.press["kind"] == "key" else None
        lo, hi = self._visible_pitches()
        rh = self.row_h(rect)
        bw = int(g.w * 0.6)
        for p in range(lo, hi + 1):
            y0 = int(self.y_of(p + 1, rect))
            y1 = int(self.y_of(p, rect))
            black = p % 12 in BLACK
            color = (40, 40, 46) if black else (226, 226, 222)
            if p in sounding or p == held:
                color = th.channel_color(self.channel, black)
            if black:
                surf.fill((226, 226, 222), (g.x, y0, g.w - 1, y1 - y0))
                surf.fill(color, (g.x, y0, bw, max(1, y1 - y0)))
            else:
                surf.fill(color, (g.x, y0, g.w - 1, max(1, y1 - y0)))
            if not black and (p % 12 in (0, 5)):
                surf.fill((150, 150, 150), (g.x, y1, g.w - 1, 1))
            elif not black and rh >= 12:
                surf.fill((196, 196, 192), (g.x + bw, y1, g.w - 1 - bw, 1))
            if p % 12 == 0 and rh >= 7:
                img = self._label(note_name(p), "label" if rh >= 11 else "tiny", (60, 60, 70))
                surf.blit(img, img.get_rect(midright=(g.right - px(5), (y0 + y1) // 2)))
        surf.set_clip(None)
        surf.fill((60, 60, 72), (g.right - 1, g.y, 1, g.h))

    # ----- ruler ------------------------------------------------------------------------
    def _draw_ruler(self, surf):
        r = self.ruler
        surf.fill(th.RULER_BG, r)
        surf.fill(th.RULER_BG, self.corner)
        lab = self._label("TIME" if self.time_ruler else "BARS", "tiny", TEXT_DIM)
        surf.blit(lab, lab.get_rect(center=self.corner.center))
        surf.set_clip(r)
        t0, t1 = self.vx.lo, self.vx.hi
        if self.time_ruler:
            tm = self.doc.tempo_map()
            s0, s1 = tm.sec(t0), tm.sec(t1)
            per_px = (s1 - s0) / max(1, r.w)
            step = next((v for v in (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
                         if v / max(per_px, 1e-9) >= px(64)), 600)
            k = math.floor(max(0.0, s0) / step)
            while k * step <= s1:
                x = int(self.x_of(tm.tick(k * step)))
                surf.fill(TEXT_DIM, (x, r.bottom - px(7), 1, px(7)))
                t = fmt_time(k * step, frac=step < 1)
                surf.blit(self._label(t, "label", TEXT_DIM), (x + px(3), r.y + px(3)))
                k += 1
        else:
            mm = self.doc.meter_map()
            ppt = r.w / self.vx.span
            bar_px = mm.bar_ticks(t0) * ppt
            step = 1
            while bar_px * step < px(36):
                step *= 2
            for t, b in mm.bars_between(t0 - mm.bar_ticks(t0), t1):
                if (b - 1) % step:
                    continue
                x = int(self.x_of(t))
                surf.fill(TEXT_DIM, (x, r.bottom - px(8), 1, px(8)))
                surf.blit(self._label(str(b), "label", TEXT_DIM), (x + px(3), r.y + px(3)))
            beat = mm.beat_ticks(t0)
            if beat * ppt >= px(10):
                for t in mm.lines_between(t0, t1, beat):
                    surf.fill((80, 80, 96), (int(self.x_of(t)), r.bottom - px(4), 1, px(4)))
        x = int(self.x_of(self.cursor))
        if r.x - px(8) <= x <= r.right + px(8):
            color = ACCENT if self.app.player.playing else PLAYHEAD
            pygame.draw.polygon(surf, color, [(x - px(6), r.y + px(5)), (x + px(6), r.y + px(5)), (x, r.bottom - 1)])
        surf.set_clip(None)
        surf.fill((50, 50, 62), (self.area.x, r.bottom - 1, self.area.w, 1))

    def _draw_splitter(self, surf, mouse):
        s = self.splitter
        hot = s.collidepoint(mouse) or (self.press is not None and self.press["kind"] == "split")
        surf.fill((44, 44, 56) if hot else (34, 34, 44), s)
        cx, cy = s.centerx, s.centery
        for dx in (-px(10), 0, px(10)):
            pygame.draw.circle(surf, (110, 110, 130) if hot else (80, 80, 96), (cx + dx, cy), max(1, px(1.6)))
