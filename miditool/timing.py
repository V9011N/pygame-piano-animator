"""
timing.py - Ticks, seconds, bars and beats.

TempoMap turns ticks into seconds and back from a file's tempo events (all
tracks', the last at a tick winning - as mido and the Human MIDI Cleaner read
them). MeterMap lays the bars and beats over the ticks from the time
signatures; a signature that comes in mid-bar starts a new bar there.
"""
from __future__ import annotations

import bisect
import math

import numpy as np

DEFAULT_TEMPO = 500_000          # microseconds per quarter note (120 bpm) until the first tempo event


def ppq_of(division):
    """Ticks per quarter note for a header's division (an SMPTE file counts a nominal half second a quarter)."""
    if division & 0x8000:
        fps = 256 - (division >> 8)
        tpf = division & 0xFF
        return max(1, int(round(fps * tpf / 2)))
    return max(1, division)


class TempoMap:
    def __init__(self, division, changes=()):
        """changes: [(tick, microseconds per quarter)] in file order (later ones win at the same tick)."""
        self.division = division
        self.ppq = ppq_of(division)
        self.smpte = bool(division & 0x8000)
        merged = {0: DEFAULT_TEMPO}
        if not self.smpte:
            for tick, us in changes:
                if us > 0:
                    merged[int(tick)] = int(us)
        self.ticks = np.array(sorted(merged), dtype=np.int64)
        self.us = np.array([merged[t] for t in self.ticks.tolist()], dtype=float)
        if self.smpte:
            fps = 256 - (division >> 8)
            tpf = division & 0xFF
            self.sec_per_tick = np.array([1.0 / (fps * tpf)])
            self.us = np.array([self.ppq / (fps * tpf) * 1e6])
        else:
            self.sec_per_tick = self.us / 1e6 / self.ppq
        gaps = np.diff(self.ticks) * self.sec_per_tick[:-1]
        self.seconds = np.concatenate(([0.0], np.cumsum(gaps)))
        self._ticks_list = self.ticks.tolist()
        self._secs_list = self.seconds.tolist()
        self._spt_list = self.sec_per_tick.tolist()

    def to_seconds(self, ticks):
        """Seconds at tick(s) (array in, array out)."""
        t = np.asarray(ticks, dtype=float)
        k = np.clip(np.searchsorted(self.ticks, t, side="right") - 1, 0, len(self.ticks) - 1)
        return self.seconds[k] + (t - self.ticks[k]) * self.sec_per_tick[k]

    def sec(self, tick):
        """Seconds at one tick (a float, fast)."""
        k = max(0, bisect.bisect_right(self._ticks_list, tick) - 1)
        return self._secs_list[k] + (tick - self._ticks_list[k]) * self._spt_list[k]

    def to_ticks(self, seconds):
        """Ticks (float) at second(s)."""
        s = np.asarray(seconds, dtype=float)
        k = np.clip(np.searchsorted(self.seconds, s, side="right") - 1, 0, len(self.seconds) - 1)
        return self.ticks[k] + (s - self.seconds[k]) / self.sec_per_tick[k]

    def tick(self, seconds):
        """Ticks (float) at one second value (fast)."""
        k = max(0, bisect.bisect_right(self._secs_list, seconds) - 1)
        return self._ticks_list[k] + (seconds - self._secs_list[k]) / self._spt_list[k]

    def bpm_at(self, tick):
        k = max(0, bisect.bisect_right(self._ticks_list, tick) - 1)
        return 60e6 / float(self.us[k])

    def segments(self, t0, t1):
        """[(from tick, to tick, bpm)] covering t0..t1."""
        out = []
        n = len(self._ticks_list)
        k = max(0, bisect.bisect_right(self._ticks_list, t0) - 1)
        while k < n and self._ticks_list[k] <= t1:
            a = max(t0, self._ticks_list[k])
            b = min(t1, self._ticks_list[k + 1]) if k + 1 < n else t1
            out.append((a, b, 60e6 / float(self.us[k])))
            k += 1
        return out


class MeterMap:
    """Bars and beats from time signatures [(tick, numerator, denominator)]."""

    def __init__(self, ppq, sigs=()):
        self.ppq = ppq
        sigs = sorted({int(t): (n, d) for t, n, d in sigs if n > 0 and d > 0}.items())
        if not sigs or sigs[0][0] > 0:
            sigs.insert(0, (0, (4, 4)))
        # segments: (start tick, numerator, denominator, beat ticks, bar ticks, first bar number)
        self.segs = []
        bar = 0
        for i, (tick, (num, den)) in enumerate(sigs):
            beat = ppq * 4.0 / den
            bar_len = beat * num
            if self.segs:
                s0, _, _, _, blen0, bar0 = self.segs[-1]
                bar = bar0 + int(math.ceil((tick - s0) / blen0 - 1e-9))
            self.segs.append((tick, num, den, beat, bar_len, bar))
        self._starts = [s[0] for s in self.segs]

    def _seg(self, tick):
        return self.segs[max(0, bisect.bisect_right(self._starts, tick) - 1)]

    def signature_at(self, tick):
        s = self._seg(tick)
        return s[1], s[2]

    def beat_ticks(self, tick):
        return self._seg(tick)[3]

    def bar_ticks(self, tick):
        return self._seg(tick)[4]

    def bar_start(self, tick):
        s0, _, _, _, blen, _ = self._seg(tick)
        return s0 + math.floor((tick - s0) / blen + 1e-9) * blen

    def bar_beat(self, tick):
        """(bar number from 1, beat from 1, ticks into the beat) at tick."""
        s0, num, _, beat, blen, bar0 = self._seg(max(0, tick))
        k = math.floor((tick - s0) / blen + 1e-9)
        into = tick - s0 - k * blen
        b = int(into // beat)
        return bar0 + k + 1, b + 1, into - b * beat

    def bars_between(self, t0, t1):
        """[(tick, bar number from 1)] of the bar lines in t0..t1."""
        out = []
        for i, (s0, num, den, beat, blen, bar0) in enumerate(self.segs):
            end = self.segs[i + 1][0] if i + 1 < len(self.segs) else math.inf
            if end < t0 or s0 > t1:
                continue
            k = max(0, math.floor((max(t0, s0) - s0) / blen))
            while True:
                t = s0 + k * blen
                if t > t1 or t >= end:
                    break
                if t >= t0:
                    out.append((t, bar0 + k + 1))
                k += 1
        return out

    def lines_between(self, t0, t1, step):
        """Grid ticks every `step` ticks in t0..t1, counted from each segment's start (bar-aligned)."""
        out = []
        if step <= 0:
            return out
        for i, (s0, num, den, beat, blen, bar0) in enumerate(self.segs):
            end = self.segs[i + 1][0] if i + 1 < len(self.segs) else math.inf
            if end < t0 or s0 > t1:
                continue
            k = max(0, math.floor((max(t0, s0) - s0) / step))
            while True:
                t = s0 + k * step
                if t > t1 or t >= end:
                    break
                if t >= t0:
                    out.append(t)
                k += 1
        return out

    def snap(self, tick, step, mode="round"):
        """tick on the grid of `step` ticks (bar-aligned): nearest ('round') or the cell it's in ('floor')."""
        if step <= 0:
            return int(round(tick))
        s0 = self._seg(tick)[0]
        q = (tick - s0) / step
        q = math.floor(q + 1e-9) if mode == "floor" else math.floor(q + 0.5)
        return int(round(s0 + q * step))
