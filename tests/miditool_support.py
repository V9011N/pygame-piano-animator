"""
Shared helpers for the fingering editor's tests (the miditool package): the
editor opened through the app (keyboard and hands hidden, so the roll has the
window's height, as the tests were written for), and scripted input.
"""
import time

import pygame
import pytest


@pytest.fixture
def app(screen, monkeypatch):
    """The fingering editor (miditool.app.EditorMode) on an empty document."""
    import main
    import pianist
    # the mouse where the test puts it (SDL's dummy driver ignores set_pos in plain pygame)
    mouse = [(0, 0)]
    monkeypatch.setattr(pygame.mouse, "set_pos", lambda *pos: mouse.__setitem__(0, tuple(pos[0] if len(pos) == 1 else pos)))
    monkeypatch.setattr(pygame.mouse, "get_pos", lambda: mouse[0])
    pianist.set_app_setting("editor_hands", False)
    host = main.App(screen, sound=False)
    host.edit()
    yield host.mode
    host.close()


def ht_read_markers(path):
    """Hand-thesia's own reader of fingering markers ({(tick, pitch): (hand, finger)})."""
    import midi_loader
    return midi_loader.read_markers(path)


def pump(app, frames=1):
    for _ in range(frames):
        for kind, token, result, error in app.jobs.poll():
            app.on_job(kind, token, result, error)
        app.editor.update(1 / 120)
        app.player.update(1 / 120, app.editor.doc)


def settle(app, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.editor._fing_wait = 1.0
        pump(app)
        ed = app.editor
        if (not app.jobs._running and app.busy is None
                and (not ed.show_fingering or ed.suggestions_ready())
                and (ed.impose is None or ed.impose.plan_rev == ed.doc.notes_rev)):
            pump(app, 60)
            return
        time.sleep(0.02)
    raise AssertionError("background jobs didn't finish")


def ev(app, etype, **kw):
    if "pos" in kw:
        pygame.mouse.set_pos(kw["pos"])
    if etype == pygame.KEYDOWN:
        kw.setdefault("mod", 0)
        kw.setdefault("unicode", "")
    return app.handle_event(pygame.event.Event(etype, **kw))


def click(app, pos, button=1):
    ev(app, pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0))
    ev(app, pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    ev(app, pygame.MOUSEBUTTONUP, pos=pos, button=button)


def drag(app, a, b, button=1, steps=10):
    ev(app, pygame.MOUSEMOTION, pos=a, rel=(0, 0), buttons=(0, 0, 0))
    ev(app, pygame.MOUSEBUTTONDOWN, pos=a, button=button)
    for k in range(1, steps + 1):
        p = (a[0] + (b[0] - a[0]) * k // steps, a[1] + (b[1] - a[1]) * k // steps)
        ev(app, pygame.MOUSEMOTION, pos=p, rel=(0, 0), buttons=(1, 0, 0))
    ev(app, pygame.MOUSEBUTTONUP, pos=b, button=button)


def key(app, k, mod=0):
    return ev(app, pygame.KEYDOWN, key=k, mod=mod)


def note_pos(ed, n, dx=4):
    return int(ed.x_of(n.start)) + dx, int(ed.y_of(n.pitch + 0.5))


def cell(ed, tick, pitch):
    return int(ed.x_of(tick)) + 2, int(ed.y_of(pitch + 0.5))
