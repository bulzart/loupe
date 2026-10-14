"""Loupe facts: deterministic, labelled facts about a flagged request.  Facts only - no verdicts (the agent / the admin judge).

Used on the pipeline side (atp.py gathers them when a flag is recorded) and on the server side (to refresh them).
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import media

STOP = set("""a an the and or but if then than that this these those to of in on at by for with without from as is are was were be been being it its
into onto over under up down out off so such not no nor only own same too very can will just do does did done has have had having each every any
all both few more most other some also again further once here there when where why how which who whom whose what while until before after
above below between through during about against""".split())
LABEL_RE = re.compile(r"\b(?:Image|Video|Audio)\s+\d+\b")
_ocr = None


def _ocr_engine():
    global _ocr
    if _ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        except Exception:  # noqa: BLE001
            _ocr = False
    return _ocr or None


def ocr_lines(png: Path, limit: int = 14) -> list[str]:
    eng = _ocr_engine()
    if not eng:
        return []
    try:
        res, _ = eng(str(png))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in res or []:
        t = str(r[1]).strip()
        if len(t) >= 2 and t not in out:
            out.append(t[:80])
    return out[:limit]


def ahash(png: Path) -> str:
    try:
        from PIL import Image
        im = Image.open(png).convert("L").resize((8, 8))
        px = list(im.getdata())
        avg = sum(px) / 64.0
        bits = "".join("1" if p > avg else "0" for p in px)
        return f"{int(bits, 2):016x}"
    except Exception:  # noqa: BLE001
        return ""


def file_facts(path: str | Path, ocr: bool = True) -> dict:
    """sha1, size, dimensions (and duration for a video), a perceptual hash of the first frame, and the text seen on screen (OCR)."""
    p = Path(str(path))
    if not p.is_file():
        return {}
    info = {"sha1": media.sha1_file(p), "size": p.stat().st_size}
    try:
        pr = media.probe(p)
        info.update({k: pr[k] for k in ("kind", "width", "height", "duration", "fps", "has_audio", "codec") if k in pr})
        times = [0.0] if info.get("kind") == "image" else sorted({0.2, round((pr.get("duration") or 1) / 2, 2), max(0.0, round((pr.get("duration") or 1) - 0.4, 2))})
        text, ahs = [], []
        for t in times[:3]:
            png = media.frame_file(p, t)
            ahs.append(ahash(png))
            if ocr:
                for ln in ocr_lines(png):
                    if ln not in text:
                        text.append(ln)
        info["phash"] = ahs[0] if ahs else ""
        if ocr:
            info["text_seen"] = text[:24]
    except Exception as e:  # noqa: BLE001
        info["facts_error"] = str(e)[:160]
    return info


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'’-]{1,}", (text or "").lower()) if w not in STOP]


def term_set(text: str) -> set[str]:
    """Words and word pairs of a prompt (lower case, without stop words and Image/Video N labels)."""
    t = LABEL_RE.sub(" ", text or "")
    ws = words(t)
    return set(ws) | {a + " " + b for a, b in zip(ws, ws[1:])}


def prompt_facts(prompt: str) -> dict:
    t = prompt or ""
    body = LABEL_RE.sub(" ", t)
    caps = []
    for sent in re.split(r"(?<=[.!?\n])\s+", body):
        toks = re.findall(r"[A-Za-z][\w'’-]*", sent)
        i = 0
        while i < len(toks):
            if toks[i][:1].isupper() and i > 0 and toks[i].lower() not in STOP:
                j = i
                while j + 1 < len(toks) and toks[j + 1][:1].isupper():
                    j += 1
                caps.append(" ".join(toks[i:j + 1]))
                i = j + 1
            else:
                i += 1
    caps += re.findall(r"\b[A-Z]{3,}\b", body)
    return {"chars": len(t), "words": len(t.split()), "sentences": len([s for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]),
            "capitalised_terms": list(dict.fromkeys(caps))[:30], "quoted": re.findall(r"[\"“]([^\"”]{2,80})[\"”]", t)[:15],
            "handles_or_urls": list(dict.fromkeys(re.findall(r"@\w{2,}|https?://\S+|www\.\S+", t)))[:10],
            "input_labels": sorted(set(LABEL_RE.findall(t)))}


def sentence_diff(old: str, new: str, cap: int = 24) -> list[str]:
    def sp(t):
        return [s.strip() for s in re.split(r"(?<=[.;!?])\s+|\n+", t or "") if s.strip()]
    a, b = sp(old), sp(new)
    lines = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            lines += ["- " + s[:300] for s in a[i1:i2]]
        if op in ("replace", "insert"):
            lines += ["+ " + s[:300] for s in b[j1:j2]]
    return lines[:cap] + ([f"… {len(lines) - cap} more"] if len(lines) > cap else [])


def diff_facts(old: dict, new: dict) -> dict:
    """What changed between the flagged request and the accepted one: sentences, words, inputs (by sha1 / name), parameters."""
    po, pn = old.get("prompt", ""), new.get("prompt", "")
    wo, wn = set(words(LABEL_RE.sub(" ", po))), set(words(LABEL_RE.sub(" ", pn)))

    def key(i):
        return i.get("sha1") or (i.get("facts") or {}).get("sha1") or i.get("name") or i.get("media_id")
    io = {key(i): i for i in old.get("inputs", []) if key(i)}
    inn = {key(i): i for i in new.get("inputs", []) if key(i)}
    label = lambda i: f"{i.get('label', '')} {i.get('name', '')}".strip()  # noqa: E731
    pa, pb = old.get("params", {}) or {}, new.get("params", {}) or {}
    return {"sentences": sentence_diff(po, pn),
            "words_removed": sorted(wo - wn)[:60], "words_added": sorted(wn - wo)[:60],
            "inputs_removed": [label(io[k]) for k in io if k not in inn], "inputs_added": [label(inn[k]) for k in inn if k not in io],
            "inputs_kept": len([k for k in io if k in inn]),
            "params_changed": {k: [pa.get(k), pb.get(k)] for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)}}
