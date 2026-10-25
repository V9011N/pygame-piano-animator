"""
impose.py - Impose a perfect source on the loaded MIDI (the Human MIDI Cleaner's method).

The perfect source (a clean MIDI of the written music) and the loaded file (a
performance of it) are aligned with the Cleaner's jump-aware DTW; its match
confidence says whether they are the same piece. Only then is anything
offered: the performed notes the source doesn't contain are planned for
removal, the written notes the performance lacks for adding (in the
performer's own timing and dynamics), each of which can be declined. Applying
is one undoable edit. Nothing else in the file changes - and every note that
stays keeps its fingering; with "copy fingering" a note without any takes its
source note's.

The analysis runs in a worker process (`analyze`, `replan` are plain
functions on picklable data); ImposeSession is what the editor keeps.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .cleaner import cleaning as cl
from .cleaner.confidence import LIKELY_MATCH, UNCERTAIN_MATCH, analyze as _analyze
from .cleaner.loader import MidiData
from .document import DRUM_CHANNEL, Note

MIN_NOTES = 8

OUTSIDE, MATCHED, DISPLACED, UNTRUSTED, REMOVE, ADD, HELD = (cl.OUTSIDE, cl.MATCHED, cl.DISPLACED, cl.UNTRUSTED,
                                                             cl.REMOVE, cl.ADD, cl.HELD)


def midi_data(doc, origin=None):
    """(MidiData, [note id per row]): the document's pitched notes for the Cleaner, in seconds."""
    notes = [n for n in doc.notes.values() if n.channel != DRUM_CHANNEL and n.end > n.start]
    on, off = doc.note_seconds(notes)
    pitches = np.array([n.pitch for n in notes], dtype=int)
    order = np.lexsort((pitches, on)) if notes else np.zeros(0, int)
    k = len(notes)
    data = MidiData(
        path=Path(doc.path or doc.title),
        pitches=pitches[order],
        onsets=np.asarray(on, float)[order],
        offsets=np.maximum(np.asarray(off, float), np.asarray(on, float))[order],
        velocities=np.array([n.velocity for n in notes], dtype=int)[order] if k else np.zeros(0, int),
        tracks=np.array([n.track for n in notes], dtype=int)[order] if k else np.zeros(0, int),
        channels=np.array([n.channel for n in notes], dtype=int)[order] if k else np.zeros(0, int),
        on_events=np.zeros(k, int),
        off_events=np.zeros(k, int),
        origin=origin,
    )
    return data, [notes[i].id for i in order]


def analyze(perfect, human):
    """(MatchResult, Alignment, CleaningPlan) for two MidiData (the worker's job)."""
    result, alignment = _analyze(perfect, human)
    plan = cl.plan_cleaning(perfect, human, alignment)
    return result, alignment, plan


def replan(perfect, human, alignment):
    """A new CleaningPlan after the human notes changed, on the same alignment."""
    return cl.plan_cleaning(perfect, human, alignment)


def analyze_job(perfect, human, human_ids):
    """The worker's first job: (MatchResult, Alignment, CleaningPlan, human_ids)."""
    result, alignment, plan = analyze(perfect, human)
    return result, alignment, plan, human_ids


def replan_job(perfect, human, alignment, human_ids):
    """The worker's job after edits: (CleaningPlan, human_ids)."""
    return replan(perfect, human, alignment), human_ids


def verdict_color_level(confidence):
    return "good" if confidence >= LIKELY_MATCH else "uncertain" if confidence >= UNCERTAIN_MATCH else "bad"


