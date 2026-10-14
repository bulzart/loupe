"""Loupe core: paths, SQLite, admin login, sessions, API keys, audit.  Standard library only.

Loupe is the local web app of the /analyze-then-pick pipeline: it keeps the Higgsfield flag record, the generation misses
with their wrong-vs-correct sample images, and serves those samples to the QC phase through an API key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("LOUPE_HOME") or Path(__file__).resolve().parent.parent)
DATA = Path(os.environ.get("LOUPE_DATA") or HOME / "data")
ATP = Path(os.environ.get("ATP_HOME") or Path.home() / ".claude" / "analyze-then-pick")
PORT = int(os.environ.get("LOUPE_PORT") or 8787)
HOST = "127.0.0.1"

SCOPES = ("flags:write", "flags:read", "qc:read", "runs:read", "admin")
SESSION_HOURS = 12
MEDIA_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def ensure_dirs():
    for d in ("flags", "samples", "cache/frames", "uploads", "outbox", "keys", "keeper", "captures"):
        (DATA / d).mkdir(parents=True, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, csrf TEXT, created TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS api_keys(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, prefix TEXT, hash TEXT UNIQUE,
  scopes TEXT, created TEXT, last_used TEXT, revoked INTEGER DEFAULT 0, uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS flags(id TEXT PRIMARY KEY, seq INTEGER, created TEXT, updated TEXT, status TEXT, kind TEXT,
  run TEXT, video TEXT, attempt TEXT, model TEXT, mode TEXT, title TEXT, tags TEXT, search TEXT, doc TEXT);
CREATE INDEX IF NOT EXISTS flags_run ON flags(run);
CREATE INDEX IF NOT EXISTS flags_status ON flags(status);
CREATE TABLE IF NOT EXISTS flag_inputs(flag_id TEXT, idx INTEGER, sha1 TEXT, name TEXT, role TEXT, suspect INTEGER);
CREATE INDEX IF NOT EXISTS flag_inputs_sha ON flag_inputs(sha1);
CREATE TABLE IF NOT EXISTS usage(run TEXT, video TEXT, attempt TEXT, req TEXT, status TEXT, flagged INTEGER, model TEXT,
  mode TEXT, sha1 TEXT, name TEXT, role TEXT);
CREATE INDEX IF NOT EXISTS usage_sha ON usage(sha1);
CREATE INDEX IF NOT EXISTS usage_name ON usage(name);
CREATE TABLE IF NOT EXISTS filehash(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha1 TEXT);
CREATE TABLE IF NOT EXISTS misses(id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, run TEXT, video TEXT,
  title TEXT, explanation TEXT, kinds TEXT, model TEXT, mode TEXT, product TEXT, status TEXT DEFAULT 'active',
  wrong_path TEXT, wrong_label TEXT, fixed_path TEXT, fixed_label TEXT);
CREATE TABLE IF NOT EXISTS moments(id INTEGER PRIMARY KEY AUTOINCREMENT, miss_id INTEGER, created TEXT, updated TEXT,
  t_wrong REAL, t_fixed REAL, details TEXT, correct_note TEXT, shapes TEXT, kinds TEXT, status TEXT DEFAULT 'active',
  w INTEGER, h INTEGER);
CREATE INDEX IF NOT EXISTS moments_miss ON moments(miss_id);
CREATE TABLE IF NOT EXISTS consults(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, key_name TEXT, run TEXT, video TEXT,
  question TEXT, sample_ids TEXT, answer TEXT, ms INTEGER, mode TEXT, cost REAL, purpose TEXT DEFAULT 'qc');
CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, tool TEXT, run TEXT, agent TEXT,
  job TEXT, summary TEXT, file TEXT, claimed_by TEXT, dismissed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
"""

_init_lock = threading.Lock()
_inited = False


def db() -> sqlite3.Connection:
    global _inited
    ensure_dirs()
    con = sqlite3.connect(str(DATA / "loupe.db"), timeout=30)
    con.row_factory = sqlite3.Row
    if not _inited:
        with _init_lock:
            if not _inited:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                if "purpose" not in [r[1] for r in con.execute("PRAGMA table_info(consults)")]:      # a database made before planning hints existed
                    con.execute("ALTER TABLE consults ADD COLUMN purpose TEXT DEFAULT 'qc'")
                con.commit()
                _inited = True
    con.execute("PRAGMA busy_timeout=30000")
    return con


def setting_get(k: str, default=None):
    con = db()
    try:
        r = con.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default
    finally:
        con.close()


def setting_set(k: str, v):
    con = db()
    try:
        con.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, jdump(v)))
        con.commit()
    finally:
        con.close()


