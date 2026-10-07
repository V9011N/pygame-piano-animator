"""
sf_synth.py - Playing the notes with a SoundFont (.sf2 / .sf3) the user chose.

The default sound is the system's MIDI synth (common.MidiOut). With a
soundfont chosen in the Settings, `SoundfontOut` plays instead: the same
interface as MidiOut (note_on / note_off by the note's hand, pedals, volume,
silence), backed by TinySoundFont (the `tinysoundfont` package). Its sound
goes out through pygame's mixer (`MixerStream`): a thread keeps a short chunk
of it queued on a mixer channel of its own. (Not tinysoundfont's own player,
which needs PyAudio - and PyAudio has no ready-built package for every
Python, Python 3.14 on Windows among them. Install tinysoundfont with
`pip install --no-deps tinysoundfont`, so pip doesn't try to build PyAudio.)

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
import threading
import time

from midi_loader import LEFT

SUSTAIN, SOSTENUTO, SOFT, VOLUME = 64, 66, 67, 7
SOFT_VELOCITY = 0.7          # the soft pedal plays new notes this much softer
SF_TYPES = [("SoundFont files", "*.sf2 *.sf3"), ("All files", "*.*")]
CHUNK_FRAMES = 512           # the mixer is fed this much at a time (12 ms at 44.1 kHz), one chunk queued ahead
INSTALL_HINT = "pip install --no-deps tinysoundfont"


class MixerStream:
    """
    A synth (anything with generate(frames) -> interleaved stereo float32)
    played through pygame's mixer, on a channel kept for it: a thread keeps
    one CHUNK_FRAMES chunk queued behind the one playing. If the mixer is
    reopened at another rate (a synced recording's), the chunks are
    resampled to it.
    """

    def __init__(self, synth, lock, rate):
        self.synth, self.lock, self.rate = synth, lock, rate
        self.running = True
        self.channel = None
        self.thread = threading.Thread(target=self._run, name="soundfont", daemon=True)
        self.thread.start()

    def chunk(self, init):
        """The next chunk as a pygame Sound in the mixer's format `init` (frequency, size, channels)."""
        import numpy as np
        import pygame
        freq, size, nch = init
        n_out = CHUNK_FRAMES
        n_in = max(1, int(round(n_out * self.rate / freq)))
        with self.lock:
            buf = self.synth.generate(n_in)
        a = np.frombuffer(bytes(buf), dtype=np.float32).reshape(-1, 2)
        if len(a) != n_out:                                     # (another rate: resampled)
            x = np.linspace(0.0, len(a) - 1, n_out)
            a = np.stack([np.interp(x, np.arange(len(a)), a[:, c]) for c in (0, 1)], axis=1)
        if nch == 1:
            a = a.mean(axis=1, keepdims=True)
        elif nch > 2:
            a = np.concatenate([a, np.zeros((len(a), nch - 2), dtype=a.dtype)], axis=1)
        a = np.clip(a, -1.0, 1.0)
        if size == 32:
            out = a.astype(np.float32)
        elif size == 16:
            out = ((a + 1.0) * 32767.5).astype(np.uint16)
        elif size == 8:
            out = ((a + 1.0) * 127.5).astype(np.uint8)
        elif size == -8:
            out = (a * 127).astype(np.int8)
        else:
            out = (a * 32767).astype(np.int16)
        return pygame.mixer.Sound(buffer=np.ascontiguousarray(out).tobytes())

    def _run(self):
        import pygame
        import audio_sync
        from audio_sync import MIXER_LOCK
        init = None
        while self.running:
            wait = 0.0
            try:
                with MIXER_LOCK:                                # (the mixer can't be reopened mid-step)
                    cur = pygame.mixer.get_init()
                    cur = cur and (*cur, audio_sync.mixer_opened)    # (reopened as it was: still new)
                    if cur is None:
                        self.channel, init = None, None
                        wait = 0.05
                    else:
                        if cur != init or self.channel is None:
                            init = cur
                            pygame.mixer.set_reserved(1)        # channel 0 is the synth's
                            self.channel = pygame.mixer.Channel(0)
                        if self.channel.get_queue() is not None:
                            wait = 0.002
                        else:
                            snd = self.chunk(init[:3])
                            if self.channel.get_busy():
                                self.channel.queue(snd)
                            else:
                                self.channel.play(snd)
            except Exception as exc:                        # (the mixer closing under us, say)
                print(f"Soundfont output paused ({exc})")
                self.channel, init = None, None
                wait = 0.2
            if wait:
                time.sleep(wait)

    def stop(self):
        import pygame
        from audio_sync import MIXER_LOCK
        self.running = False
        self.thread.join(timeout=1.0)
        with MIXER_LOCK:
            try:
                if self.channel is not None and pygame.mixer.get_init():
                    self.channel.stop()
            except Exception:
                pass


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
        self._lock = threading.RLock()       # (the mixer thread generates while notes come in)
        self.stream = None
        rate = 44100
        if start:
            from audio_sync import ensure_mixer
            import pygame
            ensure_mixer()
            rate = pygame.mixer.get_init()[0]
        if synth is None:
            import tinysoundfont
            synth = tinysoundfont.Synth(samplerate=rate)
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
        self.set_volume(self.volume)
        if start:
            self.stream = MixerStream(synth, self._lock, rate)

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
        with self._lock:
            if key in self._ringing:                     # struck again while it rings on
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
        with self._lock:
            self._down.discard(key)
            if self._sustain[ch] or note.pitch in self._sost[ch]:
                self._ringing.add(key)                   # the pedal holds it
            else:
                self.synth.noteoff(ch, note.pitch)

    def control_change(self, number, value):
        """The pedals (sustain 64, sostenuto 66, soft 67) and the volume (7), on both hands' channels."""
        if self.synth is None:
            return
        down = value >= 64
        with self._lock:
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
            with self._lock:
                for ch in (0, 1):
                    self.synth.control_change(ch, VOLUME, int(round(127 * self.volume)))

    def pedals_up(self):
        for c in (SUSTAIN, SOSTENUTO, SOFT):
            self.control_change(c, 0)

    def all_off(self):
        with self._lock:
            if self.synth is not None:
                self.synth.notes_off()
            self._down.clear()
            self._ringing.clear()

    def silence(self):
        if self.synth is not None:
            self.pedals_up()
            self.all_off()
            with self._lock:
                self.synth.sounds_off()

    def close(self):
        if self.synth is not None:
            self.silence()
            if self.stream is not None:
                self.stream.stop()
                self.stream = None
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
        return MidiOut(sound), f"needs the tinysoundfont package ({INSTALL_HINT})"
    except Exception as exc:
        print(f"Couldn't play the soundfont {path} ({exc})")
        return MidiOut(sound), f"couldn't be played ({exc})"
