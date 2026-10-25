"""
profiles.py - Fingering profiles: how the planner fingers the standard figures, saved by name.

A profile is a set of choices - the fingering for chromatic scales, octaves,
thirds, sixths, repeated notes and trills, how firmly each figure keeps its
standard fingering, and the hand's general habits (weak fingers, stretching,
black keys, speed) - the settings a pianist keeps in its behaviour
(pianist.BEHAVIORS) plus how firmly. A profile goes on top of the active
pianist (its hand, its other habits and fine-tuning stay); the built-in
"Default" is the active pianist as it is and can't be changed. Every other
profile is a JSON file in pianists/fingering profiles, saved as it's edited;
the active one (an app setting) is what the suggestions are planned with.

No pygame here: `prepare` runs in the planner's worker processes.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

FORMAT = "miditool-fingering-profile"
BUILTIN_ID = "default"
BUILTIN_NAME = "Default (the pianist's own)"

FIRMNESS = [("off", "Off", "Leave it to the planner"), ("loose", "Loose", "A light suggestion"),
            ("normal", "Normal", "As Hand-thesia does"), ("strict", "Strict", "Keep to it whenever the hand can")]
FIRM_MULT = {"off": 0.0, "loose": 0.5, "normal": 1.0, "strict": 2.0}

# id, group, label, kind ("options" / "firm" / "slider"), default, ... ; the descriptions say what each does
SETTINGS = [
    dict(id="scales", group="Scales", label="Diatonic scales", kind="firm", default="normal",
         desc="How firmly scale runs take their standard fingering - the thumb on each key's usual notes "
              "(Hanon 39). Normal: long scales firmly, short runs loosely; Strict: short runs as firmly."),
    dict(id="chromatic", group="Scales", label="Chromatic scales", kind="options", default="13",
         options=[("13", "1313123", "Traditional: thumb on white keys, 3 on the black keys, 2 on F / C"),
                  ("12", "1212123", "Thumb on white keys, 2 on the black keys and on F / C"),
                  ("1234", "1234 123", "Groups of up to four fingers, thumbs on white keys"),
                  ("12345", "1234 12345", "Thumb on C, E and A: groups of four, five and three"),
                  ("231", "231 231", "Thumb on D, F, G and B")],
         desc="Shown for the right hand going up from C; the left hand mirrors it."),
    dict(id="chromatic_firm", group="Scales", label="Chromatic: how firmly", kind="firm", default="normal",
         desc="How firmly chromatic runs keep the fingering above."),
    dict(id="arpeggios", group="Arpeggios", label="Arpeggios", kind="firm", default="normal",
         desc="How firmly arpeggios take the book's fingering (Hanon 41: root-position triads in every key; "
              "sevenths 1-2-3-4; inversions cyclic with the thumb on a white key)."),
    dict(id="octaves", group="Octaves", label="Octaves", kind="options", default="4black",
         options=[("4black", "4 on black keys", "1-5, with 4 on the black keys in octave passages (Hanon 51-57)"),
                  ("5", "5 always", "Thumb and little finger for every octave"),
                  ("4all", "4 always", "Thumb and 4 for every octave - for large hands")],
         desc="The right hand's top finger; the left hand mirrors it (its bottom finger). Broken octaves too."),
    dict(id="octaves_firm", group="Octaves", label="Octaves: how firmly", kind="firm", default="normal",
         desc="How firmly octaves keep the fingering above."),
    dict(id="thirds", group="Thirds", label="Scales in thirds", kind="options", default="legato",
         options=[("legato", "Legato (Hanon 52)", "Groups of four and three thirds, crossing to keep them joined"),
                  ("simple", "1-3  2-4  3-5", "The same three positions over and over, the hand shifting")],
         desc="Thirds moving step by step through a key."),
    dict(id="chrom_thirds", group="Thirds", label="Chromatic thirds", kind="options", default="hanon",
         options=[("hanon", "Hanon 50", "The printed twelve-step fingering"),
                  ("simple", "1-3  2-4", "1-3 and 2-4 in turn")],
         desc="Thirds moving by semitones."),
    dict(id="thirds_firm", group="Thirds", label="Thirds: how firmly", kind="firm", default="normal",
         desc="How firmly thirds - in scales, chromatic, trills, inside a hand position - keep their fingering."),
    dict(id="sixths", group="Sixths", label="Sixths", kind="options", default="4black",
         options=[("4black", "4 on black keys", "1-5, with 4 on a black key"),
                  ("5", "5 always", "1-5 for every sixth"),
                  ("4", "4 always", "1-4 for every sixth")],
         desc="Sixths outside a trill (trills in sixths and fourths stay 1-4 / 2-5)."),
    dict(id="sixths_firm", group="Sixths", label="Sixths: how firmly", kind="firm", default="normal",
         desc="How firmly sixths keep their fingering."),
    dict(id="repeated", group="Repeated notes and trills", label="Repeated notes", kind="options", default="321",
         options=[("321", "321 321", "Hanon 44-47: changing fingers 3-2-1 (4-3-2-1 in fours)"),
                  ("12", "1212", ""), ("13", "1313", ""), ("123", "123 123", ""), ("1234", "1234 1234", "")],
         desc="Fingers for quickly repeated notes on one key."),
    dict(id="trill", group="Repeated notes and trills", label="Trills", kind="options", default="auto",
         options=[("auto", "Any strong pair", "1-2, 1-3, 2-3 or 2-4, whichever fits - never 4-5"),
                  ("12", "1212", ""), ("13", "1313", ""), ("23", "2323", ""), ("24", "2424", ""),
                  ("14", "1414", ""), ("1423", "1423", "")],
         desc="The lower note takes the lower finger numbers (the left hand mirrors it)."),
    dict(id="fingering_model", group="The hand", label="Fingering model", kind="options", default="learned",
         options=[("learned", "Learned from pianists", "Weights fitted to how professional pianists finger real "
                   "repertoire (the PIG dataset)"),
                  ("textbook", "Textbook (Hanon)", "Hand-thesia's original weights, tuned to Hanon's printed fingering")],
         desc="The cost model behind everything the figures above don't settle."),
    dict(id="weak_bias", group="The hand", label="Weak fingers (4 and 5)", kind="slider", default=0.25,
         min=0.0, max=1.0, lo="Use them freely", hi="Avoid them", fmt="pct",
         desc="A bias against the weaker 4th and 5th fingers when stronger ones would do."),
    dict(id="stretch_bias", group="The hand", label="Wide intervals", kind="slider", default=0.0,
         min=-1.0, max=1.0, lo="Shift / cross", hi="Stretch", fmt="bias",
         desc="Reach wide intervals by stretching the hand, or by shifting and crossing."),
    dict(id="black_avoid", group="The hand", label="Thumb and 5 on black keys", kind="slider", default=0.5,
         min=0.0, max=1.0, lo="Don't mind", hi="Avoid strongly", fmt="pct",
         desc="How strongly to keep the thumb (and, less so, the little finger) off black keys."),
    dict(id="economy", group="The hand", label="Economy of motion", kind="slider", default=0.5,
         min=0.0, max=1.0, lo="Ignore finger travel", hi="Minimise it", fmt="pct",
         desc="Favour fingerings that keep the fingers near where a relaxed hand already has them."),
    dict(id="max_speed", group="The hand", label="Top travel speed", kind="slider", default=3.0,
         min=0.5, max=5.0, lo="Unhurried", hi="Virtuoso leaps", fmt="speed",
         desc="The fastest the hand travels: the split between the hands and the fingering keep to it."),
    dict(id="glissando", group="The hand", label="Glissandos", kind="options", default="on",
         options=[("on", "Slide them", "A quick string of all-white or all-black keys going one way is slid "
                   "(shown as g)"),
                  ("off", "Finger every note", "Only glissandos marked in the file are slid")],
         desc="Whether quick strings of next-door keys are played as glissandos."),
]
SETTING = {s["id"]: s for s in SETTINGS}
DEFAULTS = {s["id"]: s["default"] for s in SETTINGS}
HIDDEN = {"gliss_gap": 0.05, "gliss_min": 6, "gliss_merge": 1.0}      # (Hand-thesia behaviours not offered here)
FIG_WEIGHTS = (("chromatic_firm", ("W_CHROM",)), ("arpeggios", ("W_ARP",)), ("octaves_firm", ("W_OCT",)),
               ("thirds_firm", ("W_THIRDS",)), ("sixths_firm", ("W_SIXTHS",)))


def full_settings(settings):
    """Every setting, the profile's where it has a valid one, else the default."""
    out = dict(DEFAULTS)
    for k, v in (settings or {}).items():
        s = SETTING.get(k)
        if s is None:
            continue
        if s["kind"] == "slider":
            try:
                out[k] = min(s["max"], max(s["min"], float(v)))
            except (TypeError, ValueError):
                pass
        elif s["kind"] == "firm" and v in FIRM_MULT:
            out[k] = v
        elif s["kind"] == "options" and v in {o[0] for o in s["options"]}:
            out[k] = v
    return out


