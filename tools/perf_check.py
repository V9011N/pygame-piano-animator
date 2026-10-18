"""
perf_check.py - Play every MIDI file in a folder (the MAESTRO dataset) through
the player, frame by frame, and find where it hitches.

Each file is loaded and its hands planned as the app does (timed: the load),
then played start to end through the player itself (main.Visualizer,
offscreen) at --fps: every frame's update() timed with the hands' motion
(their poses), or with --draw the whole render() (about 30 times the work); the
rest of each frame's time goes to the hands' work ahead (Visualizer.idle), as in
the app, and what that runs over its budget counts to the frame. A frame that
takes much longer than the frame before it is a hitch; hitches close together
are one stall, reported with its time in the song and what it was spent on -
the functions below each timed by their own time in that frame (what they
called that's also timed counted apart). Then --seeks jumps to random places
(as dragging the progress bar does) and times the first frame after each.

    python tools/perf_check.py PATH/TO/MAESTRO --pianist Ninthie
    python tools/perf_check.py PATH --match Chopin --limit 10 --workers 6
    python tools/perf_check.py PATH --draw --limit 30   # whole frames, drawing too (slow: a sample)
    python tools/perf_check.py PATH --report-only

PATH, --map, --pianist, --match, --limit, --size and --keys are as for
tools/maestro_check.py (renamed files and a rename map work the same).
Times depend on the machine: compare runs made on the same one, and read the
spikes against each file's own typical frame (the median), which is what the
hitch threshold does (--hitch-x, at least --hitch-ms).

Timing wants each worker a core of its own (--workers defaults to half the
logical CPUs, less one): with more, the processes take turns and a frame can
wait a whole time slice. Each stall's CPU time is measured too, and one whose
thread got less than half the time that passed is counted as waiting (the
machine busy), apart from the rest. The first frame of each file (the player
opening, in the lead-in) is counted apart as well.

Output (--out, default ./perf_check): results.jsonl (one line a file, appended -
run again to carry on), report.md (the culprits over all stalls, the worst
files, then each file's stalls with their song times) and stalls.csv.
"""
import argparse
import collections
import concurrent.futures as cf
import contextlib
import csv
import io
import json
import math
import multiprocessing
import os
import random
import sys
import tempfile
import threading
import time
import zlib
from multiprocessing.managers import SyncManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)
import maestro_check as mc                           # noqa: E402  (files, names, pianists, the progress block)

RESULTS_FORMAT = 5
STALL_GAP_T = 0.1           # s of song, hitches this close are one stall
WAITED_CPU = 0.5            # a stall whose thread got less CPU time than this share of the time passed was waiting
SEEK_SETTLE_MAX = 120       # frames played on after a seek, at most, waiting for the exact pose
LIST_STALLS = 30            # the report lists each file's this many worst stalls (stalls.csv has them all)
CULPRITS = [                # (module, class or None, function): timed in every frame, by their own time
    ("hands", "HandAnimator", "pose"),
    ("hands", "HandAnimator", "_finger_pose"),
    ("hands", "HandAnimator", "_gliss_pose"),
    ("hands", "HandAnimator", "_settle_on_keys"),
    ("hands", "HandAnimator", "_limited_grid"),
    ("hands", "HandAnimator", "_smooth_hand_grid"),
    ("hands", "HandAnimator", "_limited_tip_grid"),
    ("hands", "HandAnimator", "_smooth_tip_grid"),
    ("hands", "HandAnimator", "_tips_at"),
    ("hands", "HandAnimator", "_key_fix"),
    ("hands", "HandAnimator", "_placed_at"),
    ("hands", "HandAnimator", "_trem_keys"),
    ("hands", "HandAnimator", "_layer_top"),
    ("hands", "HandAnimator", "footprint"),
    ("hands", "HandAnimator", "_chord_fits"),
    ("hands", None, "draw_hands"),
    ("hands", None, "draw_skeletons"),
    ("skins", None, "draw_poses"),
    ("common", "Keyboard", "draw"),
    ("common", "Performance", None),          # (all of its methods: what sounds and the keys down)
    ("main", "Visualizer", "_draw_scene"),
    ("main", "Visualizer", "_draw_controls"),
    ("main", "Visualizer", "update"),
    ("main", "Visualizer", "render"),
]


