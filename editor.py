"""
editor.py - Fingering editor: a horizontal piano roll (time runs left to
right, like a DAW) above the keyboard and the animated hands.

Notes, velocities and timing are read-only here; what you edit is which hand
plays each note and with which finger. Every note shows its finger number.

    Click a note                 select it (Ctrl/Shift+click adds or removes it)
    Drag on empty space          select every note in the box (Ctrl/Shift adds)
    Right-click a note           one note:      choose the hand, then the finger 1-5
                                 several notes: choose the hand; the fingering
                                                algorithm works out the fingers
    1-5                          set the finger of the one selected note
    Right-click one note →       sequential fingering from that note: finger
      "Sequential fingering"     that hand's notes one after another (chords
                                 top to bottom) by key:
                                   1-5 (top row or numpad)  finger
                                   M K O ; '                right hand 1-5
                                   V D W A LShift           left hand 1-5
                                   Tab / Shift+Tab          skip ahead / back
                                   Backspace                back one note
                                   Esc or Enter             done
    R / L                        move the selected notes to the right / left hand
    Ctrl+A, Esc                  select all, clear the selection
    Ctrl+Z, Ctrl+Y               undo, redo
    Mouse wheel                  scroll through time
    Ctrl+wheel, + / -            zoom time
    Shift+wheel                  scroll up/down the keyboard (turns off Follow)
    Alt+wheel                    taller / shorter rows
    Drag the ruler, middle-drag  pan; click the ruler to jump there
    Space                        play / pause
    Left / Right                 1 s back / forward (Shift: 5 s)
    Up / Down                    playback speed +/- 10 %
    Home / End                   start / end
    M, H, V                      mute, show/hide hands, follow pitch on/off
    D                            difficulty colouring on/off (green easy .. red hard)
    G                            the selected notes are a glissando (again: they're not); "g" marks
                                 glissando notes, the pianist's own and the ones you mark
    Ctrl+O, Ctrl+S (or Ctrl+E)   open another file, export
    Esc with nothing selected    back to the main menu

Exported files are copies of the original with each note's hand and finger
stored as a text event ("R3", "L1", ... "Rg" for a glissando note) right before the note; everything
else in the file is kept as it was. Loading such a file restores the hands
and fingers exactly.
"""
from __future__ import annotations

import math
import os
from dataclasses import replace

import pygame

from common import (blit_shadowed, ACCENT, BAR_BG, BG, FELT_H, FINGER_NAMES, HAND_COLORS, HAND_NAMES,
                    LEAD_IN, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TOP_BAR_H, Button, Dialog,
                    Keyboard, Performance, Transport, bottom_layout, default_export_name,
                    draw_felt, draw_hand_area, draw_pianist_badge, fmt_time, mix, pick_file,
                    run_busy, save_file_dialog)
import pianist as pianists
from fingering import CHORD_TOL, group_notes, mirror_pitch, plan_fingering, score_fingering
from hands import HandAnimator, build_hands, draw_hands, load_with_hands, pair_hands
from version import VERSION
from midi_loader import (HIGHEST_PIANO_KEY, LEFT, LOWEST_PIANO_KEY, RIGHT, MidiSong,
                         is_black_key, is_pig, note_name, save_fingered_midi,
                         save_pig)

INFO_H = 24                  # status line under the top bar
RULER_H = 20                 # bar numbers above the roll
GUTTER_W = 46                # the sideways keyboard at the left of the roll
PLAYHEAD_FRAC = 0.3          # where "now" sits across the roll
VIEW_SECS = 6.0              # seconds shown across the roll
VIEW_MIN, VIEW_MAX = 1.0, 60.0
ROW_H = 12                   # pixels per semitone (default, when the song doesn't fit)
ROW_MIN, ROW_MAX = 10, 30
RESOLVE_PAD = 3.0            # seconds of fixed context around re-fingered notes
DRAG_START = 4               # pixels before a press becomes a drag
UNDO_LIMIT = 200

SEQ_REBUILD_T = 0.35         # sequential mode: redo the hands once typing pauses this long
# sequential fingering keys: hand + finger, laid out like the hands on the keyboard
SEQ_KEYS = {pygame.K_m: (RIGHT, 1), pygame.K_k: (RIGHT, 2), pygame.K_o: (RIGHT, 3),
            pygame.K_SEMICOLON: (RIGHT, 4), pygame.K_QUOTE: (RIGHT, 5),
            pygame.K_v: (LEFT, 1), pygame.K_d: (LEFT, 2), pygame.K_w: (LEFT, 3),
            pygame.K_a: (LEFT, 4), pygame.K_LSHIFT: (LEFT, 5)}
SEQ_DIGITS = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4, pygame.K_5: 5,
              pygame.K_KP1: 1, pygame.K_KP2: 2, pygame.K_KP3: 3, pygame.K_KP4: 4, pygame.K_KP5: 5}

ROW_WHITE = (34, 34, 43)
ROW_BLACK = (27, 27, 34)
ROW_LINE = (40, 40, 50)
ROW_C_LINE = (62, 62, 78)
BEAT_LINE = (40, 40, 52)
BAR_LINE_ROLL = (74, 74, 92)
PLAYHEAD = (240, 240, 245)


def _ctrl(mod):
    return bool(mod & (pygame.KMOD_CTRL | pygame.KMOD_META))


def heat_color(d):
    """0 (easy) green -> 0.5 yellow -> 1 (hard) red; beyond 1 a deeper red."""
    stops = [(0.0, (70, 170, 90)), (0.5, (225, 200, 70)), (1.0, (220, 70, 60)), (1.5, (150, 30, 40))]
    d = max(0.0, min(1.5, d))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if d <= b:
            return mix(ca, cb, (d - a) / (b - a))
    return stops[-1][1]


def difficulty_word(d):
    return ("easy" if d < 0.25 else "moderate" if d < 0.55 else "hard" if d < 1.0 else "very hard") + \
        f" ({int(round(d * 100))})"


# --------------------------------------------------------------------------- #
# Right-click menu (with one level of submenus)
# --------------------------------------------------------------------------- #
class MenuItem:
    def __init__(self, label, value=None, children=None, checked=False, hint=None, enabled=True):
        self.label, self.value, self.children = label, value, children
        self.checked, self.hint, self.enabled = checked, hint, enabled
        self.rect = pygame.Rect(0, 0, 0, 0)


