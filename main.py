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
import math
import os
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")      # (no banner in the console or the log)
import pygame  # noqa: E402

from common import (ACCENT, KEY_STYLES, LANE_WHITE, key_style, set_key_style, PANEL, PANEL_EDGE, blit_shadowed, BAR_BG, BAR_FILL, BAR_LINE, BG, FELT_H, FPS, HAND_COLORS, LANE_LINE,
                    LEAD_IN, TEXT, TEXT_DIM, TOP_BAR_H, WINDOW_SIZE, Button, Keyboard,
                    MidiOut, Performance, Transport, bottom_layout, center_text, draw_felt,
                    draw_hand_area, draw_pianist_badge, fmt_time, load_fonts, mix, pick_file,
                    MAX_FRAME_DT, SPEED_MAX, SPEED_MIN, run_busy, wrap_text)
import paths
import pianist as pianists
from hands import build_hands, draw_hands, load_with_hands, prepare_hands
from midi_loader import LEFT, RIGHT
from audio_sync import WAVE_H, PlaybackSetup
from version import VERSION

DEFAULT_WINDOW_SECS = 3.0    # how many seconds of upcoming notes fit above the keys
WAVE_FINE = 0.1              # dragging the waveform with Shift held moves it this much slower
SEEK_STEP = 5.0
IDLE_MARGIN_T = 0.002        # s of each frame's spare time left unused (Visualizer.idle)

# What a mode's handle_event can return besides True (carry on) / False (quit)
TO_MENU = "menu"


