"""
app.py - The fingering editor's screen (Hand-thesia's "Fingering editor"): the
top bar, the status line, files, and the keyboard and hands under the roll.

EditorMode is one of Hand-thesia's modes (main.App runs it: handle_event,
update, render, idle, leave): the MIDI Tool's piano roll (editor.py) with
Hand-thesia's keyboard and animated hands below it (hands_panel.py), playing
through Hand-thesia's synth (playback.HostOut). Long work - fingering plans,
impose's comparison - runs in worker processes (jobs.py), shared by every
visit to the editor and stopped when the app closes.
"""
from __future__ import annotations

import os
import time

import pygame

from . import combine
from . import impose as imp
from . import project
from .profiles import ProfileStore
from .profiles_ui import ProfileWindow
from . import theme as th
from .document import Document, ExportError
from .editor import SNAPS, Editor
from .filedialogs import MIDI_TYPES, OPEN_TYPES, PROJECT_EXT, PROJECT_TYPES, open_file, save_file
from .hands_panel import HandsPanel
from .jobs import Jobs
from .playback import HostOut, Player
from .render import fmt_time, note_name
from .theme import ACCENT, BG, TEXT, TEXT_DIM, fit_text, mix, px
from .widgets import (Button, ContextMenu, Dialog, MenuItem, PromptDialog, draw_tooltip, icon_pause, icon_pencil,
                      icon_play, icon_redo, icon_select_left, icon_select_right, icon_undo)

MESSAGE_T = 6.0
TOOLTIP_DELAY = 0.55
TO_MENU = "menu"                 # (main.TO_MENU: back to the main menu)
PIANIST_CHECK_T = 1.0            # s between looks at whether the active pianist changed (profiles.follow_pianist)


def shared_jobs(host):
    """The worker processes (jobs.py), one set for the app: started on the first visit, kept for the next."""
    jobs = getattr(host, "editor_jobs", None)
    if jobs is None:
        jobs = host.editor_jobs = Jobs()
    return jobs


def song_document(song):
    """A MIDI document of a loaded song (midi_loader.MidiSong): its own file when it has one, else its notes."""
    path = getattr(song, "path", None)
    if path and os.path.isfile(path) and os.path.splitext(path)[1].lower() in (".mid", ".midi", ".kar", ".smf"):
        try:
            return Document.from_file(path)
        except Exception:
            pass
    return Document.from_song(song)


