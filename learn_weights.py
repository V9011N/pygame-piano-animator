"""
learn_weights.py - Fit the fingering planner's cost weights to how real
pianists finger real music: the PIG Piano Fingering Dataset.

    python learn_weights.py FingeringFiles              # learn, then test
    python learn_weights.py FingeringFiles --test-only  # just score the current weights

The dataset's standard split is used: pieces 031-150 to learn from, pieces
001-030 (each fingered by 4-6 pianists) held out to test on, so the test
numbers compare with published results. The planner itself stays the same
(a search over fingerings under a cost model); what is learned is how much
each kind of cost counts - stretches, crossings, weak fingers, black keys,
shifts against the clock, figure suggestions and so on.

The search is a coordinate search on the "general" match rate (agreement
with each pianist, averaged): each weight in turn is scaled up and down, and
a change is kept if the training pieces agree better with the pianists.
The result is written to learned_weights.json; fingering.py carries the
values it was released with.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from multiprocessing import Pool

import figures as FI
import fingering as FG
from midi_loader import LEFT, RIGHT, read_pig

TRAIN = [f"{i:03d}" for i in range(31, 151)]
TEST = [f"{i:03d}" for i in range(1, 31)]

# weights of the cost model (fingering.W) and of the figure suggestions (figures.W_*)
# The figure suggestions (the "figure" weight and figures.W_*) are left alone by
# default: they encode standard fingerings (scales, arpeggios, octaves, thirds)
# that a model tuned only on this dataset would water down; --tune-figures
# includes them.
TUNE_W = ["stretch", "cramp", "leap", "leap_dist", "cross", "cross_2", "cross_4", "cross_wide",
          "cross_thumb_black", "awkward", "same_finger", "same_finger_dist", "same_key_new_finger",
          "repeat_fast", "thumb_black", "pinky_black", "finger_4", "finger_5", "chord_stretch",
          "chord_cramp", "chord_stretch_adj", "same_shape", "shape_shift",
          "octave_4_white", "same_finger_fast", "shift_base", "shift_speed", "shift_cost",
          "steal", "velocity"]
FIGURE_W = ["figure"]
TUNE_FIG = ["W_SCALE", "W_CHROM", "W_ARP", "W_OCT", "W_THIRDS", "W_SIXTHS", "W_REP", "W_TRILL"]

_DATA = []          # [(piece id, [(hand, notes)], context notes, [ref {key: finger}])]


def _key(n):
    return (round(n.start, 3), n.pitch, n.hand)


def load(folder, pieces):
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    by = {}
    for f in files:
        m = re.match(r"^(\d+)-(\d+)", os.path.basename(f))
        if m and m.group(1) in pieces:
            by.setdefault(m.group(1), []).append(f)
    data = []
    for pid, fs in sorted(by.items()):
        song = read_pig(fs[0], with_fingering=False)
        hands = [(h, [n for n in song.notes if n.hand == h]) for h in (RIGHT, LEFT)]
        refs = [{_key(n): n.finger for n in read_pig(f).notes if n.finger} for f in fs]
        data.append((pid, hands, song.notes, refs))
    return data


def current_params(figures=False):
    p = {k: FG.BASE_W[k] for k in TUNE_W}
    if figures:
        p.update({k: FG.BASE_W[k] for k in FIGURE_W})
        p.update({k: getattr(FI, k) for k in TUNE_FIG})
    return p


def apply(params):
    for k, v in params.items():
        if k in FG.BASE_W:
            FG.BASE_W[k] = v
        else:
            setattr(FI, k, v)
    FG._applied = "stale"                  # make apply_pianist rebuild W from BASE_W


def _piece_scores(args):
    params, idx = args
    apply(params)
    import pianist
    p = pianist.default_pianist()
    out = []
    for i in idx:
        pid, hands, context, refs = _DATA[i]
        ours = {}
        for h, ns in hands:
            if ns:
                f = FG.finger_hand(ns, left=(h == LEFT), context=context, pianist=p)
                ours.update({_key(n): f.get(id(n)) for n in ns})
        rates = []
        for ref in refs:
            ks = [k for k in ours if k in ref]
            if ks:
                rates.append(sum(1 for k in ks if ours[k] == ref[k]) / len(ks))
        out.append(sum(rates) / len(rates) if rates else 0.0)
    return out


def score(pool, params, n_jobs):
    idx = list(range(len(_DATA)))
    chunks = [idx[k::n_jobs] for k in range(n_jobs)]
    parts = pool.map(_piece_scores, [(params, c) for c in chunks])
    return 100.0 * sum(sum(p) for p in parts) / len(idx)


def coordinate_search(pool, params, n_jobs, factors=((2.0, 0.5), (1.5, 0.67), (1.2, 0.83)), log=print):
    best = score(pool, params, n_jobs)
    log(f"start: {best:.2f}%")
    for fs in factors:
        improved = True
        while improved:
            improved = False
            for k in list(params):
                for f in fs:
                    trial = dict(params)
                    trial[k] = params[k] * f if params[k] else 0.05
                    s = score(pool, trial, n_jobs)
                    if s > best + 0.03:
                        log(f"  {k:20s} {params[k]:.4g} -> {trial[k]:.4g}   {best:.2f}% -> {s:.2f}%")
                        params, best, improved = trial, s, True
                        break
    return params, best


def main():
    global _DATA
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--test-only", action="store_true")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--out", default="learned_weights.json")
    ap.add_argument("--tune-figures", action="store_true", help="also tune the figure suggestion weights")
    a = ap.parse_args()
    t0 = time.time()
    if not a.test_only:
        _DATA = load(a.folder, set(TRAIN))
        print(f"{len(_DATA)} training pieces")
        with Pool(a.jobs) as pool:
            params, best = coordinate_search(pool, current_params(a.tune_figures), a.jobs)
        with open(a.out, "w") as fh:
            json.dump(params, fh, indent=2)
        print(f"training general match {best:.2f}%  ->  {a.out}  ({time.time() - t0:.0f}s)")
        apply(params)
    _DATA = load(a.folder, set(TEST))
    with Pool(a.jobs) as pool:
        s = score(pool, current_params(), a.jobs)
    print(f"test (pieces 001-030) general match {s:.2f}%")


if __name__ == "__main__":
    main()
