"""
maestro_check.py - Check the hand animation against every MIDI file in a folder
(made for the MAESTRO dataset, any folder of .mid / .midi works).

Each file is loaded and planned as the app does (hand split, fingering, the
hands' animation), then anim_check.check_song samples every note the hands
play and reports, with times in the MIDI file, where the drawn hand doesn't
match what's played - notes with no finger, fingertips off their keys
(aimed off and pulled on, the hand shifted, or left off) - and why: the
keys held too far apart for the hand ("anatomy"), the hand held back by its
top speed ("speed"), or neither ("solver": a mistake to fix).

    python tools/maestro_check.py PATH/TO/MAESTRO
    python tools/maestro_check.py PATH --pianist Ninthie --map rename_map.csv
    python tools/maestro_check.py PATH --match Chopin --limit 20 --workers 6
    python tools/maestro_check.py PATH --report-only          # rebuild the report from what's done

PATH is the dataset folder (searched recursively; one file works too). The
files may keep MAESTRO's names or be renamed to the piece they play,
"YYYY/MAESTRO - YYYY - Composer Piece.midi"; a rename map (a .csv with the
columns original,renamed, paths relative to PATH - --map, or rename_map.csv
in PATH or the folder above it) adds each file's original MAESTRO name to
the report. MAESTRO's own maestro-v3.0.0.csv in PATH, if it's there, adds
the canonical composer, title and split. A file whose name differs a little
from the map's (case, accents, spacing, punctuation, folder) is still found.

--pianist is one of the app's pianists by name (or id), or the path to a
pianist's .json; it's checked before anything starts.

Output (--out, default ./maestro_check):
  results.jsonl  one line per file, appended as each finishes - run again to
                 carry on where it stopped (keyed by the path in PATH, so the
                 folder can move);
  report.md      summary, the files with the most solver incidents first, then
                 every file's incidents, worst first: the piece, the file to
                 open, and one line per incident with its times in the file;
  incidents.csv  one row per incident listed in report.md (piece, year, file,
                 original name, hand, category, seen, offset, times, notes), to
                 sort and filter in a spreadsheet.
"""
import argparse
import concurrent.futures as cf
import contextlib
import csv
import io
import json
import multiprocessing
import os
import sys
import shutil
import re
import tempfile
import threading
import time
import unicodedata
from multiprocessing.managers import SyncManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CATS = ("solver", "speed", "anatomy", "fingering", "timing", "corrected")
SEEN = CATS[:4]             # what shows: a tip drawn off its key, the hand jolted across, a note not played
RESULTS_FORMAT = 2          # results.jsonl lines from an older check (pixels, no "corrected") are done again
PLAN_SHARE = 0.3            # of a file's work, loading and planning the hands (the rest: checking every note)


# --------------------------------------------------------------------------- #
# One file (in a worker process)
# --------------------------------------------------------------------------- #
_OPTS = {}


_NOW = {"key": None, "phase": "", "start": 0.0}


def _heartbeat(status):
    """Tell the main process, every second, which file this worker is on and how far it's got."""
    import progress
    pid = os.getpid()
    while True:
        key = _NOW["key"]
        try:
            if key is None:
                status.pop(pid, None)
            else:
                status[pid] = (key, _NOW["phase"], progress.value(), _NOW["start"])
        except Exception:                             # (the main process has gone)
            return
        time.sleep(1.0)


