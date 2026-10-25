"""
project.py - Project files (.miditool): the document and how you were working on it.

A project holds the MIDI file exactly as Export would write it (so every
note's fingering is in it, the same way), plus what isn't MIDI: the view, the
cursor, the grid, hidden channels, the selection - and an imposed perfect
source (its file, embedded) with the edits you declined, so the comparison
reopens as you left it. JSON, written atomically.
"""
from __future__ import annotations

import base64
import json
import os

from .document import Document

FORMAT = "miditool-project"
VERSION = 1


def _order(doc):
    """Note ids in a fixed order that reading the same MIDI back reproduces."""
    return [n.id for n in sorted(doc.notes.values(),
                                 key=lambda n: (n.track, n.start, n.pitch, n.channel, n.end, n.velocity))]


def save(path, editor):
    doc = editor.doc
    data, report = doc.to_bytes()
    problems = doc.verify(data, report)
    if problems:
        raise ValueError("the project couldn't be written: " + "; ".join(problems))
    order = _order(doc)
    pos = {nid: i for i, nid in enumerate(order)}
    obj = {
        "format": FORMAT,
        "version": VERSION,
        "source_path": doc.path,
        "midi": base64.b64encode(data).decode("ascii"),
        "view": {"x": [editor.vx.tlo, editor.vx.thi], "y": [editor.vy.tlo, editor.vy.thi]},
        "cursor": editor.cursor,
        "snap": editor.snap_i,
        "channel": editor.channel,
        "hidden_channels": sorted(editor.hidden_channels),
        "show_fingering": editor.show_fingering,
        "lanes_h": editor.lanes_h,
        "time_ruler": editor.time_ruler,
        "pedal_cc": editor.pedal_cc,
        "selection": sorted(pos[i] for i in editor.selection if i in pos),
        "impose": None,
    }
    s = editor.impose
    if s is not None:
        obj["impose"] = {
            "name": s.name,
            "source_path": s.source.path,
            "source_midi": base64.b64encode(s.source_bytes).decode("ascii"),
            "do_remove": s.do_remove, "do_add": s.do_add, "copy_fingering": s.copy_fingering,
            "keep": sorted(pos[i] for i in s.keep if i in pos),
            "skip": sorted(int(p) for p in s.skip),
            "show_links": editor.show_links,
        }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def load(path):
    """(Document, settings dict) of a project file."""
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    if obj.get("format") != FORMAT:
        raise ValueError("not a MIDI Tool project")
    data = base64.b64decode(obj["midi"])
    doc = Document.from_bytes(data, obj.get("source_path"))
    doc.project_path = path
    order = _order(doc)
    obj["_note_order"] = order
    return doc, obj


def is_project(path):
    return path.lower().endswith(".miditool")
