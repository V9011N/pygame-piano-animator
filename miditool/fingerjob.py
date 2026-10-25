"""
fingerjob.py - Hand-thesia's fingering, worked out for the editor.

`suggest` runs what Hand-thesia does when it opens a file (midi_loader's hands,
then each HandAnimator's planning): which hand plays each note - a fingering
marker's hand, else the track's (by name, or two tracks in different
registers) where that hand can keep up, else the hand split's - then each
hand's glissandos, finger slips and the planner, honouring every finger the
file (or you) already gave. It runs in a worker process (jobs.py) on plain
tuples, so it never holds up the window.

`resolve` re-fingers a few notes moved to a hand, everything around them
fixed (Hand-thesia's FingeringEditor._resolve).

Every job takes the active pianist with the active fingering profile on top
(profiles.ProfileStore.planner_input). Nothing here changes the document: what comes
back is a suggestion until it's applied.
"""
from __future__ import annotations

import re

import fingering as fg
import glissando
import hand_split

from . import profiles

LEFT, RIGHT = "L", "R"
RESOLVE_PAD = 3.0            # s of fixed context around re-fingered notes (Hand-thesia's editor)

PIANO_PROGRAMS = range(0, 8)
_PIANO_WORDS = ("piano", "klavier", "pianoforte", "solo", "keyboard", "clavier", "cembalo",
                "harpsichord", "right hand", "left hand", "rh", "lh", "pf", "pno")
_OTHER_WORDS = ("violin", "violini", "viola", "viole", "cello", "violoncell", "contrabass", "double bass",
                "bass", "flute", "flaut", "oboe", "oboi", "clarinet", "clarinett", "bassoon", "fagott",
                "horn", "corni", "corno", "trumpet", "tromba", "trombe", "trombone", "tuba", "timpani",
                "timp", "drum", "percussion", "strings", "string", "choir", "voice", "vocal", "soprano",
                "alto", "tenor", "guitar", "harp", "organ", "orchestra", "tutti", "ensemble")


class PNote:
    """A note as the planner reads it (Hand-thesia's midi_loader.Note fields), in seconds."""
    __slots__ = ("id", "pitch", "start", "end", "velocity", "track", "hand", "finger", "gliss")

    def __init__(self, nid, pitch, start, end, velocity, track, hand, finger, gliss):
        self.id, self.pitch, self.start, self.end = nid, pitch, start, end
        self.velocity, self.track, self.hand, self.finger, self.gliss = velocity, track, hand, finger, gliss

    @property
    def duration(self):
        return self.end - self.start

    @property
    def is_black(self):
        return self.pitch % 12 in (1, 3, 6, 8, 10)


def hand_from_name(name):
    """'Right Hand', 'RH', 'Piano L', 'Left'... -> R / L / None (Hand-thesia's midi_loader._hand_from_name)."""
    n = (name or "").lower().replace("-", " ").replace("_", " ")
    words = n.split()
    if "right" in n or "rh" in words or "r" in words or "treble" in n:
        return RIGHT
    if "left" in n or "lh" in words or "l" in words or "bass" in n:
        return LEFT
    return None


