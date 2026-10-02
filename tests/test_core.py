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
                lo = kb.rect.h - kb.black_h if n.is_black else 0
                assert abs(tip[0] - kb.key_rects[n.pitch].centerx) < 0.1 * kb.white_w      # square across the key
                assert lo <= tip[1] <= kb.rect.h                                            # and on it
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


def _chromatic_octaves(velocity):
    ps = list(range(54, 66)) + list(range(66, 54, -1))
    ns = []
    for i, p in enumerate(ps):
        t = 0.3 + i * 0.14
        ns += [Note(p, t, t + 0.12, velocity, 0, RIGHT), Note(p + 12, t, t + 0.12, velocity, 0, RIGHT)]
    return ns


def test_chromatic_octaves_play_white_keys_up_among_the_black_ones():
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    # soft right-hand chromatic octaves up and down: white keys are played up
    # by the black keys, so the hand doesn't move in and out with every octave
    ns = _chromatic_octaves(45)
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


def test_loud_notes_are_played_near_the_front_of_the_keys():
    import pygame
    import pianist
    from common import Keyboard, bottom_layout
    pygame.init()
    kb = Keyboard(bottom_layout((1600, 900))[0])
    front = kb.rect.h - kb.black_h
    # fortissimo chromatic octaves: white keys stay below the black ones, for leverage
    ns = _chromatic_octaves(120)
    a = hands.HandAnimator(song_of(ns), RIGHT)
    a._ensure_layout(kb)
    for n in ns:
        lo, hi = a._key_depths(n.pitch, n, 5)
        y = a.key_target(n.pitch, 5, n)[1]
        assert lo <= y <= hi
        if not n.is_black:
            assert y < front
    assert hands.loudness(120) == 1.0 and hands.loudness(40) == 0.0 < hands.loudness(80) < 1.0
    # the pianist's playing area bounds it all: nothing past `key_area_far`
    p = pianist.Pianist("t")
    p.behavior["key_area_far"] = 0.5
    b = hands.HandAnimator(song_of(_chromatic_octaves(40)), RIGHT, pianist=p)
    b._ensure_layout(kb)
    for n in b.by_finger[5]:
        lo, hi = b._key_depths(n.pitch, n, 5)
        full = b._key_depths(n.pitch)
        assert abs(hi - full[1]) < 1e-9 and b.key_target(n.pitch, 5, n)[1] <= hi
    assert all(b.key_target(n.pitch, 5, n)[1] < front for n in b.by_finger[5] if not n.is_black)


def test_fingers_aim_where_they_are_going_without_snapping():
    import math
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    kb = Keyboard(bottom_layout((1600, 900))[0])
    a = hands.HandAnimator(song_of(notes_at([60, 64, 67, 72])), RIGHT)
    a._ensure_layout(kb)
    wx, wy, psi = 500.0, -60.0, 0.0
    blx, bly, blz = a.base_local[2]
    hmin, hmax = a._reach_range(2, blz - 20.0, 0.99)
    # a target within reach but 60 degrees out, far past the index finger's
    # splay: it is brought to the nearest point on the limit's line, not
    # swung round at full length (the old clamp: 0.9 of the reach)
    ang = math.radians(60)
    x, y = wx + blx + 0.9 * hmax * math.sin(ang), wy + bly + 0.9 * hmax * math.cos(ang)
    cx, cy = a._clamp_tip(2, x, y, 20.0, wx, wy, psi)
    assert math.hypot(cx - wx - blx, cy - wy - bly) < 0.75 * hmax
    # the spot along a key changes smoothly as the hand moves across (the old
    # choice jumped up to 1.5 in at once)
    kx = a.key_target(67, 2)[0]
    for p in (62, 64, 66, 68):
        for f in (2, 3, 4, 5):
            ys = [a._key_spot(p, f, (kx - 300 + i * 0.5, wy, psi))[1] for i in range(1200)]
            assert max(abs(b - c) for b, c in zip(ys, ys[1:])) < 0.3 * a.ppi


