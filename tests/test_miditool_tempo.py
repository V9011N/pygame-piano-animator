"""Tempo keyframes: constant, linear and cosine curves - in the file, in seconds, in the tempo lane; baking them
into one tempo."""
import io
import math
import os

import pygame
import pytest

from miditool_support import app, ht_read_markers  # noqa: F401
from miditool import smf
from miditool import tempo as T
from miditool.document import Document, Event, Note, cc_payload, tempo_payload
from miditool.smf import META

PPQ = 480
BARS4 = 4 * 4 * PPQ


def ramp_doc(mode, b0=60.0, b1=120.0, end=BARS4):
    doc = Document.new_empty()                         # (has a 120 tempo event at tick 0 on track 0)
    k0 = next(e for e in doc.events.values() if e.is_tempo)
    doc.begin("ramp")
    doc.put_event(Event(k0.id, k0.track, 0, META, tempo_payload(b0), k0.pos))
    if mode != T.CONSTANT:
        doc.put_event(Event(doc.new_id(), 0, 0, META, T.marker_payload(mode)))
    doc.put_event(Event(doc.new_id(), 0, end, META, tempo_payload(b1)))
    doc.put_note(Note(doc.new_id(), 1, 0, 60, end, end + PPQ, 80, hand="R", finger=3))
    doc.commit()
    return doc


def file_tempos(data):
    s = smf.parse(data)
    return [(t, 60e6 / int.from_bytes(p[1:4], "big")) for tr in s.tracks for t, k, p in tr.events
            if k == META and p[0] == 0x51]


@pytest.mark.parametrize("mode", [T.LINEAR, T.COSINE])
def test_a_ramp_is_written_as_steps_and_read_back_as_a_ramp(mode, tmp_path):
    doc = ramp_doc(mode)
    data, rep = doc.to_bytes()
    steps = file_tempos(data)
    assert len(steps) > 20                                         # 32nd-note steps along the curve
    bpms = [b for _, b in steps]
    assert bpms == sorted(bpms) and bpms[0] == pytest.approx(60) and bpms[-1] == pytest.approx(120)
    back = Document.from_bytes(data)
    keys = T.keyframes(back)
    assert [(k.tick, round(k.bpm, 3), k.mode) for k in keys] == [(0, 60.0, mode), (BARS4, 120.0, T.CONSTANT)]
    assert doc.verify(data, rep) == []
    assert back.to_bytes()[0] == data                              # the same file again
    out = os.path.join(tmp_path, "ramp.mid")
    doc.export(out)                                                # (checked by reading it back)


