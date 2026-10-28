"""Loupe runs: browse the pipeline's runs and videos, and index which input assets were used in which request and what became of it."""
from __future__ import annotations

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


def video_request_files(rd: Path, video: str) -> list[Path]:
    """The request files of a video that asked for a video, by name (the stills asked for beside it are left out)."""
    rq = rd / "50-requests"
    if not rq.is_dir():
        return []
    return [f for f in sorted(rq.glob(f"video-{core.nn(video)}*request.json")) if is_video_request(_json(f))]


def _attempt_in_name(name: str) -> int:
    """The attempt a request file is for: video-03-a2-request.json is the second, a name with no attempt tag is the first."""
    m = re.search(r"-a(\d+)-", name)
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
        for cd in sorted(x for x in vd.iterdir() if x.is_dir() and re.match(r"^c\d+$", x.name)):
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
        f = u.get("file") if isinstance(u, dict) else None
        if m and isinstance(f, str) and f and Path(f).is_file():
            sent.append((Path(f), m.group(1) or "", False))
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
            out.append({"asset_id": aid, "label": name[:90], "path": path, "name": path.name, "request": rf.name})
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


def generation_assets(run: str, video: str, wrong: str = "", path: str = "") -> list[dict]:
    """Every image the generation of a video's take used, for the admin to pick the product sample from (the user, 2026-10-28: the sample picked by itself
    was sometimes wrong).  For each image input of that attempt's video request(s): the client's original (the asset the request names, from the run's
    inventory), the file sent to the model (the upload the run recorded) and the file the video's uploads record names, each once, with what the request
    calls it; `suggested` marks the automatic pick (product_samples), which is always offered.  A take of another video or of another run reads its own run.
    Read from the run's records, nothing judged; [] when nothing is found."""
    loc = locate(path or wrong) if (path or wrong) else None
    if loc and loc[1] and (loc[0], loc[1]) != (run, core.nn(video)):
        run, video, wrong = loc[0], loc[1], path or wrong
    rd = run_dir(run)
    if not rd or not video:
        return []
    nn = core.nn(video)
    st = _json(rd / "state.json") or {}
    prods = _registry_products(st)
    id_owner = {i: k for k, v in prods.items() for i in v["ids"]}
    inv = {it["id"]: it for it in ((_json(rd / "30-assets" / "project" / "asset-inventory.json") or {}).get("items") or []) if isinstance(it, dict) and it.get("id")}
    roots = [Path(x) for x in (st.get("assets_paths") or []) if isinstance(x, str)]
    led = (_json(rd / "50-requests" / f"video-{nn}-uploads.json") or {}).get("uploads")
    led = led if isinstance(led, dict) else {}
    attempt = attempt_for(rd, video, wrong)
    files = _request_files(rd, video, attempt)
    reqs = [f for f in files if attempt and _attempt_in_name(f.name) == attempt] or files[:1]
    segs = prompt_segments(run, video, wrong).get("segments") or []
    if segs:                                  # a video joined from clips: every clip's request (each generation's inputs), or the one clip picked
        reqs = [rd / "50-requests" / s["request"] for s in segs if s.get("request")] or reqs
    auto = product_samples(run, video, wrong)
    suggested = {_file_key(p["path"]) for p in auto}

    def file_of(aid: str):
        it = inv.get(aid) or {}
        p = Path(it["path"]) if it.get("path") else None
        if not (p and p.is_file()) and it.get("rel"):
            p = next((r / it["rel"] for r in roots if (r / it["rel"]).is_file()), None)
        return p if (p and p.is_file()) else None

    out, seen = [], set()

    def add(p, label: str, source: str, aid: str, req: str):
        if not p:
            return
        p = Path(p)
        if not p.is_absolute():
            p = rd / p
        if not (p.is_file() and p.suffix.lower() in IMAGE_EXT):
            return
        k = _file_key(p)
        if k in seen:
            return
        seen.add(k)
        out.append({"path": p, "name": p.name, "label": label[:120], "source": source, "asset_id": aid or "", "request": req, "suggested": k in suggested})

    for rf in reqs:
        req = _json(rf) or {}
        meaning = _input_meanings(req)
        tag = _request_tag(rf.name, nn)
        for inp in request_inputs(rd, req):
            lab = inp["label"]
            hit = _ledger_entry(led, lab, tag)
            text = str(meaning.get(lab) or (hit[0] if hit else "") or inp.get("name") or "")
            found = A_ID.findall(text)
            aid = next((a for a in found if a in id_owner), None) or (found[-1] if found else "")
            if not aid:
                code = next((c for c in P_CODE.findall(text + " " + Path(str(inp.get("path") or (hit[1] if hit else "") or "")).name) if c in prods), None)
                aid = (prods[code]["canonical"] or "") if code else ""
            what = (_said(text, aid) if aid and aid in found else "") or ((prods.get(id_owner.get(aid, "")) or {}).get("name") if aid else "") or _input_words(text)
            name = f"{lab} · {what}" if what else lab
            add(file_of(aid) if aid else None, name, "the client's original", aid, rf.name)
            add(inp.get("path"), name, "as sent to the model", aid, rf.name)
            if hit:
                add(hit[1], name, "as the uploads record names it", aid, rf.name)
    for p in auto:
        add(p["path"], p["label"], "the client's original", p["asset_id"], p.get("request", ""))
    return out


