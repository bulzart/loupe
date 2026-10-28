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
# the version as generated, "fixed" = the right version), has its own time, and is called WRONG, RIGHT or EXAMPLE (role "example": something the admin keeps for the QC keeper to understand and remember, neither a miss nor the right look) by the admin; the marks belong to a
# frame (its index).  An older moment holds no frames column at all: it is the miss frame at t_wrong and, when the report has a right version, the right frame at t_fixed.
FRAME_DESC = {"wrong": "as generated", "fixed": "correct version"}
ORIGINAL_DESC = "original video"      # either video can be the original the generation was made from, or a take of another video or run: its frames' tags say so (side_desc)
TITLE_MAX = 2000       # the title is often a whole paragraph and is burned into the image in full: never cut it short
# The prompts of the two takes: the prompt that generated Video A (the flawed version) and the prompt of Video B (the correct take), plus one line of the admin's own
# words on what in the prompt made the difference.  They live in the misses table only: never in the composite or its meta, never in the keeper's digest, and
# nothing here compares or diffs them (the keeper and the agents read both whole, on request, and judge what changed).  Every listing carries only the marker
# `prompts` (pair | wrong | fixed | empty) and the note; the pair itself is served by prompts_of() to the single-sample route and the keeper's sample_origin fetch.
PROMPT_COLS = ("wrong_prompt", "fixed_prompt", "prompt_note")
PROMPT_MAX = 12000     # per prompt: a storage bound, far above any video model's prompt length
SAMPLE_FILES_MAX = 4   # images the admin may tick for the product sample (the bottom band splits its half between them)
# A take made of several generations (clips joined) keeps the prompt of each generation with the seconds of that take it made (wrong_segments,
# fixed_segments: [{src, clip, attempt, request, t_from, t_to, prompt}]; src is the video the take is: "wrong" Video A, "fixed" Video B).  One prompt
# field per side (the user, 2026-10-28): Video A's holds the prompt of each generation the frames called WRONG come from, Video B's the prompt of each
# generation the frames called RIGHT come from, one after the other, each under the line that says which seconds of which video it is responsible for
# (segment_line).  The field keeps its text as the form wrote it; the generations kept beside it let prompts_of() tell the judge, sample by sample,
# which generations that sample's frames come from.  A prompt typed by hand replaces them.
SEGMENT_COLS = ("wrong_segments", "fixed_segments")
SEGMENTS_MAX = 12
ROLE_WORD = {"wrong": "WRONG", "right": "RIGHT", "example": "EXAMPLE"}
SIDE_ROLE = {"wrong": "wrong", "fixed": "right"}      # the frames each side's prompts are for
VIDEO_NAME = {"wrong": "Video A", "fixed": "Video B"}


def _segments_clean(v, side: str) -> list[dict]:
    out = []
    for x in (v if isinstance(v, list) else [])[:SEGMENTS_MAX]:
        if not isinstance(x, dict):
            continue
        try:
            a, b = float(x.get("t_from") or 0), float(x.get("t_to") or 0)
        except (TypeError, ValueError):
            continue
        if b <= a:
            continue
        att, src = str(x.get("attempt") or "0"), str(x.get("src") or side)
        out.append({"src": src if src in VIDEO_NAME else side, "clip": str(x.get("clip") or "")[:12], "attempt": int(att) if att.isdigit() else 0,
                    "request": str(x.get("request") or "")[:160], "t_from": round(max(0.0, a), 3), "t_to": round(b, 3), "prompt": _prompt_text(x.get("prompt"))})
    out.sort(key=lambda s: (s["src"] != "wrong", s["t_from"]))
    return out if any(s["prompt"] for s in out) else []


def _segments_of(raw) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _secs(x) -> str:
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def _numbered(segs: list[dict], side: str) -> list[dict]:
    """Each generation with its video (src) and its number in that video's take (generation `gen` of `of`)."""
    srcs = [s.get("src") or side for s in segs]
    seen: dict = {}
    out = []
    for s, src in zip(segs, srcs):
        seen[src] = seen.get(src, 0) + 1
        out.append({**s, "src": src, "gen": seen[src], "of": srcs.count(src)})
    return out


def _mmss(t) -> str:
    t = max(0.0, float(t or 0))
    return f"{int(t // 60)}:{t - int(t // 60) * 60:05.2f}"


