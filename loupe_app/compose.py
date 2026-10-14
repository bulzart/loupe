"""Loupe compose: draw the admin's arrows/circles on a frame and build the wrong-vs-correct sample image (Pillow)."""
from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (255, 45, 45)
GREEN = (46, 204, 113)
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
        try:
            if t == "arrow":
                out.append({"type": "arrow", **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "circle":
                out.append({"type": "circle", **{k: float(s[k]) for k in ("cx", "cy", "rx", "ry")}})
            elif t == "rect":
                out.append({"type": "rect", **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "text" and str(s.get("text", "")).strip():
                out.append({"type": "text", "x": float(s["x"]), "y": float(s["y"]), "text": str(s["text"]).strip()[:80]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _line_w(img) -> int:
    return max(4, round(img.height / 190))


def draw_shapes(img: Image.Image, shapes: list[dict]) -> Image.Image:
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
            d.line([(x1, y1), (x2 - hl * 0.6 * math.cos(ang), y2 - hl * 0.6 * math.sin(ang))], fill=RED, width=lw)
            d.polygon(pts, fill=RED)
        elif t == "circle":
            cx, cy = px(s["cx"], s["cy"])
            rx, ry = abs(s["rx"]) * W, abs(s["ry"]) * H
            box = [cx - rx, cy - ry, cx + rx, cy + ry]
            d.ellipse(box, outline=(255, 255, 255), width=lw + 5)
            d.ellipse(box, outline=RED, width=lw)
        elif t == "rect":
            (x1, y1), (x2, y2) = px(s["x1"], s["y1"]), px(s["x2"], s["y2"])
            box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            d.rectangle(box, outline=(255, 255, 255), width=lw + 5)
            d.rectangle(box, outline=RED, width=lw)
        elif t == "text":
            x, y = px(s["x"], s["y"])
            f = font(max(22, round(H / 38)), True)
            tw = d.textlength(s["text"], font=f)
            d.rectangle([x - 6, y - 4, x + tw + 6, y + f.size + 6], fill=(255, 255, 255))
            d.text((x, y), s["text"], font=f, fill=RED)
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


def build(wrong: Image.Image, fixed: Image.Image, shapes: list[dict], meta: dict, details: str, correct_note: str,
          include_zoom: bool = True):
    """(composite, zoom_or_None, annotated_wrong).  Left: the miss with the admin's marks; right: the same moment, correct."""
    wrong = wrong.convert("RGB")
    fixed = fixed.convert("RGB")
    annotated = draw_shapes(wrong, shapes) if shapes else wrong.copy()
    PW = 560
    M = 24
    ph = round(PW * wrong.height / wrong.width)
    ph = min(ph, 1000)
    pw = round(ph * wrong.width / wrong.height) if ph == 1000 else PW
    pw = max(pw, 200)
    fixed_p = fixed
    nbox = region_norm(shapes)
    box = to_px(nbox, wrong.width, wrong.height) if nbox else None
    fbox = to_px(nbox, fixed.width, fixed.height) if nbox else None
    if nbox:   # the same region, dashed green, on the correct frame: where to compare
        fixed_p = fixed.copy()
        _dashed_rect(ImageDraw.Draw(fixed_p), fbox, GREEN, _line_w(fixed_p))
    left = annotated.resize((pw, ph), Image.LANCZOS)
    right = fixed_p.resize((pw, ph), Image.LANCZOS)
    W = M * 3 + pw * 2
    scratch = Image.new("RGB", (10, 10))
    sd = ImageDraw.Draw(scratch)
    f_txt, f_small = font(21), font(17)
    cap_w = W - M * 2
    blocks = []
    if details.strip():
        blocks.append(("WRONG HERE", RED, _wrap(sd, details.strip(), f_txt, cap_w)))
    if correct_note.strip():
        blocks.append(("CORRECT", GREEN, _wrap(sd, correct_note.strip(), f_txt, cap_w)))
    foot_h = M + sum(24 + 27 * len(b[2]) + 10 for b in blocks)
    zoom = None
    zoom_h = 0
    if include_zoom and nbox:
        zh = 520
        zw = pw
        a = annotated.crop(box)
        b = fixed.crop(fbox)
        zoom = Image.new("RGB", (M * 3 + zw * 2, zh + M), PAPER)
        zoom.paste(_fit(a, zw, zh), (M, 0))
        zoom.paste(_fit(b, zw, zh), (M * 2 + zw, 0))
        zoom_h = zh + M
    head_h = 112
    title = (meta.get("title") or "").strip()
    H = head_h + ph + M + zoom_h + foot_h + M
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    # header
    d.text((M, 14), (title or "Generation miss")[:90], font=font(26, True), fill=INK)
    sub = "  ·  ".join(x for x in (meta.get("model"), meta.get("run"), ("video " + str(meta.get("video"))) if meta.get("video") else "",
                                    f"wrong @ {meta.get('t_wrong', 0):.2f}s", f"correct @ {meta.get('t_fixed', 0):.2f}s",
                                    meta.get("sample")) if x)
    d.text((M, 52), sub[:150], font=f_small, fill=MUTED)
    y = head_h
    d.rectangle([M, y - 32, M + 178, y - 6], fill=RED)
    d.text((M + 10, y - 29), "MISS · as generated", font=font(17, True), fill=(255, 255, 255))
    d.rectangle([M * 2 + pw, y - 32, M * 2 + pw + 158, y - 6], fill=GREEN)
    d.text((M * 2 + pw + 10, y - 29), "CORRECT · fixed", font=font(17, True), fill=(255, 255, 255))
    img.paste(left, (M, y))
    img.paste(right, (M * 2 + pw, y))
    y += ph + M
    if zoom:
        img.paste(zoom, (0, y))
        d.text((M + 6, y + 4), "zoom: marked region", font=font(15, True), fill=(255, 255, 255))
        y += zoom_h
    for label, col, lines in blocks:
        d.text((M, y), label, font=font(17, True), fill=col)
        y += 24
        for ln in lines:
            d.text((M, y), ln, font=f_txt, fill=INK)
            y += 27
        y += 10
    return img, zoom, annotated
