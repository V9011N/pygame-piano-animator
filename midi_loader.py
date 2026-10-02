"""
midi_loader.py - Load a MIDI file into a time-indexed list of piano notes.

Built on pretty_midi (pip install pretty_midi). pretty_midi pairs note_on and
note_off events and applies the tempo map, so every time here is in seconds.

Typical use:

    from midi_loader import load_midi
    song = load_midi("song.mid")
    for note in song.notes_between(t, t + 3.0):   # notes visible in the next 3 s
        ...
    pressed = song.active_notes(t)                # notes sounding right now

Run this file directly to print a summary:  python midi_loader.py song.mid
"""
from __future__ import annotations

import bisect
import os
import struct
import sys
from dataclasses import dataclass, replace
from typing import List, Optional

import pretty_midi

LOWEST_PIANO_KEY = 21    # A0
HIGHEST_PIANO_KEY = 108  # C8
MIDDLE_C = 60

LEFT = "L"
RIGHT = "R"

SUSTAIN, SOSTENUTO, SOFT = 64, 66, 67
PEDALS = (SUSTAIN, SOSTENUTO, SOFT)

_BLACK_PITCH_CLASSES = {1, 3, 6, 8, 10}
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def is_black_key(pitch: int) -> bool:
    """True for the sharps and flats (the black keys)."""
    return pitch % 12 in _BLACK_PITCH_CLASSES


def note_name(pitch: int) -> str:
    """60 -> 'C4'."""
    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


@dataclass(frozen=True)
class Note:
    pitch: int        # MIDI note number, 21..108 on an 88-key piano
    start: float      # seconds
    end: float        # seconds
    velocity: int     # 1..127
    track: int        # index of the instrument/track it came from
    hand: str         # LEFT or RIGHT (from track names/registers, else hand_split.py)
    finger: Optional[int] = None   # 1..5 when the file carries fingering (see below)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_black(self) -> bool:
        return is_black_key(self.pitch)

    @property
    def name(self) -> str:
        return note_name(self.pitch)


@dataclass(frozen=True)
class TrackInfo:
    index: int
    name: str
    program: int              # General MIDI program (0 = acoustic grand)
    note_count: int
    hand: Optional[str]       # LEFT/RIGHT if the whole track is one hand, else None


def pedal_switches(controls):
    """[(time, controller, 0 | 127)]: each pedal as an on/off switch (on at 64+), changes only."""
    out, state = [], {}
    for t, c, v in controls:
        on = 127 if v >= 64 else 0
        if state.get(c, 0) != on:
            state[c] = on
            out.append((t, c, on))
    return out


