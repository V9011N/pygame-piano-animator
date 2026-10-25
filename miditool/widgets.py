"""
widgets.py - Buttons, menus, dialogs, scroll bars and tooltips, drawn in
Hand-thesia's style (its common.Button / Dialog and editor.ContextMenu).
"""
from __future__ import annotations

import pygame

from . import theme as th
from .theme import ACCENT, BUTTON, BUTTON_HOVER, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, fit_text, mix, px


def wrap_text(font, text, width):
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            t = (cur + " " + w) if cur else w
            if font.size(t)[0] <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def draw_tooltip(surf, fonts, text, anchor, below=True):
    f = fonts["small"]
    lines = wrap_text(f, text, px(420))
    w = max(f.size(t)[0] for t in lines) + px(16)
    h = len(lines) * f.get_linesize() + px(10)
    x = min(max(px(4), anchor.centerx - w // 2), surf.get_width() - w - px(4))
    y = anchor.bottom + px(6) if below else anchor.top - h - px(6)
    if y + h > surf.get_height() - px(4):
        y = anchor.top - h - px(6)
    r = pygame.Rect(x, max(px(2), y), w, h)
    shadow = pygame.Surface((r.w + px(6), r.h + px(6)), pygame.SRCALPHA)
    shadow.fill((0, 0, 0, 80))
    surf.blit(shadow, (r.x + px(2), r.y + px(3)))
    pygame.draw.rect(surf, (52, 52, 64), r, border_radius=px(6))
    pygame.draw.rect(surf, PANEL_EDGE, r, 1, border_radius=px(6))
    for i, t in enumerate(lines):
        surf.blit(f.render(t, True, TEXT), (r.x + px(8), r.y + px(5) + i * f.get_linesize()))


class Button:
    def __init__(self, label, action, tip=None, toggle=False, font="small", icon=None):
        self.label, self.action, self.tip, self.toggle, self.font = label, action, tip, toggle, font
        self.icon = icon                      # a callable drawing into a rect (surf, rect, color)
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.enabled = True
        self.active = False
        self.visible = True
        self.badge = None                     # small count drawn at the right (impose: pending edits)

    def width(self, fonts):
        w = fonts[self.font].size(self.label)[0] + px(22) if self.label else 0
        if self.icon:
            w += px(18) + (px(4) if self.label else px(10))
        return w

    def hit(self, pos):
        return self.visible and self.enabled and self.rect.collidepoint(pos)

    def draw(self, surf, fonts, mouse, pressed=False):
        if not self.visible:
            return
        hover = self.hit(mouse)
        base = BUTTON_HOVER if hover else BUTTON
        if self.active:
            base = mix(base, ACCENT, 0.35)
        if pressed and hover:
            base = mix(base, (0, 0, 0), 0.2)
        if not self.enabled:
            base = mix(BUTTON, th.BG, 0.5)
        r = px(7)
        pygame.draw.rect(surf, base, self.rect, border_radius=r)
        pygame.draw.rect(surf, mix(base, (255, 255, 255), 0.16), self.rect, 1, border_radius=r)
        color = TEXT if self.enabled else TEXT_DIM
        x = self.rect.x + px(11)
        if self.icon:
            ir = pygame.Rect(0, 0, px(14), px(14))
            if self.label:
                ir.midleft = (self.rect.x + px(10), self.rect.centery)
                x = ir.right + px(6)
            else:
                ir.center = self.rect.center
            self.icon(surf, ir, color)
        if self.label:
            f = fonts[self.font]
            room = self.rect.right - px(8) - x
            key = (self.label, self.font, color, room, th.S)
            if getattr(self, "_img_key", None) != key:
                self._img_key = key
                self._img = f.render(fit_text(f, self.label, room), True, color)
            img = self._img
            if self.icon:
                surf.blit(img, img.get_rect(midleft=(x, self.rect.centery)))
            else:
                surf.blit(img, img.get_rect(center=self.rect.center))
        if self.badge:
            f = fonts["tiny"]
            img = f.render(str(self.badge), True, (20, 20, 24))
            b = img.get_rect()
            b.w += px(8)
            b.h += px(2)
            b.center = (self.rect.right - px(4), self.rect.top + px(3))
            pygame.draw.rect(surf, ACCENT, b, border_radius=b.h // 2)
            surf.blit(img, img.get_rect(center=b.center))


# ----------------------------------------------------------------------------- #
# Icons (small vector glyphs for buttons)
# ----------------------------------------------------------------------------- #
def icon_play(surf, r, c):
    pygame.draw.polygon(surf, c, [(r.x + px(2), r.y), (r.right - px(1), r.centery), (r.x + px(2), r.bottom)])


def icon_pause(surf, r, c):
    w = max(2, r.w // 3)
    pygame.draw.rect(surf, c, (r.x + px(1), r.y, w, r.h))
    pygame.draw.rect(surf, c, (r.right - w - px(1), r.y, w, r.h))


def icon_stop(surf, r, c):
    pygame.draw.rect(surf, c, r.inflate(-px(2), -px(2)))


def icon_pencil(surf, r, c):
    pts = [(r.x + px(2), r.bottom - px(2)), (r.x + px(2), r.bottom - px(6)), (r.right - px(5), r.y + px(1)),
           (r.right - px(1), r.y + px(5)), (r.x + px(6), r.bottom - px(2))]
    pygame.draw.polygon(surf, c, pts, max(1, px(2)))


def icon_undo(surf, r, c, flip=False):
    """A hooked arrow: undo points back (left), redo forward (right)."""
    w = max(1, px(2))
    y_top, y_bot = r.y + px(3), r.bottom - px(2)
    a, b = (r.x + px(3), r.right - px(3)) if not flip else (r.right - px(3), r.x + px(3))
    bend = r.right - px(1) if not flip else r.x + px(1)
    mid = (y_top + y_bot) // 2
    # the shaft from the tip across the top, round the bend and back under it
    pygame.draw.line(surf, c, (a, y_top), (b - (px(2) if not flip else -px(2)), y_top), w)
    rect = pygame.Rect(0, 0, 2 * (mid - y_top), 2 * (mid - y_top))
    rect.center = (b - (px(2) if not flip else -px(2)), mid)
    import math
    if not flip:
        pygame.draw.arc(surf, c, rect, -math.pi / 2, math.pi / 2, w)
        pygame.draw.line(surf, c, (rect.centerx, rect.bottom - 1), (r.x + px(6), rect.bottom - 1), w)
        pygame.draw.polygon(surf, c, [(r.x, y_top), (r.x + px(5), y_top - px(4)), (r.x + px(5), y_top + px(4))])
    else:
        pygame.draw.arc(surf, c, rect, math.pi / 2, 3 * math.pi / 2, w)
        pygame.draw.line(surf, c, (rect.centerx, rect.bottom - 1), (r.right - px(6), rect.bottom - 1), w)
        pygame.draw.polygon(surf, c, [(r.right, y_top), (r.right - px(5), y_top - px(4)), (r.right - px(5), y_top + px(4))])


def icon_redo(surf, r, c):
    icon_undo(surf, r, c, flip=True)


def icon_select_left(surf, r, c):
    pygame.draw.line(surf, c, (r.right - px(2), r.y), (r.right - px(2), r.bottom), max(1, px(2)))
    pygame.draw.polygon(surf, c, [(r.x, r.centery), (r.right - px(6), r.y + px(2)), (r.right - px(6), r.bottom - px(2))])


def icon_select_right(surf, r, c):
    pygame.draw.line(surf, c, (r.x + px(1), r.y), (r.x + px(1), r.bottom), max(1, px(2)))
    pygame.draw.polygon(surf, c, [(r.right, r.centery), (r.x + px(5), r.y + px(2)), (r.x + px(5), r.bottom - px(2))])


# ----------------------------------------------------------------------------- #
# Context menu (one level of submenus) - Hand-thesia's editor.ContextMenu
# ----------------------------------------------------------------------------- #
class MenuItem:
    def __init__(self, label, value=None, children=None, checked=False, hint=None, enabled=True, key=None,
                 separator=False, color=None):
        self.label, self.value, self.children = label, value, children
        self.checked, self.hint, self.enabled, self.key = checked, hint, enabled, key
        self.separator, self.color = separator, color
        self.rect = pygame.Rect(0, 0, 0, 0)


SEPARATOR = MenuItem("", separator=True, enabled=False)


class ContextMenu:
    def __init__(self, pos, title, items, on_pick, fonts, bounds, sub_title=None):
        self.title, self.items, self.on_pick, self.fonts = title, items, on_pick, fonts
        self.sub_title = sub_title
        self.bounds = pygame.Rect(bounds)
        self.child = None
        self.rect = self._place(pos)

    def _item_h(self, it):
        return px(9) if it.separator else px(26)

    def _place(self, pos, flip_from=None):
        f = self.fonts
        w = max([f["normal"].size(it.label)[0] + (f["small"].size(it.hint)[0] + px(18) if it.hint else 0)
                 for it in self.items] + [f["small"].size(self.title)[0] if self.title else 0]) + px(58)
        title_h = px(22) if self.title else 0
        h = sum(self._item_h(it) for it in self.items) + 2 * px(7) + title_h
        r = pygame.Rect(pos[0], pos[1], w, h)
        if r.right > self.bounds.right:
            r.x = (flip_from - w) if flip_from is not None else self.bounds.right - w
        if r.bottom > self.bounds.bottom:
            r.y = max(self.bounds.y, self.bounds.bottom - h)
        y = r.y + px(7) + title_h
        for it in self.items:
            it.rect = pygame.Rect(r.x + px(4), y, r.w - px(8), self._item_h(it))
            y += self._item_h(it)
        return r

    def contains(self, pos):
        return self.rect.collidepoint(pos) or (self.child is not None and self.child[1].contains(pos))

    def _open_child(self, i):
        it = self.items[i]
        if not it.children:
            self.child = None
            return
        if self.child and self.child[0] == i:
            return
        title = it.label if self.sub_title is None else self.sub_title
        sub = ContextMenu((self.rect.right - px(2), it.rect.y - px(7) - px(22)), title, it.children,
                          None, self.fonts, self.bounds)
        if sub.rect.x < self.rect.right - px(2):
            sub.rect = sub._place((self.rect.x - sub.rect.w + px(2), it.rect.y - px(7) - px(22)),
                                  flip_from=self.rect.x + px(2))
        self.child = (i, sub)

    def motion(self, pos):
        if self.child and self.child[1].rect.collidepoint(pos):
            self.child[1].motion(pos)
            return
        for i, it in enumerate(self.items):
            if it.enabled and not it.separator and it.rect.collidepoint(pos):
                if it.children:
                    self._open_child(i)
                elif self.child:
                    self.child = None

    def click(self, pos):
        """True when the menu is done (something was picked, or the click missed it)."""
        if self.child and self.child[1].rect.collidepoint(pos):
            for it in self.child[1].items:
                if it.enabled and not it.separator and it.rect.collidepoint(pos):
                    self.on_pick(self.items[self.child[0]].value, it.value)
                    return True
            return False
        for i, it in enumerate(self.items):
            if it.enabled and not it.separator and it.rect.collidepoint(pos):
                if it.children:
                    self._open_child(i)
                    return False
                self.on_pick(it.value, None)
                return True
        return not self.rect.collidepoint(pos)

    def key(self, key):
        """Item shortcuts: True when something was picked."""
        if self.child:
            for it in self.child[1].items:
                if it.enabled and it.key == key:
                    self.on_pick(self.items[self.child[0]].value, it.value)
                    return True
        for i, it in enumerate(self.items):
            if it.enabled and it.key == key:
                if it.children:
                    self._open_child(i)
                    return False
                self.on_pick(it.value, None)
                return True
        return False

    def draw(self, surf, mouse):
        f = self.fonts
        shadow = pygame.Surface((self.rect.w + px(8), self.rect.h + px(8)), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 80))
        surf.blit(shadow, (self.rect.x + px(2), self.rect.y + px(4)))
        pygame.draw.rect(surf, PANEL, self.rect, border_radius=px(8))
        pygame.draw.rect(surf, PANEL_EDGE, self.rect, 1, border_radius=px(8))
        if self.title:
            img = f["small"].render(fit_text(f["small"], self.title, self.rect.w - px(24)), True, TEXT_DIM)
            surf.blit(img, (self.rect.x + px(14), self.rect.y + px(9)))
        for i, it in enumerate(self.items):
            if it.separator:
                y = it.rect.centery
                pygame.draw.line(surf, PANEL_EDGE, (it.rect.x + px(8), y), (it.rect.right - px(8), y))
                continue
            open_ = self.child is not None and self.child[0] == i
            if (it.rect.collidepoint(mouse) or open_) and it.enabled:
                pygame.draw.rect(surf, mix(PANEL, ACCENT, 0.28), it.rect, border_radius=px(5))
            color = (it.color or TEXT) if it.enabled else TEXT_DIM
            if it.checked:
                pygame.draw.circle(surf, ACCENT, (it.rect.x + px(12), it.rect.centery), px(4))
            img = f["normal"].render(it.label, True, color)
            surf.blit(img, img.get_rect(midleft=(it.rect.x + px(26), it.rect.centery)))
            if it.hint:
                h = f["small"].render(it.hint, True, TEXT_DIM)
                surf.blit(h, h.get_rect(midright=(it.rect.right - (px(22) if it.children else px(10)),
                                                  it.rect.centery)))
            if it.children:
                x, y = it.rect.right - px(12), it.rect.centery
                pygame.draw.polygon(surf, color, [(x - px(4), y - px(5)), (x - px(4), y + px(5)), (x + px(2), y)])
        if self.child:
            self.child[1].draw(surf, mouse)


# ----------------------------------------------------------------------------- #
# Dialogs
# ----------------------------------------------------------------------------- #
class Dialog:
    """A modal box with a message and buttons; `choice` is set when one is clicked (Esc: "cancel")."""

    def __init__(self, title, message, options, default=None, width=560):
        self.title, self.message = title, message
        self.buttons = [Button(label, value, font="normal") for label, value in options]
        self.default = default if default is not None else (options[0][1] if options else None)
        self.choice = None
        self.width = width
        self.box = pygame.Rect(0, 0, 0, 0)

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons:
                if b.hit(event.pos):
                    self.choice = b.action
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.choice = "cancel"
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.default is not None:
                self.choice = self.default

    def _layout(self, surf, fonts, extra_h=0):
        w, h = surf.get_size()
        bw_box = min(px(self.width), w - px(40))
        lines = wrap_text(fonts["normal"], self.message, bw_box - px(48))
        line_h = fonts["normal"].get_linesize()
        box = pygame.Rect(0, 0, bw_box, px(150) + len(lines) * line_h + extra_h)
        box.center = (w // 2, h // 2)
        return box, lines, line_h

    def draw(self, surf, fonts, extra_h=0):
        w, h = surf.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 140))
        surf.blit(shade, (0, 0))
        box, lines, line_h = self._layout(surf, fonts, extra_h)
        self.box = box
        pygame.draw.rect(surf, PANEL, box, border_radius=px(12))
        pygame.draw.rect(surf, PANEL_EDGE, box, 1, border_radius=px(12))
        surf.blit(fonts["button"].render(fit_text(fonts["button"], self.title, box.w - px(48)), True, TEXT),
                  (box.x + px(24), box.y + px(20)))
        for i, line in enumerate(lines):
            surf.blit(fonts["normal"].render(line, True, TEXT_DIM), (box.x + px(24), box.y + px(60) + i * line_h))
        gap = px(10)
        widths = [max(px(110), b.width(fonts) + px(16)) for b in self.buttons]
        x = box.right - px(24) - sum(widths) - gap * (len(self.buttons) - 1)
        mouse = pygame.mouse.get_pos()
        for b, bw in zip(self.buttons, widths):
            b.rect = pygame.Rect(x, box.bottom - px(22) - px(38), bw, px(38))
            b.active = b.action == self.default
            b.draw(surf, fonts, mouse)
            x += bw + gap
        return box, lines, line_h


class PromptDialog(Dialog):
    """A Dialog with one line of text to type (e.g. a tempo); `text` is it when a button other than Cancel is chosen
    (the buttons: OK and Cancel, or `options`; Enter chooses the first)."""

    def __init__(self, title, message, text="", validate=None, options=None, width=460):
        options = options or [("OK", "ok"), ("Cancel", "cancel")]
        super().__init__(title, message, options, default=options[0][1], width=width)
        self.text = text
        self.validate = validate or (lambda t: None)
        self.error = ""
        self.select_all = True

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.text = "" if self.select_all else self.text[:-1]
                self.select_all = False
                return
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._try(self.default)
                return
            if event.key == pygame.K_ESCAPE:
                self.choice = "cancel"
                return
        elif event.type == pygame.TEXTINPUT:
            self.text = (event.text if self.select_all else self.text + event.text)[:24]
            self.select_all = False
            return
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons:
                if b.hit(event.pos):
                    if b.action == "cancel":
                        self.choice = "cancel"
                    else:
                        self._try(b.action)
            return

    def _try(self, choice):
        err = self.validate(self.text)
        if err:
            self.error = err
        else:
            self.choice = choice

    def draw(self, surf, fonts, extra_h=0):
        box, lines, line_h = super().draw(surf, fonts, extra_h=px(48))
        field = pygame.Rect(box.x + px(24), box.y + px(64) + len(lines) * line_h, box.w - px(48), px(34))
        pygame.draw.rect(surf, (24, 24, 30), field, border_radius=px(6))
        pygame.draw.rect(surf, ACCENT, field, 1, border_radius=px(6))
        f = fonts["normal"]
        img = f.render(self.text, True, TEXT)
        if self.select_all and self.text:
            sel = img.get_rect(midleft=(field.x + px(10), field.centery)).inflate(px(4), px(2))
            pygame.draw.rect(surf, mix((24, 24, 30), ACCENT, 0.35), sel)
        surf.blit(img, img.get_rect(midleft=(field.x + px(10), field.centery)))
        if not self.select_all and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = field.x + px(10) + img.get_width() + px(1)
            pygame.draw.line(surf, TEXT, (x, field.y + px(7)), (x, field.bottom - px(7)))
        if self.error:
            e = fonts["small"].render(self.error, True, th.BAD)
            surf.blit(e, (field.x, field.bottom + px(4)))


# ----------------------------------------------------------------------------- #
# Scroll bars
# ----------------------------------------------------------------------------- #
class ScrollBar:
    """A track with a thumb for one axis: the content spans lo..hi, the view vlo..vhi."""

    def __init__(self, vertical=False):
        self.vertical = vertical
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.drag = None                     # (grab offset along the bar in content units)
        self.hot = False
        self.lo, self.hi, self.vlo, self.vhi = 0.0, 1.0, 0.0, 1.0

    def set(self, lo, hi, vlo, vhi):
        self.lo, self.hi = lo, max(hi, lo + 1e-9)
        self.vlo, self.vhi = vlo, vhi

    def _len(self):
        return self.rect.h if self.vertical else self.rect.w

    def _span(self):
        return self.hi - self.lo

    def thumb(self):
        n = self._len()
        span = self._span()
        a = (min(max(self.vlo, self.lo), self.hi) - self.lo) / span
        b = (max(min(self.vhi, self.hi), self.lo) - self.lo) / span
        size = max(px(24), int((b - a) * n))
        start = int(a * n)
        start = min(start, n - size)
        if self.vertical:
            # (content runs bottom-up for pitches: lo at the bottom)
            return pygame.Rect(self.rect.x + px(2), self.rect.bottom - start - size, self.rect.w - px(4), size)
        return pygame.Rect(self.rect.x + start, self.rect.y + px(2), size, self.rect.h - px(4))

    def value_at(self, pos):
        """Content position at a screen point along the bar."""
        if self.vertical:
            f = (self.rect.bottom - pos[1]) / max(1, self.rect.h)
        else:
            f = (pos[0] - self.rect.x) / max(1, self.rect.w)
        return self.lo + f * self._span()

    def press(self, pos):
        """'drag' (thumb grabbed), 'page' (track clicked: caller pages toward value_at), or None."""
        if not self.rect.collidepoint(pos):
            return None
        if self.thumb().collidepoint(pos):
            self.drag = self.value_at(pos) - self.vlo
            return "drag"
        return "page"

    def drag_to(self, pos):
        """New view start (vlo) while the thumb is dragged."""
        return self.value_at(pos) - self.drag

    def draw(self, surf, mouse):
        pygame.draw.rect(surf, th.SCROLL_TRACK, self.rect)
        t = self.thumb()
        hot = self.drag is not None or t.collidepoint(mouse)
        r = min(t.w, t.h) // 2
        pygame.draw.rect(surf, th.SCROLL_THUMB_HOT if hot else th.SCROLL_THUMB, t, border_radius=r)
