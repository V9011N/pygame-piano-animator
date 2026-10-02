"""
glissando.py - Find the glissandos in one hand's notes.

A glissando is a string of all-white or all-black keys, each the next key of
that colour, going one way, struck in quick succession: the hand slides the
backs of its fingers (the nails) along the keys instead of fingering them.

Notes marked as a glissando in the file (Note.gliss, "Rg"/"Lg" from the
fingering editor) always are one. Otherwise, if the pianist detects them
(behaviour "glissando"), a string of at least "gliss_min" notes with at most
"gliss_gap" between onsets is one. Performance MIDI is a little untidy in a
real glissando - a key missed on the way, two keys sounding in the same
instant or in swapped order - so detection allows one key of that colour
skipped per step and orders notes struck within SAME_T of each other by the
way the run goes.

Glissandos close together (less than "gliss_merge" apart, with no other
notes of the hand in between) form one episode: the hand stays in the
glissando pose through the breaks - unless the next one starts more than
MERGE_MAX_KEYS keys from where the last ended, when the hand may go back to
its rest position on the way.
"""
from __future__ import annotations

BLACK = {1, 3, 6, 8, 10}
SAME_T = 0.012          # s, notes struck closer than this sound together (their order is free)
MAX_SKIP = 1            # same-colour keys a detected run may skip at a step
TAIL_SKIP = 3           # a run's loose ends may skip this many keys of its colour...
TAIL_GAP = 2.0          # ...and come this many times the gap after (or before) it
EXPLICIT_GAP_T = 0.5    # s, explicitly marked notes further apart than this are separate glissandos
MERGE_MAX_KEYS = 5      # glissandos starting further than this many keys (of the new one's colour) from where
                        # the last ended aren't joined: the hand may go back to its rest position between them


def keys_between(a, b, black):
    """How many keys of that colour from pitch a to pitch b (the one at b counted, not a)."""
    lo, hi = min(a, b), max(a, b)
    return sum(1 for q in range(lo + 1, hi + 1) if is_black(q) == black) if a <= b else \
        sum(1 for q in range(lo, hi) if is_black(q) == black)


def is_black(p):
    return p % 12 in BLACK


def colour_index(p):
    """Position of key p among the keys of its own colour (consecutive keys of a colour differ by 1)."""
    o, pc = divmod(p, 12)
    if pc in BLACK:
        return o * 5 + sorted(BLACK).index(pc)
    return o * 7 + [0, 2, 4, 5, 7, 9, 11].index(pc)


def is_string(notes, max_skip=0):
    """
    Do these notes (in playing order) form a glissando string: all one
    colour, each the next key of that colour (up to max_skip skipped), all
    going the same way? At least three notes.
    """
    if len(notes) < 3:
        return False
    black = is_black(notes[0].pitch)
    if any(is_black(n.pitch) != black for n in notes):
        return False
    steps = [colour_index(b.pitch) - colour_index(a.pitch) for a, b in zip(notes, notes[1:])]
    if any(s == 0 or abs(s) > 1 + max_skip for s in steps):
        return False
    return all(s > 0 for s in steps) or all(s < 0 for s in steps)


def _ordered(notes):
    """The hand's notes in playing order, notes struck within SAME_T ordered the way the music around them goes."""
    ns = sorted(notes, key=lambda n: (n.start, n.pitch))
    out, i = [], 0
    while i < len(ns):
        j = i + 1
        while j < len(ns) and ns[j].start - ns[i].start < SAME_T:
            j += 1
        cluster = ns[i:j]
        if len(cluster) > 1:
            before = out[-1].pitch if out else None
            after = ns[j].pitch if j < len(ns) else None
            mean = sum(n.pitch for n in cluster) / len(cluster)
            up = (before is not None and before < mean) or (before is None and after is not None and after > mean)
            cluster.sort(key=lambda n: n.pitch, reverse=not up)
        out += cluster
        i = j
    return out


