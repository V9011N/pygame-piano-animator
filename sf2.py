"""
sf2.py - A small SoundFont player in numpy: no compiled packages needed.

`Synth` reads a SoundFont 2 file (.sf2, or .sf3 with Ogg Vorbis samples)
and renders notes to stereo float32 (`generate(frames)`), with the calls
sf_synth.SoundfontOut makes: sfload, program_select, noteon / noteoff,
control_change (the channel volume, CC 7), notes_off, sounds_off.

What it plays of the format (SoundFont 2.04): presets made of instrument
zones, merged as the spec says (preset values added to the instrument's,
key and velocity ranges intersected, global zones as defaults); samples
with their root key, tuning, scale tuning and sample offsets; loops (none,
continuous, or until the key is let go); attenuation and pan; and the volume
envelope - delay, attack, hold, decay (key-scaled) to the sustain level, and
release - in the spec's units (attack linear in amplitude, decay and release
a steady fall in dB, ENV_RANGE_DB being 100%). Not played: the modulation
envelope and LFOs, the filter, modulators, reverb and chorus. Velocity
scales the level linearly; the channel volume by its square, as in General
MIDI.

The samples stay on disk (np.memmap) until a note needs them, so a large
soundfont loads in moments. A preset's zones are put together when it is
first chosen (program_select), and .sf3 samples are decoded then - only the
chosen preset's - through pygame's mixer (Ogg Vorbis), which must be running.
"""
import io
import struct

import numpy as np

import progress

ENV_RANGE_DB = 96.0          # dB, a decay or release time is the time to fall this far (as FluidSynth)
SILENT_DB = 96.0             # dB down, a voice is over
MIN_RELEASE_S = 0.008        # s, the shortest release (no click when a key is let go)
MAX_VOICES = 128             # past this, the quietest voices are dropped first
INT16_SCALE = 1.0 / 32768.0
ENV_STEP = 32                # frames, the envelope is worked out this often (0.7 ms) and drawn straight between
_STEP_FRAC = (np.arange(ENV_STEP) / ENV_STEP).astype(np.float32)
_RAMPS = {}


def _ramp(n):
    """0, 1, ... n-1 as floats (kept: every voice wants it every chunk)."""
    r = _RAMPS.get(n)
    if r is None:
        r = np.arange(n, dtype=np.float64)
        if n <= 8192:
            _RAMPS[n] = r
    return r

# generators (SoundFont 2.04, section 8.1.2)
START_OFS, END_OFS, LOOP_START_OFS, LOOP_END_OFS = 0, 1, 2, 3
START_COARSE, END_COARSE, LOOP_START_COARSE, LOOP_END_COARSE = 4, 12, 45, 50
PAN = 17
DELAY, ATTACK, HOLD, DECAY, SUSTAIN, RELEASE = 33, 34, 35, 36, 37, 38
KEY_TO_HOLD, KEY_TO_DECAY = 39, 40
INSTRUMENT, KEY_RANGE, VEL_RANGE = 41, 43, 44
KEYNUM, VELOCITY, ATTENUATION = 46, 47, 48
COARSE_TUNE, FINE_TUNE, SAMPLE_ID, SAMPLE_MODES = 51, 52, 53, 54
SCALE_TUNING, OVERRIDING_ROOT = 56, 58
UNSIGNED = {INSTRUMENT, SAMPLE_ID, SAMPLE_MODES}
NOT_ADDED = {KEY_RANGE, VEL_RANGE, INSTRUMENT, SAMPLE_ID, SAMPLE_MODES, OVERRIDING_ROOT, KEYNUM, VELOCITY,
             START_OFS, END_OFS, LOOP_START_OFS, LOOP_END_OFS,
             START_COARSE, END_COARSE, LOOP_START_COARSE, LOOP_END_COARSE}
DEFAULTS = {DELAY: -12000, ATTACK: -12000, HOLD: -12000, DECAY: -12000, RELEASE: -12000, SUSTAIN: 0,
            KEY_TO_HOLD: 0, KEY_TO_DECAY: 0, ATTENUATION: 0, PAN: 0, COARSE_TUNE: 0, FINE_TUNE: 0,
            SCALE_TUNING: 100, OVERRIDING_ROOT: -1, SAMPLE_MODES: 0, KEYNUM: -1, VELOCITY: -1,
            START_OFS: 0, END_OFS: 0, LOOP_START_OFS: 0, LOOP_END_OFS: 0,
            START_COARSE: 0, END_COARSE: 0, LOOP_START_COARSE: 0, LOOP_END_COARSE: 0}
