"""
tempo.py - Tempo keyframes and the curves between them.

The tempo map is a list of keyframes - the file's tempo events - each saying
how the tempo gets from it to the next one: constant (MIDI's own step), a
linear ramp, or a cosine sweep (eased in and out of the change). A MIDI file
can only hold steps, so a ramp is written as tempo changes every 32nd note
along the curve, with a sequencer-specific meta event at its keyframe that
says what they are (and where the ramp ends). Reading the file back, those
steps are recognised and dropped - the ramp is a ramp again; any other program
simply plays the steps.

In the document a ramp is just its marker event (mode only, no end): the steps
are worked out from the keyframes whenever they're needed - the tempo map
(playback, seconds) and the exported file - so both always agree.

Baking (bake) leaves the piece one tempo: every tempo event and ramp marker
goes, and the notes either keep their ticks (their bars and beats) or move to
wherever keeps them sounding when they did.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import numpy as np

from .smf import META, META_TEMPO

CONSTANT, LINEAR, COSINE = 0, 1, 2
MODES = (CONSTANT, LINEAR, COSINE)
MODE_NAMES = {CONSTANT: "Constant", LINEAR: "Linear", COSINE: "Cosine sweep"}
MODE_SHORT = {CONSTANT: "Const", LINEAR: "Linear", COSINE: "Cosine"}
META_SEQ = 0x7F                  # sequencer-specific meta event
TAG = b"\x7dMTr"                 # 0x7D (the non-commercial manufacturer id) + "MTr": a MIDI Tool tempo ramp
STEP_DIV = 8                     # a ramp's steps come every ppq / STEP_DIV ticks (32nd notes)
MIN_BPM, MAX_BPM = 5.0, 960.0


def marker_payload(mode):
    """A ramp marker as the document keeps it (type byte first, no end tick)."""
    return bytes([META_SEQ]) + TAG + bytes([1, int(mode)])


def file_marker_payload(mode, end):
    """A ramp marker as the file carries it: with the tick the ramp ends at."""
    return marker_payload(mode) + int(end).to_bytes(4, "big")


def parse_marker(payload):
    """(mode, end tick or None) for a ramp marker's payload (meta type byte first), else None."""
    if len(payload) >= 7 and payload[0] == META_SEQ and payload[1:5] == TAG and payload[5] == 1 \
            and payload[6] in MODES:
        end = int.from_bytes(payload[7:11], "big") if len(payload) >= 11 else None
        return payload[6], end
    return None


def interp(b0, b1, u, mode):
    """The tempo a share u (0..1) of the way from b0 to b1."""
    u = min(1.0, max(0.0, u))
    if mode == LINEAR:
        return b0 + (b1 - b0) * u
    if mode == COSINE:
        return b0 + (b1 - b0) * (1.0 - math.cos(math.pi * u)) / 2.0
    return b0


def us_of(bpm):
    return max(1, min(0xFFFFFF, int(round(60e6 / bpm))))


def bpm_of(us):
    return 60e6 / us


@dataclass(frozen=True)
class Key:
    tick: int
    bpm: float
    mode: int
    event: object                # the tempo Event
    marker: object = None        # its ramp marker Event (None: constant)


def keyframes(doc):
    """[Key] in tick order (one per tick: the last tempo event at a tick wins, as in the tempo map)."""
    def make():
        markers = {}
        for e in doc.events.values():
            if e.kind == META and parse_marker(e.payload) is not None:
                markers[(e.track, e.tick)] = e
        keys = {}
        for e in doc.tempo_events():
            m = markers.get((e.track, e.tick))
            mode = parse_marker(m.payload)[0] if m is not None else CONSTANT
            keys[e.tick] = Key(e.tick, bpm_of(e.tempo_us), mode, e, m)
        return [keys[t] for t in sorted(keys)]
    return doc._cached("tempo_keys", doc.timing_rev, make)


