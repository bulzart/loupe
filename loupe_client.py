"""Loupe client (standard library only): used by atp.py, the hooks and the MCP server.

ensure()  start the server if it is not running;  call()  an API call with the key from DATA/keys/<name>.key (or env LOUPE_API_KEY);
queue()   when Loupe cannot be reached, keep a write in DATA/outbox/ (the server ingests it on its next start) so nothing is lost.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HOME = Path(__file__).resolve().parent
DATA = Path(os.environ.get("LOUPE_DATA") or HOME / "data")
DEFAULT_PORT = 8787


def base_url() -> str:
    port = 8787
    try:
        port = int(json.loads((DATA / "server.json").read_text(encoding="utf-8")).get("port", 8787))
    except (OSError, ValueError):
        port = int(os.environ.get("LOUPE_PORT") or 8787)
    return f"http://127.0.0.1:{port}"


def health(timeout: float = 1.2) -> dict | None:
    try:
        with urllib.request.urlopen(base_url() + "/api/health", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
            return d if d.get("app") == "loupe" else None
    except Exception:  # noqa: BLE001
        return None


def _own_port() -> int | None:
    """The port a data folder other than Loupe's own may start a server on: the one its server.json names, else LOUPE_PORT; never the shared default
    port, where Loupe's own server runs (2026-10-28: a test's server for its temp data took 8787 beside it).  None: start nothing."""
    try:
        port = int(json.loads((DATA / "server.json").read_text(encoding="utf-8")).get("port") or 0)
    except (OSError, ValueError, TypeError):
        port = int(os.environ.get("LOUPE_PORT") or 0)
    return port if port and port != DEFAULT_PORT else None


def ensure(wait: float = 14.0) -> bool:
    """True when Loupe answers; starts it detached (no console window) if it does not.  For a data folder other than Loupe's own (a test) a server
    is started only on that folder's own port, never on the shared default one."""
    if health():
        return True
    port = None
    if os.path.normcase(str(DATA.resolve())) != os.path.normcase(str((HOME / "data").resolve())):
        port = _own_port()
        if port is None:
            return False
    exe = sys.executable
    pw = Path(exe).with_name("pythonw.exe")
    if pw.exists():
        exe = str(pw)
    DATA.mkdir(parents=True, exist_ok=True)
    log = open(DATA / "server.log", "ab")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        subprocess.Popen([exe, str(HOME / "loupe.py"), "serve"] + (["--port", str(port)] if port else []), cwd=str(HOME), stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(0.4)
        if health():
            return True
    return False


def key(name: str = "pipeline") -> str:
    k = os.environ.get("LOUPE_API_KEY")
    if k:
        return k.strip()
    try:
        return (DATA / "keys" / f"{name}.key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call(method: str, path: str, body=None, key_name: str = "pipeline", timeout: float = 30.0, raw: bool = False):
    """(status, parsed JSON | bytes).  Status 0 = Loupe unreachable."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key(key_name))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return r.status, (b if raw else json.loads(b.decode("utf-8")))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b.decode("utf-8"))
        except ValueError:
            return e.code, {"ok": False, "error": b[:300].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return 0, {"ok": False, "error": f"Loupe is not reachable: {e}"}


def queue(op: str, doc: dict) -> Path:
    ob = DATA / "outbox"
    ob.mkdir(parents=True, exist_ok=True)
    p = ob / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json"
    p.write_text(json.dumps({"op": op, "doc": doc}, ensure_ascii=False), encoding="utf-8")
    return p
