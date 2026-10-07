"""
sf_synth.py - Playing the notes with a SoundFont (.sf2 / .sf3) the user chose.

The default sound is the system's MIDI synth (common.MidiOut). With a
soundfont chosen in the Settings, `SoundfontOut` plays instead: the same
interface as MidiOut (note_on / note_off by the note's hand, pedals, volume,
silence), played by the app's own soundfont player (sf2.Synth, numpy: no
compiled package to install, so it works on any Python).

The player runs in a process of its own (`_player_process`, started with
multiprocessing's "spawn"), with its own audio output: the notes are sent to
it down a pipe (`RemoteSynth`). In the app's process, the synth's thread and
the drawing took turns with Python's interpreter lock - each slowed the
other, so frames came late and the sound ran dry. There, `MixerStream` keeps
a short chunk queued on a mixer channel: a thread generating while the
process's main thread takes the notes off the pipe. If the process can't be
started, the player runs in the app's process instead.

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
STARTUP_TIMEOUT_S = 60.0     # s, the player's process says something (progress, ready) at least this often
CHUNK_FRAMES = 768           # the mixer is fed this much at a time (17 ms at 44.1 kHz), one chunk queued ahead


class MixerStream:
    """
    A synth (anything with generate(frames) -> interleaved stereo float32)
    played through pygame's mixer, on a channel kept for it: a thread keeps
    one CHUNK_FRAMES chunk queued behind the one playing. If the mixer is
    reopened at another rate (a synced recording's), the chunks are
    resampled to it.
    """

    def __init__(self, synth, lock, rate, stats=None):
        self.synth, self.lock, self.rate = synth, lock, rate
        self.stats = stats if stats is not None else {"chunks": 0, "underruns": 0}
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
                            self.stats["chunks"] += 1
                            if self.channel.get_busy():
                                self.channel.queue(snd)
                            else:
                                if self.stats["chunks"] > 2:
                                    self.stats["underruns"] += 1        # (ran dry: a gap in the sound)
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


class RemoteSynth:
    """sf2.Synth's calls, sent to the player's process (`_player_process`)."""

    def __init__(self, conn):
        self.conn = conn

    def _send(self, *msg):
        try:
            self.conn.send(msg)
        except (OSError, EOFError, ValueError):        # (the process gone: nothing to play)
            pass

    def noteon(self, ch, key, vel):
        self._send("noteon", ch, key, vel)

    def noteoff(self, ch, key):
        self._send("noteoff", ch, key)

    def control_change(self, ch, control, value):
        self._send("control_change", ch, control, value)

    def notes_off(self, ch=None):
        self._send("notes_off", ch)

    def sounds_off(self, ch=None):
        self._send("sounds_off", ch)


PLAYER_CALLS = {"noteon", "noteoff", "control_change", "notes_off", "sounds_off"}


def _player_process(conn, path):
    """
    The soundfont player's process: load `path`, open the sound, say "ready"
    (or "error"), then play what comes down the pipe until "quit" - or until
    the app is gone (the pipe breaks). While loading, "progress" (0..1).
    """
    import pygame
    import progress
    from sf2 import Synth
    stats = {"chunks": 0, "underruns": 0}
    try:
        progress.begin()
        sending = [True]

        def tell_progress():
            while sending[0]:
                conn.send(("progress", progress.value()))
                time.sleep(0.05)

        teller = threading.Thread(target=tell_progress, daemon=True)
        teller.start()
        try:
            pygame.mixer.init()
            rate = pygame.mixer.get_init()[0]
            synth = Synth(samplerate=rate)
            synth.sfload(path)
            for ch in (0, 1):
                synth.program_select(ch, 0, 0, 0)               # bank 0, preset 0: the piano in a piano soundfont
        finally:
            sending[0] = False
            teller.join()
            progress.end()
    except Exception as exc:
        conn.send(("error", str(exc) or type(exc).__name__))
        return
    conn.send(("ready", rate))
    lock = threading.RLock()
    stream = MixerStream(synth, lock, rate, stats)
    try:
        while True:
            if not conn.poll(0.25):
                continue
            msgs = [conn.recv()]
            while conn.poll():
                msgs.append(conn.recv())
            with lock:
                for op, *args in msgs:
                    if op in PLAYER_CALLS:
                        getattr(synth, op)(*args)
            if any(m[0] == "stats" for m in msgs):
                conn.send(("stats", dict(stats)))
            if any(m[0] == "quit" for m in msgs):
                break
    except (EOFError, OSError):                            # (the app has gone)
        pass
    finally:
        stream.stop()


class SoundfontError(Exception):
    """The soundfont won't load (said by the player's process)."""


def soundfont_name(path):
    """A soundfont's name to show: its file name without the extension."""
    return os.path.splitext(os.path.basename(path))[0] if path else ""


