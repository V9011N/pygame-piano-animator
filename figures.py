"""
figures.py - Recognise the standard figures of piano technique in a hand's
notes and give them their standard fingering.

A seasoned pianist doesn't finger note by note: they see "a B-flat major
scale", "an A-minor arpeggio", "chromatic thirds", "octaves" and reach for the
fingering they drilled. This module does the same, with the figures Hanon
drills and the fingerings his book prints:

  * diatonic scales, any key (major, harmonic and melodic minor): the key is
    read from the run and the music around it, the thumb goes on that key's
    standard thumb notes (per hand) and every other finger follows by
    counting scale steps (Hanon 39);
  * chromatic scales: 3 on the black keys, 1 on the white, 2 where two white
    keys meet (RH on C and F, LH on E and B) (Hanon 40);
  * arpeggios of triads and seventh chords (Hanon 41-43): the book's table for
    root-position triads in all 24 keys, the root-1 / 2-3-4 scheme for
    sevenths, and for inversions the cyclic fingering a pianist would pick
    (thumb on a white key, the widest gap across the thumb crossing, one
    finger per chord tone in every octave);
  * repeated notes: changing fingers 3-2-1 (Hanon 44-47);
  * trills: 2-3 (or 1-3), not the weak 4-5;
  * octaves, joined or broken: 1-5, with 4 on black keys (Hanon 51-57);
  * scales in thirds: groups of four and three thirds per octave (Hanon 52);
  * chromatic thirds: the printed twelve-step fingering (Hanon 50);
  * trills in thirds: 1-3 / 2-4; thirds inside a hand position 1-3, 2-4, 3-5;
  * trills in sixths and fourths: 1-4 / 2-5 (Hanon 55, 59); other sixths
    1-5 (4 on black keys).

detect() returns suggestions {id(note): (finger, weight)}; the finger may
also be a frozenset of acceptable fingers (trills: any strong pair). They are soft: the
planner in fingering.py pays `weight` for disagreeing with one, so a standard
fingering wins unless the hand physically can't do it (a held note, a leap,
the passage around it).
"""
from __future__ import annotations

import math

LEFT, RIGHT = "L", "R"
BLACK = {1, 3, 6, 8, 10}

# ----------------------------------------------------------------- keys
PC = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3, 'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7,
      'G#': 8, 'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
HARM = [0, 2, 3, 5, 7, 8, 11]
NAT = [0, 2, 3, 5, 7, 8, 10]
MELU = [0, 2, 3, 5, 7, 9, 11]
MAJ_NAME = {0: 'C', 1: 'Db', 2: 'D', 3: 'Eb', 4: 'E', 5: 'F', 6: 'F#', 7: 'G', 8: 'Ab', 9: 'A', 10: 'Bb', 11: 'B'}
MIN_NAME = {0: 'C', 1: 'C#', 2: 'D', 3: 'Eb', 4: 'E', 5: 'F', 6: 'F#', 7: 'G', 8: 'G#', 9: 'A', 10: 'Bb', 11: 'B'}

# Scale thumb notes per key: (right hand, left hand) - standard fingerings, as in Hanon 39
THUMBS = {
    ('C', 'maj'): ('C F', 'C G'), ('G', 'maj'): ('G C', 'G D'), ('D', 'maj'): ('D G', 'D A'),
    ('A', 'maj'): ('A D', 'A E'), ('E', 'maj'): ('E A', 'E B'), ('B', 'maj'): ('B E', 'B F#'),
    ('F#', 'maj'): ('B F', 'B F'), ('Db', 'maj'): ('F C', 'F C'), ('Ab', 'maj'): ('C F', 'C G'),
    ('Eb', 'maj'): ('F C', 'G D'), ('Bb', 'maj'): ('C F', 'D A'), ('F', 'maj'): ('F C', 'C F'),
    ('A', 'min'): ('A D', 'A E'), ('E', 'min'): ('E A', 'E B'), ('B', 'min'): ('B E', 'B F#'),
    ('F#', 'min'): ('A D', 'B F'), ('C#', 'min'): ('E A', 'E C'), ('G#', 'min'): ('B E', 'B G'),
    ('Eb', 'min'): ('F B', 'F B'), ('Bb', 'min'): ('C F', 'C F'), ('F', 'min'): ('F C', 'C F'),
    ('C', 'min'): ('C F', 'C G'), ('G', 'min'): ('G C', 'G D'), ('D', 'min'): ('D G', 'D A'),
}
# melodic minors whose raised 6th/7th move the thumb going up (and back coming down)
MEL_UP = {('Eb', 'min'): ('F C', 'F C'), ('G#', 'min'): ('B F', 'B G'), ('F#', 'min'): ('A D#', 'B F'),
          ('C#', 'min'): ('E A#', 'E C')}
MEL_DOWN = {('Eb', 'min'): ('F B', 'F B'), ('G#', 'min'): ('E B', 'B F#'), ('F#', 'min'): ('A D', 'B E'),
            ('C#', 'min'): ('E A', 'E B')}

# Krumhansl-Kessler key profiles
KK_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _pcs(spec):
    return {PC[x] for x in spec.split()}


def key_scale(key, melodic=False):
    """(pitch classes going up, going down) of a key."""
    name, mode = key
    t = PC[name]
    if mode == 'maj':
        s = sorted((t + x) % 12 for x in MAJOR)
        return s, s
    if melodic:
        return sorted((t + x) % 12 for x in MELU), sorted((t + x) % 12 for x in NAT)
    s = sorted((t + x) % 12 for x in HARM)
    return s, s


