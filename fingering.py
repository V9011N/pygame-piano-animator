"""
fingering.py - Piano fingering for any MIDI input.

Three layers:

1. Context (figures.py): the passage is read the way a pianist reads it -
   scales in their key, chromatic runs, arpeggios, repeated notes, trills,
   octaves, scales in thirds, chromatic thirds, sixths - and each recognised
   figure gets its standard fingering (Hanon's) as a strong suggestion.

2. A pianist's cost model for everything else (per pair of consecutive
   notes, and per pair of adjacent notes in a chord), in the right-hand frame -
   the left hand is planned as a right hand on a keyboard mirrored about D4,
   which maps white keys to white and black to black:
     * stretching or cramping fingers away from one-white-key-per-finger, and
       neighbouring long fingers (2-3, 3-4, 4-5) held a third apart;
     * crossings: the thumb passing under 2/3/4 going up, or 2/3/4 passing
       over the thumb going down, are normal (cheapest with 3, dearer the
       wider); any other crossing is awkward;
     * the same finger on two different keys in a row, and the same fingers
       on a small chord shape moving by step (4-2, 4-2, 4-2 can't be legato);
     * the thumb (and to a lesser degree the little finger) on black keys;
     * a light bias against the weak 4th and 5th fingers when the strong
       ones would do as well;
     * fast repeated notes are easier with changing fingers.

3. Physical limits (the things that made fingerings "impossible"), checked
   against the actual timing of the piece:
     * a chord, plus any keys the hand is still holding, must fit the hand:
       fingers in pitch order, every pair within its maximum stretch;
     * a finger holding a key can't play another one without letting go first;
     * the same finger can't move to a new key when the notes are close in time;
     * the whole hand can only shift so far between two quick notes, unless
       the shift happens through a thumb crossing; beyond reach it leaps, and
       a leap costs the same whichever fingers take off and land.

Measured against the Hanon book fingering (all 60 exercises, both hands, the
fingering in the files ignored), about 22% of notes differ, down from ~30%
before the figure layer; the scale, chromatic, arpeggio, octave and
scale-in-thirds exercises now match within 0-10%. Most of what remains is the
book deliberately training the weak fingers (4-5 trills, 3-4-3-2 patterns),
which this planner avoids on purpose.

A beam search over the notes (grouped into chords) finds the cheapest
fingering. It keeps each candidate's full history, so keys held from several
chords back are respected. Fingers already given in the file are kept.
"""
from __future__ import annotations

from itertools import combinations, product

MIRROR_SUM = 2 * 62            # reflect about D4: 124 - p

_BLACK = {1, 3, 6, 8, 10}
_WHITE_IDX = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}
_BLACK_POS = {1: 1 - 0.072, 3: 2 + 0.072, 6: 4 - 0.084, 8: 5.0, 10: 6 + 0.084}
_OFF = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}          # natural spacing: one white key per finger
# Largest reach between two fingers of the same hand, in white keys (a hand
# that spans a 9th from thumb to little finger)
MAX_SPAN = {(1, 2): 5.5, (1, 3): 6.5, (1, 4): 7.2, (1, 5): 8.3, (2, 3): 2.7,
            (2, 4): 4.6, (2, 5): 6.0, (3, 4): 2.4, (3, 5): 4.3, (4, 5): 2.6}

CHORD_TOL = 0.03               # onsets closer than this form one chord
BEAM = 32
IMPOSSIBLE = 60.0

