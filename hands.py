"""
hands.py - Procedural hand skeletons for the piano animator (both hands).

The model is a right hand. The left hand is the same model on a mirrored
keyboard: pitches are reflected about D4 (the keyboard is symmetric there),
planned and posed as a right hand, and the finished skeleton is flipped back.

How it works
  1. fingering.plan_fingering() picks a finger (1 = thumb ... 5 = little
     finger) for every note that the file doesn't already finger: standard
     fingerings for recognised figures (scales, arpeggios, octaves, thirds...,
     see figures.py), a pianist's cost model and physical limits (hand span,
     held keys, speed of shifts), searched over the whole piece.
  2. HandAnimator.pose(t, keyboard) builds a 3D skeleton for time t:
       - the palm is rigid; its position AND turn are solved for every
         moment from the keys around it: keys being pressed must be reachable,
         keys the fingers are heading for pull the hand toward them, keys just
         let go of fade out. A finger that would need more splay than its
         joint allows turns the hand at the wrist first (within the wrist's
         range) and slides it second. The motion is smoothed over a short
         window, so shifts accelerate and settle;
       - every fingertip follows its own timeline: press, lift, travel to its
         next key as soon as it is free (arriving early and hovering), strike.
         Idle fingers fan out between their busy neighbours, so the whole
         hand keeps changing shape with the notes;
       - each fingertip is kept inside its finger's splay and reach range,
         then solved with planar inverse kinematics. Fingers 2-5 flex downward
         (DIP joint coupled to PIP), so seen from above their segments
         accordion in and out as they curl. The thumb flexes on its side, so
         its bend shows as a sideways curl.
  3. draw_hand() projects the skeleton straight down onto the screen and draws
     black bone segments, lowest bones first so crossings overlap correctly.

World coordinates used for posing are pixels:
    X = screen x,  Y = distance up the screen from the keyboard's front edge,
    Z = height above the key surface.
Scale comes from the drawn keyboard (6.5 in per octave) and the hand is sized
so a firm, comfortable thumb-to-little-finger stretch spans a 9th (C to the D
an octave up); that makes it about 7.6 in from wrist to middle fingertip.
"""
from __future__ import annotations

import bisect
import math

from fingering import group_notes, is_crossing, key_pos, mirror_pitch, plan_fingering
from midi_loader import RIGHT, is_black_key

# --------------------------------------------------------------------------- #
# Real-world sizes
# --------------------------------------------------------------------------- #
OCTAVE_IN = 6.5
WHITE_KEY_IN = OCTAVE_IN / 7.0
HAND_SPAN_IN = 8 * WHITE_KEY_IN          # 9th: key centres C .. D an octave up
KEY_TRAVEL_IN = 0.4                      # how far a key goes down

# Where on the key each finger lands, in inches back from the white keys' front
# edge. The curve of the fingertips puts 3 furthest in and the thumb nearest.
WHITE_DEPTH_IN = {1: 0.40, 2: 1.20, 3: 1.45, 4: 1.30, 5: 0.90}
BOUNCE_MAX_IN = 1.2                      # wrist bounce height at 100%, for chords 0.3 s+ apart
ROLL_MAX_DEG = 40.0                      # forearm rotation each way in a tremolo, at 100%
GESTURE_REPEAT_T = 0.45                  # repeated chords closer than this bounce from the wrist
GESTURE_KEY_UP_T = 0.04                  # keys springing back as the hand lifts off them
GESTURE_TREMOLO_T = 0.3                  # alternations quicker than this rotate the forearm
BLACK_DEPTH_IN = 0.35                    # distance in from the black key's front edge
# How far along a key a fingertip may slide from those spots when the hand
# can't reach them (a white key between black ones is played further in):
WHITE_SPAN_IN = (0.3, 0.45)              # from the front edge / past the black keys' front
BLACK_SPAN_IN = (0.25, 1.6)              # in from the black key's front edge
# Among black keys (chromatic octaves, say) white keys are played further up,
# just past the black keys' front, so the hand stays in instead of moving in
# and out: fully within WHITE_UP_T of a chord with a black key, fading out
# by WHITE_UP_FADE_T.
WHITE_UP_IN = 0.2                        # past the black keys' front
# Within the pianist's playing area on a key, a loud note is played nearer
# its front (more leverage): up to VEL_SOFT it may use the whole area; from
# VEL_LOUD no further up than its finger's usual spot plus LOUD_MARGIN_IN
# (white, black: the black keys already sit well in, and long fingers on
# them next to white keys need the room); in between linearly.
VEL_SOFT = 50
VEL_LOUD = 110
LOUD_MARGIN_IN = (0.25, 0.8)
WHITE_UP_T = 0.3                         # s
WHITE_UP_FADE_T = 0.6                    # s

# --------------------------------------------------------------------------- #
# Hand model: right hand, palm down, units are roughly centimetres.
# Origin = centre of the wrist, +x toward the little finger, +y toward the
# fingertips, +z up. It is rescaled to the 9th span below.
# --------------------------------------------------------------------------- #
WRIST_Z = 3.4
KNUCKLE_Z = 4.2

WRIST_SIDES = ((-2.2, 0.0, WRIST_Z), (2.2, 0.0, WRIST_Z))      # radius side, ulna side
THUMB_CMC = (-2.5, 1.6, WRIST_Z - 0.7)
MC_BASE = {2: (-1.4, 1.9, WRIST_Z + 0.5), 3: (-0.3, 2.0, WRIST_Z + 0.6),
           4: (0.9, 1.8, WRIST_Z + 0.5), 5: (1.9, 1.4, WRIST_Z + 0.3)}
# MCP joints (the big knuckles); the middle one sits highest (transverse arch)
MCP = {2: (-2.9, 8.4, KNUCKLE_Z + 0.1), 3: (-0.7, 8.6, KNUCKLE_Z + 0.3),
       4: (1.4, 8.1, KNUCKLE_Z + 0.1), 5: (3.3, 7.2, KNUCKLE_Z - 0.3)}
# Bone lengths. Fingers: proximal, middle, distal phalanx.
# Thumb: metacarpal, proximal, distal.
BONES = {1: (4.7, 3.3, 2.8), 2: (4.0, 2.3, 1.8), 3: (4.5, 2.7, 1.9),
         4: (4.2, 2.6, 1.9), 5: (3.3, 1.8, 1.7)}

# The span is measured with the hand spread over the keys as a pianist
# stretches it (from a photo of a real hand): the thumb swung 72 deg out,
# nearly flat along the keys, and the little finger 45 deg out, the middle
# three fingers close together. (It was 50 / 22 deg, which made the hand
# 27% bigger than its span says - it never looked stretched.)
_THUMB_MAX_ABD = math.radians(72)
_PINKY_MAX_ABD = math.radians(45)

# Fingertip heights (model units) above the keys
HOVER = {1: 0.9, 2: 1.4, 3: 1.4, 4: 1.4, 5: 1.4}      # resting
PREP = {1: 1.3, 2: 2.6, 3: 2.6, 4: 2.6, 5: 2.4}       # raised, ready to strike

# Joint behaviour
FINGER_COUPLING = 0.75      # DIP bends ~3/4 as much as PIP
FINGER_BEND_MAX = 1.7       # rad, PIP limit
THUMB_COUPLING = 0.85
THUMB_BEND_MAX = 1.2

# Timing (seconds)
PRESS_T = 0.035             # key going down after the strike
RELEASE_T = 0.12            # lift after a note ends
STRIKE_T = 0.12             # final drop onto the key
PREP_MAX_T = 0.9            # never start preparing earlier than this
LINGER_T = 0.06             # a released finger stays over its key this long
RETURN_T = 0.35             # ... then drifts back to its resting spot
# How strongly each finger pulls the hand toward its natural spot when a chord
# is wider than the resting hand: the thumb is flexible and takes most of the
# stretch, the little finger barely abducts, so it stays near its rest position.
FIT_WEIGHT = {1: 0.35, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.6}
EARLY_LIFT_T = 0.22         # a finger that jumps to a new key leaves the old one this early
LEGATO_STEP_WK = 2.5        # a finger moving this many white keys or less lets go only LEGATO_LIFT_T early...
LEGATO_LIFT_T = 0.03
JUMP_WK = 6.0               # ...and the full early release from this far
HAND_MOVE_TOL_WK = 0.25     # a hand shift smaller than this isn't a trip (held keys stay down)
MIN_HOLD_T = 0.04           # ...but holds its key at least this long (or half the time to the next)
STRIKE_MIN_T = 0.03         # the shortest final drop onto a key (a finger arriving at the top speed)
MAX_DELAY_T = 0.25          # a chord the hand can't reach in time is struck at most this late
CROSS_LIFT_T = 0.07         # ...and a key held across a wide thumb crossing is let go this early

# Joint limits. Splay is the angle of the knuckle->fingertip line in the hand's
# own frame, 0 = straight ahead, + = toward the little finger. The thumb's is
# measured from its base (CMC); it swings far out and can tuck under the palm.
SPLAY_LIMIT_DEG = {1: (-85, 34), 2: (-36, 14), 3: (-26, 26), 4: (-22, 24), 5: (-12, 55)}
# ...the thumb's and little finger's full stretch is only for keys they hold or are about to strike;
# in the air they keep to a comfortable spread (blended by _key_weight)
SPLAY_COMFORT_DEG = {1: (-64, 34), 2: (-24, 10), 3: (-18, 18), 4: (-15, 16), 5: (-8, 24)}
PRESS_SLACK_DEG = {1: 6, 2: 6, 3: 6, 4: 6, 5: 3}   # extra splay only while holding a key down
# The fingertip stays between these shares of the finger's length in front of
# its knuckle (no curling back under the hand, no locking straight).
REACH_MIN = {1: 0.55, 2: 0.32, 3: 0.32, 4: 0.32, 5: 0.36}
REACH_COMFORT = {1: 0.97, 2: 0.9, 3: 0.9, 4: 0.9, 5: 0.9}
# How far the hand may turn away from the forearm's natural line (wrist
# deviation): clockwise (toward the little finger), counter-clockwise.
WRIST_DEV_DEG = (-26, 8)
YAW_PRIOR = 0.25            # how strongly the hand prefers the forearm's natural turn
CROSS_TURN_DEG = 14         # extra turn toward the little finger while the thumb crosses under
CROSS_PRIOR = 4.0           # ...and how firmly the hand keeps to it
SOFT_K = 1.0                # pull of the natural hand shape...
LIMIT_K = 400.0             # ...versus the push of a finger past its limits
GN_ITERS = 8
GN_DAMPING = 0.05
HAND_SMOOTH_T = 0.07        # the hand's motion is averaged over +-this many seconds
HAND_GRID_T = 1 / 120       # ...from poses solved on this time grid

# Anticipation
ANTIC_T = 0.4               # the hand starts leaning toward a note this long before it
RELEASE_HOLD_T = 0.25       # ...and stops caring about a released key over this long
TRAVEL_SHARE = 0.6          # a free finger reaches its next key in this share of the time it has
ANTIC_HELD = 0.3            # how much a finger still holding a key leans toward its next one
RELEASE_NEED_T = 0.08       # a released key stops needing to be reachable this soon...
RELEASE_NEED = 1.0          # ...fading from this much
NEED_T = 0.15               # a finger must be able to reach its next key from this long before the strike
VOTE_POWER = 16             # reach limits count for pressed keys and ones about to be struck, not faint ones
IDLE_W = 0.05               # faint memory of the last keys so an idle hand stays put
# A hand with nothing to play gets out of the other hand's way and loosely
# follows it around, so the hands only cross when the notes make them.
IDLE_AFTER_T = 0.35         # a hand starts counting as idle this long after its last key is let go...
IDLE_RAMP_T = 0.5           # ...and is fully idle this much later
IDLE_BEFORE_T = 1.1         # it stops being idle this long before its next note...
IDLE_READY_T = 0.35         # ...and is back at work from this long before it
IDLE_CLEAR_SPAN = 0.8       # an idle hand keeps its wrist this many hand spans clear of the playing one...
IDLE_FAR_SPAN = 1.5         # ...and drifts after it once it is further off than this
IDLE_TRAIL_T = 1.2          # s, it follows where the playing hand has been over this long
IDLE_LOOK_T = 0.5           # s, and clears where it is about to be...
IDLE_DRIFT_IN = 6.0         # in/s, ...drifting back no faster than this once it has passed
IDLE_MEMORY_T = 8.0         # s, long enough to drift back across the whole keyboard
IDLE_TICK_T = 0.1           # the playing hand's path is sampled this often
IDLE_EDGE_IN = 1.5          # in, it isn't pushed further than this inside the keyboard's end
IDLE_GRID_T = 1 / 30        # the shift is solved on this time grid...
IDLE_SMOOTH_T = 0.25        # ...and averaged over +-this many seconds
# After smoothing, the hand is nudged so every finger on (or about to strike)
# its key can reach it inside its joint limits - the fingertip lands squarely
# on the key instead of being clamped off it.
KEY_FIX_T = 0.12            # s, a finger's key starts counting this long before the strike...
KEY_FIX_RELEASE_T = 0.05    # s, ...and stops this soon after it is let go (the finger lifts away)
KEY_FIX_K = 12.0            # how much a key being held outweighs keeping the smoothed hand where it was
KEY_FIX_MARGIN_DEG = 1.0    # stay this far inside the splay limits...
KEY_FIX_MARGIN = 0.02       # ...and this share of the finger's length inside its reach range
KEY_FIX_ITERS = 6
# the choice of spot along a key: (out-of-reach weight, softness in inches),
# firm on the key and soft while a finger travels there (blended by how far
# it still has to go)
KEY_SPOT_FIRM = (20.0, 0.03)
KEY_SPOT_SOFT = (4.0, 0.12)
TIP_ARM_IN = 7.0            # in, how far the fingertips swing out from the wrist when the hand turns
LIMIT_RESTART_T = 0.5       # s, the speed limit's chain restarts this far back after a seek
LIMIT_GRID_T = 1 / 60       # s, the speed limit steps on this grid (linear in between)
LIMIT_SMOOTH_HAND = 3       # grid steps each side the limited hand is averaged over...
LIMIT_SMOOTH_HAND_ON = 1    # ...and while its fingers are on keys
LIMIT_SMOOTH_TIPS = 3       # ...and the fingertips
# Scale runs: the wrist glides and the fingers do the crossing
RUN_MIN_NOTES = 7           # single notes moving by step, at least this many in a row...
RUN_GAP_T = 0.3             # s, ...none further apart than this
RUN_RAMP_T = 0.15           # s, the run's hold on the hand eases in and out over this
RUN_GLIDE_T = 0.25          # s, in a run the wrist is averaged over +-this (the crossings' steps even out)
RUN_COMPRESS_DEG = {1: (0, 0), 2: (0, 10), 3: (6, 6), 4: (8, 0), 5: (10, 0)}   # extra splay toward the hand's middle
# Glissandos (glissando.py): the hand slides the backs of its fingers along the keys
GLISS_RAMP_T = 0.15         # s, the hand forms the glissando pose this long before / leaves it after
GLISS_YAW_DEG = 70.0        # the hand turns so its fingers trail the way it slides...
GLISS_ARM_SHARE = 0.6       # ...the forearm turning with it this far (the wrist bends the rest)
GLISS_ROLL_DEG = 180.0      # turned over, palm up: the backs of the fingers (the nails) slide on the keys
GLISS_PITCH_DEG = 8.0       # ...tipped down a little toward the fingertips
GLISS_SQUEEZE = 0.8         # the knuckles drawn together: the fingers side by side, touching
GLISS_FINGER_DIR = (0.0, 1.0, -0.12)   # the fingers straight and parallel, a little down toward the tips
GLISS_THUMB_TIP = (-1.6, 2.6, -0.4)    # the thumb tucked in beside the index finger (from its knuckle, model units)
# Sliding toward the thumb (RH down, LH up) the thumb does it instead: the hand palm down, fingers 2-5
# curled right in, the thumb straight out along the keys, its nail on them
GLISS_CURL = (0.0, 1.4, -3.6)          # a curled fingertip, from its knuckle (model units): a fist, the middle joints down on the keys
GLISS_THUMB_DIR = (-0.18, 1.0, -0.3)   # the straightened thumb, along the fist (the hand turns to lay it along the keys)
GLISS_THUMB_PITCH_DEG = 4.0            # the hand tipped down this much for it
GLISS_WHITE_IN = 0.8        # in, the nails slide this far up the white keys (well clear of the black ones)...
GLISS_BLACK_IN = 0.5        # ...and this far in from the black keys' front
GLISS_THUMB_IN = 2.0        # in, with the thumb: its nail this far up the white keys, the fist's knuckles over them
FLAT_SPAN_WK = (4.5, 6.5)   # keys held this wide (white keys, outermost) start / fully flatten the hand...
FLAT_DROP = 0.6             # ...which lowers its knuckles by this share, so stretched fingers reach further
TIP_GAP_WK = 0.6            # neighbouring fingertips (2-5) keep at least this far apart in a run
SHAPE_FALLOFF = 0.6         # idle fingers follow a busy neighbour by this much per finger


def _model_span():
    tx = THUMB_CMC[0] - sum(BONES[1]) * math.sin(_THUMB_MAX_ABD)
    ty = THUMB_CMC[1] + sum(BONES[1]) * math.cos(_THUMB_MAX_ABD)
    px = MCP[5][0] + sum(BONES[5]) * math.sin(_PINKY_MAX_ABD)
    py = MCP[5][1] + sum(BONES[5]) * math.cos(_PINKY_MAX_ABD)
    return math.hypot(px - tx, py - ty)


