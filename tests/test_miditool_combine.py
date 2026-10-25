"""Another MIDI file into the open one: on top on new channels, or appended after it on the same channel."""
import os

import pygame
import pytest

from miditool_support import app, ht_read_markers  # noqa: F401
from miditool import combine, smf
from miditool import tempo as T
from miditool.document import Document, Event, Note, cc_payload, event_with, tempo_payload
from miditool.smf import META, MIDI

PPQ = 480


def with_tempo(doc, bpm):
    t = next(e for e in doc.events.values() if e.is_tempo)
    doc.begin("tempo")
    doc.put_event(event_with(t, payload=tempo_payload(bpm)))
    doc.commit()
    return doc


def song():
    """The open project: 120 BPM, 4/4, notes on channel 1 to tick 2160 (bar 2, beat 1.5), the pedal up at 2400."""
    doc = with_tempo(Document.new_empty(), 120)
    doc.begin("song")
    for k in range(9):
        doc.put_note(Note(doc.new_id(), 1, 0, 60 + k, k * 240, k * 240 + 240, 70, hand="R", finger=1 + k % 5))
    doc.put_event(Event(doc.new_id(), 1, 0, MIDI, cc_payload(0, 64, 127)))
    doc.put_event(Event(doc.new_id(), 1, 2400, MIDI, cc_payload(0, 64, 0)))
    doc.commit()
    return doc


def two_hands():
    """Another file: 60 BPM, a "Right Hand" track on channel 1 and a "Left Hand" track on channel 2, fingered;
    a pedal on the right hand's channel, a program on the left's."""
    src = with_tempo(Document.new_empty(), 60)
    name = next(e for e in src.events.values() if e.track == 1 and e.kind == META and e.payload[0] == 0x03)
    src.begin("hands")
    src.put_event(event_with(name, payload=b"\x03Right Hand"))
    lh = src.add_track("Left Hand")
    for k in range(8):
        src.put_note(Note(src.new_id(), 1, 0, 72 + k, k * 480, k * 480 + 400, 90, hand="R", finger=1 + k % 5))
        src.put_note(Note(src.new_id(), lh, 1, 48 - k, k * 480, k * 480 + 400, 60, hand="L", finger=5 - k % 5))
    src.put_event(Event(src.new_id(), 1, 960, MIDI, cc_payload(0, 64, 127)))
    src.put_event(Event(src.new_id(), lh, 0, MIDI, bytes([0xC1, 6])))
    src.commit()
    return src


def fingering_of(doc):
    return {(n.start, n.pitch): (n.hand, "g" if n.gliss else n.finger) for n in doc.notes.values() if n.has_fingering}


def in_file_order(src):
    return sorted(src.notes.values(), key=lambda n: (n.start, n.track, n.pitch, n.id))


# --------------------------------------------------------------------------- #
# On top
# --------------------------------------------------------------------------- #
def test_on_top_each_note_where_it_plays_on_new_channels_and_tracks(tmp_path):
    doc, src = song(), two_hands()
    before, _ = doc.to_bytes()
    doc.begin("On top")
    got = combine.overlay(doc, src, "Hands")
    doc.commit()
    assert got["channels"] == {0: 1, 1: 2}                         # (the project has channel 1: then 2 and 3)
    assert [doc.track_name(t) for t in got["tracks"]] == ["Right Hand", "Left Hand"]
    stm, dtm = src.tempo_map(), doc.tempo_map()
    half = 0.5 / (2 * PPQ) + 1e-9                                  # half a tick at 120
    for a, i in zip(in_file_order(src), got["ids"]):
        b = doc.notes[i]
        assert (b.pitch, b.velocity, b.hand, b.finger, b.channel) == \
               (a.pitch, a.velocity, a.hand, a.finger, got["channels"][a.channel])
        assert abs(dtm.sec(b.start) - stm.sec(a.start)) <= half and abs(dtm.sec(b.end) - stm.sec(a.end)) <= half
    assert len(doc.tempo_events()) == 1                            # (the project's tempo, alone)
    evs = {(e.track, e.payload) for e in doc.events.values() if e.kind == MIDI}
    assert (got["tracks"][0], cc_payload(1, 64, 127)) in evs and (got["tracks"][1], bytes([0xC2, 6])) in evs
    out = os.path.join(tmp_path, "top.mid")
    doc.export(out)                                                # (read back and checked)
    assert ht_read_markers(out) == fingering_of(doc)
    data = open(out, "rb").read()
    doc.undo()
    assert doc.to_bytes()[0] == before and len(doc.tracks) == 2    # (its tracks went with it)
    doc.redo()
    assert doc.to_bytes()[0] == data


