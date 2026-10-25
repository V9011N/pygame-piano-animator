"""[Ported from the Human MIDI Cleaner (midi_cleaner/cleaning.py): displaced notes keep their partner; the plan records where each added note comes from]

Plan and apply note-level cleaning of a human MIDI against its perfect source.

Only two kinds of edit exist: *remove* a human note the source doesn't contain, and *add* a
note the source has but the human file lacks. Nothing else is touched: velocities, timing,
pedal and every other event of the original file are written back exactly as recorded.

Safeguards, because the aim is to fix mistakes without damaging the performance:
  * notes are matched one-to-one, per aligned segment, by pitch and warped time;
  * a leftover human note and a leftover source note of the same pitch that are fairly close
    are one *displaced* note (timing, not a wrong note) and are left alone;
  * nothing is edited where the local alignment is poorly supported, near the joins between
    aligned segments, or in stretches the human skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .alignment import Alignment
from .loader import MidiData

MATCH_TOL = 0.30  # s: same pitch within this of its warped position is the same note
NEAR_TOL = 0.90  # s: leftover same-pitch notes this close are one displaced note, not an error
EDGE_MARGIN = 0.5  # s: no edits this close to a join between aligned segments
TRUST_WINDOW = 25  # notes either side that vote on how well the local alignment holds
MIN_TRUST = 0.6  # edit only where at least this share of nearby notes line up
CHORD_WINDOW = 0.04  # s: source notes this close together are one chord
VELOCITY_WINDOW = 0.5  # s and ...
VELOCITY_REACH = 12  # ... semitones of human notes whose velocity an added note borrows


# Per-note outcomes recorded in the plan (also used for diagnostics).
OUTSIDE, MATCHED, DISPLACED, UNTRUSTED, REMOVE, ADD, HELD = range(7)


@dataclass(frozen=True)
class NewNote:
    pitch: int
    onset: float  # seconds, absolute in the human file
    duration: float
    velocity: int
    track: int
    channel: int


@dataclass
class CleaningPlan:
    remove: np.ndarray  # indices into the human MidiData notes
    add: list[NewNote]
    matched: int = 0  # human notes confirmed against the source
    displaced: int = 0  # same note, different timing: left alone
    untrusted: int = 0  # leftovers not edited because the alignment there is poorly supported
    human_status: np.ndarray | None = None  # per human note: one of the outcomes above
    human_partner: np.ndarray | None = None  # per human note: matched (or displaced) perfect note index, else -1
    perfect_status: np.ndarray | None = None  # per perfect note
    notes: list[str] = field(default_factory=list)
    add_sources: list[int] = field(default_factory=list)  # perfect note index each `add` entry comes from
    human_segment: np.ndarray | None = None  # per human note: the aligned segment it lies in (-1 outside)

    @property
    def n_remove(self) -> int:
        return len(self.remove)

    @property
    def n_add(self) -> int:
        return len(self.add)


# ---- matching ------------------------------------------------------------------

def _match_cluster(a: np.ndarray, b: np.ndarray, tol: float) -> list[tuple[int, int]]:
    """Non-crossing pairs maximizing the match count, then minimizing total time error (exact DP)."""
    n, m = len(a), len(b)
    gain = 1000.0  # one more match always beats any amount of timing error within tol
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, prev = best[i], best[i - 1]
        for j in range(1, m + 1):
            v = max(prev[j], row[j - 1])
            d = abs(a[i - 1] - b[j - 1])
            if d <= tol:
                v = max(v, prev[j - 1] + gain - d)
            row[j] = v
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        d = abs(a[i - 1] - b[j - 1])
        if d <= tol and best[i][j] == best[i - 1][j - 1] + gain - d:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def _match_sorted(a: np.ndarray, b: np.ndarray, tol: float) -> list[tuple[int, int]]:
    """One-to-one pairs between two ascending time arrays within tol.

    Independent clusters (notes that could only ever pair among themselves) are solved
    separately, so even long repeated-note passages stay cheap.
    """
    if len(a) == 0 or len(b) == 0:
        return []
    lo = np.searchsorted(b, a - tol, side="left")
    hi = np.searchsorted(b, a + tol, side="right")
    pairs: list[tuple[int, int]] = []
    i, n = 0, len(a)
    while i < n:
        if lo[i] >= hi[i]:
            i += 1
            continue
        start, b_lo, b_hi = i, int(lo[i]), int(hi[i])
        i += 1
        while i < n and lo[i] < b_hi and lo[i] < hi[i]:
            b_hi = max(b_hi, int(hi[i]))
            i += 1
        pairs += [(start + x, b_lo + y) for x, y in _match_cluster(a[start:i], b[b_lo:b_hi], tol)]
    return pairs


def _match_by_pitch(h_idx: np.ndarray, h_time: np.ndarray, h_pitch: np.ndarray,
                    p_idx: np.ndarray, p_time: np.ndarray, p_pitch: np.ndarray,
                    tol: float) -> list[tuple[int, int]]:
    """Match human to perfect notes of equal pitch; returns (human index, perfect index) pairs."""
    out = []
    for pitch in np.intersect1d(np.unique(h_pitch), np.unique(p_pitch)):
        hs = np.flatnonzero(h_pitch == pitch)
        ps = np.flatnonzero(p_pitch == pitch)
        hs = hs[np.argsort(h_time[hs], kind="stable")]
        ps = ps[np.argsort(p_time[ps], kind="stable")]
        for i, j in _match_sorted(h_time[hs], p_time[ps], tol):
            out.append((int(h_idx[hs[i]]), int(p_idx[ps[j]])))
    return out


def _local_trust(aligned: np.ndarray, order: np.ndarray) -> np.ndarray:
    """Share of nearby notes (in time order) that line up, for every note; indexed like `aligned`."""
    flags = aligned[order].astype(float)
    csum = np.concatenate(([0.0], np.cumsum(flags)))
    n = len(flags)
    lo = np.clip(np.arange(n) - TRUST_WINDOW, 0, n)
    hi = np.clip(np.arange(n) + TRUST_WINDOW + 1, 0, n)
    trust = np.empty(n)
    trust[order] = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    return trust


# ---- planning ------------------------------------------------------------------

def plan_cleaning(perfect: MidiData, human: MidiData, alignment: Alignment) -> CleaningPlan:
    if human.tracks is None:
        raise ValueError("The human MIDI was not loaded with provenance, so it can't be edited.")
    ht = human.onsets - human.start
    pt = perfect.onsets - perfect.start
    mapped, covered = alignment.human_to_perfect(ht)
    seg_of_h, _ = alignment.segment_of_human(ht)
    n_seg = len(alignment.segments)

    h_status = np.full(len(ht), OUTSIDE, np.int8)
    h_partner = np.full(len(ht), -1, int)
    p_status = np.full(len(pt), OUTSIDE, np.int8)
    remove: list[int] = []
    missing: list[tuple[int, int]] = []  # (segment, perfect index) to add
    matched_pairs: list[tuple[int, int, int]] = []  # (segment, human idx, perfect idx)
    n_displaced = n_untrusted = 0

    for k, seg in enumerate(alignment.segments):
        h_idx = np.flatnonzero(covered & (seg_of_h == k))
        p_idx = np.flatnonzero((pt >= seg.p_lo) & (pt <= seg.p_hi))
        if len(h_idx) == 0 or len(p_idx) == 0:
            continue
        pairs = _match_by_pitch(h_idx, mapped[h_idx], human.pitches[h_idx],
                                p_idx, pt[p_idx], perfect.pitches[p_idx], MATCH_TOL)
        matched_pairs += [(k, h, p) for h, p in pairs]
        for h, p in pairs:
            h_status[h], h_partner[h], p_status[p] = MATCHED, p, MATCHED
        done_h = {h for h, _ in pairs}
        done_p = {p for _, p in pairs}

        left_h = np.array([h for h in h_idx if h not in done_h], dtype=int)
        left_p = np.array([p for p in p_idx if p not in done_p], dtype=int)
        near = _match_by_pitch(left_h, mapped[left_h], human.pitches[left_h],
                               left_p, pt[left_p], perfect.pitches[left_p], NEAR_TOL)
        n_displaced += len(near)
        for h, p in near:
            h_status[h], p_status[p] = DISPLACED, DISPLACED
            h_partner[h] = p
        displaced_h = {h for h, _ in near}
        displaced_p = {p for _, p in near}

        # How well does the alignment hold around each note? Matched and displaced notes count.
        aligned_h = np.zeros(len(human.pitches), bool)
        aligned_h[list(done_h | displaced_h)] = True
        aligned_p = np.zeros(len(pt), bool)
        aligned_p[list(done_p | displaced_p)] = True
        trust_h = _local_trust(aligned_h[h_idx], np.argsort(ht[h_idx], kind="stable"))
        trust_p = _local_trust(aligned_p[p_idx], np.argsort(pt[p_idx], kind="stable"))
        trust_h = dict(zip(h_idx, trust_h))
        trust_p = dict(zip(p_idx, trust_p))

        lo_margin = EDGE_MARGIN if k > 0 else 0.0  # only joins with another segment are risky
        hi_margin = EDGE_MARGIN if k < n_seg - 1 else 0.0

        def interior(t: float) -> bool:
            return seg.p_lo + lo_margin <= t <= seg.p_hi - hi_margin

        for h in left_h:
            if h in displaced_h:
                continue
            if trust_h[h] < MIN_TRUST or not interior(mapped[h]):
                n_untrusted += 1
                h_status[h] = UNTRUSTED
            else:
                remove.append(int(h))
                h_status[h] = REMOVE
        for p in left_p:
            if p in displaced_p:
                continue
            if trust_p[p] < MIN_TRUST or not interior(pt[p]):
                n_untrusted += 1
                p_status[p] = UNTRUSTED
            else:
                missing.append((k, int(p)))

    notes, added_p, held_p = _build_additions(perfect, human, alignment, missing, matched_pairs, ht, pt)
    p_status[added_p] = ADD
    p_status[held_p] = HELD
    h_segment = np.where(covered, seg_of_h, -1)
    return CleaningPlan(
        add_sources=list(added_p),
        human_segment=h_segment,
        remove=np.array(sorted(set(remove)), dtype=int),
        add=notes,
        matched=len(matched_pairs),
        displaced=n_displaced,
        untrusted=n_untrusted + len(held_p),
        human_status=h_status,
        human_partner=h_partner,
        perfect_status=p_status,
    )


def _onset_groups(perfect_times: np.ndarray, human_times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Collapse confirmed note pairs into chords: (mean perfect time, mean human time), ascending."""
    order = np.argsort(perfect_times, kind="stable")
    pt_, ht_ = perfect_times[order], human_times[order]
    cut = np.flatnonzero(np.diff(pt_) > CHORD_WINDOW) + 1
    starts = np.concatenate(([0], cut))
    ends = np.concatenate((cut, [len(pt_)]))
    return (np.array([pt_[a:b].mean() for a, b in zip(starts, ends)]),
            np.array([ht_[a:b].mean() for a, b in zip(starts, ends)]))


