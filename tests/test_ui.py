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