def _corr(a, b):
    ma, mb = sum(a) / 12, sum(b) / 12
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) or 1.0
    return num / den


def find_key(run_pcs, context_hist, first=None, last=None):
    """
    The key a run belongs to: among keys whose scale holds every note of the
    run, the one that best matches the music around it (Krumhansl-Kessler
    profiles), with a nudge for runs that start or end on the tonic.
    Returns ((name, 'maj'|'min'), melodic) or None.
    """
    best = None
    for t in range(12):
        for mode, prof in (('maj', KK_MAJ), ('min', KK_MIN)):
            if mode == 'maj':
                forms = [(set((t + x) % 12 for x in MAJOR), False)]
            else:
                forms = [(set((t + x) % 12 for x in HARM), False),
                         (set((t + x) % 12 for x in MELU) | set((t + x) % 12 for x in NAT), True)]
            for scale, mel in forms:
                if not run_pcs <= scale:
                    continue
                rot = prof[-t:] + prof[:-t] if t else prof
                s = _corr(context_hist, rot)
                s += 0.15 * ((first == t) + (last == t))
                if mel:
                    s -= 0.05                       # prefer the plain harmonic form when both fit
                name = MAJ_NAME[t] if mode == 'maj' else MIN_NAME[t]
                if best is None or s > best[0]:
                    best = (s, (name, mode), mel)
    return None if best is None else (best[1], best[2])


def thumb_sets(key, hand, melodic):
    h = 0 if hand == RIGHT else 1
    if melodic and key in MEL_UP:
        return _pcs(MEL_UP[key][h]), _pcs(MEL_DOWN[key][h])
    base = _pcs(THUMBS[key][h])
    return base, base


def scale_fingering(ps, thumbs_up, hand, thumbs_down, scales, opening=True):
    """
    ps: a stepwise run of single notes; thumbs_*: pitch classes the thumb plays
    on notes reached going up / going down. RH: finger = 1 + scale steps down
    to the nearest thumb note; LH: 1 + steps up. Turning notes continue the
    motion they end. A RH run that starts going up on a non-thumb note counts
    2, 3, 4 up to its first thumb (as Hanon prints).
    """
    n = len(ps)
    going_up = [(ps[i + 1] > ps[i]) if i + 1 < n else (ps[i] > ps[i - 1]) for i in range(n)]
    arrive_up = [(ps[i] > ps[i - 1]) if i else going_up[0] for i in range(n)]
    allp = sorted(set(p % 12 for p in ps))
    up_pcs, dn_pcs = scales

    def steps(pc, pcs, thumbs, direction, skip_self=False):
        if pc not in pcs:
            pcs = sorted(set(pcs) | {pc})
        k = pcs.index(pc)
        for s in range(1 if skip_self else 0, 8):
            if pcs[(k + direction * s) % len(pcs)] in thumbs:
                return s
        return None

    out = [None] * n
    if opening and hand == RIGHT and n > 1 and going_up[0] and ps[0] % 12 not in thumbs_up:
        k = 0
        while k < n and k < 3 and ps[k] % 12 not in thumbs_up and (k == 0 or ps[k] > ps[k - 1]):
            out[k] = 2 + k
            k += 1
    for i in range(n):
        if out[i] is not None:
            continue
        pc = ps[i] % 12
        up = arrive_up[i]
        pcs, thumbs = (up_pcs, thumbs_up) if up else (dn_pcs, thumbs_down)
        top = 0 < i < n - 1 and ps[i - 1] < ps[i] > ps[i + 1]
        bot = 0 < i < n - 1 and ps[i - 1] > ps[i] < ps[i + 1]
        if hand == RIGHT:
            if top and out[i - 1]:
                out[i] = min(5, out[i - 1] + 1)
                continue
            k = steps(pc, pcs, thumbs, -1)
            if k is None or k > 4:
                k = steps(pc, allp, thumbs_up | thumbs_down, -1)
        else:
            if bot and out[i - 1]:
                out[i] = min(5, out[i - 1] + 1)
                continue
            if i == 0 and going_up[0] and opening:
                k = steps(pc, up_pcs, thumbs_up, +1, skip_self=True)
            else:
                k = steps(pc, pcs, thumbs, +1)
                if k is None or k > 4:
                    k = steps(pc, allp, thumbs_up | thumbs_down, +1)
        out[i] = None if k is None or k > 4 else 1 + k
    return out


# ----------------------------------------------------------------- chromatic
CHROM_R = {0: 2, 1: 3, 2: 1, 3: 3, 4: 1, 5: 2, 6: 3, 7: 1, 8: 3, 9: 1, 10: 3, 11: 1}   # 2 on C and F
CHROM_L = {0: 1, 1: 3, 2: 1, 3: 3, 4: 2, 5: 1, 6: 3, 7: 1, 8: 3, 9: 1, 10: 3, 11: 2}   # 2 on E and B

# A pianist's preferred fingerings for some figures (set by fingering.apply_pianist)
DEFAULT_PREFS = {"chromatic": "13", "repeated": "321", "trill": "auto", "octaves": "4black"}
PREFS = dict(DEFAULT_PREFS)

