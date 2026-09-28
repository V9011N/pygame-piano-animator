"""
hand_split.py - Decide which hand plays each note when the MIDI file doesn't say.

Many MIDI files put the whole piano part on one track (or split it by voice,
not by hand). Splitting at a fixed pitch fails as soon as the hands overlap,
which in real music is all the time: the left hand plays chords above middle C
while the right hand runs, the hands swap roles, and so on.

This is a beam search over onset groups (notes that start together). At each
group the notes are sorted by pitch and the lowest k go to the left hand, the
rest to the right hand (hands rarely interleave within one chord). Each
candidate carries both hands' recent history, and is scored on what a
pianist's hands can actually do:

  * span   - notes one hand plays or holds at the same time must fit in the
             hand (an octave is easy, a 10th is the limit; wider chords are
             priced as rolled, since that is what a pianist does with them);
  * speed  - no hand travels faster than the pianist's top speed
             (pianist "max_speed", fingering.travel_time): a move that would
             need more is priced far above anything else, rising steeply, so
             a hand never "teleports" to notes the other hand could take; and
             every move is priced by how fast the hand must travel, so of
             two hands that could take a note, the one that needn't hurry does;
  * load   - at most five notes per hand, counting ones still held;
  * order  - the right hand normally stays above the left, and the hands
             need room: a third split between two hands is really one hand's
             double note;
  * range  - a mild preference for the right hand high and the left hand low;
  * voices - in files split into several (unlabelled) tracks, a track's
             notes tend to stay in one hand;
  * repeats - a chord struck again straight away is split the way it was.

Only the hand labels are decided here; fingering comes later.
"""
from __future__ import annotations

import math

from fingering import key_pos, travel_time, MOVE_SHARE

LEFT, RIGHT = "L", "R"

CHORD_TOL = 0.035        # onsets closer than this count as one group
BEAM = 32                # candidates kept per group

# span within one hand (semitones): free up to an octave, costly to a 10th
SPAN_FREE = 12
SPAN_MAX = 16
SPAN_HELD_MAX = 19       # with keys still held
SPAN_OVER = 12.0         # a chord wider than SPAN_MAX has to be rolled
# movement: a hand covering its last notes can reach anything within
# HAND_WK white keys without moving; beyond that it travels, at most at the
# pianist's top speed (TOO_FAST per 100% over the time that needs, squared)
HAND_WK = 7.0
TOO_FAST = 60.0
MOVE_COST = 0.06         # per semitone of any shift (hands prefer to stay put)
SPEED_COST = 0.25        # per 40 semitones/second of travel the note demands
CHORD_COST = 0.5         # per extra note in a chord, when chords come quickly
HELD_TOL = 0.03
TRACK_T = 1.5            # a track's notes played by one hand ...
TRACK_SWITCH = 12.0       # ... cost this to move to the other hand within TRACK_T
CROWD_GAP = 5            # semitones the hands want between them ...
CROWD = 1.5              # ... per semitone short of that
REPEAT_T = 0.5           # a chord repeated within this ...
REPEAT_SPLIT = 4.0       # ... and split differently from the last time costs this
CROWD_T = 0.25           # a hand's last notes count this long for crowding          # a note ending within this after the onset counts as released


def _groups(notes):
    out = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if out and n.start - out[-1][0] <= CHORD_TOL:
            out[-1][1].append(n)
        else:
            out.append((n.start, [n]))
    return [(t, sorted(ns, key=lambda n: n.pitch)) for t, ns in out]


class _Hand:
    """A hand's recent history along one candidate path (immutable-ish)."""
    __slots__ = ("center", "last_t", "last_lo", "last_hi", "held")

    def __init__(self, center, last_t=-math.inf, last_lo=None, last_hi=None, held=()):
        self.center = center          # where the hand is, in semitones
        self.last_t = last_t          # when it last played
        self.last_lo = last_lo        # range of the last group it played
        self.last_hi = last_hi
        self.held = held              # ((end_time, pitch), ...) still sounding

    def after(self, t, notes):
        held = tuple(h for h in self.held if h[0] > t + HELD_TOL)
        if not notes:
            return _Hand(self.center, self.last_t, self.last_lo, self.last_hi, held)
        ps = [n.pitch for n in notes]
        m = sum(ps) / len(ps)
        # the hand centres on what it just played, remembering a little of before
        c = m if self.last_lo is None else 0.7 * m + 0.3 * self.center
        return _Hand(c, t, min(ps), max(ps), held + tuple((n.end, n.pitch) for n in notes))