def resolve_pianist(arg):
    """
    --pianist: a pianist's .json (a path, from here or the app's folder), or the name or id of one of the
    app's pianists (its pianists folder) -> (the .json's absolute path or None, the pianist's name).
    Stops with the pianists there if there's no such pianist.
    """
    import pianist
    if not arg:
        return None, "the default pianist"
    for p in (arg, os.path.join(ROOT, arg), os.path.join(pianist.FOLDER, arg),
              os.path.join(pianist.FOLDER, arg + ".json")):
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    d = json.load(fh)
                return os.path.abspath(p), str(pianist.Pianist.from_json(d.get("id", "checked"), d).name)
            except Exception as exc:
                sys.exit(f"--pianist {arg}: {p} isn't a pianist's file ({type(exc).__name__}: {exc})")
    want = os.path.splitext(os.path.basename(arg))[0] if arg.lower().endswith(".json") else arg
    want = want.strip().casefold()
    pianists = pianist.list_pianists()
    for pn in pianists:
        if want in (pn.name.strip().casefold(), pn.id.casefold()):
            return os.path.abspath(pianist._path(pn.id)), pn.name
    there = ", ".join(f"{pn.name} ({pn.id}.json)" for pn in pianists) or "none"
    sys.exit(f"--pianist {arg}: no such file, and no pianist of that name in {pianist.FOLDER} (there: {there}). "
             f"Give a pianist's name, or the path to its .json.")


def _init_worker(opts, status=None):
    """A worker: headless, the chosen pianist, its own settings folder (the user's pianists left alone)."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    sys.path.insert(0, ROOT)
    import pianist
    pianist.FOLDER = tempfile.mkdtemp(prefix="maestro_check_")
    pianist.SETTINGS = os.path.join(pianist.FOLDER, "settings.json")
    if opts.get("pianist"):
        with open(opts["pianist"], encoding="utf-8") as fh:
            d = json.load(fh)
        pianist._active = pianist.Pianist.from_json(d.get("id", "checked"), d)
    else:
        pianist._active = pianist.default_pianist()
    _OPTS.update(opts)
    if status is not None:
        threading.Thread(target=_heartbeat, args=(status,), daemon=True).start()


def check_file(path, key=None):
    """{file, notes, incidents, counts, seconds, error, log} for one MIDI file."""
    import pygame                                    # noqa: F401 (Rect)
    import anim_check
    import hands
    import midi_loader
    import progress
    from common import Keyboard, bottom_layout
    t_start = time.perf_counter()
    _NOW.update(key=key or path, phase="planning", start=time.time())
    progress.begin()
    out = {"file": path, "notes": 0, "samples": 0, "incidents": [], "counts": {}, "error": None,
           "timed_out": False, "log": []}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            song, an = hands.load_with_hands(path)
            w, h = _OPTS["size"]
            kb = Keyboard(bottom_layout((w, h))[0], style=_OPTS["keys"])
            deadline = time.perf_counter() + _OPTS["timeout"] if _OPTS["timeout"] else None
            _NOW["phase"] = "checking"
            progress.begin()
            res = anim_check.check_song(song, an, kb, deadline=deadline)
        out.update(notes=res["notes"], samples=res["samples"], timed_out=res["timed_out"],
                   duration=round(song.duration, 1))
        out["incidents"] = [dict(i, line=anim_check.describe(i)) for i in res["incidents"]]
        for i in res["incidents"]:
            c = i["cat"] if i["visible"] or i["cat"] == "timing" else "corrected"
            out["counts"][c] = out["counts"].get(c, 0) + 1
        out["format"] = RESULTS_FORMAT
    except Exception as exc:
        import traceback
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["log"] = [ln for ln in buf.getvalue().splitlines() if ln.strip()][:200]
    out["seconds"] = round(time.perf_counter() - t_start, 1)
    progress.end()
    _NOW["key"] = None
    return out


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def find_midis(root):
    if os.path.isfile(root):
        return [root]
    found = []
    for d, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith((".mid", ".midi")):
                found.append(os.path.join(d, f))
    return sorted(found)


def file_key(path, root):
    """A file's key in results.jsonl: its path in the dataset folder (so the folder can move and a run still resumes)."""
    if root and os.path.isdir(root):
        try:
            return os.path.normpath(os.path.relpath(path, root)).replace(os.sep, "/")
        except ValueError:                            # (another drive)
            pass
    return os.path.normpath(path).replace(os.sep, "/")


def find_rename_map(root, given=None):
    """The rename map (original,renamed): --map, else rename_map.csv in the dataset folder or the one above it."""
    if given:
        return given
    if not root or not os.path.isdir(root):
        return None
    for d in (root, os.path.dirname(root)):
        p = os.path.join(d, "rename_map.csv")
        if os.path.isfile(p):
            return p
    return None


