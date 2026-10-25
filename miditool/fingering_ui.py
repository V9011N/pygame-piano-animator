"""
fingering_ui.py - Hand-thesia's fingering editing, in the piano roll.

Fingering shows as a tag at each note's start, in its hand's colour (green
right, blue left). A solid tag is the note's own fingering - from the file or
set by you - and is what Export writes. A hollow tag is Hand-thesia's planner
suggesting what the file doesn't say (worked out in the background from the
hands and every finger already given); "Apply suggested fingering" makes
suggestions real.

    Right-click one note         its hand, then the finger 1-5; sequential fingering from here
    Right-click several notes    one hand for all of them; the planner fingers them (around them nothing changes)
    1-5                          finger of the one selected note
    R / L                        the selected notes to the right / left hand (fingered for you)
    G                            the selected notes are a glissando (again: they're not)
    Sequential fingering         finger note after note by key, chords top to bottom:
                                   1-5             finger (the note's hand)
                                   M K O ; '       right hand 1-5
                                   V D W A LShift  left hand 1-5
                                   Tab / Shift+Tab skip ahead / back, Backspace back one, Esc / Enter done
"""
from __future__ import annotations

import pygame

from . import fingerjob, profiles
from . import theme as th
from .document import DRUM_CHANNEL, LEFT, RIGHT, note_with
from .smf import MIDI
from .theme import ACCENT, FINGER_NAMES, HAND_NAMES, px
from .widgets import SEPARATOR, MenuItem

FINGER_DEBOUNCE = 0.35       # s after an edit before the suggestions are worked out again
CHORD_TOL = 0.03             # s: onsets this close are one chord (Hand-thesia's fingering.CHORD_TOL)

SEQ_KEYS = {pygame.K_m: (RIGHT, 1), pygame.K_k: (RIGHT, 2), pygame.K_o: (RIGHT, 3),
            pygame.K_SEMICOLON: (RIGHT, 4), pygame.K_QUOTE: (RIGHT, 5),
            pygame.K_v: (LEFT, 1), pygame.K_d: (LEFT, 2), pygame.K_w: (LEFT, 3),
            pygame.K_a: (LEFT, 4), pygame.K_LSHIFT: (LEFT, 5)}
SEQ_DIGITS = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4, pygame.K_5: 5,
              pygame.K_KP1: 1, pygame.K_KP2: 2, pygame.K_KP3: 3, pygame.K_KP4: 4, pygame.K_KP5: 5}


