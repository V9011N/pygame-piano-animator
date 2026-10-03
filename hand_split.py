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
  * repeats - a chord struck again straight away is split the way it was;
  * tracks  - when the file's tracks say which hand (by name, or two tracks in
             different registers), that split is followed (exactly, crossings
             included) unless a hand couldn't keep up with it: then the
             notes go to the other hand, at TRACK_PRIOR per note.

Only the hand labels are decided here; fingering comes later.
"""
from __future__ import annotations

import bisect
import math

import progress
from figures import find_trills
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
REPEAT_SHAPE = 0.3       # ... this share of it for keys the hand's last chord was on
HELD_TOL = 0.03
TRACK_T = 1.5            # a track's notes played by one hand ...
TRACK_SWITCH = 12.0       # ... cost this to move to the other hand within TRACK_T
CROWD_GAP = 5            # semitones the hands want between them ...
CROWD = 1.5              # ... per semitone short of that
REPEAT_T = 0.5           # a chord repeated within this ...
REPEAT_SPLIT = 4.0       # ... and split differently from the last time costs this
TRACK_PRIOR = 200.0      # per note played by the other hand than its track says ...
TRACK_PRIOR_SOFT = 1.0   # ... near where that hand couldn't keep up with its track:
TRACK_FREE_T = 0.5       # within this of a move needing more than
TOO_FAST_TRACK = 0.5     # 50% over the top speed


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
    __slots__ = ("center", "last_t", "last_lo", "last_hi", "held", "last_ps")

    def __init__(self, center, last_t=-math.inf, last_lo=None, last_hi=None, held=(), last_ps=()):
        self.center = center          # where the hand is, in semitones
        self.last_t = last_t          # when it last played
        self.last_lo = last_lo        # range of the last group it played
        self.last_hi = last_hi
        self.held = held              # ((end_time, pitch), ...) still sounding
        self.last_ps = last_ps        # the keys of the last group it played

    def after(self, t, notes):
        held = tuple(h for h in self.held if h[0] > t + HELD_TOL)
        if not notes:
            return _Hand(self.center, self.last_t, self.last_lo, self.last_hi, held, self.last_ps)
        ps = [n.pitch for n in notes]
        m = sum(ps) / len(ps)
        # the hand centres on what it just played, remembering a little of before
        c = m if self.last_lo is None else 0.7 * m + 0.3 * self.center
        return _Hand(c, t, min(ps), max(ps), held + tuple((n.end, n.pitch) for n in notes), tuple(ps))


class _Part:
    """One hand's share of a group, with everything about it that doesn't depend on the hand (_hand_cost)."""
    __slots__ = ("notes", "ps", "lo", "hi", "m", "n", "span", "span1", "span2", "chord", "pref")

    def __init__(self, notes, side):
        self.notes = notes
        if not notes:
            return
        ps = self.ps = [n.pitch for n in notes]
        lo, hi = self.lo, self.hi = ps[0], ps[-1]
        self.n = len(ps)
        self.m = sum(ps) / len(ps)
        span = self.span = hi - lo
        self.span1 = 2.5 * (span - SPAN_FREE) if span > SPAN_FREE else None
        self.span2 = SPAN_OVER + 3.0 * (span - SPAN_MAX) if span > SPAN_MAX else None
        if len(ps) > 1:
            size = 0.0 if hi - lo <= 5 else 0.5 if hi - lo <= 9 else 1.0
            self.chord = CHORD_COST * size * (len(ps) - 1) ** 1.5
        else:
            self.chord = None
        if side == RIGHT:
            self.pref = 0.02 * sum(max(0, 55 - p) for p in ps)
        else:
            self.pref = 0.02 * sum(max(0, p - 67) for p in ps)


def _hand_cost(hand, t, part, side, other):
    """Cost of `hand` (history) playing `part` (_Part: its notes, sorted) at time t."""
    if not part.notes:
        return 0.0
    ps = part.ps
    lo, hi = part.lo, part.hi
    c = 0.0
    # --- how many notes and how wide, including what this hand still holds
    held = [p for e, p in hand.held if e > t + HELD_TOL]
    nall = part.n + len(held)
    if part.n > 5:
        c += 100.0
    elif nall > 5:
        c += 8.0 * (nall - 5)
    # notes struck together must fit the hand ...
    span = part.span
    if part.span1 is not None:
        c += part.span1
    if part.span2 is not None:
        c += part.span2
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
    # (a chord mostly on the keys of the hand's last one - accompaniment
    # repeating its shape - is less work: only its new keys count fully; else
    # a scale in the other hand took the odd new note, mid-run)
    if part.chord is not None:
        dt_h = t - hand.last_t if hand.last_lo is not None else 1.0
        new = sum(1 for p in ps if p not in hand.last_ps) / len(ps)
        c += part.chord * min(3.0, 0.4 / max(1e-3, dt_h)) * (REPEAT_SHAPE + (1.0 - REPEAT_SHAPE) * new)
    if hand.last_lo is None:
        c += MOVE_COST * abs(part.m - hand.center)          # first notes: near where it starts
    # --- movement since the hand last played
    if hand.last_lo is not None:
        dt = max(1e-3, t - hand.last_t)
        # how far the hand must reach: the new note furthest from where it
        # just was (a chord reaching back into the other hand's range counts)
        lh, ll = hand.last_hi, hand.last_lo
        d = max(max(0.0, p - lh, ll - p) for p in ps)
        shift = abs(part.m - hand.center)
        fade = math.exp(-dt / 1.5)                    # old positions matter less
        c += fade * MOVE_COST * shift
        # beyond the top speed it quickly becomes impossible: a two-octave
        # leap in a sixteenth is not just twice as hard as one
        r = _too_fast(hand, t, lo, hi)
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
    c += part.pref
    return c


def _too_fast(hand, t, lo, hi):
    """
    How much faster than the top speed (0 = not) `hand` must travel to play
    lo..hi at t: from where it could play its last notes to where it can
    play these, the least distance in white keys.
    """
    if hand.last_lo is None:
        return 0.0
    dt = max(1e-3, t - hand.last_t)
    was = (key_pos(hand.last_hi) - HAND_WK, key_pos(hand.last_lo))
    now = (key_pos(hi) - HAND_WK, key_pos(lo))
    gap = max(0.0, now[0] - was[1], was[0] - now[1])
    if gap <= 0:
        return 0.0
    return max(0.0, travel_time(gap, MAX_SPEED) / max(1e-3, MOVE_SHARE * dt) - 1.0)


def _track_weights(groups, prefer):
    """
    {id(note): cost per note of playing it with the other hand than `prefer`
    says}: TRACK_PRIOR (in effect fixed) everywhere except within
    TRACK_FREE_T of a moment where a hand following the tracks would have to
    travel faster than its top speed - there TRACK_PRIOR_SOFT, so the
    notes can go to the other hand.
    """
    hands = {RIGHT: _Hand(67.0), LEFT: _Hand(48.0)}
    bad = []
    for t, ns in groups:
        for side in (RIGHT, LEFT):
            mine = [n for n in ns if prefer.get(id(n)) == side]
            if not mine:
                continue
            ps = [n.pitch for n in mine]
            if _too_fast(hands[side], t, min(ps), max(ps)) > TOO_FAST_TRACK:
                bad.append(t)
            hands[side] = hands[side].after(t, mine)
    out = {}
    for t, ns in groups:
        i = bisect.bisect_left(bad, t - TRACK_FREE_T)
        near = i < len(bad) and bad[i] <= t + TRACK_FREE_T
        for n in ns:
            out[id(n)] = TRACK_PRIOR_SOFT if near else TRACK_PRIOR
    return out


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


def split_hands(notes, pianist=None, prefer=None):
    """
    {id(note): 'L' or 'R'} for every note. Works on any note objects with
    .pitch, .start and .end. `pianist` (default: the active one) sets how
    wide a hand can reach and how fast it travels. `prefer` ({id(note):
    hand}, from the file's tracks) is followed unless a hand couldn't keep
    up with it.
    """
    prefer = prefer or {}
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
    weight = _track_weights(groups, prefer) if prefer else {}
    # beam entries: (cost, right_hand, left_hand, back_pointer, split, track hands, trill hands)
    # start each hand where the opening music sits (upper / lower quartile)
    first = sorted(n.pitch for _, ns in groups[:40] for n in ns)
    hi_c = first[(3 * len(first)) // 4] if len(first) > 3 else 67.0
    lo_c = first[len(first) // 4] if len(first) > 3 else 48.0
    if hi_c - lo_c < 7:
        hi_c, lo_c = max(hi_c, 60.0), min(lo_c, 53.0)
    # When the file has several tracks (not labelled by hand, so probably
    # voices), a voice tends to stay in one hand: switching a track's notes to
    # the other hand soon after costs.
    multi = len({getattr(n, "track", 0) for _, ns in groups for n in ns if id(n) not in prefer}) > 1
    # A trill is played by one hand: the other may not take any of its notes
    # (figures.find_trills). Each path remembers the hand its trills in
    # progress went to, and splits that would give a note to the other are
    # left out (all to one hand is always one of them, so some split fits).
    trill_of, trill_end = {}, {}
    for e, run in enumerate(find_trills([n for _, ns in groups for n in ns])):
        for n in run:
            trill_of[id(n)] = e
        trill_end[e] = run[-1].start
    beam = [(0.0, _Hand(float(hi_c)), _Hand(float(lo_c)), None, None, (), ())]
    history = []
    last_group = None
    for gi, (t, ns) in enumerate(groups):
        if gi % 64 == 0:
            progress.report(gi / len(groups))
        repeat = last_group is not None and t - last_group[0] < REPEAT_T and \
            [n.pitch for n in last_group[1]] == [n.pitch for n in ns]
        last_group = (t, ns)
        # the splits tried: the lowest k notes to the left hand, and (when the
        # tracks say, and differently) exactly the tracks' split, k = -1
        splits = [(k, ns[:k], ns[k:]) for k in range(len(ns) + 1)]
        if all(id(n) in prefer for n in ns):
            ln = [n for n in ns if prefer[id(n)] == LEFT]
            if ln != ns[:len(ln)]:
                splits.append((-1, ln, [n for n in ns if prefer[id(n)] != LEFT]))
        parts = [(_Part(rn, RIGHT), _Part(ln, LEFT)) for _, ln, rn in splits]
        # each split's trill notes: {trill: hand}, or None if it would share one between the hands
        trill_hands = []
        for _, ln, rn in splits:
            d = {}
            for n, h in [(n, LEFT) for n in ln] + [(n, RIGHT) for n in rn]:
                e = trill_of.get(id(n))
                if e is not None:
                    if d.setdefault(e, h) != h:
                        d = None
                        break
            trill_hands.append(d)
        cand = []
        for bi, (cost, rh, lh, _, pk, tr, th) in enumerate(beam):
            last = dict(tr)
            held = dict(th)
            for (k, ln, rn), (rp, lp), d in zip(splits, parts, trill_hands):
                if d is None or any(held.get(e, h) != h for e, h in d.items()):
                    continue                    # (the other hand has this trill)
                c = cost + _hand_cost(rh, t, rp, RIGHT, lh) + _hand_cost(lh, t, lp, LEFT, rh)
                if repeat and k != pk:
                    c += REPEAT_SPLIT
                c += _crowding(rh, lh, t, rn, ln)
                if prefer:
                    c += sum(weight[id(n)] for n in ln if prefer.get(id(n), LEFT) != LEFT) + \
                        sum(weight[id(n)] for n in rn if prefer.get(id(n), RIGHT) != RIGHT)
                if multi:
                    for n, h in [(n, LEFT) for n in ln] + [(n, RIGHT) for n in rn]:
                        if id(n) in prefer:
                            continue
                        prev = last.get(getattr(n, "track", 0))
                        if prev and prev[0] != h and t - prev[1] < TRACK_T:
                            c += TRACK_SWITCH
                cand.append((c, bi, k, ln, rn, d))
        cand.sort(key=lambda x: x[0])
        new, seen = [], set()
        for c, bi, k, ln, rn, d in cand:
            _, rh, lh, _, _, tr, th = beam[bi]
            if d or th:
                held = dict(th)
                held.update(d)
                th = tuple(sorted((e, h) for e, h in held.items() if trill_end[e] > t + 1e-9))
            nr, nl = rh.after(t, rn), lh.after(t, ln)
            if multi:
                d = dict(tr)
                for n in ln:
                    d[getattr(n, "track", 0)] = (LEFT, t)
                for n in rn:
                    d[getattr(n, "track", 0)] = (RIGHT, t)
                tr = tuple(sorted(d.items()))
                hands_key = tuple((trk, h) for trk, (h, _) in tr)
            else:
                hands_key = ()
            # merge candidates whose hands are in (almost) the same place
            key = (round(nr.center), round(nl.center), k, hands_key, th)
            if key in seen:
                continue
            seen.add(key)
            new.append((c, nr, nl, bi, k, tr, th))
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
        for i, n in enumerate(ns):
            if k == -1:
                out[id(n)] = LEFT if prefer[id(n)] == LEFT else RIGHT
            else:
                out[id(n)] = LEFT if i < k else RIGHT
        j = bi
    return out