class MidiSong:
    """A loaded song: sorted notes plus fast queries by time."""

    def __init__(self, notes: List[Note], tracks: List[TrackInfo], duration: float,
                 bar_times: List[float], beat_times: List[float], path: Optional[str] = None,
                 controls: Optional[list] = None):
        self.notes: List[Note] = sorted(notes, key=lambda n: (n.start, n.pitch))
        # pedals: [(time, controller, value)] for sustain (64), sostenuto (66), soft (67).
        # Performance recordings (MAESTRO, Disklavier) send the pedal's exact
        # position many times a second, and a pianist's resting foot often
        # hovers just under halfway (values in the 40s-50s). Synths that do
        # half-pedalling then keep the dampers partly lifted - the notes ring on
        # while the pedal reads as up. So the pedals are played as switches
        # (the MIDI standard's 64 threshold, the same the indicator uses): 127
        # or 0, sent only when that changes. The file's raw values are kept in
        # raw_controls.
        self.raw_controls = sorted(controls or [], key=lambda c: (c[0], c[1]))
        self.controls = pedal_switches(self.raw_controls)
        self._control_times = [c[0] for c in self.controls]
        self.tracks = tracks
        self.duration = duration
        self.bar_times = list(bar_times)
        self.beat_times = list(beat_times)
        self.path = path
        self.title = os.path.splitext(os.path.basename(path))[0] if path else "Untitled"
        self.cleanup: List[str] = []           # what load_midi's sanitizing changed

        self._starts = [n.start for n in self.notes]
        # Longest note lets us bound how far back an overlapping note can start.
        self._max_duration = max((n.duration for n in self.notes), default=0.0)

    def __len__(self) -> int:
        return len(self.notes)

    def notes_between(self, t0: float, t1: float) -> List[Note]:
        """Notes that overlap the time window [t0, t1), in start order."""
        lo = bisect.bisect_left(self._starts, t0 - self._max_duration)
        hi = bisect.bisect_left(self._starts, t1)
        return [n for n in self.notes[lo:hi] if n.end > t0]

    def active_notes(self, t: float) -> List[Note]:
        """Notes sounding at time t (start <= t < end)."""
        lo = bisect.bisect_left(self._starts, t - self._max_duration)
        hi = bisect.bisect_right(self._starts, t)
        return [n for n in self.notes[lo:hi] if n.end > t]

    def controls_between(self, t0: float, t1: float) -> list:
        """Pedal events with t0 < time <= t1."""
        lo = bisect.bisect_right(self._control_times, t0)
        hi = bisect.bisect_right(self._control_times, t1)
        return self.controls[lo:hi]

    def control_state(self, t: float) -> dict:
        """{controller: value} in force at time t (0 for pedals not yet pressed)."""
        state = {c: 0 for c in PEDALS}
        for _, c, v in self.controls[:bisect.bisect_right(self._control_times, t)]:
            state[c] = v
        return state

    def sustain_at(self, t: float) -> bool:
        return self.control_state(t).get(SUSTAIN, 0) >= 64

    def pitch_range(self) -> tuple:
        if not self.notes:
            return (LOWEST_PIANO_KEY, HIGHEST_PIANO_KEY)
        pitches = [n.pitch for n in self.notes]
        return (min(pitches), max(pitches))


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _hand_from_name(name: str) -> Optional[str]:
    """Recognise track names like 'Right Hand', 'RH', 'Piano L', 'Left'."""
    n = name.lower().replace("-", " ").replace("_", " ")
    words = n.split()
    if "right" in n or "rh" in words or "r" in words or "treble" in n:
        return RIGHT
    if "left" in n or "lh" in words or "l" in words or "bass" in n:
        return LEFT
    return None


def _fit_to_piano(pitch: int) -> int:
    """Shift out-of-range notes by octaves so they land on an 88-key piano."""
    while pitch < LOWEST_PIANO_KEY:
        pitch += 12
    while pitch > HIGHEST_PIANO_KEY:
        pitch -= 12
    return pitch


# --------------------------------------------------------------------------- #
# Fingering stored in the file
# --------------------------------------------------------------------------- #
# Standard MIDI has no fingering field, so annotated files carry it as a text
# meta event placed on the note's own track at the same tick, immediately
# before the note-on it belongs to. Players ignore it.
#   "F1".."F5"               finger only (the Hanon set)
#   "R1".."R5", "L1".."L5"   hand and finger (written by the fingering editor)
#   "R", "L"                 hand only

def _vlq(d, i):
    n = 0
    while True:
        b = d[i]; i += 1; n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, i


def _write_vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _marker(data):
    """(hand, finger) for a fingering text event's bytes, or None."""
    if len(data) == 2 and data[:1] in (b'F', b'R', b'L') and data[1:2] in b'12345':
        h = data[:1].decode()
        return (None if h == 'F' else h, int(data[1:2]))
    if data in (b'R', b'L'):
        return (data.decode(), None)
    return None


def _chunks(d):
    """(header bytes, [(chunk type, chunk bytes)]) of a standard MIDI file."""
    if d[:4] != b'MThd':
        raise ValueError("not a standard MIDI file")
    hl = struct.unpack('>I', d[4:8])[0]
    header = d[8:8 + hl]
    i, out = 8 + hl, []
    while i + 8 <= len(d):
        typ, n = d[i:i + 4], struct.unpack('>I', d[i + 4:i + 8])[0]
        out.append((typ, d[i + 8:i + 8 + n]))
        i += 8 + n
    return header, out


