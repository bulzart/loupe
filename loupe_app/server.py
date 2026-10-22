"""Loupe HTTP server (standard library).  Localhost only; admin = passphrase session cookie, agents = Bearer API keys with scopes."""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import compose, core, flags, media, misses, qc, runs

WEB = core.HOME / "web"
VERSION = "1.0"
MAX_JSON = 12 * 2**20
MAX_UPLOAD = 3 * 2**30
ROUTES: list = []


class Principal:
    def __init__(self, kind, name, scopes, csrf=None, key_id=None):
        self.kind, self.name, self.scopes, self.csrf, self.key_id = kind, name, set(scopes), csrf, key_id

    @property
    def admin(self):
        return self.kind == "admin"

    def can(self, scope):
        return self.admin or scope in self.scopes or "admin" in self.scopes


def route(method, pattern, scope=None):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, scope))
        return fn
    return deco


class HttpError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


class Handler(BaseHTTPRequestHandler):
    server_version = "Loupe/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # quiet
        pass

    # ---------------------------------------------------------------- plumbing
    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").lower()
        return h in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def principal(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            k = core.key_check(auth[7:].strip())
            if k:
                return Principal("key", k["name"], k["scopes"], key_id=k["id"])
            raise HttpError(401, "invalid or revoked API key")
        s = core.session_get(self._cookie("loupe_session"))
        if s:
            return Principal("admin", "admin", core.SCOPES, csrf=s["csrf"])
        return None

    def send_json(self, obj, status=200, headers=None):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_JSON:
            raise HttpError(413, "body too large")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HttpError(400, "the body is not valid JSON")

    def serve_file(self, p: Path, ctype=None, cache="no-store"):
        size = p.stat().st_size
        ctype = ctype or mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # ---------------------------------------------------------------- dispatch
    def _dispatch(self):
        try:
            if not self._host_ok():
                raise HttpError(403, "bad Host header (Loupe answers on localhost only)")
            u = urlparse(self.path)
            path = unquote(u.path)
            self.q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not path.startswith("/api/"):
                return self.static(path)
            self.pr = None
            for method, rx, fn, scope in ROUTES:
                m = rx.match(path)
                if m and (method == self.command or (method == "GET" and self.command == "HEAD")):
                    if scope is not None:
                        self.pr = self.principal()
                        if self.pr is None:
                            raise HttpError(401, "sign in, or send an API key as 'Authorization: Bearer lk_...'")
                        if not self.pr.can(scope):
                            raise HttpError(403, f"this key lacks the '{scope}' scope")
                        if self.pr.admin and self.command not in ("GET", "HEAD"):
                            if not hmac.compare_digest(self.headers.get("X-Loupe-CSRF") or "", self.pr.csrf or "x"):
                                raise HttpError(403, "missing or wrong CSRF header")
                    return fn(self, *m.groups())
            raise HttpError(404, "no such endpoint")
        except HttpError as e:
            self.send_json({"ok": False, "error": e.msg}, e.status)
        except PermissionError as e:
            self.send_json({"ok": False, "error": str(e)}, 403)
        except (FileNotFoundError, LookupError) as e:
            self.send_json({"ok": False, "error": f"not found: {e}"}, 404)
        except (ValueError, KeyError, TypeError) as e:
            self.send_json({"ok": False, "error": f"bad request: {e}"}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"ok": False, "error": f"server error: {type(e).__name__}: {str(e)[:200]}"}, 500)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _dispatch

    def static(self, path):
        if self.command not in ("GET", "HEAD"):
            raise HttpError(405, "method not allowed")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        p = (WEB / rel).resolve()
        if not (WEB.resolve() in p.parents and p.is_file()):
            if "." not in Path(path).name:      # an app route: the single page
                p = WEB / "index.html"
            else:
                raise HttpError(404, "not found")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(str(p))[0] or "text/plain") + ("; charset=utf-8" if p.suffix in (".html", ".js", ".css") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def _ctx(h) -> dict:
    return {"principal": h.pr.name if h.pr else ""}


# ======================================================================= auth
@route("GET", "/api/health")
def health(h):
    h.send_json({"ok": True, "app": "loupe", "version": VERSION, "admin_set": core.admin_is_set()})


@route("POST", "/api/setup")
def setup(h):
    if core.admin_is_set():
        raise HttpError(403, "the admin passphrase is already set")
    core.admin_set(str(h.body_json().get("password") or ""))
    return _login_ok(h)


def _login_ok(h):
    tok, csrf = core.session_new()
    h.send_json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": f"loupe_session={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age={core.SESSION_HOURS * 3600}"})


@route("POST", "/api/login")
def login(h):
    pw = str(h.body_json().get("password") or "")
    if not core.admin_is_set():
        raise HttpError(409, "no admin passphrase yet: set one first")
    if not core.admin_check(pw):
        core.audit("anonymous", "login.failed")
        raise HttpError(401, "wrong passphrase (5 wrong tries lock the login for a minute)")
    core.audit("admin", "login")
    return _login_ok(h)


@route("POST", "/api/logout", scope="runs:read")
def logout(h):
    core.session_delete(h._cookie("loupe_session"))
    h.send_json({"ok": True}, headers={"Set-Cookie": "loupe_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})


@route("GET", "/api/session")
def session(h):
    pr = h.principal() if (h.headers.get("Authorization") or h._cookie("loupe_session")) else None
    h.send_json({"ok": True, "signed_in": bool(pr), "kind": pr.kind if pr else None, "csrf": pr.csrf if pr else None,
                 "admin_set": core.admin_is_set(), "scopes": sorted(pr.scopes) if pr else []})


# ======================================================================= settings, keys, audit
@route("GET", "/api/settings", scope="admin")
def get_settings(h):
    h.send_json({"ok": True, "allowed_roots": core.setting_get("allowed_roots", []), "default_roots": [str(r) for r in media.default_roots()],
                 "keeper_model": core.setting_get("keeper_model", qc.DEFAULT_MODEL), "keeper_extract_model": core.setting_get("keeper_extract_model", qc.EXTRACT_MODEL_DEFAULT),
                 "atp": str(core.ATP), "atg": str(core.ATG), "awl": str(core.AWL), "data": str(core.DATA), "kinds": misses.KINDS})


@route("POST", "/api/settings", scope="admin")
def set_settings(h):
    b = h.body_json()
    if "allowed_roots" in b:
        roots = []
        for r in b["allowed_roots"]:
            p = Path(str(r)).expanduser()
            if not p.is_dir():
                raise ValueError(f"not a folder: {r}")
            roots.append(str(p.resolve()))
        core.setting_set("allowed_roots", roots)
    for key in ("keeper_model", "keeper_extract_model"):       # the keeper's model, and the model that reads each sample's picture once
        if key in b and re.match(r"^[A-Za-z0-9._-]{3,60}$", str(b[key])):
            core.setting_set(key, b[key])
    core.audit("admin", "settings.update", "", core.jdump({k: b[k] for k in b if k in ("allowed_roots", "keeper_model", "keeper_extract_model")}))
    return get_settings(h)


@route("GET", "/api/keys", scope="admin")
def keys_list(h):
    h.send_json({"ok": True, "keys": core.key_list(), "scopes": list(core.SCOPES)})


@route("POST", "/api/keys", scope="admin")
def keys_create(h):
    b = h.body_json()
    k = core.key_create(str(b.get("name") or ""), list(b.get("scopes") or []))
    h.send_json({"ok": True, **k})


@route("POST", r"/api/keys/(\d+)/revoke", scope="admin")
def keys_revoke(h, kid):
    core.key_revoke(int(kid))
    h.send_json({"ok": True})


@route("GET", "/api/audit", scope="admin")
def audit_list(h):
    con = core.db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
    finally:
        con.close()
    h.send_json({"ok": True, "audit": rows})


@route("GET", "/api/overview", scope="runs:read")
def overview(h):
    con = core.db()
    try:
        n = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        o = {"flags": n("SELECT COUNT(*) FROM flags"), "flags_open": n("SELECT COUNT(*) FROM flags WHERE status='open'"),
             "misses": n("SELECT COUNT(*) FROM misses WHERE status='active'"), "samples": n("SELECT COUNT(*) FROM moments WHERE status='active'"),
             "captures_unclaimed": n("SELECT COUNT(*) FROM captures WHERE claimed_by IS NULL AND dismissed=0"),
             "keys": n("SELECT COUNT(*) FROM api_keys WHERE revoked=0"), "consults": n("SELECT COUNT(*) FROM consults")}
    finally:
        con.close()
    h.send_json({"ok": True, **o})


# ======================================================================= runs and media
@route("GET", "/api/runs", scope="runs:read")
def runs_list(h):
    h.send_json({"ok": True, "runs": runs.list_runs()})


@route("GET", r"/api/runs/([^/]+)", scope="runs:read")
def run_get(h, run):
    d = runs.run_detail(run)
    if not d:
        raise LookupError(run)
    h.send_json({"ok": True, **d})


@route("GET", "/api/video", scope="runs:read")
def video(h):
    p = media.resolve_media(h.q.get("path", ""))
    h.serve_file(p)


@route("GET", "/api/probe", scope="runs:read")
def probe(h):
    p = media.resolve_media(h.q.get("path", ""))
    h.send_json({"ok": True, **media.probe(p)})


@route("GET", "/api/frame", scope="runs:read")
def frame(h):
    p = media.resolve_media(h.q.get("path", ""))
    png = media.frame_file(p, float(h.q.get("t") or 0))
    h.serve_file(png, "image/png", cache="private, max-age=3600")


@route("PUT", "/api/upload", scope="admin")
def upload(h):
    name = core.safe_name(h.q.get("name", "upload.mp4"))
    if Path(name).suffix.lower() not in core.MEDIA_EXT:
        raise ValueError("only video and image files can be uploaded")
    n = int(h.headers.get("Content-Length") or 0)
    if n <= 0 or n > MAX_UPLOAD:
        raise HttpError(413, "empty or too large (max 3 GB)")
    dest = core.DATA / "uploads" / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    left = n
    with open(dest, "wb") as f:
        while left > 0:
            chunk = h.rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        dest.unlink(missing_ok=True)
        raise HttpError(400, "the upload ended early")
    core.audit("admin", "upload", str(dest.name))
    h.send_json({"ok": True, "path": str(dest), "label": name, **media.probe(dest)})


# ======================================================================= flags
def _product_thumb(path: Path) -> Path:
    stt = path.stat()
    key = hashlib.sha1(f"{path}|{stt.st_mtime_ns}|{stt.st_size}".encode("utf-8")).hexdigest()[:16]
    out = core.DATA / "cache" / "product" / f"{key}.png"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        compose.load_product(path, 520).save(out, optimize=True)
    return out


@route("GET", "/api/product-sample", scope="runs:read")
def product_sample(h):
    run, video, wrong = h.q.get("run", ""), h.q.get("video", ""), h.q.get("wrong", "")
    qs = "run=%s&video=%s&wrong=%s" % (quote(run), quote(video), quote(wrong))
    h.send_json({"ok": True, "samples": [{"asset_id": p["asset_id"], "label": p["label"], "name": p["name"], "image_url": f"/api/product-sample/image?{qs}&i={i}"}
                                          for i, p in enumerate(runs.product_samples(run, video, wrong))]})


@route("GET", "/api/request-prompt", scope="runs:read")
def request_prompt(h):
    """The prompt of the request file a version of a video came from (the attempt in its path picks the request): read for the report form, never judged."""
    h.send_json({"ok": True, "prompt": "", **runs.request_prompt_for(h.q.get("run", ""), h.q.get("video", ""), h.q.get("path", ""))})


@route("GET", "/api/product-sample/image", scope="runs:read")
def product_sample_image(h):
    ps = runs.product_samples(h.q.get("run", ""), h.q.get("video", ""), h.q.get("wrong", ""))
    i = int(h.q.get("i") or 0)
    if not 0 <= i < len(ps):
        raise LookupError("no such product sample")
    h.serve_file(_product_thumb(ps[i]["path"]), "image/png", cache="private, max-age=300")


@route("GET", "/api/flags", scope="flags:read")
def flags_list(h):
    total, rows = flags.search(h.q.get("q", ""), h.q.get("status", ""), int(h.q.get("limit") or 500), int(h.q.get("offset") or 0))
    h.send_json({"ok": True, "total": total, "entries": rows})


@route("GET", "/api/flags-stats", scope="flags:read")
def flags_stats(h):
    h.send_json({"ok": True, **flags.stats()})


@route("POST", "/api/flags", scope="flags:write")
def flags_add(h):
    doc = h.body_json()
    if not isinstance(doc, dict) or not doc.get("title"):
        raise ValueError("a flag needs at least a title")
    saved = flags.save(doc, h.pr.name, create=True)
    h.send_json({"ok": True, "id": saved["id"], "entry": saved})


@route("GET", r"/api/flags/(CF-\d+)", scope="flags:read")
def flags_get(h, fid):
    d = flags.get(fid)
    if not d:
        raise LookupError(fid)
    h.send_json({"ok": True, "entry": d})


@route("PUT", r"/api/flags/(CF-\d+)", scope="flags:write")
def flags_put(h, fid):
    doc = h.body_json()
    if not flags.get(fid):
        raise LookupError(fid)
    doc["id"] = fid
    h.send_json({"ok": True, "id": fid, "entry": flags.save(doc, h.pr.name)})


@route("PATCH", r"/api/flags/(CF-\d+)", scope="flags:write")
def flags_patch(h, fid):
    cur = flags.get(fid)
    if not cur:
        raise LookupError(fid)
    patch = h.body_json()
    for k in ("status", "title", "kind", "outcome", "changed", "rule", "promoted_to", "response", "notes"):
        if k in patch:
            cur[k] = patch[k]
    if isinstance(patch.get("trigger"), dict):
        cur.setdefault("trigger", {}).update(patch["trigger"])
    if isinstance(patch.get("cleared"), dict):
        cur.setdefault("cleared", {}).update(patch["cleared"])
    if patch.get("note"):
        cur.setdefault("log", []).append({"at": core.now(), "note": str(patch["note"])[:2000], "by": h.pr.name})
    h.send_json({"ok": True, "id": fid, "entry": flags.save(cur, h.pr.name)})


@route("DELETE", r"/api/flags/(CF-\d+)", scope="admin")
def flags_delete(h, fid):
    flags.delete(fid, h.pr.name)
    h.send_json({"ok": True})


@route("POST", r"/api/flags/(CF-\d+)/refresh", scope="admin")
def flags_refresh(h, fid):
    d = flags.refresh_facts(fid, h.pr.name)
    if not d:
        raise LookupError(fid)
    h.send_json({"ok": True, "entry": d})


@route("GET", r"/api/flags/(CF-\d+)/file/([^/]+)", scope="flags:read")
def flags_file(h, fid, name):
    p = flags.flag_file(fid, name)
    if not p:
        raise LookupError(name)
    h.serve_file(p, "image/jpeg", cache="private, max-age=600")


# ----------------------------------------------------------------------- captures (raw tool responses the hook saved)
@route("POST", "/api/captures", scope="flags:write")
def captures_add(h):
    h.send_json({"ok": True, "id": flags.capture_add(h.body_json())})


@route("GET", "/api/captures", scope="flags:read")
def captures_list(h):
    con = core.db()
    try:
        sql, args = "SELECT * FROM captures", []
        cond = []
        if h.q.get("job"):
            cond.append("(job LIKE ? OR summary LIKE ?)"); args += [f"%{h.q['job']}%"] * 2
        if h.q.get("run"):
            cond.append("run=?"); args.append(h.q["run"])
        if h.q.get("unclaimed"):
            cond.append("claimed_by IS NULL AND dismissed=0")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        rows = [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT ?", args + [int(h.q.get("limit") or 100)])]
    finally:
        con.close()
    if h.q.get("full"):
        for r in rows:
            try:
                r["payload"] = json.loads(Path(r["file"]).read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                r["payload"] = None
    h.send_json({"ok": True, "captures": rows})


@route("POST", r"/api/captures/(\d+)/claim", scope="flags:write")
def captures_claim(h, cid):
    con = core.db()
    try:
        con.execute("UPDATE captures SET claimed_by=? WHERE id=?", (str(h.body_json().get("flag") or h.pr.name), int(cid)))
        con.commit()
    finally:
        con.close()
    h.send_json({"ok": True})


@route("POST", r"/api/captures/(\d+)/dismiss", scope="admin")
def captures_dismiss(h, cid):
    con = core.db()
    try:
        con.execute("UPDATE captures SET dismissed=1 WHERE id=?", (int(cid),))
        con.commit()
    finally:
        con.close()
    h.send_json({"ok": True})


# ======================================================================= misses and samples
@route("GET", "/api/misses", scope="admin")
def misses_list(h):
    h.send_json({"ok": True, "misses": misses.listing(h.q.get("run", ""), h.q.get("status", ""))})


@route("GET", "/api/misses/suggest-kinds", scope="admin")
def misses_suggest_kinds(h):
    """The kinds of the vocabulary whose own words occur in the admin's text: a suggestion the admin confirms in the form, never saved by itself."""
    h.send_json({"ok": True, "suggested": misses.suggest_kinds(h.q.get("text", ""))})


@route("POST", "/api/misses", scope="admin")
def misses_create(h):
    h.send_json({"ok": True, "miss": misses.create(h.body_json(), h.pr.name)})


@route("GET", r"/api/misses/(\d+)", scope="admin")
def misses_get(h, mid):
    m = misses.get(int(mid))
    if not m:
        raise LookupError(mid)
    h.send_json({"ok": True, "miss": m})


@route("PATCH", r"/api/misses/(\d+)", scope="admin")
def misses_patch(h, mid):
    m = misses.update(int(mid), h.body_json(), h.pr.name)
    if not m:
        raise LookupError(mid)
    h.send_json({"ok": True, "miss": m})


@route("DELETE", r"/api/misses/(\d+)", scope="admin")
def misses_delete(h, mid):
    misses.delete(int(mid), h.pr.name)
    h.send_json({"ok": True})


@route("POST", r"/api/misses/(\d+)/moments", scope="admin")
def moments_add(h, mid):
    h.send_json({"ok": True, "moment": misses.add_moment(int(mid), h.body_json(), h.pr.name)})


@route("PATCH", r"/api/moments/(\d+)", scope="admin")
def moments_patch(h, mid):
    m = misses.update_moment(int(mid), h.body_json(), h.pr.name)
    if not m:
        raise LookupError(mid)
    h.send_json({"ok": True, "moment": m})


@route("DELETE", r"/api/moments/(\d+)", scope="admin")
def moments_delete(h, mid):
    misses.delete_moment(int(mid), h.pr.name)
    h.send_json({"ok": True})


@route("GET", r"/api/moments/(\d+)/image", scope="qc:read")
def moments_image(h, mid):
    p = misses.moment_file(int(mid), h.q.get("kind", "composite"))
    if not p:
        raise LookupError("image")
    h.serve_file(p, "image/png", cache="private, max-age=60")


# ======================================================================= QC (what the QC phase and the keeper use)
def _qc_sample(s: dict) -> dict:
    return {k: s[k] for k in ("id", "title", "explanation", "details", "correct_note", "kinds", "model", "mode", "product", "run", "video",
                              "t_wrong", "t_fixed", "frames", "n_frames", "idea", "better", "missed", "prompts", "prompt_note", "sample_kind", "has_right", "image_url", "zoom_url", "composite_path", "created") if k in s}


@route("GET", "/api/qc/samples", scope="qc:read")
def qc_samples(h):
    ctx = {"model": h.q.get("model", ""), "mode": h.q.get("mode", ""), "kinds": [k for k in h.q.get("kinds", "").split(",") if k],
           "product": h.q.get("product", ""), "q": h.q.get("q", "")}
    sam = qc.search(ctx, int(h.q.get("limit") or 8))
    h.send_json({"ok": True, "library_size": len(qc.library()), "samples": [dict(_qc_sample(s), score=s.get("score")) for s in sam]})


@route("GET", r"/api/qc/samples/S(\d+)", scope="qc:read")
def qc_sample(h, mid):
    s = next((x for x in qc.library() if x["moment_id"] == int(mid)), None)
    if not s:
        raise LookupError("S" + mid)
    out = _qc_sample(s)
    if h.q.get("prompts") in ("1", "true", "yes"):        # the prompts of the two takes, whole: one sample, on request only (never in a list, a brief or the keeper's digest)
        out.update({k: v for k, v in (misses.prompts_of(int(mid)) or {}).items() if k in ("wrong_prompt", "fixed_prompt", "prompt_note")})
    h.send_json({"ok": True, "sample": out})


@route("GET", r"/api/qc/samples/S(\d+)/image", scope="qc:read")
def qc_sample_image(h, mid):
    if not any(x["moment_id"] == int(mid) for x in qc.library()):
        raise LookupError("S" + mid)
    p = misses.moment_file(int(mid), h.q.get("kind", "composite"))
    if not p:
        raise LookupError("image")
    h.serve_file(p, "image/png", cache="private, max-age=60")


@route("POST", "/api/qc/brief", scope="qc:read")
def qc_brief(h):
    b = h.body_json()
    h.send_json({"ok": True, **qc.brief(b, int(b.get("limit") or 8))})


@route("POST", "/api/qc/consult", scope="qc:read")
def qc_consult(h):
    b = h.body_json()
    q = str(b.get("question") or "").strip()
    if not q:
        raise ValueError("a question is needed")
    h.send_json({"ok": True, **qc.consult(h.pr.name, b, q, "plan" if b.get("purpose") == "plan" else "qc")})


@route("GET", "/api/qc/keeper", scope="admin")
def keeper_get(h):
    con = core.db()
    try:
        cons = [dict(r) for r in con.execute("SELECT id,ts,key_name,run,video,question,sample_ids,ms,mode,cost,purpose,asks FROM consults ORDER BY id DESC LIMIT 50")]
    finally:
        con.close()
    for c in cons:          # what the keeper asked for, could not get, held back or found the library does not cover: shown to the admin, who can add what is missing
        try:
            c["asks"] = json.loads(c["asks"]) if c.get("asks") else {}
        except ValueError:
            c["asks"] = {}
    h.send_json({"ok": True, "keeper": qc.keeper_status(), "consults": cons})


@route("GET", "/api/qc/keeper/state", scope="qc:read")
def keeper_state(h):
    """What a pipeline's status or doctor command may know about the keeper without the admin's rights: in sync or not, turns, the library version, the model."""
    st = qc.keeper_status()
    h.send_json({"ok": True, **{k: st[k] for k in ("in_sync", "turns", "chars", "library", "library_sig", "last_synced", "last_used", "model", "claude_found", "created", "understanding") if k in st}})


@route("GET", "/api/qc/keeper/understanding", scope="admin")
def keeper_understanding(h):
    """What the keeper understood of each sample's picture (one reading per active sample, null when not read yet) and the counts."""
    h.send_json({"ok": True, "status": qc.understanding_status(), "readings": qc.readings()})


@route("POST", "/api/qc/keeper/understand", scope="admin")
def keeper_understand(h):
    """Read the samples that have no current reading, in the background (the watcher does the same a little after the library changes).
    {"ids": ["S12"]} re-reads those samples even when their reading is current."""
    body = h.body_json() or {}
    only = [str(x) for x in (body.get("ids") or []) if str(x).startswith("S")][:50] or None
    threading.Thread(target=qc.sync_understanding, args=(h.pr.name, only), daemon=True).start()
    h.send_json({"ok": True, "started": True, "status": qc.understanding_status()})


@route("POST", "/api/qc/keeper/sync", scope="qc:read")
def keeper_sync(h):
    """What a run start calls: samples without a current reading of their picture (new or changed ones) start being read in the background, the keeper
    is brought up to date now with what is read, and the answer says how much of the library it holds from the pictures (the watcher tells it the rest)."""
    u = qc.understanding_status()
    started = False
    if not u["current"]:
        threading.Thread(target=qc.sync_understanding, args=(h.pr.name,), daemon=True).start()
        started = True
    r = qc.sync_keeper(h.pr.name)
    h.send_json({"ok": True, **r, "understanding": {k: u[k] for k in ("library_size", "understood", "missing", "stale", "current")}, "reading_started": started})


@route("POST", "/api/qc/keeper/reset", scope="admin")
def keeper_reset(h):
    qc.keeper_reset(h.pr.name)
    h.send_json({"ok": True})


# ======================================================================= startup
def _background():
    while True:
        try:
            flags.ingest_outbox()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(20)


KEEPER_QUIET = 25     # seconds a library must stay unchanged before the keeper is told (a run of edits costs one call, not one each)


def _keeper_watch():
    """Keeps the keeper current: it boots with the server when a library exists, and re-reads the library a little after the admin's last edit.
    Before the keeper is told, every sample without a current reading of its picture is read (only those: new or changed samples)."""
    last, stable, retry_at, read_retry_at = None, 0, 0.0, 0.0
    first = True
    while True:
        try:
            sig = qc._sig()
            stable = stable + 1 if sig == last else 0
            last = sig
            if (first or stable * 10 >= KEEPER_QUIET) and time.time() >= read_retry_at and not qc.understanding_status()["current"]:
                u = qc.sync_understanding("watcher")       # only the samples without a current reading; nothing when the readings match the library
                if u.get("state") not in ("current", "busy"):
                    print("keeper readings:", {k: u.get(k) for k in ("state", "read", "retired", "failed", "cost")}, flush=True)
                if u.get("state") in ("failed", "partial"):
                    read_retry_at = time.time() + 900      # a sample that cannot be read is tried again in fifteen minutes; the keeper is told meanwhile
            elif (first or (stable * 10 >= KEEPER_QUIET and not qc.keeper_status().get("in_sync"))) and time.time() >= retry_at:     # the keeper is told once the readings are done
                first = False
                r = qc.sync_keeper("watcher")
                if r.get("state") in ("booted", "synced", "failed"):
                    print("keeper:", r, flush=True)
                if r.get("state") == "failed":
                    retry_at = time.time() + 300           # the claude CLI may be missing or signed out: try again in five minutes, not every tick
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(10)


def _warm():
    while True:
        try:
            runs.index_usage()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(300)


def serve(port: int | None = None):
    core.ensure_dirs()
    core.db().close()
    threading.Thread(target=_background, daemon=True).start()
    threading.Thread(target=_warm, daemon=True).start()
    threading.Thread(target=_keeper_watch, daemon=True).start()
    httpd = ThreadingHTTPServer((core.HOST, port or core.PORT), Handler)
    httpd.daemon_threads = True
    (core.DATA / "server.json").write_text(json.dumps({"port": httpd.server_port, "pid": __import__("os").getpid(), "started": core.now()}), encoding="utf-8")
    print(f"Loupe {VERSION} on http://{core.HOST}:{httpd.server_port}  (data: {core.DATA})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