def test_a_held_middle_voice_stays_down_under_moving_octaves():
    import fingering as F
    # Op. 25 No. 10, 0:06: the right hand holds D5 with 2 while its octaves
    # move by step around it (1-5 / 1-4); then 2 steps down to B4
    ns = []
    octs = [71, 70, 71, 70, 69, 68, 69, 68, 69, 68, 67, 66, 67]
    for i, p in enumerate(octs):
        t = 0.2 + i * 0.139
        ns += [Note(p, t, t + 0.13, 90, 0, RIGHT), Note(p + 12, t, t + 0.13, 90, 0, RIGHT)]
    ns += [Note(74, 0.2, 1.03, 90, 0, RIGHT, finger=2), Note(71, 1.034, 1.6, 90, 0, RIGHT, finger=2)]
    a = hands.HandAnimator(song_of(ns), RIGHT)
    held = {n.pitch: r - p for p, r, n in a.performance if n.pitch in (74, 71) and n.finger}
    assert held[74] >= 0.8 * 0.83                   # kept down, not let go at the next octave
    assert a._jump_lift(ns[-2], ns[-1]) <= 0.05     # a step in the same voice stays legato
    # a stretch the hand can make is a range of places, not a point
    lo, hi = F.hand_range([70, 82], [1, 4])          # an octave with 1-4: wider than 1-4's spacing
    assert hi - lo > 0.5


def test_octaves_open_to_1_5_around_a_held_inner_note():
    import fingering as F
    # Op. 25 No. 10's middle voice: D5 held under moving octaves - without it
    # the black-key octaves take 4 on top (Hanon); with it, 1-5 around a 2
    def octaves(held):
        ns = []
        for i, p in enumerate([71, 70, 71, 70, 69, 68, 69, 68]):
            t = 0.2 + i * 0.139
            ns += [Note(p, t, t + 0.13, 90, 0, RIGHT), Note(p + 12, t, t + 0.13, 90, 0, RIGHT)]
        if held:
            ns.append(Note(74, 0.2, 1.03, 90, 0, RIGHT))
        return ns
    for held in (False, True):
        ns = octaves(held)
        fing = hands.HandAnimator(song_of(ns), RIGHT).fingering
        tops = [fing[id(n)] for n in ns if n.pitch >= 80 and n.start < 1.0]
        if held:
            assert all(f == 5 for f in tops)
            assert fing[id(ns[-1])] == 2
        else:
            assert 4 in tops
    # no pair of fingers in a chord is asked to reach further than it can,
    # neighbours or not: an octave can't be 1-3 with 2 between them
    assert F.inner_room_cost([(70, 1), (74, 2), (82, 4)]) > 0 == F.inner_room_cost([(70, 1), (74, 2), (82, 5)])


def test_the_hand_reaches_an_octave_with_a_held_middle_finger():
    import pygame
    from common import Keyboard, bottom_layout
    pygame.init()
    # A4-D5-A5 held with 1-3-5 (Op. 25 No. 10's middle voice): the hand fit
    # puts every finger on its key (with the middle finger's old +-12 degree
    # splay it fell 0.12 in short)
    ns = [Note(69, 0.2, 1.2, 90, 0, RIGHT, finger=1), Note(74, 0.2, 1.2, 90, 0, RIGHT, finger=3),
          Note(81, 0.2, 1.2, 90, 0, RIGHT, finger=5)]
    kb = Keyboard(bottom_layout((1600, 900))[0])
    a = hands.HandAnimator(song_of(ns), RIGHT)
    pose = a.pose(0.7, kb)
    for n in ns:
        f = a.fingering[id(n)]
        tip = pose["struct"]["chains"][f][-1]
        assert abs(tip[0] - kb.key_rects[n.pitch].centerx) < 0.1 * kb.white_w


def _smf(tracks, division=480):
    import struct

    def vlq(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)
    body = b""
    for events in tracks:
        data = b"".join(vlq(d) + e for d, e in events) + b"\x00\xff\x2f\x00"
        body += b"MTrk" + struct.pack(">I", len(data)) + data
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division) + body