VORBIS = 0x10                # sampleType bit: an Ogg Vorbis sample (.sf3)


class SoundFontError(Exception):
    pass


def _seconds(timecents):
    return 0.0 if timecents <= -12000 else 2.0 ** (timecents / 1200.0)


def _chunks(data, start, end):
    """(id, start, end) of the RIFF sub-chunks in data[start:end]."""
    out = []
    i = start
    while i + 8 <= end:
        cid = data[i:i + 4]
        size = struct.unpack_from("<I", data, i + 4)[0]
        out.append((cid, i + 8, min(end, i + 8 + size)))
        i += 8 + size + (size & 1)
    return out


class Sample:
    __slots__ = ("data", "start", "end", "loop_start", "loop_end", "rate", "root", "correction")

    def __init__(self, data, start, end, loop_start, loop_end, rate, root, correction):
        self.data, self.start, self.end = data, start, end
        self.loop_start, self.loop_end = loop_start, loop_end
        self.rate, self.root, self.correction = rate, root, correction


class Region:
    """A key / velocity range of a preset, with everything needed to play it (spec units turned to ours)."""

    def __init__(self, g, sample):
        self.key_lo, self.key_hi = g[KEY_RANGE]
        self.vel_lo, self.vel_hi = g[VEL_RANGE]
        self.sample = sample
        ofs = lambda fine, coarse: g[fine] + 32768 * g[coarse]
        n = len(sample.data)
        self.start = min(n - 1, max(0, sample.start + ofs(START_OFS, START_COARSE)))
        self.end = min(n, max(self.start + 1, sample.end + ofs(END_OFS, END_COARSE)))
        self.loop_start = min(n - 1, max(0, sample.loop_start + ofs(LOOP_START_OFS, LOOP_START_COARSE)))
        self.loop_end = min(n, max(0, sample.loop_end + ofs(LOOP_END_OFS, LOOP_END_COARSE)))
        mode = g[SAMPLE_MODES] & 3
        self.loop = mode if mode in (1, 3) and self.loop_end - self.loop_start >= 2 else 0
        self.root = g[OVERRIDING_ROOT] if g[OVERRIDING_ROOT] >= 0 else sample.root
        self.tune = 100 * g[COARSE_TUNE] + g[FINE_TUNE] + sample.correction        # cents
        self.scale = g[SCALE_TUNING] / 100.0
        self.fixed_key, self.fixed_vel = g[KEYNUM], g[VELOCITY]
        self.gain = 10.0 ** (-max(0, g[ATTENUATION]) / 200.0)                     # (centibels)
        pan = min(500, max(-500, g[PAN])) / 1000.0 + 0.5
        self.left, self.right = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
        self.delay, self.attack = _seconds(g[DELAY]), _seconds(g[ATTACK])
        self.hold_tc, self.decay_tc = g[HOLD], g[DECAY]
        self.key_to_hold, self.key_to_decay = g[KEY_TO_HOLD], g[KEY_TO_DECAY]
        self.sustain_db = min(144.0, max(0.0, g[SUSTAIN] / 10.0))
        self.release = max(MIN_RELEASE_S, _seconds(g[RELEASE]))

    def matches(self, key, vel):
        return self.key_lo <= key <= self.key_hi and self.vel_lo <= vel <= self.vel_hi


