"""The app driven like a user would: clicks, drags, keys, the wheel - headless (SDL's dummy video driver)."""
import os
import time

import pygame
import pytest

from miditool_support import app, cell, click, drag, ev, key, note_pos, pump, settle  # noqa: F401
from miditool.document import Document, Event, Note
from miditool.smf import META


# --------------------------------------------------------------------------- #
def test_opens_straight_to_an_empty_roll(app):
    pump(app, 5)
    app.render()
    assert app.editor.doc.notes == {}
    labels = [b.label for b in app.buttons if b]
    assert labels[:3] == ["Open MIDI…", "Save Project…", "Export MIDI"]
    assert {"sel_left", "sel_right", "menu"} <= {b.action for b in app.buttons if b}
    shown = [b.rect for b in app.buttons if b] + [app.play_btn.rect, app.volume.rect]
    assert not any(a.colliderect(b) for i, a in enumerate(shown) for b in shown[i + 1:])     # (all fit)


def test_add_notes_by_double_click_and_draw(app):
    ed = app.editor
    pos = cell(ed, ed.ppq * 2, 64)
    click(app, pos)
    click(app, pos)                                    # double-click
    assert len(ed.doc.notes) == 1
    n = next(iter(ed.doc.notes.values()))
    assert n.pitch == 64 and n.start == ed.ppq * 2 and n.length == ed.ppq // 4   # snapped to 1/16
    # draw mode: click and drag to set the length
    key(app, pygame.K_p)
    a = cell(ed, ed.ppq * 4, 60)
    b = (int(ed.x_of(ed.ppq * 6)), a[1])
    drag(app, a, b)
    assert len(ed.doc.notes) == 2
    m = [x for x in ed.doc.notes.values() if x.pitch == 60][0]
    assert m.start == ed.ppq * 4 and m.end == ed.ppq * 6
    ed.doc.undo()
    assert len(ed.doc.notes) == 1


def test_move_resize_delete_undo(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    for k in range(4):
        doc.put_note(Note(doc.new_id(), 1, 0, 60 + k, ed.ppq * (1 + k), ed.ppq * (2 + k), 80))
    doc.commit()
    pump(app, 2)
    n = min(doc.notes.values(), key=lambda n: n.start)
    # move one beat right and two semitones up
    a = note_pos(ed, n, dx=int(ed.x_of(n.end) - ed.x_of(n.start)) // 2)
    b = (a[0] + int(ed.x_of(ed.ppq) - ed.x_of(0)), int(ed.y_of(n.pitch + 2.5)))
    drag(app, a, b)
    m = doc.notes[n.id]
    assert (m.start, m.pitch) == (n.start + ed.ppq, n.pitch + 2)
    # stretch it by dragging its right edge
    r = (int(ed.x_of(m.end)) - 2, int(ed.y_of(m.pitch + 0.5)))
    drag(app, r, (int(ed.x_of(m.end + ed.ppq)), r[1]))
    assert doc.notes[n.id].end == m.end + ed.ppq
    # rubber band over everything, delete, undo
    drag(app, (ed.roll.x + 3, ed.roll.y + 3), (ed.roll.right - 3, ed.roll.bottom - 3))
    assert ed.selection == set(doc.notes)
    key(app, pygame.K_DELETE)
    assert not doc.notes
    key(app, pygame.K_z, pygame.KMOD_CTRL)
    assert len(doc.notes) == 4


def test_select_left_and_right_of_the_cursor(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    for k in range(8):
        doc.put_note(Note(doc.new_id(), 1, 0, 60, ed.ppq * k, ed.ppq * k + 100, 80))
    doc.commit()
    click(app, (int(ed.x_of(ed.ppq * 3)), ed.ruler.centery))       # the cursor, from the ruler
    assert ed.cursor == pytest.approx(ed.ppq * 3, abs=ed.ticks_per_px() * 2)
    ed.cursor = ed.ppq * 3
    left = next(b for b in app.buttons if b and b.action == "sel_left")
    right = next(b for b in app.buttons if b and b.action == "sel_right")
    click(app, left.rect.center)
    assert {doc.notes[i].start for i in ed.selection} == {0, ed.ppq, ed.ppq * 2}
    click(app, right.rect.center)
    assert {doc.notes[i].start for i in ed.selection} == {ed.ppq * k for k in range(3, 8)}


def test_zoom_and_scroll_ease_and_keep_the_mouse_point(app):
    ed = app.editor
    ed.vx.jump(0, ed.ppq * 32)
    x = ed.roll.x + ed.roll.w // 3
    t_under = ed.t_of(x)
    pygame.mouse.set_pos((x, ed.roll.centery))
    ev(app, pygame.KEYDOWN, key=pygame.K_LCTRL, mod=pygame.KMOD_CTRL)
    pygame.key.set_mods(pygame.KMOD_CTRL)
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1, precise_x=0.0, precise_y=1.0, flipped=False))
    pygame.key.set_mods(0)
    assert ed.vx.target_span == pytest.approx(ed.ppq * 32 * 0.8)
    for _ in range(3):                                   # animating, the point under the mouse stays put
        ed.update(1 / 120)
        assert ed.t_of(x) == pytest.approx(t_under, rel=1e-6, abs=1e-6)
    pump(app, 120)
    assert not ed.vx.moving and ed.vx.span == pytest.approx(ed.ppq * 32 * 0.8)
    # plain wheel scrolls time, Shift+wheel scrolls rows
    lo = ed.vx.tlo
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1, precise_x=0.0, precise_y=-1.0, flipped=False))
    assert ed.vx.tlo > lo
    ulo = ed.vy.tlo
    pygame.key.set_mods(pygame.KMOD_SHIFT)
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1, precise_x=0.0, precise_y=1.0, flipped=False))
    pygame.key.set_mods(0)
    assert ed.vy.tlo == pytest.approx(ulo + 3)


