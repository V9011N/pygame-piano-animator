"""
skins.py - How the hands look. The animation (hands.py) decides where every
joint is; a skin draws flesh (or a glove, or a machine) around those joints,
seen from above, with a soft shadow on the keys.

Skins (after the references in "Hand Skin References"):
    skeleton   the original bone-and-joint drawing
    cartoon    flat skin tones with a warm outline, knuckle creases, nails,
               and a shirt cuff and sleeve at the wrist
    gloves     white cartoon gloves: bold black outline, stitching on the
               back, a puffy cuff and a thin black arm
    robot      white shell plates with gaps, metal joint cylinders, dark
               fingertip caps, a seamed and screwed palm plate and a black
               wrist cylinder

Every skin has its own colours (see SKINS) and shares these options:
finger thickness, outline width, details (nails, creases, stitching, screws),
cuff/sleeve, and the shadow.
"""
from __future__ import annotations

import math

SKINS = {
    "skeleton": {"label": "Skeleton", "slots": [("bone", "Bones", (12, 12, 14))]},
    "cartoon": {"label": "Cartoon", "slots": [("skin", "Skin", (247, 197, 152)),
                                              ("line", "Outline", (205, 128, 78)),
                                              ("sleeve", "Sleeve", (37, 178, 122)),
                                              ("nail", "Nails", (252, 223, 199))]},
    "gloves": {"label": "White gloves", "slots": [("glove", "Glove", (250, 250, 250)),
                                                  ("line", "Outline", (20, 20, 22)),
                                                  ("arm", "Arm", (20, 20, 22))]},
    "robot": {"label": "Robot", "slots": [("shell", "Shell", (232, 231, 228)),
                                          ("joint", "Joints", (62, 64, 70)),
                                          ("tip", "Fingertips", (58, 58, 62))]},
}
SKIN_ORDER = ["cartoon", "gloves", "robot", "skeleton"]
OPTIONS = {"finger_width": 1.0, "outline": 1.0, "details": True, "sleeve": True, "shadow": True}
FINGER_WIDTH_RANGE = (0.7, 1.35)
OUTLINE_RANGE = (0.0, 2.5)


def default_skin(style="cartoon"):
    d = {"style": style, "colors": {k: {s: list(c) for s, _, c in v["slots"]} for k, v in SKINS.items()}}
    d.update(OPTIONS)
    return d


def normalize(d, bone_color=None):
    """A complete skin settings dict from a possibly partial / older one."""
    out = default_skin()
    if bone_color is not None:
        out["colors"]["skeleton"]["bone"] = list(bone_color)
    if not isinstance(d, dict):
        return out
    if d.get("style") in SKINS:
        out["style"] = d["style"]
    for style, cols in (d.get("colors") or {}).items():
        if style in SKINS and isinstance(cols, dict):
            for slot, c in cols.items():
                if slot in out["colors"][style]:
                    try:
                        out["colors"][style][slot] = [max(0, min(255, int(v))) for v in c][:3]
                    except Exception:
                        pass
    for k, v in OPTIONS.items():
        if k in d:
            out[k] = type(v)(d[k]) if not isinstance(v, bool) else bool(d[k])
    out["finger_width"] = max(FINGER_WIDTH_RANGE[0], min(FINGER_WIDTH_RANGE[1], out["finger_width"]))
    out["outline"] = max(OUTLINE_RANGE[0], min(OUTLINE_RANGE[1], out["outline"]))
    return out


def primary_color(skin):
    style = skin.get("style", "cartoon")
    slot = SKINS[style]["slots"][0][0]
    return tuple(skin["colors"][style][slot])


# --------------------------------------------------------------------------- #
# Geometry helpers (screen space: x right, y down; z = height in pixels)
# --------------------------------------------------------------------------- #
FINGER_W_IN = {1: 0.82, 2: 0.70, 3: 0.72, 4: 0.68, 5: 0.60}      # proximal phalanx widths
SEG_TAPER = (1.0, 0.9, 0.84)                                     # proximal, middle, distal


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _depth(z, ppi):
    return max(0.85, min(1.45, 1.0 + 0.25 * z / ppi))


def _unit(dx, dy):
    L = math.hypot(dx, dy)
    return (dx / L, dy / L) if L > 1e-9 else (0.0, -1.0)


def _chaikin(pts, n=2):
    for _ in range(n):
        out = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        pts = out
    return pts


def _hull(pts):
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inflate(poly, d):
    """Push a convex polygon's points outward from its centroid by d pixels."""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    out = []
    for x, y in poly:
        ux, uy = _unit(x - cx, y - cy)
        out.append((x + ux * d, y + uy * d))
    return out