def format_value(s, v):
    f = s.get("fmt")
    if f == "pct":
        return f"{int(round(v * 100))}%"
    if f == "bias":
        return "neutral" if abs(v) < 0.05 else f"{int(round(abs(v) * 100))}% {'stretch' if v > 0 else 'shift'}"
    if f == "speed":
        return f"{v:.1f} m/s"
    return f"{v:g}"


# --------------------------------------------------------------------------- #
# For the planner (worker processes)
# --------------------------------------------------------------------------- #
FIRM_IDS = {"scales", "chromatic_firm", "arpeggios", "octaves_firm", "thirds_firm", "sixths_firm"}


class PlannerPianist:
    """What the planner reads from a pianist (anatomy, behaviour, fine-tuned weights), as plain data."""

    def __init__(self, data):
        self.name = data.get("name", "pianist")
        self.anatomy = dict(data.get("anatomy") or {})
        self.behavior = dict(data.get("behavior") or {})
        self.weights = dict(data.get("weights") or {})

    def b(self, key):
        import pianist
        return self.behavior.get(key, pianist.DEFAULT_BEHAVIOR.get(key, HIDDEN.get(key)))


def figure_weights(s):
    """Fine-tune values (fingering.FINE_TUNE ids) for the profile's firmness choices that aren't Normal."""
    import fingering
    base = fingering._fig_defaults()
    w = {}
    if s["scales"] == "strict":
        w["fig:W_SCALE"], w["fig:W_SCALE_LONG"] = base["W_SCALE_LONG"], base["W_SCALE_LONG"] * 2
    elif s["scales"] != "normal":
        m = FIRM_MULT[s["scales"]]
        w["fig:W_SCALE"], w["fig:W_SCALE_LONG"] = base["W_SCALE"] * m, base["W_SCALE_LONG"] * m
    for sid, names in FIG_WEIGHTS:
        if s[sid] != "normal":
            for n in names:
                w["fig:" + n] = base[n] * FIRM_MULT[s[sid]]
    return w


