"""
filedialogs.py - The system's open / save dialogs (tkinter), as Hand-thesia uses them.
"""
from __future__ import annotations

import pygame

MIDI_TYPES = [("MIDI files", "*.mid *.midi *.kar *.rmi"), ("All files", "*.*")]
PROJECT_EXT = ".miditool"
PROJECT_TYPES = [("MIDI Tool project", "*" + PROJECT_EXT)]
OPEN_TYPES = [("MIDI files and projects", "*.mid *.midi *.kar *.rmi *" + PROJECT_EXT),
              ("MIDI files", "*.mid *.midi *.kar *.rmi"), ("MIDI Tool projects", "*" + PROJECT_EXT),
              ("All files", "*.*")]


def _root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    return root


def _after():
    try:
        pygame.event.clear((pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.KEYDOWN, pygame.KEYUP,
                            pygame.TEXTINPUT, pygame.MOUSEWHEEL))
    except Exception:
        pass


def open_file(title, initialdir=None, filetypes=None):
    try:
        from tkinter import filedialog
        root = _root()
        path = filedialog.askopenfilename(parent=root, title=title, filetypes=filetypes or OPEN_TYPES,
                                          initialdir=initialdir or None)
        root.destroy()
        return path or ""
    except Exception as exc:
        print(f"File dialog unavailable ({exc})")
        return ""
    finally:
        _after()


def save_file(title, initialdir=None, initialfile=None, filetypes=None, ext=".mid"):
    try:
        from tkinter import filedialog
        root = _root()
        path = filedialog.asksaveasfilename(parent=root, title=title, filetypes=filetypes or MIDI_TYPES,
                                            defaultextension=ext, initialdir=initialdir or None,
                                            initialfile=initialfile or None)
        root.destroy()
        return path or ""
    except Exception as exc:
        print(f"File dialog unavailable ({exc})")
        return ""
    finally:
        _after()