class _Pen:
    """Anti-aliased filled shapes where pygame.gfxdraw is available."""

    def __init__(self, surf):
        import pygame
        self.pg = pygame
        self.surf = surf
        try:
            import pygame.gfxdraw as gfx
            self.gfx = gfx
        except Exception:
            self.gfx = None

    def circle(self, c, r, color):
        x, y, r = int(round(c[0])), int(round(c[1])), max(1, int(round(r)))
        if self.gfx:
            self.gfx.filled_circle(self.surf, x, y, r, color)
            self.gfx.aacircle(self.surf, x, y, r, color)
        else:
            self.pg.draw.circle(self.surf, color, (x, y), r)

    def poly(self, pts, color):
        pts = [(int(round(x)), int(round(y))) for x, y in pts]
        if len(pts) < 3:
            return
        if self.gfx:
            self.gfx.filled_polygon(self.surf, pts, color)
            self.gfx.aapolygon(self.surf, pts, color)
        else:
            self.pg.draw.polygon(self.surf, color, pts)

    def capsule(self, a, b, r, color, r2=None):
        """A rounded segment from a to b, radius r at a and r2 at b."""
        r2 = r if r2 is None else r2
        ux, uy = _unit(b[0] - a[0], b[1] - a[1])
        nx, ny = -uy, ux
        self.poly([(a[0] + nx * r, a[1] + ny * r), (b[0] + nx * r2, b[1] + ny * r2),
                   (b[0] - nx * r2, b[1] - ny * r2), (a[0] - nx * r, a[1] - ny * r)], color)
        self.circle(a, r, color)
        self.circle(b, r2, color)

    def line(self, a, b, color, w=1):
        w = max(1, int(round(w)))
        if w == 1 and self.gfx:
            self.pg.draw.aaline(self.surf, color, a, b)
        else:
            self.pg.draw.line(self.surf, color, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), w)
            if w >= 3:
                self.circle(a, w / 2, color)
                self.circle(b, w / 2, color)

    def band(self, c, u, half_len, half_w, color):
        """A rectangle centred on c, long side along u (unit vector)."""
        nx, ny = -u[1], u[0]
        pts = []
        for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pts.append((c[0] + u[0] * half_len * s + nx * half_w * t,
                        c[1] + u[1] * half_len * s + ny * half_w * t))
        self.poly(pts, color)