def _build_additions(perfect: MidiData, human: MidiData, alignment: Alignment,
                     missing: list[tuple[int, int]], matched: list[tuple[int, int, int]],
                     ht: np.ndarray, pt: np.ndarray) -> tuple[list[NewNote], list[int], list[int]]:
    """Turn missing source notes into notes in the human file's own timing and dynamics."""
    by_seg: dict[int, list[tuple[int, int]]] = {}
    for k, h, p in matched:
        by_seg.setdefault(k, []).append((h, p))
    pair_h = {k: np.array([h for h, _ in v]) for k, v in by_seg.items()}
    pair_p = {k: np.array([p for _, p in v]) for k, v in by_seg.items()}
    groups = {k: _onset_groups(pt[pair_p[k]], ht[pair_h[k]]) for k in by_seg}
    global_velocity = int(np.median(human.velocities))
    tracks, counts = np.unique(human.tracks, return_counts=True)
    default_track = int(tracks[np.argmax(counts)])
    default_channel = int(human.channels[human.tracks == default_track][0])

    out: list[NewNote] = []
    added_p: list[int] = []
    skipped: list[int] = []
    for k, p in missing:
        seg = alignment.segments[k]
        tp = pt[p]
        warp = lambda t: np.interp(t, seg.p_knots, seg.h_of_p)  # noqa: E731
        h_of, p_of = pair_h.get(k), pair_p.get(k)
        if h_of is None:
            skipped.append(p)
            continue

        gp, gh = groups[k]
        nearest_group = int(np.argmin(np.abs(gp - tp)))
        if abs(gp[nearest_group] - tp) <= CHORD_WINDOW:
            th = float(gh[nearest_group])  # play with the rest of its chord, rolled as the human rolled it
        elif gp[0] < tp < gp[-1]:
            th = float(np.interp(tp, gp, gh))  # between the human's own neighbouring notes
        else:  # past the ends: the warp, shifted by how the nearest confirmed notes deviate from it
            th = float(warp(tp) + gh[nearest_group] - warp(gp[nearest_group]))
        scale = float(np.clip(warp(tp + 0.5) - warp(tp - 0.5), 0.4, 2.5))
        duration = max(float(perfect.offsets[p] - perfect.onsets[p]) * scale, 0.05)

        abs_onset = human.start + th
        same = human.pitches == perfect.pitches[p]
        if np.any(same & (human.onsets <= abs_onset) & (human.offsets > abs_onset)):
            skipped.append(p)  # already sounding in the human performance: nothing is missing
            continue
        later = human.onsets[same & (human.onsets > abs_onset)]
        if len(later):
            duration = min(duration, max(float(later.min()) - abs_onset - 0.01, 0.03))

        near_notes = np.flatnonzero((np.abs(human.onsets - abs_onset) <= VELOCITY_WINDOW)
                                    & (np.abs(human.pitches - perfect.pitches[p]) <= VELOCITY_REACH))
        if len(near_notes) == 0:
            near_notes = np.flatnonzero(np.abs(human.onsets - abs_onset) <= 1.5)
        if len(near_notes):
            velocity = int(np.median(human.velocities[near_notes]))
            nearest = near_notes[np.argmin(np.abs(human.pitches[near_notes] - perfect.pitches[p]))]
            track, channel = int(human.tracks[nearest]), int(human.channels[nearest])
        else:
            velocity, track, channel = global_velocity, default_track, default_channel
        out.append(NewNote(int(perfect.pitches[p]), abs_onset, duration,
                           int(np.clip(velocity, 1, 127)), track, channel))
        added_p.append(p)
    return out, added_p, skipped