def _events(td):
    """
    Decode one track: [(abs_tick, kind, payload)] where kind is 'meta'
    (payload = (type, data)), 'sysex' (payload = raw bytes incl. status) or
    'midi' (payload = full status + data bytes, running status expanded).
    """
    out = []
    j = t = 0
    run = None
    while j < len(td):
        dt, j = _vlq(td, j)
        t += dt
        b = td[j]
        if b == 0xFF:
            typ = td[j + 1]
            ln, k = _vlq(td, j + 2)
            out.append((t, 'meta', (typ, td[k:k + ln])))
            j = k + ln
            continue
        if b in (0xF0, 0xF7):
            ln, k = _vlq(td, j + 1)
            out.append((t, 'sysex', td[j:k + ln]))
            j = k + ln
            continue
        if b & 0x80:
            run = b
            j += 1
        if run is None:
            raise ValueError("MIDI data without a status byte")
        n = 1 if run & 0xF0 in (0xC0, 0xD0) else 2
        out.append((t, 'midi', bytes([run]) + td[j:j + n]))
        j += n
    return out


def _encode(events):
    """Inverse of _events (no running status; end-of-track kept as given)."""
    out = bytearray()
    last = 0
    for t, kind, p in events:
        out += _write_vlq(t - last)
        last = t
        if kind == 'meta':
            typ, data = p
            out += bytes([0xFF, typ]) + _write_vlq(len(data)) + data
        else:
            out += p
    return bytes(out)


def _smf_tracks(path: str):
    """
    (header, chunks, {chunk index: decoded events}) for `path`, read the way
    load_midi read it: a file only readable after repair_smf (bad bytes,
    cut off, RIFF-wrapped) gives the repaired file's tracks.
    """
    def decode(data):
        header, chunks = _chunks(data)
        return header, chunks, {i: _events(td) for i, (typ, td) in enumerate(chunks) if typ == b'MTrk'}
    data = open(path, 'rb').read()
    try:
        return decode(data)
    except Exception:
        return decode(repair_smf(data)[0])


def read_markers(path: str) -> dict:
    """{(tick, pitch): (hand or None, finger or None)} for every marked note-on."""
    out = {}
    try:
        _, _, tracks = _smf_tracks(path)
        for events in tracks.values():
            pending = None
            for t, kind, p in events:
                if kind == 'meta':
                    if p[0] == 0x01:
                        pending = _marker(p[1]) or pending
                    continue
                if kind == 'midi' and p[0] & 0xF0 == 0x90 and p[2] > 0:
                    if pending:
                        out[(t, p[1])] = pending
                    pending = None
    except Exception:           # unreadable/odd file: just no fingering
        return {}
    return out


def read_fingering(path: str) -> dict:
    """{(tick, pitch): finger} for every note-on preceded by a fingering text event."""
    return {k: f for k, (h, f) in read_markers(path).items() if f}


def _tempo_map(header, tracks):
    """A function tick -> seconds for a file's header and decoded tracks (the same maths pretty_midi uses)."""
    division = struct.unpack('>h', header[4:6])[0]
    if division < 0:                                    # SMPTE time code
        fps, tpf = -(division >> 8), division & 0xFF
        return lambda tick: tick / (fps * tpf)
    # Like pretty_midi: tempo changes come from the first track only, and one
    # at tick 0 replaces the default 120 bpm.
    tempos = [(0, 500000)]
    first = tracks[min(tracks)] if tracks else []
    for t, kind, p in first:
        if kind == 'meta' and p[0] == 0x51:
            us = int.from_bytes(p[1], 'big')
            if t == 0:
                tempos = [(0, us)]
            elif us != tempos[-1][1]:
                tempos.append((t, us))
    starts, secs = [], []
    s = 0.0
    for i, (t, us) in enumerate(tempos):
        if i:
            s += (t - tempos[i - 1][0]) * tempos[i - 1][1] / 1e6 / division
        starts.append(t)
        secs.append(s)

    def to_sec(tick):
        i = bisect.bisect_right(starts, tick) - 1
        return secs[i] + (tick - starts[i]) * tempos[i][1] / 1e6 / division
    return to_sec