def test_on_top_of_a_format_0_file():
    one = smf.Smf(fmt=0, division=PPQ, layout=[("track", 0)], tracks=[smf.Track(
        [(0, META, tempo_payload(100)), (0, MIDI, bytes([0x90, 60, 80])), (PPQ, MIDI, bytes([0x80, 60, 64]))],
        PPQ)])
    data = smf.serialize(one)
    doc = Document.from_bytes(data)
    doc.begin("On top")
    combine.overlay(doc, two_hands(), "Hands")
    doc.commit()
    out, rep = doc.to_bytes()
    assert doc.fmt == 1 and doc.verify(out, rep) == [] and len(smf.parse(out).tracks) == 3
    doc.undo()
    assert doc.fmt == 0 and doc.to_bytes()[0] == data


def test_on_top_needs_free_channels():
    doc = song()
    doc.begin("busy")
    for ch in range(16):
        doc.put_note(Note(doc.new_id(), 1, ch, 40, 0, 10, 50))
    doc.commit()
    doc.begin("On top")
    with pytest.raises(ValueError):
        combine.overlay(doc, two_hands(), "Hands")
    doc.cancel()


# --------------------------------------------------------------------------- #
# Appended
# --------------------------------------------------------------------------- #
def section():
    """A file to append: 240 ticks a beat, 90 BPM then 60 from its 8th beat, 3/4, G major, a marker; its first note
    two beats in (on channel 4), with the pedal, volume (twice) and program set before it."""
    src = with_tempo(Document.new_empty(240), 90)
    sig = next(e for e in src.events.values() if e.is_timesig)
    src.begin("section")
    src.put_event(event_with(sig, payload=bytes([0x58, 3, 2, 24, 8])))
    src.put_event(Event(src.new_id(), 0, 0, META, bytes([0x59, 1, 0])))
    src.put_event(Event(src.new_id(), 0, 0, META, b"\x06Section B"))
    src.put_event(Event(src.new_id(), 0, 8 * 240, META, tempo_payload(60)))
    for t, p in ((0, cc_payload(3, 64, 0)), (0, cc_payload(3, 7, 100)), (10, cc_payload(3, 7, 90)),
                 (0, bytes([0xC3, 0])), (900, cc_payload(3, 64, 127))):
        src.put_event(Event(src.new_id(), 1, t, MIDI, p))
    for k in range(12):
        src.put_note(Note(src.new_id(), 1, 3, 64 + k % 5, 480 + k * 120, 480 + k * 120 + 100, 80, hand="L",
                          finger=1 + k % 5))
    src.commit()
    return src


def test_append_after_the_music(tmp_path):
    doc, src = song(), section()
    before, _ = doc.to_bytes()
    assert combine.music_end(doc) == 2400                           # (the pedal goes up after the last note ends)
    assert combine.gap_to_next_bar(doc) == pytest.approx(3.0)       # (bar 2, beat 2 -> bar 3)
    doc.begin("Append")
    got = combine.append(doc, src, 1.5)
    doc.commit()
    at = 2400 + 1.5 * PPQ
    assert got["start"] == at and got["channels"][3] == 0 and got["tempo"] == pytest.approx(90)
    new = [doc.notes[i] for i in got["ids"]]
    for a, b in zip(in_file_order(src), new):                        # beat for beat (240 -> 480 a beat)
        assert b.start == at + 2 * (a.start - 480) and b.end == at + 2 * (a.end - 480)
        assert (b.channel, b.track, b.hand, b.finger) == (0, 1, a.hand, a.finger)
    assert [(k.tick, round(k.bpm, 3)) for k in T.keyframes(doc)] == [(0, 120), (at, 90), (at + 2 * (8 - 2) * 240, 60)]
    stm, dtm = src.tempo_map(), doc.tempo_map()                      # so it plays as it did
    for a, b in zip(in_file_order(src), new):
        assert dtm.sec(b.start) - dtm.sec(at) == pytest.approx(stm.sec(a.start) - stm.sec(480), abs=1e-6)
    mm = doc.meter_map()
    assert mm.signature_at(at) == (3, 4) and mm.bar_beat(at)[1:] == (1, 0)     # (its own bars from its first note)
    at_start = sorted(e.payload for e in doc.events.values() if e.tick == at)
    assert cc_payload(0, 7, 90) in at_start and cc_payload(0, 7, 100) not in at_start   # (what's in force, once)
    assert cc_payload(0, 64, 0) in at_start and at_start.count(bytes([0xC0, 0])) == 1
    assert bytes([0x59, 1, 0]) in at_start and b"\x06Section B" in at_start
    out = os.path.join(tmp_path, "appended.mid")
    doc.export(out)
    assert ht_read_markers(out) == fingering_of(doc)
    data = open(out, "rb").read()
    doc.undo()
    assert doc.to_bytes()[0] == before
    doc.redo()
    assert doc.to_bytes()[0] == data


