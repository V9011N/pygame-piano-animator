"""[Ported from the Human MIDI Cleaner (midi_cleaner/alignment.py): unchanged]

Time-align a human performance to its perfect source.

Two passes, both DTW (dynamic time warping):
  coarse  smoothed key + pitch-class frames over the whole piece. Open-ended on the perfect axis and
          allowed to *jump* (at a cost), so a take that starts mid-piece, skips a
          section or replays one still aligns. Yields a list of monotone segments.
  fine    88-key frames with blurred onsets at FINE_HOP resolution, run inside a band
          around each coarse segment; tightens the alignment to roughly note precision.

All times are seconds relative to each file's own first note-on, so the different start
offsets of the two files never matter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .loader import MidiData

# ---- coarse pass ---------------------------------------------------------------
MAX_FRAMES = 2000  # caps the coarse DTW matrix
MIN_HOP = 0.1  # seconds per coarse frame
SUSTAIN_CAP = 0.5  # seconds of a held note that count toward chroma
SUSTAIN_WEIGHT = 0.3  # held frames count this much vs. the onset frame
COARSE_PENALTY = 0.15  # DTW cost for a non-diagonal step (stops degenerate paths)
COARSE_SMOOTHING = (1, 2, 3, 4, 3, 2, 1)  # frame weights; tolerates timing error between takes
CHROMA_WEIGHT = 1.0  # pitch-class summary added to the 88 key columns
JUMP_SECONDS = 10.0  # a jump costs as much as this many seconds of ~50%-mismatched music
JUMP_MISMATCH = 0.5
MIN_JUMP = 2.0  # seconds; smaller discontinuities are path wobble, not a real jump
MIN_SEGMENT = 3.0  # seconds; shorter aligned stretches are discarded as noise

# ---- fine pass -----------------------------------------------------------------
FINE_HOP = 0.02  # seconds per fine frame
ONSET_SIGMA = 0.03  # seconds; gaussian blur of each note onset
FINE_SUSTAIN_WEIGHT = 0.25  # held-note contribution relative to the onset peak
FINE_BAND = 2.5  # seconds either side of the coarse path the fine pass may wander
FINE_PENALTY = 0.1
LOWEST_KEY = 21  # A0
KEYS = 88
BIG = 1e4  # finite stand-in for "impossible" so cumulative sums stay valid


@dataclass(frozen=True)
class Segment:
    """One monotone stretch of the alignment (no jumps inside)."""

    p_knots: np.ndarray  # perfect-file times
    h_of_p: np.ndarray  # matching human-file times
    h_knots: np.ndarray  # human-file times
    p_of_h: np.ndarray  # matching perfect-file times
    p_lo: float  # extent on the perfect timeline (frame edges)
    p_hi: float
    h_lo: float  # extent on the human timeline
    h_hi: float


@dataclass(frozen=True)
class Alignment:
    segments: tuple[Segment, ...]  # in human-time order
    similarity: float  # mean feature cosine similarity along the path (audible cells)

    @property
    def jumps(self) -> int:
        return max(len(self.segments) - 1, 0)

    def perfect_to_human(self, times: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """Per segment: (mask of the given perfect times it covers, their human times).

        More than one segment can cover a time when the human replayed that passage.
        """
        out = []
        for s in self.segments:
            mask = (times >= s.p_lo) & (times <= s.p_hi)
            out.append((mask, np.interp(times, s.p_knots, s.h_of_p)))
        return out

    def segment_of_human(self, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(segment index, mask of the given human times lying inside that segment)."""
        if not self.segments:
            return np.zeros(len(times), int), np.zeros(len(times), bool)
        starts = np.array([s.h_lo for s in self.segments])
        ends = np.array([s.h_hi for s in self.segments])
        idx = np.clip(np.searchsorted(starts, times, side="right") - 1, 0, len(starts) - 1)
        return idx, (times >= starts[idx]) & (times <= ends[idx])

    def human_to_perfect(self, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(perfect times, mask of the given human times lying inside an aligned segment)."""
        idx, covered = self.segment_of_human(times)
        mapped = np.zeros_like(times)
        for k, s in enumerate(self.segments):
            sel = idx == k
            mapped[sel] = np.interp(times[sel], s.h_knots, s.p_of_h)
        return mapped, covered


# ---- shared helpers ------------------------------------------------------------

def _make_segment(rows: np.ndarray, cols: np.ndarray, hop: float) -> Segment:
    """Collapse a frame path (every row and column visited) into a Segment."""
    r0, c0 = int(rows.min()), int(cols.min())
    n_r, n_c = int(rows.max()) - r0 + 1, int(cols.max()) - c0 + 1

    def partners(src: np.ndarray, dst: np.ndarray, n: int, base_src: int, base_dst: int) -> np.ndarray:
        sums = np.bincount(src - base_src, weights=dst, minlength=n)
        counts = np.bincount(src - base_src, minlength=n)
        return (sums / np.maximum(counts, 1) + 0.5) * hop

    return Segment(
        p_knots=(np.arange(r0, r0 + n_r) + 0.5) * hop,
        h_of_p=partners(rows, cols, n_r, r0, c0),
        h_knots=(np.arange(c0, c0 + n_c) + 0.5) * hop,
        p_of_h=partners(cols, rows, n_c, c0, r0),
        p_lo=r0 * hop, p_hi=(r0 + n_r) * hop, h_lo=c0 * hop, h_hi=(c0 + n_c) * hop,
    )


def _unit_rows(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    norms = np.linalg.norm(frames, axis=1)
    return frames / np.maximum(norms, 1e-9)[:, None], norms < 1e-9


# ---- coarse pass ---------------------------------------------------------------

def _coarse_frames(data: MidiData, hop: float, n_frames: int) -> np.ndarray:
    """88 key columns (time-smoothed) plus 12 pitch-class columns, sqrt-compressed."""
    keys = np.zeros((n_frames, KEYS))
    on = data.onsets - data.start
    off = data.offsets - data.start
    start = np.minimum((on / hop).astype(int), n_frames - 1)
    stop = np.minimum(np.ceil(np.minimum(off, on + SUSTAIN_CAP) / hop).astype(int), n_frames)
    stop = np.maximum(stop, start + 1)
    for s, e, k in zip(start, stop, np.clip(data.pitches - LOWEST_KEY, 0, KEYS - 1)):
        keys[s:e, k] += SUSTAIN_WEIGHT
        keys[s, k] += 1.0 - SUSTAIN_WEIGHT
    kernel = np.array(COARSE_SMOOTHING, float)
    kernel /= kernel.sum()
    keys = np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="same"), 0, keys)
    chroma = np.zeros((n_frames, 12))
    for k in range(KEYS):
        chroma[:, (k + LOWEST_KEY) % 12] += keys[:, k]
    return np.sqrt(np.hstack([keys, CHROMA_WEIGHT * chroma]))  # compress: dense != loud


def _coarse_path(cost: np.ndarray, penalty: float, jump: float
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DTW over human columns j and perfect rows i with jumps along the perfect axis.

    Every human frame is explained; the path may start and end anywhere in the perfect
    piece, and may leap to any perfect frame between two human frames for a flat `jump`
    cost. Each column is one vector pass. Returns (rows, cols, jumped) where jumped[k] says
    the step into cell k was a jump.
    """
    n, m = cost.shape
    by_col = np.ascontiguousarray(cost.T)  # [j, i]
    direction = np.zeros((m, n), np.int8)  # 0 diagonal, 1 vertical, 2 horizontal, 3 jump
    jump_src = np.zeros(m, int)
    rows_idx = np.arange(n)
    codes = np.array([0, 2, 3], np.int8)

    prev = by_col[0].copy()  # free start anywhere in the perfect piece
    for j in range(1, m):
        c = by_col[j]
        src = int(prev.argmin())
        jump_src[j] = src
        options = np.stack([np.concatenate(([BIG], prev[:-1])),  # diagonal
                            prev + penalty,  # human advances alone
                            np.full(n, prev[src] + jump)])  # leap from the best cell
        pick = options.argmin(axis=0)
        entry = c + options[pick, rows_idx]
        steps = np.cumsum(c + penalty)  # perfect advances alone, down the column
        shifted = entry - steps
        running = np.minimum.accumulate(shifted)
        direction[j] = np.where(running < shifted - 1e-9, 1, codes[pick])
        prev = steps + running

    i, j = int(prev.argmin()), m - 1
    cells = []  # (row, col, whether the step into this cell was a jump), end -> start
    while True:
        d = direction[j, i] if j > 0 else -1
        cells.append((i, j, d == 3))
        if j == 0:
            break
        if d == 1:
            i -= 1
        elif d == 0:
            i, j = i - 1, j - 1
        elif d == 2:
            j -= 1
        else:
            i, j = jump_src[j], j - 1
    arr = np.array(cells[::-1])
    return arr[:, 0], arr[:, 1], arr[:, 2].astype(bool)


def _split_segments(rows: np.ndarray, cols: np.ndarray, jumped: np.ndarray,
                    hop: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """Cut the path at real jumps; ignore small discontinuities and tiny fragments."""
    real = jumped.copy()
    last_row = np.concatenate(([rows[0]], rows[:-1]))
    real &= np.abs(rows - last_row) * hop >= MIN_JUMP
    cuts = np.flatnonzero(real)
    pieces = [(rows[a:b], cols[a:b]) for a, b in zip(np.concatenate(([0], cuts)),
                                                     np.concatenate((cuts, [len(rows)])))]
    kept = [(r, c) for r, c in pieces
            if (len(c) * hop >= MIN_SEGMENT) and (r.max() - r.min() + 1) * hop >= MIN_SEGMENT]
    return kept or [max(pieces, key=lambda p: len(p[1]))]


def coarse_align(perfect: MidiData, human: MidiData) -> Alignment:
    hop = max(MIN_HOP, max(perfect.span, human.span, 1e-3) / MAX_FRAMES)
    n_p = int(np.ceil(perfect.span / hop)) + 1
    n_h = int(np.ceil(human.span / hop)) + 1
    unit_p, silent_p = _unit_rows(_coarse_frames(perfect, hop, n_p))
    unit_h, silent_h = _unit_rows(_coarse_frames(human, hop, n_h))
    cost = 1.0 - unit_p @ unit_h.T
    cost[np.ix_(silent_p, silent_h)] = 0.0  # silence matches silence

    jump = JUMP_SECONDS / hop * JUMP_MISMATCH
    rows, cols, jumped = _coarse_path(cost, COARSE_PENALTY, jump)
    pieces = _split_segments(rows, cols, jumped, hop)

    audible = ~np.outer(silent_p, silent_h)
    sims = [1.0 - cost[r, c][audible[r, c]] for r, c in pieces]
    sims = np.concatenate(sims)
    return Alignment(
        segments=tuple(_make_segment(r, c, hop) for r, c in pieces),
        similarity=float(sims.mean()) if len(sims) else 0.0,
    )


# ---- fine pass -----------------------------------------------------------------

def _key_frames(data: MidiData, n_frames: int) -> np.ndarray:
    """88-key frames: gaussian onset peaks plus a weak held-note trace (float32)."""
    frames = np.zeros((n_frames, KEYS), dtype=np.float32)
    on = data.onsets - data.start
    off = data.offsets - data.start
    radius = int(np.ceil(3 * ONSET_SIGMA / FINE_HOP))
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (offsets * FINE_HOP / ONSET_SIGMA) ** 2).astype(np.float32)
    keys = np.clip(data.pitches - LOWEST_KEY, 0, KEYS - 1)
    centre = np.round(on / FINE_HOP).astype(int)
    hold_end = np.minimum(np.ceil(np.minimum(off, on + SUSTAIN_CAP) / FINE_HOP).astype(int), n_frames)
    for c, h_end, k in zip(centre, hold_end, keys):
        lo, hi = max(c - radius, 0), min(c + radius + 1, n_frames)
        frames[lo:hi, k] += kernel[lo - c + radius:hi - c + radius]
        if h_end > c + radius + 1:
            frames[c + radius + 1:h_end, k] += FINE_SUSTAIN_WEIGHT
    return frames


def _banded_path(unit_p: np.ndarray, silent_p: np.ndarray, unit_h: np.ndarray,
                 silent_h: np.ndarray, centres: np.ndarray, half_width: int,
                 penalty: float) -> tuple[np.ndarray, np.ndarray]:
    """DTW from (0, 0) to (n_p-1, n_h-1) restricted to columns centres[i] +/- half_width.

    Rows are stored in band coordinates (column = offset[i] + k). Only a small direction
    code per cell is kept, so memory stays at rows x band bytes even for very long pieces.
    """
    n_p, n_h = len(unit_p), len(unit_h)
    width = 2 * half_width + 1
    offset = np.clip(np.rint(centres).astype(int) - half_width, -half_width, n_h - 1 - half_width)
    offset[0] = -half_width  # band is anchored so (0, 0) and (n_p-1, n_h-1) are inside
    offset[-1] = max(offset[-1], n_h - 1 - 2 * half_width)
    offset = np.maximum.accumulate(offset)
    pad = width + 1
    zeros = np.zeros((pad, unit_h.shape[1]), unit_h.dtype)
    h_pad = np.vstack([zeros, unit_h, zeros])
    silent_h_pad = np.concatenate([np.ones(pad, bool), silent_h, np.ones(pad, bool)])
    valid_pad = np.concatenate([np.zeros(pad, bool), np.ones(n_h, bool), np.zeros(pad, bool)])

    direction = np.zeros((n_p, width), np.int8)  # 0 diagonal, 1 up (row above), 2 left
    prev = np.full(width, BIG)
    for i in range(n_p):
        lo = offset[i] + pad
        window = slice(lo, lo + width)
        cost = 1.0 - h_pad[window] @ unit_p[i]
        cost = np.where(silent_h_pad[window], 0.0 if silent_p[i] else 1.0,
                        1.0 if silent_p[i] else cost)
        cost = np.where(valid_pad[window], cost, BIG).astype(np.float64)

        if i == 0:
            entry = np.full(width, BIG)
            start = -offset[0]  # band index of column 0
            entry[start] = cost[start]
            dir_entry = np.zeros(width, np.int8)
        else:
            shift = offset[i] - offset[i - 1]
            ext = np.full(width + shift + 2, BIG)
            ext[1:width + 1] = prev
            diag = ext[shift:shift + width]  # column j-1 of the row above
            up = ext[shift + 1:shift + 1 + width] + penalty  # column j of the row above
            from_up = up < diag
            entry = cost + np.where(from_up, up, diag)
            dir_entry = from_up.astype(np.int8)

        steps = np.cumsum(cost + penalty)
        shifted = entry - steps
        running = np.minimum.accumulate(shifted)
        direction[i] = np.where(running < shifted - 1e-9, 2, dir_entry)
        prev = np.minimum(steps + running, BIG * 2)

    i = n_p - 1
    k = (n_h - 1) - offset[i]
    rows, cols = [i], [offset[i] + k]
    while i > 0 or offset[i] + k > 0:
        d = direction[i, k]
        if d == 2:
            k -= 1
        else:
            shift = offset[i] - offset[i - 1]
            k = k + shift - (1 if d == 0 else 0)
            i -= 1
        k = int(np.clip(k, 0, width - 1))
        rows.append(i)
        cols.append(offset[i] + k)
        if i == 0 and offset[0] + k == 0:
            break
    return np.array(rows[::-1]), np.array(cols[::-1])


def refine(perfect: MidiData, human: MidiData, coarse: Alignment) -> Alignment:
    n_p = int(np.ceil(perfect.span / FINE_HOP)) + 1
    n_h = int(np.ceil(human.span / FINE_HOP)) + 1
    unit_p, silent_p = _unit_rows(_key_frames(perfect, n_p))
    unit_h, silent_h = _unit_rows(_key_frames(human, n_h))
    half_width = int(np.ceil(FINE_BAND / FINE_HOP))

    segments, sim_sum, sim_count = [], 0.0, 0
    for seg in coarse.segments:
        p0 = max(int(np.ceil(seg.p_lo / FINE_HOP)), 0)
        p1 = min(int(np.floor(seg.p_hi / FINE_HOP)), n_p) - 1
        h0 = max(int(np.ceil(seg.h_lo / FINE_HOP)), 0)
        h1 = min(int(np.floor(seg.h_hi / FINE_HOP)), n_h) - 1
        if p1 - p0 < 10 or h1 - h0 < 10:
            continue
        centres = np.interp((np.arange(p0, p1 + 1) + 0.5) * FINE_HOP, seg.p_knots, seg.h_of_p)
        centres = centres / FINE_HOP - 0.5 - h0
        sp, sh = silent_p[p0:p1 + 1], silent_h[h0:h1 + 1]
        up, uh = unit_p[p0:p1 + 1], unit_h[h0:h1 + 1]
        rows, cols = _banded_path(up, sp, uh, sh, centres, half_width, FINE_PENALTY)
        both = ~(sp[rows] | sh[cols])
        sims = np.einsum("ij,ij->i", up[rows], uh[cols])[both]
        sim_sum, sim_count = sim_sum + float(sims.sum()), sim_count + len(sims)
        segments.append(_make_segment(rows + p0, cols + h0, FINE_HOP))

    if not segments:  # nothing refinable: fall back to the coarse result
        return coarse
    return Alignment(tuple(segments), sim_sum / sim_count if sim_count else 0.0)


def align(perfect: MidiData, human: MidiData) -> tuple[Alignment, Alignment]:
    """Return (coarse, fine) alignments."""
    coarse = coarse_align(perfect, human)
    return coarse, refine(perfect, human, coarse)
