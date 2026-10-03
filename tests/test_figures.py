"""Scales and arpeggios in every key against the textbook (ABRSM / Hanon) fingerings."""
import random

import fingering as F
import figures as G
from midi_loader import LEFT, RIGHT, Note

MAJOR = [0, 2, 4, 5, 7, 9, 11]
HARM = [0, 2, 3, 5, 7, 8, 11]
# one octave up from the tonic; written out here, not taken from figures.py
RH = {
    ('C', 'maj'): "12312345", ('G', 'maj'): "12312345", ('D', 'maj'): "12312345", ('A', 'maj'): "12312345",
    ('E', 'maj'): "12312345", ('B', 'maj'): "12312345", ('F', 'maj'): "12341234", ('Bb', 'maj'): "41231234",
    ('Eb', 'maj'): "31234123", ('Ab', 'maj'): "34123123", ('Db', 'maj'): "23123412", ('F#', 'maj'): "23412312",
    ('A', 'min'): "12312345", ('E', 'min'): "12312345", ('B', 'min'): "12312345", ('D', 'min'): "12312345",
    ('G', 'min'): "12312345", ('C', 'min'): "12312345", ('F', 'min'): "12341234", ('Bb', 'min'): "21231234",
    ('Eb', 'min'): "31234123", ('G#', 'min'): "34123123", ('C#', 'min'): "34123123", ('F#', 'min'): "34123123",
}
LH = {
    ('C', 'maj'): "54321321", ('G', 'maj'): "54321321", ('D', 'maj'): "54321321", ('A', 'maj'): "54321321",
    ('E', 'maj'): "54321321", ('B', 'maj'): "43214321", ('F', 'maj'): "54321321", ('Bb', 'maj'): "32143213",
    ('Eb', 'maj'): "32143213", ('Ab', 'maj'): "32143213", ('Db', 'maj'): "32143213", ('F#', 'maj'): "43213214",
    ('A', 'min'): "54321321", ('E', 'min'): "54321321", ('B', 'min'): "43214321", ('D', 'min'): "54321321",
    ('G', 'min'): "54321321", ('C', 'min'): "54321321", ('F', 'min'): "54321321", ('Bb', 'min'): "21321432",
    ('Eb', 'min'): "21432132", ('G#', 'min'): "32132143", ('C#', 'min'): "32143213", ('F#', 'min'): "43213214",
}


def _scale(key, hand, octaves, dt, jitter=0.0, rng=None):
    t = G.PC[key[0]]
    iv = MAJOR if key[1] == 'maj' else HARM
    base = 12 * ((5 if hand == RIGHT else 3) + 0) + t
    ps = [base + 12 * o + x for o in range(octaves) for x in iv] + [base + 12 * octaves]
    ps = ps + ps[-2::-1]
    notes, now = [], 1.0
    for p in ps:
        j = rng.uniform(-jitter, jitter) if rng else 0.0
        notes.append(Note(p, now + j, now + j + dt * 1.1, 80, 0, hand))
        now += dt
    return notes


def _textbook(key, hand, p, top, bottom, pattern=None):
    t = G.PC[key[0]]
    iv = MAJOR if key[1] == 'maj' else HARM
    deg = iv.index((p - t) % 12)
    pat = pattern or (RH if hand == RIGHT else LH)[key]
    if deg:
        return int(pat[deg])
    if p == bottom:
        return int(pat[0])
    if hand == RIGHT and pat[0] == '1' and p != top:
        return 1                    # a tonic under the thumb: 5 (or 4) only on top
    return int(pat[7])


def _plan(notes, hand):
    vp = F.mirror_pitch if hand == LEFT else None
    g = F.group_notes(notes, vpitch=vp)
    return G.detect(g, hand, notes, vp), F.plan_fingering(g, vp, hand=hand, context=notes)