def test_append_keeps_two_hands_apart():
    """Both files with a track for each hand: the appended hands go into the project's, track for track."""
    doc, src = two_hands(), two_hands()
    doc.begin("Append")
    got = combine.append(doc, src, 0)
    doc.commit()
    new = [doc.notes[i] for i in got["ids"]]
    assert got["channels"] == {0: 0, 1: 1}
    assert {(n.hand, n.track, n.channel) for n in new} == {("R", 1, 0), ("L", 2, 1)}
    assert len(doc.tracks) == 3 and len(doc.tempo_events()) == 1   # (no new tracks; the same tempo needs no event)


# --------------------------------------------------------------------------- #
# Open MIDI's menu
# --------------------------------------------------------------------------- #

def _ev(app, etype, **kw):
    if etype == pygame.KEYDOWN:
        kw.setdefault("mod", 0)
        kw.setdefault("unicode", "")
    app.handle_event(pygame.event.Event(etype, **kw))


def _click(app, pos, button=1):
    pygame.mouse.set_pos(pos)
    _ev(app, pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    _ev(app, pygame.MOUSEBUTTONUP, pos=pos, button=button)


def test_open_midi_menu(app, tmp_path, monkeypatch):
    import miditool.app as appmod
    f = os.path.join(tmp_path, "hands.mid")
    with open(f, "wb") as fh:
        fh.write(two_hands().to_bytes()[0])
    monkeypatch.setattr(appmod, "open_file", lambda *a, **k: f)
    btn = next(b for b in app.buttons if b and b.action == "open")
    app.render()
    _click(app, btn.rect.center)
    assert [(it.value, it.enabled) for it in app.menu.items] == [("new", True), ("top", False), ("append", False)]
    _ev(app, pygame.KEYDOWN, key=pygame.K_ESCAPE)
    ed = app.editor
    doc = ed.doc
    doc.begin("x")
    doc.put_note(Note(doc.new_id(), 1, 0, 60, 0, PPQ, 80))
    doc.commit()
    # Append…: the gap asked for in beats
    app.render()
    _click(app, btn.rect.center)
    app.render()
    _click(app, app.menu.items[2].rect.center)
    assert app.dialog is not None and app.dialog.title == "Append a MIDI file" and app.dialog.text == "3"
    _ev(app, pygame.TEXTINPUT, text="2")
    _ev(app, pygame.KEYDOWN, key=pygame.K_RETURN)
    assert app.dialog is None
    new = [doc.notes[i] for i in ed.selection]
    assert len(new) == 16 and min(n.start for n in new) == PPQ + 2 * PPQ == ed.cursor
    assert {n.channel for n in new} == {0}
    _ev(app, pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)
    assert len(doc.notes) == 1
    # On top…, by its key
    _click(app, btn.rect.center)
    _ev(app, pygame.KEYDOWN, key=pygame.K_t)
    assert {doc.notes[i].channel for i in ed.selection} == {1, 2} and len(doc.tracks) == 4
    # New project…: asks about the unsaved changes first
    _click(app, btn.rect.center)
    _ev(app, pygame.KEYDOWN, key=pygame.K_n)
    assert app.dialog is not None and app.dialog.title == "Unsaved changes"
    app.render()
    _click(app, next(b for b in app.dialog.buttons if b.action == "discard").rect.center)
    assert ed.doc is not doc and len(ed.doc.notes) == 16 and ed.doc.path == f