def test_damaged_midi_files_are_repaired(tmp_path):
    import pretty_midi
    # an impossible key signature, a data byte over 127, and a file cut off mid-event
    files = {
        "badkey": _smf([[(0, b"\xff\x59\x02\x14\x00"), (0, b"\x90\x3c\x50"), (480, b"\x80\x3c\x40")]]),
        "badbyte": _smf([[(0, b"\x90\x3c\xd0"), (480, b"\x80\x3c\x40"), (0, b"\x90\x40\x50"), (480, b"\x80\x40\x40")]]),
    }
    good = _smf([[(0, b"\x90\x3c\x50"), (480, b"\x80\x3c\x40"), (0, b"\x90\x40\x50"), (480, b"\x80\x40\x40")]])
    files["truncated"] = good[:-9]
    for name, data in files.items():
        p = tmp_path / (name + ".mid")
        p.write_bytes(data)
        s = midi_loader.load_song(str(p))
        assert s.notes and s.notes[0].pitch == 60, name
        assert s.cleanup, name
    # repairing an undamaged file changes nothing
    import io
    clean, fixed = midi_loader.repair_smf(open(midi_path("demo_song.mid"), "rb").read())
    a = pretty_midi.PrettyMIDI(midi_path("demo_song.mid"))
    b = pretty_midi.PrettyMIDI(io.BytesIO(clean))
    key = lambda pm: sorted((round(n.start, 4), n.pitch, n.velocity) for i in pm.instruments for n in i.notes)
    assert fixed == 0 and key(a) == key(b)


def test_repaired_and_sanitized_files_export_their_fingering(tmp_path):
    import struct
    good = _smf([[(0, b"\x90\x3c\x50"), (480, b"\x80\x3c\x40"), (0, b"\x90\x40\x50"), (480, b"\x80\x40\x40")]])
    riff = b"RIFF" + struct.pack("<I", len(good) + 12) + b"RMIDdata" + struct.pack("<I", len(good)) + good
    doubled = _smf([[(0, b"\x90\x3c\x50"), (480, b"\x80\x3c\x40")], [(0, b"\x91\x3c\x50"), (480, b"\x81\x3c\x40")]])
    files = {"badbyte": _smf([[(0, b"\x90\x3c\xd0"), (480, b"\x80\x3c\x40"),
                               (0, b"\x90\x40\x50"), (480, b"\x80\x40\x40")]]),
             "truncated": good[:-9], "riff": riff, "doubled": doubled}
    for name, data in files.items():
        src, dst = tmp_path / (name + ".mid"), tmp_path / (name + "_fingered.mid")
        src.write_bytes(data)
        s = midi_loader.load_song(str(src))
        fing = {id(n): 3 for n in s.notes}
        # every note marked, counted once even when it was doubled on two tracks
        assert midi_loader.save_fingered_midi(str(src), str(dst), s.notes, fing) == len(s.notes), name
        back = midi_loader.load_song(str(dst))
        assert [(n.pitch, n.hand, n.finger) for n in back.notes] == [(n.pitch, n.hand, 3) for n in s.notes], name


def test_only_the_piano_part_of_a_full_score_is_kept(tmp_path):
    # a concerto's full score: a piano solo track plus orchestra tracks
    piano = [(0, b"\xff\x03\x0aPIANO SOLO"), (0, b"\xc0\x00"),
             (0, b"\x90\x3c\x50"), (0, b"\x90\x3c\x50"),        # the same key struck twice at once
             (480, b"\x80\x3c\x40"), (0, b"\x90\x40\x50"), (960, b"\x80\x40\x40")]
    piano2 = [(0, b"\xff\x03\x05Piano"), (0, b"\xc1\x00"), (240, b"\x91\x40\x50"),   # E4 again while still down
              (480, b"\x81\x40\x40")]
    violin = [(0, b"\xff\x03\x09Violini I"), (0, b"\xc2\x30"), (0, b"\x92\x48\x50"), (480, b"\x82\x48\x40")]
    timp = [(0, b"\xff\x03\x07Timpani"), (0, b"\xc3\x2f"), (0, b"\x93\x28\x50"), (480, b"\x83\x28\x40")]
    p = tmp_path / "score.mid"
    p.write_bytes(_smf([piano, piano2, violin, timp]))
    s = midi_loader.load_song(str(p))
    assert sorted(n.pitch for n in s.notes) == [60, 64, 64]         # no violin (72) or timpani (40), C4 once
    first_e = min((n for n in s.notes if n.pitch == 64), key=lambda n: n.start)
    assert first_e.end <= 0.75 + 1e-6                               # ended where the key is struck again
    assert {t.name for t in s.tracks} == {"PIANO SOLO", "Piano"}
    assert any("dropped 2 other instrument" in line for line in s.cleanup)
    # a file with nothing recognisably piano keeps all its tracks
    q = tmp_path / "strings.mid"
    q.write_bytes(_smf([violin, timp]))
    assert len(midi_loader.load_song(str(q)).notes) == 2