def frames_words(frames: list[dict]) -> str:
    """'Frame 1 (WRONG, 0:03.00) and Frame 3 (WRONG, 0:05.20)': the frames as the sample's image tags them (FRAME n, the role, the time)."""
    it = [f"Frame {f['n']} ({ROLE_WORD.get(f['role'], str(f['role']).upper())}, {_mmss(f['t'])})" for f in frames]
    return it[0] if len(it) == 1 else ", ".join(it[:-1]) + " and " + it[-1]


def segment_line(s: dict) -> str:
    """The line over a generation's prompt (the user, 2026-10-28): the frames of the sample it is responsible for, named as the image tags them, then the
    seconds of which video it generated; with no frame known, only the seconds."""
    where = (f"second {_secs(s['t_from'])} to second {_secs(s['t_to'])} of {VIDEO_NAME.get(s.get('src') or '', 'this video')} (generation "
             f"{s.get('gen') or 1} of {s.get('of') or 1} · clip {s.get('clip') or '-'} · {s.get('request') or 'request not named'})")
    if "frames" not in s:
        return f"This is the prompt that generated {where}."
    if s["frames"]:
        return f"This is the prompt responsible for {frames_words(s['frames'])} of this sample: it generated {where}."
    return f"This is the prompt that generated {where}; no frame of this sample comes from it."


def compose_text(segs: list[dict], side: str = "") -> str:
    """The prompts of a take's generations as one text, each under its line (no frames named): a side's prompt field when only its generations came."""
    return "\n\n".join(f"{segment_line(s)}\n{s.get('prompt') or ''}" for s in _numbered(segs, side))


ROLE_DEFAULT = {"wrong": "flawed", "fixed": "correct"}      # what a report's two prompts are unless they are an Example's


def prompt_roles(frames: list[dict]) -> dict:
    """What each side's prompt stands for in one sample, from the frames it speaks for (the user, 2026-10-28: a sample whose frames are only Examples has
    Example prompts, never a flawed or a correct one, so the judge, the keeper and every agent fed with it are never misled; otherwise Video A's prompt is
    the one that generated the flawed version and Video B's the correct take's).  'example' for a side that speaks only for frames called EXAMPLE (no
    frame called WRONG for Video A, none called RIGHT for Video B, and an Example taken from that side's video), and for both sides when every frame of
    the sample is an Example: just an example, neither flawed nor correct; else 'flawed' (Video A) and 'correct' (Video B)."""
    only_examples = bool(frames) and all(f["role"] == "example" for f in frames)
    out = {}
    for side in ("wrong", "fixed"):
        own = any(f["role"] == SIDE_ROLE[side] for f in frames)
        out[side] = "example" if not own and (only_examples or any(f["role"] == "example" and f["src"] == side for f in frames)) else ROLE_DEFAULT[side]
    return out


def take_words(side: str, role: str, only_examples: bool = False) -> str:
    """How a side's take is named to the judge and the agents: the flawed take (its prompt generated the flawed version), the correct take, or an example
    take of Video A or B (just an example: neither flawed nor correct)."""
    if role == "example":
        why = "this sample's frames are all Examples" if only_examples else "the frames of this sample it speaks for are called EXAMPLE"
        return f"the example take of {VIDEO_NAME[side]} (just an example, {why}: neither flawed nor correct)"
    return "the flawed take (Video A: its prompt generated the flawed version)" if side == "wrong" else "the correct take (Video B)"


def sync_prompt_roles(miss_id: int) -> dict:
    """Keeps on the report, in the database, what each of its two prompts is (the user, 2026-10-28): Video A's prompt is the one that generated the flawed
    version ('flawed') and Video B's the correct take's ('correct'), unless in every sample of the report that side's prompt is an Example's ('example':
    just an example, neither flawed nor correct).  Run after every change to the report or its samples; the samples' own words come from prompt_roles."""
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


def _with_frames(segs: list[dict], frames: list[dict], side: str) -> list[dict]:
    """The generations of a side's take that a sample's frames come from, each numbered in its take and with those frames (numbered as in the sample's
    image); every generation, with no frame, when none of them holds one.  A side's frames are the frames called WRONG (the flawed take's prompts) or
    RIGHT (the correct take's), else the frames called EXAMPLE taken from that side's video."""
    mine = [f for f in frames if f["role"] == SIDE_ROLE[side]] or [f for f in frames if f["src"] == side and f["role"] == "example"]
    out = []
    for s in _numbered(segs, side):
        last = s["gen"] == s["of"]
        fr = [f for f in mine if f["src"] == s["src"] and s["t_from"] - 1e-6 <= f["t"] and (f["t"] <= s["t_to"] + 1e-3 if last else f["t"] < s["t_to"])]
        out.append({**s, "frames": [{"n": f["n"], "role": f["role"], "t": f["t"]} for f in fr]})
    return [s for s in out if s["frames"]] or out


