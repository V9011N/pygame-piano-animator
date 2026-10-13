"""
maestro_diff.py - Compare two runs of tools/maestro_check.py: what was fixed,
what's new, what's still there - by pattern.

    python tools/maestro_diff.py OLD NEW
    python tools/maestro_diff.py runs/round0 runs/round1 --out round1_diff.md
    python tools/maestro_diff.py OLD NEW --all          # the corrected aims and late strikes too

OLD and NEW are results.jsonl files, or the folders holding them (maestro_check's
--out). Only files checked in both runs are compared (files that failed or hit
the time limit in either are counted apart, not compared).

An incident in NEW is the same as one in OLD when it's in the same file and
hand, of the same category, and their time spans overlap (within MATCH_T). The
beam search plans a whole piece at once, so a fix in one place can move
incidents elsewhere in the file: compare the new and fixed lists, not just the
totals.

Each incident gets a pattern, coarse (hand, category, what happened - "LH
solver drawn off") and fine (+ the keys: black / white / both, the register:
bass below C3, treble from C5, middle between, one note or several, and the
fingers when the check recorded them). The tables count incidents per pattern
in each run, largest first: the progress to watch is patterns shrinking.

Output: a summary on the console and a report (--out, default diff.md beside
NEW): totals, the coarse and fine pattern tables (old, new, change, fixed, new),
then every new incident (regressions to look at) and every fixed one, grouped
by pattern, with the file and its times.
"""
import argparse
import collections
import json
import os
import re
import sys

MATCH_T = 0.3              # s, incidents this close (spans overlapping, give or take) are the same one
SEEN_KINDS = ("off", "shifted")
KIND_NAME = {"off": "drawn off", "shifted": "hand shifted", "aimed off": "aimed off (corrected)",
             "fingering": "no finger", "late": "struck late"}
_NOTE = re.compile(r"^([A-G])(#?)(-?\d+)$")
_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def load(path):
    """{file key: result} from a results.jsonl (or the folder holding it)."""
    if os.path.isdir(path):
        path = os.path.join(path, "results.jsonl")
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            key = r.get("key") or os.path.normpath(r.get("file", "")).replace(os.sep, "/")
            out[key] = r
    return out


def seen(i):
    """Would you see it? (drawn off, the hand shifted, a note not played)"""
    return i["cat"] == "fingering" or (i["cat"] != "timing" and i.get("visible", i["kind"] in SEEN_KINDS))


def _pitch(name):
    m = _NOTE.match(name)
    if not m:
        return None
    return 12 * (int(m.group(3)) + 1) + _SEMI[m.group(1)] + (1 if m.group(2) else 0)


def pattern(i, fine=False):
    """The incident's pattern: "LH solver drawn off" (coarse), + keys, register, size, fingers (fine)."""
    hand = {"R": "RH", "L": "LH"}.get(i["hand"], i["hand"])
    kind = KIND_NAME.get(i["kind"], i["kind"])
    parts = [hand, i["cat"], kind]
    if not fine:
        return " ".join(parts)
    names = sorted({n for n, _ in i["notes"]})
    ps = [p for p in (_pitch(n) for n in names) if p is not None]
    black = sum(1 for n in names if "#" in n)
    keys = "black" if black == len(names) else "white" if black == 0 else "black+white"
    lo = min(ps) if ps else 60
    reg = "bass" if lo < 48 else "treble" if lo >= 72 else "middle"
    size = "one note" if len(names) == 1 else "several"
    parts += [keys, reg, size]
    if i.get("fingers"):
        parts.append("f" + "".join(str(f) for f in i["fingers"]))
    return " · ".join(parts)


def match(old, new):
    """(persisting [(o, n)], fixed [o], new [n]) for one file's incidents."""
    left = list(old)
    pairs, fresh = [], []
    for n in new:
        best = None
        for o in left:
            if o["hand"] != n["hand"] or o["cat"] != n["cat"]:
                continue
            gap = max(o["t0"], n["t0"]) - min(o["t1"], n["t1"])
            if gap <= MATCH_T and (best is None or gap < best[0]):
                best = (gap, o)
        if best:
            left.remove(best[1])
            pairs.append((best[1], n))
        else:
            fresh.append(n)
    return pairs, left, fresh


def fmt_t(t):
    m, s = divmod(max(0.0, t), 60.0)
    return f"{int(m)}:{s:06.3f}"