class ContextMenu:
    ITEM_H = 28
    PAD = 8

    def __init__(self, pos, title, items, on_pick, fonts, bounds):
        self.title, self.items, self.on_pick, self.fonts = title, items, on_pick, fonts
        self.bounds = pygame.Rect(bounds)
        self.hover = None
        self.child = None                  # (parent index, ContextMenu) when a submenu is open
        self.rect = self._place(pos)

    def _place(self, pos, flip_from=None):
        f = self.fonts
        w = max([f["normal"].size(it.label)[0] + (f["small"].size(it.hint)[0] + 16 if it.hint else 0)
                 for it in self.items] + [f["small"].size(self.title)[0] if self.title else 0]) + 58
        h = len(self.items) * self.ITEM_H + 2 * self.PAD + (22 if self.title else 0)
        r = pygame.Rect(pos[0], pos[1], w, h)
        if r.right > self.bounds.right:
            r.x = (flip_from - w) if flip_from is not None else self.bounds.right - w
        if r.bottom > self.bounds.bottom:
            r.y = max(self.bounds.y, self.bounds.bottom - h)
        y = r.y + self.PAD + (22 if self.title else 0)
        for it in self.items:
            it.rect = pygame.Rect(r.x + 4, y, r.w - 8, self.ITEM_H)
            y += self.ITEM_H
        return r

    def contains(self, pos):
        return self.rect.collidepoint(pos) or (self.child and self.child[1].contains(pos))

    def _open_child(self, i):
        it = self.items[i]
        if not it.children:
            self.child = None
            return
        if self.child and self.child[0] == i:
            return
        sub = ContextMenu((self.rect.right - 2, it.rect.y - self.PAD - 22), "Finger", it.children,
                          None, self.fonts, self.bounds)
        if sub.rect.x < self.rect.right - 2:          # no room on the right: open to the left
            sub.rect = sub._place((self.rect.x - sub.rect.w + 2, it.rect.y - self.PAD - 22),
                                  flip_from=self.rect.x + 2)
        self.child = (i, sub)

    def motion(self, pos):
        if self.child and self.child[1].rect.collidepoint(pos):
            self.child[1].motion(pos)
            return
        self.hover = None
        for i, it in enumerate(self.items):
            if it.enabled and it.rect.collidepoint(pos):
                self.hover = i
                if it.children:
                    self._open_child(i)
                elif self.child:
                    self.child = None

    def click(self, pos):
        """Returns True if the menu is done (something was picked, or the click missed)."""
        if self.child and self.child[1].rect.collidepoint(pos):
            sub = self.child[1]
            for it in sub.items:
                if it.enabled and it.rect.collidepoint(pos):
                    self.on_pick(self.items[self.child[0]].value, it.value)
                    return True
            return False
        for i, it in enumerate(self.items):
            if it.enabled and it.rect.collidepoint(pos):
                if it.children:
                    self._open_child(i)
                    return False
                self.on_pick(it.value, None)
                return True
        return not self.rect.collidepoint(pos)

    def key(self, key):
        """Keyboard shortcuts inside the menu: R/L pick a hand, 1-5 a finger."""
        hand = {pygame.K_r: RIGHT, pygame.K_l: LEFT}.get(key)
        if hand is not None:
            for i, it in enumerate(self.items):
                if it.value == hand:
                    if it.children:
                        self.hover = i
                        self._open_child(i)
                        return False
                    self.on_pick(hand, None)
                    return True
        finger = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4, pygame.K_5: 5}.get(key)
        if finger and self.child:
            self.on_pick(self.items[self.child[0]].value, finger)
            return True
        return False

    def draw(self, surf, mouse):
        f = self.fonts
        shadow = pygame.Surface((self.rect.w + 8, self.rect.h + 8), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 70))
        surf.blit(shadow, (self.rect.x + 2, self.rect.y + 4))
        pygame.draw.rect(surf, PANEL, self.rect, border_radius=8)
        pygame.draw.rect(surf, PANEL_EDGE, self.rect, 1, border_radius=8)
        if self.title:
            img = f["small"].render(self.title, True, TEXT_DIM)
            surf.blit(img, (self.rect.x + 14, self.rect.y + self.PAD + 2))
        for i, it in enumerate(self.items):
            open_ = self.child is not None and self.child[0] == i
            if (it.rect.collidepoint(mouse) or open_) and it.enabled:
                pygame.draw.rect(surf, mix(PANEL, ACCENT, 0.28), it.rect, border_radius=5)
            color = TEXT if it.enabled else TEXT_DIM
            if it.checked:
                pygame.draw.circle(surf, ACCENT, (it.rect.x + 12, it.rect.centery), 4)
            img = f["normal"].render(it.label, True, color)
            surf.blit(img, img.get_rect(midleft=(it.rect.x + 26, it.rect.centery)))
            if it.hint:
                h = f["small"].render(it.hint, True, TEXT_DIM)
                surf.blit(h, h.get_rect(midright=(it.rect.right - (22 if it.children else 10),
                                                  it.rect.centery)))
            if it.children:
                x, y = it.rect.right - 12, it.rect.centery
                pygame.draw.polygon(surf, color, [(x - 4, y - 5), (x - 4, y + 5), (x + 2, y)])
        if self.child:
            self.child[1].draw(surf, mouse)