def prompts_text(pp: dict) -> str:
    """The prompts of a sample's two takes as the judge reads them, whole.  A take made of several generations gives the prompt of each generation this
    sample's frames come from (all of them when none does), each under the line that says which seconds it is responsible for and which frames of
    this sample it made."""
    lines = []
    roles, only_ex = pp.get("prompt_roles") or {}, bool(pp.get("examples_only"))
    if only_ex:
        lines.append("this sample's frames are all Examples: the prompts below made frames the admin keeps as examples, neither a miss nor the corrected look")
    for side in ("wrong", "fixed"):
        what = take_words(side, roles.get(side, ""), only_ex)
        segs = pp.get(f"{side}_segments") or []
        if segs:
            of = max(int(s.get("of") or 0) for s in segs) or len(segs)
            lines.append(f"{what} was made of {of} generations, each with its own prompt; "
                         + ("the prompt of each generation this sample's frames come from:" if len(segs) < of else "the prompt of each:"))
            lines += [f"{segment_line(s)}\n{(s.get('prompt') or '').strip() or 'not saved'}" for s in segs]
        else:
            txt = (pp.get(f"{side}_prompt") or "").strip()
            lines.append(f"the prompt of {what} (whole, as the admin saved it):\n{txt or 'not saved'}")
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
    return str(v or "").replace("\r\n", "\n").strip()[:PROMPT_MAX]


def prompts_kind(miss: dict) -> str:
    """Which prompts the report holds: 'pair' (both takes), 'wrong' (Video A's only: the flawed version's, or an Example's), 'fixed' (Video B's only) or ''."""
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
    prompts, segcols = {k: _prompt_text(data.get(k)) for k in ("wrong_prompt", "fixed_prompt")}, {}
    for side in ("wrong", "fixed"):          # a take made of several generations: each one's prompt and seconds, kept beside the side's prompt text
        segs = _segments_clean(data.get(f"{side}_segments"), side)
        segcols[side] = json.dumps(segs, ensure_ascii=False) if len(segs) >= 2 else ""
        if segs and not prompts[f"{side}_prompt"]:
            prompts[f"{side}_prompt"] = compose_text(segs, side) if len(segs) >= 2 else segs[0]["prompt"]
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
    for k in PROMPT_COLS:       # the prompts of the two takes: stored, never burned in (no image is rebuilt for them)
        if k in patch:
            cols[k] = _prompt_text(patch[k])[:1000] if k == "prompt_note" else _prompt_text(patch[k])
    for side in ("wrong", "fixed"):          # the generations of a side's take, kept beside its prompt text (written from them when no text came); a prompt typed alone replaces them
        sk, pk = f"{side}_segments", f"{side}_prompt"
        if sk in patch:
            segs = _segments_clean(patch[sk], side)
            cols[sk] = json.dumps(segs, ensure_ascii=False) if len(segs) >= 2 else ""
            if segs and not _prompt_text(patch.get(pk)):
                cols[pk] = compose_text(segs, side) if len(segs) >= 2 else segs[0]["prompt"]
        elif pk in patch and cur.get(sk) and _prompt_text(patch[pk]) != (cur.get(pk) or ""):
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


def _take_generations(miss: dict, src: str) -> list[dict]:
    """The generations one of the report's videos was made of, each with its clip and seconds: the ones kept with the prompt fields for that video (src),
    else read from the run (runs.prompt_segments: a take joined from clips, or a take of one clip); [] for a video of one generation or one that cannot be
    read.  Facts only: they number a frame's generation in its tag, so a frame ties back to the prompt that made it."""
    saved: list[dict] = []
    for side in ("wrong", "fixed"):
        raw = miss.get(f"{side}_segments")
        for s in (_segments_of(raw) if isinstance(raw, str) or raw is None else raw):
            if (s.get("src") or side) == src and not any(x.get("clip") == s.get("clip") and abs(float(x["t_from"]) - float(s["t_from"])) < 1e-3 for x in saved):
                saved.append(s)
    if len(saved) >= 2:
        return sorted(saved, key=lambda s: float(s["t_from"]))
    path = miss.get("fixed_path" if src == "fixed" else "wrong_path") or ""
    if not path:
        return []
    try:
        seg = runs.prompt_segments(miss.get("run") or "", miss.get("video") or "", path)
    except Exception:  # noqa: BLE001  (a run that cannot be read: the frame keeps its video, with no generation)
        return []
    return (seg.get("segments") or []) if seg.get("kind") in ("clips", "clip") else []


def _frame_generation(segs: list[dict], t: float) -> dict | None:
    """The generation of a take that made the frame at t: its number in the take, its clip and its seconds; after the last one, where that one ends."""
    if not segs:
        return None
    n = len(segs)
    for k, s in enumerate(segs):
        a, b = float(s["t_from"]), float(s["t_to"])
        if a - 1e-6 <= t and (t <= b + 1e-3 if k == n - 1 else t < b):
            return {"gen": k + 1, "of": n, "clip": str(s.get("clip") or ""), "t_from": round(a, 3), "t_to": round(b, 3)}
    return {"after": round(float(segs[-1]["t_to"]), 3)} if t > float(segs[-1]["t_to"]) else None


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
    """How many generations made a take, as the report keeps their prompts: the number of its segments, 1 for a single prompt, 0 for none."""
    segs = miss.get(f"{side}_segments")
    n = len(_segments_of(segs) if isinstance(segs, str) else (segs or []))
    return n if n >= 2 else (1 if (miss.get(f"{side}_prompt") or "").strip() else 0)


def moment_view(r, miss: dict) -> dict:
    mid = r["id"]
    kinds = [k for k in (r["kinds"] or "").split(",") if k] or miss.get("kinds", [])
    d = sample_dir(mid)
    frames = frames_of(r, miss)
    has_wrong, has_right = any(f["role"] == "wrong" for f in frames), any(f["role"] == "right" for f in frames)
    return {"moment_id": mid, "frames": [dict(f, desc=f.get("desc") or FRAME_DESC[f["src"]], video=f.get("video") or VIDEO_LETTER[f["src"]]) for f in frames],
            "n_frames": len(frames), "has_right_version": miss.get("fixed_path") or "",
            "has_wrong_version": miss.get("wrong_path") or "", "has_wrong": has_wrong, "sample_kind": ("pair" if has_right else "miss-only") if has_wrong else "scenario",
            "idea": r["idea"] or "", "better": r["better"] or "", "missed": r["missed"] or "", "id": f"S{mid}", "miss_id": miss["id"], "title": miss["title"], "explanation": miss["explanation"], "details": r["details"],
            "correct_note": r["correct_note"], "kinds": kinds, "model": miss["model"], "mode": miss["mode"], "product": miss["product"],
            "run": miss["run"], "video": miss["video"], "t_wrong": r["t_wrong"], "t_fixed": r["t_fixed"], "shapes": compose.clean_shapes(json.loads(r["shapes"] or "[]")),
            "status": r["status"], "miss_status": miss["status"], "created": r["created"], "updated": r["updated"],
            "prompts": prompts_kind(miss), "prompt_note": miss.get("prompt_note") or "", "prompts_sig": prompts_sig(miss), "prompt_roles": prompt_roles(frames),
            "prompt_generations": {side: _generations(miss, side) for side in ("wrong", "fixed")},
            "has_right": has_right, "image_url": f"/api/moments/{mid}/image?kind=composite", "zoom_url": f"/api/moments/{mid}/image?kind=zoom" if (d / "zoom.png").exists() else "",
            "composite_path": str(d / "composite.png")}


def prompts_of(mid: int) -> dict | None:
    """The prompts of the two takes of the report a sample (moment id) belongs to, whole, with the admin's note: for the single-sample route (?prompts=1)
    and the keeper's sample_origin fetch only.  None when the sample does not exist; empty strings when the admin saved no prompt."""
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
    d.update({"id": f"S{mid}", "miss_id": m["id"], "prompts": prompts_kind(d)})
    d["frames"] = [{"n": i + 1, "src": f["src"], "role": f["role"], "t": f["t"]} for i, f in enumerate(frames_of(mr, m))]
    d["prompt_roles"] = prompt_roles(d["frames"])       # a side that speaks only for Example frames (or a sample of Examples only) has an Example prompt
    d["examples_only"] = bool(d["frames"]) and all(f["role"] == "example" for f in d["frames"])
    for side in ("wrong", "fixed"):          # a take made of several generations: the prompt of each generation this sample's frames come from, its seconds, those frames
        segs = _segments_of(m.get(f"{side}_segments"))
        d[f"{side}_segments"] = _with_frames(segs, d["frames"], side) if len(segs) >= 2 else []
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
