"""Settings > Font: the bundled typeface by default, a chosen file or installed font, and the fallbacks."""
import os

import pygame

import common
from common import set_font_choice, ui_font
import paths


def bundled(bold=False):
    return paths.resource("assets", "fonts", common.FONT_FILES[bold])


def test_the_bundled_typeface_is_the_default_and_a_file_can_replace_it(screen, tmp_path):
    try:
        assert set_font_choice(None) == ""
        assert common.has_letters(ui_font(17)) and common.has_letters(ui_font(13, bold=True))
        default_h = ui_font(17).get_height()
        # a font file: its own size (no scaling), and made bold by pygame (no bold file of its own)
        assert set_font_choice({"file": bundled()}) == ""
        assert common.font_choice() == {"file": bundled()}
        assert ui_font(17).get_height() != default_h and ui_font(13, bold=True).get_bold()
        assert common.font_choice_name(common.font_choice()) == "SourceSansPro-Regular"
        # gone, or not a font: the bundled one, and why
        assert set_font_choice({"file": str(tmp_path / "gone.ttf")}) == "not found"
        assert common.font_choice() is None and ui_font(17).get_height() == default_h
        junk = tmp_path / "junk.ttf"
        junk.write_bytes(b"not a font at all")
        assert set_font_choice({"file": str(junk)}) == "couldn't be loaded" and common.font_choice() is None
        assert set_font_choice({"system": "no-such-font-anywhere"}) == "not found"
    finally:
        set_font_choice(None)


def test_choosing_a_font_in_the_settings(screen, tmp_path, monkeypatch):
    import app_settings
    import main
    import pianist
    try:
        app = main.App(screen, sound=False)
        app.settings()
        st = app.mode
        st.render()
        assert st.font_text() == "Now: Source Sans Pro (built in)." and not st.font_default.enabled
        # the list: the built-in one first, then the installed fonts; typing filters it
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.font_choose.rect.center))
        assert st.picker is not None and st.picker.shown[0][1] is None
        st.render()
        for ch in "zzzz-none":
            app.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=ch))
        assert st.picker.shown == []
        key = lambda k: app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""))
        key(pygame.K_ESCAPE)
        assert st.picker is None and pianist.app_setting("font") is None
        # "Browse for a file...": a font file, kept for next time; the fonts change at once
        monkeypatch.setattr(app_settings, "pick_file", lambda *a, **k: bundled())
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.font_choose.rect.center))
        st.render()
        h = app.fonts["normal"].get_height()
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.picker.browse.rect.center))
        assert pianist.app_setting("font") == {"file": bundled()} and app.fonts["normal"].get_height() != h
        st.render()
        assert st.font_text() == "Now: SourceSansPro-Regular." and st.font_default.enabled
        # the built-in row (or Use default): back to the bundled typeface
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=st.font_choose.rect.center))
        st.render()
        row = pygame.Rect(st.picker.view.x, st.picker.view.y, st.picker.view.w, app_settings.PICK_ROW_H)
        app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=row.center))
        assert pianist.app_setting("font") is None and common.font_choice() is None
        # a chosen font that has gone: the bundled one at the next start, and the Settings say why
        pianist.set_app_setting("font", {"file": str(tmp_path / "Moved.ttf")})
        app = main.App(screen, sound=False)
        assert app.font_problem == "not found"
        app.settings()
        assert app.mode.font_text() == "Now: Source Sans Pro (built in) - Moved not found."
    finally:
        set_font_choice(None)


def test_a_chosen_font_borrows_the_characters_it_lacks(screen):
    """Arrows and the like that a chosen font hasn't got come from the bundled typeface (not boxes)."""
    regular = pygame.font.Font(bundled(), 20)
    font = common.FallbackFont(bundled(bold=True), 20, regular)
    # what it has and hasn't: a missing character still measures (as the font's box), so it's told by shape
    assert font.has("A") and font.has("←")
    assert not font.has("")                                     # (private use: no ordinary font has it)
    # as if the chosen font had no arrows: those are drawn by the fallback, the rest by the font itself
    font.has = lambda ch: ch not in "←→"
    assert font._runs("Skip ← →") == [("Skip ", font), ("←", regular), (" ", font), ("→", regular)]
    img = font.render("A←", True, (255, 255, 255))
    a_w = pygame.font.Font.size(font, "A")[0]
    arrow = regular.render("←", True, (255, 255, 255))
    assert img.get_width() == a_w + arrow.get_width() == font.size("A←")[0]
    # the arrow's pixels are the fallback's own, on the shared baseline
    y = max(font.get_ascent(), regular.get_ascent()) - regular.get_ascent()
    for x in range(arrow.get_width()):
        for yy in range(arrow.get_height()):
            assert img.get_at((a_w + x, y + yy)).a == arrow.get_at((x, yy)).a
    # a font chosen in the Settings gets the fallback; the bundled one doesn't need it
    try:
        assert not isinstance(ui_font(17), common.FallbackFont)
        set_font_choice({"file": bundled(bold=True)})
        assert isinstance(ui_font(17), common.FallbackFont)
    finally:
        set_font_choice(None)
