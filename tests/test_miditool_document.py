"""The document model: lossless round trips, and fingering that survives every edit and export."""
import os
import random

import pytest

from miditool_support import app, ht_read_markers  # noqa: F401
from miditool import smf
from miditool.document import (NO_OFF, Document, Event, ExportError, Note, marker_bytes, note_with,
                               parse_marker)
from miditool.smf import META, MIDI


def _events_by_track(data):
    s = smf.parse(data)
    return [[(t, k, p) for t, k, p in tr.events] for tr in s.tracks]


def test_marker_text_round_trip():
    for text in (b"F1", b"F5", b"R1", b"R5", b"L3", b"R", b"L", b"Rg", b"Lg"):
        assert marker_bytes(*parse_marker(text)) == text
    for text in (b"R6", b"X1", b"r1", b"", b"R12", b"Fg"):
        assert parse_marker(text) is None


def _edit_heavily(doc, seed=1):
    """Delete, move, stretch, re-pitch, re-channel, add notes; add pedal and tempo events."""
    rnd = random.Random(seed)
    ids = sorted(doc.notes)
    doc.begin("test edits")
    for nid in rnd.sample(ids, len(ids) // 10):
        doc.remove_note(nid)
    left = sorted(doc.notes)
    for nid in rnd.sample(left, len(left) // 5):
        n = doc.notes[nid]
        doc.put_note(note_with(n, start=n.start + 7, end=n.end + 20))
    for nid in rnd.sample(left, len(left) // 10):
        n = doc.notes[nid]
        doc.put_note(note_with(n, pitch=min(127, n.pitch + 1), velocity=max(1, n.velocity - 5)))
    tr = next(iter(doc.notes.values())).track
    for k in range(50):
        doc.put_note(Note(doc.new_id(), tr, 0, 60 + k % 12, 1000 + 240 * k, 1200 + 240 * k, 70))
    doc.put_note(Note(doc.new_id(), tr, 0, 64, 5000, 5400, 80, hand="R", finger=3))
    doc.put_event(Event(doc.new_id(), tr, 3000, MIDI, bytes([0xB0, 64, 127])))
    doc.put_event(Event(doc.new_id(), 0, 4000, META, bytes([0x51, 0x07, 0xA1, 0x20])))
    doc.commit()


def test_stray_marker_never_fingers_another_note():
    # a marker with no note after it (the track's end), then a note added after it
    doc = Document.new_empty()
    doc._plain(1, 100, META, b"\x01R4", 50)
    doc.put_note(Note(doc.new_id(), 1, 0, 60, 200, 300, 80))
    data, rep = doc.to_bytes()
    assert rep.dropped_markers == 1
    back = Document.from_bytes(data)
    assert all(not n.has_fingering for n in back.notes.values())
    assert doc.verify(data, rep) == []


def test_export_refuses_to_write_a_lossy_file(tmp_path, monkeypatch):
    doc = Document.new_empty()
    doc.put_note(Note(doc.new_id(), 1, 0, 60, 0, 480, 80, hand="R", finger=2))
    original = Document.build_smf

    def lossy(self):
        s, rep = original(self)
        for tr in s.tracks:                       # a writer that forgets the markers
            tr.events = [e for e in tr.events if not (e[1] == META and e[2][:1] == b"\x01")]
        return s, rep
    monkeypatch.setattr(Document, "build_smf", lossy)
    out = os.path.join(tmp_path, "x.mid")
    with pytest.raises(ExportError):
        doc.export(out)
    assert not os.path.exists(out)


def test_unclosed_and_orphan_events_are_kept():
    # a note never released, and a note-off with no note: both come back as they were
    body = smf.encode_track([(0, MIDI, b"\x90\x3c\x40"), (10, MIDI, b"\x80\x40\x00"), (20, MIDI, b"\x90\x3e\x40")],
                            end_tick=100, running_status=False)
    data = b"MThd" + (6).to_bytes(4, "big") + b"\x00\x00\x00\x01\x01\xe0" + b"MTrk" + len(body).to_bytes(4, "big") + body
    doc = Document.from_bytes(data)
    assert len(doc.notes) == 2 and all(n.off_pos == NO_OFF for n in doc.notes.values())
    out, rep = doc.to_bytes()
    assert out == data
