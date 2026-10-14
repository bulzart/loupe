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


def _line_w(img) -> int:
    return max(4, round(img.height / 190))


def draw_shapes(img: Image.Image, shapes: list[dict], color=RED) -> Image.Image:
    """Draw the marks (normalised coordinates) on a copy of the frame: a white halo first so they read on any picture, then the colour."""
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    W, H = im.size
    lw = _line_w(im)

    def px(x, y):
        return x * W, y * H

    for s in shapes:
        t = s["type"]
        if t == "arrow":
            (x1, y1), (x2, y2) = px(s["x1"], s["y1"]), px(s["x2"], s["y2"])
            ang = math.atan2(y2 - y1, x2 - x1)
            hl = lw * 5.5
            hw = math.pi / 7
            pts = [(x2, y2), (x2 - hl * math.cos(ang - hw), y2 - hl * math.sin(ang - hw)),
                   (x2 - hl * math.cos(ang + hw), y2 - hl * math.sin(ang + hw))]
            # white halo first so the mark reads on any picture, then red
            d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255), width=lw + 5)
            d.polygon(pts, fill=(255, 255, 255), outline=(255, 255, 255))
            d.line([(x1, y1), (x2 - hl * 0.6 * math.cos(ang), y2 - hl * 0.6 * math.sin(ang))], fill=color, width=lw)
            d.polygon(pts, fill=color)
        elif t == "circle":
            cx, cy = px(s["cx"], s["cy"])
            rx, ry = abs(s["rx"]) * W, abs(s["ry"]) * H
            box = [cx - rx, cy - ry, cx + rx, cy + ry]
            d.ellipse(box, outline=(255, 255, 255), width=lw + 5)
            d.ellipse(box, outline=color, width=lw)
        elif t == "rect":
            (x1, y1), (x2, y2) = px(s["x1"], s["y1"]), px(s["x2"], s["y2"])
            box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            d.rectangle(box, outline=(255, 255, 255), width=lw + 5)
            d.rectangle(box, outline=color, width=lw)
        elif t == "text":
            x, y = px(s["x"], s["y"])
            f = font(max(22, round(H / 38)), True)
            tw = d.textlength(s["text"], font=f)
            d.rectangle([x - 6, y - 4, x + tw + 6, y + f.size + 6], fill=(255, 255, 255))
            d.text((x, y), s["text"], font=f, fill=color)
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


def _dashed_rect(d: ImageDraw.ImageDraw, box, color, width, dash=18):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(L // dash))
        for i in range(0, n, 2):
            a, b = i / n, min(1.0, (i + 1) / n)
            d.line([(ax + (bx - ax) * a, ay + (by - ay) * a), (ax + (bx - ax) * b, ay + (by - ay) * b)], fill=color, width=width)


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
          include_zoom: bool = True):
    """(composite, zoom_or_None, annotated_wrong, annotated_right_or_None).

    One image: the miss (WRONG) on the left with the admin's red marks, the same moment in the right version (RIGHT) on the right with the same marks in
    green, a zoom of the marked place under them, and the explanation burned in the bottom centre (title, what is wrong, what the right version shows).
    A side with no marks of its own gets a dashed box at the place that was marked on the other side, so there is always a place to compare.
    fixed=None: the miss has no right version (it stays as a miss example); the right panel is a card that says so, and only the miss is marked and zoomed."""
    wrong = wrong.convert("RGB")
    has_right = fixed is not None
    fixed = fixed.convert("RGB") if has_right else None
    if not has_right:
        shapes = [dict(s, side="wrong") for s in shapes if s.get("side") != "fixed"]
    sw = [s for s in shapes if s.get("side") != "fixed"]
    sf = [s for s in shapes if s.get("side") == "fixed"]
    annotated = draw_shapes(wrong, sw, RED) if sw else wrong.copy()
    annotated_f = (draw_shapes(fixed, sf, GREEN_DARK) if sf else fixed.copy()) if has_right else None
    nbox = region_norm(shapes)           # the place that matters: every mark on both sides
    box_w = to_px(nbox, wrong.width, wrong.height) if nbox else None
    box_f = to_px(nbox, fixed.width, fixed.height) if (nbox and has_right) else None
    left_src, right_src = annotated, annotated_f
    if has_right and nbox and not sw:    # only the right frame was marked: show the same place, dashed, on the miss
        left_src = annotated.copy()
        _dashed_rect(ImageDraw.Draw(left_src), box_w, RED, _line_w(left_src))
    if has_right and nbox and not sf:    # only the miss was marked: the same place, dashed green, on the right frame
        right_src = annotated_f.copy()
        _dashed_rect(ImageDraw.Draw(right_src), box_f, GREEN, _line_w(right_src))

    PW, M = 560, 24
    ph = min(round(PW * wrong.height / wrong.width), 1000)
    pw = round(ph * wrong.width / wrong.height) if ph == 1000 else PW
    pw = max(pw, 200)
    left = left_src.resize((pw, ph), Image.LANCZOS)
    if has_right:
        right = right_src.resize((pw, round(pw * fixed.height / fixed.width)) if abs(fixed.width / fixed.height - wrong.width / wrong.height) > 0.02 else (pw, ph), Image.LANCZOS)
    else:
        right = _placeholder(pw, ph, ["No right version", "This sample shows the miss only. Nothing in the video was corrected, so there is no right frame to compare with."])
    ph_r = right.height
    W = M * 3 + pw * 2

    zoom, zoom_h = None, 0
    if include_zoom and nbox:
        zh, zw = 520, pw
        zoom = Image.new("RGB", (M * 3 + zw * 2, zh + M), PAPER)
        zoom.paste(_fit(annotated.crop(box_w), zw, zh), (M, 0))
        if has_right:
            zoom.paste(_fit(annotated_f.crop(box_f), zw, zh), (M * 2 + zw, 0))
        else:
            zoom.paste(Image.new("RGB", (zw, zh), (34, 36, 44)), (M * 2 + zw, 0))
        zoom_h = zh + M

    # the burned caption (bottom centre): title, what is wrong here, what the right version shows
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    cap_w = W - M * 4
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
    cap_h = 36 + sum(len(ls) * (f.size + 9) + 12 for f, _c, ls in cap) if cap else 0

    head_h = 74
    H = head_h + max(ph, ph_r) + M + zoom_h + cap_h
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    sub = "  ·  ".join(x for x in (meta.get("model"), meta.get("run"), ("video " + str(meta.get("video"))) if meta.get("video") else "", meta.get("sample")) if x)
    d.text((M, 10), sub[:150], font=font(16), fill=MUTED)
    y = head_h
    lw_ = f"WRONG · as generated · {_fmt_t(meta.get('t_wrong'))}"
    lf_ = f"RIGHT · {_fmt_t(meta.get('t_fixed'))}" if has_right else "NO RIGHT VERSION · miss example only"
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
    if cap:
        d.rectangle([0, y, W, H], fill=(18, 18, 22))
        y += 24
        for f, col, lines in cap:
            for ln in lines:
                d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=col)
                y += f.size + 9
            y += 12
    return img, zoom, annotated, annotated_f
