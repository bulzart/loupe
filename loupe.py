#!/usr/bin/env python
"""Loupe - the web app of the /analyze-then-pick pipeline: flag record, generation misses with wrong-vs-correct samples, QC sample API.

  python loupe.py serve [--port 8787]          run the server (http://127.0.0.1:8787, localhost only)
  python loupe.py admin-password [--password]  set or change the admin passphrase
  python loupe.py key-create --name N --scopes flags:write,flags:read [--file NAME]   an API key (shown once; --file also saves it in data/keys/)
  python loupe.py key-list | key-revoke ID
  python loupe.py import-flags PATH            move the entries of the old caught_flags.html page into Loupe
  python loupe.py status                       is it running, how many flags / samples / keys
Scopes: flags:write, flags:read, qc:read, runs:read, admin.
"""
import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loupe_app import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("serve"); s.add_argument("--port", type=int)
    s = sp.add_parser("admin-password"); s.add_argument("--password")
    s = sp.add_parser("key-create"); s.add_argument("--name", required=True); s.add_argument("--scopes", required=True); s.add_argument("--file")
    sp.add_parser("key-list")
    s = sp.add_parser("key-revoke"); s.add_argument("id", type=int)
    s = sp.add_parser("import-flags"); s.add_argument("path")
    sp.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "serve":
        from loupe_app import server
        server.serve(a.port)
    elif a.cmd == "admin-password":
        pw = a.password or getpass.getpass("New admin passphrase (8+ characters): ")
        core.admin_set(pw)
        print("admin passphrase set")
    elif a.cmd == "key-create":
        k = core.key_create(a.name, [x.strip() for x in a.scopes.split(",") if x.strip()])
        if a.file:
            k["saved_to"] = str(core.key_write_file(a.file, k["key"]))
        print(json.dumps(k, indent=1))
    elif a.cmd == "key-list":
        print(json.dumps(core.key_list(), indent=1))
    elif a.cmd == "key-revoke":
        core.key_revoke(a.id)
        print("revoked", a.id)
    elif a.cmd == "import-flags":
        from loupe_app import flags
        print("imported", flags.import_legacy(Path(a.path)), "entries")
    elif a.cmd == "status":
        import loupe_client as lc
        h = lc.health()
        con = core.db()
        try:
            n = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("flags", "misses", "moments", "api_keys", "captures", "consults")}
        finally:
            con.close()
        print(json.dumps({"running": bool(h), "url": lc.base_url(), "admin_set": core.admin_is_set(), **n}, indent=1))


if __name__ == "__main__":
    main()