# Tunable weights (calibrated on Hanon; see module docstring)
W = {
    # consecutive notes
    "stretch": 0.35,           # per white key wider than the fingers' natural spacing
    "cramp": 0.5,              # per white key narrower
    "leap": 4.0,               # beyond the pair's maximum stretch: the hand leaps ...
    "leap_dist": 0.15,         # ... plus this per white key
    "cross": 3.2,              # thumb under / finger over the thumb
    "cross_2": 0.6,            # ... extra when the other finger is 2
    "cross_4": 0.8,            # ... or 4 (3 is the natural one)
    "cross_wide": 0.8,         # ... per white key beyond 2
    "cross_max": 4.0,          # ... and no crossing wider than this (it's a leap)
    "cross_thumb_black": 2.4,  # ... thumb landing on a black key
    "awkward": 9.6,            # any other crossing (+1 per key)
    "same_finger": 3.0,        # same finger, new key (+ same_finger_dist per key)
    "same_finger_dist": 0.4,
    "same_key_new_finger": 0.8,
    "repeat_fast": 0.6,        # same finger re-striking a key quickly
    "repeat_t": 0.2,           # below this, re-striking with the same finger gets hard...
    "repeat_very_fast": 8.0,   # ... up to this much extra, and changing finger costs less
    # individual fingers
    "thumb_black": 1.92,
    "pinky_black": 0.96,
    "finger_4": 0.12,          # the weak fingers: when two fingerings are otherwise
    "finger_5": 0.05,          # equal, take the stronger fingers (trills: figures.py)
    # chords
    "chord_stretch": 0.4,
    "chord_cramp": 0.4,
    "chord_stretch_adj": 1.2,
    "figure": 1.0,             # times the figure's weight, for leaving its standard fingering
    "thumb_double": 7.0,       # the thumb covering two neighbouring keys in a chord
    "pinky_double": 8.0,       # the little finger covering two neighbouring white keys
    "same_shape": 1.5,         # the same fingers on a chord shape that moves by step (4-2, 4-2, 4-2)
    "shape_shift": 0.5,        # moving a whole chord shape with the same fingers
    "octave_4_white": 1.5,     # an octave's top note with 4 on a white key
    # physical, against time
    "same_finger_t": 0.35,     # a finger needs about this long to reach a new key
    "same_finger_fast": 6.0,
    "shift_base": 1.0,         # white keys the hand may shift between two notes...
    "shift_cross": 3.5,        # ... or through a thumb crossing ...
    "shift_speed": 8.0,        # ... plus this many per second between them
    "shift_cost": 1.2,         # per white key beyond that
    "steal": 1.5,              # letting a held key go early to reuse its finger
    "held_tol": 0.06,          # a key released this soon after an onset isn't held
    "velocity": 0.05,          # how fast the fingers must travel from a relaxed hand (below)
}

# Economy of motion (after pianoplayer's cost): after each chord the other
# fingers are assumed to sit in a relaxed spread around the one that played
# (thumb well out to the side), and moving a finger far in little time costs,
# less for the strong fingers, and less for the long fingers on black keys.
# Offsets are in white keys from the middle finger, for the default hand;
# apply_pianist scales them to the pianist's hand.
BASE_RELAXED = {1: -2.97, 2: -1.19, 3: 0.0, 4: 1.19, 5: 2.38}
RELAXED = dict(BASE_RELAXED)
FINGER_STRENGTH = {1: 1.1, 2: 1.0, 3: 1.1, 4: 0.9, 5: 0.8}
BLACK_EASE = {1: 0.3, 2: 1.0, 3: 1.1, 4: 0.8, 5: 0.7}


# Two sets of weights for the cost model:
#   W_TEXTBOOK - hand-calibrated on Hanon's printed fingering (the values above);
#   BASE_W     - learned from how professional pianists finger real repertoire
#                (the PIG dataset, pieces 031-150; learn_weights.py). The default.
# A pianist picks one ("Fingering model" behaviour); their other preferences
# then scale it. The figure suggestions are the same in both.
W_TEXTBOOK = dict(W)
LEARNED_W = {                    # learn_weights.py on PIG pieces 031-150 (textbook value in comments)
    "cramp": 0.125,                # 0.5
    "leap_dist": 0.3,              # 0.15
    "cross": 6.4,                  # 3.2
    "cross_2": 0.3,                # 0.6
    "cross_4": 3.2,                # 0.8
    "cross_wide": 1.6,             # 0.8
    "same_finger": 6.0,            # 3.0
    "same_finger_dist": 0.2,       # 0.4
    "same_key_new_finger": 4.8,    # 0.8
    "repeat_fast": 0.3,            # 0.6
    "thumb_black": 0.24,           # 1.92
    "pinky_black": 1.152,          # 0.96
    "finger_4": 0.24,              # 0.12
    "chord_stretch": 1.6,          # 0.4
    "chord_stretch_adj": 5.76,     # 1.2
    "octave_4_white": 3.0,         # 1.5
    "shift_base": 2.0,             # 1.0
    "shift_speed": 2.0,            # 8.0
    "shift_cost": 2.4,             # 1.2
    "steal": 12.0,                 # 1.5
    "velocity": 0.1,               # 0.05
}
BASE_W = dict(W)
BASE_W.update(LEARNED_W)
W.update(BASE_W)
BASE_MAX_SPAN = dict(MAX_SPAN)
_applied = None


