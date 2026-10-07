"""
recent.py - The most recent setups launched, for the main menu's "Recent" list.

A setup is what was opened, as a dict kept in settings.json ("recent"),
newest first, RECENT_MAX at most:

  {"mode": "play", "midi": path, "soundfont": path or None}       the synth (None: the system's)
  {"mode": "play", "midi": path, "audio": path, "speed": 0.75}     a synced recording at its speed
  {"mode": "edit", "midi": path}                                   the fingering editor

Launching one again moves it to the top; the same files with another speed
or soundfont count as the same setup (the newest kept).
"""
import os

import pianist

RECENT_MAX = 5
KEY = "recent"


def _norm(path):
    return os.path.normcase(os.path.abspath(path)) if path else None


def _same(a, b):
    """The same setup: mode, MIDI file and (playing) the recording or soundfont alike."""
    if a.get("mode") != b.get("mode") or _norm(a.get("midi")) != _norm(b.get("midi")):
        return False
    if a.get("mode") != "play":
        return True
    if a.get("audio") or b.get("audio"):
        return _norm(a.get("audio")) == _norm(b.get("audio"))
    return True                      # (played with the synth: whichever soundfont, the newest kept)


def entries():
    """The recent setups, newest first (anything malformed left out)."""
    got = pianist.app_setting(KEY, []) or []
    return [e for e in got if isinstance(e, dict) and e.get("mode") in ("play", "edit") and e.get("midi")][:RECENT_MAX]


def _save(items):
    pianist.set_app_setting(KEY, items[:RECENT_MAX])


def add(entry):
    """`entry` launched: to the top of the list."""
    entry = {k: v for k, v in entry.items() if v is not None or k == "soundfont"}
    for k in ("midi", "audio", "soundfont"):
        if entry.get(k):
            entry[k] = os.path.abspath(entry[k])
    _save([entry] + [e for e in entries() if not _same(e, entry)])


def add_play(midi, soundfont=None, audio=None, speed=None):
    if audio:
        add({"mode": "play", "midi": midi, "audio": audio, "speed": round(float(speed or 1.0), 2)})
    else:
        add({"mode": "play", "midi": midi, "soundfont": soundfont})


def add_edit(midi):
    add({"mode": "edit", "midi": midi})


def remove(entry):
    """`entry` (as entries() gave it) off the list."""
    _save([e for e in entries() if e != entry])


def missing(entry):
    """The setup's files that can't be found (MIDI first), as paths."""
    paths = [entry.get("midi")] + [entry.get(k) for k in ("audio", "soundfont") if entry.get(k)]
    return [p for p in paths if not (p and os.path.isfile(p))]


def describe(entry):
    """(title, subtitle) to show for a setup."""
    name = os.path.basename(entry.get("midi") or "?")
    if entry.get("mode") == "edit":
        return name, "Fingering editor"
    if entry.get("audio"):
        speed = int(round(100 * float(entry.get("speed") or 1.0)))
        return name, f"Play  ·  synced to {os.path.basename(entry['audio'])} at {speed}%"
    sf = entry.get("soundfont")
    if sf:
        return name, f"Play  ·  soundfont {os.path.splitext(os.path.basename(sf))[0]}"
    return name, "Play  ·  default sound"
