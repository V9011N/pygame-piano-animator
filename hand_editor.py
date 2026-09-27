"""
hand_editor.py - The pianist studio: browse, create and edit pianists (hand
anatomy, hand colour, technique) and choose the active one, whose hands are
used by the player and the fingering editor.

Pages
    Browser    search, filter by hand size, sort; make a pianist active, edit,
               duplicate or delete them; "New pianist" starts by asking a name.
    Overview   the hand, zoomed in, stretched out or in its natural curve;
               colour sliders; buttons into Anatomy and Behaviour; Save (a new
               pianist can be saved once both have been visited).
    Anatomy    click any of the 19 bones (or its cell in the table) to set its
               length; sliders scale all metacarpals / all phalanges together;
               "Show hand span" lays the stretched hand over the keys, thumb on C.
    Behaviour  every motion and fingering preference, each with its own control.
"""
from __future__ import annotations

import math

import pygame

import pianist as pianists
from common import (ACCENT, BAR_BG, BG, PANEL, PANEL_EDGE, TEXT, TEXT_DIM, TOP_BAR_H, Button,
                    Dialog, Slider, TextInput, mix)
from hands import (HOVER, INCHES_PER_UNIT, WHITE_DEPTH_IN, WHITE_KEY_IN, HandGeometry, bone_width,
                   halo_for, joint_radius, static_skeleton, curl_factor)
from midi_loader import is_black_key, note_name
import skins

PANEL_W = 380
ROW_H = 56
KIND_ORDER = ["mc", "pp", "mp", "dp"]
KIND_SHORT = {"mc": "Metacarpal", "pp": "Proximal", "mp": "Middle", "dp": "Distal"}


def _cm(units):
    return units * INCHES_PER_UNIT * 2.54


