"""
app_settings.py - The Settings screen (main menu > Settings).

  * Keyboard position: the keys (and the hand area below them) drawn at their
    place in the window; click and hold the keyboard and drag it up or down.
    The highest it goes is the top of the keys at the window's centre, the
    lowest the keys' bottom half a keyboard height above the window's bottom
    (common.keyboard_y_range). Kept as a share of that range, so it holds at
    any window size ("keyboard_place" in settings.json; none = the default).
  * Keyboard type: realistic or equal keys (common.set_key_style; "keys").
  * Frame rate cap: 24 to 240 frames a second, uncapped all the way to the
    right (App.set_fps_cap; "fps_cap", none = uncapped).
  * Performance profiling: the frame rate and a translucent graph of the
    frame times in the top-left corner (App.draw_perf; "perf_overlay").
  * Soundfont: browse for a .sf2 / .sf3 file to play the notes with
    (sf_synth; App.set_soundfont; "soundfont"), or the default - the system's
    MIDI synth, also used when the chosen file can't be found or played.
"""
import os

import pygame

import pianist as pianists
from sf_synth import SF_TYPES, soundfont_name
from common import (ACCENT, BAR_BG, BG, FELT_H, KEY_STYLES, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TOP_BAR_H,
                    Button, Keyboard, Slider, blit_shadowed, pick_file, bottom_layout, draw_felt, draw_hand_area,
                    key_style, keyboard_place, keyboard_y_range, mix, set_key_style, set_keyboard_place)
from midi_loader import LEFT, RIGHT

TO_MENU = "menu"
FPS_CAP_MIN, FPS_CAP_MAX = 24, 240      # (as main.py's: the slider's top step past FPS_CAP_MAX is "uncapped")
ROW_H = 60                              # each setting's row in the panel
ROWS = 5