# Chromatic fingerings, right hand, by pitch class (the left hand uses the
# same maps on the mirrored keyboard). "start": what happens when a run
# starts (at its thumb end) on a white key the map doesn't give the thumb:
# "renumber" puts the thumb there and counts up to the next thumb, "thumb"
# only puts the thumb there, None keeps the map.
CHROM_MAPS = {
    "12":    ({0: 2, 1: 3, 2: 1, 3: 2, 4: 1, 5: 2, 6: 3, 7: 1, 8: 2, 9: 1, 10: 2, 11: 1}, "renumber"),
    "13":    (CHROM_R, "thumb"),
    "12345": ({0: 1, 1: 2, 2: 3, 3: 4, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 1, 10: 2, 11: 3}, None),
    "231":   ({0: 2, 1: 3, 2: 1, 3: 2, 4: 3, 5: 1, 6: 2, 7: 1, 8: 2, 9: 3, 10: 4, 11: 1}, None),
}


def _chrom_groups4(lo, hi):
    """{pitch: finger} for 'groups of up to four': thumbs on white keys, as far apart as possible."""
    out, f, p = {}, 1, lo
    out[p] = 1
    while p < hi:
        # the next thumb: the furthest white key at most four notes on
        nxt = None
        for k in range(4, 0, -1):
            if (p + k) % 12 not in BLACK:
                nxt = p + k
                break
        nxt = nxt or p + 1
        for q in range(p + 1, min(nxt, hi + 1)):
            out[q] = q - p + 1
        if nxt <= hi:
            out[nxt] = 1
        p = nxt
    return out


def chromatic_fingering(ps, hand):
    style = PREFS.get("chromatic", "13")
    if style == "13" and hand == RIGHT:
        m = CHROM_R
    elif style == "13":
        m = CHROM_L
    else:
        m = None
    if m is not None:                                  # the classic maps, as tuned on Hanon
        f = [m[p % 12] for p in ps]
        n = len(ps)
        for i in range(n):
            peak = 0 < i < n - 1 and ps[i - 1] < ps[i] > ps[i + 1]
            if hand == RIGHT and peak and ps[i] % 12 == 0:
                f[i] = 5 if f[i - 1] == 4 or ps[i - 1] % 12 == 11 else f[i]
        # a run starting on a white key without the thumb starts with it
        _start_thumb(ps, f, hand, "thumb", m)
        return f
    # everything else in the right-hand frame
    vps = [p if hand == RIGHT else 124 - p for p in ps]
    if style == "1234":
        fmap = _chrom_groups4(min(vps), max(vps))
        return [fmap[v] for v in vps]
    cmap, start = CHROM_MAPS.get(style, CHROM_MAPS["13"])
    f = [cmap[v % 12] for v in vps]
    _start_thumb(vps, f, RIGHT, start, cmap)
    return f


def _start_thumb(ps, f, hand, rule, cmap):
    """Apply a map's start rule at the run's thumb end (lowest note in the RH frame)."""
    if not rule:
        return
    vps = [p if hand == RIGHT else 124 - p for p in ps]
    lo = min(vps)
    if lo % 12 in BLACK or cmap[lo % 12 if hand == RIGHT else (124 - lo) % 12] == 1:
        return
    for i, v in enumerate(vps):
        if v == lo:
            f[i] = 1
        elif rule == "renumber" and lo < v:
            # count up from the thumb until the map's next thumb
            if all(cmap[(q if hand == RIGHT else 124 - q) % 12] != 1 for q in range(lo + 1, v + 1)):
                f[i] = v - lo + 1 if v - lo + 1 <= 5 else f[i]


# ----------------------------------------------------------------- arpeggios
# The book's root-position triad arpeggios: (RH {note: finger}, RH first
# finger, LH {note: finger}, LH first finger) - Hanon 41
ARP = {
    ('C', 'maj'): ('C1 E2 G3', 1, 'C1 G2 E4', 5), ('A', 'min'): ('A1 C2 E3', 1, 'A1 E2 C4', 5),
    ('F', 'maj'): ('F1 A2 C3', 1, 'F1 C2 A4', 5), ('D', 'min'): ('D1 F2 A3', 1, 'D1 A2 F4', 5),
    ('Bb', 'maj'): ('D1 F2 Bb4', 2, 'F1 D2 Bb3', 3), ('G', 'min'): ('G1 Bb2 D3', 1, 'G1 D2 Bb4', 5),
    ('Eb', 'maj'): ('G1 Bb2 Eb4', 2, 'G1 Eb2 Bb4', 3), ('C', 'min'): ('C1 Eb2 G3', 1, 'C1 G2 Eb4', 5),
    ('Ab', 'maj'): ('C1 Eb2 Ab4', 2, 'C1 Ab2 Eb4', 3), ('F', 'min'): ('F1 Ab2 C3', 1, 'F1 C2 Ab4', 5),
    ('Db', 'maj'): ('F1 Ab2 Db4', 2, 'F1 Db2 Ab4', 2), ('Bb', 'min'): ('F1 Bb2 Db3', 2, 'F1 Db2 Bb3', 3),
    ('F#', 'maj'): ('F#1 Bb2 Db3', 1, 'F#1 Db2 Bb3', 5), ('Eb', 'min'): ('Eb1 F#2 Bb3', 1, 'Eb1 Bb2 F#4', 5),
    ('B', 'maj'): ('B1 Eb2 F#3', 1, 'B1 F#2 Eb3', 5), ('G#', 'min'): ('B1 Eb2 G#4', 2, 'B1 G#2 Eb4', 3),
    ('E', 'maj'): ('E1 G#2 B3', 1, 'E1 B2 G#3', 5), ('C#', 'min'): ('E1 G#2 C#4', 2, 'E1 C#2 G#4', 3),
    ('A', 'maj'): ('A1 C#2 E3', 1, 'A1 E2 C#3', 5), ('F#', 'min'): ('A1 C#2 F#4', 2, 'A1 F#2 C#4', 3),
    ('D', 'maj'): ('D1 F#2 A3', 1, 'D1 A2 F#3', 5), ('B', 'min'): ('B1 D2 F#3', 1, 'B1 F#2 D4', 5),
    ('G', 'maj'): ('G1 B2 D3', 1, 'G1 D2 B4', 5), ('E', 'min'): ('E1 G2 B3', 1, 'E1 B2 G4', 5),
}
TRIADS = {(0, 4, 7): 'maj', (0, 3, 7): 'min', (0, 3, 6): 'dim', (0, 4, 8): 'aug'}
SEVENTHS = {(0, 4, 7, 10): 'dom7', (0, 4, 7, 11): 'maj7', (0, 3, 7, 10): 'min7',
            (0, 3, 6, 9): 'dim7', (0, 3, 6, 10): 'hdim7', (0, 3, 7, 11): 'minmaj7'}


