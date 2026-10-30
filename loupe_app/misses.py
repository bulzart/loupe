"""Loupe misses: the admin reports a miss on a video, marks moments on it, and Loupe builds the wrong-vs-correct sample image."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from PIL import Image

from . import compose, core, media, runs

KINDS = ["counts", "openings-use", "preparation", "persistent-state", "permanence", "liquids", "scale-size", "body-mechanics", "label-orientation",
         "hand-off", "environment", "timing", "stylised", "solidity", "blend-paste", "posture-place", "identity", "text-on-screen", "audio-sync",
         "product-missing", "continuity", "other"]


def suggest_kinds(text: str) -> list[str]:
    """The kinds of KINDS whose own words (split on the hyphen) occur in the text, matched on a common prefix of four letters (so "opening" and
    "opens" both find openings-use, "sizes" finds scale-size).  Nothing is weighed or ranked: a suggestion for the admin to confirm or untick."""
    words = {w for w in re.findall(r"[a-z]{3,}", str(text or "").lower())}
    out = []
    for k in KINDS:
        if k == "other":
            continue
        parts = [x for x in k.split("-") if len(x) >= 4]
        if any(w[:4] == x[:4] for x in parts for w in words):
            out.append(k)
    return out


def _kinds(v) -> str:
    items = v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")
    out = []
    for k in items:
        k = str(k).strip().lower().replace(" ", "-")
        k = core.safe_name(k, 40) if k else ""
        if k and k not in out:
            out.append(k)
    return ",".join(out[:8])


# A moment is any number of frames the admin chose, none at all included (a scenario in words only).  Each frame comes from one of the report's two videos (src "wrong" =
# the version as generated, "fixed" = the right version), has its own time, and is called WRONG, RIGHT or EXAMPLE (role "example": a suggestion of what could have changed or how one thing could have been done another way, neither a bad generation nor a correct one; a
# sample with Example frames has only Examples, one with Wrong or Right frames has none, the user, 2026-10-28) by the admin; the marks belong to a
# frame (its index).  An older moment holds no frames column at all: it is the miss frame at t_wrong and, when the report has a right version, the right frame at t_fixed.
FRAME_DESC = {"wrong": "as generated", "fixed": "correct version"}
ORIGINAL_DESC = "original video"      # either video can be the original the generation was made from, or a take of another video or run: its frames' tags say so (side_desc)
TITLE_MAX = 2000       # the title is often a whole paragraph and is burned into the image in full: never cut it short
# The prompts of a report's two boxes: the flawed version's (column wrong: the generations the frames called WRONG come from), the correct
# version's (column fixed: those the frames called RIGHT come from, from either video), or an Examples sample's one example box; plus one line of
# the admin's own words on what in the prompt made the difference.  They live in the misses table only: never in the composite or its meta, never in the keeper's digest, and
# nothing here compares or diffs them (the keeper and the agents read them whole, on request, and judge what changed).  Every listing carries only the marker
# `prompts` (pair | wrong | fixed | empty) and the note; the prompts themselves are served by prompts_of() to the single-sample route and the
# keeper's sample_origin fetch.
PROMPT_COLS = ("wrong_prompt", "fixed_prompt", "prompt_note")
# Video generation prompts are never cut (the user, 2026-10-29: "make sure to not trim the video generation prompts at all without any exception because
# we need them fully"): a prompt box and every generation's prompt are stored, read and sent whole, however long.
SAMPLE_FILES_MAX = 4   # images the admin may tick for the product sample (the bottom band splits its half between them)
# A take made of several generations (clips joined) keeps the prompt of each generation with the seconds of that take it made (wrong_segments,
# fixed_segments: [{src, clip, attempt, request, t_from, t_to, spans, gen, of, prompt}]; src is the video whose seconds that generation made: "wrong"
# Video A, "fixed" Video B).  One prompt box per kind of frame (the user, 2026-10-28 and 2026-10-29): the flawed version's (column wrong) holds the
# prompt of each generation the frames called WRONG come from, the correct version's (column fixed) the prompt of each generation the frames called
# RIGHT come from, from either video, and an Examples sample's one box that of each generation of its video its Example frames come from; one after the other, each under the line that says which seconds of which video it is responsible for
# (segment_line).  The field keeps its text as the form wrote it; the generations kept beside it let prompts_of() tell the judge, sample by sample,
# which generations that sample's frames come from.  A prompt typed by hand replaces them.
SEGMENT_COLS = ("wrong_segments", "fixed_segments")
# No cap on how many generations a box keeps either (the user, 2026-10-29: "make sure that video generation prompts from the samples are not trimmed in
# any way"): every generation the frames come from is kept, with its whole prompt.
ROLE_WORD ={"wrong": "WRONG", "right": "RIGHT", "example": "EXAMPLE"}
SIDE_ROLE = {"wrong": "wrong", "fixed": "right"}      # the frames each side's prompts are for
VIDEO_NAME = {"wrong": "Video A", "fixed": "Video B"}


def _spans_clean(v) -> list[list[float]]:
    out = []
    for sp in (v if isinstance(v, list) else []):
        try:
            a, b = float(sp[0]), float(sp[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if b > a:
            out.append([round(max(0.0, a), 3), round(b, 3)])
    return sorted(out)


def _spans(s: dict) -> list[list[float]]:
    """Every span of seconds a generation made (a clip that comes back after another one has two); an older segment has only t_from / t_to."""
    return _spans_clean(s.get("spans")) or ([[float(s["t_from"]), float(s["t_to"])]] if float(s.get("t_to") or 0) > float(s.get("t_from") or 0) else [])


def _segments_clean(v, side: str) -> list[dict]:
    out = []
    for x in (v if isinstance(v, list) else []):
        if not isinstance(x, dict):
            continue
        try:
            spans = _spans_clean(x.get("spans")) or _spans_clean([[x.get("t_from") or 0, x.get("t_to") or 0]])
        except (TypeError, ValueError):
            continue
        if not spans:
            continue
        att, src = str(x.get("attempt") or "0"), str(x.get("src") or side)
        seg = {"src": src if src in VIDEO_NAME else side, "clip": str(x.get("clip") or "")[:12], "attempt": int(att) if att.isdigit() else 0,
               "request": str(x.get("request") or "")[:160], "t_from": spans[0][0], "t_to": spans[0][1], "spans": spans, "prompt": _prompt_text(x.get("prompt"))}
        g, o = x.get("gen"), x.get("of")
        if isinstance(g, int) and isinstance(o, int) and not isinstance(g, bool) and 1 <= g <= o:
            seg["gen"], seg["of"] = g, o          # its number in its take, read from the run's join ('generation 1 of 2')
        out.append(seg)
    out.sort(key=lambda s: (s["src"] != "wrong", s["spans"][0][0]))
    return out if any(s["prompt"] for s in out) else []


def _multi(segs: list[dict]) -> bool:
    """Whether a box's generations are kept beside its text: two or more, or one generation of a take made of several (its seconds and its number say
    which part of the video that prompt made)."""
    return len(segs) >= 2 or bool(segs and int(segs[0].get("of") or 1) >= 2)


def _segments_of(raw) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _secs(x) -> str:
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def _numbered(segs: list[dict], side: str) -> list[dict]:
    """Each generation with its video (src) and its number in that video's take (generation `gen` of `of`): the number read from the run's join when
    the segment carries it, else (an older one) its place among the segments of its video."""
    srcs = [s.get("src") or side for s in segs]
    seen: dict = {}
    out = []
    for s, src in zip(segs, srcs):
        seen[src] = seen.get(src, 0) + 1
        known = isinstance(s.get("gen"), int) and isinstance(s.get("of"), int)
        out.append({**s, "src": src, "gen": s["gen"] if known else seen[src], "of": s["of"] if known else srcs.count(src)})
    return out


def _mmss(t) -> str:
    t = max(0.0, float(t or 0))
    return f"{int(t // 60)}:{t - int(t // 60) * 60:05.2f}"


def frames_words(frames: list[dict]) -> str:
    """'Frame 1 (WRONG, 0:03.00) and Frame 3 (WRONG, 0:05.20)': the frames as the sample's image tags them (FRAME n, the role, the time); frames of
    several samples are named with their sample ('Frame 1 (RIGHT, 0:00.00) of S39 and Frame 2 (WRONG, 0:05.00) of S40')."""
    by = {f.get("sample") for f in frames}
    it = [f"Frame {f['n']} ({ROLE_WORD.get(f['role'], str(f['role']).upper())}, {_mmss(f['t'])})" + (f" of {f['sample']}" if len(by) > 1 and f.get("sample") else "")
          for f in frames]
    return it[0] if len(it) == 1 else ", ".join(it[:-1]) + " and " + it[-1]


# What a generation's prompt is, named on its own line wherever it is written (the user, 2026-10-29: the agents, the QC, the keeper and the judge must
# understand which prompt is wrong, which is correct and which is just an example): a prompt behind frames called WRONG generated the flawed version,
# one behind frames called RIGHT the correct one, one behind frames called EXAMPLE is an example.
ROLE_PHRASE = {"wrong": "a prompt of the flawed version (the generation the frames called WRONG come from)",
               "right": "a prompt of the correct version (the generation the frames called RIGHT come from)",
               "example": "an example prompt (the generation the frames called EXAMPLE come from: a suggestion of what could have changed or been done another "
                          "way, neither a bad generation nor a correct one)"}


def spans_words(s: dict) -> str:
    """'second 0 to second 1.9 and second 3.4 to second 5.866': every span a generation made."""
    return " and ".join(f"second {_secs(a)} to second {_secs(b)}" for a, b in _spans(s))


def segment_line(s: dict, role: str = "") -> str:
    """The line over a generation's prompt (the user, 2026-10-28 and 2026-10-29): what that prompt is (the flawed version's, the correct version's, an
    example: ROLE_PHRASE, from the frames it made, else `role`), the seconds of which video it generated, its number in that take, and the frames it
    made, named as the image tags them; with no frame known, only the seconds."""
    where = (f"{spans_words(s)} of {VIDEO_NAME.get(s.get('src') or '', 'this video')} (generation "
             f"{s.get('gen') or 1} of {s.get('of') or 1} · clip {s.get('clip') or '-'} · {s.get('request') or 'request not named'})")
    roles = list(dict.fromkeys(f["role"] for f in s.get("frames") or [])) or ([role] if role else [])
    what = " and ".join(ROLE_PHRASE[r] for r in roles if r in ROLE_PHRASE)
    if not what:
        return f"This is the prompt that generated {where}" + ("; no frame of this sample comes from it." if "frames" in s else ".")
    if "frames" not in s:
        return f"This is {what}: it generated {where}."
    if s["frames"]:
        samples = list(dict.fromkeys(f.get("sample") for f in s["frames"]))
        tail = " of this sample" if samples == [None] else (f" of {samples[0]}" if len(samples) == 1 else "")
        return f"This is {what}: it generated {where}, and made {frames_words(s['frames'])}{tail}."
    return f"This is {what}: it generated {where}; no frame of this sample comes from it."


def compose_text(segs: list[dict], side: str = "", role: str = "") -> str:
    """The prompts of a box's generations as one text, each under its line: the frames each made when the segments carry them, else only its seconds."""
    return "\n\n".join(f"{segment_line(s, role)}\n{s.get('prompt') or ''}" for s in _numbered(segs, side))


