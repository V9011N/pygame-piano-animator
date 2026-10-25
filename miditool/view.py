"""
view.py - The visible range along one axis, eased toward where it's going.

Every scroll and zoom sets a target range [lo, hi]; each frame the current
range moves a share of the way there (exponential easing, frame-rate
independent). Easing both ends by the same share keeps any point that the
target leaves in place where it is on screen the whole way - so zooming at the
mouse holds the note under it still while it animates.
"""
from __future__ import annotations

import math

EASE_RATE = 22.0             # 1/s: ~90% of the way in 0.1 s
SETTLE_PX = 0.2              # closer than this (in pixels) counts as there


class Axis:
    def __init__(self, lo=0.0, hi=1.0, min_span=1e-6, max_span=1e12, bound_lo=-1e18, bound_hi=1e18):
        self.lo, self.hi = float(lo), float(hi)
        self.tlo, self.thi = float(lo), float(hi)
        self.min_span, self.max_span = min_span, max_span
        self.bound_lo, self.bound_hi = bound_lo, bound_hi
        self.pixels = 1000           # screen length of the axis (for "settled" checks)

    @property
    def span(self):
        return self.hi - self.lo

    @property
    def target_span(self):
        return self.thi - self.tlo

    def _clamp(self, lo, hi):
        span = min(max(hi - lo, self.min_span), self.max_span, self.bound_hi - self.bound_lo)
        mid = (lo + hi) / 2
        lo, hi = mid - span / 2, mid + span / 2
        if lo < self.bound_lo:
            lo, hi = self.bound_lo, self.bound_lo + span
        if hi > self.bound_hi:
            lo, hi = self.bound_hi - span, self.bound_hi
        return lo, hi

    def set_bounds(self, lo, hi):
        self.bound_lo, self.bound_hi = lo, hi
        self.tlo, self.thi = self._clamp(self.tlo, self.thi)

    def target(self, lo, hi):
        """Ease toward [lo, hi]."""
        self.tlo, self.thi = self._clamp(lo, hi)

    def jump(self, lo, hi):
        """Be at [lo, hi] now (dragging: the view follows the hand exactly)."""
        self.tlo, self.thi = self._clamp(lo, hi)
        self.lo, self.hi = self.tlo, self.thi

    def scroll_by(self, d):
        """Move the target by d (scrolling again while one eases adds up)."""
        self.target(self.tlo + d, self.thi + d)

    def zoom_at(self, factor, anchor):
        """Zoom the target by factor (< 1 zooms in) keeping world point `anchor` where it is on screen now."""
        span = self.target_span
        new = min(max(span * factor, self.min_span), self.max_span, self.bound_hi - self.bound_lo)
        f = (anchor - self.lo) / self.span if self.span else 0.5
        # the anchor's place in the target, so that when the view arrives it sits at the same screen spot
        lo = anchor - f * new
        self.tlo, self.thi = self._clamp(lo, lo + new)
        if self.tlo != lo:
            return
        # keep it still on the way there too: re-aim the current range so the anchor stays at f
        cur_lo = anchor - f * self.span
        self.lo, self.hi = cur_lo, cur_lo + self.span

    def update(self, dt):
        """Ease one frame; True while still moving."""
        if self.lo == self.tlo and self.hi == self.thi:
            return False
        k = 1.0 - math.exp(-EASE_RATE * max(0.0, min(dt, 0.1)))
        self.lo += (self.tlo - self.lo) * k
        self.hi += (self.thi - self.hi) * k
        per_px = max(self.span, 1e-12) / max(1, self.pixels)
        if abs(self.tlo - self.lo) < SETTLE_PX * per_px and abs(self.thi - self.hi) < SETTLE_PX * per_px:
            self.lo, self.hi = self.tlo, self.thi
            return False
        return True

    @property
    def moving(self):
        return self.lo != self.tlo or self.hi != self.thi
