"""
playback.py - Playing the document through the system's MIDI synth.

HostOut plays through Hand-thesia's synth (the system's, or a chosen
soundfont). Player turns the document into a
timeline in seconds (its tempo map) and sends what falls in each frame;
starting from any point it first sends each channel's programs and controller
values in force there (the pedals too), so it sounds as it would have.
"""
from __future__ import annotations

import bisect

import numpy as np

from .smf import MIDI

CHASE_CCS = (0, 32, 1, 7, 10, 11, 64, 66, 67, 91, 93, 121)
END_PAD_S = 0.6


class _Key:
    """A key as Hand-thesia's synths take it (common.MidiOut / sf_synth.SoundfontOut: note_on(note))."""
    __slots__ = ("pitch", "velocity", "hand")

    def __init__(self, pitch, velocity, hand):
        self.pitch, self.velocity, self.hand = pitch, velocity, hand


class HostOut:
    """
    The player's output through Hand-thesia's synth (`app.midi`: the system synth or a chosen soundfont),
    which plays a piano on two channels (the hands): the file's odd channels (2, 4...) go to the left hand's,
    the rest to the right's; the pedals go to both, other controllers and programs aren't sent. The volume
    is the synth's own (its slider in the Settings and the player).
    """

    def __init__(self, app):
        self.app = app
        self.tried = True
        self.volume = 1.0
        self.muted = False

    @property
    def midi(self):
        return self.app.midi

    @property
    def error(self):
        return "" if self.midi.available else "no synth"

    def ensure(self):
        return True                     # (no synth: it plays silently - the playhead and the hands still move)

    @staticmethod
    def _hand(ch):
        return "L" if ch & 1 else "R"

    def short(self, status, d1=0, d2=0):
        kind, ch = status & 0xF0, status & 0x0F
        if kind == 0x90 and d2 > 0:
            self.note_on(ch, d1, d2)
        elif kind in (0x80, 0x90):
            self.note_off(ch, d1)
        elif kind == 0xB0 and d1 in (64, 66, 67):
            self.midi.control_change(d1, 127 if d2 >= 64 else 0)

    def note_on(self, ch, pitch, vel):
        if not self.muted:
            self.midi.note_on(_Key(pitch, max(1, min(127, int(round(vel * self.volume)))), self._hand(ch)))

    def note_off(self, ch, pitch):
        self.midi.note_off(_Key(pitch, 0, self._hand(ch)))

    def silence(self):
        self.midi.silence()

    def close(self):
        self.silence()                  # (the synth is Hand-thesia's: it stays open)