def compare(old, new, all_kinds=False):
    keys = sorted(set(old) & set(new))
    usable = [k for k in keys if not old[k].get("error") and not new[k].get("error")
              and not old[k].get("timed_out") and not new[k].get("timed_out")]
    keep = (lambda i: True) if all_kinds else seen
    res = {"files": len(usable), "only_old": len(set(old) - set(new)), "only_new": len(set(new) - set(old)),
           "skipped": len(keys) - len(usable), "fixed": [], "new": [], "same": [], "old_n": 0, "new_n": 0,
           "count": {False: (collections.Counter(), collections.Counter()),
                     True: (collections.Counter(), collections.Counter())}}
    for k in usable:
        oi = [i for i in old[k].get("incidents", []) if keep(i)]
        ni = [i for i in new[k].get("incidents", []) if keep(i)]
        res["old_n"] += len(oi)
        res["new_n"] += len(ni)
        for fine in (False, True):
            res["count"][fine][0].update(pattern(i, fine) for i in oi)
            res["count"][fine][1].update(pattern(i, fine) for i in ni)
        pairs, fixed, fresh = match(oi, ni)
        res["same"] += [(k, o, n) for o, n in pairs]
        res["fixed"] += [(k, o) for o in fixed]
        res["new"] += [(k, n) for n in fresh]
    return res


def table(res, fine):
    old_c, new_c = res["count"][fine]
    fixed = collections.Counter(pattern(i, fine) for _, i in res["fixed"])
    fresh = collections.Counter(pattern(i, fine) for _, i in res["new"])
    rows = sorted(set(old_c) | set(new_c), key=lambda p: (-max(old_c[p], new_c[p]), p))
    lines = ["| pattern | old | new | change | fixed | new ones |", "|---|---:|---:|---:|---:|---:|"]
    for p in rows:
        d = new_c[p] - old_c[p]
        lines.append(f"| {p} | {old_c[p]} | {new_c[p]} | {d:+d} | {fixed[p]} | {fresh[p]} |")
    return lines, rows


def listing(items, fine=True):
    by = collections.defaultdict(list)
    for k, i in items:
        by[pattern(i, fine)].append((k, i))
    out = []
    for p in sorted(by, key=lambda p: (-len(by[p]), p)):
        out += ["", f"**{p}** ({len(by[p])})", "", "```"]
        for k, i in sorted(by[p], key=lambda x: (x[0], x[1]["t0"])):
            line = i.get("line") or f"{fmt_t(i['t0'])}  {i['kind']}"
            out.append(f"{os.path.basename(k)[:70]:<70}  {line}")
        out.append("```")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("old", help="the earlier run: results.jsonl, or maestro_check's --out folder")
    ap.add_argument("new", help="the later run")
    ap.add_argument("--out", help="the report (default: diff.md beside NEW)")
    ap.add_argument("--all", action="store_true", help="also the corrected aims and late strikes (not seen)")
    ap.add_argument("--top", type=int, default=15, help="patterns on the console (default 15)")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    res = compare(load(a.old), load(a.new), a.all)
    d = res["new_n"] - res["old_n"]
    pct = f" ({100 * d / res['old_n']:+.1f}%)" if res["old_n"] else ""
    head = (f"{res['files']} files in both runs ({res['skipped']} skipped: failed or time limit in either; "
            f"{res['only_old']} only in OLD, {res['only_new']} only in NEW). "
            f"{'All' if a.all else 'Seen'} incidents: {res['old_n']} -> {res['new_n']}{pct}; "
            f"{len(res['fixed'])} fixed, {len(res['new'])} new, {len(res['same'])} still there.")
    print(head)
    coarse, rows = table(res, False)
    print()
    for line in coarse[:2 + a.top]:
        print(line)
    out = a.out or os.path.join(a.new if os.path.isdir(a.new) else os.path.dirname(a.new) or ".", "diff.md")
    fine, _ = table(res, True)
    lines = ["# Round comparison", "", f"OLD `{a.old}`  ", f"NEW `{a.new}`", "", head, "",
             "Same incident: same file, hand and category, spans within "
             f"{MATCH_T} s. A fix can move incidents elsewhere in a file (the plan is of the whole piece): "
             "read the new ones as well as the totals.", "",
             "## By pattern", ""] + coarse + ["", "## By pattern, finer", "",
             "Keys: black / white / both; register: bass below C3, treble from C5; one note or several; "
             "fingers where recorded.", ""] + fine
    lines += ["", f"## New ({len(res['new'])})"] + listing(res["new"])
    lines += ["", f"## Fixed ({len(res['fixed'])})"] + listing(res["fixed"])
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:                       # (piped into head)
        sys.stderr.close()