def piece_of(rel):
    """("Composer Piece", "year") from a renamed file's name, "YYYY/MAESTRO - YYYY - Composer Piece.midi"."""
    base = os.path.splitext(os.path.basename(rel))[0]
    if base.upper().startswith("MAESTRO - "):
        base = base[len("MAESTRO - "):]
    year, sep, piece = base.partition(" - ")
    if sep and year.strip().isdigit():
        return piece.strip(), year.strip()
    return base, ""


def _loose(name):
    """A file's name to match on, whatever its folder, case, accents, punctuation or spacing."""
    n = unicodedata.normalize("NFKD", os.path.splitext(os.path.basename(name))[0])
    n = "".join(ch for ch in n if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", n).strip()


def dataset_meta(root, map_path=None):
    """
    ({file key: {"piece", "year", "original", "composer", "title", "split"}}, how the map matched) for the
    files in the dataset folder: the piece from each file's (renamed) name, its original MAESTRO name from the
    rename map, and the composer, title and split from MAESTRO's own .csv (keyed by the original names) when
    that's there too. A file is found in the map by its path, or failing that by its name alone, loosely
    (_loose); files that kept their MAESTRO names are named from the map's renamed column.
    """
    meta, info = {}, {"map": 0, "exact": 0, "loose": 0, "not_in_map": [], "not_on_disk": []}
    if not root or not os.path.isdir(root):
        return meta, info
    pairs = []                                        # (original key, renamed key)
    if map_path and os.path.isfile(map_path):
        with open(map_path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                o, r = (row.get("original") or "").strip(), (row.get("renamed") or "").strip()
                if o and r:
                    pairs.append((os.path.normpath(o).replace(os.sep, "/"), os.path.normpath(r).replace(os.sep, "/")))
    info["map"] = len(pairs)
    exact, loose = {}, {}
    for o, r in pairs:
        exact[r] = exact[o] = (o, r)
        for k in (r, o):
            loose.setdefault(_loose(k), []).append((o, r))
    canon = {}                                        # original key -> MAESTRO's own row
    for f in os.listdir(root):
        if f.lower().startswith("maestro") and f.lower().endswith(".csv"):
            with open(os.path.join(root, f), encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    k = os.path.normpath(row.get("midi_filename", "")).replace(os.sep, "/")
                    canon[k] = row
    used = set()
    for key in sorted(file_key(f, root) for f in find_midis(root)):
        pair = exact.get(key)
        if pair:
            info["exact"] += 1
        elif pairs:
            found = loose.get(_loose(key), [])
            if len(set(found)) == 1:
                pair = found[0]
                info["loose"] += 1
            else:
                info["not_in_map"].append(key)
        orig, renamed = pair if pair else ("", key)
        if pair:
            used.add(pair)
        piece, year = piece_of(renamed)
        row = canon.get(orig or key, {})
        meta[key] = {"piece": piece, "year": year or row.get("year", ""), "original": orig,
                     "composer": row.get("canonical_composer", ""), "title": row.get("canonical_title", ""),
                     "split": row.get("split", "")}
    info["not_on_disk"] = sorted(r for o, r in pairs if (o, r) not in used)
    return meta, info


def label(key, meta):
    """How a file is named in the report: "Composer Piece (year)"."""
    m = meta.get(key)
    if m is None:
        piece, year = piece_of(key)
    else:
        piece, year = m["piece"], m["year"]
    return f"{piece} ({year})" if year else piece


def load_results(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    if str(r.get("error") or "").startswith("worker failed"):
                        continue                      # (the pool broke, not the file: check it again)
                    if not r.get("error") and r.get("format") != RESULTS_FORMAT:
                        continue                      # (an older check's: done again)
                    done[r.get("key") or os.path.normpath(r["file"]).replace(os.sep, "/")] = r
                except (ValueError, KeyError):
                    pass
    return done


def fmt_t(t):
    m, s = divmod(max(0.0, t), 60.0)
    return f"{int(m)}:{s:06.3f}"


def fmt_dur(s):
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def run(args, meta):
    files = find_midis(args.path)
    keys = {f: file_key(f, args.path) for f in files}
    if os.path.isdir(args.path) and args.map:
        info = args.map_info
        print(f"Rename map {args.map} ({info['map']} files): {info['exact'] + info['loose']} of the {len(files)} files "
              f"here found in it" + (f" ({info['loose']} by name only)" if info["loose"] else ""))
        if info["not_in_map"]:
            print(f"  {len(info['not_in_map'])} files here aren't in the map (they're checked all the same, "
                  f"named from their file names), e.g.:")
            for k in info["not_in_map"][:3]:
                print(f"    {k}")
        if info["not_on_disk"]:
            print(f"  {len(info['not_on_disk'])} of the map's files aren't here, e.g.:")
            for k in info["not_on_disk"][:3]:
                print(f"    {k}")
    if args.match:
        files = [f for f in files if args.match.lower() in keys[f].lower()
                 or args.match.lower() in label(keys[f], meta).lower()]
    if args.limit:
        files = files[:args.limit]
    os.makedirs(args.out, exist_ok=True)
    res_path = os.path.join(args.out, "results.jsonl")
    done = load_results(res_path)
    todo = [f for f in files if keys[f] not in done]
    print(f"{len(files)} MIDI files, {len(files) - len(todo)} already checked, {len(todo)} to go, "
          f"{args.workers} workers -> {args.out}")
    if not todo:
        return
    opts = {"pianist": args.pianist, "size": args.size, "keys": args.keys, "timeout": args.timeout}
    sizes = {f: max(1, os.path.getsize(f)) for f in todo}     # (the work a file is, roughly: its notes)
    status_view = Status(args.progress_every)
    by_key = {keys[f]: f for f in todo}
    totals = {c: 0 for c in CATS}
    done_work, n_done = 0, 0
    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("spawn")
    mgr = SyncManager(ctx=ctx)
    mgr.start(_ignore_sigint)
    status = mgr.dict()
    ex = cf.ProcessPoolExecutor(args.workers, mp_context=ctx, initializer=_init_worker, initargs=(opts, status))
    stopped = False
    broken = None
    try:
        with open(res_path, "a", encoding="utf-8") as fh:
            futs = {ex.submit(check_file, f, keys[f]): f for f in todo}
            pending = set(futs)
            while pending and broken is None:
                fin, pending = cf.wait(pending, timeout=1.0, return_when=cf.FIRST_COMPLETED)
                for fut in fin:
                    f = futs[fut]
                    try:
                        r = fut.result()
                    except cf.process.BrokenProcessPool as exc:
                        broken = exc                  # (nothing recorded: the file is checked on the next run)
                        continue
                    except Exception as exc:
                        r = {"file": f, "error": f"worker failed: {exc}", "incidents": [], "counts": {}}
                    r["key"] = keys[f]
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    n_done += 1
                    done_work += sizes[f]
                    c = r.get("counts", {})
                    for x in CATS:
                        totals[x] += c.get(x, 0)
                    state = (f"FAILED: {r['error']}" if r.get("error") else
                             "  ".join(f"{c.get(x, 0):>3} {x}" for x in SEEN) + f"   ({c.get('corrected', 0)} corrected)" +
                             ("  (time limit: stopped early)" if r.get("timed_out") else ""))
                    status_view.log(f"[{n_done:>{len(str(len(todo)))}}/{len(todo)}] {fmt_dur(r.get('seconds', 0)):>7}  "
                                    f"{label(keys[f], meta)}\n{'':>{2 * len(str(len(todo))) + 12}}{state}")
                # what the workers are on, and how far the whole run has got
                try:
                    now = dict(status)
                except Exception:
                    now = {}
                busy, part = [], 0.0
                running = {futs[x] for x in pending}
                for key, phase, frac, started in now.values():
                    f = by_key.get(key)
                    if f is None or f not in running:
                        continue
                    share = PLAN_SHARE * frac if phase == "planning" else PLAN_SHARE + (1 - PLAN_SHARE) * frac
                    part += sizes[f] * share
                    busy.append((label(key, meta), phase, frac, time.time() - started))
                el = time.perf_counter() - t0
                work = (done_work + part) / sum(sizes.values())
                eta = f"about {fmt_dur(el / work * (1 - work))} left" if work > 0.002 and el > 20 else "estimating time left"
                head = (f"{n_done} of {len(todo)} files done, {100 * work:.1f}% of the work - {fmt_dur(el)} so far, "
                        f"{eta} - seen so far: " + ", ".join(f"{totals[x]} {x}" for x in SEEN))
                rows = [f"    {phase:<8} {100 * frac:>3.0f}%  {fmt_dur(t):>7}  {name}"
                        for name, phase, frac, t in sorted(busy, key=lambda b: -b[3])]
                status_view.show([head] + rows)
    except KeyboardInterrupt:
        stopped = True
    finally:
        status_view.clear()
        if stopped or broken is not None:
            for proc in list(getattr(ex, "_processes", {}).values()):
                proc.terminate()
        ex.shutdown(wait=not (stopped or broken is not None), cancel_futures=True)
        mgr.shutdown()
    el = time.perf_counter() - t0
    if broken is not None:
        print(f"Stopped after {fmt_dur(el)}: the worker processes ended ({broken}). {n_done} file"
              f"{'s' * (n_done != 1)} checked this run {'is' if n_done == 1 else 'are'} saved; the rest aren't marked "
              f"done - run the same command again to carry on (with fewer --workers if memory ran out).")
    elif stopped:
        print(f"Stopped after {fmt_dur(el)}: {n_done} file{'s' * (n_done != 1)} checked this run {'is' if n_done == 1 else 'are'} saved - run the same command "
              f"again to carry on where it stopped.")
    else:
        print(f"Done: {n_done} file{'s' * (n_done != 1)} in {fmt_dur(el)}.")


def _ignore_sigint():
    """(Ctrl+C is for the main process: it stops the run; the workers and the manager are ended by it.)"""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)


class Status:
    """
    The run's live progress: on a terminal, a block under the finished files' lines, redrawn in place
    every second; written to a file or a pipe, the block every `every` seconds instead.
    """

    def __init__(self, every=60.0):
        self.tty = sys.stdout.isatty() and not os.environ.get("TERM") == "dumb"
        self.every = every
        self.drawn = 0                                # lines of the block on the screen
        self.last = 0.0
        if self.tty and os.name == "nt":
            os.system("")                             # (lets Windows' console take the cursor moves)

    def _width(self):
        return max(40, shutil.get_terminal_size((120, 24)).columns - 1)

    def clear(self):
        if self.tty and self.drawn:
            sys.stdout.write(f"\x1b[{self.drawn}F\x1b[J")
            sys.stdout.flush()
        self.drawn = 0

    def log(self, text):
        """A finished file's lines, above the block."""
        self.clear()
        print(text, flush=True)
        self.last = 0.0 if self.tty else self.last

    def show(self, lines):
        if self.tty:
            w = self._width()
            self.clear()
            out = [ln if len(ln) <= w else ln[:w - 3] + "..." for ln in lines]
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
            self.drawn = len(out)
        elif time.perf_counter() - self.last >= self.every:
            self.last = time.perf_counter()
            print("-- " + time.strftime("%H:%M:%S") + " " + lines[0], flush=True)
            for ln in lines[1:]:
                print(ln, flush=True)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #
def report(args, meta):
    results = list(load_results(os.path.join(args.out, "results.jsonl")).values())
    for r in results:
        r.setdefault("key", file_key(r["file"], args.path))
        r.setdefault("counts", {})
        r.setdefault("incidents", [])

    def original(r):
        return meta.get(r["key"], {}).get("original", "")

    ok = [r for r in results if not r.get("error")]
    bad = [r for r in results if r.get("error")]
    totals = {c: sum(r["counts"].get(c, 0) for r in ok) for c in CATS}
    files_with = {c: sum(1 for r in ok if r["counts"].get(c)) for c in CATS}
    clean = sum(1 for r in ok if not any(r["counts"].get(c) for c in SEEN))
    notes = sum(r.get("notes", 0) for r in ok)
    lines = ["# Hand animation check", "",
             f"{len(results)} files checked ({len(bad)} failed, {clean} with nothing to see), "
             f"{notes:,} notes played. "
             f"Pianist: {getattr(args, 'pianist_name', None) or 'the default pianist'}; "
             f"{args.keys} keys.", "",
             "How far off is a share of the key's own width, measured from its edges (the same at any window "
             "size): \"off by 40%\" is a fingertip centre 40% of its key's width beside it.", "",
             "What you'd see:", "",
             "- *drawn off* - the fingertip is drawn off its key; *hand shifted* - out of the finger's reach, "
             "the whole hand was moved across to put it on (a jolt). Each sorted by why:",
             "  - **solver** - the keys were reachable, in time, and the hand solver still missed: a bug to fix.",
             "  - **speed** - the hand is on its way, held back by the pianist's top speed (where the keys want "
             "it and where it's drawn differ by more than half a white key).",
             "  - **anatomy** - the keys held at that moment are too far apart for this hand from any position "
             "(genuinely incompatible with the pianist's anatomy).",
             "- **fingering** - notes the planner couldn't give a finger: not played at all.", "",
             "What you wouldn't (counted, listed with --show-corrected / --show-timing):", "",
             f"- **corrected** - the solver aimed a fingertip off its key by more than "
             f"{100 * _anim_check().AIM_KEY:.0f}% of the key's width and it was pulled on before drawing: the drawn "
             "tip is on its key (the finger may look a little forced). A sign of where the solver is loose.",
             "- **timing** - notes struck later than in the file (by the top speed, or a rolled chord).", "",
             "Each incident reads: time span in the file (m:ss.sss), hand, category, what happened, "
             "then the notes involved, each with the time it's struck.", "",
             "## Totals", "", "| category | incidents | files |", "|---|---:|---:|"]
    lines += [f"| {c} | {totals[c]:,} | {files_with[c]:,} |" for c in CATS]
    worst = sorted(ok, key=lambda r: (-r["counts"].get("solver", 0), -r["counts"].get("speed", 0),
                                      -r["counts"].get("anatomy", 0), -r["counts"].get("corrected", 0),
                                      label(r["key"], meta)))
    lines += ["", "## Files with the most solver incidents (seen)", "",
              "| # | piece | " + " | ".join(CATS) + " | file |",
              "|---:|---|" + "---:|" * len(CATS) + "---|"]
    for k, r in enumerate([r for r in worst if sum(r["counts"].get(c, 0) for c in SEEN)][:args.top], 1):
        lines.append(f"| {k} | {label(r['key'], meta)} | " + " | ".join(str(r["counts"].get(c, 0)) for c in CATS)
                     + f" | `{r['key']}` |")
    if bad:
        lines += ["", "## Failed to check", ""]
        lines += [f"- {label(r['key'], meta)} - `{r['key']}`: {r['error']}" for r in bad]
    hidden = [x for x, shown in (("corrected", args.show_corrected), ("late strikes", args.show_timing)) if not shown]
    lines += ["", "## Every file", "",
              "Worst first; the file to open is under each piece's name. Times are in the MIDI file (m:ss.sss)." +
              (f" Counted, not listed: {' and '.join(hidden)}." if hidden else "")]

    def listed(i):
        if i["cat"] == "timing":
            return args.show_timing
        return i.get("visible", True) or args.show_corrected

    with open(os.path.join(args.out, "incidents.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["piece", "year", "file", "original", "hand", "category", "seen", "what", "start", "end",
                    "start_s", "end_s", "off_pct_of_key", "problems", "notes", "fingers"])
        for r in worst:
            m = meta.get(r["key"]) or dict(zip(("piece", "year"), piece_of(r["key"])))
            for i in r["incidents"]:
                if not listed(i):
                    continue                          # (the report's filters: --show-corrected, --show-timing)
                seen = "yes" if i.get("visible", True) and i["cat"] != "timing" else "no"
                w.writerow([m["piece"], m["year"], r["key"], original(r), i["hand"], i["cat"], seen, i["kind"],
                            fmt_t(i["t0"]), fmt_t(i["t1"]), f"{i['t0']:.3f}", f"{i['t1']:.3f}",
                            f"{100 * i.get('off', 0.0):.0f}", i["count"],
                            " ".join(f"{a}@{b:.3f}" for a, b in i["notes"][:20]),
                            "".join(str(f) for f in i.get("fingers", []))])
            incs = [i for i in r["incidents"] if listed(i)]
            if not incs and not any(r["counts"].get(c) for c in CATS):
                continue
            c = r["counts"]
            orig = f" (MAESTRO: `{original(r)}`)" if original(r) else ""
            lines += ["", f"### {label(r['key'], meta)}", "",
                      f"`{r['key']}`{orig}  ",
                      f"{fmt_dur(r.get('duration', 0))}, {r.get('notes', 0):,} notes - " +
                      ", ".join(f"{c.get(x, 0)} {x}" for x in CATS) +
                      (" - **stopped early (time limit)**" if r.get("timed_out") else "")]
            if incs:
                lines += ["", "```"] + [i["line"] for i in incs] + ["```"]
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Report: {os.path.join(args.out, 'report.md')} (and incidents.csv)")
    print("  ".join(f"{c}: {totals[c]:,} in {files_with[c]} files" for c in CATS))


def _anim_check():
    import anim_check
    return anim_check


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("path", help="folder of MIDI files (searched recursively), e.g. maestro-v3.0.0 - or one file")
    ap.add_argument("--map", help="the rename map, a .csv with columns original,renamed (default: rename_map.csv "
                                   "in the folder or the one above it)")
    ap.add_argument("--out", default="maestro_check", help="output folder (default ./maestro_check)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help="files checked at once (default: one per CPU, less one)")
    ap.add_argument("--pianist", help="a pianist's name (one of the app's, e.g. Ninthie) or the path to its .json "
                                       "(default: the default pianist)")
    ap.add_argument("--size", default="1920x1080", help="window size the keyboard is laid out for (default 1920x1080; offsets are in key widths, so the size matters little)")
    ap.add_argument("--keys", default="realistic", choices=("realistic", "equal"), help="keyboard type")
    ap.add_argument("--match", help="only files whose name contains this (e.g. Chopin, Ballade, 2018)")
    ap.add_argument("--limit", type=int, help="only the first N files")
    ap.add_argument("--timeout", type=float, default=900, help="seconds per file for the checking (0: none)")
    ap.add_argument("--top", type=int, default=50, help="files in the report's worst-first table")
    ap.add_argument("--show-corrected", action="store_true",
                    help="list the corrected aims in the report (tips aimed off their keys, pulled on before drawing)")
    ap.add_argument("--show-timing", action="store_true", help="list late strikes in the report, not just count them")
    ap.add_argument("--progress-every", type=float, default=60,
                    help="with the output going to a file, the progress every this many seconds (default 60; "
                         "on a terminal it's live)")
    ap.add_argument("--report-only", action="store_true", help="just rebuild the report from results.jsonl")
    args = ap.parse_args()
    w, h = (int(x) for x in args.size.lower().split("x"))
    args.size = (w, h)
    args.path = os.path.abspath(args.path)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # (pieces' names on any console)
        except (AttributeError, ValueError):
            pass
    args.map = find_rename_map(args.path, args.map)
    args.pianist, args.pianist_name = resolve_pianist(args.pianist)
    meta, args.map_info = dataset_meta(args.path, args.map)
    if not args.report_only:
        run(args, meta)
    report(args, meta)


if __name__ == "__main__":
    main()