INCHES_PER_UNIT = HAND_SPAN_IN / _model_span()

# --------------------------------------------------------------------------- #
# Small vector helpers (3-tuples)
# --------------------------------------------------------------------------- #
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _norm(a): return math.sqrt(_dot(a, a))
def _lerp(a, b, s): return a + (b - a) * s
def _lerp3(a, b, s): return tuple(_lerp(x, y, s) for x, y in zip(a, b))


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)          # smootherstep


def loudness(velocity):
    """0 (soft, VEL_SOFT and below) .. 1 (loud, VEL_LOUD and above)."""
    return min(1.0, max(0.0, (velocity - VEL_SOFT) / (VEL_LOUD - VEL_SOFT)))


def busy_spans(intervals):
    """Sorted, merged (start, end) spans in which a hand is holding keys."""
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def idle_weight(spans, starts, t):
    """
    0..1 how idle a hand is at t: 0 while it holds a key or is about to play
    (from IDLE_READY_T before its next note), 1 once it has been free for
    IDLE_AFTER_T + IDLE_RAMP_T and has nothing coming within IDLE_BEFORE_T.
    `starts` is [s for s, _ in spans].
    """
    i = bisect.bisect_right(starts, t) - 1
    if i >= 0 and t < spans[i][1]:
        return 0.0
    last = spans[i][1] if i >= 0 else -math.inf
    nxt = spans[i + 1][0] if i + 1 < len(spans) else math.inf
    a = _smooth((t - last - IDLE_AFTER_T) / IDLE_RAMP_T)
    b = _smooth((nxt - t - IDLE_READY_T) / (IDLE_BEFORE_T - IDLE_READY_T))
    return a * b


def pair_hands(animators):
    """Let two hands (HandAnimators) know about each other, so an idle one keeps out of the other's way."""
    anims = list(animators)
    for a in anims:
        a.partner = next((b for b in anims if b is not a and b.hand != a.hand), None)
        a._idle_cache, a._clear_cache, a._path_cache = {}, {}, {}
        a._limit_cache, a._tip_limit_cache = {}, {}
        a._smooth_hand_cache, a._smooth_tip_cache = {}, {}


def _blend_pose(a, b, w):
    """Pose a moved toward pose b by w (0..1): every point of the skeleton, the wrist and the hand's structure."""
    L = lambda p, q: tuple(x + (y - x) * w for x, y in zip(p, q))
    out = dict(a)
    out["bones"] = [(L(p, p2), L(q, q2), k) for (p, q, k), (p2, q2, _) in zip(a["bones"], b["bones"])]
    out["joints"] = [(L(p, p2), k) for (p, k), (p2, _) in zip(a["joints"], b["joints"])]
    out["wrist"] = L(a["wrist"], b["wrist"])
    sa, sb = a["struct"], b["struct"]
    out["struct"] = {"chains": {f: [L(p, q) for p, q in zip(sa["chains"][f], sb["chains"][f])] for f in sa["chains"]},
                     "wrist": tuple(L(p, q) for p, q in zip(sa["wrist"], sb["wrist"])),
                     "arm_end": L(sa["arm_end"], sb["arm_end"]), "mirror": sa["mirror"],
                     # nails hidden in either pose stay hidden once the blend is a third of the way there
                     "nail_hide": tuple(sorted(set(sa.get("nail_hide", ())) |
                                               (set(sb.get("nail_hide", ())) if w > 0.33 else set()))),
                     "palm_up": (sb if w > 0.5 else sa).get("palm_up", False)}
    return out


def _ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) * (1 - x)


# --------------------------------------------------------------------------- #
# A particular hand: the model above with a pianist's bone lengths
# --------------------------------------------------------------------------- #
class HandGeometry:
    """
    The hand model with a pianist's anatomy (pianist.DEFAULT_ANATOMY ids ->
    lengths in model units). The wrist and the bases of the metacarpals stay
    where they are; each knuckle moves along its metacarpal to match its
    length, and the phalanges take their lengths directly.
    """

    def __init__(self, anatomy=None):
        from pianist import DEFAULT_ANATOMY
        a = dict(DEFAULT_ANATOMY)
        a.update(anatomy or {})
        self.anatomy = a
        self.thumb_cmc = THUMB_CMC
        self.mc_base = MC_BASE
        self.wrist_sides = WRIST_SIDES
        self.mcp = {}
        for f in range(2, 6):
            d = _sub(MCP[f], MC_BASE[f])
            self.mcp[f] = _add(MC_BASE[f], _mul(d, a[f"mc{f}"] / _norm(d)))
        self.bones = {1: (a["mc1"], a["pp1"], a["dp1"])}
        for f in range(2, 6):
            self.bones[f] = (a[f"pp{f}"], a[f"mp{f}"], a[f"dp{f}"])
        # the relaxed fingertips sit this far in front of the middle knuckle
        self.rest_reach = 4.2 * sum(self.bones[3]) / sum(BONES[3])

    def base(self, f):
        return self.thumb_cmc if f == 1 else self.mcp[f]

    def span_units(self):
        """Comfortable thumb-to-little-finger stretch (the 9th of the default hand)."""
        tx = THUMB_CMC[0] - sum(self.bones[1]) * math.sin(_THUMB_MAX_ABD)
        ty = THUMB_CMC[1] + sum(self.bones[1]) * math.cos(_THUMB_MAX_ABD)
        px = self.mcp[5][0] + sum(self.bones[5]) * math.sin(_PINKY_MAX_ABD)
        py = self.mcp[5][1] + sum(self.bones[5]) * math.cos(_PINKY_MAX_ABD)
        return math.hypot(px - tx, py - ty)

    def pair_reach(self, a, b):
        """How far apart fingertips a < b can comfortably get (model units, flat hand)."""
        def tip(f, outward):
            if f == 1:
                ang = -_THUMB_MAX_ABD
            elif f == 5 and outward:
                ang = _PINKY_MAX_ABD
            else:
                lo, hi = SPLAY_LIMIT_DEG[f]
                ang = math.radians(hi if outward else lo)
            L = sum(self.bones[f]) * REACH_COMFORT[f]
            bx, by, _ = self.base(f)
            return bx + L * math.sin(ang), by + L * math.cos(ang)
        (ax, ay), (bx, by) = tip(a, False), tip(b, True)
        return math.hypot(bx - ax, by - ay)


def curl_factor(pianist=None):
    """
    How far in front of the knuckles the fingertips sit, relative to the
    original model, from the pianist's "Finger curvature" (1.0 = the original,
    fairly curved, at 64%; up to 1.7 at 0% - flat - down to 0.6 at 100%).
    """
    c = pianist.b("finger_curve") if pianist is not None else 0.636
    return 1.0 + 1.1 * (0.636 - c)


def hand_span_inches(anatomy=None):
    return HandGeometry(anatomy).span_units() * INCHES_PER_UNIT


_REACH_CACHE = {}


def reach_scale(anatomy=None):
    """{(a, b): this hand's reach between fingers a and b / the default hand's}."""
    key = tuple(sorted((anatomy or {}).items()))
    got = _REACH_CACHE.get(key)
    if got is None:
        g, g0 = HandGeometry(anatomy), HandGeometry()
        got = _REACH_CACHE[key] = {(a, b): g.pair_reach(a, b) / g0.pair_reach(a, b)
                                   for a in range(1, 6) for b in range(a + 1, 6)}
    return got


# --------------------------------------------------------------------------- #
# Inverse kinematics
# --------------------------------------------------------------------------- #
def solve_chain(base, target, lengths, bulge, coupling, bend_max):
    """
    Three-bone planar chain from `base` toward `target`.

    The chain lies in the plane containing base->target and the `bulge`
    direction, and its joints bow out toward `bulge` (for a finger that's up,
    so the knuckles arch while the tip curls down). Joint 2 bends by b and
    joint 3 by coupling*b; b is found so the tip lands on the target. Returns
    [base, joint1, joint2, tip]. Out-of-reach targets give a straight chain
    pointing at the target.
    """
    L1, L2, L3 = lengths
    d = _sub(target, base)
    r = _norm(d)
    if r < 1e-6:
        d, r = (0.0, 1e-6, 0.0), 1e-6
    e1 = _mul(d, 1.0 / r)
    e2 = _sub(bulge, _mul(e1, _dot(bulge, e1)))
    n = _norm(e2)
    if n < 1e-6:
        e2 = (0.0, 0.0, 1.0) if abs(e1[2]) < 0.9 else (1.0, 0.0, 0.0)
        e2 = _sub(e2, _mul(e1, _dot(e2, e1)))
        n = _norm(e2)
    e2 = _mul(e2, 1.0 / n)

    k = 1.0 + coupling

    def reach(b):
        x = L1 + L2 * math.cos(b) + L3 * math.cos(b * k)
        y = -L2 * math.sin(b) - L3 * math.sin(b * k)
        return x, y

    if r >= L1 + L2 + L3:
        b = 0.0
    elif math.hypot(*reach(bend_max)) >= r:
        b = bend_max
    else:
        lo, hi = 0.0, bend_max
        for _ in range(22):
            mid = (lo + hi) * 0.5
            if math.hypot(*reach(mid)) > r:
                lo = mid
            else:
                hi = mid
        b = (lo + hi) * 0.5

    x, y = reach(b)
    ang = -math.atan2(y, x)
    pts = [base]
    p = base
    for L, bend in ((L1, 0.0), (L2, b), (L3, b * coupling)):
        ang -= bend
        p = _add(p, _add(_mul(e1, L * math.cos(ang)), _mul(e2, L * math.sin(ang))))
        pts.append(p)
    return pts


# --------------------------------------------------------------------------- #
# Animation
# --------------------------------------------------------------------------- #
def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _solve3(A, b):
    """Solve a 3x3 linear system (Cramer's rule)."""
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    d = det(A)
    if abs(d) < 1e-12:
        return [0.0, 0.0, 0.0]
    out = []
    for i in range(3):
        m = [row[:] for row in A]
        for r in range(3):
            m[r][i] = b[r]
        out.append(det(m) / d)
    return out


