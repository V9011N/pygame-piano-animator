"""
document.py - The MIDI document being edited.

Reading a file pulls its notes out of the tracks as Note objects (a note-on,
its note-off, and the fingering marker in front of the note-on); every other
event stays an Event, untouched. Exporting puts them all back: each event at
its tick in the order the file had it (new ones in a sensible place among
them) and each note's fingering marker immediately before its note-on. An
unedited file comes back event for event.

Fingering is stored the way Hand-thesia stores it - a text meta event just
before the note-on, on the note's own track:
    "F1".."F5"               finger only (the Hanon set)
    "R1".."R5", "L1".."L5"   hand and finger (Hand-thesia's fingering editor writes these)
    "R", "L"                 hand only
    "Rg", "Lg"               hand, and the note is part of a glissando
A marker belongs to the next note-on of its track (Hand-thesia's
midi_loader.read_markers rule), so a note keeps its fingering through every
edit - moved, re-timed, re-voiced - and loses it only when it is deleted or
its fingering is cleared on purpose. Export re-reads what it wrote and refuses
to save if any note's fingering (or any other event) didn't come back.

All positions are in ticks. Edits go through transactions (begin / put /
remove / commit) and are undoable.
"""
from __future__ import annotations

import itertools
import os
from collections import Counter
from dataclasses import dataclass, field, replace

import numpy as np

from . import smf as smflib
from . import tempo as tempolib
from .smf import META, MIDI, SYSEX
from .timing import MeterMap, TempoMap, ppq_of

LEFT, RIGHT = "L", "R"
NO_OFF = -2                  # Note.off_pos: the file had no note-off for it (the track's end closed it)
NEW = -1                     # Note.on_pos / off_pos / Event.pos: placed by the editor, not from the file
NEW_POS = 10 ** 12           # new events sort after the file's own at the same tick
DRUM_CHANNEL = 9
PEDAL_CCS = (64, 66, 67)     # sustain, sostenuto, soft
UNDO_LIMIT = 500


# --------------------------------------------------------------------------- #
# Fingering markers
# --------------------------------------------------------------------------- #
def parse_marker(data):
    """(hand, finger, gliss) for a fingering text event's bytes, or None (Hand-thesia's midi_loader._marker)."""
    if len(data) == 2 and data[:1] in (b"F", b"R", b"L") and data[1:2] in (b"1", b"2", b"3", b"4", b"5"):
        h = data[:1].decode()
        return (None if h == "F" else h, int(data[1:2]), False)
    if data in (b"Rg", b"Lg"):
        return (data[:1].decode(), None, True)
    if data in (b"R", b"L"):
        return (data.decode(), None, False)
    return None


def marker_bytes(hand, finger, gliss=False):
    """The text Hand-thesia reads for this fingering (None: no marker)."""
    if hand and gliss:
        return (hand + "g").encode()
    if hand and finger:
        return f"{hand}{finger}".encode()
    if finger:
        return f"F{finger}".encode()
    if hand:
        return hand.encode()
    return None


# --------------------------------------------------------------------------- #
# Notes and events
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Note:
    id: int
    track: int
    channel: int
    pitch: int
    start: int                   # tick
    end: int                     # tick (>= start)
    velocity: int                # 1..127
    off_velocity: int = 0        # the note-off's velocity (written when off_status is 0x80)
    off_status: int = 0x90       # 0x80 note-off, or 0x90 (note-on at velocity 0)
    hand: str | None = None      # fingering: "R" / "L" / None
    finger: int | None = None    # fingering: 1..5 / None
    gliss: bool = False          # fingering: slid in a glissando
    on_pos: int = NEW            # index of its note-on in the source track (NEW: placed by the editor)
    off_pos: int = NEW           # index of its note-off (NEW, or NO_OFF when the file had none)

    @property
    def length(self):
        return self.end - self.start

    @property
    def has_fingering(self):
        return bool(self.hand or self.finger or self.gliss)

    @property
    def marker(self):
        return marker_bytes(self.hand, self.finger, self.gliss)


