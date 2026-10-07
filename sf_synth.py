"""
sf_synth.py - Playing the notes with a SoundFont (.sf2 / .sf3) the user chose.

The default sound is the system's MIDI synth (common.MidiOut). With a
soundfont chosen in the Settings, `SoundfontOut` plays instead: the same
interface as MidiOut (note_on / note_off by the note's hand, pedals, volume,
silence), backed by TinySoundFont (the `tinysoundfont` package, which plays
through PyAudio in a thread of its own).

The pedals are played here rather than left to the synth, so every soundfont
gets them alike: the sustain pedal keeps a let-go key sounding until it
lifts, the sostenuto pedal keeps the keys that were down when it went down,
and the soft pedal plays new notes softer (SOFT_VELOCITY). The right hand is
channel 0 and the left hand channel 1, as with the system synth.

`make_synth(sound, path)` gives the soundfont's player, or the system synth
when there is no soundfont, it can't be found, or it won't load or play
(and why, for the Settings to show).
"""
import os

from midi_loader import LEFT

SUSTAIN, SOSTENUTO, SOFT, VOLUME = 64, 66, 67, 7
SOFT_VELOCITY = 0.7          # the soft pedal plays new notes this much softer
SF_TYPES = [("SoundFont files", "*.sf2 *.sf3"), ("All files", "*.*")]


def soundfont_name(path):
    """A soundfont's name to show: its file name without the extension."""
    return os.path.splitext(os.path.basename(path))[0] if path else ""


class SoundfontOut:
    def __init__(self, path, synth=None, start=True):
        """Load `path` and start playing; `synth` is a stand-in for tinysoundfont.Synth (tests)."""
        self.path = path
        self.name = soundfont_name(path)
        self.muted = False
        self.volume = 0.8
        if synth is None:
            import tinysoundfont
            synth = tinysoundfont.Synth(samplerate=44100)
        self.synth = synth
        sfid = synth.sfload(path)
        for ch in (0, 1):
            try:
                synth.program_select(ch, sfid, 0, 0)          # bank 0, preset 0: the piano in a piano soundfont
            except Exception:
                synth.program_change(ch, 0)
        self._down = set()           # (channel, pitch): keys down now
        self._ringing = set()        # keys let go while a pedal holds them
        self._sustain = {0: False, 1: False}
        self._sost = {0: set(), 1: set()}      # the keys the sostenuto pedal caught
        self._soft = {0: False, 1: False}
        if start:
            synth.start()
        self.set_volume(self.volume)

    # ----- what MidiOut offers ---------------------------------------------------
    @property
    def available(self):
        return self.synth is not None

    def status(self):
        if self.muted:
            return "muted"
        return self.name if len(self.name) <= 22 else self.name[:21] + "…"

    def note_on(self, note):
        if self.synth is None or self.muted:
            return
        ch = 1 if note.hand == LEFT else 0
        key = (ch, note.pitch)
        if key in self._ringing:                         # struck again while it rings on
            self.synth.noteoff(ch, note.pitch)
            self._ringing.discard(key)
        v = note.velocity * (SOFT_VELOCITY if self._soft[ch] else 1.0)
        self.synth.noteon(ch, note.pitch, max(1, min(127, int(round(v)))))
        self._down.add(key)

    def note_off(self, note):
        if self.synth is None:
            return
        ch = 1 if note.hand == LEFT else 0
        key = (ch, note.pitch)
        self._down.discard(key)
        if self._sustain[ch] or note.pitch in self._sost[ch]:
            self._ringing.add(key)                       # the pedal holds it
        else:
            self.synth.noteoff(ch, note.pitch)

    def control_change(self, number, value):
        """The pedals (sustain 64, sostenuto 66, soft 67) and the volume (7), on both hands' channels."""
        if self.synth is None:
            return
        down = value >= 64
        for ch in (0, 1):
            if number == SUSTAIN:
                self._sustain[ch] = down
                if not down:
                    self._release(ch)
            elif number == SOSTENUTO:
                if down and not self._sost[ch]:
                    self._sost[ch] = {p for c, p in self._down if c == ch}
                elif not down:
                    self._sost[ch] = set()
                    self._release(ch)
            elif number == SOFT:
                self._soft[ch] = down
            elif number == VOLUME:
                self.synth.control_change(ch, VOLUME, value)

    def _release(self, ch):
        """Stop the keys on channel ch that only a pedal still held."""
        for key in [k for k in self._ringing if k[0] == ch]:
            if self._sustain[ch] or key[1] in self._sost[ch] or key in self._down:
                continue
            self.synth.noteoff(*key)
            self._ringing.discard(key)

    def set_volume(self, v):
        self.volume = min(1.0, max(0.0, float(v)))
        if self.synth is not None:
            for ch in (0, 1):
                self.synth.control_change(ch, VOLUME, int(round(127 * self.volume)))

    def pedals_up(self):
        for c in (SUSTAIN, SOSTENUTO, SOFT):
            self.control_change(c, 0)

    def all_off(self):
        if self.synth is not None:
            self.synth.notes_off()
        self._down.clear()
        self._ringing.clear()

    def silence(self):
        if self.synth is not None:
            self.pedals_up()
            self.all_off()
            self.synth.sounds_off()

    def close(self):
        if self.synth is not None:
            self.silence()
            try:
                self.synth.stop()
            except Exception:
                pass
            self.synth = None


def make_synth(sound, path):
    """
    (player, problem): the soundfont at `path` playing (SoundfontOut), or the
    system's MIDI synth (common.MidiOut) with why not ("" when no soundfont
    was chosen). `sound` False: silent, whatever the setting.
    """
    from common import MidiOut
    if not sound or not path:
        return MidiOut(sound), ""
    if not os.path.isfile(path):
        return MidiOut(sound), "not found"
    try:
        return SoundfontOut(path), ""
    except ImportError:
        return MidiOut(sound), "needs the tinysoundfont package (pip install tinysoundfont)"
    except Exception as exc:
        print(f"Couldn't play the soundfont {path} ({exc})")
        return MidiOut(sound), f"couldn't be played ({exc})"
