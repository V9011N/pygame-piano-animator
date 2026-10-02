"""Glissandos: detection, the pose, and marking them in the fingering editor."""
import pygame

from conftest import midi_path

import glissando
import hands
import midi_loader
import pianist
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo

WHITE = [p for p in range(48, 100) if p % 12 not in glissando.BLACK]
BLACKS = [p for p in range(48, 100) if p % 12 in glissando.BLACK]


def run(ps, t0=1.0, gap=0.03, hand=RIGHT):
    return [Note(p, t0 + i * gap, t0 + i * gap + 0.08, 90, 0, hand) for i, p in enumerate(ps)]


def song_of(notes):
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    return MidiSong(notes, tracks, max(n.end for n in notes) + 1, [], [])


def test_detection_white_black_gap_and_length():
    up = run(WHITE[10:22])                                   # 12 white keys up, 30 ms apart
    assert [len(r) for r in glissando.detect(up, 0.05, 6)] == [12]
    down = run(BLACKS[12:2:-1])                              # 10 black keys down
    assert [len(r) for r in glissando.detect(down, 0.05, 6)] == [10]
    assert glissando.detect(run(WHITE[10:22], gap=0.07), 0.05, 6) == []        # too slow
    assert glissando.detect(run(WHITE[10:14]), 0.05, 6) == []                   # too short
    assert [len(r) for r in glissando.detect(run(WHITE[10:14]), 0.05, 3)] == [4]
    assert glissando.detect(run(range(60, 72)), 0.05, 6) == []                  # chromatic: not a glissando
    # performance untidiness: a skipped key, two keys in the same instant
    ps = WHITE[10:22]
    messy = run(ps[:5] + ps[6:])
    messy[3] = Note(messy[3].pitch, messy[2].start + 0.004, messy[3].end, 90, 0, RIGHT)
    assert [len(r) for r in glissando.detect(messy, 0.05, 6)] == [11]


def test_glissandos_close_together_are_one_episode():
    a = run(WHITE[10:22], t0=1.0)
    b = run(WHITE[21:9:-1], t0=1.8)                          # back down 0.47 s later
    c = run(WHITE[10:22], t0=5.0)                            # much later
    notes = a + b + c
    runs = glissando.detect(notes, 0.05, 6)
    assert [len(e) for e in glissando.episodes(runs, notes, 1.0)] == [2, 1]
    chord = [Note(40, 1.6, 1.7, 80, 0, RIGHT)]               # something else to play in the break
    assert [len(e) for e in glissando.episodes(runs, notes + chord, 1.0)] == [1, 1, 1]


def test_the_hand_slides_a_glissando_and_its_notes_keep_their_times(screen):
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    lead = [Note(60, 0.2, 0.5, 80, 0, RIGHT)]
    g = run(WHITE[14:30], t0=1.0)
    a = hands.HandAnimator(song_of(lead + g), RIGHT)
    assert all(a.is_gliss(n) and a.finger_for(n) is None for n in g)
    assert not a.is_gliss(lead[0]) and a.finger_for(lead[0])
    heard = {id(n): (s, e) for s, e, n in a.performance}
    assert all(heard[id(n)] == (n.start, n.end) for n in g)          # exactly as written
    a._ensure_layout(kb)
    normal = a.pose(0.3, kb)
    for n in g[4:12]:
        t = n.start + 0.01
        assert a._gliss_w(t)[0] == 1.0
        p = a.pose(t, kb)
        assert len(p["bones"]) == len(normal["bones"])                 # the same skeleton, so poses blend
        tip = min((c[-1] for c in p["struct"]["chains"].values()), key=lambda q: q[2])
        assert abs(tip[0] - kb.key_rects[n.pitch].centerx) < 1.5 * kb.white_w
    a.pose(g[0].start - 0.05, kb)                                      # blending in
    # the pianist can be told not to: only marked glissandos are slid then
    p = pianist.active().copy()
    p.behavior["glissando"] = "off"
    b = hands.HandAnimator(song_of(lead + g), RIGHT, pianist=p)
    assert not any(b.is_gliss(n) for n in g)
    marked = [Note(n.pitch, n.start, n.end, 90, 0, RIGHT, None, True) for n in g]
    c = hands.HandAnimator(song_of(lead + marked), RIGHT, pianist=p)
    assert all(c.is_gliss(n) for n in marked)


def test_marking_a_glissando_in_the_editor_and_exporting_it(screen, tmp_path):
    import pretty_midi
    import main
    # a slow white-key string (too slow to be found by itself) and a chord
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0, name="Right hand")
    ps = WHITE[20:28]
    for k, p in enumerate(ps):
        inst.notes.append(pretty_midi.Note(80, p, 1.0 + 0.15 * k, 1.1 + 0.15 * k))
    for p in (48, 52, 55):
        inst.notes.append(pretty_midi.Note(80, p, 3.0, 3.5))
    pm.instruments.append(inst)
    src = tmp_path / "s.mid"
    pm.write(str(src))
    app = main.App(screen, sound=False)
    app.edit(midi_loader.load_song(str(src)))
    ed = app.mode
    run_idx = [i for i, n in enumerate(ed.notes) if n.pitch in ps]
    chord_idx = [i for i, n in enumerate(ed.notes) if n.pitch not in ps]
    assert not any(ed.is_gliss(i) for i in run_idx)
    assert ed.gliss_candidates(chord_idx) is None             # a chord is no glissando
    assert ed.gliss_candidates(run_idx)
    ed.set_gliss(run_idx, True)
    assert all(ed.notes[i].gliss and ed.is_gliss(i) for i in run_idx)
    ed.render()
    ed.undo()
    assert not any(ed.notes[i].gliss for i in run_idx)
    ed.undo(redo=True)
    out = tmp_path / "g.mid"
    fing = {id(n): ("g" if n.gliss else f) for n, f in zip(ed.notes, ed.finger)}
    midi_loader.save_fingered_midi(str(src), str(out), ed.notes, fing)
    back = midi_loader.load_song(str(out))
    assert sorted(n.pitch for n in back.notes if n.gliss) == ps
    assert not any(n.gliss for n in back.notes if n.pitch not in ps)
