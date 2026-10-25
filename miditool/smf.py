"""
smf.py - Standard MIDI Files, read and written without losing anything.

A track is kept as the list of its events - (absolute tick, kind, payload) -
exactly as the file has them (running status expanded), plus the tick of its
end-of-track event. Writing those events back gives the same events in the
same order; chunks other than tracks are kept byte for byte, in place.

    kind MIDI   payload = status byte + data bytes        (b"\\x90\\x3c\\x50")
    kind META   payload = meta type + data                 (b"\\x01R3": a text event "R3")
    kind SYSEX  payload = the raw event: F0/F7, length, data

A file the strict reader can't take (bad bytes, cut off, wrapped in RIFF) is
repaired first (`repair`, after Hand-thesia's midi_loader.repair_smf) and
what that changed is reported in `Smf.notes`.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MIDI, META, SYSEX = 0, 1, 2

META_TEXT = 0x01
META_TRACK_NAME = 0x03
META_EOT = 0x2F
META_TEMPO = 0x51
META_TIMESIG = 0x58
META_KEYSIG = 0x59

DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


class SmfError(ValueError):
    """The bytes aren't a Standard MIDI File this reader can take."""


def read_vlq(d, i):
    """(value, next index) of the variable-length number at d[i]."""
    v = 0
    for _ in range(4):
        if i >= len(d):
            raise SmfError("the file ends inside a number")
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i
    raise SmfError("a variable-length number is longer than 4 bytes")


def vlq(n):
    """The variable-length encoding of n >= 0."""
    if n < 0:
        raise SmfError("negative delta time")
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


@dataclass
class Track:
    events: list                     # [(tick, kind, payload)] in file order
    end_tick: int = 0                # where its end-of-track event is (the last event's tick if it has none)
    has_eot: bool = True
    running: int = 0                 # events that used running status in the file


@dataclass
class Smf:
    fmt: int = 1
    division: int = 480              # the header's field: ticks per quarter note, or SMPTE when the top bit is set
    tracks: list = field(default_factory=list)
    layout: list = field(default_factory=list)   # chunk order: ("track", index) or ("raw", type, data)
    header_extra: bytes = b""        # header bytes past the standard six (rare)
    trailing: bytes = b""            # stray bytes after the last chunk (a few files end with some)
    notes: list = field(default_factory=list)    # what reading had to repair

    @property
    def uses_running_status(self):
        return any(t.running for t in self.tracks)

    @property
    def smpte(self):
        return bool(self.division & 0x8000)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _parse_track(td):
    events = []
    j = t = 0
    run = None
    used = 0
    n = len(td)
    end = None
    while j < n:
        dt, j = read_vlq(td, j)
        t += dt
        if j >= n:
            raise SmfError("a track ends inside an event")
        b = td[j]
        if b == 0xFF:
            if j + 1 >= n:
                raise SmfError("a track ends inside a meta event")
            typ = td[j + 1]
            ln, k = read_vlq(td, j + 2)
            if k + ln > n:
                raise SmfError("a meta event runs past the end of its track")
            if typ == META_EOT:
                end = t
                break
            events.append((t, META, bytes([typ]) + td[k:k + ln]))
            j = k + ln
            continue
        if b in (0xF0, 0xF7):
            ln, k = read_vlq(td, j + 1)
            if k + ln > n:
                raise SmfError("a system-exclusive message runs past the end of its track")
            events.append((t, SYSEX, bytes(td[j:k + ln])))
            j = k + ln
            continue
        if b & 0x80:
            if b >= 0xF0:
                raise SmfError("system message %02X inside a track" % b)
            run = b
            j += 1
        else:
            if run is None:
                raise SmfError("a data byte without a status byte")
            used += 1
        m = DATA_LEN[run & 0xF0]
        if j + m > n:
            raise SmfError("a track ends inside an event")
        data = td[j:j + m]
        if any(x & 0x80 for x in data):
            raise SmfError("a data byte over 127")
        events.append((t, MIDI, bytes([run]) + bytes(data)))
        j += m
    return Track(events, end if end is not None else t, end is not None, used)


def parse(data):
    """The Smf in `data` (strict: raises SmfError on anything malformed)."""
    data = bytes(data)
    if data[:4] != b"MThd":
        raise SmfError("not a standard MIDI file")
    if len(data) < 14:
        raise SmfError("the header is cut off")
    hl = struct.unpack(">I", data[4:8])[0]
    if hl < 6 or 8 + hl > len(data):
        raise SmfError("bad header length")
    fmt, _ntrks, division = struct.unpack(">HHH", data[8:14])
    if fmt > 2:
        raise SmfError("unknown MIDI file format %d" % fmt)
    smf = Smf(fmt=fmt, division=division, header_extra=data[14:8 + hl])
    i = 8 + hl
    while i < len(data):
        if i + 8 > len(data):
            smf.trailing = data[i:]                   # a few stray bytes after the last chunk: kept as they are
            break
        typ, ln = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + ln]
        if len(body) < ln:
            raise SmfError("the last chunk is cut off")
        if typ == b"MTrk":
            smf.layout.append(("track", len(smf.tracks)))
            smf.tracks.append(_parse_track(body))
        else:
            smf.layout.append(("raw", typ, body))
        i += 8 + ln
    if not smf.tracks:
        raise SmfError("the file has no tracks")
    if fmt == 2:
        raise SmfError("type-2 (multi-song) MIDI files aren't supported")
    return smf