def note_with(n, **kw):
    """A copy of note n with fields changed; where it now sits in its track is the editor's choice."""
    if "start" in kw and kw["start"] != n.start:
        kw.setdefault("on_pos", NEW)
    if "end" in kw and kw["end"] != n.end:
        kw.setdefault("off_pos", NEW)
    if "track" in kw and kw["track"] != n.track:
        kw.setdefault("on_pos", NEW)
        if n.off_pos != NO_OFF:
            kw.setdefault("off_pos", NEW)
    return replace(n, **kw)


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    track: int
    tick: int
    kind: int                    # smf.MIDI / META / SYSEX
    payload: bytes
    pos: int = NEW               # index in the source track (NEW: placed by the editor)

    @property
    def status(self):
        return self.payload[0] if self.kind == MIDI else None

    @property
    def is_cc(self):
        return self.kind == MIDI and self.payload[0] & 0xF0 == 0xB0

    @property
    def channel(self):
        return self.payload[0] & 0x0F if self.kind == MIDI else None

    @property
    def meta_type(self):
        return self.payload[0] if self.kind == META else None

    @property
    def is_tempo(self):
        return self.kind == META and self.payload[0] == smflib.META_TEMPO and len(self.payload) == 4

    @property
    def tempo_us(self):
        return int.from_bytes(self.payload[1:4], "big")

    @property
    def is_timesig(self):
        return self.kind == META and self.payload[0] == smflib.META_TIMESIG and len(self.payload) >= 3

    @property
    def is_ramp(self):
        """A tempo ramp marker (tempo.py): how the tempo gets from this keyframe to the next."""
        return self.kind == META and tempolib.parse_marker(self.payload) is not None

    @property
    def is_timing(self):
        return self.is_tempo or self.is_timesig or self.is_ramp


def event_with(e, **kw):
    if "tick" in kw and kw["tick"] != e.tick:
        kw.setdefault("pos", NEW)
    if "track" in kw and kw["track"] != e.track:
        kw.setdefault("pos", NEW)
    return replace(e, **kw)


def cc_payload(channel, number, value):
    return bytes([0xB0 | channel, number, max(0, min(127, int(value)))])


def tempo_payload(bpm):
    us = max(1, min(0xFFFFFF, int(round(60e6 / bpm))))
    return bytes([smflib.META_TEMPO]) + us.to_bytes(3, "big")


@dataclass
class TrackInfo:
    end_tick: int = 0            # the source's end-of-track tick (kept: a track may end after its last event)


@dataclass
class Edit:
    """One undoable step: {id: (before, after)} for notes and events (None = not there), {track: (before, after)}
    for where tracks end, `structure` (before, after) when tracks were added - each (format, layout, track count);
    `retime` (forward, back): the tick maps when the step moved everything in time."""
    label: str
    notes: dict
    events: dict
    tracks: dict = field(default_factory=dict)
    structure: tuple = None
    retime: object = None


@dataclass
class ExportReport:
    marked: int = 0              # notes written with a fingering marker
    dropped_markers: int = 0     # stray marker-like texts left out (they'd have fingered another note)
    dropped_ids: list = field(default_factory=list)
    problems: list = field(default_factory=list)