# --------------------------------------------------------------------------- #
# A hand in screen space
# --------------------------------------------------------------------------- #
class _Hand:
    def __init__(self, struct, project, ppi, skin):
        self.ppi = ppi
        self.skin = skin
        self.style = skin["style"]
        self.col = {k: tuple(v) for k, v in skin["colors"][self.style].items()}
        P = project
        self.chains = {f: [P(p) for p in c] for f, c in struct["chains"].items()}
        self.wr, self.wu = (P(p) for p in struct["wrist"])
        self.arm_end = P(struct["arm_end"])
        fw = skin["finger_width"]
        # segment radii per finger: (thumb: metacarpal, proximal, distal; others: prox, mid, dist)
        self.radius = {}
        for f, c in self.chains.items():
            base = FINGER_W_IN[f] * fw * ppi / 2
            if f == 1:
                rs = [base * 1.22, base * 1.0, base * 0.9]
                segs = list(zip(c, c[1:]))
            else:
                rs = [base * t for t in SEG_TAPER]
                segs = list(zip(c[1:], c[2:]))
            self.radius[f] = [(a, b, r * _depth((a[2] + b[2]) / 2, ppi)) for (a, b), r in zip(segs, rs)]
        self.lw = skin["outline"] * 0.035 * ppi
        wx, wy = (self.wr[0] + self.wu[0]) / 2, (self.wr[1] + self.wu[1]) / 2
        self.wc = (wx, wy, (self.wr[2] + self.wu[2]) / 2)
        self.arm_u = _unit(self.arm_end[0] - wx, self.arm_end[1] - wy)
        self.wrist_w = math.hypot(self.wu[0] - self.wr[0], self.wu[1] - self.wr[1])
        pts = [self.wr, self.wu, self.chains[1][0]] + [self.chains[f][1] for f in range(2, 6)] + \
              [self.chains[f][0] for f in range(2, 6)]
        self.palm_z = sum(p[2] for p in pts) / len(pts)
        # The palm must hold the whole width of every finger at its knuckle, so
        # the index and little fingers don't hang off its corners: each knuckle
        # (and the little finger's metacarpal down to the wrist) is widened
        # sideways by that finger's radius, across its metacarpal.
        flat = [(p[0], p[1]) for p in pts]
        for f in range(2, 6):
            base, mcp = self.chains[f][0], self.chains[f][1]
            ux, uy = _unit(mcp[0] - base[0], mcp[1] - base[1])
            nx, ny = -uy, ux
            r = self.radius[f][0][2] * (1.12 if f in (2, 5) else 1.0)
            flat += [(mcp[0] + nx * r, mcp[1] + ny * r), (mcp[0] - nx * r, mcp[1] - ny * r)]
            if f in (2, 5):
                # carry the edge of the hand down toward the wrist
                for t in (0.35, 0.7):
                    q = (mcp[0] + (base[0] - mcp[0]) * t, mcp[1] + (base[1] - mcp[1]) * t)
                    flat += [(q[0] + nx * r * 0.9, q[1] + ny * r * 0.9), (q[0] - nx * r * 0.9, q[1] - ny * r * 0.9)]
        hull = _hull(flat)
        self.palm = _chaikin(_inflate(hull, 0.08 * fw * ppi), 2) if len(hull) >= 3 else []
        self._make_web()

    def _make_web(self):
        """
        The first web space (thenar web): the flesh joining the thumb to the
        index finger. Its free edge runs from partway up the thumb's
        proximal phalanx to the thumb side of the index knuckle, sagging
        toward the wrist in a curve - deeper when the thumb is close to the
        hand, stretched almost straight when it is spread wide.
        """
        th, ix = self.chains[1], self.chains[2]
        cmc, t_mcp, t_ip = th[0], th[1], th[2]
        base2, mcp2 = ix[0], ix[1]
        r_t = self.radius[1][1][2]
        r_i = self.radius[2][0][2]
        # the index side: offset from the metacarpal toward the thumb
        ux, uy = _unit(mcp2[0] - base2[0], mcp2[1] - base2[1])
        nx, ny = -uy, ux
        if (cmc[0] - mcp2[0]) * nx + (cmc[1] - mcp2[1]) * ny < 0:
            nx, ny = -nx, -ny
        k_out = (mcp2[0] + nx * r_i * 0.9, mcp2[1] + ny * r_i * 0.9)
        k_mid = (mcp2[0] + (base2[0] - mcp2[0]) * 0.45 + nx * r_i * 0.8,
                 mcp2[1] + (base2[1] - mcp2[1]) * 0.45 + ny * r_i * 0.8)
        # the thumb side: partway up the proximal phalanx, on the index side of it
        a = (t_mcp[0] + (t_ip[0] - t_mcp[0]) * 0.35, t_mcp[1] + (t_ip[1] - t_mcp[1]) * 0.35)
        tx, ty = _unit(t_ip[0] - t_mcp[0], t_ip[1] - t_mcp[1])
        mx, my = -ty, tx
        if (mcp2[0] - a[0]) * mx + (mcp2[1] - a[1]) * my < 0:
            mx, my = -mx, -my
        t_att = (a[0] + mx * r_t * 0.7, a[1] + my * r_t * 0.7)
        t_root = (t_mcp[0] + mx * r_t * 0.8, t_mcp[1] + my * r_t * 0.8)
        # the free edge: a curve sagging toward the wrist
        span = math.hypot(k_out[0] - t_att[0], k_out[1] - t_att[1])
        reach = sum(math.hypot(q[0] - p_[0], q[1] - p_[1]) for p_, q in zip(th, th[1:]))
        spread = min(1.0, span / max(1e-6, 0.55 * reach))      # 0 = thumb at the hand, 1 = spread
        mid = ((t_att[0] + k_out[0]) / 2, (t_att[1] + k_out[1]) / 2)
        sag = span * (0.32 - 0.2 * spread)
        dx, dy = _unit(self.wc[0] - mid[0], self.wc[1] - mid[1])
        ctrl = (mid[0] + dx * sag * 2, mid[1] + dy * sag * 2)
        edge = []
        for i in range(11):
            t = i / 10
            edge.append(((1 - t) ** 2 * t_att[0] + 2 * (1 - t) * t * ctrl[0] + t * t * k_out[0],
                         (1 - t) ** 2 * t_att[1] + 2 * (1 - t) * t * ctrl[1] + t * t * k_out[1]))
        self.web_edge = edge
        self.web = [(cmc[0], cmc[1]), (t_root[0], t_root[1])] + edge + [k_mid, (base2[0], base2[1])]

    def along(self, d):
        """Point d pixels from the wrist centre, back along the forearm."""
        return (self.wc[0] + self.arm_u[0] * d, self.wc[1] + self.arm_u[1] * d)

    def parts(self):
        """[(z, kind, finger)] in drawing order (lowest first)."""
        items = [(-1e9, "arm", None), (self.palm_z, "palm", None)]
        for f, segs in self.radius.items():
            z = sum((a[2] + b[2]) / 2 for a, b, _ in segs) / len(segs)
            items.append((z + (0.0 if f == 1 else 1.0), "finger", f))
        return sorted(items, key=lambda it: it[0])

    # ----- silhouettes (outline / fill / shadow) ------------------------------------------
    def fill_finger(self, pen, f, color, grow=0.0):
        for a, b, r in self.radius[f]:
            pen.capsule(a, b, r + grow, color)

    def fill_palm(self, pen, color, grow=0.0, web_color=None):
        """The palm and the thumb web; `grow` widens both by that many pixels (outlines)."""
        if self.web:
            wc = web_color or color
            pen.poly(self.web, wc)
            if grow:
                for p, q in zip(self.web_edge, self.web_edge[1:]):
                    pen.line(p, q, wc, 2 * grow)
        if self.palm:
            pen.poly(_inflate(self.palm, grow) if grow else self.palm, color)

    def shadow(self, pen, color):
        def off(p):
            return (p[0] + 0.28 * p[2], p[1] + 0.42 * p[2], p[2])
        if self.palm:
            dz = self.palm_z
            pen.poly([(x + 0.28 * dz, y + 0.42 * dz) for x, y in self.palm], color)
            pen.poly([(x + 0.28 * dz, y + 0.42 * dz) for x, y in self.web], color)
        for f in self.radius:
            for a, b, r in self.radius[f]:
                pen.capsule(off(a), off(b), r, color)


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
def _draw_cartoon(pen, h):
    c, skin = h.col, h.skin
    line = c["line"]
    lw = max(1.0, h.lw)
    parts = h.parts()
    # arm, cuff and sleeve
    arm_len = math.hypot(h.arm_end[0] - h.wc[0], h.arm_end[1] - h.wc[1])
    hw = h.wrist_w * 0.55
    cuff0 = 1.0 * h.ppi
    end = h.along(arm_len)
    if skin["sleeve"]:
        pen.capsule(h.along(0), h.along(cuff0 + 4), hw + lw, line)
        pen.capsule(h.along(0), h.along(cuff0 + 4), hw, c["skin"])
        sl0, sl1 = cuff0, cuff0 + 0.42 * h.ppi
        u = h.arm_u
        mid = h.along((sl0 + sl1) / 2)
        pen.band(mid, u, (sl1 - sl0) / 2 + lw, hw * 1.28 + lw, _mix(line, (60, 60, 60), 0.4))
        pen.band(mid, u, (sl1 - sl0) / 2, hw * 1.28, (242, 242, 238))
        s_mid = h.along((sl1 + arm_len) / 2)
        pen.band(s_mid, u, (arm_len - sl1) / 2, hw * 1.34 + lw, _mix(c["sleeve"], (0, 0, 0), 0.35))
        pen.band(s_mid, u, (arm_len - sl1) / 2, hw * 1.34, c["sleeve"])
        # cuff button
        nx, ny = -u[1], u[0]
        b = (mid[0] + nx * hw * 0.8, mid[1] + ny * hw * 0.8)
        pen.circle(b, max(1.5, 0.05 * h.ppi), (90, 80, 76))
    else:
        pen.capsule(h.along(0), end, hw + lw, line)
        pen.capsule(h.along(0), end, hw, c["skin"])
    # hand: outline pass, then fill pass, so the silhouette is one shape
    for _, kind, f in parts:
        if kind == "palm":
            h.fill_palm(pen, line, lw)
        elif kind == "finger":
            h.fill_finger(pen, f, line, lw)
    for _, kind, f in parts:
        if kind == "palm":
            h.fill_palm(pen, c["skin"])
            if skin["details"]:
                _web_crease(pen, h, _mix(line, c["skin"], 0.35))
        elif kind == "finger":
            h.fill_finger(pen, f, c["skin"])
            if skin["details"]:
                _creases(pen, h, f, _mix(line, c["skin"], 0.25))
                _nail(pen, h, f, c["nail"], _mix(line, c["nail"], 0.3))