class FingeringMixin:
    def _init_fingering(self):
        self.show_fingering = False
        self.sugg = {}                   # note id -> (hand, finger or None, gliss): the planner's suggestion
        self.sugg_rev = None             # (notes_rev, timing_rev, profile rev) the suggestions were worked out for
        self.seq = None                  # sequential fingering: {"steps", "k", "j", "hand"}
        self._fing_wait = 0.0
        self._fing_asked = None

    def _reset_fingering(self):
        self.sugg = {}
        self.sugg_rev = None
        self.seq = None
        self._fing_asked = None
        self.app.jobs.cancel("fingering")

    # ----- suggestions ------------------------------------------------------------
    def toggle_fingering(self):
        self.show_fingering = not self.show_fingering
        if self.show_fingering:
            own = sum(1 for n in self.doc.notes.values() if n.finger or n.gliss)
            self.say(f"Fingering: {own} of {len(self.doc.notes)} notes fingered in the file (solid tags); "
                     "the hollow ones are suggestions - right-click to set, Apply makes them real")
        else:
            self.stop_sequential()

    def fingering_status(self):
        if not self.show_fingering:
            return ""
        if self.app.jobs.busy("fingering") or (not self.suggestions_ready() and self.doc.notes):
            return "Working out fingering…"
        return ""

    def _fing_key(self):
        """What the suggestions depend on: the notes, the tempo (the planner works in seconds), the profile."""
        return (self.doc.notes_rev, self.doc.timing_rev, self.app.profiles.rev)

    def suggestions_ready(self):
        return self.sugg_rev == self._fing_key()

    def _update_fingering(self, dt):
        if not (self.show_fingering or self.app.wants_fingering()) or not self.doc.notes:
            return False
        rev = self._fing_key()
        if rev == self._fing_asked:
            return False
        if self.press is not None:
            return False
        self._fing_wait += dt
        if self._fing_wait < FINGER_DEBOUNCE and self._fing_asked is not None:
            return True
        self._fing_wait = 0.0
        self._fing_asked = rev
        self.app.jobs.request("fingering", fingerjob.suggest, self._job_inputs, token=rev)
        return False

    def _track_info(self):
        doc = self.doc
        counts = {}
        for n in doc.notes.values():
            counts[n.track] = counts.get(n.track, 0) + 1
        progs = {}
        for e in sorted(doc.events.values(), key=lambda e: (e.tick, e.pos)):
            if e.kind == MIDI and e.payload[0] & 0xF0 == 0xC0 and e.track not in progs:
                progs[e.track] = e.payload[1]
        return {ti: (doc.track_name(ti), progs.get(ti, 0), counts.get(ti, 0)) for ti in range(len(doc.tracks))}

    def _rows(self, use_suggestions=False, hand_of=None):
        doc = self.doc
        notes = [n for n in doc.notes.values() if n.channel != DRUM_CHANNEL]
        on, off = doc.note_seconds(notes)
        rows = []
        for n, s, e in zip(notes, on.tolist(), off.tolist()):
            hand, finger, gliss = n.hand, n.finger, n.gliss
            if use_suggestions:
                sg = self.sugg.get(n.id)
                hand = hand or (sg[0] if sg else None) or (LEFT if n.pitch < 60 else RIGHT)
                if not finger and not gliss and sg:
                    finger = sg[1]
            if hand_of and n.id in hand_of:
                hand = hand_of[n.id]
            rows.append((n.id, n.pitch, s, max(e, s + 0.05), n.velocity, n.track, hand, finger, gliss))
        return rows

    def _job_inputs(self):
        return self._rows(), self._track_info(), self.app.profiles.planner_input()

    def _resolve(self, rows, ids, hand):
        """fingerjob.resolve here and now (a few notes), then the planner back on the active pianist."""
        try:
            return fingerjob.resolve(rows, set(ids), hand, self.app.profiles.planner_input())
        finally:
            profiles.restore_host()

    def on_fingering(self, token, result, error):
        if error is not None:
            self.say(f"Couldn't work out the fingering: {error}")
            return
        self.sugg = result
        self.sugg_rev = token

    def hand_of(self, n):
        if n.hand:
            return n.hand
        sg = self.sugg.get(n.id)
        return sg[0] if sg else (LEFT if n.pitch < 60 else RIGHT)

    # ----- edits ------------------------------------------------------------------
    def set_note_fingering(self, nid, hand, finger, label=None):
        n = self.doc.notes.get(nid)
        if n is None:
            return
        if n.hand == hand and n.finger == finger and not n.gliss:
            return
        self.doc.begin(label or f"{_name(n.pitch)} → {HAND_NAMES[hand].lower()}, finger {finger}")
        self.doc.put_note(note_with(n, hand=hand, finger=finger, gliss=False))
        self.doc.commit()
        self.say(f"{_name(n.pitch)}: {HAND_NAMES[hand].lower()}, finger {finger} ({FINGER_NAMES[finger].lower()})")

    def set_hand(self, ids, hand):
        """Several notes to one hand; the planner fingers them, everything else stays as it is."""
        ids = [i for i in ids if i in self.doc.notes]
        if not ids:
            return
        rows = self._rows(use_suggestions=True, hand_of={i: hand for i in ids})
        got = self._resolve(rows, ids, hand)
        self.doc.begin(f"{len(ids)} notes → {HAND_NAMES[hand].lower()}")
        for i in ids:
            n = self.doc.notes[i]
            self.doc.put_note(note_with(n, hand=hand, finger=got.get(i), gliss=False))
        self.doc.commit()
        self.say(f"{len(ids)} note{'s' if len(ids) != 1 else ''} → {HAND_NAMES[hand].lower()}; "
                 "fingering worked out for them")

    def clear_fingering(self, ids):
        ids = [i for i in ids if i in self.doc.notes and self.doc.notes[i].has_fingering]
        if not ids:
            self.say("No fingering to clear there")
            return
        self.doc.begin(f"Clear fingering of {len(ids)} notes")
        for i in ids:
            self.doc.put_note(note_with(self.doc.notes[i], hand=None, finger=None, gliss=False))
        self.doc.commit()
        self.say(f"Fingering cleared from {len(ids)} note{'s' if len(ids) != 1 else ''}")

    def apply_suggestions(self, ids=None):
        if not self.suggestions_ready():
            self.say("The suggestions are still being worked out - try again in a moment")
            return
        pool = ids if ids is not None else list(self.doc.notes)
        todo = []
        for i in pool:
            n = self.doc.notes.get(i)
            sg = self.sugg.get(i)
            if n is None or sg is None or n.finger or n.gliss or not (sg[1] or sg[2]):
                continue
            todo.append(note_with(n, hand=n.hand or sg[0], finger=sg[1], gliss=sg[2]))
        if not todo:
            self.say("Nothing to apply: those notes already have their own fingering")
            return
        self.doc.begin(f"Apply suggested fingering ({len(todo)} notes)")
        for n in todo:
            self.doc.put_note(n)
        self.doc.commit()
        self.say(f"Suggested fingering applied to {len(todo)} notes - Export MIDI writes it into the file")

    def gliss_candidates(self, ids):
        import glissando
        ns = sorted((self.doc.notes[i] for i in ids), key=lambda n: (n.start, n.pitch))
        if len({self.hand_of(n) for n in ns}) != 1 or len(ns) < 3:
            return None
        tm = self.doc.tempo_map()

        class _N:
            def __init__(s, n):
                s.n, s.pitch, s.start = n, n.pitch, tm.sec(n.start)
        order = glissando._ordered([_N(n) for n in ns])
        return order if glissando.is_string(order) else None

    def set_gliss(self, ids, on):
        ids = sorted(i for i in ids if i in self.doc.notes)
        if not ids:
            return
        hand = self.hand_of(self.doc.notes[ids[0]])
        if on:
            self.doc.begin(f"{len(ids)} notes → glissando")
            for i in ids:
                self.doc.put_note(note_with(self.doc.notes[i], hand=self.doc.notes[i].hand or hand, finger=None, gliss=True))
            self.doc.commit()
            self.say(f"{len(ids)} notes marked as a glissando")
        else:
            rows = self._rows(use_suggestions=True)
            got = self._resolve(rows, ids, hand)
            self.doc.begin(f"{len(ids)} notes → not a glissando")
            for i in ids:
                self.doc.put_note(note_with(self.doc.notes[i], gliss=False, finger=got.get(i)))
            self.doc.commit()
            self.say(f"{len(ids)} notes are no longer a glissando; fingering worked out for them")

    def _fingering_key(self, e):
        k = e.key
        sel = [i for i in self.selection if i in self.doc.notes]
        if k in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            if len(sel) == 1:
                n = self.doc.notes[sel[0]]
                self.set_note_fingering(n.id, self.hand_of(n), k - pygame.K_0)
            elif sel:
                self.say("A finger can only be set on one note at a time; R / L re-fingers a group")
            return True
        if k in (pygame.K_r, pygame.K_l) and sel:
            self.set_hand(sel, RIGHT if k == pygame.K_r else LEFT)
            return True
        if k == pygame.K_g and sel:
            if all(self.doc.notes[i].gliss for i in sel):
                self.set_gliss(sel, False)
            elif len(sel) > 2 and self.gliss_candidates(sel):
                self.set_gliss(sel, True)
            else:
                self.say("A glissando is a string of next-door white keys (or black keys) going one way, "
                         "in one hand - at least 3 notes")
            return True
        return False

    def _fingering_menu_items(self, sel):
        """(menu items, handler(value, sub) -> handled) for the selected notes."""
        doc = self.doc
        items = []
        if len(sel) == 1:
            n = doc.notes[sel[0]]
            hand = self.hand_of(n)
            for h, key in ((RIGHT, pygame.K_r), (LEFT, pygame.K_l)):
                fingers = [MenuItem(f"{f}  {FINGER_NAMES[f]}", f, checked=(n.hand == h or (not n.hand and h == hand))
                                    and n.finger == f, hint=str(f), key=pygame.K_0 + f) for f in range(1, 6)]
                items.append(MenuItem(HAND_NAMES[h], ("hand", h), children=fingers, checked=(h == hand),
                                      hint="R" if h == RIGHT else "L", key=key))
            items.append(MenuItem("Sequential fingering from here", "seq", hint="Enter", key=pygame.K_RETURN))
            sg = self.sugg.get(n.id)
            if sg and not (n.finger or n.gliss) and (sg[1] or sg[2]):
                items.append(MenuItem(f"Apply suggestion ({'g' if sg[2] else sg[1]})", "apply"))
            if n.gliss:
                items.append(MenuItem("Not a glissando (the whole glissando)", "nogliss", hint="G"))
            if n.has_fingering:
                items.append(MenuItem("Clear fingering", "clear"))
        else:
            hands = {self.hand_of(doc.notes[i]) for i in sel}
            for h, k in ((RIGHT, "R"), (LEFT, "L")):
                items.append(MenuItem(HAND_NAMES[h] + " (finger them for me)", ("hand", h), checked=hands == {h}, hint=k))
            if any(not (doc.notes[i].finger or doc.notes[i].gliss) and self.sugg.get(i, (0, 0, 0))[1] for i in sel):
                items.append(MenuItem("Apply suggested fingering", "apply"))
            if all(doc.notes[i].gliss for i in sel):
                items.append(MenuItem("Not a glissando", "nogliss", hint="G"))
            elif self.gliss_candidates(sel):
                items.append(MenuItem("Glissando", "gliss", hint="G"))
            if any(doc.notes[i].has_fingering for i in sel):
                items.append(MenuItem("Clear fingering", "clear"))

        def handle(v, sub):
            if isinstance(v, tuple) and v[0] == "hand":
                if len(sel) == 1:
                    if sub:
                        self.set_note_fingering(sel[0], v[1], sub)
                    return True
                self.set_hand(sel, v[1])
                return True
            if v == "seq":
                self.start_sequential(sel[0])
            elif v == "apply":
                self.apply_suggestions(sel)
            elif v == "clear":
                self.clear_fingering(sel)
            elif v == "gliss":
                self.set_gliss(sel, True)
            elif v == "nogliss":
                if len(sel) == 1:
                    self._ungliss_run(sel[0])
                else:
                    self.set_gliss(sel, False)
            else:
                return False
            return True
        return items, handle

    def _ungliss_run(self, nid):
        import glissando
        n = self.doc.notes[nid]
        hand = self.hand_of(n)
        tm = self.doc.tempo_map()

        class _N:
            def __init__(s, m):
                s.m, s.pitch, s.start, s.gliss = m, m.pitch, tm.sec(m.start), m.gliss
        same = [_N(m) for m in self.doc.notes.values() if self.hand_of(m) == hand]
        run = next((r for r in glissando.explicit(same) if any(x.m.id == nid for x in r)), None)
        self.set_gliss([x.m.id for x in run] if run else [nid], False)

    # ----- sequential fingering (Hand-thesia's) ----------------------------------------
    def start_sequential(self, nid):
        if not self.show_fingering:
            self.show_fingering = True
        n0 = self.doc.notes[nid]
        hand = self.hand_of(n0)
        tm = self.doc.tempo_map()
        mine = sorted((n for n in self.doc.notes.values() if self.hand_of(n) == hand and n.channel != DRUM_CHANNEL),
                      key=lambda n: (n.start, -n.pitch))
        steps, cur, t_cur = [], [], None
        for n in mine:
            t = tm.sec(n.start)
            if cur and t - t_cur > CHORD_TOL:
                steps.append(cur)
                cur = []
            if not cur:
                t_cur = t
            cur.append(n.id)                      # highest first within a chord
        if cur:
            steps.append(cur)
        k = next(k for k, st in enumerate(steps) if nid in st)
        self.seq = {"steps": steps, "k": k, "j": steps[k].index(nid), "hand": hand}
        self.app.player.stop()
        self._seq_show()
        self.say(f"Sequential fingering, {HAND_NAMES[hand].lower()}: 1-5 · right hand M K O ; ' · "
                 "left hand V D W A LShift · Backspace back · Tab skip · Esc done")

    def seq_note(self):
        q = self.seq
        return q["steps"][q["k"]][q["j"]] if q else None

    def _seq_has(self, nid):
        return self.seq is not None and any(nid in st for st in self.seq["steps"])

    def stop_sequential(self, why=None):
        if self.seq is not None:
            self.seq = None
            self.say(why or "Sequential fingering finished")

    def _seq_show(self):
        nid = self.seq_note()
        n = self.doc.notes.get(nid)
        if n is None:
            self.stop_sequential("Sequential fingering stopped: that note is gone")
            return
        self.selection = {nid}
        self.cursor = n.start
        self.reveal(n.start, n.pitch)

    def _seq_goto(self, nid):
        q = self.seq
        k = next((k for k, st in enumerate(q["steps"]) if nid in st), None)
        if k is not None:
            q["k"], q["j"] = k, q["steps"][k].index(nid)
        self._seq_show()

    def _seq_step(self, d):
        q = self.seq
        k, j = q["k"], q["j"] + d
        while j >= len(q["steps"][k]):
            j -= len(q["steps"][k])
            k += 1
            if k >= len(q["steps"]):
                self.stop_sequential("Sequential fingering: reached the end of the piece")
                return
        while j < 0:
            k -= 1
            if k < 0:
                k, j = 0, 0
                break
            j += len(q["steps"][k])
        q["k"], q["j"] = k, j
        self._seq_show()

    def _seq_key(self, e):
        """Keys while sequential fingering is on; False = not ours."""
        k, mod = e.key, e.mod
        if mod & (pygame.KMOD_CTRL | pygame.KMOD_META):
            if k in (pygame.K_z, pygame.K_y):
                redo = k == pygame.K_y or bool(mod & pygame.KMOD_SHIFT)
                self.undo(redo=redo)
                if self.seq is not None and self.selection:
                    target = next(iter(self.selection))
                    if self._seq_has(target):
                        self._seq_goto(target)
                return True
            return False
        if k in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.stop_sequential()
            return True
        nid = self.seq_note()
        n = self.doc.notes.get(nid)
        if n is None:
            self.stop_sequential()
            return True
        if k in SEQ_KEYS:
            hand, f = SEQ_KEYS[k]
        elif k in SEQ_DIGITS:
            hand, f = self.hand_of(n), SEQ_DIGITS[k]
        elif k == pygame.K_TAB:
            self._seq_step(-1 if mod & pygame.KMOD_SHIFT else 1)
            return True
        elif k == pygame.K_BACKSPACE:
            self._seq_step(-1)
            return True
        else:
            return False
        self.set_note_fingering(nid, hand, f)
        self._seq_step(1)
        return True

    def seq_status(self):
        q = self.seq
        nid = self.seq_note()
        n = self.doc.notes.get(nid)
        if n is None:
            return ""
        size = len(q["steps"][q["k"]])
        where = f"note {q['j'] + 1} of {size} in this chord (top down)" if size > 1 else "single note"
        return (f"SEQUENTIAL ({HAND_NAMES[q['hand']].lower()})  {_name(n.pitch)}, {where}   ·   1-5 · "
                "R: M K O ; '  · L: V D W A LShift · Tab skip · Backspace back · Esc done")

    def _draw_seq_marks(self, surf, idx_l, xs, ws, ys, h):
        q = self.seq
        chord = set(q["steps"][q["k"]])
        cur = self.seq_note()
        notes = self.doc.index().notes
        for i, x, w, y in zip(idx_l, xs, ws, ys):
            nid = notes[i].id
            r = pygame.Rect(x, y, w, h)
            if nid == cur:
                pygame.draw.rect(surf, (255, 255, 255), r.inflate(px(6), px(6)), 2, border_radius=px(5))
                pygame.draw.rect(surf, ACCENT, r.inflate(px(2), px(2)), 2, border_radius=px(4))
                tip = [(r.x - px(10), r.centery - px(6)), (r.x - px(3), r.centery), (r.x - px(10), r.centery + px(6))]
                pygame.draw.polygon(surf, ACCENT, tip)
            elif nid in chord:
                pygame.draw.rect(surf, (200, 200, 210), r.inflate(px(4), px(4)), 1, border_radius=px(5))


def _name(p):
    from .render import note_name
    return note_name(p)