def chord_of(pcs):
    """(root_pc, kind) for a set of 3-4 pitch classes forming a triad or seventh, else None."""
    pcs = set(pcs)
    for r in sorted(pcs):
        rel = tuple(sorted((p - r) % 12 for p in pcs))
        if rel in TRIADS:
            return r, TRIADS[rel]
        if rel in SEVENTHS:
            return r, SEVENTHS[rel]
    return None


def _fmap(spec):
    out = {}
    for tok in spec.split():
        out[PC[tok[:-1]]] = int(tok[-1])
    return out


def _cyclic_cost(order_pcs, fmap, rh_frame_up):
    """
    How awkward a cyclic arpeggio fingering is, in the right-hand frame going
    up: order_pcs are the chord tones in rising order starting from the thumb.
    Stretches inside the hand, the width of the thumb crossing, the thumb on
    a black key and the weak fourth finger all cost.
    """
    c = 0.0
    fs = [fmap[p] for p in order_pcs]
    ivs = [(order_pcs[(k + 1) % len(order_pcs)] - order_pcs[k]) % 12 for k in range(len(order_pcs))]
    comfy = {(1, 2): 4, (2, 3): 3, (3, 4): 2, (2, 4): 5, (1, 3): 7}
    for k in range(len(fs) - 1):
        a, b = fs[k], fs[k + 1]
        lim = comfy.get((a, b), 4)
        c += 0.6 * max(0, ivs[k] - lim)
    cross = ivs[-1]                             # last finger -> thumb, an octave on
    c += 0.5 * max(0, cross - 3) + (0.8 if cross > 5 else 0.0)
    if order_pcs[0] in BLACK:
        c += 1.5
    if 4 in fs:
        c += 0.3
    return c


def arpeggio_map(pcs_order, hand, vp):
    """
    Best cyclic finger map {pc: finger} for an arpeggio whose chord tones are
    pcs_order (rising, in the hand's own frame via vp). Every tone gets one
    finger in every octave; the thumb goes on one tone and the others rise
    2-3(-4) or 2-4 from it.
    """
    pcs = sorted(set(pcs_order))
    if len(pcs) not in (3, 4):
        return None
    # work in the right-hand frame: rising order after mirroring
    frame = sorted(pcs, key=lambda p: vp(p + 60))
    best = None
    for s in range(len(frame)):
        rot = frame[s:] + frame[:s]
        for fingers in ([1, 2, 3], [1, 2, 4]) if len(rot) == 3 else ([1, 2, 3, 4],):
            fmap = dict(zip(rot, fingers))
            # intervals in the frame (mirrored for the LH)
            order_frame = [vp(p + 60) % 12 for p in rot]
            c = _cyclic_cost(order_frame, dict(zip(order_frame, fingers)), True)
            if best is None or c < best[0]:
                best = (c, fmap)
    return best[1]


# ----------------------------------------------------------------- double notes
CHROM3_R = [(1, 3), (2, 4), (1, 3), (2, 4), (3, 5), (1, 3), (2, 4), (1, 3), (2, 4), (1, 3), (2, 4), (3, 5)]
CHROM3_L = [(4, 2), (3, 1), (5, 3), (4, 2), (3, 1), (4, 2), (3, 1), (4, 2), (3, 1), (5, 3), (4, 2), (3, 1)]
THIRDS_4GROUP = {('C', 'maj'): 'C', ('G', 'maj'): 'G', ('D', 'maj'): 'D', ('A', 'maj'): 'D',
                 ('E', 'maj'): 'A', ('F', 'maj'): 'F', ('Bb', 'maj'): 'C', ('Eb', 'maj'): 'F',
                 ('Ab', 'maj'): 'Bb', ('A', 'min'): 'A', ('D', 'min'): 'G', ('G', 'min'): 'C'}
T4R = [(1, 2), (1, 3), (2, 4), (3, 5)]
T3R = [(1, 3), (2, 4), (3, 5)]
T4L = [(5, 3), (4, 2), (3, 1), (2, 1)]
T3L = [(5, 3), (4, 2), (3, 1)]


_WI = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}
_BP = {1: 0.93, 3: 2.07, 6: 3.92, 8: 5.0, 10: 6.08}


def _kp(p):
    o, pc = divmod(p, 12)
    return o * 7 + (_WI[pc] + 0.5 if pc in _WI else _BP[pc])


def octave_pair(lo, hi, hand):
    """RH thumb below, 5 on top (4 on a black key); LH 5 below (4 on black), thumb on top."""
    four = PREFS.get("octaves", "4black") == "4black"
    if hand == RIGHT:
        return 1, (4 if four and hi % 12 in BLACK else 5)
    return (4 if four and lo % 12 in BLACK else 5), 1


