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