def pianist_data(p):
    """A pianist as plain data for the planner jobs (they run in other processes)."""
    return {"name": p.name, "anatomy": dict(p.anatomy), "behavior": dict(p.behavior),
            "weights": dict(getattr(p, "weights", None) or {})}


def planner_pianist(planner):
    """
    The pianist the planner works with: planner = {"pianist": pianist_data, "settings": a profile's settings or
    None} - the active pianist, a saved profile's choices on top (its figures and habits in the behaviour, its
    firmness in the fine-tuned weights). None: the active pianist as it is.
    """
    if planner is None:
        import pianist
        return pianist.active()
    p = PlannerPianist(planner["pianist"])
    settings = planner.get("settings")
    if settings:
        s = full_settings(settings)
        p.behavior.update({k: v for k, v in s.items() if k not in FIRM_IDS})
        p.weights.update(figure_weights(s))
    return p


def prepare(planner):
    """Set the planner up for `planner` (planner_pianist); returns the pianist."""
    import fingering
    p = planner_pianist(planner)
    fingering.apply_pianist(p)
    return p


def restore_host():
    """The planner set up for the active pianist again (after a job run in this process)."""
    import fingering
    import pianist
    fingering.apply_pianist(pianist.active())


# --------------------------------------------------------------------------- #
# Profiles on disk
# --------------------------------------------------------------------------- #
def data_dir():
    """Where profiles live: beside the pianists (pianist.FOLDER)."""
    import pianist
    return os.path.join(pianist.FOLDER, "fingering profiles")