# ----------------------------------------------------------------- detection
# (scales are suggested lightly: pianists finger short scale runs in real music more freely
#  than Hanon does - measured on the PIG dataset, see learn_weights.py)
W_SCALE, W_CHROM, W_ARP, W_OCT, W_THIRDS, W_SIXTHS, W_REP, W_TRILL = 0.75, 3.0, 2.5, 4.0, 3.0, 2.0, 1.5, 2.0
# a quick repeated pair before an outward leap (2-1 then 5)
W_REP_LEAP = 25.0
W_REP_RUN = 6.0             # runs of 3+ quick repeats (Hanon 44-47)
GAP = 0.6              # notes further apart than this don't belong to one figure


def _hist(notes, t0, t1):
    h = [0.0] * 12
    for n in notes:
        if n.end > t0 and n.start < t1:
            h[n.pitch % 12] += min(1.0, n.end - n.start) + 0.1
    return h


def detect(groups, hand, context=None, vpitch=None):
    """
    groups: [(start, [notes])] for one hand (as fingering.group_notes makes).
    context: all the song's notes (both hands), used to find keys.
    Returns {id(note): (finger, weight)}.
    """
    vp = vpitch or (lambda p: p)
    context = context if context is not None else [n for _, ns in groups for n in ns]
    out = {}

    def put(n, f, w):
        if isinstance(f, frozenset) or (f and 1 <= f <= 5):
            old = out.get(id(n))
            if old is None or w > old[1]:
                out[id(n)] = (f, w)

    # ---------------- single-note figures
    singles = []            # runs of consecutive single-note groups (no big time gaps)
    cur = []
    for gi, (t, ns) in enumerate(groups):
        if len(ns) == 1 and (not cur or t - groups[cur[-1]][0] <= GAP):
            cur.append(gi)
        else:
            if len(cur) > 1:
                singles.append(cur)
            cur = [gi] if len(ns) == 1 else []
    if len(cur) > 1:
        singles.append(cur)

    for run in singles:
        notes = [groups[g][1][0] for g in run]
        ps = [n.pitch for n in notes]
        ts = [n.start for n in notes]
        _repeated_and_trills(notes, ps, ts, hand, put)
        _stepwise(notes, ps, ts, hand, context, put)
        _arpeggios(notes, ps, hand, vp, put)
        _broken_octaves(notes, ps, hand, put)

    # ---------------- double notes
    dyads = []
    cur = []
    for gi, (t, ns) in enumerate(groups):
        if len(ns) == 2 and (not cur or t - groups[cur[-1]][0] <= GAP):
            cur.append(gi)
        else:
            if cur:
                dyads.append(cur)
            cur = [gi] if len(ns) == 2 else []
    if cur:
        dyads.append(cur)
    for run in dyads:
        pairs = [sorted(groups[g][1], key=lambda n: n.pitch) for g in run]
        _double_notes(pairs, hand, context, put)
    # octaves: in a passage of octaves the book's rule (1-5, 4 on black keys);
    # a lone octave just wants thumb and 4 or 5, whichever leads on best
    octs = []
    for gi, (t, ns) in enumerate(groups):
        if len(ns) >= 2:
            s = sorted(ns, key=lambda n: n.pitch)
            if s[-1].pitch - s[0].pitch == 12:
                octs.append((gi, s))
    in_run = set()
    for (g1, _), (g2, _) in zip(octs, octs[1:]):
        if g2 == g1 + 1 and groups[g2][0] - groups[g1][0] <= GAP:
            in_run |= {g1, g2}
    for gi, s in octs:
        w = W_OCT * (1.0 if len(s) == 2 else 0.6)
        a, b = octave_pair(s[0].pitch, s[-1].pitch, hand)
        if gi not in in_run and PREFS.get("octaves", "4black") == "4black":
            if hand == RIGHT:
                b = frozenset((4, 5))
            else:
                a = frozenset((4, 5))
        put(s[0], a, w)
        put(s[-1], b, w)
    return out