# --------------------------------------------------------------------------- #
# Timing (in a worker)
# --------------------------------------------------------------------------- #
class Timer:
    """Own time per timed function within the current frame (nested timed calls counted apart)."""

    def __init__(self):
        self.frame = collections.Counter()
        self.stack = []

    def wrap(self, name, fn):
        timer = self

        def timed(*a, **kw):
            t0 = time.perf_counter()
            timer.stack.append(0.0)
            try:
                return fn(*a, **kw)
            finally:
                child = timer.stack.pop()
                el = time.perf_counter() - t0
                timer.frame[name] += el - child
                if timer.stack:
                    timer.stack[-1] += el
        timed.__wrapped__ = fn
        return timed

    def install(self):
        import importlib
        mods = [importlib.import_module(m) for m in ("main", "hands", "skins", "common")]
        for mod, cls, fn in CULPRITS:
            owner = importlib.import_module(mod)
            if cls:
                owner = getattr(owner, cls)
            names = [fn] if fn else [n for n, v in vars(owner).items() if callable(v) and not n.startswith("__")]
            for n in names:
                f = vars(owner).get(n) if cls else getattr(owner, n, None)
                if f is None or getattr(f, "__wrapped__", None) or isinstance(f, (staticmethod, classmethod, type)):
                    continue
                timed = self.wrap(f"{cls + '.' if cls else ''}{n}", f)
                setattr(owner, n, timed)
                if not cls:                           # (imported elsewhere by name)
                    for m in mods:
                        if getattr(m, n, None) is f:
                            setattr(m, n, timed)

    def take(self):
        out, self.frame = self.frame, collections.Counter()
        return out


_OPTS, _NOW, _TIMER = {}, {"key": None, "phase": "", "frac": 0.0, "start": 0.0}, None


def _heartbeat(status):
    import progress
    pid = os.getpid()
    parent = multiprocessing.parent_process()
    while True:
        if parent is not None and not parent.is_alive():
            os._exit(1)                               # (the run was stopped: don't carry on alone)
        key = _NOW["key"]
        try:
            if key is None:
                status.pop(pid, None)
            else:
                frac = progress.value() if _NOW["phase"] == "loading" else _NOW["frac"]
                status[pid] = (key, _NOW["phase"], frac, _NOW["start"])
        except Exception:
            return
        time.sleep(1.0)


def _init_worker(opts, status=None):
    global _TIMER
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    import pianist
    pianist.FOLDER = tempfile.mkdtemp(prefix="perf_check_")
    pianist.SETTINGS = os.path.join(pianist.FOLDER, "settings.json")
    if opts.get("pianist"):
        with open(opts["pianist"], encoding="utf-8") as fh:
            d = json.load(fh)
        pianist._active = pianist.Pianist.from_json(d.get("id", "checked"), d)
    else:
        pianist._active = pianist.default_pianist()
    import pygame
    pygame.init()
    pygame.display.set_mode(tuple(opts["size"]))
    _TIMER = Timer()
    _TIMER.install()
    _OPTS.update(opts)
    if status is not None:
        threading.Thread(target=_heartbeat, args=(status,), daemon=True).start()


