"""Choosing a soundfont: the player (with a stand-in synth), the pedals, the Settings and the sound choice."""
import pygame

from midi_loader import LEFT, RIGHT, Note


class FakeSynth:
    """Records what sf2.Synth would be asked to do."""

    def __init__(self):
        self.on, self.off, self.cc = [], [], []

    def sfload(self, path):
        return 0

    def program_select(self, ch, sfid, bank, preset):
        pass

    def noteon(self, ch, key, vel):
        self.on.append((ch, key, vel))

    def noteoff(self, ch, key):
        self.off.append((ch, key))

    def control_change(self, ch, cc, v):
        self.cc.append((ch, cc, v))

    def notes_off(self, ch=None):
        pass

    def sounds_off(self, ch=None):
        pass

    def stop(self):
        pass


def player(name="Grand.sf2"):
    from sf_synth import SoundfontOut
    return SoundfontOut(name, synth=FakeSynth(), start=False)


def test_the_pedals_hold_and_soften_the_notes():
    out = player()
    s = out.synth
    c4, e4 = Note(60, 0, 1, 100, 0, RIGHT), Note(64, 0, 1, 100, 0, RIGHT)
    lo = Note(36, 0, 1, 80, 1, LEFT)
    # sustain: a let-go key rings until the pedal lifts
    out.control_change(64, 127)
    out.note_on(c4)
    out.note_off(c4)
    assert s.off == []
    out.control_change(64, 0)
    assert s.off == [(0, 60)]
    # sostenuto: only the keys down when it went down are held
    out.note_on(c4)
    out.control_change(66, 127)
    out.note_on(e4)
    out.note_off(c4)
    out.note_off(e4)
    assert s.off == [(0, 60), (0, 64)]                  # E4 stops, C4 is caught
    out.control_change(66, 0)
    assert s.off[-1] == (0, 60)
    # a key struck again while it rings on stops first
    out.control_change(64, 127)
    out.note_on(lo)
    out.note_off(lo)
    out.note_on(lo)
    assert s.off[-1] == (1, 36) and s.on[-1] == (1, 36, 80)        # (the left hand on channel 1)
    # the soft pedal plays new notes softer
    out.control_change(67, 127)
    out.note_on(c4)
    assert s.on[-1] == (0, 60, 70)
    # the volume is the channels' volume
    out.set_volume(0.5)
    assert (0, 7, 64) in s.cc and (1, 7, 64) in s.cc
    assert out.status() == "Grand" and out.name == "Grand"


def test_no_soundfont_or_a_missing_one_plays_the_system_synth(tmp_path):
    from common import MidiOut
    from sf_synth import make_synth
    out, problem = make_synth(False, None)
    assert isinstance(out, MidiOut) and problem == "" and out.name == ""
    out, problem = make_synth(True, str(tmp_path / "gone.sf2"))
    assert isinstance(out, MidiOut) and problem == "not found"


def test_settings_choose_a_soundfont_and_the_sound_choice_names_it(screen, tmp_path, monkeypatch):
    import app_settings
    import main
    import pianist
    import sf_synth
    from conftest import midi_path
    sf = tmp_path / "Concert Grand.sf2"
    sf.write_bytes(b"RIFF")
    monkeypatch.setattr(sf_synth, "make_synth", lambda sound, path: (player(path), "") if path else
                        (main.MidiOut(False), ""))
    monkeypatch.setattr(app_settings, "pick_file", lambda *a, **k: str(sf))
    app = main.App(screen, sound=True)
    app.settings()
    st = app.mode
    st.render()
    assert st.sound_text() == "Now: default (the system's MIDI synth)."
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.sf_browse.rect.center))
    assert app.midi.name == "Concert Grand" and pianist.app_setting("soundfont") == str(sf)
    assert st.sound_text() == "Now: Concert Grand."
    # the button after choosing a MIDI file to play names it
    app.choose_playback(midi_path("demo_song.mid"))
    app.mode.render()
    assert app.mode.default_button.label == "Soundfont: Concert Grand"
    assert "Concert Grand soundfont" in app.mode.default_button.sub
    # back to the default
    app.settings()
    st = app.mode
    st.render()
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.sf_default.rect.center))
    assert app.midi.name == "" and pianist.app_setting("soundfont") is None
    app.choose_playback(midi_path("demo_song.mid"))
    app.mode.render()
    assert app.mode.default_button.label == "Default sound"
    # a soundfont that has gone: "default", and why
    monkeypatch.setattr(sf_synth, "make_synth", lambda sound, path: (main.MidiOut(False), "not found"))
    app.set_soundfont(str(tmp_path / "moved.sf2"))
    app.settings()
    assert app.mode.sound_text() == "Now: default - moved not found."