def is_piano_part(name, program):
    """A piano part: a piano program and no other instrument's name, or a piano-ish name (Hand-thesia's rule)."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    text = " ".join(words)
    named_piano = any(w in words or (" " in w and w in text) for w in _PIANO_WORDS)
    named_other = any(any(word.startswith(o) for word in words) for o in _OTHER_WORDS if " " not in o) or \
        any(o in text for o in _OTHER_WORDS if " " in o)
    if named_piano:
        return True
    return program in PIANO_PROGRAMS and not named_other


def piano_tracks(tracks):
    """
    Which tracks get fingering: tracks = {index: (name, program, note count)}. When there are piano parts
    and other instruments (a concerto's score), only the piano parts; else every track.
    """
    with_notes = {i: t for i, t in tracks.items() if t[2]}
    piano = {i for i, (name, prog, _) in with_notes.items() if is_piano_part(name, prog)}
    return piano if piano and len(piano) < len(with_notes) else set(with_notes)


def track_hands(tracks, mean_pitch):
    """{track: hand} from the tracks' names (all of them named), else two tracks in different registers."""
    named = {i: hand_from_name(t[0]) for i, t in tracks.items()}
    if named and all(named.values()):
        return named
    if len(tracks) == 2:
        a, b = sorted(tracks)
        if abs(mean_pitch[a] - mean_pitch[b]) >= 7:
            return {a: RIGHT, b: LEFT} if mean_pitch[a] >= mean_pitch[b] else {a: LEFT, b: RIGHT}
    return {}


def suggest(rows, tracks, planner=None):
    """
    rows: [(id, pitch, start s, end s, velocity, track, hand, finger, gliss)] (hand/finger/gliss: the
    note's own fingering, from the file or the editor). tracks: {index: (name, program, note count)}.
    Returns {id: (hand, finger or None, gliss)} for every note fingered: a note's own hand and finger are
    kept as they are; what it lacks is worked out.
    """
    pianist = profiles.prepare(planner)
    keep = piano_tracks(tracks)
    notes = [PNote(*r) for r in rows if r[5] in keep]
    if not notes:
        return {}
    by_track = {}
    for n in notes:
        by_track.setdefault(n.track, []).append(n.pitch)
    playing = {i: tracks[i] for i in by_track}
    mean = {i: sum(ps) / len(ps) for i, ps in by_track.items()}
    t_hand = track_hands(playing, mean)

    # hands: a marker's hand is fixed; the rest follow the track where that hand can keep up
    loose = [n for n in notes if not n.hand]
    if loose:
        hands = hand_split.split_hands(loose, pianist=pianist,
                                       prefer={id(n): t_hand[n.track] for n in loose if n.track in t_hand})
        for n in loose:
            n.hand = hands.get(id(n)) or (LEFT if n.pitch < 60 else RIGHT)

    out = {}
    for hand in (RIGHT, LEFT):
        hn = sorted((n for n in notes if n.hand == hand), key=lambda n: (n.start, n.pitch))
        if not hn:
            continue
        runs = glissando.find(hn, pianist)                 # marked ones, and detected
        slid = {id(n) for r in runs for n in r}
        rest = [n for n in hn if id(n) not in slid]
        vp = fg.mirror_pitch if hand == LEFT else None
        slips = fg.find_slips(rest, vp, given=lambda n: n.finger)
        core = [n for n in rest if id(n) not in slips]
        plan = fg.plan_fingering(fg.group_notes(core, vpitch=vp), vp, hand=hand, context=notes, repair=False,
                                 pianist=pianist)
        for n in rest:
            if id(n) in slips:
                f = plan.get(id(slips[id(n)]))
                if f:
                    plan[id(n)] = f
        for n in hn:
            if id(n) in slid:
                out[n.id] = (hand, None, True)
            else:
                out[n.id] = (hand, n.finger or plan.get(id(n)), False)
    return out


def resolve(rows, free, hand, planner=None):
    """
    Fingers for the notes `free` (ids), all now in `hand`, every other note's finger fixed as given.
    rows: like suggest's, every note of the song (the context) with hand/finger as they stand (suggested
    fingers included). Returns {id: finger}.
    """
    pianist = profiles.prepare(planner)
    notes = [PNote(*r) for r in rows]
    mine = [n for n in notes if n.id in free]
    if not mine:
        return {}
    t0 = min(n.start for n in mine) - RESOLVE_PAD
    t1 = max(n.end for n in mine) + RESOLVE_PAD
    window = []
    for n in notes:
        if n.hand != hand or n.end < t0 or n.start > t1:
            continue
        if n.id in free:
            n.finger = None
        window.append(n)
    vp = fg.mirror_pitch if hand == LEFT else None
    result = fg.plan_with_slips(window, vp, hand=hand, context=notes, pianist=pianist)
    return {n.id: result.get(id(n)) for n in window if n.id in free}


def difficulty(rows, hand, planner=None):
    """{id: cost} of a hand's fingering as it stands (Hand-thesia's score_fingering)."""
    pianist = profiles.prepare(planner)
    notes = [PNote(*r) for r in rows]
    hn = [n for n in notes if n.hand == hand and n.finger]
    if not hn:
        return {}
    vp = fg.mirror_pitch if hand == LEFT else None
    fixed = {id(n): n.finger for n in hn}
    costs = fg.score_fingering(fg.group_notes(hn, vpitch=vp), fixed, vp, hand, context=notes, pianist=pianist)
    return {n.id: costs.get(id(n), 0.0) for n in hn}
