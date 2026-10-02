"""Loading in the background: the progress bar, results and errors handed back, and every way in."""
import pygame
import pytest

from conftest import midi_path

import progress
from midi_loader import RIGHT


def test_stages_nest_and_only_count_while_tracked():
    progress.report(0.5)                       # nothing tracked: ignored
    progress.begin()
    with progress.stage(0.0, 0.5):
        progress.report(0.5)
        assert progress.value() == 0.25
        with progress.stage(0.5, 1.0):
            progress.report(0.0)
            assert progress.value() == 0.25
            progress.report(1.0)
            assert progress.value() == 0.5
    assert progress.value() == 0.5
    with progress.stage(0.5, 1.0):
        progress.report(0.2)                   # never goes back
        assert progress.value() == 0.6
    assert progress.value() == 1.0
    progress.end()
    progress.report(0.0)
    assert progress.value() == 1.0


def test_run_busy_returns_the_result_and_raises_the_error(screen):
    from common import load_fonts, run_busy
    fonts = load_fonts()
    seen = []

    def job():
        for i in range(5):
            progress.report(i / 5)
            seen.append(progress.value())
        return 42
    assert run_busy(screen, fonts, "Working…", job) == 42
    assert seen == sorted(seen) and seen[-1] == 0.8

    def bad():
        raise ValueError("no good")
    with pytest.raises(ValueError):
        run_busy(screen, fonts, "Working…", bad)
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    run_busy(screen, fonts, "Working…", lambda: None)
    assert any(e.type == pygame.QUIT for e in pygame.event.get())      # closing the window waits, isn't lost


def test_a_file_loads_with_its_hands_planned(screen):
    import main
    from audio_sync import PlaybackSetup
    app = main.App(screen, sound=False)
    app.choose_playback(midi_path("demo_song.mid"))
    setup = app.mode
    assert isinstance(setup, PlaybackSetup) and setup.hands and RIGHT in setup.hands
    setup._do("default")
    v = app.mode
    assert isinstance(v, main.Visualizer) and v.hands is setup.hands           # planned once
    v.update(0.1)
    v.render()
    app.menu()
    app.choose_playback(str(midi_path("demo_song.mid")) + ".missing")
    assert isinstance(app.mode, main.MainMenu) and "Could not open" in app.mode.message


def test_the_editor_opens_another_file(screen):
    import main
    app = main.App(screen, sound=False)
    app.edit(midi_path("demo_song.mid"))
    ed = app.mode
    first = ed.song
    assert ed._load_path(midi_path("demo_song.mid"))
    assert ed.song is not first and ed.hands and len(ed.finger) == len(ed.notes)
    ed._load_path(midi_path("demo_song.mid") + ".missing")    # a message, not a crash
    ed.render()
