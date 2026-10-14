"""Loupe media: which files may be served, ffprobe / frame extraction (cached), thumbnails, file hashes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import core

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_tool(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",):
        for ext in (".exe", ""):
            c = base / (name + ext)
            if c.exists():
                return str(c)
    return name


def run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NOWIN)


# ------------------------------------------------------------------ which files may be read
def default_roots() -> list[Path]:
    home = Path.home()
    roots = [core.ATP / "runs", core.DATA]
    for d in ("Documents", "Downloads", "Desktop", "Videos", "Pictures"):
        roots.append(home / d)
    return roots


def allowed_roots() -> list[Path]:
    extra = [Path(p) for p in (core.setting_get("allowed_roots", []) or [])]
    out = []
    for r in default_roots() + extra:
        try:
            out.append(r.resolve())
        except OSError:
            pass
    return out


def resolve_media(path_str: str) -> Path:
    """The real path of a media file the app may read; PermissionError / FileNotFoundError otherwise."""
    if not path_str or "\x00" in path_str:
        raise FileNotFoundError("no path")
    p = Path(path_str)
    try:
        rp = p.resolve(strict=True)
    except OSError:
        raise FileNotFoundError(path_str)
    if not rp.is_file() or rp.suffix.lower() not in core.MEDIA_EXT:
        raise PermissionError("only video and image files can be served")
    n = os.path.normcase(str(rp))
    for r in allowed_roots():
        rn = os.path.normcase(str(r))
        if n == rn or n.startswith(rn.rstrip("\\/") + os.sep):
            return rp
    raise PermissionError("outside the folders Loupe may read; add the folder in Settings")


def kind_of(p: Path) -> str:
    return "video" if p.suffix.lower() in VIDEO_EXT else "image"


# ------------------------------------------------------------------ probing
_probe_cache: dict = {}


def probe(p: Path) -> dict:
    key = (str(p), p.stat().st_mtime)
    if key in _probe_cache:
        return _probe_cache[key]
    info = {"kind": kind_of(p), "size": p.stat().st_size}
    try:
        cp = run([find_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)], 30)
        j = json.loads(cp.stdout.decode("utf-8", "replace") or "{}")
        v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
        fr = v.get("avg_frame_rate", "0/1")
        try:
            n, d = fr.split("/")
            fps = float(n) / float(d) if float(d) else 0.0
        except ValueError:
            fps = 0.0
        info.update(width=int(v.get("width") or 0), height=int(v.get("height") or 0), fps=round(fps, 3),
                    duration=float(j.get("format", {}).get("duration") or v.get("duration") or 0), has_audio=bool(a),
                    codec=v.get("codec_name", ""))
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)[:200]
    _probe_cache[key] = info
    return info


# ------------------------------------------------------------------ frames
def frame_file(p: Path, t: float) -> Path:
    """A lossless PNG of the video frame at time t (seconds), cached; an image is converted to PNG as is."""
    st = p.stat()
    t = max(0.0, float(t or 0.0))
    key = hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}|{t:.3f}".encode()).hexdigest()
    out = core.DATA / "cache" / "frames" / f"{key}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.png")
    if kind_of(p) == "image":
        from PIL import Image
        Image.open(p).convert("RGB").save(tmp, "PNG")
    else:
        dur = probe(p).get("duration") or 0.0
        if dur and t > dur - 0.02:
            t = max(0.0, dur - 0.04)
        cp = run([find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", str(p),
                  "-an", "-frames:v", "1", str(tmp)], 90)
        if cp.returncode != 0 or not tmp.exists():
            raise RuntimeError("ffmpeg could not extract a frame: " + cp.stderr.decode("utf-8", "replace")[-300:])
    os.replace(tmp, out)
    return out


def thumb_jpeg(p: Path, t: float | None = None, width: int = 240) -> bytes:
    from PIL import Image
    png = frame_file(p, t or 0.0)
    im = Image.open(png).convert("RGB")
    h = max(1, round(im.height * width / im.width))
    im = im.resize((width, h))
    import io
    b = io.BytesIO()
    im.save(b, "JPEG", quality=78)
    return b.getvalue()


# ------------------------------------------------------------------ hashes
def sha1_file(p: Path) -> str:
    """sha1 of a file, remembered by (path, size, mtime).  No database connection is held while the file is read."""
    st = p.stat()
    con = core.db()
    try:
        r = con.execute("SELECT * FROM filehash WHERE path=?", (str(p),)).fetchone()
        if r and r["size"] == st.st_size and abs(r["mtime"] - st.st_mtime) < 1e-6:
            return r["sha1"]
    finally:
        con.close()
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest()
    con = core.db()
    try:
        con.execute("INSERT INTO filehash(path,size,mtime,sha1) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,"
                    "mtime=excluded.mtime,sha1=excluded.sha1", (str(p), st.st_size, st.st_mtime, sha))
        con.commit()
    finally:
        con.close()
    return sha
