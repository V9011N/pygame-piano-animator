"""The app's screens, driven headlessly with scripted events."""
import pygame

from conftest import midi_path

import main
import midi_loader


def app_on(screen):
    return main.App(screen, sound=False)


def key(app, k, mod=0):
    return app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod))


def test_menu_player_and_studio_render(screen):
    app = app_on(screen)
    app.mode.render()
    app.play(midi_path("demo_song.mid"))
    assert type(app.mode).__name__ == "Visualizer"
    app.mode.seek(1.0)
    app.mode.render()
    app.studio()
    app.mode.render()
    assert type(app.mode).__name__ == "PianistStudio"


def test_editor_sequential_fingering(screen):
    app = app_on(screen)
    app.edit(midi_loader.load_song(midi_path("demo_song.mid")))
    ed = app.mode
    assert type(ed).__name__ == "FingeringEditor"
    first = min(range(len(ed.notes)), key=lambda i: (ed.notes[i].start, -ed.notes[i].pitch))
    ed.start_sequential(first)
    hand = ed.seq["hand"]
    assert all(ed.notes[j].hand == hand for st in ed.seq["steps"] for j in st)
    ed.render()

    key(app, pygame.K_3)                                  # same hand, finger 3
    assert ed.finger[first] == 3 and first in ed.user_set
    assert ed.seq_note() != first                         # moved on
    nxt = ed.seq_note()
    key(app, pygame.K_QUOTE if hand == "R" else pygame.K_LSHIFT)   # that hand's 5
    assert ed.finger[nxt] == 5
    key(app, pygame.K_BACKSPACE)
    assert ed.seq_note() == nxt
    ed.update(0.5)                                        # deferred rebuild of the hands
    assert ed._rebuild_due is None
    ed.render()
    key(app, pygame.K_ESCAPE)
    assert ed.seq is None and ed.dirty


def test_nail_shrinks_to_the_tip_and_goes_as_the_finger_curls():
    # measured on the author's hand: the whole nail shows with the last phalanx
    # pointing down to ~45 deg, a short cap at the very end by ~60, none from ~75
    import math
    import skins

    class Pen:
        pts = None

        def poly(self, pts, color):
            self.pts = pts

    class Hand:
        pass
    r, L = 9.0, 22.0
    spans = []
    for deg in (0, 30, 45, 60, 75, 90, 110):
        t = math.radians(deg)
        a = (0.0, 0.0, 40.0)                              # last knuckle; the finger points up (-y)
        b = (0.0, -L * math.cos(t), 40.0 - L * math.sin(t))
        h = Hand()
        h.radius = {3: [((0.0, 30.0, 60.0), a, r * 1.2), (a, b, r)]}
        pen = Pen()
        skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
        if pen.pts is None:
            spans.append(None)
            continue
        ys = [p[1] for p in pen.pts]
        xs = [p[0] for p in pen.pts]
        tip_end = min(b[1] - r, a[1] - r * 1.2)            # far end of the finger's outline
        spans.append((min(ys) - tip_end, max(ys) - min(ys), max(xs) - min(xs)))
    assert all(s is not None for s in spans[:4]) and spans[4:] == [None, None, None]
    gaps, lengths, widths = zip(*spans[:4])
    # the skin beyond the nail shrinks away, and the nail gets shorter, then narrower too
    assert gaps[0] > 0.5 * r and gaps[3] < 0.15 * r
    assert all(x >= y - 1e-6 for x, y in zip(gaps, gaps[1:]))
    assert all(x >= y - 1e-6 for x, y in zip(lengths, lengths[1:]))
    assert lengths[3] < 0.3 * lengths[0] and widths[3] < 0.8 * widths[0]


def test_no_nail_on_a_curled_finger():
    import skins

    class Pen:
        pts = None

        def poly(self, pts, color):
            self.pts = pts

    class Hand:
        pass
    # strongly curved: the middle phalanx points straight down and the tip tucks back under it
    pip, dip = (550.7, 572.2, 57.3), (550.7, 572.3, 26.6)
    h = Hand()
    h.radius = {3: [((557.2, 622.8, 59.8), pip, 13.8), (pip, dip, 12.0), (dip, (553.2, 591.8, 17.7), 9.7)]}
    pen = Pen()
    skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
    assert pen.pts is None
    # pointing straight down: no nail either (it faces forward)
    h.radius[3][-1] = (dip, (550.9, 573.5, 0.0), 9.7)
    skins._nail(pen, h, 3, (255, 255, 255), (0, 0, 0))
    assert pen.pts is None