def apply_pianist(p):
    """
    Set the weights and reach limits for a pianist (None: the calibrated
    defaults): reach from their anatomy, the weak-finger, stretch and
    black-key preferences, and their figure fingerings (figures.PREFS).
    """
    global _applied
    key = None if p is None else (tuple(sorted(p.anatomy.items())), tuple(sorted(p.behavior.items())))
    if key == _applied:
        return
    _applied = key
    import figures
    base = W_TEXTBOOK if p is not None and p.b("fingering_model") == "textbook" else BASE_W
    W.clear()
    W.update(base)
    MAX_SPAN.update(BASE_MAX_SPAN)
    figures.PREFS.update(figures.DEFAULT_PREFS)
    RELAXED.update(BASE_RELAXED)
    if p is None:
        return
    from hands import reach_scale, hand_span_inches
    k = hand_span_inches(p.anatomy) / hand_span_inches(None)
    for f, v in BASE_RELAXED.items():
        RELAXED[f] = v * k
    stretch = p.b("stretch_bias")                 # -1 shift/cross .. +1 stretch
    scale = reach_scale(p.anatomy)
    for pair, v in BASE_MAX_SPAN.items():
        # the anatomical reach; a pianist who prefers shifting uses a little less of it
        MAX_SPAN[pair] = v * scale[pair] * (1.0 + 0.06 * min(0.0, stretch))
    k = p.b("weak_bias") / 0.25                   # 1 at the default
    W["finger_4"] = base["finger_4"] * k
    W["finger_5"] = base["finger_5"] * k
    for name in ("stretch", "chord_stretch", "chord_stretch_adj"):
        W[name] = base[name] * (1.0 - 0.6 * stretch)
    for name in ("cross", "leap", "shift_cost"):
        W[name] = base[name] * (1.0 + 0.4 * stretch)
    W["velocity"] = base["velocity"] * p.b("economy") / 0.5
    b = p.b("black_avoid") / 0.5
    W["thumb_black"] = base["thumb_black"] * b
    W["pinky_black"] = base["pinky_black"] * b
    W["cross_thumb_black"] = base["cross_thumb_black"] * b
    figures.PREFS.update(chromatic=p.b("chromatic"), repeated=p.b("repeated"),
                         trill=p.b("trill"), octaves=p.b("octaves"))


def is_black(pitch):
    return pitch % 12 in _BLACK


def key_pos(pitch):
    """Horizontal position of a key's centre, in white-key widths."""
    octave, pc = divmod(pitch, 12)
    return octave * 7 + (_WHITE_IDX[pc] + 0.5 if pc in _WHITE_IDX else _BLACK_POS[pc])


def mirror_pitch(pitch):
    return MIRROR_SUM - pitch


# --------------------------------------------------------------------------- #
# Pianist's cost model (right-hand frame)
# --------------------------------------------------------------------------- #
def is_crossing(p1, f1, p2, f2):
    """Thumb passing under (going up) or a finger passing over the thumb (going down)."""
    d = key_pos(p2) - key_pos(p1)
    return (d > 0 and f2 == 1 and f1 in (2, 3, 4)) or (d < 0 and f1 == 1 and f2 in (2, 3, 4))


def thumb_pair(a, b):
    """
    Can one thumb cover both keys? Two neighbouring white keys (C-D, E-F,
    B-C...) or two neighbouring black keys (C#-D#, F#-G#, G#-A#, and the wider
    D#-F# and A#-C#). The keyboard is symmetric about D, so this holds in the
    left hand's mirrored frame too.
    """
    a, b = min(a, b), max(a, b)
    if a == b:
        return False
    if is_black(a) != is_black(b):
        return False
    if not is_black(a):
        return b - a <= 2 and abs(key_pos(b) - key_pos(a) - 1.0) < 1e-6
    return b - a in (2, 3)


def pinky_pair(a, b):
    """Can the little finger cover both keys? Only two neighbouring white keys."""
    a, b = min(a, b), max(a, b)
    return (a != b and not is_black(a) and not is_black(b) and b - a <= 2
            and abs(key_pos(b) - key_pos(a) - 1.0) < 1e-6)


