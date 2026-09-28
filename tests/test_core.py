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


def test_fingers_stay_on_their_keys():
    import bisect
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    # left hand: 2 repeating on a white key between 5 and 1 on black keys, so
    # the hand sits forward and the index has to play its key further in
    ns = []
    for k in range(12):
        t = 0.5 + k * 0.3
        ns.append(Note(53, t, t + 0.27, 80, 1, LEFT, finger=2))
        ns.append(Note(46 if k % 2 == 0 else 56, t, t + 0.27, 80, 1, LEFT, finger=5 if k % 2 == 0 else 1))
    kb = Keyboard(bottom_layout((1600, 900))[0])
    a = hands.HandAnimator(song_of(ns, 4.5), LEFT)
    xs = []
    for i in range(4 * 60):
        t = i / 60
        pose = a.pose(t, kb)
        for f in (1, 2, 5):
            if a._pressing(f, t):
                n = a.by_finger[f][bisect.bisect_right(a.finger_starts[f], t) - 1]
                tip = pose["struct"]["chains"][f][-1]
                lo, hi = a._key_depths(n.pitch)
                assert abs(tip[0] - kb.key_rects[n.pitch].centerx) < 0.1 * kb.white_w      # square across the key
                assert lo - 1 <= tip[1] <= hi + 1                                           # and on it
        if 1.0 < t < 4.0:
            xs.append(pose["struct"]["chains"][2][-1][0])
    assert max(xs) - min(xs) < 0.15 * kb.white_w          # no twitching on the repeated key


def test_impossible_given_fingering_is_repaired_for_playback():
    # an octave fingered 4-5 (as in a file): the player plays it 1-5, keeping
    # the 5; the editor (repair=False) keeps the file's fingers
    ns = []
    for k in range(4):
        t = k * 0.4
        ns += [Note(81, t, t + 0.3, 80, 0, RIGHT, finger=4), Note(93, t, t + 0.3, 80, 0, RIGHT, finger=5)]
    s = song_of(ns)
    a = hands.HandAnimator(s, RIGHT)
    assert [a.finger_for(n) for n in ns[:2]] == [1, 5]
    assert len(a.repaired) == 4
    b = hands.HandAnimator(s, RIGHT, repair=False)
    assert [b.finger_for(n) for n in ns[:2]] == [4, 5] and not b.repaired


def test_held_key_is_let_go_before_a_leap():
    # the octave is still down when the chord two octaves lower starts
    ns = [Note(72, 0.0, 0.52, 80, 0, RIGHT, finger=1), Note(84, 0.0, 0.52, 80, 0, RIGHT, finger=5),
          Note(48, 0.5, 0.8, 80, 0, RIGHT, finger=1), Note(52, 0.5, 0.8, 80, 0, RIGHT, finger=3)]
    a = hands.HandAnimator(song_of(ns), RIGHT)
    ends = {n.pitch: e for s, e, n in a.performance}
    assert ends[84] <= 0.5 - 0.1


def test_hand_split_keeps_to_the_top_speed():
    import hand_split
    import fingering as F
    # Liszt-style alternation (Dante Sonata): repeated high chords for the
    # right hand, low and middle chords for the left, ~0.11 s apart. The left
    # hand must not take the bottom of the high chords and then leap down.
    ns = []
    t = 0.0
    for _ in range(3):
        for chord in ([70, 76, 79, 82], [38, 43, 46, 52], [69, 76, 81], [55, 58, 64]):
            for _ in range(2):
                ns += [Note(p, t, t + 0.06, 80, 0, None) for p in chord]
                t += 0.11
    split = hand_split.split_hands(ns)
    for n in ns:
        if n.pitch >= 69:
            assert split[id(n)] == RIGHT
    # and no hand has to move faster than the top speed
    for h in (LEFT, RIGHT):
        mine = sorted((n for n in ns if split[id(n)] == h), key=lambda n: n.start)
        groups = F.group_notes(mine)
        for (t0, a), (t1, b) in zip(groups, groups[1:]):
            wk = lambda ps: (F.key_pos(max(ps)) - hand_split.HAND_WK, F.key_pos(min(ps)))
            ra, rb = wk([n.pitch for n in a]), wk([n.pitch for n in b])
            gap = max(0.0, rb[0] - ra[1], ra[0] - rb[1])
            assert F.travel_time(gap) <= F.MOVE_SHARE * (t1 - t0) + 1e-6