def test_hand_split_with_voice_tracks_and_repeated_chords():
    import hand_split
    # two unlabelled tracks (voices) and a chord struck twice: the track
    # memory and the repeated-chord rule used the same variable (crashed)
    ns = []
    for k in range(6):
        t = k * 0.2
        ns += [Note(p, t, t + 0.18, 80, 0, None) for p in (72, 76)]
        ns += [Note(p, t, t + 0.18, 80, 1, None) for p in (48, 55)]
    split = hand_split.split_hands(ns)
    assert all(split[id(n)] == (RIGHT if n.pitch > 60 else LEFT) for n in ns)


def test_track_hands_give_way_only_where_a_hand_cant_keep_up():
    import hand_split
    # the ossia cadenza of Rachmaninoff 3 (0:57): one track holds both hands'
    # notes - a chord low and an octave high, alternating every 0.08 s
    ns, prefer = [], {}
    for k in range(16):
        t = k * 0.082
        ps = (48, 52, 55) if k % 2 == 0 else (84, 96)
        for p in ps:
            n = Note(p - (k // 2) % 3, t, t + 0.07, 80, 1, LEFT)
            ns.append(n)
            prefer[id(n)] = LEFT
    split = hand_split.split_hands(ns, prefer=prefer)
    high = [n for n in ns if n.pitch > 70]
    assert sum(split[id(n)] == RIGHT for n in high) >= len(high) - 2      # the free hand takes them
    # where the tracks are playable they are followed exactly - crossings included
    ns, prefer = [], {}
    for k in range(8):
        t = k * 0.25
        r = Note(60 + k % 3, t, t + 0.2, 80, 0, RIGHT)
        lft = Note(64 + k % 3, t + 0.12, t + 0.2, 80, 1, LEFT)          # the left hand crossed above
        ns += [r, lft]
        prefer[id(r)], prefer[id(lft)] = RIGHT, LEFT
    split = hand_split.split_hands(ns, prefer=prefer)
    assert all(split[id(n)] == prefer[id(n)] for n in ns)


def test_wrist_glides_through_scale_runs_and_arpeggios_are_left_alone():
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1600, 900))[0])
    major = [0, 2, 4, 5, 7, 9, 11]
    up = [60 + 12 * o + x for o in range(3) for x in major] + [96]
    scale = notes_at(up + up[-2::-1], step=0.08, dur=0.084)
    a = hands.HandAnimator(song_of(scale), RIGHT)
    a._ensure_layout(kb)
    assert len(a.runs) == 1
    xs, close = [], 0
    t = scale[0].start
    while t < scale[-1].end:
        wx, wy, psi = a._limited_at(t)
        xs.append(wx)
        a.pose(t, kb)
        c = a._run_w(t)
        tips = a._separate({f: a._limit_tip(f, p, wx, wy, psi, c) for f, p in a._limited_tips(t).items()},
                           wx, wy, psi, t, c)
        loc = {f: hands.HandAnimator._rot(x - wx, y - wy, -psi)[0] for f, (x, y, _) in tips.items()}
        close += any(loc[f + 1] - loc[f] < 0.5 * kb.white_w for f in (2, 3, 4))
        t += 1 / 60
    v = [b - x for x, b in zip(xs, xs[1:])]
    signs = [1 if d > 0.3 else -1 for d in v if abs(d) > 0.3]
    reversals = sum(1 for p, q in zip(signs, signs[1:]) if p != q)
    assert reversals <= 4, reversals          # up and back down: one turn (was 18)
    assert close == 0                          # fingers compress, but never overlap
    # arpeggios (thirds and wider) are not runs: their motion is untouched
    arp = [60, 64, 67, 72, 76, 79, 84, 88, 91, 96]
    b = hands.HandAnimator(song_of(notes_at(arp + arp[-2::-1], step=0.09)), RIGHT)
    assert b.runs == []


