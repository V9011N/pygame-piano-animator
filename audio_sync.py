"""
audio_sync.py - Play a recording in time with the MIDI and the hands.

After a MIDI file is chosen to play (PlaybackSetup), the player can use the
default sound (the MIDI synth) or a rendered audio file synced to it. For a
synced file the playback speed is chosen first and stays fixed; the audio
must be at least as long as the MIDI takes at that speed. In the player the
recording's waveform runs across the top on the MIDI's timeline, and
dragging it left or right lines the audio up with the notes
(SyncAudio.offset).

Timing: the MIDI's time t runs at `speed`; the recording at its own rate,
so the audio heard at song time t is at offset + t / speed seconds
(offset: where in the recording the MIDI's time 0 falls).
"""
from __future__ import annotations

import os

import numpy as np
import pygame

from common import (ACCENT, BAR_BG, BG, PANEL, PANEL_EDGE, SPEED_MAX, TEXT, TEXT_DIM,
                    Button, Slider, _after_dialog, _tk_root, fmt_time, run_busy)

AUDIO_TYPES = [("Audio files", "*.wav *.ogg *.mp3 *.flac"), ("All files", "*.*")]
PEAK_T = 0.005               # s, the waveform is kept as the loudest sample in each slice this long
WAVE_H = 56                  # px, the waveform strip under the player's top bar
WAVE_COLOR = (96, 150, 210)  # the recording's waveform
WAVE_DIM = (62, 82, 108)     # ...where it lies outside the MIDI


def pick_audio_file(title="Choose the audio file to sync", initialdir=None):
    """Native open dialog for an audio file; '' if cancelled or unavailable."""
    try:
        from tkinter import filedialog
        root = _tk_root()
        path = filedialog.askopenfilename(parent=root, title=title, filetypes=AUDIO_TYPES,
                                          initialdir=initialdir or None)
        root.destroy()
        return path or ""
    except Exception as exc:
        print(f"File dialog unavailable ({exc})")
        return ""
    finally:
        _after_dialog()


def ensure_mixer():
    if not pygame.mixer.get_init():
        pygame.mixer.init()


class SyncAudio:
    """A decoded recording: its waveform, and playback from any point."""

    def __init__(self, path):
        ensure_mixer()
        self.path = path
        self.name = os.path.basename(path)
        self.sound = pygame.mixer.Sound(path)        # decoded to the mixer's format
        self.length = self.sound.get_length()
        freq, size, channels = pygame.mixer.get_init()
        self.freq, self.channels = freq, channels
        self.sample_bytes = abs(size) // 8
        self.frame_bytes = self.sample_bytes * channels
        self.raw = self.sound.get_raw()
        self.offset = 0.0
        self.channel = None
        self.peaks = self._peaks()

    def _peaks(self):
        """The loudest sample (0..1) in each PEAK_T slice, all channels together."""
        dtype = {1: np.uint8, 2: np.int16, 4: np.float32 if self.sample_bytes == 4 else np.int32}[self.sample_bytes]
        a = np.frombuffer(self.raw, dtype=dtype)
        if dtype == np.uint8:
            a = a.astype(np.int16) - 128
        frames = len(a) // self.channels
        a = np.abs(a[:frames * self.channels].reshape(frames, self.channels).astype(np.float32)).max(axis=1)
        n = max(1, int(PEAK_T * self.freq))
        k = len(a) // n
        p = a[:k * n].reshape(k, n).max(axis=1) if k else np.zeros(1, np.float32)
        top = float(p.max()) or 1.0
        return p / top

    # ----- playback ------------------------------------------------------------
    def play_from(self, pos):
        """Play from `pos` seconds into the recording (nothing past its end)."""
        self.stop()
        if pos >= self.length:
            return
        start = max(0, int(pos * self.freq)) * self.frame_bytes
        clip = pygame.mixer.Sound(buffer=memoryview(self.raw)[start:])
        self.channel = clip.play()
        self._clip = clip                            # keep it alive while it plays

    def stop(self):
        if self.channel is not None:
            self.channel.stop()
            self.channel = None
        self._clip = None

    # ----- the waveform strip -----------------------------------------------------
    def strip(self, w, h, t0, t1, speed, cover):
        """
        The waveform as a w x h surface for song times t0..t1 across it (the
        player's timeline), at this offset: a column per pixel, bright where
        the MIDI (song times 0..cover) is.
        """
        surf = pygame.Surface((w, h))
        surf.fill(BAR_BG)
        xs = np.arange(w + 1)
        ts = t0 + (t1 - t0) * xs / max(1, w)                     # song time at each column edge
        n = len(self.peaks)
        idx = np.clip(((self.offset + ts / speed) / PEAK_T).astype(np.int64), 0, n - 1)
        inside = (self.offset + ts[:-1] / speed >= 0) & (self.offset + ts[1:] / speed <= self.length)
        # the loudest slice under each column (a column narrower than a slice shows that slice)
        top = np.maximum.reduceat(self.peaks, idx[:-1])
        top[-1] = self.peaks[idx[-2]:max(idx[-2] + 1, idx[-1])].max()
        mid = h / 2
        half = np.maximum(1, (top * (mid - 3)).astype(np.int64))
        for x in np.nonzero(inside)[0]:
            color = WAVE_COLOR if 0 <= ts[x] <= cover else WAVE_DIM
            pygame.draw.line(surf, color, (int(x), int(mid - half[x])), (int(x), int(mid + half[x])))
        return surf