# --------------------------------------------------------------------------- #
# The editor
# --------------------------------------------------------------------------- #
class FingeringEditor(Transport):
    def __init__(self, app, song, hands=None):
        self.app = app
        self.screen = app.screen
        self.fonts = app.fonts
        self._init_transport(app.midi, app.speed)
        self.view_secs = VIEW_SECS
        self.row_h = ROW_H
        self.pitch_top = 84.0
        self.follow_pitch = True
        self.show_hands = True
        self.selection = set()             # note indices
        self.user_set = set()              # notes whose finger was set by hand, this session
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.menu = None
        self.dialog = None
        self._after_dialog = None
        self.press = None                  # what the left/middle button is doing
        self.hover = None
        self.message, self.message_age = "", 0.0
        self._drawn = []                   # [(rect, note index)] from the last frame
        self._label_cache = {}
        self.show_difficulty = False
        self.difficulty = None             # per note: 0 easy .. 1 hard (and above), when computed
        self.seq = None                    # sequential fingering: {"steps", "k", "j"} while on
        self._rebuild_due = None           # time left before a deferred _rebuild
        self.top_buttons = [Button("Follow pitch", "follow", font="small"),
                            Button("Difficulty", "difficulty", font="small"),
                            Button("Open…", "open", font="small"),
                            Button("Export…", "export", font="small"),
                            Button("Menu", "menu", font="small")]
        self.layout(self.screen.get_size())
        self.load_song(song, hands)

    # ----- song and fingering state ----------------------------------------
    def load_song(self, song, hands=None):
        """Edit `song`; `hands`, when already built for it (build_hands with repair=False), saves planning them again."""
        self.midi.silence()
        self.song = song
        self.notes = list(song.notes)
        # the editor shows the file's fingering as it is, even where it can't be played
        planned = hands if hands is not None else build_hands(song, repair=False)
        self.finger = [planned[n.hand].finger_for(n) if n.hand in planned else None
                       for n in self.notes]
        self.hands = planned
        self.difficulty = None
        self.perf = Performance.from_animators(planned.values()) if planned else None
        self.index = {id(n): i for i, n in enumerate(self.notes)}
        self.selection.clear()
        self.seq = None
        self._rebuild_due = None
        self.user_set.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        first = self.notes[0].start if self.notes else 0.0
        self.t = max(-LEAD_IN, first - 0.5)
        self.paused = True
        self.sounding = {}
        self._fit_rows()
        pygame.display.set_caption(f"Piano Animator {VERSION} - editing {song.title}")

    def _rebuild(self):
        """After an edit: a new song object (hands changed) and fresh hand animators."""
        self._rebuild_due = None
        old = self.song
        self.song = MidiSong(self.notes, old.tracks, old.duration, old.bar_times,
                             old.beat_times, old.path, old.raw_controls)
        self.notes = self.song.notes            # same order: sorting is stable
        self.index = {id(n): i for i, n in enumerate(self.notes)}
        fing = {id(n): f for n, f in zip(self.notes, self.finger) if f}
        self.hands = {h: HandAnimator(self.song, h, fingering=fing) for h in (RIGHT, LEFT)
                      if any(n.hand == h for n in self.notes)}
        pair_hands(self.hands.values())
        self.perf = Performance.from_animators(self.hands.values()) if self.hands else None
        self.difficulty = None
        self.seek(self.t)
        self.dirty = True

    # ----- difficulty -------------------------------------------------------
    def toggle_difficulty(self):
        self.show_difficulty = not self.show_difficulty
        if self.show_difficulty:
            self._ensure_difficulty()
            self.say("Difficulty: green is easy, red is hard for this pianist's hand at this tempo "
                     "(the stripe shows the hand)")

    def _ensure_difficulty(self):
        """Per-note difficulty of the current fingering (recomputed after edits)."""
        if self.difficulty is not None:
            return self.difficulty
        raw = [0.0] * len(self.notes)
        for hand in (RIGHT, LEFT):
            idx = [i for i, n in enumerate(self.notes) if n.hand == hand and self.finger[i]]
            if not idx:
                continue
            ns = [self.notes[i] for i in idx]
            vp = mirror_pitch if hand == LEFT else None
            fixed = {id(self.notes[i]): self.finger[i] for i in idx}
            costs = score_fingering(group_notes(ns, vpitch=vp), fixed, vp, hand, context=self.notes)
            for i in idx:
                raw[i] = costs.get(id(self.notes[i]), 0.0)
        # scale so that the hardest tenth of the piece reads as red
        nz = sorted(c for c in raw if c > 0)
        ref = nz[int(0.9 * (len(nz) - 1))] if nz else 1.0
        ref = max(ref, 1.0)
        self.difficulty = [min(1.5, c / ref) for c in raw]
        return self.difficulty

    def _snapshot(self, idxs):
        return [(i, self.notes[i].hand, self.finger[i], i in self.user_set, self.notes[i].gliss) for i in idxs]

    def _restore(self, snap):
        for i, hand, finger, user, gliss in snap:
            if self.notes[i].hand != hand or self.notes[i].gliss != gliss:
                self.notes[i] = replace(self.notes[i], hand=hand, gliss=gliss)
                self.index[id(self.notes[i])] = i
            self.finger[i] = finger
            (self.user_set.add if user else self.user_set.discard)(i)

    def _push_undo(self, idxs, label):
        self.undo_stack.append((label, self._snapshot(idxs)))
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def undo(self, redo=False):
        src, dst = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not src:
            self.say("Nothing to redo" if redo else "Nothing to undo")
            return
        label, snap = src.pop()
        dst.append((label, self._snapshot([s[0] for s in snap])))
        self._restore(snap)
        self._rebuild()
        self.say(("Redid: " if redo else "Undid: ") + label)

    def set_note(self, i, hand, finger, rebuild=True):
        """One note: this hand, this finger (kept exactly as given). With
        rebuild=False the hands are redone a moment later (sequential mode)."""
        n = self.notes[i]
        if n.hand == hand and self.finger[i] == finger and not n.gliss:
            self.user_set.add(i)
            return
        self._push_undo([i], f"{note_name(n.pitch)} → {HAND_NAMES[hand].lower()}, finger {finger}")
        self.notes[i] = replace(n, hand=hand, gliss=False)      # a finger of its own: not slid
        self.index[id(self.notes[i])] = i      # (self.notes is the song's own list)
        self.finger[i] = finger
        self.user_set.add(i)
        if rebuild:
            self._rebuild()
        else:
            self.dirty = True
            self._rebuild_due = SEQ_REBUILD_T
        self.say(f"{note_name(n.pitch)} at {fmt_time(n.start, True)}: "
                 f"{HAND_NAMES[hand].lower()}, finger {finger} ({FINGER_NAMES[finger].lower()})")

    def is_gliss(self, i):
        """Is note i slid in a glissando (marked in the file, or found by the pianist)?"""
        n = self.notes[i]
        return n.gliss or (n.hand in self.hands and self.hands[n.hand].is_gliss(n))

    def gliss_candidates(self, idxs):
        """The notes idxs in playing order if they can be marked as one glissando, else None."""
        import glissando
        ns = sorted((self.notes[i] for i in idxs), key=lambda n: (n.start, n.pitch))
        if len({n.hand for n in ns}) != 1:
            return None
        order = glissando._ordered(ns)
        return order if glissando.is_string(order) else None

    def set_gliss(self, idxs, on):
        """Mark the notes idxs as one glissando (or unmark them)."""
        idxs = sorted(idxs)
        self._push_undo(idxs, f"{len(idxs)} notes → {'glissando' if on else 'not a glissando'}")
        for i in idxs:
            self.notes[i] = replace(self.notes[i], gliss=on)
            self.index[id(self.notes[i])] = i
            if on:
                self.user_set.add(i)
            else:
                self.user_set.discard(i)
                self.finger[i] = None
        if not on:
            self._resolve(idxs, self.notes[idxs[0]].hand)
        self._rebuild()
        self.say(f"{len(idxs)} notes marked as a glissando" if on else
                 f"{len(idxs)} notes are no longer a glissando; fingering worked out for them")

    def set_hand(self, idxs, hand):
        """Several notes: move them to one hand and let the planner finger them."""
        idxs = sorted(idxs)
        if not idxs:
            return
        self._push_undo(idxs, f"{len(idxs)} notes → {HAND_NAMES[hand].lower()}")
        for i in idxs:
            if self.notes[i].hand != hand:
                self.notes[i] = replace(self.notes[i], hand=hand)
            self.finger[i] = None
            self.user_set.discard(i)
        self._resolve(idxs, hand)
        self._rebuild()
        self.say(f"{len(idxs)} notes → {HAND_NAMES[hand].lower()}; fingering worked out for them")

    def _resolve(self, idxs, hand):
        """Finger the notes `idxs` (all now in `hand`), keeping every other finger as it is."""
        free = set(idxs)
        t0 = min(self.notes[i].start for i in idxs) - RESOLVE_PAD
        t1 = max(self.notes[i].end for i in idxs) + RESOLVE_PAD
        tmp, back = [], {}
        for i, n in enumerate(self.notes):
            if n.hand != hand or n.end < t0 or n.start > t1:
                continue
            m = replace(n, finger=None if i in free else self.finger[i])
            tmp.append(m)
            back[id(m)] = i
        vp = mirror_pitch if hand == LEFT else None
        result = plan_fingering(group_notes(tmp, vpitch=vp), vp, hand=hand, context=self.notes)
        for m in tmp:
            i = back[id(m)]
            if i in free:
                self.finger[i] = result.get(id(m))

    # ----- export / open ----------------------------------------------------
    # ----- sequential fingering -------------------------------------------------
    def start_sequential(self, i):
        """Finger note after note from note i on, by key (see the module docstring)."""
        # only the hand of the note it starts from: the other hand's notes are skipped
        hand = self.notes[i].hand
        order = sorted((j for j in range(len(self.notes)) if self.notes[j].hand == hand),
                       key=lambda j: (self.notes[j].start, -self.notes[j].pitch))
        steps, cur = [], []
        for j in order:
            if cur and self.notes[j].start - self.notes[cur[0]].start > CHORD_TOL:
                steps.append(cur)
                cur = []
            cur.append(j)                                  # highest first within a chord
        if cur:
            steps.append(cur)
        k = next(k for k, st in enumerate(steps) if i in st)
        self.seq = {"steps": steps, "k": k, "j": steps[k].index(i), "hand": hand}
        self.paused = True
        self.midi.silence()
        self._seq_show()
        self.say(f"Sequential fingering, {HAND_NAMES[hand].lower()}: 1-5 · right hand M K O ; ' · "
                 "left hand V D W A LShift · Backspace back · Tab skip · Esc done")

    def seq_note(self):
        q = self.seq
        return q["steps"][q["k"]][q["j"]] if q else None

    def stop_sequential(self, why=None):
        if self.seq:
            self.seq = None
            self._flush()
            self.say(why or "Sequential fingering finished")

    def _seq_show(self):
        """Bring the current note into view: playhead on its chord, selected."""
        i = self.seq_note()
        self.selection = {i}
        n = self.notes[i]
        self.seek(n.start + 0.01)
        if self.follow_pitch:
            self._follow(1.0)
        if not (self.u_of(self.roll_rect.bottom) + 1 <= n.pitch <= self.pitch_top - 1):
            self.pitch_top = n.pitch + self._rows_visible() / 2
            self._clamp_pitch()

    def _seq_goto(self, i):
        """Go to note i, if it's in the sequence (an undo can reach a note of the other hand: then stay)."""
        q = self.seq
        k = next((k for k, st in enumerate(q["steps"]) if i in st), None)
        if k is not None:
            q["k"], q["j"] = k, q["steps"][k].index(i)
        self._seq_show()

    def _seq_step(self, d):
        """Move d notes along (chords count note by note, top to bottom)."""
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

    def _seq_key(self, event):
        """Keys while sequential fingering is on; False = not ours (handle as usual)."""
        k, mod = event.key, getattr(event, "mod", 0)
        if _ctrl(mod):
            if k in (pygame.K_z, pygame.K_y):         # undo / redo, and go to that note
                redo = k == pygame.K_y or bool(mod & pygame.KMOD_SHIFT)
                stack = self.redo_stack if redo else self.undo_stack
                target = stack[-1][1][0][0] if stack and stack[-1][1] else None
                self.undo(redo=redo)
                if self.seq and target is not None:
                    self._seq_goto(target)
                return True
            return False
        if k in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.stop_sequential()
            return True
        i = self.seq_note()
        if k in SEQ_KEYS:
            hand, f = SEQ_KEYS[k]
        elif k in SEQ_DIGITS:
            hand, f = self.notes[i].hand, SEQ_DIGITS[k]
        elif k == pygame.K_TAB:
            self._seq_step(-1 if mod & pygame.KMOD_SHIFT else 1)
            return True
        elif k == pygame.K_BACKSPACE:
            self._seq_step(-1)
            return True
        else:
            return False
        self.set_note(i, hand, f, rebuild=False)
        n = self.notes[i]
        self.say(f"{note_name(n.pitch)}: {HAND_NAMES[hand].lower()} {f} ({FINGER_NAMES[f].lower()})")
        self._seq_step(1)
        return True

    def _flush(self):
        if self._rebuild_due is not None:
            self._rebuild()

    def export(self):
        self._flush()
        src_pig = is_pig(self.song.path)
        if not src_pig and (not self.song.path or not os.path.exists(self.song.path)):
            self.say("Can't export: the original MIDI file is no longer where it was loaded from")
            return False
        path = save_file_dialog(initialdir=os.path.dirname(os.path.abspath(self.song.path or ".")),
                                initialfile=default_export_name(self.song.path), pig_only=src_pig)
        self.press = None
        if not path:
            return False
        try:
            fing = {id(n): ("g" if n.gliss else f) for n, f in zip(self.notes, self.finger)}
            if src_pig or is_pig(path):
                # PIG text: the notes as they are, with each note's hand and finger
                count = save_pig(path if is_pig(path) else os.path.splitext(path)[0] + ".txt",
                                 self.notes, fing)
            else:
                count = save_fingered_midi(self.song.path, path, self.notes, fing)
        except Exception as exc:
            self.say(f"Export failed: {exc}")
            return False
        self.dirty = False
        missing = len(self.notes) - count
        self.say(f"Exported to {path}" + (f"  ({missing} notes couldn't be matched)" if missing > 0 else ""))
        return True

    def _open_other(self):
        path = pick_file("Open MIDI file to edit",
                         initialdir=os.path.dirname(os.path.abspath(self.song.path)) if self.song.path else None)
        self.press = None
        return self._load_path(path) if path else True

    def _load_path(self, path):
        try:
            song, hands = run_busy(self.screen, self.fonts, f"Loading {os.path.basename(path)}…",
                                   lambda: load_with_hands(path, repair=False))
            self.load_song(song, hands)
            self.app.last_dir = os.path.dirname(os.path.abspath(path))
        except Exception as exc:
            self.say(f"Could not open {os.path.basename(path)}: {exc}")
        return True

    def _guard(self, then):
        """Run `then` now, or after asking what to do with unsaved changes."""
        self.seq = None
        self._flush()
        if not self.dirty:
            return then()
        self.dialog = Dialog("Unsaved fingering changes", "Export them before you go?",
                             [("Export…", "export"), ("Discard", "discard"), ("Cancel", "cancel")])
        self._after_dialog = then
        return True

    def say(self, text):
        self.message, self.message_age = text, 0.0

    def idle(self, budget):
        """The frame's spare time (App.run): the hands work ahead (HandAnimator.prepare)."""
        if self.hands and not self._rebuild_due:
            from hands import prepare_hands
            prepare_hands(self.hands.values(), self.t, self.keyboard, budget)

    def leave(self):
        self.midi.silence()                     # (with the pedal down, notes would ring on)
        self.app.speed = self.speed

    # ----- geometry ---------------------------------------------------------
    def layout(self, size):
        w, h = size
        kb_rect, self.hand_rect = bottom_layout(size)
        self.bar_rect = pygame.Rect(0, 0, w, TOP_BAR_H)
        self.info_rect = pygame.Rect(0, TOP_BAR_H, w, INFO_H)
        top = TOP_BAR_H + INFO_H
        self.ruler_rect = pygame.Rect(GUTTER_W, top, w - GUTTER_W, RULER_H)
        roll_top = top + RULER_H
        roll_h = kb_rect.y - FELT_H - roll_top
        self.roll_rect = pygame.Rect(GUTTER_W, roll_top, w - GUTTER_W, max(40, roll_h))
        self.gutter_rect = pygame.Rect(0, roll_top, GUTTER_W, self.roll_rect.h)
        self.felt_rect = pygame.Rect(0, kb_rect.y - FELT_H, w, FELT_H)
        if hasattr(self, "keyboard"):
            self.keyboard.layout(kb_rect)
        else:
            self.keyboard = Keyboard(kb_rect)
        x = w - 8
        for b in reversed(self.top_buttons):
            bw = self.fonts["small"].size(b.label)[0] + 24
            b.rect = pygame.Rect(x - bw, 5, bw, TOP_BAR_H - 10)
            x -= bw + 6
        self._buttons_left = x

    @property
    def pps(self):
        return self.roll_rect.w / self.view_secs

    @property
    def playhead_x(self):
        return self.roll_rect.x + int(self.roll_rect.w * PLAYHEAD_FRAC)

    def x_of(self, t):
        return self.playhead_x + (t - self.t) * self.pps

    def t_of(self, x):
        return self.t + (x - self.playhead_x) / self.pps

    def y_of(self, pitch):
        """Top edge of a pitch's row."""
        return self.roll_rect.y + (self.pitch_top - pitch) * self.row_h

    def u_of(self, y):
        """Continuous pitch coordinate at a y (row p spans u in [p-1, p])."""
        return self.pitch_top - (y - self.roll_rect.y) / self.row_h

    def _rows_visible(self):
        return self.roll_rect.h / self.row_h

    def _clamp_pitch(self):
        rows = self._rows_visible()
        hi = HIGHEST_PIANO_KEY + 1.0
        lo = LOWEST_PIANO_KEY - 1.0 + rows
        self.pitch_top = max(min(self.pitch_top, hi), min(lo, hi))

    def _fit_rows(self):
        """Fit the song's range if it fits at a readable row height; else follow the music."""
        lo, hi = self.song.pitch_range() if self.notes else (48, 72)
        span = hi - lo + 3
        if span * ROW_H <= self.roll_rect.h:
            self.row_h = min(ROW_MAX, self.roll_rect.h / span)
            self.pitch_top = hi + 1.5
            self.follow_pitch = False
        else:
            self.row_h = ROW_H
            self.follow_pitch = True
            self._follow(1.0)
        self._clamp_pitch()

    def _follow(self, amount):
        """Move the view toward the notes around the playhead."""
        left = self.t - self.view_secs * PLAYHEAD_FRAC
        right = self.t + self.view_secs * (1 - PLAYHEAD_FRAC)
        ns = self.song.notes_between(left, right)
        if not ns:
            return
        rows = self._rows_visible()
        lo, hi = min(n.pitch for n in ns), max(n.pitch for n in ns)
        if hi - lo + 2 > rows:                    # too wide: centre on what's near "now"
            near = self.song.notes_between(self.t - 1.0, self.t + 1.5) or ns
            lo, hi = min(n.pitch for n in near), max(n.pitch for n in near)
        target = (lo + hi) / 2 + rows / 2
        self.pitch_top += (target - self.pitch_top) * amount
        self._clamp_pitch()

    def _note_rect(self, n):
        x0 = self.x_of(n.start)
        x1 = self.x_of(n.end)
        y = self.y_of(n.pitch)
        return pygame.Rect(int(x0), int(y) + 1, max(3, int(x1) - int(x0) - 1), max(2, int(self.row_h) - 1))

    def note_at(self, pos):
        for rect, i in reversed(self._drawn):
            if rect.collidepoint(pos):
                return i
        return None

    def _notes_in_band(self, ta, tb, ua, ub):
        ta, tb = min(ta, tb), max(ta, tb)
        ua, ub = min(ua, ub), max(ua, ub)
        out = set()
        for n in self.song.notes_between(ta, tb):
            if n.start < tb and n.end > ta and n.pitch - 1 < ub and n.pitch > ua:
                out.add(self.index[id(n)])
        return out

    # ----- input ------------------------------------------------------------
    def handle_event(self, event):
        if event.type == pygame.VIDEORESIZE:          # (also under a dialog: the layout must follow the window)
            self.screen = pygame.display.get_surface() or self.screen
            self.layout(self.screen.get_size())
            self.menu = None
            return True
        if self.dialog:
            if event.type == pygame.QUIT:
                return False
            self.dialog.handle_event(event)
            choice = self.dialog.choice
            if choice:
                self.dialog, then = None, self._after_dialog
                if choice == "export" and self.export():
                    return then()
                if choice == "discard":
                    self.dirty = False
                    return then()
            return True

        if event.type == pygame.QUIT:
            return self._guard(lambda: False)
        if event.type == pygame.DROPFILE:
            path = event.file
            return self._guard(lambda: self._load_path(path))

        if self.menu:
            if event.type == pygame.MOUSEMOTION:
                self.menu.motion(event.pos)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
                inside = self.menu.contains(event.pos)
                if self.menu.click(event.pos):
                    self.menu = None
                    if not inside and event.button == 3 and self.roll_rect.collidepoint(event.pos):
                        self._context_menu(event.pos)      # right-click elsewhere: a new menu there
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE or self.menu.key(event.key):
                    self.menu = None
            return True

        if event.type == pygame.KEYDOWN:
            return self._key(event)
        if event.type == pygame.MOUSEWHEEL:
            self._wheel(event)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            return self._mouse_down(event)
        elif event.type == pygame.MOUSEMOTION:
            self._mouse_move(event)
        elif event.type == pygame.MOUSEBUTTONUP:
            self._mouse_up(event)
        return True

    def _key(self, event):
        if self.seq and self._seq_key(event):
            return True
        k, mod = event.key, getattr(event, "mod", 0)
        ctrl, shift = _ctrl(mod), bool(mod & pygame.KMOD_SHIFT)
        if k == pygame.K_ESCAPE:
            if self.selection:
                self.selection.clear()
                return True
            return self._guard(lambda: "menu")
        if ctrl:
            if k == pygame.K_a:
                self.selection = set(range(len(self.notes)))
                self.say(f"All {len(self.notes)} notes selected")
            elif k == pygame.K_z:
                self.undo(redo=shift)
            elif k == pygame.K_y:
                self.undo(redo=True)
            elif k in (pygame.K_s, pygame.K_e):
                self.export()
            elif k == pygame.K_o:
                return self._guard(self._open_other)
            return True
        if k == pygame.K_SPACE:
            self.toggle_pause()
        elif k == pygame.K_LEFT:
            self.seek(self.t - (5.0 if shift else 1.0))
        elif k == pygame.K_RIGHT:
            self.seek(self.t + (5.0 if shift else 1.0))
        elif k == pygame.K_UP:
            self.change_speed(0.1)
        elif k == pygame.K_DOWN:
            self.change_speed(-0.1)
        elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self._zoom_time(1 / 1.25)
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self._zoom_time(1.25)
        elif k == pygame.K_HOME:
            self.seek(-LEAD_IN)
        elif k == pygame.K_END:
            self.seek(self.song.duration)
        elif k == pygame.K_m:
            self.midi.muted = not self.midi.muted
            if self.midi.muted:
                self.midi.silence()
        elif k == pygame.K_h:
            self.show_hands = not self.show_hands
        elif k == pygame.K_d:
            self.toggle_difficulty()
        elif k == pygame.K_v:
            self.follow_pitch = not self.follow_pitch
        elif k in (pygame.K_r, pygame.K_l) and self.selection:
            self.set_hand(self.selection, RIGHT if k == pygame.K_r else LEFT)
        elif k == pygame.K_g and self.selection:
            sel = sorted(self.selection)
            if all(self.notes[j].gliss for j in sel):
                self.set_gliss(sel, False)
            elif len(sel) > 1 and self.gliss_candidates(sel):
                self.set_gliss(sel, True)
            else:
                self.say("A glissando is a string of next-door white keys (or black keys) going one way, "
                         "in one hand - at least 3 notes")
        elif k in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            if len(self.selection) == 1:
                i = next(iter(self.selection))
                self.set_note(i, self.notes[i].hand, k - pygame.K_0)
            elif self.selection:
                self.say("A finger can only be set on one note at a time; use R / L to re-finger a group")
        return True

    def _zoom_time(self, factor, anchor_x=None):
        # keep the time under the mouse where it is (the playhead stays put)
        anchor_x = self.playhead_x if anchor_x is None else anchor_x
        ta = self.t_of(anchor_x)
        self.view_secs = max(VIEW_MIN, min(VIEW_MAX, self.view_secs * factor))
        if anchor_x != self.playhead_x:
            self.seek(ta - (anchor_x - self.playhead_x) / self.pps)

    def _wheel(self, event):
        mod = pygame.key.get_mods()
        dy = getattr(event, "y", 0)
        dx = getattr(event, "x", 0)
        mouse = pygame.mouse.get_pos()
        if _ctrl(mod):
            self._zoom_time(0.8 ** dy, mouse[0] if self.roll_rect.collidepoint(mouse) else None)
        elif mod & pygame.KMOD_ALT:
            u = self.u_of(mouse[1])
            self.row_h = max(ROW_MIN, min(ROW_MAX, self.row_h + dy))
            self.pitch_top = u + (mouse[1] - self.roll_rect.y) / self.row_h
            self._clamp_pitch()
        elif mod & pygame.KMOD_SHIFT or self.gutter_rect.collidepoint(mouse):
            self.follow_pitch = False
            self.pitch_top += 3 * dy
            self._clamp_pitch()
        else:
            # wheel up = back in time (like scrolling a document up)
            self.seek(self.t - dy * self.view_secs * 0.08 + dx * self.view_secs * 0.08)

    def _mouse_down(self, event):
        pos, b = event.pos, event.button
        mod = pygame.key.get_mods()
        if b in (4, 5):                     # old-style wheel events (pygame 1)
            return True
        if b == 1:
            for btn in self.top_buttons:
                if btn.hit(pos):
                    return self._button(btn.action)
            if self.ruler_rect.collidepoint(pos):
                self.press = {"kind": "ruler", "pos": pos, "t": self.t, "moved": False}
            elif (self.roll_rect.collidepoint(pos) and self.seq and self.note_at(pos) is not None
                  and any(self.note_at(pos) in st for st in self.seq["steps"])):
                self._seq_goto(self.note_at(pos))     # sequential mode: carry on from this note
            elif self.roll_rect.collidepoint(pos):
                i = self.note_at(pos)
                self.press = {"kind": "band", "pos": pos, "note": i, "moved": False,
                              "add": bool(mod & pygame.KMOD_SHIFT) or _ctrl(mod),
                              "start": (self.t_of(pos[0]), self.u_of(pos[1])),
                              "before": set(self.selection)}
            elif self.gutter_rect.collidepoint(pos):
                self.press = {"kind": "pan", "pos": pos, "t": self.t, "top": self.pitch_top,
                              "moved": False, "time": False}
        elif b == 2 and self.roll_rect.collidepoint(pos):
            self.press = {"kind": "pan", "pos": pos, "t": self.t, "top": self.pitch_top,
                          "moved": False, "time": True}
        elif b == 3 and (self.roll_rect.collidepoint(pos)):
            self._context_menu(pos)
        return True

    def _button(self, action):
        if action == "open":
            return self._guard(self._open_other)
        if action == "export":
            self.export()
        elif action == "menu":
            return self._guard(lambda: "menu")
        elif action == "difficulty":
            self.toggle_difficulty()
        elif action == "follow":
            self.follow_pitch = not self.follow_pitch
        return True

    def _mouse_move(self, event):
        pos = event.pos
        self.hover = self.note_at(pos) if self.roll_rect.collidepoint(pos) else None
        p = self.press
        if not p:
            return
        dx, dy = pos[0] - p["pos"][0], pos[1] - p["pos"][1]
        if abs(dx) + abs(dy) >= DRAG_START:
            p["moved"] = True
        if not p["moved"]:
            return
        if p["kind"] == "ruler":
            self.seek(p["t"] - dx / self.pps)
        elif p["kind"] == "pan":
            if p["time"]:
                self.seek(p["t"] - dx / self.pps)
            self.follow_pitch = False
            self.pitch_top = p["top"] + dy / self.row_h
            self._clamp_pitch()
        elif p["kind"] == "band":
            self._update_band(pos)

    def _update_band(self, pos):
        p = self.press
        ta, ua = p["start"]
        x = max(self.roll_rect.x, min(self.roll_rect.right - 1, pos[0]))
        y = max(self.roll_rect.y, min(self.roll_rect.bottom - 1, pos[1]))
        inside = self._notes_in_band(ta, self.t_of(x), ua, self.u_of(y))
        self.selection = (p["before"] | inside) if p["add"] else inside

    def _mouse_up(self, event):
        if event.button not in (1, 2):
            return
        p, self.press = self.press, None
        if not p:
            return
        if p["kind"] == "ruler" and not p["moved"]:
            self.seek(self.t_of(event.pos[0]))
        elif p["kind"] == "band" and not p["moved"]:
            i = p["note"]
            if i is None:
                if not p["add"]:
                    self.selection.clear()
            elif p["add"]:
                self.selection ^= {i}
            else:
                self.selection = {i}
        elif p["kind"] == "band":
            n = len(self.selection)
            if n:
                self.say(f"{n} note{'s' if n != 1 else ''} selected - right-click one of them to choose the hand")

    def _context_menu(self, pos):
        i = self.note_at(pos)
        if i is None:
            if not self.selection:
                return
            if len(self.selection) == 1:
                i = next(iter(self.selection))
        elif i not in self.selection:
            self.selection = {i}
        bounds = pygame.Rect(0, 0, *self.screen.get_size())
        if len(self.selection) > 1:
            sel = sorted(self.selection)
            hands = {self.notes[j].hand for j in sel}
            title = f"{len(sel)} notes selected"
            items = [MenuItem(HAND_NAMES[h], h, checked=hands == {h}, hint=k)
                     for h, k in ((RIGHT, "R"), (LEFT, "L"))]
            if all(self.notes[j].gliss for j in sel):
                items.append(MenuItem("Not a glissando", "nogliss", hint="g"))
            elif self.gliss_candidates(sel):
                items.append(MenuItem("Glissando", "gliss", hint="g"))

            def pick_many(h, _f):
                if h in ("gliss", "nogliss"):
                    self.set_gliss(sel, h == "gliss")
                else:
                    self.set_hand(sel, h)
            self.menu = ContextMenu(pos, title, items, pick_many, self.fonts, bounds)
            return
        n = self.notes[i]
        cur_f = self.finger[i]
        items = []
        for h, k in ((RIGHT, "R"), (LEFT, "L")):
            fingers = [MenuItem(f"{f}  {FINGER_NAMES[f]}", f, checked=(h == n.hand and f == cur_f),
                                hint=str(f)) for f in range(1, 6)]
            items.append(MenuItem(HAND_NAMES[h], h, children=fingers, checked=(h == n.hand), hint=k))
        items.append(MenuItem("Sequential fingering from here", "seq", hint="↵"))
        if n.gliss:
            items.append(MenuItem("Not a glissando (the whole glissando)", "nogliss", hint="g"))
        title = f"{note_name(n.pitch)} at {fmt_time(n.start, True)}"

        def pick(h, f):
            if h == "nogliss":
                import glissando
                same = [j for j, m in enumerate(self.notes) if m.hand == n.hand]
                run = next((r for r in glissando.explicit([self.notes[j] for j in same])
                            if any(m is n for m in r)), [n])
                self.set_gliss([self.index[id(m)] for m in run], False)
            elif h == "seq":
                self.start_sequential(i)
            elif f:
                self.set_note(i, h, f)
        self.menu = ContextMenu(pos, title, items, pick, self.fonts, bounds)

    # ----- per frame --------------------------------------------------------
    def update(self, dt):
        if self._rebuild_due is not None:
            self._rebuild_due -= dt
            if self._rebuild_due <= 0:
                self._rebuild()
        Transport.update(self, dt)
        self.message_age += dt
        p = self.press
        if p and p["kind"] == "band" and p["moved"]:
            # dragging a selection past the edge scrolls the roll
            x, y = pygame.mouse.get_pos()
            r = self.roll_rect
            if x > r.right - 12:
                self.seek(self.t + self.view_secs * 1.2 * dt)
            elif x < r.x + 12:
                self.seek(self.t - self.view_secs * 1.2 * dt)
            if y < r.y + 8:
                self.follow_pitch = False
                self.pitch_top += 20 * dt
                self._clamp_pitch()
            elif y > r.bottom - 8:
                self.follow_pitch = False
                self.pitch_top -= 20 * dt
                self._clamp_pitch()
            self._update_band((x, y))
        elif self.follow_pitch and not self.menu and not p:
            self._follow(min(1.0, dt * 4.0))

    # ----- drawing ----------------------------------------------------------
    def _label(self, text, font, color):
        key = (text, font, color)
        img = self._label_cache.get(key)
        if img is None:
            img = self._label_cache[key] = self.fonts[font].render(text, True, color)
        return img

    def render(self):
        s = self.screen
        s.fill(BG)
        self._draw_roll(s)
        self._draw_gutter(s)
        self._draw_ruler(s)

        draw_felt(s, self.felt_rect)
        self.keyboard.draw(s, self.keys_down())
        draw_hand_area(s, self.hand_rect)
        if self.hands and self.show_hands:
            s.set_clip(pygame.Rect(0, self.felt_rect.top, s.get_width(), s.get_height()))
            draw_hands(s, [a.pose(self.t, self.keyboard) for a in self.hands.values()])
            s.set_clip(None)
        draw_pianist_badge(s, self.fonts, self.hand_rect, pianists.active(),
                           self.sustain_down() if self.song.controls else None)
        self._draw_top_bar(s)
        self._draw_info(s)
        if self.menu:
            self.menu.draw(s, pygame.mouse.get_pos())
        if self.dialog:
            self.dialog.draw(s, self.fonts)

    def _draw_roll(self, s):
        r = self.roll_rect
        s.set_clip(r)
        rh = self.row_h
        # rows
        p_hi = min(HIGHEST_PIANO_KEY, int(math.ceil(self.pitch_top)))
        p_lo = max(LOWEST_PIANO_KEY, int(math.floor(self.u_of(r.bottom))))
        for p in range(p_lo, p_hi + 1):
            y = int(self.y_of(p))
            pygame.draw.rect(s, ROW_BLACK if is_black_key(p) else ROW_WHITE, (r.x, y, r.w, int(rh) + 1))
            if p % 12 == 0:
                pygame.draw.line(s, ROW_C_LINE, (r.x, int(self.y_of(p - 1))), (r.right, int(self.y_of(p - 1))))
            elif rh >= 10 and p % 12 == 5:
                pygame.draw.line(s, ROW_LINE, (r.x, int(self.y_of(p - 1))), (r.right, int(self.y_of(p - 1))))
        # beats and bars
        t0, t1 = self.t_of(r.x), self.t_of(r.right)
        beats = self.song.beat_times
        if beats and self.pps * (self.song.duration / max(1, len(beats))) >= 14:
            for bt in beats:
                if t0 <= bt <= t1:
                    x = int(self.x_of(bt))
                    pygame.draw.line(s, BEAT_LINE, (x, r.y), (x, r.bottom))
        for bt in self.song.bar_times:
            if t0 <= bt <= t1:
                x = int(self.x_of(bt))
                pygame.draw.line(s, BAR_LINE_ROLL, (x, r.y), (x, r.bottom))

        # notes
        self._drawn = []
        font = "finger" if rh >= 13 else "label"
        diff = self._ensure_difficulty() if self.show_difficulty else None
        perf = self._performance()
        held = {id(n) for _, _, n in perf.active(self.t)} if perf else set()   # what the hands hold now
        for n in self.song.notes_between(t0, t1):
            if n.pitch > self.pitch_top + 1 or n.pitch < self.u_of(r.bottom) - 1:
                continue
            i = self.index[id(n)]
            rect = self._note_rect(n)
            self._drawn.append((rect, i))
            sel = i in self.selection
            color = HAND_COLORS[n.hand][1 if n.is_black else 0]
            if diff is not None:
                color = heat_color(diff[i])
            if id(n) in held:
                color = mix(color, (255, 255, 255), 0.3)
            if sel:
                color = mix(color, (255, 255, 255), 0.25)
            radius = min(4, rect.h // 3, rect.w // 3)
            pygame.draw.rect(s, color, rect, border_radius=radius)
            if diff is not None:
                # the hand, as a stripe down the note's start
                pygame.draw.rect(s, HAND_COLORS[n.hand][0], (rect.x, rect.y, min(4, rect.w), rect.h),
                                 border_top_left_radius=radius, border_bottom_left_radius=radius)
            pygame.draw.rect(s, ACCENT if sel else mix(color, (0, 0, 0), 0.5), rect,
                             2 if sel else 1, border_radius=radius)
            if i == self.hover and not sel:
                pygame.draw.rect(s, (255, 255, 255), rect, 1, border_radius=radius)
        # finger numbers on top, so a long note never hides a short one's label
        for rect, i in self._drawn:
            self._draw_finger(s, rect, i, font)
        # sequential fingering: the chord being fingered, and the note that's next
        if self.seq:
            q = self.seq
            chord = set(q["steps"][q["k"]])
            cur = self.seq_note()
            for rect, i in self._drawn:
                if i == cur:
                    pygame.draw.rect(s, (255, 255, 255), rect.inflate(6, 6), 2, border_radius=5)
                    pygame.draw.rect(s, ACCENT, rect.inflate(2, 2), 2, border_radius=4)
                    tip = [(rect.x - 10, rect.centery - 6), (rect.x - 3, rect.centery),
                           (rect.x - 10, rect.centery + 6)]
                    pygame.draw.polygon(s, ACCENT, tip)
                elif i in chord:
                    pygame.draw.rect(s, (200, 200, 210), rect.inflate(4, 4), 1, border_radius=5)

        # rubber band
        p = self.press
        if p and p["kind"] == "band" and p["moved"]:
            ta, ua = p["start"]
            mx, my = pygame.mouse.get_pos()
            x0, y0 = self.x_of(ta), self.y_of(ua)
            band = pygame.Rect(int(min(x0, mx)), int(min(y0, my)),
                               int(abs(mx - x0)), int(abs(my - y0))).clip(r)
            if band.w > 0 and band.h > 0:
                fill = pygame.Surface(band.size, pygame.SRCALPHA)
                fill.fill((*ACCENT, 40))
                s.blit(fill, band.topleft)
                pygame.draw.rect(s, ACCENT, band, 1)

        # playhead
        x = self.playhead_x
        pygame.draw.line(s, PLAYHEAD, (x, r.y), (x, r.bottom), 1)
        s.set_clip(None)

    def _draw_finger(self, s, rect, i, font):
        f = self.finger[i]
        user = i in self.user_set
        text = str(f) if f else "?"
        if self.is_gliss(i):
            text, user = "g", self.notes[i].gliss          # slid in a glissando (white: marked by you)
        dark = (15, 15, 20)
        if user:
            img = self._label(text, font, (255, 255, 255))
        else:
            img = self._label(text, font, dark)
        w, h = img.get_width(), img.get_height()
        if rect.w >= w + 6 and rect.h >= h - 4 and not user:
            s.blit(img, img.get_rect(midleft=(rect.x + 3, rect.centery)))
            return
        # a tag at the note's start (short notes, and fingers you set yourself)
        tag = pygame.Rect(rect.x, 0, w + 6, min(rect.h + 2, h + 2))
        tag.centery = rect.centery
        base = HAND_COLORS[self.notes[i].hand][0]
        bg = mix(base, (0, 0, 0), 0.45) if user else mix(base, (255, 255, 255), 0.35)
        pygame.draw.rect(s, bg, tag, border_radius=3)
        if user:
            pygame.draw.rect(s, ACCENT, tag, 1, border_radius=3)
        s.blit(img, img.get_rect(center=tag.center))

    def _draw_gutter(self, s):
        g = self.gutter_rect
        pygame.draw.rect(s, (18, 18, 22), g)
        s.set_clip(g)
        sounding = self.keys_down()
        p_hi = min(HIGHEST_PIANO_KEY, int(math.ceil(self.pitch_top)))
        p_lo = max(LOWEST_PIANO_KEY, int(math.floor(self.u_of(g.bottom))))
        for p in range(p_lo, p_hi + 1):
            y = int(self.y_of(p))
            h = max(1, int(self.y_of(p - 1)) - y)
            black = is_black_key(p)
            color = (40, 40, 46) if black else (226, 226, 222)
            if p in sounding:
                color = HAND_COLORS[sounding[p]][1 if black else 0]
            w = int(g.w * 0.62) if black else g.w - 1
            pygame.draw.rect(s, color, (g.x, y, w, h))
            if not black:
                pygame.draw.line(s, (150, 150, 150), (g.x, y + h - 1), (g.x + g.w - 1, y + h - 1))
            if p % 12 == 0 and self.row_h >= 8:
                img = self._label(note_name(p), "label", (60, 60, 70))
                s.blit(img, img.get_rect(midright=(g.right - 4, y + h // 2)))
        s.set_clip(None)
        pygame.draw.line(s, (60, 60, 72), (g.right - 1, g.y), (g.right - 1, g.bottom))

    def _draw_ruler(self, s):
        r = self.ruler_rect
        pygame.draw.rect(s, (36, 36, 46), r)
        pygame.draw.rect(s, (36, 36, 46), (0, r.y, GUTTER_W, r.h))
        s.set_clip(r)
        t0, t1 = self.t_of(r.x), self.t_of(r.right)
        bars = self.song.bar_times
        step = 1
        if len(bars) > 1:
            bar_px = self.pps * (bars[-1] - bars[0]) / (len(bars) - 1)
            while bar_px * step < 34:
                step *= 2
        for k, bt in enumerate(bars):
            if t0 - 5 <= bt <= t1 and k % step == 0:
                x = int(self.x_of(bt))
                pygame.draw.line(s, TEXT_DIM, (x, r.bottom - 7), (x, r.bottom))
                img = self._label(str(k + 1), "label", TEXT_DIM)
                s.blit(img, (x + 3, r.y + 3))
        if self.show_difficulty and self.difficulty:
            # where the hard spots are, along the bottom of the ruler
            for n in sorted(self.song.notes_between(t0, t1), key=lambda n: self.difficulty[self.index[id(n)]]):
                d = self.difficulty[self.index[id(n)]]
                if d < 0.25:
                    continue
                x0, x1 = int(self.x_of(n.start)), int(self.x_of(n.end))
                pygame.draw.rect(s, heat_color(d), (x0, r.bottom - 5, max(2, x1 - x0), 5))
        if not bars:
            # no bar lines in the file (PIG files): mark the seconds instead
            step = next(v for v in (0.5, 1, 2, 5, 10, 30, 60) if v * self.pps >= 60)
            k = math.floor(max(0.0, t0) / step)
            while k * step <= t1:
                x = int(self.x_of(k * step))
                pygame.draw.line(s, TEXT_DIM, (x, r.bottom - 5), (x, r.bottom))
                lab = fmt_time(k * step, True)[:-1] if step < 1 else fmt_time(k * step)
                s.blit(self._label(lab, "label", TEXT_DIM), (x + 3, r.y + 3))
                k += 1
        x = self.playhead_x
        pygame.draw.polygon(s, PLAYHEAD, [(x - 6, r.y + 4), (x + 6, r.y + 4), (x, r.bottom - 1)])
        s.set_clip(None)

    def _draw_top_bar(self, s):
        r = self.bar_rect
        pygame.draw.rect(s, BAR_BG, r)
        self.top_buttons[0].active = self.follow_pitch
        self.top_buttons[1].active = self.show_difficulty
        title = self.song.title + ("  •" if self.dirty else "")
        font = self.fonts["normal"]
        x = 10 + blit_shadowed(s, font, title, TEXT, (10, (r.h - font.get_height()) // 2)) + 18
        state = "Playing" if not self.paused else "Paused"
        info = (f"{state}   {fmt_time(self.t, True)} / {fmt_time(self.song.duration)}    "
                f"speed {int(round(self.speed * 100))}%    view {self.view_secs:.1f}s    {self.midi.status()}")
        font = self.fonts["small"]
        if x + font.size(info)[0] < self._buttons_left:
            blit_shadowed(s, font, info, TEXT_DIM, (x, (r.h - font.get_height()) // 2))
        mouse = pygame.mouse.get_pos()
        for b in self.top_buttons:
            b.draw(s, self.fonts, mouse)

    def _draw_info(self, s):
        r = self.info_rect
        pygame.draw.rect(s, (30, 30, 38), r)
        pygame.draw.line(s, (50, 50, 62), (0, r.bottom - 1), (r.w, r.bottom - 1))
        if self.seq:
            i = self.seq_note()
            n = self.notes[i]
            q = self.seq
            size = len(q["steps"][q["k"]])
            where = f"note {q['j'] + 1} of {size} in this chord (top down)" if size > 1 else "single note"
            last = f"   ·   {self.message}" if self.message and self.message_age < 2.0 else ""
            text = (f"SEQUENTIAL ({HAND_NAMES[q['hand']].lower()})  {note_name(n.pitch)}, {where}   ·   1-5 · "
                    f"R: M K O ; '  · L: V D W A LShift · Tab skip · Backspace back · Esc done{last}")
            color = ACCENT
        elif self.message and self.message_age < 6.0:
            text, color = self.message, ACCENT
        elif self.hover is not None:
            n = self.notes[self.hover]
            f = self.finger[self.hover]
            how = ("glissando" + (" (marked)" if n.gliss else "")) if self.is_gliss(self.hover) else \
                f"finger {f if f else '?'}{' (' + FINGER_NAMES[f].lower() + ')' if f else ''}"
            text = (f"{note_name(n.pitch)}   {HAND_NAMES[n.hand].lower()}, {how}"
                    f"   at {fmt_time(n.start, True)}   length {n.end - n.start:.2f}s   velocity {n.velocity}"
                    + ("   · set by you" if self.hover in self.user_set else "")
                    + (f"   · {difficulty_word(self.difficulty[self.hover])}" if self.difficulty else ""))
            color = TEXT
        elif self.selection:
            k = len(self.selection)
            text = (f"{k} notes selected - right-click to move them to one hand (the fingering is worked out for you)"
                    if k > 1 else "1 note selected - right-click (or press 1-5) to set its finger")
            color = TEXT
        else:
            text = ("Click or drag to select notes · right-click to set hand & finger · wheel scrolls · "
                    "Ctrl+wheel zooms · Shift+wheel moves up/down · Ctrl+Z undo · Ctrl+S export")
            color = TEXT_DIM
        img = self.fonts["small"].render(text, True, color)
        s.blit(img, (10, r.y + (r.h - img.get_height()) // 2))
        legend = self._label("white number = set by you", "label", TEXT_DIM)
        if 10 + img.get_width() + legend.get_width() + 30 < r.w:
            s.blit(legend, legend.get_rect(midright=(r.right - 10, r.centery)))
