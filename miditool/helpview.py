"""
helpview.py - Every shortcut on one page (F1).
"""
from __future__ import annotations

import pygame

from . import theme as th
from .theme import ACCENT, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, px

SECTIONS = [
    ("Files", [
        ("Ctrl+O", "open a MIDI file or project (or drop one on the window)"),
        ("Open MIDI…", "or add one: on top (new channels), or appended"),
        ("Ctrl+S", "save the project (Ctrl+Shift+S: save as…)"),
        ("Ctrl+E", "export MIDI - every note's fingering goes with it"),
        ("Ctrl+I", "impose a perfect source"),
    ]),
    ("Moving around", [
        ("Wheel", "scroll through time (sideways swipes too)"),
        ("Shift+wheel", "scroll up and down the keyboard"),
        ("Ctrl+wheel", "zoom time at the mouse (over the keys: rows)"),
        ("Alt+wheel", "zoom rows at the mouse"),
        ("Middle-drag", "pan in any direction"),
        ("Z / Shift+Z", "fit everything / the selection"),
        ("+ / -", "zoom time"),
    ]),
    ("Notes", [
        ("Click", "select (Shift / Ctrl+click: add or remove)"),
        ("Drag empty space", "select every note in the box"),
        ("Drag a note", "move (Ctrl: copy); its edges: length"),
        ("Alt while dragging", "no snapping to the grid"),
        ("Double-click / P", "add a note / Draw mode"),
        ("Delete", "remove the selected notes"),
        ("Arrows", "move them (Up/Down: semitones, Shift: octaves)"),
        ("Ctrl+C X V D", "copy, cut, paste at the cursor, duplicate"),
        ("[  /  ]", "select left / right of the cursor"),
        ("Ctrl+Z / Ctrl+Y", "undo / redo"),
    ]),
    ("Playing", [
        ("Space", "play / pause from the cursor"),
        ("Ruler", "click or drag to move the cursor"),
        ("Home / End", "cursor to the start / end"),
    ]),
    ("Lanes", [
        ("Velocity", "drag across the bars (only the selection, if any)"),
        ("Pedal", "click Sus / Sos / Soft; drag to draw (Shift: on/off), right-drag to lift"),
        ("Tempo", "drag a keyframe; double-click adds one; right-click: its curve"),
        ("Tempo header", "click the BPM to type it, the curve under it to change it"),
        ("Bake…", "one tempo for all: notes kept in place, or in time"),
        ("Divider", "drag to give the lanes more or less room"),
    ]),
    ("Fingering", [
        ("F", "show it - solid: the note's own, hollow: suggested"),
        ("Fingering…", "profiles: scales, octaves, thirds, sixths, trills..."),
        ("Right-click", "hand and finger; several notes: one hand"),
        ("1-5 / R / L / G", "finger / right or left hand / glissando"),
        ("Sequential", "M K O ; ' right 1-5 · V D W A LShift left 1-5"),
    ]),
    ("Impose", [
        ("N / Shift+N", "next / previous planned edit"),
        ("Click green", "add it or not"),
        ("K, Alt+click red", "remove it or keep it"),
        ("Apply", "make the accepted edits - one undo step"),
    ]),
]


def draw(surf, fonts):
    w, h = surf.get_size()
    shade = pygame.Surface((w, h), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 150))
    surf.blit(shade, (0, 0))
    f_head, f_key, f_text = fonts["bold"], fonts["small"], fonts["small"]
    line = f_text.get_linesize() + px(2)
    col_w = min(px(560), (w - px(80)) // 2)
    # two columns, sections kept whole
    cols, heights = [[], []], [0, 0]
    total = sum(px(34) + len(items) * line for _, items in SECTIONS)
    for sec in SECTIONS:
        c = 0 if heights[0] < total / 2 else 1
        cols[c].append(sec)
        heights[c] += px(34) + len(sec[1]) * line
    box = pygame.Rect(0, 0, 2 * col_w + px(60), max(heights) + px(90))
    box.center = (w // 2, h // 2)
    box.y = max(px(10), box.y)
    pygame.draw.rect(surf, PANEL, box, border_radius=px(12))
    pygame.draw.rect(surf, PANEL_EDGE, box, 1, border_radius=px(12))
    surf.blit(fonts["button"].render("Shortcuts", True, TEXT), (box.x + px(24), box.y + px(16)))
    hint = f_text.render("any key or click closes this", True, TEXT_DIM)
    surf.blit(hint, hint.get_rect(topright=(box.right - px(24), box.y + px(22))))
    key_w = px(150)
    for c, secs in enumerate(cols):
        x = box.x + px(24) + c * (col_w + px(12))
        y = box.y + px(62)
        for title, items in secs:
            surf.blit(f_head.render(title, True, ACCENT), (x, y))
            y += px(26)
            for k, text in items:
                surf.blit(f_key.render(k, True, TEXT), (x, y))
                img = f_text.render(th.fit_text(f_text, text, col_w - key_w - px(10)), True, TEXT_DIM)
                surf.blit(img, (x + key_w, y))
                y += line
            y += px(8)
