"""
hands.py - Procedural hand skeletons for Hand-thesia (both hands).

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
  3. draw_hands() projects the skeleton straight down onto the screen and draws
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
import time
from operator import mul

import midi_loader
import progress
from fingering import group_notes, is_crossing, key_pos, mirror_pitch, plan_fingering
from midi_loader import LEFT, RIGHT, is_black_key

# --------------------------------------------------------------------------- #
# Real-world sizes
# --------------------------------------------------------------------------- #
OCTAVE_IN = 6.5
WHITE_KEY_IN = OCTAVE_IN / 7.0
HAND_SPAN_IN = 8 * WHITE_KEY_IN          # 9th: key centres C .. D an octave up
KEY_TRAVEL_IN = 0.4                      # how far a key goes down

# Where on the key each finger lands, in inches back from the white keys' front
# edge. The curve of the fingertips puts 3 furthest in and the thumb nearest.
WHITE_DEPTH_IN = {1: 0.35, 2: 1.70, 3: 1.95, 4: 1.80, 5: 0.90}
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
# Linked tendons: a finger curving down takes these with it (pianist "tendon_link" of the way,
# times how far down it is), unless they're reaching for a key; never below TENDON_FLOOR_IN
TENDON_FOLLOWERS = {3: (4,), 4: (3,), 5: (3, 4)}
TENDON_FLOOR_IN = 0.15      # in, above the key tops
THUMB_BRIDGE_LIFT_IN = 0.05 # in: a thumb bridging two black keys lies flat across them, its MCP joint this little higher...
THUMB_BRIDGE_DEPTH_IN = 1.4 # in: ...its tip this far in along the far key (BLACK_SPAN_IN), so it crosses the near one...
THUMB_BRIDGE_NEAR_IN = 0.3  # ...and over the near one at least this far in from its front (the hand comes in for that)
PREP = {1: 1.3, 2: 2.6, 3: 2.6, 4: 2.6, 5: 2.4}       # raised, ready to strike

# Joint behaviour
FINGER_COUPLING = 0.6       # DIP bends 0.6 as much as PIP (the author's hand playing: ~0.5; 0.75 hooked the tips)
FINGER_BEND_MAX = 1.7       # rad, PIP limit
THUMB_COUPLING = 0.85
THUMB_BEND_MAX = 1.2

# Timing (seconds)
PRESS_T = 0.035             # key going down after the strike
RELEASE_T = 0.12            # lift after a note ends
STRIKE_T = 0.12             # final drop onto the key
LINGER_T = 0.06             # a released finger stays over its key this long
RETURN_T = 0.35             # ... then drifts back to its resting spot
# How strongly each finger pulls the hand toward its natural spot when a chord
# is wider than the resting hand: the thumb is flexible and takes most of the
# stretch, the little finger barely abducts, so it stays near its rest position.
FIT_WEIGHT = {1: 0.35, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.6}
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
REACH_COMFORT = {1: 0.97, 2: 0.98, 3: 0.98, 4: 0.98, 5: 0.98}   # (a playing finger is long, gently arched)
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
HAND_GRID_T = 1 / 120       # the hand's poses are solved on this time grid (smoothed over pianist "smoothness")

# Anticipation
RELEASE_HOLD_T = 0.25       # ...and stops caring about a released key over this long
ANTIC_HELD = 0.3            # how much a finger still holding a key leans toward its next one
RELEASE_NEED_T = 0.08       # a released key stops needing to be reachable this soon...
RELEASE_NEED = 1.0          # ...fading from this much
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
RUN_MIN_NOTES = 7           # single notes (or octaves, double notes) moving by step, at least this many in a row...
RUN_GAP_T = 0.3             # s, ...none further apart than this
RUN_RAMP_T = 0.15           # s, the run's hold on the hand eases in and out over this
RUN_GLIDE_T = 0.25          # s, in a run the wrist is averaged over +-this (the crossings' steps even out)
RUN_TURN_STIFF = 3.0        # in a run the key fit turns the hand this much less readily, moving it instead
TREM_MIN_NOTES = 6          # tremolos (and trills): at least this many strikes, each a repeat of one a few
TREM_PERIOD = 4             # strikes back (up to this many) - the same key or chord again...
TREM_GAP_T = 0.3            # s, ...none further apart than this
TREM_JUMP = 4               # semitones: a tremolo whose lowest or highest key jumps further starts again there
TREM_REACH_EXTRA = 1.25     # white keys: a tremolo's cycle may span this much more than the thumb-little finger stretch
TREM_HOLD_T = 0.5           # s, in a tremolo the wrist is averaged over +-this: it stays put, each finger on its key
RUN_COMPRESS_DEG = {1: (0, 0), 2: (0, 10), 3: (6, 6), 4: (8, 0), 5: (10, 0)}   # extra splay toward the hand's middle
# Glissandos (glissando.py): the hand slides the backs of its fingers along the keys
GLISS_RAMP_T = 0.15         # s, the hand forms the glissando pose this long before / leaves it after
GLISS_SPEED_SHARE = 0.9     # a glissando slides at most this share of the top speed (the hand turns too)
GLISS_EASE_PEAK = 1.5       # an eased step (turning round, after a pause) peaks at this times its mean speed
GLISS_YAW_DEG = 70.0        # the hand turns so its fingers trail the way it slides...
GLISS_ARM_SHARE = 0.6       # ...the forearm turning with it this far (the wrist bends the rest)
GLISS_ROLL_DEG = 180.0      # turned over, palm up: the backs of the fingers (the nails) slide on the keys
GLISS_PITCH_DEG = 8.0       # ...tipped down a little toward the fingertips
GLISS_SQUEEZE = 0.8         # the knuckles drawn together: the fingers side by side, touching
GLISS_FINGER_DIR = (0.0, 1.0, -0.12)   # the fingers straight and parallel, a little down toward the tips
GLISS_THUMB_TIP = (1.8, -1.4, -2.6)    # the thumb tucked into the palm, across it below the knuckles (from the index knuckle, model units)
# Sliding toward the thumb (RH down, LH up) the thumb does it instead: the hand palm down, fingers 2-5
# curled right in, the thumb straight out along the keys, its nail on them
GLISS_CURL = (0.0, 1.4, -3.6)          # a curled fingertip, from its knuckle (model units): a fist, the middle joints down on the keys
GLISS_THUMB_DIR = (-0.18, 1.0, -0.3)   # the straightened thumb, along the fist (the hand turns to lay it along the keys)
GLISS_THUMB_PITCH_DEG = 4.0            # the hand tipped down this much for it
GLISS_WHITE_IN = 0.8        # in, the nails slide this far up the white keys (well clear of the black ones)...
GLISS_BLACK_IN = 0.5        # ...and this far in from the black keys' front
GLISS_TRAVEL_ACC = 40.0     # m/s^2, to and from a glissando the hand speeds up (and slows down) this fast...
GLISS_TRAVEL_DT = 1 / 120   # s, ...worked out in steps this long...
GLISS_TRAVEL_MAX_T = 3.0    # s, ...for at most this long
WARM_AHEAD_T = 3.5          # s, prepare() keeps the hand's motion worked out this far ahead of the song
GLISS_ARRIVE_T = 0.03       # s, rushing to a key after a glissando, the hand is there this long before the strike
GLISS_THUMB_ARM_DEG = 18.0  # with the thumb, the forearm angled this far toward the way it slides (elbow trailing)
GLISS_THUMB_IN = 2.0        # in, with the thumb: its nail up to this far up the white keys, the fist's knuckles over them...
GLISS_FIST_CLEAR = 0.45     # in, ...but the knuckles' centres this far short of the black keys' front (finger radius + a gap)
FLAT_SPAN_WK = (4.5, 6.5)   # keys held this wide (white keys, outermost) start / fully flatten the hand...
FLAT_DROP = 0.6             # ...which lowers its knuckles by this share, so stretched fingers reach further
TIP_GAP_WK = 0.6            # neighbouring fingertips (2-5) keep at least this far apart in a run
TIP_GAP_MIN_WK = 0.4        # ...or as near as their keys are (one on its key), but never nearer than this
PREP_LOOKBACK_T = 2.0       # s, a finger's first key: neighbours' keys this far back may be in its way
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


def _gram(cols):
    """J^T J for the three Jacobian columns (symmetric: each entry summed once)."""
    a00, a01, a02 = sum(map(mul, cols[0], cols[0])), sum(map(mul, cols[0], cols[1])), sum(map(mul, cols[0], cols[2]))
    a11, a12, a22 = sum(map(mul, cols[1], cols[1])), sum(map(mul, cols[1], cols[2])), sum(map(mul, cols[2], cols[2]))
    return [[a00, a01, a02], [a01, a11, a12], [a02, a12, a22]]


def _clamp_apply(prep, x, y):
    """_clamp_tip's clamp, from _clamp_prep's (base, rotations, splay and reach limits)."""
    bx, by, cn, sn, cp, sp, lo, hi, hmin, hmax = prep
    dx, dy = x - bx, y - by
    lx, ly = dx * cn - dy * sn, dx * sn + dy * cn          # into the hand frame (rot by -psi)
    a, h = math.atan2(lx, ly), math.hypot(lx, ly)
    ac = lo if a < lo else hi if a > hi else a
    if ac != a:
        h *= max(0.0, math.cos(a - ac))          # nearest point on the limit's line
    a = ac
    h = hmin if h < hmin else hmax if h > hmax else h
    ux, uy = h * math.sin(a), h * math.cos(a)
    return bx + (ux * cp - uy * sp), by + (ux * sp + uy * cp)


def _shift_pose(p, dx, dy, mirror):
    """Pose p moved by (dx, dy) on screen (its working-frame wrist the other way round when mirrored)."""
    sh = lambda q: (q[0] + dx, q[1] + dy) + tuple(q[2:])
    out = dict(p)
    out["bones"] = [(sh(a), sh(b), k) for a, b, k in p["bones"]]
    out["joints"] = [(sh(q), k) for q, k in p["joints"]]
    st = dict(p["struct"])
    st["chains"] = {f: [sh(q) for q in c] for f, c in st["chains"].items()}
    st["wrist"] = tuple(sh(q) for q in st["wrist"])
    st["arm_end"] = sh(st["arm_end"])
    out["struct"] = st
    wx, wy, psi = p["wrist"]
    out["wrist"] = (wx + (-dx if mirror else dx), wy + dy, psi)
    return out
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


def prepare_hands(animators, t, kb, budget):
    """Share `budget` seconds of a frame's spare time among the hands' work ahead (HandAnimator.prepare)."""
    anims = list(animators)
    end = time.perf_counter() + budget
    for i, a in enumerate(anims):
        left = end - time.perf_counter()
        if left <= 0:
            break
        a.prepare(t, kb, left / (len(anims) - i))


def build_hands(song, **kw):
    """{hand: HandAnimator} for each hand that has notes, paired (pair_hands); `kw` goes to each animator."""
    counts = {h: sum(1 for n in song.notes if n.hand == h) for h in (RIGHT, LEFT)}
    total = sum(counts.values()) or 1
    out, done = {}, 0
    for h in (RIGHT, LEFT):
        if counts[h]:
            with progress.stage(done / total, (done + counts[h]) / total):
                out[h] = HandAnimator(song, h, **kw)
            done += counts[h]
    pair_hands(out.values())
    return out


def load_with_hands(path_or_song, **kw):
    """(song, hands.build_hands(song, **kw)) for a MIDI/PIG path or a song; reports progress (progress.py)."""
    song = path_or_song
    if isinstance(path_or_song, str):
        with progress.stage(0.0, 0.5):
            song = midi_loader.load_song(path_or_song)
    with progress.stage(0.5 if isinstance(path_or_song, str) else 0.0, 1.0):
        return song, build_hands(song, **kw)


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
                     "palm_up": (sb if w > 0.5 else sa).get("palm_up", False),
                     "flush": _lerp(sa.get("flush", 0.0), sb.get("flush", 0.0), w),
                     "thumb_edge": _lerp(sa.get("thumb_edge", 0.0), sb.get("thumb_edge", 0.0), w)}
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
        # the wrist's width (the studio's "Wrist width", anatomy["wrist"]): its two sides further apart or closer;
        # the cuff and forearm the skins draw follow them
        from pianist import WRIST, clamp_wrist
        self.wrist_scale = clamp_wrist(a.get(WRIST, 1.0))
        self.wrist_sides = tuple((x * self.wrist_scale, y, z) for x, y, z in WRIST_SIDES)
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
    original model, from the pianist's "Finger curvature". Sampled from the
    author's hand playing scales (video, 2026-10-03): at the default 40% a
    pressed middle fingertip is 2.4 in in front of its knuckle and 1.8 in
    below it - the first phalanx sloping down ~25 deg, the middle ~45, the
    last ~55, a long, gently arched finger. 100% is the original, curved
    model (1.0); 0% nearly straight (2.16).
    """
    c = pianist.b("finger_curve") if pianist is not None else 0.4
    return 1.0 + 1.16 * (1.0 - c)


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
        go of (fading out) and the key it plays next (fading in over self.antic_t)
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
        # fingering and the fingers' timeline, and keep their times - but for
        # a step further than the slide can go in the time (_gliss_schedule).
        import glissando
        self.gliss_runs = glissando.find(notes, self.pianist)
        self.gliss_ids = {id(n) for r in self.gliss_runs for n in r}
        self.gliss_eps = glissando.episodes(self.gliss_runs, notes, float(self.pianist.b("gliss_merge")))
        self.gliss_start = self._gliss_schedule()
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
                # every chord struck while it is held, not just the next one
                # (1-3 chromatic scales, legato: 3 still on G# when 2 comes
                # to A# after the thumb's A - 2 over 3)
                gi = bisect.bisect_right(starts, n.start + 1e-6)
                while gi < len(self.groups) and starts[gi] <= self.finger_ends[f][i] + 0.1:
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
                    gi += 1
        # Nothing travels faster than the pianist's top speed: keys are let
        # go early enough to get to the next ones, or those are struck late.
        self._speed_schedule()
        self.white_up = self._white_up()
        # what this hand actually plays: [(press, release, note)]
        self.performance = [(self.finger_starts[f][i], self.finger_ends[f][i], n)
                            for f, ns in self.by_finger.items() for i, n in enumerate(ns)]
        self.performance += self._gliss_performance()
        self.spans = busy_spans((s0, e) for s0, e, _ in self.performance)
        self.span_starts = [a for a, _ in self.spans]
        self.partner = None                      # the other hand, see pair_hands
        self._idle_cache, self._clear_cache, self._path_cache = {}, {}, {}
        self._find_gestures()
        self._gliss_cache = None
        self._gliss_follow_cache = {}
        self._follow_jobs = {}
        self._warm_k = None
        self._strike_list = None
        self._prep_cache = {}                    # _blocked_until
        self._gliss_starts = [self.gliss_start[id(ep[0][0])] for ep in self.gliss_eps]
        self.runs = self._find_runs()
        self.run_starts = [a for a, _ in self.runs]
        self.trems = self._find_tremolos()
        self._group_ts = [t for t, _ in self.groups]
        self.trem_starts = [a for a, _ in self.trems]
        self._layout_sig = None

    # ----- scale runs ------------------------------------------------------------
    def _find_runs(self):
        """
        [(start, end)] of this hand's scale runs: RUN_MIN_NOTES or more single
        notes in a row, each a step (1-2 semitones, or a harmonic minor's
        augmented second after a step) from the one before and at most
        RUN_GAP_T after it - or as many octaves (or other double notes) in a
        row, all their notes moving together by the same step (chromatic
        octaves: the hand glides, not turning for each 4 and 5). Arpeggios
        (thirds and wider) never qualify.
        """
        runs, cur = [], []

        def close():
            if len(cur) >= RUN_MIN_NOTES:
                runs.append((cur[0][0], cur[-1][2]))
        for t, ns in self.groups:
            ps = sorted(n.pitch for n in ns)
            if cur:
                prev = cur[-1][1]
                if len(ps) == 1 and len(prev) == 1:
                    d = abs(ps[0] - prev[0])
                    step = 1 <= d <= 2 or (d == 3 and len(cur) > 1 and len(cur[-2][1]) == 1 and
                                           1 <= abs(prev[0] - cur[-2][1][0]) <= 2)
                else:
                    ds = {a - b for a, b in zip(ps, prev)} if len(ps) == len(prev) else set()
                    step = len(ds) == 1 and 1 <= abs(next(iter(ds))) <= 2
                if not step or t - cur[-1][0] > RUN_GAP_T:
                    close()
                    cur = []
            if len(ps) > 2:                     # (chords: not a run, but may start nothing either)
                close()
                cur = []
                continue
            cur.append((t, ps, min(max(n.end for n in ns), t + RUN_GAP_T)))
        close()
        return runs

    def _find_tremolos(self):
        """
        [(start, end)] of this hand's tremolos and trills: TREM_MIN_NOTES or
        more strikes in a row, at most TREM_GAP_T apart, repeating with one
        period p (2 to TREM_PERIOD strikes): every key or chord is struck
        again p strikes later or was p strikes before (so a key that moves
        on, the tremolo's middle note going up a semitone, keeps it going),
        at least two different ones. Played from one place: the wrist holds
        still and each finger stays over its key - so each cycle's keys must
        be within the hand's reach (a repeated broken chord wider than that,
        Chopin's Op. 25 No. 12, is played moving).
        """
        import fingering as fg
        # (a white key past the planner's widest stretch: rocking the forearm, a tremolo reaches a little further)
        reach = fg.BASE_MAX_SPAN[(1, 5)] * reach_scale(self.pianist.anatomy)[(1, 5)] + TREM_REACH_EXTRA
        groups = self.groups
        n = len(groups)
        sets = [frozenset(m.pitch for m in ns) for _, ns in groups]
        spans = []
        for p in range(2, TREM_PERIOD + 1):
            ok = [(i >= p and sets[i] == sets[i - p]) or (i + p < n and sets[i] == sets[i + p]) for i in range(n)]
            lo = None
            for i in range(n + 1):
                if i < n and ok[i] and (lo is None or groups[i][0] - groups[i - 1][0] <= TREM_GAP_T):
                    if lo is None:
                        lo = i
                    continue
                if lo is not None and i - lo >= TREM_MIN_NOTES and len(set(sets[lo:i])) >= 2:
                    spans.append((lo, i - 1))
                lo = i if i < n and ok[i] else None
        merged = []
        for lo, hi in sorted(spans):
            if merged and groups[lo][0] - groups[merged[-1][1]][0] <= TREM_GAP_T and lo <= merged[-1][1] + TREM_PERIOD:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))     # pieces of one tremolo (a stray chord between)
            else:
                merged.append((lo, hi))
        # where the tremolo jumps to a new place (its lowest or highest key moves
        # by more than TREM_JUMP semitones) it is a new tremolo: the hand moves there
        pieces = []
        for lo, hi in merged:
            def rng(i):
                ps = [p for s_ in sets[max(start, i - TREM_PERIOD + 1):i + 1] for p in s_]
                return min(ps), max(ps)
            start = lo
            for i in range(lo + TREM_PERIOD, hi + 1):          # (once a whole cycle is in)
                if i - start < TREM_PERIOD:
                    continue
                (a0, b0), (a1, b1) = rng(i - 1), rng(i)
                if abs(a1 - a0) > TREM_JUMP or abs(b1 - b0) > TREM_JUMP:
                    pieces.append((start, i - 1))
                    start = i
            pieces.append((start, hi))
        out = []
        for lo, hi in pieces:
            if hi - lo + 1 < TREM_MIN_NOTES or len(set(sets[lo:hi + 1])) < 2:
                continue
            kp = [[key_pos(self.vp(p)) for p in s_] for s_ in sets[lo:hi + 1]]
            if any(max(max(c) for c in kp[i:i + TREM_PERIOD]) - min(min(c) for c in kp[i:i + TREM_PERIOD]) > reach
                   for i in range(max(1, len(kp) - TREM_PERIOD + 1))):
                continue
            t1, ns = groups[hi]
            out.append((groups[lo][0], min(max(m.end for m in ns), t1 + TREM_GAP_T)))
        return out

    def _trem_span(self, t):
        """The tremolo (start, end) covering t, or None."""
        i = bisect.bisect_right(self.trem_starts, t) - 1
        if i >= 0 and t < self.trems[i][1]:
            return self.trems[i]
        return None

    def _trem_w(self, t):
        """(0..1 how much t is inside a tremolo, eased in and out over RUN_RAMP_T; that tremolo or None)."""
        r = self._trem_span(t)
        if r is None:
            return 0.0, None
        a, b = r
        # eased in and out inside the tremolo: the hand is free again by its end
        ramp = min(RUN_RAMP_T, (b - a) / 3)
        return _smooth(min((t - a) / ramp, (b - t) / ramp, 1.0)), r

    def _trem_note(self, f, t, r):
        """
        Finger f's key in tremolo r at t: the one it played last, or its next
        - whichever is nearer in time when they differ (a finger the tremolo
        moves on to a new key goes with it).
        """
        a, b = r
        # only a key from the current cycle: within the hand's last (next) TREM_PERIOD strikes
        gts = self._group_ts
        g = bisect.bisect_right(gts, t)
        a = max(a, gts[max(0, g - TREM_PERIOD)] if gts else a)
        b = min(b, gts[min(len(gts) - 1, g + TREM_PERIOD - 1)] if gts else b)
        starts = self.finger_starts[f]
        i = bisect.bisect_right(starts, t) - 1
        last = self.by_finger[f][i] if i >= 0 and starts[i] >= a - 1e-6 else None
        nxt = self.by_finger[f][i + 1] if i + 1 < len(starts) and starts[i + 1] <= b + 1e-6 else None
        if last is None or nxt is None or last.pitch == nxt.pitch:
            return last or nxt
        return last if t - starts[i] <= starts[i + 1] - t else nxt

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
        Each of a pair gives way by its mobility: none while on its key, then
        growing as the key no longer needs reaching (1 - _key_weight: eased
        out after the release, in before the strike) - not all at once at the
        release, which made the neighbours jump in chromatic scales.
        """
        if comp <= 0:
            return tips
        rot, gap = self._rot, TIP_GAP_WK * self.kb.white_w
        fs = [f for f in (2, 3, 4, 5) if f in tips]
        loc = {f: list(rot(tips[f][0] - wx, tips[f][1] - wy, -psi)) for f in fs}
        mob = {f: 0.0 if self._pressing(f, t) else 1.0 - self._key_weight(f, t)[0] for f in fs}
        # next to a finger on its key, a free one keeps no further off than
        # its own last or next key does (adjacent keys are nearer than the
        # gap): pushed further, it stepped back as it let go and jumped on
        # when the neighbour did (1-3 chromatic scales)
        pair_gap = {}
        for a, b in zip(fs, fs[1:]):
            g_ab = gap
            for fixed, free in ((a, b), (b, a)):
                if mob[fixed] > 1e-3 or mob[free] <= 1e-3:
                    continue
                held = self._key_weight(fixed, t)[1]
                if held is None:
                    continue
                k = self._key_x_across(held, fixed, tips[fixed][1], wx, wy, psi)
                for fx in self._near_keys_x(free, t, tips[free][1], wx, wy, psi):
                    apart = (fx - k) if free == b else (k - fx)
                    if apart > 0:               # (a key on the far side was played before the hand moved on)
                        g_ab = min(g_ab, max(TIP_GAP_MIN_WK * self.kb.white_w, apart))
            pair_gap[a] = g_ab
        for _ in range(12):
            moved = False
            for a, b in zip(fs, fs[1:]):
                d = pair_gap[a] - (loc[b][0] - loc[a][0])
                m = mob[a] + mob[b]
                if d <= 1e-6 or m <= 1e-3:
                    continue
                moved = True
                loc[a][0] -= d * mob[a] / m
                loc[b][0] += d * mob[b] / m
            if not moved:
                break
        out = dict(tips)
        k = min(1.0, 3.0 * comp)                  # fully apart early in the run's ease-in
        for f in fs:
            x, y = rot(loc[f][0], loc[f][1], psi)
            ox, oy, oz = tips[f]
            out[f] = (_lerp(ox, wx + x, k), _lerp(oy, wy + y, k), oz)
        return out

    def _near_keys_x(self, f, t, y, wx, wy, psi):
        """Across the hand (as _separate measures), finger f's keys: the one it is on or last left, and its next."""
        starts = self.finger_starts[f]
        i = bisect.bisect_right(starts, t) - 1
        return [self._key_x_across(self.by_finger[f][j], f, y, wx, wy, psi)
                for j in (i, i + 1) if 0 <= j < len(starts)]

    def _key_x_across(self, n, f, y, wx, wy, psi):
        x, _ = self.key_target(n.pitch, f, n)
        return self._rot(x - wx, y - wy, -psi)[0]

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
            self.performance += self._gliss_performance()
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
        tips = self._tendon_pull(tips, t)
        for f in range(1, 6):
            tips[f] = self._limit_tip(f, tips[f], wx, wy, psi, comp, self._key_weight(f, t)[0], low)
        return self._separate(tips, wx, wy, psi, t, comp)

    def _thumb_bridge(self, t, pts):
        """
        The thumb chain `pts` (CMC, MCP, IP, tip), laid across two black keys
        when it plays a pair of them (_bridge_pair) - not pointing up the
        keys between them as if playing the white keys there. The thumb is
        straight from its MCP joint to the tip (no bend at the IP joint), the
        tip on the far key, lying as nearly along the keyboard as its base
        lets it so its side is on the near key too; only the MCP joint
        angles. Blended in by how firmly the thumb is on (or about to be on)
        that pair (_key_weight).
        """
        w, n = self._key_weight(1, t)
        if n is None or w <= 0.0:
            return pts
        pk = self.pair_key.get(id(n))
        if not pk or not self._bridge_pair(pk, 1):
            return pts
        S = self.S
        near = max(self.key_target(p, 1, n)[0] for p in pk)
        cmc, tip = pts[0], pts[3]
        l1, l2, l3 = (L * S for L in self.geo.bones[1])
        side = 1.0 if near >= tip[0] else -1.0
        mz = tip[2] + THUMB_BRIDGE_LIFT_IN * self.ppi              # (lying flat: the MCP joint barely higher)
        r = math.sqrt(max(1e-6, (l2 + l3) ** 2 - (mz - tip[2]) ** 2))   # tip to MCP, across
        q = math.sqrt(max(1e-6, l1 ** 2 - (cmc[2] - mz) ** 2))          # CMC to MCP, across
        dx, dy = cmc[0] - tip[0], cmc[1] - tip[1]
        d = max(1e-6, math.hypot(dx, dy))
        ux, uy = dx / d, dy / d
        if d >= r + q or d <= abs(r - q):
            mx, my = tip[0] + ux * r, tip[1] + uy * r               # (out of reach: straight at the base)
        else:
            # where the straight thumb (r from the tip) meets the metacarpal (q from the CMC):
            # of the two places, the one nearer along the keyboard toward the near key
            a_ = (r * r - q * q + d * d) / (2 * d)
            h = math.sqrt(max(0.0, r * r - a_ * a_))
            cx, cy = tip[0] + ux * a_, tip[1] + uy * a_
            cands = [(cx - uy * h, cy + ux * h), (cx + uy * h, cy - ux * h)]
            mx, my = max(cands, key=lambda c: side * (c[0] - tip[0]) - abs(c[1] - tip[1]))
        mcp = (mx, my, mz)
        k = l3 / (l2 + l3)
        ip = tuple(tv + (mv - tv) * k for tv, mv in zip(tip, mcp))
        bridged = [cmc, mcp, ip, tip]
        return [tuple(_lerp(a, b, w) for a, b in zip(p, q_)) for p, q_ in zip(pts, bridged)]

    def _hover(self, f):
        """Fingertip f's resting height (world z) over the keys."""
        return HOVER[f] * self.S + self.retract_up_in * self.ppi * (0.5 if f == 1 else 1.0)

    def _tendon_pull(self, tips, t):
        """
        Linked tendons (TENDON_FOLLOWERS): 3 curving down takes 4 with it, 4
        takes 3, and 5 takes both - by `self.tendon` of how far down the leader
        is (0 hovering, 1 at the key tops), toward TENDON_FLOOR_IN, the deepest
        leader counting; a follower reaching for a key of its own (_reaching)
        is free of it.
        """
        if self.tendon <= 0.0:
            return tips
        down = {}
        for lead in TENDON_FOLLOWERS:
            h = self._hover(lead)
            down[lead] = min(1.0, max(0.0, (h - tips[lead][2]) / h)) if h > 0 else 0.0
        floor = TENDON_FLOOR_IN * self.ppi
        out = dict(tips)
        for f in (3, 4):
            d = max(down[lead] for lead, fol in TENDON_FOLLOWERS.items() if f in fol)
            x, y, z = tips[f]
            if d <= 0.0 or z <= floor:
                continue
            free = 1.0 - self._reaching(f, t)
            if free > 0.0:
                out[f] = (x, y, z - self.tendon * d * free * (z - floor))
        return out

    def _reaching(self, f, t):
        """0..1: how much finger f is busy with a key of its own - 1 while it presses one, rising with its trip to the next."""
        if self._pressing(f, t):
            return 1.0
        starts = self.finger_starts[f]
        i = bisect.bisect_right(starts, t) - 1
        if i + 1 >= len(starts):
            return 0.0
        prep_start, strike_start, _ = self._prep_window(f, i)
        if t >= strike_start:
            return 1.0
        if t <= prep_start:
            return 0.0
        return self._travel(f, i, t)[0]

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
        tw, tr = self._trem_w(t)
        for f in range(1, 6):
            w, n = self._key_weight(f, t)
            if tw > 0.0:
                # in a tremolo every finger playing in it counts as on its key throughout
                m = self._trem_note(f, t, tr)
                if m is not None and (n is None or (n.pitch == m.pitch and w < tw)):
                    w, n = tw if n is None else max(w, tw), m      # (a finger moving to a new key keeps to it)
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
            pk = self._pk(n)
            if f == 1 and self._bridge_pair(pk, 1):
                # a thumb bridging two black keys lies straight across them (_thumb_bridge): from
                # its tip on the far key, over the near one (THUMB_BRIDGE_NEAR_IN in from its front),
                # to its MCP joint - which its metacarpal must reach: the hand comes in over the
                # keys enough for that (as a real one does to lay its thumb along them)
                nx = max(self.key_target(p, 1, n)[0] for p in pk)
                py = self.kb.rect.h - self.kb.black_h + THUMB_BRIDGE_NEAR_IN * self.ppi
                l1, l2, l3 = (L * self.S for L in self.geo.bones[1])
                d = max(1e-6, math.hypot(nx - kx, py - ky))
                mx, my = kx + (nx - kx) * (l2 + l3) / d, ky + (py - ky) * (l2 + l3) / d
                dz = self.base_local[1][2] * low + self.travel              # (the base is higher: across, less)
                reach = math.sqrt(max(0.0, l1 * l1 - dz * dz))
                cons.append((mx, [my], self.base_local[1], -math.pi, math.pi, 0.0, reach, KEY_FIX_K * w))
        if not cons:
            return wx, wy, psi
        arm = 3.0 * self.S                             # turning counts as moving the knuckles this far
        arm *= 1.0 + (RUN_TURN_STIFF - 1.0) * comp     # (in a run the hand keeps its turn: chromatic octaves)

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
            A = _gram(cols)
            g = [-sum(map(mul, cols[i], r0)) for i in range(3)]
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
        self.tendon = p.b("tendon_link")
        self.curl_k = curl_factor(p)
        c = p.b("finger_curve")
        self.curl_lift_in = 0.6 * (c - 0.4) - 0.11        # hand height: -0.35 in (flat) .. +0.25 in (curved)
        self.reach_bonus = 0.09 * max(0.0, 0.4 - c) / 0.4       # flat fingers may extend further
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
        by at most MAX_DELAY_T and never past the hand's next chord. The
        first chord after a glissando is reached from where the slide ended
        (_gliss_exits), in time to be there GLISS_ARRIVE_T before it.
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
        exits = self._gliss_exits()
        exit_ts = [e[0] for e in exits]
        gl_starts = sorted(self.gliss_start[id(ep[0][0])] for ep in self.gliss_eps)
        prev_s1 = -math.inf
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
            k = bisect.bisect_right(exit_ts, s1) - 1
            if k >= 0 and exits[k][0] > prev_s1:          # a glissando ended since the last chord
                t_end, at = exits[k]
                need = fg.travel_time(fg.range_gap(at, rng), self.max_speed)
                delay = max(delay, t_end + need + GLISS_ARRIVE_T - s1)
            prev_s1 = s1
            if delay > 1e-4:
                # (and the hand must be free to form the next glissando: its blend-in starts GLISS_RAMP_T early)
                k = bisect.bisect_right(gl_starts, s1)
                free = gl_starts[k] - GLISS_RAMP_T if k < len(gl_starts) else math.inf
                delay = min(delay, MAX_DELAY_T, max(0.0, nxt - 0.03 - s1), max(0.0, free - MIN_HOLD_T - s1))
                if delay > 1e-4:
                    self.delayed += 1
                    for m in ns:
                        so[id(m)] += delay
                        e = fe[id(m)]
                        fe[id(m)] = min(e + delay, free) if e <= free else e + delay
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
        if it's down, keeps them sounding): a note the top finger can't reach
        from is let go in time for the hand to stretch on to the top at its
        top speed, and the top note waits for that if the roll is quicker
        (never past the hand's next chord).
        """
        import fingering as fg
        scale = reach_scale(self.pianist.anatomy)
        so, eo = self.start_of, self.end_of

        def reach(f1, f2):
            a, b = min(f1, f2), max(f1, f2)
            return fg.BASE_MAX_SPAN[(a, b)] * scale[(a, b)] + 0.25

        def pos(n):
            """Where the note's finger is: between the two keys it covers, if a pair - the far one for a thumb bridge."""
            pk = self.pair_key.get(id(n))
            if pk:
                a, b = key_pos(self.vp(pk[0])), key_pos(self.vp(pk[1]))
                if self._bridge_pair(pk, self.fingering[id(n)]):
                    return min(a, b)
                return (a + b) / 2
            return key_pos(self.vp(n.pitch))

        groups = self.groups
        for gi, (_, ns) in enumerate(groups):
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
            latest = max(so[id(top)], (groups[gi + 1][0] - 0.03) if gi + 1 < len(groups) else math.inf)
            for n in order[:-1]:
                f = self.fingering[id(n)]
                if f == ft:                     # (one finger on two keys, a thumb on a pair: no stretch between them)
                    continue
                over = abs(pt - pos(n)) - reach(f, ft)
                if over > 0:
                    need = fg.travel_time(over, self.max_speed)
                    eo[id(n)] = min(eo[id(n)], max(so[id(n)] + 0.03, so[id(top)] - need))
                    so[id(top)] = min(latest, max(so[id(top)], eo[id(n)] + need))
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
        self._gliss_follow_cache = {}
        self._follow_jobs = {}
        self._warm_k = None
        self._strike_list = None
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
            if self._bridge_pair(pitch, f):
                # the far key, well in along it: the straight thumb lies across to it, over the near one
                x = min(x1, x2)
                return x, self.kb.rect.h - self.kb.black_h + THUMB_BRIDGE_DEPTH_IN * self.ppi
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

    @staticmethod
    def _bridge_pair(pk, f):
        """
        Is this the thumb bridging two black keys (C#-D#, D#-F#, A#-C#...)? It
        lies flat across the keyboard over the white keys between them: its
        tip on the far key (away from the other fingers), its side on the
        near one - so its tip aims at the far key (key_target), its reach is
        measured from there, and _thumb_bridge lays it across.
        """
        return f == 1 and isinstance(pk, tuple) and all(is_black_key(p) for p in pk)

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
        return _clamp_apply(self._clamp_prep(f, z, wx, wy, psi, slack, margin, comp, stretch, low), x, y)

    def _clamp_prep(self, f, z, wx, wy, psi, slack=0.0, margin=0.0, comp=0.0, stretch=1.0, low=1.0):
        """What _clamp_tip needs for finger f at this hand and height, worked out once (_clamp_apply)."""
        rot = self._rot
        blx, bly, blz = self.base_local[f]
        bx, by = rot(blx, bly, psi)
        bx, by = wx + bx, wy + by
        lo, hi = self._splay_at(f, comp, stretch)
        m = math.radians(KEY_FIX_MARGIN_DEG) * margin / KEY_FIX_MARGIN if margin else 0.0
        lo, hi = lo - slack + m, hi + slack - m
        hmin, hmax = self._reach_range(f, blz * low - z, 0.99)
        if z > 0:
            hmin *= self.curl_min            # a retracting pianist curls idle fingers further in
        dm = margin * self.length[f]
        hmin, hmax = hmin + dm, max(hmin + dm, hmax - dm)
        return (bx, by, math.cos(-psi), math.sin(-psi), math.cos(psi), math.sin(psi), lo, hi, hmin, hmax)

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
        prep = self._clamp_prep(f, z, *hand, margin=KEY_FIX_MARGIN, comp=comp, low=low)
        cx, cy = _clamp_apply(prep, kx, ky)
        e0 = math.hypot(cx - kx, cy - ky)
        far = _smooth((e0 - 1.5 * span) / (1.5 * span))
        if e0 < 0.5 or far >= 1.0:
            return kx, ky
        kk = _lerp(KEY_SPOT_FIRM[0], KEY_SPOT_SOFT[0], soft)
        tau = _lerp(KEY_SPOT_FIRM[1], KEY_SPOT_SOFT[1], soft) * self.ppi
        cands = [(ky, kk * e0)]
        for i in range(21):
            y = lo + span * i / 20
            cx, cy = _clamp_apply(prep, kx, y)
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

        S = self.S
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
            G = _gram(cols)
            A = [[G[i][j] / (scale[i] * scale[j]) for j in range(3)] for i in range(3)]
            g = [-sum(map(mul, cols[i], r0)) / scale[i] for i in range(3)]
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
        w, r = self._trem_w(t)
        if r is not None:
            # a tremolo: the wrist holds still, averaged over the tremolo's own notes
            a, b = r
            hold = self._hand_avg(t, TREM_HOLD_T, a, b)
            q = tuple(_lerp(x, y, w) for x, y in zip(q, hold))
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
        sd = sf = sw = 0.0
        for k in range(k0, k1 + 1):
            w = 1.0 - abs(k * g - t) / span
            if w > 0:
                d = self._idle_cache.get(k)
                if d is None:
                    if len(self._idle_cache) > 4000:
                        self._idle_cache.clear()
                    d = self._idle_cache[k] = self._idle_shift_parts(k * g)
                sd, sf, sw = sd + w * d[0], sf + w * d[1], sw + w
        dx = sd / sw if sw else 0.0
        f = sf / sw if sw else 0.0
        if f > 0.0:
            # drawn to its next notes: smoothed as a share of the way there from where it is now
            nxt = self._next_strike(t)
            if nxt < math.inf:
                dx += f * (self._hand_at(nxt)[0] - wx)
        if abs(dx) < 1e-6:
            return wx, wy, psi
        dx = self._short_of_next(t, wx, dx)          # (the smoothing mustn't carry it past them either)
        return wx + dx, wy, psi + self._yaw(wx + dx) - self._yaw(wx)

    def _idle_shift_parts(self, t):
        """
        (shift, share): the idle hand's shift at t, and - moved as far as its
        next notes - the share (0..1) of the way to them, which _placed_at
        smooths as a share (the shift is then only a push beyond them).
        """
        p = self.partner
        p_idle = p.idle_at(t)
        mine, theirs = self._first_t(), p._first_t()
        if t < theirs < mine:
            p_idle = 0.0          # the other hand plays first: from the very start, keep to this side of it
        w = self.idle_at(t) * (1.0 - p_idle)
        rise = self._idle_rise(t)
        if rise <= 0.0:
            return 0.0, 0.0
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
        # Moved as far as the notes it plays next (either way), the hand stays
        # there until it plays them - that part doesn't fade with the other
        # hand's rests or ahead of the notes, or the hand would slide back
        # across them and on again. Beyond them it may only be pushed out of
        # the other hand's way (that part fades as usual), never pulled.
        nxt = self._next_strike(t)
        if nxt < math.inf and abs(target - raw) > 1e-6:
            xn = self._hand_at(nxt)[0]
            if raw < xn <= target:
                return w * (target - xn), rise
            if target <= xn < raw:
                return 0.0, rise
        if w <= 0.0:
            return 0.0, 0.0
        return self._short_of_next(t, raw, w * (target - raw)), 0.0

    def _idle_rise(self, t):
        """How far into its rest the hand is at t: idle_weight without the ramp down before the next note."""
        i = bisect.bisect_right(self.span_starts, t) - 1
        if i >= 0 and t < self.spans[i][1]:
            return 0.0
        last = self.spans[i][1] if i >= 0 else -math.inf
        return _smooth((t - last - IDLE_AFTER_T) / IDLE_RAMP_T)

    def _short_of_next(self, t, raw, dx):
        """
        Shift dx of the hand at raw, as a pull toward the other hand (dx < 0):
        it may only bring the hand toward where it plays next, never past it -
        so a hand resting where it plays next stays put (it would drift off and
        jerk back). Getting out of the other hand's way (dx > 0) isn't cut.
        """
        nxt = self._next_strike(t)
        if dx < 0 and nxt < math.inf:
            return max(dx, min(0.0, self._hand_at(nxt)[0] - raw))
        return dx

    def _first_t(self):
        """When this hand strikes its first fingered key (inf if never)."""
        return self._group_ts[0] if self._group_ts else math.inf

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
    def _blocked_until(self, f, i):
        """
        When finger f (2-5) can set off from its note i (or from where it
        rests, i = -1) toward note i+1: not before every neighbouring finger
        holding a key short of that one - a finger on f's far side of it,
        in the way - has let go: fingers can't pass each other (in a 1-3
        chromatic scale the index waited pressed against the middle finger
        and then jumped with it). The thumb passes under them.
        """
        if f == 1:
            return -math.inf
        cache = self._prep_cache
        key = (f, i)
        got = cache.get(key)
        if got is None:
            notes, starts = self.by_finger[f], self.finger_starts[f]
            dst = key_pos(self.vp(notes[i + 1].pitch))
            got = -math.inf
            if i < 0 or key_pos(self.vp(notes[i].pitch)) != dst:
                t1 = starts[i + 1]
                t0 = self.finger_ends[f][i] if i >= 0 else t1 - PREP_LOOKBACK_T
                for g in range(2, 6):
                    if g == f:
                        continue
                    gs, ge, gn = self.finger_starts[g], self.finger_ends[g], self.by_finger[g]
                    j = bisect.bisect_right(gs, t1) - 1
                    while j >= 0 and ge[j] > t0:
                        p = key_pos(self.vp(gn[j].pitch))
                        if (p < dst) if g > f else (p > dst):
                            got = max(got, ge[j])
                        j -= 1
            cache[key] = got
        return got

    def _prep_window(self, f, i):
        """(prep_start, strike_start, strike) for finger f's note i+1 (i = its last note, or -1)."""
        starts, ends = self.finger_starts[f], self.finger_ends[f]
        nxt = starts[i + 1]
        # a long trip at the top speed may start earlier than usual, and
        # when time is short the final drop is cut so the trip keeps to it
        need = self.lead.get(id(self.by_finger[f][i + 1]), 0.0)
        prep_start = max(nxt - max(self.prep_max_t, STRIKE_MIN_T + need), ends[i] if i >= 0 else -math.inf)
        # (not through a neighbour still on its key)
        prep_start = max(prep_start, min(self._blocked_until(f, i), nxt - STRIKE_MIN_T))
        window = max(0.0, nxt - prep_start)
        strike = min(STRIKE_T, 0.35 * window)
        if window - strike < need:
            strike = min(strike, max(STRIKE_MIN_T, window - need))
        return prep_start, nxt - strike, strike

    def _travel(self, f, i, t):
        """
        How far (0..1) finger f has travelled from its last key toward the
        next one at time t. It leaves as soon as it is free and gets there
        early (self.travel_share of the time), then hovers, ready to strike.
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
        hover = self._hover(f)
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
        # than self.prep_max_t ahead), arriving raised and ready to strike.
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
        band = 0.5 * WHITE_KEY_IN * self.ppi
        for g in range(1, 6):
            # a thumb tucked under (or a finger crossed over the thumb) says
            # nothing about where the other fingers belong - fading out over
            # half a key as it gets there, not all at once: a switch made the
            # idle fingers jump each time the thumb passed under (chromatic scales)
            w = busy[g]
            if g == 1:
                w *= _smooth(0.5 + (rest[2][0] - local[1][0]) / band)
            else:
                w *= 1.0 - min(1.0, busy[1] / 0.1) * _smooth(0.5 + (local[1][0] - local[g][0]) / band)
            if w <= 1e-3:
                continue
            dx, dy = local[g][0] - rest[g][0], local[g][1] - rest[g][1]
            disp[g] = (dx * w, dy * w)
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
        if g >= 1.0:
            return self._gliss_pose(t, ep, kb, g)
        fp = self._finger_pose(t, kb)
        target = self._gliss_across(t, kb)
        if target is None:
            rush = self._gliss_rush(t, kb)
            if rush is not None:
                target = (rush[0] + rush[2], rush[1] + rush[3])
            else:
                fp = self._gliss_travel(t, kb, fp)
        p = fp if g <= 0.0 else _blend_pose(fp, self._gliss_pose(t, ep, kb, g), g)
        if target is not None:
            px, py = self._pose_wrist(p)
            p = _shift_pose(p, target[0] - px, target[1] - py, self.mirror)
        return p

    @staticmethod
    def _pose_wrist(p):
        (ax, ay, _), (bx, by, _) = p["struct"]["wrist"]
        return (ax + bx) / 2, (ay + by) / 2

    def _gliss_follow(self, i, kb, leaving):
        """
        The hand's way from glissando episode i back to the finger pose
        (leaving) or from the finger pose into it: [(t, x, y)] on screen, in
        time order. It chases the finger pose (or, played backwards, runs
        from it) at no more than the top speed, speeding up and slowing down
        at GLISS_TRAVEL_ACC, until it is back on it.
        """
        key = (i, leaving)
        path = self._gliss_follow_cache.get(key)
        if path is not None:
            return path or None
        job = self._follow_jobs.pop(key, None) or self._gliss_follow_steps(i, kb, leaving)
        for _ in job:                               # (whatever prepare() hasn't done yet)
            pass
        return self._gliss_follow_cache[key] or None

    def _gliss_follow_steps(self, i, kb, leaving):
        """_gliss_follow as a job that yields after each step, so prepare() can do it a little at a time."""
        key = (i, leaving)
        t0, t1, _ = self._gliss_paths()[i]
        W = self._pose_wrist
        # the glissando pose is held until the blend is done (the contact stays put after t1 / before t0)
        ta = t1 + GLISS_RAMP_T if leaving else t0 - GLISS_RAMP_T
        x, y = W(self._gliss_pose(t1 if leaving else t0, i, kb))
        vmax = self.max_speed / 0.0254 * self.ppi
        acc = GLISS_TRAVEL_ACC / 0.0254 * self.ppi
        dt = GLISS_TRAVEL_DT * (1 if leaving else -1)
        vx = vy = 0.0
        t, path = ta, [(ta, x, y)]
        for _ in range(int(GLISS_TRAVEL_MAX_T / abs(dt))):
            t += dt
            fx, fy = W(self._finger_pose(t, kb))
            dx, dy = fx - x, fy - y
            d = math.hypot(dx, dy)
            if d < 0.5 and math.hypot(vx, vy) < acc * abs(dt) * 2:
                break
            sp = min(vmax, math.sqrt(2 * acc * d))           # slowing down to arrive
            wx, wy = (dx / d * sp, dy / d * sp) if d > 1e-9 else (0.0, 0.0)
            ex, ey = wx - vx, wy - vy
            e = math.hypot(ex, ey)
            if e > acc * abs(dt):
                ex, ey = ex / e * acc * abs(dt), ey / e * acc * abs(dt)
            vx, vy = vx + ex, vy + ey
            x, y = x + vx * abs(dt), y + vy * abs(dt)
            path.append((t, x, y))
            yield
        # ...but it must be back on the finger pose before the hand's next key
        # counts (or may leave it only once its last key before the glissando
        # is let go): if the chase would take longer, None - _gliss_rush moves
        # the whole pose instead, over all the time there is
        if (path[-1][0] > self._gliss_rush_window(i, True)[1]) if leaving else \
                (path[-1][0] < self._gliss_rush_window(i, False)[0]):
            self._gliss_follow_cache[key] = False
            return
        if not leaving:
            path.reverse()
        self._gliss_follow_cache[key] = path

    def prepare(self, t, kb, budget=0.005):
        """
        Work done ahead, a little each frame (about `budget` seconds): the
        hand's motion (its speed-limited grid, a chain where each step needs
        the one before) carried on up to WARM_AHEAD_T past t, then the ways
        to and from the glissandos it reaches - so working any of it out
        when it's needed never stalls a frame. In steady play it costs no
        more than the frames would have spent anyway.
        """
        self._ensure_layout(kb)
        end = time.perf_counter() + budget
        g = LIMIT_GRID_T
        k_now = math.floor(t / g)
        if self._warm_k is None or self._warm_k < k_now or self._warm_k > k_now + 4 * WARM_AHEAD_T / g:
            self._warm_k = k_now                     # (after a seek: from here)
        k_end = math.floor((t + WARM_AHEAD_T) / g)
        while self._warm_k < k_end:
            if time.perf_counter() > end:
                return
            self._warm_k += 1
            self._smooth_tip_grid(self._warm_k)      # (and the hand's grid under it)
        if not self.gliss_eps:
            return
        frontier = self._warm_k * g
        paths = self._gliss_paths()
        k = bisect.bisect_right(self._gliss_starts, frontier)
        for i in range(max(0, k - 2), k):
            t0, t1, _ = paths[i]
            # once the warm motion reaches where each way starts, until it's done
            for leaving, start in ((False, t0 - GLISS_RAMP_T), (True, t1 + GLISS_RAMP_T)):
                key = (i, leaving)
                if key in self._gliss_follow_cache or frontier < start or t > start + GLISS_TRAVEL_MAX_T:
                    continue
                job = self._follow_jobs.get(key)
                if job is None:
                    job = self._follow_jobs[key] = self._gliss_follow_steps(i, kb, leaving)
                for _ in job:
                    if time.perf_counter() > end:
                        return
                self._follow_jobs.pop(key, None)

    def _gliss_rush_window(self, i, leaving):
        """
        (from, to): leaving episode i, from its end to just before the hand's
        next fingered strike; arriving, from just after the last key before
        it is let go to its start.
        """
        t0, t1, _ = self._gliss_paths()[i]
        if leaving:
            return t1, max(t1, self._next_strike(t1) - GLISS_ARRIVE_T)
        return min(t0, self._last_release(t0) + KEY_FIX_RELEASE_T), t0

    def _travel_u(self, u, T, D):
        """
        Share of a move of D pixels done at u (0..1) of T seconds: smootherstep
        when that keeps to the top speed, else speeding up, cruising at the
        slowest speed that makes it and slowing down (GLISS_TRAVEL_ACC).
        """
        u = max(0.0, min(1.0, u))
        vmax = self.max_speed / 0.0254 * self.ppi
        if D * 1.875 / max(1e-6, T) <= vmax:
            return _smooth(u)
        acc = GLISS_TRAVEL_ACC / 0.0254 * self.ppi
        disc = (acc * T) ** 2 - 4 * acc * D
        v = (acc * T - math.sqrt(disc)) / 2 if disc > 0 else acc * T / 2
        ta = v / acc
        x = u * T
        if x < ta:
            d = 0.5 * acc * x * x
        elif x > T - ta:
            d = v * (T - ta) - 0.5 * acc * (T - x) ** 2
        else:
            d = 0.5 * acc * ta * ta + v * (x - ta)
        return max(0.0, min(1.0, d / max(1e-6, v * (T - ta))))

    def _gliss_rush(self, t, kb):
        """
        (fx, fy, dx, dy): when there isn't time to chase the finger pose
        (_gliss_follow None), where the wrist should be at t - the finger
        pose's wrist (fx, fy) plus an offset (dx, dy) from where the glissando
        left the hand (or where the next begins), easing out over
        _gliss_rush_window. None when it doesn't apply.
        """
        if not self.gliss_eps:
            return None
        out, into, prev, nxt = self._gliss_neighbours(t, kb)
        W = self._pose_wrist
        paths = self._gliss_paths()
        for i, leaving, path in ((prev, True, out), (nxt, False, into)):
            if i is None or path is not None:
                continue
            ta, tb = self._gliss_rush_window(i, leaving)
            t0, t1, _ = paths[i]
            # never over the other glissando's way in or out: arriving waits until the hand has
            # left the last one (its blend, and its way back), leaving is done before the next one's
            if leaving and nxt is not None and nxt != i:
                tb = min(tb, into[0][0] if into else paths[nxt][0] - GLISS_RAMP_T)
            elif not leaving and prev is not None and prev != i:
                ta = max(ta, out[-1][0] if out else paths[prev][1] + GLISS_RAMP_T)
            if (tb <= t1 + GLISS_RAMP_T) if leaving else (ta >= t0 - GLISS_RAMP_T):
                continue                 # no more time than the blend itself: leave it to the blend
            if not ta <= t <= tb:
                continue
            key = ("rush", i, leaving)
            d0 = self._gliss_follow_cache.get(key)
            if d0 is None:
                at = t1 if leaving else t0
                gx, gy = W(self._gliss_pose(at, i, kb))
                fx0, fy0 = W(self._finger_pose(tb if not leaving else ta, kb))
                d0 = self._gliss_follow_cache[key] = (gx - fx0, gy - fy0)
            u = self._travel_u((t - ta) / max(1e-6, tb - ta), tb - ta, math.hypot(*d0))
            w = 1.0 - u if leaving else u
            fx, fy = W(self._finger_pose(t, kb))
            return fx, fy, d0[0] * w, d0[1] * w
        return None

    def _strikes(self):
        """[(struck, let go)] of the hand's fingered notes, by when struck."""
        if self._strike_list is None:
            out = []
            for f, ns in self.by_finger.items():
                for n, e in zip(ns, self.finger_ends[f]):
                    out.append((self.start_of[id(n)], e))
            out.sort()
            self._strike_list = out
            self._strike_starts = [p[0] for p in out]
        return self._strike_list

    def _next_strike(self, t):
        """When the hand next strikes a fingered key after t (inf if never)."""
        self._strikes()
        k = bisect.bisect_right(self._strike_starts, t)
        return self._strike_starts[k] if k < len(self._strike_starts) else math.inf

    def _last_release(self, t):
        """When the hand lets go of the last fingered key it struck before t (-inf if none)."""
        ps = self._strikes()
        k = bisect.bisect_left(self._strike_starts, t)
        return max((e for _, e in ps[:k]), default=-math.inf) if k else -math.inf

    @staticmethod
    def _along(path, t):
        k = bisect.bisect_right([p[0] for p in path], t) - 1
        k = max(0, min(len(path) - 2, k))
        (ta, xa, ya), (tb, xb, yb) = path[k], path[k + 1]
        u = max(0.0, min(1.0, (t - ta) / max(1e-9, tb - ta)))
        return xa + (xb - xa) * u, ya + (yb - ya) * u

    def _gliss_neighbours(self, t, kb):
        """(the hand's way back from the last glissando episode, its way into the next, their indices) at t."""
        paths = self._gliss_paths()
        k = bisect.bisect_right(self._gliss_starts, t) - 1                  # the last episode started
        prev = k if k >= 0 and paths[k][1] <= t else None
        nxt = k + 1 if k + 1 < len(paths) else None
        # a way to or from a glissando lasts at most GLISS_TRAVEL_MAX_T: further
        # off it can't apply yet (() - nothing to follow, not a rush)
        reach = GLISS_TRAVEL_MAX_T + GLISS_RAMP_T + 0.1
        out = (self._gliss_follow(prev, kb, True) if t <= paths[prev][1] + reach else ()) \
            if prev is not None else None
        into = (self._gliss_follow(nxt, kb, False) if t >= paths[nxt][0] - reach else ()) \
            if nxt is not None else None
        return out, into, prev, nxt

    def _gliss_across(self, t, kb):
        """
        Where the wrist is at t when there's no time to get back to the
        finger pose between two glissandos (the way back from one ends after
        the way into the next begins): straight from where the first one
        ends to where the next begins, over the whole gap, blends included.
        None otherwise.
        """
        if not self.gliss_eps:
            return None
        out, into, prev, nxt = self._gliss_neighbours(t, kb)
        if not (out and into and out[-1][0] > into[0][0]):
            return None
        paths = self._gliss_paths()
        te, ts = paths[prev][1], paths[nxt][0]
        if self._next_strike(te) < ts:
            return None                                  # keys to play in between: not straight across
        if not te <= t <= ts:
            return None
        (_, xe, ye), (_, xs, ys) = out[0], into[-1]
        T = max(1e-6, ts - te)
        u = self._travel_u((t - te) / T, T, math.hypot(xs - xe, ys - ye))
        return xe + (xs - xe) * u, ye + (ys - ye) * u

    def _gliss_travel(self, t, kb, fp):
        """
        The finger pose, moved so the hand leaves a glissando from where it
        ended and comes to the next one where it starts, never faster than
        the top speed (_gliss_follow): after a glissando it catches up with
        the finger pose, before one it leaves it just in time. (When the two
        overlap, _gliss_across takes over.)
        """
        if not self.gliss_eps:
            return fp
        out, into, _, _ = self._gliss_neighbours(t, kb)
        if out and t > out[-1][0]:
            out = None
        if into and t < into[0][0]:
            into = None
        if not out and not into:
            return fp
        fx, fy = self._pose_wrist(fp)
        hx, hy = self._along(out or into, t)
        return _shift_pose(fp, hx - fx, hy - fy, self.mirror)

    # ----- glissandos ------------------------------------------------------------
    def _gliss_schedule(self):
        """
        {id(note): when it's struck} for the glissando notes: their own times,
        except that the slide keeps to the top speed. A key further on than
        the hand can slide in the time it has (a run's loose end skipping
        keys, in a file that hurries it) is struck late, and the keys after
        it with it. The step's speed is as _gliss_contact moves: steady
        within a run, eased (GLISS_EASE_PEAK at its fastest) where it turns
        round or pauses; GLISS_SPEED_SHARE leaves room for the hand's turn.
        """
        import fingering as fg
        out = {}
        per_wk = fg.WHITE_KEY_M / (self.max_speed * GLISS_SPEED_SHARE)        # s per white key
        for ep in self.gliss_eps:
            prev = None                                  # (note, its time, its run's direction)
            for r in ep:
                d = 1 if r[-1].pitch >= r[0].pitch else -1
                for n in r:
                    t = n.start
                    if prev is not None:
                        pn, pt, pd = prev
                        need = abs(key_pos(n.pitch) - key_pos(pn.pitch)) * per_wk
                        if pd != d or t - pt > 0.2:
                            need *= GLISS_EASE_PEAK
                        t = max(t, pt + need)
                    out[id(n)] = t
                    prev = (n, t, d)
        return out

    def _gliss_end(self, ep):
        """When the hand leaves glissando episode ep's last key (its glissando pose holds until then)."""
        last = ep[-1][-1]
        ls = self.gliss_start[id(last)]
        return max(ls, min(last.end + ls - last.start, ls + 0.12))

    def _gliss_exits(self):
        """[(when the hand leaves a glissando, the hand's range there - as fingering.hand_range)], in time order."""
        import fingering as fg
        out = []
        for ep in self.gliss_eps:
            first, last = ep[-1][0], ep[-1][-1]
            # palm up (toward the little finger) the index and middle touch, else the thumb
            f = 2 if self.vp(last.pitch) >= self.vp(first.pitch) else 1
            out.append((self._gliss_end(ep), fg.hand_range([self.vp(last.pitch)], [f])))
        out.sort(key=lambda e: e[0])
        return out

    def _gliss_performance(self):
        """[(press, release, note)] for the glissando notes, at their scheduled times."""
        out = []
        for r in self.gliss_runs:
            for n in r:
                s = self.gliss_start[id(n)]
                out.append((s, n.end + s - n.start, n))
        return out

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
                white_in = GLISS_WHITE_IN if d > 0 else min(GLISS_THUMB_IN, front / self.ppi)
                for n, x in zip(r, xs):
                    y = front + GLISS_BLACK_IN * self.ppi if is_black_key(n.pitch) else white_in * self.ppi
                    pts.append((self.gliss_start[id(n)], x, y, d))
            out.append((pts[0][0], self._gliss_end(ep), pts))
        self._gliss_cache = out
        return out

    def _gliss_w(self, t):
        """(0..1 how much the hand is in its glissando pose at t, that episode's index or None)."""
        if not self.gliss_eps:
            return 0.0, None
        paths = self._gliss_paths()
        i = bisect.bisect_right(self._gliss_starts, t + GLISS_RAMP_T) - 1
        best = (0.0, None)
        # the next one may already be coming in while the last is still on or going out
        for j in (i, i - 1):
            if j < 0:
                continue
            t0, t1, _ = paths[j]
            if t < t0:
                w = _smooth(1.0 - (t0 - t) / GLISS_RAMP_T)
            elif t <= t1:
                w = 1.0
            else:
                w = _smooth(1.0 - (t - t1) / GLISS_RAMP_T)
            if w > best[0]:
                best = (w, j)
        return best

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
        # (tucked, it bends across the palm, on the palm's side)
        thumb = solve_chain(cmc, _lerp3(out, tucked, u), list(geo.bones[1]), _lerp3((-0.85, 0.0, 0.5), (-0.6, 0.0, -0.8), u),
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
        front = self.kb.rect.h - self.kb.black_h
        if u < 1.0 and cy <= front + 1e-6:
            # with the thumb on a white key, the fist stays clear of the black keys' fronts
            reach = max(place(p)[1] for f in range(2, 6) for p in chains[f]) - ap[1]
            cy = min(cy, max(GLISS_WHITE_IN * self.ppi, front - GLISS_FIST_CLEAR * self.ppi - reach))
        # the point of the fingers on the contact point, nothing below the keys
        ox, oy, oz = cx - ap[0], cy - ap[1], -self.travel * 0.5 - low[2]

        def world(p, roll=True):
            x, y, z = place(p, roll)
            return (ox + x, oy + y, oz + z)
        wr, wu = world(geo.wrist_sides[0], False), world(geo.wrist_sides[1], False)
        wcmc = world(cmc)
        wb = {f: world(bases[f]) for f in range(2, 6)}
        bones, joints = [], []
        # with the thumb, the forearm leans the way the hand slides, as if pushing the thumb along the keys
        push = math.radians(GLISS_THUMB_ARM_DEG) * (1.0 - u)
        natural = -0.45 * math.atan2(cx - self.shoulder_x, self.forearm_len) * u      # (the lean is from straight up)
        back = rot(0.0, -1.0, natural + GLISS_ARM_SHARE * turn + push)
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
        # palm up the fingers lie flush side by side; with the thumb its nail is on its outer edge
        extra = {"nail_hide": hide, "palm_up": palm_up, "flush": g * u, "thumb_edge": g * (1.0 - u)}
        struct = {"chains": out_chains, "wrist": (wr, wu), "arm_end": arm_end, "mirror": self.mirror, **extra}
        if self.mirror:
            fx = lambda p: (2 * self.axis_x - p[0], p[1], p[2])
            bones = [(fx(a), fx(b), k) for a, b, k in bones]
            joints = [(fx(p), k) for p, k in joints]
            struct = {"chains": {f: [fx(p) for p in c] for f, c in out_chains.items()},
                      "wrist": (fx(wr), fx(wu)), "arm_end": fx(arm_end), "mirror": True, **extra}
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
        pts = self._thumb_bridge(t, pts)
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


NATURAL_THUMB_REACH = 0.93  # the resting thumb's tip at most this share of its length from its base: curved,
                            # however short the thumb (a short one can't reach the resting spot and went straight)


def _within(base, target, reach):
    """target, pulled in toward base to at most reach away."""
    d = math.dist(base, target)
    if d <= reach or d < 1e-9:
        return target
    return tuple(b + (t - b) * reach / d for b, t in zip(base, target))


FINGER_BEND_RAD = (0.6, 0.8, 0.5)     # static_skeleton bends: radians at MCP, PIP, DIP for bend 1
THUMB_SWING_RAD = 0.8                 # the thumb's swing out from the palm (about the CMC) for bend -1
THUMB_TUCK_MIN = 0.2                  # bend 1 swings it in to point just short of the index knuckle
THUMB_TUCK_SHY = 0.26                 # (by this much), at least THUMB_TUCK_MIN
THUMB_FLEX_RAD = (0.0, 0.25, 0.3)     # and flexes it at CMC, MCP, IP
BEND_RANGE = {"finger": (-0.5, 1.0), "thumb": (-0.6, 1.0)}


def clamp_bend(finger, b):
    lo, hi = BEND_RANGE["thumb" if finger == 1 else "finger"]
    return max(lo, min(hi, b))


def _flex(pts, angles):
    """pts (a finger chain) bent down at each joint by angles[i] radians (negative: up), each
    joint turning everything beyond it about the horizontal axis across the finger."""
    pts = [tuple(p) for p in pts]
    dx, dy = pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return pts
    d = (dx / n, dy / n)
    for i, th in enumerate(angles):
        if not th or i >= len(pts) - 1:
            continue
        c, s = math.cos(th), math.sin(th)
        px, py, pz = pts[i]
        for j in range(i + 1, len(pts)):
            rx, ry, rz = pts[j][0] - px, pts[j][1] - py, pts[j][2] - pz
            along = rx * d[0] + ry * d[1]
            side = (rx - along * d[0], ry - along * d[1])
            a2, z2 = along * c + rz * s, -along * s + rz * c
            pts[j] = (px + side[0] + a2 * d[0], py + side[1] + a2 * d[1], pz + z2)
    return pts


def _swing(pts, ang):
    """pts turned about the vertical axis through pts[0], by ang radians toward +x."""
    c, s = math.cos(ang), math.sin(ang)
    px, py, _ = pts[0]
    out = [pts[0]]
    for x, y, z in pts[1:]:
        rx, ry = x - px, y - py
        out.append((px + rx * c + ry * s, py - rx * s + ry * c, z))
    return out


def bend_chain(finger, pts, b, toward=None):
    """A finger's chain (MCP..tip; the thumb's CMC..tip) curved in by b (clamp_bend's range):
    fingers curl down (negative: lift), the thumb swings into the palm and flexes (negative: out).
    toward: the index knuckle, which the fully tucked thumb points just short of (any further
    and the palm, seen from above, hides it)."""
    b = clamp_bend(finger, b)
    if not b:
        return list(pts)
    if finger == 1:
        if b < 0:
            return _swing(pts, b * THUMB_SWING_RAD)
        now = math.atan2(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        room = THUMB_TUCK_MIN
        if toward is not None:
            room = max(room, math.atan2(toward[0] - pts[0][0], toward[1] - pts[0][1]) - THUMB_TUCK_SHY - now)
        return _flex(_swing(pts, b * room), [b * a for a in THUMB_FLEX_RAD])
    if b < 0:
        return _flex(pts, [b * FINGER_BEND_RAD[0] * 1.3, b * 0.2, 0.0])
    return _flex(pts, [b * a for a in FINGER_BEND_RAD])


def static_skeleton(geo, shape="stretched", curl=1.0, bends=None):
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
    bends: {finger: b} curls a finger further in from that shape (bend_chain).
    """
    bones, joints, tips, chains = [], [], {}, {}
    bends = bends or {}

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
            pts = solve_chain(mcp, _within(mcp, rest[f], REACH_COMFORT[f] * sum(geo.bones[f])),
                              list(geo.bones[f]), (0.0, 0.0, 1.0), FINGER_COUPLING, FINGER_BEND_MAX)
        else:
            pts = straight(mcp, splay[f], geo.bones[f], z_drop=mcp[2] - 0.3)
        if bends.get(f):
            pts = bend_chain(f, pts, bends[f])
        for (a, b), kind, bid in zip(zip(pts, pts[1:]), ("proximal", "middle", "distal"),
                                     (f"pp{f}", f"mp{f}", f"dp{f}")):
            bones.append((a, b, kind, bid))
        joints += [(base[f], "knuckle")] + [(p, "knuckle") for p in pts[:3]] + [(pts[3], "tip")]
        tips[f] = pts[3]
        chains[f] = [base[f]] + list(pts)
    if shape == "natural":
        bulge = (-0.85, 0.0, 0.5)
        pts = solve_chain(cmc, _within(cmc, rest[1], NATURAL_THUMB_REACH * sum(geo.bones[1])),
                          list(geo.bones[1]), bulge, THUMB_COUPLING, THUMB_BEND_MAX)
    else:
        pts = straight(cmc, splay[1], geo.bones[1], z_drop=cmc[2] - 0.3)
    if bends.get(1):
        pts = bend_chain(1, pts, bends[1], toward=geo.mcp[2])
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