class ExportError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Fast note lookups (rebuilt after edits)
# --------------------------------------------------------------------------- #
class NoteIndex:
    def __init__(self, notes):
        ns = sorted(notes, key=lambda n: (n.start, n.pitch, n.id))
        self.notes = ns
        k = len(ns)
        self.start = np.fromiter((n.start for n in ns), np.int64, k)
        self.end = np.fromiter((n.end for n in ns), np.int64, k)
        self.pitch = np.fromiter((n.pitch for n in ns), np.int16, k)
        self.channel = np.fromiter((n.channel for n in ns), np.int8, k)
        self.velocity = np.fromiter((n.velocity for n in ns), np.int16, k)
        self.ids = np.fromiter((n.id for n in ns), np.int64, k)
        self.max_len = int((self.end - self.start).max()) if k else 0
        self.pos = {n.id: i for i, n in enumerate(ns)}

    def window(self, t0, t1):
        """Indices (into .notes) of the notes overlapping ticks [t0, t1)."""
        lo = int(np.searchsorted(self.start, t0 - self.max_len, "left"))
        hi = int(np.searchsorted(self.start, t1, "left"))
        if hi <= lo:
            return np.zeros(0, np.int64)
        e = self.end[lo:hi]
        s = self.start[lo:hi]
        return np.flatnonzero((e > t0) | (s >= t0)) + lo


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
class Document:
    def __init__(self):
        self.fmt = 1
        self.division = 480
        self.header_extra = b""
        self.trailing = b""
        self.layout = []
        self.running_status = True
        self.tracks: list[TrackInfo] = []
        self.notes: dict[int, Note] = {}
        self.events: dict[int, Event] = {}
        self.path = None                  # the MIDI file it came from
        self.project_path = None          # the project file it was last saved to / opened from
        self.name = None                  # its title when it has no file (from_song)
        self.warnings: list[str] = []
        self._ids = itertools.count(1)
        self.version = 0                  # bumped by every change
        self.notes_rev = 0
        self.events_rev = 0
        self.timing_rev = 0
        self.undo_stack: list[Edit] = []
        self.redo_stack: list[Edit] = []
        self._tx = None
        self._saved_at = None
        self._cache = {}

    # ----- construction -----------------------------------------------------
    @classmethod
    def new_empty(cls, division=480):
        doc = cls()
        doc.division = division
        doc.layout = [("track", 0), ("track", 1)]
        doc.tracks = [TrackInfo(0), TrackInfo(0)]
        doc._plain(0, 0, META, bytes([smflib.META_TRACK_NAME]) + b"Tempo", 0)
        doc._plain(0, 0, META, bytes([smflib.META_TIMESIG, 4, 2, 24, 8]), 1)
        doc._plain(0, 0, META, tempo_payload(120.0), 2)
        doc._plain(1, 0, META, bytes([smflib.META_TRACK_NAME]) + b"Piano", 0)
        doc._plain(1, 0, MIDI, bytes([0xC0, 0]), 1)
        doc.running_status = True
        return doc

    @classmethod
    def from_song(cls, song, division=480):
        """
        A new document of a song Hand-thesia loaded (midi_loader.MidiSong - a PIG file, say, or notes made in
        memory): at 120 BPM, so a second is two beats; the right hand on a track and channel 1, the left on
        channel 2, each note with its hand and finger; the pedals with the right hand.
        """
        doc = cls.new_empty(division)
        doc.name = getattr(song, "title", None)
        per_s = division * 2
        doc.layout.append(("track", 2))
        doc.tracks.append(TrackInfo(0))
        doc._plain(1, 0, META, bytes([smflib.META_TRACK_NAME]) + b"Right hand", 0)
        doc._plain(2, 0, META, bytes([smflib.META_TRACK_NAME]) + b"Left hand", 0)
        doc._plain(2, 0, MIDI, bytes([0xC1, 0]), 1)
        doc.events = {e.id: e for e in doc.events.values()
                      if not (e.track == 1 and e.kind == META and e.payload[0] == smflib.META_TRACK_NAME
                              and e.payload[1:] == b"Piano")}
        for n in song.notes:
            left = n.hand == LEFT
            start = max(0, int(round(n.start * per_s)))
            doc._add(Note(next(doc._ids), 2 if left else 1, 1 if left else 0, n.pitch, start,
                          max(start + 1, int(round(n.end * per_s))), max(1, min(127, n.velocity)),
                          hand=n.hand, finger=getattr(n, "finger", None), gliss=bool(getattr(n, "gliss", False))))
        for t, c, v in getattr(song, "raw_controls", None) or []:
            doc._plain(1, max(0, int(round(t * per_s))), MIDI, bytes([0xB0, c & 0x7F, v & 0x7F]), NEW)
        return doc

    @classmethod
    def from_bytes(cls, data, path=None):
        doc = cls.from_smf(smflib.parse_lenient(data))
        doc.path = path
        return doc

    @classmethod
    def from_file(cls, path):
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read(), path)

    @classmethod
    def from_smf(cls, s):
        doc = cls()
        doc.fmt, doc.division, doc.header_extra, doc.trailing = s.fmt, s.division, s.header_extra, s.trailing
        doc.layout = list(s.layout)
        doc.running_status = s.uses_running_status
        doc.warnings = list(s.notes)
        for ti, tr in enumerate(s.tracks):
            doc.tracks.append(TrackInfo(tr.end_tick))
            open_ = {}                     # (channel, pitch) -> [(start, velocity, on pos, mark)] first in, first out
            pending = None                 # (pos, tick, payload, mark): the marker waiting for the next note-on
            for pos, (tick, kind, p) in enumerate(tr.events):
                if kind == META and p[0] == smflib.META_TEXT:
                    mark = parse_marker(p[1:])
                    if mark is not None:
                        if pending is not None:          # superseded (the reader keeps the last): an ordinary text
                            doc._plain(ti, pending[1], META, pending[2], pending[0])
                        pending = (pos, tick, p, mark)
                        continue
                if kind == MIDI:
                    st = p[0] & 0xF0
                    if st == 0x90 and p[2] > 0:
                        mark = pending[3] if pending else (None, None, False)
                        pending = None
                        open_.setdefault((p[0] & 0x0F, p[1]), []).append((tick, p[2], pos, mark))
                        continue
                    if st == 0x80 or st == 0x90:
                        q = open_.get((p[0] & 0x0F, p[1]))
                        if q:
                            start, vel, on_pos, (hand, finger, gliss) = q.pop(0)
                            doc._add(Note(next(doc._ids), ti, p[0] & 0x0F, p[1], start, tick, vel,
                                          off_velocity=p[2] if st == 0x80 else 0, off_status=st,
                                          hand=hand, finger=finger, gliss=gliss, on_pos=on_pos, off_pos=pos))
                            continue
                doc._plain(ti, tick, kind, p, pos)
            if pending is not None:
                doc._plain(ti, pending[1], META, pending[2], pending[0])
            for (ch, pitch), q in open_.items():               # never released: the track's end closes them
                for start, vel, on_pos, (hand, finger, gliss) in q:
                    doc._add(Note(next(doc._ids), ti, ch, pitch, start, max(start, tr.end_tick), vel,
                                  hand=hand, finger=finger, gliss=gliss, on_pos=on_pos, off_pos=NO_OFF))
        doc._read_ramps()
        return doc

    def _read_ramps(self):
        """Tempo ramps written by this editor: the steps inside each go (they're worked out again), the marker
        keeps only its mode."""
        markers = [e for e in self.events.values() if e.is_ramp]
        if not markers:
            return
        tempos = {}
        for e in self.events.values():
            if e.is_tempo:
                tempos.setdefault(e.track, []).append(e)
        for m in markers:
            mode, end = tempolib.parse_marker(m.payload)
            if end is not None:
                for e in tempos.get(m.track, ()):
                    if m.tick < e.tick < end:
                        self.events.pop(e.id, None)
            self.events[m.id] = replace(m, payload=tempolib.marker_payload(mode))

    def _add(self, note):
        self.notes[note.id] = note

    def _plain(self, track, tick, kind, payload, pos):
        e = Event(next(self._ids), track, tick, kind, bytes(payload), pos)
        self.events[e.id] = e
        return e

    def new_id(self):
        return next(self._ids)

    # ----- names -------------------------------------------------------------
    @property
    def title(self):
        p = self.project_path or self.path
        return os.path.splitext(os.path.basename(p))[0] if p else (self.name or "Untitled")

    @property
    def ppq(self):
        return ppq_of(self.division)

    def track_name(self, ti):
        def make():
            names = {}
            for e in self._sorted_events(lambda e: e.kind == META and e.payload[0] == smflib.META_TRACK_NAME):
                names.setdefault(e.track, e.payload[1:].decode("latin-1", "replace").strip())
            return names
        return self._cached("track_names", self.events_rev, make).get(ti) or f"Track {ti + 1}"

    # ----- transactions and undo ----------------------------------------------
    @property
    def in_transaction(self):
        return self._tx is not None

    def begin(self, label):
        if self._tx is None:
            self._tx = {"label": label, "notes": {}, "events": {}, "tracks": {}, "structure": None, "depth": 1}
        else:
            self._tx["depth"] += 1

    def relabel(self, label):
        if self._tx is not None:
            self._tx["label"] = label

    def commit(self):
        """End the transaction; returns its Edit (None if nothing changed)."""
        tx = self._tx
        if tx is None:
            return None
        tx["depth"] -= 1
        if tx["depth"] > 0:
            return None
        self._tx = None
        notes = {i: (old, self.notes.get(i)) for i, old in tx["notes"].items() if old != self.notes.get(i)}
        events = {i: (old, self.events.get(i)) for i, old in tx["events"].items() if old != self.events.get(i)}
        tracks = {i: (old, self.tracks[i].end_tick) for i, old in tx["tracks"].items()
                  if old != self.tracks[i].end_tick}
        structure = (tx["structure"], self._structure()) if tx["structure"] not in (None, self._structure()) else None
        if not notes and not events and not tracks and structure is None:
            return None
        edit = Edit(tx["label"], notes, events, tracks, structure)
        self.undo_stack.append(edit)
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        return edit

    def cancel(self):
        """Undo whatever the open transaction changed, and close it."""
        tx = self._tx
        if tx is None:
            return
        self._tx = None
        self._apply(Edit(tx["label"], {i: (old, self.notes.get(i)) for i, old in tx["notes"].items()},
                         {i: (old, self.events.get(i)) for i, old in tx["events"].items()},
                         {i: (old, self.tracks[i].end_tick) for i, old in tx["tracks"].items()},
                         (tx["structure"], self._structure()) if tx["structure"] is not None else None), back=True)

    def _auto(self):
        if self._tx is None:
            self.begin("Edit")
            return True
        return False

    def put_note(self, note):
        auto = self._auto()
        self._tx["notes"].setdefault(note.id, self.notes.get(note.id))
        self.notes[note.id] = note
        self._touched_notes()
        if auto:
            self.commit()

    def remove_note(self, nid):
        if nid not in self.notes:
            return
        auto = self._auto()
        self._tx["notes"].setdefault(nid, self.notes.get(nid))
        del self.notes[nid]
        self._touched_notes()
        if auto:
            self.commit()

    def put_event(self, ev):
        auto = self._auto()
        old = self.events.get(ev.id)
        self._tx["events"].setdefault(ev.id, old)
        self.events[ev.id] = ev
        self._touched_events(old, ev)
        if auto:
            self.commit()

    def remove_event(self, eid):
        old = self.events.get(eid)
        if old is None:
            return
        auto = self._auto()
        self._tx["events"].setdefault(eid, old)
        del self.events[eid]
        self._touched_events(old, None)
        if auto:
            self.commit()

    def add_track(self, name=None):
        """A new, empty track after the others - an edit like any other (undoable) - named `name` (bytes or str)
        if given; its index. A format 0 file becomes format 1 (one track is all format 0 can hold)."""
        auto = self._auto()
        if self._tx["structure"] is None:
            self._tx["structure"] = self._structure()
        ti = len(self.tracks)
        self.tracks.append(TrackInfo(0))
        self.layout.append(("track", ti))
        if self.fmt == 0:
            self.fmt = 1
        self.version += 1
        if name:
            raw = name if isinstance(name, bytes) else name.encode("latin-1", "replace")
            self.put_event(Event(self.new_id(), ti, 0, META, bytes([smflib.META_TRACK_NAME]) + raw))
        if auto:
            self.commit()
        return ti

    def _structure(self):
        return self.fmt, tuple(self.layout), len(self.tracks)

    def set_track_end(self, ti, tick):
        """Move track ti's end-of-track (an edit like any other: undoable)."""
        tick = int(tick)
        if self.tracks[ti].end_tick == tick:
            return
        auto = self._auto()
        self._tx["tracks"].setdefault(ti, self.tracks[ti].end_tick)
        self.tracks[ti].end_tick = tick
        self.version += 1
        if auto:
            self.commit()

    def _touched_notes(self):
        self.notes_rev += 1
        self.version += 1

    def _touched_events(self, a, b):
        self.events_rev += 1
        self.version += 1
        if any(e is not None and e.is_timing for e in (a, b)):
            self.timing_rev += 1

    def _apply(self, edit, back):
        if edit.structure is not None and not back:
            self._set_structure(edit.structure[1])           # (tracks first: what follows may be in them)
        for nid, (old, new) in edit.notes.items():
            v = old if back else new
            if v is None:
                self.notes.pop(nid, None)
            else:
                self.notes[nid] = v
        timing = False
        for eid, (old, new) in edit.events.items():
            v = old if back else new
            timing = timing or any(e is not None and e.is_timing for e in (old, new))
            if v is None:
                self.events.pop(eid, None)
            else:
                self.events[eid] = v
        for ti, (old, new) in edit.tracks.items():
            self.tracks[ti].end_tick = old if back else new
        if edit.structure is not None and back:
            self._set_structure(edit.structure[0])           # (tracks last: they're empty again by now)
        if edit.notes:
            self.notes_rev += 1
        if edit.events:
            self.events_rev += 1
        if timing:
            self.timing_rev += 1
        self.version += 1

    def _set_structure(self, s):
        self.fmt, layout, n = s
        self.layout = list(layout)
        del self.tracks[n:]
        while len(self.tracks) < n:
            self.tracks.append(TrackInfo(0))

    def undo(self):
        if self._tx is not None or not self.undo_stack:
            return None
        e = self.undo_stack.pop()
        self._apply(e, back=True)
        self.redo_stack.append(e)
        return e

    def redo(self):
        if self._tx is not None or not self.redo_stack:
            return None
        e = self.redo_stack.pop()
        self._apply(e, back=False)
        self.undo_stack.append(e)
        return e

    @property
    def dirty(self):
        return (self.undo_stack[-1] if self.undo_stack else None) is not self._saved_at

    def mark_saved(self):
        self._saved_at = self.undo_stack[-1] if self.undo_stack else None

    # ----- derived data (cached) ------------------------------------------------
    def _cached(self, key, rev, make):
        got = self._cache.get(key)
        if got is None or got[0] != rev:
            got = self._cache[key] = (rev, make())
        return got[1]

    def index(self) -> NoteIndex:
        return self._cached("index", self.notes_rev, lambda: NoteIndex(self.notes.values()))

    def _sorted_events(self, pred):
        evs = [e for e in self.events.values() if pred(e)]
        evs.sort(key=lambda e: (e.tick, e.track, e.pos if e.pos >= 0 else NEW_POS + e.id))
        return evs

    def tempo_events(self):
        return self._cached("tempo_events", self.timing_rev, lambda: self._sorted_events(lambda e: e.is_tempo))

    def tempo_map(self) -> TempoMap:
        """Ticks <-> seconds, ramps (tempo.py) played as the steps they're written as."""
        return self._cached("tempo", self.timing_rev, lambda: TempoMap(self.division, tempolib.expanded(self)))

    def meter_map(self) -> MeterMap:
        def make():
            sigs = [(e.tick, e.payload[1], 2 ** e.payload[2])
                    for e in self._sorted_events(lambda e: e.is_timesig) if e.payload[2] < 8]
            return MeterMap(self.ppq, sigs)
        return self._cached("meter", self.timing_rev, make)

    def pedal_series(self):
        """{(channel, controller): (ticks array, values array)} for the pedals (64, 66, 67), in time order."""
        def make():
            out = {}
            for e in self._sorted_events(lambda e: e.is_cc and e.payload[1] in PEDAL_CCS):
                out.setdefault((e.payload[0] & 0x0F, e.payload[1]), []).append((e.tick, e.payload[2], e.id))
            return {k: (np.array([t for t, _, _ in v], np.int64), np.array([x for _, x, _ in v], np.int16),
                        [i for _, _, i in v]) for k, v in out.items()}
        return self._cached("pedals", self.events_rev, make)

    def channel_counts(self):
        return self._cached("channels", self.notes_rev, lambda: Counter(n.channel for n in self.notes.values()))

    def end_tick(self):
        """The last tick anything happens at."""
        def make():
            ix = self.index()
            last = int(ix.end.max()) if len(ix.end) else 0
            for e in self.events.values():
                last = max(last, e.tick)
            return last
        return self._cached("end", self.version, make)

    def pitch_range(self):
        ix = self.index()
        if not len(ix.pitch):
            return (48, 72)
        return int(ix.pitch.min()), int(ix.pitch.max())

    def track_for_channel(self, channel):
        """The track new events on this channel go to: the one with most of its notes (else the busiest)."""
        counts = Counter(n.track for n in self.notes.values() if n.channel == channel)
        if not counts:
            counts = Counter(n.track for n in self.notes.values())
        if counts:
            return counts.most_common(1)[0][0]
        for ti in range(len(self.tracks)):
            if any(e.track == ti and e.kind == MIDI for e in self.events.values()):
                return ti
        if not self.tracks:
            self.tracks.append(TrackInfo(0))
            self.layout.append(("track", 0))
        return len(self.tracks) - 1

    def tempo_track(self):
        evs = self.tempo_events()
        return evs[0].track if evs else 0

    def release_style(self):
        """(status, velocity) for new notes' note-offs: what the file mostly uses."""
        c = Counter(n.off_status for n in self.notes.values() if n.off_pos != NO_OFF)
        if c and c.most_common(1)[0][0] == 0x80:
            vels = Counter(n.off_velocity for n in self.notes.values() if n.off_status == 0x80)
            return 0x80, vels.most_common(1)[0][0]
        return (0x90, 0) if c else (0x80, 64)

    # ----- export -------------------------------------------------------------
    def build_smf(self):
        """(Smf, ExportReport): the document as a MIDI file, every note's fingering marker before its note-on."""
        report = ExportReport()
        per_track = [[] for _ in self.tracks]
        stray_ids = []
        keys = tempolib.keyframes(self)
        ramp_end = {}
        for i, k in enumerate(keys):
            if k.marker is not None:
                ramp_end[k.marker.id] = keys[i + 1].tick if i + 1 < len(keys) else k.tick
                if i + 1 < len(keys):
                    for j, (t, us) in enumerate(tempolib.ramp_steps(k, keys[i + 1], self.ppq)):
                        per_track[k.event.track].append(((t, 1, 3 * NEW_POS + j, 0, 0), META,
                                                         bytes([smflib.META_TEMPO]) + us.to_bytes(3, "big"), None, None))
        for e in self.events.values():
            key = (e.tick, 1, e.pos if e.pos >= 0 else NEW_POS + e.id, 0, 0)
            tag = "stray" if (e.kind == META and e.payload[0] == smflib.META_TEXT
                              and parse_marker(e.payload[1:]) is not None) else None
            payload = e.payload
            if e.kind == META and e.is_ramp:
                payload = tempolib.file_marker_payload(tempolib.parse_marker(e.payload)[0], ramp_end.get(e.id, e.tick))
            per_track[e.track].append((key, e.kind, payload, tag, e.id))
        for n in self.notes.values():
            items = per_track[n.track]
            base = (n.start, 1, n.on_pos, 0) if n.on_pos >= 0 else (n.start, 2, n.pitch, n.id)
            mk = marker_bytes(n.hand, n.finger, n.gliss)
            if mk:
                items.append((base + (0,), META, bytes([smflib.META_TEXT]) + mk, "mark", None))
                report.marked += 1
            items.append((base + (1,), MIDI, bytes([0x90 | n.channel, n.pitch, n.velocity]),
                          "on_marked" if mk else "on", None))
            if n.off_pos != NO_OFF:
                key = (n.end, 1, n.off_pos, 0, 0) if n.off_pos >= 0 else (n.end, 0, n.pitch, n.id, 0)
                off = bytes([n.off_status | n.channel, n.pitch, n.off_velocity if n.off_status == 0x80 else 0])
                items.append((key, MIDI, off, None, None))
        tracks = []
        for ti, items in enumerate(per_track):
            items.sort(key=lambda it: it[0])
            out = []
            out_ids = {}                   # index in `out` -> event id, for the marker-like texts
            strays = []                    # indices in `out` of marker-like texts since the last note-on
            for key, kind, payload, tag, eid in items:
                if tag == "on":
                    # a note-on without its own marker: the reader would hand it a waiting stray
                    for k in strays:
                        out[k] = None
                        report.dropped_markers += 1
                        stray_ids.append(out_ids[k])
                    strays = []
                elif tag == "on_marked":
                    strays = []            # (its own marker, right before it, wins)
                if tag == "stray":
                    strays.append(len(out))
                    out_ids[len(out)] = eid
                out.append((key[0], kind, payload))
            out = [ev for ev in out if ev is not None]
            last = out[-1][0] if out else 0
            tracks.append(smflib.Track(out, max(self.tracks[ti].end_tick, last)))
        s = smflib.Smf(fmt=self.fmt, division=self.division, tracks=tracks,
                       layout=list(self.layout), header_extra=self.header_extra, trailing=self.trailing)
        report.dropped_ids = stray_ids
        return s, report

    def to_bytes(self):
        s, report = self.build_smf()
        return smflib.serialize(s, running_status=self.running_status), report

    def verify(self, data, report):
        """Problems (empty if none) found reading `data` back: every note, its fingering, every event."""
        problems = []
        try:
            back = Document.from_bytes(data)
        except Exception as exc:
            return [f"the written file can't be read back ({exc})"]

        def ons(d):
            return Counter((n.track, n.channel, n.pitch, n.start, n.velocity, n.hand, n.finger, n.gliss)
                           for n in d.notes.values())

        def offs(d):
            return Counter((n.track, n.channel, n.pitch, n.end, n.off_status, n.off_velocity)
                           for n in d.notes.values() if n.off_pos != NO_OFF)

        def evs(d, skip=()):
            return Counter((e.track, e.tick, e.kind, e.payload) for e in d.events.values() if e.id not in skip)

        a, b = ons(self), ons(back)
        if a != b:
            lost = sum(((a - b)).values())
            fing = sum(1 for k in (a - b) if k[5] or k[6] or k[7])
            problems.append(f"{lost} note(s) didn't come back as written" +
                            (f" ({fing} with fingering)" if fing else ""))
        if offs(self) != offs(back):
            problems.append("some note endings didn't come back as written")
        if evs(self, set(report.dropped_ids)) != evs(back):
            problems.append("some non-note events didn't come back as written")
        return problems

    def export(self, path):
        """Write the MIDI file to `path` (atomically), checked by reading it back. Returns the ExportReport."""
        data, report = self.to_bytes()
        problems = self.verify(data, report)
        if problems:
            raise ExportError("Export cancelled - nothing was written: " + "; ".join(problems) + ".")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return report

    # ----- seconds ------------------------------------------------------------
    def note_seconds(self, notes):
        """(onsets, offsets) in seconds for a list of notes."""
        tm = self.tempo_map()
        s = np.fromiter((n.start for n in notes), np.int64, len(notes))
        e = np.fromiter((n.end for n in notes), np.int64, len(notes))
        return tm.to_seconds(s), tm.to_seconds(e)
