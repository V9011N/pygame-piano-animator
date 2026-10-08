"""The main menu's Recent list: setups recorded as they're launched, opened again, and dropped when a file's gone."""
import os
import shutil
import wave

import pygame

import recent
from conftest import midi_path


def song_copy(tmp_path, name="demo_song.mid"):
    dst = tmp_path / name
    shutil.copy(midi_path("demo_song.mid"), dst)
    return str(dst)


def silent_wav(path, seconds, rate=22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return str(path)


def test_the_list_keeps_the_newest_five_setups_once_each(tmp_path):
    files = []
    for i in range(7):
        files.append(str(tmp_path / f"s{i}.mid"))
        recent.add_edit(files[-1])
    got = recent.entries()
    assert [os.path.basename(e["midi"]) for e in got] == ["s6.mid", "s5.mid", "s4.mid", "s3.mid", "s2.mid"]
    # the same setup again moves to the top; played and edited are different setups
    recent.add_edit(files[3])
    recent.add_play(files[3], soundfont=None)
    got = recent.entries()
    assert [(e["mode"], os.path.basename(e["midi"])) for e in got[:3]] == \
        [("play", "s3.mid"), ("edit", "s3.mid"), ("edit", "s6.mid")]
    # played with another soundfont: the same setup, the newest kept; with a recording: another
    recent.add_play(files[3], soundfont=str(tmp_path / "Grand.sf2"))
    recent.add_play(files[3], audio=str(tmp_path / "take.wav"), speed=0.75)
    got = recent.entries()
    assert recent.describe(got[0]) == ("s3.mid", "Play  ·  synced to take.wav at 75%")
    assert recent.describe(got[1]) == ("s3.mid", "Play  ·  soundfont Grand")
    assert recent.describe(got[2]) == ("s3.mid", "Fingering editor")
    recent.remove(got[1])
    assert len(recent.entries()) == 4 and recent.entries()[1]["mode"] == "edit"


def test_launches_are_recorded_and_open_again(screen, tmp_path):
    import editor
    import main
    app = main.App(screen, sound=False)
    path = song_copy(tmp_path)
    app.edit(path)
    assert isinstance(app.mode, editor.FingeringEditor)
    app.menu()
    app.play(path)
    assert isinstance(app.mode, main.Visualizer)
    got = recent.entries()
    assert [(e["mode"], e["midi"]) for e in got] == [("play", path), ("edit", path)]
    assert got[0]["soundfont"] is None
    # opened from the menu: the Recent button, then the setup's row (or its number key)
    app.menu()
    menu = app.mode
    menu.render()
    rb = next(b for b in menu.buttons if b.action == "recent")
    assert rb.enabled
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rb.rect.center))
    assert menu.recent is not None
    menu.render()
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_2, mod=0, unicode="2"))
    assert isinstance(app.mode, editor.FingeringEditor) and app.mode.song.path == path
    assert recent.entries()[0]["mode"] == "edit"                    # (to the top again)


def test_a_synced_recording_opens_again_at_its_speed(screen, tmp_path):
    import main
    from audio_sync import SyncAudio
    app = main.App(screen, sound=False)
    path = song_copy(tmp_path)
    song, hands = app._load(path)
    take = silent_wav(tmp_path / "take.wav", song.duration / 0.5 + 1)
    app.play(song, audio=SyncAudio(take), speed=0.5, hands=hands)
    assert recent.entries()[0] == {"mode": "play", "midi": path, "audio": take, "speed": 0.5}
    app.menu()
    app.open_recent(recent.entries()[0])
    v = app.mode
    assert isinstance(v, main.Visualizer) and v.audio is not None and v.audio.path == take and v.speed == 0.5
    v.audio.stop()


def test_a_setup_whose_file_is_gone_says_so_and_leaves_the_list(screen, tmp_path):
    import main
    app = main.App(screen, sound=False)
    path = song_copy(tmp_path)
    keep = song_copy(tmp_path, "other.mid")
    recent.add_edit(keep)
    recent.add_play(path, audio=str(tmp_path / "gone.wav"), speed=1.0)
    app.open_recent(recent.entries()[0])
    menu = app.mode
    assert isinstance(menu, main.MainMenu) and menu.dialog is not None
    assert menu.dialog.title == "File not found" and "audio file gone.wav" in menu.dialog.message
    assert [e["midi"] for e in recent.entries()] == [keep]
    menu.render()                                                    # (the error drawn over the menu)
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r"))
    assert menu.dialog is None
    # the MIDI file itself gone
    os.remove(keep)
    app.open_recent(recent.entries()[0])
    assert "MIDI file other.mid" in app.mode.dialog.message and recent.entries() == []
    app.mode.render()
    assert not next(b for b in app.mode.buttons if b.action == "recent").enabled