class HandAnimator:
    """
    The pose is solved fresh for every frame from what the hand has to do
    around that moment, so the whole hand keeps adapting instead of jumping
    between fixed keyframes:

      * every finger's current key (pressing, weight 1), the key it just let
        go of (fading out) and the key it plays next (fading in over ANTIC_T)
        become weighted targets;
      * the wrist position AND its turn are fit to those targets (a weighted
        rigid fit with the forearm's natural turn as a prior), then corrected
        until every finger can reach its key without going past its joint
        limits - an out-of-range finger turns the hand about its knuckle
        first (wrist rotation, within WRIST_DEV_DEG) and slides it second;
      * each fingertip then follows its own timeline (press, lift, travel to
        the next key, strike). Fingers with nothing to do fan out between
        their busy neighbours, so the hand's shape follows the notes;
      * finally every fingertip is clamped to its finger's splay/reach range
        before the bones are solved, so no finger ever bends out of the hand.
    """

    def __init__(self, song, hand=RIGHT, fingering=None, pianist=None, repair=True):
        import pianist as pianists
        self.pianist = pianist or pianists.active()
        self.geo = HandGeometry(self.pianist.anatomy)
        self.skin = self.pianist.skin_settings()
        self.color = tuple(self.skin["colors"]["skeleton"]["bone"])
        self._set_behavior(self.pianist)
        self.hand = hand
        self.song = song
        # The left hand is a right hand on the mirrored keyboard: everything
        # is solved in mirrored coordinates and flipped back when drawn.
        self.mirror = hand != RIGHT
        self.vp = mirror_pitch if self.mirror else (lambda p: p)
        self._bias = 0.0
        notes = [n for n in song.notes if n.hand == hand]
        # Glissandos are slid, not fingered: their notes stay out of the
        # fingering and the fingers' timeline, and keep their times exactly.
        import glissando
        self.gliss_runs = glissando.find(notes, self.pianist)
        self.gliss_ids = {id(n) for r in self.gliss_runs for n in r}
        self.gliss_eps = glissando.episodes(self.gliss_runs, notes, float(self.pianist.b("gliss_merge")))
        notes = [n for n in notes if id(n) not in self.gliss_ids]
        self.groups = group_notes(notes, vpitch=self.vp)
        # `fingering` ({id(note): finger}) skips the planner, e.g. when the
        # fingering editor already knows every finger
        # a chord the file fingers impossibly (an octave with 4-5) is played
        # with fingers that reach (unless `repair` is off); those notes:
        self.repaired = set()
        if fingering is None:
            fingering = plan_fingering(self.groups, self.vp, hand=hand, context=song.notes,
                                       pianist=self.pianist, repair=repair, repaired=self.repaired)
        self.fingering = {id(n): fingering[id(n)] for _, ns in self.groups for n in ns
                          if fingering.get(id(n))}
        # notes without a finger (a sixth note in one hand's chord) aren't animated
        self.groups = [(t, [n for n in ns if id(n) in self.fingering]) for t, ns in self.groups]
        self.groups = [g for g in self.groups if g[1]]
        # One finger on two neighbouring keys (the thumb, or the little finger
        # on two white keys): its tip aims between them.
        from fingering import double_ok
        self.pair_key = {}
        for _, ns in self.groups:
            for a, b in zip(ns, ns[1:]):
                f = self.fingering[id(a)]
                if f == self.fingering[id(b)] and double_ok(self.vp(a.pitch), self.vp(b.pitch), f):
                    pk = (min(a.pitch, b.pitch), max(a.pitch, b.pitch))
                    self.pair_key[id(a)] = self.pair_key[id(b)] = pk

        self.by_finger = {f: [] for f in range(1, 6)}
        for _, ns in self.groups:
            for n in ns:
                self.by_finger[self.fingering[id(n)]].append(n)
        # When each note is actually struck and let go by this hand: chords too
        # wide for it are rolled, and fingers let go early when they must.
        so = self.start_of = {id(n): n.start for _, ns in self.groups for n in ns}
        eo = self.end_of = {id(n): n.end for _, ns in self.groups for n in ns}
        self.rolled = 0
        self._roll_wide_chords()
        for f in self.by_finger:
            self.by_finger[f].sort(key=lambda n: so[id(n)])
        self.finger_starts = {f: [so[id(n)] for n in ns] for f, ns in self.by_finger.items()}
        # When a finger has to jump straight to a different key it lets go of
        # the current one a little early.
        self.finger_ends = {}
        for f, ns in self.by_finger.items():
            ends = []
            for i, n in enumerate(ns):
                e, s0 = eo[id(n)], so[id(n)]
                j = i + 1
                while j < len(ns) and so[id(ns[j])] - s0 < 0.01:     # a thumb on two keys at once
                    j += 1
                if j < len(ns):
                    nx = ns[j]
                    s1 = so[id(nx)]
                    e = min(e, s1)
                    if nx.pitch != n.pitch:
                        e = min(e, max(s0 + 0.5 * (s1 - s0), s1 - self._jump_lift(n, nx)))
                ends.append(max(e, s0 + 1e-3))
            self.finger_ends[f] = ends
        # A held key that the next chord's fingers have to cross (5 landing
        # left of a held 2, say - anything but the thumb passing under) is
        # let go early too, so the hand isn't asked to do both at once.
        # So is one that a new chord is out of reach of (a leap while the
        # last notes are still down): the hand can't be on both.
        from fingering import MAX_SPAN
        starts = [g[0] for g in self.groups]
        for f, ns in self.by_finger.items():
            for i, n in enumerate(ns):
                s0 = so[id(n)]
                gi = bisect.bisect_right(starts, n.start + 1e-6)
                if gi >= len(self.groups) or starts[gi] > self.finger_ends[f][i] + 0.1:
                    continue
                for m in self.groups[gi][1]:
                    g = self.fingering[id(m)]
                    if g == f or m.pitch == n.pitch:
                        continue
                    vn, vm = self.vp(n.pitch), self.vp(m.pitch)
                    if abs(key_pos(vm) - key_pos(vn)) > MAX_SPAN[(min(f, g), max(f, g))] + 0.5:
                        lift = self.early_lift
                    elif 1 in (f, g):
                        # a wide thumb-under / finger-over: not quite legato
                        if is_crossing(vn, f, vm, g) and abs(key_pos(vm) - key_pos(vn)) > 2.0:
                            lift = CROSS_LIFT_T
                        else:
                            continue
                    elif (g > f) != (vm > vn):
                        lift = self.early_lift
                    else:
                        continue
                    sm = so[id(m)]
                    e = max(s0 + 0.5 * (sm - s0), sm - lift)
                    self.finger_ends[f][i] = max(s0 + 1e-3, min(self.finger_ends[f][i], e))
        # Nothing travels faster than the pianist's top speed: keys are let
        # go early enough to get to the next ones, or those are struck late.
        self._speed_schedule()
        self.white_up = self._white_up()
        # what this hand actually plays: [(press, release, note)]
        self.performance = [(self.finger_starts[f][i], self.finger_ends[f][i], n)
                            for f, ns in self.by_finger.items() for i, n in enumerate(ns)]
        self.performance += [(n.start, n.end, n) for r in self.gliss_runs for n in r]
        self.spans = busy_spans((s0, e) for s0, e, _ in self.performance)
        self.span_starts = [a for a, _ in self.spans]
        self.partner = None                      # the other hand, see pair_hands
        self._idle_cache, self._clear_cache, self._path_cache = {}, {}, {}
        self._find_gestures()
        self._gliss_cache = None
        self._gliss_starts = [ep[0][0].start for ep in self.gliss_eps]
        self.runs = self._find_runs()
        self.run_starts = [a for a, _ in self.runs]
        self._layout_sig = None

    # ----- scale runs ------------------------------------------------------------
    def _find_runs(self):
        """
        [(start, end)] of this hand's scale runs: RUN_MIN_NOTES or more single
        notes in a row, each a step (1-2 semitones, or a harmonic minor's
        augmented second after a step) from the one before and at most
        RUN_GAP_T after it. Arpeggios (thirds and wider) never qualify.
        """
        runs, cur = [], []

        def close():
            if len(cur) >= RUN_MIN_NOTES:
                runs.append((cur[0][0], cur[-1][2]))
        for t, ns in self.groups:
            if len(ns) != 1:
                close()
                cur = []
                continue
            n = ns[0]
            if cur:
                d = abs(n.pitch - cur[-1][1])
                step = 1 <= d <= 2 or (d == 3 and len(cur) > 1 and 1 <= abs(cur[-1][1] - cur[-2][1]) <= 2)
                if not step or t - cur[-1][0] > RUN_GAP_T:
                    close()
                    cur = []
            cur.append((t, n.pitch, min(n.end, t + RUN_GAP_T)))
        close()
        return runs

    def _run_span(self, t):
        """The run (start, end) whose eased reach covers t, or None."""
        i = bisect.bisect_right(self.run_starts, t + RUN_RAMP_T) - 1
        if i >= 0 and t < self.runs[i][1] + RUN_RAMP_T:
            return self.runs[i]
        return None

    def _run_w(self, t):
        """0..1 how much t is inside a scale run (eased in and out over RUN_RAMP_T)."""
        r = self._run_span(t)
        if r is None:
            return 0.0
        a, b = r
        return _smooth(min((t - a) / RUN_RAMP_T + 1.0, (b - t) / RUN_RAMP_T + 1.0, 1.0))

    def _low(self, t):
        """
        How high the hand is (1 = its usual height): stretched over a wide
        chord (an octave) a pianist flattens the hand and drops the knuckles,
        so the thumb and little finger reach further across the keys.
        """
        xs = []
        for f in range(1, 6):
            w, n = self._key_weight(f, t)
            if n is not None and w > 0.01 and self._has_key(self._pk(n)):
                xs.append((self.key_target(self._pk(n), f, n)[0], w))
        if len(xs) < 2:
            return 1.0
        (x0, w0), (x1, w1) = min(xs), max(xs)
        span = (x1 - x0) / self.kb.white_w
        flat = _smooth((span - FLAT_SPAN_WK[0]) / (FLAT_SPAN_WK[1] - FLAT_SPAN_WK[0])) * min(w0, w1)
        return 1.0 - FLAT_DROP * flat

    def _splay_at(self, f, comp, stretch=1.0):
        """
        Finger f's splay limits (rad): its full stretch (`stretch` 1, for a key
        it holds or strikes) or its comfortable spread in the air (0), widened
        toward the hand's middle by `comp` (a run).
        """
        lo, hi = self.splay[f]
        if stretch < 1.0:
            clo, chi = self.splay_comfort[f]
            lo, hi = _lerp(clo, lo, stretch), _lerp(chi, hi, stretch)
        if comp > 0:
            elo, ehi = RUN_COMPRESS_DEG[f]
            lo, hi = lo - math.radians(elo) * comp, hi + math.radians(ehi) * comp
        return lo, hi

    def _separate(self, tips, wx, wy, psi, t, comp):
        """
        In a run, fingertips 2-5 that would come closer than TIP_GAP_WK (or
        pass each other) are pushed apart across the hand - a finger on its
        key stays put - blended in by `comp`: compressed, never overlapping.
        """
        if comp <= 0:
            return tips
        rot, gap = self._rot, TIP_GAP_WK * self.kb.white_w
        fs = [f for f in (2, 3, 4, 5) if f in tips]
        loc = {f: list(rot(tips[f][0] - wx, tips[f][1] - wy, -psi)) for f in fs}
        fixed = {f: self._pressing(f, t) for f in fs}
        for _ in range(12):
            moved = False
            for a, b in zip(fs, fs[1:]):
                d = gap - (loc[b][0] - loc[a][0])
                if d <= 1e-6 or (fixed[a] and fixed[b]):
                    continue
                moved = True
                if fixed[a]:
                    loc[b][0] += d
                elif fixed[b]:
                    loc[a][0] -= d
                else:
                    loc[a][0] -= d / 2
                    loc[b][0] += d / 2
            if not moved:
                break
        out = dict(tips)
        k = min(1.0, 3.0 * comp)                  # fully apart early in the run's ease-in
        for f in fs:
            x, y = rot(loc[f][0], loc[f][1], psi)
            ox, oy, oz = tips[f]
            out[f] = (_lerp(ox, wx + x, k), _lerp(oy, wy + y, k), oz)
        return out

    # ----- wrist gestures ------------------------------------------------------
    def _find_gestures(self):
        """
        Passages the wrist plays rather than the fingers:

        - repeated chords (two or more keys struck again, identically, within
          GESTURE_REPEAT_T): the hand drops onto each chord and springs back up
          between them (wrist bounce), the fingers staying close to the keys;
        - tremolos (two different key sets - notes or chords - alternating
          quickly, A B A B..., at least four strikes, their centres 3+
          semitones apart): the forearm rotates, the side that strikes going
          down each time.

        Kept as runs of strike times; `_gesture_at` turns them into the hand's
        lift, roll and how much the fingers still act on their own.
        """
        so = self.start_of
        gs = [(min(so[id(n)] for n in ns), tuple(sorted(n.pitch for n in ns)), ns) for _, ns in self.groups]
        self.bounce_runs, self.roll_runs = [], []
        # repeated chords
        rep = [False] * len(gs)
        for i in range(1, len(gs)):
            if len(gs[i][1]) >= 2 and gs[i][1] == gs[i - 1][1] and gs[i][0] - gs[i - 1][0] < GESTURE_REPEAT_T:
                rep[i] = rep[i - 1] = True
        run = []                                    # group indices
        for i, g in enumerate(gs):
            if rep[i] and run and g[0] - gs[run[-1]][0] < GESTURE_REPEAT_T:
                run.append(i)
            else:
                if len(run) >= 3:
                    self.bounce_runs.append(run)
                run = [i] if rep[i] else []
        if len(run) >= 3:
            self.bounce_runs.append(run)
        # tremolos
        def centre(ps):
            return sum(self.vp(p) for p in ps) / len(ps)
        i = 0
        while i + 3 < len(gs):
            a, b = gs[i][1], gs[i + 1][1]
            if (a != b and not set(a) & set(b) and abs(centre(a) - centre(b)) >= 3
                    and gs[i + 1][0] - gs[i][0] < GESTURE_TREMOLO_T):
                j = i + 1
                while (j + 1 < len(gs) and gs[j + 1][1] == gs[j - 1][1]
                       and gs[j + 1][0] - gs[j][0] < GESTURE_TREMOLO_T):
                    j += 1
                if j - i + 1 >= 4:
                    hi = a if centre(a) > centre(b) else b            # the little-finger side
                    sep = abs(centre(a) - centre(b))
                    amp = 0.55 + 0.45 * min(1.0, sep / 12.0)
                    self.roll_runs.append(([gs[k][0] for k in range(i, j + 1)],
                                           [1.0 if gs[k][1] == hi else -1.0 for k in range(i, j + 1)],
                                           amp))
                    i = j + 1
                    continue
            i += 1
        # a tremolo that changes harmony (Hanon 60), or bounced chords that move
        # on, is one gesture: runs that follow on closely are joined
        merged = []
        for r in self.roll_runs:
            if merged and r[0][0] - merged[-1][0][-1] < GESTURE_TREMOLO_T:
                ts, sd, amp = merged[-1]
                merged[-1] = (ts + r[0], sd + r[1], max(amp, r[2]))
            else:
                merged.append(r)
        self.roll_runs = merged
        merged = []
        for r in self.bounce_runs:
            if merged and gs[r[0]][0] - gs[merged[-1][-1]][0] < GESTURE_REPEAT_T:
                merged[-1] = merged[-1] + r
            else:
                merged.append(r)
        # Played from the wrist, each chord is let go as the hand springs up,
        # well before the next one (the fingers hold their shape; the hand
        # takes the keys down and lets them up): its keys come up at
        # `bounce_release` of the way to the next chord.
        runs = []
        for r in merged:
            strikes, releases = [], []
            for k, gi in enumerate(r):
                t0, _, ns = gs[gi]
                if k + 1 < len(r):
                    cut = t0 + self.bounce_release * (gs[r[k + 1]][0] - t0)
                    for n in ns:
                        f = self.fingering[id(n)]
                        for j, m in enumerate(self.by_finger[f]):
                            if m is n:
                                self.finger_ends[f][j] = max(self.finger_starts[f][j] + 1e-3,
                                                             min(self.finger_ends[f][j], cut))
                                break
                strikes.append(t0)
                releases.append(min(self.finger_ends[self.fingering[id(n)]][
                    next(j for j, m in enumerate(self.by_finger[self.fingering[id(n)]]) if m is n)] for n in ns))
            runs.append((strikes, releases))
        self.bounce_runs = runs
        if runs:
            self.performance = [(self.finger_starts[f][i], self.finger_ends[f][i], n)
                                for f, ns in self.by_finger.items() for i, n in enumerate(ns)]
            self.performance += [(n.start, n.end, n) for r in self.gliss_runs for n in r]
        self._bounce_starts = [r[0][0] for r in self.bounce_runs]
        self._roll_starts = [r[0][0] for r in self.roll_runs]

    def _gesture_at(self, t):
        """(lift in pixels, roll in radians (+: little-finger side down), 0..1 how much the wrist is playing)."""
        lift, roll, w = 0.0, 0.0, 0.0
        if self.bounce_runs and self.bounce_in > 0:
            k = bisect.bisect_right(self._bounce_starts, t + 0.3) - 1
            for kk in (k - 1, k):            # neighbouring runs' fades may overlap: add them up
                if kk < 0:
                    continue
                l_, w_ = self._bounce_one(self.bounce_runs[kk], t)
                lift += l_
                w = max(w, w_)
        if self.roll_runs and self.roll_max > 0:
            k = bisect.bisect_right(self._roll_starts, t + 0.25) - 1
            for kk in (k - 1, k):
                if kk < 0:
                    continue
                r_, w_ = self._roll_one(self.roll_runs[kk], t)
                roll += r_
                w = max(w, w_)
        return lift, roll, min(1.0, w)

    def _bounce_one(self, run, t):
        """(lift px, weight): the hand springs up once a chord is let go and drops onto the next."""
        ts, rel = run
        if not (ts[0] - 0.3 <= t <= rel[-1] + 0.45):
            return 0.0, 0.0
        i = bisect.bisect_right(ts, t) - 1
        top = lambda gap: self.bounce_in * self.ppi * min(1.0, 0.25 + gap / 0.12)
        if i < 0:                                    # coming down onto the first chord
            gap = max(0.05, rel[0] - ts[0] if len(ts) < 2 else ts[1] - rel[0])
            x = min(1.0, (ts[0] - t) / gap)
            w = _smooth(1.0 - (ts[0] - t) / 0.3)
            return top(gap) * math.sin(0.5 * math.pi * x) * w, w
        if t < rel[i]:                               # the chord is down
            return 0.0, 1.0
        if i >= len(ts) - 1:                         # springing off the last one, settling
            age = t - rel[-1]
            w = 1.0 - _smooth(age / 0.45)
            gap = max(0.05, ts[-1] - rel[-2]) if len(ts) > 1 else 0.1
            return top(gap) * math.sin(math.pi * min(0.5, age / gap)) * w, w
        gap = max(1e-3, ts[i + 1] - rel[i])
        v = (t - rel[i]) / gap
        return top(gap) * math.sin(math.pi * v ** 0.7), 1.0

    def _roll_one(self, run, t):
        ts, sides, amp = run
        if not (ts[0] - 0.25 <= t <= ts[-1] + 0.35):
            return 0.0, 0.0
        R = self.roll_max * amp
        i = bisect.bisect_right(ts, t) - 1
        if i < 0:
            f = _smooth(1.0 - (ts[0] - t) / 0.25)
            return sides[0] * R * f, f
        if i >= len(ts) - 1:
            f = 1.0 - _smooth((t - ts[-1]) / 0.35)
            return sides[-1] * R * f, f
        u = (t - ts[i]) / (ts[i + 1] - ts[i])
        # it stays rolled onto the side that just struck, then swings over
        # onto the other side for its strike
        c = 0.5 * (1.0 + math.cos(math.pi * u ** 1.8))
        return R * (sides[i] * c + sides[i + 1] * (1.0 - c)), 1.0

    def _press_amount(self, f, t):
        """0..1 how far finger f has its key down: ramping in on the strike, out as the key rises."""
        starts, ends = self.finger_starts[f], self.finger_ends[f]
        i = bisect.bisect_right(starts, t) - 1
        if i < 0:
            return 0.0
        if t < ends[i]:
            return min(1.0, (t - starts[i]) / PRESS_T)
        # (the hand lets the keys up quickly: they spring back with it)
        return max(0.0, 1.0 - _smooth((t - ends[i]) / GESTURE_KEY_UP_T))

    def _near_note(self, f, t, win):
        """Finger f has a note within win seconds (before or after t)."""
        starts, ends = self.finger_starts[f], self.finger_ends[f]
        i = bisect.bisect_right(starts, t) - 1
        if i >= 0 and t - ends[i] < win:
            return True
        return i + 1 < len(starts) and starts[i + 1] - t < win

    def _key_weight(self, f, t):
        """(0..1, note) how much finger f's key must be reachable at t: 1 while held, ramping around the strike and release."""
        starts, ends = self.finger_starts[f], self.finger_ends[f]
        i = bisect.bisect_right(starts, t) - 1
        best, note = 0.0, None
        if i >= 0:
            w = 1.0 if t < ends[i] else (1.0 - _smooth((t - ends[i]) / KEY_FIX_RELEASE_T)) ** 2
            if w > 0:
                best, note = w, self.by_finger[f][i]
        if i + 1 < len(starts):
            w = _smooth(1.0 - (starts[i + 1] - t) / KEY_FIX_T) ** 2
            if w > best:
                best, note = w, self.by_finger[f][i + 1]
        return best, note

    def _limited_at(self, t):
        """
        The hand (wx, wy, psi) at t as it is drawn: placed (_placed_at),
        fitted to its keys (_key_fix) and then held to the top speed. On its
        grid (LIMIT_GRID_T), each step may move the wrist plus the fingertips' swing
        about it (turn times TIP_ARM_IN) at most the top speed allows; the
        schedule already keeps to it, so this only trims what the solver's
        own corrections add. Steps follow on from the one before (cached);
        after a seek the chain restarts LIMIT_RESTART_T earlier, long enough
        for any lag to have settled. The limited path is then averaged over
        +-LIMIT_SMOOTH_HAND steps (triangular, so without delay), which
        rounds off the corners where the limit or the key fit kicks in.
        Between grid points: linear.
        """
        g = LIMIT_GRID_T
        k = math.floor(t / g)
        a, b = self._smooth_hand_grid(k), self._smooth_hand_grid(k + 1)
        u = t / g - k
        return tuple(x + (y - x) * u for x, y in zip(a, b))

    def _smooth_hand_grid(self, k):
        cache = self._smooth_hand_cache
        q = cache.get(k)
        if q is None:
            if len(cache) > 20000:
                cache.clear()
            def avg(n):
                acc, ws = [0.0, 0.0, 0.0], 0.0
                for d in range(-n, n + 1):
                    w = n + 1 - abs(d)
                    v = self._limited_grid(k + d)
                    acc = [a + w * x for a, x in zip(acc, v)]
                    ws += w
                return [a / ws for a in acc]
            # wide while the hand travels, narrow while its fingers are on
            # keys (with chords a few hundredths apart a wide window would
            # mix in the pose for the next chord): by how much the keys
            # count now (_key_weight: ramping in before a strike, out after)
            on = max(self._key_weight(f, k * LIMIT_GRID_T)[0] for f in range(1, 6))
            wide, narrow = avg(LIMIT_SMOOTH_HAND), avg(LIMIT_SMOOTH_HAND_ON)
            q = cache[k] = tuple(_lerp(a, b, on) for a, b in zip(wide, narrow))
        return q

    def _tips_at(self, t, hand):
        """
        {finger: (x, y, z)} fingertip targets at t for the hand at `hand`
        (wx, wy, psi): first with the plain rest spots, then again with rest
        spots shaped around the busy fingers; each kept inside its finger's
        range (_limit_tip).
        """
        wx, wy, psi = hand
        rot = self._rot

        def to_world_xy(lx, ly):
            x, y = rot(lx, ly, psi)
            return wx + x, wy + y
        tips, busy, local = {}, {}, {}
        rb = self.retract_back
        for f in range(1, 6):
            rx, ry = self.rest_local[f]
            tips[f], busy[f] = self._tip_target(f, t, to_world_xy(rx, ry - rb[f]), hand)
            local[f] = rot(tips[f][0] - wx, tips[f][1] - wy, -psi)
        shaped = self._shaped_rests(busy, local)
        comp, low = self._run_w(t), self._low(t)
        for f in range(1, 6):
            if busy[f] < 1.0:
                tips[f], _ = self._tip_target(f, t, to_world_xy(shaped[f][0], shaped[f][1] - rb[f]), hand)
            tips[f] = self._limit_tip(f, tips[f], wx, wy, psi, comp, self._key_weight(f, t)[0], low)
        return self._separate(tips, wx, wy, psi, t, comp)

    def _limited_tips(self, t):
        """
        {finger: (x, y, z)} fingertips at t: _tips_at on the hand grid, held
        to the top speed across the keys like the hand (_limited_at); linear
        between grid points.
        """
        g = LIMIT_GRID_T
        k = math.floor(t / g)
        a, b = self._smooth_tip_grid(k), self._smooth_tip_grid(k + 1)
        u = t / g - k
        return {f: tuple(x + (y - x) * u for x, y in zip(a[f], b[f])) for f in a}

    def _smooth_tip_grid(self, k):
        """
        _limited_tip_grid averaged across the keys over +-LIMIT_SMOOTH_TIPS
        steps (height as it is), for fingers in the air - one holding its
        key stays exactly on it.
        """
        cache = self._smooth_tip_cache
        q = cache.get(k)
        if q is None:
            if len(cache) > 20000:
                cache.clear()
            n = LIMIT_SMOOTH_TIPS
            grids = [(n + 1 - abs(d), self._limited_tip_grid(k + d)) for d in range(-n, n + 1)]
            ws = sum(w for w, _ in grids)
            mid = grids[n][1]
            q = cache[k] = {}
            for f in mid:
                if self._pressing(f, k * LIMIT_GRID_T):
                    q[f] = mid[f]              # a finger on its key stays exactly there
                else:
                    q[f] = (sum(w * g_[f][0] for w, g_ in grids) / ws,
                            sum(w * g_[f][1] for w, g_ in grids) / ws, mid[f][2])
        return q

    def _limited_tip_grid(self, k):
        cache = self._tip_limit_cache
        if k in cache:
            return cache[k]
        if len(cache) > 20000:
            cache.clear()
        g = LIMIT_GRID_T
        step = self.max_speed / 0.0254 * self.ppi * g
        j, todo = k, []
        while j not in cache and len(todo) < int(LIMIT_RESTART_T / g):
            todo.append(j)
            j -= 1
        prev = cache.get(j)
        for j in reversed(todo):
            tips = self._tips_at(j * g, self._smooth_hand_grid(j))
            q = {}
            for f, (x, y, z) in tips.items():
                if prev is not None:
                    px, py, _ = prev[f]
                    d = math.hypot(x - px, y - py)
                    if d > step:
                        x, y = px + (x - px) * step / d, py + (y - py) * step / d
                q[f] = (x, y, z)
            prev = cache[j] = q
        return prev

    def _limited_grid(self, k):
        cache = self._limit_cache
        if k in cache:
            return cache[k]
        if len(cache) > 20000:
            cache.clear()
        g = LIMIT_GRID_T
        step = self.max_speed / 0.0254 * self.ppi * g           # pixels per grid step
        arm = TIP_ARM_IN * self.ppi
        j = k
        todo = []
        while j not in cache and len(todo) < int(LIMIT_RESTART_T / g):
            todo.append(j)
            j -= 1
        prev = cache.get(j)
        for j in reversed(todo):
            tt = j * g
            q = self._key_fix(tt, *self._placed_at(tt))
            if prev is not None:
                dx, dy, dp = q[0] - prev[0], q[1] - prev[1], q[2] - prev[2]
                move = math.hypot(dx, dy) + abs(dp) * arm
                if move > step:
                    k_ = step / move
                    q = (prev[0] + dx * k_, prev[1] + dy * k_, prev[2] + dp * k_)
            prev = cache[j] = q
        return prev

    def _key_fix(self, t, wx, wy, psi):
        """
        The smoothed hand (wx, wy, psi), moved and turned as little as it
        takes for every finger holding a key (or about to strike one, see
        _key_weight) to reach it within its splay and reach limits, so
        _limit_tip never has to pull a fingertip off its key.
        """
        cons = []
        comp, low = self._run_w(t), self._low(t)
        for f in range(1, 6):
            w, n = self._key_weight(f, t)
            if n is None or not self._has_key(self._pk(n)):
                continue
            kx, ky = self.key_target(self._pk(n), f, n)
            ylo, yhi = self._key_depths(self._pk(n))
            ys = sorted([ky] + [ylo + (yhi - ylo) * i / 12 for i in range(13)], key=lambda y: abs(y - ky))
            lo, hi = self._splay_at(f, comp)
            # a finger already down on its key may use its pressing slack (as _limit_tip allows)
            m = math.radians(KEY_FIX_MARGIN_DEG) - (math.radians(PRESS_SLACK_DEG[f]) if self._pressing(f, t) else 0.0)
            hmin, hmax = self._reach_range(f, self.base_local[f][2] * low + self.travel, 0.99)
            dm = KEY_FIX_MARGIN * self.length[f]
            cons.append((kx, ys, self.base_local[f], lo + m, hi - m, hmin + dm, hmax - dm, KEY_FIX_K * w))
        if not cons:
            return wx, wy, psi
        arm = 3.0 * self.S                             # turning counts as moving the knuckles this far

        def residuals(q):
            x, y, p = q
            c, s_ = math.cos(p), math.sin(p)
            out = [x - wx, y - wy, arm * (p - psi)]
            for kx, ys, (blx, bly, _), lo, hi, hmin, hmax, k in cons:
                # anywhere along the key will do: the depth that needs the least
                best = None
                for ky in ys:
                    dx = kx - (x + blx * c - bly * s_)
                    dy = ky - (y + blx * s_ + bly * c)
                    lx, ly = dx * c + dy * s_, -dx * s_ + dy * c
                    a, h = math.atan2(lx, ly), math.hypot(lx, ly)
                    ea = h * (max(0.0, a - hi) + min(0.0, a - lo))
                    eh = max(0.0, h - hmax) + min(0.0, h - hmin)
                    if best is None or ea * ea + eh * eh < best[0] - 1e-9:
                        best = (ea * ea + eh * eh, ea, eh)
                out += [k * best[1], k * best[2]]
            return out
        q = [wx, wy, psi]
        r0 = residuals(q)
        cost = sum(v * v for v in r0)
        eps = (0.5, 0.5, 1e-3)
        for _ in range(KEY_FIX_ITERS):
            if sum(v * v for v in r0[3:]) < 1e-6:
                break
            cols = []
            for j in range(3):
                qq = list(q)
                qq[j] += eps[j]
                cols.append([(a - b) / eps[j] for a, b in zip(residuals(qq), r0)])
            A = [[sum(ci * cj for ci, cj in zip(cols[i], cols[j])) for j in range(3)] for i in range(3)]
            g = [-sum(ci * r for ci, r in zip(cols[i], r0)) for i in range(3)]
            for i in range(3):
                A[i][i] *= 1.0 + GN_DAMPING
            dq = _solve3(A, g)
            # only ever downhill: halve a step that would overshoot
            for _ in range(6):
                qn = [q[i] + dq[i] for i in range(3)]
                rn = residuals(qn)
                cn = sum(v * v for v in rn)
                if cn < cost:
                    q, r0, cost = qn, rn, cn
                    break
                dq = [d * 0.5 for d in dq]
            else:
                break
        return tuple(q)

    def _pressing(self, f, t):
        starts = self.finger_starts[f]
        i = bisect.bisect_right(starts, t) - 1
        return i >= 0 and t < self.finger_ends[f][i]

    def _set_behavior(self, p):
        """Turn the pianist's behaviour settings into this animator's timings and sizes."""
        a_hand, a_fing = p.b("antic_hand"), p.b("antic_fingers")
        self.antic_t = 0.15 + 0.5 * a_hand                 # 0.4 s at the default 0.5
        self.need_t = 0.06 + 0.18 * a_hand                 # 0.15 s
        import pianist as pianists
        self.prep_max_t, self.travel_share = pianists.finger_lead(a_fing)   # 0.9 s, 0.6
        self.prep_h = {f: h * (0.5 + p.b("lift_height")) for f, h in PREP.items()}
        self.cross_turn = math.radians(p.b("cross_turn"))
        self.cross_norm = math.radians(CROSS_TURN_DEG)     # the crossing prior is tuned to 14 deg
        self.smooth_t = p.b("smoothness")
        self.early_lift = p.b("early_release")
        self.max_speed = float(p.b("max_speed"))
        self.area_near = p.b("key_area_near")
        self.area_far = max(self.area_near + 0.05, p.b("key_area_far"))
        self.roll_dt = p.b("roll_speed")
        self.cross_arc_in = 0.5 + 3.0 * p.b("cross_height")
        self.curl_k = curl_factor(p)
        c = p.b("finger_curve")
        self.curl_lift_in = 0.9 * (c - 0.636)             # hand height: -0.57 in (flat) .. +0.33 in (curved)
        self.reach_bonus = 0.09 * max(0.0, 0.636 - c) / 0.636   # flat fingers may extend further
        r = p.b("retraction")
        self.retract_back_in = 1.4 * r                     # idle fingers pull back ...
        self.retract_up_in = 0.7 * r                       # ... and up
        self.curl_min = 1.0 - 0.55 * r
        # wrist gestures: bounce on repeated chords, rotation in tremolos
        self.bounce_in = BOUNCE_MAX_IN * p.b("wrist_bounce")
        self.bounce_release = 0.85 - 0.4 * p.b("wrist_bounce")      # 65% of the way at the default
        self.roll_max = math.radians(ROLL_MAX_DEG) * p.b("tremolo_rotation")
        self.gesture_act = p.b("gesture_finger_action")

    def _white_up(self):
        """{id(note): 0..1} how far toward WHITE_UP_IN each white-key note is played: black keys near in time pull it up."""
        so = self.start_of
        times = [min(so[id(n)] for n in ns) for _, ns in self.groups]
        black = [t for t, (_, ns) in zip(times, self.groups) if any(is_black_key(n.pitch) for n in ns)]
        out = {}
        for t, (_, ns) in zip(times, self.groups):
            i = bisect.bisect_left(black, t)
            near = min((abs(black[j] - t) for j in (i - 1, i) if 0 <= j < len(black)), default=math.inf)
            up = _smooth((WHITE_UP_FADE_T - near) / (WHITE_UP_FADE_T - WHITE_UP_T))
            if up > 0:
                for n in ns:
                    if not is_black_key(n.pitch):
                        out[id(n)] = up
        return out

    def _jump_lift(self, n, nx):
        """
        How early a finger lets go of note n to play nx next: the pianist's
        early release for a real jump, scaled down for a short move (a step
        in the same voice stays legato, only lifting LEGATO_LIFT_T before).
        """
        d = abs(key_pos(self.vp(nx.pitch)) - key_pos(self.vp(n.pitch)))
        k = min(1.0, max(0.0, (d - LEGATO_STEP_WK) / (JUMP_WK - LEGATO_STEP_WK)))
        return LEGATO_LIFT_T + (self.early_lift - LEGATO_LIFT_T) * k

    def _speed_schedule(self):
        """
        Make the timeline keep to the top speed (pianist "max_speed"). Chord
        by chord: a finger moving to a new key must let go of its last one
        early enough to get there (fingering.travel_time) and drop onto it
        (STRIKE_MIN_T); when the
        whole hand has to move (its range for the last chord and this one
        don't meet), every key it still holds must be let go early enough
        for that. A key is held at least MIN_HOLD_T (or half the time to the
        new chord); if that leaves too little time the chord is struck late,
        by at most MAX_DELAY_T and never past the hand's next chord.
        `self.lead` keeps the travel time each note needs, for _prep_window
        and _travel; `self.delayed` counts late chords.
        """
        import fingering as fg
        so = self.start_of
        fe = {id(n): self.finger_ends[f][i] for f, ns in self.by_finger.items() for i, n in enumerate(ns)}
        prev_of = {}
        for f, ns in self.by_finger.items():
            for i, n in enumerate(ns):
                j = i - 1
                while j >= 0 and so[id(n)] - so[id(ns[j])] < 0.01:      # a thumb on two keys at once
                    j -= 1
                prev_of[id(n)] = ns[j] if j >= 0 else None
        self.lead = {}
        self.delayed = 0
        prev_range = None
        groups = self.groups
        for gi, (_, ns) in enumerate(groups):
            s1 = min(so[id(m)] for m in ns)
            nxt = min(so[id(m)] for m in groups[gi + 1][1]) if gi + 1 < len(groups) else math.inf
            vps = [self.vp(m.pitch) for m in ns]
            fs = [self.fingering[id(m)] for m in ns]
            rng = fg.hand_range(vps, fs)
            cons = []                                   # (note, must be let go this long before s1)
            for m in ns:
                n = prev_of[id(m)]
                if n is not None and n.pitch != m.pitch:
                    need = fg.travel_time(key_pos(self.vp(m.pitch)) - key_pos(self.vp(n.pitch)), self.max_speed)
                    self.lead[id(m)] = need
                    cons.append((n, STRIKE_MIN_T + need))
            gap = fg.range_gap(prev_range, rng) if prev_range is not None else 0.0
            if gap > HAND_MOVE_TOL_WK:
                need = fg.travel_time(gap, self.max_speed)
                lead = STRIKE_MIN_T + need
                for m in ns:
                    self.lead[id(m)] = max(self.lead.get(id(m), 0.0), need)
                # keys still held: kept down if they fit with the new chord
                # (and whatever else is kept) and their finger isn't needed -
                # a held middle voice under moving octaves, say - else let go
                chord = list(zip(vps, fs))
                kept = []
                for gj in range(gi - 1, -1, -1):
                    if s1 - groups[gj][0] > 4.0:
                        break
                    for n in groups[gj][1]:
                        if fe[id(n)] > s1 - lead:
                            key = (self.vp(n.pitch), self.fingering[id(n)])
                            if key[1] in fs or any(k[1] == key[1] for k in kept) or \
                                    fg.shape_cost(kept + [key] + chord) >= fg.IMPOSSIBLE:
                                cons.append((n, lead))
                            else:
                                kept.append(key)
            prev_range = rng
            delay = 0.0
            for n, lead in cons:
                floor = so[id(n)] + min(MIN_HOLD_T, 0.5 * max(0.0, s1 - so[id(n)]))
                delay = max(delay, floor - (s1 - lead))
            if delay > 1e-4:
                delay = min(delay, MAX_DELAY_T, max(0.0, nxt - 0.03 - s1))
                if delay > 1e-4:
                    self.delayed += 1
                    for m in ns:
                        so[id(m)] += delay
                        fe[id(m)] += delay
                    s1 += delay
            for n, lead in cons:
                floor = so[id(n)] + min(MIN_HOLD_T, 0.5 * max(0.0, s1 - so[id(n)]))
                fe[id(n)] = max(floor, min(fe[id(n)], s1 - lead), so[id(n)] + 1e-3)
        for f, ns in self.by_finger.items():
            ns.sort(key=lambda n: so[id(n)])
            self.finger_starts[f] = [so[id(n)] for n in ns]
            ends = [max(fe[id(n)], so[id(n)] + 1e-3) for n in ns]
            for i in range(len(ns) - 1):              # a finger lets go before it strikes again
                if self.finger_starts[f][i + 1] - self.finger_starts[f][i] >= 0.01:
                    ends[i] = max(self.finger_starts[f][i] + 1e-3, min(ends[i], self.finger_starts[f][i + 1]))
            self.finger_ends[f] = ends

    def _roll_wide_chords(self):
        """
        A chord this hand can't span is rolled from the bottom up, and the
        lower notes are let go as the hand moves on to the top ones (the pedal,
        if it's down, keeps them sounding).
        """
        import fingering as fg
        scale = reach_scale(self.pianist.anatomy)
        so, eo = self.start_of, self.end_of

        def reach(f1, f2):
            a, b = min(f1, f2), max(f1, f2)
            return fg.BASE_MAX_SPAN[(a, b)] * scale[(a, b)] + 0.25

        def pos(n):
            """Where the note's finger is: between the two keys it covers, if a pair."""
            pk = self.pair_key.get(id(n))
            if pk:
                return (key_pos(self.vp(pk[0])) + key_pos(self.vp(pk[1]))) / 2
            return key_pos(self.vp(n.pitch))

        for _, ns in self.groups:
            if len(ns) < 2:
                continue
            fs = [(pos(n), self.fingering[id(n)], n) for n in ns]
            too_wide = any(f1 != f2 and abs(p2 - p1) > reach(f1, f2)
                           for i, (p1, f1, _) in enumerate(fs) for p2, f2, _ in fs[i + 1:])
            if not too_wide:
                continue
            self.rolled += 1
            order = sorted(ns, key=lambda n: n.pitch)            # bottom to top, both hands
            for k, n in enumerate(order):
                so[id(n)] = n.start + k * self.roll_dt
            top = order[-1]
            ft, pt = self.fingering[id(top)], pos(top)
            for n in order[:-1]:
                f = self.fingering[id(n)]
                if f != ft and abs(pt - pos(n)) > reach(f, ft):
                    eo[id(n)] = min(eo[id(n)], so[id(top)] + 0.02)
            for n in order:
                eo[id(n)] = max(eo[id(n)], so[id(n)] + 0.03)

    def finger_for(self, note):
        """Planned finger for a note (None if it isn't played by this hand, or is slid in a glissando)."""
        return self.fingering.get(id(note))

    def is_gliss(self, note):
        """Is this note slid in a glissando by this hand?"""
        return id(note) in self.gliss_ids

    # ----- geometry that depends on the drawn keyboard ----------------------
    def _ensure_layout(self, kb):
        sig = (tuple(kb.rect), kb.white_w, kb.black_h, kb.style)
        if sig == self._layout_sig:
            return
        self._layout_sig = sig
        self.kb = kb
        self.ppi = kb.white_w / WHITE_KEY_IN                  # pixels per inch
        S = self.S = INCHES_PER_UNIT * self.ppi               # pixels per model unit
        u = 1.0 / INCHES_PER_UNIT                             # model units per inch

        # Resting fingertip spots in the hand's frame: one white key apart,
        # each at its finger's usual depth on the key.
        geo = self.geo
        front = geo.mcp[3][1] + geo.rest_reach * self.curl_k - WHITE_DEPTH_IN[3] * u
        self.rest_local = {
            f: ((geo.mcp[3][0] + (f - 3) * WHITE_KEY_IN * u) * S,
                (front + WHITE_DEPTH_IN[f] * u) * S)
            for f in range(1, 6)}
        # where idle fingers wait: pulled back (and up, see _tip_target) by the
        # pianist's retraction; the thumb only half as much
        back = self.retract_back_in * self.ppi
        self.retract_back = {f: back * (0.5 if f == 1 else 1.0) for f in range(1, 6)}
        # Each finger's base joint (MCP, or the thumb's CMC) in the hand frame,
        # its length and its splay range.
        # flatter fingers go with a lower hand, curved ones with a higher one
        self.z_off = self.curl_lift_in * self.ppi
        self.base_local = {f: (geo.base(f)[0] * S, geo.base(f)[1] * S, geo.base(f)[2] * S + self.z_off)
                           for f in range(1, 6)}
        self.length = {f: sum(geo.bones[f]) * S for f in range(1, 6)}
        self.splay = {f: tuple(math.radians(a) for a in SPLAY_LIMIT_DEG[f]) for f in range(1, 6)}
        self.splay_comfort = {f: tuple(math.radians(a) for a in SPLAY_COMFORT_DEG[f]) for f in range(1, 6)}
        self.travel = KEY_TRAVEL_IN * self.ppi
        self._cache = {}
        self._idle_cache, self._clear_cache, self._path_cache = {}, {}, {}
        self._limit_cache, self._tip_limit_cache = {}, {}
        self._smooth_hand_cache, self._smooth_tip_cache = {}, {}
        self._gliss_cache = None
        # mirror axis (centre of D4) and the shoulder, ~10 semitones from D4
        # toward the hand's own side (in the mirrored frame for the left hand)
        self.axis_x = kb.key_rects[62].centerx if 62 in kb.key_rects else kb.rect.centerx
        sp = 52 if self.mirror else 72
        sx = kb.key_rects[sp].centerx if sp in kb.key_rects else kb.rect.centerx
        self.shoulder_x = self._mx(sx)
        self.forearm_len = 16 * self.ppi

    def _pk(self, note):
        """The key(s) a note's finger aims at: its pitch, or (lo, hi) when it covers two."""
        return self.pair_key.get(id(note), note.pitch)

    def _has_key(self, pk):
        rects = self.kb.key_rects
        return all(p in rects for p in pk) if isinstance(pk, tuple) else pk in rects

    def key_target(self, pitch, f, note=None):
        """
        Fingertip contact point (X, Y) for a finger on a key (or between two,
        for a pair). A white key is played at its finger's usual depth - or,
        for a note among black keys (`white_up`), further up, just past the
        black keys' front, so the hand needn't move in and out between them.
        """
        if isinstance(pitch, tuple):
            (x1, y1), (x2, y2) = (self.key_target(p, f, note) for p in pitch)
            return (x1 + x2) / 2, (y1 + y2) / 2
        r = self.kb.key_rects[pitch]
        x = r.centerx
        front = self.kb.rect.h - self.kb.black_h
        if is_black_key(pitch):
            y = front + BLACK_DEPTH_IN * self.ppi
        else:
            y = WHITE_DEPTH_IN[f] * self.ppi
            up = self.white_up.get(id(note), 0.0) if note is not None else 0.0
            if up > 0:
                y = max(y, _lerp(y, front + WHITE_UP_IN * self.ppi, up))
                if pitch in self.kb.tails:                 # equal keys: up among the blacks, the key is its back
                    x = _lerp(x, self.kb.tails[pitch].centerx, up)
        lo, hi = self._key_depths(pitch, note, f)          # the playing area, for this loudness
        return self._mx(x), min(max(y, lo), hi)

    def _key_depths(self, pk, note=None, f=None):
        """
        (lowest, highest) world Y a fingertip may play key(s) pk at: the
        pianist's playing area on the key (key_area_near .. key_area_far of
        its playable length, WHITE_SPAN_IN / BLACK_SPAN_IN). With `note`
        and its finger `f`: where it aims, by its loudness - a loud note no
        further up than its finger's usual spot (plus LOUD_MARGIN_IN),
        nearer the key's front, where it has leverage; a soft one anywhere
        in the area. (A finger that can't reach its aim may still slide
        within the whole area, see _key_spot.)
        """
        if isinstance(pk, tuple):
            spans = [self._key_depths(p, note, f) for p in pk]
            lo, hi = max(a for a, _ in spans), min(b for _, b in spans)
            return (lo, hi) if lo <= hi else (hi, lo)
        front = self.kb.rect.h - self.kb.black_h
        if is_black_key(pk):
            a, b = front + BLACK_SPAN_IN[0] * self.ppi, front + BLACK_SPAN_IN[1] * self.ppi
        else:
            a, b = WHITE_SPAN_IN[0] * self.ppi, front + WHITE_SPAN_IN[1] * self.ppi
        lo, hi = a + (b - a) * self.area_near, a + (b - a) * self.area_far
        if note is not None and f is not None:
            black = is_black_key(pk)
            usual = front + BLACK_DEPTH_IN * self.ppi if black else WHITE_DEPTH_IN[f] * self.ppi
            cap = max(lo, min(hi, usual + LOUD_MARGIN_IN[black] * self.ppi))
            hi = _lerp(hi, cap, loudness(note.velocity))
        return lo, hi

    def _clamp_tip(self, f, x, y, z, wx, wy, psi, slack=0.0, margin=0.0, comp=0.0, stretch=1.0, low=1.0):
        """
        (x, y) clamped into finger f's splay and reach range (shrunk by
        `margin` share, widened by `slack` rad): the reachable point closest
        to it. A target beyond the splay limit is brought onto the limit's
        line at its nearest point there - not swung round at full length,
        which would stretch the finger out past where it is going.
        """
        rot = self._rot
        blx, bly, blz = self.base_local[f]
        bx, by = rot(blx, bly, psi)
        bx, by = wx + bx, wy + by
        lx, ly = rot(x - bx, y - by, -psi)
        a, h = math.atan2(lx, ly), math.hypot(lx, ly)
        lo, hi = self._splay_at(f, comp, stretch)
        m = math.radians(KEY_FIX_MARGIN_DEG) * margin / KEY_FIX_MARGIN if margin else 0.0
        lo, hi = lo - slack + m, hi + slack - m
        hmin, hmax = self._reach_range(f, blz * low - z, 0.99)
        if z > 0:
            hmin *= self.curl_min            # a retracting pianist curls idle fingers further in
        dm = margin * self.length[f]
        hmin, hmax = hmin + dm, max(hmin + dm, hmax - dm)
        ac = _clamp(a, lo, hi)
        if ac != a:
            h *= max(0.0, math.cos(a - ac))          # nearest point on the limit's line
        a, h = ac, _clamp(h, hmin, hmax)
        cx, cy = rot(h * math.sin(a), h * math.cos(a), psi)
        return bx + cx, by + cy

    def _key_spot(self, pk, f, hand, z=None, note=None, soft=0.0, comp=0.0, low=1.0):
        """
        Where finger f plays key(s) pk with the hand at `hand` (wx, wy, psi):
        squarely across the key, and along it where it aims (key_target:
        its usual depth, bounded by the note's loudness), or as little
        further in or out (anywhere in the pianist's playing area,
        _key_depths) as it takes to be inside the finger's splay and reach
        range. None for `hand` gives the usual spot.

        The depth is a soft choice over spots along the key (each priced by
        how far it is from the aim plus a weight times how far out of reach
        it is), so it moves smoothly as the hand moves instead of jumping
        between spots: firm (KEY_SPOT_FIRM) on the key, softer
        (KEY_SPOT_SOFT, by `soft` 0..1) while the finger is on its way; a
        key far out of reach fades back to its usual spot.
        """
        kx, ky = self.key_target(pk, f, note)
        if hand is None:
            return kx, ky
        z = -self.travel if z is None else z
        lo, hi = self._key_depths(pk)          # the whole playing area, if reach needs it
        span = max(1e-6, hi - lo)
        cx, cy = self._clamp_tip(f, kx, ky, z, *hand, margin=KEY_FIX_MARGIN, comp=comp, low=low)
        e0 = math.hypot(cx - kx, cy - ky)
        far = _smooth((e0 - 1.5 * span) / (1.5 * span))
        if e0 < 0.5 or far >= 1.0:
            return kx, ky
        kk = _lerp(KEY_SPOT_FIRM[0], KEY_SPOT_SOFT[0], soft)
        tau = _lerp(KEY_SPOT_FIRM[1], KEY_SPOT_SOFT[1], soft) * self.ppi
        cands = [(ky, kk * e0)]
        for i in range(21):
            y = lo + span * i / 20
            cx, cy = self._clamp_tip(f, kx, y, z, *hand, margin=KEY_FIX_MARGIN, comp=comp, low=low)
            cands.append((y, abs(y - ky) + kk * math.hypot(cx - kx, cy - y)))
        best = min(c for _, c in cands)
        ws = [(y, math.exp(-(c - best) / tau)) for y, c in cands]
        y = sum(y * w for y, w in ws) / sum(w for _, w in ws)
        return kx, _lerp(y, ky, far)

    def _mx(self, x):
        """Screen x <-> this hand's working frame (mirrored for the left hand)."""
        return 2 * self.axis_x - x if self.mirror else x

    def _yaw(self, wx):
        """Natural hand turn from a forearm swinging around the right shoulder."""
        forearm = math.atan2(wx - self.shoulder_x, self.forearm_len)
        return -0.3 * forearm

    @staticmethod
    def _rot(x, y, psi):
        c, s = math.cos(psi), math.sin(psi)
        return x * c - y * s, x * s + y * c

    def _reach_range(self, f, dz, share=None):
        """Allowed horizontal distance, knuckle to fingertip, for a tip dz below the knuckle."""
        L = self.length[f] * (min(0.99, REACH_COMFORT[f] + self.reach_bonus) if share is None else share)
        hmax = math.sqrt(max(0.0, L * L - dz * dz))
        return min(REACH_MIN[f] * self.length[f], hmax), hmax

    # ----- what the hand is doing around time t ----------------------------
    def _items_at(self, t):
        """
        [(pitch, finger, pull, need, note_start, released)] for time t: `pull` is how much the key
        shapes the hand (it leans toward keys a finger is heading for), `need`
        how strictly the finger must be able to reach it (1 while pressed,
        rising just before a strike, fading right after a release).
        """
        items = []
        for f in range(1, 6):
            notes, starts, ends = self.by_finger[f], self.finger_starts[f], self.finger_ends[f]
            n = len(notes)
            if not n:
                continue
            i = bisect.bisect_right(starts, t) - 1
            nxt = i + 1 if i + 1 < n else None
            ramp = _smooth(1.0 - (starts[nxt] - t) / self.antic_t) if nxt is not None else 0.0
            need_next = _smooth(1.0 - (starts[nxt] - t) / self.need_t) ** 3 if nxt is not None else 0.0
            if i < 0:                                                  # nothing played yet
                items.append((self._pk(notes[0]), f, max(ramp, 1e-6 + IDLE_W * 0.5 ** (starts[0] - t)), need_next, starts[0], 0, notes[0]))
                continue
            # the key before last, if it's still fading out
            if i >= 1 and t >= ends[i - 1]:
                hold = min(RELEASE_HOLD_T, max(1e-3, starts[i] - ends[i - 1]))
                w = 1.0 - _smooth((t - ends[i - 1]) / hold)
                if w > 0:
                    items.append((self._pk(notes[i - 1]), f, w, RELEASE_NEED * (1.0 - _smooth((t - ends[i - 1]) / RELEASE_NEED_T)), starts[i - 1], 1, notes[i - 1]))
            if t < ends[i]:
                # pressing; it can only lean toward its next key for now
                items.append((self._pk(notes[i]), f, 1.0, 1.0, starts[i], 0, notes[i]))
                if nxt is not None and ramp > 0:
                    w = ANTIC_HELD * ramp
                    items.append((self._pk(notes[nxt]), f, w, w ** VOTE_POWER, starts[nxt], 0, notes[nxt]))
                continue
            # Free: the pull moves from the old key to the next one exactly as
            # the finger itself travels there (see _travel).
            age = t - ends[i]
            if nxt is not None:
                s, prep_start = self._travel(f, i, t)
                immediate = prep_start <= ends[i] + 1e-6
            else:
                s, immediate = 0.0, False
            fade = 1.0 if immediate else 1.0 - _smooth(age / RELEASE_HOLD_T)
            w = (1.0 - s) * max(fade, 1e-6 + IDLE_W * 0.5 ** age)
            if w > 0:
                items.append((self._pk(notes[i]), f, w, RELEASE_NEED * (1.0 - _smooth(age / RELEASE_NEED_T)), starts[i], 1, notes[i]))
            if nxt is not None:
                w = max(ANTIC_HELD * ramp, s)
                c = max((ANTIC_HELD * ramp) ** VOTE_POWER, need_next)
                if w > 0 or c > 0:
                    items.append((self._pk(notes[nxt]), f, w, c, starts[nxt], 0, notes[nxt]))
        return [it for it in items if self._has_key(it[0])]

    def _solve_hand(self, items, comp=0.0, low=1.0):
        """
        Wrist (x, y) and hand turn psi serving the weighted items:
          1. a weighted rigid fit of the natural fingertip spots onto the keys
             (closed form, with the forearm's natural turn as a prior);
          2. a few damped Gauss-Newton steps on a smooth cost that adds
             penalties for any finger that would need more splay or reach
             than its limits allow, and for the wrist turning past its range.
             Keys being pressed (weight 1) dominate; keys further ahead only
             nudge. Turning the hand is cheap, so an out-of-range finger is
             fixed mostly by wrist rotation, the rest by sliding the hand.
        """
        if not items:
            return (self._mx(self.kb.rect.centerx), -150.0, 0.0)
        # One target per finger: a finger heading from one key to the next is
        # somewhere in between, and that's where the hand should lean.
        per = {}
        arriving = {}
        rel = {}
        for p, f, w, c, st, r, n in items:
            x, y = self.key_target(p, f, n)
            per.setdefault(f, []).append((x, y, w, c))
            if r:
                rel[f] = rel.get(f, 0.0) + c
            a = arriving.setdefault(f, [0.0, 0.0])
            a[0] += w * st
            a[1] += w
        fit, lim, imp = [], [], {}
        for f, lst in per.items():
            ws = sum(w for _, _, w, _ in lst)
            if ws > 0:
                imp[f] = ws + 2.0 * max(c for _, _, _, c in lst)    # pull, plus how soon it's needed
                fit.append(((sum(x * w for x, _, w, _ in lst) / ws, sum(y * w for _, y, w, _ in lst) / ws),
                            self.rest_local[f], ws * FIT_WEIGHT[f], f))
            sc = sum(c for _, _, _, c in lst)
            if sc > 1e-9:
                lim.append((sum(x * c for x, _, _, c in lst) / sc,
                            sum(y * c for _, y, _, c in lst) / sc, f, max(c for _, _, _, c in lst),
                            rel.get(f, 0.0) / sc))
        # Targets out of the fingers' natural order (the thumb passing under,
        # a finger crossing over it, or a finger already reaching past the
        # next position) would twist the whole hand in a rigid fit: the
        # less important of each such pair (the one needed later) is left out
        # of the shape fit. The reach limits below still look after it.
        if not fit:
            return (self._mx(self.kb.rect.centerx), -150.0, 0.0)
        wk = self.kb.white_w
        keep = [1.0] * len(fit)
        for i in range(len(fit)):
            for j in range(len(fit)):
                if fit[i][3] < fit[j][3]:
                    v = _smooth((fit[i][0][0] - fit[j][0][0] + 0.5 * wk) / wk)
                    if v > 0:
                        wi, wj = imp[fit[i][3]] ** 6, imp[fit[j][3]] ** 6
                        keep[i] *= 1.0 - 0.98 * v * wj / (wi + wj)
                        keep[j] *= 1.0 - 0.98 * v * wi / (wi + wj)
        # While the thumb passes under the fingers (or they cross over it)
        # the hand turns toward the little finger (clockwise from above for
        # the right hand), which shortens the thumb's reach under the palm.
        # Going the other way (a finger crossing over the thumb) it turns the
        # other way, so the crossing finger points where it's going.
        under = over = 0.0
        th = [e for e in fit if e[3] == 1]
        if th:
            (tx1, _), _, w1, _ = th[0]
            # whichever of the crossed pair plays later is the one crossing
            s1 = arriving[1][0] / max(1e-9, arriving[1][1])
            for (tx, _), _, w, f in fit:
                if f != 1:
                    v = _smooth((tx1 - tx + 0.5 * wk) / wk) * min(1.0, w / 0.5) * min(1.0, w1 / (0.5 * FIT_WEIGHT[1]))
                    sf = arriving[f][0] / max(1e-9, arriving[f][1])
                    k = _smooth(0.5 + (s1 - sf) / 0.1)          # 1: thumb is newer (passing under)
                    under = max(under, v * k)
                    over = max(over, v * (1.0 - k))
        self._bias = self.cross_turn * (over - under)
        fit = [(t, r, w * k) for (t, r, w, _), k in zip(fit, keep)]
        q = list(self._fit_hand(fit))

        rot, S = self._rot, self.S
        dev_lo, dev_hi = (math.radians(a) for a in WRIST_DEV_DEG)
        arm = 3.0 * S
        wsum = sum(w for _, _, w in fit)
        k_soft = SOFT_K
        k_prior = math.sqrt((YAW_PRIOR + CROSS_PRIOR * abs(self._bias) / self.cross_norm) * wsum) * arm
        k_dev = math.sqrt(LIMIT_K * 25) * arm
        lims = []
        for tx, ty, f, c, rshare in lim:
            lo, hi = self._splay_at(f, comp)
            hmin, hmax = self._reach_range(f, self.base_local[f][2] * low + self.travel)
            # keys being pressed don't saturate; keys just let go of barely count
            soft = max(1e-3, 1.0 - c)
            lims.append((tx, ty, self.base_local[f], lo, hi, hmin, hmax, math.sqrt(LIMIT_K * c),
                         0.25 * (1 - rshare + rshare * c) / soft, wk * (1 - rshare + rshare * c) / soft))
        bias = self._bias
        fits = [(t, r, math.sqrt(k_soft * w)) for t, r, w in fit]

        def residuals(wx, wy, psi):
            c, s_ = math.cos(psi), math.sin(psi)
            out = []
            for (tx, ty), (rx, ry), kk in fits:
                out.append(kk * (wx + rx * c - ry * s_ - tx))
                out.append(kk * (wy + rx * s_ + ry * c - ty))
            dpsi = psi - self._yaw(wx)
            out.append(k_prior * (dpsi - bias))
            out.append(k_dev * (max(0.0, dpsi - dev_hi) + min(0.0, dpsi - dev_lo)))
            for tx, ty, (blx, bly, _), lo, hi, hmin, hmax, k, sa, sh in lims:
                dx = tx - (wx + blx * c - bly * s_)
                dy = ty - (wy + blx * s_ + bly * c)
                lx, ly = dx * c + dy * s_, -dx * s_ + dy * c        # into the hand frame
                a, h = math.atan2(lx, ly), math.hypot(lx, ly)
                # saturating for keys not (yet) pressed: one hopelessly out of
                # reach (left behind in a leap) only tugs, it doesn't wrench
                # the hand round
                ea = max(0.0, a - hi) + min(0.0, a - lo)
                eh = max(0.0, h - hmax) + min(0.0, h - hmin)
                out.append(k * h * ea / (1.0 + abs(ea) / sa))
                out.append(k * eh / (1.0 + abs(eh) / sh))
            return out

        eps = (0.5, 0.5, 1e-3)
        for _ in range(GN_ITERS):
            r0 = residuals(*q)
            cols = []
            for j in range(3):
                qq = list(q)
                qq[j] += eps[j]
                cols.append([(a - b) / eps[j] for a, b in zip(residuals(*qq), r0)])
            # (J^T J + damping) dq = -J^T r, in units scaled so psi ~ pixels
            scale = (1.0, 1.0, arm)
            A = [[sum(ci * cj for ci, cj in zip(cols[i], cols[j])) / (scale[i] * scale[j])
                  for j in range(3)] for i in range(3)]
            g = [-sum(ci * r for ci, r in zip(cols[i], r0)) / scale[i] for i in range(3)]
            lam = GN_DAMPING * (A[0][0] + A[1][1] + A[2][2]) / 3 + 1e-9
            for i in range(3):
                A[i][i] += lam
            dq = _solve3(A, g)
            q = [q[i] + dq[i] / scale[i] for i in range(3)]
        psi0 = self._yaw(q[0])
        q[2] = _clamp(q[2], psi0 + dev_lo, psi0 + dev_hi)           # never past the wrist's range
        return tuple(q)

    def _fit_hand(self, pts):
        """Weighted rigid fit of the natural fingertip spots onto the keys."""
        rot, S = self._rot, self.S
        dev_lo, dev_hi = (math.radians(a) for a in WRIST_DEV_DEG)
        ws = sum(w for _, _, w in pts)
        tbx = sum(t[0] * w for t, _, w in pts) / ws
        tby = sum(t[1] * w for t, _, w in pts) / ws
        rbx = sum(r[0] * w for _, r, w in pts) / ws
        rby = sum(r[1] * w for _, r, w in pts) / ws
        C = Sn = 0.0
        for (tx, ty), (rx, ry), w in pts:
            ax, ay, bx, by = rx - rbx, ry - rby, tx - tbx, ty - tby
            C += w * (ax * bx + ay * by)
            Sn += w * (ax * by - ay * bx)
        # the forearm's own turn (plus any crossing turn), as if a pair of points
        prior = (YAW_PRIOR + CROSS_PRIOR * abs(self._bias) / self.cross_norm) * ws * (3.0 * S) ** 2
        wx, wy = tbx - rbx, tby - rby
        for _ in range(2):
            psi0 = self._yaw(wx)
            pb = psi0 + self._bias
            psi = math.atan2(Sn + prior * math.sin(pb), C + prior * math.cos(pb))
            psi = _clamp(psi, psi0 + dev_lo, psi0 + dev_hi)
            ox, oy = rot(rbx, rby, psi)
            wx, wy = tbx - ox, tby - oy
        return wx, wy, psi

    def _hand_at(self, t):
        """
        Hand pose at t, smoothed over time with a short triangular window so
        quick shifts accelerate and settle instead of snapping. Solves are
        made on a fixed time grid and cached, so each frame costs one or two.
        """
        g = HAND_GRID_T
        if self.smooth_t <= 0:
            return self._solve_hand(self._items_at(t), self._run_w(t), self._low(t))
        q = self._hand_avg(t, self.smooth_t)
        w = self._run_w(t)
        if w > 0:
            # a scale run: the wrist glides - its path averaged over the
            # run's own notes - and the fingers do the crossing
            a, b = self._run_span(t)
            glide = self._hand_avg(t, RUN_GLIDE_T, a - RUN_RAMP_T, b + RUN_RAMP_T)
            q = tuple(_lerp(x, y, w) for x, y in zip(q, glide))
        return q

    def _hand_avg(self, t, span, lo=-math.inf, hi=math.inf):
        """The solved hand averaged over t +- span (triangular), only from poses within lo..hi."""
        g = HAND_GRID_T
        k0 = math.ceil((t - span) / g)
        k1 = math.floor((t + span) / g)
        sx = sy = sp = sw = 0.0
        for k in range(k0, k1 + 1):
            w = 1.0 - abs(k * g - t) / span
            if w <= 0 or not lo <= k * g <= hi:
                continue
            x, y, p = self._grid_pose(k)
            sx, sy, sp, sw = sx + w * x, sy + w * y, sp + w * p, sw + w
        return sx / sw, sy / sw, sp / sw

    def idle_at(self, t):
        """0..1 how idle this hand is at t (see idle_weight)."""
        return idle_weight(self.spans, self.span_starts, t) if self.spans else 1.0

    def _placed_at(self, t):
        """
        _hand_at, moved out of the other hand's way while this one is idle
        and the other is playing: the idle hand keeps clear of where the
        playing hand is and is about to be, and drifts after it when it
        gets far away, so the playing hand never has to cross over it.
        """
        wx, wy, psi = self._hand_at(t)
        p = self.partner
        if p is None or not p.spans:
            return wx, wy, psi
        p._ensure_layout(self.kb)
        g, span = IDLE_GRID_T, IDLE_SMOOTH_T
        k0, k1 = math.ceil((t - span) / g), math.floor((t + span) / g)
        sd = sw = 0.0
        for k in range(k0, k1 + 1):
            w = 1.0 - abs(k * g - t) / span
            if w > 0:
                d = self._idle_cache.get(k)
                if d is None:
                    if len(self._idle_cache) > 4000:
                        self._idle_cache.clear()
                    d = self._idle_cache[k] = self._idle_shift(k * g)
                sd, sw = sd + w * d, sw + w
        dx = sd / sw if sw else 0.0
        if abs(dx) < 1e-6:
            return wx, wy, psi
        return wx + dx, wy, psi + self._yaw(wx + dx) - self._yaw(wx)

    def _idle_shift(self, t):
        """How far (working-frame x, + = away from the other hand) to move the idle hand at t."""
        p = self.partner
        w = self.idle_at(t) * (1.0 - p.idle_at(t))
        if w <= 0.0:
            return 0.0
        raw = self._hand_at(t)[0]
        span = self.geo.span_units() * self.S
        i1 = math.floor(t / IDLE_TICK_T)
        ticks = range(i1 - int(round(IDLE_TRAIL_T / IDLE_TICK_T)), i1 + 1)
        trail = sum(self._partner_x(i) for i in ticks) / len(ticks)
        lo = self._clear_line(int(round(t / IDLE_GRID_T))) + IDLE_CLEAR_SPAN * span
        hi = max(lo, trail + IDLE_FAR_SPAN * span)
        target = min(max(raw, lo), hi)
        if target > raw:                             # stay on the keyboard
            edge = max(self._mx(self.kb.rect.left), self._mx(self.kb.rect.right)) - IDLE_EDGE_IN * self.ppi
            target = min(target, max(raw, edge))
        return w * (target - raw)

    def _partner_x(self, i):
        """The other hand's wrist x at tick i (IDLE_TICK_T), in this hand's frame."""
        x = self._path_cache.get(i)
        if x is None:
            if len(self._path_cache) > 20000:
                self._path_cache.clear()
            p = self.partner
            x = self._path_cache[i] = self._mx(p._mx(p._grid_pose(int(round(i * IDLE_TICK_T / HAND_GRID_T)))[0]))
        return x

    def _clear_line(self, k):
        """
        On the idle grid: how far toward this hand the other one reaches now
        and over the next IDLE_LOOK_T (in this hand's frame), remembered
        through the idle stretch with a fall-off of IDLE_DRIFT_IN, so that
        once the playing hand has passed, the idle one drifts back rather
        than springing.
        """
        cache, p, g = self._clear_cache, self.partner, IDLE_GRID_T
        if k in cache:
            return cache[k]
        if len(cache) > 20000:
            cache.clear()
        at, tick = self._partner_x, IDLE_TICK_T

        def ahead(j):
            i0 = math.floor(j * g / tick)
            return max(at(i) for i in range(i0, math.ceil((j * g + IDLE_LOOK_T) / tick) + 1))
        todo, j = [], k
        while j not in cache and len(todo) < IDLE_MEMORY_T / g and \
                (j == k or self.idle_at(j * g) * (1.0 - p.idle_at(j * g)) > 0.0):
            todo.append(j)
            j -= 1
        prev = cache.get(j)
        fall = IDLE_DRIFT_IN * self.ppi * g
        for j in reversed(todo):
            a = ahead(j)
            prev = cache[j] = a if prev is None else max(a, prev - fall)
        return prev

    def _grid_pose(self, k):
        cache = self._cache
        pose = cache.get(k)
        if pose is None:
            if len(cache) > 4000:
                cache.clear()
            t = k * HAND_GRID_T
            pose = cache[k] = self._solve_hand(self._items_at(t), self._run_w(t), self._low(t))
        return pose

    # ----- fingertip timeline -------------------------------------------------
    def _prep_window(self, f, i):
        """(prep_start, strike_start, strike) for finger f's note i+1 (i = its last note, or -1)."""
        starts, ends = self.finger_starts[f], self.finger_ends[f]
        nxt = starts[i + 1]
        # a long trip at the top speed may start earlier than usual, and
        # when time is short the final drop is cut so the trip keeps to it
        need = self.lead.get(id(self.by_finger[f][i + 1]), 0.0)
        prep_start = max(nxt - max(self.prep_max_t, STRIKE_MIN_T + need), ends[i] if i >= 0 else -math.inf)
        window = max(0.0, nxt - prep_start)
        strike = min(STRIKE_T, 0.35 * window)
        if window - strike < need:
            strike = min(strike, max(STRIKE_MIN_T, window - need))
        return prep_start, nxt - strike, strike

    def _travel(self, f, i, t):
        """
        How far (0..1) finger f has travelled from its last key toward the
        next one at time t. It leaves as soon as it is free and gets there
        early (TRAVEL_SHARE of the time), then hovers, ready to strike.
        """
        prep_start, strike_start, _ = self._prep_window(f, i)
        window = strike_start - prep_start
        # arrive early and hover - unless that would mean going faster than the top speed
        need = self.lead.get(id(self.by_finger[f][i + 1]), 0.0)
        span = max(1e-3, window * self.travel_share, min(need, window))
        return _smooth((t - prep_start) / span), prep_start

    def _tip_target(self, f, t, rest_xy, hand=None):
        """
        (x, y, z) world target for fingertip f at time t, and how busy it is
        (0..1). With the hand's (wx, wy, psi), keys are aimed at where this
        hand can play them (_key_spot), the same spot all the way from the
        approach through the strike, the press and the release.
        """
        S = self.S
        hover = HOVER[f] * S + self.retract_up_in * self.ppi * (0.5 if f == 1 else 1.0)
        prep = self.prep_h[f] * S
        travel = self.travel
        notes, starts, ends = self.by_finger[f], self.finger_starts[f], self.finger_ends[f]
        i = bisect.bisect_right(starts, t) - 1
        prev = notes[i] if i >= 0 else None
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        prev_end = ends[i] if prev else -math.inf
        comp, low = self._run_w(t), self._low(t)

        if prev and t < prev_end:                                    # pressing
            kx, ky = self._key_spot(self._pk(prev), f, hand, note=prev, comp=comp, low=low)
            return (kx, ky, -travel * min(1.0, (t - starts[i]) / PRESS_T)), 1.0

        if prev:                                                     # released
            kx, ky = self._key_spot(self._pk(prev), f, hand, note=prev, comp=comp, low=low)
            since = t - prev_end
            z = _lerp(-travel, hover, _ease_out(since / RELEASE_T))
            s = _smooth((since - LINGER_T) / RETURN_T)
            idle = (_lerp(kx, rest_xy[0], s), _lerp(ky, rest_xy[1], s), z)
            busy = 1.0 - s
        else:
            idle = (rest_xy[0], rest_xy[1], hover)
            busy = 0.0

        if nxt is None:
            return idle, busy
        # Head for the next key as soon as this finger is free (but not more
        # than PREP_MAX_T ahead), arriving raised and ready to strike.
        prep_start, strike_start, strike = self._prep_window(f, i)
        if t < prep_start:
            return idle, busy
        s = self._travel(f, i, t)[0] if t < strike_start else 1.0
        kx, ky = self._key_spot(self._pk(nxt), f, hand, note=nxt, soft=1.0 - s, comp=comp, low=low)
        if t < strike_start:
            arc = 0.0
            if f != 1:   # fingers arc up and over; the thumb slides under instead
                arc = min(0.3 * abs(kx - idle[0]), 1.5 * S)
                tx = self._thumb_x(t)
                if tx is not None and kx < tx - 0.3 * self.kb.white_w and idle[0] > kx:
                    arc = max(arc, self.cross_arc_in * self.ppi)      # crossing over the thumb
                arc *= math.sin(math.pi * s)
            return ((_lerp(idle[0], kx, s), _lerp(idle[1], ky, s), _lerp(idle[2], prep, s) + arc),
                    max(busy, s))
        s = (t - strike_start) / max(1e-3, strike)
        return (kx, ky, _lerp(prep, 0.0, s * s)), 1.0

    def _thumb_x(self, t):
        """Working-frame x of the key the thumb is on (or last played), if any."""
        starts = self.finger_starts[1]
        i = bisect.bisect_right(starts, t) - 1
        if i < 0:
            return None
        return self.key_target(self._pk(self.by_finger[1][i]), 1)[0]

    def _shaped_rests(self, busy, local):
        """
        Resting spots (hand frame) for the fingers, shifted so idle fingers
        fan out between busy neighbours: when 1 and 5 stretch an octave the
        middle fingers spread with them, when the hand closes they close too.
        """
        rest = self.rest_local
        disp = {}
        for g in range(1, 6):
            if busy[g] <= 0.05:
                continue
            dx, dy = local[g][0] - rest[g][0], local[g][1] - rest[g][1]
            # a thumb tucked under (or a finger crossed over the thumb) says
            # nothing about where the other fingers belong
            if g == 1 and local[1][0] > rest[2][0]:
                continue
            if g > 1 and busy[1] > 0.05 and local[g][0] < local[1][0]:
                continue
            disp[g] = (dx * busy[g], dy * busy[g])
        cap_x, cap_y = 0.9 * WHITE_KEY_IN * self.ppi, 0.5 * WHITE_KEY_IN * self.ppi
        out = {}
        for f in range(1, 6):
            lo = max((g for g in disp if g < f), default=None)
            hi = min((g for g in disp if g > f), default=None)
            if lo is not None and hi is not None:
                s = (f - lo) / (hi - lo)
                dx = _lerp(disp[lo][0], disp[hi][0], s)
                dy = _lerp(disp[lo][1], disp[hi][1], s)
            elif lo is not None or hi is not None:
                g = lo if lo is not None else hi
                k = SHAPE_FALLOFF ** abs(f - g)
                dx, dy = disp[g][0] * k, disp[g][1] * k
            else:
                dx = dy = 0.0
            out[f] = (rest[f][0] + _clamp(dx, -cap_x, cap_x), rest[f][1] + _clamp(dy, -cap_y, cap_y))
        return out

    def _limit_tip(self, f, tip, wx, wy, psi, comp=0.0, stretch=1.0, low=1.0):
        """
        Clamp a fingertip target to the finger's splay and reach range (widened
        inward in a run, `comp`; the full stretch only as far as `stretch`).
        """
        # a finger already down on its key may stretch a touch further
        # rather than slide off it while the hand is still moving
        slack = math.radians(PRESS_SLACK_DEG[f]) if tip[2] < 0 else 0.0
        x, y = self._clamp_tip(f, tip[0], tip[1], tip[2], wx, wy, psi, slack, comp=comp, stretch=stretch, low=low)
        return (x, y, tip[2])

    # ----- full pose ------------------------------------------------------------
    def pose(self, t, kb):
        """
        Skeleton at time t (see _finger_pose), blended into the glissando
        pose (_gliss_pose) around this hand's glissandos.
        """
        self._ensure_layout(kb)
        g, ep = self._gliss_w(t)
        if g <= 0.0:
            return self._finger_pose(t, kb)
        gp = self._gliss_pose(t, ep, kb, g)
        if g >= 1.0:
            return gp
        return _blend_pose(self._finger_pose(t, kb), gp, g)

    # ----- glissandos ------------------------------------------------------------
    def _gliss_paths(self):
        """
        Per glissando episode, in the working frame: (start, end, [(t, x, y,
        direction)] - where the backs of the fingers touch the keys at each
        note, and which way the run goes (+1: toward the little finger).
        """
        if self._gliss_cache is not None:
            return self._gliss_cache
        out = []
        for ep in self.gliss_eps:
            pts = []
            for r in ep:
                xs = [self.key_target(n.pitch, 2)[0] for n in r]
                d = 1.0 if xs[-1] >= xs[0] else -1.0
                front = self.kb.rect.h - self.kb.black_h
                white_in = GLISS_WHITE_IN if d > 0 else GLISS_THUMB_IN
                for n, x in zip(r, xs):
                    y = front + GLISS_BLACK_IN * self.ppi if is_black_key(n.pitch) else white_in * self.ppi
                    pts.append((n.start, x, y, d))
            last = ep[-1][-1]
            out.append((ep[0][0].start, max(last.start, min(last.end, last.start + 0.12)), pts))
        self._gliss_cache = out
        return out

    def _gliss_w(self, t):
        """(0..1 how much the hand is in its glissando pose at t, that episode's index or None)."""
        if not self.gliss_eps:
            return 0.0, None
        paths = self._gliss_paths()
        i = bisect.bisect_right(self._gliss_starts, t + GLISS_RAMP_T) - 1
        if i < 0:
            return 0.0, None
        t0, t1, _ = paths[i]
        if t < t0:
            return _smooth(1.0 - (t0 - t) / GLISS_RAMP_T), i
        if t <= t1:
            return 1.0, i
        if t < t1 + GLISS_RAMP_T:
            return _smooth(1.0 - (t - t1) / GLISS_RAMP_T), i
        return 0.0, None

    def _gliss_contact(self, t, i):
        """(x, y, roll direction -1..1) where the fingertips are on the keys at t in episode i."""
        pts = self._gliss_paths()[i][2]
        if t <= pts[0][0]:
            return pts[0][1], pts[0][2], pts[0][3]
        if t >= pts[-1][0]:
            return pts[-1][1], pts[-1][2], pts[-1][3]
        k = bisect.bisect_right([p[0] for p in pts], t) - 1
        (ta, xa, ya, da), (tb, xb, yb, db) = pts[k], pts[k + 1]
        u = (t - ta) / max(1e-6, tb - ta)
        if da != db or tb - ta > 0.2:
            u = _smooth(u)                    # a break between glissandos: travel there and turn round
        return _lerp(xa, xb, u), _lerp(ya, yb, u), _lerp(da, db, u)

    def _gliss_pose(self, t, i, kb, g=1.0):
        """
        The glissando pose. Sliding toward the little finger (RH up, LH
        down): the hand flat and turned over, palm up, the fingers straight
        and together, the thumb tucked in along the index, the fingers
        trailing - the backs of the fingertips (the nails) on the keys.
        Sliding toward the thumb (RH down, LH up): palm down, fingers 2-5
        curled right in, the thumb straight out along the keys, its nail on
        them. One family of poses (u: 1 the first, 0 the second), so turning
        round between glissandos goes smoothly from one to the other.
        """
        S, geo, rot = self.S, self.geo, self._rot
        cx, cy, d = self._gliss_contact(t, i)
        m3 = geo.mcp[3]
        sq = lambda p: (m3[0] + (p[0] - m3[0]) * GLISS_SQUEEZE, p[1], p[2])
        mcps = {f: sq(geo.mcp[f]) for f in range(2, 6)}
        bases = {f: (geo.mc_base[f][0] * (0.5 + 0.5 * GLISS_SQUEEZE),) + tuple(geo.mc_base[f][1:]) for f in range(2, 6)}
        cmc = geo.thumb_cmc
        u = _smooth((d + 1.0) / 2.0)          # 1: toward the little finger (palm up), 0: toward the thumb

        def unit(v):
            n_ = math.sqrt(sum(c * c for c in v))
            return tuple(c / n_ for c in v)
        fd = unit(GLISS_FINGER_DIR)
        chains = {}
        for f in range(2, 6):
            L = sum(geo.bones[f]) * 0.98
            flat = _add(mcps[f], _mul(fd, L))                   # straight, side by side
            curled = _add(mcps[f], GLISS_CURL)                  # curled into the palm
            chains[f] = solve_chain(mcps[f], _lerp3(curled, flat, u), list(geo.bones[f]), (0.0, 0.0, 1.0),
                                    FINGER_COUPLING, FINGER_BEND_MAX)
        tucked = _add(mcps[2], GLISS_THUMB_TIP)
        out = _add(cmc, _mul(unit(GLISS_THUMB_DIR), sum(geo.bones[1]) * 0.98))
        thumb = solve_chain(cmc, _lerp3(out, tucked, u), list(geo.bones[1]), (-0.85, 0.0, 0.5),
                            THUMB_COUPLING, THUMB_BEND_MAX)
        # what touches the keys: the backs of the index and middle fingertips, or the thumb's nail
        apex = _lerp3(thumb[-1], _lerp3(chains[2][-1], chains[3][-1], 0.5), u)

        # turned over (palm up) and turned so the fingers trail, tipped down a little
        zc = WRIST_Z
        # (turning over as it blends in, so the blend never folds the hand flat)
        ro = math.radians(GLISS_ROLL_DEG) * g * u
        ph = math.radians(_lerp(GLISS_THUMB_PITCH_DEG, GLISS_PITCH_DEG, u))
        cr, sr, cp, sp = math.cos(ro), math.sin(ro), math.cos(ph), math.sin(ph)
        # palm up the fingers trail; with the thumb, the hand turns to lay the thumb along the keys
        tv = (thumb[-1][0] - cmc[0], thumb[-1][1] - cmc[1])
        turn = _lerp(math.atan2(tv[0], tv[1]), math.radians(GLISS_YAW_DEG), u)
        psi = self._yaw(cx) * u + turn

        def place(p, roll=True):
            x, y, z = p
            if roll:
                x, z = x * cr + (z - zc) * sr, zc - x * sr + (z - zc) * cr
            y, z = y * cp + (z - zc) * sp, zc - y * sp + (z - zc) * cp
            x, y = rot(x * S, y * S, psi)
            return (x, y, z * S)
        # resting on the keys: no part of a finger below them
        low = min((place(p) for c in list(chains.values()) + [thumb] for p in c[1:]), key=lambda p: p[2])
        ap = place(apex)
        # the point of the fingers on the contact point, nothing below the keys
        ox, oy, oz = cx - ap[0], cy - ap[1], -self.travel * 0.5 - low[2]

        def world(p, roll=True):
            x, y, z = place(p, roll)
            return (ox + x, oy + y, oz + z)
        wr, wu = world(geo.wrist_sides[0], False), world(geo.wrist_sides[1], False)
        wcmc = world(cmc)
        wb = {f: world(bases[f]) for f in range(2, 6)}
        bones, joints = [], []
        back = rot(0.0, -1.0, -0.45 * math.atan2(cx - self.shoulder_x, self.forearm_len) + GLISS_ARM_SHARE * turn)
        fl = 12 * self.ppi
        for p in (wr, wu):
            bones.append((p, (p[0] + back[0] * fl, p[1] + back[1] * fl, p[2] + 0.5 * S), "forearm"))
        ring = [wr, wcmc, wb[2], wb[3], wb[4], wb[5], wu]
        for a, b in zip(ring, ring[1:]):
            bones.append((a, b, "carpal"))
        bones.append((wr, wu, "carpal"))
        out_chains = {}
        for f in range(2, 6):
            pts = [world(p) for p in chains[f]]
            bones.append((wb[f], pts[0], "metacarpal"))
            out_chains[f] = [wb[f]] + pts
            for (a, b), kind in zip(zip(pts, pts[1:]), ("proximal", "middle", "distal")):
                bones.append((a, b, kind))
            joints += [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
        pts = [world(p) for p in thumb]
        out_chains[1] = pts
        for (a, b), kind in zip(zip(pts, pts[1:]), ("metacarpal", "proximal", "distal")):
            bones.append((a, b, kind))
        joints += [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
        joints += [(p, "wrist") for p in (wr, wu)]
        arm_end = ((wr[0] + wu[0]) / 2 + back[0] * fl, (wr[1] + wu[1]) / 2 + back[1] * fl,
                   (wr[2] + wu[2]) / 2 + 0.5 * S)
        wx = (wr[0] + wu[0]) / 2
        wy = (wr[1] + wu[1]) / 2
        # palm up, the fingers' nails are on the keys: of the nails only the thumb's shows
        hide = (2, 3, 4, 5)
        palm_up = math.cos(ro) < 0.0                 # turned over past its side: we see the palm
        struct = {"chains": out_chains, "wrist": (wr, wu), "arm_end": arm_end, "mirror": self.mirror,
                  "nail_hide": hide, "palm_up": palm_up}
        if self.mirror:
            fx = lambda p: (2 * self.axis_x - p[0], p[1], p[2])
            bones = [(fx(a), fx(b), k) for a, b, k in bones]
            joints = [(fx(p), k) for p, k in joints]
            struct = {"chains": {f: [fx(p) for p in c] for f, c in out_chains.items()},
                      "wrist": (fx(wr), fx(wu)), "arm_end": fx(arm_end), "mirror": True, "nail_hide": hide,
                      "palm_up": palm_up}
        return {"bones": bones, "joints": joints, "front_y": kb.rect.bottom, "ppi": self.ppi,
                "wrist": (wx, wy, psi), "hand": self.hand, "color": self.color,
                "struct": struct, "skin": self.skin, "t": t, "song": self.song}

    def _finger_pose(self, t, kb):
        """
        Skeleton at time t as a dict:
            'bones':  [(p, q, kind)]  3D world points, kind in
                      {'forearm','carpal','metacarpal','proximal','middle','distal'}
            'joints': [(p, kind)]
            'front_y': screen y of the keyboard's front edge (for projection)
        """
        self._ensure_layout(kb)
        S, rot = self.S, self._rot
        wx, wy, psi = self._limited_at(t)
        # The wrist playing (repeated chords, tremolos): the hand is a rigid
        # unit that takes the keys down itself - it drops by the key's depth
        # while a chord is held and springs up between chords (lift), and in
        # tremolos rolls about the forearm (little-finger side down for
        # roll > 0), pivoting on whichever side is holding its keys. The
        # fingers keep their place in the hand, lifting on their own only by
        # gesture_finger_action.
        lift, roll, gw = self._gesture_at(t)
        sin_r, cos_r, xc = math.sin(roll), math.cos(roll), self.geo.mcp[3][0] * S
        hb = [0.0]                              # the hand's vertical offset (set below)

        def droll(lx):
            return -sin_r * (lx - xc)

        def world(p):
            lx = p[0] * S
            # rolled about the forearm axis: the sides dip / rise and draw in a little
            x, y = rot(xc + (lx - xc) * cos_r, p[1] * S, psi)
            return (wx + x, wy + y, (p[2] * S + self.z_off) * low + gw * (hb[0] + droll(lx)))

        # fingertips, held to the top speed across the keys (_limited_tips)
        comp, low = self._run_w(t), self._low(t)
        tips = {f: self._limit_tip(f, p, wx, wy, psi, comp, self._key_weight(f, t)[0], low)
                for f, p in self._limited_tips(t).items()}
        tips = self._separate(tips, wx, wy, psi, t, comp)
        if gw > 0:
            travel = self.travel
            lxs = {f: rot(tips[f][0] - wx, tips[f][1] - wy, -psi)[0] for f in tips}
            press = {f: self._press_amount(f, t) for f in tips}
            ps = sum(press.values())
            if ps > 1e-6:
                held = sum(w * (-travel - droll(lxs[f])) for f, w in press.items()) / ps
                pw = min(1.0, ps)
                hb[0] = (1.0 - pw) * lift + pw * held
            else:
                hb[0] = lift
            for f in tips:
                if self._pressing(f, t):
                    continue                     # on its key (the hand is placed to suit)
                z0 = tips[f][2]
                own = self.gesture_act * z0 if z0 > 0 else 0.0
                # fingers out of the gesture are held a key's depth higher in
                # the hand, so they clear the keys while it is down
                spare = 0.0 if self._near_note(f, t, GESTURE_REPEAT_T) else travel
                rigid = hb[0] + droll(lxs[f]) + own + spare
                z = gw * rigid + (1.0 - gw) * z0
                tips[f] = (tips[f][0], tips[f][1], max(min(z0, 0.0), z))

        bones, joints = [], []
        up = (0.0, 0.0, 1.0)
        geo = self.geo
        wr, wu = world(geo.wrist_sides[0]), world(geo.wrist_sides[1])
        cmc = world(geo.thumb_cmc)
        bases = {f: world(geo.mc_base[f]) for f in range(2, 6)}

        # Forearm: radius and ulna heading back toward the elbow.
        back = self._rot(0.0, -1.0, -0.45 * math.atan2(wx - self.shoulder_x, self.forearm_len))
        fl = 12 * self.ppi
        for p in (wr, wu):
            bones.append((p, (p[0] + back[0] * fl, p[1] + back[1] * fl, p[2] + 0.5 * S), "forearm"))
        # Carpals: a ring from the wrist through the metacarpal bases.
        ring = [wr, cmc, bases[2], bases[3], bases[4], bases[5], wu]
        for a, b in zip(ring, ring[1:]):
            bones.append((a, b, "carpal"))
        bones.append((wr, wu, "carpal"))

        chains = {}
        for f in range(2, 6):
            mcp = world(geo.mcp[f])
            bones.append((bases[f], mcp, "metacarpal"))
            pts = solve_chain(mcp, tips[f], [L * S for L in geo.bones[f]], up,
                              FINGER_COUPLING, FINGER_BEND_MAX)
            chains[f] = [bases[f]] + list(pts)
            for (a, b), kind in zip(zip(pts, pts[1:]), ("proximal", "middle", "distal")):
                bones.append((a, b, kind))
            joints += [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]

        # Thumb: flexes on its side, bowing outward (away from the palm) and a bit up.
        out = self._rot(-1.0, 0.0, psi)
        bulge = (out[0] * 0.85, out[1] * 0.85, 0.5)
        pts = solve_chain(cmc, tips[1], [L * S for L in geo.bones[1]], bulge,
                          THUMB_COUPLING, THUMB_BEND_MAX)
        chains[1] = list(pts)
        for (a, b), kind in zip(zip(pts, pts[1:]), ("metacarpal", "proximal", "distal")):
            bones.append((a, b, kind))
        joints += [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
        joints += [(p, "wrist") for p in (wr, wu)]

        # the hand's structure, for skins (skins.py): finger chains from their
        # base (metacarpal base / thumb CMC) to the tip, the wrist and the forearm
        arm_end = ((wr[0] + wu[0]) / 2 + back[0] * fl, (wr[1] + wu[1]) / 2 + back[1] * fl,
                   (wr[2] + wu[2]) / 2 + 0.5 * S)
        struct = {"chains": chains, "wrist": (wr, wu), "arm_end": arm_end, "mirror": self.mirror}
        if self.mirror:
            fx = lambda p: (2 * self.axis_x - p[0], p[1], p[2])
            bones = [(fx(a), fx(b), k) for a, b, k in bones]
            joints = [(fx(p), k) for p, k in joints]
            struct = {"chains": {f: [fx(p) for p in c] for f, c in chains.items()},
                      "wrist": (fx(wr), fx(wu)), "arm_end": fx(arm_end), "mirror": True}
        return {"bones": bones, "joints": joints, "front_y": kb.rect.bottom, "ppi": self.ppi,
                "wrist": (wx, wy, psi), "hand": self.hand, "color": self.color,
                "struct": struct, "skin": self.skin, "t": t, "song": self.song}


# --------------------------------------------------------------------------- #
# Still poses of a hand (for the pianist editor)
# --------------------------------------------------------------------------- #
STRETCH_SPLAY_DEG = {1: -50.0, 2: -10.0, 3: -1.0, 4: 7.0, 5: 16.0}
SPAN_SPLAY_DEG = {1: -math.degrees(_THUMB_MAX_ABD), 2: -22.0, 3: 0.0, 4: 8.0,
                  5: math.degrees(_PINKY_MAX_ABD)}


def static_skeleton(geo, shape="stretched", curl=1.0):
    """
    A still right hand, in model units (wrist centre at the origin, +x toward
    the little finger, +y toward the fingertips, +z up; z = 0 is the key
    surface): {'bones': [(p, q, kind, bone_id)], 'joints': [(p, kind)],
    'tips': {finger: p}}. bone_id is a pianist.BONES id (None for the carpals
    and forearm).

    shape: 'stretched' - every finger straight and flat, fanned out;
           'natural'   - the relaxed curve the animation rests in;
           'span'      - thumb and little finger stretched as far as is
                         comfortable (how the hand span is measured).
    """
    bones, joints, tips, chains = [], [], {}, {}

    def straight(base, ang_deg, lengths, z_drop=0.0):
        a = math.radians(ang_deg)
        d = (math.sin(a), math.cos(a))
        pts, p = [base], base
        total = sum(lengths)
        for L in lengths:
            p = (p[0] + d[0] * L, p[1] + d[1] * L, p[2] - z_drop * L / total)
            pts.append(p)
        return pts

    if shape == "natural":
        u = 1.0 / INCHES_PER_UNIT
        front = geo.mcp[3][1] + geo.rest_reach * curl - WHITE_DEPTH_IN[3] * u
        rest = {f: (geo.mcp[3][0] + (f - 3) * WHITE_KEY_IN * u, front + WHITE_DEPTH_IN[f] * u, HOVER[f])
                for f in range(1, 6)}
    splay = SPAN_SPLAY_DEG if shape == "span" else STRETCH_SPLAY_DEG

    wr, wu = geo.wrist_sides
    cmc = geo.thumb_cmc
    base = geo.mc_base
    bones.append((wr, (wr[0] - 0.4, wr[1] - 6.0, wr[2] + 0.4), "forearm", None))
    bones.append((wu, (wu[0] + 0.4, wu[1] - 6.0, wu[2] + 0.4), "forearm", None))
    ring = [wr, cmc, base[2], base[3], base[4], base[5], wu]
    for a, b in zip(ring, ring[1:]):
        bones.append((a, b, "carpal", None))
    bones.append((wr, wu, "carpal", None))

    for f in range(2, 6):
        mcp = geo.mcp[f]
        bones.append((base[f], mcp, "metacarpal", f"mc{f}"))
        if shape == "natural":
            pts = solve_chain(mcp, rest[f], list(geo.bones[f]), (0.0, 0.0, 1.0),
                              FINGER_COUPLING, FINGER_BEND_MAX)
        else:
            pts = straight(mcp, splay[f], geo.bones[f], z_drop=mcp[2] - 0.3)
        for (a, b), kind, bid in zip(zip(pts, pts[1:]), ("proximal", "middle", "distal"),
                                     (f"pp{f}", f"mp{f}", f"dp{f}")):
            bones.append((a, b, kind, bid))
        joints += [(base[f], "knuckle")] + [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
        tips[f] = pts[3]
        chains[f] = [base[f]] + list(pts)
    if shape == "natural":
        bulge = (-0.85, 0.0, 0.5)
        pts = solve_chain(cmc, rest[1], list(geo.bones[1]), bulge, THUMB_COUPLING, THUMB_BEND_MAX)
    else:
        pts = straight(cmc, splay[1], geo.bones[1], z_drop=cmc[2] - 0.3)
    for (a, b), kind, bid in zip(zip(pts, pts[1:]), ("metacarpal", "proximal", "distal"),
                                 ("mc1", "pp1", "dp1")):
        bones.append((a, b, kind, bid))
    joints += [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
    joints += [(wr, "wrist"), (wu, "wrist")]
    tips[1] = pts[3]
    chains[1] = list(pts)
    arm_end = ((wr[0] + wu[0]) / 2, (wr[1] + wu[1]) / 2 - 7.0, (wr[2] + wu[2]) / 2 + 0.4)
    struct = {"chains": chains, "wrist": (wr, wu), "arm_end": arm_end, "mirror": False}
    return {"bones": bones, "joints": joints, "tips": tips, "struct": struct}


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
BONE_COLOR = (12, 12, 14)
HALO_COLOR = (205, 205, 212)          # thin light outline so black bones read on dark areas
BONE_WIDTH_IN = {"forearm": 0.16, "carpal": 0.10, "metacarpal": 0.14,
                 "proximal": 0.15, "middle": 0.13, "distal": 0.11}
JOINT_RADIUS_IN = {"knuckle": 0.085, "tip": 0.075, "wrist": 0.09}


def halo_for(color):
    """A thin outline that contrasts with the bone colour (light for dark bones, dark for light)."""
    lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    return HALO_COLOR if lum < 140 else (22, 22, 26)


def depth_scale(z, ppi):
    """Higher bones are drawn a little thicker, so the knuckle arch reads from above."""
    return min(1.6, max(0.8, 1.0 + 0.25 * z / ppi))


def bone_width(kind, z, ppi):
    return max(2, int(round(BONE_WIDTH_IN[kind] * ppi * depth_scale(z, ppi))))


def joint_radius(kind, z, ppi):
    return max(2, int(round(JOINT_RADIUS_IN[kind] * ppi * depth_scale(z, ppi))))


def draw_hand(surf, pose):
    draw_hands(surf, [pose])


# --------------------------------------------------------------------------- #
# Hands crossing each other
# --------------------------------------------------------------------------- #
CROSS_GROUP = 0.035         # s, onsets closer than this are one moment
CROSS_MERGE = 0.35          # s, crossings by the same hand closer than this are one episode
CROSS_HOME = 4.0            # s, window for each hand's usual place (simultaneous crossings)
CROSS_RAMP = 0.4            # s, the top hand rises this long before / settles after
CROSS_LIFT_IN = 0.8         # in, how far the top hand's wrist rises over the other hand
CROSS_STALE = 1.5           # s, a hand silent longer than this is placed at its next notes


def crossing_episodes(song):
    """
    [(t0, t1, top_hand, lift 0..1)] for the stretches where the hands cross
    or interlock. The hand that crosses always goes OVER, so the question is
    which hand crossed, and that is decided by who moved into whose ground:

    - the left hand playing above where the right hand is (the left hand's
      new notes above the right hand's current ones) - the left hand crossed
      going up, and it's on top;
    - the right hand playing below where the left hand is - the right hand
      crossed going down, and it's on top.

    A hand that is idle doesn't count: it moves out of the playing hand's way
    instead (HandAnimator._placed_at), so a leap by the other hand is no crossing.
    A crossing lasts until the hands are back on their own sides; the other
    hand playing on underneath meanwhile doesn't change who is on top. When
    both hands move into the crossing at the same moment, the one further
    from its usual place (its mean position in the seconds around) crossed.
    """
    cached = getattr(song, "_crossings", None)
    if cached is not None:
        return cached
    from midi_loader import LEFT
    episodes = []
    groups, spans = {}, {}
    for h in (RIGHT, LEFT):
        ns = sorted((n for n in song.notes if n.hand == h), key=lambda n: n.start)
        gs = []
        for n in ns:
            if gs and n.start - gs[-1][0] < CROSS_GROUP:
                gs[-1][1].append(n.pitch)
            else:
                gs.append((n.start, [n.pitch]))
        groups[h] = [(t, min(ps), max(ps)) for t, ps in gs]
        spans[h] = busy_spans((n.start, n.end) for n in ns)
    if groups[RIGHT] and groups[LEFT]:
        starts = {h: [g[0] for g in groups[h]] for h in groups}
        span_starts = {h: [a for a, _ in spans[h]] for h in spans}

        def current(h, t):
            """
            (low, high) of the hand's latest chord at t (its next one if it has
            been silent), or None while it is idle: then it has moved out of
            the playing hand's way (HandAnimator._placed_at), so it can't be crossed.
            """
            if idle_weight(spans[h], span_starts[h], t) > 0.5:
                return None
            gs, i = groups[h], bisect.bisect_right(starts[h], t + 1e-6) - 1
            if i >= 0 and (t - gs[i][0] < CROSS_STALE or i + 1 >= len(gs)):
                return gs[i][1], gs[i][2]
            return gs[i + 1][1], gs[i + 1][2] if i + 1 < len(gs) else None

        def home(h, t):
            gs, lo = groups[h], bisect.bisect_left(starts[h], t - CROSS_HOME)
            hi = bisect.bisect_right(starts[h], t + CROSS_HOME)
            sel = gs[lo:hi] or gs
            return sum((g[1] + g[2]) / 2 for g in sel) / len(sel)

        moments = sorted({g[0] for h in groups for g in groups[h]})
        clustered = []
        for t in moments:
            if clustered and t - clustered[-1] < CROSS_GROUP:
                continue
            clustered.append(t)
        state, t_on, depth, full = None, 0.0, 0.0, False
        runs = []
        for t in clustered:
            tt = t + CROSS_GROUP * 0.99
            L, R = current(LEFT, tt), current(RIGHT, tt)
            crossed = L is not None and R is not None and L[1] > R[0]
            if not crossed:
                if state is not None:
                    runs.append((t_on, t, state, depth, full))
                    state = None
                continue
            if state is None:
                newL = any(abs(g[0] - t) < CROSS_GROUP for g in groups[LEFT][max(0, bisect.bisect_left(starts[LEFT], t - CROSS_GROUP)):][:2])
                newR = any(abs(g[0] - t) < CROSS_GROUP for g in groups[RIGHT][max(0, bisect.bisect_left(starts[RIGHT], t - CROSS_GROUP)):][:2])
                if newL and not newR:
                    state = LEFT
                elif newR and not newL:
                    state = RIGHT
                else:
                    devL = (L[0] + L[1]) / 2 - home(LEFT, t)
                    devR = home(RIGHT, t) - (R[0] + R[1]) / 2
                    state = LEFT if devL > devR else RIGHT
                t_on, depth, full = t, 0.0, False
            depth = max(depth, L[1] - R[0])
            full = full or (L[0] + L[1]) / 2 > (R[0] + R[1]) / 2
        if state is not None:
            runs.append((t_on, clustered[-1] + 0.3, state, depth, full))
        for t0, t1, top, d, f in runs:
            strength = 1.0 if f else max(0.3, min(1.0, d / 6.0))
            # the hand starts rising when it sets off from its previous chord
            i = bisect.bisect_left(starts[top], t0 - CROSS_GROUP) - 1
            lead = t0 - starts[top][i] if i >= 0 else CROSS_RAMP
            lead = max(0.25, min(0.6, lead))
            if episodes and episodes[-1][2] == top and t0 - episodes[-1][1] < CROSS_MERGE:
                p0, _, _, ps, pl = episodes[-1]
                episodes[-1] = (p0, t1, top, max(ps, strength), pl)
            else:
                episodes.append((t0, t1, top, strength, lead))
    try:
        song._crossings = episodes
    except AttributeError:
        pass
    return episodes


def _crossing_lifts(episodes, t):
    """{hand: 0..1 how far it is raised} at time t."""
    out = {}
    for t0, t1, top, strength, lead in episodes:
        if t0 - lead <= t <= t1 + CROSS_RAMP:
            if t < t0:
                a = (t - (t0 - lead)) / (0.8 * lead)       # fully up a little before the crossing note
            elif t > t1:
                a = 1.0 - (t - t1) / CROSS_RAMP
            else:
                a = 1.0
            a = max(0.0, min(1.0, a))
            a = a * a * (3 - 2 * a)
            out[top] = max(out.get(top, 0.0), a * strength)
        elif t0 - 0.6 > t:
            break
    return out


def _crossing_at(episodes, t):
    """(top hand, 0..1 how far it is raised) at time t."""
    lifts = _crossing_lifts(episodes, t)
    if not lifts:
        return None, 0.0
    top = max(lifts, key=lifts.get)
    return top, lifts[top]


def _lift_pose(pose, dz):
    """A copy of pose with the wrist, palm and forearm raised by dz pixels, fading to 0 at the fingertips."""
    st = pose["struct"]
    y_tip = max(c[-1][1] for c in st["chains"].values())
    wr, wu = st["wrist"]
    y_wr = (wr[1] + wu[1]) / 2
    span = max(1e-6, y_tip - y_wr)

    def up(p):
        w = max(0.0, min(1.0, (y_tip - p[1]) / span)) ** 0.8
        return (p[0], p[1], p[2] + dz * w)
    out = dict(pose)
    out["struct"] = dict(st, chains={f: [up(p) for p in c] for f, c in st["chains"].items()},
                         wrist=(up(wr), up(wu)), arm_end=up(st["arm_end"]))
    out["bones"] = [(up(a), up(b), k) for a, b, k in pose["bones"]]
    out["joints"] = [(up(p), k) for p, k in pose["joints"]]
    return out


def arrange_crossing(poses):
    """
    Order (and lift) two hands that cross: the crossing hand is drawn on top
    and its wrist raised above the other. Other poses pass through unchanged.
    """
    hands_ = {p.get("hand"): p for p in poses}
    if len(poses) != 2 or len(hands_) != 2 or "song" not in poses[0] or poses[0]["song"] is None:
        return poses
    lifts = _crossing_lifts(crossing_episodes(poses[0]["song"]), poses[0].get("t", 0.0))
    lifts = {h: a for h, a in lifts.items() if h in hands_ and a > 0.0}
    if not lifts:
        return poses
    top = max(lifts, key=lifts.get)
    out = []
    for h, p in hands_.items():
        if lifts.get(h):
            p = _lift_pose(p, CROSS_LIFT_IN * p["ppi"] * lifts[h])
        out.append(dict(p, layer=1 if h == top else 0))
    return sorted(out, key=lambda p: p["layer"])


def draw_hands(surf, poses):
    """Draw hand poses with their pianist's skin (skins.py; 'skeleton' is drawn here)."""
    poses = arrange_crossing(poses)
    poses = sorted(poses, key=lambda p: p.get("layer", 0))
    skinned = [p for p in poses if (p.get("skin") or {}).get("style", "skeleton") != "skeleton"]
    if skinned:
        from skins import draw_poses
        draw_poses(surf, skinned)
    bare = [p for p in poses if p not in skinned]
    if bare:
        draw_skeletons(surf, bare)


def draw_skeletons(surf, poses):
    """Draw one or more hand poses as bones and joints, lowest bones first across all of them
    (layer by layer: a hand crossing over the other is a layer above it)."""
    import pygame  # only needed for drawing

    if not poses:
        return
    layers = sorted({p.get("layer", 0) for p in poses})
    if len(layers) > 1:
        for L in layers:
            draw_skeletons(surf, [dict(p, layer=0) for p in poses if p.get("layer", 0) == L])
        return
    fy, ppi = poses[0]["front_y"], poses[0]["ppi"]

    def scr(p):
        return (int(round(p[0])), int(round(fy - p[1])))

    try:
        from skins import draw_webs
        draw_webs(surf, [(pose["struct"], (lambda q, fy=pose["front_y"]: (q[0], fy - q[1], q[2])),
                          pose["ppi"], pose.get("color", BONE_COLOR)) for pose in poses if "struct" in pose])
    except ImportError:
        pass

    items = []
    for pose in poses:
        color = pose.get("color", BONE_COLOR)
        halo = halo_for(color)
        for a, b, kind in pose["bones"]:
            items.append(((a[2] + b[2]) * 0.5, "bone", a, b, kind, color, halo))
        for p, kind in pose["joints"]:
            items.append((p[2] + 0.01, "joint", p, None, kind, color, halo))
    items.sort(key=lambda it: it[0])                    # lowest first

    for _, typ, a, b, kind, color, halo in items:
        if typ == "bone":
            w = bone_width(kind, (a[2] + b[2]) * 0.5, ppi)
            pa, pb = scr(a), scr(b)
            pygame.draw.line(surf, halo, pa, pb, w + 2)
            pygame.draw.circle(surf, halo, pa, (w + 2) // 2)
            pygame.draw.circle(surf, halo, pb, (w + 2) // 2)
            pygame.draw.line(surf, color, pa, pb, w)
            pygame.draw.circle(surf, color, pa, w // 2)
            pygame.draw.circle(surf, color, pb, w // 2)
        else:
            r = joint_radius(kind, a[2], ppi)
            pygame.draw.circle(surf, halo, scr(a), r + 1)
            pygame.draw.circle(surf, color, scr(a), r)
