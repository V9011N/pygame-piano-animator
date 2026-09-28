"""Loading, fingering, pedals and hand crossings - no window needed."""
import math

from conftest import midi_path

import fingering as F
import hands
import midi_loader
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo


def notes_at(pitches, hand=RIGHT, step=0.25, dur=0.24):
    return [Note(p, i * step, i * step + dur, 80, 0 if hand == RIGHT else 1, hand)
            for i, p in enumerate(pitches)]


def plan(notes, hand=RIGHT):
    vp = F.mirror_pitch if hand == LEFT else None
    g = F.group_notes(notes, vpitch=vp)
    r = F.plan_fingering(g, vp, hand=hand, context=notes)
    return [r.get(id(n)) for n in sorted(notes, key=lambda n: (n.start, n.pitch))]


def song_of(notes, duration=None):
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    return MidiSong(notes, tracks, duration or max(n.end for n in notes), [], [])


# ----- loading -------------------------------------------------------------------
def test_demo_song_loads_with_hands():
    s = midi_loader.load_song(midi_path("demo_song.mid"))
    assert len(s.notes) > 0
    assert {n.hand for n in s.notes} <= {RIGHT, LEFT}
    assert s.duration > 0


def test_pedals_are_switches():
    raw = [(0.0, 64, 44), (0.1, 64, 59), (0.2, 64, 70), (0.3, 64, 90), (0.4, 64, 30), (0.5, 67, 127)]
    assert midi_loader.pedal_switches(raw) == [(0.2, 64, 127), (0.4, 64, 0), (0.5, 67, 127)]
    s = song_of(notes_at([60, 62]))
    s2 = MidiSong(s.notes, s.tracks, s.duration, [], [], None, raw)
    assert s2.raw_controls == sorted(raw)
    assert not s2.sustain_at(0.15) and s2.sustain_at(0.25) and not s2.sustain_at(0.45)


# ----- fingering ---------------------------------------------------------------
def test_c_major_scale_right_hand():
    assert plan(notes_at([60, 62, 64, 65, 67, 69, 71, 72])) == [1, 2, 3, 1, 2, 3, 4, 5]


def test_thumb_and_pinky_pairs():
    assert F.thumb_pair(60, 62) and F.thumb_pair(64, 65)            # C-D, E-F
    assert F.thumb_pair(63, 66) and F.thumb_pair(70, 73)            # D#-F#, A#-C#
    assert not F.thumb_pair(60, 61) and not F.thumb_pair(60, 64)
    assert F.pinky_pair(71, 72) and not F.pinky_pair(61, 63)


def test_thumb_covers_two_black_keys_instead_of_rolling():
    chord = [Note(p, 0.0, 1.0, 80, 0, RIGHT) for p in (61, 63, 68, 73)]   # C#-D#-G#-C#
    assert plan(chord) == [1, 1, 3, 5]


def test_plain_chord_keeps_one_finger_per_note():
    chord = [Note(p, 0.0, 1.0, 80, 0, RIGHT) for p in (60, 62, 65, 69)]   # C-D-F-A
    assert plan(chord) == [1, 2, 3, 5]


def test_given_fingers_are_kept():
    ns = notes_at([60, 62, 64])
    ns[1] = Note(62, ns[1].start, ns[1].end, 80, 0, RIGHT, finger=4)
    assert plan(ns)[1] == 4


# ----- hands -------------------------------------------------------------------
def test_hand_pose_is_finite():
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    s = midi_loader.load_song(midi_path("demo_song.mid"))
    kb = Keyboard(bottom_layout((1600, 900))[0])
    for h in {n.hand for n in s.notes}:
        a = hands.HandAnimator(s, h)
        pose = a.pose(s.duration / 2, kb)
        pts = [p for c in pose["struct"]["chains"].values() for p in c]
        assert len(pose["struct"]["chains"]) == 5
        assert all(math.isfinite(v) for p in pts for v in p)


def test_crossing_hand_goes_over():
    def crossing(kind):
        ns = []
        for k in range(16):
            t = k * 0.25
            if kind == "L_up":
                ns.append(Note([60, 64, 67, 64][k % 4], t, t + 0.24, 70, 0, RIGHT))
            else:
                ns.append(Note([48, 52, 55, 52][k % 4], t, t + 0.24, 70, 1, LEFT))
        for k, t in enumerate([0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]):
            if kind == "L_up":
                ns.append(Note([36, 43, 84, 86, 84, 43, 36, 43][k], t, t + 0.45, 80, 1, LEFT))
            else:
                ns.append(Note([79, 76, 36, 38, 36, 76, 79, 76][k], t, t + 0.45, 80, 0, RIGHT))
        return song_of(ns, 4.0)
    assert {e[2] for e in hands.crossing_episodes(crossing("L_up"))} == {LEFT}
    assert {e[2] for e in hands.crossing_episodes(crossing("R_down"))} == {RIGHT}


def test_repeated_chords_bounce_and_tremolo_rolls():
    rep = [Note(p, i * 0.2, i * 0.2 + 0.18, 80, 0, RIGHT) for i in range(8) for p in (60, 64)]
    a = hands.HandAnimator(song_of(rep), RIGHT)
    assert len(a.bounce_runs) == 1
    trem = []
    for i in range(12):
        ps = (64, 67) if i % 2 == 0 else (60,)
        trem += [Note(p, i * 0.1, i * 0.1 + 0.09, 80, 0, RIGHT) for p in ps]
    b = hands.HandAnimator(song_of(trem), RIGHT)
    assert len(b.roll_runs) == 1


def test_idle_hand_gets_out_of_the_way():
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    # the left hand plays a chord, rests while the right hand leaps down past
    # it and back up, then plays again
    ns = [Note(p, t, t + 0.6, 70, 1, LEFT) for t in (0.0, 7.0) for p in (48, 52, 55)]
    seq = [(0.0, 72), (0.4, 76), (1.6, 48), (1.9, 52), (2.5, 45), (3.0, 40), (3.3, 43),
           (4.2, 76), (4.5, 79), (5.2, 36), (5.5, 40), (6.2, 72), (6.5, 76)]
    ns += [Note(p, t, t + 0.28, 80, 0, RIGHT) for t, p in seq]
    s = song_of(ns, 8.0)
    assert hands.crossing_episodes(s) == []
    kb = Keyboard(bottom_layout((1600, 900))[0])
    an = {h: hands.HandAnimator(s, h) for h in (RIGHT, LEFT)}
    hands.pair_hands(an.values())
    xs = []
    for i in range(0, 8 * 30):
        t = i / 30
        x = {h: a.pose(t, kb)["wrist"][0] for h, a in an.items()}
        lx = 2 * an[LEFT].axis_x - x[LEFT]                      # back from the mirrored frame
        xs.append(lx)
        if 1.8 <= t <= 6.0:                                      # left hand idle: never crossed
            assert x[RIGHT] - lx > 0.6 * kb.white_w / 0.9 * hands.hand_span_inches()
    # it follows smoothly and is back on its chord in time
    assert max(abs(b - a) for a, b in zip(xs, xs[1:])) < 30
    assert abs(xs[int(7.0 * 30)] - xs[int(0.3 * 30)]) < 0.3 * kb.white_w
