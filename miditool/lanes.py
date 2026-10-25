"""
lanes.py - The velocity, pedal and tempo lanes under the roll (same time axis).

    Velocity   a bar per note; drag across bars to set them (only the selected
               notes, if any are selected)
    Pedal      sustain (filled), sostenuto and soft: click a name to choose which
               you edit; drag to draw its curve (Shift: just down / up), right-drag
               to lift it over a stretch. Edits go to the channels the pedal is on
               (Hand-thesia's exports carry it on both hands' channels)
    Tempo      keyframes and the curves between them (constant, linear, cosine
               sweep - tempo.py): drag a keyframe anywhere, a stretch up or down;
               double-click adds a keyframe; right-click chooses the curve. In the
               lane's header, click the BPM to type the tempo at the cursor, the
               curve under it to change the curve there, and Bake… to give the
               whole piece one tempo (tempo.bake).
"""
from __future__ import annotations

import math

import numpy as np
import pygame

from . import tempo as tempolib
from . import theme as th
from .document import PEDAL_CCS, Event, cc_payload, event_with, note_with, tempo_payload
from .smf import META
from .theme import ACCENT, TEXT, TEXT_DIM, mix, px
from .widgets import SEPARATOR, ContextMenu, MenuItem, PromptDialog

LANES = (("velocity", "Velocity", 0.42), ("pedal", "Pedal", 0.32), ("tempo", "Tempo", 0.26))
VEL_STEM = [mix(c, (0, 0, 0), 0.35) for c in th.CHANNEL_COLORS]
PEDAL_NAMES = {64: "Sus", 66: "Sos", 67: "Soft"}
PEDAL_COLORS = {64: (232, 150, 70), 66: (120, 200, 230), 67: (190, 140, 235)}
TEMPO_COLOR = (238, 200, 92)


def _bpm_problem(text):
    """What's wrong with a typed tempo (None: nothing)."""
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return "Type a number, like 96 or 132.5"
    return None if tempolib.MIN_BPM <= v <= tempolib.MAX_BPM else \
        f"Between {tempolib.MIN_BPM:g} and {tempolib.MAX_BPM:g} BPM"


def _mmss(seconds):
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


