"""Fingering profiles: stored by name, and what each choice does to the planned fingering."""
import os
import time

import pygame
import pytest

from miditool import fingerjob
from miditool import profiles as P
from miditool_support import app  # noqa: F401

BLACK = {1, 3, 6, 8, 10}


@pytest.fixture
def store():
    return P.ProfileStore()


def rows_of(chords, dt=0.3, hand="R"):
    """Rows for fingerjob: each chord (tuple of pitches) struck dt after the last."""
    rows, nid = [], 0
    for k, ch in enumerate(chords):
        for p in ch:
            nid += 1
            rows.append((nid, p, k * dt, k * dt + dt * 0.9, 80, 1, hand, None, False))
    return rows


TRACKS = {0: ("Tempo", 0, 0), 1: ("Piano", 0, 100)}


def planner(**settings):
    """The active pianist (the default one here) with a profile of `settings` on top (None: none)."""
    import pianist
    return {"pianist": P.pianist_data(pianist.active()), "settings": dict(P.DEFAULTS, **settings) if settings else None}


def plan(rows, **settings):
    got = fingerjob.suggest(rows, TRACKS, planner(**settings))
    return [got[r[0]][1] for r in rows]


def tops(rows, fingers, n=2):
    return [f for (r, f) in zip(rows, fingers)][n - 1::n]


def test_the_store(store, tmp_path):
    assert [p.name for p in store.profiles] == [P.BUILTIN_NAME] and store.active.builtin
    a = store.create("Big hands", {"octaves": "4all"})
    assert store.active is a and a.get("octaves") == "4all"
    rev = store.rev
    store.set_value(a, "thirds", "simple")
    assert store.rev > rev
    store.rename(a, "Large hands")
    again = P.ProfileStore()                                     # (read back from disk)
    b = again.active
    assert (b.name, b.get("octaves"), b.get("thirds")) == ("Large hands", "4all", "simple")
    assert again.delete(b) and again.active.builtin
    assert not os.listdir(P.data_dir())
    assert not again.delete(again.active)                        # (Default stays)


def test_settings_are_checked(store):
    p = store.create("Odd", {"octaves": "7", "weak_bias": 9.0, "nonsense": 1})
    assert p.get("octaves") == "4black" and p.get("weak_bias") == 1.0 and "nonsense" not in p.settings


def test_default_is_the_active_pianist_exactly():
    import pianist
    rows = rows_of([(60, 72), (61, 73), (63, 75), (66, 78), (68, 80), (70, 82)])
    assert plan(rows) == [fingerjob.suggest(rows, TRACKS)[r[0]][1] for r in rows]
    assert plan(rows) == plan(rows, octaves="4black")                # (every default spelled out: the same)
    # another active pianist: Default is theirs, a profile goes on top of theirs
    p = pianist.Pianist("Big")
    p.behavior["octaves"] = "5"
    pianist.set_active(pianist.save(p))
    black = rows_of([(61, 73), (63, 75), (66, 78), (68, 80), (70, 82), (73, 85)])
    assert tops(black, plan(black)) == [5] * 6
    store = P.ProfileStore()
    assert store.profiles[0].get("octaves") == "5"
    assert tops(black, plan(black, octaves="4black")) == [4] * 6


def test_octaves_with_4_or_5():
    black = rows_of([(61, 73), (63, 75), (66, 78), (68, 80), (70, 82), (73, 85)])
    assert tops(black, plan(black)) == [4] * 6                       # Hanon: 4 on the black keys
    assert tops(black, plan(black, octaves="5")) == [5] * 6
    white = rows_of([(60, 72), (62, 74), (64, 76), (65, 77), (67, 79), (69, 81)])
    assert tops(white, plan(white)) == [5] * 6
    assert tops(white, plan(white, octaves="4all")) == [4] * 6       # large hands: 4 on white keys too


def test_chromatic_scale_styles():
    run = rows_of([(p,) for p in range(60, 73)], dt=0.12)
    f13 = plan(run)
    f12 = plan(run, chromatic="12")
    # "1313123": 3 on every black key; "1212123": 2 on D#, G#, A# (3 still after the 2 on C and F)
    black13 = {f for r, f in zip(run, f13) if r[1] % 12 in BLACK}
    black12 = {f for r, f in zip(run, f12) if r[1] % 12 in (3, 8, 10)}
    assert black13 == {3} and black12 == {2}