def save_fingered_midi(src_path: str, dst_path: str, notes, fingers) -> int:
    """
    Write a copy of `src_path` to `dst_path` with every note's hand and finger
    stored as "R3"/"L1"-style text events (see above). Everything else in the
    file - tempo, pedal, program changes, track layout - is kept byte for byte.
    `notes` are the loaded Note objects (with the hand to store) and `fingers`
    maps id(note) -> finger (or None). Returns how many notes were marked.
    """
    header, chunks, tracks = _smf_tracks(src_path)
    to_sec = _tempo_map(header, tracks)
    # loaded notes by (pitch, start in ms); the loader shifts out-of-range
    # pitches by octaves, so look them up the same way
    index = {}
    for n in notes:
        index.setdefault((n.pitch, round(n.start * 1000)), []).append(n)

    def find(pitch, t):
        p = _fit_to_piano(pitch)
        ms = round(t * 1000)
        for d in (0, -1, 1, -2, 2, -3, 3):
            got = index.get((p, ms + d))
            if got:
                return got[0]
        return None

    marked = set()
    out = bytearray(b'MThd' + struct.pack('>I', len(header)) + header)
    for i, (typ, td) in enumerate(chunks):
        if typ != b'MTrk':
            out += typ + struct.pack('>I', len(td)) + td
            continue
        new = []
        for ev in tracks[i]:
            t, kind, p = ev
            if kind == 'meta' and p[0] == 0x01 and _marker(p[1]):
                continue                                  # old fingering: replaced below
            if kind == 'midi' and p[0] & 0xF0 == 0x90 and p[2] > 0:
                n = find(p[1], to_sec(t))
                if n is not None:
                    f = fingers.get(id(n))
                    text = (n.hand + (str(f) if f else "")).encode()
                    new.append((t, 'meta', (0x01, text)))
                    marked.add(id(n))       # a note doubled on two tracks is still one note
            new.append(ev)
        data = _encode(new)
        out += b'MTrk' + struct.pack('>I', len(data)) + data
    tmp = dst_path + ".tmp"
    with open(tmp, 'wb') as fh:
        fh.write(out)
    os.replace(tmp, dst_path)
    return len(marked)


# --------------------------------------------------------------------------- #
# Sanitizing: making odd or damaged MIDI files readable
# --------------------------------------------------------------------------- #
# General MIDI programs 0-7: acoustic / electric pianos, harpsichord, clavinet
PIANO_PROGRAMS = range(0, 8)
_PIANO_WORDS = ("piano", "klavier", "pianoforte", "solo", "keyboard", "clavier", "cembalo",
                "harpsichord", "right hand", "left hand", "rh", "lh", "pf", "pno")
_OTHER_WORDS = ("violin", "violini", "viola", "viole", "cello", "violoncell", "contrabass", "double bass",
                "bass", "flute", "flaut", "oboe", "oboi", "clarinet", "clarinett", "bassoon", "fagott",
                "horn", "corni", "corno", "trumpet", "tromba", "trombe", "trombone", "tuba", "timpani",
                "timp", "drum", "percussion", "strings", "string", "choir", "voice", "vocal", "soprano",
                "alto", "tenor", "guitar", "harp", "organ", "orchestra", "tutti", "ensemble")
SAME_ONSET_T = 0.005     # s, the same pitch struck within this on two tracks is one note


def _words(name):
    import re
    return re.findall(r"[a-z]+", (name or "").lower())


def is_piano_track(inst) -> bool:
    """A piano part: a piano program and no other instrument's name, or a piano-ish name."""
    words = _words(inst.name)
    text = " ".join(words)
    named_piano = any(w in words or (" " in w and w in text) for w in _PIANO_WORDS)
    named_other = any(any(word.startswith(o) for word in words) for o in _OTHER_WORDS if " " not in o) or \
        any(o in text for o in _OTHER_WORDS if " " in o)
    if named_piano:
        return True
    return inst.program in PIANO_PROGRAMS and not named_other