def test_finger_thickness_range_and_default():
    import skins
    assert skins.FINGER_WIDTH_RANGE == (0.5, 1.2)
    assert skins.default_skin()["finger_width"] == 0.75
    # older pianists' values outside the new range are brought into it
    assert skins.normalize({"finger_width": 1.35})["finger_width"] == 1.2
    assert skins.normalize({"finger_width": 0.3})["finger_width"] == 0.5


def test_changelog_button_glows_until_opened(screen):
    app = app_on(screen)
    menu = app.mode
    assert menu.changelog_new                                  # fresh pianists folder: not seen yet
    menu.render()
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=menu.changelog_button.rect.center)
    app.handle_event(click)
    assert menu.changelog is not None and not menu.changelog_new
    menu.render()
    app.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-5))
    assert menu.changelog.scroll > 0
    key(app, pygame.K_ESCAPE)                                  # closes the changelog, not the app
    assert menu.changelog is None and app.mode is menu
    assert not main.MainMenu(app).changelog_new                # remembered
    entries = main.changelog_entries()
    assert len(entries) > 10 and all(bullets for _, bullets in entries)


def test_equal_keys_toggle_and_layout(screen):
    import common
    import pianist
    app = app_on(screen)
    assert common.key_style() == "realistic"
    app.settings()                                               # (the keyboard type is in the Settings)
    app.mode.render()
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=app.mode.keys.rect.center))
    assert common.key_style() == "equal" and pianist.app_setting("keys") == "equal"
    try:
        kb = common.Keyboard(pygame.Rect(0, 0, 1400, 150))
        assert kb.style == "equal"
        widths = {w for _, w in kb.lanes.values()}
        assert len(widths) == 1                                    # every lane exactly the same width...
        xs = [kb.lanes[p][0] for p in range(21, 109)]
        assert {b - a for a, b in zip(xs, xs[1:])} == {kb.pitch}   # ...and the same step (so the same gaps)
        blacks = {kb.key_rects[p].w for p in range(21, 109) if midi_loader.is_black_key(p)}
        tails = {kb.tails[p].w for p in kb.tails if p not in (21, 108)}
        assert blacks == tails == widths
        for group in ((0, 2, 4), (5, 7, 9, 11)):                   # the fronts: exactly alike within a group...
            fronts = {round(kb.fronts[p][1] - kb.fronts[p][0], 6) for p in range(22, 108) if p % 12 in group}
            assert len(fronts) == 1
        gaps = {round(kb.fronts[q][0] - kb.fronts[p][1], 6)          # ...and every gap between them the lanes' gap
                for p, q in zip(sorted(kb.fronts), sorted(kb.fronts)[1:])}
        assert gaps == {kb.gap}
        # drawn: every gap between fronts holds exactly `gap` pixels' worth of the gap colour
        surf = pygame.Surface((1400, 150))
        kb.draw(surf, {})
        row = [surf.get_at((x, 140))[0] for x in range(kb.keys_x[0], kb.keys_x[1])]
        dark = [(common.WHITE_KEY[0] - v) / (common.WHITE_KEY[0] - common.KEY_GAP[0]) for v in row]
        runs, cur = [], 0.0
        for d in dark:
            if d > 0.005:
                cur += d
            elif cur:
                runs.append(cur)
                cur = 0.0
        assert len(runs) == 51 and all(abs(r - kb.gap) < 0.03 for r in runs)
        assert kb.key_rects[108] == pygame.Rect(kb.tails[108].x, 0, kb.tails[108].w, 150)    # C8: front = back
        xs = [kb.lanes[p][0] for p in range(21, 109)]
        assert all(b > a for a, b in zip(xs, xs[1:]))              # in pitch order, never overlapping
        for p in range(21, 109):
            if midi_loader.is_black_key(p):
                assert kb.lanes[p] == (kb.key_rects[p].x, kb.key_rects[p].w)   # black key = its lane
            else:                                                  # a white key's back is its lane
                assert abs(kb.tails[p].centerx - (kb.lanes[p][0] + kb.lanes[p][1] / 2)) <= 1 or p in (21, 108)
        # A0's back takes what's left of the width (at most EQUAL_A0_MAX lanes), the rest an even margin
        left, right = kb.key_rects[21].left, kb.tails[108].right
        assert kb.tails[21].left == left and kb.pitch <= kb.tails[21].w <= common.EQUAL_A0_MAX * kb.pitch
        assert abs(left - (1400 - (right + kb.gap - kb.gap // 2))) <= 1
        for w in (1280, 1600, 1920, 2560, 3840):                   # and the same at any width
            k = common.Keyboard(pygame.Rect(0, 0, w, 150))
            assert len({lw for _, lw in k.lanes.values()}) == 1
            assert len({round(k.fronts[p][1] - k.fronts[p][0], 6) for p in range(22, 108) if p % 12 in (5, 7, 9, 11)}) == 1
        app.play(midi_path("demo_song.mid"))
        app.mode.seek(3.0)
        app.mode.render()
        app.edit(midi_loader.load_song(midi_path("demo_song.mid")))
        app.mode.render()
    finally:
        common.set_key_style("realistic")


class _FakePort:
    """A MIDI output that records what is sent to it."""

    def __init__(self):
        self.sent = []

    def write_short(self, status, a=0, b=0):
        self.sent.append((status, a, b))

    def note_on(self, pitch, vel, ch=0):
        self.sent.append(("on", pitch, ch))

    def note_off(self, pitch, vel=0, ch=0):
        self.sent.append(("off", pitch, ch))

    def set_instrument(self, *a):
        pass

    def close(self):
        pass


def test_leaving_with_the_pedal_down_silences_the_synth(screen):
    import main
    from common import MidiOut
    from midi_loader import LEFT, RIGHT, MidiSong, Note, TrackInfo
    notes = [Note(60 + i, 0.2 * i, 0.2 * i + 0.15, 80, 0, RIGHT) for i in range(10)]
    tracks = [TrackInfo(0, "Right", 0, 1, RIGHT), TrackInfo(1, "Left", 0, 1, LEFT)]
    song = MidiSong(notes, tracks, 5.0, [], [], controls=[(0.0, 64, 127)])   # sustain pedal down throughout
    for mode in ("player", "editor"):
        app = main.App(screen, sound=False)
        app.midi = MidiOut(False)
        app.midi.port = port = _FakePort()
        if mode == "player":
            app.play(song)
        else:
            app.edit(song)
        v = app.mode
        v.seek(0.5)
        if v.paused:
            v.toggle_pause()
        for _ in range(10):
            v.update(0.05)
        assert any(m[:3] == (0xB0, 64, 127) for m in port.sent if isinstance(m[0], int))   # the pedal went down
        port.sent.clear()
        app.menu()                                                      # leave mid-performance
        for ch in (0, 1):
            assert (0xB0 | ch, 64, 0) in port.sent                      # pedal up...
            assert (0xB0 | ch, 123, 0) in port.sent                     # ...all notes off...
            assert (0xB0 | ch, 120, 0) in port.sent                     # ...and all sound off


def test_version_and_changelog_headings_are_well_formed():
    # versions and the changelog are the author's to set (a snapshot needn't have an entry)
    import os
    import re
    from version import VERSION
    assert re.fullmatch(r"v\d\d\.\d+\.\d+(\.SNAPSHOT-\d+)?", VERSION)
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
    with open(path, encoding="utf-8") as fh:
        top = next(line for line in fh if line.startswith("## "))
    assert re.match(r"## v\d\d\.\d+\.\d+\S* - ", top), top


def test_pedals_light_up_and_keep_clear_of_the_version(screen):
    # soft (left) down, sostenuto (middle) and sustain (right) up: only the left one is red
    import common
    import pianist
    from midi_loader import SOFT, SOSTENUTO, SUSTAIN
    app = app_on(screen)
    screen.fill((0, 0, 0))
    area = pygame.Rect(0, screen.get_height() - 300, screen.get_width(), 300)
    r = common.draw_pianist_badge(screen, app.fonts, area, pianist.active(), {SOFT: 127, SOSTENUTO: 0, SUSTAIN: 0})
    scale = r.w / (common.PEDAL_SIZE[0] + 0.7)

    def foot(x, y):
        return screen.get_at((int(r.x + x * scale), int(r.y + (y - common.PEDAL_TOP) * scale)))[:3]
    red, mid, right = foot(31, 72), foot(60, 70), foot(89, 70)
    assert red[0] > red[1] + 80                                   # lit
    for c in (mid, right):
        assert c[0] > 150 and c[1] > 130 and c[2] < c[1] - 30      # brass
    version_top = screen.get_height() - app.fonts["small"].get_height() - 4    # (App.draw_version)
    assert r.bottom <= version_top


def test_keyboard_placement_limits():
    import common
    size = (1280, 800)
    try:
        default, _ = common.bottom_layout(size)
        common.set_keyboard_place(0.0)                           # highest: the top of the keys at the centre
        kb, hand = common.bottom_layout(size)
        assert kb.y == size[1] // 2 and hand.top == kb.bottom and hand.bottom == size[1]
        common.set_keyboard_place(1.0)                           # lowest: half a keyboard above the bottom
        kb, hand = common.bottom_layout(size)
        assert size[1] - kb.bottom == kb.h // 2 and hand.h == size[1] - kb.bottom
        common.set_keyboard_place(None)
        assert common.bottom_layout(size)[0] == default
    finally:
        common.set_keyboard_place(None)


def test_settings_screen_drags_the_keyboard_and_toggles_profiling(screen):
    import common
    import pianist
    app = app_on(screen)
    try:
        app.mode.render()
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=app.mode.settings_button.rect.center))
        st = app.mode
        assert type(st).__name__ == "SettingsScreen"
        st.render()
        kb, _ = st._kb()
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=kb.center))
        app.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(kb.centerx, 0), rel=(0, 0), buttons=(1, 0, 0)))
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(kb.centerx, 0)))
        assert common.keyboard_place() == 0.0 and pianist.app_setting("keyboard_place") == 0.0
        assert st._kb()[0].y == screen.get_height() // 2               # (it can't go higher)
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.perf.rect.center))
        assert app.perf_overlay and pianist.app_setting("perf_overlay") is True
        for _ in range(5):
            app._frame_ms.append(16)
        st.render()
        app.draw_perf()
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.reset.rect.center))
        assert common.keyboard_place() is None
        key(app, pygame.K_ESCAPE)
        assert type(app.mode).__name__ == "MainMenu"
        assert app_on(screen).perf_overlay                             # remembered
    finally:
        common.set_keyboard_place(None)