class Voice:
    __slots__ = ("ch", "key", "r", "pos", "step", "gain", "t", "rate", "hold", "decay", "released", "rel_db",
                 "done", "fixed")

    def __init__(self, ch, key, vel, r, rate):
        self.ch, self.key, self.r, self.rate = ch, key, r, rate
        key = r.fixed_key if r.fixed_key >= 0 else key
        vel = r.fixed_vel if r.fixed_vel >= 0 else vel
        semis = (key - r.root) * r.scale + r.tune / 100.0
        self.step = 2.0 ** (semis / 12.0) * r.sample.rate / rate
        self.pos = float(r.start)
        self.gain = r.gain * vel / 127.0
        self.t = 0                                   # output frames played
        self.hold = _seconds(r.hold_tc + r.key_to_hold * (60 - key))
        self.decay = _seconds(r.decay_tc + r.key_to_decay * (60 - key))
        self.released = None                         # the output frame the key was let go at
        self.rel_db = 0.0                            # ...and the envelope then, in dB down
        self.done = False
        # what Synth._render needs that doesn't change: loop and end, envelope times and rates, gains
        self.fixed = (self.step, r.loop_start, r.loop_end, r.end - 1, r.loop, r.delay, r.attack,
                      r.delay + r.attack + self.hold, ENV_RANGE_DB / self.decay if self.decay > 0 else 1e12,
                      r.sustain_db, ENV_RANGE_DB / r.release, self.gain * r.left, self.gain * r.right)

    def _held_db(self, ts):
        """The envelope in dB down (attack aside) at times ts (s) while the key is held: hold, then decay."""
        r = self.r
        t0 = r.delay + r.attack + self.hold
        if self.decay <= 0:
            return np.where(ts < t0, 0.0, r.sustain_db)
        return np.minimum(r.sustain_db, np.maximum(0.0, ts - t0) * (ENV_RANGE_DB / self.decay))

    def release(self):
        if self.released is not None:
            return
        r = self.r
        t = self.t / self.rate
        if t < r.delay + r.attack:
            amp = 0.0 if t < r.delay else (t - r.delay) / r.attack
            self.rel_db = SILENT_DB if amp <= 0 else -20.0 * np.log10(amp)
        else:
            self.rel_db = float(self._held_db(np.array([t]))[0])
        self.released = self.t

    def level_db(self):
        """Roughly how far down it is now (for dropping voices)."""
        if self.released is None:
            return float(self._held_db(np.array([self.t / self.rate]))[0])
        return self.rel_db + (self.t - self.released) / self.rate * ENV_RANGE_DB / self.r.release


