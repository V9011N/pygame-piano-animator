"""
video_export.py - A performance rendered to an .mp4: the player's picture at
the window's size, VIDEO_FPS frames a second, with its sound.

VideoExport runs in steps (step(budget)) from the player's frames, so the
window stays alive (progress, Cancel): each step draws as many frames as fit
in the time it's given (Visualizer.render_video_frame, onto a surface of its
own) and hands them to a writer thread, which pipes them to ffmpeg (the
imageio-ffmpeg package's own binary). The sound is made meanwhile in a
thread of its own, then the two are put together:

  - a synced recording: cut from its decoded samples where the song is
    heard (audio_sync: song time t at offset + t / speed);
  - otherwise the notes as the hands play them, rendered offline through the
    built-in SoundFont player (sf2) with the player's pedal handling
    (sf_synth.SoundfontOut, driven by a Transport of its own): the chosen
    soundfont, else pretty_midi's General MIDI one (TimGM6mb.sf2), else none
    (a silent video).

The video runs from the song's start (its lead-in) to the end of playback,
plus VIDEO_TAIL_S for the last notes to ring out, at the playback speed.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import pygame

VIDEO_FPS = 60
VIDEO_TAIL_S = 1.5          # s of video after playback ends (the last notes and the pedal ring out)
AUDIO_RATE = 44100
AUDIO_BLOCK = 256           # frames the offline synth makes at a time (the notes' timing step, 5.8 ms)
QUEUE_FRAMES = 4            # frames waiting for ffmpeg at most (a 5K frame is 33 MB)
NORMALIZE_PEAK = 0.89      # the synth's sound brought up (never down) to peak here (-1 dBFS)...
NORMALIZE_GAIN_MAX = 16.0   # ...by at most this much
CRF = 18                    # x264 quality (lower: better, bigger)
# overall progress: the frames, the sound, and putting them together
WEIGHTS = (0.86, 0.10, 0.04)


class ExportError(Exception):
    """The export can't go ahead (no ffmpeg, a file that can't be written...)."""


def ffmpeg_exe():
    """The ffmpeg binary that comes with imageio-ffmpeg; ExportError if it isn't installed."""
    try:
        import imageio_ffmpeg
    except ImportError:
        raise ExportError("Video export needs the imageio-ffmpeg package:\npip install imageio-ffmpeg")
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise ExportError(f"ffmpeg wasn't found ({exc})")


def _popen(cmd, **kw):
    """ffmpeg, without a console window flashing up on Windows (the compiled app has none)."""
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cmd, **kw)


def default_soundfont():
    """The General MIDI soundfont that comes with pretty_midi, if it's there."""
    try:
        import pretty_midi
        path = os.path.join(os.path.dirname(pretty_midi.__file__), "TimGM6mb.sf2")
        return path if os.path.isfile(path) else None
    except Exception:
        return None


def frame_bytes(surf):
    """A surface's pixels as RGB bytes (pygame-ce: tobytes; older pygame: tostring)."""
    f = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
    return f(surf, "RGB")


# --------------------------------------------------------------------------- #
# The sound
# --------------------------------------------------------------------------- #
def render_notes_audio(song, perf, speed, t0, seconds, soundfont, volume=0.8, cancel=None, report=None):
    """
    (seconds x AUDIO_RATE) x 2 float32: the hands' notes and the pedals from
    song time t0 on, at `speed`, played through `soundfont` offline.
    """
    from common import Transport
    from sf_synth import SoundfontOut

    out_dev = SoundfontOut(soundfont, start=False, process=False)
    out_dev.set_volume(volume)

    class Offline(Transport):
        pass

    tr = Offline()
    tr._init_transport(out_dev, speed)
    tr.song, tr.perf = song, perf
    tr.seek(t0)
    tr.paused = False
    tr._send_pedals()
    total = int(round(seconds * AUDIO_RATE))
    out = np.zeros((total, 2), np.float32)
    pos = 0
    synth = out_dev.synth
    while pos < total:
        if cancel is not None and cancel.is_set():
            return None
        n = min(AUDIO_BLOCK, total - pos)
        tr.update(n / AUDIO_RATE)
        out[pos:pos + n] = np.frombuffer(synth.generate(n), np.float32).reshape(-1, 2)
        pos += n
        if report is not None and pos % (AUDIO_BLOCK * 64) < AUDIO_BLOCK:
            report(pos / total)
    return out