def test_the_progress_bar_is_clear_and_the_controls_sit_under_it(screen):
    import pianist
    app = app_on(screen)
    app.play(midi_path("demo_song.mid"))
    v = app.mode
    v.render()
    bar = v.bar_rect
    # nothing on the bar but the name and time: every control is below it, on the right
    for rect, _ in v._top_items:
        assert rect.top >= bar.bottom and rect.right > screen.get_width() // 2
    assert v.controls_rect.top >= v.fall_rect.top and v.keys_button.top >= v.controls_rect.bottom
    # so a click anywhere along the bar seeks - even at its right end, where the controls were
    for x in (bar.right - 12, bar.right - 200, bar.centerx):
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, bar.centery)))
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(x, bar.centery)))
        assert abs(v.t - v.song.duration * x / bar.w) < 0.6
    # the arrow-keys button shows the key controls (kept for next time); a click on them doesn't seek
    assert not v.show_keys and v.keys_rect.w == 0
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=v.keys_button.center))
    v.render()
    assert v.show_keys and v.keys_rect.h > 200 and pianist.app_setting("player_keys") is True
    t0 = v.t
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=v.keys_rect.center))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=v.keys_rect.center))
    assert v.t == t0 and not v.dragging_bar
    assert any(k == "Space" for k, _ in v._key_controls()) and all(d for _, d in v._key_controls())
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=v.keys_button.center))
    v.render()
    assert not v.show_keys and v.keys_rect.w == 0
    # "sound on / off": a click mutes and unmutes, as M does (a stand-in soundfont player to hear it)
    from test_soundfont import player
    v.midi = app.midi = player("Grand.sf2")
    v.render()
    assert v.sound_rect.w and any("Grand soundfont" in tip for _, tip in v._top_items)
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=v.sound_rect.center))
    assert app.midi.muted
    key(app, pygame.K_m)
    assert not app.midi.muted