def detect(notes, max_gap, min_len):
    """[[notes of one glissando, in playing order]] among one hand's notes."""
    ns = _ordered(notes)
    runs, cur, d = [], [], 0

    def close():
        if len(cur) >= min_len:
            runs.append(list(cur))
    for n in ns:
        if cur:
            a = cur[-1]
            step = colour_index(n.pitch) - colour_index(a.pitch) if is_black(n.pitch) == is_black(a.pitch) else 0
            ok = step != 0 and abs(step) <= 1 + MAX_SKIP and n.start - a.start <= max_gap + 1e-9 and \
                (d == 0 or (step > 0) == (d > 0))
            if ok:
                cur.append(n)
                d = step
                continue
            close()
            # a note that turns the run round can start the next one
            cur, d = ([a, n], step) if step != 0 and abs(step) <= 1 + MAX_SKIP and \
                n.start - a.start <= max_gap + 1e-9 else ([n], 0)
            continue
        cur, d = [n], 0
    close()
    # runs that share their turning note: that note belongs to the first
    taken, out = set(), []
    for r in runs:
        r = [n for n in r if id(n) not in taken]
        if len(r) >= min(min_len, 3):
            out.append(r)
            taken |= {id(n) for n in r}
    # loose ends: a slower note or two leading in, or the last keys flicked
    # past a few skipped ones, are part of the slide
    pos = {id(n): i for i, n in enumerate(ns)}
    for r in out:
        d = 1 if colour_index(r[-1].pitch) > colour_index(r[0].pitch) else -1
        for end, step in ((1, 1), (0, -1)):
            while True:
                edge = r[-1] if end else r[0]
                i = pos[id(edge)] + step
                if not 0 <= i < len(ns) or id(ns[i]) in taken:
                    break
                n = ns[i]
                k = (colour_index(n.pitch) - colour_index(edge.pitch)) * step
                if is_black(n.pitch) != is_black(edge.pitch) or not 0 < k * d <= TAIL_SKIP + 1 or \
                        abs(n.start - edge.start) > TAIL_GAP * max_gap:
                    break
                taken.add(id(n))
                if end:
                    r.append(n)
                else:
                    r.insert(0, n)
    return out


def explicit(notes):
    """[[notes]] of the glissandos marked in the file (Note.gliss), split where they're far apart."""
    ns = [n for n in _ordered(notes) if getattr(n, "gliss", False)]
    runs, cur = [], []
    for n in ns:
        if cur and n.start - cur[-1].start > EXPLICIT_GAP_T:
            runs.append(cur)
            cur = []
        cur.append(n)
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def find(notes, pianist=None):
    """
    The glissandos in one hand's notes: [[notes]] in time order - the file's
    marked ones, and (if the pianist detects them) the detected ones that
    don't overlap those.
    """
    runs = explicit(notes)
    on = pianist is None or pianist.b("glissando") == "on"
    if on:
        gap = float(pianist.b("gliss_gap")) if pianist is not None else 0.05
        k = int(round(pianist.b("gliss_min"))) if pianist is not None else 6
        marked = {id(n) for r in runs for n in r}
        runs += [r for r in detect([n for n in notes if not getattr(n, "gliss", False)], gap, k)
                 if not any(id(n) in marked for n in r)]
    return sorted(runs, key=lambda r: r[0].start)


def episodes(runs, notes, merge_t):
    """
    [[runs]]: glissandos less than merge_t apart, with none of the hand's
    other notes starting in between, are one episode (the hand stays in the
    glissando pose through the break) - unless the next one starts more than
    MERGE_MAX_KEYS keys (of its colour) from where the last one ended.
    """
    in_run = {id(n) for r in runs for n in r}
    others = sorted(n.start for n in notes if id(n) not in in_run)
    import bisect
    out = []
    for r in runs:
        if out:
            last = out[-1][-1]
            t0, t1 = last[-1].start, r[0].start
            i = bisect.bisect_right(others, t0)
            near = keys_between(last[-1].pitch, r[0].pitch, is_black(r[0].pitch)) <= MERGE_MAX_KEYS
            if t1 - t0 < merge_t and near and (i >= len(others) or others[i] >= t1):
                out[-1].append(r)
                continue
        out.append([r])
    return out