def _hand_cost(hand, t, notes, side, other):
    """Cost of `hand` (history) playing `notes` (sorted) at time t."""
    if not notes:
        return 0.0
    ps = [n.pitch for n in notes]
    lo, hi = ps[0], ps[-1]
    c = 0.0
    # --- how many notes and how wide, including what this hand still holds
    held = [p for e, p in hand.held if e > t + HELD_TOL]
    allp = ps + held
    if len(ps) > 5:
        c += 100.0
    elif len(allp) > 5:
        c += 8.0 * (len(allp) - 5)
    # notes struck together must fit the hand ...
    span = hi - lo
    if span > SPAN_FREE:
        c += 2.5 * (span - SPAN_FREE)
    if span > SPAN_MAX:
        c += SPAN_OVER + 3.0 * (span - SPAN_MAX)
    # ... and should fit with what it still holds (softer: held keys can be
    # let go early, and a rolled chord needn't be held all at once)
    if held:
        span_h = max(hi, max(held)) - min(lo, min(held))
        if span_h > max(span, SPAN_FREE):
            c += 0.8 * (span_h - max(span, SPAN_FREE))
        if span_h > SPAN_HELD_MAX:
            c += 20.0
    # --- chords at speed: a hand playing several notes at once, again and
    # again, is working harder than two hands sharing the load
    # (small shapes - thirds, fourths, sixths - are what one hand is for)
    if len(ps) > 1:
        dt_h = t - hand.last_t if hand.last_lo is not None else 1.0
        size = 0.0 if hi - lo <= 5 else 0.5 if hi - lo <= 9 else 1.0
        c += CHORD_COST * size * (len(ps) - 1) ** 1.5 * min(3.0, 0.4 / max(1e-3, dt_h))
    if hand.last_lo is None:
        m = sum(ps) / len(ps)
        c += MOVE_COST * abs(m - hand.center)          # first notes: near where it starts
    # --- movement since the hand last played
    if hand.last_lo is not None:
        dt = max(1e-3, t - hand.last_t)
        # how far the hand must reach: the new note furthest from where it
        # just was (a chord reaching back into the other hand's range counts)
        d = max(max(0.0, p - hand.last_hi, hand.last_lo - p) for p in ps)
        m = sum(ps) / len(ps)
        shift = abs(m - hand.center)
        fade = math.exp(-dt / 1.5)                    # old positions matter less
        c += fade * MOVE_COST * shift
        # the least the hand must travel (white keys): from where it could
        # play its last notes to where it can play these
        was = (key_pos(hand.last_hi) - HAND_WK, key_pos(hand.last_lo))
        now = (key_pos(hi) - HAND_WK, key_pos(lo))
        gap = max(0.0, now[0] - was[1], was[0] - now[1])
        if gap > 0:
            # beyond the top speed it quickly becomes impossible: a
            # two-octave leap in a sixteenth is not just twice as hard as one
            r = travel_time(gap, MAX_SPEED) / max(1e-3, MOVE_SHARE * dt) - 1.0
            if r > 0:
                c += TOO_FAST * (r + r * r)
        # how fast the hand must travel to get there: of two hands that could
        # take a note, the one that needn't hurry should
        c += SPEED_COST * d / max(dt, 0.05) / 40.0
    # --- the right hand above the left
    if other.last_lo is not None and t - other.last_t < 1.0:
        if side == RIGHT and lo < other.center - 2:
            c += 0.25 * (other.center - 2 - lo)
        if side == LEFT and hi > other.center + 2:
            c += 0.25 * (hi - other.center - 2)
    # --- range preference (weak)
    if side == RIGHT:
        c += 0.02 * sum(max(0, 55 - p) for p in ps)
    else:
        c += 0.02 * sum(max(0, p - 67) for p in ps)
    return c


def _crowding(rh, lh, t, rn, ln):
    """
    A double note (two notes struck together, nothing else) split between the
    hands, with the hands squeezed a few keys apart, is really one hand's
    third or fourth: that costs.
    """
    if len(rn) == 1 and len(ln) == 1:
        gap = rn[0].pitch - ln[0].pitch
        return CROWD * max(0, CROWD_GAP - gap)
    return 0.0


_BASE_SPANS = (SPAN_FREE, SPAN_MAX, SPAN_HELD_MAX)


