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
    # starting more than 5 keys from where the last one ended: the hand may go back to rest
    far = run(WHITE[10:22], t0=1.0) + run(WHITE[30:18:-1], t0=1.8)            # 9 white keys away
    near = run(WHITE[10:22], t0=1.0) + run(WHITE[26:14:-1], t0=1.8)           # 5 away
    assert [len(e) for e in glissando.episodes(glissando.detect(far, 0.05, 6), far, 1.0)] == [1, 1]
    assert [len(e) for e in glissando.episodes(glissando.detect(near, 0.05, 6), near, 1.0)] == [2]


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
        assert set(p["struct"]["nail_hide"]) >= {2, 3, 4, 5}           # only the thumb's nail could show
        tip = min((c[-1] for c in p["struct"]["chains"].values()), key=lambda q: q[2])
        assert abs(tip[0] - kb.key_rects[n.pitch].centerx) < 1.5 * kb.white_w
    half = a.pose(g[0].start - 0.05, kb)                               # blending in
    assert set(half["struct"]["nail_hide"]) >= {2, 3, 4, 5}
    assert not a.pose(0.3, kb)["struct"].get("nail_hide")
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


def test_palm_up_toward_the_little_finger_thumb_method_toward_the_thumb(screen):
    import math
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    d3 = lambda a, b: math.dist(a, b)
    for hand, ps, palm_up in ((RIGHT, WHITE[14:30], True), (RIGHT, WHITE[29:13:-1], False),
                              (LEFT, WHITE[16:0:-1], True), (LEFT, WHITE[1:17], False)):
        g = run(ps, t0=1.0, hand=hand)
        a = hands.HandAnimator(song_of(g), hand)
        a._ensure_layout(kb)
        n = g[8]
        s = a.pose(n.start + 0.01, kb)["struct"]
        assert s["palm_up"] == palm_up, (hand, palm_up)
        assert (s["flush"], s["thumb_edge"]) == ((1.0, 0.0) if palm_up else (0.0, 1.0))
        if palm_up:
            continue
        ch = s["chains"]
        # fingers 2-5 curled right in: their tips near their knuckles
        for f in range(2, 6):
            length = sum(d3(p, q) for p, q in zip(ch[f][1:], ch[f][2:]))
            assert d3(ch[f][1], ch[f][-1]) < 0.6 * length, f
        # the thumb straight, along the keys, its tip (the nail) on the glissando's key
        th = ch[1]
        assert d3(th[0], th[-1]) > 0.95 * sum(d3(p, q) for p, q in zip(th, th[1:]))
        dx, dy = th[-1][0] - th[1][0], th[-1][1] - th[1][1]
        assert abs(dx) < 0.3 * abs(dy)
        assert abs(th[-1][0] - kb.key_rects[n.pitch].centerx) < 1.5 * kb.white_w
        # the fist's knuckles (the middle joints) over the keys (y: up the keys from their front edge)
        assert all(ch[f][2][1] > 0.25 * a.ppi for f in range(2, 6))
        front = kb.rect.h - kb.black_h                      # ...but not into the black keys
        assert max(p[1] for f in range(2, 6) for p in ch[f]) < front - 0.3 * a.ppi


def test_flush_fingers_touch_outline_to_outline():
    import skins
    sk = skins.normalize({}, bone_color=(200, 200, 200))
    fw, ppi, gap = sk["finger_width"], 30.0, 1.0
    # fingers 2-5 straight up the screen, spread wide apart
    chains = {f: [(x, 0.0, 0.0), (x, -10.0, 0.0), (x, -40.0, 0.0), (x, -60.0, 0.0), (x, -75.0, 0.0)]
              for f, x in zip((2, 3, 4, 5), (0.0, 40.0, 80.0, 120.0))}
    skins._flush(chains, fw, gap, ppi, 1.0)
    for a, b in ((2, 3), (3, 4), (4, 5)):
        want = (skins.FINGER_W_IN[a] + skins.FINGER_W_IN[b]) * fw * ppi / 2 + gap     # at the knuckles
        assert abs(chains[b][1][0] - chains[a][1][0] - want) < 1e-6
        assert chains[b][-1][0] > chains[a][-1][0]                                   # still in order
