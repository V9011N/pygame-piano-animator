"""
combine.py - A second MIDI file brought into the open one (Open MIDI's menu).

    overlay   on top of it, from the start: each of the file's parts becomes a
              new track, each of its channels moves to a channel the project
              doesn't use, and every note and controller lands at the moment it
              plays in the file (its seconds, laid on this project's tempo).
    append    after it, on the same channels: the file's first note comes a gap
              of some beats after the project's music ends (its last note or
              pedal release); the file's tempo, meter and controller settings
              at that note come with it, then everything after it, beat for
              beat - so it plays as it did.

Both work inside the caller's transaction (one undo step), through put_note /
put_event; every note keeps its fingering.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from . import tempo as tempolib
from .document import DRUM_CHANNEL, NEW, Event, note_with, parse_marker, tempo_payload
from .smf import META, META_KEYSIG, META_TEXT, META_TIMESIG, META_TRACK_NAME, MIDI

TEXTS = (0x01, 0x05, 0x06, 0x07)            # text, lyric, marker, cue point: they come along when appending


def _is_channel_msg(e):
    """A controller, program, bend or pressure message (note-ons and -offs are notes already)."""
    return e.kind == MIDI and (e.payload[0] & 0xF0) not in (0x80, 0x90)


def _in_order(events):
    return sorted(events, key=lambda e: (e.tick, e.track, e.pos if e.pos >= 0 else 1 << 60, e.id))


def _channels(doc):
    """The channels a document has notes or controllers on."""
    return set(doc.channel_counts()) | {e.payload[0] & 0x0F for e in doc.events.values() if _is_channel_msg(e)}


def _name_of(doc, ti):
    """A track's name as its file has it (bytes), or None."""
    for e in _in_order(e for e in doc.events.values() if e.track == ti and e.kind == META):
        if e.payload[0] == META_TRACK_NAME and e.payload[1:].strip():
            return e.payload[1:]
    return None


def _busiest_track(notes, channel):
    c = Counter(n.track for n in notes if n.channel == channel)
    return c.most_common(1)[0][0] if c else None


# --------------------------------------------------------------------------- #
# On top
# --------------------------------------------------------------------------- #
def overlay(doc, src, name):
    """
    `src` on top of `doc` from the start (inside the caller's transaction). Returns {"ids": the new notes' ids,
    "channels": {src channel: doc channel}, "tracks": the new tracks}, or None when src has no notes. Raises
    ValueError when doc hasn't channels enough left for src's.
    """
    if not src.notes:
        return None
    used = _channels(doc)
    want = sorted(_channels(src))
    cmap = {}
    if DRUM_CHANNEL in want and DRUM_CHANNEL not in used:
        cmap[DRUM_CHANNEL] = DRUM_CHANNEL                    # (drums stay drums)
    free = [c for c in range(16) if c not in used and c != DRUM_CHANNEL]
    rest = [c for c in want if c not in cmap]
    if len(rest) > len(free):
        raise ValueError(f"it needs {len(rest)} free channel{'s' if len(rest) != 1 else ''} and this project has "
                         f"{len(free)} left")
    cmap.update(zip(rest, free))
    note_tracks = sorted({n.track for n in src.notes.values()})
    tmap = {}
    for k, t in enumerate(note_tracks):
        tmap[t] = doc.add_track(_name_of(src, t) or (name if len(note_tracks) == 1 else f"{name} {k + 1}"))
    by_channel = {c: tmap.get(_busiest_track(src.notes.values(), c), tmap[note_tracks[0]]) for c in want}
    st, dt = src.tempo_map(), doc.tempo_map()

    def when(ticks):                         # src's ticks -> its seconds -> doc's ticks
        return np.rint(dt.to_ticks(st.to_seconds(np.asarray(ticks, np.int64)))).astype(np.int64)

    notes = sorted(src.notes.values(), key=lambda n: (n.start, n.track, n.pitch, n.id))
    a = np.fromiter((n.start for n in notes), np.int64, len(notes))
    b = np.fromiter((n.end for n in notes), np.int64, len(notes))
    starts = when(a)
    ends = np.maximum(when(b), starts + 1)          # (a new note needs a tick: its note-off would go first)
    ids = []
    for n, s, e in zip(notes, starts.tolist(), ends.tolist()):
        nn = note_with(n, id=doc.new_id(), track=tmap[n.track], channel=cmap[n.channel], start=s, end=e,
                       on_pos=NEW, off_pos=NEW)
        doc.put_note(nn)
        ids.append(nn.id)
    evs = _in_order(e for e in src.events.values() if _is_channel_msg(e))
    for e, t in zip(evs, when([e.tick for e in evs]).tolist() if evs else ()):
        ch = e.payload[0] & 0x0F
        doc.put_event(Event(doc.new_id(), tmap[e.track] if e.track in tmap else by_channel[ch], t, MIDI,
                            bytes([(e.payload[0] & 0xF0) | cmap[ch]]) + e.payload[1:]))
    return {"ids": ids, "channels": cmap, "tracks": sorted(tmap.values())}


