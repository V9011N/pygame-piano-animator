"""
pianist.py - Pianists: a name, hand colour, hand anatomy (the lengths of the
19 bones of the hand) and technique preferences, saved as JSON files in the
"pianists" folder next to this file. One pianist is "active": the hand
animation, the fingering planner and the hand split use them in every mode.

Anatomy is stored in the hand model's own units (see hands.py; one unit is
about 1.09 cm on the drawn keyboard's real scale).
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "pianists")
SETTINGS = os.path.join(FOLDER, "settings.json")
DEFAULT_ID = "default"

# --------------------------------------------------------------------------- #
# Anatomy: 5 metacarpals + 14 phalanges
# --------------------------------------------------------------------------- #
FINGER_WORD = {1: "Thumb", 2: "Index", 3: "Middle", 4: "Ring", 5: "Little"}
BONE_WORD = {"mc": "metacarpal", "pp": "proximal phalanx", "mp": "middle phalanx", "dp": "distal phalanx"}

# bone id -> (finger, kind); thumb has no middle phalanx
BONES = [("mc1", 1, "mc"), ("pp1", 1, "pp"), ("dp1", 1, "dp")]
for _f in range(2, 6):
    BONES += [(f"mc{_f}", _f, "mc"), (f"pp{_f}", _f, "pp"), (f"mp{_f}", _f, "mp"), (f"dp{_f}", _f, "dp")]
BONE_IDS = [b[0] for b in BONES]
BONE_INFO = {b[0]: (b[1], b[2]) for b in BONES}
METACARPALS = [b for b in BONE_IDS if b.startswith("mc")]
PHALANGES = [b for b in BONE_IDS if not b.startswith("mc")]

# The default hand (the model the animation was built on): metacarpals 2-5 are
# the distances from their base to the knuckle in hands.py; the rest are the
# phalanx lengths there.
DEFAULT_ANATOMY = {
    "mc1": 4.7, "pp1": 3.3, "dp1": 2.8,
    "mc2": 6.684, "pp2": 4.0, "mp2": 2.3, "dp2": 1.8,
    "mc3": 6.626, "pp3": 4.5, "mp3": 2.7, "dp3": 1.9,
    "mc4": 6.332, "pp4": 4.2, "mp4": 2.6, "dp4": 1.9,
    "mc5": 5.974, "pp5": 3.3, "mp5": 1.8, "dp5": 1.7,
}
# Each bone may be 70%..135% of the default: from a small (child's or
# petite adult's) hand to a very large one, in any proportion.
BONE_MIN, BONE_MAX = 0.70, 1.35


def bone_name(bid):
    f, kind = BONE_INFO[bid]
    return f"{FINGER_WORD[f]} {BONE_WORD[kind]}"


def clamp_bone(bid, v):
    d = DEFAULT_ANATOMY[bid]
    return max(d * BONE_MIN, min(d * BONE_MAX, v))


# --------------------------------------------------------------------------- #
# Behaviour
# --------------------------------------------------------------------------- #
# kind "slider": min, max, default, fmt(value)->str, lo/hi end labels
# kind "options": [(value, label, detail)], default
def _pct(v): return f"{int(round(v * 100))}%"
def _ms(v): return f"{int(round(v * 1000))} ms"
def _deg(v): return f"{int(round(v))}°"
def _mps(v): return f"{v:.1f} m/s"


def finger_lead(a):
    """
    (longest head start in s, share of it spent travelling) for the finger
    anticipation setting a (-1..1): from 0 to 1 a finger may set off 0.4 to
    1.4 s ahead and arrive early to hover; below 0 it sets off later and
    later (0.07 s at -1) and arrives just in time.
    """
    if a >= 0:
        return 0.4 + a, 0.9 - 0.6 * a
    return 0.4 + 0.33 * a, 0.9 - 0.1 * a


def _lead(v):
    return f"{int(round(finger_lead(v)[0] * 1000))} ms ahead"
def _bias(v):
    if abs(v) < 0.05:
        return "neutral"
    return f"{int(round(abs(v) * 100))}% toward {'stretching' if v > 0 else 'shifting / crossing'}"


BEHAVIORS = [
    # ---- motion (hands.py)
    dict(id="retraction", group="Motion", label="Retraction of unused fingers", kind="slider",
         min=0.0, max=1.0, default=0.1, fmt=_pct, lo="Stay over the keys", hi="Curl well back",
         desc="How far fingers that aren't playing, and aren't about to, pull back and up "
              "toward the palm."),
    dict(id="finger_curve", group="Motion", label="Finger curvature", kind="slider",
         min=0.0, max=1.0, default=0.4, fmt=_pct, lo="Flatter fingers", hi="Strongly curved",
         desc="How curved the fingers are while they press the keys: flatter fingers let the hand sit "
              "further back from the keys, curved fingers bring it forward over them."),
    dict(id="antic_hand", group="Motion", label="Anticipation speed (hand)", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Last moment", hi="Very early",
         desc="How early the whole hand moves into position for the notes coming up."),
    dict(id="antic_fingers", group="Motion", label="Anticipation speed (fingers)", kind="slider",
         min=-1.0, max=1.0, default=0.5, fmt=_lead, lo="Just in time", hi="Very early",
         desc="How early each finger travels to its next key and waits there, ready to strike - "
              "a thumb passing under, say. The value is the longest head start; a finger never "
              "leaves before it has let go of its last key, and never so late that it would have "
              "to move faster than the top travel speed."),
    dict(id="cross_height", group="Motion", label="Crossing height", kind="slider",
         min=0.0, max=1.0, default=0.33, fmt=_pct, lo="Skim over", hi="Arch high",
         desc="How high a finger arches when it crosses over the thumb."),
    dict(id="lift_height", group="Motion", label="Finger lift height", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Close to the keys", hi="High lift",
         desc="How high a finger rises above its key before striking it."),
    dict(id="wrist_bounce", group="Motion", label="Wrist bounce (repeated chords)", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Still wrist", hi="Big bounce",
         desc="In repeated chords and octaves the hand plays from the wrist: it drops onto each chord "
              "and springs back up between them. This sets how high it springs."),
    dict(id="tremolo_rotation", group="Motion", label="Wrist rotation (tremolos)", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Fingers only", hi="Strong rotation",
         desc="In tremolos and other quick alternations between the two sides of the hand, the forearm "
              "rotates, rocking the thumb side and the little-finger side down in turn."),
    dict(id="gesture_finger_action", group="Motion", label="Finger action in bounces & tremolos",
         kind="slider", min=0.0, max=1.0, default=0.25, fmt=_pct, lo="Hand does it all",
         hi="Fingers lift fully",
         desc="How much the fingers still lift on their own when the wrist bounce or rotation is "
              "doing the playing (repeated chords, tremolos)."),
    dict(id="cross_turn", group="Motion", label="Wrist turn for thumb crossings", kind="slider",
         min=0.0, max=30.0, default=14.0, fmt=_deg, lo="Quiet wrist", hi="Turns a lot",
         desc="How far the hand turns toward the little finger while the thumb passes under "
              "(and back the other way when a finger crosses over)."),
    dict(id="smoothness", group="Motion", label="Hand motion smoothness", kind="slider",
         min=0.02, max=0.15, default=0.07, fmt=_ms, lo="Quick, direct", hi="Smooth, flowing",
         desc="How much the hand's movements are smoothed: shifts accelerate and settle over "
              "this long."),
    dict(id="early_release", group="Motion", label="Early release before a jump", kind="slider",
         min=0.05, max=0.40, default=0.22, fmt=_ms, lo="Legato, late", hi="Detached, early",
         desc="How early a finger lets go of its key when it has to jump straight to another one. "
              "This changes what you hear (without the pedal)."),
    dict(id="max_speed", group="Motion", label="Top travel speed", kind="slider",
         min=0.5, max=5.0, default=3.0, fmt=_mps, lo="Unhurried", hi="Virtuoso leaps",
         desc="The fastest any part of the hand - wrist or fingertip - can travel across the keys. "
              "Fingerings and the split between the hands are chosen so the hands never need to "
              "move faster; when the music still asks for more, the key is let go earlier or struck "
              "late, and that is what you hear."),
    dict(id="roll_speed", group="Motion", label="Rolled chord speed", kind="slider",
         min=0.015, max=0.08, default=0.035, fmt=_ms, lo="Quick roll", hi="Slow roll",
         desc="Chords too wide for this hand are rolled from the bottom up; this is the time "
              "between the notes of the roll."),
    # ---- fingering (fingering.py / figures.py / hand_split.py)
    dict(id="fingering_model", group="Fingering", label="Fingering model", kind="options",
         default="learned",
         options=[("learned", "Learned from pianists", "Weights fitted to how professional pianists "
                   "finger real repertoire (the PIG dataset)"),
                  ("textbook", "Textbook (Hanon)", "The original weights, tuned to match Hanon's "
                   "printed fingering, weak-finger training included")],
         desc="The overall cost model behind the fingering. Standard fingerings for scales, "
              "arpeggios, octaves and thirds apply either way; the other settings here adjust it."),
    dict(id="weak_bias", group="Fingering", label="Weak finger bias", kind="slider",
         min=0.0, max=1.0, default=0.25, fmt=_pct, lo="Use 4 and 5 freely", hi="Avoid 4 and 5",
         desc="Bias against the weaker 4th and 5th fingers when stronger ones would do. "
              "Every note is still played."),
    dict(id="stretch_bias", group="Fingering", label="Stretch bias", kind="slider",
         min=-1.0, max=1.0, default=0.0, fmt=_bias, lo="Shift / cross", hi="Stretch",
         desc="Reach wide intervals by stretching the hand, or by shifting and crossing over/under. "
              "The reach itself comes from this pianist's anatomy."),
    dict(id="economy", group="Fingering", label="Economy of motion", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Ignore finger travel", hi="Minimise travel",
         desc="How much to favour fingerings that keep the fingers close to where a relaxed hand "
              "already has them, weighing how far each finger must travel against the time it has."),
    dict(id="black_avoid", group="Fingering", label="Thumb and little finger on black keys", kind="slider",
         min=0.0, max=1.0, default=0.5, fmt=_pct, lo="Don't mind", hi="Avoid strongly",
         desc="How strongly to avoid putting the thumb (and, less so, the little finger) on black keys."),
    dict(id="chromatic", group="Fingering", label="Chromatic scale fingering", kind="options",
         default="13",
         options=[("12", "1212123 12121 23…", "1-2: thumb on white keys, 2 on the black keys and on F / C"),
                  ("13", "1313123 13131 23…", "Traditional: thumb on white keys, 3 on the black keys, 2 on F / C"),
                  ("1234", "1234 123 1234 123…", "Groups of up to four fingers, thumbs on white keys"),
                  ("12345", "1234 12345 123…", "Thumb on C, E and A; groups of four, five and three"),
                  ("231", "231231 212341…", "Thumb on D, F, G and B")],
         desc="Fingering for chromatic scales (shown for the right hand going up from C; "
              "the left hand mirrors it)."),
    dict(id="repeated", group="Fingering", label="Repeated note fingering", kind="options",
         default="321",
         options=[("321", "321321…", "Hanon's changing fingers 3-2-1 (4-3-2-1 in fours)"),
                  ("12", "121212…", ""), ("13", "131313…", ""),
                  ("123", "123123…", ""), ("1234", "12341234…", "")],
         desc="Fingers for quickly repeated notes on one key."),
    dict(id="trill", group="Fingering", label="Trill fingering", kind="options",
         default="auto",
         options=[("auto", "Automatic", "Any strong pair (1-2, 1-3, 2-3, 2-4), never 4-5"),
                  ("12", "121212…", ""), ("23", "232323…", ""), ("13", "131313…", ""),
                  ("14", "141414…", ""), ("1423", "14231423…", "")],
         desc="Fingers for trills (the lower note takes the lower finger numbers)."),
    dict(id="octaves", group="Fingering", label="Octave fingering", kind="options",
         default="4black",
         options=[("4black", "1-5, 4 on black keys", "Hanon: 4 on the black keys in octave passages"),
                  ("5", "1-5 always", "Thumb and little finger for every octave")],
         desc="Fingers for octaves (the right hand's; the left hand mirrors it)."),
]
BEHAVIOR = {b["id"]: b for b in BEHAVIORS}
DEFAULT_BEHAVIOR = {b["id"]: b["default"] for b in BEHAVIORS}


# --------------------------------------------------------------------------- #
# The pianist
# --------------------------------------------------------------------------- #
@dataclass
class Pianist:
    name: str
    id: str = ""
    color: tuple = (12, 12, 14)
    anatomy: dict = field(default_factory=lambda: dict(DEFAULT_ANATOMY))
    behavior: dict = field(default_factory=lambda: dict(DEFAULT_BEHAVIOR))
    created: float = 0.0
    modified: float = 0.0
    skin: dict = field(default_factory=dict)      # how the hands look (skins.py)

    @property
    def builtin(self):
        return self.id == DEFAULT_ID

    def copy(self):
        import copy
        return Pianist(self.name, self.id, tuple(self.color), dict(self.anatomy), dict(self.behavior),
                       self.created, self.modified, copy.deepcopy(self.skin_settings()))

    def skin_settings(self):
        """The full skin settings (style, colours per style, options)."""
        from skins import normalize
        if not self.skin or "colors" not in self.skin:
            self.skin = normalize(self.skin, bone_color=self.color)
        return self.skin

    @property
    def badge_color(self):
        from skins import primary_color
        return primary_color(self.skin_settings())

    def b(self, key):
        """A behaviour value (the default if it isn't set)."""
        return self.behavior.get(key, DEFAULT_BEHAVIOR[key])

    def to_json(self):
        return {"name": self.name, "color": list(self.color), "anatomy": self.anatomy,
                "behavior": self.behavior, "created": self.created, "modified": self.modified,
                "skin": self.skin_settings()}

    @classmethod
    def from_json(cls, pid, d):
        anatomy = dict(DEFAULT_ANATOMY)
        for k, v in (d.get("anatomy") or {}).items():
            if k in anatomy:
                anatomy[k] = clamp_bone(k, float(v))
        behavior = dict(DEFAULT_BEHAVIOR)
        for k, v in (d.get("behavior") or {}).items():
            spec = BEHAVIOR.get(k)
            if not spec:
                continue
            if spec["kind"] == "slider":
                behavior[k] = max(spec["min"], min(spec["max"], float(v)))
            elif v in [o[0] for o in spec["options"]]:
                behavior[k] = v
        color = tuple(max(0, min(255, int(c))) for c in (d.get("color") or (12, 12, 14))[:3])
        from skins import normalize
        # pianists saved before skins existed keep their bone colour, as skeletons
        skin = normalize(d.get("skin") or {"style": "skeleton"}, bone_color=color)
        return cls(str(d.get("name") or pid), pid, color, anatomy, behavior,
                   float(d.get("created", 0)), float(d.get("modified", 0)), skin)

    # ----- derived measures ---------------------------------------------------
    def span_whites(self):
        """Comfortable thumb-to-little-finger stretch, in white keys (key centre to key centre)."""
        from hands import hand_span_inches, WHITE_KEY_IN
        return hand_span_inches(self.anatomy) / WHITE_KEY_IN

    def span_semitones(self):
        return self.span_whites() * 12.0 / 7.0

    def span_label(self):
        from hands import hand_span_inches
        return f"{interval_name(self.span_whites())}  ·  {hand_span_inches(self.anatomy) * 2.54:.1f} cm"

    def size_class(self):
        w = self.span_whites()                       # the default hand spans 8 (a 9th)
        return "Small" if w < 7.6 else "Medium" if w < 8.6 else "Large"


def default_pianist():
    from skins import default_skin
    return Pianist("Default", DEFAULT_ID, created=0.0, modified=0.0, skin=default_skin("cartoon"))


def _ordinal(n):
    return {1: "unison", 8: "octave"}.get(n, f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}")


def interval_name(whites):
    """A white-key distance (key centres) as an interval: 7 -> octave, 8 -> 9th, 9 -> 10th."""
    n = int(math.floor(whites + 0.15)) + 1
    return _ordinal(n)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "pianist"
    return s[:40]


def _path(pid):
    return os.path.join(FOLDER, pid + ".json")


def list_pianists():
    """All pianists: the built-in default first, then the saved ones."""
    out = [default_pianist()]
    if os.path.isdir(FOLDER):
        for fn in sorted(os.listdir(FOLDER)):
            if not fn.endswith(".json") or fn == "settings.json":
                continue
            pid = fn[:-5]
            if pid == DEFAULT_ID:
                continue
            try:
                with open(_path(pid), encoding="utf-8") as fh:
                    out.append(Pianist.from_json(pid, json.load(fh)))
            except Exception as exc:
                print(f"Skipping pianist file {fn}: {exc}")
    return out


def load(pid):
    if not pid or pid == DEFAULT_ID:
        return default_pianist()
    try:
        with open(_path(pid), encoding="utf-8") as fh:
            return Pianist.from_json(pid, json.load(fh))
    except Exception:
        return default_pianist()


def name_taken(name, except_id=None):
    n = name.strip().lower()
    return any(p.name.strip().lower() == n and p.id != except_id for p in list_pianists())


def save(p):
    """Write a pianist (giving it an id from its name the first time). Returns it."""
    if p.builtin:
        raise ValueError("The default pianist can't be changed; duplicate it instead")
    os.makedirs(FOLDER, exist_ok=True)
    now = time.time()
    if not p.id:
        base = _slug(p.name)
        pid, k = base, 2
        while pid == DEFAULT_ID or os.path.exists(_path(pid)):
            pid, k = f"{base}-{k}", k + 1
        p.id = pid
        p.created = p.created or now
    p.modified = now
    tmp = _path(p.id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(p.to_json(), fh, indent=2)
    os.replace(tmp, _path(p.id))
    if _active is not None and _active.id == p.id:
        set_active(p)
    return p


def delete(p):
    if p.builtin:
        return
    try:
        os.remove(_path(p.id))
    except FileNotFoundError:
        pass
    if active().id == p.id:
        set_active(default_pianist())


# --------------------------------------------------------------------------- #
# The active pianist
# --------------------------------------------------------------------------- #
_active = None


def active():
    global _active
    if _active is None:
        pid = DEFAULT_ID
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                pid = json.load(fh).get("active", DEFAULT_ID)
        except Exception:
            pass
        _active = load(pid)
    return _active


def set_active(p):
    global _active
    _active = p.copy()
    try:
        os.makedirs(FOLDER, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump({"active": p.id}, fh)
    except Exception as exc:
        print(f"Couldn't save the active pianist ({exc})")