class EditorMode:
    def __init__(self, host, source=None):
        """`source`: a MIDI file or project to open, a loaded song (midi_loader.MidiSong), or None (empty)."""
        self.host = host
        self.screen = host.screen
        th.set_scale(1.0)
        self.fonts = th.load_fonts()
        self.jobs = shared_jobs(host)
        self.profiles = ProfileStore()
        self.window = None               # the Fingering window, while it's open
        self.out = HostOut(host)
        self.player = Player(self.out)
        self.message, self.message_t = "", -1e9
        self.dialog = None
        self._dialog_then = None
        self.busy = None
        self.menu = None
        self.help = False
        self._hover_btn = (None, 0.0)
        self._exit = None                # TO_MENU once leaving is settled (to_menu)
        self._pianist_check = 0.0
        self.last_export = None
        self.last_dir = host.last_dir
        from common import VolumeSlider
        self.volume = VolumeSlider(host.midi.volume, self._set_volume)
        self.editor = Editor(self)
        self.panel = HandsPanel(self)
        self._build_buttons()
        self.layout()
        self.editor.set_document(Document.new_empty())
        pygame.key.set_repeat(380, 32)
        self._update_title()
        if isinstance(source, str):
            self.open_path(source)
        elif source is not None:
            self.open_song(source)
        self.jobs.warm_up()

    def _set_volume(self, v):
        import pianist
        self.host.midi.set_volume(v)
        pianist.set_app_setting("volume", round(v, 3))

    def wants_fingering(self):
        """The planner's suggestions are wanted even with the fingering hidden: the hands play them."""
        return self.panel.wants_fingering()

    # ----- layout -----------------------------------------------------------------
    def _build_buttons(self):
        B = Button
        self.buttons = [
            B("Open MIDI…", "open", "Open a MIDI file or a project (Ctrl+O) - or add one to this project: on top, "
                                    "on a new channel, or appended after it"),
            B("Save Project…", "save", "Save everything - edits, view, an imposed source - as a project (Ctrl+S)"),
            B("Export MIDI", "export", "Write the edited MIDI file; every note's fingering goes with it (Ctrl+E)"),
            None,
            B("", "undo", "Undo (Ctrl+Z)", icon=icon_undo),
            B("", "redo", "Redo (Ctrl+Y)", icon=icon_redo),
            None,
            B("Select Left", "sel_left", "Select every note left of the cursor ([; Shift adds)", icon=icon_select_left),
            B("Select Right", "sel_right", "Select every note right of the cursor (]; Shift adds)", icon=icon_select_right),
            None,
            B("Draw", "draw", "Draw notes: click to add, drag for length (P)", toggle=True, icon=icon_pencil),
            B("Snap 1/16", "snap", "The grid notes snap to (Alt while dragging: no snap)"),
            B("Fingering…", "fingering", "Fingering profiles: how scales, octaves, thirds... are fingered "
                                       "(F shows the fingering on the notes)", toggle=True),
            B("Impose…", "impose", "Impose a perfect source: compare it with this file, then remove the extra "
                                   "notes and add the missing ones"),
            None,
            B("Menu", "menu", "Back to the main menu (Esc with nothing selected)"),
        ]
        self.play_btn = B("", "play", "Play / pause from the cursor (Space)", icon=icon_play)

    def layout(self, size=None):
        self.screen = self.host.screen
        w, h = self.screen.get_size()
        self.fonts.clear()                       # (the font may have changed: Settings > Font)
        self.fonts.update(th.load_fonts())
        self.__dict__.pop("_text_cache", None)
        bottom = self.panel.layout((w, h))
        self.top_h, self.info_h = px(44), px(26)
        self.top = pygame.Rect(0, 0, w, self.top_h)
        self.info = pygame.Rect(0, self.top_h, w, self.info_h)
        f = self.fonts
        bh = self.top_h - px(12)
        # right: play and the time (the bar.beat readout goes first when there's no room)
        self.time_w = f["normal"].size("888.8.888   88:88.88")[0]
        self.show_bar_time = w >= px(1250)
        if not self.show_bar_time:
            self.time_w = f["normal"].size("88:88.88")[0]
        pw = self.play_btn.width(f) + px(6)
        self.play_btn.rect = pygame.Rect(w - px(10) - self.time_w - px(10) - pw, px(6), pw, bh)
        vw = self.volume.width() if w >= px(1000) else 0     # the volume (Hand-thesia's synth), left of play -
        self.volume.rect = pygame.Rect(self.play_btn.rect.x - px(12) - vw, 0, vw, self.top_h)   # if there's room
        right_limit = self.volume.rect.x - px(12) if vw else self.play_btn.rect.x - px(12)
        # labels as they are; if they don't fit, icon-only buttons first, then shorter labels
        for b in self.buttons:
            if b is not None:
                b.label = getattr(b, "full_label", b.label)
                b.full_label = b.label
        short = {"open": "Open…", "save": "Save…", "export": "Export", "fingering": "Fingers", "impose": "Impose…",
                 "menu": "Menu"}
        for stage in range(3):
            if stage >= 1:
                for b in self.buttons:
                    if b is not None and b.icon is not None:
                        b.label = ""
            if stage >= 2:
                for b in self.buttons:
                    if b is not None and b.action in short:
                        b.label = short[b.action]
            widths = [b.width(f) if b else px(10) for b in self.buttons]
            if px(8) + sum(widths) + px(4) * len(self.buttons) <= right_limit:
                break
        total = px(8) + sum(widths) + px(4) * len(self.buttons)
        shrink = min(1.0, right_limit / max(1, total))
        x = px(8)
        for b, bw in zip(self.buttons, widths):
            if b is None:
                x += int(px(10) * shrink)
                continue
            ww = max(px(28), int(bw * shrink))
            b.rect = pygame.Rect(x, px(6), ww, bh)
            x += ww + px(4)
        self.chip_rects = []
        self.editor.layout(pygame.Rect(0, self.top_h + self.info_h, w, max(px(120), bottom - self.top_h - self.info_h)))

    @property
    def overlay_top(self):
        """Where the performance overlay may start (main.App.draw_perf): below the bars."""
        return self.top_h + self.info_h

    def _update_title(self):
        doc = self.editor.doc
        from version import VERSION
        t = f"Hand-thesia {VERSION} - editing {doc.title}{' •' if doc.dirty else ''}"
        if getattr(self, "_title", None) != t:
            self._title = t
            pygame.display.set_caption(t)

    def say(self, text):
        self.message, self.message_t = text, time.perf_counter()

    def text(self, font, text, color, width=None):
        """A rendered line of text (shortened to `width`), kept for the next frames."""
        cache = self.__dict__.setdefault("_text_cache", {})
        key = (font, text, color, width, th.S)
        img = cache.get(key)
        if img is None:
            if len(cache) > 600:
                cache.clear()
            f = self.fonts[font]
            img = cache[key] = f.render(fit_text(f, text, width) if width else text, True, color)
        return img

    # ----- per frame (main.App.run) -------------------------------------------------------
    def hands_time(self):
        """The song time (s) the hands show: the playhead while it plays, else the cursor."""
        doc = self.editor.doc
        return self.player.sec if self.player.playing else doc.tempo_map().sec(self.editor.cursor)

    def scrubbing(self):
        """Is the cursor being dragged along the ruler? (The hands are drawn quick then: hands.live_poses.)"""
        p = self.editor.press
        return bool(p and p["kind"] == "ruler")

    def update(self, dt):
        self._pianist_check -= dt
        if self._pianist_check <= 0:
            self._pianist_check = PIANIST_CHECK_T
            self.profiles.follow_pianist()
        for kind, token, result, error in self.jobs.poll():
            self.on_job(kind, token, result, error)
        ed = self.editor
        ed.update(dt)
        playing = self.player.playing
        # after a jump the song waits for the hands' exact pose (hands.hands_waiting)
        if self.panel.hold(self.hands_time(), self.scrubbing(), playing):
            dt = 0.0
        if not self.player.update(dt, ed.doc) and not self.player.playing and playing:
            ed.cursor = self.player.tick_position(ed.doc)
        if self.window is not None:
            self.window.update(dt)
        self.panel.update(dt)

    def idle(self, budget):
        """The frame's spare time (main.App.run): the hands work ahead."""
        if self.panel.shown:
            self.panel.idle(self.hands_time(), budget, self.scrubbing())

    def leave(self):
        self.player.stop()
        self.out.silence()                      # (with the pedal down, notes would ring on)
        for kind in ("fingering", "replan", "impose"):
            self.jobs.cancel(kind)

    def seek(self, t):
        """The cursor to song time t (s), brought into view (main.py's --screenshot --at)."""
        ed = self.editor
        ed.cursor = max(0, int(round(ed.doc.tempo_map().tick(max(0.0, t)))))
        span = ed.vx.target_span
        ed.vx.jump(max(0, ed.cursor - span * 0.3), max(0, ed.cursor - span * 0.3) + span)

    def settle(self, timeout=120.0):
        """Wait for the fingering and the hands to be worked out (a screenshot, the tests)."""
        t0 = time.perf_counter()
        ed = self.editor
        while time.perf_counter() - t0 < timeout:
            ed._fing_wait = self.panel._wait = 1.0          # (no waiting for more edits here)
            self.update(0.0)
            if not ed.doc.notes or not self.panel.shown:
                break
            if ed.suggestions_ready() and not self.panel.building and self.panel.built_for == self.panel._key():
                break
            if self.panel.building:
                self.panel.wait_built(timeout)
            else:
                time.sleep(0.01)
        self.update(0.0)

    def to_menu(self):
        """Back to the main menu - after asking about unsaved changes."""
        def go():
            self._exit = TO_MENU
            return True
        return self.guard(go)

    # ----- input ----------------------------------------------------------------------
    def handle_event(self, e):
        """True to carry on, False to quit, TO_MENU to go back to the main menu (main.App.handle_event)."""
        r = self._handle(e)
        if self._exit is not None and r is not False:
            r, self._exit = self._exit, None
        return r

    def _handle(self, e):
        if e.type == pygame.QUIT:
            if self.busy is not None:
                return False
            return self.guard(lambda: False)
        if e.type == pygame.VIDEORESIZE:
            self.layout()
            if self.window is not None:
                self.window.layout()
            return True
        if self.window is not None:
            if not self.window.handle_event(e):
                self.window = None
            return True
        if self.busy is not None:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                self.jobs.cancel(self.busy["kind"])
                self.busy = None
                self.say("Cancelled")
            return True
        if self.help:
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.help = False
            return True
        if self.dialog is not None:
            self.dialog.handle_event(e)
            if self.dialog.choice is not None:
                d, then = self.dialog, self._dialog_then
                self.dialog, self._dialog_then = None, None
                if isinstance(d, PromptDialog):
                    pygame.key.stop_text_input()
                if then is not None:
                    return then(d.choice, d) is not False
            return True
        if e.type == pygame.DROPFILE:
            path = e.file
            return self.guard(lambda: self.open_path(path))
        if self.menu is not None:
            m = self.menu
            if e.type == pygame.MOUSEMOTION:
                m.motion(e.pos)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
                if m.click(e.pos):
                    self.menu = None
            elif e.type == pygame.KEYDOWN and (e.key == pygame.K_ESCAPE or m.key(e.key)):
                self.menu = None
            return True
        ed = self.editor
        if ed.menu is None and ed.press is None and self.panel.handle_event(e):
            return True
        if e.type == pygame.MOUSEWHEEL and ed.menu is None and pygame.mouse.get_pos()[1] >= self.panel.top():
            return True
        if ed.tempo_edit is not None:                 # the BPM is being typed: its keys; a click elsewhere sets it
            if e.type in (pygame.KEYDOWN, pygame.TEXTINPUT):
                ed.tempo_edit_event(e)
                return True
            if e.type == pygame.MOUSEBUTTONDOWN:
                field = next((r for r, (k, _) in ed._chips if k == "tempo_bpm"), None)
                if field is None or not field.collidepoint(e.pos):
                    ed.commit_tempo_edit()
                else:
                    return True
        if self.volume.handle_event(e):
            return True
        if e.type == pygame.MOUSEMOTION:
            hb = next((b for b in self.buttons + [self.play_btn] if b and b.rect.collidepoint(e.pos)), None)
            if hb is None and self.panel.arrow.collidepoint(e.pos):
                hb = self.panel
            if hb is not self._hover_btn[0]:
                self._hover_btn = (hb, time.perf_counter())
        if e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3) and e.pos[1] < self.top_h + self.info_h \
                and self.editor.menu is None:
            if e.button == 1:
                for b in self.buttons + [self.play_btn]:
                    if b and b.hit(e.pos):
                        self.button(b.action, b)
                        return True
            for r, ch in self.chip_rects:
                if r.collidepoint(e.pos):
                    self._chip_click(ch, e.button, pygame.key.get_mods())
                    return True
            return True
        if e.type == pygame.KEYDOWN and self.editor.menu is None and self.editor.seq is None:
            if self._key(e):
                return True
        self.editor.handle_event(e)
        return True

    def _key(self, e):
        """App-wide shortcuts; True if the key was one. (Never a request to quit.)"""
        ctrl = bool(e.mod & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(e.mod & pygame.KMOD_SHIFT)
        k = e.key
        if ctrl and k == pygame.K_o:
            self.guard(self.open_dialog)
        elif ctrl and k == pygame.K_s:
            self.save_project(ask=shift or not self.editor.doc.project_path)
        elif ctrl and k == pygame.K_e:
            self.export_midi()
        elif ctrl and k == pygame.K_i:
            self.button("impose")
        elif k == pygame.K_SPACE and not ctrl:
            self.toggle_play()
        elif k == pygame.K_F1:
            self.help = True
        elif k == pygame.K_h and not ctrl:
            self.panel.toggle()
        else:
            return False
        return True

    def button(self, action, btn=None):
        ed = self.editor
        if action == "open":
            self.open_menu(btn)
        elif action == "save":
            self.save_project(ask=True)
        elif action == "export":
            self.export_midi()
        elif action == "undo":
            ed.undo()
        elif action == "redo":
            ed.undo(redo=True)
        elif action == "sel_left":
            ed.select_side(False, add=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
        elif action == "sel_right":
            ed.select_side(True, add=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
        elif action == "draw":
            ed.toggle_draw()
        elif action == "snap":
            items = [MenuItem(label, i, checked=i == ed.snap_i) for i, (label, _) in enumerate(SNAPS)]

            def pick(v, _):
                ed.snap_i = v
                self.say(f"Snap: {SNAPS[v][0]}")
            self.menu = ContextMenu((btn.rect.x, btn.rect.bottom + px(2)), "Snap to", items, pick, self.fonts,
                                    pygame.Rect(0, 0, *self.screen.get_size()))
        elif action == "fingering":
            ed.cancel_tempo_edit()
            self.window = ProfileWindow(self)
        elif action == "impose":
            if ed.impose is not None:
                self.say("A perfect source is already imposed - close the comparison first (Close, in its bar)")
            elif not ed.doc.notes:
                self.say("Open the MIDI file to clean up first, then impose its perfect source on it")
            else:
                self.impose_dialog()
        elif action == "play":
            self.toggle_play()
        elif action == "menu":
            self.to_menu()
        return True

    def _chip_click(self, ch, button, mod):
        ed = self.editor
        if button == 3 or mod & (pygame.KMOD_CTRL | pygame.KMOD_META):
            if ch in ed.hidden_channels:
                ed.hidden_channels.discard(ch)
                self.say(f"Channel {ch + 1} shown")
            else:
                ed.hidden_channels.add(ch)
                ed.selection = {i for i in ed.selection if ed.doc.notes[i].channel != ch}
                self.say(f"Channel {ch + 1} hidden (right-click its chip to show it again)")
        else:
            ed.channel = ch
            ed.hidden_channels.discard(ch)
            self.say(f"New notes go on channel {ch + 1}")

    def toggle_play(self):
        ed = self.editor
        if self.player.playing:
            self.player.stop()
            ed.cursor = int(round(self.player.tick_position(ed.doc)))
        else:
            if not self.player.start(ed.doc, ed.cursor):
                self.say(f"Can't play: {self.out.error or 'no MIDI output'}")
                return
            self.panel._pose_state.pop("t", None)          # (a jump: the hands' exact pose first)
            ed.follow = True

    # ----- dialogs ----------------------------------------------------------------------
    def ask(self, title, message, options, then, default=None):
        self.dialog = Dialog(title, message, options, default=default)
        self._dialog_then = lambda choice, d: then(choice)

    def prompt(self, title, message, text, validate, done, options=None, width=460):
        """Ask for a line of text: done(text) on OK - or, with options [(label, value)], done(text, value) for the
        button chosen (other than "cancel")."""
        self.dialog = PromptDialog(title, message, text, validate, options, width)
        pygame.key.start_text_input()
        if options is None:
            self._dialog_then = lambda choice, d: done(d.text) if choice == "ok" else None
        else:
            self._dialog_then = lambda choice, d: done(d.text, choice) if choice != "cancel" else None

    def guard(self, then):
        """Run `then` now, or after asking what to do with unsaved changes."""
        doc = self.editor.doc
        if not doc.dirty or not (doc.notes or doc.undo_stack):
            return then()

        def chosen(c):
            if c == "save":
                if self.save_project(ask=not doc.project_path) and not doc.dirty:
                    return then()
            elif c == "export":
                if self.export_midi() and not doc.dirty:
                    return then()
            elif c == "discard":
                return then()
            return True
        self.ask("Unsaved changes", f"“{doc.title}” has changes that aren't saved. Save them first?",
                 [("Save Project…", "save"), ("Export MIDI", "export"), ("Discard", "discard"), ("Cancel", "cancel")],
                 chosen, default="save")
        return True

    # ----- files ------------------------------------------------------------------------
    def _start_dir(self):
        doc = self.editor.doc
        p = doc.project_path or doc.path
        return os.path.dirname(os.path.abspath(p)) if p else self.last_dir

    def open_dialog(self):
        path = open_file("Open a MIDI file or project", self._start_dir(), OPEN_TYPES)
        if path:
            self.open_path(path)
        return True

    def open_path(self, path):
        self.player.stop()
        try:
            if project.is_project(path):
                doc, settings = project.load(path)
            else:
                doc, settings = Document.from_file(path), None
        except Exception as exc:
            self.ask("Couldn't open the file", f"{os.path.basename(path)}: {exc}", [("OK", "ok")], lambda c: None)
            return True
        self.last_dir = self.host.last_dir = os.path.dirname(os.path.abspath(path))
        import recent
        recent.add_edit(os.path.abspath(path))
        ed = self.editor
        view = settings.get("view") if settings else None
        ed.set_document(doc, view=view)
        if settings:
            self._restore(settings)
        doc.mark_saved()
        fing = sum(1 for n in doc.notes.values() if n.has_fingering)
        chans = sorted(doc.channel_counts())
        msg = (f"Opened {os.path.basename(path)}: {len(doc.notes):,} notes"
               + (f" on channel{'s' if len(chans) > 1 else ''} {', '.join(str(c + 1) for c in chans)}" if chans else "")
               + (f", {fing:,} with fingering (F shows it)" if fing else ""))
        if doc.warnings:
            msg += " - " + " ".join(doc.warnings)
        self.say(msg)
        self._update_title()
        return True

    def open_song(self, song):
        """Edit a song already loaded (midi_loader.MidiSong): its MIDI file, or a new one of its notes."""
        self.player.stop()
        doc = song_document(song)
        self.editor.set_document(doc)
        doc.mark_saved()
        if song.path:
            self.last_dir = os.path.dirname(os.path.abspath(song.path))
        self.say(f"Opened {doc.title}: {len(doc.notes):,} notes")
        self._update_title()
        return True

    def _restore(self, s):
        ed = self.editor
        order = s["_note_order"]
        ed.cursor = int(s.get("cursor", 0))
        ed.snap_i = int(s.get("snap", ed.snap_i)) if 0 <= int(s.get("snap", 0)) < len(SNAPS) else ed.snap_i
        ed.channel = int(s.get("channel", ed.channel)) & 15
        ed.hidden_channels = set(s.get("hidden_channels", []))
        ed.time_ruler = bool(s.get("time_ruler", False))
        ed.pedal_cc = int(s.get("pedal_cc", 64))
        if s.get("lanes_h"):
            ed.lanes_h = int(s["lanes_h"])
            ed.layout(ed.area)
        ed.selection = {order[i] for i in s.get("selection", []) if 0 <= i < len(order)}
        if s.get("show_fingering") and not ed.show_fingering:
            ed.toggle_fingering()
        imp_s = s.get("impose")
        if imp_s:
            import base64
            data = base64.b64decode(imp_s["source_midi"])
            keep = {order[i] for i in imp_s.get("keep", []) if 0 <= i < len(order)}
            self._start_impose(Document.from_bytes(data, imp_s.get("source_path")), data, imp_s.get("name"),
                               restore={"keep": keep, "skip": set(imp_s.get("skip", [])),
                                        "do_remove": imp_s.get("do_remove", True), "do_add": imp_s.get("do_add", True),
                                        "copy_fingering": imp_s.get("copy_fingering", False),
                                        "show_links": imp_s.get("show_links", True)})

    # ----- another file into this one (combine.py) -------------------------------------------
    def open_menu(self, btn=None):
        """Open MIDI's dropdown: a new project, or a file added to this one - on top, or appended."""
        has = bool(self.editor.doc.notes)
        items = [MenuItem("New project…", "new", hint="Ctrl+O", key=pygame.K_n),
                 MenuItem("On top, on a new channel…", "top", enabled=has, key=pygame.K_t),
                 MenuItem("Append, on the same channel…", "append", enabled=has, key=pygame.K_a)]

        def pick(v, _):
            if v == "new":
                self.guard(self.open_dialog)
            else:
                self.add_dialog(v)
        at = (btn.rect.x, btn.rect.bottom + px(2)) if btn is not None else pygame.mouse.get_pos()
        self.menu = ContextMenu(at, "Open MIDI", items, pick, self.fonts, pygame.Rect(0, 0, *self.screen.get_size()))

    def add_dialog(self, mode):
        """Choose a MIDI file (or project) to add to this one: on top ("top") or appended ("append")."""
        if self.editor.impose is not None:
            self.say("Close the comparison first (Close, in its bar), then add the file")
            return True
        path = open_file("Open a MIDI file on top of this one" if mode == "top" else "Open a MIDI file to append",
                         self._start_dir(), OPEN_TYPES)
        if not path:
            return True
        name = os.path.basename(path)
        try:
            src = project.load(path)[0] if project.is_project(path) else Document.from_file(path)
        except Exception as exc:
            self.ask("Couldn't open the file", f"{name}: {exc}", [("OK", "ok")], lambda c: None)
            return True
        self.last_dir = os.path.dirname(os.path.abspath(path))
        if not src.notes:
            self.say(f"{name} has no notes - nothing to add")
        elif mode == "top":
            self.add_on_top(src, name)
        else:
            self.append_prompt(src, name)
        return True

    def add_on_top(self, src, name):
        """`src` on top of the project from the start: new tracks, new channels, every note where it plays."""
        ed = self.editor
        doc = ed.doc
        self.player.stop()
        doc.begin(f"Open {name} on top")
        try:
            got = combine.overlay(doc, src, os.path.splitext(name)[0])
        except ValueError as exc:
            doc.cancel()
            self.ask("Couldn't add the file", f"{name}: {exc}.", [("OK", "ok")], lambda c: None)
            return
        doc.commit()
        chans = sorted(set(got["channels"].values()))
        ed.hidden_channels -= set(chans)
        ed.selection = set(got["ids"])
        fing = sum(1 for i in got["ids"] if doc.notes[i].has_fingering)
        k = len(got["tracks"])
        self.say(f"Opened {name} on top: {len(got['ids']):,} notes on channel{'s' if len(chans) > 1 else ''} "
                 f"{', '.join(str(c + 1) for c in chans)} ({k} new track{'s' if k != 1 else ''}), each where it plays "
                 "in its file" + (f", {fing:,} with fingering" if fing else "") + " - selected")
        self._update_title()

    def append_prompt(self, src, name):
        """Ask how many beats after the project's music `src` starts, then append it."""
        doc = self.editor.doc
        if doc.division & 0x8000 or src.division & 0x8000:
            self.say("A file that counts time in SMPTE frames has no beats to append by")
            return
        end = combine.music_end(doc)
        mm = doc.meter_map()
        bar, beat, into = mm.bar_beat(end)
        gap = round(combine.gap_to_next_bar(doc), 3)
        same = "the same channel" if len(doc.channel_counts()) == 1 else "the same channels"
        msg = (f"{name} goes on {same}, after this project's music, which ends (its last note or pedal release) at "
               f"bar {bar}, beat {beat + into / mm.beat_ticks(end):.3g}. Its first note comes this many beats later"
               + (f" - {gap:g} puts it on the next bar." if gap else " - that's a bar line, so 0 starts it right there.")
               + "\nGap, in beats:")

        def validate(text):
            try:
                v = float(text.replace(",", "."))
            except ValueError:
                return "Type a number of beats, like 2 or 0.5"
            return None if end + v * mm.beat_ticks(end) >= 0 else "That's before the piece begins"

        def done(text, _):
            self.append_file(src, name, float(text.replace(",", ".")))
        self.prompt("Append a MIDI file", msg, f"{gap:g}", validate, done,
                    options=[("Append", "append"), ("Cancel", "cancel")], width=560)

    def append_file(self, src, name, gap):
        """`src` appended `gap` beats after the project's music, on the same channels - one undo step."""
        ed = self.editor
        doc = ed.doc
        self.player.stop()
        doc.begin(f"Append {name}")
        try:
            got = combine.append(doc, src, gap)
        except ValueError as exc:
            doc.cancel()
            self.ask("Couldn't append the file", f"{name}: {exc}.", [("OK", "ok")], lambda c: None)
            return
        doc.commit()
        chans = sorted(set(got["channels"].values()))
        ed.hidden_channels -= set(chans)
        ed.selection = set(got["ids"])
        ed.cursor = got["start"]
        ed._update_bounds()
        ed.reveal(got["start"], center=True)
        bar, beat, _ = doc.meter_map().bar_beat(got["start"])
        fing = sum(1 for i in got["ids"] if doc.notes[i].has_fingering)
        self.say(f"Appended {name}: {len(got['ids']):,} notes on channel{'s' if len(chans) > 1 else ''} "
                 f"{', '.join(str(c + 1) for c in chans)} from bar {bar}.{beat} ({gap:g} beat{'s' if gap != 1 else ''} "
                 "after the end)" + (f", at its own tempo ({got['tempo']:.4g} BPM)" if got["tempo"] else "")
                 + (f", {fing:,} with fingering" if fing else "") + " - selected")
        self._update_title()

    def save_project(self, ask=True):
        ed = self.editor
        doc = ed.doc
        path = doc.project_path
        if ask or not path:
            path = save_file("Save project", os.path.dirname(path) if path else self._start_dir(),
                             (doc.title + PROJECT_EXT), PROJECT_TYPES, PROJECT_EXT)
            if not path:
                return False
            if not path.lower().endswith(PROJECT_EXT):
                path += PROJECT_EXT
        try:
            project.save(path, ed)
        except Exception as exc:
            self.ask("Couldn't save the project", str(exc), [("OK", "ok")], lambda c: None)
            return False
        doc.project_path = path
        doc.mark_saved()
        self.say(f"Project saved: {path}")
        self._update_title()
        return True

    def export_midi(self):
        doc = self.editor.doc
        if self.editor.press is not None:
            return False
        if self.last_export and os.path.dirname(self.last_export):
            initdir, initfile = os.path.dirname(self.last_export), os.path.basename(self.last_export)
        else:
            initdir = self._start_dir()
            base = os.path.splitext(os.path.basename(doc.path))[0] if doc.path else doc.title
            initfile = f"{base} (edited).mid"
        path = save_file("Export MIDI", initdir, initfile, MIDI_TYPES, ".mid")
        if not path:
            return False
        try:
            report = doc.export(path)
        except ExportError as exc:
            self.ask("Export cancelled", str(exc), [("OK", "ok")], lambda c: None)
            return False
        except OSError as exc:
            self.ask("Couldn't write the file", str(exc), [("OK", "ok")], lambda c: None)
            return False
        self.last_export = path
        doc.mark_saved()
        msg = f"Exported {os.path.basename(path)}: {len(doc.notes):,} notes"
        if report.marked:
            msg += f", {report.marked:,} with their fingering"
        if report.dropped_markers:
            msg += (f" ({report.dropped_markers} stray fingering text{'s' if report.dropped_markers > 1 else ''} "
                    "with no note of its own left out, so it can't land on another note)")
        self.say(msg + " - checked by reading it back")
        self._update_title()
        return True

    # ----- impose ---------------------------------------------------------------------
    def impose_dialog(self):
        path = open_file("Choose the perfect source to impose", self._start_dir(), MIDI_TYPES)
        if not path:
            return True
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            src = Document.from_bytes(data, path)
        except Exception as exc:
            self.ask("Couldn't open the perfect source", f"{os.path.basename(path)}: {exc}", [("OK", "ok")],
                     lambda c: None)
            return True
        self._start_impose(src, data, src.title)
        return True

    def _start_impose(self, src, data, name, restore=None):
        doc = self.editor.doc
        perfect, perfect_ids = imp.midi_data(src)
        human, human_ids = imp.midi_data(doc)
        if perfect.note_count < imp.MIN_NOTES or human.note_count < imp.MIN_NOTES:
            self.ask("Not enough notes", "Both files need some notes to be compared.", [("OK", "ok")], lambda c: None)
            return
        token = {"rev": doc.notes_rev, "src": src, "data": data, "name": name, "perfect": perfect,
                 "perfect_ids": perfect_ids, "origin": human.start, "restore": restore}
        self.jobs.request("impose", imp.analyze_job, lambda: (perfect, human, human_ids), token=token)
        self.busy = {"kind": "impose", "text": f"Comparing with {name}…", "t0": time.perf_counter()}

    def on_job(self, kind, token, result, error):
        ed = self.editor
        if kind == "fingering":
            ed.on_fingering(token, result, error)
        elif kind == "replan":
            ed.on_replan(token, result, error)
        elif kind == "impose":
            self.busy = None
            if error is not None:
                self.ask("Couldn't compare the files", str(error), [("OK", "ok")], lambda c: None)
                return
            result_, alignment, plan, human_ids = result
            session = imp.ImposeSession(token["src"], token["data"], result_, alignment, plan, token["perfect"],
                                        token["perfect_ids"], human_ids, token["origin"], plan_rev=token["rev"],
                                        name=token["name"])
            if not session.allowed:
                self.ask("Not similar enough",
                         f"{token['name']} and {ed.doc.title} don't look like the same piece "
                         f"({result_.confidence:.0%} match - {result_.verdict.lower()}). Imposing one on the "
                         "other would damage it, so nothing was changed.", [("OK", "ok")], lambda c: None)
                return
            r = token.get("restore")
            if r:
                session.keep, session.skip = set(r["keep"]), set(r["skip"])
                session.do_remove, session.do_add = r["do_remove"], r["do_add"]
                session.copy_fingering = r["copy_fingering"]
                ed.show_links = r["show_links"]
            ed.open_impose(session)
            if session.uncertain:
                self.say(f"Uncertain match ({result_.confidence:.0%}): {result_.verdict} - review every planned "
                         "edit before applying")

    # ----- drawing ----------------------------------------------------------------------
    def render(self):
        s = self.screen
        ed = self.editor          # (every part of the window is painted below: no clearing first)
        ed.tip = None
        ed.render(s)
        self.panel.draw(s, self.hands_time(), self.scrubbing(), self.host.fonts)
        self._draw_top(s)
        self._draw_info(s)
        ed.render_overlays(s)
        if self.window is not None:
            self.window.draw(s)
        if self.menu is not None:
            self.menu.draw(s, pygame.mouse.get_pos())
        if self.dialog is None and self.menu is None and ed.menu is None and ed.press is None and self.window is None:
            b, t0 = self._hover_btn
            if b is self.panel and time.perf_counter() - t0 >= TOOLTIP_DELAY and \
                    b.arrow.collidepoint(pygame.mouse.get_pos()):
                draw_tooltip(s, self.fonts, b.tooltip(), b.arrow, below=False)
            elif b is not None and b is not self.panel and b.tip and time.perf_counter() - t0 >= TOOLTIP_DELAY and \
                    b.rect.collidepoint(pygame.mouse.get_pos()):
                draw_tooltip(s, self.fonts, b.tip, b.rect)
            elif ed.tip is not None:
                draw_tooltip(s, self.fonts, ed.tip[0], ed.tip[1], below=False)
            elif self.volume.rect.collidepoint(pygame.mouse.get_pos()) and not self.volume.dragging:
                draw_tooltip(s, self.fonts, "Volume - click, drag or scroll", self.volume.rect)
        if self.dialog is not None:
            self.dialog.draw(s, self.fonts)
        if self.help:
            from . import helpview
            helpview.draw(s, self.fonts)
        if self.busy is not None:
            self._draw_busy(s)
        self._update_title()

    def _draw_top(self, s):
        ed = self.editor
        s.fill(th.BAR_BG, self.top)
        mouse = pygame.mouse.get_pos()
        pressed = pygame.mouse.get_pressed()[0]
        for b in self.buttons:
            if b is None:
                continue
            if b.action == "draw":
                b.active = ed.draw_mode
            elif b.action == "fingering":
                b.active = ed.show_fingering
            elif b.action == "snap":
                full = f"Snap {SNAPS[ed.snap_i][0]}"
                b.label = full if self.fonts[b.font].size(full)[0] + px(18) <= b.rect.w else SNAPS[ed.snap_i][0]
            elif b.action == "undo":
                b.enabled = bool(ed.doc.undo_stack)
            elif b.action == "redo":
                b.enabled = bool(ed.doc.redo_stack)
            elif b.action == "impose":
                b.active = ed.impose is not None
            b.draw(s, self.fonts, mouse, pressed)
        if self.volume.rect.w:
            self.volume.value = self.host.midi.volume
            self.volume.draw(s, self.host.fonts, muted=self.host.midi.muted)
        self.play_btn.icon = icon_pause if self.player.playing else icon_play
        self.play_btn.active = self.player.playing
        self.play_btn.draw(s, self.fonts, mouse, pressed)
        doc = ed.doc
        bar, beat, into = doc.meter_map().bar_beat(ed.cursor)
        secs = doc.tempo_map().sec(ed.cursor)
        if self.show_bar_time:
            img = self.text("normal", f"{bar}.{beat}.{int(into):03d}", TEXT)
            x = self.play_btn.rect.right + px(10)
            s.blit(img, img.get_rect(midleft=(x, self.top.centery)))
        img2 = self.text("normal", fmt_time(secs), TEXT_DIM if self.show_bar_time else TEXT)
        s.blit(img2, img2.get_rect(midright=(self.top.right - px(10), self.top.centery)))
        s.fill((20, 20, 26), (0, self.top.bottom - 1, self.top.w, 1))

    def _info_text(self):
        ed = self.editor
        now = time.perf_counter()
        if ed.seq is not None:
            return ed.seq_status(), ACCENT
        if self.message and now - self.message_t < MESSAGE_T:
            return self.message, ACCENT
        if ed.hover is not None and ed.hover in ed.doc.notes:
            n = ed.doc.notes[ed.hover]
            doc = ed.doc
            tm = doc.tempo_map()
            bar, beat, _ = doc.meter_map().bar_beat(n.start)
            dur = tm.sec(n.end) - tm.sec(n.start)
            fing = ""
            if n.has_fingering:
                fing = f"   ·   fingering {(n.hand or '') + (str(n.finger) if n.finger else ('g' if n.gliss else ''))}"
            elif ed.show_fingering and n.id in ed.sugg:
                h, f, g = ed.sugg[n.id]
                fing = f"   ·   suggested {h}{f if f else ('g' if g else '?')}"
            st = ""
            if ed.impose is not None:
                code = ed.impose.status_of.get(n.id)
                st = {imp.MATCHED: "   ·   matched", imp.DISPLACED: "   ·   off its place (left alone)",
                      imp.REMOVE: "   ·   not in the source: " + ("kept" if n.id in ed.impose.keep else "to remove"),
                      imp.UNTRUSTED: "   ·   not edited (unsure alignment)",
                      imp.OUTSIDE: "   ·   outside the aligned stretches"}.get(code, "")
            return (f"{note_name(n.pitch)}   ·   channel {n.channel + 1}   ·   {doc.track_name(n.track)}   ·   "
                    f"bar {bar}.{beat} ({fmt_time(tm.sec(n.start))})   ·   {dur:.3f} s   ·   velocity {n.velocity}"
                    + fing + st), TEXT
        if ed.selection:
            k = len(ed.selection)
            return (f"{k} note{'s' if k != 1 else ''} selected - drag to move, Delete removes, right-click for hands, "
                    "fingers, channel"), TEXT
        if ed.draw_mode:
            return "Draw: click to add a note, drag to set its length · right-click a note for more · P or Esc to stop", TEXT_DIM
        return ("Click or drag to select · double-click adds a note · wheel scrolls, Ctrl+wheel zooms time, "
                "Alt+wheel zooms rows, Shift+wheel moves up/down · middle-drag pans · Space plays · F1 all shortcuts"), TEXT_DIM

    def _draw_info(self, s):
        r = self.info
        s.fill(th.INFO_BG, r)
        s.fill(th.INFO_LINE, (0, r.bottom - 1, r.w, 1))
        ed = self.editor
        # channel chips (right): click sets the channel for new notes, right-click hides / shows
        f = self.fonts["tiny"]
        chans = sorted(set(ed.doc.channel_counts()) | {ed.channel})
        x = r.right - px(8)
        self.chip_rects = []
        status = ed.fingering_status()
        for ch in reversed(chans):
            img = self.text("tiny", str(ch + 1), (20, 20, 24))
            w = max(px(20), img.get_width() + px(10))
            box = pygame.Rect(x - w, r.y + px(5), w, r.h - px(10))
            c = th.CHANNEL_COLORS[ch]
            hidden = ch in ed.hidden_channels
            pygame.draw.rect(s, mix(c, th.INFO_BG, 0.7) if hidden else c, box, border_radius=px(4))
            if ch == ed.channel:
                pygame.draw.rect(s, (255, 255, 255), box.inflate(px(4), px(4)), max(1, px(1.5)), border_radius=px(5))
            s.blit(img, img.get_rect(center=box.center))
            self.chip_rects.append((box, ch))
            x = box.x - px(5)
        lab = self.text("tiny", "CHANNELS", TEXT_DIM)
        s.blit(lab, lab.get_rect(midright=(x - px(4), r.centery)))
        x -= lab.get_width() + px(16)
        if status:
            img = self.text("small", status, ACCENT)
            s.blit(img, img.get_rect(midright=(x, r.centery)))
            x -= img.get_width() + px(16)
        text, color = self._info_text()
        img = self.text("small", text, color, x - px(20))
        s.blit(img, img.get_rect(midleft=(px(10), r.centery)))

    def _draw_busy(self, s):
        w, h = s.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 120))
        s.blit(shade, (0, 0))
        box = pygame.Rect(0, 0, px(440), px(120))
        box.center = (w // 2, h // 2)
        pygame.draw.rect(s, th.PANEL, box, border_radius=px(12))
        pygame.draw.rect(s, th.PANEL_EDGE, box, 1, border_radius=px(12))
        img = self.fonts["button"].render(fit_text(self.fonts["button"], self.busy["text"], box.w - px(40)), True, TEXT)
        s.blit(img, (box.x + px(20), box.y + px(18)))
        t = time.perf_counter() - self.busy["t0"]
        bar = pygame.Rect(box.x + px(20), box.y + px(64), box.w - px(40), px(8))
        pygame.draw.rect(s, (30, 30, 38), bar, border_radius=px(4))
        seg = bar.w // 4
        off = int((t * 0.6 % 1.0) * (bar.w + seg)) - seg
        part = pygame.Rect(bar.x + off, bar.y, seg, bar.h).clip(bar)
        pygame.draw.rect(s, ACCENT, part, border_radius=px(4))
        img = self.fonts["small"].render(f"{t:.0f} s · Esc cancels", True, TEXT_DIM)
        s.blit(img, (box.x + px(20), box.y + px(84)))