def test_hand_flattens_for_an_octave_and_spreads_like_a_real_hand():
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1600, 900))[0])
    # the span pose (thumb 72 deg out, little finger 45 deg) is the pianist's span
    geo = hands.HandGeometry()
    assert abs(geo.span_units() * hands.INCHES_PER_UNIT - hands.HAND_SPAN_IN) < 0.01
    octave = [Note(60, 1.0, 2.0, 80, 0, RIGHT, 1), Note(72, 1.0, 2.0, 80, 0, RIGHT, 5)]
    third = [Note(60, 1.0, 2.0, 80, 0, RIGHT, 1), Note(64, 1.0, 2.0, 80, 0, RIGHT, 3)]
    a = hands.HandAnimator(song_of(octave), RIGHT)
    b = hands.HandAnimator(song_of(third), RIGHT)
    for x in (a, b):
        x._ensure_layout(kb)
    assert abs(a._low(1.5) - (1 - hands.FLAT_DROP)) < 1e-6     # an octave: the hand drops and flattens
    assert b._low(1.5) == 1.0                                  # a third: its usual height
    # both keys of the octave under their fingers while held
    wx, wy, psi = a._limited_at(1.5)
    tips = a._limited_tips(1.5)
    for f, n in ((1, octave[0]), (5, octave[1])):
        kx, _ = a.key_target(n.pitch, f, n)
        tip = a._limit_tip(f, tips[f], wx, wy, psi, 0.0, a._key_weight(f, 1.5)[0], a._low(1.5))
        assert abs(tip[0] - kx) < 0.1 * kb.white_w
    # in the air the little finger keeps a comfortable spread, not its full stretch
    lo, hi = a._splay_at(5, 0.0, stretch=0.0)
    assert abs(math.degrees(hi) - hands.SPLAY_COMFORT_DEG[5][1]) < 1e-9


def test_a_short_thumb_still_rests_in_a_curve():
    import math
    import pianist
    from hands import HandGeometry, static_skeleton, NATURAL_THUMB_REACH
    for change in ({}, {"mc1": 3.6, "pp1": 2.5, "dp1": 2.1}):
        anatomy = dict(pianist.active().anatomy)
        anatomy.update(change)
        geo = HandGeometry(anatomy)
        th = static_skeleton(geo, "natural")["struct"]["chains"][1]
        assert math.dist(th[0], th[-1]) <= NATURAL_THUMB_REACH * sum(geo.bones[1]) + 1e-6, change


def test_a_tremolo_is_played_from_one_place(screen):
    # Dante Sonata's opening tremolo: Eb-A-Eb (5-3-1) over and over, the left hand
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    ps = [27, 33, 39] * 10
    notes = [Note(p, 1.0 + 0.08 * i, 1.0 + 0.08 * i + 0.07, 80, 1, LEFT) for i, p in enumerate(ps)]
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    a = hands.HandAnimator(MidiSong(notes, tracks, 5.0, [], []), LEFT)
    assert len(a.trems) == 1 and a.trems[0][0] == notes[0].start
    a._ensure_layout(kb)
    xs = [a._pose_wrist(a.pose(1.4 + 0.01 * i, kb))[0] for i in range(150)]
    assert (max(xs) - min(xs)) / a.ppi < 0.25, (max(xs) - min(xs)) / a.ppi      # the wrist holds still
    # and the figures that aren't tremolos aren't: a scale, an arpeggio, a repeated note
    for ps in (list(range(48, 72)), [48, 52, 55, 60, 64, 67, 72, 67, 64, 60, 55, 52, 48], [60] * 12):
        ns = [Note(p, 1.0 + 0.08 * i, 1.07 + 0.08 * i, 80, 0, RIGHT) for i, p in enumerate(ps)]
        b = hands.HandAnimator(MidiSong(ns, tracks, 5.0, [], []), RIGHT)
        assert b.trems == [], ps
    # a broken chord repeated over more than a hand can reach (Op. 25 No. 12) is played moving
    wide = [Note(p, 1.0 + 0.08 * i, 1.07 + 0.08 * i, 80, 0, RIGHT) for i, p in enumerate([43, 48, 64, 55] * 4)]
    assert hands.HandAnimator(MidiSong(wide, tracks, 5.0, [], []), RIGHT).trems == []
    # an octave tremolo still holds still
    octave = [Note(p, 1.0 + 0.08 * i, 1.07 + 0.08 * i, 80, 0, RIGHT) for i, p in enumerate([60, 72] * 6)]
    assert len(hands.HandAnimator(MidiSong(octave, tracks, 5.0, [], []), RIGHT).trems) == 1


