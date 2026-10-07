"""
app_settings.py - The Settings screen (main menu > Settings).

  * Keyboard position: the keys (and the hand area below them) drawn at their
    place in the window; click and hold the keyboard and drag it up or down.
    The highest it goes is the top of the keys at the window's centre, the
    lowest the keys' bottom half a keyboard height above the window's bottom
    (common.keyboard_y_range). Kept as a share of that range, so it holds at
    any window size ("keyboard_place" in settings.json; none = the default).
  * Performance profiling: the frame rate and a translucent graph of the
    frame times in the top-left corner (App.draw_perf; "perf_overlay").
"""
import pygame

import pianist as pianists
from common import (ACCENT, BAR_BG, BG, FELT_H, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TOP_BAR_H, Button,
                    Keyboard, blit_shadowed, bottom_layout, draw_felt, draw_hand_area, keyboard_place,
                    keyboard_y_range, mix, set_keyboard_place)
from midi_loader import LEFT, RIGHT

TO_MENU = "menu"


class SettingsScreen:
    def __init__(self, app):
        self.app = app
        self.overlay_top = TOP_BAR_H
        self.done = Button("Done", "done", font="small")
        self.reset = Button("Reset to default", "reset", font="small")
        self.perf = Button("", "perf", font="small")
        self.dragging = None             # the mouse's offset from the keyboard's top while it's dragged
        self.layout(app.screen.get_size())
        pygame.display.set_caption("Hand-thesia - Settings")

    def layout(self, size):
        w, h = size
        self.bar_rect = pygame.Rect(0, 0, w, TOP_BAR_H)
        self.done.rect = pygame.Rect(w - 10 - 90, 5, 90, TOP_BAR_H - 10)
        pw = min(640, w - 40)
        # the panel sits above the highest the keys can go (the window's centre)
        self.panel = pygame.Rect((w - pw) // 2, TOP_BAR_H + 16, pw, 166)
        x, y = self.panel.x + 20, self.panel.y
        self.reset.rect = pygame.Rect(self.panel.right - 20 - 150, y + 18, 150, 30)
        self.perf.rect = pygame.Rect(self.panel.right - 20 - 150, y + 108, 150, 30)
        self._rows = (x, y)

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
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.done.hit(event.pos):
                return TO_MENU
            if self.reset.hit(event.pos):
                set_keyboard_place(None)
                pianists.set_app_setting("keyboard_place", None)
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
        s.blit(f["normal"].render("Keyboard position", True, TEXT), (x, y + 16))
        s.blit(f["small"].render("Click and hold the keyboard, then drag it up or down.", True, TEXT_DIM),
               (x, y + 42))
        where = "default" if keyboard_place() is None else f"{int(round(100 * (1 - keyboard_place())))}% up"
        s.blit(f["small"].render(f"Now: {where}", True, TEXT_DIM), (x, y + 62))
        pygame.draw.line(s, mix(PANEL_EDGE, PANEL, 0.5), (x, y + 92), (p.right - 20, y + 92))
        s.blit(f["normal"].render("Performance profiling", True, TEXT), (x, y + 106))
        s.blit(f["small"].render("Frame rate and a frame-time graph in the top-left corner.", True, TEXT_DIM),
               (x, y + 132))
        self.perf.label = "Shown" if self.app.perf_overlay else "Hidden"
        self.perf.active = self.app.perf_overlay
        for b in (self.reset, self.perf):
            b.draw(s, f, mouse)

        pygame.draw.rect(s, BAR_BG, self.bar_rect)
        blit_shadowed(s, f["normal"], "Settings", TEXT,
                      (10, (self.bar_rect.h - f["normal"].get_height()) // 2))
        self.done.draw(s, f, mouse)
