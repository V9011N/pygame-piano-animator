"""Rendering a performance to an .mp4 (video_export.py, the player's export button)."""
import glob
import os
import time

import numpy as np
import pygame
import pytest

import main
import pianist
import video_export
from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo


def short_song():
    ns = [Note(p, 0.3 + 0.2 * i, 0.6 + 0.2 * i, 90, 0, RIGHT) for i, p in enumerate((60, 64, 67, 72))]
    ns.append(Note(48, 0.3, 1.2, 80, 1, LEFT))
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    song = MidiSong(ns, tracks, 1.3, [], [])
    song.title = "short"
    return song


def player(screen, size=(321, 201)):
    surf = pygame.display.set_mode(size)
    app = main.App(surf, sound=False)
    app.play(short_song())
    return app.mode


def click(v, pos):
    v.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))


def test_the_recording_is_cut_where_the_song_is_heard():
    class Rec:                                   # a decoded recording: 1 kHz, mono, its samples numbered
        freq, channels, sample_bytes = 1000, 1, 2
        offset = 0.5
        raw = np.arange(1, 3001, dtype=np.int16).tobytes()
    # song time -1 at half speed is heard at 0.5 + -1 / 0.5 = -1.5 s into it: 1.5 s of silence first
    out, rate = video_export.recording_audio(Rec, -1.0, 0.5, 5.0)
    assert rate == 1000 and out.shape == (5000, 1)
    assert not out[:1500].any()
    assert out[1500, 0] == 1 and out[1500 + 2999, 0] == 3000
    assert not out[1500 + 3000:].any()                  # (past its end: silence)


def test_the_export_menu_chooses_what_is_recorded(screen):
    v = player(screen, (900, 560))
    v.render()
    assert v.export_button.w > 0 and not v.export_menu
    click(v, v.export_button.center)
    v.render()
    assert v.export_menu and v.export_confirm.w > 0
    part, r = v._export_checks[0]
    assert part == "bar" and v.export_parts["bar"]
    click(v, r.center)
    assert not v.export_parts["bar"] and pianist.app_setting("export_parts")["bar"] is False
    # without the progress bar the notes fall from the very top of the video
    v.layout((900, 560), video=v.export_parts)
    assert v.bar_rect.h == 0 and v.fall_rect.top == 0
    v.layout((900, 560))
    click(v, (450, 300))                                 # a click elsewhere closes the menu
    assert not v.export_menu


@pytest.mark.skipif(not video_export.default_soundfont(), reason="no General MIDI soundfont to render the notes")
def test_a_performance_renders_to_an_mp4_with_its_sound(screen, tmp_path):
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    v = player(screen)
    v.export_parts.update(bar=True, pedals=False, badge=False)
    path = str(tmp_path / "out.mp4")
    v.start_export(path)
    ex = v.export
    assert ex.size == (320, 200)                         # (even sides)
    deadline = time.time() + 120
    while v.export is not None and time.time() < deadline:
        v.update(0.016)
        v.render()
    assert v.export is None and ex.finished and not ex.error, ex.error
    assert v.dialog is not None and v.dialog.title == "Video exported"
    frames, secs = imageio_ffmpeg.count_frames_and_secs(path)
    assert frames == ex.frames and abs(secs - ex.seconds) < 0.1
    assert ex.has_audio
    assert sorted(os.listdir(tmp_path)) == ["out.mp4"]      # the temporary files are gone
    assert v.t == v.start_time()                          # back where playback was


def test_cancelling_an_export_leaves_nothing_behind(screen, tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    v = player(screen)
    v.start_export(str(tmp_path / "out.mp4"))
    for _ in range(3):
        v.update(0.016)
        v.render()
    click(v, v.export_cancel.center)
    assert v.export is None and v.dialog is None
    deadline = time.time() + 10
    while glob.glob(str(tmp_path / "*")) and time.time() < deadline:
        time.sleep(0.1)
    assert glob.glob(str(tmp_path / "*")) == []