def _repeated_and_trills(notes, ps, ts, hand, put):
    n = len(ps)
    i = 0
    while i < n:
        # repeated notes, quick: change fingers 3-2-1
        j = i
        while j + 1 < n and ps[j + 1] == ps[i] and ts[j + 1] - ts[j] < 0.25:
            j += 1
        if j > i:
            L = j - i + 1
            style = PREFS.get("repeated", "321")
            if style == "321":
                cyc = [4, 3, 2, 1] if L % 4 == 0 and L % 3 != 0 else [3, 2, 1]
                if L == 2:
                    cyc = None
                    if ts[j] - ts[i] < 0.2:
                        # a quick pair changes fingers 2-1 (RH moving up / LH down
                        # onto the thumb). Before an outward leap (RH up, LH down)
                        # that's the idiom - 2-1 then 5 on the note beyond, the
                        # thumb spanning to it (La Campanella: 2-1-5) - so it's
                        # held firmly then; otherwise only when really quick.
                        nxt = ps[j + 1] if j + 1 < n and ts[j + 1] - ts[j] < 0.3 else None
                        out_ = None if nxt is None else (nxt - ps[j] if hand == RIGHT else ps[j] - nxt)
                        leap = out_ is not None and out_ >= 5
                        cyc = [2, 1] if leap or ts[j] - ts[i] < 0.15 else None
                        if leap:
                            for k, f in ((i, 2), (j, 1)):
                                put(notes[k], f, W_REP_LEAP)
            else:
                cyc = [int(c) for c in style]
            if cyc:
                # a run of repeats is a clear figure: its finger changes must beat
                # the planner's (learned) dislike of changing finger on one key
                w = W_REP_RUN if L >= 3 else W_REP
                if style != "321":
                    w *= 1.5                                        # the pianist asked for this one
                for k in range(L):
                    put(notes[i + k], cyc[k % len(cyc)], w)
            i = j + 1
            continue
        i += 1
    # trills: two neighbouring notes alternating quickly, 4+ times
    i = 0
    while i + 3 < n:
        a, b = ps[i], ps[i + 1]
        if 1 <= abs(a - b) <= 2 and ts[i + 1] - ts[i] < 0.2:
            j = i + 1
            while j + 1 < n and ps[j + 1] == ps[j - 1] and ts[j + 1] - ts[j] < 0.2:
                j += 1
            if j - i + 1 >= 6:                    # shorter shakes belong to the figure around them
                lo, hi = min(a, b), max(a, b)
                style = PREFS.get("trill", "auto")
                if style == "auto":
                    # any strong pair (1-2, 1-3, 2-3, 2-4) will do - just not the weak 4-5
                    low_f, high_f = frozenset((1, 2, 3)), frozenset((2, 3, 4))
                    if hand == LEFT:
                        low_f, high_f = high_f, low_f          # the LH's thumb is on top
                    for k in range(i, j + 1):
                        put(notes[k], low_f if ps[k] == lo else high_f, W_TRILL)
                else:
                    # the pianist's pattern: the thumb-side note takes the lower numbers
                    seq = [int(c) for c in style]
                    ev, od = seq[0::2], seq[1::2]
                    lower, upper = (ev, od) if min(ev) < min(od) else (od, ev)
                    thumb_side = lo if hand == RIGHT else hi
                    kl = ku = 0
                    for k in range(i, j + 1):
                        if ps[k] == thumb_side:
                            put(notes[k], lower[kl % len(lower)], W_TRILL * 1.5)
                            kl += 1
                        else:
                            put(notes[k], upper[ku % len(upper)], W_TRILL * 1.5)
                            ku += 1
                i = j + 1
                continue
        i += 1


def _stepwise(notes, ps, ts, hand, context, put):
    """Scales and chromatic runs."""
    n = len(ps)
    i = 0
    while i < n - 1:
        if not (1 <= abs(ps[i + 1] - ps[i]) <= 2):
            i += 1
            continue
        j = i + 1
        while j + 1 < n and 1 <= abs(ps[j + 1] - ps[j]) <= 2:
            j += 1
        seg = list(range(i, j + 1))
        i = j
        # the longest one-way stretch must go beyond a hand position
        best = cur = 1
        for k in range(1, len(seg) - 1):
            same = (ps[seg[k + 1]] - ps[seg[k]] > 0) == (ps[seg[k]] - ps[seg[k - 1]] > 0)
            cur = cur + 1 if same else 1
            best = max(best, cur)
        best += 1
        sp = [ps[k] for k in seg]
        # chromatic stretches: four or more semitone steps in a row
        chrom = [False] * len(seg)
        k = 0
        while k < len(seg) - 1:
            if abs(sp[k + 1] - sp[k]) == 1:
                m = k
                while m + 1 < len(seg) and sp[m + 1] - sp[m] == sp[k + 1] - sp[k]:
                    m += 1
                if m - k >= 4:
                    for q in range(k, m + 1):
                        chrom[q] = True
                k = max(m, k + 1)
            else:
                k += 1
        if any(chrom):
            cps = [sp[q] for q in range(len(seg)) if chrom[q]]
            cf = chromatic_fingering(cps, hand)
            it = iter(cf)
            for q in range(len(seg)):
                if chrom[q]:
                    put(notes[seg[q]], next(it), W_CHROM)
        if best < 6:
            continue                              # a five-finger pattern: leave it to the planner
        # diatonic stretches (the rest)
        blocks, b = [], []
        for q in range(len(seg)):
            if not chrom[q]:
                b.append(q)
            elif b:
                blocks.append(b)
                b = []
        if b:
            blocks.append(b)
        for b in blocks:
            if len(b) < 5:
                continue
            bp = [sp[q] for q in b]
            pcs = set(p % 12 for p in bp)
            t0, t1 = ts[seg[b[0]]], ts[seg[b[-1]]]
            hist = _hist(context, t0 - 2.0, t1 + 2.0)
            k = find_key(pcs, hist, bp[0] % 12, bp[-1] % 12)
            if not k or k[0] not in THUMBS:
                continue
            key, mel = k
            tu, td = thumb_sets(key, hand, mel)
            opening = b[0] == 0 or abs(sp[b[0]] - (ps[seg[b[0]] - 1] if seg[b[0]] > 0 else 999)) > 2
            fs = scale_fingering(bp, tu, hand, td, key_scale(key, mel), opening=opening)
            # a run that stops at its top (RH) / bottom (LH) and leaps away
            # ends on the next finger, not on a thumb out on its own
            nxt = seg[b[-1]] + 1
            ends_open = nxt >= n or abs(ps[nxt] - bp[-1]) > 2
            if ends_open and len(fs) > 1 and fs[-1] == 1 and fs[-2] in (3, 4):
                if (hand == RIGHT and bp[-1] > bp[-2]) or (hand == LEFT and bp[-1] < bp[-2]):
                    fs[-1] = fs[-2] + 1
            for q, f in zip(b, fs):
                put(notes[seg[q]], f, W_SCALE)


