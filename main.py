"""
main.py - Hand-thesia: falling-notes player and fingering editor, built on Pygame.

    python main.py                  # main menu
    python main.py song.mid         # play a file straight away (falling notes)
    python main.py song.mid --edit  # open a file in the fingering editor
    python main.py song.mid --no-sound --speed 0.75
    python main.py song.mid --screenshot frame.png --at 12.5   # save one frame and exit

Main menu
    Play a MIDI file    browse for a file and watch it in falling-notes mode
    Fingering editor    browse for a file, fine-tune its fingering, export it
    Pianists & hands    create pianists (hand anatomy, colour, technique) and
                        choose the active one, used for the hands everywhere

Falling-notes controls
    Space           play / pause
    Left / Right    seek 5 s back / forward
    Up / Down       playback speed +/- 10 %
    + / -           zoom (show fewer / more seconds of upcoming notes)
    M               mute / unmute
    Home or R       restart
    O               open another MIDI file (or drag & drop a file onto the window)
    H               show / hide the hand skeleton
    F               show / hide finger numbers on the falling notes
    Click the top bar to seek.  Esc goes back to the menu.

Playing a file first asks how it should sound (audio_sync.PlaybackSetup):
the MIDI synth, or a recording synced to the notes - its speed chosen
first and fixed. With a recording, its waveform runs under the top bar:
drag it (Shift: finer) or press , / . (Shift: x10) to line it up.

The editor's controls are listed in editor.py (and shown in the editor itself).

Layout of the player, top to bottom: progress bar, falling-notes area,
keyboard, and the hand area. The hand skeletons (hands.py, both hands) are
drawn over the keyboard and hand area.
"""
from __future__ import annotations

import argparse
import collections
import math
import os
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")      # (no banner in the console or the log)
import pygame  # noqa: E402

from common import (ACCENT, LANE_WHITE, set_key_style, PANEL, PANEL_EDGE, blit_shadowed, BAR_BG, BAR_FILL, BAR_LINE, BG, FELT_H, FPS, HAND_COLORS, LANE_LINE,
                    LEAD_IN, TEXT, TEXT_DIM, TOP_BAR_H, WINDOW_SIZE, Button, Keyboard,
                    MidiOut, Performance, Transport, bottom_layout, center_text, draw_felt,
                    draw_hand_area, draw_pianist_badge, fmt_time, load_fonts, mix, pick_file,
                    DEFAULT_VOLUME, END_PAD_T, MAX_FRAME_DT, SPEED_MAX, SPEED_MIN, VolumeSlider, draw_tooltip, run_busy,
                    set_keyboard_place, wrap_text, Dialog, draw_arrow_keys, ui_font, set_font_choice)
import paths
import pianist as pianists
import recent
from hands import build_hands, draw_hands, load_with_hands, prepare_hands
from midi_loader import LEFT, RIGHT
from audio_sync import WAVE_H, PlaybackSetup
from version import VERSION

DEFAULT_WINDOW_SECS = 3.0    # how many seconds of upcoming notes fit above the keys
WAVE_FINE = 0.1              # dragging the waveform with Shift held moves it this much slower
SEEK_STEP = 5.0
MENU_TITLE_H = 130          # px, the main menu's title and subtitle above its first button
MENU_BUTTON_MIN_H = 54      # ...its buttons no lower than this with two lines of text...
MENU_BUTTON_SMALL_H = 40    # ...and this with just their names (a small window)
FINGER_PX_MAX = 17          # finger numbers on the notes: this big at most (13 before)...
FINGER_PX_MIN = 11          # ...but narrower than a black key's notes (Visualizer._finger_size)
FPS_CAP_MIN, FPS_CAP_MAX = 24, 240     # the frame rate cap's range (Settings; past the top: uncapped)
CONTROLS_H = 30            # the player's controls panel under the progress bar...
PANEL_MARGIN = 8            # ...this far in from the window's right and below the bar (or the waveform)
PANEL_ALPHA = 170           # ...and its panels' opacity (0-255): translucent over the notes
KEYS_BUTTON_W, KEYS_BUTTON_H = 58, 42    # the arrow-keys button under it (shows / hides the key controls)
UNCAPPED_IDLE_HZ = 240      # uncapped, the hands work ahead (App.run's idle) as if at this frame rate
PERF_FRAMES = 120           # the performance overlay (Settings): this many frames' times...
PERF_W, PERF_GRAPH_H = 230, 46
PERF_MAX_MS = 50.0          # ...graphed up to this
IDLE_MARGIN_T = 0.002        # s of each frame's spare time left unused (Visualizer.idle)

# What a mode's handle_event can return besides True (carry on) / False (quit)
TO_MENU = "menu"


# --------------------------------------------------------------------------- #
# The falling-notes player
# --------------------------------------------------------------------------- #
def note_rect(x, w, keyline, start, end, t, pps):
    """
    A falling note's box at song time t (pps: pixels a second): one length for
    the note (its duration, rounded once) and its bottom at the nearest pixel,
    so it moves as one solid block. (Rounding the top and the bottom each on
    their own made the length - and on a short note its finger number's size -
    flick between two values from frame to frame.)
    """
    h = max(4, round((end - start) * pps))
    bottom = round(keyline - (start - t) * pps)
    return pygame.Rect(x, bottom - h, w, h)