def _fit_spans(pianist):
    """Scale the one-hand span limits to the pianist's hand (the defaults are for a 9th)."""
    global SPAN_FREE, SPAN_MAX, SPAN_HELD_MAX
    k = 1.0
    if pianist is not None:
        try:
            from hands import hand_span_inches
            k = hand_span_inches(pianist.anatomy) / hand_span_inches(None)
        except Exception:
            k = 1.0
    SPAN_FREE, SPAN_MAX, SPAN_HELD_MAX = (v * k for v in _BASE_SPANS)


MAX_SPEED = 3.0          # m/s, from the pianist (split_hands)


def split_hands(notes, pianist=None):
    """
    {id(note): 'L' or 'R'} for every note. Works on any note objects with
    .pitch, .start and .end. `pianist` (default: the active one) sets how
    wide a hand can reach.
    """
    if pianist is None:
        try:
            import pianist as pianists
            pianist = pianists.active()
        except Exception:
            pianist = None
    _fit_spans(pianist)
    global MAX_SPEED
    MAX_SPEED = float(pianist.b("max_speed")) if pianist is not None else 3.0
    groups = _groups(notes)
    if not groups:
        return {}
    # beam entries: (cost, right_hand, left_hand, back_pointer, split)
    # start each hand where the opening music sits (upper / lower quartile)
    first = sorted(n.pitch for _, ns in groups[:40] for n in ns)
    hi_c = first[(3 * len(first)) // 4] if len(first) > 3 else 67.0
    lo_c = first[len(first) // 4] if len(first) > 3 else 48.0
    if hi_c - lo_c < 7:
        hi_c, lo_c = max(hi_c, 60.0), min(lo_c, 53.0)
    # When the file has several tracks (not labelled by hand, so probably
    # voices), a voice tends to stay in one hand: switching a track's notes to
    # the other hand soon after costs.
    multi = len({getattr(n, "track", 0) for _, ns in groups for n in ns}) > 1
    beam = [(0.0, _Hand(float(hi_c)), _Hand(float(lo_c)), None, None, ())]
    history = []
    prev = None
    for t, ns in groups:
        repeat = prev is not None and t - prev[0] < REPEAT_T and \
            [n.pitch for n in prev[1]] == [n.pitch for n in ns]
        prev = (t, ns)
        cand = []
        for bi, (cost, rh, lh, _, pk, tr) in enumerate(beam):
            last = dict(tr)
            for k in range(len(ns) + 1):              # lowest k notes -> left hand
                ln, rn = ns[:k], ns[k:]
                c = cost + _hand_cost(rh, t, rn, RIGHT, lh) + _hand_cost(lh, t, ln, LEFT, rh)
                if repeat and k != pk:
                    c += REPEAT_SPLIT
                c += _crowding(rh, lh, t, rn, ln)
                if multi:
                    for n, h in [(n, LEFT) for n in ln] + [(n, RIGHT) for n in rn]:
                        prev = last.get(getattr(n, "track", 0))
                        if prev and prev[0] != h and t - prev[1] < TRACK_T:
                            c += TRACK_SWITCH
                cand.append((c, bi, k))
        cand.sort(key=lambda x: x[0])
        new, seen = [], set()
        for c, bi, k in cand:
            _, rh, lh, _, _, tr = beam[bi]
            nr, nl = rh.after(t, ns[k:]), lh.after(t, ns[:k])
            if multi:
                d = dict(tr)
                for n in ns[:k]:
                    d[getattr(n, "track", 0)] = (LEFT, t)
                for n in ns[k:]:
                    d[getattr(n, "track", 0)] = (RIGHT, t)
                tr = tuple(sorted(d.items()))
                hands_key = tuple((trk, h) for trk, (h, _) in tr)
            else:
                hands_key = ()
            # merge candidates whose hands are in (almost) the same place
            key = (round(nr.center), round(nl.center), k, hands_key)
            if key in seen:
                continue
            seen.add(key)
            new.append((c, nr, nl, bi, k, tr))
            if len(new) >= BEAM:
                break
        history.append([(e[3], e[4]) for e in new])
        beam = new

    # trace back the best path
    out = {}
    j = 0
    for gi in range(len(groups) - 1, -1, -1):
        bi, k = history[gi][j]
        _, ns = groups[gi]
        for n in ns[:k]:
            out[id(n)] = LEFT
        for n in ns[k:]:
            out[id(n)] = RIGHT
        j = bi
    return out
