"""Loupe full backup: the whole record in one folder that goes to git (the admin, 2026-11-16: "everything should be pushed to git").

The seed (seed.py) is the secret-free start of a fresh clone: samples, moments, flags. This is the other thing: export() writes data/backup/ with
  loupe-full.db       an online copy of the live database, every table: settings (the keeper's model and rules, the passphrase hash, the keeper session),
                      the API keys' hashes, the login session, the consult log, audit, usage and file indexes, captures, flags, samples;
  keys/               the agents' key files (the plain keys: the database keeps only their hashes);
  captures/           the raw Higgsfield responses the hooks saved (their signed URLs lose the query string);
  keeper-understood/  the keeper's readings of the samples' pictures;
  backup.json         what it holds and when.
restore() puts it back on a machine: into a data folder with no database, or over an existing one with replace (the old file is moved aside first,
never deleted).  The live database itself is never committed (SQLite is writing it), and the keeper's working context and the frame cache are not part
of this: Loupe rebuilds them.  The folder holds secrets: the repository is private, and only the admin decides that it travels.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

from . import core
from .seed import _scrub

# (folder under data/, folder inside data/backup/, scrub signed URLs out of its .json files)
PARTS = (("keys", "keys", False), ("captures", "captures", True), ("keeper/understood", "keeper-understood", False))


def backup_dir() -> Path:
    return core.DATA / "backup"


def _copy_part(src: Path, dst: Path, scrub: bool, keep_existing: bool = False) -> int:
    n = 0
    if not src.is_dir():
        return 0
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        t = dst / p.relative_to(src)
        if keep_existing and t.exists():
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        raw = p.read_bytes()
        if scrub and p.suffix.lower() == ".json":
            try:                                            # bytes in, bytes out: no newline translation, no re-encoding of what is not text
                raw = _scrub(raw.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                pass
        t.write_bytes(raw)
        n += 1
    return n


def export() -> dict:
    dst = backup_dir()
    dst.mkdir(parents=True, exist_ok=True)
    tmp = dst / "loupe-full.db.tmp"
    if tmp.exists():
        tmp.unlink()
    src = core.db()
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("PRAGMA journal_mode=DELETE")          # one plain file, no -wal / -shm sidecars
        scrubbed = 0
        for fid, doc, search in con.execute("SELECT id, doc, search FROM flags").fetchall():
            d2, s2 = _scrub(doc), _scrub(search)
            if d2 != doc or s2 != search:
                con.execute("UPDATE flags SET doc=?, search=? WHERE id=?", (d2, s2, fid))
                scrubbed += 1
        con.commit()
        tables = {r[0]: None for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1")}
        for t in tables:
            tables[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        con.execute("VACUUM")
    finally:
        con.close()
    for side in (tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        if side.exists():
            side.unlink()
    tmp.replace(dst / "loupe-full.db")
    files = {}
    for live_rel, name, scrub in PARTS:
        d = dst / name
        if d.exists():
            shutil.rmtree(d)
        files[name] = _copy_part(core.DATA / live_rel, d, scrub)
    manifest = {"exported": core.now(), "tables": tables, "files": files, "flags_scrubbed": scrubbed,
                "holds_secrets": True,
                "note": "the whole record: every table of the database (settings with the passphrase hash and the keeper session, API key hashes, the login session, "
                        "consults, audit, usage and file indexes, captures, flags, samples), the agents' key files, the raw capture files and the keeper's readings; "
                        "signed URLs lose their query string in the flag records and the capture files; restore with `python loupe.py backup restore`"}
    (dst / "backup.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def restore(replace: bool = False) -> dict:
    """Put data/backup/ back into the data folder.  No database yet: it is copied in.  A database exists: only with replace, and the old one is moved
    aside (loupe.db.before-restore-<time>) first.  Key, capture and reading files that exist are kept unless replace.  The server must be stopped."""
    src = backup_dir() / "loupe-full.db"
    if not src.is_file():
        return {"restored": False, "reason": "there is no backup in data/backup/ (python loupe.py backup export writes it)"}
    live = core.DATA / "loupe.db"
    moved = ""
    if live.exists():
        if not replace:
            return {"restored": False, "reason": "a database exists already: stop the server and run `python loupe.py backup restore --replace` (the old one is moved aside, not deleted)"}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            f = live.with_name("loupe.db" + suffix)
            if f.exists():
                f.replace(live.with_name(f"loupe.db{suffix}.before-restore-{stamp}"))
        moved = f"loupe.db.before-restore-{stamp}"
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, live)
    files = {}
    for live_rel, name, _scrub_flag in PARTS:
        files[name] = _copy_part(backup_dir() / name, core.DATA / live_rel, False, keep_existing=not replace)
    return {"restored": True, "database": str(live), "old_database_moved_to": moved, "files_copied": files,
            "note": "start the server (`python loupe.py serve`); the passphrase and the API keys are the backup's"}
