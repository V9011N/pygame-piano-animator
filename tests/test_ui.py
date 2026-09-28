"""The app's screens, driven headlessly with scripted events."""
import pygame

from conftest import midi_path

import main
import midi_loader


def app_on(screen):
    return main.App(screen, sound=False)


def key(app, k, mod=0):
    return app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod))


def test_menu_player_and_studio_render(screen):
    app = app_on(screen)
    app.mode.render()
    app.play(midi_path("demo_song.mid"))
    assert type(app.mode).__name__ == "Visualizer"
    app.mode.seek(1.0)
    app.mode.render()
    app.studio()
    app.mode.render()
    assert type(app.mode).__name__ == "PianistStudio"


def test_editor_sequential_fingering(screen):
    app = app_on(screen)
    app.edit(midi_loader.load_song(midi_path("demo_song.mid")))
    ed = app.mode
    assert type(ed).__name__ == "FingeringEditor"
    first = min(range(len(ed.notes)), key=lambda i: (ed.notes[i].start, -ed.notes[i].pitch))
    ed.start_sequential(first)
    hand = ed.seq["hand"]
    assert all(ed.notes[j].hand == hand for st in ed.seq["steps"] for j in st)
    ed.render()

    key(app, pygame.K_3)                                  # same hand, finger 3
    assert ed.finger[first] == 3 and first in ed.user_set
    assert ed.seq_note() != first                         # moved on
    nxt = ed.seq_note()
    key(app, pygame.K_QUOTE if hand == "R" else pygame.K_LSHIFT)   # that hand's 5
    assert ed.finger[nxt] == 5
    key(app, pygame.K_BACKSPACE)
    assert ed.seq_note() == nxt
    ed.update(0.5)                                        # deferred rebuild of the hands
    assert ed._rebuild_due is None
    ed.render()
    key(app, pygame.K_ESCAPE)
    assert ed.seq is None and ed.dirty


def test_nail_slides_to_tip_and_squashes_as_finger_curls():
    import math
    import skins

    class Pen:
        def poly(self, pts, color):
            self.pts = pts

    class Hand:
        pass
    r, L = 9.0, 22.0
    spans = []
    for deg in (0, 30, 60, 80, 90, 110):
        t = math.radians(deg)
        a = (0.0, 0.0, 40.0)                              # last knuckle; the finger points up (-y)
        b = (0.0, -L * math.cos(t), 40.0 - L * math.sin(t))
        h = Hand()
        h.radius = {3: [((0.0, 30.0, 60.0), a, r * 1.2), (a, b, r)]}
        pen = Pen()
        skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
        ys = [p[1] for p in pen.pts]
        tip_end = min(b[1] - r, a[1] - r * 1.2)            # far end of the finger's outline
        spans.append((min(ys) - tip_end, max(ys) - min(ys)))
    gaps, lengths = zip(*spans)
    # the skin beyond the nail shrinks away, and the nail gets shorter
    assert gaps[0] > 0.5 * r and all(g < 0.15 * r for g in gaps[3:])
    assert all(x >= y - 1e-6 for x, y in zip(gaps, gaps[1:4]))
    assert all(x >= y - 1e-6 for x, y in zip(lengths, lengths[1:4]))
    assert lengths[3] < 0.5 * lengths[0]