def audit(actor: str, action: str, target: str = "", detail: str = ""):
    con = db()
    try:
        con.execute("INSERT INTO audit(ts,actor,action,target,detail) VALUES(?,?,?,?,?)", (now(), actor, action, target, detail[:2000]))
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ admin password
ITER = 200_000
_fail = {"n": 0, "until": 0.0}


def _pbkdf2(pw: str, salt: bytes, iters: int = ITER) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)).decode("ascii")


def admin_is_set() -> bool:
    return setting_get("admin_pw") is not None


def admin_set(pw: str):
    if len(pw) < 8:
        raise ValueError("the admin passphrase must have at least 8 characters")
    salt = secrets.token_bytes(16)
    setting_set("admin_pw", {"salt": base64.b64encode(salt).decode("ascii"), "hash": _pbkdf2(pw, salt), "iters": ITER})
    audit("system", "admin.set-password")


def admin_check(pw: str) -> bool:
    if time.time() < _fail["until"]:
        return False
    rec = setting_get("admin_pw")
    ok = False
    if rec:
        got = _pbkdf2(pw, base64.b64decode(rec["salt"]), int(rec.get("iters", ITER)))
        ok = hmac.compare_digest(got, rec["hash"])
    if ok:
        _fail["n"] = 0
        return True
    _fail["n"] += 1
    if _fail["n"] >= 5:
        _fail["until"] = time.time() + 60
        _fail["n"] = 0
    time.sleep(0.4)
    return False


# ------------------------------------------------------------------ sessions
def session_new() -> tuple[str, str]:
    tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    con = db()
    try:
        con.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        con.execute("INSERT INTO sessions(token,csrf,created,expires) VALUES(?,?,?,?)", (tok, csrf, now(), time.time() + SESSION_HOURS * 3600))
        con.commit()
    finally:
        con.close()
    return tok, csrf


def session_get(tok: str):
    if not tok:
        return None
    con = db()
    try:
        r = con.execute("SELECT * FROM sessions WHERE token=? AND expires>?", (tok, time.time())).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def session_delete(tok: str):
    con = db()
    try:
        con.execute("DELETE FROM sessions WHERE token=?", (tok,))
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ API keys
def key_create(name: str, scopes: list[str]) -> dict:
    scopes = [s for s in scopes if s in SCOPES]
    if not name.strip() or not scopes:
        raise ValueError("a key needs a name and at least one scope: " + ", ".join(SCOPES))
    plain = "lk_" + secrets.token_urlsafe(30)
    con = db()
    try:
        cur = con.execute("INSERT INTO api_keys(name,prefix,hash,scopes,created) VALUES(?,?,?,?,?)",
                          (name.strip(), plain[:8], hashlib.sha256(plain.encode()).hexdigest(), ",".join(scopes), now()))
        con.commit()
        kid = cur.lastrowid
    finally:
        con.close()
    audit("admin", "key.create", f"key {kid}", f"{name} {scopes}")
    return {"id": kid, "name": name.strip(), "scopes": scopes, "key": plain}


def key_check(plain: str):
    if not plain or not plain.startswith("lk_"):
        return None
    h = hashlib.sha256(plain.encode()).hexdigest()
    con = db()
    try:
        r = con.execute("SELECT * FROM api_keys WHERE hash=? AND revoked=0", (h,)).fetchone()
        if not r:
            return None
        con.execute("UPDATE api_keys SET last_used=?, uses=uses+1 WHERE id=?", (now(), r["id"]))
        con.commit()
        return {"id": r["id"], "name": r["name"], "scopes": set(r["scopes"].split(","))}
    finally:
        con.close()


def key_list() -> list[dict]:
    con = db()
    try:
        return [dict(id=r["id"], name=r["name"], prefix=r["prefix"], scopes=r["scopes"].split(","), created=r["created"],
                     last_used=r["last_used"], revoked=bool(r["revoked"]), uses=r["uses"])
                for r in con.execute("SELECT * FROM api_keys ORDER BY id")]
    finally:
        con.close()


def key_revoke(kid: int):
    con = db()
    try:
        con.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (kid,))
        con.commit()
    finally:
        con.close()
    audit("admin", "key.revoke", f"key {kid}")


def key_write_file(name: str, plain: str):
    """Keep a key where the pipeline's scripts read it (DATA/keys/<name>.key, never tracked)."""
    ensure_dirs()
    p = DATA / "keys" / f"{name}.key"
    p.write_text(plain, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


# ------------------------------------------------------------------ small helpers
def nn(video) -> str:
    m = re.search(r"(\d{1,3})", str(video or ""))
    return m.group(1).zfill(2) if m else str(video or "")


def safe_name(s: str, n: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s))[:n].strip("._") or "file"
