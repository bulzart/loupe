"""Loupe runs: browse the pipeline's runs and videos, and index which input assets were used in which request and what became of it."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import core, media

VIDEO_NAMES = ("*.mp4", "*.mov", "*.webm", "*.mkv", "*.m4v")


def runs_root() -> Path:
    return core.ATP / "runs"


def _json(p: Path):
    try:
        return json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def run_dir(run: str) -> Path | None:
    if not run or "/" in run or "\\" in run or run.startswith("."):
        return None
    d = runs_root() / run
    return d if d.is_dir() else None


def run_number(name: str) -> str:
    m = re.search(r"-(\d+)$", name)
    return m.group(1).lstrip("0") or "0" if m else ""


def _mp4s(d: Path) -> list[Path]:
    out = []
    for pat in VIDEO_NAMES:
        out += list(d.glob(pat))
    return sorted(out)


def list_runs() -> list[dict]:
    root = runs_root()
    res = []
    if not root.is_dir():
        return res
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        st = _json(d / "state.json")
        if st is not None:
            vids = st.get("videos") or []
            res.append({"run": d.name, "kind": "run", "mode": st.get("mode"), "submode": st.get("analyze_submode"), "model": st.get("model"),
                        "stage": st.get("stage"), "created": st.get("created"), "updated": st.get("updated"),
                        "videos": len(vids) if isinstance(vids, list) else 0, "number": run_number(d.name)})
        elif _mp4s(d):
            res.append({"run": d.name, "kind": "library", "videos": len(_mp4s(d)), "number": "", "mode": "files", "model": "", "stage": ""})
    return res


def _version(p: Path, label: str, kind: str) -> dict:
    st = p.stat()
    return {"path": str(p), "label": label, "kind": kind, "mtime": st.st_mtime, "size": st.st_size}


def library_matches(run: str, nn: str) -> list[dict]:
    """Loose files in the runs folder's non-run subfolders (client fixes) that name this run's number and video."""
    num = run_number(run)
    res = []
    root = runs_root()
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
    d = run_dir(run)
    if not d:
        return []
    out = []
    vd = d / "60-outputs" / f"video-{nn}"
    if vd.is_dir():
        for p in sorted(vd.glob("*.mp4")):
            out.append(_version(p, p.name, "output"))
        for ad in sorted(x for x in vd.iterdir() if x.is_dir() and re.match(r"^a\d+$", x.name)):
            for p in sorted(ad.glob("*.mp4")):
                out.append(_version(p, f"{ad.name} · {p.name}", "attempt"))
            for sub in sorted(x for x in ad.iterdir() if x.is_dir()):
                for p in sorted(sub.glob("*.mp4")):
                    out.append(_version(p, f"{ad.name} · {sub.name}/{p.name}", "attempt"))
        for cd in sorted(x for x in vd.iterdir() if x.is_dir() and re.match(r"^c\d+$", x.name)):
            for p in sorted(cd.rglob("*.mp4")):
                out.append(_version(p, f"{cd.name}/{p.relative_to(cd).as_posix()}", "clip"))
    dd = d / "deliverables"
    if dd.is_dir():
        for p in sorted(dd.glob(f"video-{nn}-*.mp4")):
            out.append(_version(p, f"deliverables/{p.name}", "deliverable"))
    out += library_matches(run, nn)
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
NOT_PRODUCT = re.compile(r"identity|guide|prepared|source|start frame|hand-off|portrait|face", re.I)


def _product_ids(st: dict) -> set:
    """The asset ids the run's registry lists as products (an entry marked unused is not one)."""
    a = st.get("assets") if isinstance(st.get("assets"), dict) else {}
    ids = set()
    for v in ((a.get("products") or {}) if isinstance(a.get("products"), dict) else {}).values():
        if isinstance(v, dict) and not v.get("unused"):
            if isinstance(v.get("canonical"), str):
                ids.add(v["canonical"])
            for k in ("files", "other_files"):
                ids.update(x for x in (v.get(k) or []) if isinstance(x, str))
    return ids


def _request_files(rd: Path, video: str, attempt: int) -> list[Path]:
    files = sorted((rd / "50-requests").glob(f"video-{core.nn(video)}*request.json"))
    if attempt:
        want = [f for f in files if f"-a{attempt}-" in f.name] if attempt > 1 else [f for f in files if not re.search(r"-a\d+-", f.name)]
        files = want + [f for f in files if f not in want]
    return files


def product_samples(run: str, video: str, wrong: str = "") -> list[dict]:
    """The product image(s) a video's request used, as files in the client's own reference folder.  The request names each image input with
    its asset id ("<what it is>: A14"); the run's registry says which ids are products; the run's asset inventory maps an id to the original file.
    The attempt of the wrong version (a1, a2 ...) picks the request.  Nothing found: an empty list (the report then has no product sample)."""
    rd = run_dir(run)
    if not rd or not video:
        return []
    st = _json(rd / "state.json") or {}
    ids = _product_ids(st)
    inv = {it["id"]: it for it in ((_json(rd / "30-assets" / "project" / "asset-inventory.json") or {}).get("items") or []) if isinstance(it, dict) and it.get("id")}
    roots = [Path(p) for p in (st.get("assets_paths") or []) if isinstance(p, str)]
    m = re.search(r"\ba(\d+)\b", str(wrong or "").replace("\\", " ").replace("/", " "), re.I)
    attempt = int(m.group(1)) if m else 0
    for rf in _request_files(rd, video, attempt):
        req = _json(rf) or {}
        meaning = next((req[k] for k in ("medias_meaning", "media_meaning") if isinstance(req.get(k), dict)), {})
        out, seen = [], set()
        for label, text in meaning.items():
            if not str(label).lower().startswith("image"):
                continue
            text = str(text)
            found = A_ID.findall(text)
            aid = next((a for a in found if a in ids), None)
            if aid is None and not ids and found and not NOT_PRODUCT.search(text):      # an older run with no registry: the image that is not a person, a guide or a source
                aid = found[-1]
            if not aid or aid in seen:
                continue
            it = inv.get(aid) or {}
            path = Path(it["path"]) if it.get("path") else None
            if not (path and path.is_file()) and it.get("rel"):
                path = next((r / it["rel"] for r in roots if (r / it["rel"]).is_file()), None)
            if not (path and path.is_file()):
                continue
            seen.add(aid)
            out.append({"asset_id": aid, "label": re.split(r":\s*A\d", text)[0].strip()[:90] or aid, "path": path, "name": path.name, "request": rf.name})
        if out:
            return out[:2]
    return []


# ------------------------------------------------------------------ usage index (which input went into which request, and what happened)
ROLE_LABEL = {"image_references": "Image", "video_references": "Video", "audio_references": "Audio"}


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
        for d in sorted(runs_root().iterdir()) if runs_root().is_dir() else []:
            if budget is not None and time.time() - t0 > budget:
                break
            sp = d / "state.json"
            if not sp.is_file():
                continue
            sig = f"{sp.stat().st_mtime_ns}"
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
                m = re.match(r"^video-(\d+)(?:-(a\d+))?", rf.name)
                video, attempt = (m.group(1), m.group(2) or "a1") if m else ("", "")
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