def double_ok(p1, p2, f):
    """One finger on both keys: the thumb (thumb_pair) or the little finger (pinky_pair)."""
    return (f == 1 and thumb_pair(p1, p2)) or (f == 5 and pinky_pair(p1, p2))


def unary_cost(pitch, f):
    c = W["finger_4"] if f == 4 else W["finger_5"] if f == 5 else 0.0
    if is_black(pitch):
        if f == 1:
            c += W["thumb_black"]
        elif f == 5:
            c += W["pinky_black"]
    return c


def pair_cost(p1, f1, p2, f2):
    """Cost of playing p2 with f2 right after p1 with f1."""
    d = key_pos(p2) - key_pos(p1)
    if f1 == f2:
        return 0.0 if p1 == p2 else W["same_finger"] + W["same_finger_dist"] * abs(d)
    if abs(d) < 1e-9:
        return W["same_key_new_finger"]
    if is_crossing(p1, f1, p2, f2):
        if abs(d) > W["cross_max"]:
            return leap_cost(d)                      # too wide to pass under/over: a leap
        other = f1 if f2 == 1 else f2
        c = W["cross"] + {2: W["cross_2"], 3: 0.0, 4: W["cross_4"]}[other]
        c += W["cross_wide"] * max(0.0, abs(d) - 2.0)
        if f2 == 1 and is_black(p2):
            c += W["cross_thumb_black"]
        return c
    if (d > 0) == (f2 > f1):
        rel, nat = abs(d), abs(_OFF[f2] - _OFF[f1])
        c = W["stretch"] * max(0.0, rel - nat) + W["cramp"] * max(0.0, nat - rel)
        limit = MAX_SPAN[(min(f1, f2), max(f1, f2))]
        if rel > limit:
            # Out of reach from where the hand is: the hand has to leap, and
            # a leap costs the same whichever fingers take off and land - the
            # notes after it decide those.
            return leap_cost(d)
        return c
    return W["awkward"] + abs(d)


def leap_cost(d):
    return W["leap"] + W["leap_dist"] * abs(d)


def chord_pair_cost(pl, fl, ph, fh):
    """Cost of two adjacent chord notes pl < ph played by fingers fl < fh."""
    rel, nat = key_pos(ph) - key_pos(pl), _OFF[fh] - _OFF[fl]
    if fl >= fh:
        if fl == fh and double_ok(pl, ph, fl):
            return W["thumb_double"] if fl == 1 else W["pinky_double"]
        # only when a finger was given (file / editor): one finger on two keys,
        # or a crossing inside the chord - allowed, but free notes avoid it
        if fl == fh:
            return IMPOSSIBLE
        return W["cross"] * 2 + W["chord_stretch"] * abs(rel) + \
            (IMPOSSIBLE if rel > MAX_SPAN.get((fh, fl), 12) else 0.0)
    c = W["chord_stretch"] * max(0.0, rel - nat) + W["chord_cramp"] * max(0.0, nat - rel)
    if fh - fl == 1 and fl != 1:
        # neighbouring long fingers (2-3, 3-4, 4-5) held apart: thirds want 1-3 / 2-4 / 3-5
        c += W["chord_stretch_adj"] * max(0.0, rel - 1.2)
    if rel > MAX_SPAN[(fl, fh)]:
        c += IMPOSSIBLE
    if fl == 1 and fh == 4 and rel >= 6.0 and not is_black(ph):
        c += W["octave_4_white"]                 # octaves: 1-5, with 4 only on black keys
    return c