def parse_lenient(data):
    """parse(data); if that fails, the repaired bytes parsed (what was fixed goes in .notes)."""
    try:
        return parse(data)
    except SmfError as first:
        try:
            fixed, count = repair(data)
            smf = parse(fixed)
        except Exception:
            raise first
        smf.notes.append("The file was damaged (%s) and has been repaired: %d event(s) dropped or fixed."
                         % (first, count))
        return smf


def read_file(path):
    with open(path, "rb") as fh:
        return parse_lenient(fh.read())


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def encode_track(events, end_tick=0, running_status=True):
    """The bytes of an MTrk body for events [(tick, kind, payload)] (sorted by tick), ended at end_tick or later."""
    out = bytearray()
    last = 0
    run = None
    for t, kind, p in events:
        if t < last:
            raise SmfError("events out of order")
        out += vlq(t - last)
        last = t
        if kind == MIDI:
            if running_status and p[0] == run:
                out += p[1:]
            else:
                out += p
                run = p[0]
        elif kind == META:
            out += b"\xff" + p[:1] + vlq(len(p) - 1) + p[1:]
            run = None
        else:
            out += p
            run = None
    out += vlq(max(end_tick, last) - last) + b"\xff\x2f\x00"
    return bytes(out)


def serialize(smf, running_status=None):
    """The file's bytes. running_status: None = as the source used it."""
    rs = smf.uses_running_status if running_status is None else running_status
    ntrk = len(smf.tracks)
    fmt = smf.fmt if not (smf.fmt == 0 and ntrk > 1) else 1
    hdr = struct.pack(">HHH", fmt, ntrk, smf.division) + smf.header_extra
    out = bytearray(b"MThd" + struct.pack(">I", len(hdr)) + hdr)
    layout = smf.layout or [("track", i) for i in range(ntrk)]
    seen = set()
    for item in layout:
        if item[0] == "track":
            seen.add(item[1])
            tr = smf.tracks[item[1]]
            body = encode_track(tr.events, tr.end_tick, rs)
            out += b"MTrk" + struct.pack(">I", len(body)) + body
        else:
            out += item[1] + struct.pack(">I", len(item[2])) + item[2]
    for i, tr in enumerate(smf.tracks):                 # tracks added since reading
        if i not in seen:
            body = encode_track(tr.events, tr.end_tick, rs)
            out += b"MTrk" + struct.pack(">I", len(body)) + body
    return bytes(out) + smf.trailing


# --------------------------------------------------------------------------- #
# Repair (Hand-thesia's midi_loader.repair_smf)
# --------------------------------------------------------------------------- #
def _vlq_read(data, i):
    v = 0
    for _ in range(4):
        if i >= len(data):
            raise IndexError
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i
    return v, i


def repair(data):
    """
    A cleaned copy of a Standard MIDI File's bytes, and how many events were
    dropped or fixed: every track is walked event by event (running status
    included), data bytes over 127 are clipped, a truncated track keeps what
    it has, meta events are kept only when valid (tempo, time and key
    signatures, text, end of track) and system-exclusive messages dropped,
    their delta times carried into the next event.
    """
    data = bytes(data)
    if data[:4] != b"MThd":
        j = data.find(b"MThd")                 # RIFF-wrapped files and the like
        if j < 0:
            raise SmfError("not a MIDI file")
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
                if b == 0xFF:
                    mtype = body[j + 1]
                    mlen, k = _vlq_read(body, j + 2)
                    payload = body[k:k + mlen]
                    j = k + mlen
                    ok = (mtype in range(0x01, 0x08)
                          or (mtype == 0x51 and mlen == 3 and int.from_bytes(payload, "big") > 0)
                          or (mtype == 0x58 and mlen == 4 and payload[0] > 0 and payload[1] <= 6)
                          or (mtype == 0x59 and mlen == 2
                              and -7 <= int.from_bytes(payload[:1], "big", signed=True) <= 7
                              and payload[1] in (0, 1)))
                    if mtype == 0x2F:
                        break
                    if ok and len(payload) == mlen:
                        out += vlq(delta + carry) + bytes([0xFF, mtype]) + vlq(mlen) + payload
                        carry = 0
                    else:
                        carry += delta
                        fixed += 1
                    continue
                if b in (0xF0, 0xF7):
                    slen, k = _vlq_read(body, j + 1)
                    j = k + slen
                    carry += delta
                    fixed += 1
                    continue
                if b & 0x80:
                    status = b
                    j += 1
                if status is None or status >= 0xF0:
                    raise IndexError
                n = DATA_LEN[status & 0xF0]
                if j + n > len(body):
                    raise IndexError
                vals = list(body[j:j + n])
                j += n
                if any(v > 127 for v in vals):
                    vals = [min(v, 127) for v in vals]
                    fixed += 1
                out += vlq(delta + carry) + bytes([status] + vals)
                carry = 0
            except IndexError:
                fixed += 1
                break
        out += b"\x00\xff\x2f\x00"
        tracks.append(bytes(out))
    if not tracks:
        raise SmfError("no tracks")
    head = b"MThd" + struct.pack(">IHHH", 6, 1 if len(tracks) > 1 else min(fmt, 1), len(tracks), division)
    return head + b"".join(b"MTrk" + struct.pack(">I", len(t)) + t for t in tracks), fixed