CLIP_PART_FILES = ("trimmed.mp4", "conformed.mp4", "raw.mp4")     # the file whose length a clip has in the joined video: trimmed when it was trimmed


def _clip_dirs(vd: Path) -> list[Path]:
    return sorted((x for x in vd.iterdir() if x.is_dir() and re.fullmatch(r"c\d+", x.name)), key=lambda x: int(x.name[1:])) if vd.is_dir() else []


def _clip_attempt_used(rd: Path, nn: str, cd: Path) -> int:
    """The attempt of a clip that went into the video: the attempts result.json records for that clip, else the highest attempt folder holding a file."""
    res = _json(rd / "60-outputs" / f"video-{nn}" / "result.json") or {}
    for j in res.get("jobs") or []:
        if isinstance(j, dict) and str(j.get("clip") or "").lower() == cd.name and str(j.get("attempts") or "").isdigit() and (cd / f"a{j['attempts']}").is_dir():
            return int(j["attempts"])
    for ad in reversed(_attempt_dirs(cd)):
        if any((ad / n).is_file() for n in CLIP_PART_FILES):
            return int(ad.name[1:])
    return 0


def _clip_request(rd: Path, nn: str, clip: str, attempt: int) -> Path | None:
    """The video request of one clip and attempt: video-NN-cK-aJ-request.json, or video-NN-cK-request.json for the first attempt; a request whose own
    record names the clip is taken when its name does not."""
    files = video_request_files(rd, nn)
    own = [f for f in files if re.fullmatch(rf"video-{re.escape(nn)}-{re.escape(clip)}(?:-a\d+)?-request", f.stem, re.I)]
    if not own:
        own = [f for f in files if str((_json(f) or {}).get("clip") or "").lower() == clip]
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
    """The generations a version of a video was made of, each with its prompt and the seconds of that version it made (the user, 2026-10-28: a clip
    made of several generations keeps each generation's prompt, and the judge is told which seconds, and which frames of a sample, each prompt made).
    A version joined from clips (the clean video, the preview, a deliverable, joined.mp4 of a video with c1/, c2/ ... folders) gives one segment per
    clip in their order, each as long as that clip's own file in the join (trimmed.mp4, else conformed.mp4, else raw.mp4), its prompt read from that
    clip's request; a take of one clip gives that clip's own request.  Read from the run's files, nothing judged.  {kind: clips | clip | "",
    segments: [{clip, attempt, request, file, t_from, t_to, prompt}], note}; kind "" means a video of one generation (request_prompt_for)."""
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
        dur = float(media.probe(p).get("duration") or 0)
        return {"kind": "clip", "segments": [{"clip": own.name, "attempt": att, "request": rq.name if rq else "", "file": p.name, "t_from": 0.0,
                                               "t_to": round(dur, 3), "prompt": _request_prompt_text(rq)}],
                "note": f"a take of clip {own.name} alone: its own request" + ("" if rq else " (no request file of that clip was found)")}
    segs, t = [], 0.0
    for cd in clips:
        att = _clip_attempt_used(rd, nn, cd)
        part = next((cd / f"a{att}" / n for n in CLIP_PART_FILES if (cd / f"a{att}" / n).is_file()), None) if att else None
        dur = float(media.probe(part).get("duration") or 0) if part else 0.0
        rq = _clip_request(rd, nn, cd.name, att)
        segs.append({"clip": cd.name, "attempt": att, "request": rq.name if rq else "", "file": f"{cd.name}/a{att}/{part.name}" if part else "",
                     "t_from": round(t, 3), "t_to": round(t + dur, 3), "prompt": _request_prompt_text(rq)})
        t += dur
    total = float(media.probe(p).get("duration") or 0)
    note = (f"{len(segs)} generations joined in clip order; each span is its clip's own length in the join ("
            + ", ".join(f"{s['clip']} {s['t_to'] - s['t_from']:.3f} s from {s['file'] or 'no file'}" for s in segs) + f"): together {t:.3f} s, this file {total:.3f} s")
    if total and abs(total - t) > 0.15:
        note += ". They differ (inserts between the clips, or another cut): check each span against the video before saving"
    return {"kind": "clips", "segments": segs, "note": note, "clips_s": round(t, 3), "file_s": round(total, 3)}


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
USAGE_INDEX_VERSION = 2      # bumped when the index reads the request files differently: every run is then indexed again once


def _uploads(rd: Path) -> dict:
    """media id -> {name, file}: the label an upload was given and the local file the pipeline recorded for it."""
    res = {}
    for fn in ("uploads.json", "uploads-map.json"):
        j = _json(rd / "30-assets" / fn)
        for name, v in (j.items() if isinstance(j, dict) else []):
            mid = v.get("media_id") if isinstance(v, dict) else v
            if mid and mid not in res:
                res[mid] = {"name": name, "file": (v.get("file") if isinstance(v, dict) else "") or ""}
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
            for rf in sorted(rq.glob("video-*request.json")) if rq.is_dir() else []:
                req = _json(rf)
                if not isinstance(req, dict):
                    continue
                m = re.match(r"^video-(\d+)(?:-([a-z]\d+))?", rf.name)
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