def _agreement(cases):
    hit = sug_hit = total = 0
    for key, hand, notes in cases:
        sug, plan = _plan(notes, hand)
        top, bottom = max(n.pitch for n in notes), min(n.pitch for n in notes)
        order = sorted(notes, key=lambda n: n.start)
        ends = order[:3] + order[-2:]
        for n in notes:
            if n in ends or n.pitch in (top, bottom):
                continue                       # where a run starts (2-3-4 or the table), turns and ends, books differ
            want = _textbook(key, hand, n.pitch, top, bottom)
            if key == ('G#', 'min') and hand == LEFT and plan[id(n)] != want:
                want = _textbook(key, hand, n.pitch, top, bottom, "32143213")   # thumb on F## (G): also printed
            total += 1
            hit += plan[id(n)] == want
            sug_hit += bool(sug.get(id(n))) and sug[id(n)][0] == want
    return sug_hit / total, hit / total


def test_scales_in_every_key_get_the_textbook_fingering():
    cases = [(key, hand, _scale(key, hand, 2, 0.09)) for key in RH for hand in (RIGHT, LEFT)]
    sug, plan = _agreement(cases)
    assert sug > 0.99 and plan > 0.98, (sug, plan)


def test_fast_uneven_scales_stay_scales():
    # 24 notes a second, played unevenly: neighbours can start closer than CHORD_TOL
    rng = random.Random(7)
    keys = [('C', 'maj'), ('E', 'maj'), ('Bb', 'maj'), ('Db', 'maj'), ('A', 'min'), ('Eb', 'min')]
    cases = [(key, hand, _scale(key, hand, 2, 0.042, 0.014, rng)) for key in keys for hand in (RIGHT, LEFT)]
    _, plan = _agreement(cases)
    assert plan > 0.97, plan


def test_four_octave_left_hand_e_major_keeps_its_cycle():
    # the end of Chopin's Concerto No. 1: the LH used to slip into 3-2-1 crossings
    notes = _scale(('E', 'maj'), LEFT, 4, 0.065)[:29]
    _, plan = _plan(notes, LEFT)
    by_pitch = {n.pitch % 12: plan[id(n)] for n in notes[8:]}
    assert by_pitch[4] == 1 and by_pitch[11] == 1          # thumbs on E and B
    assert by_pitch[6] == 4                                # 4 over the thumb onto F#


def test_harmonic_minor_augmented_second_is_a_step():
    assert G._is_step([62, 63, 66, 67], 1)                 # D Eb F# G: F# minor's aug. 2nd
    assert not G._is_step([60, 63, 66, 69, 72], 1)         # a diminished seventh arpeggio


def test_root_position_arpeggios_follow_the_book():
    hits = total = 0
    for key, (rh, rs, lh, ls) in G.ARP.items():
        third = 4 if key[1] == 'maj' else 3
        for hand in (RIGHT, LEFT):
            fmap = G._fmap(rh if hand == RIGHT else lh)
            lo = 12 * (5 if hand == RIGHT else 3) + G.PC[key[0]]
            ps = [lo + 12 * o + x for o in range(2) for x in (0, third, 7)] + [lo + 24]
            ps = ps + ps[-2::-1]
            notes = [Note(p, 1 + 0.09 * i, 1.09 + 0.09 * i, 80, 0, hand) for i, p in enumerate(ps)]
            _, plan = _plan(notes, hand)
            for n in notes[1:-1]:
                if n.pitch in (min(ps), max(ps)):
                    continue
                total += 1
                hits += plan[id(n)] == fmap[n.pitch % 12]
    assert hits / total > 0.9, hits / total


def test_a_fast_step_is_not_a_chord():
    a = Note(60, 1.0, 1.05, 80, 0, RIGHT)
    b = Note(62, 1.02, 1.07, 80, 0, RIGHT)          # 20 ms later: the next note of a run
    c = Note(64, 1.02, 1.07, 80, 0, RIGHT)          # a third struck with it: a chord
    assert len(F.group_notes([a, b])) == 2
    assert len(F.group_notes([a, c])) == 1