def ramp_steps(k0, k1, ppq):
    """[(tick, microseconds per quarter)]: the steps a ramp from k0 to k1 is written as (k0's own event aside)."""
    if k0.mode == CONSTANT or k1.tick <= k0.tick or abs(k1.bpm - k0.bpm) < 1e-9:
        return []
    step = max(1, ppq // STEP_DIV)
    span = k1.tick - k0.tick
    out = []
    last = us_of(k0.bpm)
    t = k0.tick + step
    while t < k1.tick:
        mid = (t + min(t + step, k1.tick)) / 2.0            # the step plays the curve's value at its middle
        us = us_of(interp(k0.bpm, k1.bpm, (mid - k0.tick) / span, k0.mode))
        if us != last:
            out.append((t, us))
            last = us
        t += step
    return out


def expanded(doc):
    """[(tick, microseconds per quarter)]: every keyframe and every ramp step - what is played and exported."""
    keys = keyframes(doc)
    out = []
    for i, k in enumerate(keys):
        out.append((k.tick, us_of(k.bpm)))
        if i + 1 < len(keys):
            out += ramp_steps(k, keys[i + 1], doc.ppq)
    return out


def governing(keys, tick):
    """Index of the keyframe in force at `tick` (the last at or before it), or -1."""
    return bisect.bisect_right([k.tick for k in keys], tick) - 1


DEFAULT_BPM = 120.0             # (MIDI's tempo until the first tempo event)


def bpm_at(doc, tick):
    """The tempo the curve gives at `tick` (between a ramp's steps, the smooth value)."""
    keys = keyframes(doc)
    i = governing(keys, tick)
    if i < 0:
        return DEFAULT_BPM
    k = keys[i]
    if i + 1 < len(keys) and k.mode != CONSTANT:
        n = keys[i + 1]
        return interp(k.bpm, n.bpm, (tick - k.tick) / max(1, n.tick - k.tick), k.mode)
    return k.bpm


def segment_points(doc, t0, t1, ticks_per_sample):
    """[(tick, bpm)] along the curve over t0..t1, sampled every ticks_per_sample within ramps (for drawing)."""
    keys = keyframes(doc)
    if not keys:
        return [(t0, DEFAULT_BPM), (t1, DEFAULT_BPM)]
    pts = []
    first = max(0, governing(keys, t0))
    if keys[0].tick > t0:
        pts += [(t0, DEFAULT_BPM), (keys[0].tick, DEFAULT_BPM)]
    for i in range(first, len(keys)):
        k = keys[i]
        if k.tick > t1:
            break
        nxt = keys[i + 1] if i + 1 < len(keys) else None
        a = max(k.tick, t0) if i == first else k.tick
        b = min(nxt.tick, t1) if nxt is not None else t1
        if nxt is None or k.mode == CONSTANT:
            pts += [(a, k.bpm), (b, k.bpm)]
            if nxt is not None and nxt.tick <= t1:
                pts.append((nxt.tick, nxt.bpm))
            continue
        n = max(2, int((b - a) / max(1e-9, ticks_per_sample)) + 1)
        for j in range(n + 1):
            t = a + (b - a) * j / n
            pts.append((t, interp(k.bpm, nxt.bpm, (t - k.tick) / max(1, nxt.tick - k.tick), k.mode)))
    return pts


# --------------------------------------------------------------------------- #
# Baking: one tempo for the whole piece
# --------------------------------------------------------------------------- #
def is_tempo_data(e):
    """Whether an event tells a player anything about tempo: a tempo event (of any length) or a ramp marker."""
    return e.kind == META and len(e.payload) > 0 and \
        (e.payload[0] == META_TEMPO or parse_marker(e.payload) is not None)


def average_bpm(doc):
    """The one tempo that keeps the piece as long as it is: its beats over its minutes (to the last event)."""
    end = doc.end_tick()
    secs = doc.tempo_map().sec(end)
    return end / doc.ppq / (secs / 60.0) if end > 0 and secs > 0 else bpm_at(doc, 0)


def bake(doc, bpm, keep_timing=False):
    """
    One tempo for the whole piece, inside the caller's transaction: every tempo event (any track, any length)
    and every ramp marker goes, leaving a single `bpm` tempo event at tick 0. The notes keep their ticks - their
    bars and beats - and play at `bpm` throughout; or, keep_timing, every note, every other event and each track's
    end moves to the tick that sounds at the moment it did. Returns that move's tick maps (forward: old -> new,
    back: new -> old), or None when nothing moved.
    """
    from .document import Event, event_with, note_with, tempo_payload
    if doc.division & 0x8000:
        raise ValueError("an SMPTE-timed file counts frames, not beats: it has no tempo to bake")
    tm = doc.tempo_map()                         # (the timing being baked in)
    track = doc.tempo_track()
    keep = next((e for e in doc.tempo_events() if e.tick == 0), None)
    for e in [e for e in doc.events.values() if is_tempo_data(e)]:
        if keep is None or e.id != keep.id:
            doc.remove_event(e.id)
    if keep is not None:
        doc.put_event(event_with(keep, payload=tempo_payload(bpm)))            # (where the file had it)
    else:
        doc.put_event(Event(doc.new_id(), track, 0, META, tempo_payload(bpm)))
    if not keep_timing:
        return None
    k = bpm / 60.0 * doc.ppq                     # ticks a second at the new tempo

    def moved(ticks):
        return np.rint(tm.to_seconds(np.asarray(ticks, np.int64)) * k).astype(np.int64)

    # (each keeps its place in its track: the move keeps the order of everything there)
    notes = list(doc.notes.values())
    if notes:
        starts = np.fromiter((n.start for n in notes), np.int64, len(notes))
        ends = np.fromiter((n.end for n in notes), np.int64, len(notes))
        a = moved(starts)
        b = np.maximum(moved(ends), a + (ends > starts))             # (a note keeps some length)
        for n, s, e in zip(notes, a.tolist(), b.tolist()):
            if s != n.start or e != n.end:
                doc.put_note(note_with(n, start=s, end=e, on_pos=n.on_pos, off_pos=n.off_pos))
    evs = list(doc.events.values())
    for ev, t in zip(evs, moved([ev.tick for ev in evs]).tolist()):
        if t != ev.tick:
            doc.put_event(event_with(ev, tick=t, pos=ev.pos))
    for ti, info in enumerate(doc.tracks):
        doc.set_track_end(ti, int(moved([info.end_tick])[0]))
    return (lambda t: tm.sec(t) * k), (lambda t: tm.tick(t / k))