def test_scroll_bars_and_middle_drag(app):
    ed = app.editor
    doc = ed.doc
    doc.put_note(Note(doc.new_id(), 1, 0, 60, ed.ppq * 200, ed.ppq * 201, 80))     # a long song
    pump(app, 2)
    ed.vx.jump(0, ed.ppq * 16)
    ed.vy.jump(40, 70)
    ed.sync_scrollbars()
    t = ed.hscroll.thumb()
    drag(app, t.center, (t.centerx + 200, t.centery))
    assert ed.vx.lo > ed.ppq * 16
    v = ed.vscroll.thumb()
    drag(app, v.center, (v.centerx, v.centery - 100))
    assert ed.vy.lo > 40
    lo, ulo = ed.vx.lo, ed.vy.lo
    drag(app, (ed.roll.centerx, ed.roll.centery), (ed.roll.centerx - 150, ed.roll.centery + 60), button=2)
    assert ed.vx.lo > lo and ed.vy.lo > ulo


def test_velocity_pedal_and_tempo_lanes(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    for k in range(8):
        doc.put_note(Note(doc.new_id(), 1, 0, 60, ed.ppq * k, ed.ppq * k + 200, 80))
    doc.commit()
    ed.vx.jump(-ed.ppq * 0.5, ed.ppq * 9)
    pump(app, 2)
    _, vel = ed.lane_rects["velocity"]
    y = vel.y + vel.h // 4
    drag(app, (int(ed.x_of(0)) + 1, y), (int(ed.x_of(ed.ppq * 7)) + 3, y))
    vels = {n.velocity for n in doc.notes.values()}
    assert len(vels) == 1 and 90 < vels.pop() < 110
    # pedal: draw a press, then lift part of it
    _, ped = ed.lane_rects["pedal"]
    drag(app, (int(ed.x_of(ed.ppq)), ped.y + 5), (int(ed.x_of(ed.ppq * 4)), ped.y + 5))
    ser = doc.pedal_series()
    ticks, vals, _ = ser[(0, 64)]
    assert vals.max() == 127 and vals[-1] == 0
    drag(app, (int(ed.x_of(ed.ppq * 2)), ped.centery), (int(ed.x_of(ed.ppq * 3)), ped.centery), button=3)
    ticks, vals, _ = doc.pedal_series()[(0, 64)]
    on_at = lambda t: int(vals[max(0, int((ticks <= t).sum()) - 1)])
    assert on_at(ed.ppq * 1.5) == 127 and on_at(ed.ppq * 2.5) == 0 and on_at(ed.ppq * 3.5) == 127
    # tempo: drag the step up
    _, tmp = ed.lane_rects["tempo"]
    before = doc.tempo_map().bpm_at(ed.ppq * 4)
    drag(app, (int(ed.x_of(ed.ppq * 4)), int(ed._tempo_y(before, tmp))), (int(ed.x_of(ed.ppq * 4)), tmp.y + 6))
    assert doc.tempo_map().bpm_at(ed.ppq * 4) > before + 5
    key(app, pygame.K_z, pygame.KMOD_CTRL)
    assert doc.tempo_map().bpm_at(ed.ppq * 4) == pytest.approx(before)


def test_fingering_keys_menu_and_suggestions(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    for k, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):                 # a C major scale, right hand
        doc.put_note(Note(doc.new_id(), 1, 0, p, ed.ppq * k // 2, ed.ppq * k // 2 + 200, 80))
    doc.commit()
    ed.vx.jump(-50, ed.ppq * 5)
    ed.vy.jump(55, 78)
    key(app, pygame.K_f)
    settle(app)
    assert ed.show_fingering and len(ed.sugg) == 8
    assert all(f for h, f, g in ed.sugg.values())
    # one note: 3
    n = min(doc.notes.values(), key=lambda n: n.start)
    click(app, note_pos(ed, n))
    planned = ed.hand_of(n)                              # (Hand-thesia's hand split decides)
    key(app, pygame.K_3)
    assert (doc.notes[n.id].hand, doc.notes[n.id].finger) == (planned, 3)
    # the menu: left hand, finger 2
    click(app, note_pos(ed, n), button=3)
    assert ed.menu is not None
    key(app, pygame.K_l)                       # opens "Left hand"
    key(app, pygame.K_2)
    assert (doc.notes[n.id].hand, doc.notes[n.id].finger) == ("L", 2)
    # apply the suggestions to the rest: every note then carries its own fingering
    settle(app)
    ed.apply_suggestions()
    assert all(m.finger for m in doc.notes.values())
    assert doc.notes[n.id].finger == 2                    # (its own finger untouched)


def test_sequential_fingering(app):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    for k, p in enumerate([64, 65, 67]):
        doc.put_note(Note(doc.new_id(), 1, 0, p, ed.ppq * k, ed.ppq * k + 200, 80, hand="R"))
    doc.commit()
    first = min(doc.notes.values(), key=lambda n: n.start)
    ed.show_fingering = True
    ed.start_sequential(first.id)
    for k in (pygame.K_1, pygame.K_2, pygame.K_3):
        key(app, k)
    assert [n.finger for n in sorted(doc.notes.values(), key=lambda n: n.start)] == [1, 2, 3]
    assert ed.seq is None                                   # (reached the end)


def test_project_round_trip(app, tmp_path):
    ed = app.editor
    doc = ed.doc
    doc.begin("setup")
    ids = []
    for k in range(5):
        n = Note(doc.new_id(), 1, 0, 60 + k, ed.ppq * k, ed.ppq * k + 100, 70 + k, hand="R", finger=k + 1)
        doc.put_note(n)
        ids.append(n.id)
    doc.put_event(Event(doc.new_id(), 1, 50, 0, bytes([0xB0, 64, 127])))
    doc.commit()
    ed.selection = {ids[1], ids[3]}
    ed.vx.jump(10, 5000)
    ed.cursor = 777
    from miditool import project
    path = os.path.join(tmp_path, "p.miditool")
    project.save(path, ed)
    app.open_path(path)
    ed2 = app.editor
    assert sorted((n.pitch, n.start, n.velocity, n.hand, n.finger) for n in ed2.doc.notes.values()) == \
        [(60 + k, ed.ppq * k, 70 + k, "R", k + 1) for k in range(5)]
    assert sorted(ed2.doc.notes[i].pitch for i in ed2.selection) == [61, 63]
    assert ed2.cursor == 777 and ed2.vx.lo == pytest.approx(10)
    assert not ed2.doc.dirty




def test_impose_a_perfect_source_keeps_fingering(app):
    """A played copy of a melody with one wrong note and one missing: impose the score on it."""
    import random
    rnd = random.Random(3)
    melody = [60 + rnd.choice([0, 2, 4, 5, 7, 9, 11, 12]) for _ in range(60)]
    perfect = Document.new_empty()
    perfect.begin("score")
    for k, p in enumerate(melody):
        perfect.put_note(Note(perfect.new_id(), 1, 0, p, 240 * k, 240 * k + 200, 80))
    perfect.commit()
    data = perfect.to_bytes()[0]
    doc = app.editor.doc
    doc.begin("played")
    for k, p in enumerate(melody):
        if k == 30:
            continue                                          # missing
        jitter = rnd.randint(-12, 12)
        doc.put_note(Note(doc.new_id(), 1, 0, p, 240 * k + jitter, 240 * k + 190, 70, hand="R", finger=1 + k % 5))
    doc.put_note(Note(doc.new_id(), 1, 0, 90, 240 * 20 + 60, 240 * 20 + 120, 40))     # a wrong note
    doc.commit()
    before = {n.id: n for n in doc.notes.values()}
    app._start_impose(Document.from_bytes(data, "score.mid"), data, "score")
    settle(app)
    s = app.editor.impose
    assert s is not None and s.allowed
    assert [doc.notes[i].pitch for i in s.removals] == [90] and len(s.additions) == 1
    app.render()
    click(app, next(b for b in app.editor._impose_buttons if b.action == "apply").rect.center)
    assert len(doc.notes) == len(before)                      # one out, one in
    assert all((doc.notes[i].hand, doc.notes[i].finger) == (n.hand, n.finger) for i, n in before.items()
               if i in doc.notes)
    key(app, pygame.K_z, pygame.KMOD_CTRL)                    # one undo takes it all back
    assert set(doc.notes) == set(before)