def _arpeggios(notes, ps, hand, vp, put):
    """Runs of chord tones rising or falling by thirds/fourths across more than an octave
    (close position; figures in sixths and wider are left to the planner)."""
    n = len(ps)
    i = 0
    while i < n - 2:
        j = i
        # thirds and fourths (and the step from a seventh to the root)
        while j + 1 < n and 1 <= abs(ps[j + 1] - ps[j]) <= 5 and \
                len(set(p % 12 for p in ps[i:j + 2])) <= 4 and \
                (abs(ps[j + 1] - ps[j]) >= 3 or len(set(p % 12 for p in ps[i:j + 2])) == 4):
            j += 1
        if j - i >= 3:
            seg = list(range(i, j + 1))
            sp = [ps[k] for k in seg]
            pcs = set(p % 12 for p in sp)
            ch = chord_of(pcs) if 3 <= len(pcs) <= 4 else None
            span = max(sp) - min(sp)
            if ch and span > 13:                  # an octave fits the hand: no crossing needed
                root, kind = ch
                if kind in ('dim7', 'aug', 'dim') and sp[0] % 12 in pcs:
                    root = sp[0] % 12                # symmetric chords: the root is where it starts
                fs = _arp_fingers(sp, hand, root, kind, pcs, vp)
                if fs:
                    for k, f in zip(seg, fs):
                        put(notes[k], f, W_ARP)
            i = j
        else:
            i += 1


def _arp_fingers(sp, hand, root, kind, pcs, vp):
    n = len(sp)
    fmap, start = None, None
    if kind in ('maj', 'min'):
        name = (MAJ_NAME if kind == 'maj' else MIN_NAME)[root]
        alt = {'Db': 'C#', 'C#': 'Db', 'F#': 'Gb', 'Eb': 'D#', 'Ab': 'G#', 'G#': 'Ab', 'Bb': 'A#'}
        key = (name, kind) if (name, kind) in ARP else (alt.get(name, name), kind)
        if key in ARP and sp[0] % 12 == root:
            rh, rs, lh, ls = ARP[key]
            fmap, start = (_fmap(rh), rs) if hand == RIGHT else (_fmap(lh), ls)
    if fmap is None and len(pcs) == 4 and sp[0] % 12 == root:
        order = sorted(pcs, key=lambda p: (p - root) % 12)
        m = {0: 1, 1: 2, 2: 3, 3: 4} if hand == RIGHT else {0: 1, 1: 4, 2: 3, 3: 2}
        fmap = {p: m[k] for k, p in enumerate(order)}
        start = 1 if hand == RIGHT else 5
    if fmap is None:
        fmap = arpeggio_map(sorted(pcs), hand, vp)
        if fmap is None:
            return None
    f = [fmap.get(p % 12) for p in sp]
    for i in range(n):
        peak = 0 < i < n - 1 and sp[i - 1] < sp[i] > sp[i + 1] or (i == n - 1 and n > 1 and sp[i] > sp[i - 1])
        low = 0 < i < n - 1 and sp[i - 1] > sp[i] < sp[i + 1] or (i == n - 1 and n > 1 and sp[i] < sp[i - 1])
        if hand == RIGHT and peak and f[i] == 1:
            f[i] = 5                               # top of the arpeggio: no thumb up there
        if hand == LEFT and low and f[i] == 1:
            f[i] = 5
    rising = n > 1 and sp[1] > sp[0]
    if start is not None and rising:
        f[0] = start
    elif n > 1 and f[0] == 1 and ((hand == RIGHT and not rising) or (hand == LEFT and rising)):
        f[0] = 5                                   # starting from the top (RH) / bottom (LH)
    return f


def _broken_octaves(notes, ps, hand, put):
    n = len(ps)

    def repeated(k):
        """Note k is one of a pair/run of quick repeats on one key."""
        return any(0 <= j < n and ps[j] == ps[k] for j in (k - 1, k + 1))

    for i, p in enumerate(ps):
        if repeated(i):
            continue            # quick repeats on one key: the repeated-note rule fingers them
        nb = [k for k in (i - 1, i + 1) if 0 <= k < n]
        if any(ps[k] - p == 12 for k in nb):
            role = 'low'
        elif any(p - ps[k] == 12 for k in nb):
            role = 'high'
        else:
            continue
        black = p % 12 in BLACK
        # after a finger change on a repeated note (2-1 in the RH) the thumb
        # spans the octave: the outer note takes 5
        partner_rep = any(abs(ps[k] - p) == 12 and repeated(k) for k in nb)
        outer = 5 if (partner_rep or not black) else 4
        if hand == RIGHT:
            f = 1 if role == 'low' else outer
        else:
            f = outer if role == 'low' else 1
        put(notes[i], f, W_OCT * 0.8)