class ToneSynth(FakeSynth):
    """A stand-in that generates a steady stereo tone (left 0.5, right -0.5)."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def generate(self, frames):
        import numpy as np
        self.asked.append(frames)
        return memoryview(np.tile(np.float32([0.5, -0.5]), frames).tobytes())


def test_the_soundfont_plays_through_the_mixer_in_its_format():
    import threading
    from sf_synth import CHUNK_FRAMES, MixerStream
    stream = MixerStream.__new__(MixerStream)                # (no thread: chunks made by hand)
    stream.synth, stream.lock, stream.rate = ToneSynth(), threading.RLock(), 44100
    pygame.mixer.quit()
    pygame.mixer.init(frequency=44100, size=-16, channels=2)
    try:
        init = pygame.mixer.get_init()
        a = pygame.sndarray.array(stream.chunk(init))
        assert a.shape == (CHUNK_FRAMES, 2) and abs(a[0, 0] - 16384) < 2 and abs(a[0, 1] + 16384) < 2
        # another rate (a synced recording's): resampled to the same length
        a = pygame.sndarray.array(stream.chunk((22050, init[1], init[2])))
        assert stream.synth.asked[-1] == 2 * CHUNK_FRAMES and len(a) == CHUNK_FRAMES
        # the thread keeps the reserved channel playing
        out = MixerStream(ToneSynth(), threading.RLock(), 44100)
        for _ in range(100):
            if out.synth.asked:
                break
            pygame.time.wait(10)
        assert out.synth.asked and out.channel is not None
        out.stop()
        assert not out.thread.is_alive()
    finally:
        pygame.mixer.quit()


def test_reopening_the_mixer_for_a_recording_doesnt_crash_the_soundfont():
    """A synced recording reopens the mixer at its own rate while the soundfont's thread feeds it."""
    import threading
    from audio_sync import ensure_mixer
    from sf_synth import CHUNK_FRAMES, MixerStream
    pygame.mixer.quit()
    ensure_mixer(44100)
    out = MixerStream(ToneSynth(), threading.RLock(), 44100)
    try:
        for rate in (48000, 22050, 44100, 48000):
            ensure_mixer(rate)
            pygame.time.wait(30)
        n = len(out.synth.asked)
        for _ in range(100):
            if len(out.synth.asked) > n:
                break
            pygame.time.wait(10)
        assert len(out.synth.asked) > n and out.channel is not None     # still feeding the reopened mixer
        assert out.synth.asked[-1] == round(CHUNK_FRAMES * 44100 / 48000)        # (resampled to its rate)
    finally:
        out.stop()
        pygame.mixer.quit()


# ----- the built-in player (sf2.py) ------------------------------------------------
def tiny_sf2(path, preset_gens=(), inst_gens=(), loop=True, rate=22050, root=69):
    """A SoundFont with one preset (0:0) of one instrument zone: a sine at `root`, looped over whole periods."""
    import struct
    import numpy as np
    period = rate / 440.0 * 2 ** ((69 - root) / 12)
    n = int(round(period * 50))
    wave = (np.sin(2 * np.pi * np.arange(n + 46) / period) * 16000).astype("<i2")
    smpl = wave.tobytes()
    gens = lambda gs: b"".join(struct.pack("<Hh", op, v) if not isinstance(v, tuple) else
                               struct.pack("<HBB", op, *v) for op, v in gs)

    def chunk(cid, data):
        return cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")

    pg = gens(list(preset_gens) + [(41, 0)])
    ig = gens(list(inst_gens) + [(54, 1 if loop else 0), (53, 0)])
    n_pg, n_ig = len(pg) // 4, len(ig) // 4
    pdta = b"pdta" + b"".join([
        chunk(b"phdr", struct.pack("<20sHHHIII", b"Tone", 0, 0, 0, 0, 0, 0) +
              struct.pack("<20sHHHIII", b"EOP", 0, 0, 1, 0, 0, 0)),
        chunk(b"pbag", struct.pack("<HH", 0, 0) + struct.pack("<HH", n_pg, 0)),
        chunk(b"pmod", b"\0" * 10),
        chunk(b"pgen", pg + b"\0" * 4),
        chunk(b"inst", struct.pack("<20sH", b"Tone", 0) + struct.pack("<20sH", b"EOI", 1)),
        chunk(b"ibag", struct.pack("<HH", 0, 0) + struct.pack("<HH", n_ig, 0)),
        chunk(b"imod", b"\0" * 10),
        chunk(b"igen", ig + b"\0" * 4),
        chunk(b"shdr", struct.pack("<20sIIIIIBbHH", b"sine", 0, n, 0, n, rate, root, 0, 0, 1) +
              struct.pack("<20sIIIIIBbHH", b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0)),
    ])
    body = b"sfbk" + chunk(b"LIST", b"INFO" + chunk(b"ifil", struct.pack("<HH", 2, 1))) + \
        chunk(b"LIST", b"sdta" + chunk(b"smpl", smpl)) + chunk(b"LIST", pdta)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def render(synth, key, vel, held, after=0.0):
    import numpy as np
    synth.noteon(0, key, vel)
    a = np.frombuffer(bytes(synth.generate(int(synth.rate * held))), np.float32).reshape(-1, 2)
    synth.noteoff(0, key)
    b = np.frombuffer(bytes(synth.generate(int(synth.rate * after) or 1)), np.float32).reshape(-1, 2)
    return a, b


def peak_hz(x, rate):
    import numpy as np
    x = x[:16384]
    f = np.abs(np.fft.rfft(x * np.hanning(len(x)), 1 << 18))
    return np.argmax(f) * rate / (1 << 18)