def test_a_rolled_chord_too_wide_to_hold_is_let_go_in_time(screen):
    # a tenth with 3 and 5 can't be held: the lower key is let go so the hand reaches the top one
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    ns = [Note(48, 1.0, 1.3, 80, 0, RIGHT), Note(64, 1.004, 1.3, 80, 0, RIGHT), Note(60, 2.0, 2.3, 80, 0, RIGHT)]
    a = hands.HandAnimator(song_of(ns, 3.0), RIGHT, fingering={id(ns[0]): 3, id(ns[1]): 5, id(ns[2]): 1})
    assert a.rolled == 1
    heard = {id(n): (s, e) for s, e, n in a.performance}
    assert heard[id(ns[0])][1] < heard[id(ns[1])][0]                 # let go before the top is struck
    a._ensure_layout(kb)
    for n in ns[:2]:
        s, e = heard[id(n)]
        tip = a.pose(s + 0.01, kb)["struct"]["chains"][a.finger_for(n)][-1]
        r = kb.key_rects[n.pitch]
        assert r.left - 0.25 * kb.white_w <= tip[0] <= r.right + 0.25 * kb.white_w, n.pitch


def test_hands_start_uncrossed(screen):
    # Dante Sonata: the left hand starts alone, above where the right hand will come in much later
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    ns = [Note(p, t, t + 0.5, 80, 1, LEFT) for t in (1.0, 2.0, 3.0) for p in (57, 69)]
    ns += [Note(p, 6.0, 6.5, 80, 0, RIGHT) for p in (45, 48, 54)]
    s = song_of(ns, 7.0)
    an = {h: hands.HandAnimator(s, h) for h in (RIGHT, LEFT)}
    hands.pair_hands(an.values())
    for t in (0.0, 0.5, 1.0, 2.5):
        x = {h: an[h]._pose_wrist(an[h].pose(t, kb))[0] for h in an}
        assert x[RIGHT] > x[LEFT], t


def test_the_idle_pull_stops_at_the_next_notes(screen):
    # Op. 25 No. 6: the right hand rests far from the left one; drawn toward it,
    # it never passes the chord it plays next (and so never jerks back)
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    ns = [Note(p, 0.3 * i, 0.3 * i + 0.25, 70, 1, LEFT) for i in range(30) for p in (44, 51)]
    ns += [Note(p, 1.0, 1.4, 80, 0, RIGHT) for p in (91, 94)] + [Note(p, 4.0, 4.4, 80, 0, RIGHT) for p in (76, 80)]
    s = song_of(ns, 9.0)
    an = {h: hands.HandAnimator(s, h) for h in (RIGHT, LEFT)}
    hands.pair_hands(an.values())
    r = an[RIGHT]
    r._ensure_layout(kb)
    xn = r._hand_at(4.0)[0]
    xs = [r._limited_at(1.5 + 0.02 * i)[0] for i in range(125)]
    assert min(xs) > xn - 0.3 * kb.white_w, (min(xs), xn)


def test_a_hand_resting_where_it_plays_next_stays_put(screen):
    # Op. 25 No. 6 at 0:25: the right hand rests high up between two passages
    # there, while the left plays far below; it isn't drawn down and back
    from common import Keyboard, bottom_layout
    kb = Keyboard(bottom_layout((1400, 860))[0])
    ns = [Note(p, 0.3 * i, 0.3 * i + 0.25, 70, 1, LEFT) for i in range(30) for p in (44, 51)]
    ns += [Note(p, 1.0, 1.4, 80, 0, RIGHT) for p in (94, 97)] + [Note(p, 4.0, 4.4, 80, 0, RIGHT) for p in (95, 98)]
    s = song_of(ns, 9.0)
    an = {h: hands.HandAnimator(s, h) for h in (RIGHT, LEFT)}
    hands.pair_hands(an.values())
    r = an[RIGHT]
    r._ensure_layout(kb)
    xs = [r._limited_at(1.5 + 0.02 * i)[0] for i in range(125)]
    assert min(xs) > r._limited_at(1.45)[0] - 0.3 * kb.white_w, (min(xs), max(xs))   # never drawn down