class ImposeSession:
    """An imposed perfect source and what it proposes for the loaded document."""

    def __init__(self, source_doc, source_bytes, result, alignment, plan, perfect_data, perfect_ids,
                 human_ids, human_origin, plan_rev=None, name=None):
        self.source = source_doc                 # read-only
        self.name = name or source_doc.title
        self.plan_rev = plan_rev                 # the document's notes_rev the plan was made for
        self.source_bytes = source_bytes         # (kept for project files)
        self.result = result
        self.alignment = alignment
        self.perfect = perfect_data
        self.perfect_ids = perfect_ids           # perfect row -> source note id
        self.human_origin = human_origin         # the loaded file's time origin the alignment uses
        self.do_remove = True
        self.do_add = True
        self.copy_fingering = False
        self.keep = set()                        # note ids the user won't have removed
        self.skip = set()                        # perfect rows the user won't have added
        self.set_plan(plan, human_ids)
        self._warp_cache = None
        self.source_has_fingering = any(n.has_fingering for n in source_doc.notes.values())

    @property
    def allowed(self):
        return self.result.confidence >= UNCERTAIN_MATCH

    @property
    def uncertain(self):
        return self.result.confidence < LIKELY_MATCH

    def set_plan(self, plan, human_ids):
        self.plan = plan
        self.human_ids = human_ids               # human row -> note id (for this plan)
        st = plan.human_status
        self.status_of = {nid: int(st[i]) for i, nid in enumerate(human_ids)}
        self.partner_of = {nid: (int(plan.human_partner[i]), int(plan.human_segment[i]))
                           for i, nid in enumerate(human_ids) if plan.human_partner[i] >= 0}
        self.removals = [human_ids[i] for i in plan.remove]
        self.additions = list(zip(plan.add_sources, plan.add))      # [(perfect row, NewNote)]
        self.add_of = {p: nn for p, nn in self.additions}

    # ----- what will be done ----------------------------------------------------
    def accepted_removals(self, doc):
        if not self.do_remove:
            return []
        return [nid for nid in self.removals if nid not in self.keep and nid in doc.notes]

    def accepted_additions(self):
        if not self.do_add:
            return []
        return [(p, nn) for p, nn in self.additions if p not in self.skip]

    def source_note(self, p):
        return self.source.notes.get(self.perfect_ids[p])

    def fingering_copies(self, doc):
        """[(note id, source note)]: matched notes without fingering whose source note has some."""
        if not (self.copy_fingering and self.source_has_fingering):
            return []
        out = []
        for nid, (p, _) in self.partner_of.items():
            n = doc.notes.get(nid)
            src = self.source_note(p)
            if n is not None and src is not None and not n.has_fingering and src.has_fingering \
                    and self.status_of.get(nid) == MATCHED:
                out.append((nid, src))
        return out

    def pending_count(self, doc):
        return len(self.accepted_removals(doc)) + len(self.accepted_additions()) + len(self.fingering_copies(doc))

    def toggle_removal(self, nid):
        (self.keep.discard if nid in self.keep else self.keep.add)(nid)

    def toggle_addition(self, p):
        (self.skip.discard if p in self.skip else self.skip.add)(p)

    def apply(self, doc):
        """Make the accepted edits (one undoable step). Returns (removed, added, fingered)."""
        rm = self.accepted_removals(doc)
        add = self.accepted_additions()
        fing = self.fingering_copies(doc)
        if not (rm or add or fing):
            return 0, 0, 0
        tm = doc.tempo_map()
        off_status, off_vel = doc.release_style()
        doc.begin("Impose perfect source")
        for nid in rm:
            doc.remove_note(nid)
        for p, nn in add:
            start = max(0, int(round(tm.tick(nn.onset))))
            end = max(start + 1, int(round(tm.tick(nn.onset + nn.duration))))
            src = self.source_note(p) if self.copy_fingering else None
            doc.put_note(Note(doc.new_id(), nn.track, nn.channel, nn.pitch, start, end, nn.velocity,
                              off_velocity=off_vel, off_status=off_status,
                              hand=src.hand if src else None, finger=src.finger if src else None,
                              gliss=src.gliss if src else False))
        from .document import note_with
        for nid, src in fing:
            n = doc.notes.get(nid)
            if n is not None and not n.has_fingering:
                doc.put_note(note_with(n, hand=src.hand, finger=src.finger, gliss=src.gliss))
        doc.commit()
        self.keep.clear()
        self.skip.clear()
        return len(rm), len(add), len(fing)

    # ----- the source in the loaded file's time ------------------------------------
    def warped(self, doc):
        """
        The source's notes laid onto the loaded file's ticks through the alignment, per aligned stretch:
        dict with arrays start, end (ticks), pitch, row (perfect row), seg, sorted by start; and
        `at` {(row, seg): index}. Recomputed when the loaded file's tempo changes.
        """
        key = doc.timing_rev
        if self._warp_cache is not None and self._warp_cache[0] == key:
            return self._warp_cache[1]
        pd = self.perfect
        rel_on = pd.onsets - pd.start
        rel_off = pd.offsets - pd.start
        tm = doc.tempo_map()
        starts, ends, pitches, rows, segs = [], [], [], [], []
        for k, s in enumerate(self.alignment.segments):
            m = np.flatnonzero((rel_on >= s.p_lo) & (rel_on <= s.p_hi))
            if not len(m):
                continue
            on = np.interp(rel_on[m], s.p_knots, s.h_of_p) + self.human_origin
            off = np.interp(np.minimum(rel_off[m], s.p_hi), s.p_knots, s.h_of_p) + self.human_origin
            off = np.maximum(off, on + 0.03)
            starts.append(tm.to_ticks(on))
            ends.append(tm.to_ticks(off))
            pitches.append(pd.pitches[m])
            rows.append(m)
            segs.append(np.full(len(m), k))
        if starts:
            st = np.concatenate(starts)
            order = np.argsort(st, kind="stable")
            w = {"start": np.round(st[order]).astype(np.int64),
                 "end": np.round(np.concatenate(ends)[order]).astype(np.int64),
                 "pitch": np.concatenate(pitches)[order].astype(np.int16),
                 "row": np.concatenate(rows)[order],
                 "seg": np.concatenate(segs)[order]}
        else:
            w = {"start": np.zeros(0, np.int64), "end": np.zeros(0, np.int64), "pitch": np.zeros(0, np.int16),
                 "row": np.zeros(0, int), "seg": np.zeros(0, int)}
        w["at"] = {(int(r), int(g)): i for i, (r, g) in enumerate(zip(w["row"], w["seg"]))}
        w["max_len"] = int((w["end"] - w["start"]).max()) if len(w["start"]) else 0
        self._warp_cache = (key, w)
        return w

    def add_ticks(self, doc, nn):
        tm = doc.tempo_map()
        start = int(round(tm.tick(nn.onset)))
        return start, max(start + 1, int(round(tm.tick(nn.onset + nn.duration))))

    def summary(self, doc):
        r = self.result
        return {
            "confidence": r.confidence, "verdict": r.verdict, "coverage": r.coverage, "segments": r.segments,
            "matched": int(self.plan.matched), "displaced": int(self.plan.displaced),
            "untrusted": int(self.plan.untrusted),
            "remove": len(self.removals), "add": len(self.additions),
            "remove_on": len(self.accepted_removals(doc)), "add_on": len(self.accepted_additions()),
        }
