"""
pig_eval.py - Measure the fingering planner against human fingerings from the
PIG Piano Fingering Dataset (Nakamura et al.), or any files in its format.

    python pig_eval.py PianoFingeringDataset_v1.2/FingeringFiles
    python pig_eval.py some/folder --pianist "Clara" --pieces 001,002,015

Files are named like 001-1_fingering.txt, 001-2_fingering.txt: piece 001 as
fingered by two pianists. For each piece the planner fingers the notes (the
fingering in the files is ignored; the hands come from the channels) and is
compared with every pianist's fingering, the way the dataset's paper does:

    general  match rate with each pianist, averaged     (M_gen)
    high     match rate with the pianist it agrees with most   (M_high)
    soft     share of notes matching at least one pianist  (M_soft)

The averages are over pieces. Finger substitutions ("3_1") count as their
first finger.

The dataset is free for research use; download it from
https://beam.kisarazu.ac.jp/research/PianoFingeringDataset/ after agreeing
to its terms.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
from collections import defaultdict

from fingering import finger_hand
from midi_loader import LEFT, RIGHT, read_pig


def _key(n):
    return (round(n.start, 3), n.pitch, n.hand)


def evaluate(folder, pianist=None, pieces=None, verbose=True):
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    by_piece = defaultdict(list)
    for f in files:
        m = re.match(r"^(\d+)-(\d+)", os.path.basename(f))
        pid = m.group(1) if m else os.path.splitext(os.path.basename(f))[0]
        if pieces and pid not in pieces:
            continue
        by_piece[pid].append(f)
    if not by_piece:
        print(f"No PIG files found in {folder}")
        return None
    rows = []
    t0 = time.time()
    for pid, fs in sorted(by_piece.items()):
        song = read_pig(fs[0], with_fingering=False)
        ours = {}
        for h in (RIGHT, LEFT):
            ns = [n for n in song.notes if n.hand == h]
            fing = finger_hand(ns, left=(h == LEFT), context=song.notes, pianist=pianist)
            for n in ns:
                ours[_key(n)] = fing.get(id(n))
        refs = []
        for f in fs:
            ref = read_pig(f)
            refs.append({_key(n): n.finger for n in ref.notes if n.finger})
        res = {}
        for hand in (RIGHT, LEFT, None):
            keys = [k for k in ours if hand is None or k[2] == hand]
            rates, any_hit, total = [], 0, 0
            for ref in refs:
                ks = [k for k in keys if k in ref]
                if ks:
                    rates.append(sum(1 for k in ks if ours[k] == ref[k]) / len(ks))
            for k in keys:
                have = [ref[k] for ref in refs if k in ref]
                if have:
                    total += 1
                    any_hit += ours[k] in have
            if rates:
                res[hand] = (sum(rates) / len(rates), max(rates), any_hit / max(1, total))
        rows.append((pid, len(fs), res))
        if verbose and None in res:
            g, hi, so = res[None]
            print(f"{pid}  {len(fs)} pianist(s)  general {100 * g:5.1f}%  high {100 * hi:5.1f}%  "
                  f"soft {100 * so:5.1f}%", flush=True)
    print()
    for hand, label in ((RIGHT, "right hand"), (LEFT, "left hand"), (None, "both hands")):
        vals = [r[2][hand] for r in rows if hand in r[2]]
        if vals:
            g = sum(v[0] for v in vals) / len(vals)
            hi = sum(v[1] for v in vals) / len(vals)
            so = sum(v[2] for v in vals) / len(vals)
            print(f"{label:11s}  general {100 * g:5.1f}%   high {100 * hi:5.1f}%   soft {100 * so:5.1f}%"
                  f"   ({len(vals)} pieces)")
    print(f"time {time.time() - t0:.0f}s")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder with PIG *_fingering.txt files")
    ap.add_argument("--pianist", help="name of a saved pianist (default: the active one)")
    ap.add_argument("--pieces", help="comma-separated piece numbers, e.g. 001,002")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    a = ap.parse_args()
    import pianist as pianists
    p = None
    if a.pianist:
        match = [q for q in pianists.list_pianists() if q.name.lower() == a.pianist.lower()]
        if not match:
            sys.exit(f"No pianist called {a.pianist!r}")
        p = match[0]
    pieces = set(a.pieces.split(",")) if a.pieces else None
    evaluate(a.folder, p, pieces, verbose=not a.quiet)


if __name__ == "__main__":
    main()
