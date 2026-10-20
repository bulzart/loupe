"""Loupe runs: browse the pipeline's runs and videos, and index which input assets were used in which request and what became of it."""
from __future__ import annotations

import json
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


def delivered_attempt(rd: Path, nn: str) -> int:
    """The attempt whose finished file is the video's clean deliverable, byte for byte (0 when none is): the take its clean video and preview come from."""
    for clean in (rd / "60-outputs" / f"video-{core.nn(nn)}" / "clean.mp4", rd / "deliverables" / f"video-{core.nn(nn)}-clean.mp4"):
        if clean.is_file():
            n = _identical_attempt(rd, nn, clean)
            if n:
                return n
    return 0


def attempt_of_path(rd: Path, nn: str, path: str) -> int:
    """The attempt (1, 2 ...) a picked file of a video belongs to, or 0 when it cannot be told: the attempt folder it sits in (60-outputs/video-NN/a2/raw.mp4),
    else the attempt it is a copy of."""
    if not path:
        return 0
    p = Path(path)
    for part in reversed(p.parts[:-1]):
        m = re.fullmatch(r"a(\d+)", part)
        if m:
            return int(m.group(1))
    return _identical_attempt(rd, nn, p) if p.is_file() else 0


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
    its clean video and preview are made from it), 'processed' (conformed, trimmed, with the original audio, copies, previews), 'deliverable' or 'client-fix';
    `attempt` is the attempt folder a file sits in; `delivered` marks the attempt whose finished file is the run's clean video, byte for byte."""
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
    n_del = delivered_attempt(d, nn)
    for v in out:
        if v.get("attempt"):
            v["delivered"] = bool(n_del) and v["attempt"] == n_del
    return out


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
    """Product names the run's asset registry mentions (free text from its own file, for tagging; never a built-in list)."""
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
    """The run's product registry: key -> {name, canonical, ids}; an entry marked unused is left out."""
    a = st.get("assets") if isinstance(st.get("assets"), dict) else {}
    res = {}
    for key, v in ((a.get("products") or {}) if isinstance(a.get("products"), dict) else {}).items():
        if not isinstance(v, dict) or v.get("unused"):
            continue
        ids = _expand(v.get("canonical") or "") + _expand(v.get("files")) + _expand(v.get("other_files"))
        res[str(key)] = {"name": str(v.get("name") or v.get("flavour") or v.get("role") or "").strip(), "canonical": (_expand(v.get("canonical") or "") or ids[:1] or [""])[0],
                         "ids": list(dict.fromkeys(ids))}
    return res


def _request_files(rd: Path, video: str, attempt: int) -> list[Path]:
    """The video requests of a video, the attempt's own first; with no attempt known, the latest attempt first."""
    files = video_request_files(rd, video)
    if attempt:
        want = [f for f in files if _attempt_in_name(f.name) == attempt]
        return want + [f for f in files if f not in want]
    return sorted(files, key=lambda f: (_attempt_in_name(f.name), f.name), reverse=True)


def product_samples(run: str, video: str, wrong: str = "") -> list[dict]:
    """The product image(s) a video's request used, as the original files in the client's own reference folder.  The request names each image input
    (by its asset id, "<what it is>: A14", or by the product code in its prepared file name, "P08-front.jpg"); the run's registry says which ids and
    codes are products; the run's asset inventory maps an id to the original file.  The attempt of the wrong version (a1, a2 ...) picks the request.
    Nothing found: an empty list (the report then has no product sample)."""
    rd = run_dir(run)
    if not rd or not video:
        return []
    st = _json(rd / "state.json") or {}
    prods = _registry_products(st)
    id_owner = {i: k for k, v in prods.items() for i in v["ids"]}
    inv = {it["id"]: it for it in ((_json(rd / "30-assets" / "project" / "asset-inventory.json") or {}).get("items") or []) if isinstance(it, dict) and it.get("id")}
    roots = [Path(x) for x in (st.get("assets_paths") or []) if isinstance(x, str)]
    attempt = attempt_for(rd, video, wrong)

    def file_of(aid: str):
        it = inv.get(aid) or {}
        path = Path(it["path"]) if it.get("path") else None
        if not (path and path.is_file()) and it.get("rel"):
            path = next((r / it["rel"] for r in roots if (r / it["rel"]).is_file()), None)
        return path if (path and path.is_file()) else None

    for rf in _request_files(rd, video, attempt):
        req = _json(rf) or {}
        meaning = next((req[k] for k in ("medias_meaning", "media_meaning") if isinstance(req.get(k), dict)), {})
        files = req.get("media_files") if isinstance(req.get("media_files"), dict) else {}
        out, seen = [], set()
        for label, text in meaning.items():
            if not str(label).lower().startswith("image"):
                continue
            text = str(text)
            hay = text + " " + Path(str(files.get(label) or "")).name
            found = A_ID.findall(text)
            aid = next((a for a in found if a in id_owner), None)           # the request names the asset itself
            key = id_owner.get(aid or "")
            from_text = aid is not None
            if aid is None:                                                 # or the product code of its prepared file: the registry's canonical original
                code = next((c for c in P_CODE.findall(hay) if c in prods), None)
                if code:
                    key, aid = code, prods[code]["canonical"] or None
            if aid is None and not prods and found and not NOT_PRODUCT.search(text):      # an older run with no registry
                aid = found[-1]
            if not aid or aid in seen:
                continue
            path = file_of(aid)
            if not path:
                continue
            seen.add(aid)
            said = re.split(r":\s*A\d", text)[0].strip() if from_text else ""       # what the request itself called it, else the registry's name
            name = said or (prods.get(key or "") or {}).get("name") or aid
            out.append({"asset_id": aid, "label": name[:90], "path": path, "name": path.name, "request": rf.name})
        if out:
            return out[:2]
    return []


def request_prompt_for(run: str, video: str, hint: str = "") -> dict:
    """The prompt of the request file a version of a video came from: the attempt the version belongs to picks the request (its attempt folder, or the attempt it
    is a copy of, or the attempt word of a label such as 'a2 · raw.mp4'); a file whose attempt cannot be told takes the latest attempt's request.  Requests for stills
    (start frames, guide plates) are never taken.  Read only: the report form fills a prompt field with it and the admin keeps or changes
    it.  {prompt, request, attempt} or {} when nothing was found."""
    rd = run_dir(run)
    if not rd or not video:
        return {}
    attempt = attempt_for(rd, video, hint)
    for rf in _request_files(rd, video, attempt):
        req = _json(rf) or {}
        pr = (req.get("params") or {}).get("prompt") if isinstance(req.get("params"), dict) else None
        pr = pr or req.get("prompt")
        if pr:
            return {"prompt": str(pr), "request": rf.name, "attempt": attempt}
    return {}


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