class Player:
    def __init__(self, out):
        self.out = out
        self.playing = False
        self.sec = 0.0
        self.speed = 1.0
        self._sched = None              # (doc version, times, messages)
        self._i = 0
        self.sounding = set()           # (channel, pitch) we started
        self.previews = []              # [(release time on the perf clock, channel, pitch)]
        self.clock = 0.0                # seconds since start (for previews)

    # ----- the timeline ---------------------------------------------------------
    def _schedule(self, doc):
        if self._sched is not None and self._sched[0] == doc.version:
            return self._sched
        tm = doc.tempo_map()
        rows = []                       # (tick, order, status, d1, d2)
        for n in doc.notes.values():
            rows.append((n.end, 0, 0x80 | n.channel, n.pitch, 0))
            rows.append((n.start, 2, 0x90 | n.channel, n.pitch, n.velocity))
        for e in doc.events.values():
            if e.kind == MIDI and (e.payload[0] & 0xF0) not in (0x80, 0x90):
                p = e.payload
                rows.append((e.tick, 1, p[0], p[1], p[2] if len(p) > 2 else 0))
        rows.sort()
        ticks = np.array([r[0] for r in rows], dtype=np.int64)
        times = tm.to_seconds(ticks).tolist() if len(rows) else []
        msgs = [(r[2], r[3], r[4]) for r in rows]
        self._sched = (doc.version, times, msgs)
        return self._sched

    def end_seconds(self, doc):
        return doc.tempo_map().sec(doc.end_tick()) + END_PAD_S

    def _chase(self, doc, tick):
        """Programs, controllers and pitch bends in force at `tick`, sent to every channel that uses them."""
        state = {}
        for e in sorted(doc.events.values(), key=lambda e: (e.tick, e.track, e.pos if e.pos >= 0 else 1 << 60)):
            if e.tick > tick:
                break
            if e.kind != MIDI:
                continue
            st, ch = e.payload[0] & 0xF0, e.payload[0] & 0x0F
            if st == 0xC0:
                state[(ch, "prog")] = (e.payload[0], e.payload[1], 0)
            elif st == 0xB0 and e.payload[1] in CHASE_CCS:
                state[(ch, "cc", e.payload[1])] = (e.payload[0], e.payload[1], e.payload[2])
            elif st == 0xE0:
                state[(ch, "bend")] = (e.payload[0], e.payload[1], e.payload[2])
        for ch in range(16):                     # (pedals left down by an earlier play)
            for c in (64, 66, 67):
                self.out.short(0xB0 | ch, c, 0)
        for key in sorted(state, key=lambda k: (k[0], 0 if k[1] == "prog" else 1)):
            self.out.short(*state[key])

    # ----- transport ----------------------------------------------------------
    def start(self, doc, tick):
        if not self.out.ensure():
            return False
        tm = doc.tempo_map()
        self.sec = tm.sec(max(0, tick))
        if self.sec >= self.end_seconds(doc) - END_PAD_S:
            self.sec = 0.0
        ver, times, _ = self._schedule(doc)
        self._ver = ver
        self._i = bisect.bisect_left(times, self.sec)
        self._chase(doc, tm.tick(self.sec))
        self.playing = True
        return True

    def stop(self):
        if self.playing:
            self.playing = False
            for ch, p in self.sounding:
                self.out.note_off(ch, p)
            self.sounding.clear()
            self.out.silence()

    def tick_position(self, doc):
        return doc.tempo_map().tick(self.sec)

    def update(self, dt, doc):
        """Advance (when playing) and send what's due. Returns False when playback reached the end."""
        self.clock += dt
        if self.previews:
            due = [p for p in self.previews if p[0] <= self.clock]
            for _, ch, pitch in due:
                if (ch, pitch) not in self.sounding:
                    self.out.note_off(ch, pitch)
            self.previews = [p for p in self.previews if p[0] > self.clock]
        if not self.playing:
            return True
        ver, times, msgs = self._schedule(doc)
        if self._sched is not None and self._i > len(times):
            self._i = len(times)
        t1 = self.sec + dt * self.speed
        if ver != getattr(self, "_ver", ver):            # the document changed: carry on from here
            self._i = bisect.bisect_right(times, self.sec)
        self._ver = ver
        i = self._i
        n = len(times)
        while i < n and times[i] <= t1:
            st, d1, d2 = msgs[i]
            kind = st & 0xF0
            ch = st & 0x0F
            if kind == 0x90:
                if (ch, d1) in self.sounding:
                    self.out.note_off(ch, d1)
                self.sounding.add((ch, d1))
                self.out.note_on(ch, d1, d2)
            elif kind == 0x80:
                if (ch, d1) in self.sounding:
                    self.sounding.discard((ch, d1))
                    self.out.note_off(ch, d1)
            else:
                self.out.short(st, d1, d2)
            i += 1
        self._i = i
        self.sec = t1
        if self.sec >= self.end_seconds(doc):
            self.stop()
            return False
        return True

    def preview(self, ch, pitch, vel=90, dur=0.35):
        """Sound one note briefly (clicking a key, drawing a note)."""
        if not self.out.ensure():
            return
        self.out.note_off(ch, pitch)
        self.out.note_on(ch, pitch, vel)
        self.previews.append((self.clock + dur, ch, pitch))
