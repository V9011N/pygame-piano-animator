"""
hands_panel.py - Hand-thesia's keyboard and animated hands under the piano roll.

The hands play what the roll holds: every note of the piano parts with its own
fingering where it has one, else the planner's suggestion (fingering_ui's
`sugg`, worked out in a worker process), at the cursor - or the playhead while
it plays. They're built again (in a thread: HandAnimator's set-up takes a
while for a long piece) whenever the notes, the timing, the fingering or the
active pianist change; until then the last ones stay on screen.

A small translucent arrow hides the panel (the roll then takes the whole
height, a thin strip with the arrow left at the bottom) and shows it again;
H does the same. The choice is kept (app setting "editor_hands").

On screen the hands go through hands.live_poses, like the player's: quick
while the cursor is dragged along the ruler or after a jump until the exact
pose is worked out (the frames' spare time: idle), and playback waits for
them meanwhile (hands_waiting).
"""
from __future__ import annotations

import threading

import pygame

import pianist as pianists
from common import (FELT_H, Keyboard, Performance, bottom_layout, draw_felt, draw_hand_area,
                    draw_pianist_badge)
from hands import (HOLD_BUDGET, HandAnimator, draw_hands, hands_waiting, live_poses, pair_hands,
                   prepare_hands, settle_heap)
from midi_loader import HIGHEST_PIANO_KEY, LEFT, LOWEST_PIANO_KEY, RIGHT, MidiSong, Note, TrackInfo

from .document import DRUM_CHANNEL
from .smf import MIDI
from .theme import TEXT_DIM, mix, px

STRIP_H = 20                 # the hidden panel's strip at the bottom (its arrow)
ARROW_W, ARROW_H = 46, 16
ARROW_ALPHA, ARROW_HOVER_ALPHA = 105, 215
REBUILD_DEBOUNCE = 0.25      # s after the last change before the hands are built again


def build_song(rows, controls, duration):
    """
    A MidiSong of the hands' notes and {id(note): finger}: rows = [(pitch, start s, end s, velocity, track,
    hand, finger, gliss)], controls = [(time s, controller, value)] (the pedals).
    """
    notes = [Note(p, s, max(e, s + 0.02), max(1, min(127, v)), tr, h, f, g)
             for p, s, e, v, tr, h, f, g in rows if LOWEST_PIANO_KEY <= p <= HIGHEST_PIANO_KEY]
    tracks = sorted({n.track for n in notes})
    infos = [TrackInfo(i, f"Track {i + 1}", 0, sum(1 for n in notes if n.track == i), None) for i in tracks]
    song = MidiSong(notes, infos, duration, [], [], controls=controls)
    return song, {id(n): n.finger for n in song.notes if n.finger}


def build_hands(rows, controls, duration):
    """(song, {hand: HandAnimator}) for build_song's input, paired (hands.pair_hands)."""
    song, fing = build_song(rows, controls, duration)
    anims = {h: HandAnimator(song, h, fingering=fing, repair=False) for h in (RIGHT, LEFT)
             if any(n.hand == h for n in song.notes)}
    pair_hands(anims.values())
    return song, anims