def shape_cost(pairs):
    """
    Cost of a hand shape: [(pitch, finger)] for every key down at once.
    Fingers must rise with pitch (the thumb may tuck under a held finger at a
    price) and every adjacent pair must be within reach.
    """
    pairs = sorted(pairs)
    c = 0.0
    for (p1, f1), (p2, f2) in zip(pairs, pairs[1:]):
        if f1 == f2:
            if double_ok(p1, p2, f1):
                continue
            return IMPOSSIBLE
        if f2 < f1:
            if 1 in (f1, f2) and key_pos(p2) - key_pos(p1) <= 2.5:
                c += 3.0
                continue
            return IMPOSSIBLE
        if key_pos(p2) - key_pos(p1) > MAX_SPAN[(f1, f2)]:
            return IMPOSSIBLE
    return c


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def group_notes(notes, vpitch=None, tolerance=CHORD_TOL):
    """Chords: [(start, [notes])], each ordered by vpitch (finger numbers rise along it)."""
    vp = vpitch or (lambda p: p)
    groups = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if groups and n.start - groups[-1][0] <= tolerance:
            groups[-1][1].append(n)
        else:
            groups.append((n.start, [n]))
    out = []
    for start, ns in groups:
        ns = sorted(ns, key=lambda n: vp(n.pitch))
        # one hand, five fingers (a key more each if the thumb / little finger
        # can take two): drop inner notes
        def room(ns):
            if len(ns) < 2:
                return 5
            return (5 + thumb_pair(vp(ns[0].pitch), vp(ns[1].pitch))
                    + pinky_pair(vp(ns[-2].pitch), vp(ns[-1].pitch)))
        while len(ns) > room(ns):
            ns.pop(len(ns) // 2)
        out.append((start, ns))
    return out


def _transition(pps, pf, ps, st, dt):
    """Cost of moving from the previous chord (pps, fingers pf) to (ps, st) dt later."""
    if len(ps) > 1 and len(pps) == len(ps):
        moves = [b - a for a, b in zip(pps, ps)]
        if max(moves) == min(moves) and moves[0] != 0:
            # The same shape moved bodily (parallel octaves, sixths, chords):
            # only the hand's travel counts, plus a little for each finger
            # that changes (1-5 to 1-4 on a black key, say). The very same
            # fingers on a small shape (thirds, fourths) moving by step can't
            # be legato, so that costs extra: 4-2, 4-2, 4-2 is a poor habit.
            d = abs(key_pos(ps[0]) - key_pos(pps[0]))
            over = d - (W["shift_base"] + W["shift_speed"] * dt)
            c = W["shape_shift"] + 0.1 * d + (W["shift_cost"] * over if over > 0 else 0.0)
            c += 0.3 * sum(1 for a, b in zip(pf, st) if a != b)
            if st == pf and ps[-1] - ps[0] < 7 and abs(moves[0]) <= 2:
                c += W["same_shape"]
            return c
    c = 0.0
    cross = False
    for p2, f2 in zip(ps, st):
        j = min(range(len(pps)), key=lambda j: abs(pps[j] - p2))
        p1, f1 = pps[j], pf[j]
        c += pair_cost(p1, f1, p2, f2)
        if f1 == f2:
            if p1 != p2 and dt < W["same_finger_t"]:
                c += W["same_finger_fast"] * (1.0 - dt / W["same_finger_t"]) * (1 + 0.2 * abs(p2 - p1))
            elif p1 == p2 and dt < 0.2:
                c += W["repeat_fast"]
                if dt < W["repeat_t"]:
                    # a key must rise before it can be struck again: at this speed a
                    # pianist changes fingers (the key only half rises under one finger)
                    c += W["repeat_very_fast"] * (1.0 - dt / W["repeat_t"])
        elif p1 == p2 and dt < W["repeat_t"]:
            c -= W["same_key_new_finger"] * (1.0 - dt / W["repeat_t"])
        if not cross:
            cross = any(is_crossing(q, g, p2, f2) for q, g in zip(pps, pf))
    c = c / len(ps) * (1.0 if len(ps) == 1 else 1.3)
    if W["velocity"]:
        # where each finger is, if the hand relaxed around the last chord's first note
        a1 = key_pos(pps[0]) - RELAXED[pf[0]]
        v = 0.0
        for p2, f2 in zip(ps, st):
            sp = abs(key_pos(p2) - (a1 + RELAXED[f2])) / (dt + 0.1) / FINGER_STRENGTH[f2]
            if is_black(p2):
                sp /= BLACK_EASE[f2]
            v += sp
        c += W["velocity"] * v / len(ps)
    # How far the whole hand shifts. Legato shifts go through a crossing;
    # anything else is a jump, and jumps take time.
    a1 = sum(key_pos(p) - _OFF[f] for p, f in zip(pps, pf)) / len(pps)
    a2 = sum(key_pos(p) - _OFF[f] for p, f in zip(ps, st)) / len(ps)
    over = abs(a2 - a1) - ((W["shift_cross"] if cross else W["shift_base"]) + W["shift_speed"] * dt)
    if over > 0:
        c += W["shift_cost"] * over
    return c


def _states(given, ps=None):
    """
    Candidate finger tuples for a chord whose notes may already have a finger
    (from the file or the editor). Normally one finger per note, rising with
    pitch; when the two lowest keys (in the hand's frame) are neighbours the
    thumb can cover (thumb_pair), also the thumb on both and the other
    fingers on the rest, and likewise the little finger on the two highest
    (neighbouring white keys, pinky_pair), or both. That frees a finger, so
    chords that would otherwise need a stretch between neighbouring fingers
    (or a roll) fit, and a sixth / seventh key can be played. A given finger is always kept, even when
    it breaks that pattern (a thumb on two keys, a crossing inside the
    chord): the free notes then take whatever fingers are left, in order
    where possible.
    """
    k = len(given)
    states = list(combinations(range(1, 6), k)) if k <= 5 else []
    if ps is not None and k >= 3:
        tp, pp = thumb_pair(ps[0], ps[1]), pinky_pair(ps[-2], ps[-1])
        if tp:
            states += [(1, 1) + c for c in combinations(range(2, 6), k - 2)]
        if pp:
            states += [c + (5, 5) for c in combinations(range(1, 5), k - 2)]
        if tp and pp and k >= 5:
            states += [(1, 1) + c + (5, 5) for c in combinations(range(2, 5), k - 4)]
    if not states:
        states = [tuple(range(1, 6))[:k]]
    if not any(given):
        return states
    fixed = [st for st in states if all(g is None or g == f for g, f in zip(given, st))]
    if fixed:
        return fixed
    free = [i for i, g in enumerate(given) if g is None]
    left = [f for f in range(1, 6) if f not in given]
    pools = list(combinations(left, len(free))) if len(left) >= len(free) else \
        list(product(range(1, 6), repeat=len(free)))
    out = []
    for pool in pools:
        st = list(given)
        for i, f in zip(free, pool):
            st[i] = f
        out.append(tuple(st))
    return out or [tuple(g or 1 for g in given)]


def plan_fingering(groups, vpitch=None, beam=BEAM, hand=None, context=None, figures=True,
                   pianist=None, fixed=None, costs=None, repair=False, repaired=None):
    """
    Fingering for chord groups (from group_notes). Returns {id(note): finger}.
    Notes that already carry a finger (from the file) keep it. `vpitch` maps
    pitches into the right-hand frame (mirror_pitch for the left hand).
    `hand` ('R'/'L', default from vpitch) and `context` (all the song's notes,
    for key finding) feed the figure recognition in figures.py, whose standard
    fingerings become strong suggestions.

    `fixed` ({id(note): finger}) pins fingers without touching the notes;
    `costs`, a dict, is filled with {id(note): cost of its chord on the
    chosen path} - how hard that moment is (see score_fingering).

    With `repair`, a chord whose given fingers can't be played together (a
    pair further apart than those fingers reach, say an octave with 4-5)
    keeps as many of them as still leave a playable chord and plans the
    rest; the notes whose finger changed go into the set `repaired`.
    """
    if not groups:
        return {}
    if pianist is None:
        import pianist as pianists
        pianist = pianists.active()
    apply_pianist(pianist)
    vp = vpitch or (lambda p: p)
    held_tol, steal = W["held_tol"], W["steal"]
    if hand is None:
        hand = "L" if vpitch is not None and vpitch(60) != 60 else "R"
    suggest = {}
    if figures:
        from figures import detect
        suggest = detect(groups, hand, context, vp)

    # beam entries: (cost, fingers of the last chord, held keys, back pointer, state)
    #   held keys: ((end_time, vpitch, finger), ...)
    beam_ = [(0.0, None, (), None, None)]
    history = []
    prev_ps = prev_start = None
    for start, ns in groups:
        ps = [vp(n.pitch) for n in ns]
        given = [fixed.get(id(n)) if fixed is not None else getattr(n, "finger", None) for n in ns]
        sug = [suggest.get(id(n)) for n in ns]

        def chord_costs(states):
            local = []
            for st in states:
                c = sum(unary_cost(p, f) for p, f in zip(ps, st))
                for s_, f in zip(sug, st):
                    if s_ and (f not in s_[0] if isinstance(s_[0], frozenset) else s_[0] != f):
                        c += W["figure"] * s_[1]
                for (pl, fl), (ph, fh) in zip(zip(ps, st), list(zip(ps, st))[1:]):
                    c += chord_pair_cost(pl, fl, ph, fh)
                local.append(c)
            return local
        states = _states(given, ps)
        local = chord_costs(states)
        if repair and len(ns) > 1 and any(given) and min(local) >= IMPOSSIBLE:
            # the given fingers can't play this chord: keep as many as still fit
            idx = [i for i, g in enumerate(given) if g]
            for drop in range(1, len(idx) + 1):
                best = None
                for out in combinations(idx, drop):
                    g2 = [None if i in out else g for i, g in enumerate(given)]
                    st2 = _states(g2, ps)
                    loc2 = chord_costs(st2)
                    if min(loc2) < IMPOSSIBLE and (best is None or min(loc2) < best[0]):
                        best = (min(loc2), st2, loc2)
                if best:
                    states, local = best[1], best[2]
                    break

        dt = start - prev_start if prev_start is not None else 1.0
        cand = []
        for bi, (cost, pf, held, _, _) in enumerate(beam_):
            held_now = tuple(h for h in held if h[0] > start + held_tol)
            held_map = {}
            for h in held_now:
                held_map.setdefault(h[2], set()).add(h[1])
            for si, st in enumerate(states):
                c = cost + local[si]
                keep = True
                if held_now:
                    # A finger still holding a key is busy. Performance MIDI
                    # often keeps keys down longer than the hand must (or
                    # bakes the pedal into the note lengths), so taking a busy
                    # finger just means letting that key go early - at a
                    # price. Keys still held must fit with the new ones, or
                    # they're let go too.
                    c += steal * sum(1 for f, p in zip(st, ps) if f in held_map and p not in held_map[f])
                    rest = [(h[1], h[2]) for h in held_now if h[2] not in st]
                    if rest:
                        # what the held keys add to the new chord's own shape
                        # (the chord itself is already priced in `local`)
                        sc = shape_cost(rest + list(zip(ps, st))) - shape_cost(list(zip(ps, st)))
                        if sc > steal * len(rest):
                            sc, keep = steal * len(rest), False
                        c += max(0.0, sc)
                if pf is not None:
                    c += _transition(prev_ps, pf, ps, st, dt)
                cand.append((c, bi, si, keep))
        cand.sort(key=lambda x: x[0])
        new, seen = [], set()
        for c, bi, si, keep in cand:
            _, pf, held, _, _ = beam_[bi]
            st = states[si]
            held_now = tuple(h for h in held if keep and h[0] > start + held_tol and h[2] not in st)
            key = (st, tuple(sorted((h[2], h[1]) for h in held_now)))
            if key in seen:
                continue
            seen.add(key)
            nh = held_now + tuple((n.end, p, f) for n, p, f in zip(ns, ps, st))
            new.append((c, st, nh, bi, si))
            if len(new) >= beam:
                break
        history.append([(e[3], e[1], e[0]) for e in new])
        beam_ = new
        prev_ps, prev_start = ps, start

    result = {}
    j = 0
    for gi in range(len(groups) - 1, -1, -1):
        bi, st, c = history[gi][j]
        for n, f in zip(groups[gi][1], st):
            result[id(n)] = f
            if repaired is not None:
                g = fixed.get(id(n)) if fixed is not None else getattr(n, "finger", None)
                if g and g != f:
                    repaired.add(id(n))
        if costs is not None:
            prev = history[gi - 1][bi][2] if gi > 0 else 0.0
            for n in groups[gi][1]:
                costs[id(n)] = max(0.0, c - prev)
        j = bi
    return result


def finger_hand(notes, left=False, beam=BEAM, context=None, pianist=None):
    """Convenience: {id(note): finger} for one hand's notes (context: the whole song, for keys)."""
    vp = mirror_pitch if left else None
    return plan_fingering(group_notes(notes, vpitch=vp), vp, beam=beam,
                          hand="L" if left else "R", context=context, pianist=pianist)


def score_fingering(groups, fingering, vpitch=None, hand=None, context=None, pianist=None):
    """
    {id(note): difficulty} for a given fingering: what each chord costs the
    planner's physical model (stretches, crossings, shifts against the clock,
    finger speed...), leaving out how far it is from the standard fingerings.
    """
    costs = {}
    plan_fingering(groups, vpitch, beam=4, hand=hand, context=context, figures=False,
                   pianist=pianist, fixed=fingering, costs=costs)
    return costs