def test_volume_and_top_bar_tooltips(screen):
    import pianist
    app = app_on(screen)
    app.play(midi_path("demo_song.mid"))
    v = app.mode
    v.render()
    tips = [tip for _, tip in v._top_items]
    assert any("Volume" in t for t in tips) and any("key controls" in t for t in tips) and all(tips)
    assert any("hands" in d for _, d in v._key_controls())
    r = v.volume._track()
    t0 = v.t
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(r.x + r.w // 4, r.centery)))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(r.x + r.w // 4, r.centery)))
    assert abs(app.midi.volume - 0.25) < 0.03 and abs(pianist.app_setting("volume") - 0.25) < 0.03
    assert v.t == t0 and not v.dragging_bar                          # (the click didn't seek: it's in the panel)
    # the finger numbers fit the narrowest notes
    px = v._finger_size(v.keyboard)
    narrow = min(w for _, w in v.keyboard.lanes.values())
    assert v._finger_font(px).size("8")[0] + 3 <= narrow or px == 11
    # the editor has the same volume
    app.edit(midi_path("demo_song.mid"))
    ed = app.mode
    ed.render()
    assert abs(ed.volume.value - 0.25) < 0.03
    r = ed.volume._track()
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(r.right, r.centery)))
    assert app.midi.volume == 1.0