ROLE_DEFAULT = {"wrong": "flawed", "fixed": "correct"}      # what a report's two prompts are unless they are an Example's


def prompt_roles(frames: list[dict]) -> dict:
    """What each prompt box stands for in one sample, from the frames it speaks for (the user, 2026-10-28: a sample whose frames are only Examples has
    Example prompts, never a flawed or a correct one, so the judge, the keeper and every agent fed with it are never misled; otherwise the flawed
    version's box, column wrong, holds the generations the frames called WRONG come from and the correct version's, column fixed, those the frames
    called RIGHT come from, from either video).  'example' for a box that speaks only for frames called EXAMPLE (no frame called WRONG for the flawed
    version's box, none called RIGHT for the correct version's, and an Example taken from that box's video), and for both boxes when every frame of
    the sample is an Example: an example is a suggestion of what could have changed or how it could have been done another way, neither a bad
    generation nor a correct one; else 'flawed' (column wrong) and 'correct' (column fixed)."""
    only_examples = bool(frames) and all(f["role"] == "example" for f in frames)
    out = {}
    for side in ("wrong", "fixed"):
        own = any(f["role"] == SIDE_ROLE[side] for f in frames)
        out[side] = "example" if not own and (only_examples or any(f["role"] == "example" and f["src"] == side for f in frames)) else ROLE_DEFAULT[side]
    return out


def prompt_sides(frames: list[dict]) -> list[str]:
    """The prompt boxes a sample speaks through (the user, 2026-10-29: "examples -> one fill from prompt box ... right or wrong who have two input
    boxes", then "if there's only 3 right frames or 3 wrong frames, or 3 examples then only fill in one prompt"): the flawed version's box (column
    wrong) when a frame is called WRONG, the correct version's box (column fixed) when one is called RIGHT, so a sample of frames called RIGHT only speaks
    through the correct version's box alone; an Examples sample takes all its frames from one video and has one box, that video's.  Both, as saved, for a
    sample in words only (no frame) and for an older Examples sample that took its frames from two videos."""
    if not frames:
        return ["wrong", "fixed"]
    if all(f["role"] == "example" for f in frames):
        return [side for side in ("wrong", "fixed") if any(f["src"] == side for f in frames)]
    return [side for side in ("wrong", "fixed") if any(f["role"] == SIDE_ROLE[side] for f in frames)]


def sample_prompts_kind(miss: dict, frames: list[dict]) -> str:
    """Which prompts a sample keeps (prompts_kind), counting only the boxes it speaks through: an Examples sample keeps its one video's."""
    sides = prompt_sides(frames)
    return prompts_kind({f"{s}_prompt": (miss.get(f"{s}_prompt") or "") if s in sides else "" for s in ("wrong", "fixed")})


def box_words(side: str, role: str, only_examples: bool = False) -> str:
    """How a box is named to the judge and the agents (the user, 2026-10-29: clear which prompt is wrong, which is correct and which is just an example):
    the flawed version's prompt box (the generations the frames called WRONG come from), the correct version's (those called RIGHT come from, from
    whichever video), or the example prompt box of Video A or B (the generations the Example frames come from: a suggestion of what could have changed
    or how it could have been done another way, neither a bad generation nor a correct one)."""
    if role == "example":
        why = "this sample's frames are all Examples" if only_examples else "the frames of this sample it speaks for are called EXAMPLE"
        return (f"the example prompt box of {VIDEO_NAME[side]} (the generations of {VIDEO_NAME[side]} the Example frames come from; {why}; an example is a suggestion of what "
                "could have changed or how it could have been done another way, neither a bad generation nor a correct one)")
    return ("the flawed version's prompt box (the generations the frames called WRONG come from: they generated the flawed version)" if side == "wrong"
            else "the correct version's prompt box (the generations the frames called RIGHT come from: they generated the correct version)")


def sync_prompt_roles(miss_id: int) -> dict:
    """Keeps on the report, in the database, what each of its two prompt boxes is (the user, 2026-10-28 and 2026-10-29): the flawed version's box
    (column wrong, 'flawed') and the correct version's (column fixed, 'correct', from either video), unless in every sample of the report that box's
    prompt is an Example's ('example': a suggestion of what could have changed or how it could have been done another way, neither a bad generation
    nor a correct one).  Run after every change to the report or its samples; the samples' own words come from prompt_roles."""
    con = core.db()
    try:
        miss = con.execute("SELECT * FROM misses WHERE id=?", (miss_id,)).fetchone()
        if not miss:
            return {}
        per = [prompt_roles(frames_of(r, dict(miss))) for r in con.execute("SELECT * FROM moments WHERE miss_id=?", (miss_id,)).fetchall()]
        roles = {side: ("example" if per and all(p[side] == "example" for p in per) else ROLE_DEFAULT[side]) for side in ("wrong", "fixed")}
        if (miss["wrong_prompt_role"], miss["fixed_prompt_role"]) != (roles["wrong"], roles["fixed"]):
            con.execute("UPDATE misses SET wrong_prompt_role=?, fixed_prompt_role=? WHERE id=?", (roles["wrong"], roles["fixed"], miss_id))
            con.commit()
        return roles
    finally:
        con.close()


