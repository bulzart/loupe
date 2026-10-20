"""Loupe seed: the admin's sample library as a file a fresh clone starts from.

export() copies the live database (an online backup, nothing is locked) and strips everything that is not the admin's record: the login
sessions, the API keys, the settings (the passphrase hash and the keeper session live there), the raw Higgsfield captures, the audit, the usage
and file indexes and the consult log; it scrubs signed URLs out of the flag records, then writes data/seed/loupe-seed.db beside a small
manifest (seed.json).  import_if_empty() copies the seed into place when a data folder has no database yet, so with data/samples, data/uploads,
data/flags and data/seed tracked in git a clone opens with every sample, and the admin only sets a passphrase and creates keys.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from . import core

DROP = ("sessions", "api_keys", "settings", "captures", "audit", "usage", "filehash", "consults")
SIGNED = re.compile(r"""(https?://[^\s"'<>\\]+?)\?[^\s"'<>\\]*(?:X-Amz-|[Ss]ignature=|[Ss]ig=)[^\s"'<>\\]*""")


def _scrub(text: str) -> str:
    """A presigned or otherwise signed URL loses its query string; everything else stays."""
    return SIGNED.sub(r"\1?<signed-query-removed>", text or "")


def seed_path() -> Path:
    return core.DATA / "seed" / "loupe-seed.db"


def export(dst: Path | None = None) -> dict:
    dst = dst or seed_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
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
        con.execute("PRAGMA journal_mode=DELETE")          # one plain file, no -wal / -shm sidecars beside the seed
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in DROP:
            if t in names:
                con.execute(f"DELETE FROM {t}")
        if "sqlite_sequence" in names:
            con.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(DROP)), DROP)
        scrubbed = 0
        for fid, doc, search in con.execute("SELECT id, doc, search FROM flags").fetchall():
            d2, s2 = _scrub(doc), _scrub(search)
            if d2 != doc or s2 != search:
                con.execute("UPDATE flags SET doc=?, search=? WHERE id=?", (d2, s2, fid))
                scrubbed += 1
        con.commit()
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("misses", "moments", "flags", "flag_inputs") if t in names}
        con.execute("VACUUM")
    finally:
        con.close()
    for side in (dst, dst.with_name(dst.name + "-wal"), dst.with_name(dst.name + "-shm"), tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        if side.exists():
            side.unlink()
    tmp.replace(dst)
    samples = core.DATA / "samples"
    manifest = {"exported": core.now(), "tables": counts, "dropped": list(DROP), "flags_scrubbed": scrubbed,
                "samples": len([p for p in samples.iterdir() if p.is_dir()]) if samples.is_dir() else 0,
                "note": "misses, moments, flags and flag inputs only: no passphrase, session, API key, capture, audit, usage, file index or consult; "
                        "signed URLs removed from the flag records; the misses' video paths are those of the machine that exported (the sample images do not need them)"}
    (dst.parent / "seed.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def import_if_empty() -> bool:
    """A data folder with no database yet starts from the seed; a database that exists is never touched."""
    live, seed = core.DATA / "loupe.db", seed_path()
    if live.exists() or not seed.exists():
        return False
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed, live)
    return True