class SettingsScreen:
    def __init__(self, app):
        self.app = app
        self.overlay_top = TOP_BAR_H
        self.done = Button("Done", "done", font="small")
        self.reset = Button("Reset to default", "reset", font="small")
        self.perf = Button("", "perf", font="small")
        self.keys = Button("", "keys", font="small")
        self.sf_browse = Button("Browse…", "sf_browse", font="small")
        self.sf_default = Button("Use default", "sf_default", font="small")
        cap = app.fps_cap
        self.fps = Slider("", FPS_CAP_MIN, FPS_CAP_MAX + 1, FPS_CAP_MAX + 1 if cap is None else cap,
                          self._set_cap, fmt=self._cap_text, step=1, lo_label=str(FPS_CAP_MIN),
                          hi_label="uncapped")
        self.dragging = None             # the mouse's offset from the keyboard's top while it's dragged
        self.layout(app.screen.get_size())
        pygame.display.set_caption("Hand-thesia - Settings")

    def layout(self, size):
        w, h = size
        self.bar_rect = pygame.Rect(0, 0, w, TOP_BAR_H)
        self.done.rect = pygame.Rect(w - 10 - 90, 5, 90, TOP_BAR_H - 10)
        pw = min(820, w - 40)
        # the panel sits above the highest the keys can go (the window's centre)
        self.panel = pygame.Rect((w - pw) // 2, TOP_BAR_H + 16, pw, ROWS * ROW_H + 20)
        x, y = self.panel.x + 20, self.panel.y + 12
        right = self.panel.right - 20
        self.reset.rect = pygame.Rect(right - 150, y + 6, 150, 30)
        self.keys.rect = pygame.Rect(right - 150, y + ROW_H + 6, 150, 30)
        self.fps.layout(pygame.Rect(right - 240, y + 2 * ROW_H - 6, 248, 52))
        self._text_w = right - 260 - x              # (descriptions stop short of the controls)
        self.perf.rect = pygame.Rect(right - 150, y + 3 * ROW_H + 6, 150, 30)
        self.sf_default.rect = pygame.Rect(right - 110, y + 4 * ROW_H + 6, 110, 30)
        self.sf_browse.rect = pygame.Rect(right - 110 - 8 - 110, y + 4 * ROW_H + 6, 110, 30)
        self._rows = (x, y)

    # ----- the soundfont ---------------------------------------------------------
    def sound_text(self):
        """What plays the notes now - and, if the chosen soundfont can't be used, why not."""
        path = pianists.app_setting("soundfont")
        if self.app.midi.name:
            return f"Now: {self.app.midi.name}."
        problem = getattr(self.app, "soundfont_problem", "")
        if path and problem:
            return f"Now: default - {soundfont_name(path)} {problem}."
        return "Now: default (the system's MIDI synth)."

    def _browse_soundfont(self):
        path = pianists.app_setting("soundfont")
        start = os.path.dirname(path) if path else None
        chosen = pick_file("Choose a soundfont", initialdir=start, filetypes=SF_TYPES)
        if chosen:
            self.app.set_soundfont(chosen)

    # ----- the frame rate cap ----------------------------------------------------
    @staticmethod
    def _cap_text(v):
        return "uncapped" if v > FPS_CAP_MAX else f"{int(round(v))} fps"

    def _set_cap(self, v):
        self.app.set_fps_cap(None if v > FPS_CAP_MAX else int(round(v)))

    # ----- the keyboard's place ------------------------------------------------
    def _kb(self):
        return bottom_layout(self.app.screen.get_size())

    def _place_from_top(self, kb_top):
        size = self.app.screen.get_size()
        kb_rect, _ = self._kb()
        top, low = keyboard_y_range(size, kb_rect.h)
        return 0.0 if low <= top else min(1.0, max(0.0, (kb_top - top) / (low - top)))

    # ----- input -----------------------------------------------------------------
    def handle_event(self, event):
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.VIDEORESIZE:
            self.layout(event.size if hasattr(event, "size") else self.app.screen.get_size())
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return TO_MENU
        elif self.fps.handle_event(event):
            return True
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.done.hit(event.pos):
                return TO_MENU
            if self.reset.hit(event.pos):
                set_keyboard_place(None)
                pianists.set_app_setting("keyboard_place", None)
                return True
            if self.sf_browse.hit(event.pos):
                self._browse_soundfont()
                return True
            if self.sf_default.hit(event.pos):
                self.app.set_soundfont(None)
                return True
            if self.keys.hit(event.pos):
                style = KEY_STYLES[(KEY_STYLES.index(key_style()) + 1) % len(KEY_STYLES)]
                set_key_style(style)
                pianists.set_app_setting("keys", style)
                return True
            if self.perf.hit(event.pos):
                self.app.perf_overlay = not self.app.perf_overlay
                pianists.set_app_setting("perf_overlay", self.app.perf_overlay)
                return True
            kb_rect, _ = self._kb()
            grab = kb_rect.inflate(0, 2 * FELT_H).move(0, -FELT_H // 2)
            if grab.collidepoint(event.pos):
                self.dragging = event.pos[1] - kb_rect.y
        elif event.type == pygame.MOUSEMOTION and self.dragging is not None:
            set_keyboard_place(self._place_from_top(event.pos[1] - self.dragging))
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dragging is not None:
            self.dragging = None
            pianists.set_app_setting("keyboard_place", keyboard_place())
        return True

    def update(self, dt):
        pass

    def leave(self):
        pass

    # ----- drawing ---------------------------------------------------------------
    def render(self):
        s = self.app.screen
        f = self.app.fonts
        w, h = s.get_size()
        s.fill(BG)
        kb_rect, hand_rect = self._kb()
        top, low = keyboard_y_range((w, h), kb_rect.h)
        kb = Keyboard(kb_rect)
        draw_felt(s, pygame.Rect(0, kb_rect.y - FELT_H, w, FELT_H))
        kb.draw(s, {60: RIGHT, 64: RIGHT, 67: RIGHT, 48: LEFT, 55: LEFT})
        draw_hand_area(s, hand_rect)
        # where the keys' top may go: dashed lines at the highest and lowest, over everything
        for y, label in ((top, "highest"), (low, "lowest")):
            for x0 in range(0, w, 14):
                pygame.draw.line(s, mix(ACCENT, BG, 0.45), (x0, y), (min(w, x0 + 7), y))
            img = f["small"].render(label, True, mix(ACCENT, TEXT, 0.4))
            blit_shadowed(s, f["small"], label, mix(ACCENT, TEXT, 0.4), (w - img.get_width() - 10, y - img.get_height() - 2))
        mouse = pygame.mouse.get_pos()
        grab = kb_rect.inflate(0, 2 * FELT_H).move(0, -FELT_H // 2)
        if self.dragging is not None or grab.collidepoint(mouse):
            glow = pygame.Surface(kb_rect.size, pygame.SRCALPHA)
            glow.fill((*ACCENT, 40 if self.dragging is None else 70))
            s.blit(glow, kb_rect)
            pygame.draw.rect(s, ACCENT, kb_rect, 2)
            tip = "Drag up or down" if self.dragging is None else "Release to keep it here"
            blit_shadowed(s, f["normal"], tip, TEXT, (kb_rect.centerx - f["normal"].size(tip)[0] // 2,
                                                      kb_rect.y - FELT_H - f["normal"].get_height() - 6))

        # the panel
        p = self.panel
        pygame.draw.rect(s, PANEL, p, border_radius=8)
        pygame.draw.rect(s, PANEL_EDGE, p, 1, border_radius=8)
        x, y = self._rows
        where = "default" if keyboard_place() is None else f"{int(round(100 * (1 - keyboard_place())))}% up"
        rows = [("Keyboard position", f"Click and hold the keyboard, then drag it up or down. Now: {where}."),
                ("Keyboard type", "Realistic keys, or equal keys: every key, black or white, the same width."),
                ("Frame rate cap", "The most frames a second (all the way right: uncapped)."),
                ("Performance profiling", "Frame rate and a frame-time graph in the top-left corner."),
                ("Soundfont", self.sound_text())]
        for i, (title, desc) in enumerate(rows):
            ry = y + i * ROW_H
            if i:
                pygame.draw.line(s, mix(PANEL_EDGE, PANEL, 0.5), (x, ry - 8), (p.right - 20, ry - 8))
            s.blit(f["normal"].render(title, True, TEXT), (x, ry))
            clip = s.get_clip()
            s.set_clip(pygame.Rect(x, ry + 20, max(0, self._text_w), 30))
            s.blit(f["small"].render(desc, True, TEXT_DIM), (x, ry + 26))
            s.set_clip(clip)
        self.keys.label = "Equal keys" if key_style() == "equal" else "Realistic keys"
        self.perf.label = "Shown" if self.app.perf_overlay else "Hidden"
        self.perf.active = self.app.perf_overlay
        self.sf_default.enabled = bool(self.app.midi.name or pianists.app_setting("soundfont"))
        for b in (self.reset, self.keys, self.perf, self.sf_browse, self.sf_default):
            b.draw(s, f, mouse)
        self.fps.draw(s, f)

        pygame.draw.rect(s, BAR_BG, self.bar_rect)
        blit_shadowed(s, f["normal"], "Settings", TEXT,
                      (10, (self.bar_rect.h - f["normal"].get_height()) // 2))
        self.done.draw(s, f, mouse)