def recording_audio(audio, t0, speed, seconds):
    """
    (samples int16 frames x channels, rate): the synced recording as heard
    from song time t0 on (offset + t / speed), silence where it hasn't begun
    or has ended.
    """
    rate, ch, sb = audio.freq, audio.channels, audio.sample_bytes
    dtype = {1: np.uint8, 2: np.int16, 4: np.float32}[sb]
    a = np.frombuffer(audio.raw, dtype=dtype).reshape(-1, ch)
    if sb == 1:
        a = ((a.astype(np.int16) - 128) << 8)
    elif sb == 4:
        a = (np.clip(a, -1.0, 1.0) * 32767).astype(np.int16)
    total = int(round(seconds * rate))
    out = np.zeros((total, ch), np.int16)
    start = int(round((audio.offset + t0 / speed) * rate))
    lo, hi = max(0, start), min(len(a), start + total)
    if hi > lo:
        out[lo - start:hi - start] = a[lo:hi]
    return out, rate


def write_wav(path, samples, rate):
    """int16 (frames x channels) or float (-1..1) samples to a .wav file."""
    if samples.dtype != np.int16:
        samples = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(samples).tobytes())


# --------------------------------------------------------------------------- #
# The export
# --------------------------------------------------------------------------- #
class VideoExport:
    """One export of the player's song to `path` (see the module's notes)."""

    def __init__(self, vis, path, size):
        self.vis, self.path = vis, path
        self.size = (size[0] - size[0] % 2, size[1] - size[1] % 2)      # (x264's 4:2:0 wants even sides)
        self.fps = VIDEO_FPS
        self.t0 = vis.start_time()
        self.speed = vis.speed
        self.seconds = (vis.end_time() - self.t0) / self.speed + VIDEO_TAIL_S
        self.frames = int(self.seconds * self.fps)
        self.done_frames = 0
        self.started = time.perf_counter()
        self.error = None
        self.finished = False
        self.cancelled = False
        self.preview = None                  # the last frame drawn (shown dimmed behind the progress)
        self.audio_frac = 0.0
        self.mux_frac = 0.0
        self._cancel = threading.Event()
        self._queue = queue.Queue(QUEUE_FRAMES)
        base = os.path.splitext(path)[0]
        self._video_tmp = base + ".video-tmp.mp4"
        self._wav_tmp = base + ".audio-tmp.wav"
        self._exe = ffmpeg_exe()
        self._proc = self._start_video()
        self._writer = threading.Thread(target=self._write, name="video writer", daemon=True)
        self._writer.start()
        self.has_audio = None                # decided by the sound's thread
        self._audio_thread = threading.Thread(target=self._make_audio, name="video sound", daemon=True)
        self._audio_thread.start()
        self._mux_thread = None

    # ----- ffmpeg ------------------------------------------------------------------
    def _start_video(self):
        w, h = self.size
        cmd = [self._exe, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(self.fps), "-i", "-",
               "-an", "-c:v", "libx264", "-preset", "fast", "-crf", str(CRF), "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", self._video_tmp]
        try:
            return _popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            raise ExportError(f"ffmpeg didn't start ({exc})")

    def _write(self):
        """The writer thread: frames from the queue into ffmpeg (None: the end)."""
        try:
            while True:
                data = self._queue.get()
                if data is None:
                    break
                self._proc.stdin.write(data)
            self._proc.stdin.close()
            err = self._proc.stderr.read().decode("utf-8", "replace").strip()
            if self._proc.wait() != 0 and not self._cancel.is_set():
                self.error = f"ffmpeg couldn't write the video:\n{err[-400:]}"
        except (OSError, ValueError) as exc:
            if not self._cancel.is_set():
                self.error = f"Writing the video failed ({exc})"

    def _make_audio(self):
        """The sound's thread: the recording's slice, or the notes rendered offline, to a .wav."""
        vis = self.vis
        try:
            if vis.audio is not None:
                samples, rate = recording_audio(vis.audio, self.t0, self.speed, self.seconds)
            else:
                sf = getattr(vis.midi, "path", None)
                sf = sf if sf and os.path.isfile(sf) else default_soundfont()
                if not sf:
                    self.has_audio = False
                    self.audio_frac = 1.0
                    return
                samples = render_notes_audio(vis.song, vis._performance(), self.speed, self.t0, self.seconds, sf,
                                             vis.midi.volume, self._cancel,
                                             lambda f: setattr(self, "audio_frac", f))
                rate = AUDIO_RATE
                if samples is None:
                    return
                # as loud as it can be without clipping (a soundfont plays well below full scale)
                peak = float(np.abs(samples).max()) if len(samples) else 0.0
                if peak > 1e-6:
                    samples *= min(NORMALIZE_GAIN_MAX, NORMALIZE_PEAK / peak)
            write_wav(self._wav_tmp, samples, rate)
            self.has_audio = True
        except Exception as exc:                 # (a sound that won't render: the video goes on without it)
            print(f"Video export: no sound ({exc})")
            self.has_audio = False
        self.audio_frac = 1.0

    def _mux(self):
        """Video and sound together into the chosen file (the video as it is, the sound to AAC)."""
        try:
            if self.has_audio:
                cmd = [self._exe, "-y", "-loglevel", "error", "-i", self._video_tmp, "-i", self._wav_tmp,
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                       "-shortest", "-movflags", "+faststart", self.path]
                p = _popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                err = p.communicate()[1].decode("utf-8", "replace").strip()
                if p.returncode != 0:
                    if not self._cancel.is_set():
                        self.error = f"ffmpeg couldn't add the sound:\n{err[-400:]}"
                    return
            else:
                os.replace(self._video_tmp, self.path)
            self.mux_frac = 1.0
            self.finished = True
        except OSError as exc:
            self.error = f"Couldn't save {os.path.basename(self.path)} ({exc})"
        finally:
            self._cleanup()

    def _cleanup(self):
        for p in (self._video_tmp, self._wav_tmp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    # ----- driven by the player -------------------------------------------------------
    def step(self, budget):
        """Draw and queue frames for about `budget` seconds; then, once all are written, put it together."""
        if self.error or self.finished or self.cancelled:
            return
        end = time.perf_counter() + budget
        while self.done_frames < self.frames and time.perf_counter() < end:
            if not self._writer.is_alive():
                self.error = self.error or "The video writer stopped"
                self.cancel()
                return
            t = self.t0 + self.done_frames / self.fps * self.speed
            surf = self.vis.render_video_frame(t)
            data = frame_bytes(surf)
            self.preview = surf
            while True:                                          # (ffmpeg behind: wait for a place)
                try:
                    self._queue.put(data, timeout=0.1)
                    break
                except queue.Full:
                    if self._cancel.is_set():
                        return
                    if not self._writer.is_alive():
                        self.error = self.error or "The video writer stopped"
                        self.cancel()
                        return
            self.done_frames += 1
        if self.done_frames >= self.frames and self._mux_thread is None:
            self._queue.put(None)
            self._mux_thread = threading.Thread(target=self._finish, name="video mux", daemon=True)
            self._mux_thread.start()

    def _finish(self):
        self._writer.join()
        self._audio_thread.join()
        if self.error or self._cancel.is_set():
            self._cleanup()
            return
        self._mux()

    def cancel(self):
        """Stop now: ffmpeg killed, the sound abandoned, nothing left behind."""
        self.cancelled = True
        self._cancel.set()
        try:
            self._proc.kill()
        except OSError:
            pass
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        threading.Thread(target=self._after_cancel, daemon=True).start()

    def _after_cancel(self):
        self._writer.join(5.0)
        self._audio_thread.join(30.0)
        self._cleanup()

    @property
    def progress(self):
        v = self.done_frames / max(1, self.frames)
        return WEIGHTS[0] * v + WEIGHTS[1] * self.audio_frac + WEIGHTS[2] * self.mux_frac

    def status(self):
        """What it's doing now, for the progress screen."""
        if self.done_frames < self.frames:
            return f"Rendering frames  {self.done_frames} / {self.frames}"
        if self.audio_frac < 1.0:
            return f"Rendering the sound  {int(self.audio_frac * 100)}%"
        return "Putting the video together…"

    def eta(self):
        """Seconds left (a guess from the pace so far), or None early on."""
        p, el = self.progress, time.perf_counter() - self.started
        if p < 0.01 or el < 1.0:
            return None
        return el * (1.0 - p) / p