def _web_crease(pen, h, color):
    """The short fold line where the thumb web meets the back of the hand."""
    e = h.web_edge
    if len(e) < 5:
        return
    p = e[len(e) // 2]
    d = (h.wc[0] - p[0], h.wc[1] - p[1])
    L = math.hypot(*d) or 1
    a = (p[0] + d[0] / L * 0.12 * h.ppi, p[1] + d[1] / L * 0.12 * h.ppi)
    b = (p[0] + d[0] / L * 0.45 * h.ppi, p[1] + d[1] / L * 0.45 * h.ppi)
    pen.line(a, b, color, max(1, 0.03 * h.ppi))


def _creases(pen, h, f, color):
    segs = h.radius[f]
    joints = [(segs[i][1], segs[i][2], segs[i][0]) for i in range(len(segs) - 1)]
    for p, r, prev in joints[-2:]:
        ux, uy = _unit(p[0] - prev[0], p[1] - prev[1])
        nx, ny = -uy, ux
        for off in (-0.12, 0.12):
            q = (p[0] + ux * r * off, p[1] + uy * r * off)
            pen.line((q[0] + nx * r * 0.45, q[1] + ny * r * 0.45),
                     (q[0] - nx * r * 0.45, q[1] - ny * r * 0.45), color, max(1, r * 0.12))


def _nail(pen, h, f, fill, edge):
    """
    The nail on the back of the distal phalanx, seen from above. As the
    fingertip curls down, the phalanx turns away from the viewer: the nail
    is foreshortened along the finger and slides out toward the end of the
    finger (it sits on the top of the phalanx, which now faces forward), so
    the skin in front of it shrinks away until the squashed nail fills the
    rounded end of the finger.
    """
    segs = h.radius[f]
    a, b, r = segs[-1]
    pa, pb, pr = segs[-2]
    dx, dy = b[0] - a[0], b[1] - a[1]
    drop = a[2] - b[2]
    L = math.hypot(math.hypot(dx, dy), drop)             # true length of the phalanx
    if L < 3:
        return
    # the finger's forward direction on screen: the phalanx's own, or the one
    # before it once it is seen nearly end-on (a curled tip can even tuck back)
    px, py = _unit(pb[0] - pa[0], pb[1] - pa[1])
    fwd = dx * px + dy * py
    w = max(0.0, min(1.0, (fwd / L - 0.2) / 0.3))
    dux, duy = _unit(dx, dy)
    ux, uy = _unit(px + (dux - px) * w, py + (duy - py) * w)
    nx, ny = -uy, ux
    # pitch of the phalanx below the horizontal, capped at straight down
    cos = max(0.0, fwd / L)
    sin = max(0.0, drop / L) if fwd > 0 else 1.0
    # where the finger's outline ends (from b, along u): the tip, or the last
    # knuckle when the tip is tucked under it; the rounded end is that circle
    xa = (a[0] - b[0]) * ux + (a[1] - b[1]) * uy
    xc, rc = (0.0, r) if r >= xa + pr else (xa, pr)
    # along the phalanx (from b) the nail runs s0..s1 on the back of the finger:
    # flat, it sits short of the tip; pitched down, its free edge moves out to
    # the end of the outline and its length is foreshortened. It is a curved
    # shell, so even seen nearly end-on it keeps some depth.
    s0, s1 = -0.5 * L - 0.35 * r, 0.12 * r
    x1 = s1 * cos + (xc + rc - 0.08 * r) * sin
    x0 = x1 - max((s1 - s0) * cos, 0.7 * r * sin)
    mid, hl = (x0 + x1) / 2, (x1 - x0) / 2

    def shape(grow):
        pts = []
        for i in range(32):
            t = 2 * math.pi * i / 32
            c, s = math.cos(t), math.sin(t)
            x = mid + (hl + grow * max(cos, 0.35)) * math.copysign(abs(c) ** 0.7, c)
            y = (0.5 * r + grow) * math.copysign(abs(s) ** 0.7, s)
            if x > xc:                                   # stay inside the rounded end
                y = math.copysign(min(abs(y), 0.94 * math.sqrt(max(0.0, rc * rc - (x - xc) ** 2))), y)
            pts.append((b[0] + ux * x + nx * y, b[1] + uy * x + ny * y))
        return pts
    pen.poly(shape(0.12 * r), edge)
    pen.poly(shape(0.0), fill)


def _draw_gloves(pen, h):
    c, skin = h.col, h.skin
    line = c["line"]
    lw = max(1.5, h.lw * 2.6)
    halo = _mix(c["glove"], (120, 120, 130), 0.35)         # so the outline reads on the dark floor too
    parts = h.parts()
    arm_len = math.hypot(h.arm_end[0] - h.wc[0], h.arm_end[1] - h.wc[1])
    u = h.arm_u
    # thin arm
    ar = 0.2 * h.ppi * skin["finger_width"]
    pen.capsule(h.along(0.3 * h.ppi), h.along(arm_len), ar + 1, halo)
    pen.capsule(h.along(0.3 * h.ppi), h.along(arm_len), ar, c["arm"])
    shade = _mix(c["glove"], (0, 0, 0), 0.13)
    for _, kind, f in parts:
        if kind == "palm":
            h.fill_palm(pen, halo, lw + 1)
        elif kind == "finger":
            h.fill_finger(pen, f, halo, lw + 1)
    for _, kind, f in parts:
        if kind == "palm":
            h.fill_palm(pen, line, lw)
        elif kind == "finger":
            h.fill_finger(pen, f, line, lw)
    for _, kind, f in parts:
        if kind == "palm":
            h.fill_palm(pen, shade)
            if h.web:
                wx_ = sum(p[0] for p in h.web) / len(h.web)
                wy_ = sum(p[1] for p in h.web) / len(h.web)
                pen.poly([(x + (wx_ - x) * 0.12 - 0.04 * h.ppi, y + (wy_ - y) * 0.12 - 0.04 * h.ppi)
                          for x, y in h.web], c["glove"])
            if h.palm:
                cx = sum(p[0] for p in h.palm) / len(h.palm)
                cy = sum(p[1] for p in h.palm) / len(h.palm)
                pen.poly([(x + (cx - x) * 0.06 - 0.05 * h.ppi, y + (cy - y) * 0.06 - 0.05 * h.ppi)
                          for x, y in h.palm], c["glove"])
        elif kind == "finger":
            h.fill_finger(pen, f, shade)
            for a, b, r in h.radius[f]:
                o = 0.22 * r
                pen.capsule((a[0] - o, a[1] - o), (b[0] - o, b[1] - o), r * 0.8, c["glove"])
    if skin["details"]:
        # the three stitches on the back of the glove
        k = [h.chains[f][1] for f in range(2, 6)]
        st = _mix(line, c["glove"], 0.35)
        for i in range(3):
            m = ((k[i][0] + k[i + 1][0]) / 2, (k[i][1] + k[i + 1][1]) / 2)
            w = (m[0] + (h.wc[0] - m[0]) * 0.55, m[1] + (h.wc[1] - m[1]) * 0.55)
            m2 = (m[0] + (h.wc[0] - m[0]) * 0.12, m[1] + (h.wc[1] - m[1]) * 0.12)
            pen.line(m2, w, st, max(1.5, 0.045 * h.ppi))
        for _, kind, f in parts:
            if kind == "finger":
                _creases(pen, h, f, _mix(line, c["glove"], 0.45))
    if skin["sleeve"]:
        # puffy cuff across the wrist
        nx, ny = -u[1], u[0]
        cc = h.along(0.22 * h.ppi)
        half = h.wrist_w * 0.62
        a = (cc[0] + nx * half, cc[1] + ny * half)
        b = (cc[0] - nx * half, cc[1] - ny * half)
        r = 0.26 * h.ppi
        pen.capsule(a, b, r + lw + 1, halo)
        pen.capsule(a, b, r + lw, line)
        pen.capsule(a, b, r, shade)
        pen.capsule((a[0] - 0.04 * h.ppi, a[1] - 0.04 * h.ppi), (b[0] - 0.04 * h.ppi, b[1] - 0.04 * h.ppi),
                    r * 0.78, c["glove"])


def _draw_robot(pen, h):
    c, skin = h.col, h.skin
    shell, joint, tip = c["shell"], c["joint"], c["tip"]
    edge = _mix(shell, (0, 0, 0), 0.45)
    gloss = _mix(shell, (255, 255, 255), 0.7)
    chrome = _mix(joint, (255, 255, 255), 0.55)
    lw = max(1.0, h.lw * 0.8)
    arm_len = math.hypot(h.arm_end[0] - h.wc[0], h.arm_end[1] - h.wc[1])
    hw = h.wrist_w * 0.52
    u = h.arm_u
    # black cylinder, then the white wrist shell
    cyl0 = 1.45 * h.ppi
    mid = h.along((cyl0 + arm_len) / 2)
    pen.band(mid, u, (arm_len - cyl0) / 2, hw * 0.95, (22, 22, 24))
    nx, ny = -u[1], u[0]
    pen.line((mid[0] - nx * hw * 0.5 - u[0] * (arm_len - cyl0) / 2, mid[1] - ny * hw * 0.5 - u[1] * (arm_len - cyl0) / 2),
             (mid[0] - nx * hw * 0.5 + u[0] * (arm_len - cyl0) / 2, mid[1] - ny * hw * 0.5 + u[1] * (arm_len - cyl0) / 2),
             (70, 70, 76), max(1, 0.05 * h.ppi))
    pen.capsule(h.along(0), h.along(cyl0), hw + lw, edge)
    pen.capsule(h.along(0), h.along(cyl0), hw, shell)
    if skin["details"]:
        a = h.along(cyl0 * 0.62)
        pen.line((a[0] + nx * hw, a[1] + ny * hw), (a[0] - nx * hw, a[1] - ny * hw), edge, 1)
    for _, kind, f in h.parts():
        if kind == "palm":
            membrane = _mix(joint, (0, 0, 0), 0.3)
            h.fill_palm(pen, edge, lw, web_color=_mix(joint, (0, 0, 0), 0.55))
            h.fill_palm(pen, shell, web_color=membrane)
            if skin["details"] and h.web_edge:
                # a soft sheen along the membrane's edge
                sheen = _mix(joint, (255, 255, 255), 0.25)
                e = h.web_edge
                for p, q in zip(e[2:-3], e[3:-2]):
                    d = (h.wc[0] - p[0], h.wc[1] - p[1])
                    L = math.hypot(*d) or 1
                    o = 0.06 * h.ppi
                    pen.line((p[0] + d[0] / L * o, p[1] + d[1] / L * o), (q[0] + d[0] / L * o, q[1] + d[1] / L * o),
                             sheen, 1)
            # redraw the palm plate over the membrane's root
            pen.poly(h.palm, shell) if h.palm else None
            if skin["details"] and h.palm:
                _palm_seams(pen, h, edge)
        elif kind == "finger":
            segs = h.radius[f]
            if f == 1:
                # the thumb's base: a dark glossy housing, as on the reference
                a, b, r = segs[0]
                pen.capsule(a, b, r * 0.9 + lw, _mix(joint, (0, 0, 0), 0.5))
                pen.capsule(a, b, r * 0.9, _mix(joint, (0, 0, 0), 0.25))
                pen.line((a[0] - r * 0.3, a[1] - r * 0.3), (b[0] - r * 0.3, b[1] - r * 0.3),
                         _mix(joint, (255, 255, 255), 0.35), max(1, r * 0.15))
                segs = segs[1:]
            # joints first: metal cylinders at every knuckle
            for a, b, r in segs:
                for p in (a,):
                    pen.circle(p, r * 0.78, _mix(joint, (0, 0, 0), 0.35))
                    pen.circle(p, r * 0.62, joint)
                    pen.circle((p[0] - r * 0.18, p[1] - r * 0.18), r * 0.26, chrome)
                    pen.circle(p, max(1, r * 0.12), _mix(joint, (0, 0, 0), 0.5))
            gap = 0.075 * h.ppi
            for i, (a, b, r) in enumerate(segs):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                ux, uy = _unit(b[0] - a[0], b[1] - a[1])
                g0 = min(gap + r * 0.5, L * 0.35)
                g1 = min(gap + r * 0.5, L * 0.35) if i < len(segs) - 1 else 0.0
                p0 = (a[0] + ux * g0, a[1] + uy * g0)
                p1 = (b[0] - ux * g1, b[1] - uy * g1)
                rr = r * (0.95 if i < len(segs) - 1 else 0.9)
                pen.capsule(p0, p1, rr + lw, edge)
                pen.capsule(p0, p1, rr, shell)
                nx2, ny2 = -uy, ux
                o = rr * 0.45
                last = i == len(segs) - 1
                g1 = (p0[0] + (p1[0] - p0[0]) * (0.4 if last else 1.0),
                      p0[1] + (p1[1] - p0[1]) * (0.4 if last else 1.0))
                # gloss along the lit side
                pen.line((p0[0] - nx2 * o, p0[1] - ny2 * o), (g1[0] - nx2 * o, g1[1] - ny2 * o), gloss,
                         max(1, rr * 0.18))
                if last:
                    t0 = (p0[0] + (p1[0] - p0[0]) * 0.45, p0[1] + (p1[1] - p0[1]) * 0.45)
                    pen.capsule(t0, p1, rr * 0.98, tip)
                    pen.circle((p1[0] - nx2 * rr * 0.35 - ux * rr * 0.3, p1[1] - ny2 * rr * 0.35 - uy * rr * 0.3),
                               max(1, rr * 0.2), _mix(tip, (255, 255, 255), 0.3))


def _palm_seams(pen, h, edge):
    k2, k5 = h.chains[2][1], h.chains[5][1]
    cm = h.chains[1][0]
    wc = h.wc
    a = (cm[0] + (k2[0] - cm[0]) * 0.55, cm[1] + (k2[1] - cm[1]) * 0.55)
    side = (h.wu[0] + (k5[0] - h.wu[0]) * 0.55, h.wu[1] + (k5[1] - h.wu[1]) * 0.55)
    ctrl = ((a[0] + side[0]) / 2 + (wc[0] - (a[0] + side[0]) / 2) * 0.35,
            (a[1] + side[1]) / 2 + (wc[1] - (a[1] + side[1]) / 2) * 0.35)
    pts = []
    for i in range(13):
        t = i / 12
        pts.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * ctrl[0] + t * t * side[0],
                    (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * ctrl[1] + t * t * side[1]))
    for p, q in zip(pts, pts[1:]):
        pen.line(p, q, edge, max(1, 0.03 * h.ppi))
    r = max(1.2, 0.045 * h.ppi)
    for t in (0.25, 0.5):
        s = (side[0] + (h.wu[0] - side[0]) * t, side[1] + (h.wu[1] - side[1]) * t)
        s = (s[0] + (wc[0] - s[0]) * 0.18, s[1] + (wc[1] - s[1]) * 0.18)
        pen.circle(s, r, _mix(edge, (255, 255, 255), 0.3))


