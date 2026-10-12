"""
anim_check.py - Does what the hands are drawn doing match what's played?

check_song(song, hands, kb) samples every note the hands play (their
performance, which the sound follows) and reports, with times in the MIDI
file:

  - "fingering": notes the planner couldn't give a finger (not played at all);
  - key problems, from each held fingertip (HandAnimator._finger_pose /
    _settle_on_keys, "key_pulled" / "key_check"):
      off       - nothing could put it there: the tip is drawn off its key
                  (visible);
      shifted   - pulled, the finger still couldn't reach: the whole hand was
                  moved across to put it there (visible as a jolt);
      aimed off - the hand solver's tip was off its key and was pulled onto
                  it before drawing (corrected: the drawn tip is on its key,
                  the finger may look a little forced);
    how far off is a share of the key's own width, measured from its edges
    (the same at any window size: 0.5 = half the key's width beside it);
    each sorted into
      "anatomy" - the keys held at that moment are too far apart for this
                  hand (HandAnimator._chord_fits says no hand position holds
                  them) - genuinely incompatible with the pianist's anatomy;
      "speed"   - the hand is held back by the pianist's top speed (where the
                  keys want it and where it's drawn differ by more than
                  SPEED_LAG_WK), so it's still on its way;
      "solver"  - neither: reachable, in time - the hand solver's mistake;
  - "timing": notes struck later than in the file (the top speed, or a
    rolled chord) by more than LATE_T.

Problems close together in one hand (INCIDENT_GAP_T) and of one kind are
put together as one incident. Used by tools/maestro_check.py (any folder of
MIDI files, e.g. the MAESTRO dataset).
"""
import bisect
import math

import progress
from midi_loader import note_name

SAMPLE_AFTER_T = 0.03       # s, each held note is checked this long after it's struck (capped at half its length)...
OFF_KEY = 0.05              # ...drawn off its key past this share of the key's width
SHIFT_KEY = 0.15            # ...the hand shifted to put a tip this far off its key on it
AIM_KEY = 0.5               # ...the solver's tip aimed this far off its key (pulled on)
SPEED_LAG_WK = 0.5          # white keys between where the keys want the hand and where the top speed lets it be
LATE_T = 0.1                # s, struck this much later than in the file
INCIDENT_GAP_T = 0.6        # s, problems this close in one hand, of one kind, are one incident
SEVERITY = {"off": 3, "shifted": 2, "aimed off": 1}
VISIBLE = ("off", "shifted")  # (aimed off is corrected before drawing)


def fmt_t(t):
    m, s = divmod(max(0.0, t), 60.0)
    return f"{int(m)}:{s:06.3f}"


def _held_at(a, t):
    """The notes hand-animator `a` holds down at t (its performance)."""
    return [n for s, e, n in a.performance if s <= t < e]


def _lag(a, t):
    """White keys between where the keys want the hand (placement + key fit) and where it's drawn (speed-limited)."""
    try:
        want = a._key_fix(t, *a._placed_at(t))[0]
        drawn = a._limited_at(t)[0]
        return abs(want - drawn) / a.kb.white_w
    except Exception:
        return 0.0