# --------------------------------------------------------------------------- #
# The screens between choosing a MIDI file and playing it
# --------------------------------------------------------------------------- #
class PlaybackSetup:
    """Default sound, or a synced audio file (its speed chosen first, fixed after)."""

    def __init__(self, app, song, hands=None):
        self.app, self.song = app, song
        self.hands = hands                  # hands.build_hands(song), already planned
        self.page = "sound"
        self.message = ""
        self.speed = 1.0
        self.default_button = Button("Default sound", "default", font="button", key_hint="1",
                                     sub="Play the notes through the MIDI synth (speed changeable)")
        self.sync_button = Button("Sync an audio file", "sync", font="button", key_hint="2",
                                  sub="Play a recording in time with the notes and hands")
        self.back_button = Button("Back", "back", font="normal", key_hint="Esc")
        self.choose_button = Button("Choose audio file…", "choose", font="button", key_hint="Enter")
        self.slider = Slider("Playback speed (fixed once playing)", 0.25, SPEED_MAX, 1.0, self._set_speed,
                             fmt=lambda v: f"{int(round(v * 100))}%", step=0.05,
                             lo_label="25%", hi_label=f"{int(SPEED_MAX * 100)}%")
        self.layout(app.screen.get_size())

    def _set_speed(self, v):
        self.speed = round(v, 2)
        self.message = ""

    def needed(self):
        """How long the MIDI takes at the chosen speed: the shortest audio that will do."""
        return self.song.duration / self.speed

    def layout(self, size):
        w, h = size
        bw = min(560, w - 80)
        bh = max(64, min(88, int(h * 0.105)))
        y = int(h * 0.32)
        self.default_button.rect = pygame.Rect((w - bw) // 2, y, bw, bh)
        self.sync_button.rect = pygame.Rect((w - bw) // 2, y + bh + 16, bw, bh)
        self.slider.layout(pygame.Rect((w - bw) // 2, y, bw, 60))
        self.choose_button.rect = pygame.Rect((w - bw) // 2, y + 120, bw, bh)
        self.back_button.rect = pygame.Rect((w - 160) // 2, y + 2 * bh + 60, 160, 40)

    def handle_event(self, event):
        from main import TO_MENU
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.VIDEORESIZE:
            self.layout(self.app.screen.get_size())
            return True
        if self.page == "speed" and self.slider.handle_event(event):
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.page == "speed":
                    self.page, self.message = "sound", ""
                    return True
                return TO_MENU
            if self.page == "sound" and event.key == pygame.K_1:
                return self._do("default")
            if self.page == "sound" and event.key == pygame.K_2:
                return self._do("sync")
            if self.page == "speed" and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return self._do("choose")
            if self.page == "speed" and event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                v = self.slider.value + (0.05 if event.key == pygame.K_RIGHT else -0.05)
                self.slider.value = min(SPEED_MAX, max(0.25, round(v, 2)))
                self._set_speed(self.slider.value)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            buttons = [self.default_button, self.sync_button] if self.page == "sound" else [self.choose_button]
            for b in buttons + [self.back_button]:
                if b.hit(event.pos):
                    return self._do(b.action)
        return True

    def _do(self, action):
        from main import TO_MENU
        if action == "default":
            self.app.play(self.song, hands=self.hands)
        elif action == "sync":
            self.page, self.message = "speed", ""
        elif action == "back":
            if self.page == "speed":
                self.page, self.message = "sound", ""
            else:
                return TO_MENU
        elif action == "choose":
            path = pick_audio_file(initialdir=self.app.last_dir)
            if path:
                self.open_audio(path)
        return True

    def open_audio(self, path):
        """Load and check a recording; play with it if it's long enough. True if it was."""
        try:
            audio = run_busy(self.app.screen, self.app.fonts, f"Loading {os.path.basename(path)}…",
                             lambda: SyncAudio(path))
        except Exception as exc:
            self.message = f"Could not open {os.path.basename(path)}: {exc}"
            return False
        if audio.length < self.needed():
            self.message = (f"{audio.name} is {fmt_time(audio.length)} long; at {int(round(self.speed * 100))}% "
                            f"the MIDI takes {fmt_time(self.needed())}. Choose a longer file or a faster speed.")
            return False
        self.app.play(self.song, audio=audio, speed=self.speed, hands=self.hands)
        return True

    def update(self, dt):
        pass

    def leave(self):
        pass

    def render(self):
        s, f = self.app.screen, self.app.fonts
        w, h = s.get_size()
        s.fill(BG)
        title = f["title"].render(self.song.title, True, TEXT)
        top = self.default_button.rect.y
        s.blit(title, title.get_rect(midbottom=(w // 2, top - 56)))
        if self.page == "sound":
            sub = "How should it sound?"
        else:
            sub = (f"Choose the speed, then the audio file: at {int(round(self.speed * 100))}% the MIDI takes "
                   f"{fmt_time(self.needed())}, so the audio must be at least that long")
        img = f["normal"].render(sub, True, TEXT_DIM)
        s.blit(img, img.get_rect(midbottom=(w // 2, top - 22)))
        mouse = pygame.mouse.get_pos()
        if self.page == "sound":
            self.default_button.draw(s, f, mouse)
            self.sync_button.draw(s, f, mouse)
        else:
            box = self.slider.rect.inflate(24, 24)
            pygame.draw.rect(s, PANEL, box, border_radius=10)
            pygame.draw.rect(s, PANEL_EDGE, box, 1, border_radius=10)
            self.slider.draw(s, f)
            self.choose_button.draw(s, f, mouse)
        self.back_button.draw(s, f, mouse)
        if self.message:
            y = self.back_button.rect.bottom + 24
            for line in _wrap(f["normal"], self.message, min(760, w - 60)):
                img = f["normal"].render(line, True, ACCENT)
                s.blit(img, img.get_rect(midtop=(w // 2, y)))
                y += f["normal"].get_linesize()


def _wrap(font, text, width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and font.size(trial)[0] > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])
