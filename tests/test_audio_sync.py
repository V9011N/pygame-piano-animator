"""Syncing a recording to the MIDI: the setup screens, the length check, the waveform drag and the clock."""
import time
import wave

import numpy as np
import pygame

from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo


def song_of(seconds):
    notes = [Note(60 + i % 12, 0.5 * i, 0.5 * i + 0.4, 80, 0, RIGHT) for i in range(int(seconds * 2))]
    notes += [Note(48, 0.0, 1.0, 80, 1, LEFT)]
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    return MidiSong(notes, tracks, seconds, [], [])


def write_wav(path, seconds, sr=22050):
    t = np.arange(int(sr * seconds)) / sr
    x = (np.sin(2 * np.pi * 220 * t) * 8000 * (t % 1.0 < 0.2)).astype(np.int16)   # a click each second
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return str(path)


def key(k):
    return pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="")


def test_setup_checks_the_length_and_syncs(screen, tmp_path):
    import main
    from audio_sync import PlaybackSetup
    app = main.App(screen, sound=False)
    song = song_of(10.0)
    app.choose_playback(song)
    setup = app.mode
    assert isinstance(setup, PlaybackSetup) and setup.page == "sound"
    setup.render()
    app.handle_event(key(pygame.K_2))                       # sync an audio file: the speed first
    assert setup.page == "speed"
    setup._set_speed(0.5)                                   # half speed: the MIDI takes 20 s
    assert abs(setup.needed() - 20.0) < 1e-6
    setup.render()
    assert not setup.open_audio(write_wav(tmp_path / "short.wav", 15.0))     # too short at 50%
    assert "15" in setup.message or "0:15" in setup.message
    assert app.mode is setup
    assert setup.open_audio(write_wav(tmp_path / "long.wav", 24.0))
    v = app.mode
    assert isinstance(v, main.Visualizer) and v.audio is not None
    assert v.paused and v.speed == 0.5                     # paused to line it up, speed fixed
    assert v.wave_rect.h > 0 and v.fall_rect.top == v.wave_rect.bottom
    app.handle_event(key(pygame.K_UP))
    assert v.speed == 0.5
    v.render()
    # default sound: straight to the player, playing, the speed changeable
    app.choose_playback(song)
    app.handle_event(key(pygame.K_1))
    assert isinstance(app.mode, main.Visualizer) and app.mode.audio is None and app.mode.wave_rect.h == 0


def test_dragging_the_waveform_moves_the_recording(screen, tmp_path):
    import main
    from audio_sync import SyncAudio
    app = main.App(screen, sound=False)
    app.play(song_of(10.0), audio=SyncAudio(write_wav(tmp_path / "a.wav", 14.0)), speed=1.0)
    v = app.mode
    r = v.wave_rect
    x0 = r.x + r.w // 2
    v.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x0, r.centery)))
    v.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(x0 - r.w // 10, r.centery), rel=(0, 0), buttons=(1, 0, 0)))
    v.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(x0 - r.w // 10, r.centery)))
    # dragged left by a tenth of the 10 s timeline: the recording starts 1 s later in it
    assert abs(v.audio.offset - 1.0) < 0.05
    v.seek(4.0)
    assert abs(v.audio_pos() - 5.0) < 0.05
    off = v.audio.offset
    v.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PERIOD, mod=0, unicode="."))
    assert abs(v.audio.offset - (off - 0.01)) < 1e-9               # nudged: 10 ms
    v.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_COMMA, mod=pygame.KMOD_SHIFT, unicode=","))
    assert abs(v.audio.offset - (off + 0.09)) < 1e-9               # 100 ms with Shift
    v.render()


def test_the_song_follows_the_recordings_clock(screen, tmp_path):
    import main
    from audio_sync import SyncAudio
    app = main.App(screen, sound=False)
    audio = SyncAudio(write_wav(tmp_path / "a.wav", 14.0))
    audio.offset = 0.5
    app.play(song_of(10.0), audio=audio, speed=0.5)
    v = app.mode
    v.seek(2.0)
    v.toggle_pause()                                        # play: the recording starts at 0.5 + 2 / 0.5
    assert not v.paused and audio.channel is not None
    time.sleep(0.2)
    v.update(0.0)                                           # however long the frame said it was
    assert abs(v.t - (2.0 + 0.2 * 0.5)) < 0.03
    assert abs(v.audio_pos() - (0.5 + v.t / 0.5)) < 1e-9
    v.toggle_pause()
    assert audio.channel is None
    app.menu()                                              # the synth gets its sound back
    assert not app.midi.muted


def test_waveform_peaks_and_strip(screen, tmp_path):
    from audio_sync import PEAK_T, SyncAudio
    a = SyncAudio(write_wav(tmp_path / "a.wav", 3.0))
    assert abs(a.length - 3.0) < 0.01
    assert abs(len(a.peaks) * PEAK_T - 3.0) < 0.05 and a.peaks.max() == 1.0
    loud = a.peaks[int(0.1 / PEAK_T)]
    quiet = a.peaks[int(0.6 / PEAK_T)]
    assert loud > 0.9 and quiet < 0.05                       # the clicks show
    surf = a.strip(300, 40, 0.0, 3.0, 1.0, 3.0)
    assert surf.get_size() == (300, 40)