class Synth:
    """A soundfont played in numpy, with the calls sf_synth makes (as tinysoundfont.Synth's)."""

    def __init__(self, gain=0, samplerate=44100):
        self.rate = int(samplerate)
        self.master = 10.0 ** (gain / 20.0)
        self.presets = {}                # (bank, preset) -> [(generators, sample number)]
        self._regions = {}               # (bank, preset) -> [Region], put together when first chosen
        self._samples_ = []              # Sample, or how to decode it (.sf3), or None
        self.channels = {}               # channel -> {"regions", "volume"}
        self.voices = []
        self._map = None                 # the file's samples (memory-mapped)

    # ----- loading ---------------------------------------------------------------
    def sfload(self, path, gain=0, max_voices=MAX_VOICES):
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"sfbk":
                raise SoundFontError("not a SoundFont file")
            fh.seek(0, 2)
            size = fh.tell()
        # the chunk layout, reading little but the preset data
        with open(path, "rb") as fh:
            top = self._top_lists(fh, size)
            if b"sdta" not in top or b"pdta" not in top:
                raise SoundFontError("no samples or presets in it")
            fh.seek(top[b"pdta"][0])
            pdta = fh.read(top[b"pdta"][1] - top[b"pdta"][0])
            fh.seek(top[b"sdta"][0])
            sdta_head = fh.read(min(4096, top[b"sdta"][1] - top[b"sdta"][0]))
        smpl = None
        for cid, s, e in self._scan(sdta_head, top[b"sdta"]):
            if cid == b"smpl":
                smpl = (s, e)
        if smpl is None:
            raise SoundFontError("no sample data in it")
        parts = {cid: pdta[s:e] for cid, s, e in _chunks(pdta, 0, len(pdta))}
        for need in (b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr"):
            if need not in parts:
                raise SoundFontError(f"its preset data has no {need.decode()}")
        self._path = path
        self._samples_ = self._samples(path, parts[b"shdr"], smpl)
        self._presets(parts, self._samples_)
        if not self.presets:
            raise SoundFontError("no presets in it")
        return 0

    @staticmethod
    def _top_lists(fh, size):
        out = {}
        i = 12
        while i + 12 <= size:
            fh.seek(i)
            cid, n, kind = struct.unpack("<4sI4s", fh.read(12))
            if cid == b"LIST":
                out[kind] = (i + 12, min(size, i + 8 + n))
            i += 8 + n + (n & 1)
        return out

    @staticmethod
    def _scan(head, span):
        """The sdta list's chunks: (id, start, end) in the file."""
        out = []
        base, end = span
        i = 0
        while i + 8 <= len(head) and base + i < end:
            cid, n = struct.unpack_from("<4sI", head, i)
            out.append((cid, base + i + 8, min(end, base + i + 8 + n)))
            i += 8 + n + (n & 1)
        return out

    def _samples(self, path, shdr, smpl):
        s0, s1 = smpl
        # (a plain array over the mapped file: indexing a np.memmap goes through Python each time)
        data = self._map = np.memmap(path, dtype="<i2", mode="r", offset=s0,
                                     shape=((s1 - s0) // 2,)).view(np.ndarray)
        recs = [struct.unpack_from("<20sIIIIIBbHH", shdr, i) for i in range(0, len(shdr) - 46 + 1, 46)][:-1]
        out = []
        for name, start, end, ls, le, rate, root, corr, link, kind in recs:
            if kind & 0x8000:                                   # (ROM samples: none here)
                out.append(None)
                continue
            if kind & VORBIS:
                out.append((s0 + start, s0 + end, ls, le, rate, root, corr))     # (decoded when needed)
                continue
            out.append(Sample(data, start, end, ls, le, max(1, rate), root if root <= 127 else 60, corr))
        return out

    @staticmethod
    def _vorbis(path, b0, b1, ls, le, rate, root, corr):
        """An .sf3 sample: Ogg Vorbis, decoded through pygame's mixer (loop points count from its start)."""
        import pygame
        if not pygame.mixer.get_init():
            raise SoundFontError("its samples are compressed (.sf3) and the sound isn't running")
        with open(path, "rb") as fh:
            fh.seek(b0)
            ogg = fh.read(b1 - b0)
        snd = pygame.mixer.Sound(file=io.BytesIO(ogg))
        freq, size, nch = pygame.mixer.get_init()
        a = pygame.sndarray.array(snd)
        if a.ndim > 1:
            a = a[:, 0]                                  # (a mono sample, decoded to every channel)
        if size == 32:
            a = np.clip(a * 32767.0, -32768, 32767).astype(np.int16)
        elif size in (8, -8):
            a = ((a.astype(np.int16) - (128 if size == 8 else 0)) * 256).astype(np.int16)
        elif a.dtype != np.int16:
            a = a.astype(np.int16)
        # the decoded length against the stream's own (the last page's granule position): its true rate
        frames = 0
        j = ogg.rfind(b"OggS")
        if j >= 0 and j + 14 <= len(ogg):
            frames = struct.unpack_from("<q", ogg, j + 6)[0]
        # (decoded frames per original frame: the mixer's rate over the sample's, as the decoder really did it)
        k = len(a) / frames if frames > 0 and len(a) else freq / max(1, rate)
        pad = np.zeros(8, np.int16)
        return Sample(np.concatenate([a, pad]), 0, len(a), int(round(ls * k)), int(round(le * k)),
                      max(1.0, rate * k), root if root <= 127 else 60, corr)

    @staticmethod
    def _gens(pgen, lo, hi):
        g = {}
        for i in range(lo, hi):
            op, = struct.unpack_from("<H", pgen, 4 * i)
            if op in (KEY_RANGE, VEL_RANGE):
                g[op] = (pgen[4 * i + 2], pgen[4 * i + 3])
            elif op in UNSIGNED:
                g[op] = struct.unpack_from("<H", pgen, 4 * i + 2)[0]
            else:
                g[op] = struct.unpack_from("<h", pgen, 4 * i + 2)[0]
        return g

    @classmethod
    def _zones(cls, heads, bag, gen, hsize, hfmt, bag_field):
        """Each header's zones: [generator dicts], from its bag range."""
        n_gens = len(gen) // 4
        bags = [struct.unpack_from("<HH", bag, i)[0] for i in range(0, len(bag) - 3, 4)]
        hs = [struct.unpack_from(hfmt, heads, i) for i in range(0, len(heads) - hsize + 1, hsize)]
        out = []
        for k in range(len(hs) - 1):
            b0, b1 = hs[k][bag_field], hs[k + 1][bag_field]
            zones = []
            for b in range(b0, min(b1, len(bags) - 1)):
                zones.append(cls._gens(gen, bags[b], min(bags[b + 1], n_gens)))
            out.append((hs[k], zones))
        return out

    def _presets(self, parts, samples):
        insts = self._zones(parts[b"inst"], parts[b"ibag"], parts[b"igen"], 22, "<20sH", 1)
        presets = self._zones(parts[b"phdr"], parts[b"pbag"], parts[b"pgen"], 38, "<20sHHHIII", 3)
        for (name, preset, bank, *_), pzones in presets:
            pglobal = {}
            if pzones and INSTRUMENT not in pzones[0]:
                pglobal, pzones = pzones[0], pzones[1:]
            regions = []
            for pz in pzones:
                if INSTRUMENT not in pz or pz[INSTRUMENT] >= len(insts):
                    continue
                pg = {**pglobal, **pz}
                izones = insts[pz[INSTRUMENT]][1]
                iglobal = {}
                if izones and SAMPLE_ID not in izones[0]:
                    iglobal, izones = izones[0], izones[1:]
                for iz in izones:
                    if SAMPLE_ID not in iz or iz[SAMPLE_ID] >= len(samples) or samples[iz[SAMPLE_ID]] is None:
                        continue
                    g = {KEY_RANGE: (0, 127), VEL_RANGE: (0, 127), **DEFAULTS, **iglobal, **iz}
                    for op, v in pg.items():
                        if op in (KEY_RANGE, VEL_RANGE):
                            lo, hi = g[op]
                            g[op] = (max(lo, v[0]), min(hi, v[1]))
                        elif op not in NOT_ADDED:
                            g[op] = g.get(op, 0) + v
                    if g[KEY_RANGE][0] > g[KEY_RANGE][1] or g[VEL_RANGE][0] > g[VEL_RANGE][1]:
                        continue
                    regions.append((g, iz[SAMPLE_ID]))
            if regions:
                self.presets.setdefault((bank, preset), regions)

    def _sample(self, i):
        smp = self._samples_[i]
        if isinstance(smp, tuple):
            smp = self._samples_[i] = self._vorbis(self._path, *smp)
        return smp

    def regions(self, bank, preset):
        """
        A preset's regions (the first preset's if it has none), put together
        the first time: its .sf3 samples decoded and packed into one array (the
        voices are played together, an array at a time), and its .sf2 samples
        read through once, so the first notes don't wait on the disk.
        """
        key = (bank, preset) if (bank, preset) in self.presets else \
            (0, preset) if (0, preset) in self.presets else next(iter(self.presets), None)
        if key is None:
            return []
        if key not in self._regions:
            zones = self.presets[key]
            used = sorted({i for _, i in zones})
            todo = [i for i in used if isinstance(self._samples_[i], tuple)]
            with progress.stage(0.0, 0.5 if todo else 0.0):
                for k, i in enumerate(todo):
                    self._sample(i)
                    progress.report((k + 1) / len(todo))
            if todo:
                self._pack([self._samples_[i] for i in todo])
            with progress.stage(0.5 if todo else 0.0, 1.0):
                self._warm([self._samples_[i] for i in used if self._samples_[i].data is self._map])
            self._regions[key] = [Region(g, self._samples_[i]) for g, i in zones]
        return self._regions[key]

    @staticmethod
    def _pack(samples):
        """Decoded samples into one array (each keeps its place in it)."""
        pool = np.concatenate([smp.data for smp in samples])
        at = 0
        for smp in samples:
            n = len(smp.data)
            smp.data = pool
            smp.start, smp.end = smp.start + at, smp.end + at
            smp.loop_start, smp.loop_end = smp.loop_start + at, smp.loop_end + at
            at += n

    @staticmethod
    def _warm(samples):
        """Read the samples' pages of the file once (into the system's file cache)."""
        spans = sorted({(smp.start, smp.end) for smp in samples})
        total = sum(e - s for s, e in spans) or 1
        done = 0
        for s0, s1 in spans:
            if samples and s1 > s0:
                int(samples[0].data[s0:s1:2048].sum())           # (a value in every 4 KB page)
            done += s1 - s0
            progress.report(done / total)

    # ----- playing ---------------------------------------------------------------
    def _channel(self, ch):
        c = self.channels.get(ch)
        if c is None:
            c = self.channels[ch] = {"regions": self.regions(0, 0), "volume": 100}
        return c

    def program_select(self, ch, sfid, bank, preset, is_drums=False):
        self._channel(ch)["regions"] = self.regions(bank, preset)

    def program_change(self, ch, preset, is_drums=False):
        self.program_select(ch, 0, 0, preset)

    def noteon(self, ch, key, vel):
        if vel <= 0:
            self.noteoff(ch, key)
            return
        for r in self._channel(ch)["regions"]:
            if r.matches(key, vel):
                self.voices.append(Voice(ch, key, vel, r, self.rate))
        if len(self.voices) > MAX_VOICES:
            self.voices.sort(key=lambda v: (v.released is None, -v.level_db()), reverse=True)
            del self.voices[MAX_VOICES:]

    def noteoff(self, ch, key):
        for v in self.voices:
            if v.ch == ch and v.key == key:
                v.release()

    def control_change(self, ch, control, value):
        if control == 7:
            self._channel(ch)["volume"] = min(127, max(0, int(value)))
        elif control in (120,):
            self.sounds_off(ch)
        elif control in (123,):
            self.notes_off(ch)

    def notes_off(self, ch=None):
        for v in self.voices:
            if ch is None or v.ch == ch:
                v.release()

    def sounds_off(self, ch=None):
        self.voices = [v for v in self.voices if ch is not None and v.ch != ch]

    def generate(self, frames):
        """The next `frames` frames: interleaved stereo float32 (a memoryview)."""
        out = np.zeros((frames, 2), np.float32)
        if self.voices:
            groups = {}
            for v in self.voices:
                groups.setdefault(id(v.r.sample.data), []).append(v)
            for vs in groups.values():
                self._render(vs, frames, out)
            self.voices = [v for v in self.voices if not v.done]
        return memoryview(out.reshape(-1))

    def _render(self, vs, n, out):
        """
        Add voices `vs` (sharing one sample array) to out (n x 2): every voice at
        once, as (voices x frames) arrays - a voice at a time, Python's overhead
        was most of the cost.
        """
        d = vs[0].r.sample.data
        (step, ls, le, last, mode, delay, attack, t0, slope, sus, rslope, gl, gr) = \
            np.array([v.fixed for v in vs], dtype=np.float64).T
        pos = np.array([v.pos for v in vs])
        t = np.array([v.t for v in vs], dtype=np.float64)
        rel = np.array([v.released is not None for v in vs])
        tr = np.array([v.released or 0 for v in vs], dtype=np.float64)
        rel_db = np.array([v.rel_db for v in vs])
        vol = np.array([self._channel(v.ch)["volume"] for v in vs], dtype=np.float64) / 127.0
        ramp = _ramp(n + 1)
        # where in the samples: looped voices wrap round their loop
        p = pos[:, None] + step[:, None] * ramp
        lp = (mode == 1) | ((mode == 3) & ~rel)
        span = np.maximum(1.0, le - ls)
        if lp.any():
            wrap = lp[:, None] & (p >= le[:, None])
            p = np.where(wrap, ls[:, None] + np.mod(p - ls[:, None], span[:, None]), p)
        new_pos = p[:, -1]
        p = p[:, :-1]
        valid = lp[:, None] | (p < last[:, None])            # (an unlooped sample's end: silence after)
        np.minimum(p, last[:, None], out=p)
        i = p.astype(np.int64)
        f = (p - i).astype(np.float32)
        i1 = i + 1
        if lp.any():
            i1 = np.where(lp[:, None] & (i1 >= le[:, None]), i1 - span[:, None].astype(np.int64), i1)
        np.minimum(i1, last[:, None].astype(np.int64), out=i1)
        a = d[i].astype(np.float32)
        w = a + (d[i1].astype(np.float32) - a) * f
        # the volume envelope, in dB down: hold then decay to the sustain level; after the key, the release -
        # worked out every ENV_STEP frames and drawn straight between (smooth: no audible difference)
        m = -(-n // ENV_STEP)
        tc = (t[:, None] + _ramp(m + 1) * ENV_STEP) / self.rate
        db = np.minimum(sus[:, None], np.maximum(0.0, tc - t0[:, None]) * slope[:, None])
        if rel.any():
            db = np.where(rel[:, None], rel_db[:, None] + np.maximum(0.0, tc - (tr / self.rate)[:, None])
                          * rslope[:, None], db)
        ac = np.exp(db * (-np.log(10.0) / 20.0)).astype(np.float32)
        amp = (ac[:, :-1, None] + (ac[:, 1:] - ac[:, :-1])[:, :, None] * _STEP_FRAC).reshape(len(vs), -1)[:, :n]
        attacking = ~rel & (t / self.rate < delay + attack)
        if attacking.any():                                  # (the attack, frame by frame: it can be 1 ms)
            k = np.nonzero(attacking)[0]
            ts = (t[k, None] + ramp[:n]) / self.rate
            up = np.clip((ts - delay[k, None]) / np.maximum(attack[k], 1e-9)[:, None], 0.0, 1.0)
            amp[k] = np.where(ts < (delay + attack)[k, None], up, amp[k])
        w *= amp
        w *= valid
        g = vol * vol * (self.master * INT16_SCALE)
        out[:, 0] += (gl * g).astype(np.float32) @ w
        out[:, 1] += (gr * g).astype(np.float32) @ w
        over = (db[:, m] >= SILENT_DB) | ~valid[:, -1]
        for k, v in enumerate(vs):
            v.pos = float(new_pos[k])
            v.t += n
            if over[k]:
                v.done = True