class Visualizer(Transport):
    def __init__(self, screen, song=None, midi=None, speed=1.0, fonts=None, audio=None, hands=None):
        self.screen = screen
        self.fonts = fonts or load_fonts()
        self._init_transport(midi or MidiOut(False), speed)
        # a synced recording (audio_sync.SyncAudio): heard instead of the synth,
        # the speed fixed, the song's clock following the audio's (wall clock)
        self.audio = audio
        self._synth_muted = self.midi.muted
        if audio:
            self.midi.muted = True
            self.midi.silence()
        self.audio_muted = False
        self._audio_pending = False      # waiting for the recording's start (the song's lead-in)
        self._anchor = None              # (song time, wall time) the clock runs from while playing
        self.dragging_wave = None        # the mouse x while the waveform is dragged (0 is a place too)
        self._wave_cache = (None, None)
        self.window_secs = DEFAULT_WINDOW_SECS
        self.dragging_bar = False
        self._glow_cache = {}
        self._finger_cache = {}          # (text, colour, size) -> rendered finger number
        self._finger_fonts = {}
        self.volume = VolumeSlider(self.midi.volume, self._set_volume)
        self._top_items = []             # [(rect, tooltip)] of the controls panel's items, for hovering
        self.controls_rect = pygame.Rect(0, 0, 0, 0)     # the controls panel (speed, view, sound, volume)
        self.sound_rect = pygame.Rect(0, 0, 0, 0)        # ...its "sound on / off" (click: mute / unmute)
        self.keys_button = pygame.Rect(0, 0, 0, 0)       # the arrow-keys button under it
        self.keys_rect = pygame.Rect(0, 0, 0, 0)         # the key controls' list, when shown
        self.show_keys = bool(pianists.app_setting("player_keys", False))
        self.hands = {}
        self.show_hands = True
        self.show_fingers = True
        self.layout(screen.get_size())
        if song:
            self.set_song(song, hands)

    # ----- setup -------------------------------------------------------------
    def set_song(self, song, hands=None):
        """Play `song`; `hands` (hands.build_hands), when already built for it, saves planning them again."""
        self.midi.silence()
        self.song = song
        self.hands = hands if hands is not None else build_hands(song)
        # what's heard and the keys that go down follow what the hands play
        self.perf = Performance.from_animators(self.hands.values()) if self.hands else None
        self.t = -LEAD_IN
        self.paused = bool(self.audio)          # with a recording: paused, to line it up first
        self.sounding = {}
        pygame.display.set_caption(f"Hand-thesia {VERSION} - {song.title}")

    def layout(self, size):
        w, h = size
        kb_rect, self.hand_rect = bottom_layout(size)
        self.bar_rect = pygame.Rect(0, 0, w, TOP_BAR_H)
        wave_h = WAVE_H if self.audio else 0
        self.wave_rect = pygame.Rect(0, TOP_BAR_H, w, wave_h)
        top = TOP_BAR_H + wave_h
        self.fall_rect = pygame.Rect(0, top, w, kb_rect.y - FELT_H - top)
        self.felt_rect = pygame.Rect(0, kb_rect.y - FELT_H, w, FELT_H)
        if hasattr(self, "keyboard"):
            self.keyboard.layout(kb_rect)
        else:
            self.keyboard = Keyboard(kb_rect)
        self._glow_cache.clear()

    @property
    def overlay_top(self):
        """Where the performance overlay may start (App.draw_perf): below the top bar and the waveform."""
        return self.fall_rect.top

    # ----- input ------------------------------------------------------------
    def handle_event(self, event):
        """True to carry on, False to quit, TO_MENU to go back to the main menu."""
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.get_surface()
            self.layout(self.screen.get_size())
        elif event.type == pygame.DROPFILE:
            return ("open", event.file)
        elif self.volume.handle_event(event):
            return True
        elif event.type == pygame.KEYDOWN:
            k = event.key
            if k == pygame.K_ESCAPE:
                return TO_MENU
            elif k == pygame.K_SPACE:
                self.toggle_pause()
            elif k == pygame.K_LEFT:
                self.seek(self.t - SEEK_STEP)
            elif k == pygame.K_RIGHT:
                self.seek(self.t + SEEK_STEP)
            elif k == pygame.K_UP and not self.audio:          # a synced recording fixes the speed
                self.change_speed(0.1)
            elif k == pygame.K_DOWN and not self.audio:
                self.change_speed(-0.1)
            elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.window_secs = max(0.75, self.window_secs / 1.25)
            elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.window_secs = min(12.0, self.window_secs * 1.25)
            elif k == pygame.K_m:
                self.toggle_mute()
            elif k in (pygame.K_COMMA, pygame.K_PERIOD) and self.audio:
                # nudge the recording: 10 ms, or 100 ms with Shift
                step = 0.1 if event.mod & pygame.KMOD_SHIFT else 0.01
                self._nudge_audio(step if k == pygame.K_COMMA else -step)
                if not self.paused:
                    self._start_audio()
            elif k == pygame.K_h:
                self.show_hands = not self.show_hands
            elif k == pygame.K_f:
                self.show_fingers = not self.show_fingers
            elif k in (pygame.K_HOME, pygame.K_r):
                self.seek(-LEAD_IN)
            elif k == pygame.K_o:
                path = pick_file()
                if path:
                    return ("open", path)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.keys_button.collidepoint(event.pos):
                self.show_keys = not self.show_keys
                pianists.set_app_setting("player_keys", self.show_keys)
            elif self.sound_rect.collidepoint(event.pos):
                self.toggle_mute()
            elif self.controls_rect.collidepoint(event.pos) or (self.show_keys and
                                                                 self.keys_rect.collidepoint(event.pos)):
                pass                                 # (the panels: no seeking or dragging through them)
            elif self.bar_rect.collidepoint(event.pos):
                self.dragging_bar = True
                self._seek_to_x(event.pos[0])
            elif self.audio and self.wave_rect.collidepoint(event.pos):
                self.dragging_wave = event.pos[0]
        elif event.type == pygame.MOUSEMOTION and self.dragging_bar:
            self._seek_to_x(event.pos[0])
        elif event.type == pygame.MOUSEMOTION and self.dragging_wave is not None:
            self._drag_wave(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging_bar and self.audio and not self.paused:
                self.dragging_bar = False
                self._start_audio()
            self.dragging_bar = False
            if self.dragging_wave is not None:
                self.dragging_wave = None
                if not self.paused:
                    self._start_audio()              # carry on from the new alignment
        return True

    def toggle_mute(self):
        """M, or a click on "sound on / off": the synced recording's sound, or the synth's."""
        if self.audio:
            self.audio_muted = not self.audio_muted
            self._apply_audio_volume()
        else:
            self.midi.muted = not self.midi.muted
            if self.midi.muted:
                self.midi.silence()

    def _set_volume(self, v):
        """The volume slider: the synth's volume, and a synced recording's (kept for next time)."""
        self.midi.set_volume(v)
        self._apply_audio_volume()
        pianists.set_app_setting("volume", round(v, 3))

    def _apply_audio_volume(self):
        if self.audio and self.audio.channel is not None:
            self.audio.channel.set_volume(0.0 if self.audio_muted else self.midi.volume)

    # ----- a synced recording ----------------------------------------------------
    def end_time(self):
        """With a recording, playback goes on to its end, past the last note if need be."""
        end = Transport.end_time(self)
        if self.audio:
            end = max(end, (self.audio.length - self.audio.offset) * self.speed + END_PAD_T)
        return end

    def audio_pos(self, t=None):
        """Where in the recording the song is at time t (seconds)."""
        return self.audio.offset + (self.t if t is None else t) / self.speed

    def _drag_wave(self, x):
        """Dragging the waveform moves the recording against the notes (10x finer with Shift held)."""
        span = self.song.duration if self.song and self.song.duration > 0 else 1.0
        # the strip shows song times 0..duration; the recording moves with the mouse
        fine = WAVE_FINE if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1.0
        self._nudge_audio(-(x - self.dragging_wave) / max(1, self.wave_rect.w) * span / self.speed * fine)
        self.dragging_wave = x
        if not self.paused:
            self.audio.stop()

    def _nudge_audio(self, d):
        """Shift the recording d seconds against the notes (+: its waveform moves left), kept overlapping them."""
        cover = (self.song.duration if self.song else 0.0) / self.speed
        self.audio.offset = max(-cover, min(self.audio.length, self.audio.offset + d))

    def _start_audio(self):
        """(Re)start the recording at the song's place, and the clock with it."""
        self.audio.stop()
        pos = self.audio_pos()
        self._audio_pending = pos < 0           # the song's lead-in comes before the recording
        if not self._audio_pending:
            self.audio.play_from(pos)           # (copying the rest of the recording takes a moment...)
            self._apply_audio_volume()
        self._anchor = (self.t, time.perf_counter())     # (...so the clock starts once it plays)

    def seek(self, t):
        Transport.seek(self, t)
        if self.audio and not self.paused:
            if self.dragging_bar:               # scrubbing: the recording restarts once, on release
                self.audio.stop()
                self._anchor = (self.t, time.perf_counter())
            else:
                self._start_audio()

    def toggle_pause(self):
        Transport.toggle_pause(self)
        if self.audio:
            if self.paused:
                self.audio.stop()
                self._audio_pending = False
            else:
                self._start_audio()

    def update(self, dt):
        held = self.dragging_wave is not None or self.dragging_bar
        if self.audio and self.song and not self.paused and not held:
            # the song follows the recording's (the wall) clock, not the frame clock
            t_a, wall = self._anchor
            target = t_a + (time.perf_counter() - wall) * self.speed
            dt = max(0.0, (target - self.t) / self.speed)
        elif self.audio and held:
            dt = 0.0                                # held while the recording is moved, or the song scrubbed
        Transport.update(self, dt)
        if self.audio:
            if self.paused:
                self.audio.stop()
                self._audio_pending = False
            elif self._audio_pending and self.audio_pos() >= 0:
                self._start_audio()

    def _seek_to_x(self, x):
        if self.song and self.song.duration > 0:
            frac = min(1.0, max(0.0, x / self.bar_rect.w))
            self.seek(frac * self.song.duration)

    def idle(self, budget):
        """The frame's spare time (App.run): the hands work ahead (HandAnimator.prepare)."""
        if self.song and self.hands:
            prepare_hands(self.hands.values(), self.t, self.keyboard, budget)

    def leave(self):
        self.midi.silence()                     # (with the pedal down, notes would ring on)
        if self.audio:
            self.audio.stop()
            self.midi.muted = self._synth_muted

    # ----- drawing ----------------------------------------------------------
    def _glow(self, color, w, h):
        key = (color, w, h)
        surf = self._glow_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h))
            for y in range(h):
                f = (y / max(1, h - 1)) ** 2 * 0.7
                pygame.draw.line(surf, tuple(int(c * f) for c in color), (0, y), (w, y))
            self._glow_cache[key] = surf
        return surf

    def _lanes(self, kb, fall):
        """The falling-notes area's background (lane lines, lighter white-key lanes), drawn once and cached."""
        w = self.screen.get_width()
        key = (w, tuple(fall), tuple(kb.rect), kb.style)
        if getattr(self, "_lanes_key", None) != key:
            surf = pygame.Surface((w, fall.h + 1))
            surf.fill(BG)
            for x in kb.lane_lines:
                pygame.draw.line(surf, LANE_LINE, (x, 0), (x, fall.h))
            for x, lw in kb.lane_shade:                 # equal keys: white-key lanes a shade lighter
                pygame.draw.rect(surf, LANE_WHITE, (x, 0, lw, fall.h))
            self._lanes_surf, self._lanes_key = surf, key
        return self._lanes_surf

    def _finger_size(self, kb):
        """The finger numbers' font size: as big as FINGER_PX_MAX, but narrower than the narrowest note (a black key's)."""
        narrow = min((w for _, w in kb.lanes.values()), default=20)
        key = (narrow,)
        if self._finger_fonts.get("key") != key:
            size = FINGER_PX_MIN
            for px in range(FINGER_PX_MAX, FINGER_PX_MIN - 1, -1):
                if self._finger_font(px).size("8")[0] + 3 <= narrow:
                    size = px
                    break
            self._finger_fonts["key"], self._finger_fonts["size"] = key, size
        return self._finger_fonts["size"]

    def _finger_font(self, px):
        f = self._finger_fonts.get(px)
        if f is None:
            f = self._finger_fonts[px] = ui_font(px, bold=True)
        return f

    def _finger_img(self, txt, color, px):
        """A finger number with its drop shadow (dark under a light number, light under a dark one)."""
        key = (txt, color, px)
        img = self._finger_cache.get(key)
        if img is None:
            font = self._finger_font(px)
            face = font.render(txt, True, color)
            light = sum(color) > 380
            shadow = font.render(txt, True, (0, 0, 0) if light else (255, 255, 255))
            shadow.set_alpha(170 if light else 120)
            img = pygame.Surface((face.get_width() + 2, face.get_height() + 2), pygame.SRCALPHA)
            img.blit(shadow, (2, 2))
            if light:                    # (a light number on a light note: a thin dark outline too)
                dark = font.render(txt, True, (15, 15, 20))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    img.blit(dark, (1 + dx, 1 + dy))
            img.blit(face, (1, 1))
            self._finger_cache[key] = img
        return img

    def render(self):
        s = self.screen
        s.fill(BG)
        fall = self.fall_rect
        kb = self.keyboard
        s.blit(self._lanes(kb, fall), (0, fall.top))

        active = []
        if self.song:
            t, song = self.t, self.song
            pps = fall.h / self.window_secs          # pixels per second
            keyline = fall.bottom
            t_top = t + self.window_secs

            for bt in song.bar_times:
                if t <= bt <= t_top:
                    y = int(keyline - (bt - t) * pps)
                    pygame.draw.line(s, BAR_LINE, (0, y), (fall.w, y))

            visible = song.notes_between(t, t_top)
            # what the hands are holding down now (their performance, not the file's times)
            active = [n for _, _, n in self._performance().active(t)]
            held = {id(n) for n in active}
            fpx = self._finger_size(kb)
            s.set_clip(fall)
            # White-key notes first so black-key notes draw on top of them.
            for black_pass in (False, True):
                for n in visible:
                    if n.is_black != black_pass or n.pitch not in kb.lanes:
                        continue
                    x, w = kb.lanes[n.pitch]
                    rect = note_rect(x, w, keyline, n.start, n.end, t, pps)
                    color = HAND_COLORS[n.hand][1 if black_pass else 0]
                    if id(n) in held:
                        color = mix(color, (255, 255, 255), 0.3)
                    radius = min(5, w // 3)
                    pygame.draw.rect(s, color, rect, border_radius=radius)
                    pygame.draw.rect(s, mix(color, (0, 0, 0), 0.45), rect, 1, border_radius=radius)
                    # equal keys: every note is the same width, so a white-key note's
                    # finger number is white (a black-key note's stays dark)
                    equal_white = kb.style == "equal" and not black_pass
                    px = min(fpx, rect.h - 2)            # (a short note: a smaller number, inside it)
                    if self.show_fingers and n.hand in self.hands and px >= FINGER_PX_MIN:
                        finger = self.hands[n.hand].finger_for(n)
                        if self.hands[n.hand].is_gliss(n):
                            finger = "g"                 # slid in a glissando
                        if finger:
                            # white number for a white key, black for a black one (equal keys)
                            color = (250, 250, 250) if equal_white else (15, 15, 20)
                            img = self._finger_img(str(finger), color, px)
                            s.blit(img, img.get_rect(midbottom=(rect.centerx + 1, rect.bottom + 1)))

            # Glow rising from the keys that are currently pressed (by the hands).
            gh = max(20, fall.h // 6)
            for n in active:
                if n.pitch in kb.lanes:
                    x, w = kb.lanes[n.pitch]
                    glow = self._glow(HAND_COLORS[n.hand][0], w + 8, gh)
                    s.blit(glow, (x - 4, keyline - gh), special_flags=pygame.BLEND_ADD)
            s.set_clip(None)

        draw_felt(s, self.felt_rect)
        kb.draw(s, {n.pitch: n.hand for n in active})
        draw_hand_area(s, self.hand_rect)
        if self.hands and self.show_hands:
            s.set_clip(pygame.Rect(0, self.felt_rect.top, s.get_width(), s.get_height()))
            draw_hands(s, [a.pose(self.t, kb) for a in self.hands.values()])
            s.set_clip(None)
        draw_pianist_badge(s, self.fonts, self.hand_rect, pianists.active(),
                           self.song.control_state(self.t) if self.song else None)
        self._draw_top_bar()
        if self.audio:
            self._draw_wave()
        self._draw_controls()
        self._draw_tooltip()

        if not self.song:
            center_text(s, self.fonts, self.fall_rect, "Drop a MIDI file here, or press O to open one")
        elif self.paused:
            center_text(s, self.fonts, self.fall_rect, "Paused  -  Space to play")

    def _draw_wave(self):
        """The recording's waveform on the song's timeline, a progress fill from the left, draggable."""
        s, r, a = self.screen, self.wave_rect, self.audio
        if not self.song or r.h <= 0:
            return
        dur = max(1e-6, self.song.duration)
        key = (a.offset, r.w, r.h, dur, self.speed)
        if self._wave_cache[0] != key:
            self._wave_cache = (key, a.strip(r.w, r.h, 0.0, dur, self.speed, dur))
        s.blit(self._wave_cache[1], r.topleft)
        frac = min(1.0, max(0.0, self.t / dur))
        x = int(r.w * frac)
        if x > 0:
            shade = pygame.Surface((x, r.h), pygame.SRCALPHA)
            shade.fill((*BAR_FILL, 46))
            s.blit(shade, r.topleft)
        pygame.draw.line(s, BAR_FILL, (r.x + x, r.y), (r.x + x, r.bottom - 1), 2)
        pygame.draw.line(s, PANEL_EDGE, (r.x, r.bottom - 1), (r.right, r.bottom - 1))
        font = self.fonts["small"]
        pos = self.audio_pos()
        hint = ("drag to line the recording up (Shift: finer),  , .  nudge 10 ms" if self.paused or self.dragging_wave is not None
                else "")
        label = f"{a.name}   {fmt_time(max(0.0, pos))} / {fmt_time(a.length)}   offset {a.offset:+.2f}s   {hint}"
        blit_shadowed(s, font, label, TEXT, (r.x + 8, r.y + 4))

    def _draw_top_bar(self):
        """The progress bar: the song's name and time, nothing else (click or drag anywhere on it to seek)."""
        s, r = self.screen, self.bar_rect
        pygame.draw.rect(s, BAR_BG, r)
        if self.song and self.song.duration > 0:
            frac = min(1.0, max(0.0, self.t / self.song.duration))
            pygame.draw.rect(s, mix(BAR_FILL, BAR_BG, 0.35), (0, 0, int(r.w * frac), r.h))
            pygame.draw.line(s, BAR_FILL, (int(r.w * frac), 0), (int(r.w * frac), r.h), 2)
            total = self.end_time() - END_PAD_T if self.audio else self.song.duration
            left = f"{self.song.title}    {fmt_time(self.t)} / {fmt_time(total)}"
        else:
            left = "No song loaded"
        font = self.fonts["normal"]
        blit_shadowed(s, font, left, TEXT, (10, (r.h - font.get_height()) // 2))

    def _key_controls(self):
        """[(key, what it does)] for the key controls' list."""
        keys = [("Space", "Play / pause"), ("← →", f"Skip back / forward {SEEK_STEP:.0f} s")]
        if self.audio:
            keys += [(", .", "Nudge the recording: 10 ms (Shift: 100 ms)")]
        else:
            keys += [("↑ ↓", "Faster / slower")]
        keys += [("+ −", "Show more / fewer seconds of notes"), ("Home / R", "Back to the start"),
                 ("M", "Mute / unmute the recording" if self.audio else "Mute / unmute"),
                 ("H", "Show / hide the hands"), ("F", "Show / hide the finger numbers"),
                 ("O", "Open another MIDI file"), ("Esc", "Back to the main menu")]
        return keys

    @staticmethod
    def _panel(s, rect):
        """A translucent rounded panel behind controls drawn over the notes."""
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*BAR_BG, PANEL_ALPHA), panel.get_rect(), border_radius=8)
        s.blit(panel, rect.topleft)

    def _draw_controls(self):
        """
        Under the progress bar, on the right, on translucent panels: speed, view,
        sound on / off and the volume; under them the arrow-keys button, which
        shows or hides the key controls with what each does.
        """
        s = self.screen
        font = self.fonts["small"]
        muted = self.audio_muted if self.audio else self.midi.muted
        if self.audio:
            sound = ("sound off" if muted else "sound on", "The synced recording - click or M to mute / unmute")
            speed = (f"speed {int(round(self.speed * 100))}% (fixed)", "Playback speed, fixed for a synced recording")
        else:
            what = f"The {self.midi.name} soundfont" if self.midi.name else "The system's MIDI synth"
            if not self.midi.available:
                sound = ("no synth", "No MIDI synth was found to play the notes")
            else:
                sound = ("sound off" if muted else "sound on", f"{what} - click or M to mute / unmute")
            speed = (f"speed {int(round(self.speed * 100))}%", "Playback speed - ↑ / ↓ to change")
        items = [speed, (f"view {self.window_secs:.1f}s", "Seconds of notes shown falling - + / - to change"), sound]
        pad, gap, h = 12, 18, CONTROLS_H
        widths = [font.size(text)[0] for text, _ in items]
        vw = self.volume.width()
        w = pad + sum(widths) + gap * len(items) + vw + pad
        top = self.fall_rect.top + PANEL_MARGIN
        panel = pygame.Rect(self.screen.get_width() - PANEL_MARGIN - w, top, w, h)
        self.controls_rect = panel
        self._panel(s, panel)
        self._top_items = []
        x = panel.x + pad
        ty = panel.centery - font.get_height() // 2
        for (text, tip), tw in zip(items, widths):
            item = pygame.Rect(x - 4, panel.y, tw + 8, h)
            hot = text == sound[0] and self.midi.available or (self.audio and text == sound[0])
            col = ACCENT if text == "sound off" else (TEXT if hot and item.collidepoint(pygame.mouse.get_pos())
                                                    else TEXT_DIM)
            blit_shadowed(s, font, text, col, (x, ty))
            self._top_items.append((item, tip))
            if text == sound[0]:
                self.sound_rect = item if (self.audio or self.midi.available) else pygame.Rect(0, 0, 0, 0)
            x += tw + gap
        self.volume.rect = pygame.Rect(x, panel.y, vw, h)
        self.volume.draw(s, self.fonts, muted=muted)
        self._top_items.append((self.volume.rect, "Volume - click, drag or scroll"))

        # the arrow-keys button, and the key controls under it when shown
        b = pygame.Rect(panel.right - KEYS_BUTTON_W, panel.bottom + 6, KEYS_BUTTON_W, KEYS_BUTTON_H)
        self.keys_button = b
        self._panel(s, b)
        hover = b.collidepoint(pygame.mouse.get_pos())
        draw_arrow_keys(s, b, TEXT if hover or self.show_keys else TEXT_DIM, ACCENT if self.show_keys else None)
        self._top_items.append((b, "Hide the key controls" if self.show_keys else "Show the key controls"))
        if self.show_keys:
            keys = self._key_controls()
            kf = self.fonts["small"]
            kw = max(kf.size(k)[0] for k, _ in keys)
            dw = max(kf.size(d)[0] for _, d in keys)
            line = kf.get_linesize() + 4
            box = pygame.Rect(0, b.bottom + 6, pad + kw + 16 + dw + pad, 10 + line * len(keys) + 6)
            box.right = panel.right
            self.keys_rect = box
            self._panel(s, box)
            y = box.y + 10
            for k, d in keys:
                img = kf.render(k, True, TEXT)
                s.blit(img, (box.x + pad + kw - img.get_width(), y))         # (keys right-aligned)
                s.blit(kf.render(d, True, TEXT_DIM), (box.x + pad + kw + 16, y))
                y += line
        else:
            self.keys_rect = pygame.Rect(0, 0, 0, 0)

    def _draw_tooltip(self):
        """What the top bar's control under the mouse does."""
        mouse = pygame.mouse.get_pos()
        if self.dragging_bar or self.volume.dragging:
            return
        for rect, tip in self._top_items:
            if rect.collidepoint(mouse):
                draw_tooltip(self.screen, self.fonts, tip, rect)
                return


# --------------------------------------------------------------------------- #
# Main menu
# --------------------------------------------------------------------------- #
CHANGELOG = paths.resource("CHANGELOG.md")


def _seen_path():
    return os.path.join(pianists.FOLDER, "changelog_seen.txt")


def latest_changes(path=CHANGELOG):
    """The changelog's newest heading (its version and title), or "" if there is none."""
    try:
        with open(path, encoding="utf-8") as fh:
            return next((line.strip() for line in fh if line.startswith("## ")), "")
    except OSError:
        return ""


def changelog_seen():
    """
    Whether the changelog has been opened since its newest entry arrived (by
    the entry, not VERSION: a snapshot build without one isn't news).
    """
    try:
        with open(_seen_path(), encoding="utf-8") as fh:
            return fh.read().strip() == latest_changes()
    except OSError:
        return False


def mark_changelog_seen():
    try:
        os.makedirs(pianists.FOLDER, exist_ok=True)
        with open(_seen_path(), "w", encoding="utf-8") as fh:
            fh.write(latest_changes())
    except OSError as exc:
        print(f"Couldn't save that the changelog was seen ({exc})")


def changelog_entries(path=CHANGELOG):
    """[(heading, [bullet text])] from CHANGELOG.md, newest first (continuation lines joined)."""
    entries = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return [("Changelog not found", [])]
    for line in lines:
        if line.startswith("## "):
            entries.append((line[3:].strip(), []))
        elif entries and line.startswith("- "):
            entries[-1][1].append(line[2:].strip())
        elif entries and entries[-1][1] and line.startswith("  ") and line.strip():
            entries[-1][1][-1] += " " + line.strip()
    return entries


class ChangelogView:
    """The changelog in a scrollable panel over the main menu."""

    def __init__(self, fonts):
        self.fonts = fonts
        self.entries = changelog_entries()
        self.scroll = 0
        self.closed = False
        self.close_button = Button("Close", "close")
        self._rows, self._width = [], None

    def _layout(self, w, h):
        self.box = pygame.Rect(0, 0, min(760, w - 60), h - 80)
        self.box.center = (w // 2, h // 2)
        self.view = pygame.Rect(self.box.x + 24, self.box.y + 64, self.box.w - 48, self.box.h - 64 - 70)
        self.close_button.rect = pygame.Rect(self.box.right - 24 - 120, self.box.bottom - 24 - 38, 120, 38)
        if self._width != self.view.w:            # (re)wrap the text for this width
            self._width = self.view.w
            f, rows = self.fonts, []
            for heading, bullets in self.entries:
                rows.append((f["button"], ACCENT if heading.split(" ")[0] == VERSION else TEXT, heading, 0, 8))
                indent = f["normal"].size("•  ")[0]
                for b in bullets:
                    for i, part in enumerate(wrap_text(f["normal"], b, self.view.w - indent - 4)):
                        rows.append((f["normal"], TEXT_DIM, ("•  " if i == 0 else "") + part, 0 if i == 0 else indent, 0))
                rows.append((f["normal"], TEXT_DIM, "", 0, 0))
            self._rows = rows
        total = sum(font.get_linesize() + gap for font, _, _, _, gap in self._rows)
        self.max_scroll = max(0, total - self.view.h)
        self.scroll = min(max(0, self.scroll), self.max_scroll)

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE):
                self.closed = True
            elif event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                self.scroll += 40 if event.key == pygame.K_DOWN else self.view.h - 40
            elif event.key in (pygame.K_UP, pygame.K_PAGEUP):
                self.scroll -= 40 if event.key == pygame.K_UP else self.view.h - 40
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll -= event.y * 48
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.close_button.hit(event.pos) or not self.box.collidepoint(event.pos):
                self.closed = True

    def draw(self, surf):
        w, h = surf.get_size()
        self._layout(w, h)
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        pygame.draw.rect(surf, PANEL, self.box, border_radius=12)
        pygame.draw.rect(surf, PANEL_EDGE, self.box, 1, border_radius=12)
        surf.blit(self.fonts["big"].render("What's new", True, TEXT), (self.box.x + 24, self.box.y + 18))
        cur = self.fonts["small"].render(f"You have {VERSION}", True, TEXT_DIM)
        surf.blit(cur, cur.get_rect(topright=(self.box.right - 24, self.box.y + 30)))
        surf.set_clip(self.view)
        y = self.view.y - self.scroll
        for font, color, text, indent, gap in self._rows:
            y += gap
            if text and self.view.y - 40 < y < self.view.bottom:
                surf.blit(font.render(text, True, color), (self.view.x + indent, y))
            y += font.get_linesize()
        surf.set_clip(None)
        if self.max_scroll:
            frac = self.view.h / (self.view.h + self.max_scroll)
            bar_h = max(30, int(self.view.h * frac))
            bar_y = self.view.y + int((self.view.h - bar_h) * self.scroll / self.max_scroll)
            pygame.draw.rect(surf, PANEL_EDGE, (self.box.right - 14, bar_y, 5, bar_h), border_radius=3)
            hint = self.fonts["small"].render("Wheel or arrow keys to scroll", True, TEXT_DIM)
            surf.blit(hint, hint.get_rect(midleft=(self.box.x + 24, self.close_button.rect.centery)))
        self.close_button.draw(surf, self.fonts, pygame.mouse.get_pos())


class RecentView:
    """The Recent list over the main menu: the last setups opened (recent.py); `chosen` when one is clicked."""

    ROW_H = 72

    def __init__(self, fonts):
        self.fonts = fonts
        self.items = recent.entries()
        self.rows = []
        for i, entry in enumerate(self.items):
            title, sub = recent.describe(entry)
            self.rows.append(Button(title, i, font="button", sub=sub, key_hint=str(i + 1)))
        self.close_button = Button("Close", "close", key_hint="Esc")
        self.chosen = None
        self.closed = False

    def _layout(self, w, h):
        n = len(self.rows)
        row = max(54, min(self.ROW_H, (h - 40 - 134) // max(1, n) - 10))     # (smaller rows in a small window)
        self.box = pygame.Rect(0, 0, min(620, w - 60), 64 + n * (row + 10) + 70)
        self.box.center = (w // 2, h // 2)
        y = self.box.y + 64
        for b in self.rows:
            b.rect = pygame.Rect(self.box.x + 24, y, self.box.w - 48, row)
            y += row + 10
        self.close_button.rect = pygame.Rect(self.box.right - 24 - 120, self.box.bottom - 24 - 38, 120, 38)

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.closed = True
            elif pygame.K_1 <= event.key < pygame.K_1 + len(self.items):
                self.chosen = self.items[event.key - pygame.K_1]
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.rows:
                if b.hit(event.pos):
                    self.chosen = self.items[b.action]
                    return
            if self.close_button.hit(event.pos) or not self.box.collidepoint(event.pos):
                self.closed = True

    def draw(self, surf):
        w, h = surf.get_size()
        self._layout(w, h)
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        pygame.draw.rect(surf, PANEL, self.box, border_radius=12)
        pygame.draw.rect(surf, PANEL_EDGE, self.box, 1, border_radius=12)
        surf.blit(self.fonts["big"].render("Recent", True, TEXT), (self.box.x + 24, self.box.y + 18))
        mouse = pygame.mouse.get_pos()
        for b in self.rows + [self.close_button]:
            b.draw(surf, self.fonts, mouse)


class MainMenu:
    def __init__(self, app):
        self.app = app
        self.message = ""
        self.buttons = [
            Button("Play a MIDI file", "play", font="button", key_hint="P",
                   sub="Browse for a file and watch it in falling-notes mode"),
            Button("Fingering editor", "edit", font="button", key_hint="E",
                   sub="Open a MIDI file, fine-tune its fingering and export it"),
            Button("Recent", "recent", font="button", key_hint="R", sub=""),
            Button("Pianists & hands", "pianists", font="button", key_hint="H",
                   sub="Create pianists: hand anatomy, colour and technique; choose the active one"),
            Button("Quit", "quit", font="normal", key_hint="Esc"),
        ]
        self.changelog_button = Button("What's new", "changelog", font="small")
        self.settings_button = Button("Settings", "settings", font="small")
        self.overlay_top = 0
        self.changelog_new = not changelog_seen()       # glows until opened
        self.changelog = None
        self.recent = None              # the Recent list (RecentView) while it's open
        self.dialog = None              # an error to acknowledge (a recent setup's file not found)
        self.layout(app.screen.get_size())
        pygame.display.set_caption(f"Hand-thesia {VERSION}")

    def layout(self, size):
        w, h = size
        bw = min(560, w - 80)
        # the buttons, Quit and the active pianist fit between the title and the tip above the keyboard
        kb_h = bottom_layout((w, h))[0].h
        line = self.app.fonts["small"].get_height()            # the tip's and the active pianist line's height
        floor = h - kb_h - FELT_H - 14 - line                  # (the tip's top)
        below = 16 + 40 + 16 + line + 6                        # Quit, then the active pianist (and a gap)
        n, gap = 4, 16 if h >= 760 else 10
        fit = (floor - MENU_TITLE_H - below - (n - 1) * gap) // n
        bh = max(MENU_BUTTON_SMALL_H, min(88, int(h * 0.105), fit))
        self._compact = bh < MENU_BUTTON_MIN_H              # (no room for the buttons' second lines)
        block = n * bh + (n - 1) * gap + below
        top = MENU_TITLE_H - (20 if self._compact else 0)      # (a small window: the title a little higher)
        y = max(top, min(int(h * 0.27), floor - block))
        for b in self.buttons[:n]:
            b.rect = pygame.Rect((w - bw) // 2, y, bw, bh)
            y += bh + gap
        self.buttons[n].rect = pygame.Rect((w - 160) // 2, y + 4 - gap + 12, 160, 40)
        self._active_y = self.buttons[n].rect.bottom + 16
        self.changelog_button.rect = pygame.Rect(w - 16 - 110, 16, 110, 32)
        self.settings_button.rect = pygame.Rect(w - 16 - 110 - 10 - 100, 16, 100, 32)

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            return False
        if self.dialog and event.type != pygame.VIDEORESIZE:
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.dialog.choice = "ok"
            else:
                self.dialog.handle_event(event)
            if self.dialog.choice:
                self.dialog = None
            return True
        if self.changelog and event.type != pygame.VIDEORESIZE:
            self.changelog.handle_event(event)
            if self.changelog.closed:
                self.changelog = None
            return True
        if self.recent and event.type != pygame.VIDEORESIZE:
            self.recent.handle_event(event)
            if self.recent.chosen is not None:
                entry, self.recent = self.recent.chosen, None
                self.app.open_recent(entry)
            elif self.recent.closed:
                self.recent = None
            return True
        if event.type == pygame.VIDEORESIZE:
            self.layout(event.size if hasattr(event, "size") else self.app.screen.get_size())
        elif event.type == pygame.DROPFILE:
            self.app.choose_playback(event.file)
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return False
            if event.key in (pygame.K_p, pygame.K_1):
                return self._do("play")
            if event.key in (pygame.K_e, pygame.K_2):
                return self._do("edit")
            if event.key in (pygame.K_r, pygame.K_3):
                return self._do("recent")
            if event.key in (pygame.K_h, pygame.K_4):
                return self._do("pianists")
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons + [self.changelog_button, self.settings_button]:
                if b.hit(event.pos):
                    return self._do(b.action)
        return True

    def open_changelog(self):
        self.changelog = ChangelogView(self.app.fonts)
        if self.changelog_new:
            self.changelog_new = False
            mark_changelog_seen()

    def show_error(self, title, message):
        self.dialog = Dialog(title, message, [("OK", "ok")])

    def _do(self, action):
        if action == "changelog":
            self.open_changelog()
            return True
        if action == "recent":
            if recent.entries():
                self.recent = RecentView(self.app.fonts)
            return True
        if action == "quit":
            return False
        if action == "settings":
            self.app.settings()
            return True
        if action == "pianists":
            self.app.studio()
            return True
        path = pick_file("Open MIDI file" if action == "play" else "Open MIDI file to edit",
                         initialdir=self.app.last_dir)
        if path:
            (self.app.choose_playback if action == "play" else self.app.edit)(path)
        return True

    def update(self, dt):
        pass

    def leave(self):
        pass

    def render(self):
        s = self.app.screen
        f = self.app.fonts
        w, h = s.get_size()
        s.fill(BG)
        # a strip of keyboard along the bottom, for a bit of atmosphere
        kb_rect, hand_rect = bottom_layout((w, h))
        # (true proportions, like the player's, just without the hand area)
        kb = Keyboard(pygame.Rect(kb_rect.x, h - kb_rect.h, kb_rect.w, kb_rect.h))
        kb.draw(s, {60: RIGHT, 64: RIGHT, 67: RIGHT, 36: LEFT, 43: LEFT})
        draw_felt(s, pygame.Rect(0, kb.rect.y - FELT_H, w, FELT_H))

        top = self.buttons[0].rect.y
        title = f["title"].render("Hand-thesia", True, TEXT)
        s.blit(title, title.get_rect(midbottom=(w // 2, top - 50)))
        sub = f["normal"].render("MIDI playback with animated hands and fingering", True, TEXT_DIM)
        s.blit(sub, sub.get_rect(midbottom=(w // 2, top - 20)))
        mouse = pygame.mouse.get_pos()
        if self.recent or self.dialog:
            mouse = (-1, -1)                         # (no hover under the panel)
        items = recent.entries()
        rb = self.buttons[2]
        rb.enabled = bool(items)
        if items:
            title, sub = recent.describe(items[0])
            rb.sub = f"The last {len(items)} setups you opened  ·  latest: {title}" if len(items) > 1 \
                else f"The last setup you opened: {title}"
        else:
            rb.sub = "The files you open will be listed here"
        for b in self.buttons:
            sub = b.sub
            if self._compact:
                b.sub = None
            b.draw(s, f, mouse)
            b.sub = sub
        # the active pianist, quietly
        act = pianists.active()
        img = f["small"].render(f"Active pianist: {act.name}  ·  {act.span_label()}", True, TEXT_DIM)
        r = img.get_rect(midtop=(w // 2 + 10, self._active_y))
        s.blit(img, r)
        pygame.draw.circle(s, act.badge_color, (r.x - 14, r.centery), 6)
        pygame.draw.circle(s, (150, 150, 160), (r.x - 14, r.centery), 6, 1)
        hint = self.message or "Tip: drop a MIDI file on this window to play it"
        img = f["small"].render(hint, True, TEXT_DIM)
        tip = img.get_rect(midbottom=(w // 2, kb.rect.y - FELT_H - 14))
        if self.message or tip.top >= r.bottom + 4:           # (the tip only where there's room for it)
            s.blit(img, tip)
        if self.changelog_new:
            self._draw_glow(s, self.changelog_button.rect)
        self.changelog_button.draw(s, f, mouse)
        self.settings_button.draw(s, f, mouse)
        if self.changelog:
            self.changelog.draw(s)
        if self.recent:
            self.recent.draw(s)
        if self.dialog:
            self.dialog.draw(s, f)

    @staticmethod
    def _draw_glow(s, rect, period=2.4):
        """A soft accent glow around `rect`, slowly pulsing."""
        pulse = 0.5 - 0.5 * math.cos(2 * math.pi * (pygame.time.get_ticks() / 1000.0) / period)
        pad = 14
        glow = pygame.Surface((rect.w + 2 * pad, rect.h + 2 * pad), pygame.SRCALPHA)
        for i in range(pad, 0, -2):
            a = int((40 + 120 * pulse) * (1 - i / pad))
            pygame.draw.rect(glow, (*ACCENT, a), glow.get_rect().inflate(-2 * (pad - i), -2 * (pad - i)),
                             border_radius=8 + i)
        s.blit(glow, (rect.x - pad, rect.y - pad))
        edge = mix(PANEL_EDGE, ACCENT, 0.4 + 0.6 * pulse)
        pygame.draw.rect(s, edge, rect.inflate(2, 2), 2, border_radius=9)


# --------------------------------------------------------------------------- #
# The application: owns the window, the synth and the current mode
# --------------------------------------------------------------------------- #
def fps_cap_value(cap):
    """A frame rate cap as kept: an int within FPS_CAP_MIN..FPS_CAP_MAX, or None (uncapped)."""
    if cap is None:
        return None
    try:
        return max(FPS_CAP_MIN, min(FPS_CAP_MAX, int(round(float(cap)))))
    except (TypeError, ValueError):
        return FPS


class App:
    def __init__(self, screen, sound=True, speed=1.0):
        self.screen = screen
        self.font_problem = set_font_choice(pianists.app_setting("font"))      # (Settings > Font)
        self.fonts = load_fonts()
        set_key_style(pianists.app_setting("keys", "realistic"))
        set_keyboard_place(pianists.app_setting("keyboard_place"))
        self.perf_overlay = bool(pianists.app_setting("perf_overlay", False))
        self.fps_cap = fps_cap_value(pianists.app_setting("fps_cap", FPS))   # None: uncapped
        self._frame_ms = collections.deque(maxlen=PERF_FRAMES)       # the last frames' times, for the overlay
        self._sound = sound
        self.midi, self.soundfont_problem = self._make_synth(pianists.app_setting("soundfont"))
        self.midi.set_volume(pianists.app_setting("volume", DEFAULT_VOLUME))
        self.speed = min(SPEED_MAX, max(SPEED_MIN, speed))
        self.last_dir = None
        self._fresh = True
        self.mode = MainMenu(self)

    def _switch(self, mode):
        self.mode.leave()
        self.mode = mode
        self._fresh = True

    def menu(self, message=""):
        if isinstance(self.mode, Visualizer) and not self.mode.audio:
            self.speed = self.mode.speed          # (a synced recording's speed was chosen for it alone)
        self._switch(MainMenu(self))
        self.mode.message = message

    def _load(self, path_or_song, **kw):
        """
        (song, its hands - hands.build_hands(song, **kw)) for a path or a song
        already loaded, worked out in the background behind a progress bar;
        (None, None) if the file can't be read.
        """
        if isinstance(path_or_song, str):
            text = f"Loading {os.path.basename(path_or_song)}…"
        else:
            text = "Preparing the hands…"
        try:
            song, hands = run_busy(self.screen, self.fonts, text, lambda: load_with_hands(path_or_song, **kw))
        except Exception as exc:
            if not isinstance(path_or_song, str):
                raise
            print(f"Could not load {path_or_song}: {exc}")
            if isinstance(self.mode, MainMenu):
                self.mode.message = f"Could not open {os.path.basename(path_or_song)}: {exc}"
            return None, None
        if isinstance(path_or_song, str):
            self.last_dir = os.path.dirname(os.path.abspath(path_or_song))
            if not song.notes:
                if isinstance(self.mode, MainMenu):
                    self.mode.message = f"{os.path.basename(path_or_song)} has no notes to play"
                return None, None
        return song, hands

    def choose_playback(self, path_or_song, hands=None):
        """A MIDI file chosen to play: first how it should sound (audio_sync.PlaybackSetup)."""
        song, hands = self._load(path_or_song) if hands is None else (path_or_song, hands)
        if song:
            self._switch(PlaybackSetup(self, song, hands))

    def play(self, path_or_song, audio=None, speed=None, hands=None):
        """Play straight away: with the synth, or with a synced recording (audio_sync.SyncAudio) at `speed`."""
        song, hands = self._load(path_or_song) if hands is None else (path_or_song, hands)
        if song:
            self._switch(Visualizer(self.screen, song, midi=self.midi, speed=speed or self.speed,
                                    fonts=self.fonts, audio=audio, hands=hands))
            if song.path:
                recent.add_play(song.path, soundfont=getattr(self.midi, "path", None),
                                audio=audio.path if audio else None, speed=speed)

    def edit(self, path_or_song):
        from editor import FingeringEditor
        # the editor shows the file's fingering as it is, even where it can't be played
        song, hands = self._load(path_or_song, repair=False)
        if song:
            self._switch(FingeringEditor(self, song, hands))
            if song.path:
                recent.add_edit(song.path)

    def open_recent(self, entry):
        """
        A setup from the Recent list, opened again as it was: the editor, or
        playing with its soundfont (or the default sound) or its recording at
        its speed. If one of its files can't be found: an error saying so, and
        the setup leaves the list.
        """
        gone = recent.missing(entry)
        if gone:
            recent.remove(entry)
            kinds = {entry.get("midi"): "MIDI file", entry.get("audio"): "audio file",
                     entry.get("soundfont"): "soundfont"}
            what = " and ".join(f"the {kinds.get(p, 'file')} {os.path.basename(p or '?')}" for p in gone)
            self.menu()
            self.mode.show_error("File not found",
                                 f"Couldn't find {what}. The setup has been removed from the Recent list.")
            return
        midi = entry["midi"]
        if entry.get("mode") == "edit":
            self.edit(midi)
        elif entry.get("audio"):
            song, hands = self._load(midi)
            if song:
                setup = PlaybackSetup(self, song, hands)
                setup.page = "speed"
                setup.slider.value = min(SPEED_MAX, max(0.25, float(entry.get("speed") or 1.0)))
                setup._set_speed(setup.slider.value)
                self._switch(setup)
                setup.open_audio(entry["audio"])   # (playing if it loads and is long enough, else why not)
        else:
            if (entry.get("soundfont") or None) != (getattr(self.midi, "path", None) or None):
                self.set_soundfont(entry.get("soundfont"))
            self.play(midi)

    def studio(self):
        from hand_editor import PianistStudio
        self._switch(PianistStudio(self))

    def handle_event(self, event):
        if event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.get_surface() or self.screen
            if hasattr(self.mode, "screen"):
                self.mode.screen = self.screen
        result = self.mode.handle_event(event)
        if result == TO_MENU:
            self.menu()
            return True
        if isinstance(result, tuple) and result[0] == "open":      # another MIDI file to play
            self.choose_playback(result[1])
            return True
        return result

    def run(self):
        clock = pygame.time.Clock()
        running = True
        last = None
        while running:
            # A frame that took long (a file loading, the first poses being
            # solved) must not jump the song ahead: the clock only moves on
            # by at most MAX_FRAME_DT per frame, and not at all on the frame
            # right after a new mode (song) was set up.
            # (tick only for the cap: it counts whole milliseconds, so at 144 Hz a 6.94 ms frame moved the
            # song on 6 or 7 ms - unevenly, a jitter - so the frame's time is measured to the microsecond)
            clock.tick(self.fps_cap or 0)
            now = time.perf_counter()
            real = now - last if last is not None else 0.0
            last = now
            self._frame_ms.append(real * 1000.0)
            dt = min(real, MAX_FRAME_DT)
            if self._fresh:
                dt, self._fresh = 0.0, False
            for event in pygame.event.get():
                if not self.handle_event(event):
                    running = False
                    break
            frame_start = time.perf_counter()
            self.mode.update(dt)
            self.mode.render()
            self.draw_version()
            if self.perf_overlay:
                self.draw_perf()
            pygame.display.flip()
            # what's left of this frame's time goes to work done ahead (instead of sleeping in tick)
            spare = 1.0 / (self.fps_cap or UNCAPPED_IDLE_HZ) - (time.perf_counter() - frame_start) - IDLE_MARGIN_T
            if spare > 0 and hasattr(self.mode, "idle"):
                self.mode.idle(spare)
        self.close()

    def draw_version(self):
        """The version in the window's bottom-left corner."""
        font = self.fonts["small"]
        blit_shadowed(self.screen, font, VERSION, TEXT_DIM, (6, self.screen.get_height() - font.get_height() - 4))

    def draw_perf(self):
        """
        Performance profiling (Settings): the frame rate and a translucent graph
        of the last PERF_FRAMES frames' times, in the top-left corner - below
        the mode's top bar (its `overlay_top`). Lines at 60 and 30 fps.
        """
        frames = list(self._frame_ms)[1:] or [0]
        recent = frames[-30:]
        avg = sum(recent) / len(recent)
        fps = 1000.0 / avg if avg > 0 else 0.0
        w, gh = PERF_W, PERF_GRAPH_H
        font = self.fonts["small"]
        th = font.get_height()
        top = getattr(self.mode, "overlay_top", TOP_BAR_H) + 8
        panel = pygame.Surface((w, th + gh + 14), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 110))
        g = pygame.Rect(6, th + 8, w - 12, gh)
        y_of = lambda ms: g.bottom - min(1.0, ms / PERF_MAX_MS) * g.h
        for ms, col in ((1000 / 60, (120, 200, 120, 150)), (1000 / 30, (230, 150, 80, 150))):
            pygame.draw.line(panel, col, (g.x, y_of(ms)), (g.right, y_of(ms)))
        step = g.w / max(1, PERF_FRAMES - 1)
        x0 = g.right - step * (len(frames) - 1)
        pts = [(x0 + i * step, y_of(ms)) for i, ms in enumerate(frames)]
        if len(pts) > 1:
            pygame.draw.lines(panel, (*ACCENT, 230), False, pts, 1)
        for (x, y), ms in zip(pts, frames):
            if ms > 1000 / 30:                                  # a slow frame stands out
                pygame.draw.circle(panel, (240, 90, 80, 230), (int(x), int(y)), 2)
        self.screen.blit(panel, (8, top))
        blit_shadowed(self.screen, font, f"{fps:.0f} FPS   {avg:.1f} ms   (worst {max(frames):.0f} ms)",
                      TEXT, (14, top + 4))

    def _make_synth(self, path):
        """(player, problem) for the soundfont at `path` (sf_synth.make_synth), loaded behind a progress bar."""
        from sf_synth import make_synth, soundfont_name
        if not (self._sound and path and os.path.isfile(path)):
            return make_synth(self._sound, path)
        return run_busy(self.screen, self.fonts, f"Loading the soundfont {soundfont_name(path)}…",
                        lambda: make_synth(self._sound, path))

    def set_soundfont(self, path):
        """
        Play with the soundfont at `path` from now on, or the system's synth
        (None); kept for next time. Returns the problem if it can't be played
        ("" if fine) - the system's synth plays then.
        """
        volume = self.midi.volume
        self.midi.close()
        self.midi, self.soundfont_problem = self._make_synth(path)
        self.midi.set_volume(volume)
        pianists.set_app_setting("soundfont", path)
        return self.soundfont_problem

    def set_font(self, choice):
        """
        Use the font `choice` (common.font_files: None for the bundled one) for
        everything from now on, kept for next time; "" if fine, else why not
        (the bundled one is used then). The fonts dict is updated in place, so
        every screen holding it follows.
        """
        self.font_problem = set_font_choice(choice)
        pianists.set_app_setting("font", choice or None)
        self.fonts.clear()
        self.fonts.update(load_fonts())
        if hasattr(self.mode, "layout"):
            self.mode.layout(self.screen.get_size())
        return self.font_problem

    def sound_label(self):
        """What the notes are played with, for buttons and the Settings: the soundfont's name, or the default."""
        return f"the {self.midi.name} soundfont" if self.midi.name else "the default system synth"

    def set_fps_cap(self, cap):
        """The frame rate cap (Settings): FPS_CAP_MIN..FPS_CAP_MAX, or None for uncapped; kept for next time."""
        self.fps_cap = fps_cap_value(cap)
        pianists.set_app_setting("fps_cap", self.fps_cap)

    def settings(self):
        from app_settings import SettingsScreen
        self._switch(SettingsScreen(self))

    def close(self):
        self.mode.leave()
        self.midi.close()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
LOG_MAX_BYTES = 1_000_000   # the log starts afresh past this size


def _log_to_file():
    """
    Compiled with no console window (build.py), print() and errors would go
    nowhere: send them to a log file in the user's data folder (paths.log_path).
    """
    path = paths.log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "w" if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES else "a"
        log = open(path, mode, encoding="utf-8", buffering=1, errors="replace")
    except OSError:
        return
    log.write(f"\n--- Hand-thesia {VERSION}, {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    sys.stdout = sys.stderr = log


def _report_crash():
    """Log what went wrong and tell the user where the details are (a message box: there's no console)."""
    import traceback
    details = traceback.format_exc()
    try:
        sys.stderr.write(details)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        from tkinter import messagebox
        from common import _tk_root
        root = _tk_root()
        messagebox.showerror("Hand-thesia", "Hand-thesia ran into a problem and has to close.\n\n"
                             f"{details.strip().splitlines()[-1]}\n\nThe details are in {paths.log_path()}",
                             parent=root)
        root.destroy()
    except Exception:
        pass


def _set_window_icon():
    try:
        pygame.display.set_icon(pygame.image.load(paths.resource("assets", "icon.png")))
    except Exception:                          # (no icon is no reason not to start)
        pass


def main():
    parser = argparse.ArgumentParser(description="Hand-thesia: falling notes and fingering editor")
    parser.add_argument("--version", action="version", version=f"Hand-thesia {VERSION}")
    parser.add_argument("midi", nargs="?", help="MIDI file to open straight away")
    parser.add_argument("--edit", action="store_true", help="open the file in the fingering editor")
    parser.add_argument("--no-sound", action="store_true", help="don't play through the MIDI synth")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed (1.0 = normal)")
    parser.add_argument("--screenshot", metavar="PNG", help="render one frame to this file and exit")
    parser.add_argument("--at", type=float, default=5.0, help="song time for --screenshot (seconds)")
    parser.add_argument("--audio", metavar="FILE", help="play this recording in sync instead of the synth")
    parser.add_argument("--audio-speed", type=float, default=1.0, help="MIDI speed with --audio (fixed)")
    parser.add_argument("--audio-offset", type=float, default=0.0,
                        help="where in the recording the MIDI starts (seconds), with --audio")
    args = parser.parse_args()

    pygame.init()
    _set_window_icon()
    screen = pygame.display.set_mode(WINDOW_SIZE, pygame.RESIZABLE)
    pygame.display.set_caption(f"Hand-thesia {VERSION}")
    app = App(screen, sound=not args.no_sound and not args.screenshot, speed=args.speed)
    audio = None
    if args.midi and args.audio and not args.edit:
        from audio_sync import SyncAudio
        try:
            audio = SyncAudio(args.audio)
            audio.offset = args.audio_offset
        except Exception as exc:
            print(f"Could not open {args.audio}: {exc} - playing with the synth instead")
    if audio is not None:
        app.play(args.midi, audio=audio, speed=min(SPEED_MAX, max(SPEED_MIN, args.audio_speed)))
    elif args.midi:
        (app.edit if args.edit else app.play)(args.midi)

    if args.screenshot:
        if hasattr(app.mode, "seek"):
            app.mode.seek(args.at)
            app.mode.paused = True
        app.mode.render()
        app.draw_version()
        pygame.image.save(app.screen, args.screenshot)
        print(f"Saved {args.screenshot}")
        app.close()
        pygame.quit()
        return

    app.run()
    pygame.quit()


def _no_console():
    """True when output has nowhere to show: no console window (the compiled program, started from Explorer)."""
    try:
        return sys.stdout is None or not sys.stdout.isatty()
    except Exception:
        return True


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()            # (the soundfont player's process, compiled: sf_synth)
    if paths.COMPILED and _no_console():
        _log_to_file()
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        _report_crash()
        sys.exit(1)
