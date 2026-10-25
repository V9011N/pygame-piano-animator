"""
jobs.py - Long work (fingering plans, impose analysis) in worker processes.

Python threads share one interpreter lock, so a planner running in a thread
would cost the window its 120 fps. These jobs run in their own processes
instead ("spawn": the same on Windows and elsewhere). One job per kind runs at
a time; asking again while it runs just marks it wanted again, and the newest
inputs go in when it finishes - a stale result is dropped by its token.
"""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor

WORKERS = 2
INLINE = False              # run each job here and now instead (the tests: no processes to start)


def _warm():
    from . import fingerjob, impose  # noqa: F401  (imports done before the first real job)
    return True


class Jobs:
    def __init__(self):
        self._pool = None
        self._running = {}          # kind -> (future, token)
        self._again = {}            # kind -> (fn, make_args, token): asked for while one ran

    def _ensure(self):
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=WORKERS, mp_context=multiprocessing.get_context("spawn"))
            for _ in range(WORKERS):
                self._pool.submit(_warm)
        return self._pool

    def warm_up(self):
        if INLINE:
            return
        try:
            self._ensure()
        except Exception as exc:                     # (no processes: jobs then fail and say so)
            print(f"Worker processes unavailable ({exc})")

    def busy(self, kind):
        return kind in self._running

    def request(self, kind, fn, make_args, token=None):
        """Run fn(*make_args()) for `kind`; if one is running, again with fresh arguments once it's done."""
        if kind in self._running:
            self._again[kind] = (fn, make_args, token)
            return
        self._start(kind, fn, make_args, token)

    def _start(self, kind, fn, make_args, token):
        args = make_args()
        from concurrent.futures import Future
        if INLINE:
            fut = Future()
            try:
                fut.set_result(fn(*args))
            except Exception as exc:
                fut.set_exception(exc)
            finally:
                from . import profiles
                profiles.restore_host()         # (the planner set up for the active pianist again)
            self._running[kind] = (fut, token)
            return
        try:
            fut = self._ensure().submit(fn, *args)
        except Exception as exc:
            fut = Future()
            fut.set_exception(exc)
        self._running[kind] = (fut, token)

    def cancel(self, kind):
        """Forget a kind's job (it may still finish in its process; the result is dropped)."""
        self._running.pop(kind, None)
        self._again.pop(kind, None)

    def poll(self):
        """[(kind, token, result, error)] of the jobs finished since the last poll."""
        out = []
        for kind, (fut, token) in list(self._running.items()):
            if not fut.done():
                continue
            del self._running[kind]
            try:
                out.append((kind, token, fut.result(), None))
            except Exception as exc:
                out.append((kind, token, None, exc))
            again = self._again.pop(kind, None)
            if again is not None:
                self._start(kind, *again)
        return out

    def shutdown(self):
        """Stop the workers now (a long fingering plan would otherwise hold up closing the window)."""
        if self._pool is not None:
            procs = list(getattr(self._pool, "_processes", {}).values())
            self._pool.shutdown(wait=False, cancel_futures=True)
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            self._pool = None
