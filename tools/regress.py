"""
regress.py - One round's checks on the fingering and the hands, side by side
with the rounds before.

    python tools/regress.py --label round1 --pianist ninthie-1.json \\
        --pig PianoFingeringDataset/FingeringFiles --hanon "Hanon MIDI" \\
        --bench bench_files --baseline runs/round0

Each part runs if its data is given:
  tests     pytest -q (--no-tests to skip);
  --pig     the PIG dataset: match rates on its test split (pieces 001-030, the
            reference in docs/ARCHITECTURE.md) and on all its pieces (pig_eval.py);
  --hanon   MIDI files with the fingering written in (the Hanon book's): the
            share of fingered notes the planner fingers otherwise (the files'
            fingering ignored);
  --bench   a folder of MIDI files (a sample of MAESTRO): tools/maestro_check.py
            into runs/<label> (or --runs), compared with --baseline (an earlier
            run's folder) by tools/maestro_diff.py - seen incidents, fixed, new.

The round's numbers are added to history.jsonl in the runs folder, and the
history is printed: each round's tests, PIG test / all, Hanon, and the bench's
seen incidents (with fixed / new against its baseline).
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                       capture_output=True, text=True)
    last = [ln for ln in p.stdout.splitlines() if ln.strip()][-1:] or ["(no output)"]
    return {"ok": p.returncode == 0, "line": last[0]}


def run_pig(folder, pianist):
    import pig_eval
    with contextlib.redirect_stdout(io.StringIO()):
        rows = pig_eval.evaluate(folder, pianist, None, verbose=False) or []
    test, both = pig_eval.summary(rows, set(pig_eval.TEST)), pig_eval.summary(rows)
    if None not in both:
        return None
    return {"test": [round(100 * x, 2) for x in test[None][:3]] if None in test else None,
            "all": [round(100 * x, 2) for x in both[None][:3]], "pieces": both[None][3]}


def run_hanon(path, pianist):
    """Share (%) of the fingered notes in the files the planner fingers otherwise, both hands."""
    import midi_loader
    from fingering import finger_hand
    files = [path] if os.path.isfile(path) else sorted(
        os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs if f.lower().endswith((".mid", ".midi")))
    total = differ = 0
    for f in files:
        with contextlib.redirect_stdout(io.StringIO()):
            song = midi_loader.load_song(f)
        for hand in (midi_loader.RIGHT, midi_loader.LEFT):
            ns = [n for n in song.notes if n.hand == hand]
            book = {i: n.finger for i, n in enumerate(ns) if n.finger}
            if not book:
                continue
            bare = [replace(n, finger=None) for n in ns]
            ours = finger_hand(bare, left=(hand == midi_loader.LEFT), context=song.notes, pianist=pianist)
            for i, fb in book.items():
                total += 1
                differ += ours.get(id(bare[i])) != fb
    return {"files": len(files), "notes": total, "differ_pct": round(100 * differ / total, 2) if total else None}


def run_bench(folder, out, pianist_arg, workers, baseline):
    cmd = [sys.executable, os.path.join(TOOLS, "maestro_check.py"), folder, "--out", out,
           "--workers", str(workers), "--progress-every", "600", "--timeout", "0"]
    if pianist_arg:
        cmd += ["--pianist", pianist_arg]
    subprocess.run(cmd, check=False)
    import maestro_diff
    new = maestro_diff.load(out)
    seen = sum(1 for r in new.values() for i in r.get("incidents", []) if maestro_diff.seen(i))
    res = {"files": len(new), "seen": seen,
           "by_pattern": dict(__import__("collections").Counter(
               maestro_diff.pattern(i) for r in new.values() for i in r.get("incidents", []) if maestro_diff.seen(i)))}
    if baseline:
        d = maestro_diff.compare(maestro_diff.load(baseline), new)
        res.update(baseline=baseline, fixed=len(d["fixed"]), new=len(d["new"]), base_seen=d["old_n"])
    return res


def show(history):
    print()
    print(f"{'round':<16} {'when':<16} {'commit':<8} {'tests':<6} {'PIG test':>9} {'PIG all':>8} {'Hanon':>7} "
          f"{'bench seen':>10} {'fixed':>6} {'new':>5}")
    for h in history:
        pig, ha, b = h.get("pig") or {}, h.get("hanon") or {}, h.get("bench") or {}
        tests = h.get("tests")
        print(f"{h['label'][:16]:<16} {h['when']:<16} {h.get('commit', '')[:8]:<8} "
              f"{'-' if tests is None else 'ok' if tests['ok'] else 'FAIL':<6} "
              f"{(str(pig['test'][0]) + '%') if pig.get('test') else '-':>9} "
              f"{(str(pig['all'][0]) + '%') if pig.get('all') else '-':>8} "
              f"{(str(ha['differ_pct']) + '%') if ha.get('differ_pct') is not None else '-':>7} "
              f"{b.get('seen', '-'):>10} {b.get('fixed', '-'):>6} {b.get('new', '-'):>5}")
    print("(PIG: general match, both hands; Hanon: notes fingered unlike the book; bench: seen incidents)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--label", default=time.strftime("%Y%m%d-%H%M"), help="the round's name (default: now)")
    ap.add_argument("--pianist", help="a pianist's .json, or a saved pianist's name (default: the default pianist)")
    ap.add_argument("--pig", help="the PIG dataset's FingeringFiles folder")
    ap.add_argument("--hanon", help="a MIDI file or folder with the book's fingering written in")
    ap.add_argument("--bench", help="a folder of MIDI files to run the hands' check on")
    ap.add_argument("--baseline", help="an earlier bench run's folder (runs/<label>) to compare with")
    ap.add_argument("--runs", default="runs", help="where bench runs and history.jsonl go (default ./runs)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-tests", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pianist as pianists
    import pig_eval
    p = pig_eval.load_pianist(a.pianist) if a.pianist else pianists.default_pianist()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    rec = {"label": a.label, "when": time.strftime("%Y-%m-%d %H:%M"), "commit": commit, "pianist": p.name}
    if not a.no_tests:
        print("tests ...", flush=True)
        rec["tests"] = run_tests()
        print("  " + rec["tests"]["line"])
    if a.pig:
        print("PIG ...", flush=True)
        rec["pig"] = run_pig(a.pig, p)
        print(f"  {rec['pig']}")
    if a.hanon:
        print("Hanon ...", flush=True)
        rec["hanon"] = run_hanon(a.hanon, p)
        print(f"  {rec['hanon']}")
    if a.bench:
        print("bench ...", flush=True)
        out = os.path.join(a.runs, a.label)
        rec["bench"] = run_bench(a.bench, out, a.pianist, a.workers, a.baseline)
        print(f"  seen {rec['bench']['seen']}" + (f", fixed {rec['bench']['fixed']}, new {rec['bench']['new']} "
                                                 f"against {a.baseline}" if a.baseline else ""))
    os.makedirs(a.runs, exist_ok=True)
    hist_path = os.path.join(a.runs, "history.jsonl")
    with open(hist_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    with open(hist_path, encoding="utf-8") as fh:
        history = [json.loads(ln) for ln in fh if ln.strip()]
    show(history)


if __name__ == "__main__":
    main()