def _vlq_read(data, i):
    """(value, next index) of a variable-length quantity at data[i] (at most 4 bytes)."""
    v = 0
    for k in range(4):
        if i >= len(data):
            raise IndexError
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i
    return v, i


_DATA_LEN = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}


def repair_smf(data: bytes):
    """
    A cleaned copy of a Standard MIDI File's bytes, and how many events were
    dropped or fixed: every track is walked event by event (running status
    included), data bytes over 127 are clipped, a truncated track keeps what
    it has, meta events are kept only when valid (tempo, time and key
    signatures, text, end of track) and system-exclusive messages dropped,
    their delta times carried into the next event.
    """
    import struct
    if data[:4] != b"MThd":
        j = data.find(b"MThd")                 # RIFF-wrapped files and the like
        if j < 0:
            raise ValueError("not a MIDI file")
        data = data[j:]
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, _, division = struct.unpack(">HHH", data[8:14])
    i = 8 + hlen
    tracks, fixed = [], 0
    while i + 8 <= len(data):
        cid, clen = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + clen]
        i += 8 + clen
        if cid != b"MTrk":
            continue
        out, j, carry, status = bytearray(), 0, 0, None
        while j < len(body):
            try:
                delta, j = _vlq_read(body, j)
                b = body[j]
                if b == 0xFF:                               # meta
                    mtype = body[j + 1]
                    mlen, k = _vlq_read(body, j + 2)
                    payload = body[k:k + mlen]
                    j = k + mlen
                    ok = (mtype in range(0x01, 0x08) or mtype == 0x03
                          or (mtype == 0x51 and mlen == 3 and int.from_bytes(payload, "big") > 0)
                          or (mtype == 0x58 and mlen == 4 and payload[0] > 0 and payload[1] <= 6)
                          or (mtype == 0x59 and mlen == 2 and -7 <= int.from_bytes(payload[:1], "big", signed=True) <= 7
                              and payload[1] in (0, 1)))
                    if mtype == 0x2F:
                        break
                    if ok and len(payload) == mlen:
                        out += _write_vlq(delta + carry) + bytes([0xFF, mtype]) + _write_vlq(mlen) + payload
                        carry = 0
                    else:
                        carry += delta
                        fixed += 1
                    continue
                if b in (0xF0, 0xF7):                       # system exclusive: not for a piano
                    slen, k = _vlq_read(body, j + 1)
                    j = k + slen
                    carry += delta
                    fixed += 1
                    continue
                if b & 0x80:
                    status = b
                    j += 1
                if status is None or status >= 0xF0:
                    raise IndexError                         # no status to run on: give up on the track
                n = _DATA_LEN[status >> 4]
                if j + n > len(body):
                    raise IndexError
                vals = list(body[j:j + n])
                j += n
                if any(v > 127 for v in vals):
                    vals = [min(v, 127) for v in vals]
                    fixed += 1
                out += _write_vlq(delta + carry) + bytes([status] + vals)
                carry = 0
            except IndexError:
                fixed += 1                                   # a truncated or garbled end: keep what came before
                break
        out += b"\x00\xff\x2f\x00"
        tracks.append(bytes(out))
    if not tracks:
        raise ValueError("no tracks")
    head = b"MThd" + struct.pack(">IHHH", 6, 1 if len(tracks) > 1 else fmt, len(tracks), division)
    return head + b"".join(b"MTrk" + struct.pack(">I", len(t)) + t for t in tracks), fixed


def read_midi(path: str):
    """
    pretty_midi.PrettyMIDI for `path`, reading damaged files leniently: if
    the strict parse fails, the file's bytes are repaired (repair_smf) and
    parsed again. Returns (PrettyMIDI, [what was done]).
    """
    import io
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # tempo events on other tracks etc: harmless
        try:
            return pretty_midi.PrettyMIDI(path), []
        except Exception as first:
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                clean, fixed = repair_smf(data)
                pm = pretty_midi.PrettyMIDI(io.BytesIO(clean))
            except Exception:
                raise first
            return pm, ["repaired the file after: %s (%d event(s) dropped or fixed)" % (first, fixed)]