# --------------------------------------------------------------------------- #
# The falling-notes player
# --------------------------------------------------------------------------- #
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
        self._finger_cache = {}          # (text, colour) -> rendered finger number
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
                if self.audio:
                    self.audio_muted = not self.audio_muted
                    if self.audio.channel is not None:
                        self.audio.channel.set_volume(0.0 if self.audio_muted else 1.0)
                else:
                    self.midi.muted = not self.midi.muted
                    if self.midi.muted:
                        self.midi.silence()
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
            if self.bar_rect.collidepoint(event.pos):
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

    # ----- a synced recording ----------------------------------------------------
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
            if self.audio.channel is not None and self.audio_muted:
                self.audio.channel.set_volume(0.0)
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

    def _finger_img(self, txt, color):
        img = self._finger_cache.get((txt, color))
        if img is None:
            img = self._finger_cache[(txt, color)] = self.fonts["finger"].render(txt, True, color)
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
            s.set_clip(fall)
            # White-key notes first so black-key notes draw on top of them.
            for black_pass in (False, True):
                for n in visible:
                    if n.is_black != black_pass or n.pitch not in kb.lanes:
                        continue
                    x, w = kb.lanes[n.pitch]
                    y_bottom = keyline - (n.start - t) * pps
                    y_top = keyline - (n.end - t) * pps
                    rect = pygame.Rect(x, int(y_top), w, max(4, int(y_bottom - y_top)))
                    color = HAND_COLORS[n.hand][1 if black_pass else 0]
                    if id(n) in held:
                        color = mix(color, (255, 255, 255), 0.3)
                    radius = min(5, w // 3)
                    pygame.draw.rect(s, color, rect, border_radius=radius)
                    pygame.draw.rect(s, mix(color, (0, 0, 0), 0.45), rect, 1, border_radius=radius)
                    # equal keys: every note is the same width, so a white-key note's
                    # finger number is white (a black-key note's stays dark)
                    equal_white = kb.style == "equal" and not black_pass
                    if self.show_fingers and n.hand in self.hands and rect.h >= 14:
                        finger = self.hands[n.hand].finger_for(n)
                        if self.hands[n.hand].is_gliss(n):
                            finger = "g"                 # slid in a glissando
                        if finger:
                            pos = (rect.centerx, rect.bottom - 1)
                            if equal_white:      # white number for a white key, black for a black one
                                txt = str(finger)
                                dark = self._finger_img(txt, (15, 15, 20))
                                w, h = dark.get_size()
                                at = (pos[0] - w // 2, pos[1] - h)
                                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                                    s.blit(dark, (at[0] + dx, at[1] + dy))
                                s.blit(self._finger_img(txt, (250, 250, 250)), at)
                            else:
                                img = self._finger_img(str(finger), (15, 15, 20))
                                s.blit(img, img.get_rect(midbottom=pos))

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
        s, r = self.screen, self.bar_rect
        pygame.draw.rect(s, BAR_BG, r)
        if self.song and self.song.duration > 0:
            frac = min(1.0, max(0.0, self.t / self.song.duration))
            pygame.draw.rect(s, mix(BAR_FILL, BAR_BG, 0.35), (0, 0, int(r.w * frac), r.h))
            pygame.draw.line(s, BAR_FILL, (int(r.w * frac), 0), (int(r.w * frac), r.h), 2)
            left = f"{self.song.title}    {fmt_time(self.t)} / {fmt_time(self.song.duration)}"
        else:
            left = "No song loaded"
        font = self.fonts["normal"]
        blit_shadowed(s, font, left, TEXT, (10, (r.h - font.get_height()) // 2))

        if self.audio:
            sound = "audio muted" if self.audio_muted else "synced audio"
            right = (f"speed {int(round(self.speed * 100))}% (fixed)   view {self.window_secs:.1f}s   "
                     f"{sound}      Space  ←→  , .  +/-  M  O  H  F   Esc menu")
        else:
            right = (f"speed {int(round(self.speed * 100))}%   view {self.window_secs:.1f}s   "
                     f"{self.midi.status()}      Space  ←→  ↑↓  +/-  M  O  H  F   Esc menu")
        font = self.fonts["small"]
        blit_shadowed(s, font, right, TEXT_DIM, (r.w - font.size(right)[0] - 10, (r.h - font.get_height()) // 2))


# --------------------------------------------------------------------------- #
# Main menu
# --------------------------------------------------------------------------- #
CHANGELOG = paths.resource("CHANGELOG.md")


def _seen_path():
    return os.path.join(pianists.FOLDER, "changelog_seen.txt")


def changelog_seen():
    """Whether the changelog has been opened since this version arrived."""
    try:
        with open(_seen_path(), encoding="utf-8") as fh:
            return fh.read().strip() == VERSION
    except OSError:
        return False


def mark_changelog_seen():
    try:
        os.makedirs(pianists.FOLDER, exist_ok=True)
        with open(_seen_path(), "w", encoding="utf-8") as fh:
            fh.write(VERSION)
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


class MainMenu:
    def __init__(self, app):
        self.app = app
        self.message = ""
        self.buttons = [
            Button("Play a MIDI file", "play", font="button", key_hint="P",
                   sub="Browse for a file and watch it in falling-notes mode"),
            Button("Fingering editor", "edit", font="button", key_hint="E",
                   sub="Open a MIDI file, fine-tune its fingering and export it"),
            Button("Pianists & hands", "pianists", font="button", key_hint="H",
                   sub="Create pianists: hand anatomy, colour and technique; choose the active one"),
            Button("Quit", "quit", font="normal", key_hint="Esc"),
        ]
        self.changelog_button = Button("What's new", "changelog", font="small")
        self.keys_button = Button("", "keys", font="small")
        self.changelog_new = not changelog_seen()       # glows until opened
        self.changelog = None
        self.layout(app.screen.get_size())
        pygame.display.set_caption(f"Hand-thesia {VERSION}")

    def layout(self, size):
        w, h = size
        bw = min(560, w - 80)
        bh = max(64, min(88, int(h * 0.105)))
        y = int(h * 0.27)
        for b in self.buttons[:3]:
            b.rect = pygame.Rect((w - bw) // 2, y, bw, bh)
            y += bh + 16
        self.buttons[3].rect = pygame.Rect((w - 160) // 2, y + 4, 160, 40)
        self._active_y = y + 60
        self.changelog_button.rect = pygame.Rect(w - 16 - 110, 16, 110, 32)
        self.keys_button.rect = pygame.Rect(w - 16 - 110 - 10 - 150, 16, 150, 32)

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            return False
        if self.changelog and event.type != pygame.VIDEORESIZE:
            self.changelog.handle_event(event)
            if self.changelog.closed:
                self.changelog = None
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
            if event.key in (pygame.K_h, pygame.K_3):
                return self._do("pianists")
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons + [self.changelog_button, self.keys_button]:
                if b.hit(event.pos):
                    return self._do(b.action)
        return True

    def open_changelog(self):
        self.changelog = ChangelogView(self.app.fonts)
        if self.changelog_new:
            self.changelog_new = False
            mark_changelog_seen()

    def _do(self, action):
        if action == "changelog":
            self.open_changelog()
            return True
        if action == "keys":
            style = KEY_STYLES[(KEY_STYLES.index(key_style()) + 1) % len(KEY_STYLES)]
            set_key_style(style)
            pianists.set_app_setting("keys", style)
            return True
        if action == "quit":
            return False
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
        for b in self.buttons:
            b.draw(s, f, mouse)
        # the active pianist, quietly
        act = pianists.active()
        img = f["small"].render(f"Active pianist: {act.name}  ·  {act.span_label()}", True, TEXT_DIM)
        r = img.get_rect(midtop=(w // 2 + 10, self._active_y))
        s.blit(img, r)
        pygame.draw.circle(s, act.badge_color, (r.x - 14, r.centery), 6)
        pygame.draw.circle(s, (150, 150, 160), (r.x - 14, r.centery), 6, 1)
        hint = self.message or "Tip: drop a MIDI file on this window to play it"
        img = f["small"].render(hint, True, TEXT_DIM)
        s.blit(img, img.get_rect(midbottom=(w // 2, kb.rect.y - FELT_H - 14)))
        if self.changelog_new:
            self._draw_glow(s, self.changelog_button.rect)
        self.changelog_button.draw(s, f, mouse)
        self.keys_button.label = "Keys: equal" if key_style() == "equal" else "Keys: realistic"
        self.keys_button.draw(s, f, mouse)
        if self.changelog:
            self.changelog.draw(s)

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
class App:
    def __init__(self, screen, sound=True, speed=1.0):
        self.screen = screen
        self.fonts = load_fonts()
        set_key_style(pianists.app_setting("keys", "realistic"))
        self.midi = MidiOut(sound)
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

    def edit(self, path_or_song):
        from editor import FingeringEditor
        # the editor shows the file's fingering as it is, even where it can't be played
        song, hands = self._load(path_or_song, repair=False)
        if song:
            self._switch(FingeringEditor(self, song, hands))

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
        while running:
            # A frame that took long (a file loading, the first poses being
            # solved) must not jump the song ahead: the clock only moves on
            # by at most MAX_FRAME_DT per frame, and not at all on the frame
            # right after a new mode (song) was set up.
            dt = min(clock.tick(FPS) / 1000.0, MAX_FRAME_DT)
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
            pygame.display.flip()
            # what's left of this frame's time goes to work done ahead (instead of sleeping in tick)
            spare = 1.0 / FPS - (time.perf_counter() - frame_start) - IDLE_MARGIN_T
            if spare > 0 and hasattr(self.mode, "idle"):
                self.mode.idle(spare)
        self.close()

    def draw_version(self):
        """The version in the window's bottom-left corner."""
        font = self.fonts["small"]
        blit_shadowed(self.screen, font, VERSION, TEXT_DIM, (6, self.screen.get_height() - font.get_height() - 4))

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
    if paths.COMPILED and _no_console():
        _log_to_file()
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        _report_crash()
        sys.exit(1)
