"""[Ported from the Human MIDI Cleaner (midi_cleaner/confidence.py): unchanged]

Estimate how likely a human-played MIDI is a performance of a given perfect MIDI.

The two files live on different clocks (the perfect source has its own tempo map, the
human one is played with rubato), so nothing here compares raw timestamps:

  1. `alignment.align` recovers the tempo warp between the files (coarse, then fine). It
     tolerates skipped and repeated sections by aligning in several segments.
  2. Every perfect note is pushed through the warp and we look for the same pitch in the
     human file near that time, and the reverse. Agreement is corrected for chance by
     repeating the test with the alignment deliberately knocked off by seconds.
  3. The evidence is blended into one 0..1 confidence about *identity* (is this the same
     piece?). *Coverage* (how much of the source the human plays) is reported separately,
     so a partial take is a weaker-coverage match, not a mismatch.

Velocities, durations and pedal are deliberately ignored: they are what a human adds,
not what identifies the piece.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .alignment import Alignment, align
from .loader import MidiData

MATCH_TOLERANCE = 0.25  # seconds either side of the warped onset
CHANCE_SHIFTS = (-4.0, -2.5, -1.7, -1.0, 1.0, 1.7, 2.5, 4.0)  # seconds, for the chance baseline

# Fine-alignment similarity is rescaled so unrelated music -> 0 and a clean match -> 1.
ALIGNMENT_FLOOR, ALIGNMENT_CEIL = 0.40, 0.80
# Alignment quality is the most discriminating signal on the benchmark (tools/benchmark.py).
# Recall outweighs precision: the cleaner must find every intended note, while extra human
# notes are expected (that is what gets cleaned).
WEIGHT_RECALL, WEIGHT_PRECISION, WEIGHT_ALIGNMENT = 0.3, 0.2, 0.5
EVIDENCE_NOTES = 30  # a match needs a few dozen agreeing notes before it counts in full
LIKELY_MATCH, UNCERTAIN_MATCH = 0.60, 0.30
FULL_COVERAGE = 0.9  # coverage below this is called out as a partial take
CLEAN_HUMAN = 0.65  # corrected precision below this (intact takes stay >= 0.72) is flagged


@dataclass(frozen=True)
class MatchResult:
    confidence: float  # 0..1 identity confidence, the headline number
    verdict: str
    # Evidence, each 0..1
    alignment_similarity: float  # mean key-level cosine similarity along the fine DTW path
    coarse_similarity: float  # same, for the pitch-class first pass
    perfect_recall: float  # chance-corrected: covered perfect notes found in the human file
    human_precision: float  # chance-corrected: human notes found in the perfect file
    raw_recall: float
    raw_precision: float
    chance_recall: float  # note-match rate expected from an unrelated alignment
    chance_precision: float
    # Structure
    coverage: float  # fraction of the perfect notes that fall inside an aligned stretch
    segments: int  # aligned stretches; more than 1 means the human skipped/replayed material
    # Context for display
    note_ratio: float  # human notes / perfect notes
    span_ratio: float  # human length / perfect length
    match_tolerance: float  # seconds


def note_hits(src_times: np.ndarray, src_pitches: np.ndarray,
              dst_times: np.ndarray, dst_pitches: np.ndarray, tol: float) -> np.ndarray:
    """Per source note: is there a same-pitch destination onset within tol seconds?"""
    hit = np.zeros(len(src_times), bool)
    for pitch in np.unique(src_pitches):
        sel = src_pitches == pitch
        d = dst_times[dst_pitches == pitch]
        if len(d) == 0:
            continue
        d = np.concatenate(([-np.inf], np.sort(d), [np.inf]))  # sentinels bound the lookup
        s = src_times[sel]
        idx = np.searchsorted(d, s)
        hit[sel] = np.minimum(np.abs(d[idx] - s), np.abs(d[idx - 1] - s)) <= tol
    return hit


def _perfect_found(al: Alignment, perfect: MidiData, human: MidiData, shift: float
                   ) -> tuple[np.ndarray, np.ndarray]:
    """(found, covered) per perfect note. A replayed passage may be reached via several segments."""
    t = perfect.onsets - perfect.start
    human_t = human.onsets - human.start
    found = np.zeros(len(t), bool)
    covered = np.zeros(len(t), bool)
    for mask, mapped in al.perfect_to_human(t):
        if mask.any():
            covered |= mask
            found[mask] |= note_hits(mapped[mask] + shift, perfect.pitches[mask],
                                     human_t, human.pitches, MATCH_TOLERANCE)
    return found, covered


def _human_found(al: Alignment, perfect: MidiData, human: MidiData, shift: float) -> np.ndarray:
    human_t = human.onsets - human.start
    mapped, covered = al.human_to_perfect(human_t)
    hits = note_hits(mapped + shift, human.pitches, perfect.onsets - perfect.start,
                     perfect.pitches, MATCH_TOLERANCE)
    return hits & covered


def _chance_corrected(raw: float, chance: float) -> float:
    return float(np.clip((raw - chance) / max(1.0 - chance, 1e-9), 0.0, 1.0))


def verdict_for(confidence: float, coverage: float, precision: float) -> str:
    if confidence < UNCERTAIN_MATCH:
        return "Unlikely to be a match"
    if confidence < LIKELY_MATCH:
        return "Uncertain - review before cleaning"
    caveats = []
    if coverage < FULL_COVERAGE:
        caveats.append(f"covers {coverage:.0%} of the source")
    if precision < CLEAN_HUMAN:
        caveats.append(f"only {precision:.0%} of the human's notes match")
    return "Likely a match" + (f" ({'; '.join(caveats)})" if caveats else "")


def score(perfect: MidiData, human: MidiData, coarse: Alignment, fine: Alignment) -> MatchResult:
    found, covered = _perfect_found(fine, perfect, human, 0.0)
    coverage = float(covered.mean())
    n_covered = int(covered.sum())
    raw_recall = float(found[covered].mean()) if n_covered else 0.0
    found_h = _human_found(fine, perfect, human, 0.0)
    raw_precision = float(found_h.mean())

    # Chance level: the same tests with the alignment deliberately knocked off by seconds.
    chance_recall = chance_precision = 0.0
    if n_covered:
        chance_recall = float(np.mean([
            _perfect_found(fine, perfect, human, s)[0][covered].mean() for s in CHANCE_SHIFTS]))
    chance_precision = float(np.mean([_human_found(fine, perfect, human, s).mean()
                                      for s in CHANCE_SHIFTS]))

    recall = _chance_corrected(raw_recall, chance_recall)
    precision = _chance_corrected(raw_precision, chance_precision)
    alignment_score = float(np.clip(
        (fine.similarity - ALIGNMENT_FLOOR) / (ALIGNMENT_CEIL - ALIGNMENT_FLOOR), 0.0, 1.0))
    evidence = min(float(found[covered].sum()), float(found_h.sum()))
    sample = evidence / (evidence + EVIDENCE_NOTES)
    confidence = sample * (WEIGHT_RECALL * recall + WEIGHT_PRECISION * precision
                           + WEIGHT_ALIGNMENT * alignment_score)

    return MatchResult(
        confidence=confidence,
        verdict=verdict_for(confidence, coverage, precision),
        alignment_similarity=fine.similarity,
        coarse_similarity=coarse.similarity,
        perfect_recall=recall,
        human_precision=precision,
        raw_recall=raw_recall,
        raw_precision=raw_precision,
        chance_recall=chance_recall,
        chance_precision=chance_precision,
        coverage=coverage,
        segments=len(fine.segments),
        note_ratio=human.note_count / perfect.note_count,
        span_ratio=human.span / max(perfect.span, 1e-3),
        match_tolerance=MATCH_TOLERANCE,
    )


def compare(perfect: MidiData, human: MidiData) -> MatchResult:
    coarse, fine = align(perfect, human)
    return score(perfect, human, coarse, fine)


def analyze(perfect: MidiData, human: MidiData) -> tuple[MatchResult, Alignment]:
    """Match confidence plus the fine alignment, which the cleaner builds on."""
    coarse, fine = align(perfect, human)
    return score(perfect, human, coarse, fine), fine