def _in_spans(t: float, spans: list[list[float]], end: float) -> bool:
    """A time inside one of a generation's spans: from its start up to (not at) its end, where the next span starts; at the very end of the take too."""
    return any(a - 1e-6 <= t and (t < b - 1e-9 or (abs(b - end) < 1e-6 and t <= b + 1e-3)) for a, b in spans)


def _with_frames(segs: list[dict], frames: list[dict], side: str) -> list[dict]:
    """The generations of a box that a sample's frames come from, each numbered in its take and with those frames (numbered as in the sample's image),
    and only those (the user, 2026-10-29: "only include that generation prompts in the prompt filler not more").  The frames a box speaks for are those
    called WRONG (the flawed version's box) or RIGHT (the correct version's, from whichever video), else, in an Examples sample, the frames called EXAMPLE
    taken from that box's video."""
    mine = [f for f in frames if f["role"] == SIDE_ROLE[side]] or [f for f in frames if f["src"] == side and f["role"] == "example"]
    numbered = _numbered(segs, side)
    out = []
    for s in numbered:
        end = max((b for x in numbered if x["src"] == s["src"] for _a, b in _spans(x)), default=0.0)
        fr = [f for f in mine if f["src"] == s["src"] and _in_spans(float(f["t"]), _spans(s), end)]
        out.append({**s, "frames": [{"n": f["n"], "role": f["role"], "t": f["t"], **({"sample": f["sample"]} if f.get("sample") else {})} for f in fr]})
    return [s for s in out if s["frames"]]


def prompts_text(pp: dict) -> str:
    """The prompts of a sample as the judge reads them, whole (the user, 2026-10-29: clear which prompt is wrong, which is correct and which is just an
    example): the flawed version's box when a frame is called WRONG, the correct version's when one is called RIGHT, an Examples sample's one box; in each
    only the generations this sample's frames come from, each under the line that says what that prompt is, which seconds of which video it generated
    and which frames of this sample it made; a box this sample has no frame for is named as absent, never filled in."""
    lines = []
    roles, only_ex = pp.get("prompt_roles") or {}, bool(pp.get("examples_only"))
    sides = [s for s in (pp.get("prompt_sides") or ("wrong", "fixed")) if s in VIDEO_NAME]
    fr = pp.get("frames") or []
    if only_ex and len(sides) == 1:
        lines.append("this sample's frames are all Examples, each a suggestion of what could have changed or how it could have been done another way, neither a bad "
                     f"generation nor a correct one; an Examples sample takes all its frames from one video, here {VIDEO_NAME[sides[0]]}, and keeps one prompt box: "
                     "the example prompt of each generation its Example frames come from, each under the seconds it made (no flawed and no correct prompt)")
    elif only_ex:
        lines.append("this sample's frames are all Examples, each a suggestion of what could have changed or how it could have been done another way, neither a bad generation nor a correct one: the prompts below are the example prompts of the generations they come from (an older sample that took its Examples from two videos)")
    elif fr:
        if "wrong" not in sides:
            lines.append("this sample keeps no flawed version's prompt: none of its frames is called WRONG, so its frames called RIGHT show the correct version only")
        if "fixed" not in sides:
            lines.append("this sample keeps no correct version's prompt: none of its frames is called RIGHT (nothing of it shows the correct version)")
    for side in sides:
        what = box_words(side, roles.get(side, ""), only_ex)
        segs = pp.get(f"{side}_segments") or []
        if segs:
            of = max(int(s.get("of") or 0) for s in segs) or len(segs)
            lines.append(f"{what}: the prompt of each generation this sample's frames come from ({len(segs)} of the {of} generation(s) that take was made of), "
                         "each under the line that says what it is, the seconds it generated and the frames it made:")
            lines += [f"{segment_line(s)}\n{(s.get('prompt') or '').strip() or 'not saved'}" for s in segs]
        elif pp.get(f"{side}_unmatched"):
            lines.append(f"{what}: none of the generations it holds made a frame of this sample, so no prompt of it is this sample's (the admin's report keeps "
                         "it for its other samples)")
        else:
            txt = (pp.get(f"{side}_prompt") or "").strip()
            lines.append(f"{what} (whole, as the admin saved it):\n{txt or 'not saved'}")
    if pp.get("prompt_note"):
        lines.append("the admin's note on the prompt: " + str(pp["prompt_note"])[:600])
    return "\n\n".join(lines)


def _sample_files(raw) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict) and x.get("path")] if isinstance(v, list) else []


def _chosen_files(v, run: str, video: str, wrong_hint: str, wrong_path: str) -> str:
    """The product sample's files the admin ticked (the user, 2026-10-28), held to the images that take's generation used (runs.generation_assets): JSON
    [{path, label, asset_id}], or '' for the automatic pick.  A file that is not one of them is refused."""
    items = [str(x.get("path") if isinstance(x, dict) else x) for x in (v if isinstance(v, list) else []) if x]
    if not items:
        return ""
    allowed = {runs._file_key(a["path"]): a for a in runs.generation_assets(run, video, wrong_hint, path=wrong_path)}
    out = []
    for p in items[:SAMPLE_FILES_MAX]:
        a = allowed.get(runs._file_key(p))
        if not a:
            raise ValueError(f"{Path(p).name} is not one of the images this generation used")
        out.append({"path": str(a["path"]), "label": a["label"], "asset_id": a.get("asset_id", "")})
    return json.dumps(out, ensure_ascii=False)


def _prompt_text(v) -> str:
    """A generation's prompt (or a short text), whole: a video generation prompt is never cut."""
    return str(v or "").replace("\r\n", "\n").strip()


def _field_text(v) -> str:
    """A prompt box as saved (wrong_prompt, fixed_prompt): one generation's prompt, or the prompt of each generation of a joined take under its line;
    whole, never cut."""
    return str(v or "").replace("\r\n", "\n").strip()


def prompts_kind(miss: dict) -> str:
    """Which prompts the report (or one sample's boxes) holds: 'pair' (both boxes), 'wrong' (the flawed version's box only, or Video A's example
    prompt), 'fixed' (the correct version's box only, or Video B's example prompt) or ''."""
    w, f = bool((miss.get("wrong_prompt") or "").strip()), bool((miss.get("fixed_prompt") or "").strip())
    return "pair" if w and f else ("wrong" if w else ("fixed" if f else ""))


def prompts_sig(miss: dict) -> str:
    """A short version of the three prompt fields, so the keeper's library version changes when they do (it then re-reads them on request)."""
    import hashlib
    blob = "\x1f".join(str(miss.get(k) or "") for k in PROMPT_COLS)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10] if blob.strip("\x1f") else ""


def _frames_clean(raw, miss: dict) -> list[dict]:
    out = []
    for f in (raw or [])[:compose.MAX_FRAMES]:
        if not isinstance(f, dict):
            continue
        src = "fixed" if f.get("src") == "fixed" else "wrong"
        role = {"right": "right", "example": "example", "neutral": "example", "none": "example"}.get(f.get("role"), "wrong")      # "neutral" is the name this role had first
        try:
            t = max(0.0, float(f.get("t") or 0.0))
        except (TypeError, ValueError):
            t = 0.0
        if src == "fixed" and not miss.get("fixed_path"):
            raise ValueError("a frame comes from the right version, but this report has none")
        if src == "wrong" and not miss.get("wrong_path"):
            raise ValueError("a frame comes from Video A, but this report has none")
        out.append({"src": src, "role": role, "t": round(t, 3)})
    roles = {f["role"] for f in out}
    if "example" in roles and roles & {"wrong", "right"}:        # the user, 2026-10-28: Examples never share a sample with a miss or its correct version
        raise ValueError("a sample is either a miss with its correct version (frames called Wrong and Right) or Examples only, never both: call every frame Example, or none (an Example is a suggestion of what could have changed or how it could have been done another way, neither a bad generation nor a correct one)")
    if roles == {"example"} and len({f["src"] for f in out}) > 1:      # the user, 2026-10-29: Examples come from one video, which has one prompt box
        raise ValueError("an Examples sample takes all its frames from one video and has one prompt box, that video's (the prompt of each generation "
                         "of it its Example frames come from, each under the seconds it made): these Example frames come from Video A and Video B; take "
                         "every frame from one video (only a sample of frames called Wrong and Right may take them from both videos: each frame's "
                         "generation feeds the flawed or the correct version's box by what the frame is called)")
    return out