def check_song(song, hands, kb, deadline=None):
    """
    {"notes": n, "samples": n, "problems": [...], "incidents": [...], "timed_out": bool}
    (see the module's notes). `deadline` (time.perf_counter()) stops early. Reports how far
    it's got (progress.py).
    """
    import time
    problems = []
    samples = 0
    timed_out = False
    # every held note, a moment after it's struck, in time order (the hand's caches follow on)
    sample_times = {h: sorted({round(s + min(SAMPLE_AFTER_T, max(0.0, (e - s) / 2)), 4)
                               for s, e, n in a.performance if e > s and not a.is_gliss(n)})
                    for h, a in hands.items()}
    total = max(1, sum(len(ts) for ts in sample_times.values()))
    for h, a in hands.items():
        a._ensure_layout(kb)
        for n in getattr(a, "unplaced", []):
            problems.append({"hand": h, "t": n.start, "kind": "fingering", "cat": "fingering",
                             "notes": [(note_name(n.pitch), n.start)], "off": 0.0,
                             "what": "no finger: not played"})
        for s, e, n in a.performance:
            if s - n.start > LATE_T:
                problems.append({"hand": h, "t": n.start, "kind": "late", "cat": "timing",
                                 "notes": [(note_name(n.pitch), n.start)], "off": 0.0,
                                 "what": f"struck {s - n.start:.2f} s late"})
        found = []
        for t in sample_times[h]:
            if timed_out or (deadline is not None and time.perf_counter() > deadline):
                timed_out = True
                break
            p = a.pose(t, kb)
            samples += 1
            if samples % 25 == 0:
                progress.report(samples / total)
            pulled = p.get("key_pulled", {})
            for f, n, before, after in p.get("key_check", []):
                aim = pulled.get(f, 0.0)
                if after > OFF_KEY:
                    kind, off = "off", after
                elif before > SHIFT_KEY:
                    kind, off = "shifted", before
                elif aim > AIM_KEY:
                    kind, off = "aimed off", aim
                else:
                    continue
                found.append((t, f, n, kind, off))
        # what caused each: the keys held then too far apart (anatomy), the top speed, or the solver
        lags = {t: _lag(a, t) for t in {x[0] for x in found}}
        held = {t: _held_at(a, t) for t in lags}
        fits = {}
        for t, f, n, kind, off in found:
            chord = held[t]
            key = frozenset(id(m) for m in chord)
            if key not in fits:
                fits[key] = len(chord) < 2 or a._chord_fits(chord)
            if not fits[key]:
                cat = "anatomy"
            elif lags[t] > SPEED_LAG_WK:
                cat = "speed"
            else:
                cat = "solver"
            problems.append({"hand": h, "t": t, "kind": kind, "cat": cat, "finger": f,
                             "notes": [(note_name(n.pitch), n.start)], "off": off,
                             "what": f"finger {f} {kind} {100 * off:.0f}% of its key"})
        a._layout_sig = None                     # (_chord_fits laid it out on its own keyboard)
        a._ensure_layout(kb)
    n_notes = sum(len(a.performance) for a in hands.values())
    return {"notes": n_notes, "samples": samples, "problems": problems,
            "incidents": incidents(problems), "timed_out": timed_out}


def _visible(p):
    return p["kind"] in VISIBLE or p["cat"] == "fingering"


def incidents(problems):
    """
    Problems close together (INCIDENT_GAP_T) in one hand, of one category and alike in being seen or
    not ("visible": drawn off its key, the hand jolted across, a note not played - or corrected before
    drawing), as incidents, in time order.
    """
    out = []
    for p in sorted(problems, key=lambda p: (p["hand"], p["cat"], _visible(p), p["t"])):
        last = out[-1] if out else None
        if (last and last["hand"] == p["hand"] and last["cat"] == p["cat"] and last["visible"] == _visible(p)
                and p["t"] - last["t1"] <= INCIDENT_GAP_T):
            last["t1"] = max(last["t1"], p["t"])
            last["notes"] += p["notes"]
            last["off"] = max(last["off"], p["off"])
            last["count"] += 1
            if p.get("finger") and p["finger"] not in last["fingers"]:
                last["fingers"] = sorted(last["fingers"] + [p["finger"]])
            if SEVERITY.get(p["kind"], 0) > SEVERITY.get(last["kind"], 0):
                last["kind"] = p["kind"]
        else:
            out.append({"hand": p["hand"], "cat": p["cat"], "kind": p["kind"], "visible": _visible(p),
                        "t0": p["t"], "t1": p["t"], "notes": list(p["notes"]), "off": p["off"], "count": 1,
                        "fingers": [p["finger"]] if p.get("finger") else []})
    return sorted(out, key=lambda i: i["t0"])


def describe(inc, max_notes=6):
    """One readable line for an incident."""
    hand = {"R": "RH", "L": "LH"}.get(inc["hand"], inc["hand"])
    span = fmt_t(inc["t0"]) if inc["t1"] - inc["t0"] < 0.001 else f"{fmt_t(inc['t0'])} - {fmt_t(inc['t1'])}"
    seen, names = set(), []
    for name, t in inc["notes"]:
        if (name, round(t, 3)) not in seen:
            seen.add((name, round(t, 3)))
            names.append(f"{name}@{fmt_t(t)}")
    more = f" (+{len(names) - max_notes} more)" if len(names) > max_notes else ""
    what = {"fingering": "no finger, not played", "timing": "struck late"}.get(inc["cat"])
    if what is None:                             # (how far off: a share of the key's width)
        pct = f"{100 * inc['off']:.0f}%"
        what = {"off": f"drawn off by {pct}", "shifted": f"hand shifted, tip {pct} off",
                "aimed off": f"aimed off by {pct}"}.get(inc["kind"], f"{inc['kind']} {pct}")
    return f"{span:<21} {hand}  {inc['cat']:<9} {what:<26} {', '.join(names[:max_notes])}{more}"
