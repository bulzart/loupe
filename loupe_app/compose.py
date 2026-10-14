"""Loupe compose: draw the admin's arrows/circles on a frame and build the wrong-vs-correct sample image (Pillow)."""
from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (255, 45, 45)
GREEN = (46, 204, 113)
GREEN_DARK = (22, 163, 74)
INK = (24, 26, 32)
PAPER = (245, 245, 247)
MUTED = (120, 124, 134)
_fonts: dict = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _fonts:
        return _fonts[key]
    f = None
    for name in (("arialbd.ttf", "segoeuib.ttf", "calibrib.ttf") if bold else ("arial.ttf", "segoeui.ttf", "calibri.ttf")):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
            try:
                f = ImageFont.truetype(base + name, size)
                break
            except OSError:
                continue
        if f:
            break
    if not f:
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except OSError:
            f = ImageFont.load_default()
    _fonts[key] = f
    return f


def clean_shapes(shapes) -> list[dict]:
    """Validate the admin's shapes (normalised 0..1 coordinates); drop anything malformed."""
    out = []
    for s in (shapes or [])[:40]:
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        side = "fixed" if s.get("side") == "fixed" else "wrong"     # a mark belongs to the wrong frame (red) or the fixed one (green)
        try:
            if t == "arrow":
                out.append({"type": "arrow", "side": side, **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "circle":
                out.append({"type": "circle", "side": side, **{k: float(s[k]) for k in ("cx", "cy", "rx", "ry")}})
            elif t == "rect":
                out.append({"type": "rect", "side": side, **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "text" and str(s.get("text", "")).strip():
                out.append({"type": "text", "side": side, "x": float(s["x"]), "y": float(s["y"]), "text": str(s["text"]).strip()[:80]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _line_w(img_or_h) -> float:
    """The weight of a mark in pixels: thin (about 1/480 of the picture's height), so a mark points at the problem and does not take the eye."""
    h = img_or_h if isinstance(img_or_h, (int, float)) else img_or_h.height
    return max(1.5, h / 480)


def draw_shapes(img: Image.Image, shapes: list[dict], color=RED, lw: float | None = None, box: tuple | None = None, ref_h: float | None = None) -> Image.Image:
    """Draw the marks (normalised coordinates) on a copy of the picture, thin and anti-aliased: a thin white halo first so they read on any
    picture, then the colour.  box = (ax, ay, sx, sy) maps a normalised point to pixels (x = ax + nx * sx) for a picture that shows only part of
    the frame (the zoom); ref_h is the height the weights and the label size are scaled to (the panel's height)."""
    im = img.convert("RGB").copy()
    W, H = im.size
    ax, ay, sx, sy = box or (0.0, 0.0, float(W), float(H))
    ref_h = ref_h or H
    lw = lw or _line_w(ref_h)
    halo = lw + 2.0
    ss = 3 if W * H <= 1_500_000 else 2           # draw large, shrink: smooth edges at a fractional weight
    ov = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    white = (255, 255, 255, 255)
    col = tuple(color) + (255,)

    def px(x, y):
        return (ax + x * sx) * ss, (ay + y * sy) * ss

    def w(v):
        return max(1, round(v * ss))

    texts = []
    for s in shapes:
        t = s["type"]
        if t == "arrow":
            (x1, y1), (x2, y2) = px(s["x1"], s["y1"]), px(s["x2"], s["y2"])
            ang = math.atan2(y2 - y1, x2 - x1)
            hl = max(lw * 7.0, ref_h / 45) * ss
            hw = math.pi / 7
            pts = [(x2, y2), (x2 - hl * math.cos(ang - hw), y2 - hl * math.sin(ang - hw)), (x2 - hl * math.cos(ang + hw), y2 - hl * math.sin(ang + hw))]
            d.line([(x1, y1), (x2 - hl * 0.6 * math.cos(ang), y2 - hl * 0.6 * math.sin(ang))], fill=white, width=w(halo))
            d.polygon(pts, fill=white, outline=white, width=w(1.5))
            d.line([(x1, y1), (x2 - hl * 0.6 * math.cos(ang), y2 - hl * 0.6 * math.sin(ang))], fill=col, width=w(lw))
            d.polygon(pts, fill=col)
        elif t == "circle":
            cx, cy = px(s["cx"], s["cy"])
            rx, ry = abs(s["rx"]) * sx * ss, abs(s["ry"]) * sy * ss
            bb = [cx - rx, cy - ry, cx + rx, cy + ry]
            d.ellipse(bb, outline=white, width=w(halo))
            d.ellipse(bb, outline=col, width=w(lw))
        elif t == "rect":
            (x1, y1), (x2, y2) = px(s["x1"], s["y1"]), px(s["x2"], s["y2"])
            bb = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            d.rectangle(bb, outline=white, width=w(halo))
            d.rectangle(bb, outline=col, width=w(lw))
        elif t == "text":
            texts.append(s)
    small = ov.resize((W, H), Image.LANCZOS)
    im = Image.alpha_composite(im.convert("RGBA"), small).convert("RGB")
    if texts:
        dd = ImageDraw.Draw(im)
        f = font(max(18, round(ref_h / 42)), True)
        for s in texts:
            x, y = ax + s["x"] * sx, ay + s["y"] * sy
            tw = dd.textlength(s["text"], font=f)
            dd.rectangle([x - 5, y - 3, x + tw + 5, y + f.size + 5], fill=(255, 255, 255))
            dd.text((x, y), s["text"], font=f, fill=color)
    return im


def region_norm(shapes: list[dict], pad: float = 0.07):
    """The box (0..1) that holds every shape, padded; None when there are no shapes."""
    xs, ys = [], []
    for s in shapes:
        t = s["type"]
        if t in ("arrow", "rect"):
            xs += [s["x1"], s["x2"]]; ys += [s["y1"], s["y2"]]
        elif t == "circle":
            xs += [s["cx"] - abs(s["rx"]), s["cx"] + abs(s["rx"])]; ys += [s["cy"] - abs(s["ry"]), s["cy"] + abs(s["ry"])]
        elif t == "text":
            xs += [s["x"], s["x"] + 0.25]; ys += [s["y"], s["y"] + 0.04]
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    minw, minh = 0.38, 0.24     # a tiny mark still shows its surroundings
    if x1 - x0 < minw:
        c = (x0 + x1) / 2; x0, x1 = max(0.0, c - minw / 2), min(1.0, c + minw / 2)
    if y1 - y0 < minh:
        c = (y0 + y1) / 2; y0, y1 = max(0.0, c - minh / 2), min(1.0, c + minh / 2)
    return x0, y0, x1, y1


def to_px(box, W: int, H: int):
    return int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)


def _wrap(d, text: str, f, width: int) -> list[str]:
    lines = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = (cur + " " + w).strip()
            if d.textlength(trial, font=f) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _fit(im: Image.Image, w: int, h: int, bg=(18, 18, 22)) -> Image.Image:
    s = min(w / im.width, h / im.height)
    r = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(r, ((w - r.width) // 2, (h - r.height) // 2))
    return canvas


PRODUCT_BG = (206, 209, 215)


def load_product(path, max_side: int = 1400) -> Image.Image:
    """A product photo from the reference folder on a quiet neutral ground (transparent backgrounds become the ground), at a manageable size."""
    im = Image.open(path).convert("RGBA")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    return Image.alpha_composite(Image.new("RGBA", im.size, PRODUCT_BG + (255,)), im).convert("RGB")


def _fmt_t(t) -> str:
    t = max(0.0, float(t or 0))
    m = int(t // 60)
    return f"{m}:{t - m * 60:05.2f}"


def _placeholder(w: int, h: int, lines: list[str]) -> Image.Image:
    """The right-hand panel of a miss that has no right version: a quiet card that says so."""
    im = Image.new("RGB", (w, h), (34, 36, 44))
    d = ImageDraw.Draw(im)
    f1, f2 = font(30, True), font(22)
    y = h // 2 - 70
    for i, ln in enumerate(lines):
        f = f1 if i == 0 else f2
        for part in _wrap(d, ln, f, w - 70):
            d.text(((w - d.textlength(part, font=f)) / 2, y), part, font=f, fill=(235, 235, 240) if i == 0 else (170, 174, 186))
            y += f.size + 10
        y += 8
    return im


def build(wrong: Image.Image, fixed: Image.Image | None, shapes: list[dict], meta: dict, details: str, correct_note: str,
          include_zoom: bool = True, product_samples: list | None = None):
    """(composite, zoom_or_None, annotated_wrong, annotated_right_or_None).

    One image: the miss (WRONG) on the left with the admin's red marks, the same moment in the right version (RIGHT) on the right with the same marks in
    green, a zoom of the marked place under them, and the explanation burned in the bottom centre (title, what is wrong, what the right version shows).
    The marks are exactly what the admin drew on each side: nothing is copied or added; each zoom is cut around its own side's marks (the other side's when it has none).
    fixed=None: the miss has no right version (it stays as a miss example); the right panel is a card that says so, and only the miss is marked and zoomed.
    The marks are drawn after each picture has its final size, thin and at the same weight in the panels and in the zoom."""
    wrong = wrong.convert("RGB")
    has_right = fixed is not None
    fixed = fixed.convert("RGB") if has_right else None
    if not has_right:
        shapes = [dict(s, side="wrong") for s in shapes if s.get("side") != "fixed"]
    sw = [s for s in shapes if s.get("side") != "fixed"]
    sf = [s for s in shapes if s.get("side") == "fixed"]
    annotated = draw_shapes(wrong, sw, RED) if sw else wrong.copy()                      # the full-size files
    annotated_f = (draw_shapes(fixed, sf, GREEN_DARK) if sf else fixed.copy()) if has_right else None
    nbox = region_norm(shapes)           # the place that matters: every mark on both sides

    PW, M = 560, 24
    ph = min(round(PW * wrong.height / wrong.width), 1000)
    pw = round(ph * wrong.width / wrong.height) if ph == 1000 else PW
    pw = max(pw, 200)
    lwp = _line_w(ph)                    # one mark weight for the panels and the zoom
    left = wrong.resize((pw, ph), Image.LANCZOS)
    left = draw_shapes(left, sw, RED, lw=lwp, ref_h=ph) if sw else left
    if has_right:
        rh = round(pw * fixed.height / fixed.width) if abs(fixed.width / fixed.height - wrong.width / wrong.height) > 0.02 else ph
        right = fixed.resize((pw, rh), Image.LANCZOS)
        right = draw_shapes(right, sf, GREEN_DARK, lw=lwp, ref_h=ph) if sf else right
    else:
        rh = ph
        right = _placeholder(pw, ph, ["No right version", "This sample shows the miss only. Nothing in the video was corrected, so there is no right frame to compare with."])
    ph_r = right.height
    W = M * 3 + pw * 2

    zoom, zoom_h = None, 0
    if include_zoom and nbox:
        zh, zw = 520, pw

        def zoomed(src: Image.Image, marks: list[dict], color):
            bx0, by0, bx1, by1 = to_px(region_norm(marks) or nbox, src.width, src.height)       # around this side's own marks, else around the other side's
            cw_, ch_ = max(1, bx1 - bx0), max(1, by1 - by0)
            sc = min(zw / cw_, zh / ch_)
            nw, nh = max(1, round(cw_ * sc)), max(1, round(ch_ * sc))
            ox, oy = (zw - nw) // 2, (zh - nh) // 2
            canvas = Image.new("RGB", (zw, zh), (18, 18, 22))
            canvas.paste(src.crop((bx0, by0, bx1, by1)).resize((nw, nh), Image.LANCZOS), (ox, oy))
            if marks:       # normalised point -> pixel of this zoom: ox + (x * W - bx0) * scale
                canvas = draw_shapes(canvas, marks, color, lw=lwp, box=(ox - bx0 * sc, oy - by0 * sc, src.width * sc, src.height * sc), ref_h=ph)
            return canvas

        zoom = Image.new("RGB", (M * 3 + zw * 2, zh + M), PAPER)
        zoom.paste(zoomed(wrong, sw, RED), (M, 0))
        zoom.paste(zoomed(fixed, sf, GREEN_DARK) if has_right else Image.new("RGB", (zw, zh), (34, 36, 44)), (M * 2 + zw, 0))
        zoom_h = zh + M

    # the burned caption (bottom centre): title, what is wrong here, what the right version shows
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    ps = list(product_samples or [])       # [(image, label)]: the bottom band is then split, the description on the left half, the product sample on the right
    colw = W // 2
    cap_w = (colw - M * 3) if ps else (W - M * 4)
    f_title, f_txt, f_cap = font(30, True), font(25), font(23)
    title = (meta.get("title") or "").strip()
    if title and details.strip().lower().startswith(title.lower().rstrip(". ")):
        title = ""       # the title was cut from the explanation: say it once
    cap = []     # (font, colour, lines)
    if title:
        cap.append((f_title, (255, 255, 255), _wrap(scratch, title, f_title, cap_w)))
    if details.strip():
        cap.append((f_txt, (255, 214, 214), _wrap(scratch, details.strip(), f_txt, cap_w)))
    if correct_note.strip() and has_right:
        cap.append((f_cap, (150, 235, 185), _wrap(scratch, "Right: " + correct_note.strip(), f_cap, cap_w)))
    text_h = 36 + sum(len(ls) * (f.size + 9) + 12 for f, _c, ls in cap) if cap else 0
    cap_h = max(text_h, 300) if ps else text_h        # the product needs some height to be recognised

    head_h = 74
    H = head_h + max(ph, ph_r) + M + zoom_h + cap_h
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    sub = "  ·  ".join(x for x in (meta.get("model"), meta.get("run"), ("video " + str(meta.get("video"))) if meta.get("video") else "", meta.get("sample")) if x)
    d.text((M, 10), sub[:150], font=font(16), fill=MUTED)
    y = head_h
    lw_ = f"WRONG · as generated · {_fmt_t(meta.get('t_wrong'))}"
    lf_ = f"RIGHT · correct version · {_fmt_t(meta.get('t_fixed'))}" if has_right else "NO RIGHT VERSION · miss example only"
    fb = font(17, True)
    d.rectangle([M, y - 32, M + d.textlength(lw_, font=fb) + 20, y - 6], fill=RED)
    d.text((M + 10, y - 29), lw_, font=fb, fill=(255, 255, 255))
    d.rectangle([M * 2 + pw, y - 32, M * 2 + pw + d.textlength(lf_, font=fb) + 20, y - 6], fill=GREEN_DARK if has_right else (96, 100, 112))
    d.text((M * 2 + pw + 10, y - 29), lf_, font=fb, fill=(255, 255, 255))
    img.paste(left, (M, y))
    img.paste(right, (M * 2 + pw, y))
    y += max(ph, ph_r) + M
    if zoom:
        img.paste(zoom, (0, y))
        d.text((M + 6, y + 4), "zoom: marked place", font=font(15, True), fill=(255, 255, 255))
        y += zoom_h
    if cap or ps:
        d.rectangle([0, y, W, H], fill=(18, 18, 22))
        band_top, mid = y, (colw if ps else W)
        y += 24 + max(0, (cap_h - text_h) // 2)
        for f, col, lines in cap:
            for ln in lines:
                d.text(((mid - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=col)
                y += f.size + 9
            y += 12
        if ps:
            d.line([(colw, band_top + 16), (colw, H - 16)], fill=(58, 60, 70), width=1)
            x0, y0, x1, y1 = colw + M, band_top + 16, W - M, H - 16
            d.rectangle([x0, y0, x1, y1], fill=PRODUCT_BG)
            lab_h = 30
            iw_all, ih_all = x1 - x0 - 24, y1 - y0 - 24 - lab_h
            cell = iw_all // len(ps)
            for i, (pim, _lab) in enumerate(ps):
                sc = min(cell / pim.width, ih_all / pim.height)
                nw, nh = max(1, round(pim.width * sc)), max(1, round(pim.height * sc))
                img.paste(pim.resize((nw, nh), Image.LANCZOS), (x0 + 12 + i * cell + (cell - nw) // 2, y0 + 12 + (ih_all - nh) // 2))
            lab = "PRODUCT SAMPLE · " + " / ".join(l for _i, l in ps)
            fl = font(15, True)
            while d.textlength(lab, font=fl) > x1 - x0 - 24 and len(lab) > 24:
                lab = lab[:-4].rstrip() + "…"
            d.text((x0 + 12, y1 - lab_h + 6), lab, font=fl, fill=(40, 42, 50))
    return img, zoom, annotated, annotated_f
