"""[Ported from the Human MIDI Cleaner (midi_cleaner/loader.py): MidiData can keep a fixed origin (`origin`) so an alignment stays valid after edits]

Load a MIDI file into a flat, time-sorted note table (seconds, tempo-resolved).

Besides the times, pitches and velocities that matching needs, every note remembers where
it came from in the file (track, channel, tick, event position). That lets the cleaner edit
the original file in place so everything it does not touch stays exactly as recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DRUM_CHANNEL = 9
DEFAULT_TEMPO = 500_000  # microseconds per beat (120 BPM) until the first tempo event


class MidiLoadError(Exception):
    """Raised with a user-presentable message when a file can't be used."""


class TempoMap:
    """Ticks <-> seconds for a file, using the tempo events of *all* tracks."""

    def __init__(self, ticks_per_beat: int, changes: list[tuple[int, int]]):
        merged: dict[int, int] = {0: DEFAULT_TEMPO}
        for tick, tempo in sorted(changes):
            merged[tick] = tempo  # the last event at a tick wins
        self.ticks = np.array(sorted(merged), dtype=np.int64)
        tempos = np.array([merged[t] for t in self.ticks], dtype=float)
        self.sec_per_tick = tempos / 1e6 / ticks_per_beat
        gaps = np.diff(self.ticks) * self.sec_per_tick[:-1]
        self.seconds = np.concatenate(([0.0], np.cumsum(gaps)))

    def to_seconds(self, ticks: np.ndarray | int) -> np.ndarray:
        ticks = np.asarray(ticks)
        k = np.clip(np.searchsorted(self.ticks, ticks, side="right") - 1, 0, len(self.ticks) - 1)
        return self.seconds[k] + (ticks - self.ticks[k]) * self.sec_per_tick[k]

    def to_ticks(self, seconds: np.ndarray | float) -> np.ndarray:
        seconds = np.asarray(seconds, dtype=float)
        k = np.clip(np.searchsorted(self.seconds, seconds, side="right") - 1, 0, len(self.seconds) - 1)
        return np.rint(self.ticks[k] + (seconds - self.seconds[k]) / self.sec_per_tick[k]).astype(np.int64)


@dataclass(frozen=True)
class MidiData:
    path: Path
    pitches: np.ndarray  # int, MIDI note numbers
    onsets: np.ndarray  # float seconds
    offsets: np.ndarray  # float seconds
    velocities: np.ndarray  # int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # Provenance for in-place editing; None for synthetic data built in the tools.
    tracks: np.ndarray | None = None  # track index of each note
    channels: np.ndarray | None = None
    on_events: np.ndarray | None = None  # position of the note-on message within its track
    off_events: np.ndarray | None = None  # position of the note-off message (-1 if none)
    tempo_map: TempoMap | None = None
    # The time everything is measured from (relative times = onsets - start). None: the first
    # note-on. Fixed when an alignment is reused after the notes were edited.
    origin: float | None = None

    @property
    def note_count(self) -> int:
        return len(self.pitches)

    @property
    def start(self) -> float:
        return float(self.origin) if self.origin is not None else float(self.onsets[0])

    @property
    def end(self) -> float:
        return float(self.offsets.max())

    @property
    def span(self) -> float:
        """Seconds from first note-on to last note-off."""
        return self.end - self.start


def _read_notes(midi) -> tuple[list[tuple], list[tuple[int, int]]]:
    """Walk every track; return (notes, tempo changes). Notes pair FIFO per (channel, pitch)."""
    notes: list[tuple] = []  # (track, channel, pitch, velocity, on_tick, off_tick, on_event, off_event)
    tempos: list[tuple[int, int]] = []
    for ti, track in enumerate(midi.tracks):
        tick = 0
        pending: dict[tuple[int, int], list[int]] = {}  # (channel, pitch) -> indices into notes
        last_tick = 0
        for ei, msg in enumerate(track):
            tick += msg.time
            last_tick = tick
            if msg.type == "set_tempo":
                tempos.append((tick, msg.tempo))
            elif msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append(len(notes))
                notes.append([ti, msg.channel, msg.note, msg.velocity, tick, None, ei, -1])
            elif msg.type in ("note_off", "note_on"):
                queue = pending.get((msg.channel, msg.note))
                if queue:
                    n = notes[queue.pop(0)]
                    n[5], n[7] = tick, ei
        for queue in pending.values():  # notes never released end with the track
            for idx in queue:
                notes[idx][5] = last_tick
    return [tuple(n) for n in notes], tempos


def load_midi(path: str | Path) -> MidiData:
    import mido  # (only here: this app reads files through document.py)
    path = Path(path)
    if not path.is_file():
        raise MidiLoadError(f"File not found: {path}")
    try:
        midi = mido.MidiFile(str(path), clip=True)
    except Exception as exc:  # mido raises a variety of types for bad files
        raise MidiLoadError(f"Could not read '{path.name}' as MIDI: {exc}") from exc
    if midi.type == 2:
        raise MidiLoadError(f"'{path.name}' is a type-2 (multi-song) MIDI file, which isn't supported.")

    notes, tempos = _read_notes(midi)
    # Zero-length notes (released at the very tick they start) are inaudible glitches that some
    # exporters leave next to a repeated note. Ignore them; they stay untouched in the file.
    notes = [n for n in notes if n[1] != DRUM_CHANNEL and n[5] > n[4]]
    if not notes:
        raise MidiLoadError(f"'{path.name}' contains no pitched notes.")

    tempo_map = TempoMap(midi.ticks_per_beat, tempos)
    cols = list(zip(*notes))
    on_ticks, off_ticks = np.array(cols[4]), np.array(cols[5])
    onsets = tempo_map.to_seconds(on_ticks)
    offsets = np.maximum(tempo_map.to_seconds(off_ticks), onsets)
    pitches = np.array(cols[2], dtype=int)
    order = np.lexsort((pitches, onsets))  # by onset, then pitch
    take = lambda c, dtype=int: np.array(c, dtype=dtype)[order]  # noqa: E731
    return MidiData(
        path=path,
        pitches=pitches[order],
        onsets=onsets[order],
        offsets=offsets[order],
        velocities=take(cols[3]),
        tracks=take(cols[0]),
        channels=take(cols[1]),
        on_events=take(cols[6]),
        off_events=take(cols[7]),
        tempo_map=tempo_map,
    )