# ---- applying ------------------------------------------------------------------

def build_cleaned_midi(plan: CleaningPlan, human: MidiData,
                       remove: bool = True, add: bool = True) -> mido.MidiFile:
    """The human file with the chosen edits; every other event is preserved exactly."""
    import mido  # (only here: this app writes files through document.py)
    if human.tracks is None or human.tempo_map is None:
        raise ValueError("The human MIDI was not loaded with provenance, so it can't be edited.")
    source = mido.MidiFile(str(human.path), clip=True)

    drop: set[tuple[int, int]] = set()
    if remove:
        for i in plan.remove:
            drop.add((int(human.tracks[i]), int(human.on_events[i])))
            if human.off_events[i] >= 0:
                drop.add((int(human.tracks[i]), int(human.off_events[i])))

    # Release style: mirror the file (MAESTRO writes note_on with velocity 0).
    uses_note_off = any(m.type == "note_off" for t in source.tracks for m in t)

    new_events: dict[int, list[tuple[int, int, mido.Message]]] = {}  # track -> (tick, order, msg)
    if add:
        for n in plan.add:
            on = int(human.tempo_map.to_ticks(n.onset))
            off = max(int(human.tempo_map.to_ticks(n.onset + n.duration)), on + 1)
            release = (mido.Message("note_off", channel=n.channel, note=n.pitch, velocity=64)
                       if uses_note_off else
                       mido.Message("note_on", channel=n.channel, note=n.pitch, velocity=0))
            bucket = new_events.setdefault(n.track, [])
            bucket.append((on, 2, mido.Message("note_on", channel=n.channel, note=n.pitch,
                                               velocity=n.velocity)))
            bucket.append((off, 0, release))

    out = mido.MidiFile(type=source.type, ticks_per_beat=source.ticks_per_beat)
    for ti, track in enumerate(source.tracks):
        events: list[tuple[int, int, int, mido.Message]] = []  # (tick, order, seq, msg)
        tick = 0
        for ei, msg in enumerate(track):
            tick += msg.time
            if (ti, ei) in drop:
                continue
            if msg.type == "end_of_track":
                continue
            events.append((tick, 1, ei, msg))
        for seq, (t, order, msg) in enumerate(new_events.get(ti, [])):
            events.append((t, order, len(track) + seq, msg))
        events.sort(key=lambda e: e[:3])

        rebuilt = mido.MidiTrack()
        last = 0
        for t, _, _, msg in events:
            rebuilt.append(msg.copy(time=t - last))
            last = t
        rebuilt.append(mido.MetaMessage("end_of_track", time=max(tick - last, 0)))
        out.tracks.append(rebuilt)
    return out


def apply_cleaning(plan: CleaningPlan, human: MidiData, out_path: str | Path,
                   remove: bool = True, add: bool = True) -> Path:
    """Build the cleaned file and save it to out_path (folders are created as needed)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_cleaned_midi(plan, human, remove, add).save(str(out_path))
    return out_path