def sanitize_instruments(pm, include_drums: bool = False):
    """
    [(index, instrument)] worth playing on the piano, and [what was done]:
    tracks without notes and percussion go; when there are piano parts AND
    other instruments (a concerto's full score), only the piano parts stay.
    A file with nothing recognisably piano keeps every track.
    """
    report = []
    insts = [(i, inst) for i, inst in enumerate(pm.instruments) if inst.notes]
    if not include_drums:
        drums = [inst for _, inst in insts if inst.is_drum]
        if drums:
            report.append("dropped %d percussion track(s)" % len(drums))
        insts = [(i, inst) for i, inst in insts if not inst.is_drum]
    piano = [(i, inst) for i, inst in insts if is_piano_track(inst)]
    if piano and len(piano) < len(insts):
        others = [inst.name or "track %d" % i for i, inst in insts if (i, inst) not in piano]
        report.append("kept the piano part%s (%s), dropped %d other instrument track(s): %s" % (
            "s" if len(piano) > 1 else "", ", ".join(inst.name or "track %d" % i for i, inst in piano),
            len(others), ", ".join(others[:8]) + (", ..." if len(others) > 8 else "")))
        insts = piano
    return insts, report


def sanitize_notes(notes, min_duration: float = 0.05):
    """
    Clean note list (Note objects) and [what was done]: notes with
    impossible times go; times start at 0; velocities are 1..127; pitches
    are folded onto the piano; a note doubled on another track at the same
    moment is kept once; and a key struck again while it is still down
    ends the earlier note there (a piano key can't sound twice).
    """
    import math
    report = []
    ok = [n for n in notes if math.isfinite(n.start) and math.isfinite(n.end) and n.end >= n.start]
    if len(ok) < len(notes):
        report.append("dropped %d note(s) with impossible times" % (len(notes) - len(ok)))
    t0 = min((n.start for n in ok), default=0.0)
    if t0 < 0:
        ok = [replace(n, start=n.start - t0, end=n.end - t0) for n in ok]
        report.append("shifted everything %.3f s later (the file started before 0)" % -t0)
    fixed = 0
    out = []
    for n in ok:
        v, p = min(127, max(1, int(n.velocity))), _fit_to_piano(int(n.pitch))
        end = max(n.end, n.start + min_duration)
        if (v, p, end) != (n.velocity, n.pitch, n.end):
            fixed += n.velocity != v or n.pitch != p
            n = replace(n, velocity=v, pitch=p, end=end)
        out.append(n)
    if fixed:
        report.append("brought %d velocit(ies) / pitch(es) into range" % fixed)
    # same pitch: doubled notes and re-strikes of a key still down
    by_pitch = {}
    for n in sorted(out, key=lambda n: (n.pitch, n.start, -n.end)):
        by_pitch.setdefault(n.pitch, []).append(n)
    result, dup, cut = [], 0, 0
    for p, ns in by_pitch.items():
        kept = []
        for n in ns:
            if kept and abs(n.start - kept[-1].start) <= SAME_ONSET_T:
                dup += 1                       # the same key struck twice at once: one note
                if n.end > kept[-1].end:
                    kept[-1] = replace(kept[-1], end=n.end)
                continue
            if kept and n.start < kept[-1].end:
                kept[-1] = replace(kept[-1], end=max(kept[-1].start + 1e-3, n.start))
                cut += 1
            kept.append(n)
        result += kept
    if dup:
        report.append("merged %d doubled note(s)" % dup)
    if cut:
        report.append("ended %d note(s) where their key is struck again" % cut)
    return result, report


