"""Loupe compose: draw the admin's arrows/circles on a frame and build the wrong-vs-correct sample image (Pillow)."""
from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED = (255, 45, 45)
GREEN = (46, 204, 113)
GREEN_DARK = (22, 163, 74)
EXAMPLE = (59, 110, 220)       # an example: a suggestion of what could have changed or been done another way, neither a bad generation nor a correct one
INK = (24, 26, 32)
PAPER = (245, 245, 247)
MUTED = (120, 124, 134)
MAX_FRAMES = 12
MARK_SHAPES = ("arrow", "circle", "rect")        # the marks that point at a place: a frame gets a close range (zoom) only for one of these, never for a text label alone
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
    for s in (shapes or [])[:120]:
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        try:       # a mark belongs to one frame of the moment (its index); an older mark names a side: the miss frame is 0, the right one is 1
            fr = int(s["frame"]) if s.get("frame") is not None else (1 if s.get("side") == "fixed" else 0)
        except (TypeError, ValueError):
            fr = 0
        fr = min(max(fr, 0), MAX_FRAMES - 1)
        try:
            if t == "arrow":
                out.append({"type": "arrow", "frame": fr, **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "circle":
                out.append({"type": "circle", "frame": fr, **{k: float(s[k]) for k in ("cx", "cy", "rx", "ry")}})
            elif t == "rect":
                out.append({"type": "rect", "frame": fr, **{k: float(s[k]) for k in ("x1", "y1", "x2", "y2")}})
            elif t == "text" and str(s.get("text", "")).strip():
                out.append({"type": "text", "frame": fr, "x": float(s["x"]), "y": float(s["y"]), "text": str(s["text"]).strip()[:80]})
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


def band_rows(n: int) -> list[int]:
    """How many pictures each row of the band holds: up to four in one row, more in two rows, the longer first."""
    if n <= 0:
        return []
    if n <= 4:
        return [n]
    top = -(-n // 2)
    return [top, n - top]


def band_heading(n: int, meta: dict | None) -> str:
    """The band's heading: how many of the images sent with this generation it shows (meta images_sent, verified from the run's records), else how
    many product samples it shows (an older sample, or a run whose records verify nothing it sent)."""
    sent = len((meta or {}).get("images_sent") or []) if isinstance(meta, dict) else 0
    if sent and n == sent:
        return f"{sent} IMAGE{'S' if sent > 1 else ''} SENT WITH THIS GENERATION"
    if sent:
        return f"{n} OF THE {sent} IMAGES SENT WITH THIS GENERATION"
    return f"PRODUCT SAMPLE{'S' if n > 1 else ''} · {n} IMAGE{'S' if n > 1 else ''} USED IN THIS VIDEO"


def _wrap_hard(d, text: str, f, width: int) -> list[str]:
    """_wrap, and a word longer than the width (a file name) broken at its last '-', '_' or space that fits (a '.' only when none fits past the
    first third, so 'Front.png' stays whole), else where it must: nothing is cut."""
    out = []
    for ln in _wrap(d, text, f, width):
        while ln and d.textlength(ln, font=f) > width:
            k = len(ln)
            while k > 1 and d.textlength(ln[:k], font=f) > width:
                k -= 1
            cut = max(ln.rfind(c, 0, k) for c in "-_ ")
            if cut < k // 3:
                cut = max(cut, ln.rfind(".", 0, k))
            k = cut + 1 if cut > 0 else k
            out.append(ln[:k].rstrip())
            ln = ln[k:].lstrip()
        if ln:
            out.append(ln)
    return out


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


def _secs(x) -> str:
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def gen_words(g: dict | None) -> str:
    """The generation that made a frame, in a tag's words (the user, 2026-10-28: a frame must tie back to the prompt that made it): 'generation 1 of 2 ·
    clip c1 (0–6.1 s)' for a take joined from several generations, 'clip c2' for a take of one clip, '' when the take is one generation."""
    if not g:
        return ""
    if g.get("after") is not None:
        return f"after the last generation ({_secs(g['after'])} s)"
    if g.get("insert"):
        return f"not generated: {g['insert']} ({_secs(g.get('t_from') or 0)}–{_secs(g.get('t_to') or 0)} s)"
    if (g.get("of") or 1) > 1:
        spans = g.get("spans") or [[g.get("t_from") or 0, g.get("t_to") or 0]]
        return f"generation {g.get('gen')} of {g.get('of')} · clip {g.get('clip') or '-'} (" + " and ".join(f"{_secs(a)}–{_secs(b)} s" for a, b in spans) + ")"
    return f"clip {g['clip']}" if g.get("clip") else ""


SOURCE_WORDS = {"as generated": "video A (the version as generated", "correct version": "video B (the right version, a different video",
                "original video": "the original video (the source clip the generation was made from, not generated"}


def sources_legend(descs: list[str]) -> str:
    """What the frames' source tags mean, said in the image itself (the QC keeper and the agents see only this file): one clause per tag, and a tag that
    names another video or run ('correct version · video 04', 'as generated · run R video 03') says where the frame was taken from."""
    parts = []
    for x in descs:
        base, _sep, where = x.partition(" · ")
        words = SOURCE_WORDS.get(base)
        if not words:
            continue
        if where:
            words += ", taken from " + (where if where.startswith("run ") else where + " of the same run")
        parts.append(f"frames tagged '{x}' are from {words})")
    s = "; ".join(parts)
    return s[:1].upper() + s[1:] + "." if s else ""


def build(frames: list[dict], meta: dict, details: str, correct_note: str, include_zoom: bool = True, product_samples: list | None = None,
          idea: str = "", better: str = "", missed: str = ""):
    """(composite, zoom_or_None, [annotated frame, ...]).

    frames: [{"img": a PIL image, "role": "wrong" | "right" | "example", "desc": where it comes from, "t": seconds, "shapes": [its marks]}] in the order the admin chose;
    there may be none at all (a report that is only words).  A frame that also has "n" (its number in the sample), "video" ("A" or "B") and "gen" (the generation of
    a joined take that made it, or None) gets a tag of two lines, the same words the saved prompts use: FRAME n, its role and its time on top, and under it VIDEO A
    or B, where it comes from and its generation (gen_words), with a legend line that ties a frame to its generation's prompt when any frame has one.
    One image: every frame in a panel with a tag (red WRONG, green RIGHT or blue EXAMPLE, where it comes from, its time) and exactly the marks the admin drew on it (nothing is
    copied or added), two panels in a row up to two frames and three in a row beyond that, every row centred.  Under a row of two or three frames, the close range (zoom) of
    each frame the admin marked with a circle, an arrow or a box, cut around that frame's own marks and set under it; a frame with no such mark (unmarked, or a text label
    only) has none, and neither has a frame whose marks cover most of it (it would repeat the panel), so a row with no close range has no zoom row.  A frame that sits
    alone in its row (a one-frame sample, or the last frame of a row of three) and has a close range is shown the other way: the normal-range shot on the left and, next
    to it, its close range at the same height, so no row is left half empty.  Each tag says where its frame comes from (desc: as generated, correct version, original
    video, and the other video or run it was taken from); frames from more than one source get a legend of the tags.  A lone miss frame also carries a grey NO RIGHT VERSION
    tag.  The words are burned in at the bottom with the product sample beside them: the title, the explanation, and the admin's idea (what was meant to happen), what could have
    been better and what the AI agents missed or could not understand, each when given.  The marks are drawn after each picture has its final size, thin and at the same weight in
    the panels and in the zoom."""
    fr = [dict(f, img=f["img"].convert("RGB"), shapes=list(f.get("shapes") or [])) for f in frames][:MAX_FRAMES]
    n = len(fr)
    colr = lambda role: {"wrong": RED, "right": GREEN_DARK}.get(role, EXAMPLE)      # noqa: E731
    annotated = [draw_shapes(f["img"], f["shapes"], colr(f["role"])) if f["shapes"] else f["img"].copy() for f in fr]      # the full-size files
    has_right = any(f["role"] == "right" for f in fr)
    # each frame's own close range: the place its own marks hold, only for a frame with a circle, an arrow or a box (never cut around another frame's marks)
    own = [region_norm(f["shapes"]) if any(s["type"] in MARK_SHAPES for s in f["shapes"]) else None for f in fr]
    big = [bool(b) and (b[2] - b[0]) * (b[3] - b[1]) > 0.6 for b in own]       # its marks cover most of the frame: a close range would show the same picture again
    zb = [b if (include_zoom and b and not g) else None for b, g in zip(own, big)]
    cols = max(1, n) if n <= 2 else 3

    PW, M = 560, 24
    if n:
        f0 = fr[0]["img"]
        ph = min(round(PW * f0.height / f0.width), 1000)
        pw = round(ph * f0.width / f0.height) if ph == 1000 else PW
        pw = max(pw, 200)
    else:
        ph, pw = 0, PW
    lwp = _line_w(ph or 996)             # one mark weight for the panels and the zoom
    pics = []
    for i in range(n):
        im = fr[i]["img"]
        hi = round(pw * im.height / im.width) if abs(im.width / im.height - f0.width / f0.height) > 0.02 else ph
        pic = im.resize((pw, hi), Image.LANCZOS)
        pics.append(draw_shapes(pic, fr[i]["shapes"], colr(fr[i]["role"]), lw=lwp, ref_h=ph) if fr[i]["shapes"] else pic)
    rows = [list(range(k, min(k + cols, n))) for k in range(0, n, cols)]
    pair = [len(row) == 1 and zb[row[0]] is not None for row in rows]          # a frame alone in its row: the normal range and its close range side by side
    zrow = [not pair[r] and any(zb[i] is not None for i in row) for r, row in enumerate(rows)]      # a zoom row under a row of frames only when one of them has a close range
    cols_w = max(cols, 2)                # never narrower than two panels, so the words always have room
    W = M * (cols_w + 1) + pw * cols_w
    row_x = lambda k: (W - (k * pw + (k - 1) * M)) // 2      # noqa: E731  every row is centred

    zh = 520

    def zoomed(i: int, height: int):
        im, marks = fr[i]["img"], fr[i]["shapes"]
        bx0, by0, bx1, by1 = to_px(zb[i], im.width, im.height)       # around this frame's own marks
        cw_, ch_ = max(1, bx1 - bx0), max(1, by1 - by0)
        sc = min(pw / cw_, height / ch_)
        nw, nh = max(1, round(cw_ * sc)), max(1, round(ch_ * sc))
        ox, oy = (pw - nw) // 2, (height - nh) // 2
        canvas = Image.new("RGB", (pw, height), (18, 18, 22))
        canvas.paste(im.crop((bx0, by0, bx1, by1)).resize((nw, nh), Image.LANCZOS), (ox, oy))
        if marks:       # normalised point -> pixel of this zoom: ox + (x * W - bx0) * scale
            canvas = draw_shapes(canvas, marks, colr(fr[i]["role"]), lw=lwp, box=(ox - bx0 * sc, oy - by0 * sc, im.width * sc, im.height * sc), ref_h=ph)
        return canvas

    zoom_rows: dict[int, Image.Image] = {}        # the zoom file: the close ranges of a row of frames, each under its own frame (a frame without one leaves its place empty)
    for r, row in enumerate(rows):
        if not zrow[r]:
            continue
        zr = Image.new("RGB", (W, zh + M), PAPER)
        x0 = row_x(len(row))
        for c, i in enumerate(row):
            if zb[i] is None:
                continue
            cell = zoomed(i, zh)
            ImageDraw.Draw(cell).text((6, 4), "zoom: marked place", font=font(15, True), fill=(255, 255, 255))
            zr.paste(cell, (x0 + c * (M + pw), 0))
        zoom_rows[r] = zr
    zoom = None
    if zoom_rows:
        zoom = Image.new("RGB", (W, (zh + M) * len(zoom_rows)), PAPER)
        for k, zr in enumerate(zoom_rows.values()):
            zoom.paste(zr, (0, k * (zh + M)))

    # the burned words (bottom): the title, the explanation, the idea, what could be better, what the agents missed, what the right frame shows
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    ps = list(product_samples or [])       # [(image, label)]: the bottom band is then split, the words on the left half, the product sample on the right
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
    for label, text, col in (("The idea: ", idea, (170, 205, 255)), ("Could have been better: ", better, (255, 222, 150)), ("The AI agents missed: ", missed, (255, 170, 170))):
        if text.strip():
            cap.append((f_cap, col, _wrap(scratch, label + text.strip(), f_cap, cap_w)))
    if correct_note.strip() and has_right:
        cap.append((f_cap, (150, 235, 185), _wrap(scratch, "Right: " + correct_note.strip(), f_cap, cap_w)))
    legend = []        # what the tags mean, said in the image itself: the QC keeper and the agents see only this file
    descs = list(dict.fromkeys(str(f.get("desc") or "") for f in fr))
    if len(descs) > 1 or any(" · " in x for x in descs):         # frames from two sources, or taken from another video or run
        said = sources_legend(descs)
        if said:
            legend.append(said)
    if any(big):
        legend.append("Where the marks cover most of a frame, no close range is shown for it: compare the whole panels.")
    if any(f.get("role") == "example" for f in fr):         # what an EXAMPLE is, said in the image itself (the user, 2026-10-28)
        legend.append("A frame tagged EXAMPLE is neither a bad generation nor a correct one: it is a suggestion of what could have changed or how one thing "
                      "could have been done another way.")
    two = any(f.get("video") for f in fr)       # numbered frames that name their video and the generation that made them: a second tag line under each
    if two and any(gen_words(f.get("gen")) for f in fr):
        legend.append("Each frame is tagged with its number (FRAME n, the number the prompts saved with this sample use) and, under it, its video (VIDEO A "
                      "as generated, VIDEO B the right version) and the generation of that take that made it (its clip and seconds): the prompt of that "
                      "generation is the one that names this frame.")
    f_leg = font(19)
    for t in reversed(legend):          # the legend reads first, above the admin's words
        cap.insert(0, (f_leg, (170, 174, 184), _wrap(scratch, t, f_leg, cap_w)))
    text_h = 36 + sum(len(ls) * (f.size + 9) + 12 for f, _c, ls in cap) if cap else 0
    cap_h = max(text_h, 300) if ps else text_h        # the product needs some height to be recognised
    # the band (the admin, 2026-10-30): how many of the images sent with this generation it shows, and each one's full name under its own picture,
    # wrapped, never cut; up to four in a row, more in two rows; the band grows when the names need the room
    f_ps, f_ps_head = font(15, True), font(16, True)
    ps_box_w = (W - M) - (W // 2 + M) - 24
    ps_rows = band_rows(len(ps))
    ps_cell = ps_box_w // max(ps_rows or [1])
    ps_names = [_wrap_hard(scratch, str(lab or ""), f_ps, ps_cell - 12) for _im, lab in ps]
    ps_head = _wrap_hard(scratch, band_heading(len(ps), meta), f_ps_head, ps_box_w) if ps else []
    row_names_h, k = [], 0
    for n_row in ps_rows:
        row_names_h.append(max(len(x) for x in ps_names[k:k + n_row]) * (f_ps.size + 5))
        k += n_row
    ps_text_h = (10 + len(ps_head) * (f_ps_head.size + 6) + 6 + sum(h_ + 14 for h_ in row_names_h) + 4) if ps else 0
    if ps:
        cap_h = max(cap_h, 32 + (200 if len(ps_rows) == 1 else 140 * len(ps_rows)) + ps_text_h)      # about 200 px for the pictures of one row

    head_h, tag_room = ((104 if two else 74) if n else 40), (70 if two else 40)
    row_h = [max(pics[i].height for i in row) for row in rows]
    H = (head_h + sum(rh + M + ((zh + M) if z else 0) for rh, z in zip(row_h, zrow)) + tag_room * max(0, len(rows) - 1)
         + max(cap_h, 60 if not rows else 0))
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    sub = "  ·  ".join(x for x in (meta.get("model"), meta.get("run"), ("video " + str(meta.get("video"))) if meta.get("video") else "", meta.get("sample")) if x)
    d.text((M, 10), sub[:150], font=font(16), fill=MUTED)
    fb = font(17, True)

    def tag(x: int, y: int, text, fill, maxw: int = 0):
        """A coloured tag above a panel.  Given as parts [verdict, source, time] with the panel's width, a source too long for the panel shrinks the letters,
        then is shortened, so a tag never runs over the next panel and its time always shows."""
        parts = list(text) if isinstance(text, (list, tuple)) else [text]
        join = lambda ps: " · ".join(p for p in ps if p)      # noqa: E731
        f = fb
        if maxw:
            for size in range(17, 10, -1):
                f = font(size, True)
                if d.textlength(join(parts), font=f) + 20 <= maxw:
                    break
            while len(parts) == 3 and d.textlength(join(parts), font=f) + 20 > maxw and len(parts[1]) > 6:
                parts[1] = parts[1][:-2].rstrip(" ·") + "…"
        s = join(parts)
        d.rectangle([x, y - 32, x + d.textlength(s, font=f) + 20, y - 6], fill=fill)
        d.text((x + 10, y - 29 + (fb.size - f.size) // 2), s, font=f, fill=(255, 255, 255))
        return int(x + d.textlength(s, font=f) + 20)

    def tag2(x: int, y: int, parts: list, maxw: int):
        """The second line of a numbered frame's tag, on a dark ground: its video, where it comes from and the generation that made it.  It shrinks, then
        shortens the source words and then the generation's, so it never runs over the next panel; VIDEO A or B always shows."""
        parts = [p for p in parts if p]
        join = lambda ps: " · ".join(ps)      # noqa: E731
        f = font(15, True)
        for size in range(15, 9, -1):
            f = font(size, True)
            if d.textlength(join(parts), font=f) + 20 <= maxw:
                break
        k = 1
        while k < len(parts) and d.textlength(join(parts), font=f) + 20 > maxw:
            if len(parts[k]) > 6:
                parts[k] = parts[k][:-2].rstrip(" ·") + "…"
            else:
                k += 1
        s = join(parts)
        d.rectangle([x, y - 32, x + d.textlength(s, font=f) + 20, y - 6], fill=(46, 50, 60))
        d.text((x + 10, y - 28 + (15 - f.size) // 2), s, font=f, fill=(235, 238, 245))

    y = head_h
    for r, row in enumerate(rows):
        if r:
            y += tag_room
        x0 = row_x(2 if pair[r] else len(row))
        for c, i in enumerate(row):
            f = fr[i]
            x = x0 + c * (M + pw)
            role_word = {"wrong": "WRONG", "right": "RIGHT"}.get(f["role"], "EXAMPLE")
            ty = y - 30 if two else y        # a numbered frame: FRAME n, its role and time on top; its video, source and generation under it
            if two:
                end = tag(x, ty, [f"FRAME {f.get('n') or i + 1}", role_word, _fmt_t(f.get("t"))], colr(f["role"]), maxw=pw)
                tag2(x, y, [f"VIDEO {f.get('video') or '?'}", str(f.get("desc") or ""), gen_words(f.get("gen"))], maxw=pw)
            else:
                end = tag(x, y, [role_word, str(f.get("desc") or ""), _fmt_t(f.get("t"))], colr(f["role"]), maxw=pw)
            if n == 1 and f["role"] == "wrong":
                tag(end + 8, ty, "NO RIGHT VERSION", (96, 100, 112))      # no right frame to compare with: said in the image itself
            img.paste(pics[i], (x, y))
            if pair[r]:       # the close range next to the normal-range shot, at the same height
                tag(x0 + M + pw, ty, "CLOSE RANGE", (70, 74, 90))
                img.paste(zoomed(i, pics[i].height), (x0 + M + pw, y))
        y += row_h[r] + M
        if zrow[r]:
            img.paste(zoom_rows[r], (0, y))
            y += zh + M
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
            hy = y0 + 10
            for ln in ps_head:
                d.text((x0 + 12, hy), ln, font=f_ps_head, fill=(40, 42, 50))
                hy += f_ps_head.size + 6
            iy = hy + 6
            cell = (x1 - x0 - 24) // max(ps_rows)
            avail = y1 - 4 - iy
            ih_max = max(40, (avail - sum(h_ + 14 for h_ in row_names_h)) // len(ps_rows))
            row_ih, k = [], 0
            for n_row in ps_rows:          # a row as tall as its tallest picture at the cell's width, at most its share of the band
                row_ih.append(max(40, min(ih_max, max(round(ps[k + j][0].height * (cell - 12) / max(1, ps[k + j][0].width)) for j in range(n_row)))))
                k += n_row
            iy += max(0, (avail - sum(r_ + h_ + 14 for r_, h_ in zip(row_ih, row_names_h))) // 2)      # the pictures and their names centred together
            k = 0
            for n_row, names_h, ih_row in zip(ps_rows, row_names_h, row_ih):
                rx = x0 + 12 + ((x1 - x0 - 24) - n_row * cell) // 2          # a shorter row is centred
                for j in range(n_row):
                    (pim, _lab), names = ps[k + j], ps_names[k + j]
                    sc = min((cell - 12) / pim.width, ih_row / pim.height)
                    nw, nh = max(1, round(pim.width * sc)), max(1, round(pim.height * sc))
                    cx = rx + j * cell
                    img.paste(pim.resize((nw, nh), Image.LANCZOS), (cx + (cell - nw) // 2, iy + (ih_row - nh) // 2))
                    ny = iy + ih_row + 8
                    for ln in names:          # its whole name, centred under its own picture
                        d.text((cx + (cell - d.textlength(ln, font=f_ps)) / 2, ny), ln, font=f_ps, fill=(40, 42, 50))
                        ny += f_ps.size + 5
                iy += ih_row + names_h + 14
                k += n_row
    return img, zoom, annotated