class Profile:
    def __init__(self, pid, name, settings=None, builtin=False):
        self.id, self.name, self.builtin = pid, name, builtin
        self.settings = full_settings(settings)

    def get(self, key):
        return self.settings.get(key, DEFAULTS.get(key))

    def planner_settings(self):
        """The profile's own choices for the planner: None for the default (the active pianist as it is)."""
        return None if self.builtin else dict(self.settings)

    def differs(self):
        """Setting ids whose value isn't the default."""
        return [k for k, v in self.settings.items() if v != DEFAULTS[k]]


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return (s or "profile") + "-" + uuid.uuid4().hex[:6]


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def active_settings():
    """The active pianist's own choices, as profile settings (the firmness ones: Normal)."""
    import pianist
    p = pianist.active()
    return {k: p.b(k) for k in SETTING if k not in FIRM_IDS and k in pianist.DEFAULT_BEHAVIOR}


class ProfileStore:
    """The profiles, the active one, and `rev` - bumped whenever what the planner should use changes."""

    def __init__(self):
        self.rev = 0
        self.problems = []
        self.reload()

    def reload(self):
        self.profiles = [Profile(BUILTIN_ID, BUILTIN_NAME, active_settings(), builtin=True)]
        d = data_dir()
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if not f.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        obj = json.load(fh)
                    if obj.get("format") != FORMAT:
                        continue
                    self.profiles.append(Profile(f[:-5], str(obj.get("name") or f[:-5]), obj.get("settings")))
                except (OSError, ValueError) as exc:
                    self.problems.append(f"{f}: {exc}")
        self.profiles[1:] = sorted(self.profiles[1:], key=lambda p: p.name.lower())
        want = self._read_active()
        self.active = next((p for p in self.profiles if p.id == want), self.profiles[0])
        self.rev += 1

    def _read_active(self):
        import pianist
        return pianist.app_setting("fingering_profile", BUILTIN_ID) or BUILTIN_ID

    def _write_active(self):
        import pianist
        try:
            pianist.set_app_setting("fingering_profile", self.active.id)
        except OSError as exc:
            self.problems.append(str(exc))

    def planner_input(self):
        """What the planner jobs get (prepare): the active pianist, and the active profile's choices."""
        import pianist
        return {"pianist": pianist_data(pianist.active()), "settings": self.active.planner_settings()}

    def follow_pianist(self):
        """The active pianist may have changed (another one chosen, its settings edited): Default follows it."""
        import pianist
        key = (pianist.active().id, tuple(sorted(pianist_data(pianist.active())["behavior"].items(), key=str)))
        if key != getattr(self, "_pianist_key", None):
            self._pianist_key = key
            self.profiles[0].settings = full_settings(active_settings())
            self.rev += 1

    def get(self, pid):
        return next((p for p in self.profiles if p.id == pid), None)

    def select(self, pid):
        p = self.get(pid)
        if p is not None and p is not self.active:
            self.active = p
            self.rev += 1
            self._write_active()
        return p

    def unique_name(self, base):
        names = {p.name.lower() for p in self.profiles}
        if base.lower() not in names:
            return base
        k = 2
        while f"{base} {k}".lower() in names:
            k += 1
        return f"{base} {k}"

    def save(self, p):
        if p.builtin:
            return
        _atomic_json(os.path.join(data_dir(), p.id + ".json"),
                     {"format": FORMAT, "version": 1, "name": p.name, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "settings": p.settings})

    def create(self, name, settings=None):
        """A new saved profile (made active)."""
        p = Profile(_slug(name), self.unique_name(name), settings)
        self.save(p)
        self.profiles.append(p)
        self.profiles[1:] = sorted(self.profiles[1:], key=lambda q: q.name.lower())
        self.select(p.id)
        return p

    def rename(self, p, name):
        name = name.strip()[:48]
        if p.builtin or not name or name == p.name:
            return False
        others = {q.name.lower() for q in self.profiles if q is not p}
        p.name = name if name.lower() not in others else self.unique_name(name)
        self.save(p)
        self.profiles[1:] = sorted(self.profiles[1:], key=lambda q: q.name.lower())
        return True

    def delete(self, p):
        if p.builtin:
            return False
        try:
            os.remove(os.path.join(data_dir(), p.id + ".json"))
        except FileNotFoundError:
            pass
        self.profiles = [q for q in self.profiles if q is not p]
        if self.active is p:
            self.active = self.profiles[0]
            self.rev += 1
            self._write_active()
        return True

    def set_value(self, p, key, value, save=True):
        """Change one setting of a (saved) profile; the planner follows if it's the active one."""
        if p.builtin:
            return False
        p.settings = full_settings(dict(p.settings, **{key: value}))
        if save:
            self.save(p)
        if p is self.active:
            self.rev += 1
        return True