def _frames_legacy(tw, tf, miss: dict) -> list[dict]:
    tw = max(0.0, float(tw or 0.0))
    fr = [{"src": "wrong", "role": "wrong", "t": round(tw, 3)}] if miss.get("wrong_path") else []
    if miss.get("fixed_path"):
        fr.append({"src": "fixed", "role": "right", "t": round(max(0.0, float(tf if tf not in (None, "") else tw)), 3)})
    return fr


def frames_of(row, miss: dict) -> list[dict]:
    try:
        raw = row["frames"]
    except (KeyError, IndexError):
        raw = None
    if raw not in (None, ""):          # saved with frames, possibly none: that is a scenario in words only, not an older moment
        try:
            return [{"src": f["src"], "role": "example" if f["role"] == "neutral" else f["role"], "t": f["t"], **({"desc": str(f["desc"])} if f.get("desc") else {}),
                     **({"video": str(f["video"])} if f.get("video") else {}), **({"gen": f["gen"]} if isinstance(f.get("gen"), dict) else {})}
                    for f in json.loads(raw) if isinstance(f, dict) and "t" in f]
        except (TypeError, ValueError):
            pass
    return _frames_legacy(row["t_wrong"], row["t_fixed"], miss)


def _times(frames: list[dict]) -> tuple[float, float]:
    """The two summary times kept for listings: the first miss frame and the first right frame."""
    if not frames:
        return 0.0, 0.0
    tw = next((f["t"] for f in frames if f["role"] == "wrong"), frames[0]["t"])
    return tw, next((f["t"] for f in frames if f["role"] == "right"), tw)


def _fit_shapes(shapes: list[dict], keep: list[int]) -> list[dict]:
    """The marks of the frames that stay, renumbered to the new frame order."""
    return [dict(s, frame=keep.index(s["frame"])) for s in shapes if s["frame"] in keep]


def _write_frames(mid: int, frames: list[dict], shapes: list[dict]):
    tw, tf = _times(frames)
    con = core.db()
    try:
        con.execute("UPDATE moments SET frames=?, shapes=?, t_wrong=?, t_fixed=?, updated=? WHERE id=?", (core.jdump(frames), core.jdump(shapes), tw, tf, core.now(), mid))
        con.commit()
    finally:
        con.close()


def sample_dir(mid: int) -> Path:
    return core.DATA / "samples" / str(int(mid))


# ------------------------------------------------------------------ misses
def create(data: dict, actor: str) -> dict:
    wrong, fixed = str(data.get("wrong_path") or ""), str(data.get("fixed_path") or "")
    if wrong:
        media.resolve_media(wrong)      # raises when the file is missing or not readable
    if fixed:
        media.resolve_media(fixed)      # neither video is required: a report can be a scenario in words only (the idea, what could be better, what the agents missed)
    if not str(data.get("title") or "").strip():
        raise ValueError("a title is needed (one line: what went wrong)")
    chosen = _chosen_files(data.get("sample_files"), str(data.get("run") or ""), core.nn(data.get("video")) if data.get("video") else "",
                           str(data.get("wrong_label") or wrong), wrong)
    prompts, segcols = {k: _field_text(data.get(k)) for k in ("wrong_prompt", "fixed_prompt")}, {}
    for side in ("wrong", "fixed"):          # the generations a box holds (a take made of several): each one's prompt and seconds, kept beside the box's text
        segs = _segments_clean(data.get(f"{side}_segments"), side)
        segcols[side] = json.dumps(segs, ensure_ascii=False) if _multi(segs) else ""
        if segs and not prompts[f"{side}_prompt"]:
            prompts[f"{side}_prompt"] = compose_text(segs, side) if _multi(segs) else segs[0]["prompt"]
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO misses(created,updated,run,video,title,explanation,kinds,model,mode,product,status,wrong_path,wrong_label,
                             fixed_path,fixed_label,sample_on,wrong_prompt,fixed_prompt,prompt_note,sample_files,wrong_segments,fixed_segments)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (core.now(), core.now(), str(data.get("run") or ""), core.nn(data.get("video")), str(data["title"]).strip()[:TITLE_MAX],
                           str(data.get("explanation") or "").strip(), _kinds(data.get("kinds")), str(data.get("model") or ""),
                           str(data.get("mode") or ""), str(data.get("product") or ""), "active", wrong, str(data.get("wrong_label") or ""),
                           fixed, str(data.get("fixed_label") or ""), 0 if data.get("product_sample") is False else 1,
                           prompts["wrong_prompt"], prompts["fixed_prompt"], _prompt_text(data.get("prompt_note"))[:1000], chosen, segcols["wrong"], segcols["fixed"]))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    core.audit(actor, "miss.create", f"miss {mid}", data.get("title", ""))
    sync_prompt_roles(mid)
    return get(mid)


def _miss_row(r) -> dict:
    d = dict(r)
    d["kinds"] = [k for k in (d.get("kinds") or "").split(",") if k]
    d["product_sample"] = d.get("sample_on") != 0       # the product sample is shown in the saved image unless the admin turned it off
    d["prompts"] = prompts_kind(d)
    d["sample_files"] = _sample_files(d.get("sample_files"))      # the product sample's files the admin ticked; [] = the automatic pick
    for k in SEGMENT_COLS:          # each generation's prompt and span of a take made of several
        d[k] = _segments_of(d.get(k))
    d["prompt_roles"] = {side: d.get(f"{side}_prompt_role") or ROLE_DEFAULT[side] for side in ("wrong", "fixed")}      # what each prompt is, as kept in the database
    return d


