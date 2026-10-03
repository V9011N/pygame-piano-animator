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
    menu = app.mode
    assert common.key_style() == "realistic"
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=menu.keys_button.rect.center))
    assert common.key_style() == "equal" and pianist.app_setting("keys") == "equal"
    try:
        kb = common.Keyboard(pygame.Rect(0, 0, 1400, 150))
        assert kb.style == "equal"
        widths = {w for _, w in kb.lanes.values()}
        assert max(widths) - min(widths) <= 1                      # every lane the same width
        xs = [kb.lanes[p][0] for p in range(21, 109)]
        assert all(b > a for a, b in zip(xs, xs[1:]))              # in pitch order, never overlapping
        for p in range(21, 109):
            if midi_loader.is_black_key(p):
                assert kb.lanes[p] == (kb.key_rects[p].x, kb.key_rects[p].w)   # black key = its lane
            else:                                                  # a white key's back is its lane
                assert abs(kb.tails[p].centerx - (kb.lanes[p][0] + kb.lanes[p][1] / 2)) <= 1 or p in (21, 108)
        assert kb.key_rects[21].left <= 1 and kb.key_rects[108].right >= 1398
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
