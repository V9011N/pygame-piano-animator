"""
main.py - Piano Animator: falling-notes player and fingering editor, built on Pygame.

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

The editor's controls are listed in editor.py (and shown in the editor itself).

Layout of the player, top to bottom: progress bar, falling-notes area,
keyboard, and the hand area. The hand skeletons (hands.py, both hands) are
drawn over the keyboard and hand area.
"""
from __future__ import annotations

import argparse
import math
import os

import pygame

from common import (ACCENT, PANEL, PANEL_EDGE, blit_shadowed, BAR_BG, BAR_FILL, BAR_LINE, BG, FELT_H, FPS, HAND_COLORS, LANE_LINE,
                    LEAD_IN, TEXT, TEXT_DIM, TOP_BAR_H, WINDOW_SIZE, Button, Keyboard,
                    MidiOut, Performance, Transport, bottom_layout, center_text, draw_felt,
                    draw_hand_area, draw_pianist_badge, fmt_time, load_fonts, mix, pick_file,
                    MAX_FRAME_DT, show_loading)
import pianist as pianists
from hands import HandAnimator, draw_hands, pair_hands
from midi_loader import LEFT, RIGHT, load_song
from version import VERSION

DEFAULT_WINDOW_SECS = 3.0    # how many seconds of upcoming notes fit above the keys
SEEK_STEP = 5.0

# What a mode's handle_event can return besides True (carry on) / False (quit)
TO_MENU = "menu"


