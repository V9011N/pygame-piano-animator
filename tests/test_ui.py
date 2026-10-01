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


def test_no_nail_on_the_knuckle_of_a_tightly_curled_finger():
    import skins

    class Pen:
        pts = None

        def poly(self, pts, color):
            self.pts = pts

    class Hand:
        pass
    # strongly curved: the middle phalanx points straight down (its direction
    # on screen a fraction of a pixel, backward) and the tip tucks back under it
    pip, dip = (550.7, 572.2, 57.3), (550.7, 572.3, 26.6)
    h = Hand()
    h.radius = {3: [((557.2, 622.8, 59.8), pip, 13.8), (pip, dip, 12.0), (dip, (553.2, 591.8, 17.7), 9.7)]}
    pen = Pen()
    skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
    assert pen.pts is None
    # only just past straight down, the nail still caps the end of the finger
    h.radius[3][-1] = (dip, (550.9, 573.5, 0.0), 9.7)
    skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
    assert pen.pts and min(p[1] for p in pen.pts) < 572.3 - 9.0


def test_finger_thickness_range_and_default():
    import skins
    assert skins.FINGER_WIDTH_RANGE == (0.5, 1.2)
    assert skins.default_skin()["finger_width"] == 0.75
    # older pianists' values outside the new range are brought into it
    assert skins.normalize({"finger_width": 1.35})["finger_width"] == 1.2
    assert skins.normalize({"finger_width": 0.3})["finger_width"] == 0.5


def test_changelog_button_glows_until_opened(screen):
    app = app_on(screen)
    menu = app.mode
    assert menu.changelog_new                                  # fresh pianists folder: not seen yet
    menu.render()
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=menu.changelog_button.rect.center)
    app.handle_event(click)
    assert menu.changelog is not None and not menu.changelog_new
    menu.render()
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-5))
    assert menu.changelog.scroll > 0
    key(app, pygame.K_ESCAPE)                                  # closes the changelog, not the app
    assert menu.changelog is None and app.mode is menu
    assert not main.MainMenu(app).changelog_new                # remembered
    entries = main.changelog_entries()
    assert len(entries) > 10 and all(bullets for _, bullets in entries)