def test_thirds_legato_or_simple():
    up = [(60, 64), (62, 65), (64, 67), (65, 69), (67, 71), (69, 72), (71, 74), (72, 76)]
    rows = rows_of(up, dt=0.25)
    simple = plan(rows, thirds="simple")
    pairs = list(zip(simple[0::2], simple[1::2]))
    assert pairs[:3] == [(1, 3), (2, 4), (3, 5)]
    assert simple != plan(rows)                                      # (the legato fingering crosses instead)


def test_firmness_off_leaves_it_to_the_planner():
    black = rows_of([(61, 73), (63, 75), (66, 78), (68, 80), (70, 82), (73, 85)])
    w = P.figure_weights(P.full_settings({"octaves_firm": "off", "scales": "strict"}))
    assert w["fig:W_OCT"] == 0.0 and w["fig:W_SCALE"] == w["fig:W_SCALE_LONG"] / 2 > 0
    assert len(plan(black, octaves_firm="off")) == 12                # (still fingered, by the cost model alone)


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #

def _click(app, pos):
    pygame.mouse.set_pos(pos)
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1))


def _chip(win, sid, value):
    row = next(r for r in win.rows if r["kind"] == "setting" and r["s"]["id"] == sid)
    win.scroll = win.scroll_t = max(0, row["y"] - 20)
    rr = win._row_rect(row)
    cr = next(c for v, _, _, c in row["chips"] if v == value)
    from miditool.theme import px
    return cr.move(rr.x, rr.y + px(6)).center


def test_the_window(app):
    from miditool.document import Note
    ed = app.editor
    doc = ed.doc
    doc.begin("x")
    for k, (lo, hi) in enumerate([(61, 73), (63, 75), (66, 78), (68, 80), (70, 82), (73, 85)]):
        for p in (lo, hi):
            doc.put_note(Note(doc.new_id(), 1, 0, p, 240 * k, 240 * k + 200, 80, hand="R"))
    doc.commit()
    btn = next(b for b in app.buttons if b and b.action == "fingering")
    _click(app, btn.rect.center)
    win = app.window
    assert win is not None and app.profiles.active.builtin
    app.render()
    _click(app, _chip(win, "octaves", "5"))                          # Default can't change: a new profile
    act = app.profiles.active
    assert not act.builtin and act.get("octaves") == "5"
    assert os.path.exists(os.path.join(P.data_dir(), act.id + ".json"))
    # a slider
    row = next(r for r in win.rows if r["kind"] == "setting" and r["s"]["id"] == "weak_bias")
    win.scroll = win.scroll_t = max(0, row["y"] - 20)
    track = win._slider_track(row, win._row_rect(row))
    pygame.mouse.set_pos(track.midright)
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=track.midright, button=1))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=track.midright, button=1))
    assert act.get("weak_bias") == pytest.approx(1.0, abs=0.02)
    # rename it
    rename = next(b for b in win.list_buttons if b.action == "rename")
    _click(app, rename.rect.center)
    app.handle_event(pygame.event.Event(pygame.TEXTINPUT, text="Octaves on 5"))
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r"))
    assert act.name == "Octaves on 5"
    app.render()
    _click(app, win.done.rect.center)                                # closing shows the fingering
    assert app.window is None and ed.show_fingering
    t0 = time.time()
    while not ed.suggestions_ready() and time.time() - t0 < 60:
        for kind, token, result, error in app.jobs.poll():
            app.on_job(kind, token, result, error)
        ed.update(0.05)
        time.sleep(0.02)
    tops_ = [ed.sugg[n.id][1] for n in sorted(doc.notes.values(), key=lambda n: (n.start, n.pitch))][1::2]
    assert tops_ == [5] * 6
    # back to Default: the suggestions follow
    _click(app, btn.rect.center)
    win = app.window
    _click(app, win._list_rows()[0][1].center)
    assert app.profiles.active.builtin
    _click(app, win.done.rect.center)


def test_suggestions_come_from_worker_processes(screen, monkeypatch):
    """The real thing (the tests otherwise run jobs inline): the planner in spawned processes, the hands built."""
    import main
    from conftest import midi_path
    from miditool import jobs
    monkeypatch.setattr(jobs, "INLINE", False)
    host = main.App(screen, sound=False)
    try:
        host.edit(midi_path("demo_song.mid"))
        mode = host.mode
        mode.settle(timeout=120)
        ed = mode.editor
        assert ed.suggestions_ready() and len(ed.sugg) == len(ed.doc.notes)
        assert mode.panel.anims
        assert host.editor_jobs._pool is not None and getattr(host.editor_jobs._pool, "_processes", None)
    finally:
        host.close()
    assert host.editor_jobs._pool is None                     # (the workers stopped with the app)