class HandsPanel:
    def __init__(self, mode):
        self.mode = mode
        self.shown = bool(pianists.app_setting("editor_hands", True))
        self.song = None
        self.anims = {}
        self.perf = None
        self.built_for = None            # the key (_key) the hands on screen were built for
        self._job = None                 # (thread, key, result box) while hands are built
        self._wait = 0.0
        self._pose_state = {}            # (hands.live_poses)
        self.waiting = False             # (hands.hands_waiting: playback holds for the exact pose)
        self.keyboard = Keyboard(pygame.Rect(0, 0, 520, 60))
        self.arrow = pygame.Rect(0, 0, 1, 1)
        self.size = (0, 0)

    # ----- geometry ---------------------------------------------------------------
    def layout(self, size):
        """Where the panel goes in a window of `size`; returns the y where the editor above it must end."""
        self.size = w, h = size
        kb_rect, self.hand_rect = bottom_layout(size)
        self.keyboard.layout(kb_rect)
        self.felt_rect = pygame.Rect(0, kb_rect.y - FELT_H, w, FELT_H)
        aw, ah = px(ARROW_W), px(ARROW_H)
        if self.shown:
            self.strip = None
            self.arrow = pygame.Rect(w // 2 - aw // 2, self.felt_rect.bottom, aw, ah)
            return self.felt_rect.top
        sh = px(STRIP_H)
        self.strip = pygame.Rect(0, h - sh, w, sh)
        self.arrow = pygame.Rect(w // 2 - aw // 2, h - sh + (sh - ah) // 2, aw, ah)
        return self.strip.top

    def toggle(self):
        self.shown = not self.shown
        pianists.set_app_setting("editor_hands", self.shown)
        self.mode.layout()
        self.mode.say("Keyboard and hands shown (H or the arrow hides them)" if self.shown
                      else "Keyboard and hands hidden (H or the arrow shows them)")

    def handle_event(self, e):
        """True if the event was the panel's (a click on the arrow, or anywhere on the panel)."""
        if e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
            if self.arrow.collidepoint(e.pos):
                if e.button == 1:
                    self.toggle()
                return True
            return self.top() <= e.pos[1]
        return False

    def top(self):
        return self.felt_rect.top if self.shown else self.strip.top

    # ----- the hands ----------------------------------------------------------------
    def wants_fingering(self):
        """The hands need the planner's suggestions (fingering_ui asks for them while this is True)."""
        return self.shown

    def _key(self):
        ed = self.mode.editor
        p = pianists.active()
        return (id(ed.doc), ed.doc.notes_rev, ed.doc.timing_rev, ed.doc.events_rev, ed.sugg_rev, p.id,
                getattr(p, "modified", None))

    def _inputs(self):
        """What build_hands takes, from the document and the suggestions (on this thread)."""
        ed = self.mode.editor
        doc = ed.doc
        notes = [n for n in doc.notes.values() if n.channel != DRUM_CHANNEL and (n.hand or n.id in ed.sugg)]
        on, off = doc.note_seconds(notes)
        rows = []
        for n, s, e in zip(notes, on.tolist(), off.tolist()):
            sg = ed.sugg.get(n.id)
            hand = n.hand or (sg[0] if sg else None) or (LEFT if n.pitch < 60 else RIGHT)
            finger, gliss = n.finger, n.gliss
            if not finger and not gliss and sg:
                finger, gliss = sg[1], sg[2]
            rows.append((n.pitch, s, e, n.velocity, n.track, hand, finger, gliss))
        tm = doc.tempo_map()
        controls = []
        for ev in doc.events.values():
            p = ev.payload
            if ev.kind == MIDI and p[0] & 0xF0 == 0xB0 and len(p) > 2 and p[1] in (64, 66, 67):
                controls.append((tm.sec(ev.tick), p[1], p[2]))
        duration = max((e for _, _, e, *_ in rows), default=0.0) + 1.0
        return rows, controls, duration

    def update(self, dt):
        """Start building the hands once the suggestions are ready for what's on the roll; take them when built."""
        if self._job is not None:
            thread, key, box = self._job
            if thread.is_alive():
                return
            self._job = None
            if "error" in box:
                self.mode.say(f"Couldn't build the hands: {box['error']}")
                self.built_for = key
            else:
                self.song, self.anims = box["result"]
                self.perf = Performance.from_animators(self.anims.values()) if self.anims else None
                self.built_for = key
                self._pose_state = {}
                settle_heap()
        ed = self.mode.editor
        if not self.shown or not ed.suggestions_ready() and ed.doc.notes:
            return
        key = self._key()
        if key == self.built_for or ed.press is not None:
            return
        self._wait += dt
        if self._wait < REBUILD_DEBOUNCE and self.built_for is not None and self.built_for[0] == key[0]:
            return
        self._wait = 0.0
        if not ed.doc.notes:
            self.song, self.anims, self.perf, self.built_for = None, {}, None, key
            return
        box = {}
        args = self._inputs()

        def work():
            try:
                box["result"] = build_hands(*args)
            except Exception as exc:          # (a message on screen, not a crash)
                box["error"] = exc
        thread = threading.Thread(target=work, name="editor hands", daemon=True)
        self._job = (thread, key, box)
        thread.start()

    def wait_built(self, timeout=60.0):
        """Block until the hands being built are done (tests, screenshots)."""
        if self._job is not None:
            self._job[0].join(timeout)
        self.update(0.0)

    @property
    def building(self):
        return self._job is not None

    def ready(self):
        return bool(self.shown and self.anims)

    def hold(self, t, scrubbing, playing):
        """Must playback wait this frame for the hands' exact pose after a jump (hands.hands_waiting)?"""
        self.waiting = bool(self.ready() and playing and not scrubbing and not self.building
                            and hands_waiting(self.anims.values(), t, self.keyboard, self._pose_state))
        return self.waiting

    def idle(self, t, budget, scrubbing):
        """The frame's spare time: the hands work ahead (not while scrubbed: each jump would start it over)."""
        if self.ready() and not scrubbing:
            prepare_hands(self.anims.values(), t, self.keyboard, max(budget, HOLD_BUDGET) if self.waiting else budget)

    # ----- drawing ------------------------------------------------------------------
    def draw(self, s, t, scrubbing, fonts):
        mouse = pygame.mouse.get_pos()
        if self.shown:
            draw_felt(s, self.felt_rect)
            down = {n.pitch: n.hand for _, _, n in self.perf.active(t)} if self.perf else {}
            self.keyboard.draw(s, down)
            draw_hand_area(s, self.hand_rect)
            if self.anims:
                s.set_clip(pygame.Rect(0, self.felt_rect.top, s.get_width(), s.get_height() - self.felt_rect.top))
                draw_hands(s, live_poses(self.anims.values(), t, self.keyboard, scrubbing, self._pose_state))
                s.set_clip(None)
            pedals = self.song.control_state(t) if self.song is not None else None
            draw_pianist_badge(s, fonts, self.hand_rect, pianists.active(), pedals)
            ed = self.mode.editor
            note = None
            if ed.doc.notes and (self.building or not ed.suggestions_ready()) and not self.anims:
                note = "Planning the hands…"
            elif not ed.doc.notes:
                note = "The hands play the notes on the roll"
            if note:
                img = fonts["small"].render(note, True, TEXT_DIM)
                s.blit(img, img.get_rect(center=(self.hand_rect.centerx, self.hand_rect.y + self.hand_rect.h // 3)))
        else:
            s.fill((22, 22, 28), self.strip)
            s.fill((50, 50, 62), (0, self.strip.y, self.strip.w, 1))
        self._draw_arrow(s, self.arrow.collidepoint(mouse))

    def _draw_arrow(self, s, hot):
        """The little translucent tab: a chevron down (hide) while shown, up (show) while hidden."""
        r = self.arrow
        tab = pygame.Surface(r.size, pygame.SRCALPHA)
        a = ARROW_HOVER_ALPHA if hot else ARROW_ALPHA
        rad = px(6)
        if self.shown:
            pygame.draw.rect(tab, (18, 18, 24, a), tab.get_rect(), border_bottom_left_radius=rad,
                             border_bottom_right_radius=rad)
        else:
            pygame.draw.rect(tab, (60, 60, 76, a), tab.get_rect(), border_radius=rad)
        cx, cy = r.w // 2, r.h // 2
        dx, dy = px(7), px(3.5)
        col = (*mix((200, 200, 210), (255, 206, 84), 0.6 if hot else 0.0), min(255, a + 40))
        pts = [(cx - dx, cy - dy), (cx, cy + dy), (cx + dx, cy - dy)] if self.shown else \
            [(cx - dx, cy + dy), (cx, cy - dy), (cx + dx, cy + dy)]
        pygame.draw.lines(tab, col, False, pts, max(2, px(2)))
        s.blit(tab, r)

    def tooltip(self):
        return "Hide the keyboard and hands (H)" if self.shown else "Show the keyboard and hands (H)"