class SoundfontOut:
    def __init__(self, path, synth=None, start=True, process=True):
        """
        Load `path` and start playing: in a process of its own (`process`),
        else in this one; `synth` is a stand-in for sf2.Synth, played here
        (tests).
        """
        self.path = path
        self.name = soundfont_name(path)
        self.muted = False
        self.volume = 0.8
        self._lock = threading.RLock()       # (the mixer thread generates while notes come in)
        self.stream = None
        self.proc = None
        if synth is None and start and process:
            try:
                synth = self._start_process(path)
            except SoundfontError:
                raise
            except Exception as exc:                          # (no process: played here instead)
                print(f"Soundfont player process didn't start ({exc}); playing in the app")
                synth = None
        if synth is None:
            rate = 44100
            if start:
                from audio_sync import ensure_mixer
                import pygame
                ensure_mixer()
                rate = pygame.mixer.get_init()[0]
            from sf2 import Synth
            synth = Synth(samplerate=rate)
            synth.sfload(path)
            for ch in (0, 1):
                synth.program_select(ch, 0, 0, 0)               # bank 0, preset 0: the piano in a piano soundfont
            if start:
                self.stream = MixerStream(synth, self._lock, rate)
        elif self.proc is None:
            sfid = synth.sfload(path)
            for ch in (0, 1):
                synth.program_select(ch, sfid, 0, 0)
        self._down = set()           # (channel, pitch): keys down now
        self._ringing = set()        # keys let go while a pedal holds them
        self._sustain = {0: False, 1: False}
        self._sost = {0: set(), 1: set()}      # the keys the sostenuto pedal caught
        self._soft = {0: False, 1: False}
        self.synth = synth
        self.set_volume(self.volume)

    def _start_process(self, path):
        """The player's process, loaded and ready: its RemoteSynth (progress passed on meanwhile)."""
        import multiprocessing
        import progress
        ctx = multiprocessing.get_context("spawn")
        conn, child = ctx.Pipe()
        proc = ctx.Process(target=_player_process, args=(child, path), name="soundfont player", daemon=True)
        proc.start()
        child.close()
        try:
            while True:
                if not conn.poll(STARTUP_TIMEOUT_S):
                    raise RuntimeError("it didn't answer")
                op, *args = conn.recv()
                if op == "progress":
                    progress.report(args[0])
                elif op == "ready":
                    break
                elif op == "error":
                    raise SoundfontError(args[0])
        except BaseException:
            proc.terminate()
            proc.join(1.0)
            conn.close()
            raise
        self.proc, self.conn = proc, conn
        return RemoteSynth(conn)

    def stats(self):
        """The player's chunks played and underruns so far (its process asked), or None."""
        if self.proc is not None:
            with self._lock:
                self.synth._send("stats")
                while self.conn.poll(1.0):
                    op, *args = self.conn.recv()
                    if op == "stats":
                        return args[0]
            return None
        return dict(self.stream.stats) if self.stream is not None else None

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
            if self.proc is not None:
                self.synth._send("quit")
                self.proc.join(1.0)
                if self.proc.is_alive():
                    self.proc.terminate()
                self.conn.close()
                self.proc = None
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
    except Exception as exc:
        print(f"Couldn't play the soundfont {path} ({exc})")
        return MidiOut(sound), f"couldn't be played ({exc})"
