"""Fine-tuning every fingering weight per pianist: the registry, overrides, storage and the studio page."""
import pygame

import fingering as F
import figures as G
import pianist
from midi_loader import RIGHT, Note


def test_every_weight_can_be_tuned():
    ids = set(F.FINE_TUNE_IDS)
    assert set(F.W) <= ids                                           # every cost weight
    figs = {k for k in dir(G) if k.startswith("W_")}
    assert {"fig:" + k for k in figs} <= ids                         # every figure weight
    assert {f"strength:{f}" for f in range(1, 6)} <= ids and {f"black_ease:{f}" for f in range(1, 6)} <= ids
    assert len(ids) == len(F.FINE_TUNE)                              # no duplicates
    for tid in ids:
        lo, hi, step = F.tune_range(tid)
        assert lo == 0.0 and hi >= 1.0 and 0 < step < hi


def test_overrides_apply_on_top_and_reset_cleanly():
    p = pianist.Pianist("Tuner")
    defaults = F.tuned_defaults(p)
    assert defaults["cross"] == F.BASE_W["cross"]
    p.weights = {"cross": 9.0, "fig:W_SCALE": 0.0, "strength:5": 2.0}
    F.apply_pianist(p)
    assert F.W["cross"] == 9.0 and G.W_SCALE == 0.0 and F.FINGER_STRENGTH[5] == 2.0
    assert F.tuned_defaults(p) == defaults                         # defaults don't move with the overrides
    p.weights = {}
    F.apply_pianist(p)
    assert F.W["cross"] == defaults["cross"] and G.W_SCALE == defaults["fig:W_SCALE"]
    assert F.FINGER_STRENGTH[5] == defaults["strength:5"]
    # a behaviour setting moves a weight's default; an override still wins
    p.behavior["stretch_bias"] = 1.0
    assert F.tuned_defaults(p)["cross"] > defaults["cross"]
    p.weights = {"cross": 1.0}
    F.apply_pianist(p)
    assert F.W["cross"] == 1.0
    F.apply_pianist(None)


def test_an_override_changes_the_fingering():
    # C D E F G A B C going up: the thumb passes under after 3 - unless crossing costs a fortune
    ps = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [Note(p, 1.0 + 0.25 * i, 1.2 + 0.25 * i, 80, 0, RIGHT) for i, p in enumerate(ps)]
    g = F.group_notes(notes)
    p = pianist.Pianist("Tuner")
    plain = F.plan_fingering(g, hand=RIGHT, context=notes, pianist=p)
    p.weights = {"cross": 60.0, "figure": 0.0, "awkward": 60.0}
    tuned = F.plan_fingering(g, hand=RIGHT, context=notes, pianist=p)
    assert [plain[id(n)] for n in notes] != [tuned[id(n)] for n in notes]
    F.apply_pianist(None)


def test_tuned_weights_are_saved_with_the_pianist():
    p = pianist.Pianist("Tuner", "tuner")
    p.weights = {"cross": 9.0, "fig:W_ARP": 1.5}
    d = p.to_json()
    d["weights"]["no_such_weight"] = 3.0
    d["weights"]["steal"] = -2.0
    q = pianist.Pianist.from_json("tuner", d)
    assert q.weights == {"cross": 9.0, "fig:W_ARP": 1.5, "steal": 0.0}
    assert q.copy().weights == q.weights and q.copy().weights is not q.weights


def test_studio_fine_tune_page(screen):
    import main
    app = main.App(screen, sound=False)
    app.studio()
    st = app.mode
    st._start_new("Tuner")
    st._action("behavior")
    st.render()
    st._action("finetune")
    assert st.page == "finetune" and len(st._ft_rows) == len(F.FINE_TUNE)
    st.render()
    row = next(r for r in st._ft_rows if r["id"] == "cross")
    row["slider"].on_change(9.0)
    other = next(r for r in st._ft_rows if r["id"] == "steal")
    other["slider"].on_change(1.0)
    assert st.work.weights == {"cross": 9.0, "steal": 1.0}
    st.render()
    st._reset_weight("cross")                                      # its own reset button
    assert st.work.weights == {"steal": 1.0} and row["slider"].value == st.ft_defaults["cross"]
    st._action("reset_weights")                                    # reset all
    assert st.work.weights == {}
    st._ft_scroll(10 ** 6)                                         # scrolled to the end and drawn
    st.render()
    st.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
    assert st.page == "behavior"