def _double_notes(pairs, hand, context, put):
    """Sequences of two-note chords: thirds (scale / chromatic), sixths, fourths."""
    n = len(pairs)
    ivs = [b.pitch - a.pitch for a, b in pairs]
    lows = [a.pitch for a, b in pairs]
    # --- chromatic thirds: minor/major thirds whose lower note moves by semitones
    i = 0
    while i < n:
        if ivs[i] in (3, 4):
            j = i
            while (j + 1 < n and ivs[j + 1] in (3, 4) and abs(lows[j + 1] - lows[j]) == 1
                   and (j == i or lows[j + 1] - lows[j] == lows[j] - lows[j - 1])):
                j += 1
            if j - i >= 3:
                tab = CHROM3_R if hand == RIGHT else CHROM3_L
                for k in range(i, j + 1):
                    a, b = pairs[k]
                    fl, fh = tab[a.pitch % 12]
                    put(a, fl, W_THIRDS)
                    put(b, fh, W_THIRDS)
                i = j + 1
                continue
        i += 1
    # --- scales in thirds: thirds moving stepwise (diatonic) in one direction
    i = 0
    while i < n:
        if ivs[i] in (3, 4):
            j = i
            while (j + 1 < n and ivs[j + 1] in (3, 4) and 1 <= abs(lows[j + 1] - lows[j]) <= 2
                   and (j == i or (lows[j + 1] > lows[j]) == (lows[j] > lows[j - 1]))):
                j += 1
            if j - i >= 3:
                seg = range(i, j + 1)
                pcs = set(pairs[k][0].pitch % 12 for k in seg) | set(pairs[k][1].pitch % 12 for k in seg)
                hist = _hist(context, pairs[i][0].start - 2, pairs[j][0].start + 2)
                k = find_key(pcs, hist)
                if k and k[0][1] in ('maj', 'min'):
                    key = k[0]
                    up, _ = key_scale(key, False)
                    tonic = PC[key[0]]
                    scale = sorted(up, key=lambda pc: (pc - tonic) % 12)
                    g4 = scale.index(PC[THIRDS_4GROUP[key]]) if key in THIRDS_4GROUP else 0
                    for q in seg:
                        a, b = pairs[q]
                        pc = a.pitch % 12
                        if pc not in scale:
                            continue
                        kk = (scale.index(pc) - g4) % 7
                        fl, fh = ((T4R if hand == RIGHT else T4L)[kk] if kk < 4
                                  else (T3R if hand == RIGHT else T3L)[kk - 4])
                        put(a, fl, W_THIRDS * 1.7)     # the crossings this needs look
                        put(b, fh, W_THIRDS * 1.7)     # awkward note by note: insist
                i = j + 1
                continue
        i += 1
    # --- trills in thirds (two thirds alternating): the lower one 1-3, the
    # upper 2-4 (LH: upper 3-1, lower 4-2) - Chopin Op. 25 No. 6, Hanon 54
    i = 0
    while i + 3 < n:
        if ivs[i] in (3, 4) and ivs[i + 1] in (3, 4) and 1 <= abs(lows[i] - lows[i + 1]) <= 2:
            j = i + 1
            while j + 1 < n and lows[j + 1] == lows[j - 1] and ivs[j + 1] in (3, 4):
                j += 1
            if j - i + 1 >= 4:
                lo = min(lows[i], lows[i + 1])
                for k in range(i, j + 1):
                    a, b = pairs[k]
                    lower = a.pitch == lo
                    if hand == RIGHT:
                        fl, fh = (1, 3) if lower else (2, 4)
                    else:
                        fl, fh = (4, 2) if lower else (3, 1)
                    put(a, fl, W_THIRDS)
                    put(b, fh, W_THIRDS)
                i = j + 1
                continue
        i += 1
    # --- thirds inside one hand position (C/E D/F E/G D/F ...): the lowest
    # third 1-3, the next 2-4, the top one 3-5 (LH 5-3, 4-2, 3-1), as Hanon
    # prints them (Nos. 50, 54)
    pos = [(1, 3), (2, 4), (3, 5)] if hand == RIGHT else [(5, 3), (4, 2), (3, 1)]
    i = 0
    while i < n:
        if ivs[i] not in (3, 4):
            i += 1
            continue
        j = i
        lo_k = hi_k = _kp(lows[i])
        while j + 1 < n and ivs[j + 1] in (3, 4):
            k = _kp(lows[j + 1])
            if max(hi_k, k) - min(lo_k, k) > 2.2:
                break
            lo_k, hi_k = min(lo_k, k), max(hi_k, k)
            j += 1
        if j - i >= 2 and hi_k - lo_k >= 0.9:
            for q in range(i, j + 1):
                a, b = pairs[q]
                r = int(round(_kp(a.pitch) - lo_k))
                if 0 <= r <= 2:
                    put(a, pos[r][0], W_THIRDS * 0.7)
                    put(b, pos[r][1], W_THIRDS * 0.7)
        i = j + 1
    # --- trills in sixths / fourths: two dyads alternating
    i = 0
    while i + 3 < n:
        if ivs[i] in (5, 6, 8, 9) and ivs[i + 1] in (5, 6, 8, 9) and lows[i] != lows[i + 1] \
                and abs(lows[i] - lows[i + 1]) <= 2:
            j = i + 1
            while j + 1 < n and lows[j + 1] == lows[j - 1] and ivs[j + 1] == ivs[j - 1]:
                j += 1
            if j - i + 1 >= 4:
                lo = min(lows[i], lows[i + 1])
                for k in range(i, j + 1):
                    a, b = pairs[k]
                    lower = a.pitch == lo
                    if hand == RIGHT:
                        fl, fh = (1, 4) if lower else (2, 5)
                    else:
                        fl, fh = (5, 2) if lower else (4, 1)
                    put(a, fl, W_SIXTHS)
                    put(b, fh, W_SIXTHS)
                i = j + 1
                continue
        i += 1
    # --- other sixths: 1-5, 4 on a black key (RH); LH 5-1, 4 on black
    for a, b in pairs:
        if b.pitch - a.pitch in (8, 9):
            fl, fh = (1, 4 if b.pitch % 12 in BLACK else 5) if hand == RIGHT else (4 if a.pitch % 12 in BLACK else 5, 1)
            put(a, fl, W_SIXTHS)
            put(b, fh, W_SIXTHS)
