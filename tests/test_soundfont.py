"""Choosing a soundfont: the player (with a stand-in synth), the pedals, the Settings and the sound choice."""
import pygame

from midi_loader import LEFT, RIGHT, Note


class FakeSynth:
    """Records what tinysoundfont.Synth would be asked to do."""

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