_STYLES = {"cartoon": _draw_cartoon, "gloves": _draw_gloves, "robot": _draw_robot}
_shadow_cache = {}


def _shadow_surface(size):
    import pygame
    s = _shadow_cache.get(size)
    if s is None:
        _shadow_cache.clear()
        s = _shadow_cache[size] = pygame.Surface(size, pygame.SRCALPHA)
    s.fill((0, 0, 0, 0))
    return s


def draw_skinned(surf, items):
    """
    items: [(struct, project, ppi, skin)] - project maps a struct point to
    (screen x, screen y, height in pixels). Shadows first (all hands), then
    each hand, lowest first.
    """
    import pygame
    hands = []
    for it in items:
        h = _Hand(*it[:4])
        h.layer = it[4] if len(it) > 4 else 0
        hands.append(h)
    pen = _Pen(surf)
    # layer by layer (a hand crossing over the other is a layer above it, and
    # casts its shadow onto it): shadows first, then the hands, lowest first
    for layer in sorted({h.layer for h in hands}):
        group = [h for h in hands if h.layer == layer]
        if any(h.skin["shadow"] for h in group):
            sh = _shadow_surface(surf.get_size())
            spen = _Pen(sh)
            for h in group:
                if h.skin["shadow"]:
                    h.shadow(spen, (0, 0, 0, 70))
            clip = surf.get_clip()
            surf.blit(sh, (0, 0))
            surf.set_clip(clip)
        for h in sorted(group, key=lambda h: h.palm_z):
            _STYLES[h.style](pen, h)