# --------------------------------------------------------------------------- #
# Appended
# --------------------------------------------------------------------------- #
def music_end(doc):
    """The tick the project's music ends at: its last note's end, or a pedal event after it (a release)."""
    ix = doc.index()
    end = int(ix.end.max()) if len(ix.end) else 0
    for ticks, _, _ in doc.pedal_series().values():
        if len(ticks):
            end = max(end, int(ticks[-1]))
    return end


def append_point(doc, gap_beats):
    """Where appended music's first note goes: `gap_beats` beats (the meter's, there) after the music ends."""
    end = music_end(doc)
    return max(0, int(round(end + gap_beats * doc.meter_map().beat_ticks(end))))


def gap_to_next_bar(doc):
    """Beats from the end of the music to the next bar line (0 when it ends on one)."""
    end = music_end(doc)
    mm = doc.meter_map()
    into = end - mm.bar_start(end)
    return 0.0 if into < 1 else (mm.bar_ticks(end) - into) / mm.beat_ticks(end)


def _key_at(doc, tick):
    sigs = [e for e in _in_order(e for e in doc.events.values() if e.kind == META and e.payload[0] == META_KEYSIG)
            if e.tick <= tick]
    return sigs[-1].payload[1:3] if sigs else bytes(2)


def append(doc, src, gap_beats):
    """
    `src` after `doc`'s music (inside the caller's transaction): on the channels doc already uses (each of src's
    channels stays itself if doc has notes on it, else goes to doc's main channel), its parts in doc's (track for
    track when both have as many parts, else the track of the channel), its first note `gap_beats` beats after the
    music ends. Returns {"ids", "start" (the first new note's tick), "channels" {src: doc}, "tempo" (the BPM set
    at the start, or None)}, or None when src has no notes. Raises ValueError for SMPTE-timed files (no beats).
    """
    if not src.notes:
        return None
    if doc.division & 0x8000 or src.division & 0x8000:
        raise ValueError("a file that counts time in SMPTE frames has no beats to append by")
    scale = doc.ppq / src.ppq
    first = min(n.start for n in src.notes.values())
    at = append_point(doc, gap_beats)
    shift = at - int(round(first * scale))

    def place(ticks):                        # beat for beat, src's first note at `at`
        return np.maximum(np.rint(np.asarray(ticks, np.float64) * scale).astype(np.int64) + shift, at)

    counts = doc.channel_counts()
    main = counts.most_common(1)[0][0] if counts else 0
    cmap = {c: (c if c in counts else main) for c in sorted(_channels(src))}
    mine = sorted({n.track for n in doc.notes.values()})
    theirs = sorted({n.track for n in src.notes.values()})
    tmap = dict(zip(theirs, mine)) if len(theirs) == len(mine) else {}
    by_channel = {c: doc.track_for_channel(c) for c in set(cmap.values())}
    conductor = doc.tempo_track()
    keys = tempolib.keyframes(src)
    tempo_here = tempolib.bpm_at(doc, at)
    sig_here = doc.meter_map().signature_at(at)
    key_here = _key_at(doc, at)

    def track(src_track, ch):
        return tmap[src_track] if src_track in tmap else by_channel[ch]

    notes = sorted(src.notes.values(), key=lambda n: (n.start, n.track, n.pitch, n.id))
    a = np.fromiter((n.start for n in notes), np.int64, len(notes))
    b = np.fromiter((n.end for n in notes), np.int64, len(notes))
    starts = place(a)
    ends = np.maximum(place(b), starts + 1)
    ids = []
    for n, s, e in zip(notes, starts.tolist(), ends.tolist()):
        ch = cmap[n.channel]
        nn = note_with(n, id=doc.new_id(), track=track(n.track, ch), channel=ch, start=s, end=e,
                       on_pos=NEW, off_pos=NEW)
        doc.put_note(nn)
        ids.append(nn.id)

    out = []                                 # (src tick, doc track, kind, payload), put in this order
    # what is in force at the first note: the last of each kind before it (MIDI's 4/4, C major if none), at `at`
    lead, texts = {}, []
    if not any(e.tick == first for e in src.events.values() if e.is_timesig):
        lead["sig"] = Event(0, 0, 0, META, bytes([META_TIMESIG, 4, 2, 24, 8]))
    if not any(e.tick == first and e.payload[0] == META_KEYSIG for e in src.events.values() if e.kind == META):
        lead["key"] = Event(0, 0, 0, META, bytes([META_KEYSIG, 0, 0]))
    for e in _in_order(e for e in src.events.values() if e.tick < first):
        p = e.payload
        if _is_channel_msg(e) and (p[0] & 0xF0) != 0xA0:
            lead[("ch", cmap[p[0] & 0x0F], p[0] & 0xF0, p[1] if (p[0] & 0xF0) == 0xB0 else None)] = e
        elif e.is_timesig:
            lead["sig"] = e
        elif e.kind == META and p[0] == META_KEYSIG and len(p) >= 3:
            lead["key"] = e
        elif e.kind == META and p[0] in TEXTS and not (p[0] == META_TEXT and parse_marker(p[1:]) is not None):
            texts.append(e)
    for kind, e in lead.items():
        if kind == "sig":
            if (e.payload[1], 2 ** e.payload[2]) != sig_here:
                out.append((first, conductor, META, e.payload))
        elif kind == "key":
            if e.payload[1:3] != key_here:
                out.append((first, conductor, META, e.payload))
        else:
            ch = cmap[e.payload[0] & 0x0F]
            out.append((first, track(e.track, ch), MIDI, bytes([(e.payload[0] & 0xF0) | ch]) + e.payload[1:]))
    out += [(first, conductor, META, e.payload) for e in texts]
    # the tempo at the first note (and the curve it's on) when it isn't the project's there; src's keyframes after
    tempo = None
    i = tempolib.governing(keys, first)
    bpm = tempolib.bpm_at(src, first)
    mode = keys[i].mode if 0 <= i < len(keys) - 1 else tempolib.CONSTANT
    if mode != tempolib.CONSTANT or abs(bpm - tempo_here) > 1e-6:
        out.append((first, conductor, META, tempo_payload(bpm)))
        if mode != tempolib.CONSTANT:
            out.append((first, conductor, META, tempolib.marker_payload(mode)))
        tempo = bpm
    for k in keys:
        if k.tick > first:
            out.append((k.tick, conductor, META, k.event.payload))
            if k.marker is not None:
                out.append((k.tick, conductor, META, k.marker.payload))
    # everything else from the first note on
    for e in _in_order(e for e in src.events.values() if e.tick >= first):
        p = e.payload
        if _is_channel_msg(e):
            ch = cmap[p[0] & 0x0F]
            out.append((e.tick, track(e.track, ch), MIDI, bytes([(p[0] & 0xF0) | ch]) + p[1:]))
        elif e.is_timesig or (e.kind == META and p[0] == META_KEYSIG) or \
                (e.kind == META and p[0] in TEXTS and not (p[0] == META_TEXT and parse_marker(p[1:]) is not None)):
            out.append((e.tick, conductor, META, p))
    if out:
        for (_, t, kind, payload), tick in zip(out, place([o[0] for o in out]).tolist()):
            doc.put_event(Event(doc.new_id(), t, tick, kind, payload))
    return {"ids": ids, "start": at, "channels": cmap, "tempo": tempo}