def load_midi(path: str, include_drums: bool = False, split_pitch: int = MIDDLE_C,
              min_duration: float = 0.05) -> MidiSong:
    """
    Load a MIDI file.

    include_drums: keep percussion tracks (normally meaningless on a piano).
    split_pitch:   only used if hand_split.py is missing: notes below this
                   pitch are LEFT and the rest RIGHT. Normally, when the
                   tracks don't say which hand plays what, hand_split.py
                   works it out from the music (span, speed, voice leading).
    min_duration:  very short notes are stretched to this length (seconds) so
                   they stay visible when drawn.
    """
    pm, report = read_midi(path)
    instruments, r = sanitize_instruments(pm, include_drums)
    report += r

    # Decide which hand each track belongs to.
    #   1. If every track is named like "Right Hand" / "LH", the names win.
    #   2. Exactly two tracks that sit in clearly different registers: the
    #      higher one is the right hand.
    #   3. Otherwise (one track for the whole piano, or tracks by voice) each
    #      note's hand is worked out from the music by hand_split.py.
    track_hand = {i: _hand_from_name(inst.name) for i, inst in instruments}
    all_named = all(track_hand.values())
    if not all_named:
        track_hand = {i: None for i, _ in instruments}
        if len(instruments) == 2:
            (ia, a), (ib, b) = instruments
            mean_a = sum(n.pitch for n in a.notes) / len(a.notes)
            mean_b = sum(n.pitch for n in b.notes) / len(b.notes)
            if abs(mean_a - mean_b) >= 7:
                track_hand[ia], track_hand[ib] = (RIGHT, LEFT) if mean_a >= mean_b else (LEFT, RIGHT)

    # fingering keyed by (start time, pitch) so it lines up with pretty_midi's notes
    # (hand, finger) marks keyed by (start time, pitch) so they line up with pretty_midi's notes
    try:
        marks = {(round(float(pm.tick_to_time(t)), 4), p): hf for (t, p), hf in read_markers(path).items()}
    except Exception:                          # a damaged file: no fingering marks then
        marks = {}

    notes: List[Note] = []
    tracks: List[TrackInfo] = []
    marked = set()                             # (start, pitch) of notes whose hand a marker gives
    for i, inst in instruments:
        for n in inst.notes:
            mark_hand, finger = marks.get((round(float(n.start), 4), n.pitch), (None, None))
            if mark_hand:
                marked.add((float(n.start), _fit_to_piano(int(n.pitch))))
            notes.append(Note(int(n.pitch), float(n.start), float(n.end), int(n.velocity), i,
                              mark_hand or track_hand.get(i), finger))
        tracks.append(TrackInfo(i, inst.name or f"Track {i}", inst.program, len(inst.notes), track_hand.get(i)))
    notes, r = sanitize_notes(notes, min_duration)
    report += r

    # Work out each note's hand from the music. A hand the track says (by
    # its name or register) is followed unless that hand couldn't keep up
    # (one track holding both hands' notes in a passage); only the hands
    # stored with the notes (fingering markers) are taken as they are.
    loose = [n for n in notes if (n.start, n.pitch) not in marked]
    if loose:
        try:
            from hand_split import split_hands
            hands = split_hands(loose, prefer={id(n): n.hand for n in loose if n.hand})
        except ImportError:
            hands = {id(n): n.hand or (LEFT if n.pitch < split_pitch else RIGHT) for n in loose}
        moved = sum(1 for n in loose if n.hand and hands[id(n)] != n.hand)
        if moved:
            report.append("gave %d note(s) to the other hand than their track's, which couldn't keep up" % moved)
        notes = [replace(n, hand=hands[id(n)]) if id(n) in hands else n for n in notes]

    try:
        bar_times = [float(t) for t in pm.get_downbeats()]
        beat_times = [float(t) for t in pm.get_beats()]
    except Exception:  # odd time-signature data; bar lines are only cosmetic
        bar_times, beat_times = [], []

    controls = []
    for inst in pm.instruments:                   # a pedal may sit on a track of its own
        if inst.is_drum:
            continue
        for cc in getattr(inst, "control_changes", []):
            if cc.number in PEDALS:
                controls.append((float(cc.time), int(cc.number), int(cc.value)))

    duration = max((n.end for n in notes), default=0.0)
    song = MidiSong(notes, tracks, duration, bar_times, beat_times, path, controls)
    song.cleanup = report                      # what sanitizing changed, for the curious
    for line in report:
        print("%s: %s" % (os.path.basename(path), line))
    return song


