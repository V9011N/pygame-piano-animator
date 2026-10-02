"""
progress.py - How far a long job (loading a song, planning the hands) has got.

The job runs in a worker thread (common.run_busy) while the window draws a
progress bar from value(). Deep loops call report(fraction done); stage(lo, hi)
maps the fractions reported inside it onto lo..hi of the enclosing stage, so
each part of a job reports 0..1 without knowing what surrounds it. When no
job is being tracked, report does nothing (the same code runs without a bar).
"""
from contextlib import contextmanager

_active = False
_value = 0.0
_span = (0.0, 1.0)


def begin():
    global _active, _value, _span
    _active, _value, _span = True, 0.0, (0.0, 1.0)


def end():
    global _active
    _active = False


def value():
    """The whole job's fraction done, 0..1."""
    return _value


def report(frac):
    """The current stage is `frac` (0..1) done."""
    global _value
    if _active:
        lo, hi = _span
        _value = max(_value, lo + (hi - lo) * min(1.0, max(0.0, frac)))


@contextmanager
def stage(lo, hi):
    """The work inside fills lo..hi (fractions) of the current stage."""
    global _span
    outer = _span
    a, b = outer
    _span = (a + (b - a) * lo, a + (b - a) * hi)
    try:
        yield
    finally:
        _span = outer
        report(hi)