class LanesMixin:
    def _init_lanes(self):
        self.lane_rects = {}
        self.lanes_rect = pygame.Rect(0, 0, 0, 0)
        self.pedal_cc = 64
        self.pedal_chans = None          # None: wherever the pedal already is (else the current channel)
        self._pedal_surf = None
        self._chips = []
        self.tempo_edit = None           # the BPM being typed in the tempo lane's header: {"text", "fresh"}

    def _reset_lanes(self):
        self.pedal_chans = None
        self.cancel_tempo_edit()

    def _layout_lanes(self, rect, gw, sb):
        self.lanes_rect = rect
        y = rect.y
        hs = [int(rect.h * share) for _, _, share in LANES]
        hs[-1] = rect.h - sum(hs[:-1])
        # the tempo header's rows (the BPM; the curve and Bake…) get the room they need, from the velocity lane's
        short = self._tempo_head_rects(pygame.Rect(0, 0, gw, 0))[1].bottom + px(1) - hs[-1]
        if short > 0 and hs[0] - short >= hs[-1] + short:
            hs[0] -= short
            hs[-1] += short
        self.lane_rects = {}
        for (key, _, _), h in zip(LANES, hs):
            head = pygame.Rect(rect.x, y, gw, h)
            body = pygame.Rect(rect.x + gw, y, rect.w - gw - sb, h)
            self.lane_rects[key] = (head, body)
            y += h

    def lane_at(self, pos):
        for key, (head, body) in self.lane_rects.items():
            if head.collidepoint(pos) or body.collidepoint(pos) or \
                    pygame.Rect(body.right, body.y, self.SB, body.h).collidepoint(pos):
                return key
        return None

    # ----- data ------------------------------------------------------------------
    def pedal_edit_channels(self):
        if self.pedal_chans is not None:
            return sorted(self.pedal_chans)
        chans = sorted({ch for (ch, cc) in self.doc.pedal_series()})
        return chans or [self.channel]

    def _pedal_curve(self, cc, chans):
        """(ticks, values) of controller cc over the channels (the highest of them at each moment); cached."""
        cache = self.__dict__.setdefault("_curve_cache", {})
        key = (cc, tuple(chans))
        got = cache.get(key)
        if got is not None and got[0] == self.doc.events_rev:
            return got[1]
        ser = self.doc.pedal_series()
        parts = [ser[(ch, cc)] for ch in chans if (ch, cc) in ser]
        if not parts:
            curve = None
        elif len(parts) == 1:
            curve = parts[0][0], parts[0][1]
        else:
            ticks = np.unique(np.concatenate([p[0] for p in parts]))
            vals = np.zeros(len(ticks), np.int16)
            for t, v, _ in parts:
                i = np.searchsorted(t, ticks, "right") - 1
                vals = np.maximum(vals, np.where(i >= 0, v[np.maximum(i, 0)], 0))
            curve = ticks, vals
        cache[key] = (self.doc.events_rev, curve)
        return curve

    def _value_at(self, ticks, values, tick):
        i = int(np.searchsorted(ticks, tick, "right")) - 1
        return int(values[i]) if i >= 0 else 0

    # ----- drawing ------------------------------------------------------------------
    def _draw_lanes(self, surf, mouse):
        for key, label, _ in LANES:
            head, body = self.lane_rects[key]
            surf.fill(th.LANE_BG, body)
            surf.fill(th.LANE_HEAD_BG, head)
            surf.fill(th.LANE_HEAD_BG, (body.right, body.y, self.SB, body.h))
            surf.set_clip(body)
            self._draw_lane_grid(surf, body)
            getattr(self, f"_draw_{key}")(surf, body, mouse)
            x = int(self.x_of(self.cursor))
            if body.x <= x < body.right:
                surf.fill(ACCENT if self.app.player.playing else (200, 200, 210), (x, body.y, 1, body.h))
            surf.set_clip(None)
            surf.fill((46, 46, 58), (self.lanes_rect.x, head.y, self.lanes_rect.w, 1))
            img = self._label(label.upper(), "tiny", TEXT_DIM)
            surf.blit(img, (head.x + px(6), head.y + px(5)))
            surf.fill((60, 60, 72), (head.right - 1, head.y, 1, head.h))
        self._draw_lane_heads(surf, mouse)

    def _draw_lane_grid(self, surf, body):
        _, beats, bars = self._grid_ticks()
        for t, _ in bars:
            surf.fill((44, 44, 56), (int(self.x_of(t)), body.y, 1, body.h))

    def _draw_lane_heads(self, surf, mouse):
        self._chips = []
        head, _ = self.lane_rects["pedal"]
        x, y = head.x + px(5), head.y + px(18)
        for cc in PEDAL_CCS:
            img = self._label(PEDAL_NAMES[cc], "tiny", (20, 20, 24) if cc == self.pedal_cc else TEXT_DIM)
            r = pygame.Rect(x, y, head.w - px(10), img.get_height() + px(2))
            if r.bottom > head.bottom - px(2):
                break
            if cc == self.pedal_cc:
                pygame.draw.rect(surf, PEDAL_COLORS[cc], r, border_radius=px(3))
            elif r.collidepoint(mouse):
                pygame.draw.rect(surf, (52, 52, 64), r, border_radius=px(3))
            surf.blit(img, img.get_rect(center=r.center))
            self._chips.append((r, ("cc", cc)))
            y = r.bottom + px(2)
        chans = self.pedal_edit_channels()
        text = "ch " + ",".join(str(c + 1) for c in chans[:3]) + ("…" if len(chans) > 3 else "")
        img = self._label(text, "tiny", TEXT_DIM)
        r = pygame.Rect(head.x + px(5), y + px(1), head.w - px(10), img.get_height() + px(2))
        if r.bottom <= head.bottom - px(1):
            if r.collidepoint(mouse):
                pygame.draw.rect(surf, (52, 52, 64), r, border_radius=px(3))
            surf.blit(img, img.get_rect(center=r.center))
            self._chips.append((r, ("chans", None)))
        self._draw_tempo_head(surf, mouse)
        head, _ = self.lane_rects["velocity"]
        if self.press and self.press["kind"] == "vel" and head.h > px(30):
            img = self._label(str(self.press.get("value", "")), "label", ACCENT)
            surf.blit(img, (head.x + px(6), head.y + px(19)))

    def _draw_velocity(self, surf, body, mouse):
        for f in (0.25, 0.5, 0.75):
            surf.fill((36, 36, 46), (body.x, int(body.bottom - f * (body.h - px(4))), body.w, 1))
        ix = self.doc.index()
        idx = ix.window(self.vx.lo, self.vx.hi)
        if self.hidden_channels and len(idx):
            idx = idx[~np.isin(ix.channel[idx], list(self.hidden_channels))]
        if not len(idx):
            return
        ppt = body.w / self.vx.span
        xs = np.floor((ix.start[idx] - self.vx.lo) * ppt + body.x).astype(np.int64)
        cap = len(idx) < 1200
        if len(idx) > 900:
            # dense: per pixel column only the loudest bar shows anyway (selected ones always drawn)
            sel_mask = np.isin(ix.ids[idx], np.fromiter(self.selection, np.int64, len(self.selection))) \
                if self.selection else np.zeros(len(idx), bool)
            vel = ix.velocity[idx].astype(np.int64) + sel_mask * 1000
            rev = np.argsort(vel, kind="stable")[::-1]
            _, first = np.unique(xs[rev], return_index=True)
            keep = np.sort(rev[first])
            idx, xs = idx[keep], xs[keep]
        hs = np.maximum(1, (ix.velocity[idx].astype(np.float64) / 127.0 * (body.h - px(5)))).astype(np.int64)
        sel = self.selection
        notes = ix.notes
        bw = max(1, px(2))
        cap_h = px(3)
        bottom = body.bottom
        dr = pygame.draw.rect
        later = []
        for i, x, h in zip(idx.tolist(), xs.tolist(), hs.tolist()):
            n = notes[i]
            if n.id in sel:
                later.append((x, h))
                continue
            dr(surf, VEL_STEM[n.channel], (x, bottom - h, bw, h))
            if cap:
                dr(surf, th.CHANNEL_COLORS[n.channel], (x - 1, bottom - h - 1, bw + 2, cap_h))
        stem = mix(ACCENT, (0, 0, 0), 0.25)
        for x, h in later:
            dr(surf, stem, (x, bottom - h, bw, h))
            dr(surf, ACCENT, (x - 1, bottom - h - 1, bw + 2, cap_h))

    def _draw_pedal(self, surf, body, mouse):
        w, h = body.w, body.h - px(3)
        if w <= 0 or h <= 0:
            return
        top0 = body.y + px(3)
        bottom = top0 + h
        # the half-way line (on / off for a switch)
        surf.fill((48, 48, 60), (body.x, top0 + h // 2, body.w, 1))
        chans = self.pedal_edit_channels()
        edges = self.vx.lo + np.arange(w + 1) * (self.vx.span / w)
        order = [c for c in PEDAL_CCS if c != self.pedal_cc] + [self.pedal_cc]
        for cc in order:
            curve = self._pedal_curve(cc, chans)
            if curve is None:
                continue
            v = self._columns(curve[0], curve[1], edges)
            if not v.any():
                continue
            ys = bottom - np.round(v / 127.0 * (h - 1)).astype(np.int64)
            cut = np.flatnonzero(np.diff(ys)) + 1            # runs of one height
            a = np.concatenate(([0], cut)) + body.x
            b = np.concatenate((cut, [w])) + body.x
            yv = ys[np.concatenate(([0], cut))]
            pts = np.column_stack((np.stack((a, b), axis=1).ravel(), np.repeat(yv, 2))).tolist()
            col = PEDAL_COLORS[cc]
            if cc == self.pedal_cc:
                pygame.draw.polygon(surf, mix(th.LANE_BG, col, 0.5), [(body.x, bottom)] + pts + [(body.right, bottom)])
                pygame.draw.lines(surf, col, False, pts, 1)
            else:
                pygame.draw.lines(surf, mix(th.LANE_BG, col, 0.75), False, pts, max(1, px(1.5)))
        p = self.press
        if p and p["kind"] == "pedal" and len(p["points"]) > 0:
            pts = [(int(self.x_of(t)), int(body.bottom - v / 127.0 * (h - 1))) for t, v in p["points"]]
            color = (220, 90, 80) if p["erase"] else PEDAL_COLORS[self.pedal_cc]
            if len(pts) > 1:
                pygame.draw.lines(surf, color, False, pts, max(1, px(2)))
            else:
                pygame.draw.circle(surf, color, pts[0], px(3))

    @staticmethod
    def _columns(ticks, values, edges):
        """Per pixel column: the highest value in force anywhere in it (quick pedalling stays visible)."""
        vals = values.astype(np.int64)
        i0 = np.searchsorted(ticks, edges[:-1], "right") - 1
        v = np.where(i0 >= 0, vals[np.maximum(i0, 0)], 0)
        a = np.searchsorted(ticks, edges[:-1], "right")
        b = np.searchsorted(ticks, edges[1:], "right")
        has = b > a
        if has.any():
            starts = a[has]
            last = int(b[has][-1])
            cm = np.maximum.reduceat(vals[:last], starts)
            v[has] = np.maximum(v[has], cm)
        return v

    def _tempo_range(self):
        """(lowest, highest) BPM the lane shows (held still while a keyframe is dragged)."""
        p = self.press
        if p is not None and p.get("kind") in ("tempo_seg", "tempo_key") and "range" in p:
            return p["range"]
        keys = tempolib.keyframes(self.doc)
        bpms = [k.bpm for k in keys] or [tempolib.DEFAULT_BPM]
        if keys and keys[0].tick > 0:
            bpms.append(tempolib.DEFAULT_BPM)
        lo, hi = min(bpms), max(bpms)
        mid = (lo + hi) / 2
        half = max((hi - lo) / 2 * 1.25, 20.0)
        return max(1.0, mid - half), mid + half

    def _tempo_y(self, bpm, body):
        lo, hi = self._tempo_range()
        return body.bottom - px(4) - (bpm - lo) / (hi - lo) * (body.h - px(10))

    def _bpm_at_y(self, y, body):
        lo, hi = self._tempo_range()
        return lo + (body.bottom - px(4) - y) / max(1, body.h - px(10)) * (hi - lo)

    def _draw_tempo(self, surf, body, mouse):
        tpp = self.vx.span / max(1, body.w)
        pts = tempolib.segment_points(self.doc, max(0.0, self.vx.lo), self.vx.hi, tpp * px(3))
        line = [(int(self.x_of(t)), int(self._tempo_y(b, body))) for t, b in pts]
        if len(line) >= 2:
            pygame.draw.lines(surf, TEMPO_COLOR, False, line, max(1, px(2)))
        keys = tempolib.keyframes(self.doc)
        hot_id = self.press.get("key_event") if self.press else None
        hover = self._key_near(mouse, body) if body.collidepoint(mouse) and self.press is None else None
        last_x = -1e9
        for i in range(max(0, tempolib.governing(keys, self.vx.lo)), len(keys)):
            k = keys[i]
            if k.tick > self.vx.hi:
                break
            x, y = int(self.x_of(k.tick)), int(self._tempo_y(k.bpm, body))
            hot = k.event.id == hot_id or (hover is not None and hover.event.id == k.event.id)
            r = px(5) if hot else px(4)
            pygame.draw.circle(surf, ACCENT if hot else TEMPO_COLOR, (x, y), r)
            pygame.draw.circle(surf, th.LANE_BG, (x, y), max(1, r - px(2)))
            if x - last_x > px(48):
                ramp = k.mode != tempolib.CONSTANT and i + 1 < len(keys)
                text = f"{k.bpm:.4g}" + (f"  {tempolib.MODE_SHORT[k.mode].lower()}" if ramp else "")
                img = self._label(text, "tiny", mix(TEMPO_COLOR, TEXT_DIM, 0.3))
                ty = max(body.y + 1, min(y - img.get_height() - px(2), body.bottom - img.get_height()))
                surf.blit(img, (x + px(6), ty))
                last_x = x

    def _draw_tempo_head(self, surf, mouse):
        """The tempo lane's header: the BPM at the cursor (click to type it); under it the curve there (click to
        change it) and Bake… (one tempo for the whole piece)."""
        head, _ = self.lane_rects["tempo"]
        if head.h <= px(30):
            return
        f = self.fonts["label"]
        r, row = self._tempo_head_rects(head)
        ed = self.tempo_edit
        if ed is not None:
            pygame.draw.rect(surf, (20, 20, 26), r, border_radius=px(3))
            pygame.draw.rect(surf, ACCENT, r, 1, border_radius=px(3))
            img = f.render(ed["text"], True, TEXT)
            at = (r.x + px(4), r.centery - img.get_height() // 2)
            if ed["fresh"] and ed["text"]:
                pygame.draw.rect(surf, mix((20, 20, 26), ACCENT, 0.4),
                                 (at[0] - 1, r.y + px(2), img.get_width() + 2, r.h - px(4)))
            surf.blit(img, at)
            if not ed["fresh"] and (pygame.time.get_ticks() // 500) % 2 == 0:
                cx = at[0] + img.get_width() + 1
                pygame.draw.line(surf, TEXT, (cx, r.y + px(3)), (cx, r.bottom - px(4)))
        else:
            if r.collidepoint(mouse) and self.press is None:
                pygame.draw.rect(surf, (52, 52, 64), r, border_radius=px(3))
            img = self._label(f"{tempolib.bpm_at(self.doc, self.cursor):.1f}", "label", TEMPO_COLOR)
            surf.blit(img, (r.x + px(3), r.centery - img.get_height() // 2))
        self._chips.append((r, ("tempo_bpm", None)))
        if row.bottom > head.bottom - px(1):
            return
        keys = tempolib.keyframes(self.doc)
        i = tempolib.governing(keys, self.cursor)
        mode = keys[i].mode if i >= 0 else tempolib.CONSTANT
        r2 = pygame.Rect(row.x, row.y, row.h + px(2), row.h)                  # the curve, drawn
        r3 = pygame.Rect(r2.right + px(3), row.y, row.right - r2.right - px(3), row.h)
        for rr, kind in ((r2, "tempo_mode"), (r3, "tempo_bake")):
            hot = rr.collidepoint(mouse) and self.press is None
            pygame.draw.rect(surf, (52, 52, 64) if hot else (36, 36, 46), rr, border_radius=px(3))
            self._chips.append((rr, (kind, None)))
        self._curve_glyph(surf, r2.inflate(-px(7), -px(7)), mode,
                          TEMPO_COLOR if mode != tempolib.CONSTANT else TEXT_DIM)
        f = self.fonts["tiny"]
        img = self._label(th.fit_text(f, "Bake…", r3.w - px(2)), "tiny",
                          TEXT if r3.collidepoint(mouse) and self.press is None else TEXT_DIM)
        surf.blit(img, img.get_rect(center=r3.center))

    def _tempo_head_rects(self, head):
        """The tempo header's BPM field, and the row under it for the curve and Bake…"""
        r = pygame.Rect(head.x + px(4), head.y + px(16), head.w - px(9), self.fonts["label"].get_height() + px(2))
        return r, pygame.Rect(r.x, r.bottom + px(2), r.w, self.fonts["tiny"].get_height() + px(2))

    @staticmethod
    def _curve_glyph(surf, r, mode, color):
        """A tempo curve's shape in a few pixels: a step (constant), a straight ramp, a sweep (cosine)."""
        x0, x1, lo, hi = r.x, r.right, r.bottom, r.y
        if mode == tempolib.LINEAR:
            pts = [(x0, lo), (x1, hi)]
        elif mode == tempolib.COSINE:
            pts = [(x0 + (x1 - x0) * j / 10, lo + (hi - lo) * (1 - math.cos(math.pi * j / 10)) / 2) for j in range(11)]
        else:
            xm = (x0 + x1) // 2
            pts = [(x0, lo), (xm, lo), (xm, hi), (x1, hi)]
        pygame.draw.lines(surf, color, False, pts, max(1, px(1.5)))

    # ----- input -------------------------------------------------------------------
    def _lane_wheel(self, e, mouse, mod, dy):
        if not self.lanes_rect.collidepoint(mouse):
            return False
        if mod & (pygame.KMOD_CTRL | pygame.KMOD_META):
            self.zoom_time(0.8 ** dy, mouse[0] if self.roll.x <= mouse[0] <= self.roll.right else None)
        else:
            self.scroll_time(-dy + getattr(e, "precise_x", e.x))
        return True

    def _lane_cursor_shape(self, pos):
        key = self.lane_at(pos)
        if key is None:
            return None
        head, body = self.lane_rects[key]
        if not body.collidepoint(pos):
            return pygame.SYSTEM_CURSOR_HAND if any(r.collidepoint(pos) for r, _ in self._chips) else None
        if key == "tempo":
            k = self._key_near(pos, body)
            return pygame.SYSTEM_CURSOR_SIZEALL if k is not None and k.tick > 0 else pygame.SYSTEM_CURSOR_SIZENS
        return pygame.SYSTEM_CURSOR_CROSSHAIR

    def _lane_down(self, e, mod):
        pos, b = e.pos, e.button
        for r, (kind, val) in self._chips:
            if r.collidepoint(pos) and b == 1:
                if kind == "cc":
                    self.pedal_cc = val
                    self.say(f"Pedal: editing {dict(((64, 'sustain'), (66, 'sostenuto'), (67, 'soft')))[val]}")
                elif kind == "tempo_bpm":
                    self.start_tempo_edit()
                elif kind == "tempo_mode":
                    self._cycle_mode_at_cursor()
                elif kind == "tempo_bake":
                    self.bake_prompt()
                else:
                    self._pedal_channel_menu(pos)
                return True
        key = self.lane_at(pos)
        if key is None:
            return True
        head, body = self.lane_rects[key]
        if not body.collidepoint(pos):
            return True
        if key == "velocity" and b == 1:
            self.doc.begin("Velocity")
            self.press = {"kind": "vel", "last": pos, "moved": True, "count": set()}
            self._paint_velocity(pos, pos)
        elif key == "pedal" and b in (1, 3):
            self.press = {"kind": "pedal", "points": [], "erase": b == 3, "moved": True, "body": body}
            self._pedal_point(pos, mod)
        elif key == "tempo":
            self._tempo_down(pos, b, body)
        return True

    def _lane_drag(self, pos, mod):
        p = self.press
        if p["kind"] == "vel":
            self._paint_velocity(p["last"], pos)
            p["last"] = pos
        elif p["kind"] == "pedal":
            self._pedal_point(pos, mod)
        elif p["kind"] in ("tempo_seg", "tempo_key"):
            self._tempo_drag(pos, mod)

    def _lane_up(self, p):
        k = p["kind"]
        if k == "vel":
            self.doc.commit()
            n = len(p["count"])
            if n:
                self.say(f"Velocity {p.get('value')}: {n} note{'s' if n != 1 else ''}")
        elif k == "pedal":
            self._apply_pedal(p)
        elif k in ("tempo_seg", "tempo_key"):
            self.doc.commit()
            if "bpm" in p:
                bar, beat, _ = self.doc.meter_map().bar_beat(p.get("tick", 0))
                self.say(f"Tempo {p['bpm']:g} BPM" + (f" at bar {bar}.{beat}" if k == "tempo_key" else ""))

    # velocity -----------------------------------------------------------------------
    def _vel_at_y(self, y):
        _, body = self.lane_rects["velocity"]
        return int(max(1, min(127, round((body.bottom - y) / max(1, body.h - px(5)) * 127))))

    def _paint_velocity(self, a, b):
        """Set the notes whose bars lie between screen points a and b to the line's height there."""
        _, body = self.lane_rects["velocity"]
        x0, x1 = sorted((a[0], b[0]))
        tol = px(3)
        t0, t1 = self.t_of(x0 - tol), self.t_of(x1 + tol)
        ix = self.doc.index()
        only = {i for i in self.selection if i in self.doc.notes}
        va, vb = self._vel_at_y(a[1]), self._vel_at_y(b[1])
        for i in ix.window(t0, t1):
            n = ix.notes[i]
            if not (t0 <= n.start <= t1) or not self.visible(n) or (only and n.id not in only):
                continue
            x = self.x_of(n.start)
            f = 0.0 if b[0] == a[0] else (x - a[0]) / (b[0] - a[0])
            v = int(round(va + (vb - va) * min(1.0, max(0.0, f))))
            if n.velocity != v:
                self.doc.put_note(note_with(n, velocity=v))
            self.press["count"].add(n.id)
        self.press["value"] = vb

    # pedal ---------------------------------------------------------------------------
    def _pedal_point(self, pos, mod):
        p = self.press
        body = p["body"]
        t = max(0, int(round(self.t_of(min(max(pos[0], body.x), body.right)))))
        if p["erase"]:
            v = 0
        elif mod & pygame.KMOD_SHIFT:
            v = 127 if pos[1] < body.centery else 0
        else:
            v = int(max(0, min(127, round((body.bottom - pos[1]) / max(1, body.h - px(3)) * 127))))
            if v >= 120:
                v = 127
            elif v <= 6:
                v = 0
        pts = p["points"]
        if pts and abs(self.x_of(pts[-1][0]) - self.x_of(t)) < 1:
            pts[-1] = (pts[-1][0], v)
        else:
            pts.append((t, v))

    def _apply_pedal(self, p):
        pts = sorted(p["points"])
        if not pts:
            return
        cc = self.pedal_cc
        chans = self.pedal_edit_channels()
        t0, t1 = pts[0][0], pts[-1][0]
        if len(pts) == 1 and not p["erase"]:
            self._pedal_click(cc, chans, t0, pts[0][1])
            return
        if p["erase"]:
            pts = [(t0, 0)]
        # keep the curve sparse: only where the value changes
        thin = []
        for t, v in pts:
            if not thin or thin[-1][1] != v:
                thin.append((t, v))
        doc = self.doc
        ser = doc.pedal_series()
        doc.begin("Erase pedal" if p["erase"] else "Draw pedal")
        for ch in chans:
            track = doc.track_for_channel(ch)
            after = 0
            if (ch, cc) in ser:
                ticks, vals, ids = ser[(ch, cc)]
                after = self._value_at(ticks, vals, t1)
                for t, eid in zip(ticks.tolist(), ids):
                    if t0 <= t <= t1:
                        doc.remove_event(eid)
            for t, v in thin:
                doc.put_event(Event(doc.new_id(), track, int(t), 0, cc_payload(ch, cc, v)))
            if thin[-1][1] != after:
                doc.put_event(Event(doc.new_id(), track, int(t1) + 1, 0, cc_payload(ch, cc, after)))
        doc.commit()
        name = {64: "Sustain", 66: "Sostenuto", 67: "Soft"}[cc]
        self.say(f"{name} pedal {'lifted' if p['erase'] else 'drawn'} on channel{'s' if len(chans) > 1 else ''} "
                 + ", ".join(str(c + 1) for c in chans))

    def _pedal_click(self, cc, chans, tick, value):
        """A click (no drag): the pedal goes to this value here and stays until its next change."""
        doc = self.doc
        ser = doc.pedal_series()
        doc.begin("Pedal")
        for ch in chans:
            if (ch, cc) in ser:
                ticks, vals, ids = ser[(ch, cc)]
                for t, eid in zip(ticks.tolist(), ids):
                    if t == tick:
                        doc.remove_event(eid)
            doc.put_event(Event(doc.new_id(), doc.track_for_channel(ch), int(tick), 0, cc_payload(ch, cc, value)))
        doc.commit()
        name = {64: "Sustain", 66: "Sostenuto", 67: "Soft"}[cc]
        self.say(f"{name} pedal {'down' if value >= 64 else 'up'} from here (drag to draw it, right-drag to lift it)")

    def _pedal_channel_menu(self, pos):
        have = sorted({ch for (ch, cc) in self.doc.pedal_series()})
        used = sorted(self.doc.channel_counts())
        items = [MenuItem("Where the pedal already is" + (f" ({', '.join(str(c + 1) for c in have)})" if have else ""),
                          "auto", checked=self.pedal_chans is None)]
        items.append(SEPARATOR)
        for ch in sorted(set(used) | set(have) | {self.channel}):
            items.append(MenuItem(f"Channel {ch + 1}", ch, checked=self.pedal_chans == {ch},
                                  color=th.CHANNEL_COLORS[ch]))
        if len(used) > 1:
            items.append(MenuItem("Every channel with notes", "all", checked=self.pedal_chans == set(used)))

        def pick(v, _):
            if v == "auto":
                self.pedal_chans = None
            elif v == "all":
                self.pedal_chans = set(used)
            else:
                self.pedal_chans = {v}
        self.menu = ContextMenu(pos, "Pedal channels (shown and edited)", items, pick, self.fonts,
                                pygame.Rect(0, 0, *self.app.screen.get_size()))

    # tempo ----------------------------------------------------------------------------
    def _key_near(self, pos, body):
        """The keyframe whose dot is near pos (within a few pixels across), or None."""
        best, bd = None, px(8)
        for k in tempolib.keyframes(self.doc):
            d = abs(self.x_of(k.tick) - pos[0])
            if d <= bd:
                best, bd = k, d
        return best

    def _key_by_event(self, eid):
        return next((k for k in tempolib.keyframes(self.doc) if k.event.id == eid), None)

    def _put_key(self, key, tick=None, bpm=None, mode=None):
        """Change a keyframe (inside a transaction): its tick, its tempo, its curve to the next one."""
        doc = self.doc
        e = key.event
        kw = {}
        if bpm is not None:
            kw["payload"] = tempo_payload(bpm)
        if tick is not None and tick != e.tick:
            kw["tick"] = int(tick)
        if kw:
            doc.put_event(event_with(e, **kw))
        t = e.tick if tick is None else int(tick)
        new = key.mode if mode is None else mode
        m = key.marker
        if new == tempolib.CONSTANT:
            if m is not None:
                doc.remove_event(m.id)
        elif m is None:
            doc.put_event(Event(doc.new_id(), e.track, t, META, tempolib.marker_payload(new)))
        elif m.tick != t or tempolib.parse_marker(m.payload)[0] != new:
            doc.put_event(event_with(m, tick=t, payload=tempolib.marker_payload(new)))

    def _add_key(self, tick, bpm, mode=tempolib.CONSTANT):
        doc = self.doc
        same = [k for k in tempolib.keyframes(doc) if k.tick == tick]
        if same:
            self._put_key(same[0], bpm=bpm, mode=mode)
            return same[0].event.id
        e = Event(doc.new_id(), doc.tempo_track(), int(tick), META, tempo_payload(bpm))
        doc.put_event(e)
        if mode != tempolib.CONSTANT:
            doc.put_event(Event(doc.new_id(), e.track, int(tick), META, tempolib.marker_payload(mode)))
        return e.id

    def _delete_key(self, key):
        self.doc.remove_event(key.event.id)
        if key.marker is not None:
            self.doc.remove_event(key.marker.id)

    def _where(self, tick):
        bar, beat, _ = self.doc.meter_map().bar_beat(tick)
        return f"bar {bar}.{beat}"

    def _tempo_down(self, pos, b, body):
        k = self._key_near(pos, body)
        if b == 3:
            self._tempo_menu(pos, k, body)
            return
        if b != 1:
            return
        import time as _time
        now = _time.perf_counter()
        last_t, last_pos = getattr(self, "_tempo_click", (0.0, (0, 0)))
        self._tempo_click = (now, pos)
        if now - last_t < 0.38 and abs(pos[0] - last_pos[0]) < px(6):
            if k is not None:
                self._tempo_prompt(key=k)
            else:
                self._add_key_at(pos)
            return
        rng = self._tempo_range()
        if k is not None:
            self.press = {"kind": "tempo_key", "key_event": k.event.id, "moved": True, "pos": pos, "range": rng,
                          "started": False, "tick": k.tick}
            return
        # a stretch between keyframes: drag the keyframe it starts from up or down
        keys = tempolib.keyframes(self.doc)
        i = tempolib.governing(keys, self.t_of(pos[0]))
        self.press = {"kind": "tempo_seg", "key_event": keys[i].event.id if i >= 0 else None, "moved": True,
                      "pos": pos, "range": rng, "started": False, "tick": keys[i].tick if i >= 0 else 0}

    def _tempo_drag(self, pos, mod):
        p = self.press
        _, body = self.lane_rects["tempo"]
        free = bool(mod & pygame.KMOD_ALT)
        bpm = min(tempolib.MAX_BPM, max(tempolib.MIN_BPM, self._bpm_at_y(pos[1], body)))
        bpm = round(bpm, 1) if free else round(bpm * 2) / 2
        if not p["started"]:
            self.doc.begin("Move tempo keyframe" if p["kind"] == "tempo_key" else "Tempo")
            p["started"] = True
        key = self._key_by_event(p["key_event"]) if p["key_event"] is not None else None
        if p["kind"] == "tempo_seg":
            if key is None:                      # (no keyframe yet: the stretch is the default 120 - make one)
                p["key_event"] = self._add_key(0, bpm)
            else:
                self._put_key(key, bpm=bpm)
            p["bpm"] = bpm
            return
        if key is None:
            return
        tick = key.tick
        if key.tick > 0 and not (mod & pygame.KMOD_SHIFT):        # (Shift: up and down only)
            keys = [k for k in tempolib.keyframes(self.doc) if k.event.id != key.event.id]
            before = [k.tick for k in keys if k.tick < key.tick]
            after = [k.tick for k in keys if k.tick > key.tick]
            lo = (before[-1] + 1) if before else 1
            hi = (after[0] - 1) if after else 10 ** 9
            tick = min(hi, max(lo, self.snap(self.t_of(pos[0]), free=free)))
        self._put_key(key, tick=tick, bpm=bpm)
        p["bpm"], p["tick"] = bpm, tick

    def _add_key_at(self, pos):
        tick = max(0, self.snap(self.t_of(pos[0])))
        keys = tempolib.keyframes(self.doc)
        i = tempolib.governing(keys, tick)
        mode = keys[i].mode if i >= 0 else tempolib.CONSTANT
        bpm = round(tempolib.bpm_at(self.doc, tick), 1)
        self.doc.begin("Add tempo keyframe")
        self._add_key(tick, bpm, mode)
        self.doc.commit()
        self.say(f"Tempo keyframe at {self._where(tick)}: {bpm:g} BPM - drag it; right-click it to choose the curve")

    def _set_mode(self, key, mode):
        self.doc.begin(f"Tempo curve: {tempolib.MODE_NAMES[mode].lower()}")
        self._put_key(key, mode=mode)
        self.doc.commit()
        keys = tempolib.keyframes(self.doc)
        last = bool(keys) and keys[-1].tick == key.tick
        self.say(f"From {self._where(key.tick)} the tempo is {tempolib.MODE_NAMES[mode].lower()}"
                 + (" to the next keyframe" if not last else " (that matters once there's a keyframe after it)"))

    def _cycle_mode_at_cursor(self):
        keys = tempolib.keyframes(self.doc)
        i = tempolib.governing(keys, self.cursor)
        if i < 0:
            self.say("No tempo keyframe there yet - click the BPM to type one, or double-click the tempo lane")
            return
        k = keys[i]
        self._set_mode(k, tempolib.MODES[(tempolib.MODES.index(k.mode) + 1) % len(tempolib.MODES)])

    def _tempo_prompt(self, key=None, tick=None):
        tick = key.tick if key is not None else tick
        bpm = key.bpm if key is not None else tempolib.bpm_at(self.doc, tick)
        eid = key.event.id if key is not None else None

        def done(text):
            v = float(text.replace(",", "."))
            self.doc.begin("Tempo")
            k = self._key_by_event(eid) if eid is not None else None
            if k is not None:
                self._put_key(k, bpm=v)
            else:
                keys = tempolib.keyframes(self.doc)
                i = tempolib.governing(keys, tick)
                self._add_key(tick, v, keys[i].mode if i >= 0 else tempolib.CONSTANT)
            self.doc.commit()
            self.say(f"Tempo {v:g} BPM at {self._where(tick)}")
        self.app.prompt("Tempo", f"BPM at {self._where(tick)}:", f"{bpm:.5g}", _bpm_problem, done)

    def _tempo_menu(self, pos, k, body):
        keys = tempolib.keyframes(self.doc)
        tick = max(0, self.snap(self.t_of(pos[0])))
        i = next((j for j, x in enumerate(keys) if k is not None and x.event.id == k.event.id),
                 tempolib.governing(keys, self.t_of(pos[0])))
        seg = keys[i] if i >= 0 else None
        items = []
        if seg is not None:
            curves = [MenuItem(tempolib.MODE_NAMES[m], m, checked=seg.mode == m) for m in tempolib.MODES]
            label = "Curve to the next keyframe" if i + 1 < len(keys) else "Curve (after the next keyframe)"
            items.append(MenuItem(label, "curve", children=curves))
        if k is not None:
            items.append(MenuItem(f"Set this keyframe's tempo ({k.bpm:.4g} BPM)…", "set", hint="dbl-click"))
        items.append(MenuItem("Add a keyframe here", "add", hint="dbl-click"))
        if k is not None and (k.tick > 0 or len(keys) > 1):
            items.append(MenuItem("Delete this keyframe", "delete", color=th.REMOVE_COLOR))
        items.append(SEPARATOR)
        items.append(MenuItem("Bake to one tempo…", "bake"))

        def pick(v, sub):
            if v == "curve" and sub is not None:
                self._set_mode(seg, sub)
            elif v == "set":
                self._tempo_prompt(key=k)
            elif v == "bake":
                self.bake_prompt()
            elif v == "add":
                self._add_key_at((int(self.x_of(tick)), pos[1]))
            elif v == "delete":
                self.doc.begin("Delete tempo keyframe")
                self._delete_key(k)
                self.doc.commit()
                self.say(f"Tempo keyframe at {self._where(k.tick)} deleted")
        where = keys[i].tick if (k is not None and i >= 0) else tick
        self.menu = ContextMenu(pos, f"Tempo at {self._where(where)}", items, pick, self.fonts,
                                pygame.Rect(0, 0, *self.app.screen.get_size()))

    # the BPM in the header, typed ---------------------------------------------------
    def start_tempo_edit(self):
        bpm = tempolib.bpm_at(self.doc, self.cursor)
        self.tempo_edit = {"text": f"{bpm:.5g}", "fresh": True}
        pygame.key.start_text_input()
        self.say(f"Type the tempo at the cursor ({self._where(self.cursor)}): Enter sets it, Esc cancels, "
                 "Up / Down nudge it")

    def tempo_edit_event(self, e):
        """Keys while the BPM is being typed (all of them are the field's)."""
        ed = self.tempo_edit
        if e.type == pygame.TEXTINPUT:
            chars = "".join(c for c in e.text if c.isdigit() or c in ".,").replace(",", ".")
            if chars:
                ed["text"] = (chars if ed["fresh"] else ed["text"] + chars)[:7]
                ed["fresh"] = False
            return True
        if e.type != pygame.KEYDOWN:
            return False
        k = e.key
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            self.commit_tempo_edit()
        elif k == pygame.K_ESCAPE:
            self.cancel_tempo_edit()
        elif k == pygame.K_BACKSPACE:
            ed["text"] = "" if ed["fresh"] else ed["text"][:-1]
            ed["fresh"] = False
        elif k in (pygame.K_UP, pygame.K_DOWN):
            try:
                v = float(ed["text"])
            except ValueError:
                v = tempolib.bpm_at(self.doc, self.cursor)
            step = 10 if e.mod & pygame.KMOD_SHIFT else 1
            v = min(tempolib.MAX_BPM, max(tempolib.MIN_BPM, v + (step if k == pygame.K_UP else -step)))
            ed["text"], ed["fresh"] = f"{v:g}", False
        return True

    def cancel_tempo_edit(self):
        if self.tempo_edit is not None:
            self.tempo_edit = None
            pygame.key.stop_text_input()

    def commit_tempo_edit(self):
        ed = self.tempo_edit
        if ed is None:
            return
        self.cancel_tempo_edit()
        try:
            v = float(ed["text"])
        except ValueError:
            if ed["text"]:
                self.say(f"“{ed['text']}” isn't a tempo - nothing changed")
            return
        if not tempolib.MIN_BPM <= v <= tempolib.MAX_BPM:
            self.say(f"A tempo is between {tempolib.MIN_BPM:g} and {tempolib.MAX_BPM:g} BPM - nothing changed")
            return
        self.set_tempo_at_cursor(v)

    def set_tempo_at_cursor(self, bpm):
        """The tempo in force at the cursor becomes `bpm`: its keyframe's - or, inside a ramp, a new keyframe's."""
        doc = self.doc
        keys = tempolib.keyframes(doc)
        c = int(self.cursor)
        i = tempolib.governing(keys, c)
        doc.begin("Tempo")
        if i < 0:
            self._add_key(0, bpm)
            where = "from the start"
        elif keys[i].tick == c or keys[i].mode == tempolib.CONSTANT or i + 1 >= len(keys):
            self._put_key(keys[i], bpm=bpm)
            where = f"from {self._where(keys[i].tick)}"
        else:
            self._add_key(c, bpm, keys[i].mode)
            where = f"at {self._where(c)} (a new keyframe in the {tempolib.MODE_NAMES[keys[i].mode].lower()})"
        doc.commit()
        self.say(f"Tempo {bpm:g} BPM {where}")

    # baking: one tempo for the whole piece -------------------------------------------
    def bake_prompt(self):
        """Ask for the one tempo the piece is to have, and whether the notes keep their place or their timing."""
        doc = self.doc
        if doc.division & 0x8000:
            self.say("This file counts time in SMPTE frames, not beats - it has no tempo to bake")
            return
        if self.impose is not None:
            self.say("Close the comparison first (Close, in its bar) - baking the tempo would put it out of step")
            return
        self.cancel_tempo_edit()
        keys = tempolib.keyframes(doc)
        avg = tempolib.average_bpm(doc)
        bpms = [k.bpm for k in keys] + ([tempolib.DEFAULT_BPM] if not keys or keys[0].tick > 0 else [])
        lo, hi = min(bpms), max(bpms)
        if hi - lo < 0.005:
            now = f"It's {lo:.4g} BPM throughout now."
        else:
            now = (f"Now: {lo:.4g} to {hi:.4g} BPM over {len(keys):,} keyframe{'s' if len(keys) != 1 else ''}, "
                   f"{avg:.4g} on average.")
        msg = (f"Every tempo change goes and the whole piece gets one tempo. {now}\n"
               "Keep in place - the notes stay on their bars and beats, played at the new tempo.\n"
               "Keep the timing - every note still plays exactly when it does now (the notes move to the new "
               "tempo's beats).")

        def done(text, choice):
            self.bake_tempo(float(text.replace(",", ".")), keep_timing=choice == "timing")
        self.app.prompt("Bake the tempo", msg, f"{round(avg, 1):g}", _bpm_problem, done,
                        options=[("Keep in place", "place"), ("Keep the timing", "timing"), ("Cancel", "cancel")],
                        width=640)

    def bake_tempo(self, bpm, keep_timing=False):
        """`bpm` for the whole piece (tempo.bake) as one undo step; notes that move take the cursor and view along."""
        doc = self.doc
        if self.app.player.playing:
            self.app.toggle_play()                    # (stopped: the cursor is where it got to)
        tempos = sum(1 for e in doc.events.values() if tempolib.is_tempo_data(e) and not e.is_ramp)
        ramps = sum(1 for e in doc.events.values() if e.is_ramp)
        was = doc.tempo_map().sec(doc.end_tick())
        doc.begin(f"Bake the tempo ({bpm:g} BPM)")
        moves = tempolib.bake(doc, bpm, keep_timing)
        edit = doc.commit()
        if edit is not None and moves is not None:
            edit.retime = moves
            self._retimed(moves[0])
        gone = f"{tempos:,} tempo event{'s' if tempos != 1 else ''}" + \
            (f" and {ramps} ramp{'s' if ramps != 1 else ''}" if ramps else "")
        if keep_timing:
            self.say(f"Tempo baked: {bpm:g} BPM throughout (was {gone}) - every note plays when it did")
        else:
            now = doc.tempo_map().sec(doc.end_tick())
            self.say(f"Tempo baked: {bpm:g} BPM throughout (was {gone}) - the notes kept their place; "
                     f"it lasts {_mmss(now)} now ({_mmss(was)} before)")