def test_trills_are_found_among_both_hands_notes():
    from figures import find_trills
    from midi_loader import Note, RIGHT
    trill = [Note(60 if i % 2 == 0 else 62, 0.1 * i, 0.1 * i + 0.09, 80, 0, RIGHT) for i in range(10)]
    others = [Note(p, 0.05 + 0.15 * i, 0.05 + 0.15 * i + 0.1, 70, 0, RIGHT) for i, p in enumerate([48, 55, 52, 67, 72, 64])]
    found = find_trills(trill + others)
    assert len(found) == 1 and [id(n) for n in found[0]] == [id(n) for n in trill]
    # seconds struck together, again and again, aren't a trill; nor is a short shake
    seconds = [Note(p, 0.15 * i, 0.15 * i + 0.1, 80, 0, RIGHT) for i in range(6) for p in (60, 62)]
    assert find_trills(seconds) == []
    assert find_trills(trill[:5]) == []


def test_a_trill_is_played_by_one_hand():
    # a trill on C4-D4 right between the hands (LH up to A3, RH down to E4): the split used
    # to hand some of its notes to the left hand; now the other hand may not pitch in
    import hand_split
    from figures import find_trills
    from midi_loader import Note, RIGHT
    ns = [Note(60 if i % 2 == 0 else 62, 0.5 + 0.08 * i, 0.5 + 0.08 * i + 0.07, 80, 0, RIGHT) for i in range(14)]
    for i in range(10):
        t = 0.5 + 0.15 * i
        ns.append(Note([45, 52, 57, 52][i % 4], t + 0.02, t + 0.14, 70, 0, RIGHT))
        ns.append(Note([64, 67, 72, 67][i % 4], t + 0.07, t + 0.14, 70, 0, RIGHT))
    (trill,) = find_trills(ns)
    r = hand_split.split_hands(ns)
    assert len({r[id(n)] for n in trill}) == 1


def test_chord_tones_are_not_trills_but_double_notes_are():
    from figures import find_trills
    from midi_loader import Note, RIGHT
    # chords alternating quickly (Ravel, Scarbo, 0:35) share neighbouring keys - D#6/E6, G6/G#6
    a, b = (68, 75, 79), (74, 76, 80, 86, 88)
    chords = [Note(p, 0.07 * i, 0.07 * i + 0.06, 80, 0, RIGHT) for i in range(20) for p in (a if i % 2 == 0 else b)]
    assert find_trills(chords) == []
    # a trill in thirds (E-G / F-A): each voice is a trill
    thirds = [Note(p, 0.1 * i, 0.1 * i + 0.09, 80, 0, RIGHT) for i in range(8) for p in ((64, 67) if i % 2 == 0 else (65, 69))]
    assert len(find_trills(thirds)) == 2


def test_a_track_alternating_chords_wider_than_a_hand_goes_to_both_hands():
    # the file puts Scarbo's alternating chords (G#5-D#6-G6 / D6-E6-G#6-D7-E7, 70 ms apart, 20
    # semitones together) all in the right hand's track: the split may give some to the left
    import hand_split
    from midi_loader import Note, RIGHT
    a, b = (68, 75, 79), (74, 76, 80, 86, 88)
    ns = [Note(p, 0.07 * i, 0.07 * i + 0.06, 80, 0, RIGHT) for i in range(40) for p in (a if i % 2 == 0 else b)]
    r = hand_split.split_hands(ns, prefer={id(n): RIGHT for n in ns})
    assert sum(1 for n in ns if r[id(n)] == LEFT) > len(ns) // 5
    # a single wide leap within the track's reach of time stays with the track
    leap = [Note(48, 0.0, 0.1, 80, 0, RIGHT), Note(72, 0.12, 0.2, 80, 0, RIGHT), Note(74, 0.6, 0.7, 80, 0, RIGHT)]
    r = hand_split.split_hands(leap, prefer={id(n): RIGHT for n in leap})
    assert all(r[id(n)] == RIGHT for n in leap)