def test_frame_rate_cap_setting(screen):
    import pianist
    app = app_on(screen)
    assert app.fps_cap == 60                                         # the default
    app.settings()
    st = app.mode
    st.render()
    tr = st.fps.track
    for x, want in ((tr.x - 4, 24), (tr.right + 4, None), (tr.x + tr.w // 2, None)):
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x, tr.centery)))
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(x, tr.centery)))
        if want is None and x < tr.right:
            assert 24 < app.fps_cap < 240                            # somewhere in between
        else:
            assert app.fps_cap == want, (x, app.fps_cap)             # all the way right: uncapped
    assert pianist.app_setting("fps_cap") == app.fps_cap
    app.set_fps_cap(None)
    assert app_on(screen).fps_cap is None                            # remembered, uncapped too
    import main
    assert main.fps_cap_value(500) == 240 and main.fps_cap_value(5) == 24 and main.fps_cap_value("x") == 60


def test_a_falling_note_keeps_its_length_and_moves_smoothly():
    """No flicker: frame after frame a note's box is exactly the same length, and it moves on steadily."""
    pps = 173.3                                      # (pixels a second: not a whole number)
    rects = [main.note_rect(10, 20, 700, 5.0, 5.37, t, pps) for t in [i / 144 for i in range(500)]]
    assert len({r.h for r in rects}) == 1                # one length, every frame (no flicker)
    steps = [b.bottom - a.bottom for a, b in zip(rects, rects[1:])]
    assert all(s in (1, 2) for s in steps)           # 1.2 px a frame: never still, never back
    for i, r in enumerate(rects):                    # and within half a pixel of where it truly is
        assert abs(r.bottom - (700 - (5.0 - i / 144) * pps)) <= 0.5


def test_bend_chain_curls_fingers_down_and_tucks_the_thumb():
    import math
    import pianist
    from hands import BEND_RANGE, HandGeometry, clamp_bend, curl_factor, static_skeleton
    p = pianist.Pianist("x")
    geo = HandGeometry(p.anatomy)
    for shape in ("stretched", "natural"):
        flat = static_skeleton(geo, shape, curl_factor(p))
        bent = static_skeleton(geo, shape, curl_factor(p), {2: 1.0, 5: -0.5, 1: 1.0})
        t0, t1 = flat["tips"], bent["tips"]
        assert t1[2][2] < t0[2][2] - 0.5                    # the index curls down
        assert t1[5][2] > t0[5][2] + 0.5                    # the little finger lifts
        assert t1[3] == t0[3] and t1[4] == t0[4]            # the others stay put
        assert t1[1][0] > t0[1][0]                          # the thumb swings in toward the palm
        def turns(chain):                                   # each thumb joint's turn toward +x (rad)
            d = [math.atan2(q[0] - p[0], q[1] - p[1]) for p, q in zip(chain, chain[1:])]
            return [b - a for a, b in zip(d, d[1:])]
        flat_t, bent_t = turns(flat["struct"]["chains"][1]), turns(bent["struct"]["chains"][1])
        assert all(b > a + 0.3 for a, b in zip(flat_t, bent_t))   # every joint curls, not just the base
        for f in (1, 2):                                    # bones keep their lengths
            for a, b, c, d in zip(flat["struct"]["chains"][f], flat["struct"]["chains"][f][1:],
                                  bent["struct"]["chains"][f], bent["struct"]["chains"][f][1:]):
                assert math.isclose(math.dist(a, b), math.dist(c, d), rel_tol=1e-9)
    assert clamp_bend(2, 5) == BEND_RANGE["finger"][1] and clamp_bend(1, -5) == BEND_RANGE["thumb"][0]


def test_overview_scrolls_a_hovered_finger_and_the_shape_button_resets(screen):
    app = app_on(screen)
    app.studio()
    st = app.mode
    st._start_new("Bendy")
    st.render()
    _, pts = st._finger_lines[2]
    pos = ((pts[-2][0] + pts[-1][0]) // 2, (pts[-2][1] + pts[-1][1]) // 2)
    st.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
    assert st.hover_finger == 2
    for _ in range(3):                                      # scrolling down curls it in
        st.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1))
        st.render()
    assert st.hover_finger == 2 and abs(st.bends[2] - 0.3) < 1e-9
    st.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=8))
    assert st.bends[2] == -0.5                              # up: straightens, then lifts (clamped)
    st.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(2, screen.get_height() // 2),
                                       rel=(0, 0), buttons=(0, 0, 0)))
    assert st.hover_finger is None
    st._action("shape")
    assert st.bends == {} and st.shape == "natural"
    assert "bends" not in st.work.to_json()                 # (a preview: nothing saved)
