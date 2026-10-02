"""Edge cases found by fuzzing the app: empty files, resizing under a dialog, the window's left edge, names."""
import os

import pretty_midi
import pygame

from conftest import midi_path

import main
import midi_loader
import pianist


def write_midi(path, notes):
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(0)
    inst.notes = [pretty_midi.Note(80, p, s, e) for p, s, e in notes]
    pm.instruments.append(inst)
    pm.write(str(path))
    return str(path)


def test_a_file_without_notes_says_so(screen, tmp_path):
    app = main.App(screen, sound=False)
    path = write_midi(tmp_path / "silent.mid", [])
    song = midi_loader.load_song(path)
    assert song and len(song) == 0                    # loaded, though empty
    for go in (app.choose_playback, app.play, app.edit):
        app.menu()
        go(path)
        assert isinstance(app.mode, main.MainMenu) and "no notes" in app.mode.message
    # an empty song handed over directly still plays and edits without trouble
    app.play(song)
    v = app.mode
    v.toggle_pause()
    for t in (-5.0, 0.0, 10.0):
        v.seek(t)
        v.update(1 / 60)
        v.render()
    app.edit(song)
    app.mode.render()


def test_resizing_under_the_editors_dialog(screen):
    app = main.App(screen, sound=False)
    app.edit(midi_path("demo_song.mid"))
    ed = app.mode
    ed.dirty = True
    ed._guard(lambda: True)                           # "Unsaved fingering changes" asks
    assert ed.dialog is not None
    pygame.display.set_mode((700, 480), pygame.RESIZABLE)
    app.handle_event(pygame.event.Event(pygame.VIDEORESIZE, size=(700, 480), w=700, h=480))
    assert ed.dialog is not None and ed.keyboard.rect.bottom <= 480
    ed.render()


def test_dragging_the_waveform_from_the_left_edge(screen, tmp_path):
    from audio_sync import SyncAudio
    from test_audio_sync import song_of, write_wav
    app = main.App(screen, sound=False)
    app.play(song_of(10.0), audio=SyncAudio(write_wav(tmp_path / "a.wav", 14.0)), speed=1.0)
    v = app.mode
    v.seek(3.0)
    v.toggle_pause()                                  # playing
    r = v.wave_rect
    assert r.x == 0
    v.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(r.w // 10, r.centery)))
    v.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(0, r.centery), rel=(0, 0), buttons=(1, 0, 0)))
    assert abs(v.audio.offset - 1.0) < 0.05           # moved, and still being dragged at x = 0
    v.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(r.w // 20, r.centery), rel=(0, 0), buttons=(1, 0, 0)))
    assert abs(v.audio.offset - 0.5) < 0.05
    v.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(0, r.centery)))
    assert v.dragging_wave is None and v.audio.channel is not None          # the recording plays on


def test_a_pianist_called_settings_keeps_the_settings_file():
    p = pianist.save(pianist.Pianist("Settings"))
    assert p.id != "settings"
    pianist.set_app_setting("keys", "equal")
    assert pianist.app_setting("keys") == "equal"
    assert any(q.name == "Settings" for q in pianist.list_pianists())
    assert os.path.exists(pianist.SETTINGS)


def test_undo_in_sequential_mode_reaching_the_other_hand(screen):
    from midi_loader import LEFT, RIGHT
    app = main.App(screen, sound=False)
    app.edit(midi_path("demo_song.mid"))
    ed = app.mode
    li = next(i for i, n in enumerate(ed.notes) if n.hand == LEFT)
    ri = next(i for i, n in enumerate(ed.notes) if n.hand == RIGHT)
    ed.set_note(li, LEFT, 3 if ed.finger[li] != 3 else 2)      # an edit to the left hand...
    ed.start_sequential(ri)                                    # ...then right-hand sequential fingering
    at = ed.seq_note()
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL, unicode=""))
    assert ed.seq is not None and ed.seq_note() == at          # undone, and still where it was
    assert not ed.undo_stack
    ed.render()