def get(mid: int) -> dict | None:
    con = core.db()
    try:
        r = con.execute("SELECT * FROM misses WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        if r["wrong_prompt_role"] is None:          # saved before the roles were kept: written now, from its samples
            con.close()
            sync_prompt_roles(mid)
            con = core.db()
            r = con.execute("SELECT * FROM misses WHERE id=?", (mid,)).fetchone()
        d = _miss_row(r)
        d["moments"] = [moment_view(m, d) for m in con.execute("SELECT * FROM moments WHERE miss_id=? ORDER BY t_wrong, id", (mid,))]
        return d
    finally:
        con.close()


def listing(run: str = "", status: str = "") -> list[dict]:
    con = core.db()
    try:
        sql, args = "SELECT * FROM misses", []
        cond = []
        if run:
            cond.append("run=?"); args.append(run)
        if status:
            cond.append("status=?"); args.append(status)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        out = []
        rows = con.execute(sql + " ORDER BY id DESC", args).fetchall()
        if any(r["wrong_prompt_role"] is None for r in rows):         # saved before the roles were kept: written now, from their samples
            con.close()
            for r in rows:
                if r["wrong_prompt_role"] is None:
                    sync_prompt_roles(r["id"])
            con = core.db()
            rows = con.execute(sql + " ORDER BY id DESC", args).fetchall()
        for r in rows:
            d = _miss_row(r)
            ms = con.execute("SELECT * FROM moments WHERE miss_id=? ORDER BY t_wrong, id", (r["id"],)).fetchall()
            d["moments"] = [moment_view(m, d) for m in ms]
            out.append(d)
        return out
    finally:
        con.close()


def update(mid: int, patch: dict, actor: str) -> dict | None:
    cur = get(mid)
    if not cur:
        return None
    cols = {}
    for k in ("title", "explanation", "model", "mode", "product", "status", "run", "wrong_label", "fixed_label"):
        if k in patch:
            cols[k] = str(patch[k] or "")
    if "video" in patch:
        cols["video"] = core.nn(patch["video"])
    if "product_sample" in patch:
        cols["sample_on"] = 0 if patch["product_sample"] in (False, 0, "0", "false") else 1
    if "kinds" in patch:
        cols["kinds"] = _kinds(patch["kinds"])
    for k in PROMPT_COLS:       # the prompts of the two boxes: stored, never burned in (no image is rebuilt for them)
        if k in patch:
            cols[k] = _prompt_text(patch[k])[:1000] if k == "prompt_note" else _field_text(patch[k])
    for side in ("wrong", "fixed"):          # the generations a box holds, kept beside its text (written from them when no text came); a prompt typed alone replaces them
        sk, pk = f"{side}_segments", f"{side}_prompt"
        if sk in patch:
            segs = _segments_clean(patch[sk], side)
            cols[sk] = json.dumps(segs, ensure_ascii=False) if _multi(segs) else ""
            if segs and not _field_text(patch.get(pk)):
                cols[pk] = compose_text(segs, side) if _multi(segs) else segs[0]["prompt"]
        elif pk in patch and cur.get(sk) and _field_text(patch[pk]) != (cur.get(pk) or ""):
            cols[sk] = ""
    if "sample_files" in patch:
        eff = {k: (patch[k] if k in patch else cur.get(k)) for k in ("run", "video", "wrong_label", "wrong_path")}
        new = _chosen_files(patch["sample_files"], str(eff["run"] or ""), core.nn(eff["video"]) if eff["video"] else "",
                            str(eff["wrong_label"] or eff["wrong_path"] or ""), str(eff["wrong_path"] or ""))
        if _sample_files(new) != cur["sample_files"]:
            cols["sample_files"] = new
    for k in ("wrong_path", "fixed_path"):
        if k in patch and (patch[k] or "") != cur[k]:
            v = str(patch[k] or "")
            if v:       # either video can be cleared (a miss example, or a scenario in words only) or attached later
                media.resolve_media(v)
            cols[k] = v
    if cols:
        cols["updated"] = core.now()
        con = core.db()
        try:
            con.execute("UPDATE misses SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?", list(cols.values()) + [mid])
            con.commit()
        finally:
            con.close()
        core.audit(actor, "miss.update", f"miss {mid}", ",".join(cols))
        changed = [src for src, k in (("wrong", "wrong_path"), ("fixed", "fixed_path")) if k in cols and cols[k] != (cur[k] or "")]
        if changed:
            for mo in cur["moments"]:       # a right version attached later adds the right frame at the same second; clearing a video drops the frames that came from it
                fr, sh = [{k: f[k] for k in ("src", "role", "t")} for f in mo["frames"]], compose.clean_shapes(mo["shapes"])
                drop = [src for src in changed if not cols["wrong_path" if src == "wrong" else "fixed_path"]]
                if drop:
                    keep = [i for i, f in enumerate(fr) if f["src"] not in drop]
                    fr, sh = [fr[i] for i in keep], _fit_shapes(sh, keep)
                elif "fixed" in changed and not cur["fixed_path"] and len(fr) == 1 and fr[0]["role"] == "wrong" and fr[0]["src"] == "wrong":
                    fr.append({"src": "fixed", "role": "right", "t": fr[0]["t"]})
                _write_frames(mo["moment_id"], fr, sh)
        if any(k in cols for k in ("title", "explanation", "wrong_path", "fixed_path", "model", "run", "video", "sample_on", "wrong_label", "sample_files")):
            for m in cur["moments"]:       # the sample image carries the title, the caption and the source of both frames
                regenerate(m["moment_id"])
    return get(mid)


def delete(mid: int, actor: str):
    con = core.db()
    try:
        ids = [r["id"] for r in con.execute("SELECT id FROM moments WHERE miss_id=?", (mid,))]
        con.execute("DELETE FROM moments WHERE miss_id=?", (mid,))
        con.execute("DELETE FROM misses WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    for i in ids:
        shutil.rmtree(sample_dir(i), ignore_errors=True)
    core.audit(actor, "miss.delete", f"miss {mid}")


# ------------------------------------------------------------------ moments
def add_moment(miss_id: int, data: dict, actor: str) -> dict:
    m = get(miss_id)
    if not m:
        raise LookupError("no such miss")
    frames = _frames_clean(data["frames"], m) if data.get("frames") is not None else _frames_legacy(data.get("t_wrong"), data.get("t_fixed"), m)
    tw, tf = _times(frames)
    shapes = _fit_shapes(compose.clean_shapes(data.get("shapes")), list(range(len(frames))))
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO moments(miss_id,created,updated,t_wrong,t_fixed,details,correct_note,shapes,kinds,status,frames,idea,better,missed)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (miss_id, core.now(), core.now(), tw, tf, str(data.get("details") or "").strip(),
                           str(data.get("correct_note") or "").strip(), core.jdump(shapes), _kinds(data.get("kinds")), "active", core.jdump(frames),
                           str(data.get("idea") or "").strip(), str(data.get("better") or "").strip(), str(data.get("missed") or "").strip()))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    first = str(data.get("details") or "").strip()
    if not (m["explanation"] or "").strip() and first:     # the report's summary defaults to its first explanation
        con = core.db()
        try:
            con.execute("UPDATE misses SET explanation=?, updated=? WHERE id=?", (first, core.now(), miss_id))
            con.commit()
        finally:
            con.close()
    regenerate(mid)
    core.audit(actor, "moment.add", f"moment {mid}", f"miss {miss_id}: " + ", ".join(f"{f['role']} @ {f['t']:.2f}s" for f in frames))
    refresh_boxes(miss_id, actor)          # the report's boxes Loupe wrote from a joined take now name this sample's frames by its id
    return moment(mid)


def update_moment(mid: int, patch: dict, actor: str) -> dict | None:
    cur = moment(mid)
    if not cur:
        return None
    cols = {}
    frames = None
    if patch.get("frames") is not None:
        frames = _frames_clean(patch["frames"], {"fixed_path": cur["has_right_version"], "wrong_path": cur["has_wrong_version"]})
    elif "t_wrong" in patch or "t_fixed" in patch:       # an older caller moves the first miss frame and the first right frame
        frames = [dict(f) for f in cur["frames"]]
        for key, role in (("t_wrong", "wrong"), ("t_fixed", "right")):
            if key in patch:
                f = next((f for f in frames if f["role"] == role), None)
                if f:
                    f["t"] = round(max(0.0, float(patch[key])), 3)
    if frames is not None:
        cols["frames"] = core.jdump(frames)
        cols["t_wrong"], cols["t_fixed"] = _times(frames)
    for k in ("details", "correct_note", "status", "idea", "better", "missed"):
        if k in patch:
            cols[k] = str(patch[k] or "").strip()
    if "shapes" in patch or frames is not None:
        nfr = len(frames if frames is not None else cur["frames"])
        cols["shapes"] = core.jdump(_fit_shapes(compose.clean_shapes(patch["shapes"] if "shapes" in patch else cur["shapes"]), list(range(nfr))))
    if "kinds" in patch:
        cols["kinds"] = _kinds(patch["kinds"])
    if cols:
        cols["updated"] = core.now()
        con = core.db()
        try:
            con.execute("UPDATE moments SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?", list(cols.values()) + [mid])
            con.commit()
        finally:
            con.close()
        core.audit(actor, "moment.update", f"moment {mid}", ",".join(cols))
        if any(k in cols for k in ("frames", "t_wrong", "t_fixed", "details", "correct_note", "shapes", "idea", "better", "missed")):
            regenerate(mid)
        if "frames" in cols:
            refresh_boxes(cur["miss_id"], actor)        # the boxes Loupe wrote from a joined take follow the frames
    return moment(mid)


def delete_moment(mid: int, actor: str):
    con = core.db()
    try:
        row = con.execute("SELECT miss_id FROM moments WHERE id=?", (mid,)).fetchone()
        con.execute("DELETE FROM moments WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    shutil.rmtree(sample_dir(mid), ignore_errors=True)
    if row:
        sync_prompt_roles(row["miss_id"])
        refresh_boxes(row["miss_id"], actor)          # a deleted sample's generations leave the boxes Loupe wrote from a joined take
    core.audit(actor, "moment.delete", f"moment {mid}")


def side_desc(miss: dict, src: str) -> str:
    """Where the frames of one of the report's videos come from, in the words of their tag: 'as generated' (Video A) or 'correct version' (Video B), or
    'original video' when that video is the original the run recorded for the report's video (the source clip as given, or the source as an attempt sent it);
    a file of another video of the run, or of another run, adds where it was taken from ('correct version · video 04', 'as generated · run R video 03').
    Read from the run's records and from where the file sits (runs.origin), never judged."""
    path = miss.get("fixed_path" if src == "fixed" else "wrong_path") or ""
    try:
        o = runs.origin(miss.get("run") or "", miss.get("video") or "", path) if path else {}
    except Exception:  # noqa: BLE001  (a take whose run cannot be read is still a frame: its tag says only its side)
        o = {}
    base = ORIGINAL_DESC if o.get("original") else FRAME_DESC[src]
    where = (f"run {o['run']} video {o['video']}" if o.get("video") else f"run {o['run']}") if o.get("run") else (f"video {o['video']}" if o.get("video") else "")
    return f"{base} · {where}" if where else base


VIDEO_LETTER = {"wrong": "A", "fixed": "B"}


def _take_generations(miss: dict, src: str) -> dict:
    """The generations one of the report's videos was made of, each with its clip, its number and every span of seconds it made, read from the run
    (runs.prompt_segments: how a joined take was put together, or a take of one clip), else the ones kept with the report's prompt boxes for that video
    (a run that can no longer be read); {segments, inserts}.  Facts only: they number a frame's generation in its tag, so a frame ties back to the
    prompt that made it."""
    path = miss.get("fixed_path" if src == "fixed" else "wrong_path") or ""
    if path:
        try:
            seg = runs.prompt_segments(miss.get("run") or "", miss.get("video") or "", path)
        except Exception:  # noqa: BLE001  (a run that cannot be read: the saved generations, else the frame keeps its video with no generation)
            seg = {}
        if seg.get("kind") in ("clips", "clip") and seg.get("segments"):
            return {"segments": seg["segments"], "inserts": seg.get("inserts") or []}
        if seg.get("kind") == "clips":
            return {"segments": [], "inserts": [], "unknown": True}
    saved: list[dict] = []
    for side in ("wrong", "fixed"):
        raw = miss.get(f"{side}_segments")
        for s in (_segments_of(raw) if isinstance(raw, str) or raw is None else raw):
            if (s.get("src") or side) == src and isinstance(s.get("gen"), int) and not any(x.get("clip") == s.get("clip") and x.get("attempt") == s.get("attempt") for x in saved):
                saved.append(s)
    return {"segments": sorted(saved, key=lambda s: _spans(s)[0][0] if _spans(s) else 0.0), "inserts": []}


def _frame_generation(take, t: float) -> dict | None:
    """The generation of a take that made the frame at t: its number in the take, its clip, the span holding t and all its spans; inside an insert, that
    insert (not generated); after the last generation, where it ends."""
    segs = take.get("segments") if isinstance(take, dict) else (take or [])
    if not segs:
        return None
    end = max(b for s in segs for _a, b in _spans(s))
    for k, s in enumerate(segs):
        if _in_spans(float(t), _spans(s), end):
            a, b = next(((a, b) for a, b in _spans(s) if a - 1e-6 <= t <= b + 1e-3), _spans(s)[0])
            g = {"gen": int(s.get("gen") or k + 1), "of": int(s.get("of") or len(segs)), "clip": str(s.get("clip") or ""), "t_from": round(a, 3), "t_to": round(b, 3)}
            if len(_spans(s)) > 1:
                g["spans"] = _spans(s)
            return g
    for a, b, f in (take.get("inserts") or []) if isinstance(take, dict) else []:
        if a - 1e-6 <= t < b:
            return {"insert": str(f), "t_from": round(a, 3), "t_to": round(b, 3)}
    return {"after": round(end, 3)} if t > end else None


# ------------------------------------------------------------------ what Fill writes in each prompt box (the user, 2026-10-29)
# "if two or more samples are from one video generation only ... include that generation prompts in the prompt filler not more, but if the frames used as
# wrong, right or examples are from more than a single generation ... then in the prompt fill of flawed versions include the video prompts of the
# generations whom's frames turned out wrong ..., and in the right/correct prompt filler box put the prompts that generated the correct version ..., but
# if there's only 3 right frames or 3 wrong frames, or 3 examples then only fill in one prompt".  A frame called WRONG feeds the flawed version's box
# (column wrong), one called RIGHT the correct version's box (column fixed), from whichever video it comes from; an Example feeds the one box of the video
# it comes from.  Every sample of the report counts (the boxes belong to the report), each frame goes to the generation that made it (read from the run's
# join), and a box holds the prompt of each of those generations and of no other; a box no frame feeds stays empty.  One implementation serves the
# report form (POST /api/misses/prompt-boxes), the boxes kept in step after a sample is saved or deleted, and the library's own maintenance.
COLUMN_OF_ROLE = {"wrong": "wrong", "right": "fixed"}
BOX_ROLE = {"wrong": "flawed", "right": "correct", "example": "example"}


def _column(f: dict) -> str:
    return COLUMN_OF_ROLE.get(f["role"]) or f["src"]


def _box_take(run: str, video: str, path: str) -> dict:
    """What the run says of one of the report's videos, for the boxes: a joined take (its generations and their spans), a take of one generation (its
    prompt), a joined take whose seconds cannot be told, or nothing."""
    if not path:
        return {"kind": "none"}
    seg = runs.prompt_segments(run, video, path)
    if seg.get("kind") == "clips":
        if seg.get("spans_known") and seg.get("segments"):
            return {"kind": "joined", "segments": seg["segments"], "inserts": seg.get("inserts") or [], "note": seg.get("note", "")}
        return {"kind": "unknown", "note": seg.get("note", "")}
    if seg.get("kind") == "clip" and seg.get("segments"):
        s = seg["segments"][0]
        return {"kind": "single", "prompt": s["prompt"], "request": s["request"], "attempt": s["attempt"], "clip": s["clip"], "duration": s["t_to"], "note": seg.get("note", "")}
    r = runs.request_prompt_for(run, video, path)
    if not r.get("prompt"):
        return {"kind": "none", "note": r.get("note") or "no request file with a prompt was found for that version: paste it"}
    try:
        dur = round(float(media.probe(media.resolve_media(path)).get("duration") or 0), 3)
    except (OSError, PermissionError, ValueError):
        dur = 0.0
    return {"kind": "single", "prompt": r["prompt"], "request": r.get("request", ""), "attempt": r.get("attempt", 0), "clip": "", "duration": dur, "note": ""}


def prompt_boxes(miss: dict | None, run: str, video: str, paths: dict, frames_now: list[dict] | None = None, editing: int | None = None) -> dict:
    """What Fill writes in each of a report's two prompt boxes (see above): {"wrong": box, "fixed": box, "notes": [...]}, a box being {used, role
    (flawed | correct | example, or several joined by '+'), text, segments (kept beside the text: the generations of a take made of several), frames (the
    frames that feed it, each with the generation that made it), problems (frames no prompt can be tied to, and why)}.  `frames_now` are the frames on
    screen (the sample being added, or `editing`, the sample being changed); the report's other saved samples count too.  A report in words only (no frame
    at all) gives each box its own video's whole take, as before."""
    fr = []
    for m in (miss or {}).get("moments") or []:
        if editing and m.get("moment_id") == editing:
            continue
        for i, f in enumerate(m.get("frames") or []):
            fr.append({"src": f["src"], "role": f["role"], "t": float(f["t"]), "n": i + 1, "sample": m.get("id") or f"S{m.get('moment_id')}"})
    here = f"S{editing}" if editing else "the sample being added"
    for i, f in enumerate(frames_now or []):
        fr.append({"src": "fixed" if f.get("src") == "fixed" else "wrong", "role": {"right": "right", "example": "example"}.get(f.get("role"), "wrong"),
                   "t": max(0.0, float(f.get("t") or 0)), "n": i + 1, "sample": here})
    if len({f["sample"] for f in fr}) <= 1:
        for f in fr:
            f.pop("sample", None)          # one sample: its frames are named 'of this sample'
    takes: dict = {}

    def take(src):
        if src not in takes:
            takes[src] = _box_take(run, video, paths.get(src) or "")
        return takes[src]
    out: dict = {"notes": []}
    for col in ("wrong", "fixed"):
        box = {"used": False, "role": "", "text": "", "segments": [], "frames": [], "problems": []}
        out[col] = box
        if not fr:       # a scenario in words only: the box of each video holds that video's whole take
            tk = take(col)
            if tk["kind"] == "single":
                box.update(used=bool(tk["prompt"]), text=tk["prompt"])
            elif tk["kind"] == "joined":
                segs = [dict(s, src=col) for s in tk["segments"]]
                box.update(used=True, segments=segs, text=compose_text(segs, col))
            elif tk.get("note"):
                box["problems"].append(tk["note"])
            continue
        feed = [f for f in fr if _column(f) == col]
        if not feed:
            continue
        box["used"] = True
        box["role"] = "+".join(dict.fromkeys(BOX_ROLE[f["role"]] for f in feed))
        gens: dict = {}
        for f in sorted(feed, key=lambda f: (f["src"] != "wrong", f["t"])):
            tk = take(f["src"])
            word = frames_words([f]) + (f" of {f['sample']}" if f.get("sample") else "")
            if tk["kind"] == "joined":
                g = _frame_generation(tk, f["t"])
                s = next((x for x in tk["segments"] if g and g.get("clip") == x["clip"] and g.get("gen") == x["gen"]), None)
                if not s:
                    why = (f"inside {g['insert']}, which no generation made" if g and g.get("insert") else
                           f"after the end of the last generation (second {_secs(g['after'])})" if g and g.get("after") is not None else "in no generation's seconds")
                    box["problems"].append(f"{word} of {VIDEO_NAME[f['src']]} is {why}: no prompt made it")
                    continue
                key = (f["src"], s["clip"], s["attempt"])
                gens.setdefault(key, {**s, "src": f["src"], "frames": []})["frames"].append(f)
            elif tk["kind"] == "single":
                key = (f["src"], tk["clip"], tk["attempt"])
                gens.setdefault(key, {"src": f["src"], "clip": tk["clip"], "attempt": tk["attempt"], "request": tk["request"], "t_from": 0.0,
                                      "t_to": tk["duration"] or 0.0, "spans": [[0.0, tk["duration"]]] if tk["duration"] else [], "gen": 1, "of": 1,
                                      "prompt": tk["prompt"], "single": True, "frames": []})["frames"].append(f)
            else:
                box["problems"].append(f"{word} of {VIDEO_NAME[f['src']]}: " + (tk.get("note") or f"{VIDEO_NAME[f['src']]} is not picked"))
        if box["problems"] and any("could not be read" in p or "not picked" in p for p in box["problems"]):
            continue         # a frame whose generation cannot be told: nothing is written for it, never a guess
        order = sorted(gens.values(), key=lambda g: (g["src"] != "wrong", int(g.get("gen") or 1)))
        box["frames"] = [{"src": f["src"], "role": f["role"], "t": f["t"], "n": f["n"], **({"sample": f["sample"]} if f.get("sample") else {}),
                          "clip": g.get("clip") or "", "gen": g.get("gen"), "of": g.get("of")} for g in order for f in g["frames"]]
        if len(order) == 1 and order[0].get("single"):
            box["text"] = order[0]["prompt"]          # one take of one generation: its prompt, as before
            continue
        box["segments"] = [{k: v for k, v in g.items() if k not in ("frames", "single")} for g in order]
        box["text"] = "\n\n".join(f"{segment_line(dict(g, frames=g['frames']))}\n{g.get('prompt') or ''}" for g in order)
    for src in ("wrong", "fixed"):
        tk = takes.get(src)
        if tk and tk.get("note") and tk["kind"] == "joined":
            out["notes"].append(f"{VIDEO_NAME[src]}: {tk['note']}")
    return out


def refresh_boxes(miss_id: int, actor: str = "system") -> dict:
    """Keeps a report's boxes that Loupe wrote from a joined take (their generations are kept beside them) in step with its samples after a sample is
    saved or deleted: the prompts of the generations the frames come from, named with their samples, and an emptied box when no frame feeds it any more.
    A box typed by hand, or holding one generation's prompt alone, is left as it is."""
    m = get(miss_id)
    if not m:
        return {}
    cols = {}
    boxes = None
    for side in ("wrong", "fixed"):
        if not m.get(f"{side}_segments"):
            continue
        if boxes is None:
            try:
                boxes = prompt_boxes(m, m.get("run") or "", m.get("video") or "", {"wrong": m.get("wrong_path") or "", "fixed": m.get("fixed_path") or ""})
            except Exception as e:  # noqa: BLE001  (the run cannot be read: the boxes stay as saved)
                core.audit(actor, "miss.boxes.unread", f"miss {miss_id}", str(e)[:300])
                return {}
        b = boxes[side]
        if b["problems"] and not b["text"]:
            continue
        segs = _segments_clean(b["segments"], side)
        new_text = b["text"] if b["used"] else ""
        if new_text != (m.get(f"{side}_prompt") or "") or json.dumps(segs) != json.dumps(_segments_clean(m.get(f"{side}_segments"), side)):
            cols[f"{side}_prompt"], cols[f"{side}_segments"] = new_text, (json.dumps(segs, ensure_ascii=False) if _multi(segs) else "")
    if cols:
        cols["updated"] = core.now()
        con = core.db()
        try:
            con.execute("UPDATE misses SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?", list(cols.values()) + [miss_id])
            con.commit()
        finally:
            con.close()
        core.audit(actor, "miss.boxes", f"miss {miss_id}", ",".join(k for k in cols if k != "updated"))
    return cols


def regenerate(mid: int):
    """Cut every frame of the moment, draw the admin's marks on each, build the composite and the zoom, and store them.  Every frame is numbered as the saved
    prompts number it (FRAME n), names its video (VIDEO A or B) and, for a take made of several generations, the generation that made it (the user,
    2026-10-28: the frame must tie back to the prompt that made it, in the image and wherever the pipeline reads the sample)."""
    con = core.db()
    try:
        mr = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        miss = con.execute("SELECT * FROM misses WHERE id=?", (mr["miss_id"],)).fetchone()
    finally:
        con.close()
    frames = frames_of(mr, dict(miss))
    shapes = compose.clean_shapes(json.loads(mr["shapes"] or "[]"))
    descs = {src: side_desc(dict(miss), src) for src in ("wrong", "fixed")}       # what each video is: the take as generated, the right version, the original, another video's take
    takes = {src: _take_generations(dict(miss), src) for src in {f["src"] for f in frames}}
    gens = [_frame_generation(takes.get(f["src"]) or [], f["t"]) for f in frames]
    items, pngs = [], []
    for i, f in enumerate(frames):
        path = miss["fixed_path"] if f["src"] == "fixed" else miss["wrong_path"]
        if not path:             # its video was cleared from the report
            continue
        png = media.frame_file(media.resolve_media(path), f["t"])
        items.append({"img": Image.open(png).convert("RGB"), "role": f["role"], "desc": descs[f["src"]], "t": f["t"], "shapes": [s for s in shapes if s["frame"] == i],
                      "n": i + 1, "video": VIDEO_LETTER[f["src"]], "gen": gens[i]})
        pngs.append(png)
    tw, tf = _times(frames)
    meta = {"title": miss["title"], "model": miss["model"], "run": miss["run"], "video": miss["video"], "t_wrong": tw, "t_fixed": tf, "sample": f"S{mid}",
            "frames": [{"n": it["n"], "role": it["role"], "from": it["desc"], "video": it["video"], "t": it["t"], **({"generation": it["gen"]} if it["gen"] else {})}
                       for it in items]}
    samples = []
    if miss["sample_on"] != 0:
        try:       # the files the admin ticked, else the product image(s) the video used, straight from the client's reference folder; never a reason to fail the save
            chosen = [x for x in _sample_files(miss["sample_files"] if "sample_files" in miss.keys() else "") if Path(x["path"]).is_file()]
            picks = ([{"path": Path(x["path"]), "label": x.get("label") or Path(x["path"]).name, "asset_id": x.get("asset_id") or "", "name": Path(x["path"]).name} for x in chosen]
                     or runs.product_samples(miss["run"], miss["video"], miss["wrong_label"] or miss["wrong_path"], path=miss["wrong_path"] or ""))
            meta["product_sample_chosen"] = bool(chosen)
            for ps in picks:
                samples.append((compose.load_product(ps["path"]), ps["label"]))
                meta.setdefault("product_samples", []).append({"asset": ps["asset_id"], "label": ps["label"], "file": ps["name"]})
        except Exception as e:  # noqa: BLE001
            core.audit("system", "product-sample.failed", f"miss {miss['id']}", str(e)[:300])
            samples = []
    comp, zoom, ann = compose.build(items, meta, mr["details"] or miss["explanation"] or "", mr["correct_note"] or "", product_samples=samples,
                                    idea=mr["idea"] or "", better=mr["better"] or "", missed=mr["missed"] or "")
    d = sample_dir(mid)
    d.mkdir(parents=True, exist_ok=True)
    for old in list(d.glob("frame-*.png")) + [d / n for n in ("wrong.png", "annotated.png", "fixed.png", "annotated_fixed.png", "zoom.png")]:
        if old.exists():
            old.unlink()
    for k, (png, a_img) in enumerate(zip(pngs, ann), 1):          # every frame, plain and marked
        shutil.copyfile(png, d / f"frame-{k}.png")
        a_img.save(d / f"frame-{k}-marked.png")
    for role, plain, marked in (("wrong", "wrong.png", "annotated.png"), ("right", "fixed.png", "annotated_fixed.png")):      # the first of each role keeps its older name
        k = next((k for k, it in enumerate(items) if it["role"] == role), None)
        if k is not None:
            shutil.copyfile(pngs[k], d / plain)
            ann[k].save(d / marked)
    comp.save(d / "composite.png", optimize=True)
    if zoom:
        zoom.save(d / "zoom.png", optimize=True)
    # a moment saved with frames keeps each frame's source words with it, so the listings (the QC library, the keeper's digest) never read the run again
    stored = (core.jdump([{**{k: v for k, v in f.items() if k != "gen"}, "desc": descs[f["src"]], "video": VIDEO_LETTER[f["src"]], **({"gen": g} if g else {})}
                          for f, g in zip(frames, gens)])
              if "frames" in mr.keys() and mr["frames"] not in (None, "") else None)
    con = core.db()
    try:
        wh = (items[0]["img"].width if items else None, items[0]["img"].height if items else None, core.now())
        if stored is not None:
            con.execute("UPDATE moments SET w=?, h=?, updated=?, frames=? WHERE id=?", (*wh, stored, mid))
        else:
            con.execute("UPDATE moments SET w=?, h=?, updated=? WHERE id=?", (*wh, mid))
        con.commit()
    finally:
        con.close()
    (d / "meta.json").write_text(json.dumps({"moment": mid, "miss": miss["id"], **meta, "shapes": shapes}, ensure_ascii=False, indent=1), encoding="utf-8")
    sync_prompt_roles(miss["id"])       # what each of the report's prompts is, kept in the database


def moment_file(mid: int, kind: str) -> Path | None:
    name = {"composite": "composite.png", "zoom": "zoom.png", "wrong": "wrong.png", "fixed": "fixed.png", "annotated": "annotated.png", "annotated_fixed": "annotated_fixed.png"}.get(kind)
    if not name and re.fullmatch(r"frame-\d{1,2}(-marked)?", kind or ""):       # frame-3 (plain) or frame-3-marked
        name = kind + ".png"
    p = sample_dir(mid) / (name or "x")
    return p if name and p.is_file() else None


def _generations(miss: dict, side: str) -> int:
    """How many generations made the take a box's prompts come from, as the report keeps them: the take's own count when its segments carry it ('of'),
    else the number of its segments, 1 for a single prompt, 0 for none."""
    segs = miss.get(f"{side}_segments")
    segs = _segments_of(segs) if isinstance(segs, str) else (segs or [])
    n = max([int(s.get("of") or 0) for s in segs if isinstance(s, dict)] + [len(segs)])
    return n if segs else (1 if (miss.get(f"{side}_prompt") or "").strip() else 0)


def moment_view(r, miss: dict) -> dict:
    mid = r["id"]
    kinds = [k for k in (r["kinds"] or "").split(",") if k] or miss.get("kinds", [])
    d = sample_dir(mid)
    frames = frames_of(r, miss)
    if any("gen" not in f for f in frames):       # an older sample keeps the generation of each frame only in the meta its image was built with
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("frames") or []
        except (OSError, ValueError, AttributeError):
            meta = []
        for f, mf in zip(frames, meta):
            if ("gen" not in f and isinstance(mf, dict) and mf.get("role") == f["role"] and isinstance(mf.get("generation"), dict)
                    and abs(float(mf.get("t") or 0) - float(f["t"])) < 1e-3):
                f["gen"] = mf["generation"]
    has_wrong, has_right = any(f["role"] == "wrong" for f in frames), any(f["role"] == "right" for f in frames)
    return {"moment_id": mid, "frames": [dict(f, desc=f.get("desc") or FRAME_DESC[f["src"]], video=f.get("video") or VIDEO_LETTER[f["src"]]) for f in frames],
            "n_frames": len(frames), "has_right_version": miss.get("fixed_path") or "",
            "has_wrong_version": miss.get("wrong_path") or "", "has_wrong": has_wrong, "sample_kind": ("pair" if has_right else "miss-only") if has_wrong else "scenario",
            "idea": r["idea"] or "", "better": r["better"] or "", "missed": r["missed"] or "", "id": f"S{mid}", "miss_id": miss["id"], "title": miss["title"], "explanation": miss["explanation"], "details": r["details"],
            "correct_note": r["correct_note"], "kinds": kinds, "model": miss["model"], "mode": miss["mode"], "product": miss["product"],
            "run": miss["run"], "video": miss["video"], "t_wrong": r["t_wrong"], "t_fixed": r["t_fixed"], "shapes": compose.clean_shapes(json.loads(r["shapes"] or "[]")),
            "status": r["status"], "miss_status": miss["status"], "created": r["created"], "updated": r["updated"],
            "prompts": sample_prompts_kind(miss, frames), "prompt_note": miss.get("prompt_note") or "", "prompts_sig": prompts_sig(miss), "prompt_roles": prompt_roles(frames),
            # the boxes this sample speaks through: the flawed version's for frames called Wrong, the correct version's for frames called Right, one for
            # an Examples sample
            "prompt_sides": prompt_sides(frames),
            "prompt_generations": {side: (_generations(miss, side) if side in prompt_sides(frames) else 0) for side in ("wrong", "fixed")},
            "has_right": has_right, "image_url": f"/api/moments/{mid}/image?kind=composite", "zoom_url": f"/api/moments/{mid}/image?kind=zoom" if (d / "zoom.png").exists() else "",
            "composite_path": str(d / "composite.png")}


def prompts_of(mid: int) -> dict | None:
    """The prompts one sample (moment id) speaks through, whole, with the admin's note: only the boxes of its report it has frames for (prompt_sides;
    the other box comes back empty) and, in each, only the generations its frames come from.  For the single-sample route (?prompts=1) and the
    keeper's sample_origin fetch only.  None when the sample does not exist; empty strings when the admin saved no prompt."""
    con = core.db()
    try:
        mr = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        r = con.execute("SELECT * FROM misses WHERE id=?", (mr["miss_id"],)).fetchone() if mr else None
    finally:
        con.close()
    if not r:
        return None
    m = dict(r)
    d = {k: (m.get(k) or "") for k in ("title", "model", "run", "video", "wrong_prompt", "fixed_prompt", "prompt_note")}
    d.update({"id": f"S{mid}", "miss_id": m["id"]})
    d["frames"] = [{"n": i + 1, "src": f["src"], "role": f["role"], "t": f["t"]} for i, f in enumerate(frames_of(mr, m))]
    d["prompt_roles"] = prompt_roles(d["frames"])       # a side that speaks only for Example frames (or a sample of Examples only) has an Example prompt
    d["examples_only"] = bool(d["frames"]) and all(f["role"] == "example" for f in d["frames"])
    # an Examples sample has one box, its one video's; otherwise one box per kind of frame (Wrong: the flawed version's, Right: the correct version's)
    d["prompt_sides"] = prompt_sides(d["frames"])
    for side in ("wrong", "fixed"):          # a take made of several generations: the prompt of each generation this sample's frames come from, its seconds, those frames
        if side not in d["prompt_sides"]:
            d[f"{side}_prompt"], d[f"{side}_segments"] = "", []      # a box this sample has no frame for (no frame called WRONG, or RIGHT; the other box of an Examples sample)
            continue
        segs = _segments_of(m.get(f"{side}_segments"))
        d[f"{side}_segments"] = (_with_frames(segs, d["frames"], side) if d["frames"] else _numbered(segs, side)) if segs else []
        if segs and not d[f"{side}_segments"]:
            d[f"{side}_prompt"], d[f"{side}_unmatched"] = "", True     # the box holds generations none of this sample's frames come from
    d["prompts"] = prompts_kind(d)
    d["text"] = prompts_text(d)
    return d


def moment(mid: int) -> dict | None:
    con = core.db()
    try:
        r = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        miss = get(r["miss_id"])
        return moment_view(r, {k: v for k, v in miss.items() if k != "moments"})
    finally:
        con.close()