def check_file(path, key=None):
    """{file, load_s, frames, median_ms, p99_ms, max_ms, hitches, stalls, seeks, ...} for one file."""
    import pygame
    import common
    import hands
    import main
    import progress
    out = {"file": path, "key": key, "format": RESULTS_FORMAT, "error": None, "stalls": [], "seeks": [], "log": []}
    t_start = time.perf_counter()
    _NOW.update(key=key or path, phase="loading", frac=0.0, start=time.time())
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            progress.begin()
            t0 = time.perf_counter()
            song, an = hands.load_with_hands(path)
            out["load_s"] = round(time.perf_counter() - t0, 2)
            progress.end()
            screen = pygame.display.get_surface()
            common.set_key_style(_OPTS["keys"])
            v = main.Visualizer(screen, song, midi=common.MidiOut(False), hands=an)
            hands.settle_heap()                       # (as App._switch does)
            v.paused = False
            dt = 1.0 / _OPTS["fps"]
            out.update(workers=_OPTS.get("workers"), cpus=os.cpu_count())
            # seeks (dragging the progress bar): the first frame after each - before playing, so
            # into parts not worked out yet, as in the app
            _NOW["phase"] = "seeking"
            rng = random.Random(zlib.crc32((key or path).encode("utf-8")))     # (the same places every run)
            _TIMER.take()
            state = {}

            def frame():
                v.update(dt)
                if _OPTS["draw"]:
                    v.render()
                else:                                 # (as the player draws them: hands.live_poses)
                    hands.live_poses(an.values(), v.t, v.keyboard, False, state)
            for _ in range(_OPTS["seeks"]):
                ts = rng.uniform(0.0, max(0.1, song.duration - 1.0))
                v.seek(ts)
                v.paused = False
                c0, f0 = time.thread_time(), time.perf_counter()
                frame()
                ms = 1000.0 * (time.perf_counter() - f0)
                cpu = 1000.0 * (time.thread_time() - c0)
                spent = _TIMER.take()
                # then played on (the work ahead in each frame's spare time) until the exact pose is worked out:
                # how long that took, and the worst frame meanwhile
                v.idle(dt - ms / 1000.0 - main.IDLE_MARGIN_T)
                n_settle, worst = 0, ms
                while n_settle < SEEK_SETTLE_MAX and not all(a.exact_ready(v.t, v.keyboard) for a in an.values()):
                    f1 = time.perf_counter()
                    frame()
                    w = time.perf_counter() - f1
                    i1 = time.perf_counter()
                    v.idle(dt - w - main.IDLE_MARGIN_T)
                    worst = max(worst, 1000.0 * (w + max(0.0, time.perf_counter() - i1 - max(0.0, dt - w - main.IDLE_MARGIN_T))))
                    n_settle += 1
                _TIMER.take()
                out["seeks"].append((round(ts, 2), round(ms, 1), [(n, round(1000 * v_, 1)) for n, v_ in spent.most_common(3)],
                                     round(cpu, 1), n_settle, round(worst, 1)))
            t_end = song.duration                     # (the player pauses at the end)
            v.seek(v.start_time())
            v.paused = False
            _NOW["phase"] = "playing"
            frames, times = [], []
            _TIMER.take()
            n_total = max(1, int((t_end - v.t) / dt))
            first = True
            while v.t < t_end and not v.paused:
                t = v.t
                c0, f0 = time.thread_time(), time.perf_counter()
                v.update(dt)
                if _OPTS["draw"]:
                    v.render()
                else:
                    hands.live_poses(an.values(), v.t, v.keyboard, False, state)
                work = time.perf_counter() - f0
                spent = _TIMER.take()
                # the rest of the frame's time goes to the hands' work ahead, as in App.run:
                # what it runs over its budget delays the next frame
                spare = dt - work - main.IDLE_MARGIN_T
                i0 = time.perf_counter()
                v.idle(spare)                         # (called with none left too, as App.run)
                over = max(0.0, time.perf_counter() - i0 - max(0.0, spare))
                ahead = _TIMER.take()
                if over > 0.0005:                     # (named "ahead: ...": what that work was spent on)
                    spent.update({"ahead: " + n: v_ for n, v_ in ahead.items()})
                ms = 1000.0 * (work + over)
                # the CPU time this thread got over the whole frame (work ahead included) against the
                # time that passed: well under it, the process was waiting (other work on the machine)
                cpu, wall = 1000.0 * (time.thread_time() - c0), 1000.0 * (time.perf_counter() - f0)
                if first:                             # (the player opening: the lead-in, nothing moves yet)
                    out["first_ms"], first = round(ms, 1), False
                    continue
                frames.append(ms)
                times.append((t, ms, spent, cpu, wall))
                if len(frames) % 120 == 0:
                    _NOW["frac"] = len(frames) / n_total
            # hitches: frames well over the file's own typical frame
            srt = sorted(frames)
            med = srt[len(srt) // 2] if srt else 0.0
            thr = max(_OPTS["hitch_ms"], _OPTS["hitch_x"] * med)
            out.update(frames=len(frames), median_ms=round(med, 2),
                       p99_ms=round(srt[min(len(srt) - 1, int(0.99 * len(srt)))], 2) if srt else 0.0,
                       max_ms=round(srt[-1], 2) if srt else 0.0, threshold_ms=round(thr, 2),
                       duration=round(song.duration, 1), notes=len(song.notes))
            hitches = [h for h in times if h[1] > thr]
            out["hitches"] = len(hitches)
            stall = None
            for t, ms, spent, cpu, wall in hitches:
                if stall and t - stall["t1"] <= STALL_GAP_T:
                    stall["t1"] = t
                    stall["ms"] += ms
                    stall["worst_ms"] = max(stall["worst_ms"], ms)
                    stall["frames"] += 1
                    stall["spent"].update(spent)
                    stall["cpu_ms"] += cpu
                    stall["wall_ms"] += wall
                else:
                    stall = {"t0": t, "t1": t, "ms": ms, "worst_ms": ms, "frames": 1,
                             "spent": collections.Counter(spent), "cpu_ms": cpu, "wall_ms": wall}
                    out["stalls"].append(stall)
            for s_ in out["stalls"]:
                tot = sum(s_["spent"].values()) or 1.0
                s_["culprits"] = [(n, round(1000 * v_, 1), round(100 * v_ / tot)) for n, v_ in s_["spent"].most_common(4)]
                del s_["spent"]
                s_["waited"] = s_["cpu_ms"] < WAITED_CPU * s_["wall_ms"]
                s_["ms"], s_["worst_ms"] = round(s_["ms"], 1), round(s_["worst_ms"], 1)
                s_["cpu_ms"], s_["wall_ms"] = round(s_["cpu_ms"], 1), round(s_["wall_ms"], 1)
            # all frames' time by function, for the run's overall picture
            whole = collections.Counter()
            for _, _, spent, _, _ in times:
                whole.update(spent)
            out["spent_s"] = {n: round(v_, 2) for n, v_ in whole.most_common(12)}
    except Exception as exc:
        import traceback
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["log"] = [ln for ln in buf.getvalue().splitlines() if ln.strip()][:50]
    out["seconds"] = round(time.perf_counter() - t_start, 1)
    _NOW["key"] = None
    progress.end()
    return out


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def load_results(path):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if str(r.get("error") or "").startswith("worker failed") or r.get("format") != RESULTS_FORMAT:
                    continue
                done[r.get("key") or r["file"]] = r
    return done


def run(args, meta):
    files = mc.find_midis(args.path)
    keys = {f: mc.file_key(f, args.path) for f in files}
    if args.match:
        files = [f for f in files if args.match.lower() in keys[f].lower()
                 or args.match.lower() in mc.label(keys[f], meta).lower()]
    if args.limit:
        files = files[:args.limit]
    os.makedirs(args.out, exist_ok=True)
    res_path = os.path.join(args.out, "results.jsonl")
    done = load_results(res_path)
    todo = [f for f in files if keys[f] not in done]
    print(f"{len(files)} MIDI files, {len(files) - len(todo)} already played, {len(todo)} to go, "
          f"{args.workers} workers -> {args.out}  ({args.fps} fps, {'drawn' if args.draw else 'hands only'})")
    if not todo:
        return
    opts = {"pianist": args.pianist, "size": args.size, "keys": args.keys, "fps": args.fps, "draw": args.draw,
            "seeks": args.seeks, "hitch_ms": args.hitch_ms, "hitch_x": args.hitch_x, "workers": args.workers}
    sizes = {f: max(1, os.path.getsize(f)) for f in todo}
    view = mc.Status(args.progress_every)
    by_key = {keys[f]: f for f in todo}
    n_done, done_work, stalls_so_far = 0, 0, 0
    t0 = time.perf_counter()
    ctx = multiprocessing.get_context("spawn")
    mgr = SyncManager(ctx=ctx)
    mgr.start(mc._ignore_sigint)
    status = mgr.dict()
    ex = cf.ProcessPoolExecutor(args.workers, mp_context=ctx, initializer=_init_worker, initargs=(opts, status))
    stopped, broken = False, None
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
                        broken = exc
                        continue
                    except Exception as exc:
                        r = {"file": f, "error": f"worker failed: {exc}", "stalls": [], "seeks": []}
                    r["key"] = keys[f]
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    n_done += 1
                    done_work += sizes[f]
                    stalls_so_far += sum(1 for s in r.get("stalls", []) if not s.get("waited"))
                    w = len(str(len(todo)))
                    state = (f"FAILED: {r['error']}" if r.get("error") else
                             f"load {r.get('load_s', 0):5.1f} s  frame median {r.get('median_ms', 0):5.1f} ms  "
                             f"max {r.get('max_ms', 0):6.1f}  {sum(1 for s in r['stalls'] if not s.get('waited')):>3} stalls  "
                             f"worst seek {max((s[5] if len(s) > 5 else s[1] for s in r['seeks']), default=0):6.1f} ms")
                    view.log(f"[{n_done:>{w}}/{len(todo)}] {mc.fmt_dur(r.get('seconds', 0)):>7}  "
                             f"{mc.label(keys[f], meta)}\n{'':>{2 * w + 12}}{state}")
                try:
                    now = dict(status)
                except Exception:
                    now = {}
                running = {futs[x] for x in pending}
                busy, part = [], 0.0
                for key, phase, frac, started in now.values():
                    f = by_key.get(key)
                    if f is None or f not in running:
                        continue
                    share = 0.2 * frac if phase == "loading" else 0.2 + 0.8 * frac if phase == "playing" else 1.0
                    part += sizes[f] * share
                    busy.append((mc.label(key, meta), phase, frac, time.time() - started))
                el = time.perf_counter() - t0
                work = (done_work + part) / sum(sizes.values())
                eta = f"about {mc.fmt_dur(el / work * (1 - work))} left" if work > 0.002 and el > 20 else "estimating time left"
                head = (f"{n_done} of {len(todo)} files done, {100 * work:.1f}% of the work - {mc.fmt_dur(el)} so far, "
                        f"{eta} - stalls so far: {stalls_so_far}")
                rows = [f"    {phase:<8} {100 * frac:>3.0f}%  {mc.fmt_dur(t):>7}  {name}"
                        for name, phase, frac, t in sorted(busy, key=lambda b: -b[3])]
                view.show([head] + rows)
    except KeyboardInterrupt:
        stopped = True
    finally:
        view.clear()
        if stopped or broken is not None:
            for proc in list(getattr(ex, "_processes", {}).values()):
                proc.terminate()
        ex.shutdown(wait=not (stopped or broken is not None), cancel_futures=True)
        mgr.shutdown()
    el = time.perf_counter() - t0
    if broken is not None:
        print(f"Stopped after {mc.fmt_dur(el)}: the worker processes ended ({broken}); run again to carry on "
              f"(with fewer --workers if memory ran out).")
    elif stopped:
        print(f"Stopped after {mc.fmt_dur(el)}: run the same command again to carry on.")
    else:
        print(f"Done: {n_done} file{'s' * (n_done != 1)} in {mc.fmt_dur(el)}.")


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #
def report(args, meta):
    results = list(load_results(os.path.join(args.out, "results.jsonl")).values())
    ok = [r for r in results if not r.get("error")]
    bad = [r for r in results if r.get("error")]
    for r in results:
        r.setdefault("key", r["file"])
    ran = lambda r: [s for s in r.get("stalls", []) if not s.get("waited")]
    stalls = [(r, s) for r in ok for s in ran(r)]
    waited = sum(1 for r in ok for s in r.get("stalls", []) if s.get("waited"))
    culprit_ms = collections.Counter()
    culprit_n = collections.Counter()
    for _, s in stalls:
        for n, ms, _ in s["culprits"]:
            culprit_ms[n] += ms
        if s["culprits"]:
            culprit_n[s["culprits"][0][0]] += 1
    whole = collections.Counter()
    for r in ok:
        whole.update(r.get("spent_s", {}))
    seeks = sorted(((s[5] if len(s) > 5 else s[1], r, s) for r in ok for s in r.get("seeks", [])
                    if len(s) < 4 or s[3] >= WAITED_CPU * s[1]), key=lambda x: -x[0])
    firsts = sorted(r["first_ms"] for r in ok if r.get("first_ms") is not None)
    frames = sum(r.get("frames", 0) for r in ok)
    workers = {r.get("workers") for r in ok if r.get("workers")}
    cpus = {r.get("cpus") for r in ok if r.get("cpus")}
    minutes = sum(r.get("duration", 0) for r in ok) / 60 or 1
    lines = ["# Playback performance", "",
             f"{len(results)} files played ({len(bad)} failed), {frames:,} frames at {args.fps} fps "
             f"({'drawn' if args.draw else 'hands only'}). Pianist: "
             f"{getattr(args, 'pianist_name', None) or 'the default pianist'}; window {args.size[0]}x{args.size[1]}; "
             f"{'/'.join(map(str, sorted(workers))) or '?'} workers on {'/'.join(map(str, sorted(cpus))) or '?'} "
             "logical CPUs.", "",
             f"**{len(stalls):,} stalls** ({len(stalls) / minutes:.2f} a minute of music) while the code ran; "
             f"{waited:,} more while the process was waiting (it got less than {WAITED_CPU:.0%} of the CPU time "
             "that passed: the machine busy with other work - fewer --workers - and not counted below).", "",
             "A **hitch** is a frame longer than the file's typical (median) frame times "
             f"{args.hitch_x:g}, and at least {args.hitch_ms:g} ms; hitches within {STALL_GAP_T} s of song are one "
             "**stall**. What a stall was spent on: each timed function's own time in it (what it called that's also "
             "timed counted apart; the rest of a frame falls to the function that called it; \"ahead:\" - the "
             "hands' work ahead in a frame's spare time, past its budget). The first frame of each file (the "
             "player opening, in the lead-in) is counted apart.", ""]
    if firsts:
        lines += [f"First frames: median {firsts[len(firsts) // 2]:.0f} ms, worst {firsts[-1]:.0f} ms.", ""]
    lines += ["## What the stalls were spent on", "",
              "| function | ms in stalls | stalls it led |", "|---|---:|---:|"]
    lines += [f"| {n} | {ms:,.0f} | {culprit_n[n]:,} |" for n, ms in culprit_ms.most_common(15)]
    lines += ["", "## Where the time goes over all frames", "", "| function | seconds |", "|---|---:|"]
    lines += [f"| {n} | {v:,.1f} |" for n, v in whole.most_common(15)]
    worst = sorted(ok, key=lambda r: (-sum(s["ms"] for s in ran(r)), mc.label(r["key"], meta)))
    worst = [r for r in worst if ran(r)]
    lines += ["", "## Files with the most time in stalls", "",
              "| # | piece | stalls | a minute | in stalls (ms) | worst frame (ms) | median frame (ms) | load (s) | file |",
              "|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    for k, r in enumerate(worst[:args.top], 1):
        st = ran(r)
        lines.append(f"| {k} | {mc.label(r['key'], meta)} | {len(st)} | {len(st) / max(1e-9, r.get('duration', 0) / 60):.1f} | "
                     f"{sum(s['ms'] for s in st):,.0f} | {max(s['worst_ms'] for s in st):.1f} | "
                     f"{r.get('median_ms', 0):.1f} | {r.get('load_s', 0):.1f} | `{r['key']}` |")
    slow = sorted(ok, key=lambda r: -r.get("load_s", 0))[:10]
    lines += ["", "## Slowest loads", "", "| piece | load (s) | notes | file |", "|---|---:|---:|---|"]
    lines += [f"| {mc.label(r['key'], meta)} | {r.get('load_s', 0):.1f} | {r.get('notes', 0):,} | `{r['key']}` |"
              for r in slow]
    if seeks:
        ms_all = sorted(x[0] for x in seeks)
        settle = sorted(s[4] for _, _, s in seeks if len(s) > 4)
        lines += ["", "## Slowest seeks (a jump, then played on until the exact pose is worked out)", "",
                  f"{len(ms_all):,} seeks: the worst frame (the first, or one while the exact pose is worked out) "
                  f"median {ms_all[len(ms_all) // 2]:.0f} ms, 90% under {ms_all[int(0.9 * (len(ms_all) - 1))]:.0f} ms"
                  + (f"; the quick pose shown for median {settle[len(settle) // 2]} frames, at most {settle[-1]}"
                     if settle else "") + ".", "",
                  "| piece | to | worst frame (ms) | first frame (ms) | frames to exact | first frame spent on | file |",
                  "|---|---:|---:|---:|---:|---|---|"]
        lines += [f"| {mc.label(r['key'], meta)} | {mc.fmt_t(s[0])} | {ms:.0f} | {s[1]:.0f} | "
                  f"{s[4] if len(s) > 4 else '-'} | {', '.join(f'{n} {v:.0f}' for n, v in s[2])} | `{r['key']}` |"
                  for ms, r, s in seeks[:15]]
    if bad:
        lines += ["", "## Failed", ""] + [f"- `{r['key']}`: {r['error']}" for r in bad]
    lines += ["", f"## The worst files' stalls", "",
              f"The {args.top} files above, worst first: each file's {LIST_STALLS} worst stalls, in song order "
              "(m:ss.sss). Every stall of every file is in stalls.csv."]
    with open(os.path.join(args.out, "stalls.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["piece", "file", "start", "end", "start_s", "end_s", "frames", "ms", "worst_ms", "cpu_ms",
                    "waited", "median_ms", "culprit", "culprit_ms", "culprit_share", "others"])
        for r in sorted(ok, key=lambda r: r["key"]):
            for s in r.get("stalls", []):
                c = s["culprits"]
                w.writerow([mc.label(r["key"], meta), r["key"], mc.fmt_t(s["t0"]), mc.fmt_t(s["t1"]),
                            f"{s['t0']:.3f}", f"{s['t1']:.3f}", s["frames"], s["ms"], s["worst_ms"], s.get("cpu_ms", ""),
                            int(bool(s.get("waited"))), r.get("median_ms", 0), c[0][0] if c else "",
                            c[0][1] if c else "", c[0][2] if c else "", "; ".join(f"{n} {v}" for n, v, _ in c[1:])])
    for r in worst[:args.top]:
        st = ran(r)
        shown = sorted(sorted(st, key=lambda s: -s["ms"])[:LIST_STALLS], key=lambda s: s["t0"])
        lines += ["", f"### {mc.label(r['key'], meta)}", "",
                  f"`{r['key']}` - {len(st)} stalls ({len(shown)} worst here), median frame "
                  f"{r.get('median_ms', 0):.1f} ms, load {r.get('load_s', 0):.1f} s", "", "```"]
        for s in shown:
            c = s["culprits"]
            span = mc.fmt_t(s["t0"]) if s["t1"] - s["t0"] < 1e-3 else f"{mc.fmt_t(s['t0'])} - {mc.fmt_t(s['t1'])}"
            lines.append(f"{span:<21} {s['frames']:>3} frame{'s' if s['frames'] > 1 else ' '}  "
                         f"{s['ms']:7.0f} ms (worst {s['worst_ms']:5.0f})  " +
                         ", ".join(f"{n} {v:.0f} ms ({p}%)" for n, v, p in c[:3]))
        lines.append("```")
    with open(os.path.join(args.out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"Report: {os.path.join(args.out, 'report.md')} (and stalls.csv)")
    print(f"{len(stalls):,} stalls while the code ran, {waited:,} while the process waited")
    if culprit_ms:
        print("Stalls spent most on: " + ", ".join(f"{n} {ms:,.0f} ms" for n, ms in culprit_ms.most_common(5)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("path", help="folder of MIDI files (searched recursively) - or one file")
    ap.add_argument("--map", help="the rename map (as maestro_check.py)")
    ap.add_argument("--out", default="perf_check", help="output folder (default ./perf_check)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2 - 1),
                    help="files played at once (default: half the logical CPUs, less one - timing wants each worker "
                         "a core of its own: with more, stalls are mostly the processes waiting their turn)")
    ap.add_argument("--pianist", help="a pianist's name or .json (default: the default pianist)")
    ap.add_argument("--size", default="1920x1080", help="the window (default 1920x1080)")
    ap.add_argument("--keys", default="realistic", choices=("realistic", "equal"))
    ap.add_argument("--fps", type=float, default=60.0, help="frames a second played (default 60)")
    ap.add_argument("--draw", action="store_true",
                    help="time whole frames, drawing too (about 30 times the work: a drawn frame is ~19 ms against "
                         "~0.6 ms for the hands' motion alone, on a 2026 laptop core)")
    ap.add_argument("--seeks", type=int, default=10, help="random jumps a file, first frame after each timed")
    ap.add_argument("--hitch-x", type=float, default=3.0, help="a hitch: this many times the file's median frame...")
    ap.add_argument("--hitch-ms", type=float, default=25.0, help="...and at least this many ms (default 25)")
    ap.add_argument("--match", help="only files whose name contains this")
    ap.add_argument("--limit", type=int, help="only the first N files")
    ap.add_argument("--top", type=int, default=50, help="files in the report's worst-first table")
    ap.add_argument("--progress-every", type=float, default=60, help="with the output to a file: progress every N s")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args.size = tuple(int(x) for x in args.size.lower().split("x"))
    args.path = os.path.abspath(args.path)
    args.pianist, args.pianist_name = mc.resolve_pianist(args.pianist)
    args.map = mc.find_rename_map(args.path, args.map)
    meta, _ = mc.dataset_meta(args.path, args.map)
    if not args.report_only:
        run(args, meta)
    report(args, meta)


if __name__ == "__main__":
    main()