def test_seconds_follow_the_curve():
    lin = ramp_doc(T.LINEAR)
    want = (60.0 / PPQ) * BARS4 / (120.0 - 60.0) * math.log(120.0 / 60.0)    # the integral of 60 / (bpm(t) ppq)
    assert lin.tempo_map().sec(BARS4) == pytest.approx(want, rel=2e-3)
    cos = ramp_doc(T.COSINE)
    assert T.bpm_at(cos, BARS4 // 2) == pytest.approx(90.0)
    assert T.bpm_at(cos, BARS4 // 4) < T.bpm_at(lin, BARS4 // 4)               # eased in: slower to leave
    const = ramp_doc(T.CONSTANT)
    assert const.tempo_map().sec(BARS4) == pytest.approx(BARS4 / PPQ * 1.0)   # 60 bpm: a second a beat


def test_other_readers_play_the_ramp(tmp_path):
    pm = pytest.importorskip("pretty_midi")
    doc = ramp_doc(T.LINEAR)
    out = os.path.join(tmp_path, "ramp.mid")
    doc.export(out)
    midi = pm.PrettyMIDI(out)
    note = [n for inst in midi.instruments for n in inst.notes][0]
    assert note.start == pytest.approx(doc.tempo_map().sec(BARS4), abs=1e-3)


def test_unchanged_files_dont_change():
    doc = Document.new_empty()
    data, _ = doc.to_bytes()
    assert Document.from_bytes(data).to_bytes()[0] == data


# --------------------------------------------------------------------------- #
# The tempo lane
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


def _type(app, text):
    _ev(app, pygame.TEXTINPUT, text=text)


def _chip(ed, kind):
    return next(r for r, (k, _) in ed._chips if k == kind)


def test_type_the_tempo_at_the_cursor(app):
    ed = app.editor
    app.render()
    _click(app, _chip(ed, "tempo_bpm").center)
    assert ed.tempo_edit is not None
    _type(app, "9")
    _type(app, "6")
    _ev(app, pygame.KEYDOWN, key=pygame.K_RETURN)
    assert ed.tempo_edit is None
    assert T.bpm_at(ed.doc, 0) == pytest.approx(96)
    # inside a ramp, typing adds a keyframe there (the ramp keeps its shape around it)
    doc = ed.doc
    doc.begin("x")
    doc.put_event(Event(doc.new_id(), 0, BARS4, META, tempo_payload(144)))
    doc.commit()
    ed._set_mode(T.keyframes(doc)[0], T.LINEAR)
    ed.cursor = BARS4 // 2
    app.render()
    _click(app, _chip(ed, "tempo_bpm").center)
    _type(app, "100")
    _ev(app, pygame.KEYDOWN, key=pygame.K_RETURN)
    keys = T.keyframes(doc)
    assert [(k.tick, round(k.bpm, 2), k.mode) for k in keys] == [(0, 96, T.LINEAR), (BARS4 // 2, 100, T.LINEAR),
                                                                 (BARS4, 144, T.CONSTANT)]
    # Esc leaves it as it was; nonsense changes nothing
    app.render()
    _click(app, _chip(ed, "tempo_bpm").center)
    _type(app, "33")
    _ev(app, pygame.KEYDOWN, key=pygame.K_ESCAPE)
    assert T.bpm_at(doc, BARS4 // 2) == pytest.approx(100)
    _ev(app, pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)
    assert len(T.keyframes(doc)) == 2


def test_choose_the_curve(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("x")
    doc.put_event(Event(doc.new_id(), 0, BARS4, META, tempo_payload(60)))
    doc.commit()
    ed.cursor = 100
    app.render()
    _click(app, _chip(ed, "tempo_mode").center)                    # Const -> Linear
    assert T.keyframes(doc)[0].mode == T.LINEAR
    _click(app, _chip(ed, "tempo_mode").center)                    # Linear -> Cosine
    assert T.keyframes(doc)[0].mode == T.COSINE
    # the right-click menu: back to constant
    _, body = ed.lane_rects["tempo"]
    ed.vx.jump(0, BARS4 * 1.2)
    pos = (int(ed.x_of(BARS4 // 3)), body.centery)
    _click(app, pos, button=3)
    assert ed.menu is not None
    curve = next(it for it in ed.menu.items if it.value == "curve")
    ed.menu.on_pick("curve", T.CONSTANT)
    ed.menu = None
    assert T.keyframes(doc)[0].mode == T.CONSTANT and T.keyframes(doc)[0].marker is None
    # the lane draws every curve without trouble
    for m in T.MODES:
        ed._set_mode(T.keyframes(doc)[0], m)
        app.render()


def test_drag_a_keyframe(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("x")
    doc.put_event(Event(doc.new_id(), 0, BARS4, META, tempo_payload(100)))
    doc.commit()
    ed.vx.jump(0, BARS4 * 1.5)
    app.render()
    _, body = ed.lane_rects["tempo"]
    k = T.keyframes(doc)[1]
    a = (int(ed.x_of(k.tick)), int(ed._tempo_y(k.bpm, body)))
    b = (int(ed.x_of(BARS4 + 4 * PPQ)), body.y + 4)
    pygame.mouse.set_pos(a)
    _ev(app, pygame.MOUSEBUTTONDOWN, pos=a, button=1)
    for i in range(1, 9):
        p = (a[0] + (b[0] - a[0]) * i // 8, a[1] + (b[1] - a[1]) * i // 8)
        pygame.mouse.set_pos(p)
        _ev(app, pygame.MOUSEMOTION, pos=p, rel=(0, 0), buttons=(1, 0, 0))
    _ev(app, pygame.MOUSEBUTTONUP, pos=b, button=1)
    k2 = T.keyframes(doc)[1]
    assert k2.tick == BARS4 + 4 * PPQ and k2.bpm > 110
    _ev(app, pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)
    k3 = T.keyframes(doc)[1]
    assert (k3.tick, round(k3.bpm, 2)) == (BARS4, 100)


# --------------------------------------------------------------------------- #
# Baking: one tempo for the whole piece
# --------------------------------------------------------------------------- #
def tempo_data_in(data):
    """Every tempo event (track, tick, payload) and every ramp tag in a MIDI file's bytes, all tracks."""
    s = smf.parse(data)
    tempos = [(ti, t, p) for ti, tr in enumerate(s.tracks) for t, k, p in tr.events if k == META and p[0] == 0x51]
    tags = [p for tr in s.tracks for t, k, p in tr.events if k == META and T.TAG in p]
    return tempos, tags


def tempo_everywhere():
    """Keyframes with a linear ramp and a cosine sweep, a tempo event on the notes' track, one that isn't the
    usual length, a ramp marker with no keyframe; fingered notes and a pedal over it all; a track that ends late."""
    doc = ramp_doc(T.LINEAR, 60, 120, BARS4)
    doc.begin("more")
    doc.put_event(Event(doc.new_id(), 0, BARS4, META, T.marker_payload(T.COSINE)))
    doc.put_event(Event(doc.new_id(), 0, 2 * BARS4, META, tempo_payload(90)))
    doc.put_event(Event(doc.new_id(), 1, 3 * BARS4, META, tempo_payload(150)))
    doc.put_event(Event(doc.new_id(), 1, PPQ, META, bytes([0x51, 0x07])))
    doc.put_event(Event(doc.new_id(), 0, 5 * PPQ, META, T.marker_payload(T.LINEAR)))
    for k in range(64):
        doc.put_note(Note(doc.new_id(), 1, 0, 60 + k % 12, k * PPQ, k * PPQ + PPQ // 2, 80,
                          hand="RL"[k % 2], finger=1 + k % 5))
    doc.put_event(Event(doc.new_id(), 1, 7 * PPQ, 0, cc_payload(0, 64, 127)))
    doc.commit()
    doc.tracks[0].end_tick = 5 * BARS4 + 7
    return doc


def fingering_of(doc):
    return {(n.start, n.pitch): (n.hand, "g" if n.gliss else n.finger) for n in doc.notes.values() if n.has_fingering}


def test_bake_in_place_leaves_one_tempo_and_every_note_where_it_was(tmp_path):
    doc = tempo_everywhere()
    before, _ = doc.to_bytes()
    tempos, tags = tempo_data_in(before)
    assert len(tempos) > 30 and len(tags) == 3                    # (ramp steps, the odd ones, three markers)
    notes = {n.id: n for n in doc.notes.values()}
    doc.begin("Bake")
    assert T.bake(doc, 100) is None
    doc.commit()
    out = os.path.join(tmp_path, "baked.mid")
    doc.export(out)                                                # (read back and checked before it's written)
    data = open(out, "rb").read()
    tempos, tags = tempo_data_in(data)
    assert [(t, p) for _, t, p in tempos] == [(0, tempo_payload(100))] and not tags
    assert {n.id: n for n in doc.notes.values()} == notes         # (not a tick moved, fingering and all)
    assert ht_read_markers(out) == fingering_of(doc)               # (Hand-thesia's own reader)
    assert T.keyframes(Document.from_bytes(data))[0].bpm == pytest.approx(100)
    mido = pytest.importorskip("mido")
    tempo_msgs = [m for tr in mido.MidiFile(out).tracks for m in tr if m.type == "set_tempo"]
    assert [(m.tempo, m.time) for m in tempo_msgs] == [(600000, 0)]
    doc.undo()
    assert doc.to_bytes()[0] == before                             # (undone: the file as it was, byte for byte)
    doc.redo()
    assert doc.to_bytes()[0] == data


def test_bake_keeping_the_timing(tmp_path):
    doc = tempo_everywhere()
    before, _ = doc.to_bytes()
    tm = doc.tempo_map()
    secs = {n.id: (tm.sec(n.start), tm.sec(n.end)) for n in doc.notes.values()}
    pedal = next(e for e in doc.events.values() if e.is_cc)
    track_end = tm.sec(doc.tracks[0].end_tick)
    doc.begin("Bake")
    forward, back = T.bake(doc, 75, keep_timing=True)
    doc.commit()
    out = os.path.join(tmp_path, "baked.mid")
    doc.export(out)
    data = open(out, "rb").read()
    tempos, tags = tempo_data_in(data)
    assert [(t, p) for _, t, p in tempos] == [(0, tempo_payload(75))] and not tags
    half = 0.5 * 60.0 / 75 / PPQ + 1e-9                            # half a tick at the new tempo
    now = Document.from_bytes(data).tempo_map()
    for n in doc.notes.values():                                   # every note sounds when it did
        assert abs(now.sec(n.start) - secs[n.id][0]) <= half and abs(now.sec(n.end) - secs[n.id][1]) <= half
    assert abs(now.sec(doc.events[pedal.id].tick) - tm.sec(pedal.tick)) <= half
    assert smf.parse(data).tracks[0].end_tick == round(track_end * 75 / 60 * PPQ)
    assert ht_read_markers(out) == fingering_of(doc)
    assert forward(back(12345)) == pytest.approx(12345)
    pm = pytest.importorskip("pretty_midi")                         # (another reader hears the same)
    heard = sorted(n.start for i in pm.PrettyMIDI(out).instruments for n in i.notes)
    assert max(abs(a - b) for a, b in zip(heard, sorted(s for s, _ in secs.values()))) <= half + 1e-6
    doc.undo()
    assert doc.to_bytes()[0] == before and doc.tracks[0].end_tick == 5 * BARS4 + 7


def test_baking_from_the_tempo_header(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("x")
    doc.put_event(Event(doc.new_id(), 0, BARS4, META, tempo_payload(60)))
    doc.put_note(Note(doc.new_id(), 1, 0, 64, 2 * BARS4, 2 * BARS4 + PPQ, 80, hand="R", finger=2))
    doc.commit()
    ed.cursor = 2 * BARS4
    key = ed._fing_key()
    app.render()
    _click(app, _chip(ed, "tempo_bake").center)
    d = app.dialog
    assert d is not None and d.title == "Bake the tempo"
    _type(app, "120")
    app.render()                                                   # (lays the buttons out)
    _click(app, next(b for b in d.buttons if b.action == "timing").rect.center)
    assert app.dialog is None
    assert [(k.tick, round(k.bpm, 3)) for k in T.keyframes(doc)] == [(0, 120.0)]
    # 8 s at 120 and 16 s at 60 up to the note: 24 s, at 120 three times four bars; its beat at 60, two at 120
    n = next(iter(doc.notes.values()))
    assert (n.start, n.end, n.hand, n.finger) == (3 * BARS4, 3 * BARS4 + 2 * PPQ, "R", 2)
    assert ed.cursor == 3 * BARS4                                  # (the cursor went with the notes)
    assert ed._fing_key() != key                                   # (the fingering gets planned again)
    _ev(app, pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)
    n = next(iter(doc.notes.values()))
    assert n.start == 2 * BARS4 and ed.cursor == 2 * BARS4 and len(T.keyframes(doc)) == 2
    # Enter: the notes keep their place
    app.render()
    _click(app, _chip(ed, "tempo_bake").center)
    _type(app, "90")
    _ev(app, pygame.KEYDOWN, key=pygame.K_RETURN)
    assert [(k.tick, round(k.bpm, 3)) for k in T.keyframes(doc)] == [(0, 90.0)]
    assert next(iter(doc.notes.values())).start == 2 * BARS4
    # and from the lane's right-click menu
    _, body = ed.lane_rects["tempo"]
    _click(app, (body.centerx, body.centery), button=3)
    assert any(it.value == "bake" for it in ed.menu.items)
    ed.menu.on_pick("bake", None)
    ed.menu = None
    assert app.dialog is not None and app.dialog.title == "Bake the tempo"