# --------------------------------------------------------------------------- #
# Drawing a still hand
# --------------------------------------------------------------------------- #
def draw_skeleton(surf, skel, to_screen, px_per_unit, color, selected=None, hover=None):
    """Draw a static_skeleton; returns [(bone_id, a, b, width)] for hit-testing."""
    ppi = px_per_unit / INCHES_PER_UNIT
    halo = halo_for(color)
    if "struct" in skel:
        def project(q):
            x, y = to_screen(q)
            return (x, y, q[2] * px_per_unit)
        skins.draw_webs(surf, [(skel["struct"], project, ppi, color)])
    items = []
    for a, b, kind, bid in skel["bones"]:
        items.append(((a[2] + b[2]) / 2, 0, a, b, kind, bid))
    for p, kind in skel["joints"]:
        items.append((p[2] + 0.01, 1, p, None, kind, None))
    items.sort(key=lambda it: (it[0], it[1]))
    segs = []
    for z, typ, a, b, kind, bid in items:
        zpx = z * px_per_unit
        if typ == 0:
            w = bone_width(kind, zpx, ppi)
            pa, pb = to_screen(a), to_screen(b)
            c = color
            hl = None
            if bid is not None and bid == selected:
                hl = ACCENT
            elif bid is not None and bid == hover:
                hl = mix(ACCENT, (255, 255, 255), 0.4)
            pygame.draw.line(surf, hl or halo, pa, pb, w + (6 if hl else 2))
            pygame.draw.circle(surf, hl or halo, pa, (w + (6 if hl else 2)) // 2)
            pygame.draw.circle(surf, hl or halo, pb, (w + (6 if hl else 2)) // 2)
            pygame.draw.line(surf, c, pa, pb, w)
            pygame.draw.circle(surf, c, pa, w // 2)
            pygame.draw.circle(surf, c, pb, w // 2)
            if bid is not None:
                segs.append((bid, pa, pb, w))
        else:
            r = joint_radius(kind, zpx, ppi)
            q = to_screen(a)
            pygame.draw.circle(surf, halo, q, r + 1)
            pygame.draw.circle(surf, color, q, r)
    return segs


def draw_skin_static(surf, skel, to_screen, px_per_unit, pianist):
    """A still hand (static_skeleton) drawn with the pianist's skin."""
    sk = pianist.skin_settings()
    if sk["style"] == "skeleton":
        return draw_skeleton(surf, skel, to_screen, px_per_unit, tuple(sk["colors"]["skeleton"]["bone"]))
    ppi = px_per_unit / INCHES_PER_UNIT

    def project(q):
        x, y = to_screen(q)
        return (x, y, q[2] * px_per_unit)
    skins.draw_skinned(surf, [(skel["struct"], project, ppi, sk)])
    return []


def fit_view(skel, rect, margin=0.9):
    """(to_screen, px_per_unit) that fits a skeleton's top view into rect."""
    xs = [p[0] for a, b, _, _ in skel["bones"] for p in (a, b)]
    ys = [p[1] for a, b, _, _ in skel["bones"] for p in (a, b)]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sc = min(rect.w / max(1e-6, x1 - x0), rect.h / max(1e-6, y1 - y0)) * margin
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def to_screen(p):
        return (int(round(rect.centerx + (p[0] - cx) * sc)), int(round(rect.centery - (p[1] - cy) * sc)))
    return to_screen, sc


def _dist_to_seg(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


# --------------------------------------------------------------------------- #
# Name prompt
# --------------------------------------------------------------------------- #
class NamePrompt:
    def __init__(self, title, text, validate, ok_label="Continue"):
        self.title, self.validate = title, validate
        self.input = TextInput(text, placeholder="Pianist name", max_len=32,
                               on_submit=lambda t: self._ok())
        self.input.set_focus(True)
        self.buttons = [Button("Cancel", "cancel"), Button(ok_label, "ok")]
        self.error = ""
        self.choice = None

    def _ok(self):
        name = self.input.text.strip()
        err = self.validate(name)
        if err:
            self.error = err
        else:
            self.choice = "ok"
            self.input.set_focus(False)

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.choice = "cancel"
            self.input.set_focus(False)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self.buttons:
                if b.hit(event.pos):
                    if b.action == "ok":
                        self._ok()
                    else:
                        self.choice = "cancel"
                        self.input.set_focus(False)
                    return
            self.input.handle_event(event)
            self.input.set_focus(True)
            return
        if self.input.handle_event(event):
            self.error = ""

    def draw(self, surf, fonts):
        w, h = surf.get_size()
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        box = pygame.Rect(0, 0, 480, 210)
        box.center = (w // 2, h // 2)
        pygame.draw.rect(surf, PANEL, box, border_radius=12)
        pygame.draw.rect(surf, PANEL_EDGE, box, 1, border_radius=12)
        surf.blit(fonts["button"].render(self.title, True, TEXT), (box.x + 24, box.y + 20))
        self.input.rect = pygame.Rect(box.x + 24, box.y + 66, box.w - 48, 40)
        self.input.draw(surf, fonts)
        if self.error:
            surf.blit(fonts["small"].render(self.error, True, (240, 120, 110)), (box.x + 26, box.y + 112))
        mouse = pygame.mouse.get_pos()
        x = box.right - 24
        for b in reversed(self.buttons):
            b.rect = pygame.Rect(x - 130, box.bottom - 24 - 42, 130, 42)
            b.draw(surf, fonts, mouse)
            x -= 142


# --------------------------------------------------------------------------- #
# The studio
# --------------------------------------------------------------------------- #
class PianistStudio:
    def __init__(self, app):
        self.app = app
        self.screen = app.screen
        self.fonts = app.fonts
        self.page = "browser"
        self.modal = None
        self._after_modal = None
        self.message, self.message_age = "", 0.0
        # browser
        self.search = TextInput(placeholder="Search pianists…", max_len=30, on_change=lambda t: self._refresh())
        self.size_filter = "All"
        self.sort = "Name"
        self.scroll = 0
        self.pianists = []
        self.shown = []
        self._info = {}
        self.selected_id = pianists.active().id
        self._last_click = (None, 0)
        self._refresh(reload=True)
        # editing
        self.work = None
        self.is_new = False
        self.visited = set()
        self.dirty = False
        self.shape = "stretched"
        self.sel_bone = None
        self.hover_bone = None
        self.span_view = False
        self.behavior_sel = pianists.BEHAVIORS[0]["id"]
        self._segs = []
        self._cells = []
        self._opt_rects = []
        self.sliders = []
        pygame.display.set_caption("Piano Animator - pianists")

    # ----- data ---------------------------------------------------------------
    def _refresh(self, reload=False):
        if reload:
            self.pianists = pianists.list_pianists()
            self._info = {}
        for p in self.pianists:
            key = (p.id, p.modified)
            if key not in self._info:
                self._info[key] = (p.span_semitones(), p.span_label(), p.size_class())
        q = self.search.text.strip().lower()
        out = [p for p in self.pianists if q in p.name.lower()]
        if self.size_filter != "All":
            out = [p for p in out if self.info(p)[2] == self.size_filter]
        if self.sort == "Name":
            out.sort(key=lambda p: (not p.builtin, p.name.lower()))
        elif self.sort == "Recent":
            out.sort(key=lambda p: -p.modified)
        else:
            out.sort(key=lambda p: -self.info(p)[0])
        self.shown = out
        self.scroll = max(0, min(self.scroll, max(0, len(out) - 1)))

    def info(self, p):
        key = (p.id, p.modified)
        if key not in self._info:
            self._info[key] = (p.span_semitones(), p.span_label(), p.size_class())
        return self._info[key]

    def selected(self):
        for p in self.pianists:
            if p.id == self.selected_id:
                return p
        return self.pianists[0]

    def say(self, text):
        self.message, self.message_age = text, 0.0

    # ----- actions --------------------------------------------------------------
    def _validate_name(self, name, except_id=None):
        if not name:
            return "Give your pianist a name"
        if name.lower() == "default":
            return "That name is taken by the built-in pianist"
        if pianists.name_taken(name, except_id):
            return "There's already a pianist with that name"
        return ""

    def new_pianist(self):
        self.modal = NamePrompt("Name your new pianist", "", self._validate_name)
        self._after_modal = self._start_new

    def _start_new(self, name):
        self.work = pianists.Pianist(name)
        self.is_new = True
        self.visited = set()
        self.dirty = True
        self._open_overview()

    def edit_selected(self):
        p = self.selected()
        if p.builtin:
            self.say("The default pianist can't be changed - duplicate it and edit the copy")
            return
        self.work = p.copy()
        self.is_new = False
        self.visited = {"anatomy", "behavior"}
        self.dirty = False
        self._open_overview()

    def duplicate_selected(self):
        src = self.selected()
        base = f"{src.name} copy"
        name, k = base, 2
        while pianists.name_taken(name):
            name, k = f"{base} {k}", k + 1
        self.modal = NamePrompt("Name the copy", name, self._validate_name, ok_label="Duplicate")

        def done(n):
            p = src.copy()
            p.id, p.name, p.created = "", n, 0.0
            pianists.save(p)
            self.selected_id = p.id
            self._refresh(reload=True)
            self.say(f"Made “{n}” - a copy of {src.name}")
        self._after_modal = done

    def delete_selected(self):
        p = self.selected()
        if p.builtin:
            return
        self.modal = Dialog(f"Delete {p.name}?", "This can't be undone.",
                            [("Delete", "delete"), ("Cancel", "cancel")])

        def done(choice):
            if choice == "delete":
                pianists.delete(p)
                self.selected_id = pianists.active().id
                self._refresh(reload=True)
                self.say(f"Deleted {p.name}")
        self._after_modal = done

    def make_active(self, p=None):
        p = p or self.selected()
        pianists.set_active(p)
        self.say(f"{p.name} is now the active pianist - their hands play in every mode")

    def save_work(self):
        if not self.can_save():
            return
        was_new = self.is_new
        pianists.save(self.work)
        self.is_new = False
        self.dirty = False
        self.selected_id = self.work.id
        self._refresh(reload=True)
        self.page = "browser"
        self.say(f"Saved {self.work.name}" + (" - use “Make active” to play with their hands" if was_new
                                                and pianists.active().id != self.work.id else ""))

    def can_save(self):
        if self.work is None:
            return False
        if self.is_new:
            return {"anatomy", "behavior"} <= self.visited
        return self.dirty

    def _leave_editor(self, then):
        if self.work is not None and self.dirty:
            self.modal = Dialog("Unsaved pianist", f"Save {self.work.name} before leaving?",
                                [("Save", "save"), ("Discard", "discard"), ("Cancel", "cancel")])

            def done(choice):
                if choice == "save":
                    if self.can_save():
                        self.save_work()
                        then()
                    else:
                        self.say("Open Anatomy and Behavior once before saving a new pianist")
                elif choice == "discard":
                    self.work, self.dirty = None, False
                    then()
            self._after_modal = done
            return
        self.work = None
        then()

    def rename_work(self):
        self.modal = NamePrompt("Rename pianist", self.work.name,
                                lambda n: self._validate_name(n, self.work.id or None), ok_label="Rename")

        def done(n):
            self.work.name = n
            self.dirty = True
        self._after_modal = done

    # ----- pages ------------------------------------------------------------------
    def _open_overview(self):
        self.page = "overview"
        self.span_view = False
        self._appearance_sliders()

    # appearance (skin) -----------------------------------------------------------
    def _skin(self):
        return self.work.skin_settings()

    def _slot(self):
        sk = self._skin()
        slots = [k for k, _, _ in skins.SKINS[sk["style"]]["slots"]]
        if getattr(self, "slot", None) not in slots:
            self.slot = slots[0]
        return self.slot

    def _appearance_sliders(self):
        sk = self._skin()
        style, slot = sk["style"], self._slot()
        col = sk["colors"][style][slot]
        self.sliders = []
        for i, ch in enumerate("RGB"):
            def setter(v, i=i):
                c = self._skin()["colors"][self._skin()["style"]][self._slot()]
                c[i] = int(round(v))
                if self._skin()["style"] == "skeleton":
                    self.work.color = tuple(c)
                self.dirty = True
            tint = [(220, 70, 70), (80, 190, 90), (80, 130, 230)][i]
            self.sliders.append(Slider(ch, 0, 255, col[i], setter, fmt=lambda v: str(int(round(v))),
                                       color=tint, step=1))

        def set_opt(key):
            def f(v):
                self._skin()[key] = v
                self.dirty = True
            return f
        self.sliders.append(Slider("Finger thickness", *skins.FINGER_WIDTH_RANGE, sk["finger_width"],
                                   set_opt("finger_width"), fmt=lambda v: f"{int(round(v * 100))}%"))
        self.sliders.append(Slider("Outline", *skins.OUTLINE_RANGE, sk["outline"], set_opt("outline"),
                                   fmt=lambda v: "none" if v < 0.05 else f"{v:.1f}×"))

    def _appearance_rects(self):
        """[(action, label, rect, active, color)] for the skin chips, colour slots and toggles."""
        body, panel, view = self._geom()
        x0, w0 = panel.x + 20, panel.w - 40
        y = panel.y + 40
        out = []
        sk = self._skin()
        half = (w0 - 8) // 2
        for i, sid in enumerate(skins.SKIN_ORDER):
            r = pygame.Rect(x0 + (i % 2) * (half + 8), y + (i // 2) * 36, half, 30)
            out.append((("skin", sid), skins.SKINS[sid]["label"], r, sk["style"] == sid, None))
        y += 80
        slots = skins.SKINS[sk["style"]]["slots"]
        n = len(slots)
        sw = (w0 - 6 * (n - 1)) // n
        for i, (key, label, _) in enumerate(slots):
            r = pygame.Rect(x0 + i * (sw + 6), y, sw, 28)
            out.append((("slot", key), label, r, key == self._slot(), tuple(sk["colors"][sk["style"]][key])))
        self._sliders_y = y + 38
        ty = self._sliders_y + 5 * 50 + 6
        tw = (w0 - 12) // 3
        for i, (key, label) in enumerate((("details", "Details"), ("sleeve", "Cuff / sleeve"),
                                          ("shadow", "Shadow"))):
            r = pygame.Rect(x0 + i * (tw + 6), ty, tw, 28)
            out.append((("toggle", key), label, r, bool(sk[key]), None))
        self._overview_buttons_y = ty + 42
        return out

    def _open_anatomy(self):
        self.page = "anatomy"
        self.visited.add("anatomy")
        self.shape_before = self.shape
        self.shape = "stretched"
        self.span_view = False
        self._anatomy_sliders()

    def _group_scale(self, ids):
        return sum(self.work.anatomy[b] / pianists.DEFAULT_ANATOMY[b] for b in ids) / len(ids)

    def _anatomy_sliders(self):
        def group_setter(ids):
            def set_(v):
                cur = self._group_scale(ids)
                for b in ids:
                    self.work.anatomy[b] = pianists.clamp_bone(b, self.work.anatomy[b] * v / cur)
                self.dirty = True
                self._anatomy_sliders_sync()
            return set_
        pct = lambda v: f"{int(round(v * 100))}% of default"
        self.g_mc = Slider("All metacarpals", pianists.BONE_MIN, pianists.BONE_MAX,
                           self._group_scale(pianists.METACARPALS), group_setter(pianists.METACARPALS), fmt=pct)
        self.g_ph = Slider("All phalanges", pianists.BONE_MIN, pianists.BONE_MAX,
                           self._group_scale(pianists.PHALANGES), group_setter(pianists.PHALANGES), fmt=pct)
        self.bone_slider = None
        if self.sel_bone:
            b = self.sel_bone
            d = pianists.DEFAULT_ANATOMY[b]

            def set_bone(v):
                self.work.anatomy[b] = pianists.clamp_bone(b, v)
                self.dirty = True
                self.g_mc.value = self._group_scale(pianists.METACARPALS)
                self.g_ph.value = self._group_scale(pianists.PHALANGES)
            self.bone_slider = Slider(pianists.bone_name(b), d * pianists.BONE_MIN, d * pianists.BONE_MAX,
                                      self.work.anatomy[b], set_bone,
                                      fmt=lambda v: f"{_cm(v):.2f} cm  ({int(round(v / d * 100))}%)",
                                      lo_label=f"{_cm(d * pianists.BONE_MIN):.1f} cm",
                                      hi_label=f"{_cm(d * pianists.BONE_MAX):.1f} cm")

    def _anatomy_sliders_sync(self):
        if self.bone_slider and self.sel_bone:
            self.bone_slider.value = self.work.anatomy[self.sel_bone]

    def _open_behavior(self):
        self.page = "behavior"
        self.visited.add("behavior")
        self._behavior_controls()

    def _behavior_controls(self):
        spec = pianists.BEHAVIOR[self.behavior_sel]
        self.b_slider = None
        if spec["kind"] == "slider":
            def set_(v, k=spec["id"]):
                self.work.behavior[k] = v
                self.dirty = True
            self.b_slider = Slider(spec["label"], spec["min"], spec["max"], self.work.b(spec["id"]), set_,
                                   fmt=spec["fmt"], lo_label=spec.get("lo"), hi_label=spec.get("hi"))

    # ----- layout helpers -------------------------------------------------------------
    def _geom(self):
        w, h = self.screen.get_size()
        body = pygame.Rect(0, TOP_BAR_H, w, h - TOP_BAR_H)
        panel = pygame.Rect(w - PANEL_W, TOP_BAR_H, PANEL_W, h - TOP_BAR_H)
        view = pygame.Rect(0, TOP_BAR_H, w - PANEL_W, h - TOP_BAR_H)
        return body, panel, view

    def _buttons(self):
        """The clickable buttons of the current page: [(Button)] with rects set."""
        body, panel, view = self._geom()
        bs = []
        x0, w0 = panel.x + 20, panel.w - 40

        def btn(label, action, y, h=40, x=None, w=None, enabled=True, active=False, font="normal"):
            b = Button(label, action, font=font)
            b.rect = pygame.Rect(x if x is not None else x0, y, w if w is not None else w0, h)
            b.enabled, b.active = enabled, active
            bs.append(b)
            return b

        W, H = self.screen.get_size()
        bs_top = Button("← Menu" if self.page == "browser" else "← Pianists", "back", font="small")
        bs_top.rect = pygame.Rect(8, 5, 110, TOP_BAR_H - 10)
        bs.append(bs_top)
        if self.page == "browser":
            left = self._browser_rects()["left"]
            btn("+  New pianist", "new", left.y + 12, x=left.x + 16, w=left.w - 32, h=40, font="normal")
            p = self.selected()
            is_active = pianists.active().id == p.id
            y = H - 20 - 44 * 4 - 12 * 3
            btn("Active ✓" if is_active else "Make active", "activate", y, h=44, enabled=not is_active,
                active=is_active)
            btn("Edit", "edit", y + 56, h=44, enabled=not p.builtin)
            btn("Duplicate", "duplicate", y + 112, h=44)
            btn("Delete", "delete", y + 168, h=44, enabled=not p.builtin)
        elif self.page == "overview":
            self._appearance_rects()
            y = self._overview_buttons_y
            half = (w0 - 8) // 2
            btn("Natural curve" if self.shape == "stretched" else "Stretched out", "shape", y, h=36,
                w=half, font="small")
            btn("Rename…", "rename", y, h=36, x=x0 + half + 8, w=half, font="small")
            btn(("✓  " if "anatomy" in self.visited else "") + "Edit anatomy", "anatomy", y + 44, h=40,
                w=half)
            btn(("✓  " if "behavior" in self.visited else "") + "Edit behavior", "behavior", y + 44, h=40,
                x=x0 + half + 8, w=half)
            btn("Save pianist", "save", H - 20 - 48, h=48, enabled=self.can_save(), font="button")
        elif self.page == "anatomy":
            y = H - 20 - 40 * 3 - 10 * 2
            btn("Hide hand span" if self.span_view else "Show hand span", "span", y, active=self.span_view)
            half = (w0 - 10) // 2
            btn("Reset bone", "reset_bone", y + 50, w=half, enabled=bool(self.sel_bone))
            btn("Reset all", "reset_all", y + 50, x=x0 + half + 10, w=half)
            btn("Done", "done", y + 100)
        elif self.page == "behavior":
            y = H - 20 - 40
            btn("Done", "done", y)
            lst = self._behavior_list_rects()
            for bid, r in lst:
                spec = pianists.BEHAVIOR[bid]
                b = Button(spec["label"], ("pick", bid), font="small")
                b.rect = r
                b.active = bid == self.behavior_sel
                bs.append(b)
            spec = pianists.BEHAVIOR[self.behavior_sel]
            if spec["kind"] == "slider":
                btn("Reset to default", "reset_behavior", self._detail_rect().y + 190, h=34,
                    x=self._detail_rect().x + 24, w=200, font="small")
        return bs

    def _browser_rects(self):
        body, panel, view = self._geom()
        W, H = self.screen.get_size()
        left = pygame.Rect(0, TOP_BAR_H, min(470, W // 2 - 60), H - TOP_BAR_H)
        search = pygame.Rect(left.x + 16, left.y + 64, left.w - 32, 38)
        chips_y = search.bottom + 12
        list_rect = pygame.Rect(left.x + 8, chips_y + 70, left.w - 16, left.bottom - chips_y - 78)
        preview = pygame.Rect(left.right, TOP_BAR_H, W - left.right - PANEL_W, H - TOP_BAR_H)
        return {"left": left, "search": search, "chips_y": chips_y, "list": list_rect, "preview": preview}

    def _chips(self):
        r = self._browser_rects()
        x, y = r["left"].x + 16, r["chips_y"]
        out = []
        f = self.fonts["small"]
        for group, opts, cur, yy in (("size", ["All", "Small", "Medium", "Large"], self.size_filter, y),
                                     ("sort", ["Name", "Recent", "Span"], self.sort, y + 34)):
            xx = x + 50
            for o in opts:
                w = f.size(o)[0] + 22
                out.append((group, o, pygame.Rect(xx, yy, w, 26), o == cur))
                xx += w + 6
        return out

    def _overview_y(self):
        return TOP_BAR_H + 20 + 30 + 3 * 60 + 70

    def _behavior_list_rects(self):
        body, panel, view = self._geom()
        x, y = 20, TOP_BAR_H + 16
        out = []
        group = None
        self._group_labels = []
        W, H = self.screen.get_size()
        n = len(pianists.BEHAVIORS)
        groups = len({b["group"] for b in pianists.BEHAVIORS})
        step = max(24, min(34, int((H - y - 16 - groups * 26) / n)))
        for spec in pianists.BEHAVIORS:
            if spec["group"] != group:
                group = spec["group"]
                self._group_labels.append((group, (x + 4, y + 4)))
                y += 26
            out.append((spec["id"], pygame.Rect(x, y, 300, step - 4)))
            y += step
        return out

    def _detail_rect(self):
        W, H = self.screen.get_size()
        return pygame.Rect(340, TOP_BAR_H + 16, W - 340 - 20, H - TOP_BAR_H - 36 - 60)

    # ----- events -----------------------------------------------------------------------
    def handle_event(self, event):
        if event.type == pygame.QUIT:
            if self.work is not None and self.dirty:
                self._leave_editor(lambda: None)
                return True
            return False
        if event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.get_surface() or self.screen
            return True
        if self.modal:
            self.modal.handle_event(event)
            choice = self.modal.choice
            if choice:
                modal, then = self.modal, self._after_modal
                self.modal = self._after_modal = None
                if isinstance(modal, NamePrompt):
                    if choice == "ok" and then:
                        then(modal.input.text.strip())
                elif then:
                    then(choice)
            return True
        page = self.page
        # typing in the search box
        if page == "browser" and self.search.handle_event(event):
            return True
        for s in self._page_sliders():
            if s.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return self._back()
            if page == "browser" and event.key == pygame.K_n:
                self.new_pianist()
            return True
        if event.type == pygame.MOUSEWHEEL and page == "browser":
            if self._browser_rects()["list"].collidepoint(pygame.mouse.get_pos()):
                self.scroll = max(0, min(max(0, len(self.shown) - 1), self.scroll - event.y))
            return True
        if event.type == pygame.MOUSEMOTION and page == "anatomy" and not self.span_view:
            self.hover_bone = self._bone_at(event.pos)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for b in self._buttons():
                if b.hit(event.pos):
                    return self._action(b.action)
            if page == "overview":
                for action, _, r, _, _ in self._appearance_rects():
                    if r.collidepoint(event.pos):
                        return self._appearance_action(action)
            if page == "browser":
                return self._browser_click(event.pos)
            if page == "anatomy":
                bid = self._bone_at(event.pos) if not self.span_view else None
                if bid is None:
                    for cb, r in self._cells:
                        if r.collidepoint(event.pos):
                            bid = cb
                if bid is not None:
                    self.sel_bone = bid
                    self._anatomy_sliders()
            if page == "behavior":
                for value, r in self._opt_rects:
                    if r.collidepoint(event.pos):
                        self.work.behavior[self.behavior_sel] = value
                        self.dirty = True
        return True

    def _appearance_action(self, action):
        kind, key = action
        sk = self._skin()
        if kind == "skin":
            sk["style"] = key
            if key == "skeleton":
                self.work.color = tuple(sk["colors"]["skeleton"]["bone"])
        elif kind == "slot":
            self.slot = key
        elif kind == "toggle":
            sk[key] = not sk[key]
        self.dirty = True
        self._appearance_sliders()
        return True

    def _page_sliders(self):
        if self.page == "overview":
            return self.sliders
        if self.page == "anatomy":
            return [s for s in (self.g_mc, self.g_ph, self.bone_slider) if s]
        if self.page == "behavior" and self.b_slider:
            return [self.b_slider]
        return []

    def _back(self):
        if self.page == "browser":
            if self.search.focus:
                self.search.set_focus(False)
                return True
            return "menu"
        if self.page in ("anatomy", "behavior"):
            if self.page == "anatomy":
                self.shape = getattr(self, "shape_before", self.shape)
            self._open_overview()
            return True
        self._leave_editor(lambda: self._to_browser())
        return True

    def _to_browser(self):
        self.page = "browser"
        self._refresh(reload=True)

    def _action(self, a):
        if isinstance(a, tuple) and a[0] == "pick":
            self.behavior_sel = a[1]
            self._behavior_controls()
            return True
        if a == "back":
            return self._back()
        if a == "new":
            self.new_pianist()
        elif a == "activate":
            self.make_active()
        elif a == "edit":
            self.edit_selected()
        elif a == "duplicate":
            self.duplicate_selected()
        elif a == "delete":
            self.delete_selected()
        elif a == "shape":
            self.shape = "natural" if self.shape == "stretched" else "stretched"
        elif a == "anatomy":
            self._open_anatomy()
        elif a == "behavior":
            self._open_behavior()
        elif a == "rename":
            self.rename_work()
        elif a == "save":
            self.save_work()
        elif a == "span":
            self.span_view = not self.span_view
        elif a == "reset_bone" and self.sel_bone:
            self.work.anatomy[self.sel_bone] = pianists.DEFAULT_ANATOMY[self.sel_bone]
            self.dirty = True
            self._anatomy_sliders()
        elif a == "reset_all":
            self.work.anatomy = dict(pianists.DEFAULT_ANATOMY)
            self.dirty = True
            self._anatomy_sliders()
        elif a == "reset_behavior":
            spec = pianists.BEHAVIOR[self.behavior_sel]
            self.work.behavior[spec["id"]] = spec["default"]
            self.dirty = True
            self._behavior_controls()
        elif a == "done":
            self._back()
        return True

    def _browser_click(self, pos):
        r = self._browser_rects()
        for group, o, rect, _ in self._chips():
            if rect.collidepoint(pos):
                if group == "size":
                    self.size_filter = o
                else:
                    self.sort = o
                self._refresh()
                return True
        lr = r["list"]
        if lr.collidepoint(pos):
            i = self.scroll + (pos[1] - lr.y) // ROW_H
            if 0 <= i < len(self.shown):
                p = self.shown[i]
                now = pygame.time.get_ticks()
                if self._last_click[0] == p.id and now - self._last_click[1] < 400 and not p.builtin:
                    self.selected_id = p.id
                    self.edit_selected()
                    return True
                self._last_click = (p.id, now)
                self.selected_id = p.id
        return True

    def _bone_at(self, pos):
        best, bd = None, 1e9
        for bid, a, b, w in self._segs:
            d = _dist_to_seg(pos, a, b)
            if d <= max(8, w / 2 + 5) and d < bd:
                best, bd = bid, d
        return best

    # ----- per frame --------------------------------------------------------------------
    def update(self, dt):
        self.message_age += dt

    def leave(self):
        self.search.set_focus(False)

    # ----- drawing ------------------------------------------------------------------------
    def render(self):
        s = self.screen
        s.fill(BG)
        getattr(self, f"_draw_{self.page}")(s)
        self._draw_top_bar(s)
        mouse = pygame.mouse.get_pos()
        for b in self._buttons():
            if isinstance(b.action, tuple):
                continue                                  # behaviour list draws itself
            b.draw(s, self.fonts, mouse)
        if self.message and self.message_age < 6:
            img = self.fonts["small"].render(self.message, True, ACCENT)
            w, h = s.get_size()
            box = img.get_rect(midbottom=(w // 2 - PANEL_W // 2, h - 12)).inflate(20, 10)
            pygame.draw.rect(s, (20, 20, 26), box, border_radius=6)
            s.blit(img, img.get_rect(center=box.center))
        if self.modal:
            self.modal.draw(s, self.fonts)

    def _draw_top_bar(self, s):
        w, _ = s.get_size()
        r = pygame.Rect(0, 0, w, TOP_BAR_H)
        pygame.draw.rect(s, BAR_BG, r)
        titles = {"browser": "Pianists",
                  "overview": (("New pianist: " if self.is_new else "Editing: ") + self.work.name) if self.work else "",
                  "anatomy": f"{self.work.name if self.work else ''} - anatomy",
                  "behavior": f"{self.work.name if self.work else ''} - behavior"}
        title = titles[self.page] + ("  •" if self.work is not None and self.dirty and self.page != "browser" else "")
        img = self.fonts["normal"].render(title, True, TEXT)
        s.blit(img, (130, (TOP_BAR_H - img.get_height()) // 2))
        act = pianists.active()
        img = self.fonts["small"].render(f"Active pianist: {act.name}", True, TEXT_DIM)
        rr = img.get_rect(midright=(w - 14, TOP_BAR_H // 2))
        s.blit(img, rr)
        pygame.draw.circle(s, act.badge_color, (rr.x - 12, TOP_BAR_H // 2), 6)
        pygame.draw.circle(s, (150, 150, 160), (rr.x - 12, TOP_BAR_H // 2), 6, 1)

    def _panel(self, s, rect):
        pygame.draw.rect(s, (32, 32, 40), rect)
        pygame.draw.line(s, (50, 50, 62), rect.topleft, rect.bottomleft)

    # browser ---------------------------------------------------------------------------
    def _draw_browser(self, s):
        r = self._browser_rects()
        body, panel, view = self._geom()
        self._panel(s, r["left"])
        pygame.draw.line(s, (50, 50, 62), r["left"].topright, r["left"].bottomright)
        self.search.rect = r["search"]
        self.search.draw(s, self.fonts)
        f = self.fonts
        s.blit(f["small"].render("Size", True, TEXT_DIM), (r["left"].x + 16, r["chips_y"] + 5))
        s.blit(f["small"].render("Sort", True, TEXT_DIM), (r["left"].x + 16, r["chips_y"] + 39))
        mouse = pygame.mouse.get_pos()
        for group, o, rect, on in self._chips():
            base = mix((54, 54, 68), ACCENT, 0.45) if on else ((74, 74, 94) if rect.collidepoint(mouse) else (54, 54, 68))
            pygame.draw.rect(s, base, rect, border_radius=13)
            img = f["small"].render(o, True, TEXT)
            s.blit(img, img.get_rect(center=rect.center))
        lr = r["list"]
        s.set_clip(lr)
        act = pianists.active().id
        for k, p in enumerate(self.shown[self.scroll:]):
            row = pygame.Rect(lr.x, lr.y + k * ROW_H, lr.w, ROW_H - 4)
            if row.y > lr.bottom:
                break
            sel = p.id == self.selected_id
            bg = mix((40, 40, 50), ACCENT, 0.22) if sel else ((44, 44, 56) if row.collidepoint(mouse) else (36, 36, 46))
            pygame.draw.rect(s, bg, row, border_radius=8)
            pygame.draw.circle(s, p.badge_color, (row.x + 22, row.centery), 10)
            pygame.draw.circle(s, (150, 150, 160), (row.x + 22, row.centery), 10, 1)
            s.blit(f["normal"].render(p.name, True, TEXT), (row.x + 44, row.y + 6))
            span, label, size = self.info(p)
            sub = f"{size} hand  ·  {label}" + ("  ·  built in" if p.builtin else "")
            s.blit(f["small"].render(sub, True, TEXT_DIM), (row.x + 44, row.y + 29))
            if p.id == act:
                chip = f["label"].render("ACTIVE", True, (20, 20, 24))
                cr = chip.get_rect(midright=(row.right - 14, row.centery)).inflate(12, 6)
                pygame.draw.rect(s, ACCENT, cr, border_radius=4)
                s.blit(chip, chip.get_rect(center=cr.center))
        s.set_clip(None)
        if not self.shown:
            img = f["small"].render("No pianists match", True, TEXT_DIM)
            s.blit(img, img.get_rect(midtop=(lr.centerx, lr.y + 20)))
        # preview
        p = self.selected()
        pv = r["preview"].inflate(-40, -80)
        pv.y += 30
        if pv.w > 120 and pv.h > 120:
            skel = static_skeleton(HandGeometry(p.anatomy), "natural", curl_factor(p))
            to_screen, sc = fit_view(skel, pv, 0.85)
            draw_skin_static(s, skel, to_screen, sc, p)
        # details panel
        self._panel(s, panel)
        x, y = panel.x + 20, panel.y + 20
        s.blit(f["big"].render(p.name, True, TEXT), (x, y))
        y += 48
        span, label, size = self.info(p)
        lines = [("Hand", f"{size}, spans {'an' if label[0] in 'aeiou' else 'a'} {label}"),
                 ("Chromatic", self._opt_label(p, "chromatic")),
                 ("Repeated notes", self._opt_label(p, "repeated")),
                 ("Trills", self._opt_label(p, "trill")),
                 ("Octaves", self._opt_label(p, "octaves")),
                 ("Weak fingers", pianists.BEHAVIOR["weak_bias"]["fmt"](p.b("weak_bias")) + " bias"),
                 ("Stretch", pianists.BEHAVIOR["stretch_bias"]["fmt"](p.b("stretch_bias")))]
        for k, v in lines:
            s.blit(f["small"].render(k, True, TEXT_DIM), (x, y))
            s.blit(f["small"].render(v, True, TEXT), (x + 120, y))
            y += 24

    def _opt_label(self, p, key):
        spec = pianists.BEHAVIOR[key]
        v = p.b(key)
        for val, label, _ in spec["options"]:
            if val == v:
                return label
        return str(v)

    # overview ---------------------------------------------------------------------------
    def _draw_hand_view(self, s, rect, shape, interactive=False, pianist=None):
        p = pianist or self.work
        skel = static_skeleton(HandGeometry(p.anatomy), shape, curl_factor(p))
        to_screen, sc = fit_view(skel, rect, 0.88)
        if interactive:
            # anatomy: always the bones, so each can be clicked
            bone = tuple(p.skin_settings()["colors"]["skeleton"]["bone"])
            self._segs = draw_skeleton(s, skel, to_screen, sc, bone, selected=self.sel_bone,
                                       hover=self.hover_bone)
        else:
            draw_skin_static(s, skel, to_screen, sc, p)
            self._segs = []
        return to_screen, sc

    def _draw_overview(self, s):
        body, panel, view = self._geom()
        pygame.draw.rect(s, (28, 28, 35), view)
        self._draw_hand_view(s, view.inflate(-40, -40), self.shape)
        cap = "Stretched out" if self.shape == "stretched" else "Natural resting curve"
        s.blit(self.fonts["small"].render(f"{cap}  ·  right hand (the left is its mirror image)", True, TEXT_DIM),
               (view.x + 16, view.bottom - 26))
        self._panel(s, panel)
        f = self.fonts
        x = panel.x + 20
        s.blit(f["small"].render("Appearance", True, TEXT_DIM), (x, panel.y + 14))
        mouse = pygame.mouse.get_pos()
        for action, label, r, on, col in self._appearance_rects():
            base = mix((54, 54, 68), ACCENT, 0.45) if on else ((74, 74, 94) if r.collidepoint(mouse) else (48, 48, 60))
            pygame.draw.rect(s, base, r, border_radius=7)
            if on:
                pygame.draw.rect(s, ACCENT, r, 1, border_radius=7)
            if col is not None:
                pygame.draw.circle(s, col, (r.x + 13, r.centery), 7)
                pygame.draw.circle(s, (150, 150, 160), (r.x + 13, r.centery), 7, 1)
                img = f["label"].render(label, True, TEXT)
                s.blit(img, img.get_rect(midleft=(r.x + 24, r.centery)))
            else:
                img = f["small"].render(label, True, TEXT if action[0] != "toggle" or on else TEXT_DIM)
                s.blit(img, img.get_rect(center=r.center))
        y = self._sliders_y
        for sl in self.sliders:
            sl.layout(pygame.Rect(x - 8, y, panel.w - 24, 48))
            sl.draw(s, f)
            y += 50
        label = self.work.span_label()
        s.blit(f["small"].render(f"Hand span: {label}", True, TEXT), (x, self._overview_buttons_y + 94))
        if self.is_new:
            need = [n for n in ("anatomy", "behavior") if n not in self.visited]
            if need:
                H = s.get_height()
                msg = "Save unlocks after visiting " + " and ".join(n.capitalize() for n in need)
                s.blit(f["label"].render(msg, True, TEXT_DIM), (x, H - 20 - 48 - 22))

    # anatomy ------------------------------------------------------------------------------
    def _draw_anatomy(self, s):
        body, panel, view = self._geom()
        pygame.draw.rect(s, (28, 28, 35), view)
        f = self.fonts
        if self.span_view:
            self._draw_span(s, view.inflate(-30, -30))
            self._segs = []
        else:
            self._draw_hand_view(s, view.inflate(-40, -40), "stretched", interactive=True)
            hint = "Click a bone to change its length"
            if self.hover_bone:
                b = self.hover_bone
                hint = f"{pianists.bone_name(b)}: {_cm(self.work.anatomy[b]):.2f} cm"
            s.blit(f["small"].render(hint, True, TEXT_DIM), (view.x + 16, view.bottom - 26))
        self._panel(s, panel)
        x, y = panel.x + 20, panel.y + 16
        for sl in (self.g_mc, self.g_ph):
            sl.layout(pygame.Rect(x - 8, y, panel.w - 24, 52))
            sl.draw(s, f)
            y += 58
        y += 4
        if self.bone_slider:
            self.bone_slider.layout(pygame.Rect(x - 8, y, panel.w - 24, 64))
            self.bone_slider.draw(s, f)
        else:
            s.blit(f["small"].render("Select a bone (hand or table) to edit it alone", True, TEXT_DIM), (x, y + 8))
        y += 72
        # bone table: fingers x (metacarpal, proximal, middle, distal)
        cw = (panel.w - 40 - 58) // 4
        for j, k in enumerate(KIND_ORDER):
            img = f["label"].render(KIND_SHORT[k], True, TEXT_DIM)
            s.blit(img, img.get_rect(midtop=(x + 58 + j * cw + cw // 2, y)))
        y += 18
        self._cells = []
        mouse = pygame.mouse.get_pos()
        for fi in range(1, 6):
            s.blit(f["small"].render(pianists.FINGER_WORD[fi], True, TEXT_DIM), (x, y + 4))
            for j, k in enumerate(KIND_ORDER):
                bid = f"{k}{fi}"
                if bid not in pianists.BONE_INFO:
                    continue
                r = pygame.Rect(x + 58 + j * cw + 2, y, cw - 4, 24)
                sel = bid == self.sel_bone
                v = self.work.anatomy[bid]
                changed = abs(v - pianists.DEFAULT_ANATOMY[bid]) > 1e-6
                bg = mix((44, 44, 56), ACCENT, 0.4) if sel else ((60, 60, 76) if r.collidepoint(mouse) or bid == self.hover_bone else (44, 44, 56))
                pygame.draw.rect(s, bg, r, border_radius=4)
                img = f["small"].render(f"{_cm(v):.1f}", True, ACCENT if changed and not sel else TEXT)
                s.blit(img, img.get_rect(center=r.center))
                self._cells.append((bid, r))
            y += 28
        y += 6
        s.blit(f["small"].render(f"Hand span: {self.work.span_label()}", True, TEXT), (x, y))
        s.blit(f["label"].render("lengths in cm  ·  highlighted = changed", True, TEXT_DIM), (x, y + 22))

    def _draw_span(self, s, rect):
        """The stretched hand over the keys, thumb on C4."""
        f = self.fonts
        lo, hi = 57, 86                                   # A3 .. D6
        whites = [p for p in range(lo, hi + 1) if not is_black_key(p)]
        # keys in their true proportions: when the view is short, narrower keys
        # (centred) rather than squashed ones
        ww = min(rect.w / len(whites), rect.h * 0.36 / 5.6)
        kh = ww * 5.6
        kw = ww * len(whites)
        kb = pygame.Rect(rect.x + (rect.w - kw) / 2, rect.y + 36, kw, kh)
        keys = {}
        wi = 0
        for p in range(lo, hi + 1):
            if not is_black_key(p):
                keys[p] = pygame.Rect(round(kb.x + wi * ww), kb.y, round(ww), kb.h)
                wi += 1
        for p in range(lo, hi + 1):
            if is_black_key(p):
                x = kb.x + sum(1 for q in whites if q < p) * ww - ww * 0.3
                keys[p] = pygame.Rect(round(x), kb.y, round(ww * 0.6), int(kb.h * 0.63))
        ppi = ww / WHITE_KEY_IN
        S = INCHES_PER_UNIT * ppi                         # pixels per model unit
        geo = HandGeometry(self.work.anatomy)
        skel = static_skeleton(geo, "span")
        t1, t5 = skel["tips"][1], skel["tips"][5]
        ang = -math.atan2(t5[1] - t1[1], t5[0] - t1[0])
        ca, sa = math.cos(ang), math.sin(ang)
        c4 = keys[60]
        ox, oy = c4.centerx, kb.bottom - WHITE_DEPTH_IN[1] * ppi

        def to_screen(p):
            dx, dy = p[0] - t1[0], p[1] - t1[1]
            rx, ry = dx * ca - dy * sa, dx * sa + dy * ca
            return (int(round(ox + rx * S)), int(round(oy - ry * S)))
        pinky_x = to_screen(t5)[0]
        reached = max((p for p, r in keys.items() if not is_black_key(p) and r.centerx <= pinky_x + 0.25 * ww),
                      default=60)
        for p, r in keys.items():
            if is_black_key(p):
                continue
            col = (242, 242, 238)
            if p == 60:
                col = mix(col, (116, 196, 64), 0.6)
            elif p == reached:
                col = mix(col, ACCENT, 0.7)
            pygame.draw.rect(s, col, r, border_bottom_left_radius=3, border_bottom_right_radius=3)
            pygame.draw.line(s, (150, 150, 150), r.topleft, (r.left, r.bottom - 1))
            if p % 12 == 0:
                img = f["label"].render(note_name(p), True, (90, 90, 100))
                s.blit(img, img.get_rect(midbottom=(r.centerx, r.bottom - 4)))
        for p, r in keys.items():
            if is_black_key(p):
                col = mix((22, 22, 26), ACCENT, 0.7) if p == reached else (22, 22, 26)
                pygame.draw.rect(s, col, r, border_bottom_left_radius=2, border_bottom_right_radius=2)
        pygame.draw.rect(s, (150, 22, 34), (kb.x, kb.y - 6, kb.w, 6))
        draw_skin_static(s, skel, to_screen, S, self.work)
        whites_up = sum(1 for q in whites if 60 < q <= reached)
        iv = pianists.interval_name(whites_up)
        text = (f"Thumb on C4, little finger reaches {note_name(reached)}: {'an' if iv[0] in 'aeiou' else 'a'} {iv}"
                f"  ·  thumb to little finger {self.work.span_label().split('·')[-1].strip()}")
        s.blit(f["normal"].render(text, True, TEXT), (rect.x, rect.y + 4))

    # behaviour ------------------------------------------------------------------------------
    def _draw_behavior(self, s):
        f = self.fonts
        W, H = s.get_size()
        mouse = pygame.mouse.get_pos()
        lst = self._behavior_list_rects()
        for group, pos in self._group_labels:
            s.blit(f["label"].render(group.upper(), True, TEXT_DIM), pos)
        for bid, r in lst:
            on = bid == self.behavior_sel
            bg = mix((44, 44, 56), ACCENT, 0.35) if on else ((58, 58, 72) if r.collidepoint(mouse) else (40, 40, 50))
            pygame.draw.rect(s, bg, r, border_radius=6)
            spec = pianists.BEHAVIOR[bid]
            img = f["small"].render(spec["label"], True, TEXT)
            s.blit(img, img.get_rect(midleft=(r.x + 10, r.centery)))
            changed = self.work.b(bid) != spec["default"]
            if changed:
                pygame.draw.circle(s, ACCENT, (r.right - 12, r.centery), 4)
        d = self._detail_rect()
        pygame.draw.rect(s, (32, 32, 40), d, border_radius=10)
        spec = pianists.BEHAVIOR[self.behavior_sel]
        x, y = d.x + 24, d.y + 20
        s.blit(f["big"].render(spec["label"], True, TEXT), (x, y))
        y += 46
        for line in _wrap(spec["desc"], f["normal"], d.w - 48):
            s.blit(f["normal"].render(line, True, TEXT_DIM), (x, y))
            y += 22
        y = max(y + 12, d.y + 120)
        self._opt_rects = []
        if spec["kind"] == "slider":
            self.b_slider.layout(pygame.Rect(x - 8, y, min(560, d.w - 40), 60))
            self.b_slider.draw(s, f)
            dflt = spec["fmt"](spec["default"])
            s.blit(f["small"].render(f"Default: {dflt}", True, TEXT_DIM), (x + 240, d.y + 198))
        else:
            cur = self.work.b(spec["id"])
            for val, label, detail in spec["options"]:
                r = pygame.Rect(x, y, min(640, d.w - 48), 58 if spec["id"] != "chromatic" else 92)
                on = val == cur
                bg = mix((44, 44, 56), ACCENT, 0.3) if on else ((54, 54, 68) if r.collidepoint(mouse) else (40, 40, 50))
                pygame.draw.rect(s, bg, r, border_radius=8)
                if on:
                    pygame.draw.rect(s, ACCENT, r, 2, border_radius=8)
                s.blit(f["button"].render(label, True, TEXT), (r.x + 14, r.y + 6))
                if detail:
                    dw = r.w - (280 if spec["id"] == "chromatic" else 28)
                    for k, line in enumerate(_wrap(detail, f["small"], dw)[:3]):
                        s.blit(f["small"].render(line, True, TEXT_DIM), (r.x + 14, r.y + 34 + 18 * k))
                if spec["id"] == "chromatic":
                    self._draw_chrom_keys(s, pygame.Rect(r.right - 250, r.y + 8, 236, r.h - 16), val)
                self._opt_rects.append((val, r))
                y += r.h + 8

    def _draw_chrom_keys(self, s, rect, style):
        """A little keyboard, C4 to D5, with the right hand's fingers going up."""
        import figures
        old = figures.PREFS.get("chromatic")
        figures.PREFS["chromatic"] = style
        ps = list(range(60, 75))
        fs = dict(zip(ps, figures.chromatic_fingering(ps, "R")))
        figures.PREFS["chromatic"] = old
        whites = [p for p in ps if not is_black_key(p)]
        ww = rect.w / len(whites)
        f = self.fonts["label"]
        for i, p in enumerate(whites):
            r = pygame.Rect(round(rect.x + i * ww), rect.y, round(ww) - 1, rect.h)
            pygame.draw.rect(s, (236, 236, 232), r, border_radius=2)
            img = f.render(str(fs[p]), True, (20, 20, 26))
            s.blit(img, img.get_rect(midbottom=(r.centerx, r.bottom - 2)))
        for p in ps:
            if is_black_key(p):
                i = sum(1 for q in whites if q < p)
                r = pygame.Rect(round(rect.x + i * ww - ww * 0.3), rect.y, round(ww * 0.6), int(rect.h * 0.6))
                pygame.draw.rect(s, (24, 24, 28), r, border_radius=2)
                img = f.render(str(fs[p]), True, (240, 240, 240))
                s.blit(img, img.get_rect(midbottom=(r.centerx, r.bottom - 2)))


def _wrap(text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.size(t)[0] > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines
