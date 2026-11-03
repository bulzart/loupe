"""Loupe runs: browse the pipeline's runs and videos, and index which input assets were used in which request and what became of it."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from . import core, media

VIDEO_NAMES = ("*.mp4", "*.mov", "*.webm", "*.mkv", "*.m4v")


def runs_roots() -> list[Path]:
    """The runs folder of every pipeline library, in the order of core.LIBS."""
    return [lib / "runs" for _name, lib in core.LIBS]


def runs_root() -> Path:
    """The first library's runs folder (kept for callers that want one)."""
    return runs_roots()[0]


def lib_of(root: Path) -> str:
    for name, lib in core.LIBS:
        if root == lib / "runs":
            return name
    return ""


def _json(p: Path):
    try:
        return json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def run_dir(run: str) -> Path | None:
    if not run or "/" in run or "\\" in run or run.startswith("."):
        return None
    found = [r / run for r in runs_roots() if (r / run).is_dir()]
    if not found:
        return None
    return max(found, key=lambda p: (p / "state.json").stat().st_mtime if (p / "state.json").is_file() else 0) if len(found) > 1 else found[0]


def run_number(name: str) -> str:
    m = re.search(r"-(\d+)$", name)
    return m.group(1).lstrip("0") or "0" if m else ""


def _mp4s(d: Path) -> list[Path]:
    out = []
    for pat in VIDEO_NAMES:
        out += list(d.glob(pat))
    return sorted(out)


def list_runs() -> list[dict]:
    """Every run of every library, newest name first; `lib` says which library a run belongs to."""
    res = []
    for root in runs_roots():
        if not root.is_dir():
            continue
        lib = lib_of(root)
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            st = _json(d / "state.json")
            if st is not None:
                vids = st.get("videos") or []
                res.append({"run": d.name, "lib": lib, "kind": "run", "mode": st.get("mode"), "submode": st.get("analyze_submode"), "model": st.get("model"),
                            "stage": st.get("stage"), "created": st.get("created"), "updated": st.get("updated"),
                            "videos": len(vids) if isinstance(vids, list) else 0, "number": run_number(d.name)})
            elif _mp4s(d):
                res.append({"run": d.name, "lib": lib, "kind": "library", "videos": len(_mp4s(d)), "number": "", "mode": "files", "model": "", "stage": ""})
    return sorted(res, key=lambda r: r["run"], reverse=True)


def _version(p: Path, label: str, kind: str, **extra) -> dict:
    st = p.stat()
    return {"path": str(p), "label": label, "kind": kind, "mtime": st.st_mtime, "size": st.st_size, **extra}


def is_video_request(req) -> bool:
    """True when a request file asked for a video.  A still (a start frame, a guide plate) is asked in its own request file named like the video's
    (video-03-frame-a2-request.json, video-03-g1-request.json): its `tool` says it is an image call, or it has no duration and carries an image model's own
    parameter (a resolution tier such as 2k, a quality).  Anything else is taken for a video."""
    if not isinstance(req, dict):
        return False
    tool = str(req.get("tool") or "").lower()
    if tool:
        return "video" in tool
    prm = req.get("params") if isinstance(req.get("params"), dict) else req
    if prm.get("duration") not in (None, ""):
        return True
    return not (re.fullmatch(r"\d+k", str(prm.get("resolution") or "").lower()) or prm.get("quality"))


REQUEST_ATTEMPT_LAST = re.compile(r"^(?P<head>video-.+?)-request-(?P<att>a\d+)\.json$", re.I)


def request_canon(name: str) -> str:
    """A request file's name in the usual form: a run that wrote the attempt last (video-06-request-a2.json, video-04-c1-request-a2.json; the
    admin, 2026-11-03: its take a2 offered no image) is read as video-06-a2-request.json.  The file keeps its own name; only how it is read
    changes.  The toolkit reads names the same way (awl.py _cf_req_canon)."""
    m = REQUEST_ATTEMPT_LAST.match(str(name))
    return f"{m.group('head')}-{m.group('att')}-request.json" if m else str(name)


def request_files_named(rq: Path, prefix: str) -> list[Path]:
    """The request files of a folder whose name starts with `prefix`, in both forms (…-request.json and …-request-aN.json), ordered by their
    usual-form name; a kept copy (….pre-product-images.json, a draft) is not one."""
    if not rq.is_dir():
        return []
    files = list(rq.glob(f"{prefix}*request.json")) + [f for f in rq.glob(f"{prefix}*request-a*.json") if REQUEST_ATTEMPT_LAST.match(f.name)]
    return sorted(set(files), key=lambda f: request_canon(f.name))


def video_request_files(rd: Path, video: str) -> list[Path]:
    """The request files of a video that asked for a video, by name (the stills asked for beside it are left out)."""
    return [f for f in request_files_named(rd / "50-requests", f"video-{core.nn(video)}") if is_video_request(_json(f))]


def _attempt_in_name(name: str) -> int:
    """The attempt a request file is for: video-03-a2-request.json (or video-03-request-a2.json) is the second, a name with no attempt tag is
    the first."""
    m = re.search(r"-a(\d+)-", request_canon(name))
    return int(m.group(1)) if m else 1


def _attempt_dirs(vd: Path) -> list[Path]:
    return sorted((x for x in vd.iterdir() if x.is_dir() and re.fullmatch(r"a\d+", x.name)), key=lambda x: int(x.name[1:])) if vd.is_dir() else []


def _identical_attempt(rd: Path, nn: str, p: Path) -> int:
    """The highest attempt that holds a file byte for byte equal to p (a clean video or a deliverable is a copy of one attempt's finished file, whatever
    its name says), else 0.  Sizes are compared first; a hash is read only for a file of the same size."""
    try:
        size = p.stat().st_size
    except OSError:
        return 0
    best, sha = 0, ""
    for ad in _attempt_dirs(rd / "60-outputs" / f"video-{core.nn(nn)}"):
        for f in _mp4s(ad):
            try:
                if f.stat().st_size != size:
                    continue
                sha = sha or media.sha1_file(p)
                if media.sha1_file(f) == sha:
                    best = max(best, int(ad.name[1:]))
            except OSError:
                continue
    return best


def recorded_outputs(rd: Path, nn: str) -> dict:
    """What the run itself recorded as the video's finished files: {"clean": Path | None, "preview": Path | None}, from state.json videos[] (the entry the
    pipeline writes when the take is accepted) and the video's result.json.  Read, not inferred: a file named here is the delivered take's."""
    out = {"clean": None, "preview": None}
    st = _json(rd / "state.json") or {}
    ent = next((v for v in (st.get("videos") or []) if isinstance(v, dict) and core.nn(v.get("video")) == core.nn(nn)), {})
    res = _json(rd / "60-outputs" / f"video-{core.nn(nn)}" / "result.json") or {}
    for key in ("clean", "preview"):
        for src in (ent, res):
            v = src.get(key) if isinstance(src, dict) else None
            if isinstance(v, str) and v:
                p = Path(v)
                if p.is_file():
                    out[key] = p
                    break
    return out


def delivered_attempt(rd: Path, nn: str) -> int:
    """The attempt whose finished file is the video's clean video, byte for byte (0 when none is): the take its clean video and preview come from.  The
    clean video is the one the run recorded (state.json videos[].clean, result.json), else the one at its usual place."""
    cands = [recorded_outputs(rd, nn)["clean"], rd / "60-outputs" / f"video-{core.nn(nn)}" / "clean.mp4", rd / "deliverables" / f"video-{core.nn(nn)}-clean.mp4"]
    for clean in cands:
        if clean and clean.is_file():
            n = _identical_attempt(rd, nn, clean)
            if n:
                return n
    return 0


def _same_file(a: Path, b: Path) -> bool:
    """The same path, or the same bytes (sizes first, a hash only for the same size)."""
    try:
        if a.resolve() == b.resolve():
            return True
        return a.stat().st_size == b.stat().st_size and media.sha1_file(a) == media.sha1_file(b)
    except OSError:
        return False


def attempt_of_path(rd: Path, nn: str, path: str) -> int:
    """The attempt (1, 2 ...) a picked file of a video belongs to, or 0 when it cannot be told.  In order: the attempt folder it sits in
    (60-outputs/video-NN/a2/raw.mp4); the attempt it is a byte-for-byte copy of (a clean video, a deliverable); the delivered attempt when the file is
    the run's own recorded clean or preview of this video, or a copy of one (the preview is made from the clean, and both are recorded together when
    the take is accepted).  Nothing else is assumed: a file that is none of these answers 0, never "the latest attempt"."""
    if not path:
        return 0
    p = Path(path)
    for part in reversed(p.parts[:-1]):
        m = re.fullmatch(r"a(\d+)", part)
        if m:
            return int(m.group(1))
    if not p.is_file():
        return 0
    n = _identical_attempt(rd, nn, p)
    if n:
        return n
    rec = recorded_outputs(rd, nn)
    if any(r and _same_file(p, r) for r in rec.values()):
        return delivered_attempt(rd, nn)
    return 0


def attempt_for(rd: Path, nn: str, hint: str) -> int:
    """The attempt a picked version belongs to.  A path is read as a path (its folder, or the attempt it is a copy of: never from a word in its name, so
    clean-prev-before-a3.mp4 is not attempt 3); a label such as 'a2 · raw.mp4' by its attempt word.  0 when unknown."""
    hint = str(hint or "")
    try:
        is_file = Path(hint).is_file()
    except OSError:
        is_file = False
    if is_file or any(s in hint for s in ("\\", "/")):
        n = attempt_of_path(rd, nn, hint)
        if n or is_file:
            return n
    m = re.search(r"\ba(\d+)\b", hint.replace("\\", " ").replace("/", " "), re.I)
    return int(m.group(1)) if m else 0


def library_matches(run: str, nn: str) -> list[dict]:
    """Loose files in the runs folder's non-run subfolders (client fixes) that name this run's number and video."""
    num = run_number(run)
    res = []
    rd = run_dir(run)
    root = rd.parent if rd else runs_root()
    if not num or not root.is_dir():
        return res
    pat = re.compile(rf"run0*{re.escape(num)}[-_]video[-_]0*{int(nn)}(?!\d)", re.I) if nn.isdigit() else None
    for d in root.iterdir():
        if d.is_dir() and not (d / "state.json").exists() and not d.name.startswith((".", "_")):
            for p in _mp4s(d):
                if pat and pat.search(p.name):
                    res.append(_version(p, f"{d.name}/{p.name}", "fixed-lib"))
    return res


def video_versions(run: str, nn: str) -> list[dict]:
    """Every file of one video a report can be made from.  `role` says what a file is: 'generated' (the video as the service returned it, raw.mp4 of an attempt:
    its clean video and preview are made from it), 'processed' (conformed, trimmed, with the original audio, copies, previews), 'deliverable', 'client-fix' or
    'original' (the original video it was made from, as the run recorded it: video_originals); `attempt` is the attempt folder a file sits in; `delivered`
    marks the attempt whose finished file is the run's clean video, byte for byte."""
    d = run_dir(run)
    if not d:
        return []
    out = []
    vd = d / "60-outputs" / f"video-{nn}"
    if vd.is_dir():
        for p in sorted(vd.glob("*.mp4")):
            out.append(_version(p, p.name, "output", role="processed"))
        for ad in _attempt_dirs(vd):
            n = int(ad.name[1:])
            top = sorted(ad.glob("*.mp4"))
            gen = next((p for p in top if p.name == "raw.mp4"), None) or (min(top, key=lambda p: p.stat().st_mtime) if top else None)
            for p in top:
                out.append(_version(p, f"{ad.name} · {p.name}", "attempt", attempt=n, role="generated" if p == gen else "processed"))
            for sub in sorted(x for x in ad.iterdir() if x.is_dir()):
                for p in sorted(sub.glob("*.mp4")):
                    out.append(_version(p, f"{ad.name} · {sub.name}/{p.name}", "attempt", attempt=n, role="processed"))
        for cd in sorted((x for x in vd.iterdir() if x.is_dir() and CLIP_NAME.fullmatch(x.name)), key=lambda x: _clip_key(x.name)):
            for p in sorted(cd.rglob("*.mp4")):
                out.append(_version(p, f"{cd.name}/{p.relative_to(cd).as_posix()}", "clip", role="generated" if p.name == "raw.mp4" else "processed"))
    dd = d / "deliverables"
    if dd.is_dir():
        for p in sorted(dd.glob(f"video-{nn}-*.mp4")):
            out.append(_version(p, f"deliverables/{p.name}", "deliverable", role="deliverable"))
    out += [dict(v, role="client-fix") for v in library_matches(run, nn)]
    out += video_originals(d, nn)
    n_del = delivered_attempt(d, nn)
    for v in out:
        if v.get("attempt"):
            v["delivered"] = bool(n_del) and v["attempt"] == n_del
    return out


# ------------------------------------------------------------------ the original video a video was made from (read from the run's own records, never guessed)
CLIP_ID = re.compile(r"\bV\d{2,3}\b")
PATH_IN_TEXT = re.compile(r"(?:[A-Za-z]:[\\/]|\b\d\d-[\w.-]+[\\/])[^\s\"'<>|*?(),;]*?\.(?:mp4|mov|webm|mkv|m4v)\b", re.I)


def _paths_in(text, rd: Path) -> list[Path]:
    """The video files a free text of the run names (an absolute path, or one inside the run such as 10-media/V08/prepared/source.mp4), existing ones only."""
    out: list[Path] = []
    for m in PATH_IN_TEXT.findall(str(text or "")):
        p = Path(m) if Path(m).is_absolute() else rd / m
        if p.is_file() and p not in out:
            out.append(p)
    return out


def _video_inputs(req: dict) -> list[str]:
    """What a request says of each of its video inputs, in order: the meaning attached to each video reference (params.medias[].meaning), else its
    medias_meaning entries Video 1, Video 2 ..."""
    prm = req.get("params") if isinstance(req.get("params"), dict) else req
    said = [str(m.get("meaning") or "") for m in (prm.get("medias") or []) if isinstance(m, dict) and m.get("role") == "video_references"]
    if any(said):
        return said
    meaning = next((req[k] for k in ("medias_meaning", "media_meaning") if isinstance(req.get(k), dict)), {})
    named = [(k, v) for k, v in meaning.items() if re.fullmatch(r"@?video\s*\d+", str(k).strip(), re.I)]
    return [str(v) for _k, v in sorted(named, key=lambda kv: int(re.sub(r"\D", "", str(kv[0])) or 0))]


def _clip_file(rd: Path, clip: str) -> Path | None:
    """The file the run's inventory holds for a source clip (V08): the original as the client gave it."""
    inv = _json(rd / "00-inventory" / "inventory.json") or {}
    for it in (inv.get("items") or []) if isinstance(inv, dict) else []:
        if isinstance(it, dict) and it.get("id") == clip and it.get("path") and Path(str(it["path"])).is_file():
            return Path(str(it["path"]))
    v = (_json(rd / "10-media" / clip / "video.json") or {}).get("video")
    return Path(v) if isinstance(v, str) and v and Path(v).is_file() else None


def _request_tag(name: str, nn: str) -> str:
    """The generation a request file is for, from its name: video-03-c1-a2-request.json is c1-a2; a name with no attempt in it is the first attempt
    (video-03-request.json is a1, video-03-c1-request.json is c1-a1)."""
    name = request_canon(name)
    t = name[len(f"video-{nn}"):] if name.startswith(f"video-{nn}") else name
    t = (t[:-len("request.json")] if t.endswith("request.json") else t).strip("-")
    return t if re.search(r"(?:^|-)a\d+", t) else (t + "-a1" if t else "a1")


def _tag_order(t: str):
    m = re.search(r"(?:^|-)a(\d+)", t)
    return int(m.group(1)) if m else 1, t


def video_originals(rd: Path, nn: str) -> list[dict]:
    """The original video a video of the run was made from, as the run recorded it, never guessed: the source clip as the client gave it (the inventory's
    file for the clip the video's records name: its request's ref or reference, the meaning of its first video input, its prepared source, its Recreate pick
    in state.json, its own entry there) and, when it is another file, the source as each attempt sent it to the model (the prepared, cut or flipped copy its
    request names: source_prepared, else the first video input, else source), which keeps the take's own timing and orientation.  A video whose records
    name no source clip and send no source video (a Create video, an older run) has none."""
    nn = core.nn(nn)
    st = _json(rd / "state.json") or {}
    clips: list[str] = []

    def clip_in(text):
        m = CLIP_ID.search(str(text or ""))
        if m and m.group(0) not in clips:
            clips.append(m.group(0))

    sent: list[tuple[Path, str, bool]] = []        # (the file, the generation that sent it, named by a request file)
    for rf in video_request_files(rd, nn):
        req = _json(rf) or {}
        vin = _video_inputs(req)
        for text in (req.get("ref"), req.get("reference"), vin[0] if vin else "", req.get("source_prepared")):
            clip_in(text)
        src = req.get("source") if isinstance(req.get("source"), str) else ""
        found = (_paths_in(req.get("source_prepared"), rd)[-1:] or _paths_in(vin[0] if vin else "", rd)[:1]
                 or ([Path(src)] if src and Path(src).is_file() else []))
        if not found:       # an older record: the first video input as Loupe resolves it from the run's uploads
            found = [Path(i["path"]) for i in request_inputs(rd, req) if i["role"] == "video_references" and i["path"]][:1]
        if found:
            sent.append((found[0], _request_tag(rf.name, nn), True))
    led = _json(rd / "50-requests" / f"video-{nn}-uploads.json") or {}       # the video's uploads record: what each label was uploaded from
    for key, u in (led.get("uploads") or {}).items() if isinstance(led, dict) and isinstance(led.get("uploads"), dict) else []:
        m = re.match(r"^\s*(?:(\S+)\s+)?@?video\s*1\b", str(key), re.I)          # the first video input: "Video 1", "a1 Video 1: original reference"
        f = run_file(rd, u.get("file")) if isinstance(u, dict) and isinstance(u.get("file"), str) else None
        if m and f and f.is_file():
            sent.append((f, m.group(1) or "", False))
    for p in st.get("recreate_picks") or []:
        if isinstance(p, dict) and core.nn(p.get("n") if p.get("n") is not None else p.get("video")) == nn:
            clip_in(p.get("ref"))
    ent = next((v for v in (st.get("videos") or []) if isinstance(v, dict) and core.nn(v.get("video")) == nn), {})
    for k in ("ref", "reference"):
        clip_in(ent.get(k))
    out: list[dict] = []
    for clip in clips:
        p = _clip_file(rd, clip)
        if p and not any(_same_file(p, Path(o["path"])) for o in out):
            out.append(_version(p, f"original · {p.name} ({clip}, the source clip as given)", "original", role="original", clip=clip))
    groups: list[list] = []        # [file, generations its request files name, generations the uploads record names]: a byte-for-byte copy is listed once
    for p, tag, by_request in sent:
        if any(_same_file(p, Path(o["path"])) for o in out):
            continue
        g = next((g for g in groups if _same_file(g[0], p)), None)
        if g is None:
            g = [p, [], []]
            groups.append(g)
        named = g[1] if by_request else g[2]
        if tag and tag not in named:
            named.append(tag)
    groups.sort(key=lambda g: min((_tag_order(t) for t in (g[1] or g[2])), default=(99, "")))       # in the order of the generations that sent them
    for p, by_requests, by_record in groups:
        tags = sorted(by_requests or by_record, key=_tag_order)        # the request files name the generations; the uploads record only when none does
        out.append(_version(p, f"original as sent{' with ' + ', '.join(tags) if tags else ''} · {p.name}", "original", role="original", sent_with=tags))
    return out


def is_original(rd: Path, nn: str, path) -> bool:
    """True when a file is one of the video's originals as the run recorded them (video_originals): the file itself, or a copy of it."""
    try:
        p = Path(str(path or ""))
        if not str(path or "") or not p.is_file():
            return False
    except (OSError, ValueError):
        return False
    return any(_same_file(p, Path(o["path"])) for o in video_originals(rd, nn))


def locate(path) -> tuple[str, str] | None:
    """The run and video a file inside a run folder belongs to, read from where it sits: (run, NN) under 60-outputs/video-NN/, 55-references/video-NN/ or
    deliverables/video-NN-*, (run, "") anywhere else in a run, None outside every run (an upload, a client fix, a typed path, a label)."""
    try:
        p = Path(str(path or "")).resolve()
    except (OSError, ValueError):
        return None
    for root in runs_roots():
        try:
            parts = p.relative_to(root.resolve()).parts
        except (OSError, ValueError):
            continue
        if len(parts) < 2 or not (root / parts[0] / "state.json").is_file():
            return None
        if len(parts) >= 3 and parts[1] in ("60-outputs", "55-references"):
            m = re.fullmatch(r"video-(\d+)", parts[2])
            if m:
                return parts[0], core.nn(m.group(1))
        if parts[1] == "deliverables":
            m = re.match(r"video-(\d+)-", parts[-1])
            if m:
                return parts[0], core.nn(m.group(1))
        return parts[0], ""
    return None


def origin(run: str, video: str, path) -> dict:
    """What a file picked for a report on (run, video) is: {"original": True} for one of that video's originals; {"run": R, "video": NN} for a file of another
    run and {"video": NN} for a file of another video of the same run (each with "original": True when it is that video's original); {} for the video's own
    files, an upload or a typed path."""
    rd = run_dir(run) if run else None
    if rd and video and is_original(rd, core.nn(video), path):
        return {"original": True}
    loc = locate(path) if path else None
    if not loc:
        return {}
    r, v = loc
    if rd is None or r != rd.name:
        res = {"run": r, "video": v}
    elif v and v != core.nn(video):
        res = {"video": v}
    else:
        return {}
    od = run_dir(r)
    if v and od and is_original(od, v, path):
        res["original"] = True
    return res


def run_detail(run: str) -> dict | None:
    d = run_dir(run)
    if not d:
        return None
    st = _json(d / "state.json") or {}
    nns = set()
    vd = d / "60-outputs"
    if vd.is_dir():
        for x in vd.iterdir():
            m = re.match(r"^video-(\d+)$", x.name)
            if x.is_dir() and m:
                nns.add(core.nn(m.group(1)))
    info = {core.nn(v.get("video")): v for v in (st.get("videos") or []) if isinstance(v, dict)}
    nns |= set(info)
    videos = []
    for n in sorted(nns, key=lambda s: (len(s), s)):
        v = info.get(n, {})
        videos.append({"video": n, "status": v.get("status"), "qc": (v.get("qc") or "")[:300], "credits": v.get("credits"),
                       "capcut_project": v.get("capcut_project"), "versions": video_versions(run, n)})
    return {"run": d.name, "mode": st.get("mode"), "submode": st.get("analyze_submode"), "model": st.get("model"), "stage": st.get("stage"),
            "created": st.get("created"), "videos": videos,
            "products": _products(d)}


def _products(d: Path) -> list[str]:
    """Product names of the run: the names its product registry in state.json holds (a product, and each variant with its flavour), else the names its
    registry.md mentions (free text from its own file, for tagging; never a built-in list)."""
    names = [v["name"] for v in _registry_products(_json(d / "state.json") or {}).values() if v.get("name")]
    if names:
        return list(dict.fromkeys(names))[:12]
    p = d / "30-assets" / "registry.md"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[:200]:
        m = re.match(r"^\s*(?:#{1,4}\s*|[-*]\s*\*\*)(?:E\d+\s*[·:.-]\s*)?([A-Z][\w &'’.-]{2,40})", ln)
        if m and any(k in ln.lower() for k in ("product", "entity", "pack", "can", "carton", "pouch", "bottle")):
            out.append(m.group(1).strip(" *"))
    return list(dict.fromkeys(out))[:12]


# ------------------------------------------------------------------ the product sample a video used (read from the run's own records, never guessed)
A_ID = re.compile(r"\bA\d{2,4}\b")
P_CODE = re.compile(r"\bP\d+(?:-[A-Za-z0-9]{1,4})?\b")
NOT_PRODUCT = re.compile(r"identity|guide|prepared|source|start frame|hand-off|portrait|face", re.I)


def _expand(spec) -> list[str]:
    """Asset ids from a list (["A14", "A15"]) or a text ("A118-A124", "A5, A7-A9")."""
    out: list[str] = []
    for tok in (spec if isinstance(spec, list) else re.split(r"[,;\s]+", str(spec or ""))):
        tok = str(tok).strip()
        m = re.fullmatch(r"([A-Za-z]+)(\d+)\s*[-–]\s*(?:[A-Za-z]+)?(\d+)", tok)
        if m and 0 <= int(m.group(3)) - int(m.group(2)) <= 60:
            w = len(m.group(2))
            out += [f"{m.group(1)}{n:0{w}d}" for n in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif A_ID.fullmatch(tok):
            out.append(tok)
    return out


def _registry_products(st: dict) -> dict:
    """The run's product registry: key -> {name, canonical, ids}; an entry marked unused is left out.  Two forms: assets.products (one entry per product,
    with canonical, files and other_files) and assets.product (one entry per product with its variants: P1 -> P1-CC with its flavour, its label image
    as the canonical one and its views)."""
    a = st.get("assets") if isinstance(st.get("assets"), dict) else {}
    res = {}

    def ids_of(*vals) -> list[str]:
        out: list[str] = []
        for v in vals:
            out += _expand(v) if isinstance(v, (list, str)) else []
        return list(dict.fromkeys(out))

    flat = (a.get("products") or {}) if isinstance(a.get("products"), dict) else {}
    for key, v in flat.items():
        if not isinstance(v, dict) or v.get("unused"):
            continue
        ids = ids_of(v.get("canonical"), v.get("label"), v.get("files"), v.get("other_files"), v.get("views"), v.get("geometry"), v.get("opened"))
        name = str(v.get("name") or "").strip()
        parent = flat.get(str(key).rsplit("-", 1)[0]) if "-" in str(key) else None       # a variant entry (P01-WM) beside its product entry (P01)
        if not name and v.get("flavour"):
            name = " · ".join(x for x in (str(parent.get("name") or "").strip() if isinstance(parent, dict) else "", str(v["flavour"]).strip()) if x)
        res[str(key)] = {"name": name or str(v.get("role") or "").strip(), "canonical": (ids_of(v.get("canonical"), v.get("label")) or ids[:1] or [""])[0], "ids": ids}
    for key, grp in flat.items():          # products listed in groups ({in_rotation: [{id: P1, name, canonical, files}], optional: [...]}); an excluded group is not used
        if not isinstance(grp, list) or str(key).lower() in ("excluded", "unused"):
            continue
        for v in grp:
            if not isinstance(v, dict) or not v.get("id") or v.get("unused"):
                continue
            ids = ids_of(v.get("canonical"), v.get("label"), v.get("files"), v.get("other_files"), v.get("views"), v.get("opened"))
            if ids:
                res.setdefault(str(v["id"]), {"name": str(v.get("name") or v.get("flavour") or "").strip(), "canonical": (ids_of(v.get("canonical")) or ids[:1])[0],
                                              "ids": ids})
    for key, p in ((a.get("product") or {}) if isinstance(a.get("product"), dict) else {}).items():      # by product, each with its variants
        if not isinstance(p, dict) or p.get("unused"):
            continue
        base = str(p.get("name") or "").strip()
        own = ids_of(p.get("canonical"), p.get("label"), p.get("canonical_geometry"), p.get("files"), p.get("views"))
        if own:
            res[str(key)] = {"name": base, "canonical": own[0], "ids": own}
        for vk, v in ((p.get("variants") or {}) if isinstance(p.get("variants"), dict) else {}).items():
            if not isinstance(v, dict) or v.get("unused"):
                continue
            ids = ids_of(v.get("label"), v.get("canonical"), v.get("opened"), v.get("views"), v.get("files"))
            if ids:
                res[str(vk)] = {"name": " · ".join(x for x in (base, str(v.get("flavour") or v.get("name") or "").strip()) if x), "canonical": ids[0], "ids": ids}
    return res


def _input_meanings(req: dict) -> dict:
    """What a request says of each of its inputs, by label (Image 1, Video 1 ...): its medias_meaning (or media_meaning) table, and the meaning written on
    each input of params.medias (the newer form), the k-th input of a role being '<Role> k'."""
    meaning = dict(next((req[k] for k in ("medias_meaning", "media_meaning") if isinstance(req.get(k), dict)), {}))
    prm = req.get("params") if isinstance(req.get("params"), dict) else req
    counts: dict = {}
    for m in prm.get("medias") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", ""))
        counts[role] = counts.get(role, 0) + 1
        base = ROLE_LABEL.get(role)
        if base and m.get("meaning"):
            meaning.setdefault(f"{base} {counts[role]}", str(m["meaning"]))
    for it in (req.get("media_map") or []) if isinstance(req.get("media_map"), list) else []:      # an older form: [{ref: '@Image1', asset: 'A17', meaning}]
        mm = re.fullmatch(r"@?(image|video|audio)\s*(\d+)", str(it.get("ref") or "").strip(), re.I) if isinstance(it, dict) else None
        if mm:
            words = " ".join(x for x in (str(it.get("meaning") or "").strip(), f"({it['asset']})" if it.get("asset") else "") if x)
            if words:
                meaning.setdefault(f"{mm.group(1).capitalize()} {mm.group(2)}", words)
    return meaning


def _said(text: str, aid: str) -> str:
    """What a request itself calls an input: its words before the asset id, without a leading 'Image 2 (...):' label or file path ('Test can, sealed:
    A14' is 'Test can, sealed'; 'Image 2: the sealed can (A14 front, ...)' is 'the sealed can'); empty when only a code is left (then the registry's
    name is used)."""
    head = text.split(aid, 1)[0]
    head = re.sub(r"^\s*@?image\s*\d+\s*(?:\([^)]*\))?\s*[:,.-]?\s*", "", head, flags=re.I)      # a leading 'Image 2 (...):' label
    head = re.sub(r"^(?:[A-Za-z]:[\\/]|\d\d-[\w.-]+[\\/])\S*\s*", "", head)                       # a leading file path
    head = head.strip(" \t:;,.(-–")
    return "" if not head or P_CODE.fullmatch(head) or A_ID.fullmatch(head) else head


def _ledger_entry(led: dict, label: str, tag: str):
    """The entry a video's uploads record (50-requests/video-NN-uploads.json) holds for an input label of a request (Image 2: the key 'Image 2', or
    'a1 Image 2: P01-CC front padded' with the attempt first): (its key and role as text, its file), the request's own generation first; None."""
    hits = [(k, v) for k, v in led.items() if isinstance(v, dict) and re.search(rf"(?:^|\s){re.escape(label)}\b", str(k), re.I)]
    if not hits:
        return None
    k, v = next(((k, v) for k, v in hits if str(k).split()[0] == tag), hits[0])
    return f"{k} {v.get('role') or ''}".strip(), str(v.get("file") or "")


def _request_files(rd: Path, video: str, attempt: int) -> list[Path]:
    """The video requests of a video, the attempt's own first; with no attempt known, the latest attempt first."""
    files = video_request_files(rd, video)
    if attempt:
        want = [f for f in files if _attempt_in_name(f.name) == attempt]
        return want + [f for f in files if f not in want]
    return sorted(files, key=lambda f: (_attempt_in_name(f.name), f.name), reverse=True)


# ------------------------------------------------------------------ where each input of a request came from (the admin, 2026-10-30)
# "the names are far better to be told as they were in their original files at the <client's> assets folders ... so the upcoming runs know which assets
# samples to use or how much of them"; then "no need to say the exact folder such as <client>/filename.png, just the exact file/image name": a file is
# named by its own name and asset id, its full path kept only as `original`.  A request records, for each input, the client's original file it was made from: params.medias[].source
# {original, asset_id, prepared, kind} (the pipeline's form since 2026-10-30), else a 'build' note ("from <file> (A82): <how it was prepared>", "byte copy
# of <file> (...)"), else the one asset id or the one original file name that what the request says of the input names.  Read, never guessed: an input
# whose original no record names has none.
MEDIA_FILE = re.compile(r"\.(?:png|jpe?g|webp|bmp|tiff?|gif|mp4|mov|m4v|webm|mkv)$", re.I)      # a value that names a file
BUILD_NOTE = re.compile(r"^\s*(byte copy of|from)\s+(.+?\.(?:png|jpe?g|webp|bmp|tiff?|gif|mp4|mov|m4v|webm|mkv))\s*(?:\((A\d{1,4})\))?\s*(?:\(([^)]*)\))?\s*:?\s*(.*)$",
                        re.I | re.S)


def _in_asset_roots(st: dict, path) -> bool:
    """Whether a file sits in one of the client's asset folders the run was given (state.json assets_paths)."""
    raw = os.path.normcase(os.path.normpath(str(path or "")))
    return any(raw.startswith(os.path.normcase(os.path.normpath(str(r)).rstrip("\\/")) + os.sep)
               for r in (st.get("assets_paths") or []) if isinstance(r, str) and r.strip())


def _inventory(rd: Path) -> tuple[dict, dict]:
    """The run's asset inventory by asset id, and by lower-cased file name (a name two items share names neither)."""
    items = [it for it in ((_json(rd / "30-assets" / "project" / "asset-inventory.json") or {}).get("items") or []) if isinstance(it, dict) and it.get("id")]
    names: dict = {}
    for it in items:
        n = Path(str(it.get("path") or it.get("rel") or "")).name.lower()
        if n:
            names.setdefault(n, []).append(it)
    return {str(it["id"]): it for it in items}, {n: v[0] for n, v in names.items() if len(v) == 1}


def _source_of(m: dict, said: str, st: dict, inv: dict, names: dict) -> dict:
    """What a request's records say one input was made from: {original (its full path), name (its file name), asset_id, prepared, kind, recorded}, or {}
    when no record names it."""
    s = m.get("source") if isinstance(m.get("source"), dict) else None
    orig = aid = prep = kind = how = ""
    said_path = next((str(m[k]).strip() for k in ("source", "source_file", "from") if isinstance(m.get(k), str) and MEDIA_FILE.search(str(m[k]).strip())), "")
    if s and str(s.get("original") or "").strip():
        orig, aid, how = str(s["original"]).strip(), str(s.get("asset_id") or "").strip(), "source"
        prep, kind = str(s.get("prepared") or "").strip(), str(s.get("kind") or "").strip()
    elif said_path:          # the forms runs wrote before the source record: the client's file in source, source_file or from, its prepare words
        orig, how, prep = said_path, "source", str(m.get("prepare") or "").strip()
    else:
        b = BUILD_NOTE.match(str(m.get("build") or ""))
        if b:
            orig, aid, how = b.group(2).strip(), b.group(3) or next(iter(A_ID.findall(b.group(4) or "")), ""), "build"
            prep = ("a byte copy" + (f" ({b.group(4)})" if b.group(4) else "")) if b.group(1).lower().startswith("byte") else (b.group(5) or b.group(4) or "").strip()
        else:
            text = " ".join(str(x or "") for x in (m.get("meaning"), said, m.get("label"), m.get("source") if isinstance(m.get("source"), str) else "",
                                                   m.get("source_file"), m.get("from")))
            ids = list(dict.fromkeys(a for a in A_ID.findall(text) if a in inv))
            hit = inv[ids[0]] if len(ids) == 1 else None
            if not ids:
                tl = text.lower()
                found = [n for n in names if n in tl]
                found = [n for n in found if not any(n != o and n in o for o in found)]
                hit = names[found[0]] if len(found) == 1 else None
            if hit:
                orig, aid, how = str(hit.get("path") or ""), str(hit.get("id") or ""), "named"
                prep = str(m.get("meaning") or said or "").strip()
    if not orig:
        return {}
    if not aid:
        aid = next((k for k, it in inv.items() if it.get("path") and _file_key(it["path"]) == _file_key(orig)), "")
    return {"original": orig, "name": Path(orig.replace("\\", "/")).name, "asset_id": aid, "prepared": prep, "kind": kind, "recorded": how}


def input_sources(rd: Path, video: str) -> dict:
    """Where each input of a video's requests came from, under every name its file goes by (the file the request or the uploads record names, and its
    media id): {original, name, asset_id, prepared, kind, recorded, image ('Image 2'), role, request}.  Every request of the video counts (its attempts,
    its clips, the drafts); the first record of a file is kept."""
    nn = core.nn(video)
    st = _json(rd / "state.json") or {}
    inv, names = _inventory(rd)
    led = (_json(rd / "50-requests" / f"video-{nn}-uploads.json") or {}).get("uploads")
    by_mid = {str(v.get("media_id")): str(run_file(rd, v.get("file")) or "") for v in (led.values() if isinstance(led, dict) else [])
              if isinstance(v, dict) and v.get("media_id")}
    ups = _uploads(rd)
    out: dict = {}
    rq = rd / "50-requests"
    for f in (sorted(rq.glob(f"video-{nn}*request*.json")) if rq.is_dir() else []):
        if "frame-request" in f.name:
            continue
        j = _json(f) or {}
        prm = j.get("params") if isinstance(j.get("params"), dict) else j
        meaning = _input_meanings(j)
        counts: dict = {}
        for m in (prm.get("medias") or []) if isinstance(prm, dict) else []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role", ""))
            counts[role] = counts.get(role, 0) + 1
            label = f"{ROLE_LABEL.get(role, role)} {counts[role]}"
            src = _source_of(m, str(meaning.get(label) or ""), st, inv, names)
            if not src:
                continue
            rec = {**src, "image": label, "role": role, "request": f.name}
            mid = str(m.get("value") or "")
            for k in (m.get("file"), by_mid.get(mid), (ups.get(mid) or {}).get("file")):
                if k:
                    out.setdefault(_file_key(run_file(rd, k)), rec)
            if mid and not mid.startswith("<"):
                out.setdefault(mid, rec)
    return out


def file_source(path, run: str = "") -> dict:
    """Where a picked file came from: the record of the request input it was sent as (input_sources of the run and video it sits in); for one of the
    client's own files (under the asset roots of `run`), that file itself with its asset id; {} when no record names it."""
    if not path:
        return {}
    loc = locate(path)
    if loc and loc[1]:
        rd = run_dir(loc[0])
        src = input_sources(rd, loc[1]).get(_file_key(path)) if rd else None
        if src:
            return src
    rd = run_dir(run) if run else None
    if rd:
        st = _json(rd / "state.json") or {}
        if _in_asset_roots(st, path):
            inv, _names = _inventory(rd)
            aid = next((k for k, it in inv.items() if it.get("path") and _file_key(it["path"]) == _file_key(path)), "")
            return {"original": str(path), "name": Path(str(path).replace("\\", "/")).name, "asset_id": aid, "prepared": "", "kind": "",
                    "recorded": "the client's file", "image": "", "role": "", "request": ""}
    return {}


def product_samples(run: str, video: str, wrong: str = "", path: str = "") -> list[dict]:
    """The product image(s) a video's request used, as the original files in the client's own reference folder.  The request names each image input
    (by its asset id, "<what it is>: A14" in its medias_meaning or on the input itself in params.medias, or by the product code in its prepared file name,
    "P08-front.jpg"); the run's registry says which ids and
    codes are products; the run's asset inventory maps an id to the original file.  The attempt of the wrong version (a1, a2 ...) picks the request; a wrong
    version that is a take of another video or of another run (its `path`, else `wrong`, sits there) reads that video's own request.
    Nothing found: an empty list (the report then has no product sample)."""
    loc = locate(path or wrong) if (path or wrong) else None
    if loc and loc[1] and (loc[0], loc[1]) != (run, core.nn(video)):
        run, video, wrong = loc[0], loc[1], path or wrong
    rd = run_dir(run)
    if not rd or not video:
        return []
    st = _json(rd / "state.json") or {}
    prods = _registry_products(st)
    id_owner = {i: k for k, v in prods.items() for i in v["ids"]}
    inv = {it["id"]: it for it in ((_json(rd / "30-assets" / "project" / "asset-inventory.json") or {}).get("items") or []) if isinstance(it, dict) and it.get("id")}
    roots = [Path(x) for x in (st.get("assets_paths") or []) if isinstance(x, str)]
    attempt = attempt_for(rd, video, wrong)
    led = (_json(rd / "50-requests" / f"video-{core.nn(video)}-uploads.json") or {}).get("uploads")
    led = led if isinstance(led, dict) else {}

    def file_of(aid: str):
        it = inv.get(aid) or {}
        path = Path(it["path"]) if it.get("path") else None
        if not (path and path.is_file()) and it.get("rel"):
            path = next((r / it["rel"] for r in roots if (r / it["rel"]).is_file()), None)
        return path if (path and path.is_file()) else None

    for rf in _request_files(rd, video, attempt):
        req = _json(rf) or {}
        meaning = _input_meanings(req)
        files = dict(req["media_files"]) if isinstance(req.get("media_files"), dict) else {}
        prm = req.get("params") if isinstance(req.get("params"), dict) else req
        n_img = sum(1 for m in (prm.get("medias") or []) if isinstance(m, dict) and m.get("role") == "image_references")
        for k in range(1, n_img + 1):         # an image input the request does not describe: the video's uploads record names it (its key, role and file)
            lab = f"Image {k}"
            hit = None if lab in meaning else _ledger_entry(led, lab, _request_tag(rf.name, core.nn(video)))
            if hit:
                meaning[lab], files[lab] = hit
        out, seen = [], set()
        for label, text in meaning.items():
            if not str(label).lower().startswith("image"):
                continue
            text = str(text)
            hay = text + " " + Path(str(files.get(label) or "")).name
            found = A_ID.findall(text)
            aid = next((a for a in found if a in id_owner), None)           # the request names the asset itself
            key = id_owner.get(aid or "")
            if aid is None:                                                 # or the product code of its prepared file: the registry's canonical original
                code = next((c for c in P_CODE.findall(hay) if c in prods), None)
                if code:
                    key, aid = code, prods[code]["canonical"] or None
            if aid is None and not prods and found and not NOT_PRODUCT.search(text):      # an older run with no registry
                aid = found[-1]
            if not aid or aid in seen or (key and key in seen):
                continue
            path = file_of(aid)
            if not path:
                continue
            seen.update({aid, key} - {None, ""})       # one picture per product: another view of the same product is not shown again
            said = _said(text, aid) if aid in found else ""       # what the request itself called it, else the registry's name
            name = said or (prods.get(key or "") or {}).get("name") or aid
            out.append({"asset_id": aid, "label": name[:90], "path": path, "name": path.name, "request": rf.name, "run": run, "video": core.nn(video)})
        if out:
            return out[:2]
    return []


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def _file_key(p) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _input_words(text: str) -> str:
    """What a request or an uploads key says of one input, without its 'a1 Image 2:' label and its role word ('a1 Image 2: P1 front padded image_references'
    is 'P1 front padded')."""
    t = re.sub(r"^\s*(?:[ac]\d+(?:-a\d+)?\s+)?(?:image|video|audio)\s*\d+\s*(?:\([^)]*\))?\s*[:,.-]?\s*", "", str(text or ""), flags=re.I)
    return re.sub(r"\s+(?:image|video|audio)_references\s*$", "", t).strip()[:80]


SENT_ROLES = {"image_references": "Image", "start_image": "Start frame", "end_image": "End frame", "mask": "Mask"}      # the images a request attaches
NO_VIDEO = {"failed", "pending", "submitted", "unknown", "cancelled", "canceled", "refused", "error"}       # ledger states of a job that made no video
PRODUCT_FILE = re.compile(r"(?:^|[\s=/\\_:-])product(?:[\s._:/\\\d-]|$)", re.I)      # how the pipelines name a prepared product view: image2-product-...png, product/P1-ref.png
CHARACTER_FILE = re.compile(r"(?:^|[\s=/\\_:-])(?:character|identity)(?:[\s._:/\\\d-]|$)", re.I)      # and a person's: image1-character-...jpg, identity/C05-head.png, identity:CH-M06
CHARACTER_SAID = re.compile(r"^\s*(?:[\w-]+\s+)?(?:character|identity)\b", re.I)      # what a request calls an input: 'identity C01 (A01 ...)', 'CH-M03 identity: A12 ...'


_FINGERPRINTS: dict = {}


def file_fingerprint(path) -> str:
    """A short fingerprint of a file's content, the first 8 hex digits of its SHA-1, so one photo is known as one file in every run whatever asset id a
    run gave it (the admin, 2026-11-03: the judge could not tell one run's A274 IMG_6917.jpg from another's A273 IMG_6917.jpg); '' when the file
    cannot be read.  The toolkit computes it the same way (awl.py _pi_fingerprint)."""
    if not path:
        return ""
    p = Path(str(path))
    try:
        stt = p.stat()
        if not p.is_file():
            return ""
    except (OSError, ValueError):
        return ""
    key = (os.path.normcase(str(p)), stt.st_size, stt.st_mtime_ns)
    if key not in _FINGERPRINTS:
        try:
            _FINGERPRINTS[key] = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
        except OSError:
            return ""
    return _FINGERPRINTS[key]


def request_fingerprint(req: dict) -> str:
    """The fingerprint the pipelines' ledgers record for a request they submit (their _fingerprint): its parameters without get_cost, as sorted JSON."""
    params = dict(req.get("params", req)) if isinstance(req, dict) else {}
    params.pop("get_cost", None)
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


UPLOAD_FILE_KEYS = ("file", "local", "path")      # the key an uploads record keeps the uploaded file under, in the forms runs wrote


def _upload_file(v: dict) -> str:
    return next((v[k].strip() for k in UPLOAD_FILE_KEYS if isinstance(v.get(k), str) and v[k].strip()), "")


def run_file(rd: Path | None, f) -> Path | None:
    """A file a run's records name, as a path (None for no name): an absolute one as it is; a relative one inside the run (55-references/...); else,
    when it names the run's own folder ('runs/<run>/55-references/...', as an agent working from the pipeline's home recorded it: the admin,
    2026-11-03, the takes of four runs offered no image), what follows that folder; else from the pipeline's home.  Returned whether or not the file
    exists (inside the run when none of them does)."""
    s = str(f or "").strip()
    if not s:
        return None
    p = Path(s)
    if p.is_absolute() or rd is None:
        return p
    inside = rd / p
    if inside.exists():
        return inside
    parts = p.parts
    for i in range(len(parts) - 1):
        if parts[i] == rd.name:
            q = rd.joinpath(*parts[i + 1:])
            if q.exists():
                return q
    home = rd.parent.parent / p
    return home if home.exists() else inside


def _uploaded_files(rd: Path, nn: str) -> dict:
    """media id -> (the local file the run uploaded under it, the words its record files it under), from the records the run wrote when it uploaded, in
    every form runs wrote them: the video's uploads records (video-NN*-uploads.json, of any shape: an entry with its media_id, or id, beside its file
    under `file`, `local` or `path`), the run's uploads record in state.json (assets.uploads, the same forms), then the run's uploads map (_uploads: the
    file it names, else its name in the run's asset folders, else, for a name that is an asset id, the one copy of that asset the run made, _asset_copy)."""
    res: dict = {}

    def walk(v, key=""):
        if isinstance(v, dict):
            mid, f = v.get("media_id") or v.get("id"), _upload_file(v)
            if isinstance(mid, str) and mid and f:
                res.setdefault(mid, (str(run_file(rd, f)), " ".join(str(x) for x in (key, v.get("who") or "", v.get("role") or "") if x)))
            for k, x in v.items():
                walk(x, k if isinstance(x, (dict, list)) else key)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
    rq = rd / "50-requests"
    for f in (sorted(rq.glob(f"video-{nn}*uploads*.json")) if rq.is_dir() else []):
        walk(_json(f))
    walk(_state_uploads(rd))
    copies = None
    for mid, up in _uploads(rd).items():
        if mid not in res:
            name = str(up.get("name") or "")
            p = up.get("file") or _asset_path(rd, name, {})
            if not p and A_ONE.fullmatch(name):
                copies = _asset_copies(rd) if copies is None else copies
                p = copies[name][0] if len(copies.get(name) or []) == 1 else None
            if p:
                res[mid] = (str(p), name)
    return res


A_ONE = re.compile(r"A\d{1,4}")      # a name that is an asset id and nothing else


def _asset_copies(rd: Path) -> dict:
    """asset id -> the image files the run made of it in its asset folders (30-assets/**, named A17-padded.jpg, A17_ref.png, A17.png): an uploads map that
    names only the asset id uploaded the run's copy of it when the run made exactly one (the client's originals are not in the run's folders); with none,
    or more than one, which file was sent is not known and nothing is taken."""
    out: dict = {}
    base = rd / "30-assets"
    for f in (base.rglob("*") if base.is_dir() else []):
        m = re.fullmatch(r"(A\d{1,4})(?:[-_. ].*)?", f.stem)
        if m and f.suffix.lower() in IMAGE_EXT and "previews" not in f.relative_to(base).parts[:-1] and f.is_file():      # prep-assets' look previews are never sent
            out.setdefault(m.group(1), []).append(f)
    return out


FILE_NAMED = re.compile(r"[\w.-]+\.(?:png|jpe?g|webp)", re.I)      # a file name written in a request's words for an input


def _request_built(rd: Path, words: str, files: dict) -> Path | None:
    """The file the run built for its requests (50-requests/**: a guide, a hand-off frame) that a request's own words for an input name ('size-and-place
    guide (video-08-c2-a2-guide.png)'): only when the words name one file and exactly one file of 50-requests has that name.  `files` caches the
    folder's listing (name -> paths) for one call."""
    named = set(FILE_NAMED.findall(words or ""))
    if len(named) != 1:
        return None
    if "" not in files:
        rq = rd / "50-requests"
        for f in (rq.rglob("*") if rq.is_dir() else []):
            if f.is_file():
                files.setdefault(f.name.lower(), []).append(f)
        files[""] = []
    hits = files.get(named.pop().lower()) or []
    return hits[0] if len(hits) == 1 else None


def _registry_characters(st: dict) -> dict:
    """The run's character registry (state.json assets.characters, in the forms runs wrote: {C13: {file: A17}}, {CH-M03: [A12]}): code -> its asset ids;
    {} when the run keeps none (a text note, or nothing)."""
    a = st.get("assets") if isinstance(st.get("assets"), dict) else {}
    ch = a.get("characters")
    out = {}
    for code, v in (ch.items() if isinstance(ch, dict) else []):
        ids = list(dict.fromkeys(A_ID.findall(json.dumps(v, ensure_ascii=False))))
        if ids and re.fullmatch(r"[A-Z][A-Za-z0-9_-]{0,15}", str(code)):
            out[str(code)] = ids
    return out


def _sent_as(occ: list, many: bool) -> str:
    """How one picture was sent: 'Image 2'; in a take joined from clips 'c1 · Image 1', 'c1, c2 · Image 1' when each clip sent it as the same input, else
    'c1 Image 1, c2 Image 2'; a generation that attached it twice: 'Image 1, Image 3'."""
    imgs = list(dict.fromkeys(i for _c, i in occ))
    clips = list(dict.fromkeys(c for c, _i in occ if c))
    if not many or not clips:
        return ", ".join(imgs)
    if len(imgs) == 1:
        return f"{', '.join(clips)} · {imgs[0]}"
    return ", ".join(f"{c} {i}" if c else i for c, i in dict.fromkeys(occ))


def _state_uploads(rd: Path):
    """The uploads record a run kept in state.json (assets.uploads): {name: {media_id or id, file or local, ...}} or a plain {name: media id}."""
    st = _json(rd / "state.json") or {}
    a = st.get("assets") if isinstance(st.get("assets"), dict) else {}
    u = a.get("uploads")
    return u if isinstance(u, (dict, list)) else {}


def _registry_product_rows(rd: Path) -> list[tuple[str, list]]:
    """The product rows of the run's asset registry (30-assets/registry.md, in either form runs write: a table row whose id starts with P-, or whose
    type cell says product): [(its id, its asset ids)].  The toolkit reads them the same way (awl.py _pi_registry_rows)."""
    try:
        text = (rd / "30-assets" / "registry.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for ln in text.splitlines():
        cells = [c.strip().strip("`").strip() for c in ln.strip().strip("|").split("|")] if ln.lstrip().startswith("|") else []
        if not cells or not re.fullmatch(r"[A-Za-z][\w-]*", cells[0]) or cells[0].lower() == "id":
            continue
        if cells[0].startswith("P-") or any(c.lower() == "product" for c in cells[1:3]):
            rows.append((cells[0], list(dict.fromkeys(A_ID.findall(ln)))))
    return rows


def _product_ids(rd: Path, st: dict) -> set:
    """The asset ids the run's registry names as a product's: state.json's product registry and the product rows of 30-assets/registry.md."""
    return {i for v in _registry_products(st).values() for i in v["ids"]} | {i for _c, ids in _registry_product_rows(rd) for i in ids}


def sent_images(run: str, video: str, wrong: str = "", path: str = "") -> list[dict]:
    """The images a take's generation was sent with its job, verified from the run's records (the admin, 2026-10-30: "only the verified assets that
    were directly used to higgsfield for that specific generation ... even if they characters or other attachment ... if it isnt directly used as a
    reference in that video generation then don't show it"): the request the ledger submitted for that attempt (each clip's, for a take joined from
    clips) as a job that made a video (completed, accepted, rejected or superseded; never failed, pending or still in flight), whose parameters
    still have the fingerprint the ledger recorded; each image it attached (SENT_ROLES) by its media id; the file the run
    uploaded under that id (_uploaded_files, else the input's own file).  Each once, in the request's order, named by its input, its asset id and the
    client's original file (_source_of), with what it is (`kind`: the source record's, else product by the registry, a product code or the pipeline's
    file name or a registry product's id in its label, else character by its file name): {path, name, label, image, role, media_id, asset_id,
    kind, original, original_name, prepared,
    recorded, request, job, words, run, video}.  [] when the records verify nothing (a request not in the ledger or changed since it was submitted, an
    upload that names no file): nothing is guessed."""
    loc = locate(path or wrong) if (path or wrong) else None
    if loc and loc[1] and (loc[0], loc[1]) != (run, core.nn(video)):
        run, video = loc[0], loc[1]
    rd = run_dir(run)
    if not rd or not video:
        return []
    nn = core.nn(video)
    hint = path or wrong                      # the take's own file tells its attempt and its join; the label is the fallback
    st = _json(rd / "state.json") or {}
    subs = [s for s in st.get("submissions") or [] if isinstance(s, dict) and s.get("request_file")]
    segs = prompt_segments(run, video, hint).get("segments") or []
    if segs:                                  # a take joined from clips: what each clip's generation was sent
        reqs = [(rd / "50-requests" / s["request"], str(s.get("clip") or "")) for s in segs if s.get("request")]
    else:
        attempt = attempt_for(rd, video, hint) or (attempt_for(rd, video, wrong) if wrong and wrong != hint else 0)
        files = _request_files(rd, video, attempt)
        if attempt:
            reqs = [(f, "") for f in files if _attempt_in_name(f.name) == attempt]
        elif len({_request_tag(f.name, nn) for f in files}) == 1:       # the video's one generation (and copies of its request under other names)
            reqs = [(f, "") for f in files]
        else:
            reqs = []                         # several generations and none known to be this take's: nothing is guessed
    many = len({c for _f, c in reqs if c}) > 1
    uploaded = _uploaded_files(rd, nn)
    inv, names = _inventory(rd)
    prods, pids = _registry_products(st), _product_ids(rd, st)
    codes = [c for c, _ids in _registry_product_rows(rd)] + list(prods)
    chars = _registry_characters(st)
    cids = {i for ids in chars.values() for i in ids}
    out, by_pic, keys, built = [], {}, {}, {}
    for rf, clip in reqs:
        req = _json(rf) or {}
        ent = [s for s in subs if _file_key(s["request_file"]) == _file_key(rf)]
        ent = [s for s in ent if s.get("job_id") and str(s.get("status") or "") not in NO_VIDEO]
        if not ent or (ent[-1].get("fingerprint") and ent[-1]["fingerprint"] != request_fingerprint(req)):
            continue          # not submitted, or changed since: what it attached is not known
        job = str(ent[-1].get("job_id") or "")
        prm = req.get("params") if isinstance(req.get("params"), dict) else req
        meaning = _input_meanings(req)
        files_said = req.get("media_files") if isinstance(req.get("media_files"), dict) else {}      # an older request's own record: {Image 1: its file}
        counts: dict = {}
        for m in (prm.get("medias") or []) if isinstance(prm, dict) else []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "")
            counts[role] = counts.get(role, 0) + 1
            mid = str(m.get("value") or "").strip()
            if role not in SENT_ROLES or not mid or mid.startswith("<"):
                continue
            img = f"Image {counts[role]}" if role == "image_references" else SENT_ROLES[role]
            f, filed = uploaded.get(mid) or (str(m.get("file") or "") or str(files_said.get(img) or "")
                                             or str(_request_built(rd, str(meaning.get(img) or ""), built) or ""), "")
            p = run_file(rd, f) if f else None
            if not (p and p.is_file() and p.suffix.lower() in IMAGE_EXT):
                continue
            pk = _file_key(p)
            if pk not in keys:
                try:
                    keys[pk] = media.sha1_file(p)
                except OSError:
                    keys[pk] = pk
            x = by_pic.get(keys[pk])
            if x:                     # the same picture sent again (by another clip's generation, or as another input): one entry, every time it was sent
                for k, v in (("_occ", (clip, img)), ("media_ids", mid), ("jobs", job), ("requests", rf.name), ("paths", p)):
                    if v not in x[k]:
                        x[k].append(v)
                continue
            said = str(meaning.get(img) or "") or _input_words(filed)
            src = _source_of(m, said, st, inv, names)
            aid = src.get("asset_id", "")
            try:
                rel = str(p.resolve().relative_to(rd.resolve()))
            except ValueError:
                rel = p.name
            own = " ".join(str(x or "") for x in (m.get("label"), p.name, filed))      # its own names: never a folder (a pass folder p1 is not the product P1)
            look = f"{own} {rel}"                     # with its folders, for the pipelines' folder words (identity/, product/)
            named = PRODUCT_FILE.search(look) or any(re.search(r"(?<![A-Za-z0-9])" + re.escape(c) + r"(?![A-Za-z0-9])", own, re.I) for c in codes)
            person = (aid in cids or CHARACTER_FILE.search(look) or CHARACTER_SAID.search(said)
                      or any(re.search(r"(?<![A-Za-z0-9])" + re.escape(c) + r"(?![A-Za-z0-9])", f"{own} {said}") for c in chars))
            kind = src.get("kind") or ("product" if (aid in pids or named
                                                     or any(c in prods for c in P_CODE.findall(" ".join((said, str(m.get("meaning") or ""), own)))))
                                       else "character" if person else "")
            x = {"path": p, "name": p.name, "role": role, "media_id": mid, "asset_id": aid, "kind": kind,
                 "original": src.get("original", ""), "original_name": src.get("name", ""), "prepared": src.get("prepared", ""),
                 "recorded": src.get("recorded", ""), "request": rf.name, "job": job,
                 "words": _input_words(str(m.get("meaning") or meaning.get(img) or "")), "run": run, "video": nn,
                 "_occ": [(clip, img)], "media_ids": [mid], "jobs": [job], "requests": [rf.name], "paths": [p], "_src": bool(src)}
            by_pic[keys[pk]] = x
            out.append(x)
    for x in out:
        x["image"] = _sent_as(x.pop("_occ"), many)
        x["label"] = " · ".join(v for v in (x["image"], x["asset_id"], x["original_name"] or x["name"]) if v) + ("" if x.pop("_src") else " · its original file is not recorded")
    return out


VIDEO_WORDS = {("A",): "Video A", ("B",): "Video B", ("A", "B"): "Videos A and B"}      # the video(s) of a report an image was sent with


def _picture_key(p) -> str:
    """What one picture is, whatever folder or run holds it: the SHA-1 of its content (its path when it cannot be read)."""
    try:
        return media.sha1_file(Path(p))
    except OSError:
        return _file_key(p)


def videos_sent_images(run: str, video: str, takes) -> dict:
    """The images sent with the generation of each of a report's videos that a sample shows (the admin, 2026-11-03: "what happens with the attachments
    when we use two different samples one for wrong example and other video for the right example, fix this too"): `takes` [(letter, hint, path)],
    Video A before Video B, each read by sent_images in its own run and video; an original video (the source clip, not generated) was sent nothing.
    When the two were made by different generations (the requests their images were sent with differ), a picture both were sent (the same content,
    from any folder or run) is one entry, and every entry names the video(s) it was sent with: `videos` ['A'], ['B'] or ['A', 'B'], its label starting
    'Video A · ', 'Video B · ' or 'Videos A and B · ', an input number, asset id or original name that differs between the two said for each
    ('Image 2 of Video A, Image 1 of Video B'), and `asset_ids` {letter: id}.  One video, or two files of one generation (a take and its preview),
    read as before: that generation's images, untagged.  {images, two, sent: {letter: how many}}."""
    per = []
    for letter, hint, path in takes:
        own = path or hint
        loc = locate(own) if own else None
        r, v = (loc[0], loc[1]) if loc and loc[1] else (run, core.nn(video) if video else "")
        rd = run_dir(r) if r else None
        if own and rd and v and is_original(rd, v, own):
            per.append((letter, []))
            continue
        try:
            per.append((letter, sent_images(run, video, hint, path=path)))
        except Exception:  # noqa: BLE001  (a run whose records cannot be read: that video shows none, the other still does)
            per.append((letter, []))
    sent = {k: len(xs) for k, xs in per}
    gens = {k: {(str(x.get("run") or ""), str(q)) for x in xs for q in (x.get("requests") or [x.get("request")])} for k, xs in per}
    have = [k for k, xs in per if xs]
    if len(have) < 2 or gens[have[0]] == gens[have[1]]:
        return {"images": next((xs for _k, xs in per if xs), []), "two": False, "sent": sent}
    out, by_key = [], {}
    for k, xs in per:
        for x in xs:
            pk = _picture_key(x["path"])
            y = by_key.get(pk)
            if y is None:
                y = by_key[pk] = {**x, "videos": [k], "_each": {k: x}, **{f: list(x.get(f) or []) for f in ("jobs", "requests", "paths", "media_ids")}}
                out.append(y)
                continue
            if k not in y["videos"]:
                y["videos"].append(k)
            y["_each"].setdefault(k, x)
            for f in ("jobs", "requests", "paths", "media_ids"):
                y[f] += [v for v in x.get(f) or [] if v not in y[f]]
            for f in ("original", "original_name", "prepared", "recorded", "asset_id", "kind", "words"):
                if not y.get(f) and x.get(f):
                    y[f] = x[f]
    for y in out:
        each = y.pop("_each")
        vids = tuple(k for k in ("A", "B") if k in y["videos"])
        y["videos"] = list(vids)

        def said(field, each=each, vids=vids):
            vals = {k: str(each[k].get(field) or "") for k in vids}
            return next(iter(vals.values())) if len(set(vals.values())) == 1 else ", ".join(f"{v} of Video {k}" for k, v in vals.items() if v)
        y["image"] = said("image")
        y["asset_ids"] = {k: str(each[k].get("asset_id") or "") for k in vids}
        name = said("original_name") if all(each[k].get("original_name") for k in vids) else str(y.get("original_name") or y.get("name") or "")
        tail = " · its original file is not recorded" if all(str(each[k].get("label") or "").endswith("its original file is not recorded") for k in vids) else ""
        y["label"] = VIDEO_WORDS[vids] + " · " + " · ".join(v for v in (y["image"], said("asset_id"), name) if v) + tail
    return {"images": out, "two": True, "sent": sent}


def generation_assets(run: str, video: str, wrong: str = "", path: str = "", fixed: str = "", fixed_path: str = "") -> list[dict]:
    """The images the admin may show in a sample's band (the admin, 2026-10-30: "just show the product image sample that was used as a reference and
    attached to that video generation thats all ... and then if i want to exclude some or add more i'll do it myself"): exactly the images sent with
    the generation of the take (sent_images), one per attached image, each ticked by default (`suggested`); neither a client's original nor any other
    file is offered.  With the report's Video B (`fixed`, `fixed_path`; the admin, 2026-11-03) the images sent with its generation are offered too,
    each naming the video it was sent with (videos_sent_images).  [] when the run's records verify none."""
    takes = [("A", wrong, path)] + ([("B", fixed, fixed_path)] if (fixed or fixed_path) else [])
    return [{**x, "source": ("sent with job" + ("s " if len(x.get("jobs") or []) > 1 else " ") + ", ".join(j[:8] for j in x.get("jobs") or [x["job"]])
                             if x.get("job") else "sent with this generation"), "suggested": True}
            for x in videos_sent_images(run, video, takes)["images"]]


CLIP_PART_FILES = ("trimmed.mp4", "conformed.mp4", "raw.mp4")     # the file whose length a clip has in the joined video: trimmed when it was trimmed
CLIP_NAME = re.compile(r"c(?:\d+|[A-Z])")      # a clip's folder in a video's outputs: c1, c2 ..., or cA, cB ... as some older runs named them


def _clip_key(name: str) -> tuple:
    """Clip order: c1, c2 ... c10 by number, then cA, cB ... by letter."""
    tail = name[1:]
    return (0, int(tail), "") if tail.isdigit() else (1, 0, tail)


def _clip_dirs(vd: Path) -> list[Path]:
    return sorted((x for x in vd.iterdir() if x.is_dir() and CLIP_NAME.fullmatch(x.name)), key=lambda x: _clip_key(x.name)) if vd.is_dir() else []


def _copy_attempt(cd: Path, p: Path) -> int:
    """The attempt folder of clip cd holding a file byte for byte equal to p (the piece a join used is often a copy of one attempt's trimmed file), else 0."""
    try:
        size, sha = p.stat().st_size, ""
    except OSError:
        return 0
    for ad in reversed(_attempt_dirs(cd)):
        for f in _mp4s(ad):
            try:
                if f.stat().st_size == size:
                    sha = sha or media.sha1_file(p)
                    if media.sha1_file(f) == sha:
                        return int(ad.name[1:])
            except OSError:
                continue
    return 0


def _clip_attempt(rd: Path, nn: str, cd: Path) -> tuple[int, str]:
    """The attempt of a clip that went into the video and how that is known, in order: the clip's own piece (c1/c1.mp4) is a byte-for-byte copy of an
    attempt's file; the run's result says which attempt it shipped ('shipped': 'a1 ...', or an older run's 'delivered_attempt': '1'); the number of
    attempts the run made for it (each new attempt replaced the one before); the highest attempt folder holding a file.  (0, '') when none tells."""
    piece = cd / f"{cd.name}.mp4"
    if piece.is_file():
        n = _copy_attempt(cd, piece)
        if n:
            return n, f"{cd.name}/{piece.name} is a copy of a{n}'s file"
    res = _json(rd / "60-outputs" / f"video-{nn}" / "result.json") or {}
    jobs = [j for j in (res.get("jobs") or []) if isinstance(j, dict) and str(j.get("clip") or "").lower() == cd.name.lower()]
    for j in jobs:
        m = (re.match(r"\s*a(\d+)\b", str(j.get("shipped") or ""), re.I)
             or re.fullmatch(r"\s*a?(\d+)\s*", str(j.get("delivered_attempt") or ""), re.I))
        if m and (cd / f"a{m.group(1)}").is_dir():
            return int(m.group(1)), f"the run's result says {cd.name} shipped a{m.group(1)}"
    for j in jobs:
        if str(j.get("attempts") or "").isdigit() and (cd / f"a{j['attempts']}").is_dir():
            return int(j["attempts"]), f"the last of the {j['attempts']} attempt(s) the run made for {cd.name}"
    for ad in reversed(_attempt_dirs(cd)):
        if any((ad / n).is_file() for n in CLIP_PART_FILES):
            return int(ad.name[1:]), f"the highest attempt folder of {cd.name}"
    return 0, ""


def _clip_attempt_used(rd: Path, nn: str, cd: Path) -> int:
    return _clip_attempt(rd, nn, cd)[0]


# Which seconds of a joined version each generation made (the user, 2026-10-29: a frame is tied to the generation that made it, and a prompt box holds
# only the prompts of the generations its frames come from).  The join is read, never assumed: the record awl.py concat writes beside its output, a
# concat list (ffmpeg's `file '...'` lines) beside the file or beside a byte-for-byte copy of it, the clips' own pieces (c1/c1.mp4 ...), or the clips'
# trimmed files in clip order; a preview or deliverable rendered from the CapCut project is read through that project.  Each is used only when its
# pieces add up to the file's length, so a clip split around another one (c1, c2, then c1 again) or trimmed in the join is told as it is.
JOIN_TOL = 0.1                   # seconds: the pieces of a join must add up to the file's length within this to be trusted
JOIN_RECORD_SUFFIX = ".join.json"      # written by awl.py concat beside its output: the output's sha1 and every piece with its clip, attempt and seconds
_CONCAT_FILE = re.compile(r"""^\s*file\s+(?:'(.*)'|"(.*)"|(\S+))\s*$""")


def _dur(p: Path) -> float:
    try:
        return float(media.probe(p).get("duration") or 0)
    except Exception:  # noqa: BLE001  (an unreadable file has no length)
        return 0.0


def _where_in_video(vd: Path, p: Path) -> tuple[str, int]:
    """The clip (c1, c2 ...) and attempt a file of a video's folder sits in: c1/a2/c1a.mp4 is clip c1 attempt 2, c1/c1.mp4 clip c1 attempt 0 (in no
    attempt folder); ('', 0) for a file outside every clip folder (an insert, a clean video)."""
    try:
        parts = p.resolve().relative_to(vd.resolve()).parts[:-1]
    except (OSError, ValueError):
        return "", 0
    clip = next((x for x in parts if CLIP_NAME.fullmatch(x)), "")
    if not clip:
        return "", 0
    return clip, next((int(x[1:]) for x in parts[parts.index(clip) + 1:] if re.fullmatch(r"a\d+", x)), 0)


def _copies(vd: Path, p: Path) -> list[Path]:
    """p and the files under its video's folder that are byte for byte p, those inside an attempt folder first: a clean video copied up from the attempt
    folder it was joined in left its join record or list there."""
    out = [p]
    try:
        size, rp, sha = p.stat().st_size, p.resolve(), ""
    except OSError:
        return out
    for f in (sorted(vd.rglob("*.mp4")) if vd.is_dir() else []):
        try:
            if f.resolve() == rp or f.stat().st_size != size:
                continue
            sha = sha or media.sha1_file(p)
            if media.sha1_file(f) == sha:
                out.append(f)
        except OSError:
            continue
    in_attempt = lambda f: any(re.fullmatch(r"a\d+", x) for x in f.parts[-3:-1])  # noqa: E731
    return sorted(out, key=lambda f: (0 if in_attempt(f) else 1, 0 if f == p else 1))


def _concat_list(txt: Path) -> list[tuple[Path, float]] | None:
    """The pieces of an ffmpeg concat list, each with the seconds it lasts in the join (its inpoint/outpoint/duration lines when given), or None when the
    file is not such a list or a piece is missing."""
    try:
        lines = txt.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    items: list[dict] = []
    for ln in lines:
        if not ln.strip() or ln.strip().startswith(("#", "ffconcat")):
            continue
        m = _CONCAT_FILE.match(ln)
        if m:
            name = next(g for g in m.groups() if g is not None).replace("'\\''", "'")
            items.append({"path": (txt.parent / name) if not Path(name).is_absolute() else Path(name)})
            continue
        d = re.match(r"^\s*(inpoint|outpoint|duration)\s+([0-9]+(?:\.[0-9]+)?)\s*$", ln)
        if not d or not items:
            return None
        items[-1][d.group(1)] = float(d.group(2))
    if not items or not all(it["path"].is_file() for it in items):
        return None
    out = []
    for it in items:
        full = _dur(it["path"])
        a = it.get("inpoint", 0.0)
        b = it.get("outpoint", a + it["duration"] if "duration" in it else full)
        out.append((it["path"], max(0.0, min(b, full) - a)))
    return out


def _join_record(vd: Path, p: Path) -> tuple[list[tuple[Path, float, str, int]], str] | None:
    """The pieces of the join that made p, from the record awl.py concat wrote beside its output (matched on the output's sha1, so a copy finds it)."""
    try:
        sha = media.sha1_file(p)
    except OSError:
        return None
    for rec in (sorted(vd.rglob("*" + JOIN_RECORD_SUFFIX)) if vd.is_dir() else []):
        j = _json(rec) or {}
        if j.get("sha1") != sha or not isinstance(j.get("pieces"), list):
            continue
        out = []
        for x in j["pieces"]:
            try:
                out.append((Path(str(x.get("file") or "")), float(x["t_to"]) - float(x["t_from"]), str(x.get("clip") or ""), int(x.get("attempt") or 0)))
            except (TypeError, ValueError, KeyError):
                return None
        return out, f"the join record {rec.relative_to(vd).as_posix()}"
    return None


def _render_of_clean(rd: Path, nn: str, p: Path) -> Path | None:
    """The clean video a render of the whole timeline (a preview, a proxy, a deliverable with text only) shows: the run's recorded clean video, else
    clean.mp4 of the video's folder, of the same length within JOIN_TOL and written before this file (a render made before the clean was replaced shows
    an older take: it is never read as the newer one)."""
    rec = recorded_outputs(rd, nn)
    for c in (rec["clean"], rd / "60-outputs" / f"video-{nn}" / "clean.mp4"):
        try:
            if c and c.is_file() and not _same_file(c, p) and abs(_dur(c) - _dur(p)) <= JOIN_TOL and c.stat().st_mtime <= p.stat().st_mtime + 1:
                return c
        except OSError:
            continue
    return None


def _capcut_stores() -> list[Path]:
    out = []
    for _name, lib in core.LIBS:
        d = ((_json(lib / "config.json") or {}).get("capcut") or {}).get("drafts_dir")
        if d and Path(d) not in out:
            out.append(Path(d))
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return out + ([default] if default not in out else [])


def _capcut_pieces(rd: Path, nn: str, p: Path) -> tuple[list[tuple[Path, float, float, float]], str] | None:
    """The main video track of the video's CapCut project as [(file, timeline start, source start, seconds)], when p is a render of it: the project the
    run recorded, unchanged since p was written, as long as p."""
    st = _json(rd / "state.json") or {}
    ent = next((v for v in (st.get("videos") or []) if isinstance(v, dict) and core.nn(v.get("video")) == nn), {})
    res = _json(rd / "60-outputs" / f"video-{nn}" / "result.json") or {}
    name = str(res.get("capcut_project") or ent.get("capcut_project") or "")
    if not name:
        return None
    draft = next((s / name / "draft_content.json" for s in _capcut_stores() if (s / name / "draft_content.json").is_file()), None)
    if not draft:
        return None
    try:
        if draft.stat().st_mtime > p.stat().st_mtime + 1:
            return None
        c = json.loads(draft.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if abs(float(c.get("duration") or 0) / 1e6 - _dur(p)) > JOIN_TOL:
        return None
    vids = {m.get("id"): m for m in (c.get("materials") or {}).get("videos") or [] if isinstance(m, dict)}
    main = next((t for t in c.get("tracks") or [] if t.get("type") == "video"), None)
    out = []
    for sg in sorted((main or {}).get("segments") or [], key=lambda s: int((s.get("target_timerange") or {}).get("start") or 0)):
        m, tr, sr = vids.get(sg.get("material_id")) or {}, sg.get("target_timerange") or {}, sg.get("source_timerange") or {}
        if not m.get("path"):
            continue
        if abs(int(sr.get("duration") or 0) - int(tr.get("duration") or 0)) > 50000:      # a speed change: its seconds are not the source's
            return None
        out.append((Path(m["path"]), int(tr.get("start") or 0) / 1e6, int(sr.get("start") or 0) / 1e6, int(tr.get("duration") or 0) / 1e6))
    return (out, f"the CapCut project {name}") if out else None


def _through(rd: Path, nn: str, vd: Path, segments: list[tuple[Path, float, float, float]]) -> list[dict]:
    """The pieces of a CapCut main track [(file, timeline start, source start, seconds)] mapped to generations: a clip's take is that clip, a joined file
    its own join (_take_spans), anything else (an insert cut from the reference) is not a generation."""
    out = []
    for f, at, src0, secs in segments:
        clip, att = _where_in_video(vd, f)
        if clip:
            out.append({"t_from": round(at, 3), "t_to": round(at + secs, 3), "clip": clip, "attempt": att, "how": "the folder it sits in", "file": f.name})
            continue
        sub = _take_spans(rd, nn, f, 1) if f.is_file() else {"pieces": []}
        if not sub["pieces"]:
            out.append({"t_from": round(at, 3), "t_to": round(at + secs, 3), "clip": "", "attempt": 0, "how": "", "file": f.name})
            continue
        for x in sub["pieces"]:
            a, b = max(x["t_from"], src0), min(x["t_to"], src0 + secs)
            if b > a:
                out.append({**x, "t_from": round(at + a - src0, 3), "t_to": round(at + b - src0, 3)})
    return out


def _take_spans(rd: Path, nn: str, p: Path, depth: int = 0) -> dict:
    """Which seconds of the version p each generation made: {pieces: [{t_from, t_to, clip, attempt, how, file}], source, note} (a piece with no clip is
    not a generation: an insert or other footage), or {pieces: [], note} when the join cannot be read.  See the block comment above."""
    vd = rd / "60-outputs" / f"video-{nn}"
    total = _dur(p)

    def done(pieces, source):
        t, out = 0.0, []
        for path, secs, clip, att in pieces:
            how = "the folder it sits in" if att else ""
            if clip and not att:
                att = _copy_attempt(vd / clip, path) if path.is_file() else 0
                how = f"a copy of a{att}'s file" if att else ""
                if not att:
                    att, how = _clip_attempt(rd, nn, vd / clip)
            out.append({"t_from": round(t, 3), "t_to": round(t + secs, 3), "clip": clip, "attempt": att, "how": how, "file": path.name})
            t += secs
        return {"pieces": out, "source": source, "total": round(t, 3)}

    def fits(pieces):
        return pieces and abs(sum(x[1] for x in pieces) - total) <= JOIN_TOL

    def from_lists(files):
        for f in files:
            for txt in sorted(f.parent.glob("*.txt")):
                lst = _concat_list(txt)
                if lst:
                    pieces = [(path, secs, *_where_in_video(vd, path)) for path, secs in lst]
                    if fits(pieces) and any(x[2] for x in pieces):
                        return done(pieces, f"the concat list {txt.relative_to(vd).as_posix() if _inside(txt, vd) else txt.name}")
        return None
    rec = _join_record(vd, p)
    if rec and fits(rec[0]):
        return done(rec[0], rec[1])
    if depth == 0:          # a render of the CapCut project (unchanged since): its main track says which file each second shows
        cc = _capcut_pieces(rd, nn, p)
        if cc:
            got = _through(rd, nn, vd, cc[0])
            if any(x["clip"] for x in got):
                return {"pieces": got, "source": cc[1], "total": round(max(x["t_to"] for x in got), 3)}
    cps = _copies(vd, p)
    in_attempt = [f for f in cps if any(re.fullmatch(r"a\d+", x) for x in f.parts[-3:-1])]
    got = from_lists(in_attempt)          # the list in the attempt folder the file was joined in (a copy of it may sit elsewhere, beside an older list)
    if got:
        return got
    if depth == 0:          # a render of the whole clean video (a preview or proxy as long as it, made after it): the clean video's join
        clean = _render_of_clean(rd, nn, p)
        if clean:
            sp = _take_spans(rd, nn, clean, 1)
            if sp["pieces"]:
                return {**sp, "source": f"{sp['source']}, through the clean video {clean.name} (this file is a render of it: as long, made after it)"}
    got = from_lists([f for f in cps if f not in in_attempt])
    if got:
        return got
    clips = _clip_dirs(vd)
    own = [(cd / f"{cd.name}.mp4", _dur(cd / f"{cd.name}.mp4"), cd.name, 0) for cd in clips if (cd / f"{cd.name}.mp4").is_file()]
    if len(own) == len(clips) and fits(own):
        return done(own, "the clips' own pieces (" + ", ".join(f"{c}/{c}.mp4" for _p, _s, c, _a in own) + ") in clip order")
    naive = []
    for cd in clips:
        att, how = _clip_attempt(rd, nn, cd)
        part = next((cd / f"a{att}" / n for n in CLIP_PART_FILES if (cd / f"a{att}" / n).is_file()), None) if att else None
        if not part:
            naive = []
            break
        naive.append((part, _dur(part), cd.name, att))
    if fits(naive):
        return done(naive, "the clips' trimmed files in clip order")
    sums =", ".join(f"{c} {s:.3f} s" for _p, s, c, _a in naive) if naive else "the clips' files could not all be found"
    return {"pieces": [], "source": "", "note": f"which seconds each generation made could not be read from the run: no join record or concat list adds up to "
                                                  f"this file ({total:.3f} s), and the clips' own lengths ({sums}) do not either (a clip split around another one, an insert, or another cut)"}


def _clip_request(rd: Path, nn: str, clip: str, attempt: int) -> Path | None:
    """The video request of one clip and attempt: video-NN-cK-aJ-request.json, or video-NN-cK-request.json for the first attempt; a request whose own
    record names the clip is taken when its name does not."""
    files = video_request_files(rd, nn)
    own = [f for f in files if re.fullmatch(rf"video-{re.escape(nn)}-{re.escape(clip)}(?:-a\d+)?-request", Path(request_canon(f.name)).stem, re.I)]
    if not own:
        own = [f for f in files if str((_json(f) or {}).get("clip") or "").lower() == clip.lower()]
    if not own:
        return None
    exact = [f for f in own if _attempt_in_name(f.name) == (attempt or 1)]
    return exact[-1] if exact else sorted(own, key=lambda f: _attempt_in_name(f.name))[-1]


def _request_prompt_text(f: Path | None) -> str:
    req = (_json(f) or {}) if f else {}
    pr = (req.get("params") or {}).get("prompt") if isinstance(req.get("params"), dict) else None
    return str(pr or req.get("prompt") or "")


def _inside(p: Path, d: Path) -> bool:
    try:
        return os.path.normcase(str(p.resolve())).startswith(os.path.normcase(str(d.resolve())) + os.sep)
    except OSError:
        return False


def prompt_segments(run: str, video: str, hint: str = "") -> dict:
    """The generations a version of a video was made of, each with its prompt and every span of seconds of that version it made, read from how the
    version was joined (_take_spans, never assumed) and numbered in clip order ('generation 1 of 2'): a clip that comes back after another one (c1, c2,
    then c1 again) is one generation with two spans, and seconds no generation made (an insert) are listed apart.  A take of one clip gives that clip's
    own request.  Read from the run's files, nothing judged.  {kind: clips | clip | "", segments: [{clip, attempt, request, file, t_from, t_to, spans,
    gen, of, prompt, attempt_from}], inserts: [[from, to, file]], spans_known, source, note, file_s}; kind "" means a video of one generation
    (request_prompt_for).  t_from / t_to are a generation's first span, kept for older readers; `spans` holds all of them.  When the join cannot be read
    (spans_known false) no segment is given: which generation made a frame is then not known, and nothing is tied to it."""
    loc = locate(hint) if hint else None
    if loc and loc[1] and (loc[0], loc[1]) != (run, core.nn(video)):
        run, video = loc
    rd = run_dir(run)
    if not rd or not video:
        return {"kind": "", "segments": []}
    nn = core.nn(video)
    vd = rd / "60-outputs" / f"video-{nn}"
    clips = _clip_dirs(vd)
    p = Path(hint) if hint else None
    if not clips or not p or not p.is_file() or is_original(rd, nn, hint):
        return {"kind": "", "segments": []}
    own = next((cd for cd in clips if _inside(p, cd)), None)
    if own:                                   # a take of one clip: its own request, the whole file
        att = next((int(x[1:]) for x in reversed(p.parts[:-1]) if re.fullmatch(r"a\d+", x)), 0) or _clip_attempt_used(rd, nn, own)
        rq = _clip_request(rd, nn, own.name, att)
        dur = round(_dur(p), 3)
        return {"kind": "clip", "segments": [{"clip": own.name, "attempt": att, "request": rq.name if rq else "", "file": p.name, "t_from": 0.0,
                                               "t_to": dur, "spans": [[0.0, dur]], "gen": 1, "of": 1, "prompt": _request_prompt_text(rq)}],
                "spans_known": True, "note": f"a take of clip {own.name} alone: its own request" + ("" if rq else " (no request file of that clip was found)")}
    total = _dur(p)
    sp = _take_spans(rd, nn, p)
    if not sp["pieces"]:
        return {"kind": "clips", "segments": [], "inserts": [], "spans_known": False, "source": "", "note": sp.get("note", ""), "file_s": round(total, 3)}
    keys = []
    for x in sp["pieces"]:
        if x["clip"] and (x["clip"], x["attempt"]) not in keys:
            keys.append((x["clip"], x["attempt"]))
    keys.sort(key=lambda k: (_clip_key(k[0]), k[1]))
    segs = []
    for k, (clip, att) in enumerate(keys, 1):
        mine = [x for x in sp["pieces"] if (x["clip"], x["attempt"]) == (clip, att)]
        spans: list[list[float]] = []
        for x in mine:
            if spans and abs(spans[-1][1] - x["t_from"]) < 1e-3:
                spans[-1][1] = x["t_to"]
            else:
                spans.append([x["t_from"], x["t_to"]])
        rq = _clip_request(rd, nn, clip, att) if att else None
        segs.append({"clip": clip, "attempt": att, "request": rq.name if rq else "", "file": ", ".join(dict.fromkeys(x["file"] for x in mine)),
                     "t_from": spans[0][0], "t_to": spans[0][1], "spans": spans, "gen": k, "of": len(keys), "prompt": _request_prompt_text(rq),
                     "attempt_from": "; ".join(dict.fromkeys(x["how"] for x in mine if x["how"]))})
    inserts = [[x["t_from"], x["t_to"], x["file"]] for x in sp["pieces"] if not x["clip"]]
    note = (f"{len(segs)} generation(s), read from {sp['source']}: "
            + "; ".join(f"{s['clip']} a{s['attempt'] or '?'} made " + " and ".join(f"{_secs(a)}-{_secs(b)} s" for a, b in s["spans"]) for s in segs)
            + "".join(f"; {_secs(a)}-{_secs(b)} s is {f}, not a generation" for a, b, f in inserts) + f" (this file {total:.3f} s)")
    return {"kind": "clips", "segments": segs, "inserts": inserts, "spans_known": True, "source": sp["source"], "note": note,
            "clips_s": sp.get("total", 0.0), "file_s": round(total, 3)}


def _secs(x) -> str:
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def request_prompt_for(run: str, video: str, hint: str = "") -> dict:
    """The prompt of the request file a version of a video came from: the attempt the version belongs to picks the request (its attempt folder, or the attempt it
    is a copy of, or the attempt word of a label such as 'a2 · raw.mp4'); a take of another video or of another run is read in its own run; the original video
    and a file whose attempt cannot be told fill nothing and say why.  Requests for stills (start frames, guide plates) are never taken.  Read only: the report
    form fills a prompt field with it and the admin keeps or changes it.  {prompt, request, attempt}, {prompt: "", note} or {} when nothing was found."""
    loc = locate(hint) if hint else None
    if loc and loc[1] and (loc[0], loc[1]) != (run, core.nn(video)):      # a take of another video or of another run fills from its own run's request
        run, video = loc
    rd = run_dir(run)
    if not rd or not video:
        return {}
    if is_original(rd, core.nn(video), hint):
        return {"prompt": "", "attempt": 0, "note": "this is the original video the generation was made from: it was not generated, so it has no prompt"}
    attempt = attempt_for(rd, video, hint)
    if not attempt:        # never a guess: the admin picks the generated take the file came from, or pastes the prompt
        return {"prompt": "", "attempt": 0, "note": "which attempt this file came from cannot be told: it sits in no attempt folder, equals no attempt's file and is not "
                                                   "the run's recorded clean video or preview (or a copy of one). Pick the generated take it came from (a1, a2 …) and fill from it, or paste the prompt."}
    for rf in _request_files(rd, video, attempt):
        req = _json(rf) or {}
        pr = (req.get("params") or {}).get("prompt") if isinstance(req.get("params"), dict) else None
        pr = pr or req.get("prompt")
        if pr:
            return {"prompt": str(pr), "request": rf.name, "attempt": attempt}
    return {"prompt": "", "attempt": attempt, "note": f"attempt a{attempt} has no request file with a prompt in this run: paste the prompt"}


# ------------------------------------------------------------------ usage index (which input went into which request, and what happened)
ROLE_LABEL = {"image_references": "Image", "video_references": "Video", "audio_references": "Audio"}
USAGE_INDEX_VERSION = 3      # bumped when the index reads the request files differently: every run is then indexed again once


def _uploads(rd: Path) -> dict:
    """media id -> {name, file}: the label an upload was given and the local file the pipeline recorded for it, from the run's uploads maps
    (30-assets/uploads.json, uploads-map.json) and the uploads record in its state.json (assets.uploads), each {name: media id} or {name: {media_id or
    id, file or local or path}}; a key starting with '_' is a note, not an upload."""
    res = {}
    for j in [_json(rd / "30-assets" / fn) for fn in ("uploads.json", "uploads-map.json")] + [_state_uploads(rd)]:
        for name, v in (j.items() if isinstance(j, dict) else []):
            if str(name).startswith("_"):
                continue
            mid = (v.get("media_id") or v.get("id")) if isinstance(v, dict) else v
            if isinstance(mid, str) and mid.strip() and not re.search(r"\s", mid.strip()) and mid not in res:
                res[mid] = {"name": name, "file": str(run_file(rd, _upload_file(v)) or "") if isinstance(v, dict) else ""}
    return res


def _asset_path(rd: Path, name: str, req: dict):
    if not name:
        return None
    sp = req.get("source_prepared") if isinstance(req, dict) else None
    if sp and name.lower().startswith("src-") and (rd / str(sp)).is_file():
        return rd / str(sp)
    for base in (rd / "30-assets" / "stage", rd / "30-assets", rd / "10-media"):
        p = base / name
        if p.is_file():
            return p
    return None


def request_inputs(rd: Path, req: dict) -> list[dict]:
    prm = req.get("params") if isinstance(req.get("params"), dict) else req
    meaning = next((req[k] for k in ("medias_meaning", "media_meaning") if isinstance(req.get(k), dict)), {})
    ups = _uploads(rd)
    counts, res = {}, []
    for m in (prm.get("medias") or []):
        if not isinstance(m, dict):
            continue
        role, mid = str(m.get("role", "?")), str(m.get("value", ""))
        counts[role] = counts.get(role, 0) + 1
        base = ROLE_LABEL.get(role)
        label = f"{base} {counts[role]}" if base else role
        up = ups.get(mid, {})
        name = str(meaning.get(label) or up.get("name") or "")
        path = _asset_path(rd, name, req)
        if not path and up.get("file") and Path(up["file"]).is_file():
            path = Path(up["file"])
        res.append({"label": label, "role": role, "media_id": mid, "name": up.get("name") or name, "path": path})
    return res


_index_lock = threading.Lock()


def index_usage(force: bool = False, budget: float | None = None) -> int:
    """(Re)build the usage table from every run's request files and ledger.  Cheap when nothing changed (keyed by state.json mtime).
    One pass at a time; `budget` (seconds) stops early, the background pass finishes the rest."""
    if not _index_lock.acquire(blocking=False):
        return 0
    n = 0
    t0 = time.time()
    try:
        for d in sorted(x for r in runs_roots() if r.is_dir() for x in r.iterdir()):
            if budget is not None and time.time() - t0 > budget:
                break
            sp = d / "state.json"
            if not sp.is_file():
                continue
            sig = f"{sp.stat().st_mtime_ns}:v{USAGE_INDEX_VERSION}"
            key = f"usage_sig:{d.name}"
            if not force and core.setting_get(key) == sig:
                continue
            st = _json(sp) or {}
            subs = {Path(str(s.get("request_file", ""))).name: s for s in (st.get("submissions") or []) if isinstance(s, dict)}
            rows = []
            rq = d / "50-requests"
            for rf in request_files_named(rq, "video-"):
                req = _json(rf)
                if not isinstance(req, dict):
                    continue
                m = re.match(r"^video-(\d+)(?:-([a-z]\d+))?", request_canon(rf.name))
                video, attempt = (m.group(1), m.group(2) or ("a1" if is_video_request(req) else "image")) if m else ("", "")
                sub = subs.get(rf.name, {})
                prm = req.get("params") if isinstance(req.get("params"), dict) else req
                for it in request_inputs(d, req):
                    sha = ""
                    if it["path"]:
                        try:
                            sha = media.sha1_file(Path(it["path"])) if Path(it["path"]).stat().st_size < 400 * 2**20 else ""
                        except OSError:
                            sha = ""
                    rows.append((d.name, video, attempt, rf.name, sub.get("status") or "", 1 if sub.get("flagged") else 0,
                                 str(prm.get("model") or st.get("model") or ""), str(st.get("mode") or ""), sha, it["name"], it["role"]))
            con = core.db()
            try:
                con.execute("DELETE FROM usage WHERE run=?", (d.name,))
                con.executemany("INSERT INTO usage(run,video,attempt,req,status,flagged,model,mode,sha1,name,role) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
                con.commit()
            finally:
                con.close()
            core.setting_set(key, sig)
            n += len(rows)
        return n
    finally:
        _index_lock.release()
