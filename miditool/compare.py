"""
compare.py - Reviewing an imposed perfect source: both piano rolls at once.

The perfect source's roll sits on top, laid onto the loaded file's timeline
through the alignment (so a note and its counterpart line up); the loaded
file's own roll - fully editable - is underneath. Lines join each performed
note to the written note it was matched with (yellow: the same note played
noticeably off its place, left alone). Notes planned for removal are red and
struck through; notes planned for adding show as green outlines where they'd
go, joined to the written note they come from.

    Click a green outline (or the green source note)   add it / don't add it
    Alt+click a red note, K, or right-click             remove it / keep it
    N / Shift+N, ◀ ▶                                     the next / previous planned edit
    Apply                                                make the accepted edits (one undo step)
"""
from __future__ import annotations

import numpy as np
import pygame

from . import impose as imp
from . import theme as th
from .render import BLACK, fmt_time, note_name
from .theme import ACCENT, TEXT, TEXT_DIM, fit_text, mix, px
from .widgets import Button, MenuItem

REPLAN_DEBOUNCE = 0.25
LINKS_MAX = 600              # more notes in view than this: only the telling links are drawn


class CompareMixin:
    def _init_compare(self):
        self.impose = None
        self.show_links = True
        self.src_roll = None
        self.impose_bar = None
        self._impose_buttons = []
        self._focus = None              # ("rm", note id) / ("add", perfect row): the edit ◀ ▶ went to
        self._replan_wait = 0.0
        self._replan_asked = None
        self._src_hover = None
        self._add_cache = None

    def open_impose(self, session):
        self.impose = session
        self._focus = None
        self._replan_asked = session.plan_rev
        self._add_cache = None
        self.layout(self.area)
        s = session.summary(self.doc)
        self.say(f"Imposed {self.impose.name}: {s['confidence']:.0%} match - {s['remove']} note(s) to remove, "
                 f"{s['add']} to add. Click them to decline, then Apply")

    def close_impose(self):
        if self.impose is not None:
            self.impose = None
            self.app.jobs.cancel("replan")
            self.layout(self.area)

    # ----- keeping the plan current ------------------------------------------------
    def _update_compare(self, dt):
        s = self.impose
        if s is None:
            return False
        rev = self.doc.notes_rev
        if rev == s.plan_rev or rev == self._replan_asked or self.press is not None:
            return False
        self._replan_wait += dt
        if self._replan_wait < REPLAN_DEBOUNCE:
            return True
        self._replan_wait = 0.0
        self._replan_asked = rev

        def inputs():
            human, ids = imp.midi_data(self.doc, origin=s.human_origin)
            return s.perfect, human, s.alignment, ids
        self.app.jobs.request("replan", imp.replan_job, inputs, token=rev)
        return False

    def on_replan(self, token, result, error):
        s = self.impose
        if s is None:
            return
        if error is not None:
            self.say(f"Couldn't update the comparison: {error}")
            return
        plan, ids = result
        s.set_plan(plan, ids)
        s.plan_rev = token
        self._add_cache = None

    @property
    def impose_fresh(self):
        return self.impose is not None and self.impose.plan_rev == self.doc.notes_rev

    def _additions_ticks(self):
        """[(perfect row, start, end, pitch, NewNote)] of the planned additions, in the loaded file's ticks."""
        s = self.impose
        key = (id(s.plan), self.doc.timing_rev)
        if self._add_cache is None or self._add_cache[0] != key:
            out = []
            for p, nn in s.additions:
                a, b = s.add_ticks(self.doc, nn)
                out.append((p, a, b, nn.pitch, nn))
            out.sort(key=lambda r: r[1])
            self._add_cache = (key, out)
        return self._add_cache[1]

    # ----- how the loaded file's notes look -------------------------------------------
    def _note_status(self, n, color, border, selected):
        s = self.impose
        st = s.status_of.get(n.id)
        mark = None
        if st == imp.REMOVE:
            if s.do_remove and n.id not in s.keep:
                color = mix(th.REMOVE_COLOR, (255, 255, 255), 0.3) if selected else th.REMOVE_COLOR
                border = mix(th.REMOVE_COLOR, (0, 0, 0), 0.5)
                mark = "remove"
            else:
                mark = "kept"
        elif st == imp.DISPLACED:
            mark = "displaced"
        elif st == imp.OUTSIDE:
            color = mix(color, th.BG, 0.55)
        if self._focus == ("rm", n.id):
            mark = "focus"
        return color, border, mark

    # ----- the source roll --------------------------------------------------------------
    def _src_color(self, row):
        s = self.impose
        st = int(s.plan.perfect_status[row])
        if st == imp.ADD:
            return th.ADD_COLOR if (s.do_add and row not in s.skip) else mix(th.ADD_COLOR, (90, 90, 100), 0.65)
        if st == imp.DISPLACED:
            return mix(th.SOURCE_NOTE, th.DISPLACED_COLOR, 0.55)
        if st in (imp.UNTRUSTED, imp.HELD, imp.OUTSIDE):
            return mix(th.SOURCE_NOTE, th.BG, 0.5)
        return th.SOURCE_NOTE

    def _draw_source_notes(self, surf):
        s = self.impose
        r = self.src_roll
        w = s.warped(self.doc)
        got = self._visible_rows(r, w["start"], w["end"], w["pitch"], w["max_len"])
        if got is None:
            return
        idx, xs, ws, ys, h = got
        rows = w["row"][idx].tolist()
        surf.set_clip(r)
        fill = surf.fill
        detail = h >= 4 and len(rows) < 2500
        src_notes = s.source.notes
        tags = self.show_fingering and h >= px(9) and s.source_has_fingering
        for row, x, wd, y in zip(rows, xs, ws, ys):
            c = self._src_color(row)
            bk = 1 if int(s.perfect.pitches[row]) % 12 in BLACK else 0
            if bk:
                c = mix(c, (0, 0, 0), 0.18)
            if detail:
                fill(mix(c, (0, 0, 0), 0.5), (x, y, wd, h))
                fill(c, (x + 1, y + 1, max(1, wd - 2), max(1, h - 2)))
            else:
                fill(c, (x, y, wd, h))
            if self._focus == ("add", row) or self._src_hover == row:
                pygame.draw.rect(surf, (255, 255, 255), (x - 2, y - 2, wd + 4, h + 4), 1)
            if tags:
                n = src_notes.get(s.perfect_ids[row])
                if n is not None and (n.finger or n.gliss):
                    text = "g" if n.gliss else str(n.finger)
                    base = th.HAND_COLORS.get(n.hand, ((150, 150, 160),))[0]
                    img = self._label(text, "finger" if h >= px(13) else "tiny", (18, 18, 22))
                    box = pygame.Rect(x, y, img.get_width() + px(6), min(h, img.get_height() + px(1)))
                    box.centery = y + h // 2
                    pygame.draw.rect(surf, base, box, border_radius=px(3))
                    surf.blit(img, img.get_rect(center=box.center))
        surf.set_clip(None)

    # ----- links between the rolls ---------------------------------------------------------
    def _draw_links(self, surf):
        if not self.show_links:
            return
        s = self.impose
        w = s.warped(self.doc)
        doc = self.doc
        ix = doc.index()
        rr, sr = self.roll, self.src_roll
        idx = ix.window(self.vx.lo, self.vx.hi)
        p_lo, p_hi = self._visible_pitches()
        lines = []
        hot_ids = set(self.selection) | ({self.hover} if self.hover else set())
        # every matched pair joined while they're few enough to follow; zoomed out, only the ones that
        # say something: off-time notes, what's selected or under the mouse, and the planned additions
        visible = [ix.notes[i] for i in idx.tolist()]
        visible = [n for n in visible if p_lo <= n.pitch <= p_hi]
        all_links = len(visible) <= LINKS_MAX and (len(visible) < 20 or self.roll.w / max(1, len(visible)) >= px(4))
        for n in visible:
            part = s.partner_of.get(n.id)
            if part is None:
                continue
            st = s.status_of.get(n.id)
            hot = n.id in hot_ids or self._src_hover == part[0]
            if not (all_links or hot or st == imp.DISPLACED):
                continue
            k = w["at"].get(part)
            if k is None:
                continue
            x0 = self.x_of(n.start)
            y0 = self.y_of(n.pitch + 1, rr)
            x1 = self.x_of(int(w["start"][k]))
            y1 = self.y_of(int(w["pitch"][k]), sr)
            if st == imp.DISPLACED:
                c = th.DISPLACED_COLOR if hot else mix(th.DISPLACED_COLOR, th.BG, 0.4)
            else:
                base = th.CHANNEL_COLORS[n.channel]
                c = mix(base, (255, 255, 255), 0.3) if hot else mix(base, th.BG, 0.6)
            lines.append((hot, c, (x0, y0), (x1, y1)))
        # planned additions: from where the note would go to the written note
        for p, a, b, pitch, nn in self._additions_ticks():
            if b < self.vx.lo or a > self.vx.hi or not (p_lo <= pitch <= p_hi):
                continue
            k = None
            for seg in range(len(s.alignment.segments)):
                k = w["at"].get((p, seg))
                if k is not None:
                    break
            if k is None:
                continue
            on = s.do_add and p not in s.skip
            c = th.ADD_COLOR if on else mix(th.ADD_COLOR, th.BG, 0.6)
            lines.append((False, mix(c, th.BG, 0.25), (self.x_of(a), self.y_of(pitch + 1, rr)),
                          (self.x_of(int(w["start"][k])), self.y_of(int(w["pitch"][k]), sr))))
        lines.sort(key=lambda L: L[0])
        for rect in (sr, rr):
            surf.set_clip(rect)
            for hot, c, a, b in lines:
                pygame.draw.aaline(surf, c, a, b) if not hot else pygame.draw.line(surf, c, a, b, max(1, px(2)))
        surf.set_clip(None)

    # ----- planned additions in the loaded file's roll ----------------------------------
    def _draw_ghosts(self, surf):
        s = self.impose
        r = self.roll
        surf.set_clip(r)
        rh = self.row_h()
        p_lo, p_hi = self._visible_pitches()
        h = max(2, int(rh) - 1)
        for p, a, b, pitch, nn in self._additions_ticks():
            if b < self.vx.lo or a > self.vx.hi or not (p_lo <= pitch <= p_hi):
                continue
            x0 = int(self.x_of(a))
            w = max(px(4), int(self.x_of(b)) - x0)
            y = int(self.y_of(pitch + 1)) + 1
            rect = pygame.Rect(x0, y, w, h)
            on = s.do_add and p not in s.skip
            if on:
                surf.fill(mix(th.ADD_COLOR, th.BG, 0.62), rect)
                pygame.draw.rect(surf, th.ADD_COLOR, rect, max(1, px(2)))
            else:
                pygame.draw.rect(surf, mix(th.ADD_COLOR, (90, 90, 100), 0.7), rect, 1)
            if self._focus == ("add", p):
                pygame.draw.rect(surf, (255, 255, 255), rect.inflate(px(6), px(6)), 2, border_radius=px(4))
        surf.set_clip(None)

    def _ghost_at(self, pos):
        if self.impose is None or not self.roll.collidepoint(pos):
            return None
        t = self.t_of(pos[0])
        pitch = int(np.floor(self.u_of(pos[1])))
        tpp = self.ticks_per_px()
        for p, a, b, pp, nn in self._additions_ticks():
            if pp == pitch and a - 2 * tpp <= t <= max(b, a + 4 * tpp):
                return p
        return None

    def _ghost_click(self, pos):
        p = self._ghost_at(pos)
        if p is None:
            return False
        self.impose.toggle_addition(p)
        self._focus = ("add", p)
        nn = self.impose.add_of.get(p)
        on = p not in self.impose.skip
        self.say(f"{note_name(nn.pitch)} will {'be added' if on else 'not be added'}" if nn else "")
        return True

    def _removal_toggle_at(self, pos):
        if self.impose is None:
            return False
        nid = self.note_at(pos)
        if nid is None or self.impose.status_of.get(nid) != imp.REMOVE:
            return False
        self._toggle_keep([nid])
        return True

    def _toggle_keep(self, ids):
        s = self.impose
        ids = [i for i in ids if s.status_of.get(i) == imp.REMOVE]
        if not ids:
            self.say("None of those notes is planned for removal")
            return
        keep = not all(i in s.keep for i in ids)
        for i in ids:
            (s.keep.add if keep else s.keep.discard)(i)
        self.say(f"{len(ids)} note{'s' if len(ids) != 1 else ''} will be "
                 + ("kept" if keep else "removed, as planned"))

    def toggle_keep_selected(self):
        self._toggle_keep(list(self.selection))

    def _hover_compare(self, pos):
        self._src_hover = None
        if self.impose is None or self.src_roll is None or not self.src_roll.collidepoint(pos):
            return
        self._src_hover = self._src_note_at(pos)

    def _src_note_at(self, pos):
        s = self.impose
        w = s.warped(self.doc)
        t = self.t_of(pos[0])
        pitch = int(np.floor(self.u_of(pos[1], self.src_roll)))
        tpp = self.ticks_per_px()
        lo = int(np.searchsorted(w["start"], t - w["max_len"] - 4 * tpp, "left"))
        hi = int(np.searchsorted(w["start"], t + tpp, "right"))
        best = None
        for k in range(lo, hi):
            if int(w["pitch"][k]) == pitch and w["start"][k] - tpp <= t <= max(w["end"][k], w["start"][k] + 4 * tpp):
                best = int(w["row"][k])
        return best

    def _src_roll_down(self, e, mod):
        row = self._src_note_at(e.pos)
        s = self.impose
        if row is None:
            return True
        st = int(s.plan.perfect_status[row])
        src = s.source_note(row)
        if e.button == 1 and st == imp.ADD:
            s.toggle_addition(row)
            self._focus = ("add", row)
            self.say(f"{note_name(int(s.perfect.pitches[row]))} will "
                     + ("not be added" if row in s.skip else "be added"))
        elif e.button == 1:
            what = {imp.MATCHED: "matched", imp.DISPLACED: "played a little off its place (left alone)",
                    imp.UNTRUSTED: "not edited (the alignment is unsure there)",
                    imp.HELD: "already sounding in the performance", imp.OUTSIDE: "outside the aligned stretches"}
            self.say(f"Source {note_name(int(s.perfect.pitches[row]))}: {what.get(st, '')}"
                     + (f" · fingering {src.hand or ''}{src.finger or ('g' if src.gliss else '')}" if src and src.has_fingering else ""))
        return True

    def _compare_menu_items(self, sel):
        s = self.impose
        if s is None:
            return [], lambda v, sub: False
        rm = [i for i in sel if s.status_of.get(i) == imp.REMOVE]
        items = []
        if rm:
            kept = all(i in s.keep for i in rm)
            label = ("Remove as planned" if kept else "Keep (don't remove)") + (f" ({len(rm)})" if len(rm) > 1 else "")
            items.append(MenuItem(label, "keep", hint="K", color=th.REMOVE_COLOR if kept else th.GOOD))

        def handle(v, sub):
            if v == "keep":
                self._toggle_keep(rm)
                return True
            return False
        return items, handle

    # ----- going through the edits ------------------------------------------------------
    def _edit_list(self):
        s = self.impose
        out = [(self.doc.notes[i].start, "rm", i) for i in s.removals if i in self.doc.notes]
        out += [(a, "add", p) for p, a, b, pitch, nn in self._additions_ticks()]
        out.sort()
        return out

    def goto_edit(self, d):
        edits = self._edit_list()
        if not edits:
            self.say("No planned edits left")
            return
        keys = [(k, v) for _, k, v in edits]
        if self._focus in keys:
            i = (keys.index(self._focus) + d) % len(edits)
        else:
            cur = self.vx.lo + self.vx.span * 0.4
            i = next((j for j, (t, _, _) in enumerate(edits) if t > cur), 0) if d > 0 else \
                max((j for j, (t, _, _) in enumerate(edits) if t < cur), default=len(edits) - 1)
        t, kind, v = edits[i]
        self._focus = (kind, v)
        if kind == "rm":
            n = self.doc.notes[v]
            self.selection = {v}
            self.reveal(n.start, n.pitch, center=True)
            state = "kept" if v in self.impose.keep else "to remove"
            self.say(f"Edit {i + 1} of {len(edits)}: {note_name(n.pitch)} {state} (K or Alt+click toggles)")
        else:
            nn = self.impose.add_of[v]
            self.reveal(t, nn.pitch, center=True)
            state = "skipped" if v in self.impose.skip else "to add"
            self.say(f"Edit {i + 1} of {len(edits)}: {note_name(nn.pitch)} {state} (click it to toggle)")

    # ----- the bar between the rolls -------------------------------------------------------
    def _layout_impose_bar(self):
        b = self.impose_bar
        s = self.impose
        f = self.fonts
        specs = [("Close", "close", "Close the comparison (nothing more is changed)", False),
                 ("Apply", "apply", "Make the accepted edits (one undo step)", False),
                 ("▶", "next", "Next planned edit (N)", False),
                 ("◀", "prev", "Previous planned edit (Shift+N)", False),
                 ("Lines", "links", "Show the lines joining matched notes", True)]
        if s.source_has_fingering:
            specs.append(("Copy fingering", "fing", "Matched and added notes without fingering take the source's", True))
        specs += [("Add", "add", "Add the written notes the performance lacks", True),
                  ("Remove", "remove", "Remove the performed notes the source doesn't have", True)]
        x = b.right - px(8)
        self._impose_buttons = []
        for label, action, tip, toggle in specs:
            btn = Button(label, action, tip=tip, toggle=toggle)
            w = btn.width(f) + (px(22) if action in ("apply", "add", "remove") else 0)
            btn.rect = pygame.Rect(x - w, b.y + px(6), w, b.h - px(12))
            x -= w + px(6)
            self._impose_buttons.append(btn)
        self._impose_text_right = x

    def _draw_impose_bar(self, surf, mouse):
        b = self.impose_bar
        s = self.impose
        surf.fill((34, 34, 44), b)
        surf.fill((60, 60, 76), (b.x, b.y, b.w, 1))
        surf.fill((60, 60, 76), (b.x, b.bottom - 1, b.w, 1))
        fresh = self.impose_fresh
        rm_on = len(s.accepted_removals(self.doc)) if fresh else 0
        add_on = len(s.accepted_additions()) if fresh else 0
        fing = len(s.fingering_copies(self.doc)) if fresh else 0
        for btn in self._impose_buttons:
            a = btn.action
            if a == "remove":
                btn.label = f"Remove {len(s.removals)}"
                btn.active = s.do_remove
            elif a == "add":
                btn.label = f"Add {len(s.additions)}"
                btn.active = s.do_add
            elif a == "fing":
                btn.active = s.copy_fingering
            elif a == "links":
                btn.active = self.show_links
            elif a == "apply":
                n = rm_on + add_on + fing
                btn.label = f"Apply {n}" if n else "Apply"
                btn.enabled = fresh and n > 0
                btn.active = btn.enabled
            btn.draw(surf, self.fonts, mouse)
        # what was found
        r = self.result_color()
        conf = s.result.confidence
        x = b.x + px(10)
        room = self._impose_text_right - x - px(8)
        title = fit_text(self.fonts["bold"], f"Perfect source: {s.name}", int(room * 0.45))
        img = self.fonts["bold"].render(title, True, TEXT)
        surf.blit(img, img.get_rect(midleft=(x, b.centery)))
        x += img.get_width() + px(12)
        verdict = s.result.verdict.split(" (")[0]
        img = self.fonts["small"].render(f"{conf:.0%} · {verdict}", True, r)
        if x + img.get_width() < self._impose_text_right:
            surf.blit(img, img.get_rect(midleft=(x, b.centery)))
            x += img.get_width() + px(12)
        stats = (f"{s.plan.matched:,} matched · {s.plan.displaced} off-time · "
                 f"{len(s.removals)} extra · {len(s.additions)} missing")
        if not fresh:
            stats = "updating…"
        img = self.fonts["small"].render(fit_text(self.fonts["small"], stats, self._impose_text_right - x - px(6)),
                                         True, TEXT_DIM)
        surf.blit(img, img.get_rect(midleft=(x, b.centery)))
        for btn in self._impose_buttons:
            if btn.rect.collidepoint(mouse) and btn.tip and self.press is None and self.menu is None:
                self.tip = (btn.tip, btn.rect)

    def result_color(self):
        lvl = imp.verdict_color_level(self.impose.result.confidence)
        return {"good": th.GOOD, "uncertain": th.WARN, "bad": th.BAD}[lvl]

    def _impose_bar_click(self, pos):
        s = self.impose
        for btn in self._impose_buttons:
            if not btn.hit(pos):
                continue
            a = btn.action
            if a == "close":
                self.close_impose()
                self.say("Comparison closed")
            elif a == "apply":
                rm, add, fing = s.apply(self.doc)
                self._focus = None
                self.say(f"Imposed: removed {rm}, added {add} note{'s' if add != 1 else ''}"
                         + (f", fingering copied to {fing}" if fing else "") + " - Ctrl+Z undoes it")
            elif a == "next":
                self.goto_edit(1)
            elif a == "prev":
                self.goto_edit(-1)
            elif a == "links":
                self.show_links = not self.show_links
            elif a == "fing":
                s.copy_fingering = not s.copy_fingering
                n = len(s.fingering_copies(self.doc))
                self.say(f"Copy fingering: {'on' if s.copy_fingering else 'off'}"
                         + (f" - {n} matched notes without fingering will take the source's" if s.copy_fingering else ""))
            elif a == "add":
                s.do_add = not s.do_add
            elif a == "remove":
                s.do_remove = not s.do_remove
            return