def test_the_built_in_player_plays_a_soundfont_in_tune_with_its_envelope(tmp_path):
    import numpy as np
    from sf2 import Synth
    # a held release of 0.5 s (timecents 1200*log2(0.5) = -1200), sustain 6 dB down, decay 1 s, preset attenuation 6 dB
    path = tiny_sf2(tmp_path / "t.sf2", preset_gens=[(48, 60)],
                    inst_gens=[(36, 0), (37, 60), (38, -1200), (43, (40, 90))])
    s = Synth(samplerate=44100)
    s.sfload(path)
    s.program_select(0, 0, 0, 0)
    s.control_change(0, 7, 127)                                        # (the default is General MIDI's 100)
    held, rel = render(s, 81, 127, 2.0, 1.0)
    assert abs(peak_hz(held[:, 0], 44100) - 880.0) < 1.0              # an octave above the sample's root, looped
    level = lambda a: float(np.sqrt((a[:, 0] ** 2).mean()))
    full = 16000 / 32768 / np.sqrt(2) * np.cos(np.pi / 4)               # the sine's RMS, panned centre
    db = lambda a: 20 * np.log10(level(a) / full)
    assert abs(db(held[int(1.5 * 44100):]) - (-6 - 6)) < 0.5            # attenuation 6 dB + sustain 6 dB
    # the release: 96 dB in 0.5 s, so 19.2 dB down 0.1 s after the key is let go; over by 0.5 s
    assert abs(db(rel[4000:4820]) - (-12 - 19.2)) < 1.5
    assert level(rel[int(0.6 * 44100):]) < 1e-5 and not s.voices            # (past -96 dB: dropped)
    # outside the key range: nothing
    a, _ = render(s, 30, 100, 0.1)
    assert level(a) == 0.0
    # velocity and the channel volume (CC 7, squared)
    a, _ = render(s, 69, 127, 1.6)
    s.control_change(0, 7, 64)
    b, _ = render(s, 69, 127, 1.6)
    assert abs(level(b[-4410:]) / level(a[-4410:]) - (64 / 127) ** 2) < 0.01
    s.control_change(0, 7, 127)
    c, _ = render(s, 69, 64, 1.6)
    assert abs(level(c[-4410:]) / level(a[-4410:]) - 64 / 127) < 0.01


def test_an_unlooped_sample_ends_and_a_bad_file_is_refused(tmp_path):
    import pytest
    from sf2 import SoundFontError, Synth
    s = Synth(samplerate=22050)
    s.sfload(tiny_sf2(tmp_path / "t.sf2", loop=False))
    s.program_select(0, 0, 0, 0)
    render(s, 69, 100, 0.2)
    assert not s.voices                                                 # (50 periods at 440 Hz: 0.11 s)
    bad = tmp_path / "bad.sf2"
    bad.write_bytes(b"RIFF\0\0\0\0WAVEfmt ")
    with pytest.raises(SoundFontError):
        Synth().sfload(str(bad))


def test_the_soundfont_player_plays_through_the_built_in_synth(tmp_path):
    """make_synth with a real file: the built-in player, no package needed."""
    from sf2 import Synth
    from sf_synth import SoundfontOut, make_synth
    path = tiny_sf2(tmp_path / "Tone.sf2")
    out, problem = make_synth(True, path)
    try:
        assert isinstance(out, SoundfontOut) and problem == "" and isinstance(out.synth, Synth)
        out.note_on(Note(69, 0, 1, 100, 0, RIGHT))
        assert len(out.synth.voices) == 1
    finally:
        out.close()
        pygame.mixer.quit()


def test_many_voices_play_together_as_one_did_alone(tmp_path):
    """The voices are rendered together (an array at a time): the mix is the sum of each alone."""
    import numpy as np
    from sf2 import Sample, Synth
    path = tiny_sf2(tmp_path / "t.sf2", inst_gens=[(38, -1200)])
    alone = []
    for key in (57, 69, 81):
        s = Synth(samplerate=22050)
        s.sfload(path)
        a, b = render(s, key, 90, 0.3, 0.6)
        alone.append(np.concatenate([a, b]))
    s = Synth(samplerate=22050)
    s.sfload(path)
    for key in (57, 69, 81):
        s.noteon(0, key, 90)
    a = np.frombuffer(bytes(s.generate(int(22050 * 0.3))), np.float32).reshape(-1, 2)
    for key in (57, 69, 81):
        s.noteoff(0, key)
    b = np.frombuffer(bytes(s.generate(int(22050 * 0.6))), np.float32).reshape(-1, 2)
    assert np.abs(np.concatenate([a, b]) - sum(alone)).max() < 1e-5
    # decoded (.sf3) samples are packed into one array, each keeping its own place
    one, two = (Sample(np.arange(10, dtype=np.int16) + k * 100, 0, 10, 2, 8, 22050, 60, 0) for k in (0, 1))
    Synth._pack([one, two])
    assert one.data is two.data and two.start == 10 and two.loop_start == 12 and two.data[two.start] == 100