def test_hands_keep_to_the_top_speed_and_play_what_they_do():
    import math
    import pygame
    import fingering as F
    from common import Keyboard, Performance, bottom_layout
    pygame.init()
    # right hand: quick leaps between a middle chord and two octaves up, the
    # last of them too quick for the default top speed
    ns = []
    for k, (t, chord) in enumerate([(0.2, [60, 64, 67]), (0.6, [84, 88, 91]), (0.8, [60, 64, 67]),
                                    (1.1, [84, 88, 91]), (1.22, [60, 64, 67]), (1.8, [72])]):
        ns += [Note(p, t, t + 0.15, 80, 0, RIGHT) for p in chord]
    s = song_of(ns, 2.5)
    kb = Keyboard(bottom_layout((1600, 900))[0])
    a = hands.HandAnimator(s, RIGHT)
    # every finger lets go early enough to reach its next key at the top speed
    for f, fns in a.by_finger.items():
        st, en = a.finger_starts[f], a.finger_ends[f]
        for i in range(1, len(fns)):
            if fns[i].pitch != fns[i - 1].pitch:
                need = F.travel_time(F.key_pos(fns[i].pitch) - F.key_pos(fns[i - 1].pitch), a.max_speed)
                assert st[i] - en[i - 1] >= need - 1e-6
    assert a.delayed >= 1                       # the last leap is struck late rather than rushed
    # the animation never goes faster than that either
    ppm = None
    prev = None
    fps = 120
    for i in range(int(2.4 * fps)):
        pose = a.pose(i / fps, kb)
        ppm = pose["ppi"] / 0.0254
        st = pose["struct"]
        w = ((st["wrist"][0][0] + st["wrist"][1][0]) / 2, (st["wrist"][0][1] + st["wrist"][1][1]) / 2)
        tips = [c[-1] for c in st["chains"].values()]
        if prev:
            assert math.hypot(w[0] - prev[0][0], w[1] - prev[0][1]) * fps / ppm <= a.max_speed * 1.02
            for p, q in zip(tips, prev[1]):
                assert math.hypot(p[0] - q[0], p[1] - q[1]) * fps / ppm <= a.max_speed * 1.35
        prev = (w, tips)
    # what is heard is exactly what the hands play: their press / release times
    perf = Performance.from_animators([a])
    assert sorted((p, r, id(n)) for p, r, n in perf.items) == sorted((p, r, id(n)) for p, r, n in a.performance)
    late = [p - n.start for p, r, n in a.performance if p > n.start + 1e-6]
    assert late and max(late) <= hands.MAX_DELAY_T + 1e-6


def test_finger_anticipation_goes_down_to_just_in_time():
    import pianist
    # a C major scale at 8 notes a second: how early does the thumb set off
    # for the notes it passes under to?
    scale = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77]
    ns = [Note(p, 0.3 + i * 0.125, 0.3 + i * 0.125 + 0.12, 80, 0, RIGHT) for i, p in enumerate(scale)]
    head = {}
    for v in (-1.0, 0.0):
        p = pianist.Pianist("t")
        p.behavior["antic_fingers"] = v
        a = hands.HandAnimator(song_of(ns), RIGHT, pianist=p)
        k = len(a.by_finger[1])
        assert k >= 3                                  # the thumb passes under at least twice
        head[v] = [a.finger_starts[1][i + 1] - a._prep_window(1, i)[0] for i in range(k - 1)]
    assert all(h <= 0.1 for h in head[-1.0])            # just in time
    assert all(h >= 0.2 for h in head[0.0])             # the old lowest setting: two notes ahead


def test_chromatic_octaves_play_white_keys_up_among_the_black_ones():
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    # right-hand chromatic octaves up and down: white keys are played up by
    # the black keys, so the hand doesn't move in and out with every octave
    ps = list(range(54, 66)) + list(range(66, 54, -1))
    ns = []
    for i, p in enumerate(ps):
        t = 0.3 + i * 0.14
        ns += [Note(p, t, t + 0.12, 80, 0, RIGHT), Note(p + 12, t, t + 0.12, 80, 0, RIGHT)]
    kb = Keyboard(bottom_layout((1600, 900))[0])
    a = hands.HandAnimator(song_of(ns), RIGHT)
    front = kb.rect.h - kb.black_h
    ys = []
    for i in range(int(3.5 * 60)):
        t = 0.6 + i / 60
        pose = a.pose(t, kb)
        ys.append(pose["wrist"][1] / pose["ppi"])
    for n in ns:
        if not n.is_black:
            assert a.white_up[id(n)] == 1.0
            assert a.key_target(n.pitch, 5, n)[1] > front          # past the black keys' front
    travel = sum(abs(b - c) for b, c in zip(ys, ys[1:])) / 3.5
    assert travel < 4.0                                   # in/s in and out (it was ~8 before)