# --------------------------------------------------------------------------- #
# The falling-notes player
# --------------------------------------------------------------------------- #
class Visualizer(Transport):
    def __init__(self, screen, song=None, midi=None, speed=1.0, fonts=None):
        self.screen = screen
        self.fonts = fonts or load_fonts()
        self._init_transport(midi or MidiOut(False), speed)
        self.window_secs = DEFAULT_WINDOW_SECS
        self.dragging_bar = False
        self._glow_cache = {}
        self.hands = {}
        self.show_hands = True
        self.show_fingers = True
        self.layout(screen.get_size())
        if song:
            self.set_song(song)

    # ----- setup -------------------------------------------------------------
    def set_song(self, song):
        self.midi.all_off()
        self.song = song
        self.hands = {h: HandAnimator(song, h) for h in (RIGHT, LEFT)
                      if any(n.hand == h for n in song.notes)}
        pair_hands(self.hands.values())
        # what's heard and the keys that go down follow what the hands play
        self.perf = Performance.from_animators(self.hands.values()) if self.hands else None
        self.t = -LEAD_IN
        self.paused = False
        self.sounding = {}
        pygame.display.set_caption(f"Piano Animator {VERSION} - {song.title}")

    def layout(self, size):
        w, h = size
        kb_rect, self.hand_rect = bottom_layout(size)
        self.bar_rect = pygame.Rect(0, 0, w, TOP_BAR_H)
        self.fall_rect = pygame.Rect(0, TOP_BAR_H, w, kb_rect.y - FELT_H - TOP_BAR_H)
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
            self.open_file(event.file)
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
            elif k == pygame.K_UP:
                self.change_speed(0.1)
            elif k == pygame.K_DOWN:
                self.change_speed(-0.1)
            elif k in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.window_secs = max(0.75, self.window_secs / 1.25)
            elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.window_secs = min(12.0, self.window_secs * 1.25)
            elif k == pygame.K_m:
                self.midi.muted = not self.midi.muted
                if self.midi.muted:
                    self.midi.all_off()
            elif k == pygame.K_h:
                self.show_hands = not self.show_hands
            elif k == pygame.K_f:
                self.show_fingers = not self.show_fingers
            elif k in (pygame.K_HOME, pygame.K_r):
                self.seek(-LEAD_IN)
            elif k == pygame.K_o:
                path = pick_file()
                if path:
                    self.open_file(path)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.bar_rect.collidepoint(event.pos):
                self.dragging_bar = True
                self._seek_to_x(event.pos[0])
        elif event.type == pygame.MOUSEMOTION and self.dragging_bar:
            self._seek_to_x(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging_bar = False
        return True

    def _seek_to_x(self, x):
        if self.song and self.song.duration > 0:
            frac = min(1.0, max(0.0, x / self.bar_rect.w))
            self.seek(frac * self.song.duration)

    def open_file(self, path):
        show_loading(self.screen, self.fonts, f"Loading {os.path.basename(path)}…")
        try:
            self.set_song(load_song(path))
        except Exception as exc:
            print(f"Could not load {path}: {exc}")

    def leave(self):
        self.midi.all_off()

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

    def render(self):
        s = self.screen
        s.fill(BG)
        fall = self.fall_rect
        kb = self.keyboard

        for x in kb.lane_lines:
            pygame.draw.line(s, LANE_LINE, (x, fall.top), (x, fall.bottom))

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
                    if self.show_fingers and n.hand in self.hands and rect.h >= 14:
                        finger = self.hands[n.hand].finger_for(n)
                        if finger:
                            img = self.fonts["finger"].render(str(finger), True, (15, 15, 20))
                            s.blit(img, img.get_rect(midbottom=(rect.centerx, rect.bottom - 1)))

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
                           self.sustain_down() if self.song and self.song.controls else None)
        self._draw_top_bar()

        if not self.song:
            center_text(s, self.fonts, self.fall_rect, "Drop a MIDI file here, or press O to open one")
        elif self.paused:
            center_text(s, self.fonts, self.fall_rect, "Paused  -  Space to play")

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

        right = (f"speed {int(round(self.speed * 100))}%   view {self.window_secs:.1f}s   "
                 f"{self.midi.status()}      Space  ←→  ↑↓  +/-  M  O  H  F   Esc menu")
        font = self.fonts["small"]
        blit_shadowed(s, font, right, TEXT_DIM, (r.w - font.size(right)[0] - 10, (r.h - font.get_height()) // 2))


# --------------------------------------------------------------------------- #
# Main menu
# --------------------------------------------------------------------------- #
CHANGELOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")


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


def wrap_text(font, text, width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and font.size(trial)[0] > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


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
                rows.append((f["button"], ACCENT if heading.startswith(VERSION) else TEXT, heading, 0, 8))
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
        self.changelog_new = not changelog_seen()       # glows until opened
        self.changelog = None
        self.layout(app.screen.get_size())
        pygame.display.set_caption(f"Piano Animator {VERSION}")

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
            self.app.play(event.file)
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
            for b in self.buttons + [self.changelog_button]:
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
        if action == "quit":
            return False
        if action == "pianists":
            self.app.studio()
            return True
        path = pick_file("Open MIDI file" if action == "play" else "Open MIDI file to edit",
                         initialdir=self.app.last_dir)
        if path:
            (self.app.play if action == "play" else self.app.edit)(path)
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
        title = f["title"].render("Piano Animator", True, TEXT)
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
        self.midi = MidiOut(sound)
        self.speed = speed
        self.last_dir = None
        self._fresh = True
        self.mode = MainMenu(self)

    def _switch(self, mode):
        self.mode.leave()
        self.mode = mode
        self._fresh = True

    def menu(self, message=""):
        if hasattr(self.mode, "speed"):
            self.speed = self.mode.speed
        self._switch(MainMenu(self))
        self.mode.message = message

    def _load(self, path):
        show_loading(self.screen, self.fonts, f"Loading {os.path.basename(path)}…")
        try:
            song = load_song(path)
        except Exception as exc:
            print(f"Could not load {path}: {exc}")
            if isinstance(self.mode, MainMenu):
                self.mode.message = f"Could not open {os.path.basename(path)}: {exc}"
            return None
        self.last_dir = os.path.dirname(os.path.abspath(path))
        return song

    def play(self, path_or_song):
        song = self._load(path_or_song) if isinstance(path_or_song, str) else path_or_song
        if song:
            self._switch(Visualizer(self.screen, song, midi=self.midi, speed=self.speed,
                                    fonts=self.fonts))

    def edit(self, path_or_song):
        from editor import FingeringEditor
        song = self._load(path_or_song) if isinstance(path_or_song, str) else path_or_song
        if song:
            self._switch(FingeringEditor(self, song))

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
            self.mode.update(dt)
            self.mode.render()
            self.draw_version()
            pygame.display.flip()
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
def main():
    parser = argparse.ArgumentParser(description="Piano Animator: falling notes and fingering editor")
    parser.add_argument("midi", nargs="?", help="MIDI file to open straight away")
    parser.add_argument("--edit", action="store_true", help="open the file in the fingering editor")
    parser.add_argument("--no-sound", action="store_true", help="don't play through the MIDI synth")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed (1.0 = normal)")
    parser.add_argument("--screenshot", metavar="PNG", help="render one frame to this file and exit")
    parser.add_argument("--at", type=float, default=5.0, help="song time for --screenshot (seconds)")
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE, pygame.RESIZABLE)
    pygame.display.set_caption(f"Piano Animator {VERSION}")
    app = App(screen, sound=not args.no_sound and not args.screenshot, speed=args.speed)
    if args.midi:
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


if __name__ == "__main__":
    main()