def draw_webs(surf, items):
    """
    The skeleton skin's thumb web: a faint translucent membrane between the
    thumb and index bones, with a slightly stronger free edge. Drawn before
    the bones. items: [(struct, project, ppi, bone colour)].
    """
    if not items:
        return
    geo_skin = normalize({"style": "cartoon"})
    sh = _shadow_surface(surf.get_size())
    pen = _Pen(sh)
    for st, pr, ppi, color in items:
        try:
            h = _Hand(st, pr, ppi, geo_skin)
        except (KeyError, IndexError, TypeError):
            continue
        if not h.web:
            continue
        c = tuple(color[:3])
        if 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2] < 90:      # dark bones: a pale membrane reads better
            c = _mix(c, (235, 235, 240), 0.7)
        pen.poly(h.web, c + (48,))
        for p_, q in zip(h.web_edge, h.web_edge[1:]):
            pen.line(p_, q, c + (130,), max(1.0, 0.018 * ppi))
    clip = surf.get_clip()
    surf.blit(sh, (0, 0))
    surf.set_clip(clip)


def draw_poses(surf, poses):
    """Animated poses from HandAnimator.pose (skins other than 'skeleton')."""
    items = []
    for p in poses:
        fy = p["front_y"]
        items.append((p["struct"], (lambda q, fy=fy: (q[0], fy - q[1], q[2])), p["ppi"], p["skin"],
                      p.get("layer", 0)))
    draw_skinned(surf, items)
