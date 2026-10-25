"""
profiles_ui.py - The Fingering window (the Fingering… button): profiles and their figure choices.

    Left     the profiles; click one to use it. New (Default's settings), Duplicate,
             Rename (or double-click), Delete (click twice).
    Right    the selected profile's choices, by figure; changes are saved as you make
             them and the suggestions on the roll follow. "Default" is the active pianist's own:
             changing anything there makes a new profile with the change.
    Bottom   show fingering on the notes (F); apply the suggestions to the notes that
             have no fingering of their own.
"""
from __future__ import annotations

import math
import time

import pygame

from . import profiles as P
from . import theme as th
from .theme import ACCENT, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, fit_text, mix, px
from .widgets import Button, draw_tooltip, wrap_text

SCROLL_EASE = 22.0


class ProfileWindow:
    def __init__(self, app):
        self.app = app
        self.fonts = app.fonts
        self.store = app.profiles
        self.scroll = 0.0
        self.scroll_t = 0.0
        self.press = None                 # a slider being dragged: {"key", "track"}
        self.rename = None                # {"text", "fresh"} while the selected profile is renamed
        self.delete_armed = 0.0           # when Delete was clicked once (a second click deletes)
        self.changed = False              # a profile was chosen or changed while the window was open
        self.notice = ""
        self.notice_t = 0.0
        self.tip = None
        self._last_click = (0.0, None)
        self.layout()

    @property
    def profile(self):
        return self.store.active

    def say(self, text):
        self.notice, self.notice_t = text, time.perf_counter()

    # ----- geometry -----------------------------------------------------------------
    def layout(self):
        w, h = self.app.screen.get_size()
        self.box = pygame.Rect(0, 0, min(px(1060), w - px(30)), min(px(820), h - px(30)))
        self.box.center = (w // 2, h // 2)
        b = self.box
        self.done = Button("Done", "done", font="normal")
        self.done.rect = pygame.Rect(b.right - px(20) - px(96), b.y + px(14), px(96), px(34))
        top = b.y + px(62)
        foot = px(56)
        self.list_rect = pygame.Rect(b.x + px(18), top, px(250), b.bottom - foot - top - px(92))
        bw = (self.list_rect.w - px(8)) // 2
        self.list_buttons = []
        for k, (label, action) in enumerate((("New", "new"), ("Duplicate", "dup"), ("Rename", "rename"),
                                             ("Delete", "delete"))):
            btn = Button(label, action, font="small")
            btn.rect = pygame.Rect(self.list_rect.x + (k % 2) * (bw + px(8)),
                                   self.list_rect.bottom + px(10) + (k // 2) * px(40), bw, px(32))
            self.list_buttons.append(btn)
        self.content = pygame.Rect(self.list_rect.right + px(22), top, b.right - px(18) - self.list_rect.right - px(22),
                                   b.bottom - foot - top - px(6))
        self.show_btn = Button("Show fingering on the notes", "show", font="small")
        self.apply_btn = Button("Apply suggestions", "apply", font="small")
        y = b.bottom - foot + px(12)
        self.show_btn.rect = pygame.Rect(b.x + px(18), y, self.show_btn.width(self.fonts) + px(8), px(32))
        self.apply_btn.rect = pygame.Rect(self.show_btn.rect.right + px(10), y, self.apply_btn.width(self.fonts) + px(48), px(32))
        self._layout_rows()

    def _layout_rows(self):
        """Rows of the settings, in content coordinates: group headings and settings with their controls."""
        f_label, f_small = self.fonts["normal"], self.fonts["small"]
        cw = self.content.w - px(14)                        # (room for the scroll bar)
        label_w = int(cw * 0.42)
        ctrl_x = label_w + px(16)
        ctrl_w = cw - ctrl_x
        rows, y, group = [], 0, None
        for s in P.SETTINGS:
            if s["group"] != group:
                group = s["group"]
                rows.append({"kind": "group", "text": group, "y": y, "h": px(34)})
                y += px(34)
            desc = wrap_text(f_small, s.get("desc", ""), label_w) if s.get("desc") else []
            left_h = f_label.get_linesize() + len(desc) * f_small.get_linesize()
            chips = []
            if s["kind"] in ("options", "firm"):
                opts = s["options"] if s["kind"] == "options" else P.FIRMNESS
                cx, cy, chip_h = 0, 0, px(30)
                for value, label, tip in opts:
                    cwid = f_small.size(label)[0] + px(22)
                    if cx and cx + cwid > ctrl_w:
                        cx, cy = 0, cy + chip_h + px(6)
                    chips.append((value, label, tip, pygame.Rect(ctrl_x + cx, cy, cwid, chip_h)))
                    cx += cwid + px(6)
                right_h = cy + chip_h
            else:
                right_h = px(46)
            h = max(left_h, right_h) + px(14)
            rows.append({"kind": "setting", "s": s, "y": y, "h": h, "desc": desc, "chips": chips,
                         "ctrl": pygame.Rect(ctrl_x, 0, ctrl_w, right_h)})
            y += h
        self.rows = rows
        self.total_h = y + px(10)
        self._clamp_scroll()

    def _clamp_scroll(self):
        hi = max(0, self.total_h - self.content.h)
        self.scroll_t = min(max(self.scroll_t, 0), hi)
        self.scroll = min(max(self.scroll, 0), hi)

    def update(self, dt):
        if abs(self.scroll - self.scroll_t) < 0.3:
            self.scroll = self.scroll_t
            return False
        self.scroll += (self.scroll_t - self.scroll) * (1 - math.exp(-SCROLL_EASE * min(dt, 0.1)))
        return True

    def _row_rect(self, row):
        return pygame.Rect(self.content.x, self.content.y + row["y"] - int(self.scroll), self.content.w - px(14), row["h"])

    def _list_rows(self):
        out = []
        y = self.list_rect.y + px(6)
        for p in self.store.profiles:
            out.append((p, pygame.Rect(self.list_rect.x + px(6), y, self.list_rect.w - px(12), px(32))))
            y += px(34)
        return out

    # ----- changing things ------------------------------------------------------------
    def _editable(self):
        """The profile edits go to: the selected one, or - for Default - a new copy of it."""
        p = self.profile
        if not p.builtin:
            return p
        p = self.store.create("My fingering", dict(p.settings))
        self.say(f"Default is the active pianist's own and doesn't change - your change is in a new profile, "
                 f"“{p.name}” (Rename gives it a name)")
        self._layout_rows()
        return p

    def set_value(self, key, value, save=True):
        p = self._editable()
        if self.store.set_value(p, key, value, save=save):
            self.changed = True

    def _select(self, p):
        self.cancel_rename()
        if p is not self.profile:
            self.store.select(p.id)
            self.changed = True
            self.say(f"Fingering with “{p.name}”")

    def _list_action(self, action):
        st = self.store
        if action == "new":
            self.cancel_rename()
            p = st.create("New profile")
            self.changed = True
            self.start_rename()
            self.say("A new profile with Default's settings - type its name, then Enter")
        elif action == "dup":
            self.cancel_rename()
            src = self.profile
            p = st.create(f"{src.name.split(' (')[0]} copy", dict(src.settings))
            self.changed = True
            self.start_rename()
            self.say(f"A copy of “{src.name}” - type its name, then Enter")
        elif action == "rename":
            if self.profile.builtin:
                self.say("Default keeps its name - New or Duplicate makes one you can name")
            else:
                self.start_rename()
        elif action == "delete":
            p = self.profile
            if p.builtin:
                self.say("Default can't be deleted")
            elif time.perf_counter() - self.delete_armed < 3.0:
                self.delete_armed = 0.0
                st.delete(p)
                self.changed = True
                self.say(f"“{p.name}” deleted - back to Default")
            else:
                self.delete_armed = time.perf_counter()
                self.say(f"Click Delete again to delete “{p.name}”")

    def start_rename(self):
        if self.profile.builtin:
            return
        self.rename = {"text": self.profile.name, "fresh": True}
        pygame.key.start_text_input()

    def cancel_rename(self):
        if self.rename is not None:
            self.rename = None
            pygame.key.stop_text_input()

    def commit_rename(self):
        r = self.rename
        self.cancel_rename()
        if r and r["text"].strip() and self.store.rename(self.profile, r["text"]):
            self.say(f"Renamed “{self.profile.name}”")

    def _footer_action(self, action):
        ed = self.app.editor
        if action == "show":
            ed.toggle_fingering()
        elif action == "apply":
            if not ed.show_fingering:
                ed.toggle_fingering()
                self.say("Fingering shown - the suggestions are being worked out; Apply again once they're there")
                return
            ed.apply_suggestions()
            self.say(self.app.message)

    # ----- input ----------------------------------------------------------------------
    def handle_event(self, e):
        """Events while the window is open (it takes them all). False when it closes."""
        if e.type in (pygame.VIDEORESIZE, getattr(pygame, "WINDOWSIZECHANGED", -1)):
            self.app.layout()
            self.layout()
            return True
        if e.type == pygame.KEYDOWN:
            if self.rename is not None:
                if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.commit_rename()
                elif e.key == pygame.K_ESCAPE:
                    self.cancel_rename()
                elif e.key == pygame.K_BACKSPACE:
                    self.rename["text"] = "" if self.rename["fresh"] else self.rename["text"][:-1]
                    self.rename["fresh"] = False
                return True
            if e.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
                return self.close()
            if e.key == pygame.K_F1:
                return True
            return True
        if e.type == pygame.TEXTINPUT and self.rename is not None:
            t = e.text.replace("/", "-").replace("\\", "-")
            self.rename["text"] = (t if self.rename["fresh"] else self.rename["text"] + t)[:48]
            self.rename["fresh"] = False
            return True
        if e.type == pygame.MOUSEWHEEL:
            dy = getattr(e, "precise_y", e.y)
            self.scroll_t -= dy * px(60)
            self._clamp_scroll()
            return True
        if e.type == pygame.MOUSEMOTION:
            if self.press is not None:
                self._drag_slider(e.pos)
            return True
        if e.type == pygame.MOUSEBUTTONUP and e.button == 1 and self.press is not None:
            p = self.press
            self.press = None
            self.set_value(p["key"], self.profile.get(p["key"]) if not self.profile.builtin else p["value"], save=True)
            return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            return self._click(e.pos)
        return True

    def _click(self, pos):
        if self.done.hit(pos) or not self.box.collidepoint(pos):
            return self.close()
        if self.rename is not None:
            rows = self._list_rows()
            hit = next((r for p, r in rows if p is self.profile and r.collidepoint(pos)), None)
            if hit is None:
                self.commit_rename()
        self._sync_buttons()
        for b in self.list_buttons:
            if b.hit(pos):
                self._list_action(b.action)
                return True
        for btn in (self.show_btn, self.apply_btn):
            if btn.hit(pos):
                self._footer_action(btn.action)
                return True
        now = time.perf_counter()
        for p, r in self._list_rows():
            if r.collidepoint(pos):
                last_t, last_id = self._last_click
                self._last_click = (now, p.id)
                if p is self.profile and now - last_t < 0.38 and last_id == p.id:
                    self._list_action("rename")
                else:
                    self._select(p)
                return True
        if self.content.collidepoint(pos):
            for row in self.rows:
                if row["kind"] != "setting":
                    continue
                rr = self._row_rect(row)
                if not rr.collidepoint(pos):
                    continue
                s = row["s"]
                for value, label, tip, cr in row["chips"]:
                    if cr.move(rr.x, rr.y + px(6)).collidepoint(pos):
                        if value != self.profile.get(s["id"]):
                            self.set_value(s["id"], value)
                            self.say(f"{s['label']}: {label}" + (f" - {tip}" if tip else ""))
                        return True
                if s["kind"] == "slider":
                    track = self._slider_track(row, rr)
                    if track.inflate(px(16), px(20)).collidepoint(pos):
                        self.press = {"key": s["id"], "track": track, "value": self.profile.get(s["id"])}
                        self._drag_slider(pos)
                        return True
        return True

    def _slider_track(self, row, rr):
        c = row["ctrl"].move(rr.x, rr.y + px(6))
        return pygame.Rect(c.x + px(8), c.y + px(14), c.w - px(110), px(6))

    def _drag_slider(self, pos):
        p = self.press
        s = P.SETTING[p["key"]]
        t = p["track"]
        f = min(1.0, max(0.0, (pos[0] - t.x) / max(1, t.w)))
        v = s["min"] + f * (s["max"] - s["min"])
        v = round(v, 2)
        if self.profile.builtin:
            self.set_value(p["key"], v, save=True)          # (the first change makes the copy)
        else:
            self.profile.settings[p["key"]] = v             # (saved, and the planner told, on release)
            p["value"] = v

    def close(self):
        self.cancel_rename()
        if self.press is not None:
            self.press = None
        ed = self.app.editor
        if self.changed and not ed.show_fingering and ed.doc.notes:
            ed.toggle_fingering()
            self.app.say(f"Fingering shown, planned with “{self.profile.name}”")
        elif self.changed:
            self.app.say(f"Fingering planned with “{self.profile.name}”")
        return False

    # ----- drawing ----------------------------------------------------------------------
    def draw(self, surf):
        f = self.fonts
        mouse = pygame.mouse.get_pos()
        w, h = surf.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        b = self.box
        pygame.draw.rect(surf, PANEL, b, border_radius=px(12))
        pygame.draw.rect(surf, PANEL_EDGE, b, 1, border_radius=px(12))
        surf.blit(f["button"].render("Fingering", True, TEXT), (b.x + px(22), b.y + px(16)))
        sub = f["small"].render("profiles: how the standard figures are fingered", True, TEXT_DIM)
        surf.blit(sub, (b.x + px(22) + f["button"].size("Fingering")[0] + px(12), b.y + px(24)))
        self.done.active = True
        self.done.draw(surf, f, mouse)
        self.tip = None
        self._draw_list(surf, mouse)
        self._draw_settings(surf, mouse)
        # footer
        ed = self.app.editor
        self.show_btn.active = ed.show_fingering
        n = sum(1 for nt in ed.doc.notes.values()
                if not (nt.finger or nt.gliss) and ed.sugg.get(nt.id, (0, 0, 0))[1]) if ed.suggestions_ready() else 0
        self.apply_btn.label = f"Apply suggestions to {n} notes" if n else "Apply suggestions"
        self.apply_btn.enabled = bool(ed.doc.notes)
        for btn in (self.show_btn, self.apply_btn):
            btn.draw(surf, f, mouse)
        text = self.notice if self.notice and time.perf_counter() - self.notice_t < 8 else (
            "Default is the active pianist's own - changing it makes a new profile" if self.profile.builtin else
            "Changes are saved as you make them; the suggestions on the roll follow")
        x = self.apply_btn.rect.right + px(16)
        img = f["small"].render(fit_text(f["small"], text, self.done.rect.right - x), True,
                                ACCENT if self.notice and time.perf_counter() - self.notice_t < 8 else TEXT_DIM)
        surf.blit(img, img.get_rect(midleft=(x, self.show_btn.rect.centery)))
        if self.tip is not None and self.press is None:
            draw_tooltip(surf, f, self.tip[0], self.tip[1])

    def _draw_list(self, surf, mouse):
        f = self.fonts
        lr = self.list_rect
        pygame.draw.rect(surf, (32, 32, 40), lr, border_radius=px(8))
        surf.set_clip(lr)
        for p, r in self._list_rows():
            sel = p is self.profile
            if sel:
                pygame.draw.rect(surf, mix(PANEL, ACCENT, 0.3), r, border_radius=px(6))
            elif r.collidepoint(mouse):
                pygame.draw.rect(surf, (52, 52, 64), r, border_radius=px(6))
            if sel:
                pygame.draw.circle(surf, ACCENT, (r.x + px(12), r.centery), px(4))
            if sel and self.rename is not None:
                field = r.inflate(-px(28), -px(6)).move(px(12), 0)
                pygame.draw.rect(surf, (20, 20, 26), field, border_radius=px(4))
                pygame.draw.rect(surf, ACCENT, field, 1, border_radius=px(4))
                img = f["normal"].render(self.rename["text"], True, TEXT)
                at = (field.x + px(6), field.centery - img.get_height() // 2)
                if self.rename["fresh"] and self.rename["text"]:
                    pygame.draw.rect(surf, mix((20, 20, 26), ACCENT, 0.35),
                                     (at[0] - 1, field.y + px(3), min(img.get_width() + 2, field.w - px(10)), field.h - px(6)))
                surf.blit(img, at, area=pygame.Rect(0, 0, field.w - px(10), img.get_height()))
                if not self.rename["fresh"] and (pygame.time.get_ticks() // 500) % 2 == 0:
                    cx = min(at[0] + img.get_width() + 1, field.right - px(4))
                    pygame.draw.line(surf, TEXT, (cx, field.y + px(5)), (cx, field.bottom - px(6)))
            else:
                color = TEXT if sel or not p.builtin else mix(TEXT, TEXT_DIM, 0.4)
                img = f["normal"].render(fit_text(f["normal"], p.name, r.w - px(34)), True, color)
                surf.blit(img, img.get_rect(midleft=(r.x + px(24), r.centery)))
        surf.set_clip(None)
        self._sync_buttons()
        for btn in self.list_buttons:
            btn.draw(surf, f, mouse)

    def _sync_buttons(self):
        for btn in self.list_buttons:
            btn.enabled = not (self.profile.builtin and btn.action in ("rename", "delete"))
            btn.active = btn.action == "delete" and time.perf_counter() - self.delete_armed < 3.0

    def _draw_settings(self, surf, mouse):
        f = self.fonts
        c = self.content
        surf.set_clip(c)
        prof = self.profile
        for row in self.rows:
            rr = self._row_rect(row)
            if rr.bottom < c.y or rr.y > c.bottom:
                continue
            if row["kind"] == "group":
                img = f["bold"].render(row["text"], True, ACCENT)
                surf.blit(img, (rr.x, rr.y + px(8)))
                pygame.draw.line(surf, (60, 60, 76), (rr.x + img.get_width() + px(12), rr.y + px(19)),
                                 (rr.right, rr.y + px(19)))
                continue
            s = row["s"]
            y = rr.y + px(6)
            surf.blit(f["normal"].render(s["label"], True, TEXT), (rr.x, y))
            yy = y + f["normal"].get_linesize()
            for line in row["desc"]:
                surf.blit(f["small"].render(line, True, TEXT_DIM), (rr.x, yy))
                yy += f["small"].get_linesize()
            value = prof.get(s["id"])
            changed = value != P.DEFAULTS[s["id"]]
            for v, label, tip, cr in row["chips"]:
                r = cr.move(rr.x, rr.y + px(6))
                on = v == value
                hot = r.collidepoint(mouse) and c.collidepoint(mouse)
                base = mix((54, 54, 68), ACCENT, 0.45) if on else ((74, 74, 94) if hot else (48, 48, 60))
                pygame.draw.rect(surf, base, r, border_radius=px(6))
                if on:
                    pygame.draw.rect(surf, ACCENT, r, 1, border_radius=px(6))
                img = f["small"].render(label, True, TEXT if on or hot else mix(TEXT, TEXT_DIM, 0.35))
                surf.blit(img, img.get_rect(center=r.center))
                if hot and tip:
                    self.tip = (tip, r)
            if s["kind"] == "slider":
                self._draw_slider(surf, row, rr, value, mouse)
            if changed:
                pygame.draw.circle(surf, ACCENT, (rr.x - px(9), y + f["normal"].get_linesize() // 2), px(3))
        surf.set_clip(None)
        # the scroll bar
        if self.total_h > c.h:
            track = pygame.Rect(c.right - px(8), c.y, px(6), c.h)
            pygame.draw.rect(surf, (36, 36, 46), track, border_radius=px(3))
            th_h = max(px(30), int(c.h * c.h / self.total_h))
            ty = c.y + int((c.h - th_h) * self.scroll / max(1, self.total_h - c.h))
            pygame.draw.rect(surf, th.SCROLL_THUMB, (track.x, ty, track.w, th_h), border_radius=px(3))

    def _draw_slider(self, surf, row, rr, value, mouse):
        f = self.fonts
        s = row["s"]
        t = self._slider_track(row, rr)
        frac = (value - s["min"]) / (s["max"] - s["min"])
        pygame.draw.rect(surf, (30, 30, 38), t, border_radius=px(3))
        fill = pygame.Rect(t.x, t.y, int(t.w * frac), t.h)
        if s["min"] < 0 < s["max"]:                       # (a bias: filled from the middle)
            mid = t.x + int(t.w * (0 - s["min"]) / (s["max"] - s["min"]))
            x2 = t.x + int(t.w * frac)
            fill = pygame.Rect(min(mid, x2), t.y, abs(x2 - mid), t.h)
        pygame.draw.rect(surf, mix(ACCENT, (0, 0, 0), 0.25), fill, border_radius=px(3))
        knob = (t.x + int(t.w * frac), t.centery)
        hot = self.press is not None and self.press["key"] == s["id"]
        pygame.draw.circle(surf, ACCENT if hot else TEXT, knob, px(8) if hot else px(7))
        img = f["small"].render(P.format_value(s, value), True, TEXT)
        surf.blit(img, img.get_rect(midleft=(t.right + px(16), t.centery)))
        lo = f["tiny"].render(s.get("lo", ""), True, TEXT_DIM)
        hi = f["tiny"].render(s.get("hi", ""), True, TEXT_DIM)
        surf.blit(lo, (t.x, t.bottom + px(8)))
        surf.blit(hi, hi.get_rect(topright=(t.right, t.bottom + px(8))))