# --------------------------------------------------------------------------- #
# PIG fingering files (the Piano Fingering Dataset's text format)
# --------------------------------------------------------------------------- #
# One note per line:  index  onset  offset  pitch  on-velocity  off-velocity
# channel  finger,  tab separated, "//" comment lines. Times are seconds,
# pitch is spelled ("C#4", "Bb3"), channel 0 = right hand, 1 = left hand,
# finger 1..5 (negative for the left hand); "3_1" is a finger substitution on
# a held key (we take the first finger).
PIG_EXTENSIONS = (".txt",)
_PIG_STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PIG_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def is_pig(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in PIG_EXTENSIONS


def pig_pitch(name: str) -> int:
    import re
    m = re.match(r"^([A-Ga-g])([#b-]*)(-?\d+)$", name.strip())
    if not m:
        raise ValueError(f"bad pitch name {name!r}")
    step, acc, octave = m.groups()
    alter = acc.count("#") - acc.count("b") - acc.count("-")
    return (int(octave) + 1) * 12 + _PIG_STEP[step.upper()] + alter


def pig_name(pitch: int) -> str:
    return f"{_PIG_NAMES[pitch % 12]}{pitch // 12 - 1}"


def read_pig(path: str, with_fingering: bool = True) -> MidiSong:
    """Load a PIG file: hands from the channels, fingering (optional) from the last column."""
    notes = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.startswith("//"):
                continue
            cols = line.split()
            if len(cols) < 7:
                continue
            onset, offset = float(cols[1]), float(cols[2])
            pitch = _fit_to_piano(pig_pitch(cols[3]))
            vel = max(1, min(127, int(float(cols[4])))) if cols[4].lstrip("-").replace(".", "").isdigit() else 64
            channel = int(cols[6])
            finger = None
            if with_fingering and len(cols) >= 8:
                f = cols[7].split("_")[0].lstrip("+-")
                finger = int(f) if f in ("1", "2", "3", "4", "5") else None
            hand = LEFT if channel == 1 else RIGHT
            notes.append(Note(pitch, onset, max(offset, onset + 0.05), vel, channel, hand, finger))
    tracks = [TrackInfo(c, name, 0, sum(1 for n in notes if n.track == c), h)
              for c, name, h in ((0, "Right hand", RIGHT), (1, "Left hand", LEFT))
              if any(n.track == c for n in notes)]
    duration = max((n.end for n in notes), default=0.0)
    return MidiSong(notes, tracks, duration, [], [], path)


def save_pig(path: str, notes, fingers) -> int:
    """Write notes as a PIG file: channel from each note's hand, fingers from `fingers` (id(note) -> finger)."""
    rows = sorted(notes, key=lambda n: (n.start, n.pitch))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("//Version: PianoFingering_v170101\n")
        for i, n in enumerate(rows):
            left = n.hand == LEFT
            f = fingers.get(id(n))
            fs = "_" if not f else str(-f if left else f)
            fh.write(f"{i}\t{n.start:.6f}\t{n.end:.6f}\t{pig_name(n.pitch)}\t{n.velocity}\t80\t"
                     f"{1 if left else 0}\t{fs}\n")
    os.replace(tmp, path)
    return len(rows)


def load_song(path: str) -> MidiSong:
    """A MIDI file or a PIG fingering file, by extension."""
    return read_pig(path) if is_pig(path) else load_midi(path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python midi_loader.py song.mid")
        sys.exit(1)
    song = load_song(sys.argv[1])
    lo, hi = song.pitch_range()
    print(f"{song.title}: {len(song)} notes, {song.duration:.1f}s, "
          f"range {note_name(lo)}-{note_name(hi)}, {len(song.bar_times)} bars")
    for t in song.tracks:
        print(f"  track {t.index}: {t.name!r}, {t.note_count} notes, hand={t.hand or 'split'}")
    fingered = sum(1 for n in song.notes if n.finger)
    if fingered:
        print(f"  fingering from file: {fingered} of {len(song)} notes")
    left = sum(1 for n in song.notes if n.hand == LEFT)
    print(f"  left hand {left} notes, right hand {len(song) - left} notes")
